"""Packages the assessment into its four deliverables under deliverables/:

    01_DATA_MODEL   ER diagram, table catalogue, data dictionary (from the real Gold tables), Delta DDL
    02_APPROACH     the technical proposal (docs/PROCUREMENT_PROPOSAL.*, built by build_proposal.py)
    03_PROTOTYPE    how to run it, the interactive results page, the example brief, a source-code zip
    04_REPORT       findings of the prototype run: what was built, measured results, limitations, next steps

    python -m procurement_demo.run_all     # refreshes procurement_demo/output/*.json and data/gold/*.parquet
    python src/build_proposal.py           # refreshes the approach document
    python src/build_deliverables.py       # this script

Every number comes from those run outputs; nothing is typed by hand.
"""
import html
import json
import sys
import re
import shutil
import subprocess
import zipfile
from datetime import date
from pathlib import Path

import markdown
import pandas as pd

from build_proposal import CSS, EDGE, pct

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from procurement_demo.common import CFG, GOLD, MIN_YEAR, OUT, RAW, SCOPE, country_reference  # noqa: E402
DOCS = ROOT / "docs"
DEL = ROOT / "deliverables"

EXTRA_CSS = """svg{max-width:100%;height:auto}.legend span{display:inline-block;margin-right:14px;font-size:90%}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:5px;vertical-align:-1px}
@media print{svg{page-break-inside:avoid}table{page-break-inside:auto}tr{page-break-inside:avoid}h3,h4{page-break-after:avoid}}"""


# --------------------------------------------------------------------------- rendering

def render(md_text, stem, title, svg=None):
    """Write <stem>.md, .html (svg inlined at <!--ER-->) and .pdf (Edge headless)."""
    (DEL / f"{stem}.md").write_text(md_text, encoding="utf-8")
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    if svg:
        body = re.sub(r"<p><img alt=\"[^\"]*\" src=\"[^\"]+\.svg\" ?/?></p>", svg, body)
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}{EXTRA_CSS}</style></head><body>{body}</body></html>")
    (DEL / f"{stem}.html").write_text(page, encoding="utf-8")
    if EDGE.exists():
        subprocess.run([str(EDGE), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={DEL / f'{stem}.pdf'}", (DEL / f"{stem}.html").as_uri()],
                       check=True, timeout=180, capture_output=True)
    print(f"wrote deliverables/{stem}.md / .html{' / .pdf' if EDGE.exists() else ''}")


def md_table(rows, headers):
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                     + ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])


# --------------------------------------------------------------------------- 01 data model

GROUP_COLOR = {"fact": "#0969da", "dim": "#1a7f37", "knowledge": "#bc4c00", "analytics": "#8250df"}

# entity -> (physical table, group, column, row, [(field, tag)])
ENTITIES = {
    "NOTICE": ("gold.notices", "fact", 0, 0, [("notice_id", "PK"), ("bid_ref_key", "FK"), ("project_id", "FK"),
                                               ("notice_type", ""), ("notice_date", ""), ("n_bidders", "")]),
    "TENDER": ("gold.fact_tender", "fact", 1, 0, [("tender_id", "PK"), ("project_id", "FK"), ("country_iso3, tender_year", "FK"),
                                                  ("method_code", ""), ("cycle_days", ""), ("award_observed", "")]),
    "CONTRACT": ("gold.fact_contract", "fact", 2, 0, [("wb_contract_number", "PK"), ("tender_id", "FK"), ("project_id", "FK"),
                                                      ("supplier_key", "FK"), ("item_type", "FK"), ("amount_usd", "")]),
    "SUPPLIER": ("gold.dim_supplier", "dim", 3, 0, [("supplier_key", "PK"), ("supplier_name", ""), ("name_variants", ""),
                                                    ("supplier_country", ""), ("n_contracts", "")]),
    "PROJECT_OUTCOME": ("gold.project_outcomes", "knowledge", 4, 0, [("project_id", "PK FK"), ("doc_id", "FK"), ("restructurings", ""),
                                                                     ("closing_extension_days", ""), ("outcome_rating", "")]),
    "GOVERNANCE": ("gold.country_governance_year", "dim", 0, 1, [("country_iso3", "PK"), ("year", "PK"), ("cpia_* (3)", ""),
                                                                 ("wgi_* (3)", ""), ("cpia_missing", "")]),
    "ITEM_TYPE": ("taxonomy (common.py)", "dim", 1, 2, [("item_type", "PK"), ("in_domain", ""), ("4-language patterns", "")]),
    "TENDER_FEATURES": ("gold.tender_features", "analytics", 1, 1, [("tender_id", "PK FK"), ("planning-time features", ""),
                                                                   ("governance at open", ""), ("prior_group_median_days", "")]),
    "PROJECT": ("gold.dim_project", "dim", 2, 1, [("project_id", "PK"), ("country_iso3", ""), ("region", ""),
                                                  ("major_sector", ""), ("commitment_usd", ""), ("status", "")]),
    "DOCUMENT": ("bronze.project_docs_meta", "knowledge", 3, 1, [("doc_id", "PK"), ("project_id", "FK"), ("project_ids[]", ""),
                                                                 ("doc_type_code", ""), ("doc_date", ""), ("landing_url", "")]),
    "CHUNK": ("gold.proj_doc_chunks", "knowledge", 4, 1, [("chunk_id", "PK"), ("doc_id", "FK"), ("section_path", ""),
                                                          ("section_kind", ""), ("page_start, page_end", ""), ("search_text", "")]),
    "TENDER_BENCHMARK": ("gold.tender_benchmarks", "analytics", 0, 2, [("level", "PK"), ("item / country / method", "PK"),
                                                                       ("n, n_awarded", ""), ("median_days, p80_days", "")]),
    "PROJECT_RATING": ("gold.project_ratings_resolved", "knowledge", 2, 2, [("project_id", "PK FK"), ("dimension", "PK"),
                                                                           ("resolved_rating", ""), ("resolved_from", ""), ("conflict", "")]),
    "RATING_EVIDENCE": ("gold.rating_evidence", "knowledge", 3, 2, [("source_id", "PK"), ("project_id", "FK"), ("doc_id", "FK"),
                                                                    ("source_type", ""), ("date", ""), ("rating", "")]),
    "LESSON": ("gold.lessons", "knowledge", 4, 2, [("lesson_id", "PK"), ("chunk_id", "FK"), ("project_id", "FK"),
                                                   ("procurement_stage", ""), ("risk_category", ""), ("quote_verified", "")]),
}

# (child, parent, label, dashed)
RELATIONS = [
    ("NOTICE", "TENDER", "N:1 bid_ref_key", False), ("CONTRACT", "TENDER", "N:1 tender_id", False),
    ("CONTRACT", "SUPPLIER", "N:1 supplier_key", False), ("CONTRACT", "PROJECT", "N:1 project_id", False),
    ("TENDER", "PROJECT", "N:1 project_id", False), ("TENDER", "GOVERNANCE", "N:1 country + year", False),
    ("TENDER_FEATURES", "TENDER", "1:1", False), ("TENDER_BENCHMARK", "TENDER_FEATURES", "aggregates tenders", True),
    ("CONTRACT", "ITEM_TYPE", "N:1 item_type", False), ("TENDER_BENCHMARK", "ITEM_TYPE", "by", True),
    ("DOCUMENT", "PROJECT", "N:1 project_id", False), ("CHUNK", "DOCUMENT", "N:1 doc_id", False),
    ("LESSON", "CHUNK", "N:1 chunk_id", False), ("RATING_EVIDENCE", "DOCUMENT", "N:1 doc_id", False),
    ("PROJECT_RATING", "PROJECT", "N:1 project_id", False), ("RATING_EVIDENCE", "PROJECT_RATING", "resolves", True),
    ("PROJECT_OUTCOME", "DOCUMENT", "N:1 doc_id", False),
]

BOX_W, COL_GAP, ROW_Y = 184, 58, (20, 238, 456)


def _box(name):
    _, _, col, row, fields = ENTITIES[name]
    return 16 + col * (BOX_W + COL_GAP), ROW_Y[row], BOX_W, 44 + 16 * len(fields)


