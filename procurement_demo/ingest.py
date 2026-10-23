"""Step 1 - pull the public World Bank feeds for the active scope into procurement_demo/data/<scope>/raw (cached).

Three ingestion modes, one pipeline:
  country         whole countries (three_country)
  project_filter  the domain's projects, then their awards, notices and documents (india_roads)
  contract_first  the domain is defined by WHAT WAS BOUGHT: pull the practice's contracts, classify each item, keep the
                  projects that bought domain items, then their notices and documents (health_lab)

    python -m procurement_demo.ingest            # skips sources already cached
    python -m procurement_demo.ingest --refresh  # re-download everything
"""
import concurrent.futures as cf
import sys

from .common import (CFG, COUNTRIES, DOC_TYPES, GOV_INDICATORS, MAX_NOTICES, MIN_YEAR, PDFS, RAW, country_reference,
                     get_json, in_domain, item_type, load_json, save_json, session)

FONE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
NOTICES = "https://search.worldbank.org/api/v2/procnotices"
PROJECTS = "https://search.worldbank.org/api/v2/projects"
WDS = "https://search.worldbank.org/api/v2/wds"
NOTICE_TYPES = ["Invitation for Bids", "Request for Expression of Interest", "Contract Award"]
NOTICE_FIELDS = ("id,notice_type,noticedate,project_id,project_name,project_ctry_name,bid_reference_no,bid_description,"
                 "procurement_group,procurement_method_code,procurement_method_name,submission_deadline_date,"
                 "contact_organization")


def awards(iso3):
    iso2, rows, skip = COUNTRIES[iso3][0], [], 0
    while True:
        d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": 1000, "skip": skip,
                            "filter": f"borrower_country_code='{iso2}'"})
        batch = d.get("data", [])
        rows += [r for r in batch if int(r.get("fiscal_year") or 0) >= MIN_YEAR]
        skip += 1000
        if len(batch) < 1000 or skip >= d.get("count", 0):
            return rows


def notices(iso3, name, ntype):
    fl = NOTICE_FIELDS + (",notice_text" if ntype == "Contract Award" else "")
    rows, os_ = [], 0
    while os_ < MAX_NOTICES:
        d = get_json(NOTICES, {"format": "json", "rows": 500, "os": os_, "project_ctry_name_exact": name,
                               "notice_type_exact": ntype, "srt": "noticedate", "order": "desc", "fl": fl})
        batch = d.get("procnotices", [])
        rows += [{**n, "country_iso3": iso3} for n in batch]
        os_ += 500
        oldest = batch[-1].get("noticedate", "") if batch else ""
        if len(batch) < 500 or (oldest[-4:].isdigit() and int(oldest[-4:]) < MIN_YEAR):
            return rows
    return rows


def projects(iso3):
    """All projects of the country, or only the scope's domain projects when the scope has a project filter."""
    keep = CFG["project_filter"] or (lambda p: True)
    rows, os_ = [], 0
    while True:
        d = get_json(PROJECTS, {"format": "json", "rows": 500, "os": os_, "countrycode_exact": COUNTRIES[iso3][0]})
        batch = list((d.get("projects") or {}).values())
        rows += [{**p, "country_iso3": iso3} for p in batch if keep(p)]
        os_ += 500
        if len(batch) < 500:
            return rows


def governance(iso3s=None):
    rows, iso3s = [], sorted(iso3s or COUNTRIES)
    for code, (source, col) in GOV_INDICATORS.items():
        for i in range(0, len(iso3s), 40):
            d = get_json(f"https://api.worldbank.org/v2/country/{';'.join(iso3s[i:i + 40])}/indicator/{code}",
                         {"format": "json", "source": source, "date": f"{MIN_YEAR - 5}:2026", "per_page": 20000})
            for r in (d[1] if len(d) > 1 and d[1] else []):
                rows.append({"country_iso3": r["countryiso3code"], "year": int(r["date"]), "indicator": col, "value": r["value"]})
    return rows


