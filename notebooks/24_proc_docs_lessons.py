# Databricks notebook source
# MAGIC %md
# MAGIC # Step 24 - Procurement: structure-aware parsing of project reports, lessons and rating evidence
# MAGIC
# MAGIC | Output | What | How |
# MAGIC |---|---|---|
# MAGIC | `silver.proj_doc_pages` | page text **with line breaks kept** | pypdf in a Spark UDF (one PDF per task) |
# MAGIC | `gold.proj_doc_chunks` | section-aware chunks: `section_path`, `section_kind`, `content_type`, page span | `section_chunks()` from `20_proc_lib`: running headers removed, numbered headings → hierarchy, chunks never cross a section, tables / annexes typed |
# MAGIC | `gold.rating_evidence` | dated ratings per project from ICR data sheets (full ISR history), ISRs and PPARs | deterministic parsers, no LLM |
# MAGIC | `gold.project_ratings_resolved` | one resolved rating per project + conflict flag + trajectory | `resolve_rating_conflict()` - completion/independent evaluation outranks mid-term supervision; losing evidence kept |
# MAGIC | `gold.lessons` | atomic lessons with stage, risk category, **verbatim evidence quote**, page | `ai_query` extraction → quote verified against the chunk (`quote_in_source`) → unverified lessons are kept but flagged and never served |
# MAGIC
# MAGIC Why not one big chunker: ICRs are 60-150 pages where the *Lessons* and *Key factors* sections carry most of the
# MAGIC procurement signal, annex tables carry numbers that break embeddings, and every page repeats a header.
# MAGIC Section-aware chunks let retrieval filter by `section_kind` and let citations point to the right section and page.

# COMMAND ----------

# MAGIC %pip install -q "pypdf>=5,<6"
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json
import pandas as pd
from pyspark.sql import functions as F, types as T

spark.sql(f"USE CATALOG {CATALOG}")
PDF_DIR = f"{RAW_DOCS_PATH}/project_docs"
log_event("proc_docs_lessons", "started")

# COMMAND ----------

# MAGIC %md ## 1. Pages (incremental: only new PDFs)

# COMMAND ----------

spark.sql("""CREATE TABLE IF NOT EXISTS silver.proj_doc_pages (doc_id STRING, page_num INT, page_text STRING, parsed_ts TIMESTAMP)
             COMMENT 'Project document text per page, line breaks kept for heading detection'""")

@F.udf("array<struct<page_num:int, page_text:string>>")
def pdf_pages(content):
    import io, re
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(bytes(content)))
    except Exception:
        return []
    out = []
    for i, p in enumerate(reader.pages, start=1):
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        out.append((i, re.sub(r"(\w)-\n(\w)", r"\1\2", t)))
    return out

new_docs = spark.sql("""SELECT doc_id FROM bronze.project_docs_meta WHERE download_status NOT LIKE 'failed%'
                        EXCEPT SELECT DISTINCT doc_id FROM silver.proj_doc_pages""")
n_new = new_docs.count()
print(n_new, "new documents")
if n_new:
    (spark.read.format("binaryFile").load(PDF_DIR)
     .withColumn("doc_id", F.regexp_extract("path", r"([^/]+)\.pdf$", 1)).join(new_docs, "doc_id")
     .repartition(max(4, min(n_new, 32)), "doc_id")
     .select("doc_id", F.explode(pdf_pages("content")).alias("p"))
     .select("doc_id", "p.page_num", "p.page_text", F.current_timestamp().alias("parsed_ts"))
     .write.mode("append").saveAsTable("silver.proj_doc_pages"))

# COMMAND ----------

# MAGIC %md ## 2. Section-aware chunks

# COMMAND ----------

CHUNK_SCHEMA = T.ArrayType(T.StructType([
    T.StructField(n, t) for n, t in (("section_path", T.StringType()), ("section_top", T.StringType()),
                                     ("section_kind", T.StringType()), ("content_type", T.StringType()),
                                     ("page_start", T.IntegerType()), ("page_end", T.IntegerType()),
                                     ("chunk_text", T.StringType()), ("n_words", T.IntegerType()))]))

@F.udf(CHUNK_SCHEMA)
def chunk_doc(pages):
    pages = sorted(((p["page_num"], p["page_text"]) for p in pages), key=lambda x: x[0])
    return [(c["section_path"], c["section_top"], section_kind(c["section_top"]), c["content_type"],
             c["page_start"], c["page_end"], c["text"], c["n_words"]) for c in section_chunks(pages)]

