# Procurement Intelligence Platform: Technical Proposal

**Evidence-backed decision support for buying medical laboratory and diagnostic equipment in World Bank-financed
health projects, built on public World Bank data**

| Item | Detail |
|---|---|
| Domain | Medical laboratory and diagnostic equipment (PCR and GeneXpert systems, imaging, analyzers, reagents and cartridges, rapid tests, microscopy, biosafety and cold chain, lab IT) in World Bank-financed health projects, all countries |
| Scope | Modules 1-4 of the assessment: ingestion and knowledge representation, predictive and retrieval engines, guardrails, continuous learning and rollout |
| Platform | Databricks lakehouse (Delta, Unity Catalog, Vector Search, MLflow, Model Serving, Lakeflow Jobs), extending an existing World Bank assistant built on the same platform |
| Evidence | A working prototype, including a chatbot and a document Q&A by country, on real public data: {{SCOPE_LINE}}; run locally and end to end on Databricks. The results quoted in this document are measured, not projected (§7) |
| Try it | {{TRY_IT}} |

---

## 0. Executive summary

Procurement teams plan each new package with three kinds of knowledge that are kept apart: **what happened** (contract
awards, notices, disbursements), **how it was done** (procurement plans, bidding documents, supervision reports) and
**why it worked or failed** (completion reports, IEG evaluations). The platform joins the three around a shared spine
of identifiers (Project ID, bid reference, supplier, equipment type, country-year).

**Why laboratory and diagnostic equipment.** It is one of the hardest things health projects buy. Specifications are
technical and easy to write around one brand. Analyzers and PCR platforms are often *closed systems*, so the
equipment choice locks in years of reagent and cartridge purchases. Value depends on installation, training,
maintenance and a cold chain, not just delivery. COVID-19 then turned it into an emergency market. The Bank's public
data already holds thousands of these contracts across more than 80 countries, plus the appraisal, supervision and
completion reports that explain what went wrong. But nothing connects a GeneXpert contract in one country to the
lesson about cartridge stock-outs in another.

The platform answers one practical question for any new package, through a brief or a chat:

> *"Given everything the Bank has seen on comparable equipment, who supplies it, what does it cost, how long will the
> tender take, what could go wrong, and what did past projects learn? Show me the evidence."*

**Design in five points**

1. **Medallion lakehouse with a knowledge graph on top.** Raw API records and PDFs land unchanged in Bronze. Typed,
   de-duplicated tables go to Silver. Gold holds resolved entities (tender, contract, supplier, project, document,
   lesson) plus an **edge table** that records how each link was made (`exact_key`, `fuzzy_desc`, `temporal`,
   `llm_extracted`) and with what confidence.
2. **Structure-aware document processing.** 100+ page reports are split along their own heading hierarchy. Running
   headers are removed, tables and annexes are typed separately, and the data sheets (ISR rating history, key dates)
   are parsed deterministically. Retrieval can therefore target *Lessons* or *Key factors* sections and cite the
   exact page.
3. **Censoring-aware timeline benchmarks plus gradient-boosted risk models.** Tenders still open are kept as censored
   observations (Kaplan-Meier) rather than dropped. Quantile models give a P50 and P80 per package. Classifiers score
   single-bid and re-tender risk. All models are validated out-of-time.
4. **Evidence-first agentic retrieval.** Every tool result is registered as citable evidence before any text is
   written. Statements are composed from that evidence, and a verifier drops any statement whose numbers, quotes or
   citations are not backed by what it cites. An LLM may rephrase, but cannot add facts.
5. **Closed loop.** New notices, awards and evaluations flow in incrementally through Change Data Feed. The vector
   index re-embeds only what changed, and realised award dates score the models' past predictions. Retraining is
   triggered by data volume, drift or coverage decay, behind a quality gate.
6. **A domain layer and a chatbot on top.** An equipment taxonomy (14 classes, patterns in four languages) turns
   free-text contract descriptions into a navigable hierarchy: Region → Country → Project → Tender → Contract →
   Supplier → Equipment type. A chatbot routes each question to the tool that can answer it exactly: SQL for
   prices, suppliers and spend; survival benchmarks for timelines; retrieval for lessons; the graph for
   relationships. The same verifier then checks every sentence.

**What the prototype shows on real data** (§7 has the details)

{{EXEC_RESULTS}}

**Rubric map**

| Rubric dimension (points) | Where it is addressed |
|---|---|
| System and knowledge architecture (30) | §1 architecture, §2 ingestion, entity resolution, graph topology, document hierarchy |
| Analytical and AI methodology (30) | §3 risk models and agentic RAG, §4 guardrails and conflict resolution |
| Domain alignment and data utilisation (25) | §2.1 source inventory, §2.3 key reconciliation, §3.1 procurement-stage targets, §2.6 missing data |
| Operationalisation and scalability (15) | §5 continuous learning, rollout phases, KPIs, §6 risks |

---

## 1. Architecture overview