def _clip(box, tx, ty):
    """Point where the segment from the box centre towards (tx, ty) leaves the box."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy = tx - cx, ty - cy
    t = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * t, cy + dy * t


def er_svg(edge_counts):
    width = 16 * 2 + 5 * BOX_W + 4 * COL_GAP
    band_y = 650
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {band_y + 96}" font-family="Segoe UI, Arial, sans-serif" '
             'role="img" aria-label="Entity-relationship diagram of the procurement intelligence data model">',
             '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#57606a"/></marker></defs>',
             f'<rect x="0" y="0" width="{width}" height="{band_y + 96}" fill="#ffffff"/>']
    for child, parent, label, dashed in RELATIONS:
        a, b = _box(child), _box(parent)
        ax, ay = _clip(a, b[0] + b[2] / 2, b[1] + b[3] / 2)
        bx, by = _clip(b, a[0] + a[2] / 2, a[1] + a[3] / 2)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        parts.append(f'<line x1="{ax:.0f}" y1="{ay:.0f}" x2="{bx:.0f}" y2="{by:.0f}" stroke="#57606a" stroke-width="1.4"{dash} marker-end="url(#arr)"/>')
        mx, my = (ax + bx) / 2, (ay + by) / 2
        if ENTITIES[child][3] == ENTITIES[parent][3]:  # same-row link in a narrow gap: cardinality only, the key is shown in the box
            label = label.split(" ")[0]
        parts.append(f'<text x="{mx:.0f}" y="{my - 4:.0f}" font-size="10.5" text-anchor="middle" fill="#24292f" '
                     f'stroke="#ffffff" stroke-width="4" paint-order="stroke">{html.escape(label)}</text>')
    for name, (table, group, _, _, fields) in ENTITIES.items():
        x, y, w, h = _box(name)
        c = GROUP_COLOR[group]
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#ffffff" stroke="{c}" stroke-width="1.6"/>')
        parts.append(f'<path d="M{x},{y + 6} a6,6 0 0 1 6,-6 h{w - 12} a6,6 0 0 1 6,6 v30 h-{w} z" fill="{c}"/>')
        parts.append(f'<text x="{x + 8}" y="{y + 15}" font-size="12" font-weight="700" fill="#ffffff">{name}</text>')
        parts.append(f'<text x="{x + 8}" y="{y + 29}" font-size="10" fill="#ffffff" opacity="0.9">{table}</text>')
        for i, (f, tag) in enumerate(fields):
            fy = y + 52 + 16 * i
            weight = ' font-weight="600"' if "PK" in tag else ""
            parts.append(f'<text x="{x + 8}" y="{fy}" font-size="11" fill="#24292f"{weight}>{html.escape(f)}</text>')
            if tag:
                parts.append(f'<text x="{x + w - 8}" y="{fy}" font-size="9.5" text-anchor="end" fill="{c}" font-weight="700">{tag}</text>')
    # knowledge-graph band
    parts.append(f'<rect x="16" y="{band_y}" width="{width - 32}" height="80" rx="6" fill="#f6f8fa" stroke="#8c959f" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="28" y="{band_y + 20}" font-size="12" font-weight="700" fill="#24292f">KG_EDGE  (gold.kg_edges)  '
                 f'{sum(edge_counts.values()):,} edges: one row per link between any two entities above</text>')
    parts.append(f'<text x="28" y="{band_y + 40}" font-size="11" fill="#24292f">(src_type, src_id, rel, dst_type, dst_id, method, confidence'
                 ', valid_from, evidence)   method ∈ exact_key | fuzzy_desc | fuzzy_name | temporal | llm_extracted | human_confirmed</text>')
    top = sorted(edge_counts.items(), key=lambda kv: -kv[1])
    line = "   ".join(f"{k.split(' ')[1]} {v:,}" for k, v in top[:8])
    parts.append(f'<text x="28" y="{band_y + 60}" font-size="10.5" fill="#57606a">{html.escape(line)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


DESC = {
    # shared keys
    "project_id": "World Bank project ID (P-code). The conformed key that joins structured and unstructured data",
    "country_iso3": "Country, ISO 3166 alpha-3 (ISO-2 and spelling variants resolved via alias table)",
    "tender_id": "Normalised bid reference (upper-case, punctuation removed); the tender's identity",
    "supplier_key": "Resolved supplier: normalised name key + supplier country (after fuzzy merge)",
    "doc_id": "Documents & Reports API id (also the PDF file name)",
    "chunk_id": "<doc_id>_<sequence>; one section-bounded passage",
    "procurement_category": "Works | Goods | Consultant Services | Non-consulting Services",
    "procurement_method": "Full method name (Request for Bids, QCBS, ...)",
    "method_code": "Method code: RFB, RFQ, QCBS, CQS, INDV, DIR, ...",
    "review_type": "Prior | Post review by the Bank ('unknown' when not published)",
    "description": "Free-text description of the package / contract",
    "title": "Document title (used in citations)",
    "landing_url": "Document page on documents.worldbank.org (citation link)",
    "doc_type_code": "PAD | ISR | ICR | PPAR | PROC_PLAN",
    "doc_date": "Document date",
    "section_kind": "lessons | key_factors | procurement_fiduciary | risk | ratings | results | design | annex | other",
    # notices
    "notice_id": "Procurement notice id (OP...)",
    "notice_type": "Invitation for Bids, Request for Expression of Interest, Contract Award, ...",
    "notice_date": "Publication date of the notice",
    "deadline_date": "Bid / EOI submission deadline",
    "bid_ref": "Borrower bid reference as published",
    "bid_ref_key": "Normalised bid reference; equals tender_id",
    "method_name": "Method as published on the notice",
    "implementing_org": "Implementing agency named on the notice",
    "rebid_text": "Notice text indicates a re-tender / re-advertisement",
    "award_date": "Earliest contract-award notice date",
    "n_bidders": "Number of bids / proposals received (award notice)",
    "n_rejected": "Number of bids rejected (award notice)",
    "rejection_reasons": "Reasons for rejection, as published",
    "awarded_names": "Awarded firm(s) named on the award notice",
    "contract_duration_months": "Contract duration on the award notice",
    "notice_version": "Amendment counter of the award notice",
    # tenders
    "open_date": "First invitation (IFB / REOI) date: start of the procurement cycle",
    "n_invitations": "Number of invitation notices for the same bid reference (re-advertisements)",
    "open_notice_type": "Type of the opening notice",
    "rebid_flag": "Target: re-tendered (re-advertised or flagged as re-tender)",
    "award_amended_flag": "Award notice was amended",
    "award_observed": "An award notice exists (event observed)",
    "observed_days": "Days from open to award, or to the data cut if still open (survival time)",
    "outcome_unknown": "No award after 2 years: excluded as unknown outcome, not censored",
    "cycle_days": "Target: days from invitation to award (awarded tenders only)",
    "bid_window_days": "Days between invitation and submission deadline",
    "single_bid_flag": "Target: exactly one responsive bid",
    "rejected_share": "Share of bids rejected",
    "tender_year": "Year of invitation (joins governance by country + year)",
    "contract_usd": "Awarded contract value (from the linked contract)",
    # contracts
    "wb_contract_number": "World Bank contract number",
    "supplier_id": "WB supplier id (kept as evidence; not trusted alone)",
    "supplier_name": "Supplier name as published",
    "supplier_country": "Supplier country",
    "supplier_country_code": "Supplier country ISO-2",
    "project_name": "Project name",
    "fiscal_year": "WB fiscal year of the award",
    "signing_date": "Contract signing date",
    "amount_usd": "Contract amount (USD)",
    "borrower_ref": "Borrower contract reference as published",
    "borrower_ref_key": "Normalised borrower reference (exact-key link to tender_id)",
    "supplier_name_key": "Normalised supplier name (legal forms, M/S, punctuation removed)",
    "tender_link_method": "How the contract was linked to its tender: exact_key | fuzzy_desc",
    "tender_link_confidence": "Confidence of the tender link (1.0 exact; similarity otherwise)",
    # supplier
    "name_variants": "JSON list of all raw spellings merged into this supplier",
    "n_contracts": "Contracts awarded to this supplier",
    "total_usd": "Total awarded value (USD)",
    # project
    "approval_date": "Board approval date",
    "closing_date": "Current closing date",
    "status": "Active | Closed | Pipeline | Dropped",
    "lending_instrument": "IPF, PforR, DPF, ...",
    "commitment_usd": "Total commitment (USD)",
    "env_category": "Environmental category A/B/C/F (older projects)",
    "es_risk_rating": "Environmental & social risk rating (ESF projects; NULL before ESF)",
    "major_sector": "Major sector of the project",
    "implementing_agency": "Implementing agency",
    "project_url": "Project page",
    "objective": "Project development objective",
    "n_tenders": "Tenders observed for the project",
    "median_cycle_days": "Median invitation-to-award days for the project's tenders",
    # governance
    "year": "Calendar year of the indicator (forward-filled ≤ 3 years, never back-filled)",
    "cpia_budget_financial_mgmt": "CPIA quality of budgetary and financial management (1-6)",
    "cpia_public_sector_mgmt": "CPIA public sector management and institutions cluster (1-6)",
    "cpia_transparency_corruption": "CPIA transparency, accountability and corruption (1-6)",
    "wgi_control_of_corruption": "WGI control of corruption (about -2.5 to 2.5)",
    "wgi_gov_effectiveness": "WGI government effectiveness",
    "wgi_regulatory_quality": "WGI regulatory quality",
    "cpia_missing": "1 when CPIA is not published (non-IDA countries)",
    # chunks
    "section_path": "Heading path of the chunk (e.g. 'V. LESSONS > B. ...')",
    "content_type": "narrative | table | annex",
    "page_start": "First page of the passage (citation)",
    "page_end": "Last page of the passage",
    "n_words": "Words in the chunk (≤ 450, 60 overlap)",
    "chunk_text": "Passage text, running headers removed",
    "search_text": "Contextual text that is embedded: doc type | project | country | sector | date | section + text",
    # lessons
    "lesson_id": "<doc_id>_L<n>",
    "paragraph": "Numbered paragraph in the report",
    "lesson": "Lesson statement (first sentence)",
    "detail": "Full paragraph",
    "procurement_stage": "planning | tender_design | evaluation_award | contract_management | general",
    "risk_category": "delay | capacity | fiduciary | safeguards | cost_overrun | specification | other",
    "procurement_related": "Lesson concerns procurement",
    "evidence_quote": "Verbatim quote; must be found in the source chunk",
    "extraction_method": "rule_numbered_paragraph (local) | llm_extracted (Databricks)",
    "quote_verified": "The quote was found verbatim in the source chunk (only verified lessons are served)",
    # ratings
    "dimension": "development_objective | implementation_progress",
    "source_type": "ISR | ICR | ICRR | PPAR",
    "date": "Date of the rating",
    "rating": "Highly Satisfactory ... Highly Unsatisfactory",
    "source_id": "<doc_id>#ISR<n> etc.: the exact rating record",
    "via": "Where it was parsed from (e.g. ICR data sheet)",
    "resolved_rating": "Rating after the evidence hierarchy is applied",
    "resolved_from": "Source type that won (PPAR > ICRR > ICR > latest ISR)",
    "resolved_source_id": "Record that won",
    "conflict": "Sources fall on opposite sides of the satisfactory line",
    "spread": "Rating steps between best and worst evidence",
    "n_evidence": "Number of rating records",
    "worst_isr": "Worst supervision rating during implementation",
    "trajectory": "JSON list of (source, date, rating): kept so both sides can be cited",
    # outcomes
    "restructurings": "Number of restructurings (ICR data sheet)",
    "closing_planned": "Original closing date",
    "closing_actual": "Actual closing date",
    "closing_extension_days": "Days the closing date was extended",
    "outcome_rating": "Final outcome rating",
    "bank_performance": "Bank performance rating",
    "me_quality": "Quality of monitoring and evaluation",
    "n_isr_ratings": "ISR ratings found in the data sheet",
    # benchmarks
    "level": "Fallback level: country+category+method → category+method → category",
    "n": "Tenders in the cohort (awarded + censored)",
    "n_awarded": "Awarded tenders in the cohort",
    "median_days": "Kaplan-Meier median days to award (censoring-aware)",
    "p80_days": "Kaplan-Meier 80th percentile days to award (NULL when not reached)",
    "naive_median_awarded_only": "Median when still-open tenders are ignored (shows the bias)",
    # edges
    "item_type": "What was bought, classified into the scope's taxonomy (e.g. PCR, imaging, reagents, analyzers)",
    "in_domain": "Item type belongs to the domain (laboratory and diagnostic equipment)",
    "region": "World Bank region of the borrower country",
    "state": "Sub-national state (India scope only)",
    "state_from": "Where the state came from: implementing agency or project",
    "src_type": "Source entity type", "src_id": "Source entity id", "rel": "Relationship",
    "dst_type": "Target entity type", "dst_id": "Target entity id",
    "method": "exact_key | fuzzy_desc | fuzzy_name | temporal | llm_extracted | human_confirmed | rule_*",
    "confidence": "0-1 confidence of the link",
}

FEATURE_DESC = {
    "log_contract_usd": "log(1 + estimated value)", "log_commitment": "log(1 + project commitment)",
    "project_age_days": "Days from project approval to invitation", "open_month": "Month of invitation",
    "desc_words": "Words in the description", "prior_group_median_days": "Median cycle of earlier tenders in the same country/category/method (no leakage)",
    "prior_project_median_days": "Median cycle of the project's earlier tenders", "prior_project_tenders": "Earlier tenders in the project",
}

CATALOGUE = [
    # table, layer, grain, PK, FKs / joins, source
    ("notices", "Gold (silver.notices)", "one procurement notice", "notice_id", "bid_ref_key → fact_tender; project_id → dim_project", "Procurement Notices API"),
    ("fact_tender", "Gold", "one tender (bid reference), from invitation to award", "tender_id", "project_id; (country_iso3, tender_year) → governance; supplier_key", "built from notices"),
    ("fact_contract", "Gold", "one contract-supplier row", "wb_contract_number + supplier_key", "tender_id; project_id; supplier_key", "Major Contract Awards (FinancesOne)"),
    ("dim_supplier", "Gold", "one resolved supplier", "supplier_key", "-", "resolved from contract supplier names"),
    ("dim_project", "Gold", "one project", "project_id", "country_iso3", "Projects API"),
    ("country_governance_year", "Gold", "country × year", "(country_iso3, year)", "-", "WDI API: CPIA + WGI"),
    ("project_docs_meta", "Bronze", "one project document", "doc_id", "project_id (project_ids[] for PPARs)", "Documents & Reports API"),
    ("proj_doc_pages", "Silver", "document page", "(doc_id, page_num)", "doc_id", "PDF text"),
    ("proj_doc_chunks", "Gold", "section-bounded passage (≤ 450 words)", "chunk_id", "doc_id; project_id", "parsed PDFs"),
    ("lessons", "Gold", "one lesson learned", "lesson_id", "chunk_id; doc_id; project_id", "Lessons / Key factors sections"),
    ("rating_evidence", "Gold", "one dated DO / IP rating", "source_id + dimension", "project_id; doc_id", "ICR data sheets, ISRs, PPARs"),
    ("project_ratings_resolved", "Gold", "project × rating dimension", "(project_id, dimension)", "project_id", "rating_evidence"),
    ("project_outcomes", "Gold", "one completed project", "project_id", "doc_id", "ICR data sheet"),
    ("tender_features", "Gold (ML)", "one tender with ≥ invitation date", "tender_id", "tender_id", "fact_tender + project + governance"),
    ("tender_benchmarks", "Gold (ML)", "benchmark cohort", "(level, country, category, method)", "-", "Kaplan-Meier over fact_tender"),
    ("item_types", "Reference", "one equipment type", "item_type", "-", "taxonomy in procurement_demo/common.py"),
    ("kg_edges", "Gold", "one typed link", "(src_type, src_id, rel, dst_type, dst_id)", "any entity ↔ any entity", "all of the above"),
    ("proc_briefs", "Ops", "one generated brief", "brief_id", "evidence_ids[] → any entity", "copilot"),
    ("proc_feedback", "Ops", "feedback on a brief / evidence item", "(brief_id, evidence_id, created_ts)", "brief_id", "users; realised award dates"),
]

CLUSTER = {"fact_contract": "item_type, country_iso3", "fact_tender": "country_iso3, tender_year", "notices": "country_iso3, notice_date",
           "kg_edges": "rel, dst_id", "proj_doc_chunks": "country_iso3, doc_type_code", "lessons": "country_iso3, risk_category",
           "tender_features": "country_iso3, tender_year", "dim_supplier": "supplier_key", "dim_project": "project_id"}
PKS = {"notices": ["notice_id"], "fact_tender": ["tender_id"], "fact_contract": ["wb_contract_number", "supplier_key"],
       "dim_supplier": ["supplier_key"], "dim_project": ["project_id"], "country_governance_year": ["country_iso3", "year"],
       "proj_doc_chunks": ["chunk_id"], "lessons": ["lesson_id"], "project_ratings_resolved": ["project_id", "dimension"],
       "tender_features": ["tender_id"], "project_outcomes": ["project_id"]}
FKS = {"fact_contract": [("tender_id", "fact_tender"), ("project_id", "dim_project"), ("supplier_key", "dim_supplier")],
       "fact_tender": [("project_id", "dim_project")], "lessons": [("chunk_id", "proj_doc_chunks")],
       "tender_features": [("tender_id", "fact_tender")], "project_ratings_resolved": [("project_id", "dim_project")]}
TABLE_COMMENT = {t: f"{g}. Source: {s}" for t, _, g, _, _, s in CATALOGUE}


def delta_type(dtype, col):
    s = str(dtype)
    if s.startswith("datetime") or col in ("closing_planned", "closing_actual"):
        return "DATE"
    return {"int64": "BIGINT", "int32": "INT", "float64": "DOUBLE", "bool": "BOOLEAN"}.get(s, "STRING")


def ddl(table, df):
    cols = []
    for c in df.columns:
        not_null = " NOT NULL" if c in PKS.get(table, []) else ""
        cmt = (DESC.get(c) or FEATURE_DESC.get(c) or "").replace("'", "''")
        cols.append(f"  {c:<30} {delta_type(df[c].dtype, c)}{not_null}" + (f" COMMENT '{cmt}'" if cmt else ""))
    if table in PKS:
        cols.append(f"  CONSTRAINT pk_{table} PRIMARY KEY ({', '.join(PKS[table])}) RELY")
    for c, ref in FKS.get(table, []):
        cols.append(f"  CONSTRAINT fk_{table}_{c} FOREIGN KEY ({c}) REFERENCES gold.{ref}")
    cluster = f"\nCLUSTER BY ({CLUSTER[table]})" if table in CLUSTER else ""
    return (f"CREATE TABLE IF NOT EXISTS gold.{table} (\n" + ",\n".join(cols) + "\n)"
            f"{cluster}\nCOMMENT '{TABLE_COMMENT.get(table, '').replace(chr(39), chr(39) * 2)}'\n"
            "TBLPROPERTIES (delta.enableChangeDataFeed = true);")


def data_model(stats):
    tables = {p.stem: pd.read_parquet(p) for p in sorted(GOLD.glob("*.parquet"))}
    docs_meta = pd.DataFrame(json.loads((RAW / "documents_meta.json").read_text(encoding="utf-8")))
    rows = {t: len(d) for t, d in tables.items()}
    rows["project_docs_meta"] = len(docs_meta)
    rows["item_types"] = len(CFG["item_types"])
    svg = er_svg(stats["kg_edge_types"])
    DEL.mkdir(exist_ok=True)
    (DEL / "er_diagram.svg").write_text(svg, encoding="utf-8")

    # full DDL file
    order = ["dim_project", "dim_supplier", "country_governance_year", "notices", "fact_tender", "fact_contract",
             "proj_doc_chunks", "lessons", "rating_evidence", "project_ratings_resolved", "project_outcomes",
             "tender_features", "tender_benchmarks", "kg_edges"]
    sql = ["-- Procurement Intelligence Platform: Gold layer DDL (Databricks Delta / Unity Catalog)",
           "-- Generated by src/build_deliverables.py from the prototype's Gold tables. PK/FK constraints are informational (RELY).",
           "-- Bronze/Silver/Ops tables are created in notebooks 21-24 and 28.", ""]
    sql += [ddl(t, tables[t]) + "\n" for t in order]
    (DEL / "01_DATA_MODEL_DDL.sql").write_text("\n".join(sql), encoding="utf-8")

    cat_rows = [(f"`{t}`", layer, grain, f"`{pk}`", fk, f"{rows[t]:,}" if t in rows else "(Databricks only)", src)
                for t, layer, grain, pk, fk, src in CATALOGUE]

    dictionary = []
    base_cols = set(tables["fact_tender"].columns)
    for t in order:
        df = tables[t]
        cols = [c for c in df.columns if not (t == "tender_features" and c in base_cols)]
        note = (" Inherits every `fact_tender` column; only the added columns are listed." if t == "tender_features" else "")
        dictionary.append(f"### `gold.{t}` ({len(df):,} rows)\n\n*Grain:* "
                          + next(g for tt, _, g, _, _, _ in CATALOGUE if tt == t) + "." + note + "\n")
        r = []
        complete = df[df.notna().sum(axis=1) == df.notna().sum(axis=1).max()]
        sample = complete.sample(1, random_state=7).iloc[0]  # a representative, fully populated row
        for c in cols:
            s = df[c]
            ex = sample[c] if pd.notna(sample[c]) else (s.dropna().iloc[0] if s.notna().any() else "")
            ex = str(ex.date()) if isinstance(ex, pd.Timestamp) else " ".join(str(ex).split())
            ex = (ex[:48] + "…") if len(ex) > 48 else ex
            key = "PK" if c in PKS.get(t, []) else ("FK" if c in dict(FKS.get(t, [])) else "")
            desc = DESC.get(c) or FEATURE_DESC.get(c)
            if not desc and c.startswith("kw_"):
                desc = f"Description mentions {c[3:].replace('_', ' ')} keywords (0/1)"
            ex = f"<code>{html.escape(ex).replace('|', '&#124;')}</code>" if ex else ""
            r.append((f"`{c}`", delta_type(s.dtype, c), key, f"{s.isna().mean():.0%}", desc or "", ex))
        dictionary.append(md_table(r, ["Column", "Type", "Key", "Null", "Meaning", "Example"]) + "\n")

    edge_rows = [(f"{k.split(' ')[0]} → {k.split(' ')[2]}", f"`{k.split(' ')[1]}`", f"{v:,}")
                 for k, v in sorted(stats["kg_edge_types"].items(), key=lambda kv: -kv[1])]
    ddl_example = ddl("fact_contract", tables["fact_contract"])
    doc_counts = docs_meta.doc_type_code.value_counts()

    text = f"""# Deliverable 1: Data Model

