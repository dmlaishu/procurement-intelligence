"""Shared paths, settings and helpers for the local Procurement Intelligence prototype.

The parsing / entity-resolution / guardrail logic is NOT duplicated here: it is loaded from
notebooks/20_proc_lib.py, the same file the Databricks notebooks %run - one implementation, two runtimes.
"""
import importlib.util
import json
import os
import random
import re
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter, Retry

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("proc_lib", ROOT / "notebooks" / "20_proc_lib.py")
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

# ── Item taxonomies: the component level beneath procurement category (first match wins) ──────────
# The lab-equipment taxonomy lives in 20_proc_lib so the Databricks notebooks classify exactly the same way.
ITEM_TYPES_LAB = lib.ITEM_TYPES_LAB
ITEM_TYPES_ROADS = [
    ("Road safety", r"road safety|black ?spot|crash|accident|ambulance|enforcement|speed (camera|management)"),
    ("Bridges and structures", r"bridge|culvert|cross[- ]?drain|retaining wall|causeway|flyover|structure"),
    ("Maintenance", r"maintenance|periodic renewal|OPRC|performance[- ]based|routine"),
    ("Supervision and quality control", r"supervision|quality (control|assurance|monitor)|independent engineer|authority'?s? "
                                        r"engineer|third[- ]party|construction management"),
    ("Design and DPR", r"\bDPRs?\b|detailed project report|detailed design|feasibility|survey|investigation|design consult"),
    ("Environmental and social", r"environment|social|resettlement|land acquisition|plantation|gender|tribal|grievance"),
    ("IT, equipment and vehicles", r"software|computer|\bIT\b|hardware|equipment|vehicle|laborator|\bGIS\b|server|"
                                   r"furniture|printer|ERP|e-?procurement"),
    ("Capacity building and studies", r"training|capacity|study|studies|audit|institutional|strategy|policy|workshop"),
    ("Road construction and upgrading", r"\broads?\b|highway|upgrad|widen|construct|improvement|strengthening|"
                                        r"reconstruct|bituminous|black ?top|carriageway|pavement|km\b|package"),
]

# ── Scope ────────────────────────────────────────────────────────────────────────────────────────
# A scope is one domain the platform is pointed at. The pipeline is identical; only what is ingested changes.
#   health_lab     (default) medical laboratory and diagnostic equipment in World Bank-financed health projects, all
#                  countries: contract-first ingestion (the domain is defined by what was bought)
#   india_roads    India, rural and inter-urban roads: project-scoped ingestion, with a State level
#   three_country  Kenya, Nigeria, Bangladesh, all sectors: country-wide ingestion (the first prototype run)
# Switch with (PowerShell):  $env:PROC_SCOPE = "india_roads"
ROAD_SECTORS = {"Rural and Inter-Urban Roads", "Roads and highways", "(Historic)Rural roads", "Rural and Inter-Urban Roads and Highways"}
ROAD_NAME = re.compile(r"\broads?\b|highway|rural connectivity|PMGSY", re.I)


def is_road_project(p):
    """Roads is the project's primary sector, or its name says so (secondary road components do not count)."""
    primary = (p.get("sector1") or {}).get("Name") if isinstance(p.get("sector1"), dict) else None
    approved = (p.get("boardapprovaldate") or "")[:4]
    return ((primary in ROAD_SECTORS or bool(ROAD_NAME.search(p.get("project_name") or "")))
            and approved.isdigit() and int(approved) >= 2005 and p.get("status") != "Dropped")


FULL_DOC_TYPES = {"Implementation Completion and Results Report": "ICR", "Implementation Completion Report Review": "ICRR",
                  "Project Appraisal Document": "PAD", "Implementation Status and Results Report": "ISR",
                  "Project Performance Assessment Report": "PPAR", "Procurement Plan": "PP"}