```
 SOURCES (public World Bank APIs)                         REFRESH
 Major Contract Awards (FinancesOne DS00005) ──────────── daily
 Procurement notices: IFB / REOI / Contract Award ─────── daily
 Projects & Operations ─────────────────────────────────── weekly
 Documents & Reports PDFs: PAD · Proc. Plan · ISR · ICR · IEG PPAR ── weekly
 CPIA + WGI governance indicators ─────────────────────── quarterly
            │
            ▼
 LAKEHOUSE  (Delta + Unity Catalog: grants · lineage · audit on tables, PDFs, functions, index, models)
 ┌─ BRONZE ── raw JSON per record (replayable) · PDFs in a governed Volume · MERGE on natural keys · CDF on
 ├─ SILVER ── contracts · notices (award HTML → bidders, prices, rejections) · projects · governance
 │            · document pages (line breaks kept)
 └─ GOLD ──── fact_tender · fact_contract · dim_supplier · dim_project · country_governance_year
              proj_doc_chunks (section-aware) · lessons (quote-verified) · rating_evidence · ratings_resolved
              kg_edges  (every link with method + confidence)
            │
            ▼
 AI LAYER
   Vector Search (hybrid, filtered by section_kind / country / doc type)
   MLflow models @champion: Kaplan-Meier benchmarks · P50/P80 cycle · single-bid · re-tender
   Unity Catalog function tools: benchmark · similar tenders · lessons · passages · project evidence · context
   Procurement Copilot: plan → tools → evidence registry → compose → VERIFY → brief with citations
            │
            ▼
 USERS: procurement specialists and task teams (chatbot, briefs) · managers (KPI and risk dashboards)
            │  feedback, realised award dates
            ▼
 LOOP: Lakeflow Job  ingest → graph → docs & lessons → index sync → monitor → [retrain gate] → retrain → @champion
```

**Why a lakehouse plus an edge table, and not a graph database alone?** Most questions are analytical (benchmarks,
cohorts, model features) and run best as SQL over columnar Delta tables. The graph questions ("which lessons apply
to contracts like this one?") are 2-4 hop traversals that SQL joins over `kg_edges` handle at this scale. The edge
table can be exported to a graph engine (GraphFrames, Neo4j) unchanged if multi-hop analytics grow. Everything sits
under one governance model: Unity Catalog grants, lineage and audit cover tables, PDFs, functions, the vector index
and the models.

---

## 2. Module 1: Ingestion pipeline and multimodal knowledge representation

### 2.1 Source inventory

| Source (public API) | Layer | Grain / natural key | Refresh | Used for |
|---|---|---|---|---|
| Major Contract Awards: FinancesOne `DS00005` | Bronze `contract_awards_raw` | contract × supplier (`wb_contract_number`, `supplier_id`) | daily | signed value, supplier, method, review type, signing date |
| Procurement notices: `/v2/procnotices` | Bronze `proc_notices_raw` | notice (`id`) | daily | **tender start** (IFB/REOI date, deadline) and **award** (notification date, every bidder, evaluated prices, rejection reasons) |
| Projects & Operations: `/v2/projects` | Bronze `projects_raw` | project (`P-code`) | weekly | approval and closing dates, commitment, E&S category, sector, implementing agency |
| Documents & Reports: `/v2/wds` + PDFs | Bronze volume + `project_docs_meta` | document (`id`), linked to 1..n projects | weekly | PAD, Procurement Plan, ISR, ICR, IEG PPAR/ICRR |
| CPIA (WDI source 2) and WGI (source 3) | Bronze `governance_raw` | country × indicator × year | quarterly | institutional context features |
| Finances (disbursements, IDA/IBRD statements) | Phase 2 | loan × month | monthly | disbursement-lag early warning (§3.6) |

**Domain-first selection.** The domain is defined by *what was bought*, not by sector codes. Ingestion pulls every
Goods contract in Health-practice projects, classifies each item (§2.7), keeps the projects that bought laboratory or
diagnostic equipment, then pulls those projects' notices and documents. The same code can be pointed at another
domain (the pipeline also has an India roads scope and a three-country scope) by changing one configuration block.

Prototype volumes (FY{{MIN_YEAR}} onwards): {{SOURCE_VOLUMES}}

### 2.2 ETL pattern

- **Bronze is immutable and replayable.** Each record keeps the exact JSON as returned (`raw_json`), plus the
  ingestion key and timestamp. Parsing rules can change later without calling the APIs again, and any Gold value can
  be traced back to its source record.
- **Idempotent and incremental.** Every write is a `MERGE` on the natural key, updating only when `raw_json` changed.
  Change Data Feed is on, so downstream steps (chunking, embedding, lesson extraction) process only new or changed
  rows. PDFs are downloaded once into a governed Volume.
- **Resilient API access.** Rate-limited sources get backoff that honours the server's retry-after (FinancesOne
  allows only a few calls per minute). Pages are fetched newest-first with a stopping date. Partial failures are
  logged to `ops.app_logs` and picked up by the next run.
- **Data contracts.** Expectations on Silver (Lakeflow Declarative Pipelines in production) cover:
  - dates parse;
  - amounts ≥ 0;
  - the procurement category is in the controlled list;
  - project IDs match `^P\d{6}$`;
  - reference keys are ≥ 5 characters after normalisation.

  Violations are quarantined, not dropped silently.
