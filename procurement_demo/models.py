"""Step 3 - timeline benchmarking and risk scoring (mirrors notebook 26).

Models (all use only information available when a package is being planned):
  * Kaplan-Meier benchmarks of invitation → award time, with still-open tenders as censored observations
  * Gradient-boosted quantile regression: P50 and P80 tender cycle time (days)
  * Gradient-boosted classifiers: single-responsive-bid risk, re-tender risk, award amendment risk
Validation is out-of-time: train on tenders opened before SPLIT_DATE, test on later ones.

    python -m procurement_demo.models
"""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_pinball_loss, roc_auc_score

from .common import GOLD, lib

SPLIT_DATE = pd.Timestamp("2024-01-01")
MODELS = GOLD / "models"
MODELS.mkdir(exist_ok=True)

CAT_FEATURES = ["procurement_category", "method_code", "country_iso3", "open_notice_type", "review_type",
                "env_category", "es_risk_rating", "major_sector", "state", "item_type", "region"]
NUM_FEATURES = ["log_contract_usd", "bid_window_days", "open_month", "log_commitment", "project_age_days",
                "prior_project_tenders", "prior_group_median_days", "prior_project_median_days", "desc_words",
                "kw_works_civil", "kw_supply", "kw_consulting", "kw_it", "kw_vehicle", "kw_lot",
                "cpia_public_sector_mgmt", "cpia_transparency_corruption", "cpia_budget_financial_mgmt",
                "wgi_gov_effectiveness", "wgi_regulatory_quality", "wgi_control_of_corruption", "cpia_missing"]
KEYWORDS = {"kw_works_civil": r"construct|rehabilit|road|bridge|civil|building|works",
            "kw_supply": r"supply|deliver|procure|purchase|equipment",
            "kw_consulting": r"consult|study|design|supervis|technical assistance|audit",
            "kw_it": r"software|system|ict|digital|computer|it ",
            "kw_vehicle": r"vehicle|motorcycle|truck",
            "kw_lot": r"\blot\b|lots"}


def prior_stats(t):
    """Leakage-safe history features: medians of tenders AWARDED BEFORE this tender opened, in the same
    country × category group and in the same project."""
    t = t.copy()
    done = t[t.award_observed & t.open_date.notna() & t.observed_days.notna()].sort_values("award_date")
    for key, cols in (("prior_group_median_days", ["country_iso3", "procurement_category"]),
                      ("prior_project_median_days", ["project_id"])):
        hist = {(k if isinstance(k, tuple) else (k,)): (g["award_date"].values, g["observed_days"].values)
                for k, g in done.groupby(cols)}
        vals = []
        for r in t[cols + ["open_date"]].itertuples(index=False):
            k = tuple(r[:-1])
            od = r[-1]
            if k not in hist or pd.isna(od):
                vals.append(np.nan)
                continue
            dates, days = hist[k]
            n = np.searchsorted(dates, np.datetime64(od))
            vals.append(float(np.median(days[:n])) if n >= 3 else np.nan)
        t[key] = vals
    counts = {pid: g["award_date"].values for pid, g in done.groupby("project_id")}
    t["prior_project_tenders"] = [
        int(np.searchsorted(counts[p], np.datetime64(od))) if p in counts and pd.notna(od) else 0
        for p, od in zip(t.project_id, t.open_date)]
    return t


def features(tenders, projects, gov):
    t = tenders[tenders.open_date.notna()].copy()
    t = t.merge(projects[["project_id", "commitment_usd", "approval_date", "env_category", "es_risk_rating", "major_sector"]],
                how="left", on="project_id")
    t = t.merge(gov.rename(columns={"year": "tender_year"}), how="left", on=["country_iso3", "tender_year"])
    t["log_contract_usd"] = np.log1p(t["contract_usd"])
    t["log_commitment"] = np.log1p(t["commitment_usd"])
    t["project_age_days"] = (t["open_date"] - t["approval_date"]).dt.days
    t["open_month"] = t["open_date"].dt.month
    desc = t["description"].fillna("").str.lower()
    t["desc_words"] = desc.str.split().str.len()
    for k, pat in KEYWORDS.items():
        t[k] = desc.str.contains(pat, regex=True).astype(int)
    t["review_type"] = t["review_type"].fillna("unknown")
    t = prior_stats(t)
    for c in CAT_FEATURES:
        t[c] = t[c].fillna("missing").astype(str)
        top = t[c].value_counts()
        t.loc[t[c].map(top) < 15, c] = "other"         # rare levels pooled
    for c in NUM_FEATURES:
        if c not in t:
            t[c] = np.nan
    return t


