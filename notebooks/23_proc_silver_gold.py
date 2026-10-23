# Databricks notebook source
# MAGIC %md
# MAGIC # Step 23 - Procurement: Silver cleaning, entity resolution and the Gold knowledge graph
# MAGIC
# MAGIC ```
# MAGIC  NOTICE (IFB/REOI) ──ANNOUNCES──▶ TENDER ──RESULTED_IN──▶ CONTRACT ──AWARDED_TO──▶ SUPPLIER
# MAGIC                                     │                        │
# MAGIC                                     └──────FINANCED_BY───────┴──▶ PROJECT ──LOCATED_IN──▶ COUNTRY ──HAS_CONTEXT(year)──▶ GOVERNANCE
# MAGIC                                                                    ▲   └──IN_SECTOR──▶ SECTOR
# MAGIC                       DOCUMENT (PAD/ISR/ICR/PPAR) ──DESCRIBES──────┘
# MAGIC                       LESSON ──FROM──▶ CHUNK ──PART_OF──▶ DOCUMENT          (added in Step 24)
# MAGIC ```
# MAGIC
# MAGIC | Layer | Table | Grain |
# MAGIC |---|---|---|
# MAGIC | silver | `contracts`, `notices`, `projects`, `governance` | one typed row per source record |
# MAGIC | gold | `fact_contract` | contract × supplier, with resolved `supplier_key` and linked `tender_id` |
# MAGIC | gold | `fact_tender` | one procurement package: open date → award date, bidders, re-bid flag, censoring flag |
# MAGIC | gold | `dim_supplier`, `dim_project`, `country_governance_year` | resolved entities |
# MAGIC | gold | `kg_edges` | every link with **method** (`exact_key`, `fuzzy_desc`, `temporal`) and **confidence** |
# MAGIC
# MAGIC **Reconciling disjoint keys:** contract ↔ tender on the normalised bid reference (exact), else on the same project +
# MAGIC category + description similarity + date window (fuzzy); supplier variants on a normalised name key, then
# MAGIC blocked fuzzy matching within the same supplier country; project ↔ governance by country and the nearest prior year.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

from pyspark.sql import functions as F, types as T

spark.sql(f"USE CATALOG {CATALOG}")
log_event("proc_silver_gold", "started")
J = lambda path: F.get_json_object("raw_json", path)          # noqa: E731
# Serverless runs with ANSI SQL on: a bad date or number would fail the whole job, so parse with try_* functions
JT = lambda path, typ: F.expr(f"try_cast(get_json_object(raw_json, '{path}') AS {typ})")   # noqa: E731
JUSD = lambda path: F.expr(f"try_cast(regexp_replace(get_json_object(raw_json, '{path}'), ',', '') AS DOUBLE)")  # noqa: E731


def WB_DATE(c):
    """'20-Oct-2026' | '2026-12-10T00:00:00Z' | '12/31/2029 12:00:00 AM' → DATE (NULL if none fits)."""
    return F.coalesce(F.call_function("try_to_date", c, F.lit("dd-MMM-yyyy")),
                      F.call_function("try_to_date", F.substring(c, 1, 10), F.lit("yyyy-MM-dd")),
                      F.call_function("try_to_date", F.split(c, " ")[0], F.lit("M/d/yyyy")))


CATEGORY = {"CW": "Works", "GO": "Goods", "CS": "Consultant Services", "NC": "Non-consulting Services"}

norm_ref_udf = F.udf(norm_ref, "string")
norm_supplier_udf = F.udf(norm_supplier, "string")
is_rebid_udf = F.udf(lambda a, b: bool(is_rebid(a, b)), "boolean")

# COMMAND ----------

# MAGIC %md ## Silver: contracts (Major Contract Awards)

# COMMAND ----------

def category_from_text(c):
    """Harmonise the award dataset's category labels with the notice codes."""
    return (F.when(F.lower(c).contains("consult"), "Consultant Services")
             .when(F.lower(c).contains("non"), "Non-consulting Services")
             .when(F.lower(c).contains("work"), "Works")
             .when(F.lower(c).contains("good"), "Goods")
             .otherwise(c))