SCOPES = {
    "health_lab": {
        "label": "Medical laboratory and diagnostic equipment in World Bank-financed health projects",
        "short": "health lab & diagnostics",
        "mode": "contract_first",                   # domain = contracts whose items fall in the domain item types
        "countries": {},                            # discovered from the contracts
        "contract_filter": "project_global_practice like '%Health%' and procurement_category='Goods'",
        "min_year": 2015,
        "item_types": ITEM_TYPES_LAB, "item_label": "Equipment type",
        "domain_items": lib.LAB_DOMAIN_ITEMS,
        "max_projects": 140,                        # domain projects whose notices are ingested (most domain spend first)
        "doc_types": FULL_DOC_TYPES,
        "max_doc_projects": 36,                     # 16 highest-spend projects + 20 highest-spend CLOSED ones (ICRs, lessons)
        "demo_package": {"description": "Supply, installation and commissioning of automated real-time PCR systems and "
                                        "GeneXpert analyzers for regional public health laboratories, Lot 2",
                         "country_iso3": "KEN", "review_type": "Prior", "procurement_category": "Goods", "method_code": "RFB",
                         "major_sector": "Health", "contract_usd": 2_500_000},
    },
    "india_roads": {
        "label": "India: rural and inter-urban roads",
        "short": "India roads",
        "mode": "project_filter",
        "countries": {"IND": ("IN", ["India"])},
        "project_filter": is_road_project,
        "min_year": 2010,
        "item_types": ITEM_TYPES_ROADS, "item_label": "Work type", "domain_items": None,
        "doc_types": FULL_DOC_TYPES,
        "max_doc_projects": 26,
        "demo_package": {"description": "Upgradation of rural roads to bituminous standard with 5-year maintenance, Package 3",
                         "country_iso3": "IND", "state": "Madhya Pradesh", "review_type": "Post", "procurement_category": "Works",
                         "method_code": "RFB", "major_sector": "Transportation", "contract_usd": 6_000_000},
    },
    "three_country": {
        "label": "Kenya, Nigeria, Bangladesh: all sectors",
        "short": "three countries",
        "mode": "country",
        "countries": {"KEN": ("KE", ["Kenya"]), "NGA": ("NG", ["Nigeria"]), "BGD": ("BD", ["Bangladesh"])},
        "min_year": 2015,
        "item_types": ITEM_TYPES_ROADS, "item_label": "Work type", "domain_items": None,
        "doc_types": {k: v for k, v in FULL_DOC_TYPES.items() if v in ("ICR", "ISR", "PAD", "PPAR")},
        "docs_per_type": {"ICR": 12, "ISR": 10, "PAD": 3, "PPAR": 3},
        "demo_package": {"description": "Rehabilitation of rural roads and bridges in two counties, Lot 1",
                         "country_iso3": "KEN", "review_type": "Prior", "procurement_category": "Works", "method_code": "RFB",
                         "major_sector": "Transportation", "contract_usd": 12_000_000},
    },
}
SCOPE = os.environ.get("PROC_SCOPE", "health_lab")
CFG = SCOPES[SCOPE]
COUNTRIES = CFG["countries"]
MIN_YEAR = CFG["min_year"]
DOC_TYPES = CFG["doc_types"]

DATA = ROOT / "procurement_demo" / "data" / SCOPE
RAW, PDFS, GOLD, OUT = DATA / "raw", DATA / "pdfs", DATA / "gold", ROOT / "procurement_demo" / "output" / SCOPE
for d in (RAW, PDFS, GOLD, OUT):
    d.mkdir(parents=True, exist_ok=True)
COUNTRY_REF = ROOT / "procurement_demo" / "data" / "countries.json"     # WB country list: ISO-2, ISO-3, name, region

MAX_NOTICES = 12000
GOV_INDICATORS = {
    "IQ.CPA.PUBS.XQ": (2, "cpia_public_sector_mgmt"),
    "IQ.CPA.TRAN.XQ": (2, "cpia_transparency_corruption"),
    "IQ.CPA.FINQ.XQ": (2, "cpia_budget_financial_mgmt"),
    "GOV_WGI_GE.EST": (3, "wgi_gov_effectiveness"),
    "GOV_WGI_RQ.EST": (3, "wgi_regulatory_quality"),
    "GOV_WGI_CC.EST": (3, "wgi_control_of_corruption"),
}
CATEGORY = {"CW": "Works", "GO": "Goods", "CS": "Consultant Services", "NC": "Non-consulting Services"}


NOT_EQUIPMENT = lib.NOT_EQUIPMENT


def item_type(description, category):
    """Classify what is being bought into the scope's item taxonomy."""
    return lib.classify_item(description, category, CFG["item_types"])


def in_domain(item):
    return CFG["domain_items"] is None or item in CFG["domain_items"]


session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])))
session.mount("http://", session.get_adapter("https://"))


def get_json(url, params, tries=8):
    """GET with backoff on 429 (FinancesOne allows only a few calls per minute and says how long to wait)."""
    for _ in range(tries):
        r = session.get(url, params=params, timeout=180)
        if r.status_code == 429:
            try:
                wait = int("".join(ch for ch in r.json().get("message", "") if ch.isdigit()) or 35) + 2
            except Exception:
                wait = 35
            time.sleep(min(wait, 90) + random.random())
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited too often: {url}")


def save_json(obj, path: Path):
    path.write_text(json.dumps(obj, default=str), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def country_reference():
    """ISO-2 → (ISO-3, name, region) for every World Bank economy; downloaded once and cached."""
    if not COUNTRY_REF.exists():
        d = get_json("https://api.worldbank.org/v2/country", {"format": "json", "per_page": 400})
        save_json([{"iso2": c["iso2Code"], "iso3": c["id"], "name": c["name"], "region": c["region"]["value"]}
                   for c in d[1] if c["region"]["value"] != "Aggregates"], COUNTRY_REF)
    # the API pads some names with spaces ("Sub-Saharan Africa ")
    return {c["iso2"]: {k: v.strip() if isinstance(v, str) else v for k, v in c.items()} for c in load_json(COUNTRY_REF)}
