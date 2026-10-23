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

st.set_page_config(page_title="Procurement Intelligence", page_icon="🔬", layout="wide")


@st.cache_resource(show_spinner="Loading the knowledge base…")
def load():
    return ProcurementChat(), json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))


bot, stats = load()

# ── sidebar ─────────────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Procurement Intelligence")
    st.caption(CFG["label"])
    a, b = st.columns(2)
    a.metric("Domain contracts", f"{stats.get('contracts_in_domain', stats['contracts']):,}")
    v = stats.get("domain_usd", 0)
    b.metric("Value", f"US${v / 1e9:.2f}B" if v >= 1e9 else f"US${v / 1e6:,.0f}M")
    a.metric("Countries", stats.get("countries_in_domain", "-"))
    b.metric("Projects", stats["projects"])
    a.metric("Suppliers", f"{stats['supplier_suppliers']:,}")
    b.metric("Reports indexed", stats["documents_parsed"])
    st.caption(f"{stats['chunks']:,} report passages · {stats['kg_edges']:,} knowledge-graph links · "
               f"{stats['contracts']:,} health-goods contracts in the same projects")
    st.markdown("**Try a question**")
    for q in EXAMPLES:
        if st.button(q, use_container_width=True):
            st.session_state.pending = q
    st.divider()
    st.caption("Every sentence cites its evidence ([C] contract, [B] benchmark, [L] lesson, [P] report passage, "
               "[R] rating, [D] derived figure). A verifier removes any sentence whose numbers or quotes are not in "
               "the cited evidence.")

chat_tab, model_tab, explore_tab = st.tabs(["Chat", "Data model", "Explore"])


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
