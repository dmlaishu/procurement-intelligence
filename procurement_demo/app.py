"""Procurement Intelligence chat - Streamlit UI over procurement_demo.chatbot.

    python -m streamlit run procurement_demo/app.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from procurement_demo.chatbot import EXAMPLES, SCHEMA_DOT, ProcurementChat  # noqa: E402
from procurement_demo.common import CFG, GOLD  # noqa: E402

st.set_page_config(page_title="Lab Equipment Procurement Assistant", page_icon="🔬", layout="wide")


@st.cache_resource(show_spinner="Loading the document index…")
def load_docs():
    from procurement_rag.search import DocSearch
    try:
        return DocSearch()
    except Exception as e:          # index not built yet
        st.warning(f"Document search is not available yet ({str(e)[:120]}). "
                   "Build it with: python -m procurement_rag.pipeline --countries KEN IND")
        return None


@st.cache_resource(show_spinner="Loading the knowledge base…")
def load():
    return ProcurementChat(), json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))


bot, stats = load()

# ── sidebar ─────────────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Lab Equipment Procurement")
    st.caption(CFG["label"])
    a, b = st.columns(2)
    a.metric("Domain contracts", f"{stats.get('contracts_in_domain', stats['contracts']):,}")
    v = stats.get("domain_usd", 0)
    b.metric("Value (US$)", f"{v / 1e9:.2f}B" if v >= 1e9 else f"{v / 1e6:,.0f}M",
             help=f"Total value of the lab and diagnostic equipment contracts: US${v:,.0f}")
    a.metric("Countries", stats.get("countries_in_domain", "-"))
    b.metric("Projects", stats["projects"])
    a.metric("Suppliers", f"{stats['supplier_suppliers']:,}")
    b.metric("Reports indexed", stats["documents_parsed"])
    st.caption(f"{stats['chunks']:,} report passages · {stats['kg_edges']:,} knowledge-graph links · "
               f"{stats['contracts']:,} health-goods contracts in the same projects")
    st.divider()
    st.caption("Every sentence cites its evidence ([C] contract, [B] benchmark, [L] lesson, [P] report passage, "
               "[R] rating, [D] derived figure). A verifier removes any sentence whose numbers or quotes are not in "
               "the cited evidence.")

# ── what this is (always visible) ───────────────────────────────────────────────────────────────
st.markdown("## Lab Equipment Procurement Assistant")
st.markdown("Ask about the laboratory and diagnostic equipment bought in World Bank health projects. "
            "**Every answer shows its source.**")
c1, c2, c3, c4 = st.columns(4)
c1.markdown("**💬 Chat**  \nPrices, suppliers, spending and tender times.")
c2.markdown("**📄 Ask the documents**  \nAnswers from one country's project reports.")
c3.markdown("**🔗 Data model**  \nHow the data is connected.")
c4.markdown("**📊 Explore**  \nCharts and tables of the contracts.")

chat_tab, docs_tab, model_tab, explore_tab, about_tab = st.tabs(
    ["Chat", "Ask the documents", "Data model", "Explore", "About"])


def show_answer(ans):
    st.markdown(ans["answer_md"] or "_No verified answer._")
    if ans.get("llm_answer"):
        with st.expander("LLM wording (checked against the evidence)"):
            st.markdown(ans["llm_answer"])
    st.caption(f"Route: {ans['route']} · scope: {ans['scope']}")
    if ans.get("dot"):
        st.graphviz_chart(ans["dot"], use_container_width=True)
    if ans.get("table") is not None and len(ans["table"]):
        st.dataframe(ans["table"], use_container_width=True, hide_index=True)
    with st.expander(f"Evidence ({len(ans['evidence'])})"):
        for e in ans["evidence"].values():
            loc = f" · p. {e['page']}" if e.get("page") else ""
            link = f" · [open]({e['url']})" if e.get("url") else ""
            st.markdown(f"**[{e['id']}]** {e.get('source', '')}{loc}{link}  \n{e['text'][:600]}")
    if ans["guardrail_log"]:
        with st.expander(f"Removed by the verifier ({len(ans['guardrail_log'])})"):
            for g in ans["guardrail_log"]:
                st.markdown(f"- {g['text'][:200]}  \n  _{'; '.join(g['problems'])}_")


with chat_tab:
    st.session_state.setdefault("history", [])
    if not st.session_state.history and "pending" not in st.session_state:     # examples until the first question
        st.markdown("**Example questions**")
        cols = st.columns(2)
        for i, ex in enumerate(EXAMPLES[:6]):
            if cols[i % 2].button(ex, key=f"chatq{i}", use_container_width=True):
                st.session_state.pending = ex
                st.rerun()
    for turn in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            show_answer(turn)
    q = st.chat_input("Ask about suppliers, contracts, prices, timelines, lessons or relationships…")
    q = q or st.session_state.pop("pending", None)
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Searching contracts, benchmarks, reports and the graph…"):
                ans = bot.ask(q)
            show_answer(ans)
        st.session_state.history.append(ans)

DOC_EXAMPLES = {
    "KEN": ["How many laboratories received COVID-19 diagnostic equipment?",
            "What equipment did the East Africa laboratory networking project provide?",
            "Which procurement methods and thresholds apply to goods?",
            "What problems delayed procurement of medical supplies?"],
    "IND": ["What diagnostic equipment is planned for tuberculosis elimination?",
            "How is the quality of sputum microscopy ensured?",
            "What laboratory services are contracted out in public hospitals?",
            "How will public health laboratories be strengthened for pandemic preparedness?"],
}
DOC_EXAMPLES_DEFAULT = ["What laboratory and diagnostic equipment is being procured?",
                        "What lessons were learned about strengthening public health laboratories?",
                        "Which procurement methods and thresholds apply to goods?",
                        "What problems were found with the maintenance of laboratory equipment?"]

with docs_tab:
    docs = load_docs()
    countries = docs.countries() if docs else {}
    if not countries:
        st.info("No countries indexed yet. Build them with: python -m procurement_rag.pipeline --countries KEN IND")
    else:
        from procurement_rag.config import CATEGORIES
        c1, c2 = st.columns(2)
        iso3 = c1.selectbox("Country", list(countries), format_func=lambda k: countries[k])
        category = c2.selectbox("Category", ["All categories", *CATEGORIES])
        info = docs.stats(iso3)
        st.caption(f"{info['documents']} World Bank documents from {info['projects']} projects in {countries[iso3]}, "
                   f"{info['chunks']:,} indexed passages · " + ", ".join(f"{v} {k}" for k, v in info["by_type"].items()))
        st.markdown("**Example questions**")
        ex_cols = st.columns(2)
        for i, q in enumerate(DOC_EXAMPLES.get(iso3, DOC_EXAMPLES_DEFAULT)):
            if ex_cols[i % 2].button(q, key=f"docq{i}", use_container_width=True):
                st.session_state.doc_question = q
        question = st.text_input("Your question", key="doc_question",
                                 placeholder=f"Ask anything about lab and diagnostic equipment procurement in {countries[iso3]}…")
        if question:
            with st.spinner(f"Searching {countries[iso3]} documents…"):
                res = docs.answer(question, iso3, None if category == "All categories" else category)
            if res["note"]:
                st.info(res["note"])
            if res["answer"]:
                st.markdown("\n".join(f"- {a['text']} **[{a['source']}]**" for a in res["answer"]))
                st.caption("Each sentence is taken from the cited document; open a source to read it in context.")
            st.markdown(f"**Sources ({len(res['sources'])})**")
            for s in res["sources"]:
                with st.expander(f"[{s['id']}] {s['doc_type']} · {s['title'][:90]} · p. {s['pages']}"):
                    st.markdown(f"Project {s['project_id']} {s['project_name']} · {s['doc_date']} · section: {s['section']}  \n"
                                f"[Open the document]({s['url']})")
                    st.write(s["text"])

with model_tab:
    st.markdown("#### How the data is connected")
    st.caption("Region → Country → Project → Tender → Contract → Supplier → Equipment type, with documents, report "
               "passages, lessons and ratings hanging off the project. Every arrow is a row type in gold.kg_edges.")
    st.graphviz_chart(SCHEMA_DOT, use_container_width=True)
    counts = bot.edges.groupby(["src_type", "rel", "dst_type", "method"]).size().rename("edges").reset_index()
    st.dataframe(counts.sort_values("edges", ascending=False), use_container_width=True, hide_index=True)

with explore_tab:
    c = bot.contracts
    types = sorted(c.item_type.unique())
    dom = sorted(CFG["domain_items"] or types)
    pick = st.multiselect("Equipment types", types, default=[t for t in dom if t in types])
    ctry = st.multiselect("Countries", sorted(c.country_iso3.dropna().unique()))
    sel = c[c.item_type.isin(pick)] if pick else c
    if ctry:
        sel = sel[sel.country_iso3.isin(ctry)]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Contracts", f"{len(sel):,}")
    k2.metric("Value", f"US${sel.amount_usd.sum() / 1e6:,.1f}M")
    k3.metric("Suppliers", f"{sel.supplier_key.nunique():,}")
    k4.metric("Countries", sel.country_iso3.nunique())
    left, right = st.columns(2)
    left.markdown("**Value by equipment type (US$M)**")
    left.bar_chart(sel.groupby("item_type").amount_usd.sum().div(1e6).sort_values(ascending=False), horizontal=True)
    right.markdown("**Value by signing year (US$M)**")
    right.bar_chart(sel.groupby(sel.signing_date.dt.year).amount_usd.sum().div(1e6))
    st.markdown("**Top countries**")
    top = (sel.groupby("country_iso3").agg(contracts=("wb_contract_number", "nunique"), value_usd=("amount_usd", "sum"),
                                           suppliers=("supplier_key", "nunique")).sort_values("value_usd", ascending=False))
    st.dataframe(top.head(20), use_container_width=True)
    st.markdown("**Contracts**")
    st.dataframe(sel.sort_values("amount_usd", ascending=False)[
        ["wb_contract_number", "supplier_name", "supplier_country", "amount_usd", "signing_date", "country_iso3",
         "project_id", "item_type", "procurement_method", "description"]].head(500), use_container_width=True, hide_index=True)

with about_tab:
    v = stats.get("domain_usd", 0)
    st.markdown("### What this is")
    st.markdown(
        "An AI-assisted procurement intelligence tool for **medical laboratory and diagnostic equipment bought in World "
        "Bank-financed health projects**. It helps people who plan or review these purchases answer practical questions: "
        "who supplies this kind of equipment and at what price, how long tenders usually take, what went wrong in past "
        "projects, and what a country's project documents say about a topic.")
    st.markdown("### Why it is needed")
    st.markdown(
        "The information exists but is scattered. **Contract awards** say what was bought and from whom; **procurement "
        "notices** say when a tender opened and how many firms bid; **project reports** (appraisal, supervision, "
        "completion and independent evaluation reports, often 100+ pages) explain why things worked or failed. Nothing "
        "links a GeneXpert contract in one country to the lesson about cartridge stock-outs in another. This tool does.")
    st.markdown("### What is inside")
    c1, c2, c3 = st.columns(3)
    c1.metric("Lab & diagnostic contracts", f"{stats.get('contracts_in_domain', 0):,}", help=f"US${v:,.0f} in total")
    c2.metric("Countries / projects", f"{stats.get('countries_in_domain', '-')} / {stats['projects']}")
    c3.metric("Project reports read", stats["documents_parsed"], help=f"{stats['chunks']:,} passages")
    docs = load_docs()
    if docs:
        names = docs.countries()
        st.caption("Ask the documents covers " + ", ".join(
            f"{names[k]} ({docs.stats(k)['documents']} documents)" for k in names) + ".")
    st.markdown("### How it works")
    st.markdown(
        "1. **Collect:** contract awards, procurement notices, project records and project PDFs are downloaded from the "
        "World Bank's public APIs.\n"
        "2. **Connect:** contracts are classified by equipment type (descriptions in English, French, Spanish and "
        "Portuguese), supplier name variants are merged, and contracts, tenders, projects and reports are linked through "
        f"shared identifiers into one knowledge graph ({stats['kg_edges']:,} links).\n"
        "3. **Analyse:** tender durations are benchmarked by equipment type, and simple models estimate time to award "
        "and risks such as a single bid.\n"
        "4. **Answer:** the Chat tab looks up the exact figures (prices, suppliers, totals) in the data; the Ask the "
        "documents tab searches a country's reports by meaning and by keyword and quotes the sentences that answer the "
        "question.")
    st.markdown("### Why you can trust an answer")
    st.markdown(
        "- Every sentence cites its source: a contract, a benchmark or a report page with a link.\n"
        "- A checker removes any sentence whose numbers or quotes are not in the cited source.\n"
        "- If the documents do not answer a question, the tool says so instead of guessing.\n"
        "- Rankings and totals are calculated from the data, never written by a language model.")
    st.markdown("### Limits")
    st.markdown(
        "- Public data only: no internal bid evaluations, unit prices or maintenance records.\n"
        "- The risk models are indicative, not decision-grade.\n"
        "- Document answers are quotations, so they are only as specific as the reports.")
    st.markdown("### Links")
    st.markdown(
        "- Full report: https://dmlaishu.github.io/procurement-intelligence/report.html\n"
        "- Code and documentation: https://github.com/dmlaishu/procurement-intelligence")
