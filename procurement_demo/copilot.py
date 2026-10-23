"""Step 4 - the Procurement Copilot: evidence-first decision support for a new procurement package.

    package ─▶ PLAN: sub-queries from the package + model risk flags
            ─▶ GATHER: benchmarks (KM), model scores, similar past tenders, verified lessons, report passages,
                       rating trajectories (conflict-resolved) for the projects the lessons come from
            ─▶ COMPOSE: every statement is built from one evidence record and carries its [id]
            ─▶ VERIFY: citation present + id exists, numbers appear in the cited evidence, quotes verbatim
            ─▶ brief (markdown + JSON), guardrail log, abstentions

The composer is deterministic here (no LLM needed to run the demo). An optional `rewrite` callable (e.g. a
Databricks Foundation Model call) may polish the wording; its output goes through the same verifier and any
sentence that fails is dropped - the LLM can rephrase evidence but cannot add facts.

    python -m procurement_demo.copilot                      # runs the demo package
"""
import json
import re
from datetime import timedelta

import numpy as np
import pandas as pd

from .common import CFG, GOLD, OUT, item_type, lib

Evidence, verify = lib.Evidence, lib.verify_statements
from .models import km_lookup, score
from .retrieval import KnowledgeBase

RISK_QUERIES = {
    "single_bid": "limited competition single bid few bidders non-responsive bids market interest",
    "rebid": "re-tendering re-bid cancelled bidding non-responsive bids exceeded estimate",
    "delay": "procurement delays slow contract award long evaluation approval no objection",
}
MIN_BENCHMARK_N = 15


SATISFACTORY = {"highly satisfactory", "satisfactory", "moderately satisfactory"}


def rating_note(rating):
    """How to read a lesson from a project whose rating sources disagree (wording depends on where it ended up)."""
    src, final, worst = rating["resolved_from"], rating["resolved_rating"], rating.get("worst_isr")
    if final.lower() in SATISFACTORY and worst and worst.lower() not in SATISFACTORY:
        return (f"this project's supervision ratings fell to {worst} before the final {src} rating of {final}, so the "
                f"lesson reflects a recovery")
    if final.lower() not in SATISFACTORY:
        return (f"this project's ratings were mixed across sources, and the final {src} rating is {final}, so the lesson "
                f"comes from a project that underperformed")
    return f"sources disagree on this project's rating; the final {src} rating is {final}"


def fmt_days(x):
    return f"{int(round(x))}"