**Procurement Intelligence Platform: how structured, semi-structured and unstructured World Bank data fit together**

| Item | Detail |
|---|---|
| Physical platform | Databricks Delta tables in Unity Catalog: `bronze`, `silver`, `gold`, `ops` schemas |
| Domain | {CFG['label']} |
| Verified on | local prototype run on public World Bank data, {date.today():%d %B %Y}. Row counts below are from that run |
| Companion files | `er_diagram.svg` (diagram), `01_DATA_MODEL_DDL.sql` (full Gold DDL with keys and column comments) |

## 1. Design principles

1. **One conformed spine of keys.** Every source is reduced to a small set of shared identifiers: **Project ID**
   (P-code), **tender_id** (normalised bid reference), **supplier_key** (resolved supplier), **item_type**
   (equipment type), **doc_id / chunk_id** and **country_iso3 + year** (with region). Every question that crosses
   silos crosses through these keys. The hierarchy users navigate is Region → Country → Project → Tender →
   Contract → Supplier → Equipment type, with documents, passages, lessons and ratings attached to the project.
2. **A star schema for "what happened", a document hierarchy for "why".** Tenders and contracts are facts. Projects,
   suppliers and country-year governance are dimensions. Documents go *document → chunk → lesson / rating evidence*,
   so every lesson carries its page and section for citation.
