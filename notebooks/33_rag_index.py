# Databricks notebook source
# MAGIC %md
# MAGIC # Step 33 - Document Q&A: Vector Search index and the pipeline job
# MAGIC
# MAGIC Delta Sync index `ai.rag_chunks_index` on `gold.rag_chunks`: Databricks embeds `search_text` with
# MAGIC `databricks-gte-large-en` and keeps the index in step with the table through its Change Data Feed, so a sync only
# MAGIC embeds new or changed passages (a new country adds its rows; the others are not re-embedded). The `country` and
# MAGIC category columns are synced as filter columns.
# MAGIC
# MAGIC The last cell creates the job `proc_rag_pipeline` (31 → 32 → 33) with a `countries` parameter, so a new country
# MAGIC is: add it in 30, then run the job with `countries = <ISO-3>`.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json, time
import pandas as pd
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest, EmbeddingSourceColumn, EndpointType, PipelineType, VectorIndexType,
)

dbutils.widgets.text("countries", "KEN,IND", "Countries (ISO-3, comma-separated)")
w = WorkspaceClient()
SOURCE = f"{CATALOG}.gold.rag_chunks"
CAT_COLS = [rag_category_key(n) for n in RAG_CATEGORIES]
COLUMNS = ["chunk_id", "country", "doc_id", "doc_type", "title", "doc_date", "project_id", "project_name", "url", "section",
           "content_type", "page_start", "page_end", "text", *CAT_COLS]
log_event("rag_index", "started", index=RAG_INDEX)

if VS_ENDPOINT not in {e.name for e in w.vector_search_endpoints.list_endpoints()}:
    print("creating endpoint", VS_ENDPOINT, "(takes a few minutes)")
    w.vector_search_endpoints.create_endpoint_and_wait(name=VS_ENDPOINT, endpoint_type=EndpointType.STANDARD)

if RAG_INDEX not in {i.name for i in w.vector_search_indexes.list_indexes(endpoint_name=VS_ENDPOINT)}:
    w.vector_search_indexes.create_index(
        name=RAG_INDEX, endpoint_name=VS_ENDPOINT, primary_key="chunk_id", index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=SOURCE, pipeline_type=PipelineType.TRIGGERED, columns_to_sync=COLUMNS,
            embedding_source_columns=[EmbeddingSourceColumn(name="search_text", embedding_model_endpoint_name=EMBEDDING_ENDPOINT)]))
    print("creating", RAG_INDEX)
else:
    w.vector_search_indexes.sync_index(index_name=RAG_INDEX)
    print("sync triggered (only new or changed passages are embedded)")

# COMMAND ----------

deadline = time.time() + 30 * 60
while time.time() < deadline:
    st = w.vector_search_indexes.get_index(RAG_INDEX).status
    print(time.strftime("%H:%M:%S"), "ready =", st.ready, "rows =", st.indexed_row_count)
    if st.ready:
        break
    time.sleep(60)
if not w.vector_search_indexes.get_index(RAG_INDEX).status.ready:
    dbutils.notebook.exit("Index still building - re-run this notebook later")

# COMMAND ----------

# MAGIC %md ## Check: one filtered query per country

# COMMAND ----------

def search(q, filters, k=5):
    res = w.vector_search_indexes.query_index(index_name=RAG_INDEX, columns=COLUMNS, query_text=q, num_results=k,
                                              filters_json=json.dumps(filters), query_type="HYBRID")
    return pd.DataFrame(res.result.data_array or [], columns=[c.name for c in res.manifest.columns])

for iso3 in [c.strip().upper() for c in dbutils.widgets.get("countries").split(",") if c.strip()]:
    df = search("laboratory equipment to be procured", {"country": iso3})
    assert len(df) and set(df.country) == {iso3}, f"{iso3}: no passages, or passages from another country"
    display(df[["country", "doc_type", "title", "page_start", "section"]])

# COMMAND ----------

# MAGIC %md ## The pipeline job (31 → 32 → 33), one parameter: `countries`

# COMMAND ----------

from databricks.sdk.service import jobs
FOLDER = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get().rsplit("/", 1)[0]


def task(key, nb, deps=()):
    return jobs.Task(task_key=key, notebook_task=jobs.NotebookTask(notebook_path=f"{FOLDER}/{nb}", source=jobs.Source.WORKSPACE),
                     depends_on=[jobs.TaskDependency(task_key=d) for d in deps], max_retries=1, timeout_seconds=3600)

settings = dict(name="proc_rag_pipeline", max_concurrent_runs=1,
                parameters=[jobs.JobParameterDefinition(name="countries", default="KEN,IND")],
                tasks=[task("fetch", "31_rag_fetch"), task("process", "32_rag_process", ["fetch"]),
                       task("index", "33_rag_index", ["process"])])
existing = list(w.jobs.list(name="proc_rag_pipeline"))
if existing:
    w.jobs.reset(job_id=existing[0].job_id, new_settings=jobs.JobSettings(**settings))
    print("updated job", existing[0].job_id)
else:
    print("created job", w.jobs.create(**settings).job_id)
log_event("rag_index", "complete")
