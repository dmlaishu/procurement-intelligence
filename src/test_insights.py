"""Tests for procurement_demo/insights.py: the planning, comparison and estimate views and the citation links.

    python src/test_insights.py          # synthetic fixtures, then a smoke test on the committed gold tables
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from procurement_demo.insights import (Insights, build_package, evidence_rows, km, linkify,  # noqa: E402
                                       planning_request)

ok = True


def check(name, cond, got=None):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  (got {got!r})"))


def fixtures():
    rng = np.random.default_rng(0)
    contracts = pd.DataFrame({
        "wb_contract_number": range(40), "supplier_name": ["Alpha Ltd"] * 20 + ["Beta SA"] * 20,
        "supplier_key": ["alpha|KE"] * 20 + ["beta|FR"] * 20, "supplier_country": ["Kenya"] * 20 + ["France"] * 20,
        "country_iso3": ["KEN"] * 25 + ["NGA"] * 15, "project_id": ["P1"] * 25 + ["P2"] * 15,
        "item_type": ["Laboratory analyzers"] * 30 + ["Microscopy"] * 10,
        "amount_usd": np.arange(1, 41) * 1000.0, "in_domain": True,
        "signing_date": pd.date_range("2020-01-01", periods=40, freq="MS"),
        "procurement_method": "Request for Bids", "description": "analyzers",
        "tender_id": [f"T{i}" for i in range(40)]})
    tenders = pd.DataFrame({
        "tender_id": [f"T{i}" for i in range(40)], "country_iso3": contracts.country_iso3,
        "item_type": contracts.item_type, "method_code": ["RFB"] * 30 + ["RFQ"] * 10,
        "procurement_method": ["Request for Bids"] * 30 + ["Request for Quotations"] * 10,
        "procurement_category": "Goods", "in_domain": True,
        "cycle_days": [float(d) for d in rng.integers(30, 200, 40)], "observed_days": 100.0,
        "award_observed": [True] * 36 + [False] * 4, "n_bidders": [1.0, 3.0] * 20,
        "single_bid_flag": [True, False] * 20})
    projects = pd.DataFrame({"project_id": ["P1", "P2"], "project_name": ["Kenya Health", "Nigeria Labs"],
                             "country_iso3": ["KEN", "NGA"], "major_sector": "Health", "env_category": "B",
                             "es_risk_rating": "Moderate", "commitment_usd": [1e8, 2e8]})
    return Insights(contracts, tenders, projects, {"KEN": "Kenya", "NGA": "Nigeria"},
                    {"cycle": {"mae_days_model_p50": 47}})


def run_synthetic():
    print("— synthetic fixtures —")
    r = km(pd.DataFrame({"cycle_days": [10, 20, 30, 40], "award_observed": [True, True, False, True]}))
    check("km: n and events", r["n"] == 4 and r["n_awarded"] == 3, r)
    check("km: median is an observed award time", r["median"] in (20, 30, 40), r)

    ins = fixtures()
    check("countries ordered by contracts", ins.countries() == ["KEN", "NGA"], ins.countries())
    d = ins.duration("KEN", "Laboratory analyzers", "RFB")
    check("duration: narrowest scope used when n ≥ 15", d["narrowest"] and d["n"] == 25, d)
    d = ins.duration("NGA", "Microscopy", "RFQ")
    check("duration: falls back to a wider scope when the cell is small", not d["narrowest"] and d["n"] >= 10, d)
    v = ins.value_stats("KEN", "Microscopy")   # Kenya has no microscopy contracts in the fixture
    check("value: country cell with < 5 contracts falls back to all countries", "all countries" in v["scope"], v)
    c = ins.competition("KEN", "Laboratory analyzers")
    check("competition: single-bid share (13 of 25 alternating)", abs(c["single_bid_share"] - 13 / 25) < 1e-9, c)
    ex = ins.examples("KEN", "Laboratory analyzers", k=3)
    check("examples: newest first, with project link", ex["signed"].is_monotonic_decreasing
          and ex["project page"].str.contains("P1").all(), ex)
    cc = ins.country_compare(["KEN", "NGA"])
    check("country_compare: one row per country", list(cc.country) == ["Kenya", "Nigeria"], cc)
    check("country_compare: contracts counted", list(cc.contracts) == [25, 15], cc.contracts.tolist())
    st = ins.supplier_table()
    check("supplier_table: one row per supplier, value-sorted", list(st.supplier) == ["Beta SA", "Alpha Ltd"], st.supplier)
    check("supplier_table: competition from linked tenders", st.linked_tenders.tolist() == [20, 20], st.linked_tenders)

    p = build_package("KEN", "Laboratory analyzers", "RFB", 50_000, planned_open_date="2027-03-01", projects=ins.projects)
    check("package: open month from the planned date", p["open_month"] == 3, p)
    check("package: country's typical project features", p["env_category"] == "B" and "log_commitment" in p, p)
    check("package: supply keyword from the default description", p["kw_supply"] == 1, p)

    parsed = {"countries": ["KEN"], "item_types": ["Laboratory analyzers"], "domain_only": True}
    check("planning_request: planning question", planning_request(
        "I'm planning to purchase laboratory analyzers in Kenya. What should I expect?", parsed) is not None)
    check("planning_request: other questions go to the chatbot",
          planning_request("Who won the largest analyzer contract in Kenya?", parsed) is None)
    check("planning_request: needs one country", planning_request(
        "planning analyzers", {**parsed, "countries": ["KEN", "NGA"]}) is None)

    ev = {"C1": {"id": "C1", "project_id": "P1", "contract": "123", "text": "x"},
          "L1": {"id": "L1", "url": "https://d.org/a.pdf", "page": "7", "project_id": "P2", "text": "y"}}
    md = linkify("Paid US$5 and US$6 `C1` `L1` `X9`", ev)
    check("linkify: contract cites link to the project page", "project-detail/P1" in md, md)
    check("linkify: PDF cites open at the page", "a.pdf#page=7" in md, md)
    check("linkify: unknown ids left as they were", "`X9`" in md, md)
    check("linkify: dollar signs escaped (no LaTeX)", "US\\$5" in md, md)
    rows = evidence_rows(ev, {"P1": "Kenya Health"})
    check("evidence_rows: type, project name, contract", rows.loc[0, "type"] == "Contract"
          and rows.loc[0, "project"] == "P1 · Kenya Health" and rows.loc[0, "contract"] == "123", rows.to_dict())


def run_gold():
    gold = ROOT / "procurement_demo" / "data" / "health_lab" / "gold"
    if not (gold / "fact_contract.parquet").exists():
        print("— gold tables not found, skipped —")
        return
    print("— committed gold tables —")
    ins = Insights.load(gold)
    check("gold: lab contracts loaded", len(ins.c) == 2220, len(ins.c))
    for iso3 in ("IND", "KEN", "NGA"):
        o = ins.plan_overview(iso3, "Laboratory analyzers", "RFB")
        check(f"gold: plan overview for {iso3} has a duration and a value", o["duration"]["median"] is not None
              and o["value"]["n"] > 0, (o["duration"], o["value"]))
    cc = ins.country_compare(["IND", "KEN", "NGA"])
    check("gold: country comparison for India, Kenya, Nigeria", len(cc) == 3 and cc.contracts.gt(0).all(), cc)
    check("gold: model metrics available for the estimate view", ins.metrics.get("cycle", {}).get("mae_days_model_p50"))


if __name__ == "__main__":
    run_synthetic()
    run_gold()
    print("\nALL PASSED" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)