contracts = (
    spark.table("bronze.contract_awards_raw")
    .select(
        F.col("wb_contract_number"),
        J("$.supplier_id").alias("supplier_id"),
        F.trim(J("$.supplier")).alias("supplier_name"),
        J("$.supplier_country").alias("supplier_country"),
        J("$.supplier_country_code").alias("supplier_country_code"),
        J("$.project_id").alias("project_id"),
        J("$.project_name").alias("project_name"),
        F.col("country_iso3"),
        J("$.region").alias("region"),
        JT("$.fiscal_year", "INT").alias("fiscal_year"),
        WB_DATE(J("$.contract_signing_date")).alias("signing_date"),
        category_from_text(J("$.procurement_category")).alias("procurement_category"),
        J("$.procurement_method").alias("procurement_method"),
        J("$.review_type").alias("review_type"),
        F.coalesce(JT("$.supplier_contract_amount_usd", "DOUBLE"), JT("$.total_contract_amount_usd", "DOUBLE")).alias("amount_usd"),
        F.trim(F.regexp_replace(J("$.contract_description"), r"\s+", " ")).alias("description"),
        J("$.borrower_contract_reference_number").alias("borrower_ref"),
        J("$.project_global_practice").alias("global_practice"),
    )
    .withColumn("borrower_ref_key", norm_ref_udf("borrower_ref"))
    .withColumn("supplier_name_key", norm_supplier_udf("supplier_name"))
)
contracts.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.contracts")
print("silver.contracts", spark.table("silver.contracts").count())

# COMMAND ----------

# MAGIC %md ## Silver: notices (with parsed Contract Award bodies)

# COMMAND ----------

AWARD_SCHEMA = T.StructType([
    T.StructField("notification_date", T.DateType()), T.StructField("contract_duration_months", T.DoubleType()),
    T.StructField("signed_currency", T.StringType()), T.StructField("signed_amount", T.DoubleType()),
    T.StructField("n_bidders", T.IntegerType()), T.StructField("n_rejected", T.IntegerType()),
    T.StructField("rejection_reasons", T.ArrayType(T.StringType())),
    T.StructField("awarded_names", T.ArrayType(T.StringType())), T.StructField("awarded_ids", T.ArrayType(T.StringType())),
    T.StructField("notice_version", T.IntegerType()),
])

@F.udf(AWARD_SCHEMA)
def parse_award_udf(notice_html):
    a = parse_award_notice(notice_html)
    return (a["notification_date"], a["contract_duration_months"], a["signed_currency"], a["signed_amount"],
            a["n_bidders"], len(a["rejected"]), a["rejection_reasons"],
            [b["name"] for b in a["awarded"]], [b["supplier_id"] for b in a["awarded"]], a["notice_version"])

notices = (
    spark.table("bronze.proc_notices_raw")
    .select(
        "notice_id", "country_iso3", "notice_type",
        WB_DATE(J("$.noticedate")).alias("notice_date"),
        WB_DATE(J("$.submission_deadline_date")).alias("deadline_date"),
        J("$.project_id").alias("project_id"),
        J("$.bid_reference_no").alias("bid_ref"),
        F.trim(F.regexp_replace(J("$.bid_description"), r"\s+", " ")).alias("description"),
        J("$.procurement_group").alias("procurement_group"),
        J("$.procurement_method_code").alias("method_code"),
        F.trim(F.regexp_replace(J("$.procurement_method_name"), r"\s+", " ")).alias("method_name"),
        J("$.contact_organization").alias("implementing_org"),
        F.when(F.col("notice_type") == "Contract Award", parse_award_udf(J("$.notice_text"))).alias("award"),
    )
    .withColumn("bid_ref_key", norm_ref_udf("bid_ref"))
    .withColumn("procurement_category", F.coalesce(F.create_map(*[x for k, v in CATEGORY.items() for x in (F.lit(k), F.lit(v))])
                                                   [F.col("procurement_group")], F.col("procurement_group")))
    .withColumn("rebid_text", is_rebid_udf("description", "bid_ref"))
)
notices.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.notices")
display(spark.sql("""SELECT notice_type, count(*) AS n, count(bid_ref_key) AS with_ref,
                            count(award.notification_date) AS award_parsed, round(avg(award.n_bidders), 2) AS avg_bidders
                     FROM silver.notices GROUP BY 1"""))

# COMMAND ----------