- **Semi-structured HTML is parsed into structure.** Contract Award notices carry a rich HTML body. It is parsed into
  notification date, contract duration, signed price, and awarded / evaluated / rejected bidders with WB supplier IDs
  and rejection reasons. This is the only public source of **bidder counts and rejection reasons**, which are the
  competition signals the models use.

### 2.3 Reconciling disjoint keys (entity resolution)

| Link | Rule (in order) | Method tag / confidence | Prototype result |
|---|---|---|---|
| Notice → Tender | normalised bid reference (`KE-MOWASCO-16771-CW-RFB` ≡ `ke mowasco 16771 cw/rfb`) | `exact_key` / 1.0 | {{N_TENDERS}} tenders |
| Contract → Tender | (1) normalised `borrower_contract_reference_number` = bid reference; (2) else same project + same category + signed within 18 months of the invitation + description token Jaccard ≥ 0.5 | `exact_key` 1.0; `fuzzy_desc` = similarity | {{LINK_RESULT}} |
| Contract → Supplier | (1) normalised name key (drops M/S, legal forms, punctuation; JV partner order made irrelevant); (2) within blocks of the same supplier country and first token, similarity ≥ 0.92 → union-find merge | `exact_key` / `fuzzy_name` 0.92 | {{SUPPLIER_RESULT}} |
| Contract / Tender / Document → Project | Project ID (P-code); PPARs covering several projects fan out to each | `exact_key` | {{PROJECT_LINK}} |
| Contract / Tender → Equipment type | description classified by an ordered taxonomy (§2.7) in English, French, Spanish and Portuguese, tolerant of garbled accents; generic classes blocked for vehicles, CCTV, printing | `keyword` / 0.7 | {{DOMAIN_RESULT}} |
| Tender → Governance context | country + year of invitation; scores forward-filled up to 3 years (they are annual and published with a lag) | `temporal` | all tenders |
| Lesson → Chunk → Document → Project | lesson keeps its source chunk and page; chunk keeps document; document keeps projects | `llm_extracted` (Databricks) / `rule_numbered_paragraph` (local) | {{LESSONS_RESULT}} |
| Country identifiers | ISO-2 (awards, projects, documents) ↔ ISO-3 (indicators) ↔ names in notices, including two spellings ("Viet Nam" / "Vietnam") | alias table | - |

Matches between 0.6 and 0.9 go to a **review queue** (a small table plus a Databricks App form). Confirmed decisions
are written back as `method = 'human_confirmed'` and reused, so the resolver improves with use. The WB `supplier_id`
is kept as evidence but not trusted alone, because the same firm appears under several IDs across years.

### 2.4 Knowledge-graph topology

```
 NOTICE ─ANNOUNCES─▶ TENDER ─RESULTED_IN─▶ CONTRACT ─AWARDED_TO─▶ SUPPLIER ─BASED_IN─▶ COUNTRY
                        │  └──OF_TYPE──┐      │  └──OF_TYPE──▶ EQUIPMENT TYPE (PCR, imaging, analyzers …)
                        └────FINANCED_BY──────┴─────▶ PROJECT ─LOCATED_IN─▶ COUNTRY ─IN_REGION─▶ REGION
                        │                               │  └─IN_SECTOR─▶ SECTOR
                        └─HAS_CONTEXT(year)─▶ GOVERNANCE│
                                                        ◀─DESCRIBES─ DOCUMENT (PAD/PP/ISR/ICR/PPAR) ◀─PART_OF─ CHUNK
                                                        ◀─ABOUT───── LESSON ─FROM─▶ CHUNK
                                                                       ├─AT_STAGE─▶ STAGE (planning … contract mgmt)
                                                                       └─APPLIES_TO─▶ CATEGORY (Works/Goods/CS/NCS)
```

Every edge row is `(src_type, src_id, rel, dst_type, dst_id, method, confidence, valid_from, evidence)`. `valid_from`
supports temporal questions such as "what did we know when this tender opened?", which the models need to avoid
leakage. **Worked example:** a supplier's contract links to a specific ICR lesson in three hops.

```sql
-- Lessons from the reports of projects where this supplier delivered PCR / GeneXpert equipment
SELECT l.lesson, l.evidence_quote, l.title, l.page_start
FROM gold.kg_edges a                                   -- CONTRACT -AWARDED_TO-> SUPPLIER
JOIN gold.kg_edges f ON f.src_id = a.src_id AND f.rel = 'FINANCED_BY'          -- CONTRACT -> PROJECT
JOIN gold.lessons  l ON l.project_id = f.dst_id AND l.quote_verified
JOIN gold.fact_contract c ON c.wb_contract_number = a.src_id
                         AND c.item_type = 'Molecular diagnostics (PCR, GeneXpert)'
WHERE a.rel = 'AWARDED_TO' AND a.dst_id = :supplier_key;
```

In the prototype the graph holds {{KG_EDGES}} edges.

**Scalability.** The full public corpus is about 300k contract rows, about 425k notices, about 22k projects and tens
of thousands of project documents. That is small for Delta. Tables are clustered by `country_iso3` and year (liquid
clustering), and the edge table by `(rel, dst_id)` for reverse lookups. Supplier resolution uses blocking, so it is
near-linear. Chunk embedding is the only heavy step, and CDF keeps it incremental.