class Encoder:
    """Ordinal-encode categoricals for HistGradientBoosting's native categorical support."""

    def fit(self, df):
        self.levels = {c: sorted(df[c].unique()) for c in CAT_FEATURES}
        # numeric features with no variation (e.g. a score not published for any demo country) are dropped
        self.num = [c for c in NUM_FEATURES if df[c].nunique(dropna=True) > 1]
        return self

    def transform(self, df):
        x = df[self.num].astype(float).copy()
        for c in CAT_FEATURES:
            m = {v: i for i, v in enumerate(self.levels[c])}
            x[c] = df[c].map(m).astype(float)
        return x

    @property
    def cat_mask(self):
        return [False] * len(self.num) + [True] * len(CAT_FEATURES)


# Most specific first. The item-type levels let a cross-country domain (e.g. PCR systems bought in 40 countries)
# benchmark against the same kind of item when no single country has enough tenders.
BENCH_LEVELS = (("country+category+method", ["country_iso3", "procurement_category", "method_code"]),
                ("item_type+method", ["item_type", "method_code"]),
                ("item_type", ["item_type"]),
                ("category+method", ["procurement_category", "method_code"]),
                ("category", ["procurement_category"]))


def km_benchmarks(t):
    rows = []
    base = t[t.cycle_days.notna() & (t.cycle_days >= 0)]
    for level, cols in BENCH_LEVELS:
        for k, g in base.groupby(cols):
            if len(g) < 10:
                continue
            km = lib.kaplan_meier(g.cycle_days.tolist(), g.award_observed.tolist())
            key = dict(zip(cols, k if isinstance(k, tuple) else (k,)))
            rows.append({"level": level, **key, "n": km["n"], "n_awarded": km["n_events"],
                         "median_days": km["median"], "p80_days": km["p80"],
                         "naive_median_awarded_only": float(g[g.award_observed].cycle_days.median())})
    return pd.DataFrame(rows)


def km_lookup(bench, row):
    """Most specific benchmark with enough data: country+category+method → category+method → category."""
    for level, cols in BENCH_LEVELS:
        b = bench[bench.level == level]
        for c in cols:
            b = b[b[c] == row[c]]
        if len(b) and pd.notna(b.iloc[0].median_days):
            return b.iloc[0]
    return None


