# Databricks notebook source
# MAGIC %md
# MAGIC # Step 25 - Procurement: Vector Search index over project documents
# MAGIC Delta Sync index `ai.proj_doc_chunks_index` on `gold.proj_doc_chunks`, on the `proc_vs_endpoint` Vector Search
# MAGIC endpoint (created here on the first run). Embeddings: `databricks-gte-large-en` on `search_text` (header + chunk).
# MAGIC Filter columns synced for the copilot: `doc_type_code`, `section_kind`, `content_type`, `country_iso3`,
# MAGIC `project_id`, `major_sector`. Queries run HYBRID (semantic + keyword) so report names, P-codes and terms such as
# MAGIC "re-tender" match exactly.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

import json, time
import pandas as pd
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest, EmbeddingSourceColumn, EndpointType, PipelineType, VectorIndexType,
)

w = WorkspaceClient()
SOURCE = f"{CATALOG}.gold.proj_doc_chunks"
COLUMNS = ["chunk_id", "doc_id", "doc_type_code", "project_id", "project_name", "major_sector", "country_iso3", "title",
           "landing_url", "section_path", "section_kind", "content_type", "page_start", "page_end", "chunk_text"]
log_event("proc_vector_index", "started", index=PROC_VS_INDEX)

if VS_ENDPOINT not in {e.name for e in w.vector_search_endpoints.list_endpoints()}:
    print("creating endpoint", VS_ENDPOINT, "(takes a few minutes)")
    w.vector_search_endpoints.create_endpoint_and_wait(name=VS_ENDPOINT, endpoint_type=EndpointType.STANDARD)

existing = {i.name for i in w.vector_search_indexes.list_indexes(endpoint_name=VS_ENDPOINT)}
if PROC_VS_INDEX not in existing:
    w.vector_search_indexes.create_index(
        name=PROC_VS_INDEX, endpoint_name=VS_ENDPOINT, primary_key="chunk_id", index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=SOURCE, pipeline_type=PipelineType.TRIGGERED, columns_to_sync=COLUMNS,
            embedding_source_columns=[EmbeddingSourceColumn(name="search_text", embedding_model_endpoint_name=EMBEDDING_ENDPOINT)]))
    print("creating", PROC_VS_INDEX)
else:
    try:
        w.vector_search_indexes.sync_index(index_name=PROC_VS_INDEX)
        print("sync triggered (incremental via Change Data Feed)")
    except Exception as e:
        if "not ready to sync" not in str(e):
            raise
        print("a sync is already running; waiting for it")

# COMMAND ----------

deadline = time.time() + 20 * 60
while time.time() < deadline:
    st = w.vector_search_indexes.get_index(PROC_VS_INDEX).status
    print(time.strftime("%H:%M:%S"), "ready =", st.ready, "rows =", st.indexed_row_count)
    if st.ready:
        break
    time.sleep(60)
if not w.vector_search_indexes.get_index(PROC_VS_INDEX).status.ready:
    dbutils.notebook.exit("Index still building - re-run this notebook later")

# COMMAND ----------

def search(q, filters=None, k=5):
    res = w.vector_search_indexes.query_index(index_name=PROC_VS_INDEX, columns=COLUMNS, query_text=q, num_results=k,
                                              filters_json=json.dumps(filters) if filters else None, query_type="HYBRID")
    df = pd.DataFrame(res.result.data_array or [], columns=[c.name for c in res.manifest.columns])
    if not df.empty:
        df["chunk_text"] = df["chunk_text"].str.slice(0, 200) + "…"
    return df

display(search("laboratory equipment delivered but not installed or maintained, no service contract or trained staff",
               {"section_kind": ["lessons", "key_factors"]}))
display(search("delays in procuring diagnostic equipment and test kits", {"country_iso3": "KEN"}))

# COMMAND ----------

try:
    spark.sql(f"GRANT SELECT ON TABLE {PROC_VS_INDEX} TO `{GROUP_AI_APP}`")
except Exception as e:
    print("⚠ grant:", str(e)[:150])
log_event("proc_vector_index", "complete")
