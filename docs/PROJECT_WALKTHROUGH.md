# Project walkthrough and engineering notes

This page is a guide to the **implemented local prototype**, its public data sources, and its limitations. For installation and the four assessment deliverables, see the [main README](../README.md).

## Try the chatbot

**[Open the browser demo](https://dmlaishu.github.io/procurement-intelligence/)**

The browser demo provides predefined procurement analysis over published, processed data. It is hosted as static files; it is **not** a live Databricks model endpoint. A separate Streamlit interface is available through `procurement_demo/app.py`.

Questions worth trying:

- Which suppliers won diagnostic equipment contracts in Sub-Saharan Africa?
- How does spending on laboratory equipment compare across countries?
- What do project reports say about equipment maintenance problems?

When reviewing an answer, check its source references and note whether the question is answerable from the published dataset.

## How the repository is organized

| Component | Location | Responsibility |
| --- | --- | --- |
| Data ingestion | `procurement_demo/ingest.py` | Retrieve source records from World Bank APIs |
| Linked analytical tables | `procurement_demo/build.py` | Clean and connect procurement entities |
| Benchmarks and prediction | `procurement_demo/models.py` | Derive tender benchmarks and model features |
| Evidence retrieval | `procurement_demo/retrieval.py` | Look up supporting records and text |
| Procurement briefs | `procurement_demo/copilot.py` | Compose and check evidence-backed statements |
| Evaluations | `procurement_demo/evaluate.py` | Test retrieval and answer safeguards |
| Browser interface | `src/web_chatbot/` | Client-side chat logic and UI |
| Databricks design | `notebooks/20_proc_lib.py`–`29_proc_monitor_job.py` | Lakehouse-oriented implementation |

## Three implementation choices

### 1. Separate the public demo from the lakehouse implementation

The published chatbot can be opened without a Databricks account. Its content is derived from preprocessed project data, which makes it convenient to review. This is a deliberate tradeoff: the public demo does not offer arbitrary live queries against Databricks.

The Databricks notebooks describe an equivalent lakehouse workflow, but **they have not been executed**. The local Python pipeline generated the results represented in the deliverables.

### 2. Treat answer evidence as data, not just prose

In `procurement_demo/copilot.py`, the brief builder first gathers model outputs, benchmarks, and retrieved lessons into an evidence registry. Statements then refer to evidence IDs and pass through a verification step. The local composer is deterministic; an optional language-model rewrite must still pass verification.

This reduces unsupported statements, but it does **not** guarantee that the underlying source data, record linkage, or classification is error-free.

### 3. Use comparable historical tenders rather than one global estimate

Tender timing depends on the procurement category, method, geography, and how much historical evidence exists. The local code uses cohort benchmarks, falls back to broader cohorts where necessary, and applies minimum-sample safeguards before presenting some estimates. Review `procurement_demo/models.py` and `procurement_demo/copilot.py` for the exact implementation.

## How to verify locally

From the repository root, with Python 3.11+:

```bash
pip install -r requirements_procurement.txt
python src/test_proc_lib.py
python -m procurement_demo.run_all
python -m streamlit run procurement_demo/app.py
```

The pipeline downloads external data and may take time or encounter upstream API changes. These commands are documented entry points, **not a claim that they were run while preparing this guide**.

The browser chatbot implementation also includes `src/web_chatbot/test_engine.js`; consult that file for client-side test coverage.

## Known limitations

- The project covers a selected equipment domain and a subset of World Bank-financed procurement, not all procurement activity.
- The static demo uses published processed data; it is not continuously synchronized with source APIs.
- Matching tenders, suppliers, and project documents can introduce missing or ambiguous links.
- Model predictions and text-derived lessons are decision-support signals, not verified procurement outcomes.
- The lakehouse notebooks have not been run end-to-end in Databricks.

## Where to investigate next

Useful follow-up engineering work includes measuring supplier matching errors, adding independent regression tests around citations and unsupported questions, testing mobile usability, and validating the Databricks path in a real workspace. Document any actual results alongside the code change that produced them.