### 2.5 Long-document parsing, chunking and embedding

ICRs and PADs run to 60-150 pages. The procurement signal is concentrated in a few sections (*Key factors that
affected implementation*, *Lessons*, *Fiduciary compliance*, procurement annexes), and every page repeats a header.
The pipeline:

1. **Text extraction.** Text-layer PDFs (most WB documents since about 2010) use `pypdf`, keeping line breaks.
   Scanned or table-heavy pages use layout-aware parsing (`ai_parse_document` on Databricks) and OCR. They are
   detected by a words-per-page threshold.
2. **Running header and footer removal.** Short lines that repeat on ≥ 30% of pages, with digits normalised ("Page 16")
   are removed.
3. **Heading hierarchy.** Report numbering is detected: Roman for level 1 (`V. LESSONS AND RECOMMENDATIONS`), letters
   for level 2 (`B. KEY FACTORS DURING IMPLEMENTATION`), and `ANNEX n`. Each chunk carries its **section path**.
4. **Typed, bounded chunks.**
   - Chunks never cross a section boundary and hold ≤ 450 words with 60 words of overlap.
   - Each is typed `narrative`, `table` (number-dense lines) or `annex`.
   - Each is classified into a `section_kind`: lessons, key_factors, procurement_fiduciary, risk, ratings, results,
     design, annex, other.
5. **Structured extraction for data sheets.** The ICR data sheet holds the complete **ISR rating history**, key dates
   (planned vs actual closing, restructurings) and final ratings. ISRs hold current and previous DO/IP ratings. These
   are parsed with deterministic rules, not an LLM, because they are tables with known layouts and exact values matter.
6. **Contextual embedding text.** Each chunk is embedded as `doc type | project | country | sector | date | section
   path` + text. A passage that never names the country or sector still matches queries about them.
7. **Embedding and index.** `databricks-gte-large-en` (1024-d) runs in a Delta Sync index on the chunk table. Filter
   columns are synced (`section_kind`, `content_type`, `doc_type_code`, `country_iso3`, `project_id`, `major_sector`)
   and queries run **hybrid** (semantic + keyword), so P-codes and terms like "re-tender" match exactly.
8. **Small-to-big context.** Retrieval returns chunks. The copilot can expand a hit to its whole section for the LLM,
   which also gives the reader the section path.

Prototype: {{DOC_RESULT}}

### 2.6 Missing and incomplete reporting

| Gap | Handling |
|---|---|
| CPIA not published for non-IDA countries (e.g. India) | kept NULL plus a `cpia_missing` flag; WGI covers all countries; gradient boosting handles NULL natively |
| Governance scores published with a lag | forward-filled ≤ 3 years, never back-filled (that would leak the future) |
| Tenders with no award yet | **right-censored**, not dropped (Kaplan-Meier); dropping them would make benchmarks too optimistic. A tender with no award notice after 2 years is not "still open" (most likely cancelled, or the award was never published), so it is excluded as *unknown outcome* and counted, rather than censored at a multi-year age. Prototype: {{CENSOR_EFFECT}} |
| Awards without an invitation notice (RFQ, direct selection, individual consultants) | contribute to supplier and competition statistics, excluded from cycle-time targets |
| Award notices published late or amended | earliest *notification* date used; amended notices flagged (`award_amended_flag`) |
| Documents only partly machine-readable | words-per-page check routes them to OCR / layout parsing; a chunk without a verified quote is never served as a lesson |
| Thin cells (e.g. microscopes in one country) | benchmark fallback hierarchy: country + category + method → equipment type + method → equipment type → category + method → category, with `n` always shown; abstain under 15 |
| Descriptions in several languages, garbled accents, or with no content ("Lot 3") | multilingual, accent-tolerant patterns; contracts that cannot be classified stay "Other goods" and are counted, never guessed |

### 2.7 Domain layer: laboratory and diagnostic equipment

The equipment taxonomy is ordered: specific classes are tested before the generic "General laboratory" class, and
non-domain classes (medicines, PPE, other medical equipment, IT and vehicles) are kept so that market context and the
models see the whole health-goods picture. Measured on the prototype data:

{{TAXONOMY_TABLE}}

A manual check of a random sample of domain-classified descriptions found the specific classes accurate (PCR kits,
viral transport media, autoclaves, ultrasound). The remaining errors were in the generic class, for example vehicles
"for the national laboratory". Those are now blocked. In production a small labelled set (about 500 descriptions)
replaces the manual check, and an LLM classifier handles the residual "Other goods".

**Domain-specific risks the platform watches**, each a retrieval topic and, where public data allows, a feature:

| Risk | Why it matters for lab and diagnostic equipment | Evidence the platform uses |
|---|---|---|
| Closed-system lock-in | the platform choice fixes reagent and cartridge suppliers for years | supplier concentration by equipment type; repeat awards to the same supplier for reagents after equipment |
| Specification bias | technical specs can be written around one brand, giving one responsive bid | single-bid risk model; bidder counts and rejection reasons from award notices |
| Installation and site readiness | equipment arrives before the lab has power, space or trained staff | lessons and ISR passages on installation, utilities and training |
| Maintenance and service | no service contract means idle equipment within a year | lessons on maintenance and after-sales; share of contracts bundling service |
| Cold chain and logistics | reagents and kits expire in transit or storage | lessons and passages on storage, stock-outs and expiry |
| Emergency procurement | COVID-19 shifted buying to direct contracting and UN agencies | method mix by year; direct-selection share; award times by method |

