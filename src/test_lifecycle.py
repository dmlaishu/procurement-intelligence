"""Tests for the lifecycle planner, the HTML helpers and the REST API, on the committed gold tables.

    python src/test_lifecycle.py          # needs fastapi + httpx for the API part (requirements_api.txt)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from procurement_demo import ui  # noqa: E402
from procurement_demo.common import GOLD  # noqa: E402
from procurement_demo.insights import Insights, build_package  # noqa: E402
from procurement_demo.lifecycle import STAGES, Fig, Lifecycle, plan_record  # noqa: E402
from procurement_demo.models import score  # noqa: E402
from procurement_demo.retrieval import KnowledgeBase  # noqa: E402

ok = True


def check(name, cond, got=None):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  (got {got!r})"))


def run_ui():
    print("— html helpers —")
    f = [Fig("Planning", "A <b>", "3 days", "Derived", "how & why", 12), Fig("Delivery", "B", "Missing", "Missing", "none")]
    h = ui.figures_html(f)
    check("figures_html escapes text", "A &lt;b&gt;" in h and "how &amp; why" in h, h)
    check("figures_html shows the n badge", "Derived · n=12" in h, h)
    check("figures_html shows Missing as a badge", "b-missing" in h, h)
    check("figures_html groups by stage", h.count('class="stage"') == 2, h)
    r = {"risk": "x", "how often": 1.7, "scope": "s", "n": 3, "signal": "", "watch for": "w"}
    check("risk_card clamps the bar to 100%", "width:100%" in ui.risk_card(1, r))


def run_lifecycle():
    print("— lifecycle on gold tables —")
    ins = Insights.load()
    kb = KnowledgeBase(GOLD)
    lc = Lifecycle(ins, kb, GOLD)
    for iso3, item in (("KEN", "Laboratory analyzers"), ("NGA", "Diagnostic imaging"), ("IND", "Microscopy")):
        pkg = build_package(iso3, item, "RFB", 200_000, planned_open_date="2027-03-01", projects=ins.projects)
        s = score(pkg)
        figs = lc.figures(iso3, item, "RFB", 200_000, s)
        check(f"{iso3}/{item}: every stage covered", {x.stage for x in figs} == set(STAGES), {x.stage for x in figs})
        check(f"{iso3}/{item}: every Derived figure has n ≥ 1",
              all(x.n and x.n > 0 for x in figs if x.basis == "Derived"), [x for x in figs if x.basis == "Derived" and not x.n])
        check(f"{iso3}/{item}: Missing figures carry no value", all(x.value == "Missing" for x in figs if x.basis == "Missing"))
        tl = lc.timeline(iso3, item, "2027-03-01", s)
        check(f"{iso3}/{item}: timeline is contiguous", all(tl.end.iloc[i] == tl.start.iloc[i + 1] for i in range(len(tl) - 1)), tl)
        check(f"{iso3}/{item}: P80 award after P50", tl.iloc[2].end >= tl.iloc[1].end, tl)
        rec = plan_record(ins.name(iso3), item, "RFB", 200_000, "2027-03-01", s, tl)
        check(f"{iso3}/{item}: plan record dates in order", rec["invitation"] <= rec["award by (P80)"] <= rec["delivered by"], rec)
    df, base = lc.optimize("KEN", "Laboratory analyzers", "RFB", 250_000, "2027-03-01")
    check("optimizer: 3 methods x 4 windows x 3 lot options scored", len(df) == 36, len(df))
    check("optimizer: your setup found", base is not None and base.method == "RFB" and base.lots == 1, base)
    check("optimizer: realistic setups ranked first", df["within past use"].iloc[0], df.head())
    big, _ = lc.optimize("KEN", "Laboratory analyzers", "RFB", 5_000_000, "2027-03-01")
    check("optimizer: RFQ not 'within past use' for a US$5M single lot",
          not big[(big.method == "RFQ") & (big.lots == 1)]["within past use"].any())
    s = score(build_package("KEN", "Laboratory analyzers", "RFB", 250_000, projects=ins.projects))
    risks, lessons = lc.premortem("KEN", "Laboratory analyzers", s)
    check("premortem: risks ranked by frequency", risks["how often"].is_monotonic_decreasing, risks)
    check("premortem: lessons carry a link and a page", lessons and all(x["url"] and x["page"] for x in lessons), lessons)


def run_api():
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("— fastapi not installed, API tests skipped —")
        return
    print("— REST API —")
    from api.main import app
    c = TestClient(app)
    check("GET /health", c.get("/health").json() == {"status": "ok"})
    o = c.get("/options").json()
    check("GET /options lists Kenya", "KEN" in o["countries"], o.keys())
    body = {"country": "KEN", "item_type": "Laboratory analyzers", "method": "RFB", "value_usd": 250000,
            "open_date": "2027-03-01"}
    r = c.post("/lifecycle", json=body)
    j = r.json()
    check("POST /lifecycle 200 with figures and timeline", r.status_code == 200 and j["figures"] and len(j["timeline"]) == 4,
          r.text[:300])
    check("POST /lifecycle hides the amendment model", not any("amendment" in k for k in j["scores"]), j["scores"])
    check("POST /optimize returns best setups", c.post("/optimize", json=body).json()["best"])
    check("POST /premortem returns risks", c.post("/premortem", json=body).json()["risks"])
    check("unknown country → 404", c.post("/lifecycle", json={**body, "country": "XXX"}).status_code == 404)
    check("bad review type → 422", c.post("/lifecycle", json={**body, "review": "Maybe"}).status_code == 422)
    a = c.post("/ask", json={"question": "Which supplier won the cheapest GeneXpert or PCR contract?"}).json()
    check("POST /ask answers with evidence", a["route"] == "rank_low" and a["evidence"], a.get("route"))
    import os
    os.environ["LABPROCURE_API_KEY"] = "secret"
    check("API key enforced when set", c.get("/options").status_code == 401
          and c.get("/options", headers={"X-API-Key": "secret"}).status_code == 200)
    del os.environ["LABPROCURE_API_KEY"]


if __name__ == "__main__":
    run_ui()
    run_lifecycle()
    run_api()
    print("\nALL PASSED" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)
