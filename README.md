# Procurement Intelligence for Laboratory and Diagnostic Equipment

AI-powered procurement intelligence for **medical laboratory and diagnostic equipment bought in World Bank-financed
health projects**, built on public World Bank data.

The platform joins three kinds of knowledge that are normally kept apart: what was bought (contract awards and
procurement notices), how it was bought (procurement plans, supervision reports) and why it worked or failed
(completion reports and IEG evaluations). It links them into one knowledge graph:

```
Region → Country → Project → Tender → Contract → Supplier → Equipment type
                      └── documents (PAD, ISR, ICR, IEG review, procurement plan) → passages → lessons, ratings
```

On top of the graph it benchmarks and predicts tender timelines and risks, and a chatbot and package briefs answer
questions in which every statement cites its evidence and is checked against it.

**What the data shows** (FY2015 onwards): 2,220 lab and diagnostic contracts worth US$1.38 billion in 91 countries and
179 projects, from 1,277 suppliers, found by classifying 17,058 health-goods contract descriptions in four languages.

## Deliverables

| # | Deliverable | File |
|---|---|---|
| 1 | Data model: ER diagram, knowledge graph, data dictionary, DDL | [deliverables/01_DATA_MODEL.pdf](deliverables/01_DATA_MODEL.pdf), [er_diagram.svg](deliverables/er_diagram.svg), [01_DATA_MODEL_DDL.sql](deliverables/01_DATA_MODEL_DDL.sql) |
| 2 | Approach: architecture and the four assessment modules | [deliverables/02_APPROACH.pdf](deliverables/02_APPROACH.pdf) |
| 3 | Prototype: pipeline, chatbot and how to run them | [deliverables/03_PROTOTYPE/README.md](deliverables/03_PROTOTYPE/README.md) |
| 4 | Report: findings, measured results, limitations, next steps | [deliverables/04_REPORT.pdf](deliverables/04_REPORT.pdf) |

The chatbot is also a static website in [deliverables/site/](deliverables/site/) (chatbot + report). It runs entirely
in the browser: open `index.html`, or host the folder on any static host.

## Run it

Needs Python 3.11+.

```bash
pip install -r requirements_procurement.txt
python src/test_proc_lib.py                     # shared library tests, ends with ALL PASSED
python -m procurement_demo.run_all              # downloads the public World Bank data and rebuilds everything
python -m streamlit run procurement_demo/app.py # the chatbot
```

Then rebuild the documents from the measured results:

```bash
python src/build_proposal.py
python src/build_deliverables.py
python src/build_web_chatbot.py && python src/build_share_page.py && python src/build_site.py
```

## Data in this repository

- `procurement_demo/data/health_lab/gold/`: the processed tables (Parquet): contracts, tenders, notices, suppliers,
  projects, governance, document chunks, lessons, ratings, knowledge-graph edges, benchmarks, model features and models.
- `procurement_demo/output/health_lab/`: evaluation results, the example brief and the results page.
- Raw API downloads and PDFs are not stored here; `run_all` fetches them again from the public World Bank APIs.

## Repository layout

```
procurement_demo/                  local pipeline: ingest, build, models, retrieval, copilot, chatbot, evaluate, report
  common.py                        scope settings (PROC_SCOPE) and the equipment taxonomy
  chatbot.py, app.py               chatbot engine and its Streamlit UI
notebooks/00_proc_config           Databricks settings: catalog proc_demo, the lab-equipment scope, endpoints
notebooks/20_proc_lib              shared pure-Python helpers and the equipment taxonomy (also imported by procurement_demo/)
notebooks/21_ingest_procurement    contract-first: Health Goods awards → classify → domain projects, notices, CPIA/WGI → bronze
notebooks/22_ingest_project_docs   PAD / procurement plan / ISR / ICR / ICR Review / PPAR PDFs per domain project → volume
notebooks/23_proc_silver_gold      silver with equipment type, supplier resolution, tenders, links, kg_edges, v_lab_contracts
notebooks/24_proc_docs_lessons     section-aware chunks, rating evidence + conflict resolution, LLM lessons + quote check
notebooks/25_proc_vector_index     hybrid Vector Search index over project-document chunks
notebooks/26_proc_risk_models      Kaplan-Meier benchmarks, quantile + risk models, MLflow/UC registry, champion gate
notebooks/27_proc_tools            UC function tools for the copilot
notebooks/28_proc_copilot          evidence registry → compose → verify → brief; briefs and feedback logged
notebooks/29_proc_monitor_job      drift / retrain triggers, KPI view, Lakeflow Job
src/                               document builders, chatbot web page (src/web_chatbot/), tests
deliverables/                      the four deliverables, the chatbot website and its source data
```

The Databricks notebooks (`00_proc_config`, 20-29) implement the same lab-equipment scope on a lakehouse (Delta, Unity
Catalog, Vector Search, MLflow, Lakeflow Jobs), using the same taxonomy and parsers as the local prototype through
`20_proc_lib`. The local prototype produced every number in the deliverables; the notebooks have been checked offline
(syntax, names, and the contract-first selection replayed on the saved API data: 17,058 Health Goods awards → 179
domain projects → US$1,375,387,988 of lab and diagnostic contracts) but not yet run on a workspace.

## Run it on Databricks

Works on Databricks Free Edition (serverless). Everything is created in its own catalog, `proc_demo`.

1. **Workspace > Create > Git folder**, URL `https://github.com/dmlaishu/procurement-intelligence`, branch
   `lab-diagnostics-procurement`. Or import the `notebooks/` folder.
2. Open `notebooks/00_proc_config` and run it once. It creates the catalog, schemas, volume and log table.
3. Run the notebooks in order on serverless compute:

   | Notebook | What it does | Rough time |
   |---|---|---|
   | `21_ingest_procurement` | World Bank APIs → bronze (the contract API is rate-limited, so this one is slow) | 15-30 min |
   | `22_ingest_project_docs` | about 130 project PDFs into the volume | 5-10 min |
   | `23_proc_silver_gold` | silver, gold, knowledge graph, `gold.v_lab_contracts` | 5 min |
   | `24_proc_docs_lessons` | chunks, ratings, LLM lessons with quote check | 10-20 min |
   | `25_proc_vector_index` | Vector Search endpoint and index | 10-20 min |
   | `26_proc_risk_models` | benchmarks and models in MLflow / Unity Catalog | 5 min |
   | `27_proc_tools` | UC function tools | 1 min |
   | `28_proc_copilot` | a cited brief for a PCR / GeneXpert tender in Kenya | 2 min |
   | `29_proc_monitor_job` | monitoring views and the `proc_lab_pipeline` job (schedule paused) | 1 min |

4. Query the results in the SQL editor, for example:

   ```sql
   SELECT * FROM proc_demo.ai.proc_lab_contracts('Molecular diagnostics (PCR, GeneXpert)', NULL, NULL, 'lowest');
   SELECT * FROM proc_demo.ai.proc_supplier_market('Diagnostic imaging', NULL, 'Sub-Saharan Africa');
   ```

If the workspace cannot create catalogs, `00_proc_config` falls back to the `workspace` catalog and prints a note.
If the default chat model is not served, it picks another Databricks-hosted chat model.

## Other scopes

The pipeline is configured by scope. Set `PROC_SCOPE` (see `procurement_demo/common.py`) to run the same code on
India rural and inter-urban roads (`india_roads`) or Kenya, Nigeria and Bangladesh across all sectors
(`three_country`).