# MAGIC %md ## Silver: projects and governance

# COMMAND ----------

projects = (
    spark.table("bronze.projects_raw")
    .select(
        "project_id", "country_iso3",
        J("$.project_name").alias("project_name"),
        J("$.regionname").alias("region"),
        WB_DATE(J("$.boardapprovaldate")).alias("approval_date"),
        WB_DATE(J("$.closingdate")).alias("closing_date"),
        J("$.status").alias("status"),
        J("$.lendinginstr").alias("lending_instrument"),
        F.coalesce(JUSD("$.totalcommamt"), JUSD("$.totalamt")).alias("commitment_usd"),
        J("$.envassesmentcategorycode").alias("env_category"),
        J("$.esrc_ovrl_risk_rate").alias("es_risk_rating"),
        J("$.mjsector_namecode[0].name").alias("major_sector"),
        J("$.sector1.Name").alias("sector1"),
        J("$.impagency").alias("implementing_agency"),
        F.coalesce(J("$.pdo"), J("$.project_abstract.cdata!")).alias("objective"),
        J("$.url").alias("project_url"),
    )
)
projects.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.projects")

gov_cols = {code: col for code, (_, col) in GOV_INDICATORS.items()}
gov_long = spark.table("bronze.governance_raw").select(
    "country_iso3", "year", "indicator_code", JT("$.value", "DOUBLE").alias("value"))
gov = (gov_long.groupBy("country_iso3", "year").pivot("indicator_code", list(gov_cols)).agg(F.first("value", ignorenulls=True)))
for code, col in gov_cols.items():
    gov = gov.withColumnRenamed(code, col)
