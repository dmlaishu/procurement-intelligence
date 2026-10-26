"""Step 2 - silver/gold build: typed tables, entity resolution, tender linking, knowledge graph, document
structure, lessons and rating evidence. Mirrors notebooks 23-24 (Spark) in pandas.

    python -m procurement_demo.build
"""
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CATEGORY, CFG, GOLD, MIN_YEAR, RAW, country_reference, in_domain, item_type, lib, load_json

TODAY = pd.Timestamp(date.today())
CENSOR_WINDOW_DAYS = 730

# ── Sub-national and component levels ────────────────────────────────────────────────────────────
# Indian states: projects are mostly state-level; national programmes (PMGSY, highways) are executed by state agencies,
# so a tender's state comes from its implementing organisation first and its project second.
INDIA_STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
                "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
                "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
                "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Jammu and Kashmir", "Delhi", "Puducherry"]
STATE_ALIASES = {r"\bOrissa\b": "Odisha", r"\bUttaranchal\b": "Uttarakhand", r"\bChattisgarh\b": "Chhattisgarh",
                 r"\bJ\s?&\s?K\b": "Jammu and Kashmir", r"\bHP\b|\bHPRIDC\b|\bHPPWD\b": "Himachal Pradesh",
                 r"\bMP\b|\bMPRRDA\b|\bMPRDC\b": "Madhya Pradesh", r"\bUP\b|\bUPPWD\b|\bUPSHA\b": "Uttar Pradesh",
                 r"\bBRRDA\b": "Bihar", r"\bTNRSP\b": "Tamil Nadu", r"\bKSHIP\b": "Karnataka", r"\bGSHP\b": "Gujarat"}
NATIONAL = re.compile(r"\bnational\b|PMGSY|state support program|NRRDA|ministry of rural development|MoRTH|NHAI", re.I)


def state_of(*texts):
    """First Indian state named in the texts (by position), or None."""
    for t in texts:
        if not isinstance(t, str) or not t:
            continue
        hits = [(m.start(), s) for s in INDIA_STATES for m in [re.search(rf"\b{re.escape(s)}\b", t, re.I)] if m]
        hits += [(m.start(), s) for pat, s in STATE_ALIASES.items() for m in [re.search(pat, t)] if m]
        if hits:
            return min(hits)[1]
    return None


REGIONAL = [("middle east", "Middle East & North Africa"), ("north africa", "Middle East & North Africa"),
            ("africa", "Sub-Saharan Africa"), ("europe", "Europe & Central Asia"), ("central asia", "Europe & Central Asia"),
            ("latin america", "Latin America & Caribbean"), ("caribbean", "Latin America & Caribbean"),
            ("south asia", "South Asia"), ("east asia", "East Asia & Pacific"), ("pacific", "East Asia & Pacific")]


def regional_region(borrower):
    """Region of a regional (multi-country) borrower such as 'Eastern and Southern Africa' or 'Western and Central Africa'."""
    b = (borrower or "").lower()
    return next((reg for key, reg in REGIONAL if key in b), None)


def to_date(s):
    d = lib.parse_wb_date(s)
    return pd.Timestamp(d) if d else pd.NaT


def write(df, name):
    df.to_parquet(GOLD / f"{name}.parquet", index=False)
    print(f"  {name:<24} {len(df):>7} rows")
    return df


def category_from_text(c):
    c = (c or "").lower()
    if "consult" in c:
        return "Consultant Services"
    if "non" in c:
        return "Non-consulting Services"
    if "work" in c:
        return "Works"
    if "good" in c:
        return "Goods"
    return c.title() or None

# ── Silver ───────────────────────────────────────────────────────────────────────────────────────

