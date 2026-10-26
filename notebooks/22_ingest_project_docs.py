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
# MAGIC | `PP` | Procurement Plan | planned packages, methods, estimated dates |
# MAGIC | `ISR` | Implementation Status and Results Report | periodic supervision ratings and roadblocks |
# MAGIC | `ICR` | Implementation Completion and Results Report | retrospective: what went wrong or right, lessons learned |
# MAGIC | `ICRR` | IEG ICR Review | independent validation of the ICR rating |
# MAGIC | `PPAR` | IEG Project Performance Assessment Report | independent evaluation |
# MAGIC
# MAGIC **Selection (per project, newest document of each type):** the `PROC_DOC_TOP_SPEND` domain projects with the
# MAGIC highest lab-equipment spend (mostly still active), plus the highest-spend **closed** domain projects up to
# MAGIC `PROC_MAX_DOC_PROJECTS`, because closed projects are the ones with completion reports, IEG reviews and lessons.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

import os, json, datetime, requests
import concurrent.futures as cf
from requests.adapters import HTTPAdapter, Retry

DOCS_API = "https://search.worldbank.org/api/v2/wds"
PDF_DIR = f"{RAW_DOCS_PATH}/project_docs"
MAX_PDF_MB = 60
os.makedirs(PDF_DIR, exist_ok=True)

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))
session.mount("http://", session.get_adapter("https://"))
ingest_ts = datetime.datetime.utcnow()
log_event("ingest_project_docs", "started", top_spend=PROC_DOC_TOP_SPEND, max_projects=PROC_MAX_DOC_PROJECTS)

projects = spark.sql("""
  SELECT d.project_id, d.country_iso3, d.domain_spend_usd, d.spend_rank, get_json_object(p.raw_json, '$.status') AS status
  FROM bronze.domain_projects d LEFT JOIN bronze.projects_raw p USING (project_id)
  ORDER BY d.spend_rank""").toPandas()
top = projects.project_id.head(PROC_DOC_TOP_SPEND).tolist()
closed = projects[projects.status == "Closed"].project_id.tolist()
order = list(dict.fromkeys(top + closed[:PROC_MAX_DOC_PROJECTS - PROC_DOC_TOP_SPEND]))
iso3 = dict(zip(projects.project_id, projects.country_iso3))
print(f"{len(order)} projects selected for documents ({len(top)} by spend, {len(order) - len(top)} closed)")

# COMMAND ----------

def documents_for(pid):
    """Newest English document of each type for one project."""
    resp = session.get(DOCS_API, timeout=120, params={
        "format": "json", "rows": 200, "proid": pid, "lang_exact": "English", "srt": "docdt", "order": "desc",
        "fl": "id,display_title,docdt,docty,projectid,pdfurl,url,abstracts"})
    resp.raise_for_status()
    docs = [d for k, d in resp.json().get("documents", {}).items() if k != "facets" and d.get("pdfurl")]
    picked = []
    for docty, code in PROC_DOC_TYPES.items():
        hit = next((d for d in docs if d.get("docty") == docty), None)
        if hit:
            hit["project_ids"] = [p.strip() for p in (hit.get("projectid") or "").split(",") if p.strip()] or [pid]
            picked.append(dict(doc=hit, iso3=iso3.get(pid), code=code, pid=pid))
    return picked

picked, errors = [], {}
with cf.ThreadPoolExecutor(max_workers=6) as pool:
    futs = {pool.submit(documents_for, pid): pid for pid in order}
    for f in cf.as_completed(futs):
        try:
            picked += f.result()
        except Exception as e:
            errors[futs[f]] = str(e)[:200]
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
                 r["iso3"], d["project_ids"], r["pid"],
                 " ".join(((d.get("abstracts") or {}).get("cdata!") or "").split()) or None,
                 d["pdfurl"], d.get("url"), r["file_path"], r["size"], r["status"], ingest_ts))

spark.sql("""
  CREATE TABLE IF NOT EXISTS bronze.project_docs_meta (
    doc_id          STRING COMMENT 'Documents & Reports id (also the PDF file name)',
    doc_type_code   STRING COMMENT 'PAD | PP | ISR | ICR | ICRR | PPAR',
    doc_type        STRING COMMENT 'Full document type from the API',
    title           STRING COMMENT 'Document title',
    doc_date        DATE   COMMENT 'Document date',
    country_iso3    STRING COMMENT 'Main borrower country of the project (ISO-3)',
    project_ids     ARRAY<STRING> COMMENT 'All project ids the document covers (PPARs can cover several)',
    project_id      STRING COMMENT 'The domain project the document was selected for',
    abstract        STRING COMMENT 'Abstract from the API (ICR abstracts state the ratings and lessons)',
    pdf_url         STRING COMMENT 'Source PDF',
    landing_url     STRING COMMENT 'Document page used in citations',
    file_path       STRING COMMENT 'PDF in the governed volume',
    file_size_bytes BIGINT COMMENT 'PDF size',
    download_status STRING COMMENT 'downloaded | already_present | failed: ...',
    ingest_ts       TIMESTAMP COMMENT 'Ingestion run time (UTC)'
  ) COMMENT 'Project documents (PAD, procurement plan, ISR, ICR, ICR Review, PPAR) linked to projects by Project ID'
  TBLPROPERTIES (delta.enableChangeDataFeed = true)
""")
spark.createDataFrame(rows, spark.table("bronze.project_docs_meta").schema).createOrReplaceTempView("new_docs")
spark.sql("""MERGE INTO bronze.project_docs_meta t USING new_docs s ON t.doc_id = s.doc_id
             WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")

# COMMAND ----------

display(spark.sql("""
  SELECT doc_type_code, count(*) AS docs, count_if(download_status NOT LIKE 'failed%') AS ok,
         count(DISTINCT project_id) AS projects, round(sum(file_size_bytes) / 1e6, 1) AS mb
  FROM bronze.project_docs_meta GROUP BY ALL ORDER BY 1
"""))

status = {}
for r in results:
    k = r["status"].split(":")[0]
    status[k] = status.get(k, 0) + 1
log_event("ingest_project_docs", "complete", selected=len(picked), status=status, errors=errors)
