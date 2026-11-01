"""Procurement Intelligence - Streamlit UI over procurement_demo (chatbot, planning, estimates, comparisons).

    python -m streamlit run procurement_demo/app.py
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from procurement_demo.chatbot import EXAMPLES, SCHEMA_DOT, ProcurementChat  # noqa: E402
from procurement_demo.common import CFG, GOLD  # noqa: E402
from procurement_demo.insights import (ARCHITECTURE_DOT, Insights, build_package, evidence_rows,  # noqa: E402
                                       linkify, planning_request)

st.set_page_config(page_title="Lab Equipment Procurement Assistant", page_icon="🔬", layout="wide")
LINK = st.column_config.LinkColumn
USD = st.column_config.NumberColumn


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
    bot = ProcurementChat()
    ins = Insights.load(country_name=bot.country_name)
    return bot, ins, json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))


bot, ins, stats = load()


def fmt_usd(x):
    if x is None or pd.isna(x):
        return "-"
    return f"US${x / 1e6:,.1f}M" if x >= 1e6 else f"US${x:,.0f}"


def fmt_days(x):
    return "-" if x is None or pd.isna(x) else f"{int(round(x))} days"


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
               "[R] rating, [D] derived figure). Click a citation to open its source. A verifier removes any sentence "
               "whose numbers or quotes are not in the cited evidence.")


# ── shared renderers ────────────────────────────────────────────────────────────────────────────
def show_sources(evidence, key):
    """The evidence behind an answer as a table: type, project, page, contract number and a link to open it."""
    if not evidence:
        return
    rows = evidence_rows(evidence, ins.project_name)
    st.markdown(f"**Sources ({len(rows)})**")
    st.dataframe(rows[["id", "type", "project", "page", "contract", "excerpt", "link"]], hide_index=True,
                 use_container_width=True, key=key,
                 column_config={"link": LINK("open", display_text="open ↗"), "excerpt": st.column_config.TextColumn(width="large")})


def show_answer(ans, key):
    st.markdown(linkify(ans["answer_md"], ans["evidence"]) or "_No verified answer._")
    if ans.get("llm_answer"):
        with st.expander("LLM wording (checked against the evidence)"):
            st.markdown(ans["llm_answer"])
    st.caption(f"Route: {ans['route']} · scope: {ans['scope']}")
    if ans.get("dot"):
        st.graphviz_chart(ans["dot"], use_container_width=True)
    if ans.get("table") is not None and len(ans["table"]):
        st.dataframe(ans["table"], use_container_width=True, hide_index=True)
    show_sources(ans["evidence"], key=f"src_{key}")
    if ans["guardrail_log"]:
        with st.expander(f"Removed by the verifier ({len(ans['guardrail_log'])})"):
            for g in ans["guardrail_log"]:
                st.markdown(f"- {g['text'][:200]}  \n  _{'; '.join(g['problems'])}_")


def plan_summary(country, item_type, method=None, compact=False, key="plan"):
    """What to expect when buying this equipment type in this country (historical evidence only)."""
    o = ins.plan_overview(country, item_type, method)
    d, val, comp = o["duration"], o["value"], o["competition"]
    k1, k2, k3 = st.columns(3)
    k1.metric("Typical time to award", fmt_days(d["median"]),
              help="Censoring-aware median from invitation to award (tenders still open are counted, not dropped).")
    k1.caption(f"80% awarded within {fmt_days(d['p80'])} · {d['scope']} · n={d['n']}".replace("$", "\\$"))
    k2.metric("Typical contract value", fmt_usd(val.get("median")))
    if val["n"]:
        k2.caption(f"middle half {fmt_usd(val['p25'])} to {fmt_usd(val['p75'])} · {val['scope']} · n={val['n']}".replace("$", "\\$"))
    k3.metric("Bidders per tender (median)", f"{comp['median_bidders']:g}" if comp["n"] else "-")
    if comp["n"]:
        k3.caption(f"{comp['single_bid_share']:.0%} drew a single bid · {comp['scope']} · n={comp['n']}")
    left, right = st.columns(2)
    left.markdown("**Procurement methods used**")
    m = o["methods"].reset_index()
    left.dataframe(m.assign(share=(m.share * 100).round(0)), hide_index=True, use_container_width=True, key=f"{key}_m",
                   column_config={"share": st.column_config.ProgressColumn("share (%)", min_value=0, max_value=100, format="%d%%")})
    right.markdown("**Largest suppliers**")
    right.dataframe(o["suppliers"], hide_index=True, use_container_width=True, key=f"{key}_s",
                    column_config={"value (US$)": USD(format="$%,.0f")})
    if not compact:
        st.markdown("**Recent comparable contracts**")
        st.dataframe(o["examples"], hide_index=True, use_container_width=True, key=f"{key}_e",
                     column_config={"project page": LINK("project page", display_text="open ↗"),
                                    "value (US$)": USD(format="$%,.0f")})
    return o


def answer_question(q, key):
    """Planning questions ('I'm planning to buy X in Y, what should I expect?') get the planning view; everything
    else goes to the chatbot."""
    req = planning_request(q, bot.parse(q))
    if req:
        st.markdown(f"**Planning {req['item_type'].lower()} in {ins.name(req['country'])}: what to expect**")
        plan_summary(req["country"], req["item_type"], compact=True, key=f"ps_{key}")
        st.caption("Open **Plan a purchase** for the cited risks and lessons, a model estimate for your package and "
                   "past contracts; **Estimate time** for a duration with its uncertainty.")
        return None
    ans = bot.ask(q)
    show_answer(ans, key)
    return ans


# ── landing: what this is, what's inside, three things to try ───────────────────────────────────
st.markdown("## Lab Equipment Procurement Assistant")
st.markdown("Plan and review purchases of **laboratory and diagnostic equipment in World Bank health projects**: how long "
            "tenders take, what similar contracts cost, who supplies them and what went wrong before. "
            "**Every answer shows its source.**")
st.info(f"**What's in the data:** {stats.get('contracts_in_domain', 0):,} lab and diagnostic contracts worth "
        f"US${stats.get('domain_usd', 0) / 1e9:.2f}B in {stats.get('countries_in_domain', '-')} countries and "
        f"{stats['projects']} projects, from {ins.c.supplier_key.nunique():,} suppliers (FY2015 onwards), linked to "
        f"tender notices and {stats['documents_parsed']} project reports. Public World Bank data only.", icon="📦")

QUICK = ["Planning to buy laboratory analyzers in Kenya: what to expect?",
         "Top suppliers of diagnostic imaging in Sub-Saharan Africa?",
         "How long does a laboratory analyzer tender take?"]
st.markdown("**Start here: try one of these**")
qcols = st.columns(3)
for i, q in enumerate(QUICK):
    if qcols[i].button(q, key=f"quick{i}", use_container_width=True):
        st.session_state.quick = q if st.session_state.get("quick") != q else None
if st.session_state.get("quick"):
    with st.container(border=True):
        st.markdown(f"**{st.session_state.quick}**")
        answer_question(st.session_state.quick, key="quick")
with st.expander("New here? A 30-second tour"):
    st.markdown(
        "1. **Plan a purchase:** pick a country and an equipment type (or describe the purchase in a sentence) to see the "
        "typical time to award, contract value, methods, suppliers, past contracts, and the risks and lessons from "
        "completed projects, each with its source.\n"
        "2. **Estimate time:** enter a tender's details for a model estimate with its uncertainty, next to what "
        "comparable tenders actually took, and how accurate the model was on tenders it never saw.\n"
        "3. **Countries / Suppliers:** India, Kenya and Nigeria side by side, or any suppliers you choose.\n"
        "4. **Chat** and **Documents:** free questions; click any citation to open the contract's project page or "
        "the report.\n"
        "5. **How it's built:** the data pipeline, the knowledge graph, and which parts run where.")

(chat_tab, plan_tab, est_tab, ctry_tab, sup_tab, docs_tab, explore_tab, build_tab, about_tab) = st.tabs(
    ["💬 Chat", "🧭 Plan a purchase", "⏱️ Estimate time", "🌍 Countries", "🏭 Suppliers", "📄 Documents",
     "📊 Explore", "🏗️ How it's built", "ℹ️ About"])

COUNTRIES = ins.countries()
ITEMS = ins.item_types()
METHODS = dict(ins.methods())
DEFAULT_METHOD = "RFB" if "RFB" in METHODS else next(iter(METHODS))


def country_select(label, key, default="KEN", container=st):
    return container.selectbox(label, COUNTRIES, index=COUNTRIES.index(default) if default in COUNTRIES else 0,
                               format_func=lambda k: f"{ins.name(k)} ({k})", key=key)


# ── 1. Chat ─────────────────────────────────────────────────────────────────────────────────────
with chat_tab:
    st.session_state.setdefault("history", [])
    if not st.session_state.history and "pending" not in st.session_state:     # examples until the first question
        st.markdown("**Example questions**")
        cols = st.columns(2)
        for i, ex in enumerate(EXAMPLES[:6]):
            if cols[i % 2].button(ex, key=f"chatq{i}", use_container_width=True):
                st.session_state.pending = ex
                st.rerun()
    for n, turn in enumerate(st.session_state.history):
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            if turn.get("plan"):
                answer_question(turn["question"], key=f"h{n}")
            else:
                show_answer(turn, key=f"h{n}")
    q = st.chat_input("Ask about suppliers, contracts, prices, timelines, lessons or relationships…")
    q = q or st.session_state.pop("pending", None)
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Searching contracts, benchmarks, reports and the graph…"):
                ans = answer_question(q, key=f"h{len(st.session_state.history)}")
        req = planning_request(q, bot.parse(q))
        st.session_state.history.append(ans if ans else {"question": q, "plan": req})

# ── 2. Plan a purchase ──────────────────────────────────────────────────────────────────────────
with plan_tab:
    st.markdown("#### Plan a purchase")
    st.caption("Choose what you are buying and where, and see what past World Bank tenders suggest: time to award, "
               "contract values, methods, suppliers, risks and lessons. Everything below is cited.")

    def read_description():
        req = planning_request(st.session_state.plan_text, bot.parse(st.session_state.plan_text), require_intent=False)
        if req:
            st.session_state.plan_country, st.session_state.plan_item = req["country"], req["item_type"]
            st.session_state.plan_note = f"Read as: {req['item_type']} in {ins.name(req['country'])}."
        else:
            st.session_state.plan_note = ("Could not find both a country and an equipment type in the description; "
                                          "choose them below.")

    st.text_input("Describe the purchase in a sentence (optional)", key="plan_text", on_change=read_description,
                  placeholder="I'm planning to purchase laboratory analyzers for a healthcare project in Kenya.")
    if st.session_state.get("plan_note"):
        st.caption(st.session_state.plan_note)
    c1, c2, c3 = st.columns(3)
    country = country_select("Country", "plan_country", container=c1)
    item = c2.selectbox("Equipment type", ITEMS, key="plan_item",
                        index=ITEMS.index("Laboratory analyzers") if "Laboratory analyzers" in ITEMS else 0)
    method = c3.selectbox("Procurement method", list(METHODS), index=list(METHODS).index(DEFAULT_METHOD),
                          format_func=lambda m: f"{METHODS[m]} ({m})", key="plan_method")
    c4, c5, c6 = st.columns(3)
    typical = ins.value_stats(country, item).get("median") or 100_000
    value = c4.number_input("Estimated contract value (US$)", min_value=1_000, value=int(round(typical, -3) or 1000),
                            step=10_000, key=f"plan_value_{country}_{item}")
    open_date = c5.date_input("Planned invitation date", value=date.today() + timedelta(days=90), key="plan_date")
    review = c6.radio("World Bank review", ["Post", "Prior"], horizontal=True, key="plan_review",
                      help="Prior review adds the Bank's no-objection steps before award.")

    st.markdown("##### What past tenders show")
    plan_summary(country, item, method)

    pkg = build_package(country, item, method, value, review_type=review, planned_open_date=open_date,
                        projects=ins.projects)
    st.markdown("##### Your package: estimate, risks and lessons")
    with st.spinner("Gathering benchmarks, model scores, similar tenders and lessons…"):
        from procurement_demo.copilot import build_brief
        brief = build_brief(pkg, bot.kb)
    s = brief["scores"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Model estimate to award (P50)", fmt_days(s["cycle_p50"]))
    m2.metric("Slow case (P80)", fmt_days(s["cycle_p80"]))
    m3.metric("Plan award milestone for", (pd.Timestamp(open_date) + timedelta(days=round(s["cycle_p80"]))).strftime("%d %b %Y"),
              help="Planned invitation date plus the P80 estimate: 4 in 5 comparable tenders were awarded by then.")
    sec = None
    for stmt in brief["statements"]:
        if stmt["section"] != sec:
            sec = stmt["section"]
            st.markdown(f"**{sec}**")
        cites = " ".join(f"`{c}`" for c in stmt["citations"])
        st.markdown("- " + linkify(f"{stmt['text']} {cites}", brief["evidence"]))
    with st.expander(f"Evidence behind this brief ({len(brief['evidence'])})"):
        show_sources(brief["evidence"], key="plan_src")
    if brief["guardrail_log"]:
        st.caption(f"{len(brief['guardrail_log'])} statement(s) removed by the verifier.")

# ── 3. Estimate tender time ─────────────────────────────────────────────────────────────────────
with est_tab:
    st.markdown("#### Estimate tender time")
    st.caption("A gradient-boosted quantile model trained on tenders opened before "
               f"{ins.metrics.get('split_date', '2024-01-01')} and tested on later ones. It gives a range, not a date.")
    with st.form("estimate"):
        e1, e2, e3 = st.columns(3)
        e_country = country_select("Country", "est_country", container=e1)
        e_item = e2.selectbox("Equipment type", ITEMS, key="est_item")
        e_method = e3.selectbox("Procurement method", list(METHODS), index=list(METHODS).index(DEFAULT_METHOD),
                                format_func=lambda m: f"{METHODS[m]} ({m})", key="est_method")
        e4, e5, e6 = st.columns(3)
        e_value = e4.number_input("Estimated contract value (US$)", min_value=1_000, value=250_000, step=10_000)
        e_window = e5.slider("Bid window (days from invitation to deadline)", 7, 90, 30)
        e_review = e6.radio("World Bank review", ["Post", "Prior"], horizontal=True)
        e_date = st.date_input("Planned invitation date", value=date.today() + timedelta(days=60), key="est_date")
        e_desc = st.text_input("Short description (optional)",
                               placeholder="Supply and installation of chemistry analyzers for county laboratories, Lot 1")
        st.form_submit_button("Estimate", type="primary")
    from procurement_demo.models import score
    epkg = build_package(e_country, e_item, e_method, e_value, description=e_desc or None, review_type=e_review,
                         planned_open_date=e_date, bid_window_days=e_window, projects=ins.projects)
    est = ins.duration_estimate(epkg, score(epkg))
    r1, r2, r3 = st.columns(3)
    r1.metric("Likely (P50)", fmt_days(est["p50"]), help="Half of tenders like this are expected to be awarded sooner.")
    r2.metric("Slow case (P80)", fmt_days(est["p80"]), help="Four in five are expected to be awarded sooner.")
    bm = est["benchmark"]
    r3.metric("What comparable tenders took (median)", fmt_days(bm["median"]),
              delta=(f"{est['p50'] - bm['median']:+.0f} days vs model" if bm["median"] else None), delta_color="off")
    r3.caption(f"P80 {fmt_days(bm['p80'])} · {bm['scope']} · n={bm['n']}")
    st.markdown(f"Expected award between **{(pd.Timestamp(e_date) + timedelta(days=round(est['p50']))).strftime('%d %b %Y')}** "
                f"(P50) and **{(pd.Timestamp(e_date) + timedelta(days=round(est['p80']))).strftime('%d %b %Y')}** (P80) "
                f"for an invitation on {pd.Timestamp(e_date).strftime('%d %b %Y')}.")
    if est["comparable_days"]:
        h = pd.DataFrame({"days": est["comparable_days"]})
        marks = pd.DataFrame({"days": [est["p50"], est["p80"]], "line": ["model P50", "model P80"]})
        chart = (alt.Chart(h).mark_bar(opacity=0.6).encode(
                    alt.X("days:Q", bin=alt.Bin(maxbins=25), title="days from invitation to award (comparable awarded tenders)"),
                    alt.Y("count()", title="tenders"))
                 + alt.Chart(marks).mark_rule(strokeWidth=2, strokeDash=[5, 3]).encode(
                    x="days:Q", color=alt.Color("line:N", title=None)))
        st.altair_chart(chart, use_container_width=True)
        st.caption(f"Bars: {len(h)} awarded tenders of the same equipment type"
                   f"{' in ' + ins.name(e_country) if len(ins._tenders(e_country, e_item)) >= 10 else ' (all countries)'}.")
    st.markdown("##### How far to trust it")
    st.markdown(
        f"- On {est['n_test']} later tenders it never saw, the P50 was off by **{est['model_mae']:.0f} days** on average, "
        f"against {est['benchmark_mae']:.0f} days for the historical benchmark and {est['median_mae']:.0f} days for a "
        f"single overall median.\n"
        f"- The P80 covered **{est['p80_coverage']:.0%}** of actual times (80% would be perfectly calibrated).\n"
        f"- The bid window, procurement method and review type matter most; the country and governance indicators much less.")
    st.markdown("##### Risk indicators (indicative)")
    if est["single_bid_prob"] is not None:
        rel = est["single_bid_prob"] / est["single_bid_base"]
        word = "higher than usual" if rel >= 1.5 else ("lower than usual" if rel <= 0.67 else "about usual")
        st.markdown(f"- **Single responsive bid:** {est['single_bid_prob']:.0%} versus a {est['single_bid_base']:.0%} base "
                    f"rate, **{word}**. The model ranks tenders better than chance (AUC {est['single_bid_auc']:.2f}) but its "
                    f"probabilities are not well calibrated, so read it as higher or lower than usual, not as a percentage.")
    st.markdown(f"- **Award amendment:** not shown. That model tested at AUC {est['amendment_auc']:.2f}, close to a coin "
                "flip, so it would add noise rather than information.")
    with st.expander("Limitations"):
        st.markdown(
            "- Only tenders with a published invitation and award have a measurable duration (a minority of lab "
            "tenders), so small-country estimates borrow from other countries.\n"
            "- Durations run from invitation to award; planning before the invitation and delivery after award are not included.\n"
            "- Public data only: no bid evaluation reports, so delays cannot be attributed to a specific cause.\n"
            "- Inputs you do not set (project environmental category, commitment size) take the country's typical health project.")

# ── 4. Compare countries ────────────────────────────────────────────────────────────────────────
with ctry_tab:
    st.markdown("#### Compare countries")
    st.caption("India, Kenya and Nigeria are the three countries with indexed project documents and the most lab "
               "tenders in Asia and Africa; add or remove countries to compare others.")
    cc1, cc2 = st.columns([3, 2])
    defaults = [k for k in ("IND", "KEN", "NGA") if k in COUNTRIES]
    picks = cc1.multiselect("Countries", COUNTRIES, default=defaults, format_func=ins.name, key="cmp_countries")
    cmp_item = cc2.selectbox("Equipment type", ["All lab and diagnostic equipment", *ITEMS], key="cmp_item")
    cmp_item = None if cmp_item.startswith("All") else cmp_item
    if picks:
        table = ins.country_compare(picks, cmp_item)
        shown = table.set_index("country").T.astype(object).where(lambda x: x.notna(), "too few to show").astype(str)
        st.dataframe(shown, use_container_width=True, height=36 * (len(shown) + 1) + 4)
        st.caption("Days to award use every goods tender with an invitation notice in the same health projects (the lab "
                   "subset alone is too small), censoring-aware; shown only with at least 5 timed tenders. Single-bid "
                   "share: tenders where only one bid was received.")
        notes = []
        t = table.dropna(subset=["value (US$M)"])
        if len(t) > 1:
            top = t.loc[t["value (US$M)"].idxmax()]
            notes.append(f"**{top['country']}** spent the most (US${top['value (US$M)']:,.1f}M across {top['contracts']} contracts).")
        timed = table.dropna(subset=["median days to award"])
        if len(timed) > 1:
            fast, slow = timed.loc[timed["median days to award"].idxmin()], timed.loc[timed["median days to award"].idxmax()]
            notes.append(f"Goods tenders in these projects were awarded fastest in **{fast['country']}** (median {fast['median days to award']:.0f} "
                         f"days) and slowest in **{slow['country']}** ({slow['median days to award']:.0f} days).")
        sb = table.dropna(subset=["single-bid share"])
        if len(sb) > 1:
            hi = sb.loc[sb["single-bid share"].idxmax()]
            notes.append(f"Competition was thinnest in **{hi['country']}**: {hi['single-bid share']:.0%} of tenders drew a single bid.")
        notes += [f"**{r['country']}** mostly used {r['most common method']}." for _, r in table.dropna(subset=["most common method"]).iterrows()]
        if notes:
            st.markdown(("**What this shows**\n" + "\n".join(f"- {n}" for n in notes)).replace("$", "\\$"))
        g1, g2 = st.columns(2)
        g1.markdown("**Value by equipment type (US$M)**")
        vt = ins.value_by_type(picks).reset_index().melt("item_type", var_name="country", value_name="US$M")
        g1.altair_chart(alt.Chart(vt).mark_bar().encode(
            y=alt.Y("item_type:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)), x=alt.X("US$M:Q"), color="country:N", yOffset="country:N",
            tooltip=["country", "item_type", "US$M"]), use_container_width=True)
        g2.markdown("**Procurement methods (share of lab tenders)**")
        ms = ins.method_share_by_country(picks, cmp_item).reset_index().melt("country_iso3", var_name="method", value_name="share")
        g2.altair_chart(alt.Chart(ms).mark_bar().encode(
            y=alt.Y("country_iso3:N", title=None), x=alt.X("share:Q", axis=alt.Axis(format="%")), color="method:N",
            tooltip=["country_iso3", "method", alt.Tooltip("share:Q", format=".0%")]), use_container_width=True)
        st.markdown("**Largest suppliers per country**")
        scols = st.columns(len(picks))
        for col, iso3 in zip(scols, picks):
            col.markdown(f"_{ins.name(iso3)}_")
            col.dataframe(ins.top_suppliers(iso3, cmp_item, k=5)[["supplier", "value (US$)"]], hide_index=True,
                          use_container_width=True, column_config={"value (US$)": USD(format="$%,.0f")})

# ── 5. Compare suppliers ────────────────────────────────────────────────────────────────────────
with sup_tab:
    st.markdown("#### Compare suppliers")
    st.caption("Track record from public contract awards. Competition indicators come from the tender each contract "
               "was linked to (about three quarters of lab contracts); delivery quality and after-sales performance are "
               "not in public data.")
    s1, s2, s3 = st.columns(3)
    s_item = s1.selectbox("Equipment type", ["All", *ITEMS], key="sup_item")
    s_ctry = s2.selectbox("Buyer country", ["All", *COUNTRIES], key="sup_ctry",
                          format_func=lambda k: k if k == "All" else ins.name(k))
    s_min = s3.slider("At least this many contracts", 1, 20, 2, key="sup_min")
    sup = ins.supplier_table(None if s_item == "All" else s_item, None if s_ctry == "All" else s_ctry)
    if sup.empty:
        st.info("No suppliers match these filters.")
    else:
        sup = sup[sup.contracts >= s_min]
        cfg = {"value_usd": USD("value (US$)", format="$%,.0f"),
               "median_contract_usd": USD("median contract (US$)", format="$%,.0f"),
               "single_bid_share": st.column_config.NumberColumn("single-bid share of linked tenders", format="%.2f"),
               "first_year": st.column_config.NumberColumn(format="%d"), "last_year": st.column_config.NumberColumn(format="%d")}
        st.dataframe(sup.drop(columns="supplier_key").head(200), hide_index=True, use_container_width=True, column_config=cfg)
        names = dict(zip(sup.supplier_key, sup.supplier + " (" + sup.based_in.fillna("?") + ")"))
        chosen = st.multiselect("Pick up to 4 suppliers to compare side by side", list(names), default=list(names)[:3],
                                max_selections=4, format_func=names.get, key="sup_pick")
        if chosen:
            side = sup.set_index("supplier_key").loc[chosen].set_index("supplier")
            for col in ("value_usd", "median_contract_usd"):
                side[col] = side[col].map(lambda x: f"US${x:,.0f}")
            for col in ("median_bidders", "median_days_to_award", "single_bid_share"):
                side[col] = side[col].map(lambda x: "-" if pd.isna(x) else f"{x:g}")
            side = side.T.astype(str)
            st.dataframe(side, use_container_width=True, height=36 * (len(side) + 1) + 4)
            byc = pd.concat([ins.supplier_detail(k)["by_country"]["sum"].rename(names[k]) for k in chosen], axis=1).fillna(0)
            byc = (byc / 1e6).round(2).reset_index(names="country").melt("country", var_name="supplier", value_name="US$M")
            st.markdown("**Where they sold (US$M)**")
            st.altair_chart(alt.Chart(byc[byc["US$M"] > 0]).mark_bar().encode(
                x=alt.X("US$M:Q"), y=alt.Y("country:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)), color="supplier:N", yOffset="supplier:N",
                tooltip=["supplier", "country", "US$M"]), use_container_width=True)
            for k in chosen:
                with st.expander(f"Contracts: {names[k]}"):
                    st.dataframe(ins.supplier_detail(k)["contracts"], hide_index=True, use_container_width=True,
                                 column_config={"project page": LINK("project page", display_text="open ↗"),
                                                "value (US$)": USD(format="$%,.0f")})

# ── 6. Ask the documents ────────────────────────────────────────────────────────────────────────
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
            by_id = {s["id"]: s for s in res["sources"]}
            if res["answer"]:
                def cite(sid):
                    s = by_id.get(sid)
                    return f"[\\[{sid}, p. {s['pages']}\\]]({s['url']})" if s and s.get("url") else f"**[{sid}]**"
                st.markdown("\n".join(f"- {a['text']} {cite(a['source'])}" for a in res["answer"]))
                st.caption("Each sentence is taken from the cited document; click a citation to open the document.")
            st.markdown(f"**Sources ({len(res['sources'])})**")
            st.dataframe(pd.DataFrame([{"id": s["id"], "document": f"{s['doc_type']} · {s['title'][:80]}",
                                        "project": f"{s['project_id']} · {s['project_name']}", "page": s["pages"],
                                        "date": s["doc_date"], "section": s["section"], "link": s["url"]}
                                       for s in res["sources"]]),
                         hide_index=True, use_container_width=True, column_config={"link": LINK("open", display_text="open ↗")})
            for s in res["sources"]:
                with st.expander(f"[{s['id']}] {s['doc_type']} · {s['title'][:90]} · p. {s['pages']}"):
                    st.markdown(f"Project {s['project_id']} {s['project_name']} · {s['doc_date']} · section: {s['section']}  \n"
                                f"[Open the document]({s['url']})")
                    st.write(s["text"])

# ── 7. Explore ──────────────────────────────────────────────────────────────────────────────────
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

# ── 8. How it's built ───────────────────────────────────────────────────────────────────────────
with build_tab:
    st.markdown("#### How it's built")
    st.markdown(
        "One pipeline, two runtimes. The **deployed app you are using** runs the local Python version: it reads processed "
        "tables (Parquet) and a ChromaDB vector index committed to the repository, and does no data processing at "
        "request time. The **Databricks pipeline** (notebooks 00 and 20-34) implements the same steps on a lakehouse and "
        "was run end to end on Databricks Free Edition; it is a separate deployment, not what serves this page. Both "
        "import the same parsing, taxonomy and guardrail code (`notebooks/20_proc_lib.py`), so they classify and verify "
        "identically.")
    st.graphviz_chart(ARCHITECTURE_DOT, use_container_width=True)
    a1, a2 = st.columns(2)
    a1.markdown("**Deployed app (Streamlit Community Cloud)**\n"
                "- `procurement_demo/`: ingest → build (silver/gold Parquet) → models → retrieval → copilot → chatbot\n"
                "- Gold tables: contracts, tenders, suppliers, projects, report passages, lessons, ratings, graph edges\n"
                "- Models: Kaplan-Meier benchmarks; scikit-learn gradient-boosted quantile and risk models (joblib)\n"
                "- Document Q&A: `procurement_rag/`, ChromaDB + `bge-small-en-v1.5` embeddings (CPU), BM25, rank fusion\n"
                "- Guardrails: citation, number and quote checks before anything is shown\n"
                "- Static copy: a browser-only JavaScript chatbot on GitHub Pages")
    a2.markdown("**Databricks pipeline (separate, batch)**\n"
                "- Bronze / silver / gold Delta tables in Unity Catalog (`proc_demo` catalog)\n"
                "- Mosaic AI Vector Search (hybrid) with `databricks-gte-large-en` embeddings\n"
                "- LLM lesson extraction with quote verification (`databricks-meta-llama-3-3-70b-instruct`)\n"
                "- MLflow + Unity Catalog model registry with a champion gate; UC functions as copilot tools\n"
                "- Lakeflow Jobs for drift checks and retraining\n"
                "- A Databricks App for document Q&A over the Vector Search index")
    st.markdown("#### Data model")
    st.caption("Region → Country → Project → Tender → Contract → Supplier → Equipment type, with documents, report "
               "passages, lessons and ratings hanging off the project. Every arrow is a row type in gold.kg_edges.")
    st.graphviz_chart(SCHEMA_DOT, use_container_width=True)
    counts = bot.edges.groupby(["src_type", "rel", "dst_type", "method"]).size().rename("edges").reset_index()
    st.dataframe(counts.sort_values("edges", ascending=False), use_container_width=True, hide_index=True)

# ── 9. About ────────────────────────────────────────────────────────────────────────────────────
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
    st.markdown("### Why you can trust an answer")
    st.markdown(
        "- Every sentence cites its source: a contract, a benchmark or a report page, with a link.\n"
        "- A checker removes any sentence whose numbers or quotes are not in the cited source.\n"
        "- If the documents do not answer a question, the tool says so instead of guessing.\n"
        "- Rankings and totals are calculated from the data, never written by a language model.\n"
        "- Estimates show their range and their measured accuracy; a model that tested no better than chance is not shown.")
    st.markdown("### Limits")
    st.markdown(
        "- Public data only: no internal bid evaluations, unit prices or maintenance records.\n"
        "- The risk models are indicative, not decision-grade.\n"
        "- Few lab tenders have both an invitation and an award date, so durations often borrow from wider groups (the "
        "scope and n are always shown).\n"
        "- Document answers are quotations, so they are only as specific as the reports.")
    st.markdown("### Links")
    st.markdown(
        "- Full report: https://dmlaishu.github.io/procurement-intelligence/report.html\n"
        "- Code and documentation: https://github.com/dmlaishu/procurement-intelligence")
