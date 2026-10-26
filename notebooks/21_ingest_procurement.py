# Databricks notebook source
# MAGIC %md
# MAGIC # Step 21 - Procurement: contract-first ingestion → Bronze
# MAGIC
# MAGIC The domain is defined by **what was bought**, not by a country list:
# MAGIC 1. Pull every Major Contract Award of the Health practice in the Goods category (FY2015+).
# MAGIC 2. Classify each contract description into the equipment taxonomy (`classify_item`, `20_proc_lib`).
# MAGIC 3. Projects that bought at least one lab or diagnostic item are the **domain projects**, ranked by domain spend.
# MAGIC    Their full Goods award history is kept, so models and market context see the whole health-goods picture.
# MAGIC 4. For the domain projects: project records, Goods procurement notices (top projects by spend) and the
# MAGIC    governance scores of their countries.
# MAGIC
# MAGIC | Source (public World Bank API) | Bronze table |
# MAGIC |---|---|
# MAGIC | Major Contract Awards (FinancesOne `DS00005`) | `bronze.contract_awards_raw` |
# MAGIC | Projects & Operations (`/v2/projects`, by project id) | `bronze.projects_raw` |
# MAGIC | Procurement notices (`/v2/procnotices`, by project id) | `bronze.proc_notices_raw` |
# MAGIC | CPIA + Worldwide Governance Indicators | `bronze.governance_raw` |
# MAGIC | Country list (ISO codes, region) | `bronze.country_ref` |
# MAGIC | Domain projects and their lab-equipment spend | `bronze.domain_projects` |
# MAGIC
# MAGIC Bronze keeps each record **exactly as received** (`raw_json`). Tables are written with `MERGE` on a natural key,
# MAGIC so re-runs are idempotent and only add new or changed records.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json, time, datetime, random, requests
import concurrent.futures as cf
from collections import Counter, defaultdict
from requests.adapters import HTTPAdapter, Retry

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])))
ingest_ts = datetime.datetime.utcnow()
log_event("ingest_procurement", "started", scope=PROC_SCOPE_LABEL)


def get_json(url, params, tries=8):
    """GET with backoff on 429 (FinancesOne allows only a few calls per minute and says how long to wait)."""
    for attempt in range(tries):
        r = session.get(url, params=params, timeout=180)
        if r.status_code == 429:
            wait = 35
            try:
                wait = int("".join(ch for ch in r.json().get("message", "") if ch.isdigit()) or 35) + 2
            except Exception:
                pass
            time.sleep(min(wait, 90) + random.random())
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited too often: {url}")


def merge_raw(rows, table, key_cols, ddl):
    """Create the bronze table if needed and MERGE rows (list of tuples matching ddl) on key_cols."""
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({ddl}) TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    if not rows:
        return 0
    df = spark.createDataFrame(rows, spark.table(table).schema).dropDuplicates(key_cols)
    df.createOrReplaceTempView("incoming")
    on = " AND ".join(f"t.{k} <=> s.{k}" for k in key_cols)
    spark.sql(f"""MERGE INTO {table} t USING incoming s ON {on}
                  WHEN MATCHED AND t.raw_json <> s.raw_json THEN UPDATE SET *
                  WHEN NOT MATCHED THEN INSERT *""")
    return df.count()

# COMMAND ----------

# MAGIC %md ## 1. Country reference (ISO-2 → ISO-3, name, region)

# COMMAND ----------

d = get_json("https://api.worldbank.org/v2/country", {"format": "json", "per_page": 400})
ref = {c["iso2Code"]: {"iso3": c["id"], "name": c["name"].strip(), "region": c["region"]["value"].strip()}
       for c in d[1] if c["region"]["value"].strip() != "Aggregates"}
spark.createDataFrame([(k, v["iso3"], v["name"], v["region"]) for k, v in ref.items()],
                      "iso2 string, iso3 string, country_name string, region string") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("bronze.country_ref")
print(len(ref), "economies")

# COMMAND ----------

# MAGIC %md ## 2. Major Contract Awards: every Health Goods award, classified

# COMMAND ----------

FONE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
all_awards, skip = [], 0
while True:
    d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": 1000, "skip": skip,
                        "filter": f"{PROC_CONTRACT_FILTER} and fiscal_year >= {PROC_MIN_YEAR}"})
    batch = d.get("data", [])
    all_awards += batch
    skip += 1000
    print(f"awards {len(all_awards):,} / {d.get('count', 0):,}")
    if len(batch) < 1000 or skip >= d.get("count", 0):
        break
    time.sleep(1.5)
