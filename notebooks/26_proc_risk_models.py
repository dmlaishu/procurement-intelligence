# Databricks notebook source
# MAGIC %md
# MAGIC # Step 26 - Procurement: timeline benchmarks and risk models (MLflow + Unity Catalog)
# MAGIC
# MAGIC | Model | Target | Method | Why |
# MAGIC |---|---|---|---|
# MAGIC | Benchmarks | invitation → award days | **Kaplan-Meier** per country × category × method, equipment type × method, equipment type, then coarser cells | open tenders are *censored*, not dropped (dropping them biases toward fast tenders) |
# MAGIC | `proc_cycle_p50` / `proc_cycle_p80` | cycle days | gradient-boosted **quantile** regression | planners need a range; P80 is the date to plan against |
# MAGIC | `proc_single_bid` | single responsive bid (competitive methods) | gradient-boosted classifier, prior-corrected | competition risk |
# MAGIC | `proc_rebid` | package re-tendered | gradient-boosted classifier, prior-corrected | re-tender risk |
# MAGIC | `proc_amendment` | award notice re-issued or amended | gradient-boosted classifier, prior-corrected | public proxy for contract amendment |
# MAGIC
# MAGIC Features are only what is known **when the package is planned** (equipment type, category, method, review type,
# MAGIC estimate, bid window, project, region and sector, governance scores, and *history up to that date*). The equipment-type
# MAGIC levels let a cross-country domain (PCR systems bought in 40 countries) benchmark against the same kind of item when
# MAGIC no single country has enough tenders. Validation is **out-of-time**.
# MAGIC The same algorithm runs locally in `procurement_demo/models.py` (verified results in `procurement_demo/output/`).

# COMMAND ----------

# MAGIC %pip install -q "mlflow-skinny[databricks]==3.16.1" "scikit-learn>=1.4,<1.8" "protobuf>=6,<7"
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json
import numpy as np
import pandas as pd
import mlflow
from mlflow.models import infer_signature
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score, brier_score_loss

spark.sql(f"USE CATALOG {CATALOG}")
mlflow.set_registry_uri("databricks-uc")
me = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{me}/proc_lab_models")
SPLIT_DATE = pd.Timestamp("2024-01-01")
# MLflow 3 saves scikit-learn models with skops by default, which serverless does not ship; cloudpickle needs nothing extra
SAVE_AS = mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE
log_event("proc_models", "started", split=str(SPLIT_DATE.date()))

# COMMAND ----------

# MAGIC %md ## 1. Feature table (planning-time features, leakage-safe history)

# COMMAND ----------

t = spark.sql("""
  SELECT t.*, p.commitment_usd, p.approval_date, p.env_category, p.es_risk_rating, p.major_sector, g.* EXCEPT (country_iso3, year)
  FROM gold.fact_tender t
  LEFT JOIN gold.dim_project p USING (project_id)
  LEFT JOIN gold.country_governance_year g ON g.country_iso3 = t.country_iso3 AND g.year = t.tender_year
  WHERE t.open_date IS NOT NULL
""").toPandas()
for c in ("open_date", "award_date", "approval_date"):
    t[c] = pd.to_datetime(t[c])

CAT = ["procurement_category", "method_code", "country_iso3", "open_notice_type", "review_type", "env_category",
       "es_risk_rating", "major_sector", "item_type", "region"]
GOV = [c for c in t.columns if c.startswith(("cpia_", "wgi_"))]
KW = {"kw_works_civil": r"construct|rehabilit|road|bridge|civil|building|works",
      "kw_supply": r"supply|deliver|procure|purchase|equipment",
      "kw_consulting": r"consult|study|design|supervis|technical assistance|audit",
      "kw_it": r"software|system|ict|digital|computer|it ", "kw_vehicle": r"vehicle|motorcycle|truck", "kw_lot": r"\blot\b|lots"}