def build_contracts():
    rows = []
    ref = country_reference()
    by_name = {c["name"].lower(): c for c in ref.values()}
    for r in load_json(RAW / "contract_awards.json"):
        # borrower: ISO-2 code, else the country name; regional projects carry a region code ("3E") and get only a region
        borrower = ref.get(r.get("borrower_country_code")) or by_name.get((r.get("borrower_country") or "").strip().lower()) or {}
        rows.append({
            "wb_contract_number": str(r.get("wb_contract_number")), "supplier_id": r.get("supplier_id"),
            "supplier_name": (r.get("supplier") or "").strip(), "supplier_country": r.get("supplier_country"),
            "supplier_country_code": r.get("supplier_country_code"), "project_id": r.get("project_id"),
            "project_name": r.get("project_name"), "country_iso3": borrower.get("iso3"),
            "region": borrower.get("region") or regional_region(r.get("borrower_country")),
            "fiscal_year": int(r.get("fiscal_year") or 0), "signing_date": to_date(r.get("contract_signing_date")),
            "procurement_category": category_from_text(r.get("procurement_category")),
            "procurement_method": " ".join((r.get("procurement_method") or "").split()),
            "review_type": r.get("review_type"),
            "amount_usd": float(r.get("supplier_contract_amount_usd") or r.get("total_contract_amount_usd") or 0) or np.nan,
            "description": " ".join((r.get("contract_description") or "").split()),
            "borrower_ref": r.get("borrower_contract_reference_number"),
        })
    df = pd.DataFrame(rows).drop_duplicates(["wb_contract_number", "supplier_id", "supplier_name"])
    df["borrower_ref_key"] = df["borrower_ref"].map(lib.norm_ref)
    df["supplier_name_key"] = df["supplier_name"].map(lib.norm_supplier)
    df["item_type"] = [item_type(d, c) for d, c in zip(df["description"], df["procurement_category"])]
    df["in_domain"] = df["item_type"].map(in_domain)
    return df


def build_notices():
    rows = []
    for n in load_json(RAW / "notices.json"):
        a = lib.parse_award_notice(n.get("notice_text")) if n["notice_type"] == "Contract Award" else None
        rows.append({
            "notice_id": n["id"], "country_iso3": n["country_iso3"], "notice_type": n["notice_type"],
            "notice_date": to_date(n.get("noticedate")), "deadline_date": to_date(n.get("submission_deadline_date")),
            "project_id": n.get("project_id"), "bid_ref": n.get("bid_reference_no"),
            "description": " ".join((n.get("bid_description") or "").split()),
            "procurement_category": CATEGORY.get(n.get("procurement_group"), n.get("procurement_group")),
            "method_code": n.get("procurement_method_code"),
            "method_name": " ".join((n.get("procurement_method_name") or "").split()),
            "implementing_org": n.get("contact_organization"),
            "rebid_text": bool(lib.is_rebid(n.get("bid_description"), n.get("bid_reference_no"))),
            "award_date": pd.Timestamp(a["notification_date"]) if a and a["notification_date"] else pd.NaT,
            "n_bidders": a["n_bidders"] if a else np.nan, "n_rejected": len(a["rejected"]) if a else np.nan,
            "rejection_reasons": "; ".join(a["rejection_reasons"]) if a else None,
            "awarded_names": "; ".join(b["name"] for b in a["awarded"]) if a else None,
            "contract_duration_months": a["contract_duration_months"] if a else np.nan,
            "notice_version": a["notice_version"] if a else np.nan,
        })
    df = pd.DataFrame(rows).drop_duplicates("notice_id")
    df["bid_ref_key"] = df["bid_ref"].map(lib.norm_ref)
    return df


def build_projects():
    rows = []
    for p in load_json(RAW / "projects.json"):
        mj = p.get("mjsector_namecode") or []
        rows.append({
            "project_id": p["id"], "country_iso3": p["country_iso3"], "project_name": p.get("project_name"),
            "approval_date": to_date(p.get("boardapprovaldate")), "closing_date": to_date(p.get("closingdate")),
            "status": p.get("status"), "lending_instrument": p.get("lendinginstr"),
            "commitment_usd": float(str(p.get("totalcommamt") or p.get("totalamt") or "0").replace(",", "") or 0) or np.nan,
            "env_category": p.get("envassesmentcategorycode"), "es_risk_rating": p.get("esrc_ovrl_risk_rate"),
            "major_sector": mj[0]["name"] if mj and mj[0].get("name") else None,
            "implementing_agency": p.get("impagency"), "project_url": p.get("url"),
            "objective": ((p.get("project_abstract") or {}).get("cdata!") or p.get("pdo") or "")[:1500],
        })
    df = pd.DataFrame(rows).drop_duplicates("project_id")
    region = {c["iso3"]: c["region"] for c in country_reference().values()}
    df["region"] = df["country_iso3"].map(region)
    india = df["country_iso3"] == "IND"
    df["state"] = None
    df.loc[india, "state"] = ["Multi-state (national)" if NATIONAL.search(n if isinstance(n, str) else "") else (state_of(n, a) or None)
                              for n, a in zip(df.loc[india, "project_name"], df.loc[india, "implementing_agency"])]
    return df