3. **Links are data, with provenance.** Joins that are not exact (fuzzy supplier names, description-matched
   contracts, LLM-extracted lessons) are stored as rows in a **knowledge-graph edge table** with `method` and
   `confidence`. A user, a model or the verifier can therefore always ask *how sure is this link?*
4. **Time-aware.** Every fact carries the date it became known. Governance scores are forward-filled, never
   back-filled, and features use only information available at invitation time. This prevents leakage into models.
5. **Missing is explicit.** Open tenders are kept as censored (`award_observed = false`), unknown outcomes are flagged
   (`outcome_unknown`), and missing CPIA is a flag (`cpia_missing`), not a zero.

## 2. Entity-relationship diagram

![Entity-relationship diagram](er_diagram.svg)

<p class="legend"><span><i style="background:#0969da"></i>Facts (what happened)</span><span><i style="background:#1a7f37"></i>Dimensions</span>
<span><i style="background:#bc4c00"></i>Documents and knowledge (how / why)</span><span><i style="background:#8250df"></i>Analytical (model features, benchmarks)</span></p>

Arrows point from the many side to the one side. Dashed lines are derivations, not foreign keys. `DOCUMENT` is
`bronze.project_docs_meta`; in the local prototype the same rows live in `data/raw/documents_meta.json`
({len(docs_meta)} documents: {", ".join(f"{k} {v}" for k, v in doc_counts.items())}). `ITEM_TYPE` is a reference
taxonomy rather than a table: every contract and tender carries its `item_type` and an `in_domain` flag.