t["log_contract_usd"] = np.log1p(t["contract_usd"].astype(float))
t["log_commitment"] = np.log1p(t["commitment_usd"].astype(float))
t["project_age_days"] = (t["open_date"] - t["approval_date"]).dt.days
t["open_month"] = t["open_date"].dt.month
desc = t["description"].fillna("").str.lower()
t["desc_words"] = desc.str.split().str.len()
for k, pat in KW.items():
    t[k] = desc.str.contains(pat, regex=True).astype(int)

# History known at the time the tender opened: medians of tenders AWARDED BEFORE that date (same country × category)
done = t[t.award_observed & t.observed_days.notna()].sort_values("award_date")
hist = {k: (g.award_date.values, g.observed_days.values) for k, g in done.groupby(["country_iso3", "procurement_category"])}
def prior_median(r):
    h = hist.get((r.country_iso3, r.procurement_category))
    if h is None:
        return np.nan
    n = np.searchsorted(h[0], np.datetime64(r.open_date))
    return float(np.median(h[1][:n])) if n >= 3 else np.nan
t["prior_group_median_days"] = [prior_median(r) for r in t.itertuples()]

NUM = ["log_contract_usd", "bid_window_days", "open_month", "log_commitment", "project_age_days", "desc_words",
       "prior_group_median_days", *KW, *GOV]
for c in CAT:
    t[c] = t[c].fillna("missing").astype(str)
    vc = t[c].value_counts()
    t.loc[t[c].map(vc) < 15, c] = "other"
train_all = t[t.open_date < SPLIT_DATE]
NUM = [c for c in NUM if train_all[c].nunique() > 1]
levels = {c: sorted(train_all[c].unique()) for c in CAT}

def X(df):
    x = df[NUM].astype(float).copy()
    for c in CAT:
        x[c] = df[c].map({v: i for i, v in enumerate(levels[c])}).astype(float)
    return x
CAT_MASK = [False] * len(NUM) + [True] * len(CAT)
spark.createDataFrame(t.astype(object).where(t.notna(), None)).write.mode("overwrite") \
    .option("overwriteSchema", "true").saveAsTable("gold.tender_features")

# COMMAND ----------

# MAGIC %md ## 2. Kaplan-Meier benchmarks (censoring-aware)

# COMMAND ----------

rows = []
base = t[t.cycle_days.notna() & (t.cycle_days >= 0)]
BENCH_LEVELS = (("country+category+method", ["country_iso3", "procurement_category", "method_code"]),
                ("item_type+method", ["item_type", "method_code"]),
                ("item_type", ["item_type"]),
                ("category+method", ["procurement_category", "method_code"]),
                ("category", ["procurement_category"]))
for level, cols in BENCH_LEVELS:
    for k, g in base.groupby(cols):
        if len(g) >= 10:
            km = kaplan_meier(g.cycle_days.tolist(), g.award_observed.tolist())
            rows.append({"level": level, **dict(zip(cols, k if isinstance(k, tuple) else (k,))), "n": km["n"],
                         "n_awarded": km["n_events"], "median_days": km["median"], "p80_days": km["p80"],
                         "naive_median_awarded_only": float(g[g.award_observed].cycle_days.median())})
bench = pd.DataFrame(rows)
spark.createDataFrame(bench.astype(object).where(bench.notna(), None)).write.mode("overwrite") \
    .option("overwriteSchema", "true").saveAsTable("gold.tender_benchmarks")
display(bench.sort_values("n", ascending=False).head(20))

# COMMAND ----------

# MAGIC %md ## 3. Train, validate out-of-time, log and register

# COMMAND ----------

