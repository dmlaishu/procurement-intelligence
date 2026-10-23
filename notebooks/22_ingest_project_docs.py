# Databricks notebook source
# MAGIC %md
# MAGIC # Step 22 - Procurement: ingest project documents → Bronze (Volume + metadata)
# MAGIC
# MAGIC The *why* behind the transactions: project documents from the Documents & Reports API, stored as PDFs in the
# MAGIC governed volume and keyed by **Project ID** so they join to contracts, notices and projects.
# MAGIC
# MAGIC | Code | Document | Lifecycle role |
# MAGIC |---|---|---|
# MAGIC | `PAD` | Project Appraisal Document | design rationale, procurement arrangements, initial risk ratings |
# MAGIC | `PROC_PLAN` | Procurement Plan | planned packages, methods, estimated dates |
# MAGIC | `ISR` | Implementation Status and Results Report | periodic supervision ratings and roadblocks |
# MAGIC | `ICR` | Implementation Completion and Results Report | retrospective: what went wrong/right, lessons learned |
# MAGIC | `PPAR` | IEG Project Performance Assessment Report | independent evaluation |
# MAGIC
# MAGIC **Selection:** for each country and type, documents of projects that **have contract awards** come first (so the
# MAGIC knowledge graph has evidence on both sides), then the newest. Demo scale is set by `PROC_DOCS_PER_TYPE` in `00_config`.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

import os, json, datetime, requests
import concurrent.futures as cf
from requests.adapters import HTTPAdapter, Retry

spark.sql(f"USE CATALOG {CATALOG}")
DOCS_API = "https://search.worldbank.org/api/v2/wds"
PDF_DIR = f"{RAW_DOCS_PATH}/project_docs"
MAX_PDF_MB = 60
os.makedirs(PDF_DIR, exist_ok=True)

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))
session.mount("http://", session.get_adapter("https://"))
ingest_ts = datetime.datetime.utcnow()
log_event("ingest_project_docs", "started", per_type=PROC_DOCS_PER_TYPE)

# Projects with at least one contract award (from Step 21) - preferred so documents link to transactions
awarded_projects = {r.pid for r in spark.sql(
    "SELECT DISTINCT get_json_object(raw_json, '$.project_id') AS pid FROM bronze.contract_awards_raw").collect()}
print(f"{len(awarded_projects)} projects with contract awards")

# COMMAND ----------

def candidates(iso3, docty, code):
    iso2 = PROC_COUNTRIES[iso3][0]
    resp = session.get(DOCS_API, timeout=120, params={
        "format": "json", "rows": 100, "docty_exact": docty, "countrycode_exact": iso2, "lang_exact": "English",
        "srt": "docdt", "order": "desc", "fl": "id,display_title,docdt,docty,projectid,pdfurl,url,abstracts"})
    resp.raise_for_status()
    docs = [d for k, d in resp.json().get("documents", {}).items() if k != "facets" and d.get("pdfurl")]
    for d in docs:
        d["project_ids"] = [p.strip() for p in (d.get("projectid") or "").split(",") if p.strip()]
    # One document per project for ISRs (newest), then: linked to awards first, newest first
    if code == "ISR":
        seen, uniq = set(), []
        for d in docs:
            key = tuple(d["project_ids"])
            if key not in seen:
                seen.add(key)
                uniq.append(d)
        docs = uniq
    linked = [d for d in docs if any(p in awarded_projects for p in d["project_ids"])]
    rest = sorted([d for d in docs if d not in linked], key=lambda d: d.get("docdt", ""), reverse=True)
    linked.sort(key=lambda d: d.get("docdt", ""), reverse=True)
    picked = (linked + rest)[:PROC_DOCS_PER_TYPE[code]]
    return [dict(doc=d, iso3=iso3, code=code) for d in picked]

jobs = [(iso3, docty, code) for iso3 in PROC_COUNTRIES for docty, code in PROC_DOC_TYPES.items()]
picked, errors = [], {}
with cf.ThreadPoolExecutor(max_workers=6) as pool:
    futs = {pool.submit(candidates, *j): j for j in jobs}
    for f in cf.as_completed(futs):
        try:
            picked += f.result()
        except Exception as e:
            errors[str(futs[f][::2])] = str(e)[:200]