def build_governance():
    g = pd.DataFrame(load_json(RAW / "governance.json"))
    wide = g.pivot_table(index=["country_iso3", "year"], columns="indicator", values="value", aggfunc="first").reset_index()
    years = pd.MultiIndex.from_product([wide["country_iso3"].unique(), range(MIN_YEAR - 3, TODAY.year + 1)],
                                       names=["country_iso3", "year"]).to_frame(index=False)
    full = years.merge(wide, how="left").sort_values(["country_iso3", "year"])
    cols = [c for c in full.columns if c not in ("country_iso3", "year")]
    full[cols] = full.groupby("country_iso3")[cols].ffill(limit=3)    # annual scores, published with a lag
    full["cpia_missing"] = full["cpia_public_sector_mgmt"].isna().astype(int) if "cpia_public_sector_mgmt" in full else 1
    return full

# ── Entity resolution ────────────────────────────────────────────────────────────────────────────

def resolve_suppliers(contracts):
    names = (contracts.dropna(subset=["supplier_name_key"])
             .groupby(["supplier_name_key", "supplier_country_code"], dropna=False)
             .size().rename("n").reset_index())
    names["node"] = names["supplier_name_key"] + "|" + names["supplier_country_code"].fillna("")
    names["block"] = names["supplier_name_key"].str.split().str[0] + "|" + names["supplier_country_code"].fillna("")
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    merges = []
    for _, grp in names.groupby("block"):
        keys = grp.sort_values("n", ascending=False)[["node", "supplier_name_key"]].values.tolist()[:400]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                s = lib.name_similarity(keys[i][1], keys[j][1])
                if s >= 0.92:
                    a, b = find(keys[i][0]), find(keys[j][0])
                    if a != b:
                        parent[b] = a
                        merges.append((keys[j][1], keys[i][1], round(s, 3)))
    names["supplier_key"] = names["node"].map(find)
    stats = {"raw_names": int(contracts["supplier_name"].nunique()), "name_keys": len(names),
             "suppliers": int(names["supplier_key"].nunique()), "fuzzy_merges": len(merges),
             "fuzzy_examples": merges[:8]}
    return names[["node", "supplier_key"]], stats

# ── Tenders ──────────────────────────────────────────────────────────────────────────────────────

def build_tenders(notices):
    inv = notices[notices.notice_type.isin(["Invitation for Bids", "Request for Expression of Interest"])
                  & notices.bid_ref_key.notna()].sort_values("notice_date")
    awd = notices[(notices.notice_type == "Contract Award") & notices.bid_ref_key.notna()].sort_values("notice_date")
    first = lambda s: s.dropna().iloc[0] if s.notna().any() else None   # noqa: E731
    i = inv.groupby("bid_ref_key").agg(
        open_date=("notice_date", "min"), deadline_date=("deadline_date", "max"), n_invitations=("notice_id", "count"),
        rebid_text=("rebid_text", "max"), project_id=("project_id", first), country_iso3=("country_iso3", first),
        procurement_category=("procurement_category", first), procurement_method=("method_name", first),
        method_code=("method_code", first), description=("description", first),
        implementing_org=("implementing_org", first), open_notice_type=("notice_type", first))
    awd = awd.assign(award_date=awd.award_date.fillna(awd.notice_date))
    a = awd.groupby("bid_ref_key").agg(
        award_date=("award_date", "min"), n_award_notices=("notice_id", "count"), max_version=("notice_version", "max"),
        n_bidders=("n_bidders", "last"), n_rejected=("n_rejected", "last"), rejection_reasons=("rejection_reasons", "last"),
        awarded_names=("awarded_names", "last"), contract_duration_months=("contract_duration_months", "last"),
        a_project_id=("project_id", first), a_country=("country_iso3", first), a_cat=("procurement_category", first),
        a_method=("method_name", first), a_code=("method_code", first), a_desc=("description", first))
    t = i.join(a, how="outer").reset_index().rename(columns={"bid_ref_key": "tender_id"})
    for col, alt in (("project_id", "a_project_id"), ("country_iso3", "a_country"), ("procurement_category", "a_cat"),
                     ("procurement_method", "a_method"), ("method_code", "a_code"), ("description", "a_desc")):
        t[col] = t[col].fillna(t[alt])
    t = t.drop(columns=[c for c in t.columns if c.startswith("a_")])
    t["n_invitations"] = t["n_invitations"].fillna(0).astype(int)
    t["rebid_flag"] = (t["n_invitations"] > 1) | t["rebid_text"].fillna(False).astype(bool)
    t["award_amended_flag"] = (t["max_version"].fillna(0) > 0) | (t["n_award_notices"].fillna(0) > 1)
    t["award_observed"] = t["award_date"].notna()
    t["observed_days"] = (t["award_date"] - t["open_date"]).dt.days
    age = (TODAY - t["open_date"]).dt.days
    # No award notice after CENSOR_WINDOW_DAYS is not "still open": the tender was most likely cancelled or its award
    # never published. Those are excluded from cycle-time targets (and counted) instead of being censored, which
    # would stretch the benchmarks toward multi-year durations.
    t["outcome_unknown"] = ~t["award_observed"] & t["open_date"].notna() & (age > CENSOR_WINDOW_DAYS)
    t["cycle_days"] = np.where(t["award_observed"], t["observed_days"], age)
    t.loc[t["open_date"].isna() | t["outcome_unknown"], "cycle_days"] = np.nan
    t["bid_window_days"] = (t["deadline_date"] - t["open_date"]).dt.days
    competitive = ~t["procurement_method"].fillna("").str.contains("direct|single", case=False)
    t["single_bid_flag"] = np.where(t["n_bidders"].notna(), (t["n_bidders"] == 1) & competitive, np.nan)
    t["rejected_share"] = t["n_rejected"] / t["n_bidders"].replace(0, np.nan)
    t["tender_year"] = t["open_date"].fillna(t["award_date"]).dt.year
    keep = (t["tender_year"] >= MIN_YEAR) & (t["observed_days"].isna() | t["observed_days"].between(0, 1500))
    return t[keep].drop(columns=["max_version", "n_award_notices", "rebid_text"])