chunks = (spark.table("silver.proj_doc_pages").groupBy("doc_id")
          .agg(F.collect_list(F.struct("page_num", "page_text")).alias("pages"))
          .select("doc_id", F.posexplode(chunk_doc("pages")).alias("k", "c"))
          .join(spark.table("bronze.project_docs_meta"), "doc_id")
          .join(spark.table("silver.projects").select("project_id", "project_name", "major_sector"), "project_id", "left")
          .select(F.concat_ws("_", "doc_id", F.format_string("%04d", "k")).alias("chunk_id"), "doc_id", "doc_type_code",
                  "project_id", "project_name", "major_sector", "country_iso3", "doc_date", "title", "landing_url",
                  "c.*",
                  F.concat_ws(" | ", "doc_type_code", F.coalesce("project_name", "title"), "project_id", "country_iso3",
                              F.coalesce("major_sector", F.lit("")), F.date_format("doc_date", "yyyy-MM"),
                              F.col("c.section_path")).alias("header"))
          .withColumn("search_text", F.concat_ws("\n", "header", "chunk_text")).drop("header"))
chunks.createOrReplaceTempView("new_chunks")
spark.sql("""CREATE TABLE IF NOT EXISTS gold.proj_doc_chunks (
  chunk_id STRING NOT NULL, doc_id STRING, doc_type_code STRING, project_id STRING, project_name STRING, major_sector STRING,
  country_iso3 STRING, doc_date DATE, title STRING, landing_url STRING, section_path STRING, section_top STRING,
  section_kind STRING COMMENT 'lessons | key_factors | procurement_fiduciary | risk | ratings | results | design | annex | other',
  content_type STRING COMMENT 'narrative | table | annex', page_start INT, page_end INT, chunk_text STRING, n_words INT,
  search_text STRING COMMENT 'Header (doc type, project, country, sector, date, section) + text; embedded by Vector Search',
  CONSTRAINT proj_doc_chunks_pk PRIMARY KEY (chunk_id))
  COMMENT 'Section-aware chunks of PAD / ISR / ICR / PPAR / procurement plans. Source of ai.proj_doc_chunks_index.'
  TBLPROPERTIES (delta.enableChangeDataFeed = true)""")
spark.sql("""MERGE INTO gold.proj_doc_chunks t USING new_chunks s ON t.chunk_id = s.chunk_id
             WHEN MATCHED AND t.search_text <> s.search_text THEN UPDATE SET *
             WHEN NOT MATCHED THEN INSERT * WHEN NOT MATCHED BY SOURCE THEN DELETE""")
display(spark.sql("""SELECT doc_type_code, section_kind, content_type, count(*) AS chunks, round(avg(n_words)) AS avg_words
                     FROM gold.proj_doc_chunks GROUP BY ALL ORDER BY 1, 4 DESC"""))

# COMMAND ----------

# MAGIC %md ## 3. Rating evidence and conflict resolution

# COMMAND ----------

docs = spark.sql("""
  SELECT m.doc_id, m.doc_type_code, m.project_id, m.doc_date,
         concat_ws('\n', transform(array_sort(collect_list(struct(p.page_num, p.page_text))), x -> x.page_text)) AS text
  FROM bronze.project_docs_meta m JOIN silver.proj_doc_pages p USING (doc_id)
  WHERE m.doc_type_code IN ('ICR', 'ISR', 'PPAR') AND p.page_num <= 25
  GROUP BY ALL""").toPandas()

