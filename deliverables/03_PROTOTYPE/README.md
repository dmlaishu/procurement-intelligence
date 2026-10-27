# Deliverable 3: Prototype

A working, end-to-end prototype of the Procurement Intelligence Platform for **medical laboratory and diagnostic
equipment in World Bank-financed health projects**, on real public World Bank data from 91 countries.
It includes a **chatbot** that answers questions with cited evidence, and a **document Q&A** that answers from a
selected country's World Bank project documents. It runs locally and on Databricks.

**Try it:** https://dmlaishu.github.io/procurement-intelligence/chat/ (public; tab "Ask the documents" for the country menus) · code: https://github.com/dmlaishu/procurement-intelligence

## What is in this folder

| File | What it is |
|---|---|
| `prototype_report.html` | results page: data coverage, knowledge graph, models, retrieval, guardrails, an example brief |
| `demo_brief.md` | the copilot's brief for a new PCR / GeneXpert package (every line cited) |
| `eval_results.json` | every measured number used in the report and approach, including the chatbot evaluation |
| `prototype_source.zip` | source code: pipeline and chatbot (`procurement_demo/`), document Q&A (`procurement_rag/`, incl. the Databricks App), Databricks notebooks 00, 20-34, tests, build scripts |

## The chatbot

```bash
python -m streamlit run procurement_demo/app.py
```

Ask in plain English, for example:

- *Which supplier won the cheapest GeneXpert or PCR contract?* → exact ranking over contract awards
- *Who are the top suppliers of diagnostic imaging equipment in Sub-Saharan Africa?* → supplier market shares
- *How much was spent on laboratory equipment by country?* → spend breakdown
- *How long does it take to award a laboratory analyzer tender?* → censoring-aware benchmarks
- *What problems did projects have with laboratory equipment maintenance?* → lessons and report passages, with pages
- *Show the entity relationship diagram* / *How is Kenya connected to its lab equipment suppliers?* → graph diagrams

Each answer shows its evidence (contract rows, benchmarks, report pages with links), the result table, and any
sentence the verifier removed. On 16 labelled questions it chose the right tool for
100% and matched an independent query in 6 of
6 exactness checks. An LLM can optionally rephrase verified answers (`PROC_CHAT_LLM=1`, see
`chatbot.py`); its text is discarded if it adds any number.

**Ask the documents** (second tab): choose a **country** (Kenya, India, Nigeria) and a
**category** (ten equipment types or the procurement process), then ask your own question. The answer is built from
that country's World Bank project documents (111 documents, 4,761 passages), each sentence with
its source and page. Evaluation: 10/10. Build or extend it:

```bash
python -m procurement_rag.pipeline --countries KEN IND      # download, chunk, embed, index
python -m procurement_rag.pipeline --add-country NGA       # register a country, then --countries NGA
python -m procurement_rag.evaluate
```

## What the pipeline does

```
World Bank APIs ──▶ ingest.py ──▶ build.py ──────────────────────▶ models.py ──────▶ copilot.py / chatbot.py
 health Goods awards,  contract-   equipment taxonomy, entity        KM benchmarks      retrieval.py (BM25 +
 notices, projects,    first       resolution, Gold tables, graph,   by equipment type, filters + RRF)
 PDFs, CPIA / WGI      selection   PDF chunking, lessons, ratings    GBM P50/P80, risk   └─▶ verifier ─▶ answer
                                                                                         app.py (Streamlit UI)
                                         evaluate.py ──▶ eval_results.json ──▶ report.py ──▶ prototype_report.html
```

## How to run it

Needs Python 3.11+. The first run downloads about 150 MB and parses about 130 PDFs (20-30 minutes); later runs reuse
the cache.

1. Unzip `prototype_source.zip` into an empty folder and open a terminal there.
2. Install the packages:

   ```bash
   pip install -r requirements_procurement.txt
   ```

3. Check the shared library (it should end with `ALL PASSED`):

   ```bash
   python src/test_proc_lib.py
   ```

4. Build everything:

   ```bash
   python -m procurement_demo.run_all
   ```

5. Start the chatbot:

   ```bash
   python -m streamlit run procurement_demo/app.py
   ```

**Point it at another domain:** the scope is one configuration block in `procurement_demo/common.py`. Set
`PROC_SCOPE=india_roads` (PowerShell: `$env:PROC_SCOPE = "india_roads"`) to run the same pipeline on Indian rural
and inter-urban roads, or `three_country` for Kenya, Nigeria and Bangladesh across all sectors.

## Two implementations, one core

| Component | Local prototype (run and verified) | Databricks (run end to end on 2026-10-27) |
|---|---|---|
| Settings | `procurement_demo/common.py` | `notebooks/00_proc_config` (catalog `proc_demo`) |
| Ingestion | `procurement_demo/ingest.py` | `notebooks/21_ingest_procurement`, `22_ingest_project_docs` |
| Gold tables, entity resolution, graph | `build.py` | `23_proc_silver_gold` |
| Documents, lessons, ratings | `build.py` (rules + quote check) | `24_proc_docs_lessons` (LLM + quote check) |
| Vector index | `retrieval.py` (BM25 + filters + RRF) | `25_proc_vector_index` (hybrid Vector Search) |
| Models | `models.py` | `26_proc_risk_models` (MLflow, Unity Catalog registry) |
| Copilot and chatbot | `copilot.py`, `chatbot.py`, `app.py` | `27_proc_tools`, `28_proc_copilot` |
| Monitoring / learning loop | `evaluate.py` | `29_proc_monitor_job` (Lakeflow Job, retrain gate) |
| Document Q&A by country | `procurement_rag/` (ChromaDB) and the "Ask the documents" tab | `30_rag_registry` → `34_rag_ask` (Vector Search, job `proc_rag_pipeline`), Databricks App in `procurement_rag/databricks_app/` |

The shared logic (parsers, entity resolution, survival estimates, verifier, equipment taxonomy, document categories
and answer-sentence rules) is one file, `notebooks/20_proc_lib.py`, used by both columns.
