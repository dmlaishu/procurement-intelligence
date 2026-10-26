# Databricks notebook source
# MAGIC %md
# MAGIC # Step 32 - Document Q&A: extract, chunk and tag → `silver.rag_pages`, `gold.rag_chunks`
# MAGIC
# MAGIC | Output | What |
# MAGIC |---|---|
# MAGIC | `silver.rag_pages` | text per page, extracted once per PDF (incremental: only PDFs not parsed yet) |
# MAGIC | `gold.rag_chunks` | section-aware passages (`section_chunks` from `20_proc_lib`: never cross a report section, keep section path and pages), one row per country × passage, with a `search_text` (document type, title, section + text) that Vector Search embeds, and a 1/0 column per category (`rag_tags`) for the dropdown filter |
# MAGIC
# MAGIC `gold.rag_chunks` has Change Data Feed on: the Vector Search index (Step 33) syncs only changed rows. Rows of the
# MAGIC processed countries whose document disappeared are deleted; other countries are not touched.

# COMMAND ----------

# MAGIC %pip install -q "pypdf>=5,<6"
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

from pyspark.sql import functions as F, types as T

dbutils.widgets.text("countries", "KEN,IND", "Countries (ISO-3, comma-separated)")
COUNTRIES = [c.strip().upper() for c in dbutils.widgets.get("countries").split(",") if c.strip()]
IN_LIST = ", ".join(f"'{c}'" for c in COUNTRIES)
CAT_COLS = [rag_category_key(n) for n in RAG_CATEGORIES]
log_event("rag_process", "started", countries=COUNTRIES)

# COMMAND ----------

# MAGIC %md ## 1. Pages (only PDFs not parsed yet)

# COMMAND ----------

spark.sql("""CREATE TABLE IF NOT EXISTS silver.rag_pages (doc_id STRING, page_num INT, page_text STRING, parsed_ts TIMESTAMP)
             COMMENT 'Document Q&A: text per page, line breaks kept for heading detection'""")


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
        out.append((i, re.sub(r"[\ud800-\udfff]", "", re.sub(r"(\w)-\n(\w)", r"\1\2", t))))
    return out


todo = spark.sql(f"""SELECT DISTINCT doc_id, file_path FROM bronze.rag_documents
                     WHERE country IN ({IN_LIST}) AND file_path IS NOT NULL
                       AND doc_id NOT IN (SELECT DISTINCT doc_id FROM silver.rag_pages)""").collect()
print(len(todo), "new PDFs to parse")
if todo:
    (spark.read.format("binaryFile").load([r.file_path for r in todo])
     .withColumn("doc_id", F.regexp_extract("path", r"([^/]+)\.pdf$", 1))
     .repartition(max(4, min(len(todo), 32)), "doc_id")
     .select("doc_id", F.explode(pdf_pages("content")).alias("p"))
     .select("doc_id", "p.page_num", "p.page_text", F.current_timestamp().alias("parsed_ts"))
     .write.mode("append").saveAsTable("silver.rag_pages"))

# COMMAND ----------

# MAGIC %md ## 2. Section-aware passages with category tags

# COMMAND ----------

CHUNK = T.ArrayType(T.StructType([T.StructField(n, t) for n, t in (
    ("section", T.StringType()), ("section_kind", T.StringType()), ("content_type", T.StringType()),
    ("page_start", T.IntegerType()), ("page_end", T.IntegerType()), ("n_words", T.IntegerType()), ("text", T.StringType()),
    *[(c, T.IntegerType()) for c in CAT_COLS])]))


@F.udf(CHUNK)
def chunk_doc(pages):
    pages = sorted(((p["page_num"], p["page_text"]) for p in pages), key=lambda x: x[0])
    out = []
    for c in section_chunks(pages):
        text = " ".join(c["text"].split())
        tags = rag_tags(text)
        out.append((c["section_path"][:200], section_kind(c["section_path"]), c["content_type"], int(c["page_start"]),
                    int(c["page_end"]), c["n_words"], text, *[tags[k] for k in CAT_COLS]))
    return out


docs = spark.sql(f"""SELECT country, doc_id, doc_type, title, doc_date, project_id, project_name,
                            coalesce(landing_url, pdf_url) AS url
                     FROM bronze.rag_documents WHERE country IN ({IN_LIST}) AND file_path IS NOT NULL""")
chunks = (spark.table("silver.rag_pages").groupBy("doc_id")
          .agg(F.collect_list(F.struct("page_num", "page_text")).alias("pages"))
          .select("doc_id", F.posexplode(chunk_doc("pages")).alias("k", "c"))
          .join(docs, "doc_id")
          .select(F.concat_ws("_", "country", "doc_id", F.format_string("%04d", "k")).alias("chunk_id"),
                  "country", "doc_id", "doc_type", "title", "doc_date", "project_id", "project_name", "url", "c.*")
          .withColumn("search_text", F.concat_ws("\n", F.concat_ws(" | ", "doc_type", "title", "section"), "text")))
chunks.createOrReplaceTempView("new_rag_chunks")

spark.sql(f"""CREATE TABLE IF NOT EXISTS gold.rag_chunks (
  chunk_id STRING NOT NULL, country STRING, doc_id STRING, doc_type STRING, title STRING, doc_date DATE, project_id STRING,
  project_name STRING, url STRING, section STRING, section_kind STRING, content_type STRING COMMENT 'narrative | table | annex',
  page_start INT, page_end INT, n_words INT, text STRING,
  {", ".join(f"{c} INT" for c in CAT_COLS)},
  search_text STRING COMMENT 'document type | title | section + text: embedded by Vector Search',
  CONSTRAINT rag_chunks_pk PRIMARY KEY (chunk_id))
  COMMENT 'Document Q&A passages per country, with 1/0 category columns for filtering. Source of ai.rag_chunks_index.'
  TBLPROPERTIES (delta.enableChangeDataFeed = true)""")
spark.sql(f"""MERGE INTO gold.rag_chunks t USING new_rag_chunks s ON t.chunk_id = s.chunk_id
              WHEN MATCHED AND (t.search_text <> s.search_text OR {" OR ".join(f"t.{c} <> s.{c}" for c in CAT_COLS)})
                THEN UPDATE SET *
              WHEN NOT MATCHED THEN INSERT *
              WHEN NOT MATCHED BY SOURCE AND t.country IN ({IN_LIST}) THEN DELETE""")

# COMMAND ----------

stats = spark.sql(f"""SELECT country, count(DISTINCT doc_id) AS documents, count(*) AS passages,
                             {", ".join(f"sum({c}) AS {c}" for c in CAT_COLS)}
                      FROM gold.rag_chunks GROUP BY 1 ORDER BY 1""")
display(stats)
log_event("rag_process", "complete", passages={r.country: r.passages for r in stats.collect()})