def link_contracts(contracts, tenders, supplier_map):
    c = contracts.merge(supplier_map, how="left",
                        left_on=contracts["supplier_name_key"].fillna("") + "|" + contracts["supplier_country_code"].fillna(""),
                        right_on="node").drop(columns=["node", "key_0"], errors="ignore")
    ids = set(tenders["tender_id"])
    c["tender_id"] = c["borrower_ref_key"].where(c["borrower_ref_key"].isin(ids))
    c["tender_link_method"] = np.where(c["tender_id"].notna(), "exact_key", None)
    c["tender_link_confidence"] = np.where(c["tender_id"].notna(), 1.0, np.nan)

    tok = lambda s: set(w for w in re.sub(r"[^a-z ]", " ", (s or "").lower()).split() if len(w) >= 3)  # noqa: E731
    t_by = defaultdict(list)
    for r in tenders.dropna(subset=["open_date", "description"]).itertuples():
        t_by[(r.project_id, r.procurement_category)].append((r.tender_id, r.open_date, tok(r.description)))
    n_fuzzy = 0
    for idx in c.index[c["tender_id"].isna()]:
        row = c.loc[idx]
        best, best_sim = None, 0.0
        ct = tok(row["description"])
        for tid, od, tt in t_by.get((row["project_id"], row["procurement_category"]), []):
            if pd.notna(row["signing_date"]) and od <= row["signing_date"] <= od + pd.Timedelta(days=540) and ct and tt:
                sim = len(ct & tt) / len(ct | tt)
                if sim > best_sim:
                    best, best_sim = tid, sim
        if best and best_sim >= 0.5:
            c.loc[idx, ["tender_id", "tender_link_method", "tender_link_confidence"]] = [best, "fuzzy_desc", round(best_sim, 3)]
            n_fuzzy += 1
    return c, n_fuzzy

# ── Documents: structure, ratings evidence, lessons ──────────────────────────────────────────────

def read_pdf(path):
    """[(page_num, text)] with line breaks kept (headings are detected line by line). Cached as JSON next to raw data."""
    cache = RAW / "pages" / (Path(path).stem + ".json")
    clean = lambda s: s.encode("utf-8", "ignore").decode("utf-8")      # noqa: E731  drop unpaired surrogates from PDF text
    if cache.exists():
        return [(n, clean(t)) for n, t in load_json(cache)]
    from pypdf import PdfReader
    pages = []
    try:
        reader = PdfReader(path)
    except Exception:
        return pages
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append((i, clean(re.sub(r"(\w)-\n(\w)", r"\1\2", text))))
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(pages), encoding="utf-8")
    return pages