---

## 3. Module 2: Predictive and prescriptive engines

### 3.1 Targets, aligned to procurement stages

| Stage | Target | Definition from public data |
|---|---|---|
| Planning | **Tender cycle time** | invitation (IFB/REOI) date → notification of award; open tenders censored |
| Tender design | **Competition risk** | single responsive bid (competitive methods only), from award notice bidder lists |
| Evaluation and award | **Re-tender risk** | same reference re-advertised, or "re-bid / re-tender / re-advertisement" in the notice |
| Contract management | **Amendment risk** | award notice re-issued (version > 0). Public data has no contract amendments; in an internal deployment the procurement tracking system (STEP) amendment records replace this proxy |
| Execution (project level) | Restructuring and closing extension | ICR key dates (planned vs actual closing, number of restructurings) |

### 3.2 Features (planning-time only, leakage-controlled)

| Group | Features |
|---|---|
| Package | category, **equipment type**, method, review type (prior/post), estimated amount (log), bid window days, invitation month, description keywords (supply, consulting, IT, vehicles, lots), description length |
| Project | commitment (log), project age at invitation, environmental category, E&S risk rating, major sector, region |
| Institutional | CPIA public-sector management, transparency, budget and financial management; WGI government effectiveness, regulatory quality, control of corruption; missing flags |
| History (as of invitation date) | median cycle of tenders **awarded before** this one opened in the same country × category and in the same project; number of prior tenders in the project |

History features are computed with a strict as-of cut (only tenders awarded before the invitation date), so the
backtest is honest.

### 3.3 Models

- **Benchmarks: Kaplan-Meier survival curves** per cohort, reporting the median and P80 with `n` and the number
  censored. This is the transparent, explainable baseline planners see first.
- **Package forecast: gradient-boosted quantile regression** (P50 and P80 of log-days). Quantiles answer the
  planner's question directly ("what date can I commit to?") and are robust to the heavy right tail.
- **Risk scores: gradient-boosted classifiers** for single-bid, re-tender and award-amendment risk. Training uses class-balanced
  weights, then probabilities are prior-corrected so they read as calibrated probabilities against the historical
  base rate.
- **Extension:** for in-flight monitoring, a discrete-time hazard model (or Cox with time-varying covariates) updates
  the probability of award in the next 30 days as the tender ages (§3.6). For packaging decisions, a Bayesian
  hierarchical model pools thin country × category cells toward the regional mean.

### 3.4 Validation (out-of-time)

Training uses tenders opened before {{SPLIT}}; testing uses tenders opened afterwards. The test set is restricted to
tenders that had at least a year to complete, so it is not biased toward fast tenders.

{{MODEL_TABLE}}

### 3.5 Explainability

Global drivers come from permutation importance on the test period. Per-package drivers use SHAP in production. Each
brief shows the benchmark beside the model estimate, so a planner can see when and why the model departs from history.

### 3.6 Execution monitoring and early warning (design)

| Signal | Source | Trigger |
|---|---|---|
| Tender overdue | open tender age vs cohort KM curve | age > cohort P80 → amber; P90 → red |
| Supervision deterioration | ISR DO/IP ratings trajectory | two-step drop or MU and below |
| Disbursement lag | disbursement feed vs approved S-curve | lag > 25% at mid-term |
| Competition stress | rising single-bid share in the country × category | rolling 12-month rate > 1.5× base rate |
| Restructuring risk | ICR key-dates history for similar projects | predicted extension > 12 months |

### 3.7 Agentic retrieval (RAG) for a new procurement package

```
 package (country, category, method, estimate, description, planned dates)
   │
   ├─ PLAN      sub-queries: package description; one per elevated risk (single bid / re-tender / delay);
   │            country-specific variant; stage-specific variant (planning, tender design …)
   ├─ ACT       tools (Unity Catalog functions + models):
   │              proc_benchmark          → B  Kaplan-Meier cohort with fallback and n
   │              @champion models        → M  P50 / P80, risk probabilities vs base rates
   │              proc_similar_tenders    → T  what comparable packages did (method, days, bidders, re-tender)
   │              proc_search_lessons     → L  quote-verified lessons (hybrid vector search, section_kind filter)
   │              proc_search_passages    → P  supporting passages from PAD/ISR/ICR/PPAR
   │              proc_project_evidence   → R  conflict-resolved rating trajectory of the lesson's project
   │              proc_country_context    → G  CPIA / WGI context
   ├─ FUSE      reciprocal-rank fusion across sub-queries; boosts for same country / sector / procurement relevance;
   │            diversity cap (≤ 2 items per report); (production) cross-encoder rerank
   ├─ REGISTER  every row becomes evidence with an id, its source, page and URL
   ├─ COMPOSE   statements built only from registered evidence; derived numbers (e.g. date = invitation + P80)
   │            become their own evidence item (D) showing the calculation
   ├─ VERIFY    §4 gate → statements that fail are dropped and logged
   └─ OUTPUT    brief: Schedule · Market approach · Risks · Lessons · Supporting evidence · Evidence list
```

