# Databricks notebook source
# MAGIC %md
# MAGIC # Step 27 - Procurement copilot tools as Unity Catalog functions
# MAGIC
# MAGIC | Function | Returns | Evidence id prefix in briefs |
# MAGIC |---|---|---|
# MAGIC | `proc_benchmark(country, category, method)` | Kaplan-Meier median / P80, n, censored count, most specific cell with ≥15 tenders | `B` |
# MAGIC | `proc_similar_tenders(description, category, country)` | past tenders: method, days to award, bidders, re-tender flag | `T` |
# MAGIC | `proc_search_lessons(query, country, category)` | **quote-verified** lessons with page and link (vector search over lesson chunks) | `L` |
# MAGIC | `proc_search_passages(query, section_kind, country)` | report passages filtered by section kind | `P` |
# MAGIC | `proc_project_evidence(project_id)` | resolved rating, conflict flag and full rating trajectory | `R` |
# MAGIC | `proc_country_context(country, year)` | CPIA / WGI scores, with missing flags | `G` |
# MAGIC
# MAGIC Governed like the Step 7 tools: `EXECUTE` grants, audit log, lineage. Model scores come from the `@champion`
# MAGIC models loaded by the copilot (Step 28) rather than SQL.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

spark.sql(f"USE CATALOG {CATALOG}")
C = CATALOG

spark.sql(f"""
CREATE OR REPLACE FUNCTION {C}.ai.proc_benchmark(
  country STRING COMMENT 'ISO-3 code, e.g. KEN', category STRING COMMENT 'Works | Goods | Consultant Services | Non-consulting Services',
  method STRING DEFAULT NULL COMMENT 'Method code, e.g. RFB, RFQ, QCBS, CQS, INDV')
RETURNS TABLE (level STRING, n BIGINT, n_awarded BIGINT, median_days DOUBLE, p80_days DOUBLE, awarded_only_median DOUBLE)
COMMENT 'Historical invitation-to-award time for comparable World Bank-financed tenders (Kaplan-Meier, open tenders counted as censored). Returns the most specific benchmark with at least 15 tenders.'
RETURN
  SELECT level, n, n_awarded, median_days, p80_days, naive_median_awarded_only FROM {C}.gold.tender_benchmarks
  WHERE n >= 15 AND procurement_category = category
    AND (level = 'category'
         OR (level = 'category+method' AND method_code = method)
         OR (level = 'country+category+method' AND method_code = method AND country_iso3 = upper(country)))
  ORDER BY CASE level WHEN 'country+category+method' THEN 1 WHEN 'category+method' THEN 2 ELSE 3 END
  LIMIT 1
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {C}.ai.proc_similar_tenders(
  description STRING COMMENT 'Package description, e.g. "rehabilitation of rural roads Lot 1"',
  category STRING COMMENT 'Procurement category', country STRING DEFAULT NULL COMMENT 'Optional ISO-3 code to prefer')
RETURNS TABLE (tender_id STRING, description STRING, country_iso3 STRING, procurement_method STRING, open_date DATE,
               observed_days INT, n_bidders INT, rebid_flag BOOLEAN, single_bid_flag BOOLEAN, project_id STRING, score DOUBLE)
COMMENT 'Past tenders in the same category with the most similar descriptions (token overlap), with their outcomes.'
RETURN
  WITH q AS (SELECT array_distinct(filter(split(lower(regexp_replace(description, '[^A-Za-z ]', ' ')), ' +'), w -> length(w) >= 4)) AS qt)
  SELECT t.tender_id, t.description, t.country_iso3, t.procurement_method, t.open_date, CAST(t.observed_days AS INT),
         CAST(t.n_bidders AS INT), t.rebid_flag, CAST(t.single_bid_flag AS BOOLEAN), t.project_id,
         size(array_intersect(q.qt, array_distinct(split(lower(regexp_replace(t.description, '[^A-Za-z ]', ' ')), ' +'))))
           * CASE WHEN t.country_iso3 = upper(country) THEN 1.3 ELSE 1.0 END AS score
  FROM {C}.gold.fact_tender t CROSS JOIN q
  WHERE t.procurement_category = category AND t.description IS NOT NULL
  ORDER BY score DESC, t.open_date DESC
  LIMIT 12
""")