def build_documents(projects):
    meta = [d for d in load_json(RAW / "documents_meta.json") if d.get("file")]
    pname = projects.set_index("project_id")["project_name"].to_dict()
    psector = projects.set_index("project_id")["major_sector"].to_dict()
    chunks, evidence, lessons, outcomes = [], [], [], []
    for d in meta:
        pages = read_pdf(d["file"])
        if not pages:
            continue
        pid = d["project_id"]
        header = (f"{d['doc_type_code']} | {pname.get(pid, d['title'])} ({pid}) | {d['country_iso3']} | "
                  f"{(psector.get(pid) or '')} | {d['doc_date'][:7]}")
        for k, c in enumerate(lib.section_chunks(pages)):
            cid = f"{d['doc_id']}_{k:04d}"
            chunks.append({"chunk_id": cid, "doc_id": d["doc_id"], "doc_type_code": d["doc_type_code"], "project_id": pid,
                           "country_iso3": d["country_iso3"], "doc_date": d["doc_date"], "title": d["title"],
                           "landing_url": d["landing_url"], "section_path": c["section_path"],
                           "section_kind": lib.section_kind(c["section_top"]), "content_type": c["content_type"],
                           "page_start": c["page_start"], "page_end": c["page_end"], "n_words": c["n_words"],
                           "chunk_text": c["text"], "search_text": f"{header} | {c['section_path']}\n{c['text']}"})
        full = "\n".join(t for _, t in pages)
        src = f"{d['doc_id']}"
        if d["doc_type_code"] == "ICR":
            ds = lib.parse_icr_datasheet("\n".join(t for _, t in pages[:20]))
            for r in ds["isr_ratings"]:
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "ISR",
                                 "date": r["date"], "rating": r["do_rating"], "source_id": f"{src}#ISR{r['seq']:02d}",
                                 "doc_id": d["doc_id"], "via": "ICR data sheet"})
            if ds["final_ratings"].get("outcome"):
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "ICR",
                                 "date": lib.parse_wb_date(d["doc_date"]), "rating": ds["final_ratings"]["outcome"],
                                 "source_id": src, "doc_id": d["doc_id"], "via": "ICR ratings summary"})
            outcomes.append({"project_id": pid, "doc_id": d["doc_id"], "restructurings": ds["restructurings"],
                             "closing_planned": ds["closing_planned"], "closing_actual": ds["closing_actual"],
                             "closing_extension_days": (ds["closing_actual"] - ds["closing_planned"]).days
                             if ds["closing_actual"] and ds["closing_planned"] else None,
                             "outcome_rating": ds["final_ratings"].get("outcome"),
                             "bank_performance": ds["final_ratings"].get("bank_performance"),
                             "me_quality": ds["final_ratings"].get("me_quality"), "n_isr_ratings": len(ds["isr_ratings"])})
        if d["doc_type_code"] == "ISR":
            r = lib.parse_isr_ratings(full[:6000])
            if r["do_current"]:
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "ISR",
                                 "date": r["isr_date"] or lib.parse_wb_date(d["doc_date"]), "rating": r["do_current"],
                                 "source_id": src, "doc_id": d["doc_id"], "via": "ISR overall ratings"})
        if d["doc_type_code"] == "ICRR":
            # IEG's ICR Review rates the project independently and its summary table sets the Bank's own ICR rating
            # beside it, so one review yields both sides of a self-evaluation vs independent-evaluation disagreement.
            ieg_rating, icr_rating = lib.parse_icrr_ratings(full)
            when = lib.parse_wb_date(d["doc_date"])
            if ieg_rating:
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "ICRR",
                                 "date": when, "rating": ieg_rating, "source_id": src, "doc_id": d["doc_id"],
                                 "via": "IEG ICR Review: outcome rating"})
            if icr_rating:
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "ICR",
                                 "date": when - pd.Timedelta(days=1).to_pytimedelta() if when else None, "rating": icr_rating,
                                 "source_id": f"{src}#ICR", "doc_id": d["doc_id"],
                                 "via": "ICR rating as reported in the IEG review"})
        if d["doc_type_code"] == "PPAR":
            rating = lib.parse_ppar_rating(full)
            if rating:
                evidence.append({"project_id": pid, "dimension": "development_objective", "source_type": "PPAR",
                                 "date": lib.parse_wb_date(d["doc_date"]), "rating": rating, "source_id": src,
                                 "doc_id": d["doc_id"], "via": "PPAR ratings table"})
    chunks = pd.DataFrame(chunks)

    # Lessons: numbered paragraphs of LESSONS sections, plus procurement-related findings in KEY FACTORS sections.
    # Local runs are rule-based; the Databricks track extracts with an LLM and keeps only quote-verified lessons.
    for doc_id, grp in chunks[chunks.section_kind.isin(["lessons", "key_factors"])].groupby("doc_id"):
        for kind, kgrp in grp.groupby("section_kind"):
            text = " ".join(_dedupe_overlap(kgrp.sort_values("chunk_id")["chunk_text"].tolist()))
            first = kgrp.iloc[0]
            paras = lib.lesson_paragraphs(text)
            for num, body in paras:
                if kind == "key_factors" and not re.search(lib.PROC_SIGNAL, body, re.I):
                    continue
                headline = lib.first_sentence(body)
                hit = kgrp[kgrp.chunk_text.str.contains(re.escape(headline[:60]), regex=True)]
                chunk = hit.iloc[0] if len(hit) else first
                lessons.append({
                    "lesson_id": f"{doc_id}_L{len(lessons):04d}", "doc_id": doc_id, "chunk_id": chunk["chunk_id"],
                    "project_id": first["project_id"], "country_iso3": first["country_iso3"],
                    "doc_type_code": first["doc_type_code"], "section_kind": kind, "paragraph": num,
                    "page_start": int(chunk["page_start"]), "lesson": headline, "detail": body[:1500],
                    "procurement_stage": ";".join(lib.classify(body, lib.STAGE_TERMS)) or "general",
                    "risk_category": ";".join(lib.classify(body, lib.RISK_TERMS)) or "other",
                    "procurement_related": bool(re.search(lib.PROC_SIGNAL, body, re.I)),
                    "evidence_quote": headline, "extraction_method": "rule_numbered_paragraph",
                    "quote_verified": lib.quote_in_source(headline, chunk["chunk_text"]),
                    "title": first["title"], "landing_url": first["landing_url"], "doc_date": first["doc_date"],
                })
    lessons = pd.DataFrame(lessons)
    evidence = pd.DataFrame(evidence)
    return chunks, lessons, evidence, pd.DataFrame(outcomes)


