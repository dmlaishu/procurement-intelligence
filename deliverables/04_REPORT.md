# Deliverable 4: Assessment Report

**AI-powered procurement intelligence for medical laboratory and diagnostic equipment in World Bank-financed health
projects: what was built, what the data shows, and what comes next**

| Item | Detail |
|---|---|
| Run date | 22 October 2026 |
| Domain | Medical laboratory and diagnostic equipment in World Bank-financed health projects |
| Data | public World Bank APIs only: Major Contract Awards, Procurement Notices, Projects, Documents & Reports, WDI (CPIA, WGI) |
| Coverage | 2,220 lab and diagnostic contracts (US$1,375.4M), 91 countries, 179 projects, FY2015 onwards |
| Companion deliverables | 1 Data Model · 2 Approach (technical proposal) · 3 Prototype (pipeline + chatbot) |

## 1. Summary

World Bank health projects buy a large amount of laboratory and diagnostic equipment, but the records of what was
bought, how and why it worked sit in separate places: contract award data, procurement notices, and PDF reports. We
built a working prototype that connects them for this one domain, across every country. It turns them into answers a
procurement team can use, through a cited chatbot and package briefs.

| Question | Answer from the prototype |
|---|---|
| How big is the domain in public data? | **2,220 contracts worth US$1,375.4M** across 91 countries, 179 projects and 1,277 suppliers, found by classifying 17,058 health-goods contract descriptions in four languages |
| Can the silos be joined? | Yes. **100%** of contracts link to their project and **67%** to their tender; 131 project reports connect through Project ID (115,327 graph links) |
| What does the market look like? | Diagnostic imaging is the largest category (US$451.6M). 53% of value was signed in 2020-2022 (COVID-19). Domestic suppliers took 58% of value; 16 suppliers won in two or more countries |
| How long do tenders take? | Medians from invitation to award range from 90 days (Laboratory analyzers) to 183 days (Biosafety, sterilization and cold chain), censoring-aware |
| Is the forecast better than a benchmark? | Yes. Out-of-time median error **47 days** vs 56 for cohort benchmarks (**17% better**) |
| Does the chatbot answer correctly? | It routed **100%** of 16 labelled questions to the right tool; ranked and aggregate answers matched an independent query in **6 of 6** checks; 100% of statements cited |
| Can hallucination be controlled? | Yes, by design. 100% of 360 brief statements cite evidence; the verifier blocked **124 of 125** deliberately injected false statements |
| Are the risk models ready to drive decisions? | Not yet. Single-bid AUC 0.67; re-tender skipped: not enough positives; award amendment AUC 0.55. Use as indicators only |

## 2. What was built

| Module (assessment §4) | Prototype component | Status |
|---|---|---|
| 1. Ingestion and knowledge representation | contract-first domain selection, multilingual equipment taxonomy, entity resolution, Gold star schema, knowledge-graph edge table, section-aware PDF chunking | built and run |
| 2. Predictive and prescriptive engines | Kaplan-Meier benchmarks by equipment type, gradient-boosted P50/P80 model, single-bid / re-tender / amendment classifiers, hybrid retrieval, package briefs | built and run |
| 2. Retrieval for users | **chatbot** (`python -m streamlit run procurement_demo/app.py`): SQL for prices, suppliers and spend; benchmarks for timelines; retrieval for lessons; graph diagrams for relationships | built, run and evaluated |
| 3. Guardrails and attribution | evidence registry, verifier (citations, numbers, quotes, pages), rating-conflict resolver incl. IEG reviews | built, run and red-teamed |
| 4. Continuous learning and rollout | incremental ingestion, monitoring and retrain gate, feedback tables, KPI view | designed and written as Databricks notebooks 21-29; **not yet executed** (trial workspace blocked job runs) |

## 3. Data: how the domain was assembled

