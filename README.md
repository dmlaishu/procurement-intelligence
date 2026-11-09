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

**Local prototype snapshot (FY2015 onward).** The [local report](deliverables/04_REPORT.md) records 2,220 lab and diagnostic
contract × supplier rows worth US$1.38 billion across 91 countries and 179 projects, with 1,277 suppliers in this
equipment subset. The prototype screened 17,058 health-goods contract descriptions in four languages. See
[Measured results](#measured-results) for the exact totals and the separate Databricks snapshot.

## Deliverables

| # | Deliverable | File |
|---|---|---|
| 1 | Data model: ER diagram, knowledge graph, data dictionary, DDL | [deliverables/01_DATA_MODEL.pdf](deliverables/01_DATA_MODEL.pdf), [er_diagram.svg](deliverables/er_diagram.svg), [01_DATA_MODEL_DDL.sql](deliverables/01_DATA_MODEL_DDL.sql) |
| 2 | Approach: architecture and the four assessment modules | [deliverables/02_APPROACH.pdf](deliverables/02_APPROACH.pdf) |
| 3 | Prototype: pipeline, chatbot and how to run them | [deliverables/03_PROTOTYPE/README.md](deliverables/03_PROTOTYPE/README.md) |
| 4 | Report: findings, measured results, limitations, next steps | [deliverables/04_REPORT.pdf](deliverables/04_REPORT.pdf) |

The static website in [deliverables/site/](deliverables/site/) is built by `python src/build_landing.py`. The home page
(`index.html`) explains the project and offers two actions: **Ask the AI Assistant** (opens the app on the Ask tab) and
**Explore the Four Stages** (`learn/`): an interactive journey through Plan, Design, Anticipate and Learn in which each
stage shows its answer for the chosen equipment, country (Kenya or India) and budget, computed in advance from the same
models (`planner-data.json`). Also in the folder but not linked from the home page: the full planner (`plan/`), the
browser-only chatbot (`ask/`) and the report (`report.html`). The app accepts deep links:
`?view=ask|plan|design|anticipate|learn|countries|suppliers|myplan` and an optional `&q=` question. Host the folder on any
static host.

## Try it

| | Link |
|---|---|
| Chatbot with document Q&A by country (public) | https://procurement-intelligence-gkhea5oeh3lwtbbs6yganq.streamlit.app/ |
| Chatbot and report, static site (public) | https://dmlaishu.github.io/procurement-intelligence/ |

The Streamlit app is `procurement_demo/app.py` (**LabProcure**), kept deliberately simple, with three tabs:

| Tab | What it does |
|---|---|
| Ask | Country and category, four sample questions, one question box. The answer is 1-3 sentences with its source; "More detail and all sources" opens the rest. Questions about suppliers, prices, totals and timelines are answered exactly from the contract tables by the chatbot; other questions from the country's project documents. Both cite and verify their sources |
| Plan | Country (Kenya or India), equipment, budget, invitation date (method, bid window and review under "Advanced options"). Three dates: likely award and safer planning date (model) and estimated delivery (an assumption), a short explanation, the sample size and the typical error; then the seven-stage journey with one figure per stage (historical, model, assumption or a verified report quote); "See supporting evidence" holds the comparable contracts, sample size and model accuracy |
| How it's built | Evaluation results, architecture (deployed app vs the Databricks pipeline), scope, data model |

The full workspace with every tab (lifecycle table, optimizer, pre-mortem, cited brief, My plan with Excel export,
country and supplier comparisons, document Q&A, explore) is still available: `python -m streamlit run
procurement_demo/app_full.py`.

Logic lives outside the UI so it is tested and reusable: `procurement_demo/insights.py` (benchmarks, comparisons,
citations), `procurement_demo/lifecycle.py` (lifecycle figures, timeline, optimizer, pre-mortem),
`procurement_demo/ui.py` (HTML badges and cards). Tests: `python src/test_insights.py`, `python src/test_lifecycle.py`.

### REST API

The same planner as a FastAPI service (`api/main.py`), with a Dockerfile:

```bash
pip install -r requirements.txt -r requirements_api.txt
uvicorn api.main:app --reload          # http://127.0.0.1:8000/docs
docker build -t labprocure-api . && docker run -p 8000:8000 labprocure-api
```

Endpoints: `GET /health`, `GET /options`, `POST /lifecycle`, `POST /optimize`, `POST /premortem`, `POST /ask`. Set
`LABPROCURE_API_KEY` to require an `X-API-Key` header.

## Document Q&A by country (RAG)

`procurement_rag/` is a reusable pipeline that fetches the World Bank project documents of a country (appraisal
documents, procurement plans, supervision, completion and IEG reports), stores them as
`data/<country>/pdf/<doc type>/`, extracts and chunks them, embeds them into a vector database (ChromaDB) and answers
questions from them with the supporting sources. The chatbot's **Ask the documents** tab has a country and a category
dropdown. Built for Kenya and India; a new country is one command
(`python -m procurement_rag.pipeline --add-country NGA` then `--countries NGA`) and does not rebuild the others.
See [procurement_rag/README.md](procurement_rag/README.md).

## Run it

Needs Python 3.11+.

```bash
pip install -r requirements_procurement.txt
python src/test_proc_lib.py                     # shared library tests, ends with ALL PASSED
python src/test_insights.py                     # planning / comparison / citation tests, ends with ALL PASSED
python src/test_lifecycle.py                    # lifecycle, optimizer, pre-mortem and API tests
python -m procurement_demo.run_all              # downloads the public World Bank data and rebuilds everything
python -m streamlit run procurement_demo/app.py # the app (app_full.py: every tab)
```

Then rebuild the documents from the measured results:

```bash
python src/build_proposal.py
python src/build_deliverables.py
python src/build_web_chatbot.py && python src/build_share_page.py && python src/build_site.py
python src/build_landing.py
```

## Data in this repository

- `procurement_demo/data/health_lab/gold/`: the processed tables (Parquet): contracts, tenders, notices, suppliers,
  projects, governance, document chunks, lessons, ratings, knowledge-graph edges, benchmarks, model features and models.
- `procurement_demo/output/health_lab/`: evaluation results, the example brief and the results page.
- Raw API downloads and PDFs are not stored here; `run_all` fetches them again from the public World Bank APIs.

## Repository layout

```
procurement_demo/                  local pipeline: ingest, build, models, retrieval, copilot, chatbot, evaluate, report
  insights.py                      planning, estimate, country and supplier comparisons, citation links (used by app.py)
  lifecycle.py                     lifecycle figures with evidence basis, timeline, what-if optimizer, pre-mortem
  ui.py                            HTML badges, lifecycle table and risk cards for the app
api/                               FastAPI service over the planner (Dockerfile at the root)
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
notebooks/30_rag_registry          document Q&A: country registry (ops.rag_countries), add a country
notebooks/31_rag_fetch             document Q&A: per-country PDFs → volume rag_docs/<country>/<doc type>/, manifest table
notebooks/32_rag_process           document Q&A: pages → section-aware passages with category columns (gold.rag_chunks)
notebooks/33_rag_index             document Q&A: Vector Search index ai.rag_chunks_index, job proc_rag_pipeline
notebooks/34_rag_ask               document Q&A: country / category dropdowns, question, cited answer, evaluation
procurement_rag/                   document Q&A, local version (ChromaDB), used by the chatbot's "Ask the documents" tab
src/                               document builders, chatbot web page (src/web_chatbot/), tests
deliverables/                      the four deliverables, the chatbot website and its source data
```

## Measured results

The local prototype and Databricks use the same equipment taxonomy and parsers in `20_proc_lib`. The figures below
come from two separate snapshots of the World Bank APIs. The local column uses the snapshot in the
[26 October 2026 report](deliverables/04_REPORT.md), with the saved
[procurement evaluation](procurement_demo/output/health_lab/eval_results.json) and
[document Q&A evaluation](procurement_rag/eval_results.json). The Databricks column uses the
[27 October 2026 run record](procurement_rag/databricks_run.json), which reports a one-time execution of notebooks
00 and 20–34 on Databricks Free Edition (serverless).

| Measure | Local prototype | Databricks run |
|---|---|---|
| Lab and diagnostic contract × supplier rows | 2,220 | 2,221 |
| Lab and diagnostic contract value (rounded to whole US dollars) | US$1,375,387,988 | US$1,375,796,408 |
| Projects with lab and diagnostic awards | 179 | 179 |
| Tenders in those projects / with both invitation and award dates | 10,403 / 1,068 | 10,407 / 1,068 |
| Resolved suppliers across health-goods awards in those projects | 7,540 | 7,543 |
| Lessons extracted / with a verified source quote | 150 / 147 (98%, rule-based extraction) | 733 / 712 (97%, LLM extraction) |
| Projects with conflicting ratings / projects with rating evidence | 8 of 34 | 8 of 33 |
| Invitation-to-award MAE, P50 model | 46.9 days | 49.1 days |
| Invitation-to-award MAE, global training median | 73.0 days | 73.0 days |
| Invitation-to-award MAE, Kaplan–Meier benchmark | 56.3 days | — |
| P80 coverage on the 259-tender cycle-time test set | 76.1% | 78% |
| Single-bid AUC / amendment AUC | 0.668 / 0.545 | 0.64 / 0.56 |
| Document Q&A evaluation (Kenya and India) | 10/10 checks passed (9 answered, 1 correctly declined) | 10/10 checks passed (9 answered, 1 correctly declined) |

Each document Q&A evaluation checks for expected evidence among the top five sources for nine questions and for a
correct decline on one question. Both runs recorded that all returned sources belonged to the selected country.

The Databricks models passed the quality gate and were registered as `@champion` in Unity Catalog. On Free Edition,
the notebooks save models with cloudpickle, qualify SQL function parameters and keep them out of subqueries, and call
model endpoints through `ai_query`.

The recorded notebook execution includes Step 29, which builds the monitoring views and creates or updates the
`proc_lab_pipeline` Lakeflow job. The job's weekly schedule is configured as **paused**. Recurring monitoring and
conditional retraining on that schedule require enabling the trigger.

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
   | `29_proc_monitor_job` | monitoring views and job setup for `proc_lab_pipeline` (weekly trigger paused) | 1 min |
   | `30_rag_registry` | document Q&A: countries table, seeded with Kenya and India | 1 min |
   | `31_rag_fetch` | about 85 PDFs for Kenya and India into the `rag_docs` volume | 5-10 min |
   | `32_rag_process` | page text and tagged passages (`gold.rag_chunks`) | 5 min |
   | `33_rag_index` | Vector Search index on the passages, and the `proc_rag_pipeline` job | 10-20 min |
   | `34_rag_ask` | dropdowns for country and category, ask a question, see the answer and sources | 1 min |

   The document Q&A (30-34) can also run on its own after `00_proc_config`: without Step 23 it selects projects by
   keyword and pinned ids only.

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
