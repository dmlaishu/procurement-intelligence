"""Builds the website's front page and guide:

    deliverables/site/index.html              the LabProcure home page (src/landing_template.html)
    deliverables/site/plan/index.html         the planner page (src/plan_template.html)
    deliverables/site/planner-data.json       the planner's model grid and evidence (src/build_planner_data.py)
    deliverables/site/learn/index.html        the four-stage guide (src/learn_template.html)

Every number on the page comes from the stored records, the saved models and their evaluation results.

    python src/build_landing.py            # then publish deliverables/site/ (GitHub Pages: the gh-pages branch)
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import build_planner_data  # noqa: E402
from procurement_demo.chatbot import ProcurementChat  # noqa: E402
from procurement_demo.common import GOLD, OUT  # noqa: E402
from procurement_demo.insights import Insights  # noqa: E402
from procurement_demo.lifecycle import Lifecycle  # noqa: E402

APP_URL = "https://procurement-intelligence-gkhea5oeh3lwtbbs6yganq.streamlit.app/"
REPO_URL = "https://github.com/dmlaishu/procurement-intelligence"
SITE = ROOT / "deliverables" / "site"


def main():
    stats = json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))
    evals = json.loads((OUT / "eval_results.json").read_text(encoding="utf-8"))
    bot = ProcurementChat()
    ins = Insights.load(country_name=bot.country_name)
    lc = Lifecycle(ins, bot.kb, GOLD)

    data = build_planner_data.build(ins, lc)
    (SITE / "planner-data.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    g, m = evals["guardrails"], ins.metrics["cycle"]
    fill = {
        "APP_URL": APP_URL, "REPO_URL": REPO_URL, "BUILT": date.today().strftime("%d %b %Y"),
        "CONTRACTS_FMT": f"{stats['contracts_in_domain']:,}", "VALUE_B": f"{stats['domain_usd'] / 1e9:.2f}",
        "COUNTRIES": stats["countries_in_domain"], "PROJECTS": stats["projects"], "REPORTS": stats["documents_parsed"],
        "EDGES": f"{stats['kg_edges']:,}", "N_TRAIN": f"{m['n_train']:,}", "N_TEST": m["n_test"],
        "N_ALL": f"{m['n_train'] + m['n_test']:,}",
        "SPLIT": date.fromisoformat(ins.metrics["split_date"]).strftime("%B %Y"),
        "COV": round(m["p80_coverage"] * 100), "MAE": round(m["mae_days_model_p50"]), "MAE_BASE": round(m["mae_days_global_median"]),
        "BENCH": f"±{round(m['mae_days_km_benchmark'])} days", "CAUGHT": g["caught"], "PLANTED": g["injected_hallucinations"],
    }
    def render(template):
        page = (ROOT / "src" / template).read_text(encoding="utf-8")
        for k, v in fill.items():
            page = page.replace("{{" + k + "}}", str(v))
        assert "{{" not in page, f"unfilled placeholder in {template}"
        return page

    page = render("landing_template.html")
    (SITE / "index.html").write_text(page, encoding="utf-8")
    (SITE / "plan").mkdir(exist_ok=True)
    (SITE / "plan" / "index.html").write_text(render("plan_template.html"), encoding="utf-8")

    learn = (ROOT / "src" / "learn_template.html").read_text(encoding="utf-8").replace("{{APP_URL}}", APP_URL)
    assert "{{" not in learn, "unfilled placeholder in the guide"
    (SITE / "learn").mkdir(exist_ok=True)
    (SITE / "learn" / "index.html").write_text(learn, encoding="utf-8")
    kb = (SITE / "planner-data.json").stat().st_size / 1024
    print(f"wrote {SITE}/index.html ({len(page) / 1024:.0f} KB), plan/index.html, planner-data.json ({kb:.0f} KB: {len(data['countries'])} countries x "
          f"{len(data['items'])} equipment types), learn/index.html")


if __name__ == "__main__":
    main()