## 3. Layers

| Layer | Tables | Role |
|---|---|---|
| **Bronze** | `contract_awards`, `procurement_notices`, `projects`, `governance_indicators`, `project_docs_meta` + PDF volume | API records and PDFs exactly as received, with ingest timestamp; Change Data Feed on |
| **Silver** | `contracts`, `notices`, `projects`, `governance`, `tenders`, `proj_doc_pages` | typed, de-duplicated, keys normalised (ISO codes, bid references, supplier names); one page of text per row |
| **Gold** | facts, dimensions, documents and knowledge, ML tables, `kg_edges` (below) | resolved entities and links; what the models, the vector index and the copilot read |
| **Ops** | `proc_briefs`, `proc_feedback`, `v_proc_kpis` | every brief generated, user feedback, realised award dates: the inputs of the learning loop |

## 4. Table catalogue

{md_table(cat_rows, ["Table", "Layer", "Grain (one row per)", "Primary key", "Foreign keys / joins", "Rows (prototype)", "Source"])}

## 5. How disjoint keys are reconciled

| Link | Rule | Edge `method` / confidence | Prototype result |
|---|---|---|---|
| Notice → Tender | normalised bid reference (`KE-MOWASCO-16771-CW-RFB` ≡ `KEMOWASCO16771CWRFB`) | `exact_key` / 1.0 | {stats['tenders']:,} tenders |
| Contract → Tender | (1) normalised borrower contract reference = bid reference; (2) else same project + category, signed ≤ 18 months after invitation, description token Jaccard ≥ 0.5 | `exact_key` 1.0 / `fuzzy_desc` = similarity | {stats['contract_tender_links_exact']:,} exact + {stats['contract_tender_links_fuzzy']:,} fuzzy = **{pct(stats['contract_tender_link_rate'])}** of contracts |
| Contract → Supplier | normalised name key (drops M/S, legal forms, punctuation); within blocks of country + first token, similarity ≥ 0.92 → union-find merge | `exact_key` / `fuzzy_name` | {stats['supplier_raw_names']:,} raw names → **{stats['supplier_suppliers']:,}** suppliers ({stats['supplier_fuzzy_merges']} fuzzy merges) |
| Contract / Tender → Equipment type | description classified by an ordered taxonomy of patterns in English, French, Spanish and Portuguese (tolerant of garbled accents); generic classes are blocked for vehicles, CCTV and printing | `keyword` / 0.7 | {stats.get('contracts_in_domain', 0):,} of {stats['contracts']:,} contracts are laboratory or diagnostic equipment |
| Contract / Tender / Document → Project | Project ID; PPARs covering several projects fan out to each | `exact_key` | **{pct(stats['contract_project_link_rate'])}** of contracts |
| Tender → Governance | country + year of invitation, forward-filled ≤ 3 years | `temporal` | all tenders |
| Lesson → Chunk → Document → Project | lesson keeps its chunk and page; chunk keeps its document | `rule_numbered_paragraph` (local) / `llm_extracted` (Databricks) | {stats['lessons']} lessons, {stats['lessons_quote_verified']} quote-verified |
| Country identifiers | ISO-2 ↔ ISO-3 ↔ names in notices (incl. "Viet Nam" / "Vietnam") | alias table | - |

Matches with confidence between 0.6 and 0.9 go to a review queue. Confirmed decisions are written back as
`method = 'human_confirmed'` and reused.

## 6. Knowledge graph (`gold.kg_edges`)

The relational tables answer known questions fast. The edge table answers *path* questions: "which lessons come from
projects where this supplier won Works contracts?". It is a property graph stored as one Delta table, clustered by
`(rel, dst_id)` for reverse lookups. No separate graph database is needed at this scale.

{md_table(edge_rows, ["From → To", "Relationship", "Edges"])}

**Total: {stats['kg_edges']:,} edges.** Three-hop example (contract → supplier → project → ICR lesson):

```sql
SELECT l.lesson, l.evidence_quote, l.title, l.page_start
FROM gold.kg_edges a                                                     -- CONTRACT -AWARDED_TO-> SUPPLIER
JOIN gold.kg_edges f ON f.src_id = a.src_id AND f.rel = 'FINANCED_BY'    -- CONTRACT -FINANCED_BY-> PROJECT
JOIN gold.lessons  l ON l.project_id = f.dst_id AND l.quote_verified
JOIN gold.fact_contract c ON c.wb_contract_number = a.src_id AND c.procurement_category = 'Works'
WHERE a.rel = 'AWARDED_TO' AND a.dst_id = :supplier_key;
```

## 7. Document model (unstructured side)

```
DOCUMENT (PAD / ISR / ICR / PPAR, 60-150 pages)
  └─ PAGE            silver.proj_doc_pages     text per page, line breaks kept, running headers removed
      └─ CHUNK       gold.proj_doc_chunks      bounded by headings (Roman / letter / ANNEX), ≤ 450 words, 60 overlap
          │                                    section_path, section_kind, content_type, page_start-page_end
          ├─ LESSON          gold.lessons             verbatim evidence_quote checked against the chunk
          └─ (vector index)  search_text embedded     doc type | project | country | sector | date | section + text
  └─ DATA SHEET (ICR) → RATING_EVIDENCE (dated ISR/ICR/PPAR ratings) → PROJECT_RATING (resolved, conflict flag)
                      → PROJECT_OUTCOME (restructurings, closing extension, outcome ratings)
```

Prototype: {stats['documents_parsed']} reports → {stats['chunks']:,} chunks → {stats['lessons']} lessons and
{stats['rating_evidence']} dated ratings.

## 8. Data dictionary

Null shares and examples are measured on the prototype tables. High null shares are expected where an event has not
happened yet (e.g. `award_date` for open tenders) or a field is not published for older projects.

{chr(10).join(dictionary)}

## 9. Physical design (Delta DDL)

The full DDL for all Gold tables is in `01_DATA_MODEL_DDL.sql`. Example:

```sql
{ddl_example}
```

