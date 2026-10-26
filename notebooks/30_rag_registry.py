# Databricks notebook source
# MAGIC %md
# MAGIC # Step 30 - Document Q&A: the country registry
# MAGIC
# MAGIC The document Q&A pipeline (30-34) works country by country. Which countries exist is data, not code: one row per
# MAGIC country in `ops.rag_countries` (ISO codes, name, pinned projects, enabled). Everything else (which projects,
# MAGIC which document types, how many) comes from `RAG_DEFAULTS` in `00_proc_config`.
# MAGIC
# MAGIC | Widget | Use |
# MAGIC |---|---|
# MAGIC | `add_country` | ISO-3 code of a country to add (e.g. `NGA`); its ISO-2 code and name come from the World Bank country list |
# MAGIC | `pin_projects` | optional comma-separated project ids to always include for that country (e.g. regional programmes) |
# MAGIC
# MAGIC Then build only that country: run 31, 32 and 33 with `countries = NGA` (or the `proc_rag_pipeline` job with that
# MAGIC parameter). Countries already built are not touched.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

import datetime, requests

dbutils.widgets.text("add_country", "", "Add country (ISO-3)")
dbutils.widgets.text("pin_projects", "", "Pinned project ids (comma-separated)")
log_event("rag_registry", "started")

spark.sql("""CREATE TABLE IF NOT EXISTS ops.rag_countries (
  iso3 STRING NOT NULL, iso2 STRING, country_name STRING, extra_projects ARRAY<STRING>, enabled BOOLEAN, added_ts TIMESTAMP)
  COMMENT 'Countries of the document Q&A pipeline; one row per country, shared defaults in 00_proc_config'""")


def upsert(rows):
    spark.createDataFrame(rows, "iso3 string, iso2 string, country_name string, extra_projects array<string>, "
                                "enabled boolean, added_ts timestamp").createOrReplaceTempView("new_countries")
    spark.sql("""MERGE INTO ops.rag_countries t USING new_countries s ON t.iso3 = s.iso3
                 WHEN MATCHED THEN UPDATE SET t.extra_projects = array_distinct(concat(t.extra_projects, s.extra_projects)),
                                              t.enabled = true
                 WHEN NOT MATCHED THEN INSERT *""")


now = datetime.datetime.utcnow()
upsert([(i3, i2, name, pins, True, now) for i3, i2, name, pins in RAG_SEED_COUNTRIES])

# COMMAND ----------

new = dbutils.widgets.get("add_country").strip().upper()
if new:
    d = requests.get(f"https://api.worldbank.org/v2/country/{new}", params={"format": "json"}, timeout=60).json()
    if len(d) < 2 or not d[1]:
        raise ValueError(f"{new} is not a World Bank country code")
    c = d[1][0]
    pins = [p.strip().upper() for p in dbutils.widgets.get("pin_projects").split(",") if p.strip()]
    upsert([(new, c["iso2Code"], c["name"].strip(), pins, True, now)])
    print(f"registered {new} ({c['name'].strip()}): now run 31-33 with countries = {new}")
    log_event("rag_registry", "country_added", iso3=new)

display(spark.sql("""SELECT r.*, coalesce(c.chunks, 0) AS indexed_passages, coalesce(c.documents, 0) AS documents
                     FROM ops.rag_countries r
                     LEFT JOIN (SELECT country, count(*) AS chunks, count(DISTINCT doc_id) AS documents
                                FROM gold.rag_chunks GROUP BY 1) c ON c.country = r.iso3
                     ORDER BY added_ts""") if spark.catalog.tableExists("gold.rag_chunks")
        else spark.table("ops.rag_countries"))