def main():
    tenders = pd.read_parquet(GOLD / "fact_tender.parquet")
    projects = pd.read_parquet(GOLD / "dim_project.parquet")
    gov = pd.read_parquet(GOLD / "country_governance_year.parquet")
    t = features(tenders, projects, gov)
    train_all, test_all = t[t.open_date < SPLIT_DATE], t[t.open_date >= SPLIT_DATE]
    enc = Encoder().fit(train_all)
    metrics = {"split_date": str(SPLIT_DATE.date()), "n_train_tenders": len(train_all), "n_test_tenders": len(test_all)}

    # Benchmarks built on training period only (for the backtest) and on everything (for serving)
    bench_train = km_benchmarks(train_all)
    bench_all = km_benchmarks(t)
    bench_all.to_parquet(GOLD / "tender_benchmarks.parquet", index=False)

    # ── cycle time quantile models ──
    obs = t[t.award_observed & (t.observed_days > 0)]
    tr, te = obs[obs.open_date < SPLIT_DATE], obs[obs.open_date >= SPLIT_DATE]
    # Test only on tenders that could have finished: opened ≥ 1 year before the data cut, so the
    # test set is not biased to fast tenders (slow ones in a recent window are still open)
    te = te[te.open_date <= te.award_date.max() - pd.Timedelta(days=365)] if len(te) > 200 else te
    xtr, xte = enc.transform(tr), enc.transform(te)
    ytr, yte = np.log1p(tr.observed_days.values), te.observed_days.values
    preds = {}
    for q in (0.5, 0.8):
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=400, learning_rate=0.05,
                                          max_leaf_nodes=31, min_samples_leaf=30, l2_regularization=1.0,
                                          categorical_features=enc.cat_mask, random_state=7)
        m.fit(xtr, ytr)
        preds[q] = np.expm1(m.predict(xte))
        joblib.dump(m, MODELS / f"cycle_p{int(q * 100)}.joblib")
    base_p50 = np.array([b["median_days"] if (b := km_lookup(bench_train, r)) is not None else np.nan
                         for _, r in te.iterrows()], dtype=float)
    base_p50 = np.where(np.isnan(base_p50), np.median(tr.observed_days), base_p50)
    naive = np.full(len(te), np.median(tr.observed_days))
    metrics["cycle"] = {
        "n_train": len(tr), "n_test": len(te),
        "mae_days_model_p50": round(mean_absolute_error(yte, preds[0.5]), 1),
        "mae_days_km_benchmark": round(mean_absolute_error(yte, base_p50), 1),
        "mae_days_global_median": round(mean_absolute_error(yte, naive), 1),
        "pinball50_model": round(mean_pinball_loss(yte, preds[0.5], alpha=0.5), 1),
        "pinball50_benchmark": round(mean_pinball_loss(yte, base_p50, alpha=0.5), 1),
        "p80_coverage": round(float(np.mean(yte <= preds[0.8])), 3),
        "median_actual_days": float(np.median(yte)),
        "within_30_days_share": round(float(np.mean(np.abs(yte - preds[0.5]) <= 30)), 3),
    }
    imp = permutation_importance(
        joblib.load(MODELS / "cycle_p50.joblib"), xte, np.log1p(yte), n_repeats=5, random_state=7,
        scoring="neg_mean_absolute_error")
    metrics["cycle"]["top_features"] = sorted(
        [(c, round(float(v), 4)) for c, v in zip(xte.columns, imp.importances_mean)], key=lambda x: -x[1])[:10]

    # ── risk classifiers ──
    for name, target, subset in (
        ("single_bid", "single_bid_flag", t[t.single_bid_flag.notna() & ~t.method_code.isin(["CDS", "DIR", "SSS", "other"])]),
        ("rebid", "rebid_flag", t[t.n_invitations > 0]),
        # public proxy for contract amendment: the award notice was re-issued or amended
        ("amendment", "award_amended_flag", t[t.award_observed]),
    ):
        tr_c, te_c = subset[subset.open_date < SPLIT_DATE], subset[subset.open_date >= SPLIT_DATE]
        y_tr, y_te = tr_c[target].astype(int).values, te_c[target].astype(int).values
        info = {"n_train": len(tr_c), "n_test": len(te_c), "base_rate_train": round(float(y_tr.mean()), 3),
                "base_rate_test": round(float(y_te.mean()), 3) if len(y_te) else None}
        if len(set(y_tr)) < 2 or len(te_c) < 50 or len(set(y_te)) < 2:
            info["status"] = "skipped: not enough positives"
            metrics[name] = info
            continue
        clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=30,
                                             l2_regularization=1.0, categorical_features=enc.cat_mask,
                                             class_weight="balanced", random_state=7)
        clf.fit(enc.transform(tr_c), y_tr)
        p = clf.predict_proba(enc.transform(te_c))[:, 1]
        # class_weight shifts probabilities; recalibrate to the training base rate with a simple prior correction
        odds = p / (1 - p + 1e-9) * (y_tr.mean() / (1 - y_tr.mean()))
        p_cal = odds / (1 + odds)
        top = np.argsort(-p)[: max(1, len(p) // 10)]
        info.update({"auc": round(roc_auc_score(y_te, p), 3), "brier": round(brier_score_loss(y_te, p_cal), 4),
                     "brier_base_rate": round(brier_score_loss(y_te, np.full(len(y_te), y_tr.mean())), 4),
                     "top_decile_rate": round(float(y_te[top].mean()), 3),
                     "lift_top_decile": round(float(y_te[top].mean() / max(y_te.mean(), 1e-9)), 2)})
        joblib.dump({"model": clf, "prior": float(y_tr.mean())}, MODELS / f"{name}.joblib")
        metrics[name] = info

    joblib.dump({"levels": enc.levels, "num": enc.num}, MODELS / "encoder.joblib")   # state, not the class (pickle-safe)
    t.to_parquet(GOLD / "tender_features.parquet", index=False)
    (GOLD / "model_metrics.json").write_text(json.dumps(metrics, indent=1, default=str), encoding="utf-8")
    print(json.dumps(metrics, indent=1, default=str))


def score(package: dict, t_features: pd.DataFrame | None = None):
    """Score one new package (dict with the CAT/NUM feature inputs we know at planning time)."""
    enc = Encoder()
    enc.__dict__.update(joblib.load(MODELS / "encoder.joblib"))
    row = pd.DataFrame([package])
    for c in CAT_FEATURES:
        v = str(row.at[0, c]) if c in row and pd.notna(row.at[0, c]) else "missing"
        row[c] = v if v in enc.levels[c] else ("other" if "other" in enc.levels[c] else "missing")
    for c in NUM_FEATURES:
        if c not in row:
            row[c] = np.nan
    x = enc.transform(row)
    out = {f"cycle_p{q}": float(np.expm1(joblib.load(MODELS / f"cycle_p{q}.joblib").predict(x)[0])) for q in (50, 80)}
    for name in ("single_bid", "rebid", "amendment"):
        path = MODELS / f"{name}.joblib"
        if path.exists():
            d = joblib.load(path)
            p = d["model"].predict_proba(x)[0, 1]
            odds = p / (1 - p + 1e-9) * (d["prior"] / (1 - d["prior"]))
            out[f"{name}_prob"] = float(odds / (1 + odds))
            out[f"{name}_base_rate"] = d["prior"]
    return out


if __name__ == "__main__":
    main()