evidence, outcomes = [], []
for d in docs.itertuples():
    if d.doc_type_code == "ICR":
        ds = parse_icr_datasheet(d.text)
        evidence += [dict(project_id=d.project_id, dimension="development_objective", source_type="ISR", date=r["date"],
                          rating=r["do_rating"], source_id=f"{d.doc_id}#ISR{r['seq']:02d}", via="ICR data sheet")
                     for r in ds["isr_ratings"]]
        if ds["final_ratings"].get("outcome"):
            evidence.append(dict(project_id=d.project_id, dimension="development_objective", source_type="ICR",
                                 date=d.doc_date, rating=ds["final_ratings"]["outcome"], source_id=d.doc_id,
                                 via="ICR ratings summary"))
        outcomes.append(dict(project_id=d.project_id, doc_id=d.doc_id, restructurings=ds["restructurings"],
                             closing_planned=ds["closing_planned"], closing_actual=ds["closing_actual"],
                             outcome_rating=ds["final_ratings"].get("outcome"),
                             me_quality=ds["final_ratings"].get("me_quality")))
    elif d.doc_type_code == "ISR":
        r = parse_isr_ratings(d.text[:6000])
        if r["do_current"]:
            evidence.append(dict(project_id=d.project_id, dimension="development_objective", source_type="ISR",
                                 date=r["isr_date"] or d.doc_date, rating=r["do_current"], source_id=d.doc_id,
                                 via="ISR overall ratings"))
    elif d.doc_type_code == "PPAR":
        import re
        m = re.search(r"\bOutcome\b[^\n]{0,40}?\b(Highly Satisfactory|Moderately Satisfactory|Satisfactory|Moderately "
                      r"Unsatisfactory|Highly Unsatisfactory|Unsatisfactory)", d.text)
        if m:
            evidence.append(dict(project_id=d.project_id, dimension="development_objective", source_type="PPAR",
                                 date=d.doc_date, rating=m.group(1), source_id=d.doc_id, via="PPAR ratings"))

ev = pd.DataFrame(evidence)
resolved = []
for (pid, dim), g in ev.groupby(["project_id", "dimension"]):
    r = resolve_rating_conflict(g.to_dict("records"))
    worst = min((x[2] for x in r["trajectory"] if x[0] == "ISR"), default=None, key=lambda v: RATING_SCALE.get(v.lower(), 9))
    resolved.append(dict(project_id=pid, dimension=dim, resolved_rating=r["resolved_rating"], resolved_from=r["resolved_from"],
                         resolved_source_id=r["resolved_source_id"], conflict=r["conflict"], spread=r["spread"],
                         worst_isr=worst, n_evidence=len(g), trajectory=json.dumps(r["trajectory"])))
for name, df in (("rating_evidence", ev), ("project_ratings_resolved", pd.DataFrame(resolved)),
                 ("project_outcomes", pd.DataFrame(outcomes))):
    spark.createDataFrame(df.astype(object).where(df.notna(), None)).write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(f"gold.{name}")
