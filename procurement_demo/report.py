"""Step 6 - one self-contained HTML report of the prototype run (data, graph, models, retrieval, guardrails, brief).

    python -m procurement_demo.report      → procurement_demo/output/prototype_report.html
"""
import html
import json
from datetime import date

import pandas as pd

from .common import CFG, GOLD, MIN_YEAR, OUT

CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#59636e;--line:#d0d7de;--soft:#f6f8fa;--accent:#0969da;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#30363d;--soft:#161b22;--accent:#4493f8;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}
body{background:var(--bg);color:var(--fg);font-family:Segoe UI,system-ui,Arial,sans-serif;max-width:1040px;margin:28px auto;padding:0 16px;line-height:1.5}
h1{font-size:1.6rem;margin-bottom:4px}h2{border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:34px;font-size:1.2rem}
.sub{color:var(--muted);margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.tile{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--soft)}.tile b{display:block;font-size:1.35rem}
.tile span{color:var(--muted);font-size:.85rem}table{border-collapse:collapse;width:100%;margin:10px 0;font-size:.92rem}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}th{background:var(--soft)}
.bar{height:10px;background:var(--accent);border-radius:3px}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
code{background:var(--soft);padding:1px 4px;border-radius:4px}.brief li{margin:4px 0}.cite{color:var(--accent);font-size:.85rem}
.wrap{overflow-x:auto}
"""


def tile(value, label):
    return f"<div class='tile'><b>{html.escape(str(value))}</b><span>{html.escape(label)}</span></div>"


def table(rows, headers):
    out = ["<div class='wrap'><table><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return "".join(out) + "</table></div>"


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def main():
    ev = json.loads((OUT / "eval_results.json").read_text(encoding="utf-8"))
    brief = json.loads((OUT / "demo_brief.json").read_text(encoding="utf-8"))
    b, m, r, g = ev["build"], ev["models"], ev["retrieval"], ev["guardrails"]
    bench = pd.read_parquet(GOLD / "tender_benchmarks.parquet")
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Procurement Intelligence Prototype</title><style>{CSS}</style></head><body>",
             "<h1>Procurement Intelligence prototype: results</h1>",
             f"<p class='sub'>Public World Bank data · {html.escape(CFG['label'])} (FY{MIN_YEAR} onwards) · run {date.today():%d %b %Y} · "
             "all figures measured by <code>python -m procurement_demo.run_all</code></p>",
             "<div class='grid'>",
             tile(f"{b['contracts']:,}", "contract-supplier rows"), tile(f"{b['tenders']:,}", "tenders (notice-linked)"),
             tile(f"{b['documents_parsed']}", "project reports parsed"), tile(f"{b['chunks']:,}", "section-aware chunks"),
             tile(f"{b['kg_edges']:,}", "knowledge-graph edges"), tile(pct(b["contract_tender_link_rate"]), "contracts linked to their tender"),
             "</div>"]

    parts.append("<h2>1. Ingestion and knowledge graph</h2>")
    parts.append(table([
        ["Contract → tender", f"{b['contract_tender_links_exact']:,} exact reference + {b['contract_tender_links_fuzzy']:,} fuzzy (project + category + date + description)", pct(b["contract_tender_link_rate"])],
        ["Contract → project", "Project ID", pct(b["contract_project_link_rate"])],
        ["Supplier resolution", f"{b['supplier_raw_names']:,} raw names → {b['supplier_name_keys']:,} normalised keys → {b['supplier_suppliers']:,} suppliers ({b['supplier_fuzzy_merges']} fuzzy merges)", "-"],
        ["Documents → transacting projects", f"{b['docs_linked_to_contract_projects']} of {b['documents_parsed']} reports belong to projects with contracts", pct(b['docs_linked_to_contract_projects'] / max(b['documents_parsed'], 1))],
        ["Tender outcomes", f"{b['tenders_with_cycle']:,} with invitation and award · {b['tenders_censored']:,} still open (censored) · {b['tenders_outcome_unknown']:,} no award after 2 years (excluded)", "-"],
    ], ["Link", "How", "Rate"]))
    ex = b.get("supplier_fuzzy_examples", [])[:5]
    if ex:
        parts.append("<p>Fuzzy supplier merges (examples): " + "; ".join(f"<code>{html.escape(a)}</code> → <code>{html.escape(c)}</code> ({s})" for a, c, s in ex) + "</p>")
    edge_rows = sorted(b["kg_edge_types"].items(), key=lambda kv: -kv[1])
    mx = max(v for _, v in edge_rows)
    parts.append(table([[html.escape(k), f"{v:,}", f"<div class='bar' style='width:{max(2, 100 * v / mx):.0f}%'></div>"] for k, v in edge_rows],
                       ["Edge type", "Edges", ""]))

    parts.append("<h2>2. Timeline benchmarks (Kaplan-Meier, censoring-aware)</h2>")
    top = bench[bench.level == "category+method"].sort_values("n", ascending=False).head(10)
    parts.append(table([[x.procurement_category, x.method_code, int(x.n), int(x.n - x.n_awarded), f"{x.median_days:.0f}",
                         f"{x.p80_days:.0f}" if pd.notna(x.p80_days) else "n/a", f"{x.naive_median_awarded_only:.0f}"]
                        for x in top.itertuples()],
                       ["Category", "Method", "Tenders", "Censored", "Median days", "P80 days", "Awarded-only median"]))

    c = m["cycle"]
    parts.append(f"<h2>3. Risk models (out-of-time: train before {m['split_date']}, test after)</h2>")
    parts.append(table([
        ["Cycle time P50 (days)", f"MAE <b>{c['mae_days_model_p50']}</b>", f"KM benchmark MAE {c['mae_days_km_benchmark']} · global median MAE {c['mae_days_global_median']}",
         f"{c['n_train']:,} / {c['n_test']:,}"],
        ["Cycle time P80", f"coverage <b>{c['p80_coverage']}</b>", "target 0.75-0.85", ""],
        ["Single responsive bid", f"AUC <b>{m['single_bid'].get('auc')}</b>", f"base rate {m['single_bid']['base_rate_train']} → {m['single_bid']['base_rate_test']} (shift) · top-decile lift {m['single_bid'].get('lift_top_decile')}",
         f"{m['single_bid']['n_train']:,} / {m['single_bid']['n_test']:,}"],
        ["Re-tender", f"AUC <b>{m['rebid'].get('auc')}</b>", f"rare: base rate {m['rebid']['base_rate_train']} · top-decile lift {m['rebid'].get('lift_top_decile')}",
         f"{m['rebid']['n_train']:,} / {m['rebid']['n_test']:,}"],
    ], ["Model", "Result", "Comparison", "Train / test"]))
    parts.append("<p>Top drivers of cycle time (permutation importance): " + ", ".join(f"<code>{k}</code>" for k, _ in c["top_features"][:6]) + "</p>")

    parts.append("<h2>4. Retrieval</h2>")
    tm = r.get("topic_macro", {})
    parts.append(table([[k, v["precision@5"], v["precision@10"], v["recall@20"], v["random_precision"], v["n_relevant"]] for k, v in r["topic"].items()]
                       + [["<b>macro</b>", f"<b>{tm.get('precision@5')}</b>", f"<b>{tm.get('precision@10')}</b>", f"<b>{tm.get('recall@20')}</b>",
                           tm.get("random_precision"), ""]],
                       ["Risk topic query", "Precision@5", "Precision@10", "Recall@20 (normalised)", "Random precision", "Relevant lessons"]))
    parts.append("<p class='sub'>Relevance labels are the lessons' keyword-derived risk categories, so this measures ranking "
                 "against a proxy; a curator-labelled set replaces it in the pilot.</p>")
    pl = r["project_link"]
    parts.append(f"<p>Structured → unstructured link test: a project's own tender descriptions retrieve that project's reports in the "
                 f"top 10 for <b>{pct(pl['hit@10'])}</b> of {pl['queries']} projects (MRR@10 {pl['mrr@10']}).</p>")

    parts.append("<h2>5. Guardrails</h2>")
    le = ev["lesson_extraction"]
    parts.append(table([
        ["Lesson quotes verified against source", f"{le['quote_verified']} / {le['extracted']}", pct(le["verification_rate"])],
        ["Statements shown with a valid citation", f"{g['statements_kept']} statements in {g['briefs']} briefs", pct(g["kept_with_citation_share"])],
        ["Statements blocked by the verifier (organic)", str(g["statements_dropped_by_verifier"]), ""],
        ["Injected hallucinations caught (fake number, fake quote, uncited claim, unknown citation)", f"{g['caught']} / {g['injected_hallucinations']}", pct(g["catch_rate"])],
        ["P80 coverage on the briefs' packages", "", str(g["p80_coverage_on_briefs"])],
        ["Projects with conflicting supervision vs completion ratings", f"{ev['conflicts']['with_conflict']} of {ev['conflicts']['projects_with_rating_evidence']}", ""],
    ], ["Check", "Count", "Rate"]))

    parts.append("<h2>6. Example brief</h2><div class='brief'>")
    p = brief["package"]
    parts.append(f"<p><b>{html.escape(p['description'])}</b> · {p['procurement_category']} · {p['method_code']} · {p['country_iso3']} · est. US${p.get('contract_usd', 0):,.0f}</p>")
    sec = None
    for s in brief["statements"]:
        if s["section"] != sec:
            if sec:
                parts.append("</ul>")
            sec = s["section"]
            parts.append(f"<h3>{html.escape(sec)}</h3><ul>")
        parts.append(f"<li>{html.escape(s['text'])} <span class='cite'>{' '.join('[' + c + ']' for c in s['citations'])}</span></li>")
    parts.append("</ul></div><h3>Evidence</h3>")
    rows = []
    for e in brief["evidence"].values():
        link = f"<a href='{html.escape(e['url'])}'>doc</a>" if e.get("url") else ""
        rows.append([f"<b>{e['id']}</b>", html.escape(e.get("source", "")), str(e.get("page", "")), html.escape(e["text"][:260]), link])
    parts.append(table(rows, ["Id", "Source", "Page", "Evidence", ""]))
    parts.append("</body></html>")
    (OUT / "prototype_report.html").write_text("".join(parts), encoding="utf-8")
    print("wrote", OUT / "prototype_report.html")


if __name__ == "__main__":
    main()
