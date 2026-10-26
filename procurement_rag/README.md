# Document Q&A: country-scoped RAG over World Bank project documents

Ask your own question about laboratory and diagnostic equipment procurement in one country, and get an answer
taken from that country's World Bank project documents, with the supporting sources (document, page, link).

Built for Kenya and India; Nigeria was added afterwards with one command to show the pipeline extends without
rebuilding.

## How it works

```
countries.json ──▶ fetch ──▶ process ──▶ index ──▶ search ──▶ chatbot tab "Ask the documents"
 (ISO codes)        │          │           │          │
                    │          │           │          └ hybrid retrieval (vector + BM25), filtered by country and
                    │          │           │            category; answer = cited sentences from the passages
                    │          │           └ embeddings (BAAI/bge-small-en-v1.5, local) → ChromaDB, incremental
                    │          └ text per page → section-aware chunks → category tags (equipment types, procurement)
                    └ projects (bought lab equipment / lab keywords / pinned) → newest PAD, procurement plans,
                      ISR, ICR, IEG review, PPAR, project papers → data/<ISO3>/pdf/<doc type>/<doc id>.pdf
```

| Step | Module | Output | Incremental |
|---|---|---|---|
| Find and download documents | `fetch.py` | `data/<ISO3>/pdf/<type>/<id>.pdf`, `data/<ISO3>/manifest.json` | files on disk are skipped |
| Extract and chunk | `process.py` | `data/<ISO3>/text/<id>.json`, `data/<ISO3>/chunks.parquet` | text extracted once per PDF |
| Embed and index | `index.py` | `data/index/` (ChromaDB collection `procurement_docs`) | only new chunks are embedded; changed tags are updated in place; other countries untouched |
| Retrieve and answer | `search.py` | answer sentences with `[S1]`… and the sources | |

**Which documents.** Projects come from (1) the procurement data: projects in that country whose contracts bought lab
or diagnostic equipment, (2) health projects matching lab keywords in the World Bank Projects API, (3) projects pinned
in `countries.json` (e.g. the East Africa Public Health Laboratory Networking Project for Kenya). For each project the
newest document of each type is kept.

**Categories.** Each passage is tagged with the equipment types it mentions (the same taxonomy that classifies the
contracts: molecular diagnostics, imaging, rapid tests, reagents, analyzers, microscopy, biosafety and cold chain,
lab information systems, mobile labs, general lab equipment) and with "Procurement process and contracts".
With a category selected, both retrieval and the answer sentences are restricted to it.

**Answers.** The answer is made of sentences taken from the retrieved passages, chosen by meaning similarity to the
question, each followed by its source, so it cannot contain anything the documents do not say. If nothing is close
enough, it says the documents do not answer it and shows the closest passages. Optionally, a writing model can phrase
the answer (`RAG_LLM_URL`, `RAG_LLM_MODEL`, `RAG_LLM_KEY`; any OpenAI-compatible endpoint, e.g. a Databricks serving
endpoint); its answer is shown only if every citation exists and every number appears in the cited sources.

## Run it

```bash
pip install -r requirements_procurement.txt
python -m procurement_rag.pipeline --countries KEN IND      # download, parse, embed (about 15 minutes the first time)
python -m procurement_rag.evaluate                         # retrieval check, writes eval_results.json
python -m streamlit run procurement_demo/app.py            # tab "Ask the documents"
python -m procurement_rag.search "What lab equipment is being procured?" --country KEN
```

Add a country:

```bash
python -m procurement_rag.pipeline --add-country NGA      # ISO codes and name from the World Bank country list
python -m procurement_rag.pipeline --countries NGA        # builds Nigeria only
```

To pin extra projects, or change how many documents per type are kept, edit `countries.json`.

## On Databricks (notebooks 30-34)

The same pipeline as notebooks, with Databricks storage and search. The category rules and the answer-sentence rules
are shared through `notebooks/20_proc_lib.py`, so both versions tag and answer the same way.

| Notebook | Local equivalent | Databricks |
|---|---|---|
| `30_rag_registry` | `countries.json`, `--add-country` | table `ops.rag_countries`; widget `add_country` (ISO-3) and `pin_projects` |
| `31_rag_fetch` | `fetch.py` | PDFs in the volume `bronze/rag_docs/<country>/<doc type>/<doc id>.pdf`; manifest `bronze.rag_documents` |
| `32_rag_process` | `process.py` | `silver.rag_pages` (text per page, parsed once), `gold.rag_chunks` (Delta, Change Data Feed, 1/0 category columns) |
| `33_rag_index` | `index.py` | Vector Search Delta Sync index `ai.rag_chunks_index` (managed `databricks-gte-large-en` embeddings, incremental), job `proc_rag_pipeline` with a `countries` parameter |
| `34_rag_ask` | `search.py` + the app tab | dropdown widgets for country, category and answer style, a question box, the answer with its sources, and the evaluation questions |

Run order: `00_proc_config`, then 30, then 31, 32 and 33 with `countries = KEN,IND`, then 34. New country: 30 with
`add_country = NGA`, then the `proc_rag_pipeline` job with `countries = NGA`.

Run on Databricks Free Edition: Kenya 29 documents / 1,239 passages and India 55 / 2,556, the same as locally; the
evaluation in 34 passes 10/10 (9 answered with 1-4 cited sentences, the Kenya imaging question declined, every source
from the selected country), about 3 seconds per answer. The similarity thresholds in `00_proc_config` (`RAG_MIN_SIM`
0.65 / 0.75) were calibrated on that run for the `databricks-gte-large-en` embeddings, which score higher than the
local model. On serverless the notebook reaches the embedding and chat models through `ai_query`.

## Current index

| Country | Projects | Documents | Passages |
|---|---|---|---|
| Kenya | 8 | 29 | 1,239 |
| India | 12 | 55 | 2,556 |
| Nigeria | 6 | 27 | 966 |

Evaluation (`eval_results.json`): 10 questions, the expected source is in the top 5 for all 9 answerable ones, the one
question the Kenya documents cannot answer (imaging purchases) is declined, every source belongs to the selected
country, median answer time about 1.2 seconds on a laptop.

## Limitations

- Answers are extracted sentences, so they read as quotations rather than a summary unless a writing model is configured.
- PDF text extraction splits some words ("proje ct"); category tags and retrieval tolerate it, but quoted sentences show it.
- Document selection is by project; documents that are not linked to a project (country studies, working papers) are not
  fetched.
- The PDFs, text, embeddings and index are not stored in the repository; the manifests list exactly what was fetched,
  and the pipeline rebuilds the rest.