def build_brief(package, kb, rewrite=None):
    ev = Evidence()
    statements = []          # (section, text, [evidence ids])
    country, category = package["country_iso3"], package["procurement_category"]
    sector = package.get("major_sector")

    # ── 1. Schedule: benchmark + model ──
    bench = pd.read_parquet(GOLD / "tender_benchmarks.parquet")
    b = km_lookup(bench, package)
    if b is not None and b["n"] >= MIN_BENCHMARK_N:
        scope = {"country+category+method": f"{category} tenders using {package['method_code']} in {country}",
                 "item_type+method": f"{package.get('item_type')} tenders using {package['method_code']} (all countries)",
                 "item_type": f"{package.get('item_type')} tenders (all countries and methods)",
                 "category+method": f"{category} tenders using {package['method_code']} (all countries)",
                 "category": f"all {category} tenders"}[b["level"]]
        has_p80 = pd.notna(b["p80_days"])     # not reached when fewer than 80% of the cohort were ever awarded
        p80_txt = (f"P80 {fmt_days(b['p80_days'])} days (80% of tenders awarded within it)" if has_p80
                   else "P80 not reached (fewer than 80% of these tenders were ever awarded)")
        bid = ev.add("B", f"Kaplan-Meier benchmark for {scope}: median {fmt_days(b['median_days'])} days, "
                          f"{p80_txt} from invitation to "
                          f"award; n={int(b['n'])} tenders, "
                          f"{int(b['n_awarded'])} awarded, {int(b['n'] - b['n_awarded'])} still open (censored); "
                          f"awarded-only median {fmt_days(b['naive_median_awarded_only'])} days.",
                     source="gold.tender_benchmarks", level=b["level"])
        statements.append(("Schedule", f"Historical benchmark ({scope}): half of tenders reach award within "
                                       f"{fmt_days(b['median_days'])} days of the invitation"
                                       + (f" and 80% within {fmt_days(b['p80_days'])} days." if has_p80
                                          else "; fewer than 80% were ever awarded, so plan for slippage."), [bid]))
        gap = b["median_days"] - b["naive_median_awarded_only"]
        if gap > 5:
            gid = ev.add("D", f"Derived: censoring-aware median {fmt_days(b['median_days'])} - awarded-only median "
                              f"{fmt_days(b['naive_median_awarded_only'])} = {fmt_days(gap)} days.", inputs=[bid])
            statements.append(("Schedule", f"Counting only completed tenders would understate the median by "
                                           f"{fmt_days(gap)} days, because slow tenders are still open.", [bid, gid]))
    else:
        statements.append(("Schedule", "Not enough comparable tenders for a reliable benchmark "
                                       f"(fewer than {MIN_BENCHMARK_N}); treat the model estimate below with caution.", []))

    s = score(package)
    mid = ev.add("M", f"Model scores for this package: cycle P50 {fmt_days(s['cycle_p50'])} days, P80 "
                      f"{fmt_days(s['cycle_p80'])} days; single-bid probability {s.get('single_bid_prob', float('nan')):.0%} "
                      f"(base rate {s.get('single_bid_base_rate', float('nan')):.0%}); re-tender probability "
                      f"{s.get('rebid_prob', float('nan')):.0%} (base rate {s.get('rebid_base_rate', float('nan')):.0%}).",
                 source="models: cycle_p50, cycle_p80, single_bid, rebid (out-of-time validated)")
    statements.append(("Schedule", f"For this package the model estimates {fmt_days(s['cycle_p50'])} days to award "
                                   f"(P50) and {fmt_days(s['cycle_p80'])} days at the 80th percentile.", [mid]))
    # Cohort check: the benchmark cell ignores review type, which the model weighs heavily (prior review adds the
    # Bank's no-objection steps). Show the matching cohort so a model-benchmark gap is explained by evidence.
    rt = package.get("review_type")
    if rt in ("Prior", "Post"):
        t = kb.tenders
        coh = t[(t.country_iso3 == country) & (t.procurement_category == category) & (t.review_type == rt)
                & t.award_observed & (t.observed_days > 0)]
        if len(coh) >= 10:
            cid = ev.add("C", f"Awarded {category} tenders in {country} under {rt.lower()} review: n={len(coh)}, median "
                              f"{fmt_days(coh.observed_days.median())} days, 75th percentile "
                              f"{fmt_days(coh.observed_days.quantile(0.75))} days.", source="gold.fact_tender")
            txt = (f"Comparable {rt.lower()}-review {category} tenders in {country} took a median of "
                   f"{fmt_days(coh.observed_days.median())} days (n={len(coh)})")
            if b is not None and s["cycle_p50"] > 1.2 * b["median_days"]:
                txt += ", which is why the model estimate sits above the overall benchmark"
            statements.append(("Schedule", txt + ".", [cid]))
    if package.get("planned_open_date"):
        od = pd.Timestamp(package["planned_open_date"])
        p80_date = (od + timedelta(days=round(s["cycle_p80"]))).strftime("%d %b %Y")
        did = ev.add("D", f"Derived: planned invitation {od.strftime('%d %b %Y')} + model P80 "
                          f"{fmt_days(s['cycle_p80'])} days = {p80_date}.", inputs=[mid])
        statements.append(("Schedule", f"Plan the award milestone for {p80_date} or later (planned invitation "
                                       f"{od.strftime('%d %b %Y')} plus the P80 estimate) rather than the median.",
                           [did, mid]))

    flags = []
    if s.get("single_bid_prob", 0) >= 1.5 * s.get("single_bid_base_rate", 1):
        flags.append("single_bid")
        statements.append(("Risks", f"Elevated risk of a single responsive bid: {s['single_bid_prob']:.0%} versus a "
                                    f"base rate of {s['single_bid_base_rate']:.0%}.", [mid]))
    if s.get("rebid_prob", 0) >= 1.5 * s.get("rebid_base_rate", 1):
        flags.append("rebid")
        statements.append(("Risks", f"Elevated re-tender risk: {s['rebid_prob']:.0%} versus a base rate of "
                                    f"{s['rebid_base_rate']:.0%}.", [mid]))
    if b is not None and s["cycle_p50"] > 1.2 * b["median_days"]:
        flags.append("delay")
        statements.append(("Risks", f"Expected cycle is longer than the benchmark median ({fmt_days(s['cycle_p50'])} "
                                    f"vs {fmt_days(b['median_days'])} days).", [mid, bid]))
    if not flags:
        statements.append(("Risks", "No model risk score is materially above its base rate for this package.", [mid]))

    # ── 2. Market approach: what similar past tenders did ──
    sim = kb.similar_tenders(package["description"], category, country)
    if len(sim):
        tids = []
        for r in sim.head(6).itertuples():
            days = f"{fmt_days(r.observed_days)} days to award" if pd.notna(r.observed_days) else "no award yet"
            bidders = f", {int(r.n_bidders)} bidder(s)" if pd.notna(r.n_bidders) else ""
            tids.append(ev.add("T", f"{r.description[:140]} | {r.country_iso3} | {r.procurement_method} | "
                                    f"opened {r.open_date.date() if pd.notna(r.open_date) else 'n/a'} | {days}{bidders}"
                                    f"{' | re-tendered' if r.rebid_flag else ''}",
                               source="gold.fact_tender", tender_id=r.tender_id, project_id=r.project_id))
        top = sim.head(12)
        methods = top.procurement_method.value_counts()
        nb = top.n_bidders.dropna()
        txt = (f"Among the {len(top)} most similar past {category} tenders, the most common method was "
               f"{methods.index[0]} ({int(methods.iloc[0])} of {len(top)})")
        if len(nb):
            txt += f"; median number of bidders {nb.median():g}"
        statements.append(("Market approach", txt + ".", tids))
        stat_id = ev.add("D", f"Derived from {len(top)} similar tenders {', '.join(tids)}: methods "
                              f"{methods.to_dict()}; bidders median {nb.median() if len(nb) else 'n/a'}; "
                              f"count {len(top)}; top method count {int(methods.iloc[0])}.", inputs=tids)
        statements[-1] = (statements[-1][0], statements[-1][1], tids + [stat_id])

    # ── 3. Lessons and passages from evaluations ──
    queries = [f"{category} {package['description']}"] + [RISK_QUERIES[f] for f in flags] + \
              [f"procurement {category.lower()} lessons implementation delays"]
    lessons = kb.search_lessons(queries, country=country, sector=sector, k=6)
    for r in lessons.itertuples():
        rating = kb.rating_context(r.project_id)
        lid = ev.add("L", r.detail, source=f"{r.doc_type_code} {r.title}", doc_id=r.doc_id, chunk_id=r.chunk_id,
                     page=int(r.page_start), url=r.landing_url, quote=r.evidence_quote, project_id=r.project_id)
        cite = [lid]
        line = f"“{r.evidence_quote}” ({r.doc_type_code}, {r.project_id}, p. {int(r.page_start)})"
        if rating and rating.get("conflict"):
            traj = json.loads(rating["trajectory"])
            rid = ev.add("R", f"Rating evidence for {r.project_id}: " + "; ".join(f"{s_} {d}: {v}" for s_, d, v in traj)
                              + f". Resolved: {rating['resolved_rating']} (from {rating['resolved_from']}).",
                         source="gold.project_ratings_resolved", project_id=r.project_id)
            line += f" - note: {rating_note(rating)}."
            cite.append(rid)
        statements.append(("Lessons from completed projects", line, cite))
    if lessons.empty:
        statements.append(("Lessons from completed projects", "No verified lessons matched this package.", []))

    passages = kb.search_passages(queries[:2], country=country, sector=sector, k=4)
    used_quotes = {r.evidence_quote for r in lessons.itertuples()} if len(lessons) else set()
    for r in passages.itertuples():
        sent = _best_sentence(r.chunk_text, queries[0] + " " + " ".join(RISK_QUERIES[f] for f in flags))
        if not sent or any(sent[:80] in q or q[:80] in sent for q in used_quotes):
            continue                                     # already shown as a lesson
        pid = ev.add("P", r.chunk_text, source=f"{r.doc_type_code} {r.title}", doc_id=r.doc_id, page=int(r.page_start),
                     section=r.section_path, url=r.landing_url, quote=sent)
        statements.append(("Supporting evidence", f"“{sent}” ({r.doc_type_code}, {r.section_path.split(' > ')[-1].title()}, "
                                                  f"p. {int(r.page_start)})", [pid]))

    if rewrite is not None:
        statements = [(sec, rewrite(txt), ids) for sec, txt, ids in statements]
    kept, log = verify(statements, ev)
    return {"package": package, "scores": s, "flags": flags, "statements": kept, "guardrail_log": log,
            "evidence": ev.items}