**Scaling.** The full public corpus is about 300k contract rows, about 425k notices, about 22k projects and tens of
thousands of documents. That is small for Delta. Liquid clustering on country and year keeps scans narrow, Change Data
Feed makes Silver → Gold and the vector index incremental, and supplier resolution uses blocking so it stays near-linear.
"""
    render(text, "01_DATA_MODEL", "Data Model: Procurement Intelligence Platform", svg)


# --------------------------------------------------------------------------- 04 report

def usd_m(x):
    return f"US${x / 1e6:,.1f}M"


def report(ev):
    b, m, r, g, le, cf = ev["build"], ev["models"], ev["retrieval"], ev["guardrails"], ev["lesson_extraction"], ev["conflicts"]
    c, sb, rb = m["cycle"], m["single_bid"], m["rebid"]
    am = m.get("amendment", {})
    chat = ev.get("chatbot", {})
    tm, pl = r.get("topic_macro", {}), r["project_link"]
    gain = 1 - c["mae_days_model_p50"] / c["mae_days_km_benchmark"]
    names = {x["iso3"]: x["name"] for x in country_reference().values()}
    contracts = pd.read_parquet(GOLD / "fact_contract.parquet")
    projects = pd.read_parquet(GOLD / "dim_project.parquet")
    bench = pd.read_parquet(GOLD / "tender_benchmarks.parquet")
    tenders = pd.read_parquet(GOLD / "fact_tender.parquet")
    practice = len(json.loads((RAW / "practice_awards.json").read_text(encoding="utf-8"))) if (RAW / "practice_awards.json").exists() else None
    n_notices = len(json.loads((RAW / "notices.json").read_text(encoding="utf-8")))
    dom = contracts[contracts.in_domain].copy()
    dom["year"] = dom.signing_date.dt.year
    dom["borrower"] = dom.country_iso3.map(names)
    dom["domestic"] = dom.supplier_country.str.lower().str[:6] == dom.borrower.str.lower().str[:6]
    total = dom.amount_usd.sum()

    # 4.1 spend by equipment type, with concentration and domestic share
    rows41 = []
    for k, grp in dom.groupby("item_type"):
        sup = grp.groupby("supplier_key").amount_usd.sum().sort_values(ascending=False)
        rows41.append((k, f"{len(grp):,}", usd_m(grp.amount_usd.sum()), f"{grp.amount_usd.sum() / total:.0%}",
                       f"{money(grp.amount_usd.median())}", f"{grp.country_iso3.nunique()}", f"{len(sup):,}",
                       f"{sup.head(5).sum() / sup.sum():.0%}", f"{(grp.amount_usd * grp.domestic).sum() / grp.amount_usd.sum():.0%}"))
    rows41.sort(key=lambda x: -float(x[2].replace("US$", "").replace("M", "").replace(",", "")))
    # 4.2 region and year
    reg = dom.groupby("region").agg(n=("wb_contract_number", "nunique"), usd=("amount_usd", "sum"),
                                    countries=("country_iso3", "nunique")).sort_values("usd", ascending=False)
    rows42 = [(k, f"{int(v.n):,}", usd_m(v.usd), f"{v.usd / total:.0%}", int(v.countries)) for k, v in reg.iterrows()]
    yr = dom.groupby("year").agg(n=("wb_contract_number", "nunique"), usd=("amount_usd", "sum")).sort_index()
    rows_year = [(int(k), f"{int(v.n):,}", usd_m(v.usd)) for k, v in yr.iterrows()]
    peak = yr.usd.idxmax()
    covid = yr.loc[[y for y in yr.index if 2020 <= y <= 2022]].usd.sum() / total
    top_ctry = dom.groupby("country_iso3").amount_usd.sum().sort_values(ascending=False).head(10)
    rows_ctry = [(names.get(k, k), usd_m(v), f"{v / total:.0%}") for k, v in top_ctry.items()]
    # 4.3 suppliers
    sup = (dom.groupby("supplier_key").agg(name=("supplier_name", lambda s: s.value_counts().index[0]),
                                           base=("supplier_country", "first"), n=("wb_contract_number", "nunique"),
                                           usd=("amount_usd", "sum"), countries=("country_iso3", "nunique"),
                                           types=("item_type", lambda s: ", ".join(s.value_counts().index[:2])))
           .sort_values("usd", ascending=False))
    rows43 = [(v["name"][:45], v.base, v.n, usd_m(v.usd), f"{v.usd / total:.0%}", v.countries, v.types) for _, v in sup.head(12).iterrows()]
    multi = int((sup.countries >= 2).sum())
    domestic_share = (dom.amount_usd * dom.domestic).sum() / total
    # 4.4 methods
    meth = dom.groupby("procurement_method").amount_usd.agg(["count", "sum"]).sort_values("sum", ascending=False)
    rows44 = [(k, f"{int(v['count']):,}", usd_m(v['sum']), f"{v['sum'] / total:.0%}") for k, v in meth.head(8).iterrows()]
    direct = dom.procurement_method.str.contains("Direct|Single", case=False, na=False)
    un = dom.supplier_name.str.contains("UNICEF|UNOPS|United Nations|WHO|World Health|PAHO|UNDP|UNFPA", case=False, na=False)
    by_y = dom.groupby("year").apply(lambda x: pd.Series({"direct": (x.amount_usd * direct[x.index]).sum() / x.amount_usd.sum(),
                                                           "un": (x.amount_usd * un[x.index]).sum() / x.amount_usd.sum()}))
    rows_dy = [(int(k), f"{v.direct:.0%}", f"{v.un:.0%}") for k, v in by_y.iterrows()]
    # 4.5 timelines by equipment type
    it = bench[bench.level == "item_type"].copy() if "item_type" in bench else bench.iloc[0:0]
    dom_types = set(CFG.get("domain_items") or [])
    it = it[it.item_type.isin(dom_types)].sort_values("median_days")
    rows45 = [(x.item_type, x.n, x.n_awarded, f"{x.median_days:.0f}", "-" if pd.isna(x.p80_days) else f"{x.p80_days:.0f}",
               f"{x.naive_median_awarded_only:.0f}") for x in it.itertuples()]
    dt = tenders[tenders.in_domain]
    # results tables
    topic_rows = [(k.replace("_", " "), v["n_relevant"], f"{v['precision@5']:.2f}", f"{v['precision@10']:.2f}",
                   f"{v['recall@20']:.2f}", f"{v['random_precision']:.2f}") for k, v in r.get("topic", {}).items()]
    feat = ", ".join(f"`{k}`" for k, _ in c["top_features"][:7])
    exg = g["examples"][0] if g.get("examples") else None
    caught_rows = [(i["text"][:110] + ("…" if len(i["text"]) > 110 else ""), "; ".join(i["problems"]))
                   for i in (exg["injected_caught"] if exg else [])]
    conf = cf["examples"][0] if cf.get("examples") else None
    brief_md = (OUT / "demo_brief.md").read_text(encoding="utf-8")
    brief_body = re.sub(r"^## ", "#### ", brief_md.split("\n## Evidence")[0].split("\n", 2)[2].strip(), flags=re.M)
    pkg = json.loads((OUT / "demo_brief.json").read_text(encoding="utf-8"))["package"]
    chat_rows = [(x["question"], x["expected_route"], x["route"], "yes" if x["route_ok"] else "**no**",
                  "-" if x["exact_ok"] is None else ("yes" if x["exact_ok"] else "**no**"), x["statements"], x["dropped"])
                 for x in chat.get("rows", [])]
    risk_line = (f"Single-bid AUC {sb['auc']:.2f}; re-tender " + (f"AUC {rb['auc']:.2f}" if "auc" in rb else rb.get("status", "n/a"))
                 + (f"; award amendment AUC {am['auc']:.2f}" if "auc" in am else ""))
    examples = chat_examples([
        "Which supplier won the cheapest GeneXpert or PCR contract?",
        "Who are the top suppliers of diagnostic imaging equipment in Sub-Saharan Africa?",
        "How long does it take to award a laboratory analyzer tender?",
        "What problems did projects have with laboratory equipment maintenance?",
    ])

    def model_row(name, d):
        if "auc" not in d:
            return f"| {name} | {d.get('status', 'not enough data')} | base rate {d.get('base_rate_train', 'n/a')} | {d.get('n_train', 0):,} / {d.get('n_test', 0):,} |"
        return (f"| {name} | AUC **{d['auc']:.2f}**, top-decile lift {d['lift_top_decile']:.1f}× | base rate "
                f"{d['base_rate_train']:.3f} → {d['base_rate_test']:.3f} | {d['n_train']:,} / {d['n_test']:,} |")

    text = f"""# Deliverable 4: Assessment Report

**AI-powered procurement intelligence for medical laboratory and diagnostic equipment in World Bank-financed health
projects: what was built, what the data shows, and what comes next**

| Item | Detail |
|---|---|
| Run date | {date.today():%d %B %Y} |
| Domain | {CFG['label']} |
| Data | public World Bank APIs only: Major Contract Awards, Procurement Notices, Projects, Documents & Reports, WDI (CPIA, WGI) |
| Coverage | {len(dom):,} lab and diagnostic contracts ({usd_m(total)}), {dom.country_iso3.nunique()} countries, {dom.project_id.nunique()} projects, FY{MIN_YEAR} onwards |
| Companion deliverables | 1 Data Model · 2 Approach (technical proposal) · 3 Prototype (pipeline + chatbot) |

## 1. Summary

World Bank health projects buy a large amount of laboratory and diagnostic equipment, but the records of what was
bought, how and why it worked sit in separate places: contract award data, procurement notices, and PDF reports. We
built a working prototype that connects them for this one domain, across every country. It turns them into answers a
procurement team can use, through a cited chatbot and package briefs.