Prototype brief for a new package, {{DEMO_PACKAGE}} (abridged):

{{BRIEF_EXAMPLE}}

Retrieval quality: {{RETRIEVAL_RESULT}}

### 3.8 Conversational access: the procurement chatbot

Most users will not start from a package form. They will ask questions. The chatbot is a thin, auditable router over
the same tools, not a free-form LLM:

| Question type | Example | Tool | Why this tool |
|---|---|---|---|
| Ranking | "Which supplier won the cheapest GeneXpert contract?" | pandas / SQL over `fact_contract` | a minimum is a calculation; an LLM should never do it |
| Suppliers and market | "Top suppliers of imaging equipment in Sub-Saharan Africa?" | group-by over resolved suppliers | supplier spellings are already merged (§2.3) |
| Spend | "How much was spent on lab equipment by country?" | aggregation with region / country / year | exact totals with the filters shown |
| Timelines | "How long does a PCR tender take?" | Kaplan-Meier benchmarks by equipment type | censoring-aware medians and P80 |
| Why / lessons / risks | "What went wrong with lab equipment maintenance?" | hybrid retrieval over lessons and passages | quotes with document, project and page |
| Relationships | "How is Kenya connected to its lab suppliers?", "Show the ER diagram" | `kg_edges` neighbourhood → diagram | every link carries its method and confidence |
| Entities | "Tell me about project P…", "What did <supplier> supply?" | profile across all tables | one view of contracts, documents and ratings |

Understanding is deterministic: entity extraction for country, region, equipment type, supplier (against resolved
names), project and years, then intent rules. Every answer is a list of statements citing evidence ids. It passes the
§4 verifier, and the page shows the evidence, the result table and, for relationship questions, the graph. An LLM
(optional) may rephrase the verified statements. Its text is discarded if it contains any number not in the
evidence. Measured on a labelled question set: {{CHATBOT_RESULT}}

{{CHAT_EXAMPLES}}

### 3.9 Document Q&A by country (reusable RAG pipeline)

The chatbot's second mode lets a user choose a **country** and a **category** and ask any question; the answer comes
only from that country's World Bank project documents and shows its sources. It is a pipeline, not a one-off index, so
a new country is configuration, not code:

| Stage | Local | Databricks | What it does |
|---|---|---|---|
| Registry | `procurement_rag/countries.json` | `ops.rag_countries` (notebook 30) | one row per country (ISO codes, name, pinned projects); shared defaults for document types and limits |
| Fetch | `fetch.py` | notebook 31 → volume `rag_docs/<country>/<type>/<id>.pdf`, `bronze.rag_documents` | projects that bought lab equipment there, lab-related health projects (Projects API), pinned regional programmes; newest PAD, project papers, procurement plans, ISR, ICR, IEG review, PPAR per project |
| Process | `process.py` | notebook 32 → `silver.rag_pages`, `gold.rag_chunks` | text per page once per PDF; section-aware passages (§2.5); category tags from the equipment taxonomy (§2.7) |
| Index | `index.py` → ChromaDB | notebook 33 → Vector Search Delta Sync index | embeddings, filterable by country and category; incremental, other countries untouched |
| Answer | `search.py`, chatbot tab | notebook 34, Databricks App | hybrid retrieval (meaning + exact words), at most two passages per document; answer = the sentences that answer the question, each with its source, or a written summary shown only if every citation and number is in the cited sources |

Answer sentences are chosen by meaning, weighted towards the question's distinctive words; for questions about
equipment, sentences that name equipment come first. If nothing in the selected country's documents is close enough,
the chatbot says so instead of guessing. {{DOCQA_RESULT}}

{{DOCQA_TABLE}}

---

## 4. Module 3: Guardrails, faithfulness and source attribution

### 4.1 Layered controls

| Layer | Control | Prototype status |
|---|---|---|
| Ingestion | Lessons enter the knowledge base only with a **verbatim evidence quote** checked against the source chunk (`quote_in_source`: exact after normalisation, else a ≥ 0.9 fuzzy window to tolerate PDF noise). Unverified lessons are stored but never served | {{L_INGEST}} |
| Ingestion | Data sheets (ratings, key dates) are parsed deterministically, never by an LLM | ISR histories parsed from every ICR |
| Retrieval | Only governed sources: UC functions over Gold tables and the index, with metadata filters (`section_kind`, `content_type`); no open web | yes |
| Composition | **Evidence registry.** Statements are built from registered items, and derived numbers are registered with their inputs | yes |
| Generation (optional LLM) | The LLM may only rephrase a statement; temperature 0; output must keep the citations | Databricks notebook 28 |
| Verification | Every statement must (1) cite ≥ 1 existing evidence id, (2) contain only numbers present in the cited evidence (rounding tolerance 2%), (3) quote only text found verbatim in the cited source. Failing statements are **dropped and logged**, never shown | {{L_VERIFY}} |
| Abstention | Below the evidence threshold (benchmark n < 15, no verified lesson), the brief says so instead of guessing | yes |
| Prompt injection | Retrieved text is data inside a delimited block; tools are read-only functions; the agent cannot write or call the web | inherited from the existing assistant (red-teamed) |
| Monitoring | MLflow traces of every tool call; groundedness and correctness judges on a growing evaluation set; verifier drop rate and user "wrong" flags on the dashboard | §5 |
| Presentation | Each statement shows clickable citations (document, section, page); model numbers are labelled as estimates with their validation coverage | yes |

