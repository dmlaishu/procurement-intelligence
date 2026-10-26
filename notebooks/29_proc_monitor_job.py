# Databricks notebook source
# MAGIC %md
# MAGIC # Step 29 - Procurement: continuous learning loop, monitoring and the Lakeflow Job
# MAGIC
# MAGIC ```
# MAGIC  daily ─▶ 21 ingest (new notices, awards, projects) ─▶ 23 silver/gold + graph ─┐
# MAGIC weekly ─▶ 22 new PAD/ISR/ICR/PPAR ─▶ 24 chunks, lessons, ratings ─▶ 25 index sync ┤
# MAGIC                                                                                  ├─▶ 29 monitor: drift + realised outcomes
# MAGIC                                                     retrain_gate (condition) ◀───┘
# MAGIC                                                            │ true
# MAGIC                                                            ▼
# MAGIC                                         26 retrain → quality gate → @champion ─▶ 28 copilot uses new models
# MAGIC ```
# MAGIC **Retrain triggers** (any): ≥ 200 newly completed tenders since the last training, feature drift PSI > 0.2 on a key
# MAGIC feature, or live P80 coverage on realised awards outside [0.70, 0.90]. The quality gate in Step 26 decides promotion.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

import json, numpy as np, pandas as pd
spark.sql(f"USE CATALOG {CATALOG}")

# Realised outcomes: tenders awarded since the champion models were trained (MLflow run time)
import mlflow
mlflow.set_registry_uri("databricks-uc")     # serverless: set the registry before any MLflow call
me = spark.sql("SELECT current_user()").first()[0]
runs = mlflow.search_runs(experiment_names=[f"/Users/{me}/proc_lab_models"], order_by=["start_time DESC"], max_results=1)
trained_at = pd.to_datetime(runs.iloc[0].start_time) if len(runs) else pd.Timestamp("2000-01-01", tz="UTC")
new = spark.sql(f"""SELECT count(*) AS n FROM gold.fact_tender
                    WHERE award_observed AND award_date > to_date('{trained_at.date()}')""").first().n

def psi(a, b, bins=10):
    a, b = a.dropna(), b.dropna()
    if len(a) < 50 or len(b) < 50:
        return 0.0
    edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    pa = np.histogram(a, edges)[0] / len(a) + 1e-6
    pb = np.histogram(b, edges)[0] / len(b) + 1e-6
    return float(np.sum((pb - pa) * np.log(pb / pa)))

f = spark.table("gold.tender_features").select("open_date", "bid_window_days", "log_contract_usd", "prior_group_median_days").toPandas()
f["open_date"] = pd.to_datetime(f["open_date"])
recent = f[f.open_date > f.open_date.max() - pd.Timedelta(days=180)]
older = f[f.open_date <= f.open_date.max() - pd.Timedelta(days=180)]
drift = {c: round(psi(older[c], recent[c]), 3) for c in ("bid_window_days", "log_contract_usd", "prior_group_median_days")}

retrain = new >= 200 or max(drift.values()) > 0.2
status = {"new_completed_tenders": int(new), "psi": drift, "retrain": retrain, "trained_at": str(trained_at)}
print(json.dumps(status, indent=1))
dbutils.jobs.taskValues.set(key="retrain", value=str(retrain).lower())

# COMMAND ----------

# MAGIC %md ## KPI views (pilot dashboard)

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE VIEW ops.v_proc_kpis AS
SELECT
  (SELECT count(*) FROM ops.proc_briefs) AS briefs_generated,
  (SELECT count(DISTINCT user) FROM ops.proc_briefs WHERE created_ts > current_timestamp() - INTERVAL 7 DAYS) AS weekly_active_users,
  (SELECT avg(size(from_json(statements, 'array<string>'))) FROM ops.proc_briefs) AS avg_statements_per_brief,
  (SELECT sum(size(from_json(guardrail_log, 'array<string>'))) FROM ops.proc_briefs) AS statements_blocked_by_verifier,
  (SELECT avg(CASE WHEN rating = 'useful' THEN 1.0 ELSE 0.0 END) FROM ops.proc_feedback) AS evidence_usefulness_rate,
  (SELECT count_if(rating = 'wrong') FROM ops.proc_feedback) AS evidence_flagged_wrong,
  (SELECT count_if(quote_verified) / count(*) FROM gold.lessons) AS lesson_quote_verification_rate
""")
display(spark.table("ops.v_proc_kpis"))

# COMMAND ----------

# MAGIC %md ## Create / update the Lakeflow Job

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs
w = WorkspaceClient()
FOLDER = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get().rsplit("/", 1)[0]

def t(key, nb, deps=(), retries=0, timeout_min=60, outcome=None):
    return jobs.Task(task_key=key, notebook_task=jobs.NotebookTask(notebook_path=f"{FOLDER}/{nb}", source=jobs.Source.WORKSPACE),
                     depends_on=[jobs.TaskDependency(task_key=d, outcome=outcome) for d in deps],
                     max_retries=retries, timeout_seconds=timeout_min * 60)

tasks = [
    t("ingest_feeds", "21_ingest_procurement", retries=2),
    t("ingest_docs", "22_ingest_project_docs", ["ingest_feeds"], retries=2),
    t("build_graph", "23_proc_silver_gold", ["ingest_feeds", "ingest_docs"]),
    t("docs_lessons", "24_proc_docs_lessons", ["build_graph"], timeout_min=120),
    t("index_sync", "25_proc_vector_index", ["docs_lessons"]),
    t("monitor", "29_proc_monitor_job", ["index_sync"]),
    jobs.Task(task_key="retrain_gate", depends_on=[jobs.TaskDependency(task_key="monitor")],
              condition_task=jobs.ConditionTask(op=jobs.ConditionTaskOp.EQUAL_TO,
                                                left="{{tasks.monitor.values.retrain}}", right="true")),
    t("retrain", "26_proc_risk_models", ["retrain_gate"], outcome="true"),
]
settings = dict(name="proc_lab_pipeline", tasks=tasks, max_concurrent_runs=1,
                schedule=jobs.CronSchedule(quartz_cron_expression="0 0 3 ? * MON", timezone_id="UTC",
                                           pause_status=jobs.PauseStatus.PAUSED))
existing = [j for j in w.jobs.list(name="proc_lab_pipeline")]
if existing:
    w.jobs.reset(job_id=existing[0].job_id, new_settings=jobs.JobSettings(**settings))
    print("updated job", existing[0].job_id)
else:
    print("created job", w.jobs.create(**settings).job_id)
log_event("proc_monitor", "complete", **{k: v for k, v in status.items() if k != "psi"}, psi=json.dumps(drift))
