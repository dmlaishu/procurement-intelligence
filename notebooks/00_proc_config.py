# Databricks notebook source
# MAGIC %md
# MAGIC # 00_proc_config - settings for the procurement notebooks (20-29)
# MAGIC Every procurement notebook starts with `%run ./00_proc_config`, so names and the scope live in one place.
# MAGIC
# MAGIC **Scope:** medical laboratory and diagnostic equipment bought in World Bank-financed health projects, all
# MAGIC countries, FY2015 onwards. Ingestion is **contract-first**: the domain is defined by what was bought. All Health
# MAGIC practice Goods contracts are pulled, each description is classified with `classify_item` (`20_proc_lib`), and
# MAGIC the projects that bought domain items become the project scope for notices, documents and governance context.
# MAGIC
# MAGIC Everything lives in its own catalog (`proc_demo`). Running this notebook creates the catalog, schemas, volume and
# MAGIC log table if they do not exist, so there is no separate setup step. Where catalogs cannot be created (some
# MAGIC workspaces only allow the default one), it falls back to `workspace` and says so.

# COMMAND ----------

CATALOG = "proc_demo"
FALLBACK_CATALOG = "workspace"          # Databricks Free Edition's default catalog

SCHEMAS = {
    "bronze": "Raw records exactly as received from the World Bank APIs, and the project PDFs.",
    "silver": "Typed, cleaned records: contracts (with equipment type), notices, projects, governance, document pages.",
    "gold":   "Knowledge graph and analytics: fact_contract, fact_tender, dimensions, kg_edges, chunks, lessons, ratings, benchmarks.",
    "ai":     "UC function tools, the vector index and the registered models.",
    "ops":    "Run logs, copilot briefs and user feedback.",
}

# ── Scope: lab and diagnostic equipment in World Bank-financed health projects ──
PROC_SCOPE_LABEL = "Medical laboratory and diagnostic equipment in World Bank-financed health projects"
PROC_CONTRACT_FILTER = "project_global_practice like '%Health%' and procurement_category='Goods'"
PROC_MIN_YEAR = 2015                     # contracts, notices and tenders from this fiscal year on
PROC_MAX_PROJECTS = 140                  # domain projects whose notices are ingested (most domain spend first)
PROC_DOC_TOP_SPEND = 16                  # documents for the highest-spend domain projects ...
PROC_MAX_DOC_PROJECTS = 36               # ... plus the highest-spend CLOSED ones (completion reports, IEG reviews, lessons)
NOTICE_TYPES = ["Invitation for Bids", "Request for Expression of Interest", "Contract Award"]
PROC_DOC_TYPES = {                       # Documents & Reports API docty → short code used everywhere
    "Implementation Completion and Results Report": "ICR",
    "Implementation Completion Report Review": "ICRR",
    "Project Appraisal Document": "PAD",
    "Implementation Status and Results Report": "ISR",
    "Project Performance Assessment Report": "PPAR",
    "Procurement Plan": "PP",
}
GOV_INDICATORS = {                       # CPIA (WDI, source 2) and WGI (source 3)
    "IQ.CPA.PUBS.XQ":  (2, "cpia_public_sector_mgmt"),
    "IQ.CPA.TRAN.XQ":  (2, "cpia_transparency_corruption"),
    "IQ.CPA.FINQ.XQ":  (2, "cpia_budget_financial_mgmt"),
    "GOV_WGI_GE.EST":  (3, "wgi_gov_effectiveness"),
    "GOV_WGI_RQ.EST":  (3, "wgi_regulatory_quality"),
    "GOV_WGI_CC.EST":  (3, "wgi_control_of_corruption"),
}
# The package the copilot demo (Step 28) writes a brief for
PROC_DEMO_PACKAGE = {
    "description": "Supply, installation and commissioning of automated real-time PCR systems and GeneXpert analyzers "
                   "for regional public health laboratories, Lot 2",
    "item_type": "Molecular diagnostics (PCR, GeneXpert)", "procurement_category": "Goods", "method_code": "RFB",
    "country_iso3": "KEN", "region": "Sub-Saharan Africa", "review_type": "Prior", "major_sector": "Health",
    "contract_usd": 2_500_000,
}