def documents(awarded_projects):
    """Newest documents per country/type, preferring projects that have contract awards (so the graph links)."""
    picked = []
    for iso3, (iso2, _) in COUNTRIES.items():
        for docty, code in DOC_TYPES.items():
            d = get_json(WDS, {"format": "json", "rows": 100, "docty_exact": docty, "countrycode_exact": iso2,
                               "lang_exact": "English", "srt": "docdt", "order": "desc",
                               "fl": "id,display_title,docdt,docty,projectid,pdfurl,url,abstracts"})
            docs = [x for k, x in d.get("documents", {}).items() if k != "facets" and x.get("pdfurl")]
            seen, uniq = set(), []
            for x in docs:
                x["project_ids"] = [p.strip() for p in (x.get("projectid") or "").split(",") if p.strip()]
                key = tuple(x["project_ids"])
                if code in ("ISR", "ICR") and key in seen:     # one per project: newest
                    continue
                seen.add(key)
                uniq.append(x)
            linked = [x for x in uniq if set(x["project_ids"]) & awarded_projects]
            rest = [x for x in uniq if x not in linked]
            for x in (linked + rest)[:CFG["docs_per_type"][code]]:
                picked.append(_doc_row(x, code, iso3, x["project_ids"][0] if x["project_ids"] else None))
    return list({p["doc_id"]: p for p in picked}.values())

# ── Domain (project-scoped) ingestion ────────────────────────────────────────────────────────────

def awards_for_projects(iso3, pids):
    """The country's transport-practice awards in one paged query, then per-project queries for domain projects
    filed under another practice."""
    iso2, rows, skip = COUNTRIES[iso3][0], [], 0
    while True:
        d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": 1000, "skip": skip,
                            "filter": f"borrower_country_code='{iso2}' and project_global_practice='Transportation'"})
        batch = d.get("data", [])
        rows += batch
        skip += 1000
        if len(batch) < 1000 or skip >= d.get("count", 0):
            break
    rows = [r for r in rows if r.get("project_id") in pids]
    for pid in sorted(pids - {r.get("project_id") for r in rows}):
        d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": 1000, "skip": 0,
                            "filter": f"project_id='{pid}'"})
        rows += d.get("data", [])
    return [r for r in rows if int(r.get("fiscal_year") or 0) >= MIN_YEAR]


def notices_for_project(iso3, pid, ntype):
    fl = NOTICE_FIELDS + (",notice_text" if ntype == "Contract Award" else "")
    rows, os_ = [], 0
    while True:
        d = get_json(NOTICES, {"format": "json", "rows": 500, "os": os_, "project_id": pid, "notice_type_exact": ntype,
                               "srt": "noticedate", "order": "desc", "fl": fl})
        batch = d.get("procnotices", [])
        rows += [{**n, "country_iso3": iso3} for n in batch if n.get("project_id") == pid]
        os_ += 500
        if len(batch) < 500:
            return rows


def documents_for_projects(project_rows, activity):
    """Per project: the newest ICR, ICR Review, PAD, ISR, PPAR and Procurement Plan, for the most active projects."""
    picked = []
    ranked = sorted(project_rows, key=lambda p: -activity.get(p["id"], 0))[:CFG["max_doc_projects"]]
    for p in ranked:
        d = get_json(WDS, {"format": "json", "rows": 200, "proid": p["id"], "lang_exact": "English", "srt": "docdt",
                           "order": "desc", "fl": "id,display_title,docdt,docty,projectid,pdfurl,url,abstracts"})
        docs = [x for k, x in d.get("documents", {}).items() if k != "facets" and x.get("pdfurl")]
        for docty, code in DOC_TYPES.items():
            hit = next((x for x in docs if x.get("docty") == docty), None)     # newest of each type
            if hit:
                hit["project_ids"] = [q.strip() for q in (hit.get("projectid") or "").split(",") if q.strip()] or [p["id"]]
                picked.append(_doc_row(hit, code, p["country_iso3"], p["id"]))
    return list({x["doc_id"]: x for x in picked}.values())