**Red-team result:** {{REDTEAM}}

### 4.2 Resolving conflicting institutional evidence

Supervision ratings (ISR, every six months) and completion evaluations (ICR, IEG) often disagree. That is expected:
they are measured at different times with different information. The rules:

1. **Same dimension only.** Compare like with like: DO rating vs outcome rating; IP rating vs implementation
   performance. Different dimensions are shown side by side, never "resolved".
2. **Authority hierarchy for the final judgement.** Independent ex-post (IEG PPAR) > independent validation (IEG ICRR)
   > self-evaluation at completion (ICR) > in-flight supervision (ISR) > appraisal expectation (PAD). Completed and
   independent evaluations see the whole outcome; mid-term ratings are snapshots.
3. **Recency within the same authority.**
4. **Never discard the loser.** The resolved record keeps the **full dated trajectory** and a `conflict` flag (evidence
   on both sides of the satisfactory line). A mid-term crisis followed by recovery is itself a lesson about what
   turned it around.
5. **Field-level source of record for transactional conflicts.** Signed amount and supplier come from the contract
   awards dataset. Bidder list and notification date come from the award notice. Dates disagreeing by more than 30
   days are flagged for review rather than averaged.
6. **Show it to the user.** When a lesson's project had conflicting ratings, the brief says so (e.g. "supervision
   ratings fell to Moderately Unsatisfactory before the final ICR rating of Satisfactory"), with both sources cited.

Prototype: {{CONFLICT_RESULT}}

---

## 5. Module 4: Continuous learning and implementation strategy

### 5.1 Closed feedback loop

| Event | Latency | What updates |
|---|---|---|
| New IFB / REOI / award notice | daily | `fact_tender` (new tender or award), censoring status, KG edges; open tenders' ages for early warning |
| New contract award row | daily | `fact_contract`, supplier resolution, tender link |
| New ISR / ICR / PPAR | weekly | chunks → index sync (CDF, only changed chunks re-embedded) → lesson extraction → rating evidence → conflict resolution |
| Realised award of a package that received a brief | on award | `ops.proc_feedback.realised_award_date`, so live P80 coverage and MAE are measured on the copilot's own predictions |
| User feedback on an evidence item (useful / not relevant / wrong) | immediate | retrieval evaluation set; "wrong" items go to curator review; repeat offenders are down-weighted |
| Retraining trigger: ≥ 200 newly completed tenders, PSI > 0.2 on a key feature, or live P80 coverage outside [0.70, 0.90] | weekly check | `26_proc_risk_models` retrains; quality gate (beats benchmark MAE and P80 coverage in range) → `@champion` alias |

The Lakeflow Job runs: ingest → build graph → docs and lessons → index sync → monitor → *(condition)* retrain →
gate. This is the same pattern as the existing assistant's job, where deployment happens only if evaluation passes.

### 5.2 Phased pilot rollout

| Phase | Duration | Scope | Exit criteria |
|---|---|---|---|
| 0 Data foundation | 4-6 weeks | all public sources for one region; entity resolution review queue; benchmarks dashboard | link rates at targets (§5.3); data contracts green for 4 weeks |
| 1 Pilot | 8-10 weeks | 2-3 country health programmes buying laboratory and diagnostic equipment, about 20 procurement specialists, TTLs and biomedical engineers; chatbot and briefs in the planning step (procurement plan review, specification drafting) | faithfulness ≥ 99%; P80 coverage 0.75-0.85; ≥ 60% weekly active; usefulness ≥ 70% |
| 2 Scale | 3-4 months | all health goods, then other domains through the same scope configuration; internal sources (STEP procurement tracking, contract amendments, internal ISR notes); early-warning alerts | KPI improvements vs matched control units (below) |
| 3 Institutionalise | ongoing | integrate into the operations portal and procurement plan workflow; IEG thematic studies feed lessons; quarterly model review | governance sign-off; cost per brief within budget |

Evaluation design: pilot units are compared with **matched control units** (same region, similar portfolio) on the
same KPIs, before and after, which gives a difference-in-differences estimate of impact.

### 5.3 KPIs

