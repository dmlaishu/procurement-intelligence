# Databricks notebook source
# MAGIC %md
# MAGIC # Step 28 - Procurement Copilot: agentic retrieval with guardrails
# MAGIC
# MAGIC ```
# MAGIC new package ──▶ PLAN  sub-queries from the description + model risk flags
# MAGIC             ──▶ ACT   UC tools: proc_benchmark · @champion models · proc_similar_tenders · proc_supplier_market
# MAGIC             │         · proc_search_lessons · proc_search_passages · proc_project_evidence · proc_country_context
# MAGIC             ──▶ EVIDENCE REGISTRY  every tool row gets an id (B1, M1, T3, L2, P1, R1, G1, D1)
# MAGIC             ──▶ COMPOSE  each statement built from registered evidence, cites its ids
# MAGIC             ──▶ (optional) LLM rewrite for readability
# MAGIC             ──▶ VERIFY  citation exists · numbers appear in cited evidence · quotes verbatim → else DROP
# MAGIC             ──▶ brief + guardrail log → ops.proc_briefs ; user feedback → ops.proc_feedback
# MAGIC ```
# MAGIC The verifier (`verify_statements` in `20_proc_lib`) is the same code the local prototype runs and red-teams.

# COMMAND ----------

# MAGIC %pip install -q "mlflow-skinny[databricks]==3.16.1" "scikit-learn>=1.4,<1.8" "openai>=1.60,<3" "protobuf>=6,<7"
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json, re, uuid, datetime
import numpy as np
import pandas as pd
import mlflow
from databricks.sdk import WorkspaceClient

spark.sql(f"USE CATALOG {CATALOG}")
mlflow.set_registry_uri("databricks-uc")
w = WorkspaceClient()

def q(sql):
    return spark.sql(sql).toPandas()