display(spark.sql("SELECT conflict, resolved_from, count(*) AS projects FROM gold.project_ratings_resolved GROUP BY ALL"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Lesson extraction with an LLM, then quote verification
# MAGIC `ai_query` reads each *Lessons* / *Key factors* / *Procurement* chunk and returns atomic lessons as JSON, each with a
# MAGIC **verbatim** evidence quote. The quote is checked against the chunk text; a lesson whose quote is not found is
# MAGIC stored with `quote_verified = false` and is never served by the copilot. This is the first anti-hallucination
# MAGIC layer: knowledge enters the system only with a verifiable anchor.

# COMMAND ----------

PROMPT = """You extract procurement lessons from a World Bank project evaluation passage.
Return JSON only: {"lessons": [{"lesson": "<one sentence, actionable>",
 "procurement_stage": "planning|tender_design|evaluation_award|contract_management|general",
 "risk_category": "delay|cost_overrun|low_competition|capacity|specification|fiduciary|safeguards|other",
 "procurement_category": "Works|Goods|Consultant Services|Non-consulting Services|General",
 "evidence_quote": "<copy 10-40 consecutive words EXACTLY from the passage that support the lesson>"}]}
Return {"lessons": []} if the passage contains no lesson. Do not use outside knowledge.
PASSAGE ("""

LESSON_JSON = ("STRUCT<lessons: ARRAY<STRUCT<lesson: STRING, procurement_stage: STRING, risk_category: STRING, "
               "procurement_category: STRING, evidence_quote: STRING>>>")
spark.sql("""CREATE TABLE IF NOT EXISTS gold.lessons (lesson_id STRING, chunk_id STRING, doc_id STRING, project_id STRING,
  country_iso3 STRING, doc_type_code STRING, section_kind STRING, page_start INT, lesson STRING, procurement_stage STRING,
  risk_category STRING, procurement_category STRING, evidence_quote STRING, quote_verified BOOLEAN, procurement_related BOOLEAN,
  extraction_method STRING, title STRING, landing_url STRING, doc_date DATE, extracted_ts TIMESTAMP)
  COMMENT 'Atomic lessons learned with a verbatim evidence quote (quote_verified = quote found in the source chunk)'""")

todo = spark.sql("""
  SELECT c.* FROM gold.proj_doc_chunks c
  LEFT ANTI JOIN (SELECT DISTINCT chunk_id FROM gold.lessons) l USING (chunk_id)
  WHERE c.content_type <> 'table'
    AND (c.section_kind IN ('lessons', 'key_factors', 'procurement_fiduciary')
         OR (c.section_kind = 'risk' AND lower(c.chunk_text) RLIKE 'procure|tender|bid|contract'))
""")
print(todo.count(), "chunks to extract")
todo.createOrReplaceTempView("todo")

extracted = spark.sql(f"""
  SELECT t.*, ai_query('{LLM_ENDPOINT}',
                       concat('{PROMPT.replace("'", "''")}', t.doc_type_code, ', ', t.section_path, '):\\n', t.chunk_text),
                       modelParameters => named_struct('temperature', 0.0, 'max_tokens', 1200),
                       failOnError => false) AS resp
  FROM todo t
""")
verify_udf = F.udf(lambda q, src: bool(quote_in_source(q, src)), "boolean")
lessons = (extracted
           .withColumn("parsed", F.from_json(F.regexp_extract("resp.result", r"(\{[\s\S]*\})", 1), LESSON_JSON))
           .select("*", F.posexplode_outer("parsed.lessons").alias("i", "l"))
           .where("l IS NOT NULL")
           .select(F.concat_ws("_L", "chunk_id", F.col("i").cast("string")).alias("lesson_id"), "chunk_id", "doc_id",
                   "project_id", "country_iso3", "doc_type_code", "section_kind", "page_start", "l.lesson",
                   "l.procurement_stage", "l.risk_category", "l.procurement_category", "l.evidence_quote",
                   verify_udf("l.evidence_quote", "chunk_text").alias("quote_verified"),
                   F.lower("chunk_text").rlike("procure|tender|bid|contract|supplier|award").alias("procurement_related"),
                   F.lit(f"llm:{LLM_ENDPOINT}").alias("extraction_method"), "title", "landing_url", "doc_date",
                   F.current_timestamp().alias("extracted_ts")))
lessons.write.mode("append").saveAsTable("gold.lessons")
display(spark.sql("""SELECT extraction_method, quote_verified, count(*) AS lessons FROM gold.lessons GROUP BY ALL"""))
display(spark.sql("""SELECT risk_category, procurement_stage, lesson, evidence_quote, title, page_start
                     FROM gold.lessons WHERE quote_verified AND procurement_related LIMIT 15"""))

# COMMAND ----------

# MAGIC %md ## 5. Knowledge-graph edges for documents and lessons

# COMMAND ----------

spark.sql("DELETE FROM gold.kg_edges WHERE src_type IN ('LESSON', 'CHUNK')")
spark.sql("""
INSERT INTO gold.kg_edges
SELECT 'CHUNK', chunk_id, 'PART_OF', 'DOCUMENT', doc_id, 'exact_key', 1.0, doc_date, section_path FROM gold.proj_doc_chunks
UNION ALL
SELECT 'LESSON', lesson_id, 'FROM', 'CHUNK', chunk_id, extraction_method, CASE WHEN quote_verified THEN 1.0 ELSE 0.3 END, doc_date, evidence_quote
FROM gold.lessons
UNION ALL
SELECT 'LESSON', lesson_id, 'ABOUT', 'PROJECT', project_id, 'exact_key', 1.0, doc_date, NULL FROM gold.lessons
UNION ALL
SELECT 'LESSON', lesson_id, 'AT_STAGE', 'STAGE', procurement_stage, 'llm_extracted', 0.8, doc_date, NULL FROM gold.lessons
UNION ALL
SELECT 'LESSON', lesson_id, 'APPLIES_TO', 'CATEGORY', procurement_category, 'llm_extracted', 0.8, doc_date, NULL FROM gold.lessons
""")

stats = spark.sql("""SELECT (SELECT count(*) FROM gold.proj_doc_chunks) AS chunks,
                            (SELECT count(*) FROM gold.lessons) AS lessons,
                            (SELECT count_if(quote_verified) FROM gold.lessons) AS lessons_verified,
                            (SELECT count_if(conflict) FROM gold.project_ratings_resolved) AS rating_conflicts""").first().asDict()
print(stats)
log_event("proc_docs_lessons", "complete", **stats)