gov.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.governance")
display(spark.sql("SELECT * FROM silver.governance WHERE year = (SELECT max(year) - 2 FROM silver.governance) ORDER BY 1"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Entity resolution: suppliers
# MAGIC 1. **Exact key:** normalised name (`M/S`, legal forms, punctuation, JV order removed) → most variants collapse.
# MAGIC 2. **Fuzzy within blocks:** keys in the same supplier country that share their first token are compared with
# MAGIC    `name_similarity`; pairs ≥ 0.92 are merged (union-find). Blocking keeps it near-linear.
# MAGIC 3. The WB `supplier_id` is kept as evidence but not trusted alone - the same firm has several ids across years.

# COMMAND ----------

names = (spark.table("silver.contracts").where("supplier_name_key IS NOT NULL")
         .groupBy("supplier_name_key", "supplier_country_code")
         .agg(F.count("*").alias("n"), F.collect_set("supplier_name").alias("variants"),
              F.collect_set("supplier_id").alias("wb_ids"))
         .toPandas())

parent = {}
def find(x):
    while parent.get(x, x) != x:
        parent[x] = parent.get(parent[x], parent[x])
        x = parent[x]
    return x

names["node"] = names["supplier_name_key"] + "|" + names["supplier_country_code"].fillna("")
names["block"] = names["supplier_name_key"].str.split().str[0] + "|" + names["supplier_country_code"].fillna("")
fuzzy_pairs = 0
for _, grp in names.groupby("block"):
    keys = grp.sort_values("n", ascending=False)[["node", "supplier_name_key"]].values.tolist()
    if len(keys) > 400:      # very common first token (e.g. 'china'): compare only within the 400 largest
        keys = keys[:400]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if name_similarity(keys[i][1], keys[j][1]) >= 0.92:
                a, b = find(keys[i][0]), find(keys[j][0])
                if a != b:
                    parent[b] = a       # larger (more contracts) key wins as canonical
                    fuzzy_pairs += 1
names["supplier_key"] = names["node"].map(find)
print(f"{len(names)} name keys → {names['supplier_key'].nunique()} suppliers ({fuzzy_pairs} fuzzy merges)")

spark.createDataFrame(names[["node", "supplier_key"]]).createOrReplaceTempView("supplier_map")

# COMMAND ----------

# MAGIC %md ## Gold: tenders (IFB/REOI → award) with survival framing
# MAGIC `cycle_days` = invitation date → notification of award. Tenders with no award yet are **right-censored**
# MAGIC (`award_observed = false`, duration = days open so far) instead of being dropped - dropping them would bias
# MAGIC benchmarks toward fast tenders.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW tender_base AS
WITH inv AS (
  SELECT bid_ref_key, min(notice_date) AS open_date, max(deadline_date) AS deadline_date,
         count(*) AS n_invitations, max(rebid_text) AS rebid_text,
         first(project_id, true) AS project_id, first(country_iso3, true) AS country_iso3,
         first(procurement_category, true) AS procurement_category, first(method_name, true) AS method_name,
         first(method_code, true) AS method_code, first(description, true) AS description,
         first(implementing_org, true) AS implementing_org, first(notice_type, true) AS open_notice_type
  FROM silver.notices
  WHERE notice_type IN ('Invitation for Bids', 'Request for Expression of Interest') AND bid_ref_key IS NOT NULL
  GROUP BY bid_ref_key
),
awd AS (
  SELECT bid_ref_key, min(coalesce(award.notification_date, notice_date)) AS award_date,
         max_by(award, notice_date) AS award, count(*) AS n_award_notices, max(award.notice_version) AS max_notice_version,
         first(project_id, true) AS project_id, first(country_iso3, true) AS country_iso3,
         first(procurement_category, true) AS procurement_category, first(method_name, true) AS method_name,
         first(method_code, true) AS method_code, first(description, true) AS description
  FROM silver.notices WHERE notice_type = 'Contract Award' AND bid_ref_key IS NOT NULL
  GROUP BY bid_ref_key
)
SELECT coalesce(i.bid_ref_key, a.bid_ref_key) AS tender_id,
       coalesce(i.project_id, a.project_id) AS project_id,
       coalesce(i.country_iso3, a.country_iso3) AS country_iso3,
       coalesce(i.procurement_category, a.procurement_category) AS procurement_category,
       coalesce(i.method_name, a.method_name) AS procurement_method,
       coalesce(i.method_code, a.method_code) AS method_code,
       coalesce(i.description, a.description) AS description,
       i.implementing_org, i.open_notice_type, i.open_date, i.deadline_date, a.award_date,
       datediff(i.deadline_date, i.open_date) AS bid_window_days,
       coalesce(i.n_invitations, 0) AS n_invitations,
       (coalesce(i.n_invitations, 0) > 1 OR coalesce(i.rebid_text, false)) AS rebid_flag,
       a.award.n_bidders AS n_bidders, a.award.n_rejected AS n_rejected, a.award.rejection_reasons AS rejection_reasons,
       a.award.contract_duration_months AS contract_duration_months,
       a.award.awarded_names AS awarded_names,
       coalesce(a.max_notice_version, 0) > 0 OR coalesce(a.n_award_notices, 0) > 1 AS award_amended_flag,
       CASE WHEN i.open_date IS NOT NULL AND a.award_date IS NOT NULL THEN datediff(a.award_date, i.open_date) END AS observed_days,
       a.award_date IS NOT NULL AS award_observed
FROM inv i FULL OUTER JOIN awd a ON i.bid_ref_key = a.bid_ref_key
""")

spark.sql("""
CREATE OR REPLACE TABLE silver.tenders
COMMENT 'Tenders before enrichment with contract data (see gold.fact_tender).'
AS SELECT *,
  -- no award notice after 2 years is not "still open": most likely cancelled or the award was never published.
  -- Those are excluded from cycle-time targets (outcome_unknown) instead of being censored at multi-year ages.
  (NOT award_observed AND open_date IS NOT NULL AND datediff(current_date(), open_date) > 730) AS outcome_unknown,
  CASE WHEN award_observed AND open_date IS NOT NULL THEN observed_days
       WHEN NOT award_observed AND open_date IS NOT NULL AND datediff(current_date(), open_date) <= 730
         THEN datediff(current_date(), open_date) END AS cycle_days,
  (n_bidders = 1 AND procurement_method NOT ILIKE '%direct%' AND procurement_method NOT ILIKE '%single%') AS single_bid_flag,
  CASE WHEN n_bidders > 0 THEN n_rejected / n_bidders END AS rejected_share,
  year(coalesce(open_date, award_date)) AS tender_year
FROM tender_base
WHERE coalesce(open_date, award_date) >= make_date(MIN_YEAR, 1, 1)
  AND (observed_days IS NULL OR observed_days BETWEEN 0 AND 1500)      -- drops reference collisions across years
""".replace("MIN_YEAR", str(PROC_MIN_YEAR)))
display(spark.sql("""
  SELECT procurement_category, count(*) AS tenders, count_if(open_date IS NOT NULL AND award_observed) AS with_cycle,
         percentile(observed_days, 0.5) AS median_days, round(avg(cast(rebid_flag AS int)), 3) AS rebid_rate,
         round(avg(cast(single_bid_flag AS int)), 3) AS single_bid_rate
  FROM silver.tenders GROUP BY 1 ORDER BY 2 DESC"""))

# COMMAND ----------

# MAGIC %md ## Gold: contracts linked to tenders and resolved suppliers

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW contract_exact AS
SELECT c.*, m.supplier_key, t.tender_id, CASE WHEN t.tender_id IS NOT NULL THEN 'exact_key' END AS tender_link_method,
       CASE WHEN t.tender_id IS NOT NULL THEN 1.0 END AS tender_link_confidence
FROM silver.contracts c
LEFT JOIN supplier_map m ON m.node = concat(c.supplier_name_key, '|', coalesce(c.supplier_country_code, ''))
LEFT JOIN silver.tenders t ON t.tender_id = c.borrower_ref_key
""")

# Fuzzy fallback for contracts without a reference match: same project + category, award within 18 months after the
# invitation, and description similarity (token Jaccard on 3+ letter words) ≥ 0.5. Best candidate wins.
spark.sql("""
CREATE OR REPLACE TEMP VIEW contract_fuzzy AS
WITH c AS (SELECT wb_contract_number, supplier_key, project_id, procurement_category, signing_date,
                  array_distinct(filter(split(lower(regexp_replace(description, '[^A-Za-z ]', ' ')), ' +'), w -> length(w) >= 3)) AS toks
           FROM contract_exact WHERE tender_id IS NULL AND description IS NOT NULL),
     t AS (SELECT tender_id, project_id, procurement_category, open_date,
                  array_distinct(filter(split(lower(regexp_replace(description, '[^A-Za-z ]', ' ')), ' +'), w -> length(w) >= 3)) AS toks
           FROM silver.tenders WHERE open_date IS NOT NULL AND description IS NOT NULL),
     cand AS (
       SELECT c.wb_contract_number, c.supplier_key, t.tender_id,
              size(array_intersect(c.toks, t.toks)) / greatest(size(array_union(c.toks, t.toks)), 1) AS sim
       FROM c JOIN t ON c.project_id = t.project_id AND c.procurement_category = t.procurement_category
                    AND c.signing_date BETWEEN t.open_date AND date_add(t.open_date, 540))
SELECT wb_contract_number, supplier_key, max_by(tender_id, sim) AS tender_id, max(sim) AS sim
FROM cand WHERE sim >= 0.5 GROUP BY 1, 2
""")

spark.sql("""
CREATE OR REPLACE TABLE gold.fact_contract
COMMENT 'Signed contracts (contract x supplier) with resolved supplier_key and the tender they came from (exact reference or fuzzy match, with method and confidence).'
AS SELECT e.* EXCEPT (tender_id, tender_link_method, tender_link_confidence),
          coalesce(e.tender_id, f.tender_id) AS tender_id,
          coalesce(e.tender_link_method, CASE WHEN f.tender_id IS NOT NULL THEN 'fuzzy_desc' END) AS tender_link_method,
          coalesce(e.tender_link_confidence, round(f.sim, 3)) AS tender_link_confidence
FROM contract_exact e
LEFT JOIN contract_fuzzy f ON e.wb_contract_number = f.wb_contract_number AND e.supplier_key <=> f.supplier_key
""")
display(spark.sql("""SELECT coalesce(tender_link_method, 'unlinked') AS link, count(*) AS contracts,
                            round(sum(amount_usd) / 1e6, 1) AS usd_m FROM gold.fact_contract GROUP BY 1"""))

# Enrich tenders with what only the contract knows: review type (prior/post), USD amount, winning supplier
enriched = spark.sql("""
SELECT t.*, c.review_type, c.contract_usd, c.supplier_key
FROM silver.tenders t
LEFT JOIN (SELECT tender_id, first(review_type, true) AS review_type, sum(amount_usd) AS contract_usd,
                  first(supplier_key, true) AS supplier_key FROM gold.fact_contract WHERE tender_id IS NOT NULL GROUP BY 1) c
USING (tender_id)
""")
enriched.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("gold.fact_tender")
spark.sql("""COMMENT ON TABLE gold.fact_tender IS 'One procurement package (tender) from invitation to award. cycle_days with
award_observed = false are right-censored (still open). Source for timeline benchmarks and risk models.'""")

# COMMAND ----------

# MAGIC %md ## Gold: dimensions

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold.dim_supplier
COMMENT 'Resolved suppliers: one row per supplier_key with all name variants and WB supplier ids seen, plus track record.'
AS SELECT supplier_key,
          max_by(supplier_name, amount_usd) AS supplier_name,
          first(supplier_country, true) AS supplier_country,
          collect_set(supplier_name) AS name_variants, collect_set(supplier_id) AS wb_supplier_ids,
          count(DISTINCT wb_contract_number) AS n_contracts, round(sum(amount_usd), 0) AS total_usd,
          count(DISTINCT country_iso3) AS n_borrower_countries, min(signing_date) AS first_contract, max(signing_date) AS last_contract
FROM gold.fact_contract WHERE supplier_key IS NOT NULL GROUP BY supplier_key
""")

spark.sql("""
CREATE OR REPLACE TABLE gold.dim_project
COMMENT 'Projects with procurement activity summary.'
AS SELECT p.*, datediff(p.closing_date, p.approval_date) AS planned_duration_days,
          c.n_contracts, c.contracted_usd, t.n_tenders, t.median_cycle_days, t.rebid_rate
FROM silver.projects p
LEFT JOIN (SELECT project_id, count(*) AS n_contracts, sum(amount_usd) AS contracted_usd FROM gold.fact_contract GROUP BY 1) c USING (project_id)
LEFT JOIN (SELECT project_id, count(*) AS n_tenders, percentile(observed_days, 0.5) AS median_cycle_days,
                  avg(cast(rebid_flag AS int)) AS rebid_rate FROM gold.fact_tender GROUP BY 1) t USING (project_id)
""")

# Governance by country-year, forward-filled up to 3 years (scores are annual and published with a lag);
# *_missing flags keep "not published" distinguishable from "low score"
gcols = list(gov_cols.values())
spark.sql(f"""
CREATE OR REPLACE TABLE gold.country_governance_year
COMMENT 'CPIA and WGI scores per country and year, forward-filled up to 3 years; cpia_missing = 1 where CPIA is not published (non-IDA countries).'
AS WITH years AS (SELECT c.country_iso3, y.year FROM (SELECT DISTINCT country_iso3 FROM silver.governance) c
                  CROSS JOIN (SELECT explode(sequence({PROC_MIN_YEAR - 3}, year(current_date()))) AS year) y)
SELECT y.country_iso3, y.year,
       {", ".join(f"last(g.{c}, true) OVER w3 AS {c}" for c in gcols)},
       CASE WHEN last(g.cpia_public_sector_mgmt, true) OVER w3 IS NULL THEN 1 ELSE 0 END AS cpia_missing
FROM years y LEFT JOIN silver.governance g USING (country_iso3, year)
WINDOW w3 AS (PARTITION BY y.country_iso3 ORDER BY y.year ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
""")

# COMMAND ----------

# MAGIC %md ## Gold: knowledge-graph edges
# MAGIC Stored as a Delta edge list (works with SQL, GraphFrames or export to a graph DB). Each edge records how it
# MAGIC was derived, so the copilot can say *"linked by exact reference"* vs *"probable match (0.62)"*.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE gold.kg_edges (
  src_type STRING, src_id STRING, rel STRING, dst_type STRING, dst_id STRING,
  method STRING COMMENT 'exact_key | fuzzy_desc | fuzzy_name | temporal | llm_extracted',
  confidence DOUBLE, valid_from DATE, evidence STRING COMMENT 'Pointer to the record/passage that supports the edge'
) COMMENT 'Knowledge graph edge list linking notices, tenders, contracts, suppliers, projects, documents, lessons, countries and governance context.'
""")
spark.sql("""
INSERT INTO gold.kg_edges
SELECT 'NOTICE', notice_id, 'ANNOUNCES', 'TENDER', bid_ref_key, 'exact_key', 1.0, notice_date, notice_type
FROM silver.notices WHERE bid_ref_key IN (SELECT tender_id FROM gold.fact_tender)
UNION ALL
SELECT 'TENDER', tender_id, 'RESULTED_IN', 'CONTRACT', wb_contract_number, tender_link_method, tender_link_confidence, signing_date, borrower_ref
FROM gold.fact_contract WHERE tender_id IS NOT NULL
UNION ALL
SELECT DISTINCT 'CONTRACT', wb_contract_number, 'AWARDED_TO', 'SUPPLIER', supplier_key,
       CASE WHEN supplier_key = concat(supplier_name_key, '|', coalesce(supplier_country_code, '')) THEN 'exact_key' ELSE 'fuzzy_name' END,
       1.0, signing_date, supplier_name
FROM gold.fact_contract WHERE supplier_key IS NOT NULL
UNION ALL
SELECT DISTINCT 'CONTRACT', wb_contract_number, 'FINANCED_BY', 'PROJECT', project_id, 'exact_key', 1.0, signing_date, NULL
FROM gold.fact_contract WHERE project_id IS NOT NULL
UNION ALL
SELECT 'TENDER', tender_id, 'FINANCED_BY', 'PROJECT', project_id, 'exact_key', 1.0, open_date, NULL
FROM gold.fact_tender WHERE project_id IS NOT NULL
UNION ALL
SELECT 'PROJECT', project_id, 'LOCATED_IN', 'COUNTRY', country_iso3, 'exact_key', 1.0, approval_date, NULL FROM silver.projects
UNION ALL
SELECT 'PROJECT', project_id, 'IN_SECTOR', 'SECTOR', major_sector, 'exact_key', 1.0, approval_date, NULL
FROM silver.projects WHERE major_sector IS NOT NULL
UNION ALL
SELECT 'TENDER', tender_id, 'HAS_CONTEXT', 'GOVERNANCE', concat(country_iso3, ':', tender_year), 'temporal', 1.0, open_date,
       'country-year governance scores, forward-filled ≤3y'
FROM gold.fact_tender WHERE tender_year IS NOT NULL
UNION ALL
SELECT 'DOCUMENT', m.doc_id, 'DESCRIBES', 'PROJECT', p.pid, 'exact_key', 1.0, m.doc_date, m.doc_type_code
FROM bronze.project_docs_meta m LATERAL VIEW explode(m.project_ids) p AS pid
WHERE m.download_status NOT LIKE 'failed%'
""")
display(spark.sql("SELECT src_type, rel, dst_type, method, count(*) AS edges FROM gold.kg_edges GROUP BY ALL ORDER BY edges DESC"))

# COMMAND ----------

# MAGIC %md ## Checks: how well did the keys reconcile?

# COMMAND ----------

checks = spark.sql("""
SELECT
  (SELECT count(*) FROM gold.fact_contract) AS contracts,
  (SELECT round(avg(CAST(tender_id IS NOT NULL AS INT)), 3) FROM gold.fact_contract) AS contract_tender_link_rate,
  (SELECT round(avg(CAST(p.project_id IS NOT NULL AS INT)), 3) FROM gold.fact_contract c LEFT JOIN silver.projects p USING (project_id)) AS contract_project_link_rate,
  (SELECT count(*) FROM gold.fact_tender) AS tenders,
  (SELECT count_if(award_observed AND open_date IS NOT NULL) FROM gold.fact_tender) AS tenders_with_cycle,
  (SELECT count(*) FROM gold.dim_supplier) AS suppliers,
  (SELECT count(DISTINCT supplier_name) FROM silver.contracts) AS raw_supplier_names
""").first().asDict()
print(json.dumps(checks, indent=1, default=str))
assert checks["tenders_with_cycle"] > 50, "Too few tenders with an invitation and an award - check notices ingestion"

for t, tags in {"gold.fact_tender": "procurement", "gold.fact_contract": "procurement", "gold.kg_edges": "knowledge_graph"}.items():
    try:
        spark.sql(f"ALTER TABLE {t} SET TAGS ('layer' = 'gold', 'domain' = '{tags}')")
    except Exception as e:
        print("⚠ tag", t, str(e)[:120])
log_event("proc_silver_gold", "complete", **checks)
