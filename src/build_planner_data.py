"""Data for the website's planner (deliverables/site/index.html), which runs without a server.

The award-time and single-bid models are evaluated here, in advance, on a grid: the PLANNER_COUNTRIES (Kenya and
India) × every equipment type × Request for Bids / Request for Quotations × 8 budget levels × 4 bid
windows. The page interpolates between budget levels (on a log scale) and reads the rest directly, so the website shows
the same models as the app. The invitation month, which the app also uses, is left out here.

Next to the grid: the historical evidence for each country and equipment type (time to award, budget range, bid window,
bids, rejected bids, amended awards, contracted delivery period, suppliers, recent contracts), each with its n and the
scope it was taken from, and the models' measured accuracy.

    used by src/build_landing.py
"""
import numpy as np

from procurement_demo.insights import Insights, build_package, project_url
from procurement_demo.lifecycle import Lifecycle
from procurement_demo.models import score_many

MIN_CONTRACTS = 20
PLANNER_COUNTRIES = ["KEN", "IND"]      # the website planner covers these countries only
METHODS = {"RFB": "Request for Bids", "RFQ": "Request for Quotations"}
VALUES = [5_000, 15_000, 40_000, 100_000, 250_000, 600_000, 1_500_000, 4_000_000]
WINDOWS = [21, 30, 45, 60]


def _r(x, nd=0):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd) if nd else int(round(float(x)))


def build(ins: Insights, lc: Lifecycle):
    vc = ins.c.country_iso3.value_counts()
    countries = [k for k in PLANNER_COUNTRIES if vc.get(k, 0) >= MIN_CONTRACTS]
    items = ins.item_types()

    # ── model grid ──
    keys, pkgs = [], []
    for c in countries:
        for it in items:
            for m in METHODS:
                for v in VALUES:
                    for w in WINDOWS:
                        keys.append((c, it, m))
                        p = build_package(c, it, m, v, bid_window_days=w, projects=ins.projects)
                        p["open_month"] = np.nan
                        pkgs.append(p)
    sc = score_many(pkgs)
    grid = {}
    per = len(VALUES) * len(WINDOWS)
    for i in range(0, len(keys), per):
        c, it, m = keys[i]
        block = sc.iloc[i:i + per]
        grid.setdefault(c, {}).setdefault(it, {})[m] = [
            [_r(a), _r(b), _r(p * 100)] for a, b, p in zip(block.cycle_p50, block.cycle_p80, block.single_bid_prob)]

    # ── evidence ──
    def scoped(df, c, it, cond=None, min_n=10):
        sel, scope = lc._scoped(df, c, it, cond=cond, min_n=min_n)
        return sel, scope

    ev = {}
    for c in countries:
        for it in items:
            e = {}
            for m in METHODS:
                d = ins.duration(c, it, m)
                e.setdefault("dur", {})[m] = {"m": _r(d["median"]), "p80": _r(d["p80"]), "n": d["n"], "scope": d["scope"]}
            v = ins.value_stats(c, it)
            if v["n"]:
                e["val"] = {"m": _r(v["median"]), "p25": _r(v["p25"]), "p75": _r(v["p75"]), "n": v["n"], "scope": v["scope"]}
            bw, s = scoped(lc.t, c, it, cond=lambda d: d.bid_window_days.between(1, 180))
            if len(bw):
                e["bw"] = {"m": _r(bw.bid_window_days.median()), "n": len(bw), "scope": s}
            bd, s = scoped(lc.t, c, it, cond=lambda d: d.n_bidders.notna())
            if len(bd):
                rej = bd.rejection_reasons.fillna("").str.len().gt(0)
                reasons = bd.rejection_reasons[rej].str.split(";").explode().str.strip().str.lower().value_counts()
                e["bids"] = {"m": float(bd.n_bidders.median()), "sb": _r(bd.single_bid_flag.mean(), 3), "rej": _r(rej.mean(), 3),
                             "top": reasons.index[0] if len(reasons) else "", "n": len(bd), "scope": s}
            aw, s = scoped(lc.t, c, it, cond=lambda d: d.award_observed.astype(bool))
            if len(aw):
                e["amend"] = {"p": _r(aw.award_amended_flag.astype(bool).mean(), 3), "n": len(aw), "scope": s}
            tm, s = scoped(lc.t, c, it, cond=lambda d: d.award_observed.astype(bool) & d.observed_days.notna())
            if len(tm):
                e["slow"] = {"p": _r((tm.observed_days > 180).mean(), 3), "n": len(tm), "scope": s}
            dl, s = scoped(lc.t, c, it, cond=lambda d: d.contract_duration_months.between(0.01, 60))
            if len(dl):
                e["deliv"] = {"m": _r(dl.contract_duration_months.median(), 1), "n": len(dl), "scope": s}
            mix = ins.method_mix(c, it)
            e["mix"] = [[k, _r(r.share, 3)] for k, r in mix.head(3).iterrows()]
            e["sup"] = [[r["supplier"], r["supplier country"], _r(r["value (US$)"])] for _, r in ins.top_suppliers(c, it, k=3).iterrows()]
            ex = ins.examples(c, it, k=3)
            e["recent"] = [[r.contract, str(r.signed), r.supplier, _r(r["value (US$)"]), r.method, str(r.description)[:90],
                            r.project.split(" · ")[0], r["project page"]] for _, r in ex.iterrows()]
            ev.setdefault(c, {})[it] = e

    rng = lc.method_ranges()
    mt = ins.metrics
    return {
        "countries": [[c, ins.name(c), int(vc[c])] for c in countries], "items": items, "methods": METHODS,
        "values": VALUES, "windows": WINDOWS, "grid": grid, "ev": ev,
        "ranges": {m: [_r(a), _r(b)] for m, (a, b) in rng.items() if m in METHODS},
        "acc": {"mae": mt["cycle"]["mae_days_model_p50"], "base": mt["cycle"]["mae_days_global_median"],
                "bench": mt["cycle"]["mae_days_km_benchmark"], "cov": mt["cycle"]["p80_coverage"], "n": mt["cycle"]["n_test"],
                "split": mt["split_date"], "sb_auc": mt["single_bid"]["auc"],
                "sb_base": float(sc.single_bid_base_rate.iloc[0]) if "single_bid_base_rate" in sc else None},
    }


__all__ = ["build", "project_url"]