picked = list({c["doc"]["id"]: c for c in picked}.values())
print(f"{len(picked)} documents selected; errors: {errors}")

# COMMAND ----------

def download(c):
    d = c["doc"]
    path = f"{PDF_DIR}/{d['id']}.pdf"
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return {**c, "file_path": path, "size": os.path.getsize(path), "status": "already_present"}
    try:
        with session.get(d["pdfurl"].replace("http://", "https://", 1), timeout=300) as r:
            r.raise_for_status()
            body = r.content
        if not body.startswith(b"%PDF"):
            raise ValueError("not a PDF")
        if len(body) > MAX_PDF_MB * 1024 * 1024:
            raise ValueError(f"larger than {MAX_PDF_MB} MB")
        with open(path, "wb") as f:
            f.write(body)
        return {**c, "file_path": path, "size": len(body), "status": "downloaded"}
    except Exception as e:
        return {**c, "file_path": None, "size": None, "status": f"failed: {str(e)[:150]}"}

with cf.ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(download, picked))

rows = []
for r in results:
    d = r["doc"]
    rows.append((d["id"], r["code"], d.get("docty"), " ".join(d.get("display_title", "").split()),
                 datetime.date.fromisoformat(d["docdt"][:10]) if d.get("docdt") else None,
                 r["iso3"], d["project_ids"], d["project_ids"][0] if d["project_ids"] else None,
                 " ".join(((d.get("abstracts") or {}).get("cdata!") or "").split()) or None,
                 d["pdfurl"], d.get("url"), r["file_path"], r["size"], r["status"], ingest_ts))

spark.sql("""
  CREATE TABLE IF NOT EXISTS bronze.project_docs_meta (
    doc_id          STRING COMMENT 'Documents & Reports id (also the PDF file name)',
    doc_type_code   STRING COMMENT 'PAD | PROC_PLAN | ISR | ICR | PPAR',
    doc_type        STRING COMMENT 'Full document type from the API',
    title           STRING COMMENT 'Document title',
    doc_date        DATE   COMMENT 'Document date',
    country_iso3    STRING COMMENT 'Country (ISO-3)',
    project_ids     ARRAY<STRING> COMMENT 'All project ids the document covers (PPARs can cover several)',
    project_id      STRING COMMENT 'Primary project id (first in the list)',
    abstract        STRING COMMENT 'Abstract from the API (ICR abstracts state the ratings and lessons)',
    pdf_url         STRING COMMENT 'Source PDF',
    landing_url     STRING COMMENT 'Document page used in citations',
    file_path       STRING COMMENT 'PDF in the governed volume',
    file_size_bytes BIGINT COMMENT 'PDF size',
    download_status STRING COMMENT 'downloaded | already_present | failed: ...',
    ingest_ts       TIMESTAMP COMMENT 'Ingestion run time (UTC)'
  ) COMMENT 'Project documents (PAD, procurement plan, ISR, ICR, PPAR) linked to projects by Project ID'
  TBLPROPERTIES (delta.enableChangeDataFeed = true)
""")
spark.createDataFrame(rows, spark.table("bronze.project_docs_meta").schema).createOrReplaceTempView("new_docs")
spark.sql("""MERGE INTO bronze.project_docs_meta t USING new_docs s ON t.doc_id = s.doc_id
             WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")

# COMMAND ----------

display(spark.sql("""
  WITH ap AS (SELECT collect_set(get_json_object(raw_json, '$.project_id')) AS ids FROM bronze.contract_awards_raw)
  SELECT m.country_iso3, m.doc_type_code, count(*) AS docs,
         count_if(m.download_status NOT LIKE 'failed%') AS ok,
         count_if(arrays_overlap(m.project_ids, ap.ids)) AS linked_to_awards,
         round(sum(m.file_size_bytes) / 1e6, 1) AS mb
  FROM bronze.project_docs_meta m CROSS JOIN ap
  GROUP BY ALL ORDER BY 1, 2
"""))

status = {}
for r in results:
    k = r["status"].split(":")[0]
    status[k] = status.get(k, 0) + 1
log_event("ingest_project_docs", "complete", selected=len(picked), status=status, errors=errors)