def _best_sentence(text, query):
    from .retrieval import tokens
    q = set(tokens(query))
    best, best_s = None, 0
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if not 12 <= len(sent.split()) <= 60:
            continue
        sc = len(q & set(tokens(sent)))
        if sc > best_s:
            best, best_s = sent, sc
    return best if best_s >= 2 else None


def to_markdown(brief):
    p = brief["package"]
    lines = [f"# Procurement brief: {p['description']}",
             f"*{p['procurement_category']} · {p['method_code']} · {p['country_iso3']} · est. US${p.get('contract_usd', 0):,.0f}*", ""]
    sec = None
    for s in brief["statements"]:
        if s["section"] != sec:
            sec = s["section"]
            lines += ["", f"## {sec}"]
        lines.append(f"- {s['text']} " + " ".join(f"[{c}]" for c in s["citations"]))
    lines += ["", "## Evidence"]
    for e in brief["evidence"].values():
        loc = f" - p. {e['page']}" if e.get("page") else ""
        url = f" - {e['url']}" if e.get("url") else ""
        lines.append(f"- **[{e['id']}]** {e.get('source', '')}{loc}{url}: {e['text'][:300]}")
    if brief["guardrail_log"]:
        lines += ["", "## Guardrail log (statements removed before display)"]
        lines += [f"- {g['problems']}: {g['text'][:160]}" for g in brief["guardrail_log"]]
    return "\n".join(lines)


