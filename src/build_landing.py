"""Builds deliverables/site/index.html, the LabProcure landing page, from src/landing_template.html.

Every number and chart on the page is computed here from the stored records and models, for one worked example
(laboratory analyzers in Kenya): the timeline, the optimizer heatmap, the pre-mortem and the trust figures.

    python src/build_landing.py            # then publish deliverables/site/ (GitHub Pages: the gh-pages branch)
"""
import html
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from procurement_demo.common import GOLD, OUT  # noqa: E402
from procurement_demo.insights import Insights, build_package  # noqa: E402
from procurement_demo.lifecycle import Lifecycle  # noqa: E402
from procurement_demo.models import score  # noqa: E402
from procurement_demo.retrieval import KnowledgeBase  # noqa: E402

APP_URL = "https://procurement-intelligence-gkhea5oeh3lwtbbs6yganq.streamlit.app/"
REPO_URL = "https://github.com/dmlaishu/procurement-intelligence"
EXAMPLE = {"country": "KEN", "item": "Laboratory analyzers", "method": "RFB", "value": 250_000}
METHOD_NAMES = {"RFB": "Request for Bids", "RFQ": "Request for Quotations", "ICB": "International Competitive Bidding"}


def main():
    stats = json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))
    evals = json.loads((OUT / "eval_results.json").read_text(encoding="utf-8"))
    ins = Insights.load(country_name={"KEN": "Kenya"})
    lc = Lifecycle(ins, KnowledgeBase(GOLD), GOLD)
    e = EXAMPLE
    today = date.today()
    open_date = date(today.year + (today.month + 3 > 12), (today.month + 3 - 1) % 12 + 1, 1)
    s = score(build_package(e["country"], e["item"], e["method"], e["value"], planned_open_date=open_date, projects=ins.projects))
    tl = lc.timeline(e["country"], e["item"], open_date, s)
    opt, base = lc.optimize(e["country"], e["item"], e["method"], e["value"], open_date)
    real = opt[opt["within past use"]]
    top = real.iloc[0]
    grid = opt[opt.method == top.method]
    heat = [{"lots": int(r.lots), "window": int(r["bid window (days)"]), "p80": float(r["P80 days"]),
             "sb": float(r["single-bid risk"]),
             "best": bool(r.lots == top.lots and r["bid window (days)"] == top["bid window (days)"])}
            for _, r in grid.iterrows()]
    d80 = top["P80 days"] - base["P80 days"]
    winner = (f"<b>Best scored:</b> {METHOD_NAMES.get(top.method, top.method)}, {int(top['bid window (days)'])}-day window, "
              f"{int(top.lots)} lot(s): P80 <b>{top['P80 days']:.0f} days</b> ({d80:+.0f} vs a "
              f"{int(base['bid window (days)'])}-day {base.method} single lot) and single-bid risk "
              f"<b>{top['single-bid risk']:.0%}</b>. Based on {int(top['history (n tenders)']):,} past {top.method} tenders.")
    risks, lessons = lc.premortem(e["country"], e["item"], s)
    data = {
        "timeline": [{"step": r.step, "start": r.start.date().isoformat(), "end": r.end.date().isoformat(),
                      "kind": r.kind, "note": html.escape(r.note)} for r in tl.itertuples()],
        "heat": heat, "windows": sorted({c["window"] for c in heat}), "lots": sorted({c["lots"] for c in heat}),
        "winner": winner,
        "risks": [{"risk": html.escape(r["risk"]), "p": float(r["how often"]), "n": int(r["n"]), "scope": html.escape(r["scope"])}
                  for r in risks.to_dict("records")],
        "lesson": ({k: html.escape(str(v)) for k, v in lessons[1 if len(lessons) > 1 else 0].items()} if lessons else None),
    }
    g, m, le = evals["guardrails"], ins.metrics["cycle"], evals["lesson_extraction"]
    fill = {
        "APP_URL": APP_URL, "REPO_URL": REPO_URL, "BUILT": today.strftime("%d %b %Y"),
        "CONTRACTS": stats["contracts_in_domain"], "VALUE_B": round(stats["domain_usd"] / 1e9, 2),
        "COUNTRIES": stats["countries_in_domain"], "PROJECTS": stats["projects"], "REPORTS": stats["documents_parsed"],
        "EDGES": f"{stats['kg_edges']:,}", "EX_LABEL": f"{e['item']} in Kenya, US${e['value']:,.0f}",
        "OPT_N": len(opt), "OPT_METHOD": METHOD_NAMES.get(top.method, top.method),
        "CAUGHT": g["caught"], "PLANTED": g["injected_hallucinations"], "MAE": round(m["mae_days_model_p50"]),
        "MAE_BASE": round(m["mae_days_global_median"]), "QUOTES": round(le["verification_rate"] * 100),
        "DATA": json.dumps(data, ensure_ascii=False),
    }
    page = (ROOT / "src" / "landing_template.html").read_text(encoding="utf-8")
    for k, v in fill.items():
        page = page.replace("{{" + k + "}}", str(v))
    assert "{{" not in page, "unfilled placeholder"
    out = ROOT / "deliverables" / "site" / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) / 1024:.0f} KB): example {fill['EX_LABEL']}, best {fill['OPT_METHOD']}, "
          f"award P80 {s['cycle_p80']:.0f} days")


if __name__ == "__main__":
    main()