def _dedupe_overlap(texts, overlap=60):
    """Chunks overlap by `overlap` words; drop the repeated prefix when re-joining a section."""
    out = []
    for t in texts:
        words = t.split()
        if out:
            prev = out[-1].split()
            for k in range(min(overlap, len(prev), len(words)), 0, -1):
                if prev[-k:] == words[:k]:
                    words = words[k:]
                    break
        out.append(" ".join(words))
    return out


def resolve_ratings(evidence):
    rows = []
    for (pid, dim), grp in evidence.groupby(["project_id", "dimension"]):
        r = lib.resolve_rating_conflict(grp.to_dict("records"))
        if r:
            mid = [e for e in r["trajectory"] if e[0] == "ISR"]
            rows.append({"project_id": pid, "dimension": dim, **{k: r[k] for k in
                         ("resolved_rating", "resolved_from", "resolved_source_id", "conflict", "spread")},
                         "n_evidence": len(grp), "worst_isr": min((e[2] for e in mid), default=None,
                                                                   key=lambda x: lib.RATING_SCALE.get(x.lower(), 9)),
                         "trajectory": json.dumps(r["trajectory"])})
    return pd.DataFrame(rows)

# ── Knowledge graph ──────────────────────────────────────────────────────────────────────────────

def kg_edges(notices, tenders, contracts, projects, chunks, lessons):
    e = []
    tid = set(tenders.tender_id)
    for r in notices[notices.bid_ref_key.isin(tid)].itertuples():
        e.append(("NOTICE", r.notice_id, "ANNOUNCES", "TENDER", r.bid_ref_key, "exact_key", 1.0))
    for r in contracts.itertuples():
        if isinstance(r.tender_id, str):
            e.append(("TENDER", r.tender_id, "RESULTED_IN", "CONTRACT", r.wb_contract_number, r.tender_link_method,
                      r.tender_link_confidence))
        if isinstance(r.supplier_key, str):
            same = r.supplier_key == f"{r.supplier_name_key}|{r.supplier_country_code or ''}"
            e.append(("CONTRACT", r.wb_contract_number, "AWARDED_TO", "SUPPLIER", r.supplier_key,
                      "exact_key" if same else "fuzzy_name", 1.0 if same else 0.92))
        if isinstance(r.project_id, str):
            e.append(("CONTRACT", r.wb_contract_number, "FINANCED_BY", "PROJECT", r.project_id, "exact_key", 1.0))
    for r in tenders.dropna(subset=["project_id"]).itertuples():
        e.append(("TENDER", r.tender_id, "FINANCED_BY", "PROJECT", r.project_id, "exact_key", 1.0))
        if pd.notna(r.tender_year):
            e.append(("TENDER", r.tender_id, "HAS_CONTEXT", "GOVERNANCE", f"{r.country_iso3}:{int(r.tender_year)}", "temporal", 1.0))
    for r in tenders.dropna(subset=["state"]).itertuples():
        e.append(("TENDER", r.tender_id, "IN_STATE", "STATE", r.state, "implementing_org" if r.state_from == "agency" else "project",
                  0.9 if r.state_from == "agency" else 0.8))
    for r in tenders.itertuples():
        e.append(("TENDER", r.tender_id, "OF_TYPE", "ITEM_TYPE", r.item_type, "keyword", 0.7))
    for r in contracts.itertuples():
        e.append(("CONTRACT", r.wb_contract_number, "OF_TYPE", "ITEM_TYPE", r.item_type, "keyword", 0.7))
    for r in projects.dropna(subset=["state"]).itertuples():
        e.append(("PROJECT", r.project_id, "LOCATED_IN", "STATE", r.state, "name_match", 0.9))
    for state, iso3 in projects.dropna(subset=["state"]).drop_duplicates("state")[["state", "country_iso3"]].values:
        e.append(("STATE", state, "PART_OF", "COUNTRY", iso3, "reference", 1.0))
    for r in projects.itertuples():
        e.append(("PROJECT", r.project_id, "LOCATED_IN", "COUNTRY", r.country_iso3, "exact_key", 1.0))
    for iso3, region in projects[["country_iso3", "region"]].dropna().drop_duplicates().values:
        e.append(("COUNTRY", iso3, "IN_REGION", "REGION", region, "reference", 1.0))
    for key, ctry in contracts[["supplier_key", "supplier_country"]].dropna().drop_duplicates("supplier_key").values:
        e.append(("SUPPLIER", key, "BASED_IN", "COUNTRY", ctry, "exact_key", 1.0))
        if isinstance(r.major_sector, str):
            e.append(("PROJECT", r.project_id, "IN_SECTOR", "SECTOR", r.major_sector, "exact_key", 1.0))
    for r in chunks.drop_duplicates("doc_id").itertuples():
        e.append(("DOCUMENT", r.doc_id, "DESCRIBES", "PROJECT", r.project_id, "exact_key", 1.0))
    for r in lessons.itertuples():
        e.append(("LESSON", r.lesson_id, "FROM", "CHUNK", r.chunk_id, r.extraction_method, 1.0 if r.quote_verified else 0.5))
        e.append(("LESSON", r.lesson_id, "ABOUT", "PROJECT", r.project_id, "exact_key", 1.0))
        for stage in r.procurement_stage.split(";"):
            e.append(("LESSON", r.lesson_id, "AT_STAGE", "STAGE", stage, "keyword", 0.7))
    return pd.DataFrame(e, columns=["src_type", "src_id", "rel", "dst_type", "dst_id", "method", "confidence"])