_demo = CFG["demo_package"]
DEMO_PACKAGE = {
    "description": _demo["description"],
    "procurement_category": _demo["procurement_category"], "method_code": _demo["method_code"],
    "country_iso3": _demo["country_iso3"],
    "open_notice_type": "Invitation for Bids", "review_type": _demo.get("review_type", "Prior"),
    "state": _demo.get("state"), "item_type": item_type(_demo["description"], _demo["procurement_category"]),
    "contract_usd": _demo["contract_usd"], "log_contract_usd": float(np.log1p(_demo["contract_usd"])),
    "bid_window_days": 30, "open_month": 3, "major_sector": _demo["major_sector"], "planned_open_date": "2027-03-01",
    "env_category": "B", "es_risk_rating": "Substantial", "log_commitment": float(np.log1p(300_000_000)),
    "project_age_days": 400, "kw_works_civil": int(_demo["procurement_category"] == "Works"),
    "kw_supply": int(_demo["procurement_category"] == "Goods"), "kw_lot": int(" lot" in _demo["description"].lower()),
    "desc_words": len(_demo["description"].split()),
}


def main():
    kb = KnowledgeBase(GOLD)
    brief = build_brief(DEMO_PACKAGE, kb)
    md = to_markdown(brief)
    (OUT / "demo_brief.md").write_text(md, encoding="utf-8")
    (OUT / "demo_brief.json").write_text(json.dumps(brief, indent=1, default=str), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