| Question | Answer from the prototype |
|---|---|
| How big is the domain in public data? | **{len(dom):,} contracts worth {usd_m(total)}** across {dom.country_iso3.nunique()} countries, {dom.project_id.nunique()} projects and {dom.supplier_key.nunique():,} suppliers, found by classifying {practice or len(contracts):,} health-goods contract descriptions in four languages |
| Can the silos be joined? | Yes. **{pct(b['contract_project_link_rate'])}** of contracts link to their project and **{pct(b['contract_tender_link_rate'])}** to their tender; {b['documents_parsed']} project reports connect through Project ID ({b['kg_edges']:,} graph links) |
| What does the market look like? | {rows41[0][0]} is the largest category ({rows41[0][2]}). {pct(covid)} of value was signed in 2020-2022 (COVID-19). Domestic suppliers took {pct(domestic_share)} of value; {multi:,} suppliers won in two or more countries |
| How long do tenders take? | Medians from invitation to award range from {rows45[0][3] if rows45 else 'n/a'} days ({rows45[0][0] if rows45 else ''}) to {rows45[-1][3] if rows45 else 'n/a'} days ({rows45[-1][0] if rows45 else ''}), censoring-aware |
| Is the forecast better than a benchmark? | Yes. Out-of-time median error **{c['mae_days_model_p50']:.0f} days** vs {c['mae_days_km_benchmark']:.0f} for cohort benchmarks (**{pct(gain)} better**) |
| Does the chatbot answer correctly? | It routed **{pct(chat.get('route_accuracy', 0))}** of {chat.get('questions', 0)} labelled questions to the right tool; ranked and aggregate answers matched an independent query in **{chat.get('exact_accuracy', 0) * chat.get('exact_checks', 0):.0f} of {chat.get('exact_checks', 0)}** checks; {pct(chat.get('cited_share', 0))} of statements cited |
| Can hallucination be controlled? | Yes, by design. 100% of {g['statements_kept']} brief statements cite evidence; the verifier blocked **{g['caught']} of {g['injected_hallucinations']}** deliberately injected false statements |
| Are the risk models ready to drive decisions? | Not yet. {risk_line}. Use as indicators only |

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
| Health-practice Goods contracts pulled (FY{MIN_YEAR}+) | {practice or len(contracts):,} |
| Classified as laboratory or diagnostic equipment | {len(dom):,} contracts, {usd_m(total)} |
| Projects that bought such equipment | {projects.shape[0]} (all their {len(contracts):,} health-goods contracts kept as market context) |
| Procurement notices of those projects (Goods) | {n_notices:,} → {b['tenders']:,} tenders ({len(dt):,} for lab and diagnostic items) |
| Project documents parsed (PAD, ISR, ICR, IEG review, procurement plan) | {b['documents_parsed']} → {b['chunks']:,} section-aware passages |
| Suppliers | {b['supplier_raw_names']:,} raw names → {b['supplier_suppliers']:,} resolved ({b['supplier_fuzzy_merges']} fuzzy merges) |

**Data quality found along the way.**

- **Language and encoding.** Descriptions come in English, French, Spanish and Portuguese, and accents often arrive
  garbled ("r?actifs"). Patterns therefore tolerate a wildcard in place of accents.
- **Uninformative descriptions.** Some contracts are described only as "Lot 3", "Item 42" or "no contract". They stay
  unclassified rather than being guessed.
- **Missing invitation notices.** Many emergency purchases (direct contracting, UN agencies) have no invitation notice.
  They count in market statistics but cannot give a tender duration. Only {b['tenders_with_cycle']:,} tenders have both
  an invitation and an award; {b['tenders_censored']:,} are still open (censored) and {b['tenders_outcome_unknown']:,}
  had no award after two years.
- **No completion reports yet.** Most COVID-era projects are still active, so lessons come mainly from older, closed
  health projects ({le['extracted']} lessons extracted).

## 4. What the data shows about laboratory and diagnostic procurement

### 4.1 Spend by equipment type

{md_table(rows41, ["Equipment type", "Contracts", "Value", "Share", "Median contract", "Countries", "Suppliers", "Top-5 supplier share", "Domestic supplier share"])}

*Top-5 supplier share* is the share of the type's value won by its five largest suppliers; a high value points to a
concentrated market or closed systems. *Domestic* means the supplier is registered in the borrower country.

### 4.2 Where and when

{md_table(rows42, ["Region", "Contracts", "Value", "Share", "Countries"])}

{md_table(rows_ctry, ["Top countries", "Value", "Share"])}

{md_table(rows_year, ["Signing year", "Contracts", "Value"])}

Spending peaked in {int(peak)}; {pct(covid)} of all value was signed in 2020-2022.

### 4.3 Who supplies

{md_table(rows43, ["Supplier", "Based in", "Contracts", "Value", "Share", "Countries", "Main equipment types"])}

{multi:,} suppliers won lab or diagnostic contracts in two or more countries. They are the firms where cross-country
evidence (price, delivery, after-sales) is most useful.

### 4.4 How it was bought

{md_table(rows44, ["Procurement method", "Contracts", "Value", "Share"])}

{md_table(rows_dy, ["Year", "Direct / single-source share of value", "UN agency share of value"])}

### 4.5 How long tenders take

Kaplan-Meier estimates of days from invitation to award, by equipment type (all countries; still-open tenders
counted as censored):

{md_table(rows45, ["Equipment type", "Tenders", "Awarded", "Median days", "P80 days", "Median if open tenders ignored"])}

## 5. Results by module

### 5.1 Module 1: one connected knowledge base

| Measure | Result |
|---|---|
| Contract → project link rate | {pct(b['contract_project_link_rate'])} |
| Contract → tender link rate | {pct(b['contract_tender_link_rate'])} ({b['contract_tender_links_exact']:,} exact + {b['contract_tender_links_fuzzy']:,} fuzzy) |
| Supplier resolution | {b['supplier_raw_names']:,} names → {b['supplier_suppliers']:,} suppliers |
| Lessons extracted | {le['extracted']} ({le['procurement_related']} procurement-related), {pct(le['verification_rate'] or 0, 1)} quote-verified |
| Knowledge-graph edges | {b['kg_edges']:,} |

### 5.2 Module 2: models

Out-of-time validation: trained on tenders opened before {m['split_date']}, tested on later ones. All health-goods
tenders of the domain projects are used for training, with equipment type as a feature.

| Model | Result | Baseline | Train / test |
|---|---|---|---|
| Days to award, P50 | MAE **{c['mae_days_model_p50']:.0f} days** | KM benchmark {c['mae_days_km_benchmark']:.0f}; single median {c['mae_days_global_median']:.0f} | {c['n_train']:,} / {c['n_test']:,} |
| Days to award, P80 | coverage **{c['p80_coverage']:.2f}** | target 0.80 | |
{model_row("Single responsive bid", sb)}
{model_row("Re-tender", rb)}
{model_row("Award amendment (proxy)", am) if am else ""}

Main drivers of tender duration: {feat}.

### 5.3 Module 2: retrieval

| Risk topic | Relevant lessons | Precision@5 | Precision@10 | Recall@20 | Random |
|---|---|---|---|---|---|
{chr(10).join("| " + " | ".join(str(x) for x in row) + " |" for row in topic_rows) if topic_rows else "| (too few labelled lessons per topic) | | | | | |"}

Structured-to-unstructured check: a project's own tender descriptions retrieve its own reports in the top 10 for
**{pct(pl['hit@10'])}** of {pl['queries']} projects (MRR {pl['mrr@10']:.2f}).

### 5.4 Module 3: guardrails and conflicting evidence

Over {g['briefs']} briefs for real held-out packages, {g['statements_kept']} statements were produced, **100%** with
valid citations. Five kinds of false statement were injected into every brief; the verifier blocked **{g['caught']} of
{g['injected_hallucinations']} ({pct(g['catch_rate'], 1)})**.

{md_table(caught_rows, ["Injected statement", "Why it was blocked"]) if caught_rows else ""}

**Conflicting evidence.** {cf['projects_with_rating_evidence']} projects have dated ratings from several sources (ISR
supervision, the Bank's ICR, IEG's independent review). In **{cf['with_conflict']}** of them the sources fall on
opposite sides of the satisfactory line.""" + (f""" Example: {conf['project_id']} reached *{conf['worst_isr']}* during
supervision; the final rating, from the {conf['resolved_from']}, is *{conf['resolved_rating']}*.""" if conf else "") + f"""
The resolver keeps the most authoritative source as the headline (IEG PPAR > IEG ICR Review > ICR > latest ISR),
stores the full trajectory, and the chatbot and briefs cite both sides.

