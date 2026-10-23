# Databricks notebook source
# MAGIC %md
# MAGIC # Step 21 - Procurement: ingest structured & semi-structured feeds → Bronze
# MAGIC
# MAGIC | Source (public World Bank API) | What it gives us | Bronze table |
# MAGIC |---|---|---|
# MAGIC | **Major Contract Awards** (FinancesOne `DS00005`) | signed contracts: supplier, amount, category, method, review type, signing date | `bronze.contract_awards_raw` |
# MAGIC | **Procurement notices** (`/v2/procnotices`) | Invitations for Bids / REOIs (open date, deadline) and Contract Award notices (bidders, prices, rejections) | `bronze.proc_notices_raw` |
# MAGIC | **Projects & Operations** (`/v2/projects`) | project metadata: approval/closing dates, commitment, E&S risk, sector, implementing agency | `bronze.projects_raw` |
# MAGIC | **CPIA + Worldwide Governance Indicators** (`/v2/.../indicator`) | country-year institutional quality scores | `bronze.governance_raw` |
# MAGIC
# MAGIC Bronze keeps each record **exactly as received** (`raw_json`) plus the ingestion keys, so parsing rules can change
# MAGIC later without re-downloading. Every table is written with `MERGE` on a natural key → re-runs are idempotent and
# MAGIC only add new/changed records (that is how the weekly job picks up new awards and notices).

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

import json, time, datetime, random, requests
import concurrent.futures as cf
from requests.adapters import HTTPAdapter, Retry

spark.sql(f"USE CATALOG {CATALOG}")
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])))
ingest_ts = datetime.datetime.utcnow()
log_event("ingest_procurement", "started", countries=list(PROC_COUNTRIES))


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

# MAGIC %md ## 1. Major Contract Awards (FinancesOne)

# COMMAND ----------

FONE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
PAGE = 1000

def fetch_awards(iso3):
    iso2 = PROC_COUNTRIES[iso3][0]
    rows, skip = [], 0
    while True:
        d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": PAGE, "skip": skip,
                            "filter": f"borrower_country_code='{iso2}'"})
        batch = d.get("data", [])
        for r in batch:
            if int(r.get("fiscal_year") or 0) >= PROC_MIN_YEAR:
                # One contract can have several supplier rows (JVs): wb_contract_number + supplier_id is the grain
                rows.append((str(r.get("wb_contract_number")), str(r.get("supplier_id") or r.get("supplier")), iso3,
                             json.dumps(r, sort_keys=True), ingest_ts))
        skip += PAGE
        if len(batch) < PAGE or skip >= d.get("count", 0):
            return rows
        time.sleep(1.5)

award_rows, errors = [], {}
for iso3 in PROC_COUNTRIES:          # sequential on purpose: the API rate-limits parallel callers
    try:
        rows = fetch_awards(iso3)
        award_rows += rows
        print(f"{iso3}: {len(rows):>6} contract-supplier rows since FY{PROC_MIN_YEAR}")
    except Exception as e:
        errors[f"awards_{iso3}"] = str(e)[:300]
        print(f"{iso3}: ✗ {e}")