all_awards = [r for r in all_awards if int(r.get("fiscal_year") or 0) >= PROC_MIN_YEAR]

# Domain projects: bought at least one lab or diagnostic item. Ranked by domain spend.
spend, n_dom, borrowers = defaultdict(float), Counter(), defaultdict(list)
for r in all_awards:
    pid = r.get("project_id")
    borrowers[pid].append((ref.get(r.get("borrower_country_code")) or {}).get("iso3"))
    if classify_item(r.get("contract_description"), "Goods") in LAB_DOMAIN_ITEMS:
        spend[pid] += float(r.get("supplier_contract_amount_usd") or 0)
        n_dom[pid] += 1
ranked = sorted(spend, key=lambda k: -spend[k])
# a project's country = the borrower country of most of its contracts (regional projects have none: NULL)
proj_iso3 = {pid: (Counter(v).most_common(1)[0][0]) for pid, v in borrowers.items()}
award_rows = [r for r in all_awards if r.get("project_id") in spend]
print(f"Health Goods awards: {len(all_awards):,}; domain projects: {len(ranked)}; their awards: {len(award_rows):,}")

spark.createDataFrame([(pid, proj_iso3.get(pid), float(spend[pid]), int(n_dom[pid]), i + 1) for i, pid in enumerate(ranked)],
                      "project_id string, country_iso3 string, domain_spend_usd double, domain_contracts int, spend_rank int") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("bronze.domain_projects")