| Step | Result |
|---|---|
| Health-practice Goods contracts pulled (FY2015+) | 17,058 |
| Classified as laboratory or diagnostic equipment | 2,220 contracts, US$1,375.4M |
| Projects that bought such equipment | 179 (all their 14,613 health-goods contracts kept as market context) |
| Procurement notices of those projects (Goods) | 15,158 → 10,403 tenders (1,947 for lab and diagnostic items) |
| Project documents parsed (PAD, ISR, ICR, IEG review, procurement plan) | 131 → 6,187 section-aware passages |
| Suppliers | 7,835 raw names → 7,540 resolved (94 fuzzy merges) |

**Data quality found along the way.**

- **Language and encoding.** Descriptions come in English, French, Spanish and Portuguese, and accents often arrive
  garbled ("r?actifs"). Patterns therefore tolerate a wildcard in place of accents.
- **Uninformative descriptions.** Some contracts are described only as "Lot 3", "Item 42" or "no contract". They stay
  unclassified rather than being guessed.
- **Missing invitation notices.** Many emergency purchases (direct contracting, UN agencies) have no invitation notice.
  They count in market statistics but cannot give a tender duration. Only 1,068 tenders have both
  an invitation and an award; 122 are still open (censored) and 442
  had no award after two years.
- **No completion reports yet.** Most COVID-era projects are still active, so lessons come mainly from older, closed
  health projects (150 lessons extracted).

## 4. What the data shows about laboratory and diagnostic procurement

### 4.1 Spend by equipment type

| Equipment type | Contracts | Value | Share | Median contract | Countries | Suppliers | Top-5 supplier share | Domestic supplier share |
|---|---|---|---|---|---|---|---|---|
| Diagnostic imaging | 292 | US$451.6M | 33% | US$565,485 | 61 | 216 | 27% | 62% |
| General laboratory and diagnostic equipment | 427 | US$264.1M | 19% | US$150,000 | 66 | 342 | 44% | 44% |
| Molecular diagnostics (PCR, GeneXpert) | 498 | US$212.2M | 15% | US$77,380 | 60 | 227 | 23% | 70% |
| Reagents and cartridges | 428 | US$151.2M | 11% | US$84,248 | 52 | 291 | 31% | 45% |
| Biosafety, sterilization and cold chain | 226 | US$126.2M | 9% | US$68,136 | 55 | 189 | 53% | 57% |
| Rapid tests and test kits | 172 | US$117.1M | 9% | US$340,175 | 46 | 107 | 57% | 62% |
| Laboratory analyzers | 119 | US$35.4M | 3% | US$132,000 | 32 | 99 | 40% | 81% |
| Microscopy | 34 | US$10.0M | 1% | US$116,964 | 17 | 28 | 72% | 97% |
| Mobile and modular laboratories | 15 | US$6.9M | 1% | US$174,374 | 5 | 13 | 83% | 68% |
| Laboratory information systems | 9 | US$0.7M | 0% | US$39,124 | 3 | 8 | 95% | 54% |

*Top-5 supplier share* is the share of the type's value won by its five largest suppliers; a high value points to a
concentrated market or closed systems. *Domestic* means the supplier is registered in the borrower country.

### 4.2 Where and when

| Region | Contracts | Value | Share | Countries |
|---|---|---|---|---|
| Sub-Saharan Africa | 868 | US$503.0M | 37% | 35 |
| Europe & Central Asia | 289 | US$319.6M | 23% | 17 |
| South Asia | 566 | US$210.9M | 15% | 5 |
| Middle East, North Africa, Afghanistan & Pakistan | 138 | US$119.0M | 9% | 7 |
| East Asia & Pacific | 115 | US$103.2M | 8% | 12 |
| Latin America & Caribbean | 178 | US$101.3M | 7% | 15 |

