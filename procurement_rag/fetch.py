"""Step 1: find the relevant World Bank projects for a country and download their documents.

Projects come from three places, in this order:
  1. projects whose contracts bought lab or diagnostic equipment (the procurement prototype's gold tables, if built)
  2. health projects found by keyword in the Projects API (laboratory, diagnostic, disease surveillance, ...)
  3. projects pinned in countries.json (e.g. regional programmes such as the East Africa lab network)
For each project, the newest documents of each configured type (appraisal document, procurement plans, supervision,
completion and IEG reports) are downloaded into data/<ISO3>/pdf/<doc type>/<doc id>.pdf and listed in
data/<ISO3>/manifest.json. Files already on disk are not downloaded again.
"""
import concurrent.futures as cf
import datetime
import json
import re
import shutil
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry

from .config import ROOT, country, country_dir

PROJECTS = "https://search.worldbank.org/api/v2/projects"
WDS = "https://search.worldbank.org/api/v2/wds"
GOLD = ROOT / "procurement_demo" / "data" / "health_lab" / "gold"
PROTOTYPE_PDFS = ROOT / "procurement_demo" / "data" / "health_lab" / "pdfs"
MAX_PDF_MB = 60

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))
session.mount("http://", session.get_adapter("https://"))


def get_json(url, params):
    r = session.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def _project_row(pid, p, source):
    sector = (p.get("sector1") or {}).get("Name") if isinstance(p.get("sector1"), dict) else None
    return {"project_id": pid, "project_name": " ".join((p.get("project_name") or "").split()),
            "status": p.get("status"), "approval_year": (p.get("boardapprovaldate") or "")[:4], "sector": sector,
            "source": source}


def project_by_id(pid, source):
    d = get_json(PROJECTS, {"format": "json", "id": pid, "fl": "id,project_name,status,boardapprovaldate,sector1"})
    p = next(iter((d.get("projects") or {}).values()), None) or {}
    return _project_row(pid, p, source)


def contract_projects(iso3):
    """Projects in this country that bought lab or diagnostic equipment, most spend first."""
    f = GOLD / "fact_contract.parquet"
    if not f.exists():
        return []
    c = pd.read_parquet(f, columns=["project_id", "country_iso3", "in_domain", "amount_usd"])
    c = c[(c.country_iso3 == iso3) & c.in_domain]
    return c.groupby("project_id").amount_usd.sum().sort_values(ascending=False).index.tolist()


def keyword_projects(cfg):
    """Health projects in the country whose description matches a lab / diagnostics keyword."""
    health = re.compile(cfg["health_pattern"], re.I)
    found = {}
    for kw in cfg["project_keywords"]:
        d = get_json(PROJECTS, {"format": "json", "rows": 100, "countrycode_exact": cfg["iso2"], "qterm": kw,
                                "fl": "id,project_name,status,boardapprovaldate,sector1"})
        for pid, p in (d.get("projects") or {}).items():
            row = _project_row(pid, p, f"keyword: {kw}")
            year = int(row["approval_year"]) if row["approval_year"].isdigit() else 0
            if (year >= cfg["min_approval_year"] and row["status"] != "Dropped"
                    and health.search(f"{row['project_name']} {row['sector'] or ''}")):
                found.setdefault(pid, row)
    return list(found.values())


def discover_projects(cfg):
    rows = []
    for pid in contract_projects(cfg["iso3"]):
        rows.append(project_by_id(pid, "bought lab or diagnostic equipment"))
    rows += keyword_projects(cfg)
    first = {}
    for r in rows:                                 # keep the first (strongest) reason a project was selected
        first.setdefault(r["project_id"], r)
    rows = list(first.values())[:cfg["max_projects"]]
    pinned = [project_by_id(pid, "pinned in countries.json") for pid in cfg.get("extra_projects", [])
              if pid not in {r["project_id"] for r in rows}]
    return rows + pinned


def project_documents(pid, cfg):
    """Newest English documents of each configured type for one project."""
    d = get_json(WDS, {"format": "json", "rows": 300, "proid": pid, "lang_exact": "English", "srt": "docdt", "order": "desc",
                       "fl": "id,display_title,docdt,docty,projectid,pdfurl,url"})
    docs = [x for k, x in d.get("documents", {}).items() if k != "facets" and x.get("pdfurl")]
    out = []
    for docty, spec in cfg["doc_types"].items():
        for x in [x for x in docs if x.get("docty") == docty][:spec["per_project"]]:
            out.append({"doc_id": x["id"], "doc_type": spec["code"], "doc_type_name": docty,
                        "title": " ".join((x.get("display_title") or "").split()), "doc_date": (x.get("docdt") or "")[:10],
                        "project_id": pid, "pdf_url": x["pdfurl"], "landing_url": x.get("url")})
    return out


def download(doc, folder, tries=3):
    path = folder / "pdf" / doc["doc_type"] / f"{doc['doc_id']}.pdf"
    rel = str(path.relative_to(folder)).replace("\\", "/")
    if path.exists() and path.stat().st_size > 0:
        return {**doc, "file": rel, "status": "present"}
    path.parent.mkdir(parents=True, exist_ok=True)
    cached = PROTOTYPE_PDFS / f"{doc['doc_id']}.pdf"         # already downloaded by the procurement prototype
    if cached.exists() and cached.stat().st_size > 0:
        shutil.copyfile(cached, path)
        return {**doc, "file": rel, "status": "copied"}
    try:
        for attempt in range(tries):
            r = session.get(doc["pdf_url"].replace("http://", "https://", 1), timeout=300)
            if r.status_code != 403 or attempt == tries - 1:
                break
            time.sleep(5 * (attempt + 1))                 # temporary throttling: wait and retry
        r.raise_for_status()
        if not r.content.startswith(b"%PDF") or len(r.content) > MAX_PDF_MB * 1024 * 1024:
            raise ValueError("not a PDF or too large")
        path.write_bytes(r.content)
        return {**doc, "file": rel, "status": "downloaded"}
    except Exception as e:
        return {**doc, "file": None, "status": f"failed: {str(e)[:120]}"}


def fetch_country(iso3):
    cfg = country(iso3)
    folder = country_dir(iso3)
    t0 = time.time()
    projects = discover_projects(cfg)
    names = {p["project_id"]: p["project_name"] for p in projects}
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        lists = list(pool.map(lambda p: project_documents(p["project_id"], cfg), projects))
    docs = [d for lst in lists for d in lst]
    docs = list({d["doc_id"]: d for d in docs}.values())[:cfg["max_docs"]]
    for d in docs:
        d["project_name"] = names.get(d["project_id"], "")
        d["country"] = iso3
    with cf.ThreadPoolExecutor(max_workers=3) as pool:      # the document server throttles more parallel downloads
        docs = list(pool.map(lambda d: download(d, folder), docs))
    manifest = {"country": iso3, "country_name": cfg["name"], "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "projects": projects, "documents": docs}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    status = {}
    for d in docs:
        k = d["status"].split(":")[0]
        status[k] = status.get(k, 0) + 1
    print(f"{iso3}: {len(projects)} projects, {len(docs)} documents {status} ({time.time() - t0:.0f}s)")
    return manifest