# ── Contract-first ingestion ─────────────────────────────────────────────────────────────────────

def practice_awards():
    """Every award of the scope's practice and category (e.g. all health Goods), FY >= MIN_YEAR."""
    rows, skip = [], 0
    while True:
        d = get_json(FONE, {"datasetId": "DS00005", "resourceId": "RS00005", "type": "json", "top": 1000, "skip": skip,
                            "filter": f"{CFG['contract_filter']} and fiscal_year >= {MIN_YEAR}"})
        batch = d.get("data", [])
        rows += batch
        skip += 1000
        print(f"  awards {len(rows):,} / {d.get('count', 0):,}", flush=True)
        if len(batch) < 1000 or skip >= d.get("count", 0):
            return [r for r in rows if int(r.get("fiscal_year") or 0) >= MIN_YEAR]


def domain_projects(award_rows):
    """Projects that bought at least one domain item, ranked by domain spend."""
    spend = {}
    for r in award_rows:
        if in_domain(item_type(r.get("contract_description"), "Goods")):
            pid = r.get("project_id")
            spend[pid] = spend.get(pid, 0) + float(r.get("supplier_contract_amount_usd") or 0)
    return sorted(spend, key=lambda k: -spend[k]), spend


def project_by_id(pid, iso3):
    d = get_json(PROJECTS, {"format": "json", "id": pid})
    p = next(iter((d.get("projects") or {}).values()), None)
    return {**p, "country_iso3": iso3} if p else {"id": pid, "country_iso3": iso3}


def notices_goods(iso3, pid, ntype):
    return [n for n in notices_for_project(iso3, pid, ntype) if n.get("procurement_group") in ("GO", None)]


def _doc_row(x, code, iso3, pid):
    return {"doc_id": x["id"], "doc_type_code": code, "doc_type": x.get("docty"),
            "title": " ".join(x.get("display_title", "").split()), "doc_date": (x.get("docdt") or "")[:10],
            "country_iso3": iso3, "project_ids": x["project_ids"], "project_id": pid,
            "abstract": " ".join(((x.get("abstracts") or {}).get("cdata!") or "").split()),
            "pdf_url": x["pdfurl"], "landing_url": x.get("url")}


def download(doc):
    path = PDFS / f"{doc['doc_id']}.pdf"
    if path.exists() and path.stat().st_size > 0:
        return {**doc, "file": str(path), "status": "cached"}
    try:
        r = session.get(doc["pdf_url"].replace("http://", "https://", 1), timeout=300)
        r.raise_for_status()
        if not r.content.startswith(b"%PDF") or len(r.content) > 60 * 1024 * 1024:
            raise ValueError("not a PDF or too large")
        path.write_bytes(r.content)
        return {**doc, "file": str(path), "status": "downloaded"}
    except Exception as e:
        return {**doc, "file": None, "status": f"failed: {str(e)[:120]}"}


def cached(name, fn, refresh):
    path = RAW / f"{name}.json"
    if path.exists() and not refresh:
        return load_json(path)
    data = fn()
    save_json(data, path)
    return data