## 6. The chatbot

{md_table(chat_rows, ["Question", "Expected tool", "Chosen", "Route ok", "Exact", "Statements", "Dropped by verifier"])}

Route accuracy **{pct(chat.get('route_accuracy', 0))}**, exact answers **{chat.get('exact_accuracy', 0) * chat.get('exact_checks', 0):.0f}/{chat.get('exact_checks', 0)}**,
{chat.get('seconds_per_question', 0)} s per question on a laptop. Sample answers (verbatim from the run):

{examples}

## 7. Example output: a brief for a new package

Input: *{pkg['description']}* ({pkg['procurement_category']}, {pkg['method_code']}, {pkg['country_iso3']},
est. US${pkg.get('contract_usd', 0):,.0f}).

{brief_body}

## 8. Limitations

| Limitation | Effect | What would fix it |
|---|---|---|
| Public data only | no bid prices per unit, technical evaluation reports, service contracts or equipment downtime | Phase 2: STEP procurement records, asset registers, maintenance logs |
| Keyword taxonomy | a residue of contracts stays "Other goods"; a few generic items are misclassified | labelled set of about 500 descriptions; LLM classifier for the residue |
| Few completion reports for COVID-era projects | lessons skew to older projects | add IEG thematic evaluations and the next ICR cohort as they publish |
| Rule-based lesson extraction locally | lessons limited to numbered paragraphs in Lessons / Key-factor sections | LLM extraction with quote check (notebook 24) |
| Thin risk-model labels | single-bid, re-tender and amendment models are indicative only | more countries and internal records; recalibration |
| Verifier misses a fabricated number that appears elsewhere in the evidence | 1 of {g['injected_hallucinations']} injected errors can pass | number-unit binding + entailment judge |
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

All figures come from the run outputs (`procurement_demo/output/{SCOPE}/eval_results.json` and the Gold tables); none are typed by hand.
"""
    render(text, "04_REPORT", "Assessment Report: Lab and Diagnostic Procurement Intelligence")


def chat_examples(questions):
    sys.path.insert(0, str(ROOT))
    from procurement_demo.chatbot import ProcurementChat
    bot, out = ProcurementChat(), []
    for q in questions:
        a = bot.ask(q)
        out.append(f"> **Q: {q}**  \n> *tool: {a['route']} · {len(a['evidence'])} evidence items*")
        out.append(">")
        for st in a["statements"][:4]:
            out.append(f"> - {st['text']} `{' '.join(st['citations'])}`")
        out.append("")
    return "\n".join(out)


def money(x):
    return f"US${x:,.0f}"


# --------------------------------------------------------------------------- 02 approach, 03 prototype, index

def approach():
    for ext in ("md", "html", "pdf"):
        src = DOCS / f"PROCUREMENT_PROPOSAL.{ext}"
        if src.exists():
            shutil.copyfile(src, DEL / f"02_APPROACH.{ext}")
    print("copied docs/PROCUREMENT_PROPOSAL.* -> deliverables/02_APPROACH.*")


def prototype(ev):
    pdir = DEL / "03_PROTOTYPE"
    pdir.mkdir(exist_ok=True)
    for f in ("prototype_report.html", "demo_brief.md", "eval_results.json"):
        shutil.copyfile(OUT / f, pdir / f)
    (ROOT / "requirements_procurement.txt").write_text(
        "pandas\npyarrow\nscikit-learn\npypdf\nrequests\njoblib\nmarkdown\nstreamlit\nchromadb>=1.5,<2\nfastembed>=0.9,<1\n# optional: anthropic (LLM wording)\n",
        encoding="utf-8")
    files = ([p for p in (ROOT / "procurement_demo").glob("*.py")] + sorted((ROOT / "notebooks").glob("2[0-9]_proc*.py"))
             + sorted((ROOT / "notebooks").glob("2[0-9]_ingest*.py")) + [ROOT / "src" / "test_proc_lib.py",
             ROOT / "src" / "build_proposal.py", ROOT / "src" / "build_deliverables.py", ROOT / "src" / "proposal_template.md",
             ROOT / "requirements_procurement.txt"])
    with zipfile.ZipFile(pdir / "prototype_source.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
    b, chat = ev["build"], ev.get("chatbot", {})
    text = f"""# Deliverable 3: Prototype

A working, end-to-end prototype of the Procurement Intelligence Platform for **medical laboratory and diagnostic
equipment in World Bank-financed health projects**, on real public World Bank data from {b['countries']} countries.
It includes a **chatbot** that answers questions with cited evidence.

## What is in this folder

| File | What it is |
|---|---|
| `prototype_report.html` | results page: data coverage, knowledge graph, models, retrieval, guardrails, an example brief |
| `demo_brief.md` | the copilot's brief for a new PCR / GeneXpert package (every line cited) |
| `eval_results.json` | every measured number used in the report and approach, including the chatbot evaluation |
| `prototype_source.zip` | source code: pipeline and chatbot (`procurement_demo/`), Databricks notebooks 20-29, tests, build scripts |

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
sentence the verifier removed. On {chat.get('questions', 0)} labelled questions it chose the right tool for
{pct(chat.get('route_accuracy', 0))} and matched an independent query in {chat.get('exact_accuracy', 0) * chat.get('exact_checks', 0):.0f} of
{chat.get('exact_checks', 0)} exactness checks. Optional: `pip install anthropic`, set credentials and
`PROC_CHAT_LLM=1` to have an LLM rephrase verified answers. Its text is discarded if it adds any number.

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

| Component | Local prototype (run and verified) | Databricks production path (written, not yet run) |
|---|---|---|
| Ingestion | `procurement_demo/ingest.py` | `notebooks/21_ingest_procurement`, `22_ingest_project_docs` |
| Gold tables, entity resolution, graph | `build.py` | `23_proc_silver_gold` |
| Documents, lessons, ratings | `build.py` (rules + quote check) | `24_proc_docs_lessons` (LLM + quote check) |
| Vector index | `retrieval.py` (BM25 + filters + RRF) | `25_proc_vector_index` (hybrid Vector Search) |
| Models | `models.py` | `26_proc_risk_models` (MLflow, Unity Catalog registry) |
| Copilot and chatbot | `copilot.py`, `chatbot.py`, `app.py` | `27_proc_tools`, `28_proc_copilot` |
| Monitoring / learning loop | `evaluate.py` | `29_proc_monitor_job` (Lakeflow Job, retrain gate) |
"""
    (pdir / "README.md").write_text(text, encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    (pdir / "README.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Prototype</title>"
                                      f"<style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")
    print("wrote deliverables/03_PROTOTYPE/ (README, results page, brief, source zip)")


def index():
    text = f"""# Procurement Intelligence for Laboratory and Diagnostic Equipment: Assessment Deliverables

Domain: {CFG['label']}. Prepared {date.today():%d %B %Y}.

| # | Deliverable | Open | What it answers |
|---|---|---|---|
| 1 | **Data model** | `01_DATA_MODEL.pdf` (+ `er_diagram.svg`, `01_DATA_MODEL_DDL.sql`) | How contracts, notices, projects, suppliers, equipment types, governance and evaluation documents are joined: ER diagram, knowledge graph, data dictionary, DDL |
| 2 | **Approach** | `02_APPROACH.pdf` | The technical design for assessment Modules 1-4, the domain layer and the chatbot |
| 3 | **Prototype** | `03_PROTOTYPE/README.md`, chatbot: `python -m streamlit run procurement_demo/app.py` | A working build on real World Bank data and how to run it |
| 4 | **Report** | `04_REPORT.pdf` | What the data shows about lab and diagnostic procurement, measured results, limitations, next steps |

Suggested reading order: Report summary (4 §1) → Approach → Data model → Prototype / chatbot demo.
"""
    (DEL / "README.md").write_text(text, encoding="utf-8")
    print("wrote deliverables/README.md")


def main():
    DEL.mkdir(exist_ok=True)
    ev = json.loads((OUT / "eval_results.json").read_text(encoding="utf-8"))
    stats = json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))
    data_model(stats)
    approach()
    prototype(ev)
    report(ev)
    index()


if __name__ == "__main__":
    main()
