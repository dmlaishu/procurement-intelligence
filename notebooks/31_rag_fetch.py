# Databricks notebook source
# MAGIC %md
# MAGIC # Step 31 - Document Q&A: find and download each country's documents → Volume + `bronze.rag_documents`
# MAGIC
# MAGIC For each country in the `countries` widget (default `KEN,IND`):
# MAGIC 1. **Projects**, in this order: projects whose contracts bought lab or diagnostic equipment (`gold.v_lab_contracts`,
# MAGIC    if Step 23 has run), health projects matching lab keywords in the Projects API, and projects pinned in
# MAGIC    `ops.rag_countries`.
# MAGIC 2. **Documents**: per project, the newest English document of each configured type (appraisal document, project
# MAGIC    papers, procurement plans, supervision report, completion report, IEG review, performance assessment).
# MAGIC 3. **Storage**: `/Volumes/<catalog>/bronze/rag_docs/<country>/<doc type>/<doc id>.pdf`; files already there are
# MAGIC    not downloaded again. The manifest is `bronze.rag_documents` (one row per country and document).
# MAGIC
# MAGIC Same rules as `procurement_rag/fetch.py` in the local prototype.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

import os, re, json, time, datetime, requests
import concurrent.futures as cf
from requests.adapters import HTTPAdapter, Retry

dbutils.widgets.text("countries", "KEN,IND", "Countries (ISO-3, comma-separated)")
COUNTRIES = [c.strip().upper() for c in dbutils.widgets.get("countries").split(",") if c.strip()]
reg = {r.iso3: r for r in spark.sql("SELECT * FROM ops.rag_countries WHERE enabled").collect()}
missing = [c for c in COUNTRIES if c not in reg]
assert not missing, f"not registered: {missing} - add them in 30_rag_registry"

PROJECTS = "https://search.worldbank.org/api/v2/projects"
WDS = "https://search.worldbank.org/api/v2/wds"
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))
session.mount("http://", session.get_adapter("https://"))
ingest_ts = datetime.datetime.utcnow()
D = RAG_DEFAULTS
log_event("rag_fetch", "started", countries=COUNTRIES)


def get_json(url, params):
    r = session.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def project_row(pid, p, source):
    sector = (p.get("sector1") or {}).get("Name") if isinstance(p.get("sector1"), dict) else None
    return {"project_id": pid, "project_name": " ".join((p.get("project_name") or "").split()), "status": p.get("status"),
            "approval_year": (p.get("boardapprovaldate") or "")[:4], "sector": sector, "source": source}


def project_by_id(pid, source):
    d = get_json(PROJECTS, {"format": "json", "id": pid, "fl": "id,project_name,status,boardapprovaldate,sector1"})
    return project_row(pid, next(iter((d.get("projects") or {}).values()), None) or {}, source)


def discover_projects(c):
    rows = []
    if spark.catalog.tableExists("gold.v_lab_contracts"):
        for r in spark.sql(f"""SELECT project_id FROM gold.v_lab_contracts WHERE country_iso3 = '{c.iso3}'
                               GROUP BY 1 ORDER BY sum(amount_usd) DESC""").collect():
            rows.append(project_by_id(r.project_id, "bought lab or diagnostic equipment"))
    health = re.compile(D["health_pattern"], re.I)
    for kw in D["project_keywords"]:
        d = get_json(PROJECTS, {"format": "json", "rows": 100, "countrycode_exact": c.iso2, "qterm": kw,
                                "fl": "id,project_name,status,boardapprovaldate,sector1"})
        for pid, p in (d.get("projects") or {}).items():
            row = project_row(pid, p, f"keyword: {kw}")
            year = int(row["approval_year"]) if row["approval_year"].isdigit() else 0
            if year >= D["min_approval_year"] and row["status"] != "Dropped" and health.search(f"{row['project_name']} {row['sector'] or ''}"):
                rows.append(row)
    first = {}
    for r in rows:                        # keep the first (strongest) reason a project was selected
        first.setdefault(r["project_id"], r)
    rows = list(first.values())[:D["max_projects"]]
    return rows + [project_by_id(pid, "pinned in ops.rag_countries") for pid in (c.extra_projects or [])
                   if pid not in first]