# ── Document Q&A by country (notebooks 30-34) ──
# Countries live in the table ops.rag_countries (notebook 30); these defaults apply to every country.
RAG_DEFAULTS = {
    "min_approval_year": 2005,
    "project_keywords": ["laboratory", "diagnostic", "disease surveillance", "tuberculosis", "pandemic preparedness"],
    "health_pattern": r"health|disease|tuberculosis|covid|pandemic|laborator|diagnost|hiv|aids|malaria|immuni|surveillance|medical|hospital",
    "doc_types": {"Project Appraisal Document": ("PAD", 1), "Project Paper": ("PPR", 1), "Procurement Plan": ("PP", 2),
                  "Implementation Status and Results Report": ("ISR", 1),
                  "Implementation Completion and Results Report": ("ICR", 1),
                  "Implementation Completion Report Review": ("ICRR", 1),
                  "Project Performance Assessment Report": ("PPAR", 1)},       # docty → (code, newest N per project)
    "max_projects": 12,
    "max_docs": 60,
}
RAG_SEED_COUNTRIES = [("KEN", "KE", "Kenya", ["P111556"]), ("IND", "IN", "India", [])]   # iso3, iso2, name, pinned projects
# Answer sentences must be at least this close in meaning to the question (cosine of databricks-gte-large-en
# embeddings). Starting values: check them with the evaluation cell in 34_rag_ask on the first run.
RAG_MIN_SIM = 0.70
RAG_MIN_SIM_NO_OVERLAP = 0.80

# ── Models, search and serving (Databricks-hosted Foundation Model APIs) ──
LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"   # replaced by an available chat endpoint if missing
EMBEDDING_ENDPOINT = "databricks-gte-large-en"
VS_ENDPOINT = "proc_vs_endpoint"
GROUP_AI_APP = "proc_ai_app"             # optional account group for the copilot app; grants are skipped if absent

# COMMAND ----------

def _ensure_catalog():
    try:
        spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG} COMMENT '{PROC_SCOPE_LABEL}: procurement intelligence'")
        return CATALOG
    except Exception as e:
        if CATALOG in {r[0] for r in spark.sql("SHOW CATALOGS").collect()}:
            return CATALOG
        print(f"⚠ cannot create catalog {CATALOG} ({str(e).splitlines()[0][:120]}); using {FALLBACK_CATALOG}")
        return FALLBACK_CATALOG

CATALOG = _ensure_catalog()
for _s, _c in SCHEMAS.items():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{_s} COMMENT '{_c}'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.raw_docs COMMENT 'Project PDFs (PAD, ISR, ICR, ICR Review, PPAR, procurement plans)'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.rag_docs COMMENT 'Document Q&A PDFs, organised as <country>/<doc type>/<doc id>.pdf'")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {CATALOG}.ops.app_logs (event_ts TIMESTAMP, run_id STRING, component STRING,
              level STRING, event STRING, details STRING) COMMENT 'Structured run events from every procurement notebook'""")
spark.sql(f"USE CATALOG {CATALOG}")

RAW_DOCS_PATH = f"/Volumes/{CATALOG}/bronze/raw_docs"
PROC_VS_INDEX = f"{CATALOG}.ai.proj_doc_chunks_index"
RAG_DOCS_PATH = f"/Volumes/{CATALOG}/bronze/rag_docs"
RAG_INDEX = f"{CATALOG}.ai.rag_chunks_index"
PROC_MODEL_PREFIX = f"{CATALOG}.ai.proc_"          # proc_cycle_p50, proc_cycle_p80, proc_single_bid, proc_rebid, proc_amendment


def resolve_llm_endpoint(preferred=LLM_ENDPOINT):
    """The preferred chat endpoint if this workspace serves it, else the first available Databricks-hosted chat model."""
    try:
        from databricks.sdk import WorkspaceClient
        eps = list(WorkspaceClient().serving_endpoints.list())
        names = {e.name for e in eps}
        if preferred in names:
            return preferred
        chat = sorted(e.name for e in eps if (e.task or "").endswith("chat") and e.name.startswith("databricks-"))
        pick = next((n for n in chat if "llama" in n), chat[0] if chat else preferred)
        print(f"⚠ {preferred} not served here; using {pick}")
        return pick
    except Exception as e:
        print(f"⚠ could not list serving endpoints ({str(e)[:100]}); keeping {preferred}")
        return preferred


print(f"Config loaded: catalog={CATALOG}, scope='{PROC_SCOPE_LABEL}', FY{PROC_MIN_YEAR}+")

# COMMAND ----------

# MAGIC %md
# MAGIC ### App logger → `ops.app_logs`

# COMMAND ----------

import logging, json, uuid, datetime

_RUN_ID = str(uuid.uuid4())


def log_event(component: str, event: str, level: str = "INFO", **details):
    """Write one structured event to Python logging and to ops.app_logs."""
    logging.getLogger(component).log(getattr(logging, level, logging.INFO), f"{event} {details}")
    try:
        spark.createDataFrame(
            [(datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None), _RUN_ID, component, level, event,
              json.dumps(details, default=str))],
            "event_ts timestamp, run_id string, component string, level string, event string, details string",
        ).write.mode("append").saveAsTable(f"{CATALOG}.ops.app_logs")
    except Exception as e:  # logging must never break the pipeline
        print(f"[log_event skipped] {e}")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