| Family | KPI | Target (pilot) | Prototype baseline |
|---|---|---|---|
| Decision impact | Procurement-plan preparation time per package | -30% | measure in Phase 0 (time-and-motion) |
| Decision impact | Share of packages awarded by their planned date (schedule realism) | +20 pp | {{KPI_SCHED}} |
| Decision impact | Re-tender rate in pilot units | -15% | {{KPI_REBID}} |
| Decision impact | Single-bid rate (competitive methods) | -10% | {{KPI_SINGLE}} |
| AI quality | Retrieval precision@5 / recall@20 on the labelled risk-factor set | ≥ 0.7 / ≥ 0.6 | {{KPI_RETR}} |
| AI quality | Statements shown with valid citations | 100% | {{KPI_CIT}} |
| AI quality | Injected-hallucination catch rate (red team) | ≥ 99% | {{KPI_RED}} |
| AI quality | P80 coverage on realised awards | 0.75-0.85 | {{KPI_P80}} |
| AI quality | Cycle-time MAE vs benchmark-only | ≥ 10% better | {{KPI_MAE}} |
| Data | Contract → tender link rate; documents linked to transacting projects | ≥ 80% (with internal data); 100% | {{KPI_LINK}} |
| Adoption | Weekly active users / pilot users; briefs per new package; evidence usefulness rate | ≥ 60%; ≥ 1; ≥ 70% | - |
| Operations | Data freshness (notice → available); p95 brief latency; cost per brief | < 24 h; < 20 s; tracked | - |

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Public data gaps (no amendments, partial notices, no internal notes) | proxies flagged as such; Phase 2 adds internal systems through the same Bronze contracts |
| Over-reliance on a model number | the brief always shows the benchmark, `n`, the P80 and validation coverage; abstains when evidence is thin |
| Bias toward data-rich countries | hierarchical fallback with `n` shown; per-country error monitoring; pooled priors for thin cells |
| Confidential documents (Official Use Only) | Unity Catalog grants per document classification; the copilot runs as a service principal with least privilege; audit of every access |
| Supplier name resolution errors | confidence on every edge; review queue; human-confirmed decisions reused |
| LLM drift or provider change | the LLM only rephrases; facts come from tools and the verifier; endpoints are swappable |
| Cost of embedding large corpora | CDF-incremental sync; embed narrative chunks only; tables summarised |

---

## 7. Prototype: what was built and measured

All figures below come from `procurement_demo/output/eval_results.json`, produced by
`python -m procurement_demo.run_all` on public World Bank data downloaded on {{RUN_DATE}}.

{{PROTOTYPE_SECTION}}

**Two implementations, one core.** The parsing, entity-resolution, censoring-aware benchmark, conflict-resolution and
verifier code lives in `notebooks/20_proc_lib.py`. The Databricks notebooks (`%run ./20_proc_lib`) and the local
prototype (`procurement_demo/`) both use it, and it is unit-tested against real notices, an ISR and an ICR
(`python src/test_proc_lib.py`).

| Component | Databricks (run end to end on {{DATABRICKS_RUN_DATE}}) | Local prototype (verified) |
|---|---|---|
| Settings | `00_proc_config` (own catalog, scope, endpoints) | `procurement_demo/common.py` |
| Ingestion | `21_ingest_procurement`, `22_ingest_project_docs` | `procurement_demo/ingest.py` |
| Silver / Gold, entity resolution, graph | `23_proc_silver_gold` | `build.py` |
| Document structure, lessons, ratings | `24_proc_docs_lessons` (LLM extraction + quote check) | `build.py` (rule-based extraction + quote check) |
| Vector index | `25_proc_vector_index` (hybrid Vector Search) | `retrieval.py` (BM25 + filters + RRF) |
| Models | `26_proc_risk_models` (MLflow, UC registry, champion gate) | `models.py` |
| Tools / copilot | `27_proc_tools`, `28_proc_copilot` | `copilot.py` |
| Monitoring, job | `29_proc_monitor_job` | `evaluate.py` |
| Document Q&A by country | `30_rag_registry` → `34_rag_ask`, Vector Search, job `proc_rag_pipeline`, Databricks App | `procurement_rag/` (ChromaDB) |
| Chatbot | Databricks App (document Q&A, workspace sign-in) | `chatbot.py` + `app.py` (Streamlit), public at the link above |

## Appendix A: Glossary

| Term | Meaning |
|---|---|
| IFB / REOI | Invitation for Bids / Request for Expressions of Interest: the notice that starts a tender |
| RFB, RFQ, QCBS, CQS, INDV | Request for Bids, Request for Quotations, Quality- and Cost-Based Selection, Consultant Qualification Selection, Individual consultant |
| Prior / post review | Bank reviews the procurement before (prior) or after (post) the award, depending on thresholds and risk |
| PAD, ISR, ICR, PPAR, ICRR | Project Appraisal Document; Implementation Status and Results Report; Implementation Completion and Results Report; IEG Project Performance Assessment Report; IEG ICR Review |
| DO / IP rating | Development Objective / Implementation Progress rating (Highly Satisfactory … Highly Unsatisfactory) |
| CPIA / WGI | Country Policy and Institutional Assessment (1-6) / Worldwide Governance Indicators (about -2.5 to 2.5) |
| Censored observation | a tender still open at the data cut: its duration is at least its current age |
| P50 / P80 | the duration within which 50% / 80% of comparable tenders are awarded |
| PCR, GeneXpert | polymerase chain reaction (molecular testing); a cartridge-based molecular platform widely used for TB and COVID-19 |
| Closed system | an analyzer that only runs its manufacturer's reagents or cartridges |
| LIMS, BSL | laboratory information management system; biosafety level of a laboratory |
