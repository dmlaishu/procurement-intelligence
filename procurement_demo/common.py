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
# English, French, Spanish and Portuguese terms; "." stands in for accents that arrive garbled in the award feed.
ITEM_TYPES_LAB = [
    ("Molecular diagnostics (PCR, GeneXpert)", r"\bq?RT-?PCR\b|\bPCR\b|gene ?xpert|molecular|mol.culaire|nucleic|nucl.ique|"
                                               r"RNA extract|\bDNA\b|\bADN\b|thermocycl|sequenc|s.quen.age|genom|g.nom|primer|amorce"),
    ("Diagnostic imaging", r"x-?ray|radiograph|radiolog|rayos? x|rayons? x|ultraso|sonograph|.chograph|ecograf|\bCT\b|"
                           r"computed tomograph|tomograf|tomodensit|\bMRI\b|\bIRM\b|magnetic resonance|mammograph|mamograf|"
                           r"fluoroscop|c-?arm|imaging|imagerie|linear accelerator|angiograph|echocardiograph"),
    ("Rapid tests and test kits", r"rapid (diagnostic )?tests?|\bRDTs?\b|\bTDRs?\b|test kits?|testing kits?|antigen|antig.n|"
                                  r"antibod|anticorps|serolog|s.rolog|self-?test|diagnostic kits?|pruebas? r.pidas?|"
                                  r"tests? (de diagnostic )?rapides?|tests? de d.pistage|kits? de (test|d.pistage|diagn)"),
    ("Reagents and cartridges", r"reagent|\br.{0,2}acti[fv]|cartridge|cartouche|calibrator|culture media|milieux? de culture|"
                                r"laboratory consumable|lab consumable|consommables? de laboratoire|consumibles? de laboratorio|"
                                r"microbiolog|agarose|viral transport|\bVTM\b|swab|.couvillon|hisopo"),
    ("Laboratory analyzers", r"analy[sz]er|analy[sz]eur|analizador|hematolog|haematolog|h.matolog|biochem|biochimi|bioqu.mic|"
                             r"chemistry|immunoassay|inmunoqu|immunochimi|ELISA|coagulat|blood gas|electrolyte|\bCD4\b|"
                             r"viral load|charge virale|carga viral|blood (grouping|bank)|banque de sang|banco de sangre|urinaly"),
    ("Microscopy", r"microscop|microtome"),
    ("Biosafety, sterilization and cold chain", r"biosafety|bios.curit|bioseguridad|biological safety|safety cabinet|"
                                                r"autoclave|steriliz|st.rilis|esteriliz|(ultra[- ]?low|deep) freezer|"
                                                r"cong.lateur|congelador|laboratory (fridge|refrigerat)|centrifug|incubat|"
                                                r"incubad|fume hood|hotte|laminar"),
    ("Laboratory information systems", r"\bLIMS\b|laborator(y|ies) information|lab information|SIL\b"),
    ("Mobile and modular laboratories", r"mobile lab|modular lab|laboratoire mobile|laboratorio m.vil|"
                                        r"container(ised|ized)? lab|BSL-?[23]"),
    ("General laboratory and diagnostic equipment", r"laborator|laboratoi|laboratorio|\blab\b|diagnost|diagn.stico|"
                                                    r"pathology|patholog|anatomo|testing equipment|specialised testing"),
    # outside the domain (kept so the market context and models see the whole health-goods picture)
    ("Medicines and vaccines", r"medicine|drug|vaccin|vacuna|pharmac|m.dicament|medicamento|antiretro|insulin|tablet|"
                               r"injection|\d+ ?mg\b|folic|ferrous|plumpy|contracepti"),
    ("PPE and medical consumables", r"\bPPE\b|\bEPI\b|personal protect|mask|masque|mascarilla|glove|gant|guante|gown|"
                                    r"coverall|goggle|face shield|visi.re|syringe|seringue|jeringa|sanitiz|disinfect|"
                                    r"d.sinfect|gel hydro|soap|savon|jab.n|hygien|hand ?wash|lavage des mains"),
    ("Other medical equipment", r"medical equipment|.quipements? (m.dicaux|biom.dicaux)|mat.riels? m.dicaux|"
                                r"equipamientos? m.dicos?|equipos? m.dicos?|ventilat|oxyg|monitor|hospital bed|\bbeds?\b|"
                                r"\blits?\b|camas?|anaesthe|anesthe|infusion|surgical|chirurgi|quir.rgic|dental|\bICU\b|"
                                r"theatre|delivery|neonatal|n.onat|h.modialys|dialys|medical gas|stent|cat.ter|catheter|"
                                r"simulat|mattress|matelas|incinerat|ambulance"),
    ("IT, furniture and vehicles", r"computer|ordinateur|computador|laptop|server|serveur|software|logiciel|IT equipment|"
                                   r"furniture|mobilier|mobiliario|vehicle|v.hicule|veh.culo|moto|printer|imprimante|"
                                   r"firewall|CCTV|internet|office|bureau"),
]
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
        "domain_items": {name for name, _ in ITEM_TYPES_LAB[:10]},
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


NOT_EQUIPMENT = re.compile(r"vehicle|v.hicule|veh.culo|pick ?-?up|motorcycle|\bmotos?\b|CCTV|access control|printing|"
                           r"impression|manuals?\b|construction|r.habilitation|works\b|travaux|obras", re.I)


def item_type(description, category):
    """Classify what is being bought into the scope's item taxonomy."""
    d = description or ""
    for name, pat in CFG["item_types"]:
        if name.startswith("General") and NOT_EQUIPMENT.search(d):
            continue                   # "vehicles for the national laboratory" is not laboratory equipment
        if re.search(pat, d, re.I):
            return name
    return {"Works": "Other works", "Goods": "Other goods", "Consultant Services": "Other consultancy",
            "Non-consulting Services": "Other services"}.get(category, "Other")


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