n_awards = merge_raw(award_rows, "bronze.contract_awards_raw", ["wb_contract_number", "supplier_key"], """
    wb_contract_number STRING COMMENT 'World Bank contract number',
    supplier_key       STRING COMMENT 'Supplier id from the dataset (or name when missing)',
    country_iso3       STRING COMMENT 'Borrower country (ISO-3) used in the API filter',
    raw_json           STRING COMMENT 'Record exactly as returned by FinancesOne DS00005',
    ingest_ts          TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
print("merged", n_awards)

# COMMAND ----------

# MAGIC %md ## 2. Procurement notices (Invitations for Bids, REOIs, Contract Awards)
# MAGIC Contract Award notices keep their HTML body: it lists every bidder, evaluated price and rejection reason.

# COMMAND ----------

NOTICES = "https://search.worldbank.org/api/v2/procnotices"
NOTICE_TYPES = ["Invitation for Bids", "Request for Expression of Interest", "Contract Award", "General Procurement Notice"]
FIELDS = ("id,notice_type,noticedate,notice_status,project_id,project_name,project_ctry_name,bid_reference_no,"
          "bid_description,procurement_group,procurement_method_code,procurement_method_name,"
          "submission_deadline_date,submission_date,contact_organization")

def fetch_notices(iso3, ctry_name, notice_type):
    fl = FIELDS + (",notice_text" if notice_type == "Contract Award" else "")
    rows, os_ = [], 0
    while os_ < PROC_MAX_NOTICES:
        d = get_json(NOTICES, {"format": "json", "rows": 500, "os": os_, "project_ctry_name_exact": ctry_name,
                               "notice_type_exact": notice_type, "srt": "noticedate", "order": "desc", "fl": fl})
        batch = d.get("procnotices", [])
        for n in batch:
            rows.append((n["id"], iso3, notice_type, json.dumps(n, sort_keys=True), ingest_ts))
        oldest = batch[-1].get("noticedate", "") if batch else ""
        os_ += 500
        if len(batch) < 500 or (oldest[-4:].isdigit() and int(oldest[-4:]) < PROC_MIN_YEAR):
            break
    return rows

jobs = [(iso3, name, t) for iso3, (_, names) in PROC_COUNTRIES.items() for name in names for t in NOTICE_TYPES]
notice_rows = []
with cf.ThreadPoolExecutor(max_workers=4) as pool:
    futs = {pool.submit(fetch_notices, *j): j for j in jobs}
    for f in cf.as_completed(futs):
        j = futs[f]
        try:
            notice_rows += f.result()
        except Exception as e:
            errors[f"notices_{j[0]}_{j[2]}"] = str(e)[:300]
            print("✗", j, e)

n_notices = merge_raw(notice_rows, "bronze.proc_notices_raw", ["notice_id"], """
    notice_id    STRING COMMENT 'Procurement notice id (OP...)',
    country_iso3 STRING COMMENT 'Country the notice was fetched for',
    notice_type  STRING COMMENT 'Invitation for Bids | Request for Expression of Interest | Contract Award | General Procurement Notice',
    raw_json     STRING COMMENT 'Notice as returned by the procnotices API (Contract Awards include the HTML body)',
    ingest_ts    TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
display(spark.sql("SELECT country_iso3, notice_type, count(*) AS notices FROM bronze.proc_notices_raw GROUP BY ALL ORDER BY 1, 2"))

# COMMAND ----------

# MAGIC %md ## 3. Projects & Operations

# COMMAND ----------

PROJECTS = "https://search.worldbank.org/api/v2/projects"
PFIELDS = ("id,project_name,countryshortname,countrycode,regionname,boardapprovaldate,closingdate,status,lendinginstr,"
           "totalcommamt,totalamt,curr_total_commitment,envassesmentcategorycode,esrc_ovrl_risk_rate,sector1,sector,"
           "mjsector_namecode,theme1,impagency,borrower,project_abstract,pdo,teamleadname,url")

def fetch_projects(iso3):
    iso2 = PROC_COUNTRIES[iso3][0]
    rows, os_ = [], 0
    while True:
        d = get_json(PROJECTS, {"format": "json", "rows": 500, "os": os_, "countrycode_exact": iso2, "fl": PFIELDS})
        batch = [p for k, p in (d.get("projects") or {}).items()]
        rows += [(p["id"], iso3, json.dumps(p, sort_keys=True), ingest_ts) for p in batch]
        os_ += 500
        if len(batch) < 500:
            return rows

project_rows = []
for iso3 in PROC_COUNTRIES:
    try:
        project_rows += fetch_projects(iso3)
    except Exception as e:
        errors[f"projects_{iso3}"] = str(e)[:300]

n_projects = merge_raw(project_rows, "bronze.projects_raw", ["project_id"], """
    project_id   STRING COMMENT 'World Bank project id (P-code)',
    country_iso3 STRING COMMENT 'Country the project was fetched for',
    raw_json     STRING COMMENT 'Project record from the Projects API',
    ingest_ts    TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")
print("projects merged", n_projects)

# COMMAND ----------

# MAGIC %md ## 4. Governance context: CPIA and WGI
# MAGIC CPIA is only published for IDA-eligible countries (e.g. not India) - the gap is kept as NULL and handled
# MAGIC explicitly in the model (missing-indicator flag), not silently imputed here.

# COMMAND ----------

gov_rows = []
iso3s = ";".join(PROC_COUNTRIES)
for code, (source, col) in GOV_INDICATORS.items():
    try:
        d = get_json(f"https://api.worldbank.org/v2/country/{iso3s}/indicator/{code}",
                     {"format": "json", "source": source, "date": f"{PROC_MIN_YEAR - 5}:{datetime.date.today().year}",
                      "per_page": 2000})
        for r in (d[1] if len(d) > 1 and d[1] else []):
            gov_rows.append((r["countryiso3code"], code, int(r["date"]), json.dumps(r, sort_keys=True), ingest_ts))
    except Exception as e:
        errors[f"gov_{code}"] = str(e)[:300]
        print("✗", code, e)

n_gov = merge_raw(gov_rows, "bronze.governance_raw", ["country_iso3", "indicator_code", "year"], """
    country_iso3   STRING COMMENT 'ISO-3 country code',
    indicator_code STRING COMMENT 'CPIA (IQ.CPA.*) or WGI (GOV_WGI_*) indicator code',
    year           INT    COMMENT 'Year of the score',
    raw_json       STRING COMMENT 'Observation as returned by the Indicators API (value may be null)',
    ingest_ts      TIMESTAMP COMMENT 'Ingestion run time (UTC)'""")

# COMMAND ----------

# MAGIC %md ## 5. Checks

# COMMAND ----------

summary = spark.sql("""
  SELECT 'contract_awards_raw' AS tbl, count(*) AS n FROM bronze.contract_awards_raw
  UNION ALL SELECT 'proc_notices_raw', count(*) FROM bronze.proc_notices_raw
  UNION ALL SELECT 'projects_raw', count(*) FROM bronze.projects_raw
  UNION ALL SELECT 'governance_raw', count(*) FROM bronze.governance_raw
""")
display(summary)
counts = {r.tbl: r.n for r in summary.collect()}
for t in ("contract_awards_raw", "proc_notices_raw", "projects_raw"):
    assert counts[t] > 0, f"bronze.{t} is empty - check errors: {errors}"
if errors:
    print("⚠ partial failures (re-run picks them up):", json.dumps(errors, indent=1)[:2000])
log_event("ingest_procurement", "complete", level="WARNING" if errors else "INFO", counts=counts, errors=errors)