for name, kinds, extra in (
    ("proc_search_lessons", "'lessons', 'key_factors'", "Lessons and key-factor passages from completion and evaluation reports"),
    ("proc_search_passages", "'lessons', 'key_factors', 'procurement_fiduciary', 'risk', 'design', 'results'",
     "Any narrative passage from project documents (PAD, ISR, ICR, PPAR)")):
    spark.sql(f"""
CREATE OR REPLACE FUNCTION {C}.ai.{name}(
  query STRING COMMENT 'What to look for, e.g. "re-tendering because bids exceeded the estimate"',
  country STRING DEFAULT NULL COMMENT 'Optional ISO-3 code')
RETURNS TABLE (chunk_id STRING, doc_type_code STRING, project_id STRING, title STRING, section_path STRING,
               page_start INT, landing_url STRING, chunk_text STRING, score DOUBLE)
COMMENT '{extra}, hybrid vector search, narrative chunks only, with page and link for citation.'
RETURN
  SELECT chunk_id, doc_type_code, project_id, title, section_path, CAST(page_start AS INT), landing_url, chunk_text,
         CAST(search_score AS DOUBLE) * CASE WHEN country_iso3 = upper(country) THEN 1.3 ELSE 1.0 END AS score
  FROM vector_search(index => '{PROC_VS_INDEX}', query_text => query, query_type => 'HYBRID', num_results => 40)
  WHERE section_kind IN ({kinds}) AND content_type = 'narrative'
  ORDER BY score DESC LIMIT 8
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {C}.ai.proc_project_evidence(project_id STRING COMMENT 'World Bank project id, e.g. P153349')
RETURNS TABLE (project_id STRING, resolved_rating STRING, resolved_from STRING, conflict BOOLEAN, worst_isr STRING,
               trajectory STRING, restructurings BIGINT, closing_planned DATE, closing_actual DATE)
COMMENT 'Resolved development-outcome rating for a project, whether supervision (ISR) and completion (ICR/IEG) evidence conflict, and the full dated rating trajectory.'
RETURN
  SELECT r.project_id, r.resolved_rating, r.resolved_from, r.conflict, r.worst_isr, r.trajectory,
         o.restructurings, o.closing_planned, o.closing_actual
  FROM {C}.gold.project_ratings_resolved r LEFT JOIN {C}.gold.project_outcomes o USING (project_id)
  WHERE r.project_id = upper(trim(proc_project_evidence.project_id))
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {C}.ai.proc_country_context(country STRING COMMENT 'ISO-3 code', yr INT DEFAULT NULL COMMENT 'Year (default: latest)')
RETURNS TABLE (country_iso3 STRING, year INT, cpia_public_sector_mgmt DOUBLE, cpia_transparency_corruption DOUBLE,
               wgi_gov_effectiveness DOUBLE, wgi_control_of_corruption DOUBLE, cpia_missing INT)
COMMENT 'Governance context (CPIA 1-6, WGI about -2.5..2.5). cpia_missing = 1 where CPIA is not published (non-IDA countries).'
RETURN
  SELECT country_iso3, year, cpia_public_sector_mgmt, cpia_transparency_corruption, wgi_gov_effectiveness,
         wgi_control_of_corruption, cpia_missing
  FROM {C}.gold.country_governance_year
  WHERE country_iso3 = upper(country) AND year = coalesce(yr, (SELECT max(year) FROM {C}.gold.country_governance_year
                                                               WHERE country_iso3 = upper(country) AND wgi_gov_effectiveness IS NOT NULL))
""")

# COMMAND ----------

tests = {
    "proc_benchmark": f"SELECT * FROM {C}.ai.proc_benchmark('KEN', 'Works', 'RFB')",
    "proc_similar_tenders": f"SELECT * FROM {C}.ai.proc_similar_tenders('rehabilitation of rural roads Lot 1', 'Works', 'KEN')",
    "proc_search_lessons": f"SELECT doc_type_code, title, page_start, left(chunk_text, 200) FROM {C}.ai.proc_search_lessons('civil works delayed re-tender bids exceeded estimate', 'KEN')",
    "proc_project_evidence": f"SELECT * FROM {C}.ai.proc_project_evidence((SELECT max(project_id) FROM {C}.gold.project_ratings_resolved WHERE conflict))",
    "proc_country_context": f"SELECT * FROM {C}.ai.proc_country_context('NGA')",
}
failed = []
for name, q in tests.items():
    try:
        df = spark.sql(q)
        n = df.count()
        print(f"✓ {name:<24} {n} rows")
        display(df.limit(8))
        if n == 0:
            failed.append(name)
    except Exception as e:
        failed.append(name)
        print(f"✗ {name}: {str(e).splitlines()[0][:200]}")
for f in ("proc_benchmark", "proc_similar_tenders", "proc_search_lessons", "proc_search_passages", "proc_project_evidence",
          "proc_country_context"):
    try:
        spark.sql(f"GRANT EXECUTE ON FUNCTION {C}.ai.{f} TO `{GROUP_AI_APP}`")
    except Exception as e:
        print("⚠ grant", f, str(e)[:120])
log_event("proc_tools", "complete", level="WARNING" if failed else "INFO", failed=failed)