def main(refresh=False):
    print(f"scope: {CFG['label']}")
    if CFG["mode"] == "contract_first":
        return main_contract_first(refresh)
    domain = CFG["mode"] == "project_filter"
    project_rows = cached("projects", lambda: [p for iso3 in COUNTRIES for p in projects(iso3)], refresh)
    print(f"projects: {len(project_rows)}")
    pids = {p["id"] for p in project_rows}

    if domain:
        award_rows = cached("contract_awards", lambda: [r for iso3 in COUNTRIES for r in awards_for_projects(iso3, pids)], refresh)
    else:
        award_rows = cached("contract_awards", lambda: [r for iso3 in COUNTRIES for r in awards(iso3)], refresh)
    print(f"contract awards: {len(award_rows)} contract-supplier rows")

    def all_notices():
        if domain:
            jobs, fn = [(p["country_iso3"], p["id"], t) for p in project_rows for t in NOTICE_TYPES], notices_for_project
        else:
            jobs, fn = [(iso3, name, t) for iso3, (_, names) in COUNTRIES.items() for name in names for t in NOTICE_TYPES], notices
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            return [n for rows in pool.map(lambda j: fn(*j), jobs) for n in rows]
    notice_rows = cached("notices", all_notices, refresh)
    print(f"notices: {len(notice_rows)}")
    gov_rows = cached("governance", governance, refresh)
    print(f"governance observations: {len(gov_rows)}")

    if domain:
        activity = {}
        for r in award_rows + notice_rows:
            activity[r.get("project_id")] = activity.get(r.get("project_id"), 0) + 1
        docs = cached("documents_meta", lambda: documents_for_projects(project_rows, activity), refresh)
    else:
        docs = cached("documents_meta", lambda: documents({r.get("project_id") for r in award_rows}), refresh)
    fetch_documents(docs)


def main_contract_first(refresh):
    ref = country_reference()
    all_awards = cached("practice_awards", practice_awards, refresh)
    ranked, spend = domain_projects(all_awards)
    keep = set(ranked)
    award_rows = [r for r in all_awards if r.get("project_id") in keep]
    save_json(award_rows, RAW / "contract_awards.json")
    print(f"practice awards: {len(all_awards):,}; domain projects: {len(ranked)}; their awards: {len(award_rows):,}")

    # a project's country = the borrower country of most of its contracts (regional projects span several)
    by_proj = {}
    for r in award_rows:
        by_proj.setdefault(r.get("project_id"), []).append((ref.get(r.get("borrower_country_code")) or {}).get("iso3"))
    proj_iso3 = {pid: max(set(v), key=v.count) for pid, v in by_proj.items()}

    def fetch_projects():
        with cf.ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(lambda pid: project_by_id(pid, proj_iso3.get(pid)), ranked))
    project_rows = cached("projects", fetch_projects, refresh)
    print(f"projects: {len(project_rows)}")

    top = ranked[:CFG["max_projects"]]

    def all_notices():
        jobs = [(proj_iso3.get(pid), pid, t) for pid in top for t in NOTICE_TYPES]
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            return [n for rows in pool.map(lambda j: notices_goods(*j), jobs) for n in rows]
    notice_rows = cached("notices", all_notices, refresh)
    print(f"notices (Goods, top {len(top)} projects): {len(notice_rows):,}")
    gov_rows = cached("governance", lambda: governance({i for i in proj_iso3.values() if i}), refresh)
    print(f"governance observations: {len(gov_rows):,}")
    # documents: the biggest buyers (mostly recent, still active) plus the biggest CLOSED projects, which are the ones
    # with completion reports, IEG reviews and lessons learned
    closed = [p["id"] for p in sorted(project_rows, key=lambda p: -spend.get(p["id"], 0)) if p.get("status") == "Closed"]
    order = list(dict.fromkeys(ranked[:16] + closed[:CFG["max_doc_projects"] - 16]))
    priority = {pid: len(order) - i for i, pid in enumerate(order)}
    docs = cached("documents_meta", lambda: documents_for_projects(project_rows, priority), refresh)
    fetch_documents(docs)


def fetch_documents(docs):
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        docs = list(pool.map(download, docs))
    save_json(docs, RAW / "documents_meta.json")
    status = {}
    for d in docs:
        k = d["status"].split(":")[0]
        status[k] = status.get(k, 0) + 1
    print(f"documents: {len(docs)} {status}")


if __name__ == "__main__":
    main(refresh="--refresh" in sys.argv)