obs = t[t.award_observed & (t.observed_days > 0)]
tr, te = obs[obs.open_date < SPLIT_DATE], obs[obs.open_date >= SPLIT_DATE]
te = te[te.open_date <= te.award_date.max() - pd.Timedelta(days=365)] if len(te) > 200 else te
metrics_all = {}
with mlflow.start_run(run_name="proc_models") as run:
    mlflow.log_params({"split_date": str(SPLIT_DATE.date()), "n_features": len(NUM) + len(CAT), "features": ",".join(NUM + CAT)})
    preds = {}
    for q in (0.5, 0.8):
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                          min_samples_leaf=30, l2_regularization=1.0, categorical_features=CAT_MASK,
                                          random_state=7).fit(X(tr), np.log1p(tr.observed_days))
        preds[q] = np.expm1(m.predict(X(te)))
        name = f"{PROC_MODEL_PREFIX}cycle_p{int(q * 100)}"
        mlflow.sklearn.log_model(m, name=f"cycle_p{int(q * 100)}", registered_model_name=name, serialization_format=SAVE_AS,
                                 signature=infer_signature(X(tr.head(5)), m.predict(X(tr.head(5)))))
    y = te.observed_days.values
    metrics_all.update({"cycle_mae_p50": mean_absolute_error(y, preds[0.5]),
                        "cycle_mae_global_median": mean_absolute_error(y, np.full(len(y), np.median(tr.observed_days))),
                        "cycle_p80_coverage": float(np.mean(y <= preds[0.8])), "cycle_n_test": len(te)})
    for name, target, subset in (("single_bid", "single_bid_flag",
                                  t[t.single_bid_flag.notna() & ~t.method_code.isin(["CDS", "DIR", "SSS", "other"])]),
                                 ("rebid", "rebid_flag", t[t.n_invitations > 0]),
                                 # public proxy for contract amendment: the award notice was re-issued or amended
                                 ("amendment", "award_amended_flag", t[t.award_observed])):
        a, b = subset[subset.open_date < SPLIT_DATE], subset[subset.open_date >= SPLIT_DATE]
        ya, yb = a[target].astype(int).values, b[target].astype(int).values
        if len(set(ya)) < 2 or len(set(yb)) < 2:
            metrics_all[f"{name}_status"] = "skipped"
            continue
        clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, min_samples_leaf=30, l2_regularization=1.0,
                                             categorical_features=CAT_MASK, class_weight="balanced", random_state=7).fit(X(a), ya)
        p = clf.predict_proba(X(b))[:, 1]
        metrics_all.update({f"{name}_auc": roc_auc_score(yb, p), f"{name}_base_rate": float(ya.mean())})
        mlflow.sklearn.log_model(clf, name=name, registered_model_name=f"{PROC_MODEL_PREFIX}{name}", serialization_format=SAVE_AS,
                                 signature=infer_signature(X(a.head(5)), clf.predict_proba(X(a.head(5)))))
    mlflow.log_metrics({k: float(v) for k, v in metrics_all.items() if isinstance(v, (int, float))})
    mlflow.log_dict({"levels": levels, "num": NUM, "cat": CAT}, "encoder.json")
print(json.dumps(metrics_all, indent=1, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Champion aliases (quality gate)
# MAGIC A new version becomes `@champion` only if it beats the benchmark and keeps P80 coverage in [0.70, 0.90].
# MAGIC The weekly job (Step 29) re-runs this notebook when drift or new completed tenders warrant it.

# COMMAND ----------

from mlflow import MlflowClient
client = MlflowClient()
gate = metrics_all["cycle_mae_p50"] < metrics_all["cycle_mae_global_median"] and 0.70 <= metrics_all["cycle_p80_coverage"] <= 0.90
for short in ("cycle_p50", "cycle_p80", "single_bid", "rebid", "amendment"):
    name = f"{PROC_MODEL_PREFIX}{short}"
    try:
        latest = max(int(v.version) for v in client.search_model_versions(f"name='{name}'"))
        client.set_registered_model_alias(name, "challenger", latest)
        if gate:
            client.set_registered_model_alias(name, "champion", latest)
        print(name, "v", latest, "→ champion" if gate else "→ challenger only")
    except Exception as e:
        print("⚠", name, str(e)[:150])
dbutils.jobs.taskValues.set(key="models_promoted", value=str(gate).lower())
log_event("proc_models", "complete", promoted=gate, **{k: v for k, v in metrics_all.items() if isinstance(v, (int, float))})