| Top countries | Value | Share |
|---|---|---|
| India | US$166.3M | 12% |
| Turkiye | US$75.6M | 5% |
| Madagascar | US$66.4M | 5% |
| Serbia | US$64.6M | 5% |
| Uzbekistan | US$56.2M | 4% |
| Ukraine | US$44.5M | 3% |
| Ghana | US$42.5M | 3% |
| Iraq | US$42.0M | 3% |
| El Salvador | US$34.9M | 3% |
| Senegal | US$31.2M | 2% |

| Signing year | Contracts | Value |
|---|---|---|
| 2019 | 38 | US$39.4M |
| 2020 | 805 | US$381.7M |
| 2021 | 310 | US$177.1M |
| 2022 | 212 | US$166.0M |
| 2023 | 326 | US$262.0M |
| 2024 | 251 | US$192.1M |
| 2025 | 184 | US$111.9M |
| 2026 | 67 | US$45.1M |

Spending peaked in 2020; 53% of all value was signed in 2020-2022.

### 4.3 Who supplies

| Supplier | Based in | Contracts | Value | Share | Countries | Main equipment types |
|---|---|---|---|---|---|---|
| UNITED NATIONS OFFICE PROJECT SERVICES (UNOPS | World | 23 | US$146.0M | 11% | 9 | General laboratory and diagnostic equipment, Molecular diagnostics (PCR, GeneXpert) |
| MAGNA PHARMACIA | Serbia | 3 | US$37.3M | 3% | 1 | Diagnostic imaging |
| UNITED NATIONS DEVELOPMENT PROGRAMME (UNDP) | World | 8 | US$36.0M | 3% | 3 | General laboratory and diagnostic equipment, Reagents and cartridges |
| BIOSENSOR HEALTHCARE | India | 38 | US$32.1M | 2% | 1 | Rapid tests and test kits |
| UNITED NATIONS CHILDREN'S FUND (UNICEF) | World | 12 | US$27.9M | 2% | 9 | Rapid tests and test kits, Reagents and cartridges |
| WORLD HEALTH ORGANIZATION (WHO) | World | 6 | US$25.7M | 2% | 4 | General laboratory and diagnostic equipment, Molecular diagnostics (PCR, GeneXpert) |
| Philips Medical Systems Nederland B.V. | Netherlands | 5 | US$23.0M | 2% | 2 | Diagnostic imaging |
| GE MEDICAL SYSTEMS | France | 8 | US$23.0M | 2% | 3 | Diagnostic imaging |
| MEDICOM D.O.O. ŠABAC | Serbia | 3 | US$17.9M | 1% | 1 | Diagnostic imaging |
| VIMA (VISION MADA) | Madagascar | 1 | US$12.7M | 1% | 1 | Biosafety, sterilization and cold chain |
| APELEM | France | 3 | US$12.5M | 1% | 2 | Diagnostic imaging |
| SIEMENS HEALTHCARE PVT. LTD. | India | 11 | US$11.8M | 1% | 1 | Molecular diagnostics (PCR, GeneXpert), Reagents and cartridges |

16 suppliers won lab or diagnostic contracts in two or more countries. They are the firms where cross-country
evidence (price, delivery, after-sales) is most useful.

### 4.4 How it was bought

| Procurement method | Contracts | Value | Share |
|---|---|---|---|
| Direct Selection | 737 | US$574.4M | 42% |
| Request for Bids | 730 | US$540.6M | 39% |
| Request for Quotations | 733 | US$218.4M | 16% |
| UN Agencies (Direct) | 20 | US$41.9M | 3% |

| Year | Direct / single-source share of value | UN agency share of value |
|---|---|---|
| 2019 | 24% | 0% |
| 2020 | 64% | 11% |
| 2021 | 57% | 18% |
| 2022 | 26% | 18% |
| 2023 | 44% | 29% |
| 2024 | 33% | 17% |
| 2025 | 28% | 21% |
| 2026 | 26% | 0% |

### 4.5 How long tenders take

Kaplan-Meier estimates of days from invitation to award, by equipment type (all countries; still-open tenders
counted as censored):

| Equipment type | Tenders | Awarded | Median days | P80 days | Median if open tenders ignored |
|---|---|---|---|---|---|
| Laboratory analyzers | 13 | 13 | 90 | 141 | 90 |
| Molecular diagnostics (PCR, GeneXpert) | 32 | 29 | 122 | 222 | 114 |
| Rapid tests and test kits | 14 | 13 | 123 | 180 | 110 |
| Diagnostic imaging | 70 | 64 | 134 | 247 | 124 |
| General laboratory and diagnostic equipment | 84 | 70 | 145 | 288 | 112 |
| Reagents and cartridges | 30 | 27 | 150 | 253 | 149 |
| Biosafety, sterilization and cold chain | 30 | 27 | 183 | 261 | 179 |

## 5. Results by module

### 5.1 Module 1: one connected knowledge base

| Measure | Result |
|---|---|
| Contract → project link rate | 100% |
| Contract → tender link rate | 67% (9,600 exact + 182 fuzzy) |
| Supplier resolution | 7,835 names → 7,540 suppliers |
| Lessons extracted | 150 (68 procurement-related), 98.0% quote-verified |
| Knowledge-graph edges | 115,327 |

### 5.2 Module 2: models

Out-of-time validation: trained on tenders opened before 2024-01-01, tested on later ones. All health-goods
tenders of the domain projects are used for training, with equipment type as a feature.

| Model | Result | Baseline | Train / test |
|---|---|---|---|
| Days to award, P50 | MAE **47 days** | KM benchmark 56; single median 73 | 759 / 259 |
| Days to award, P80 | coverage **0.76** | target 0.80 | |
| Single responsive bid | AUC **0.67**, top-decile lift 2.6× | base rate 0.417 → 0.281 | 772 / 295 |
| Re-tender | skipped: not enough positives | base rate 0.005 | 1,163 / 469 |
| Award amendment (proxy) | AUC **0.55**, top-decile lift 1.0× | base rate 0.391 → 0.447 | 773 / 295 |

Main drivers of tender duration: `bid_window_days`, `method_code`, `review_type`, `country_iso3`, `env_category`, `major_sector`, `wgi_control_of_corruption`.

### 5.3 Module 2: retrieval

| Risk topic | Relevant lessons | Precision@5 | Precision@10 | Recall@20 | Random |
|---|---|---|---|---|---|
| delay | 61 | 0.80 | 0.70 | 0.65 | 0.41 |
| capacity | 80 | 1.00 | 0.90 | 0.85 | 0.54 |
| fiduciary | 12 | 1.00 | 0.70 | 0.83 | 0.08 |
| specification | 4 | 0.20 | 0.20 | 0.50 | 0.03 |

Structured-to-unstructured check: a project's own tender descriptions retrieve its own reports in the top 10 for
**88%** of 16 projects (MRR 0.58).

### 5.4 Module 3: guardrails and conflicting evidence

Over 25 briefs for real held-out packages, 360 statements were produced, **100%** with
valid citations. Five kinds of false statement were injected into every brief; the verifier blocked **124 of
125 (99.2%)**.

| Injected statement | Why it was blocked |
|---|---|
| Among the 53 most similar past Goods tenders, the most common method was Request for Bids (7 of 12); median nu… | unsupported numbers [53.0] |
| “Contractors consistently delivered ahead of schedule across all lots.” | quote not in source: Contractors consistently delivered ahead of schedule across  |
| Framework agreements always reduce procurement time by half. | no citation |
| Among the 12 most similar past Goods tenders, the most common method was Request for Bids (7 of 12); median nu… | unknown citation ['L999']; unsupported numbers [12.0] |
| “Introduction of c ontract terms against non- deliveries and poor deliveries .” (ICR, P152625, p. 80) - note: … | page 80 not in cited evidence |

**Conflicting evidence.** 34 projects have dated ratings from several sources (ISR
supervision, the Bank's ICR, IEG's independent review). In **8** of them the sources fall on
opposite sides of the satisfactory line. Example: P129539 reached *Moderately Unsatisfactory* during
supervision; the final rating, from the ICRR, is *Satisfactory*.
The resolver keeps the most authoritative source as the headline (IEG PPAR > IEG ICR Review > ICR > latest ISR),
stores the full trajectory, and the chatbot and briefs cite both sides.

## 6. The chatbot

| Question | Expected tool | Chosen | Route ok | Exact | Statements | Dropped by verifier |
|---|---|---|---|---|---|---|
| Which supplier won the cheapest GeneXpert or PCR contract? | rank_low | rank_low | yes | yes | 6 | 0 |
| What is the largest diagnostic imaging contract? | rank_high | rank_high | yes | yes | 6 | 0 |
| What are the lowest value laboratory reagent contracts in Nigeria? | rank_low | rank_low | yes | yes | 3 | 0 |
| Who are the top suppliers of laboratory analyzers? | suppliers | suppliers | yes | yes | 7 | 0 |
| Which vendors supplied microscopes since 2019? | suppliers | suppliers | yes | - | 7 | 0 |
| How much was spent on lab and diagnostic equipment by country? | aggregate | aggregate | yes | yes | 7 | 0 |
| What is the total value of rapid test kit contracts? | aggregate | aggregate | yes | yes | 2 | 0 |
| How long does it take to award a PCR tender? | timeline | timeline | yes | - | 2 | 0 |
| How long do laboratory analyzer tenders take from invitation to award? | timeline | timeline | yes | - | 2 | 0 |
| Why were laboratory equipment deliveries delayed? | lessons | lessons | yes | - | 8 | 0 |
| What lessons did projects learn about maintaining diagnostic equipment? | lessons | lessons | yes | - | 8 | 0 |
| What risks should I plan for when procuring X-ray machines? | lessons | lessons | yes | - | 9 | 0 |
| Show the entity relationship diagram | graph | graph | yes | - | 9 | 0 |
| How is Kenya connected to its lab equipment suppliers? | graph | graph | yes | - | 4 | 0 |
| Tell me about project P173836 | project | project | yes | - | 10 | 0 |
| What did UNITED NATIONS OFFICE PROJECT SERVICES (UNOPS) supply? | supplier | supplier | yes | - | 5 | 0 |

Route accuracy **100%**, exact answers **6/6**,
0.06 s per question on a laptop. Sample answers (verbatim from the run):

> **Q: Which supplier won the cheapest GeneXpert or PCR contract?**  
> *tool: rank_low · 6 evidence items*
>
> - The lowest-value contract for Molecular diagnostics (PCR, GeneXpert) went to SCI-FI BIOLOGICALS (India): contract 1696951, US$34, for “Primers and Probes [5 nos]”. `C1`
> - AVON PHARMO CHEM (PRIVATE) LIMITED: US$126 (contract 1697389, Sri Lanka). `C2`
> - M.M. SUPPLIERS: US$131 (contract 1696989, India). `C3`
> - LABSERVICE: US$176 (contract 1665197, Kyrgyz Republic). `C4`

> **Q: Who are the top suppliers of diagnostic imaging equipment in Sub-Saharan Africa?**  
> *tool: suppliers · 2 evidence items*
>
> - 71 suppliers won 84 contracts worth US$104,305,138 for Diagnostic imaging in Sub-Saharan Africa. `D1`
> - AFRIMED MEDICAL SUPPLIES LIMITED ACCRA GHANA (Ghana): 1 contract, US$9,382,558 (9% of value), 1 country. `D1`
> - ELSMED (Cote d'Ivoire): 1 contract, US$8,169,922 (8% of value), 1 country. `D1`
> - EXP-MEDICAL SYSTEMS AFRICA LIMITED (Ghana): 1 contract, US$6,999,643 (7% of value), 1 country. `D1`

> **Q: How long does it take to award a laboratory analyzer tender?**  
> *tool: timeline · 1 evidence items*
>
> - Laboratory analyzers: half of tenders are awarded within 90 days; 80% within 141 days (n=13). `B1`
> - Medians are censoring-aware: tenders still open count as at least their current age, so slow tenders are not silently dropped. Plan milestones on the P80, not the median. `B1`

> **Q: What problems did projects have with laboratory equipment maintenance?**  
> *tool: lessons · 12 evidence items*
>
> - “These conditions were the adoption of the: (a) Cancer Control Improvement Program with Action Plan by the Government in July 2020; (b) rulebook for the establishment of a Medical Equipment Maintenance Unit within the MoH by the Government in October 2019 , and (c) issuance of a Decree to adopt the H” (ICR, P129539, p. 30) - note: this project's supervision ratings fell to Moderately Unsatisfactory before the final ICRR rating of Satisfactory, so the lesson reflects a recovery. `L1 R1`
> - “In addition to the change in the MoH leadership in mid -June 2020, the COVID -19 pandemic also slowed down and adversely affected implementation progress and delays were also encountered for the project restructuring, for the initiation of the agreed procurement processes and subsequent partial canc” (ICR, P152625, p. 31) - note: this project's ratings were mixed across sources, and the final ICRR rating is Moderately Unsatisfactory, so the lesson comes from a project that underperformed. `L2 R2`
> - “At the Government’s request, the WB activated the CERC and reallocated USS$14 million to expedite implementation of the Master Plan.” (ICR, P157291, p. 27) `L3`
> - “Notwithstanding the cancellations, the project was still able to achieve the objectives.” (ICR, P131825, p. 36) - note: this project's supervision ratings fell to Unsatisfactory before the final ICRR rating of Moderately Satisfactory, so the lesson reflects a recovery. `L4 R3`


## 7. Example output: a brief for a new package

Input: *Supply, installation and commissioning of automated real-time PCR systems and GeneXpert analyzers for regional public health laboratories, Lot 2* (Goods, RFB, KEN,
est. US$2,500,000).

#### Schedule
- Historical benchmark (Molecular diagnostics (PCR, GeneXpert) tenders using RFB (all countries)): half of tenders reach award within 124 days of the invitation and 80% within 240 days. [B1]
- Counting only completed tenders would understate the median by 6 days, because slow tenders are still open. [B1] [D1]
- For this package the model estimates 155 days to award (P50) and 252 days at the 80th percentile. [M1]
- Plan the award milestone for 08 Nov 2027 or later (planned invitation 01 Mar 2027 plus the P80 estimate) rather than the median. [D2] [M1]

#### Risks
- Expected cycle is longer than the benchmark median (155 vs 124 days). [M1] [B1]

#### Market approach
- Among the 12 most similar past Goods tenders, the most common method was Request for Bids (7 of 12); median number of bidders 1.5. [T1] [T2] [T3] [T4] [T5] [T6] [D3]

#### Lessons from completed projects
- “In addition to the change in the MoH leadership in mid -June 2020, the COVID -19 pandemic also slowed down and adversely affected implementation progress and delays were also encountered for the project restructuring, for the initiation of the agreed procurement processes and subsequent partial canc” (ICR, P152625, p. 31) - note: this project's ratings were mixed across sources, and the final ICRR rating is Moderately Unsatisfactory, so the lesson comes from a project that underperformed. [L1] [R1]
- “Introduction of c ontract terms against non- deliveries and poor deliveries .” (ICR, P152625, p. 39) - note: this project's ratings were mixed across sources, and the final ICRR rating is Moderately Unsatisfactory, so the lesson comes from a project that underperformed. [L2] [R2]
- “The COVID -19 pandemic disrupted and delayed project activities.” (ICR, P149340, p. 19) - note: this project's supervision ratings fell to Moderately Unsatisfactory before the final ICR rating of Satisfactory, so the lesson reflects a recovery. [L3] [R3]
- “The COVID-19 pandemic significantly diverted resources and attention, delaying some project activities and necessitating adjustments to Project priorities.” (ICR, P152799, p. 31) - note: this project's supervision ratings fell to Moderately Unsatisfactory before the final ICRR rating of Moderately Satisfactory, so the lesson reflects a recovery. [L4] [R4]
- “At the Government’s request, the WB activated the CERC and reallocated USS$14 million to expedite implementation of the Master Plan.” (ICR, P157291, p. 27) [L5]
- “Future similar health projects should consider the lessons learned from this project regarding timely disbursement of county allocations.” (ICR, P152394, p. 37) [L6]

#### Supporting evidence
- “For each contract to be financed by the loan, for the different procurement methods or consultant selection methods, the need for prequalification, estimated costs, prior review requirements, and time frame will be approved by the Bank with the No Objection to the Procurement Plan.” (PAD, Procurement, p. 68) [P1]
- “For effective real-time monitoring and follow up of contract management plans for key high -value contracts under the project, the Bank team advised the PMU/MoH to establish and use a basic contract management tool.” (ICR, B. Environmental, Social, And Fiduciary Compliance, p. 34) [P2]
- “These delays did not leave time for the related procurement and, finally resulted in a decision to cancel US$34.4 million from the loan.” (ICR, B. Key Factors During Implementation, p. 32) [P3]
- “Performance on procurement arrangements and compliance was hindered by difficulties and delays encountered by the countries and regional institutions.” (ICR, B. Environmental, Social, And Fiduciary Compliance, p. 36) [P4]

## 8. Limitations

| Limitation | Effect | What would fix it |
|---|---|---|
| Public data only | no bid prices per unit, technical evaluation reports, service contracts or equipment downtime | Phase 2: STEP procurement records, asset registers, maintenance logs |
| Keyword taxonomy | a residue of contracts stays "Other goods"; a few generic items are misclassified | labelled set of about 500 descriptions; LLM classifier for the residue |
| Few completion reports for COVID-era projects | lessons skew to older projects | add IEG thematic evaluations and the next ICR cohort as they publish |
| Rule-based lesson extraction locally | lessons limited to numbered paragraphs in Lessons / Key-factor sections | LLM extraction with quote check (notebook 24) |
| Thin risk-model labels | single-bid, re-tender and amendment models are indicative only | more countries and internal records; recalibration |
| Verifier misses a fabricated number that appears elsewhere in the evidence | 1 of 125 injected errors can pass | number-unit binding + entailment judge |
| Databricks path not executed | continuous-learning loop designed, not measured | run notebooks 21-29 when the workspace allows jobs |

## 9. Recommendations and next steps

1. **Pilot the chatbot with procurement specialists and biomedical engineers** in two or three health programmes
   that are about to buy lab equipment. Measure plan-preparation time and evidence usefulness.
2. **Add the equipment's life cycle:** service and maintenance contracts, reagent purchases after equipment purchases,
   and asset registers. These answer the domain's real question, whether the equipment was still working a year later.
3. **Build a labelled taxonomy set and an evaluation set** of about 200 question-answer pairs with the domain experts.
4. **Run the Databricks pipeline** to switch on LLM lesson extraction, vector search and the monitoring job.
5. **Extend the scope configuration** to the next domain (e.g. medicines, or civil works). The pipeline already runs on
   an India roads scope and a three-country scope with no code changes.

## 10. Reproducing these numbers

```bash
python -m procurement_demo.run_all
python src/build_proposal.py
python src/build_deliverables.py
```

All figures come from the run outputs (`procurement_demo/output/health_lab/eval_results.json` and the Gold tables); none are typed by hand.