# One contract can have several supplier rows (JVs): wb_contract_number + supplier is the grain
n_awards = merge_raw(
    [(str(r.get("wb_contract_number")), str(r.get("supplier_id") or r.get("supplier")),
      (ref.get(r.get("borrower_country_code")) or {}).get("iso3"), json.dumps(r, sort_keys=True), ingest_ts) for r in award_rows],
    "bronze.contract_awards_raw", ["wb_contract_number", "supplier_key"], """
    wb_contract_number STRING COMMENT 'World Bank contract number',
    supplier_key       STRING COMMENT 'Supplier id from the dataset (or name when missing)',
    country_iso3       STRING COMMENT 'Borrower country (ISO-3); NULL for regional borrowers',
    raw_json           STRING COMMENT 'Record exactly as returned by FinancesOne DS00005',
    ingest_ts          TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
print("merged", n_awards)

# COMMAND ----------

# MAGIC %md ## 3. Projects & Operations (by project id)

# COMMAND ----------

PROJECTS = "https://search.worldbank.org/api/v2/projects"
PFIELDS = ("id,project_name,countryshortname,countrycode,regionname,boardapprovaldate,closingdate,status,lendinginstr,"
           "totalcommamt,totalamt,curr_total_commitment,envassesmentcategorycode,esrc_ovrl_risk_rate,sector1,sector,"
           "mjsector_namecode,theme1,impagency,borrower,project_abstract,pdo,teamleadname,url")
errors = {}


def fetch_project(pid):
    d = get_json(PROJECTS, {"format": "json", "id": pid, "fl": PFIELDS})
    p = next(iter((d.get("projects") or {}).values()), None) or {"id": pid}
    return (pid, proj_iso3.get(pid), json.dumps(p, sort_keys=True), ingest_ts)

project_rows = []
with cf.ThreadPoolExecutor(max_workers=6) as pool:
    for pid, fut in [(pid, pool.submit(fetch_project, pid)) for pid in ranked]:
        try:
            project_rows.append(fut.result())
        except Exception as e:
            errors[f"project_{pid}"] = str(e)[:200]
n_projects = merge_raw(project_rows, "bronze.projects_raw", ["project_id"], """
    project_id   STRING COMMENT 'World Bank project id (P-code)',
    country_iso3 STRING COMMENT 'Main borrower country of the project contracts (ISO-3)',
    raw_json     STRING COMMENT 'Project record from the Projects API',
    ingest_ts    TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
print("projects merged", n_projects)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Goods procurement notices of the top domain projects
# MAGIC Invitations for Bids and REOIs open a tender; Contract Award notices close it and keep their HTML body, which
# MAGIC lists every bidder, evaluated price and rejection reason.

# COMMAND ----------

NOTICES = "https://search.worldbank.org/api/v2/procnotices"
FIELDS = ("id,notice_type,noticedate,notice_status,project_id,project_name,project_ctry_name,bid_reference_no,"
          "bid_description,procurement_group,procurement_method_code,procurement_method_name,"
          "submission_deadline_date,submission_date,contact_organization")


def fetch_notices(pid, notice_type):
    fl = FIELDS + (",notice_text" if notice_type == "Contract Award" else "")
    rows, os_ = [], 0
    while True:
        d = get_json(NOTICES, {"format": "json", "rows": 500, "os": os_, "project_id": pid, "notice_type_exact": notice_type,
                               "srt": "noticedate", "order": "desc", "fl": fl})
        batch = d.get("procnotices", [])
        rows += [(n["id"], proj_iso3.get(pid), notice_type, json.dumps(n, sort_keys=True), ingest_ts) for n in batch
                 if n.get("project_id") == pid and n.get("procurement_group") in ("GO", None)]
        os_ += 500
        if len(batch) < 500:
            return rows

top = ranked[:PROC_MAX_PROJECTS]
jobs = [(pid, t) for pid in top for t in NOTICE_TYPES]
notice_rows = []
with cf.ThreadPoolExecutor(max_workers=4) as pool:
    futs = {pool.submit(fetch_notices, *j): j for j in jobs}
    for f in cf.as_completed(futs):
        try:
            notice_rows += f.result()
        except Exception as e:
            errors[f"notices_{futs[f][0]}_{futs[f][1]}"] = str(e)[:200]
n_notices = merge_raw(notice_rows, "bronze.proc_notices_raw", ["notice_id"], """
    notice_id    STRING COMMENT 'Procurement notice id (OP...)',
    country_iso3 STRING COMMENT 'Main borrower country of the project (ISO-3)',
    notice_type  STRING COMMENT 'Invitation for Bids | Request for Expression of Interest | Contract Award',
    raw_json     STRING COMMENT 'Notice as returned by the procnotices API (Contract Awards include the HTML body)',
    ingest_ts    TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
print(f"Goods notices for the top {len(top)} domain projects: {n_notices:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Governance context: CPIA and WGI for the domain countries
# MAGIC CPIA is only published for IDA-eligible countries: the gap is kept as NULL and handled explicitly in the models
# MAGIC (missing-indicator flag), not imputed here.

# COMMAND ----------

iso3s = sorted({i for i in proj_iso3.values() if i})
gov_rows = []
for code, (source, col) in GOV_INDICATORS.items():
    for k in range(0, len(iso3s), 40):           # keep the URL short
        try:
            d = get_json(f"https://api.worldbank.org/v2/country/{';'.join(iso3s[k:k + 40])}/indicator/{code}",
                         {"format": "json", "source": source, "date": f"{PROC_MIN_YEAR - 5}:{datetime.date.today().year}",
                          "per_page": 5000})
            for r in (d[1] if len(d) > 1 and d[1] else []):
                gov_rows.append((r["countryiso3code"], code, int(r["date"]), json.dumps(r, sort_keys=True), ingest_ts))
        except Exception as e:
            errors[f"gov_{code}_{k}"] = str(e)[:200]
n_gov = merge_raw(gov_rows, "bronze.governance_raw", ["country_iso3", "indicator_code", "year"], """
    country_iso3   STRING COMMENT 'ISO-3 country code',
    indicator_code STRING COMMENT 'CPIA (IQ.CPA.*) or WGI (GOV_WGI_*) indicator code',
    year           INT    COMMENT 'Year of the score',
    raw_json       STRING COMMENT 'Observation as returned by the Indicators API (value may be null)',
    ingest_ts      TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")

# COMMAND ----------

# MAGIC %md ## 6. Checks

# COMMAND ----------

summary = spark.sql("""
  SELECT 'contract_awards_raw' AS tbl, count(*) AS n FROM bronze.contract_awards_raw
  UNION ALL SELECT 'domain_projects', count(*) FROM bronze.domain_projects
  UNION ALL SELECT 'projects_raw', count(*) FROM bronze.projects_raw
  UNION ALL SELECT 'proc_notices_raw', count(*) FROM bronze.proc_notices_raw
  UNION ALL SELECT 'governance_raw', count(*) FROM bronze.governance_raw
""")
display(summary)
counts = {r.tbl: r.n for r in summary.collect()}
for t in ("contract_awards_raw", "domain_projects", "projects_raw", "proc_notices_raw"):
    assert counts[t] > 0, f"bronze.{t} is empty - check errors: {errors}"
if errors:
    print("⚠ partial failures (a re-run picks them up):", json.dumps(errors, indent=1)[:2000])
log_event("ingest_procurement", "complete", level="WARNING" if errors else "INFO", counts=counts, n_errors=len(errors))