def project_documents(pid):
    d = get_json(WDS, {"format": "json", "rows": 300, "proid": pid, "lang_exact": "English", "srt": "docdt", "order": "desc",
                       "fl": "id,display_title,docdt,docty,projectid,pdfurl,url"})
    docs = [x for k, x in d.get("documents", {}).items() if k != "facets" and x.get("pdfurl")]
    out = []
    for docty, (code, n) in D["doc_types"].items():
        for x in [x for x in docs if x.get("docty") == docty][:n]:
            out.append({"doc_id": x["id"], "doc_type": code, "doc_type_name": docty,
                        "title": " ".join((x.get("display_title") or "").split()), "doc_date": (x.get("docdt") or "")[:10],
                        "project_id": pid, "pdf_url": x["pdfurl"], "landing_url": x.get("url")})
    return out


def download(doc, tries=3):
    path = f"{RAG_DOCS_PATH}/{doc['country']}/{doc['doc_type']}/{doc['doc_id']}.pdf"
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return {**doc, "file_path": path, "size": os.path.getsize(path), "status": "present"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        for attempt in range(tries):
            r = session.get(doc["pdf_url"].replace("http://", "https://", 1), timeout=300)
            if r.status_code != 403 or attempt == tries - 1:
                break
            time.sleep(5 * (attempt + 1))         # the document server throttles bursts: wait and retry
        r.raise_for_status()
        if not r.content.startswith(b"%PDF") or len(r.content) > 60 * 1024 * 1024:
            raise ValueError("not a PDF or too large")
        with open(path, "wb") as f:
            f.write(r.content)
        return {**doc, "file_path": path, "size": len(r.content), "status": "downloaded"}
    except Exception as e:
        return {**doc, "file_path": None, "size": None, "status": f"failed: {str(e)[:150]}"}

# COMMAND ----------

all_docs, summary = [], {}
for iso3 in COUNTRIES:
    c = reg[iso3]
    projects = discover_projects(c)
    info = {p["project_id"]: p for p in projects}
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        docs = [d for lst in pool.map(lambda p: project_documents(p["project_id"]), projects) for d in lst]
    docs = list({d["doc_id"]: d for d in docs}.values())[:D["max_docs"]]
    for d in docs:
        d.update(country=iso3, project_name=info[d["project_id"]]["project_name"], project_source=info[d["project_id"]]["source"])
    with cf.ThreadPoolExecutor(max_workers=3) as pool:       # more parallel downloads get throttled (403)
        docs = list(pool.map(download, docs))
    all_docs += docs
    status = {}
    for d in docs:
        status[d["status"].split(":")[0]] = status.get(d["status"].split(":")[0], 0) + 1
    summary[iso3] = {"projects": len(projects), "documents": len(docs), **status}
    print(iso3, summary[iso3])

# COMMAND ----------

spark.sql("""CREATE TABLE IF NOT EXISTS bronze.rag_documents (
  country STRING NOT NULL, doc_id STRING NOT NULL, doc_type STRING COMMENT 'PAD | PPR | PP | ISR | ICR | ICRR | PPAR',
  doc_type_name STRING, title STRING, doc_date DATE, project_id STRING, project_name STRING,
  project_source STRING COMMENT 'why the project was selected', pdf_url STRING, landing_url STRING,
  file_path STRING COMMENT '/Volumes/.../rag_docs/<country>/<doc type>/<doc id>.pdf', file_size_bytes BIGINT,
  status STRING COMMENT 'downloaded | present | failed: ...', ingest_ts TIMESTAMP)
  COMMENT 'Document Q&A manifest: which World Bank documents were fetched for each country'
  TBLPROPERTIES (delta.enableChangeDataFeed = true)""")
rows = [(d["country"], d["doc_id"], d["doc_type"], d["doc_type_name"], d["title"],
         datetime.date.fromisoformat(d["doc_date"]) if d["doc_date"] else None, d["project_id"], d["project_name"],
         d["project_source"], d["pdf_url"], d["landing_url"], d["file_path"], d["size"], d["status"], ingest_ts) for d in all_docs]
spark.createDataFrame(rows, spark.table("bronze.rag_documents").schema).createOrReplaceTempView("new_rag_docs")
spark.sql("""MERGE INTO bronze.rag_documents t USING new_rag_docs s ON t.country = s.country AND t.doc_id = s.doc_id
             WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")
display(spark.sql("""SELECT country, doc_type, count(*) AS documents, count_if(status NOT LIKE 'failed%') AS stored,
                            count(DISTINCT project_id) AS projects FROM bronze.rag_documents GROUP BY ALL ORDER BY 1, 2"""))
log_event("rag_fetch", "complete", summary=json.dumps(summary))