def main():
    print("silver")
    contracts = build_contracts()
    notices = build_notices()
    projects = build_projects()
    # regional projects have no single country: take the region their contracts were signed in
    reg = contracts.dropna(subset=["region"]).groupby("project_id").region.agg(lambda s: s.mode().iloc[0])
    projects["region"] = projects["region"].fillna(projects["project_id"].map(reg))
    gov = write(build_governance(), "country_governance_year")
    print("entity resolution")
    supplier_map, sup_stats = resolve_suppliers(contracts)
    print("  ", {k: v for k, v in sup_stats.items() if k != "fuzzy_examples"})
    tenders = build_tenders(notices)
    contracts, n_fuzzy = link_contracts(contracts, tenders, supplier_map)
    agg = (contracts[contracts.tender_id.notna()].groupby("tender_id")
           .agg(review_type=("review_type", "first"), contract_usd=("amount_usd", "sum"), supplier_key=("supplier_key", "first")))
    tenders = tenders.merge(agg, how="left", left_on="tender_id", right_index=True)
    # State: the implementing agency's state first (national programmes run through state agencies), else the project's
    pstate = projects.set_index("project_id")["state"].to_dict()
    agency_state = [state_of(o) if c == "IND" else None for o, c in zip(tenders["implementing_org"], tenders["country_iso3"])]
    tenders["state_from"] = np.where(pd.notna(pd.Series(agency_state, index=tenders.index)), "agency", "project")
    tenders["state"] = pd.Series(agency_state, index=tenders.index).fillna(tenders["project_id"].map(pstate))
    tenders.loc[tenders["state"].isna(), "state_from"] = None
    tenders["item_type"] = [item_type(d, c) for d, c in zip(tenders["description"], tenders["procurement_category"])]
    tenders["in_domain"] = tenders["item_type"].map(in_domain)
    tenders["region"] = tenders["project_id"].map(projects.set_index("project_id")["region"])
    contracts["state"] = contracts["tender_id"].map(tenders.set_index("tender_id")["state"]).fillna(
        contracts["project_id"].map(pstate))
    proj_stats = (tenders.groupby("project_id").agg(n_tenders=("tender_id", "count"),
                                                     median_cycle_days=("observed_days", "median")))
    projects = projects.merge(proj_stats, how="left", left_on="project_id", right_index=True)
    print("gold")
    write(contracts, "fact_contract")
    write(tenders, "fact_tender")
    write(notices, "notices")
    write(projects, "dim_project")
    suppliers = (contracts.dropna(subset=["supplier_key"]).groupby("supplier_key")
                 .agg(supplier_name=("supplier_name", lambda s: s.value_counts().index[0]),
                      name_variants=("supplier_name", lambda s: sorted(set(s))[:10]),
                      n_contracts=("wb_contract_number", "nunique"), total_usd=("amount_usd", "sum"),
                      supplier_country=("supplier_country", "first")).reset_index())
    suppliers["name_variants"] = suppliers["name_variants"].map(json.dumps)
    write(suppliers, "dim_supplier")
    print("documents")
    chunks, lessons, evidence, outcomes = build_documents(projects)
    write(chunks, "proj_doc_chunks")
    write(lessons, "lessons")
    write(evidence.astype({"date": "datetime64[ns]"}), "rating_evidence")
    write(resolve_ratings(evidence), "project_ratings_resolved")
    write(outcomes, "project_outcomes")
    edges = write(kg_edges(notices, tenders, contracts, projects, chunks, lessons), "kg_edges")

    stats = {
        "contracts": len(contracts), "tenders": len(tenders),
        "tenders_with_cycle": int((tenders.award_observed & tenders.open_date.notna()).sum()),
        "tenders_censored": int((~tenders.award_observed & tenders.cycle_days.notna()).sum()),
        "tenders_outcome_unknown": int(tenders.outcome_unknown.sum()),
        "contract_tender_link_rate": round(float(contracts.tender_id.notna().mean()), 3),
        "contract_tender_links_exact": int((contracts.tender_link_method == "exact_key").sum()),
        "contract_tender_links_fuzzy": n_fuzzy,
        "contract_project_link_rate": round(float(contracts.project_id.isin(projects.project_id).mean()), 3),
        "projects": len(projects), "documents_parsed": int(chunks.doc_id.nunique()), "chunks": len(chunks),
        "lessons": len(lessons), "lessons_quote_verified": int(lessons.quote_verified.sum()) if len(lessons) else 0,
        "rating_evidence": len(evidence), **{f"supplier_{k}": v for k, v in sup_stats.items()},
        "kg_edges": len(edges), "kg_edge_types": {" ".join(k): int(v) for k, v in edges.groupby(["src_type", "rel", "dst_type"]).size().items()},
        "docs_linked_to_contract_projects": int(chunks.drop_duplicates("doc_id").project_id.isin(contracts.project_id).sum()),
        "section_kinds": Counter(chunks.section_kind).most_common(),
        "states": int(tenders["state"].nunique()), "tender_state_coverage": round(float(tenders["state"].notna().mean()), 3),
        "tender_state_from_agency": round(float((tenders["state_from"] == "agency").mean()), 3),
        "item_types": Counter(contracts["item_type"]).most_common(),
        "contracts_in_domain": int(contracts["in_domain"].sum()), "tenders_in_domain": int(tenders["in_domain"].sum()),
        "domain_usd": float(contracts.loc[contracts["in_domain"], "amount_usd"].sum()),
        "countries": int(contracts["country_iso3"].nunique()),
        "countries_in_domain": int(contracts.loc[contracts["in_domain"], "country_iso3"].nunique()),
        "doc_types": Counter(chunks.drop_duplicates("doc_id")["doc_type_code"]).most_common(),
    }
    (GOLD / "build_stats.json").write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k not in ("kg_edge_types", "supplier_fuzzy_examples")}, indent=1, default=str))


if __name__ == "__main__":
    main()