def sql_str(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"

champions = {}
for short in ("cycle_p50", "cycle_p80", "single_bid", "rebid", "amendment"):
    try:
        champions[short] = mlflow.sklearn.load_model(f"models:/{PROC_MODEL_PREFIX}{short}@champion")
    except Exception as e:
        print("⚠ no champion for", short, str(e)[:100])
run = mlflow.search_runs(experiment_names=[f"/Users/{spark.sql('SELECT current_user()').first()[0]}/proc_lab_models"],
                         order_by=["start_time DESC"], max_results=1)
enc = mlflow.artifacts.load_dict(f"runs:/{run.iloc[0].run_id}/encoder.json") if len(run) else None
priors = {r.name: r.base for r in spark.sql("""SELECT 'single_bid' AS name, avg(cast(single_bid_flag AS DOUBLE)) AS base FROM gold.fact_tender
                                              UNION ALL SELECT 'rebid', avg(cast(rebid_flag AS DOUBLE)) FROM gold.fact_tender
                                              WHERE n_invitations > 0
                                              UNION ALL SELECT 'amendment', avg(cast(award_amended_flag AS DOUBLE))
                                              FROM gold.fact_tender WHERE award_observed""").collect()}

def score(pkg):
    row = pd.DataFrame([pkg])
    x = pd.DataFrame({c: pd.to_numeric(row.get(c), errors="coerce") for c in enc["num"]})
    for c in enc["cat"]:
        lv = {v: i for i, v in enumerate(enc["levels"][c])}
        x[c] = float(lv.get(str(pkg.get(c, "missing")), lv.get("other", np.nan)))
    out = {}
    for qn in ("p50", "p80"):
        if f"cycle_{qn}" in champions:
            out[f"cycle_{qn}"] = float(np.expm1(champions[f"cycle_{qn}"].predict(x)[0]))
    for name in ("single_bid", "rebid", "amendment"):
        if name in champions:
            p = champions[name].predict_proba(x)[0, 1]
            odds = p / (1 - p + 1e-9) * (priors[name] / (1 - priors[name]))      # undo class_weight='balanced'
            out[f"{name}_prob"], out[f"{name}_base_rate"] = float(odds / (1 + odds)), priors[name]
    return out

# COMMAND ----------

RISK_QUERIES = {"single_bid": "limited competition single bid few bidders non-responsive bids",
                "rebid": "re-tendering re-bid cancelled bidding bids exceeded estimate",
                "amendment": "contract amendment variation order change in quantities or specifications",
                "delay": "procurement delays slow contract award long evaluation no objection"}

def build_brief(pkg, rewrite=None):
    ev, st = Evidence(), []
    c, cat, m, item = pkg["country_iso3"], pkg["procurement_category"], pkg["method_code"], pkg.get("item_type")
    b = q(f"SELECT * FROM {CATALOG}.ai.proc_benchmark({sql_str(cat)}, {sql_str(item)}, {sql_str(c)}, {sql_str(m)})")
    if len(b):
        b = b.iloc[0]
        bid = ev.add("B", f"Kaplan-Meier benchmark ({b.level}): median {b.median_days:.0f} days, P80 {b.p80_days:.0f} days "
                          f"(80% of tenders awarded within it); n={b.n}, {b.n_awarded} awarded, {b.n - b.n_awarded} still open; "
                          f"awarded-only median {b.awarded_only_median:.0f} days.", source="ai.proc_benchmark")
        st.append(("Schedule", f"Comparable tenders ({b.level}) reach award in a median of {b.median_days:.0f} days and "
                               f"80% within {b.p80_days:.0f} days of the invitation.", [bid]))
    else:
        st.append(("Schedule", "Not enough comparable tenders for a reliable benchmark.", []))
    s = score(pkg)
    flags = []
    if s:
        mid = ev.add("M", f"Champion models: cycle P50 {s.get('cycle_p50', float('nan')):.0f} days, P80 {s.get('cycle_p80', float('nan')):.0f} days; "
                          f"single-bid {s.get('single_bid_prob', float('nan')):.0%} (base {s.get('single_bid_base_rate', float('nan')):.0%}); "
                          f"re-tender {s.get('rebid_prob', float('nan')):.0%} (base {s.get('rebid_base_rate', float('nan')):.0%}); "
                          f"amendment {s.get('amendment_prob', float('nan')):.0%} (base {s.get('amendment_base_rate', float('nan')):.0%}).",
                     source="models @champion")
        st.append(("Schedule", f"Model estimate for this package: {s['cycle_p50']:.0f} days (P50), {s['cycle_p80']:.0f} days (P80).", [mid]))
        for name in ("single_bid", "rebid", "amendment"):
            if s.get(f"{name}_prob", 0) >= 1.5 * s.get(f"{name}_base_rate", 1):
                flags.append(name)
                st.append(("Risks", f"Elevated {name.replace('_', '-')} risk: {s[name + '_prob']:.0%} vs base rate "
                                    f"{s[name + '_base_rate']:.0%}.", [mid]))
    sim = q(f"SELECT * FROM {CATALOG}.ai.proc_similar_tenders({sql_str(pkg['description'])}, {sql_str(cat)}, {sql_str(c)})")
    if len(sim):
        tids = [ev.add("T", f"{r.description[:140]} | {r.country_iso3} | {r.procurement_method} | "
                            f"{'%d days to award' % r.observed_days if pd.notna(r.observed_days) else 'no award yet'}"
                            f"{', %d bidder(s)' % r.n_bidders if pd.notna(r.n_bidders) else ''}", source="ai.proc_similar_tenders")
                for r in sim.head(6).itertuples()]
        top = sim.procurement_method.value_counts()
        did = ev.add("D", f"Derived from {len(sim)} similar tenders: method counts {top.to_dict()}; count {len(sim)}.", inputs=tids)
        st.append(("Market approach", f"Most similar past {cat} tenders used {top.index[0]} ({int(top.iloc[0])} of {len(sim)}).", tids + [did]))
    mk = q(f"SELECT * FROM {CATALOG}.ai.proc_supplier_market({sql_str(item)}, NULL, {sql_str(pkg.get('region'))})")
    if len(mk):
        where = pkg.get("region") or "all regions"
        mid2 = ev.add("D", f"Suppliers of {item} in {where} by contract value: " + "; ".join(
            f"{r.supplier} ({r.supplier_country}): {r.contracts} contracts, US${r.value_usd:,.0f}, {r.share:.0%} of value, "
            f"{r.countries} countries" for r in mk.head(5).itertuples()) + ".", source="ai.proc_supplier_market")
        t0 = mk.iloc[0]
        st.append(("Market approach", f"The largest supplier of {item} in {where} is {t0.supplier} ({t0.supplier_country}), with "
                                      f"{t0.share:.0%} of value from {t0.contracts} contracts.", [mid2]))
    queries = [f"{cat} {pkg['description']}"] + [RISK_QUERIES[f] for f in flags]
    seen = set()
    for qtext in queries:
        les = q(f"SELECT * FROM {CATALOG}.ai.proc_search_lessons({sql_str(qtext)}, {sql_str(c)})")
        for r in les.head(3).itertuples():
            if r.chunk_id in seen:
                continue
            seen.add(r.chunk_id)
            verified = q(f"""SELECT lesson, evidence_quote FROM gold.lessons WHERE chunk_id = {sql_str(r.chunk_id)}
                             AND quote_verified ORDER BY lesson_id LIMIT 1""")
            if verified.empty:
                continue                                   # only lessons with a verified quote are served
            lid = ev.add("L", r.chunk_text, source=f"{r.doc_type_code} {r.title}", page=r.page_start, url=r.landing_url)
            cite = [lid]
            line = f"{verified.lesson[0]} - “{verified.evidence_quote[0]}” ({r.doc_type_code}, {r.project_id}, p. {r.page_start})"
            pe = q(f"SELECT * FROM {CATALOG}.ai.proc_project_evidence({sql_str(r.project_id)})")
            if len(pe) and pe.conflict[0]:
                rid = ev.add("R", f"Ratings for {r.project_id}: {pe.trajectory[0]}; resolved {pe.resolved_rating[0]} "
                                  f"from {pe.resolved_from[0]}.", source="ai.proc_project_evidence")
                line += f" Supervision ratings fell to {pe.worst_isr[0]} before the final {pe.resolved_from[0]} rating of {pe.resolved_rating[0]}."
                cite.append(rid)
            st.append(("Lessons from completed projects", line, cite))
    if rewrite:
        st = [(sec, rewrite(t), ids) for sec, t, ids in st]
    kept, log = verify_statements(st, ev)
    return {"package": pkg, "scores": s, "flags": flags, "statements": kept, "guardrail_log": log, "evidence": ev.items}

# COMMAND ----------

# MAGIC %md
# MAGIC ### Optional LLM rewrite (readability only)
# MAGIC The model may rephrase a statement; it may not add facts. Anything it adds that the evidence does not support
# MAGIC (a number, a quote, a missing citation) is caught by the verifier and the statement is dropped.

# COMMAND ----------

LLM = resolve_llm_endpoint()
REWRITE = ("Rewrite the sentence for a procurement officer in plain English. Keep every number, quotation and parenthetical "
           "reference exactly. Do not add information. Reply with the sentence only.\n\nSentence: ")

def rewrite(text):
    """Through ai_query: on serverless, SQL AI functions reach the serving endpoints reliably (direct SDK calls time out)."""
    try:
        out = spark.sql(f"SELECT ai_query('{LLM}', :p, modelParameters => named_struct('temperature', 0.0, 'max_tokens', 300)) AS r",
                        args={"p": REWRITE + text}).first().r
        return (out or text).strip()
    except Exception:
        return text

# COMMAND ----------

PACKAGE = {**PROC_DEMO_PACKAGE, "open_notice_type": "Invitation for Bids",
           "log_contract_usd": float(np.log1p(PROC_DEMO_PACKAGE["contract_usd"])), "bid_window_days": 45, "open_month": 3,
           "env_category": "C", "log_commitment": float(np.log1p(1.5e8)), "project_age_days": 400, "kw_supply": 1, "kw_lot": 1,
           "desc_words": len(PROC_DEMO_PACKAGE["description"].split())}
brief = build_brief(PACKAGE, rewrite=rewrite)
for s_ in brief["statements"]:
    print(f"[{s_['section']}] {s_['text']}  {s_['citations']}")
print("\nDropped by verifier:", json.dumps(brief["guardrail_log"], indent=1)[:2000])

# COMMAND ----------

# MAGIC %md ## Log every brief and collect feedback (the continuous-learning inputs)

# COMMAND ----------

spark.sql("""CREATE TABLE IF NOT EXISTS ops.proc_briefs (brief_id STRING, created_ts TIMESTAMP, user STRING, package STRING,
  scores STRING, statements STRING, guardrail_log STRING, evidence_ids ARRAY<STRING>)
  COMMENT 'Every copilot brief: inputs, model scores, kept statements, statements dropped by the verifier'""")
spark.sql("""CREATE TABLE IF NOT EXISTS ops.proc_feedback (brief_id STRING, evidence_id STRING, created_ts TIMESTAMP, user STRING,
  rating STRING COMMENT 'useful | not_relevant | wrong', comment STRING,
  realised_award_date DATE COMMENT 'filled when the package is awarded - closes the loop for model evaluation')
  COMMENT 'User feedback on briefs and on individual evidence items'""")
brief_id = str(uuid.uuid4())
spark.createDataFrame([(brief_id, datetime.datetime.utcnow(), spark.sql("SELECT current_user()").first()[0],
                        json.dumps(PACKAGE), json.dumps(brief["scores"]), json.dumps(brief["statements"]),
                        json.dumps(brief["guardrail_log"]), list(brief["evidence"]))],
                      spark.table("ops.proc_briefs").schema).write.mode("append").saveAsTable("ops.proc_briefs")
log_event("proc_copilot", "brief", brief_id=brief_id, kept=len(brief["statements"]), dropped=len(brief["guardrail_log"]))
