"""LabProcure, full workspace (every tab): lifecycle planner, optimizer, pre-mortem, chat, saved plan, comparisons,
documents. The default app (app.py) is the simplified three-tab version; this one is kept for deeper exploration.

    python -m streamlit run procurement_demo/app_full.py
"""
import io
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# When the backend code changes (a redeploy can keep this Python process running), import the backend afresh and
# rebuild the cached objects. Otherwise objects built by the previous code stay in memory and newly added methods are
# missing (AttributeError).
_ROOT = Path(__file__).resolve().parent.parent
CODE_VERSION = hashlib.md5(b"".join(p.read_bytes() for d in ("procurement_demo", "procurement_rag")
                                    for p in sorted((_ROOT / d).glob("*.py")) if not p.name.startswith("app"))).hexdigest()
if getattr(sys, "_labprocure_code_version", None) != CODE_VERSION:
    for _name in [n for n in sys.modules if n.startswith(("procurement_demo.", "procurement_rag"))]:
        del sys.modules[_name]
    sys._labprocure_code_version = CODE_VERSION

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from procurement_demo import ui  # noqa: E402
from procurement_demo.chatbot import EXAMPLES, SCHEMA_DOT, ProcurementChat  # noqa: E402
from procurement_demo.common import CFG, GOLD, OUT  # noqa: E402
from procurement_demo.insights import (ARCHITECTURE_DOT, Insights, build_package, evidence_rows,  # noqa: E402
                                       linkify, planning_request)
from procurement_demo.lifecycle import STAGES, Lifecycle, figures_table, plan_record  # noqa: E402
from procurement_demo.models import score  # noqa: E402

st.set_page_config(page_title="LabProcure · Lab Equipment Procurement Intelligence", page_icon="🔬", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)
LINK = st.column_config.LinkColumn
USD = st.column_config.NumberColumn


@st.cache_resource(show_spinner="Loading the document index…", max_entries=1)
def load_docs(code_version):
    from procurement_rag.search import DocSearch
    try:
        return DocSearch()
    except Exception as e:          # index not built yet
        st.warning(f"Document search is not available yet ({str(e)[:120]}). "
                   "Build it with: python -m procurement_rag.pipeline --countries KEN IND")
        return None


@st.cache_resource(show_spinner="Loading the knowledge base…", max_entries=1)
def load(code_version):
    bot = ProcurementChat()
    ins = Insights.load(country_name=bot.country_name)
    lc = Lifecycle(ins, bot.kb, GOLD)
    stats = json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))
    ev_path = OUT / "eval_results.json"
    evals = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    rag_path = Path(__file__).resolve().parent.parent / "procurement_rag" / "eval_results.json"
    rag_eval = json.loads(rag_path.read_text(encoding="utf-8")) if rag_path.exists() else {}
    return bot, ins, lc, stats, evals, rag_eval


bot, ins, lc, stats, evals, rag_eval = load(CODE_VERSION)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_optimize(country, item, method, value, open_date, review, window):
    return lc.optimize(country, item, method, value, open_date, review, window)


def fmt_usd(x):
    if x is None or pd.isna(x):
        return "-"
    return f"US${x / 1e6:,.1f}M" if x >= 1e6 else f"US${x:,.0f}"


def fmt_days(x):
    return "-" if x is None or pd.isna(x) else f"{int(round(x))} days"


def md(s):
    return str(s).replace("$", "\\$")


COUNTRIES = ins.countries()
ITEMS = ins.item_types()
METHODS = dict(ins.methods())
DEFAULT_METHOD = "RFB" if "RFB" in METHODS else next(iter(METHODS))
n_suppliers = ins.c.supplier_key.nunique()
st.session_state.setdefault("my_plan", [])

# ── sidebar ─────────────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"### 🔬 {ui.BRAND}")
    st.caption(CFG["label"])
    a, b = st.columns(2)
    a.metric("Lab contracts", f"{stats.get('contracts_in_domain', stats['contracts']):,}")
    v = stats.get("domain_usd", 0)
    b.metric("Value", f"${v / 1e9:.2f}B" if v >= 1e9 else f"${v / 1e6:,.0f}M", help=f"US${v:,.0f}")
    a.metric("Countries", stats.get("countries_in_domain", "-"))
    b.metric("Projects", stats["projects"])
    a.metric("Suppliers", f"{n_suppliers:,}")
    b.metric("Reports read", stats["documents_parsed"])
    st.caption(f"{stats['chunks']:,} report passages · {stats['kg_edges']:,} knowledge-graph links")
    st.divider()
    st.markdown(f"**📋 My plan:** {len(st.session_state.my_plan)} package(s)")
    st.divider()
    st.markdown(ui.legend(), unsafe_allow_html=True)

# ── hero ────────────────────────────────────────────────────────────────────────────────────────
g = evals.get("guardrails", {})
m = ins.metrics.get("cycle", {})
st.markdown(ui.hero([
    ("lab & diagnostic contracts", f"{stats.get('contracts_in_domain', 0):,}"),
    ("contract value", f"${stats.get('domain_usd', 0) / 1e9:.2f}B"),
    ("countries · projects", f"{stats.get('countries_in_domain', '-')} · {stats['projects']}"),
    ("suppliers resolved", f"{n_suppliers:,}"),
    ("project reports read", f"{stats['documents_parsed']}"),
    ("knowledge-graph links", f"{stats['kg_edges'] / 1000:.0f}K"),
    ("hallucinations caught", f"{g.get('catch_rate', 0):.0%}" if g else "-"),
    ("award-time error (tested)", f"±{m.get('mae_days_model_p50', 0):.0f} days" if m else "-"),
], sub="Plan a lab or diagnostic equipment purchase end to end: when it will be awarded and delivered, how to set up the "
       "tender, how it is likely to go wrong, and what past projects learned. Every figure says how it was worked out "
       "and how many records it rests on; where public data says nothing, it says Missing."), unsafe_allow_html=True)

TAB_LABELS = ["🏠 Start", "🧭 Plan my purchase", "💬 Ask", f"📋 My plan ({len(st.session_state.my_plan)})", "🌍 Countries",
              "🏭 Suppliers", "📄 Documents", "📊 Explore", "🏗️ How it's built & tested"]
# deep links from the website: ?view=ask opens the assistant, ?view=plan the planner, and so on
VIEWS = {"start": 0, "plan": 1, "design": 1, "anticipate": 1, "ask": 2, "chat": 2, "myplan": 3, "learn": 6, "countries": 4,
         "suppliers": 5, "documents": 6, "explore": 7, "built": 8}
view = str(st.query_params.get("view", "")).lower()
VIEW_NOTE = {"plan": "**Stage 1 · Plan:** pick the country, equipment type and invitation date below, then read the timeline.",
             "design": "**Stage 2 · Design:** set the method, value and bid window, then scroll to the 🔧 Tender optimizer.",
             "anticipate": "**Stage 3 · Anticipate:** scroll to ⚠️ Pre-mortem for the ways comparable tenders went wrong.",
             "learn": "**Stage 4 · Learn:** ask what completion reports learned; try an example question below.",
             "ask": "**AI Assistant:** type a question, or click an example. Planning questions get a full lifecycle card."}
if view in VIEW_NOTE and not st.session_state.get("view_note_shown"):
    st.info(VIEW_NOTE[view] + "  Need a guide? [How to use LabProcure](https://dmlaishu.github.io/procurement-intelligence/learn/)",
            icon="🎓")
    st.session_state.view_note_shown = True
q_link = str(st.query_params.get("q", ""))[:300]
if q_link and not st.session_state.get("q_used"):        # an example sent from the website's guide: run it once
    st.session_state.q_used = True
    if view in ("ask", "chat"):
        st.session_state.pending = q_link
    elif view in ("plan", "design", "anticipate"):
        st.session_state.plan_text = q_link
        req = planning_request(q_link, bot.parse(q_link), require_intent=False)
        if req:
            st.session_state.plan_country, st.session_state.plan_item = req["country"], req["item_type"]
            st.session_state.plan_note = f"Read as: {req['item_type']} in {ins.name(req['country'])}."
    elif view == "learn":
        st.session_state.doc_question = q_link
# the website's planner hands over its inputs: ?view=plan&country=KEN&item=...&value=250000&method=RFB&date=2027-03-01&window=30
qp = st.query_params
if qp.get("country") and not st.session_state.get("plan_link_used"):
    st.session_state.plan_link_used = True
    c_in, i_in = str(qp.get("country")).upper(), str(qp.get("item", ""))
    if c_in in COUNTRIES and i_in in ITEMS:
        st.session_state.plan_country, st.session_state.plan_item = c_in, i_in
        try:
            st.session_state[f"plan_value_{c_in}_{i_in}"] = max(1_000, int(float(qp.get("value", 0))))
        except ValueError:
            pass
        if qp.get("method") in METHODS:
            st.session_state[f"plan_method_{c_in}_{i_in}"] = qp.get("method")
        try:
            st.session_state.plan_date = date.fromisoformat(str(qp.get("date")))
        except (TypeError, ValueError):
            pass
        try:
            st.session_state.plan_window = min(90, max(7, int(qp.get("window", 30))))
        except ValueError:
            pass
try:
    _tabs = st.tabs(TAB_LABELS, default=TAB_LABELS[VIEWS[view]] if view in VIEWS else None)
except TypeError:                     # Streamlit without st.tabs(default=...)
    _tabs = st.tabs(TAB_LABELS)
(start_tab, plan_tab, chat_tab, my_tab, ctry_tab, sup_tab, docs_tab, explore_tab, build_tab) = _tabs


def country_select(label, key, default="KEN", container=st):
    return container.selectbox(label, COUNTRIES, index=COUNTRIES.index(default) if default in COUNTRIES else 0,
                               format_func=lambda k: f"{ins.name(k)} ({k})", key=key)


# ── shared renderers ────────────────────────────────────────────────────────────────────────────
def show_sources(evidence, key):
    if not evidence:
        return
    rows = evidence_rows(evidence, ins.project_name)
    st.markdown(f"**Sources ({len(rows)})**")
    st.dataframe(rows[["id", "type", "project", "page", "contract", "excerpt", "link"]], hide_index=True,
                 use_container_width=True, key=key,
                 column_config={"link": LINK("open", display_text="open ↗"), "excerpt": st.column_config.TextColumn(width="large")})


def show_answer(ans, key):
    st.markdown(linkify(ans["answer_md"], ans["evidence"]) or "_No verified answer._")
    st.caption(f"Route: {ans['route']} · scope: {ans['scope']}")
    if ans.get("dot"):
        st.graphviz_chart(ans["dot"], use_container_width=True)
    if ans.get("table") is not None and len(ans["table"]):
        st.dataframe(ans["table"], use_container_width=True, hide_index=True, key=f"tbl_{key}")
    show_sources(ans["evidence"], key=f"src_{key}")
    if ans["guardrail_log"]:
        with st.expander(f"Removed by the verifier ({len(ans['guardrail_log'])})"):
            for gl in ans["guardrail_log"]:
                st.markdown(f"- {md(gl['text'][:200])}  \n  _{'; '.join(gl['problems'])}_")


def timeline_chart(tl, height=170):
    colors = alt.Scale(domain=["Derived", "Model", "Assumption"], range=["#10b981", "#4f46e5", "#9ca3af"])
    bars = alt.Chart(tl).mark_bar(cornerRadius=5, height=22).encode(
        x=alt.X("start:T", title=None, axis=alt.Axis(format="%b %Y")), x2="end:T",
        y=alt.Y("step:N", title=None, sort=None, axis=alt.Axis(labelLimit=260)),
        color=alt.Color("kind:N", scale=colors, title="basis"),
        opacity=alt.condition(alt.datum.step == "3 Award risk window", alt.value(0.45), alt.value(0.95)),
        tooltip=["step", alt.Tooltip("start:T", format="%d %b %Y"), alt.Tooltip("end:T", format="%d %b %Y"), "note", "kind"])
    return bars.properties(height=height)


def run_lifecycle(country, item, method, value, open_date, review="Post", window=None):
    pkg = build_package(country, item, method, value, review_type=review, planned_open_date=open_date,
                        bid_window_days=window or 30, projects=ins.projects)
    s = score(pkg)
    return pkg, s, lc.figures(country, item, method, value, s), lc.timeline(country, item, open_date, s)


def key_points(figs, s, tl):
    """Short written summary built only from the figures above (no free text generation)."""
    f = {x.figure: x for x in figs}
    pts = [f"Award is likely about **{fmt_days(s['cycle_p50'])}** after the invitation; plan for **{fmt_days(s['cycle_p80'])}** "
           f"(P80), i.e. by **{tl.iloc[2].end:%d %b %Y}**; if delivery takes the usual contracted period, it would end around "
           f"**{tl.iloc[3].end:%d %b %Y}** (an assumption, not a prediction)."]
    for name, txt in (("Bid window (invitation → deadline)", "Typical bid window: **{v}**."),
                      ("Most used method", "Most comparable tenders used **{v}**."),
                      ("Bids received per tender", "Comparable tenders received **{v}** bids."),
                      ("Tenders with a rejected bid", "**{v}** of comparable tenders rejected at least one bid."),
                      ("Contract duration (delivery period)", "Delivery periods run about **{v}**.")):
        if name in f:
            pts.append(txt.format(v=f[name].value))
    missing = [x.figure.lower() for x in figs if x.basis == "Missing"]
    if missing:
        pts.append("Not in public data: " + "; ".join(missing) + ".")
    return "\n".join(f"- {md(p)}" for p in pts)


def add_to_plan(country, item, method, value, open_date, s, tl):
    rec = plan_record(ins.name(country), item, method, value, open_date, s, tl)
    rec["_timeline"] = tl.assign(package=rec["package"])
    rec["_key"] = (country, item)
    st.session_state.my_plan.append(rec)


def usual_method(country, item, value):
    """The competitive method most used for this purchase whose usual contract sizes include the value."""
    rng = lc.method_ranges()
    names = {v: k for k, v in METHODS.items()}
    for name in ins.method_mix(country, item).index:
        code = names.get(name)
        if code in rng and rng[code][0] <= value <= rng[code][1]:
            return code
    return DEFAULT_METHOD


def lifecycle_card(country, item, key, compact=False):
    """The lifecycle answer used in chat and on the Start tab."""
    od = date.today() + timedelta(days=90)
    value = ins.value_stats(country, item).get("median") or 100_000
    meth = usual_method(country, item, value)
    pkg, s, figs, tl = run_lifecycle(country, item, meth, value, od)
    st.markdown(md(f"**Estimated lifecycle for {item.lower()} in {ins.name(country)}**, from comparable World Bank "
                   f"tenders (assumed: invitation {od:%d %b %Y}, {METHODS[meth]}, the method most used for this "
                   f"purchase, and the typical value {fmt_usd(value)})."))
    st.altair_chart(timeline_chart(tl, 150), use_container_width=True)
    with st.container(border=True, height=330 if compact else None):
        st.markdown(ui.figures_html(figs), unsafe_allow_html=True)
    st.markdown("**Key points**\n" + key_points(figs, s, tl))
    if st.button("➕ Add to my plan", key=f"add_{key}"):
        add_to_plan(country, item, meth, value, od, s, tl)
        st.toast("Added to My plan")
    st.caption("Change the method, value, dates or bid window, try the what-if optimizer and read the pre-mortem in "
               "**🧭 Plan my purchase**.")


# ── 🏠 Start ────────────────────────────────────────────────────────────────────────────────────
with start_tab:
    st.markdown("#### Try it in one click")
    QUICK = [("🧭 Laboratory analyzers in Kenya: what should I expect?", ("KEN", "Laboratory analyzers")),
             ("🧭 Diagnostic imaging in Nigeria: plan it end to end", ("NGA", "Diagnostic imaging")),
             ("💬 Top suppliers of diagnostic imaging in Sub-Saharan Africa?", None)]
    qc = st.columns(3)
    for i, (label, plan) in enumerate(QUICK):
        if qc[i].button(label, key=f"quick{i}", use_container_width=True):
            st.session_state.quick = i if st.session_state.get("quick") != i else None
    qi = st.session_state.get("quick")
    if qi is not None:
        label, plan = QUICK[qi]
        with st.container(border=True):
            if plan:
                lifecycle_card(*plan, key=f"quick{qi}", compact=True)
            else:
                show_answer(bot.ask(label[2:]), key="quick")
    st.markdown("#### One record from planning to learning")
    for col, (title, text) in zip(st.columns(4), [
        ("1 · Plan", "When will it be awarded and delivered? A dated timeline with a P50 and a P80, from a model tested "
                     "on tenders it never saw."),
        ("2 · Design the tender", "Method, bid window, lots: the what-if optimizer scores every combination and shows what "
                                  "each choice did to time and competition in the past."),
        ("3 · Anticipate", "A pre-mortem: the ways comparable tenders went wrong, how often, and what the completion "
                           "reports say about it, with page links."),
        ("4 · Learn", f"{evals.get('lesson_extraction', {}).get('extracted', 150)} lessons extracted from project reports, "
                      "each quote checked against its page; supervision rating paths and restructurings."),
    ]):
        col.markdown(f'<div class="lp-card"><h4>{title}</h4><small>{text}</small></div>', unsafe_allow_html=True)
    st.markdown("#### Why the numbers can be trusted")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Injected hallucinations caught", f"{g.get('caught', 0)}/{g.get('injected_hallucinations', 0)}",
              help="Wrong numbers and fake quotes were planted in 25 briefs; the verifier removed them before display.")
    t2.metric("Award-time error on unseen tenders", f"{m.get('mae_days_model_p50', 0):.0f} days",
              delta=f"{m.get('mae_days_model_p50', 0) - m.get('mae_days_global_median', 0):.0f} vs a simple median",
              delta_color="inverse")
    t3.metric("Lesson quotes verified on the page", f"{evals.get('lesson_extraction', {}).get('verification_rate', 0):.0%}")
    t4.metric("Statements carrying a citation", f"{g.get('kept_with_citation_share', 0):.1%}")
    with st.expander("What this is, why it is needed, and its limits"):
        st.markdown(
            "**What:** decision support for people who plan or review purchases of laboratory and diagnostic equipment "
            "in World Bank-financed health projects.\n\n"
            "**Why:** contract awards say what was bought and from whom; procurement notices say when a tender opened "
            "and how many firms bid; project reports explain why things worked or failed. Nothing links them. "
            "LabProcure joins them in one knowledge graph and answers from it.\n\n"
            "**Limits:** public data only (no bid evaluation reports, unit prices or maintenance records); risk scores "
            "are indicative; few lab tenders have both an invitation and an award date, so durations often borrow from "
            "wider groups, and the scope and n are always shown.")

# ── 🧭 Plan my purchase ─────────────────────────────────────────────────────────────────────────
with plan_tab:
    st.markdown("#### Plan my purchase")

    def read_description():
        req = planning_request(st.session_state.plan_text, bot.parse(st.session_state.plan_text), require_intent=False)
        if req:
            st.session_state.plan_country, st.session_state.plan_item = req["country"], req["item_type"]
            st.session_state.plan_note = f"Read as: {req['item_type']} in {ins.name(req['country'])}."
        else:
            st.session_state.plan_note = "Could not find both a country and an equipment type; choose them below."

    st.text_input("Describe the purchase in a sentence (optional)", key="plan_text", on_change=read_description,
                  placeholder="We need to buy chemistry analyzers for county hospital laboratories in Kenya")
    if st.session_state.get("plan_note"):
        st.caption(st.session_state.plan_note)
    c1, c2, c3 = st.columns(3)
    country = country_select("Country", "plan_country", container=c1)
    item = c2.selectbox("Equipment type", ITEMS, key="plan_item",
                        index=ITEMS.index("Laboratory analyzers") if "Laboratory analyzers" in ITEMS else 0)
    typical = ins.value_stats(country, item).get("median") or 100_000
    usual = usual_method(country, item, typical)
    method = c3.selectbox("Procurement method", list(METHODS), index=list(METHODS).index(usual),
                          format_func=lambda k: f"{METHODS[k]} ({k})", key=f"plan_method_{country}_{item}")
    c4, c5, c6, c7 = st.columns(4)
    value = c4.number_input("Estimated value (US$)", min_value=1_000, value=int(round(typical, -3) or 1000),
                            step=10_000, key=f"plan_value_{country}_{item}")
    open_date = c5.date_input("Planned invitation date", value=date.today() + timedelta(days=90), key="plan_date")
    window = c6.slider("Bid window (days)", 7, 90, 30, key="plan_window")
    review = c7.radio("World Bank review", ["Post", "Prior"], horizontal=True, key="plan_review",
                      help="Prior review adds the Bank's no-objection steps before award.")

    pkg, s, figs, tl = run_lifecycle(country, item, method, value, open_date, review, window)
    st.markdown("##### 📅 Your timeline")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Model estimate · likely award (P50)", f"{tl.iloc[1].end:%d %b %Y}",
              help=f"{fmt_days(s['cycle_p50'])} after the invitation: half of comparable tenders are awarded sooner.")
    k2.metric("Conservative planning date (P80)", f"{tl.iloc[2].end:%d %b %Y}",
              help=f"{fmt_days(s['cycle_p80'])} after the invitation: 4 in 5 comparable tenders were awarded by then. "
                   "Put this date in the plan.")
    k3.metric("Delivery · assumption", f"{tl.iloc[3].end:%d %b %Y}",
              help="Not a prediction: the P80 award date plus the median contracted delivery period from award notices. "
                   "Actual delivery dates are not in public data.")
    rel = s.get("single_bid_prob", 0) / max(s.get("single_bid_base_rate", 1), 1e-9)
    k4.metric("Single-bid risk", "higher than usual" if rel >= 1.5 else ("lower than usual" if rel <= .67 else "about usual"),
              help=f"Model {s.get('single_bid_prob', 0):.0%} vs base rate {s.get('single_bid_base_rate', 0):.0%}; "
                   "AUC 0.67, read as a direction, not a percentage.")
    st.markdown(
        f'<div class="lp-card"><b>How accurate is this?</b> On {m.get("n_test", 0)} later tenders the model never saw, '
        f'the P50 was off by <b>{m.get("mae_days_model_p50", 0):.0f} days</b> on average (a single overall median: '
        f'{m.get("mae_days_global_median", 0):.0f} days), and the P80 covered <b>{m.get("p80_coverage", 0):.0%}</b> of '
        f'actual award times (80% would be perfect). Read the P50 as roughly '
        f'{ui.esc(fmt_days(max(0, s["cycle_p50"] - m.get("mae_days_model_p50", 0))))} to '
        f'{ui.esc(fmt_days(s["cycle_p50"] + m.get("mae_days_model_p50", 0)))}. {ui.badge("Model")} '
        f'{ui.badge("Assumption", "Missing")} = delivery, not validated.</div>', unsafe_allow_html=True)
    st.altair_chart(timeline_chart(tl), use_container_width=True)
    if st.button("➕ Add this package to my plan", type="primary", key="plan_add"):
        add_to_plan(country, item, method, value, open_date, s, tl)
        st.toast("Added to My plan")

    st.markdown("##### 🧭 The lifecycle, stage by stage")
    st.markdown(ui.figures_html(figs), unsafe_allow_html=True)
    st.markdown("**Key points**\n" + key_points(figs, s, tl))

    st.markdown("##### 🔧 Tender optimizer: what if you set it up differently?")
    st.caption("Every combination of method, bid window and number of lots, scored by the same models. Methods are only "
               "suggested within the contract sizes they have been used for. These are patterns learned from past "
               "tenders, not guarantees: use them to ask better questions of the procurement plan.")
    with st.spinner("Scoring every combination…"):
        opt, base = cached_optimize(country, item, method, float(value), str(open_date), review, window)
    best = opt[opt["within past use"]].head(3)
    if base is not None and len(best):
        top = best.iloc[0]
        d80 = top["P80 days"] - base["P80 days"]
        dsb = top["single-bid risk"] - base["single-bid risk"]
        same = ((top.method == base.method and top["bid window (days)"] == base["bid window (days)"] and top.lots == base.lots)
                or (d80 > -7 and dsb > -0.03))           # gains this small are inside the model's error
        if not base["within past use"]:
            rng = lc.method_ranges().get(base.method)
            st.warning(f"{METHODS.get(base.method, base.method)} is rarely used at this contract size"
                       + (md(f" (its usual range is {fmt_usd(rng[0])} to {fmt_usd(rng[1])})") if rng else "")
                       + ": check the threshold in the project's procurement plan.")
        n_badge = ui.badge("Derived · n={:,}".format(int(top["history (n tenders)"])), "Derived")
        st.markdown(
            f'<div class="lp-win"><b>{"Your setup is already about as good as it gets; the best scored is" if same else "Best scored setup:"}</b> '
            f'{ui.esc(METHODS.get(top.method, top.method))}, {int(top["bid window (days)"])}-day bid window, '
            f'{int(top.lots)} lot(s) of {ui.esc(fmt_usd(top["value per lot"]))}: P80 <b>{top["P80 days"]:.0f} days</b> '
            f'({d80:+.0f} vs your setup) and single-bid risk <b>{top["single-bid risk"]:.0%}</b> ({dsb * 100:+.0f} pts). '
            f'{ui.badge("Model")} {n_badge} past {ui.esc(top.method)} tenders.</div>', unsafe_allow_html=True)
        o1, o2 = st.columns([3, 2])
        heat = opt[opt.method == top.method]
        o1.altair_chart(alt.Chart(heat).mark_rect().encode(
            x=alt.X("bid window (days):O"), y=alt.Y("lots:O"),
            color=alt.Color("P80 days:Q", scale=alt.Scale(scheme="redyellowgreen", reverse=True)),
            tooltip=["method", "bid window (days)", "lots", alt.Tooltip("P80 days:Q", format=".0f"),
                     alt.Tooltip("single-bid risk:Q", format=".0%")]).properties(
            height=170, title=f"P80 days to award, {METHODS.get(top.method, top.method)}"), use_container_width=True)
        mine = opt[(opt.method == base.method) & (opt["bid window (days)"] == base["bid window (days)"]) & (opt.lots == base.lots)]
        show = pd.concat([best.assign(setup="best"), mine.assign(setup="yours")]).drop_duplicates(
            ["method", "bid window (days)", "lots"])
        o2.dataframe(show[["setup", "method", "bid window (days)", "lots", "P50 days", "P80 days", "single-bid risk"]]
                     .round({"P50 days": 0, "P80 days": 0}), hide_index=True, use_container_width=True,
                     column_config={"single-bid risk": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})

    st.markdown("##### ⚠️ Pre-mortem: how this purchase is most likely to go wrong")
    risks, lessons = lc.premortem(country, item, s)
    r1, r2 = st.columns(2)
    with r1:
        for i, r in enumerate(risks.to_dict("records"), start=1):
            st.markdown(ui.risk_card(i, r), unsafe_allow_html=True)
    with r2:
        st.markdown("**What completion reports say**")
        for lsn in lessons:
            st.markdown(ui.lesson_card(lsn), unsafe_allow_html=True)
        if not lessons:
            st.markdown(ui.badge("Missing") + " no verified lesson matched this purchase.", unsafe_allow_html=True)

    with st.expander("📈 How far to trust the award estimate"):
        est = ins.duration_estimate(pkg, s)
        if est["comparable_days"]:
            h = pd.DataFrame({"days": est["comparable_days"]})
            marks = pd.DataFrame({"days": [est["p50"], est["p80"]], "line": ["model P50", "model P80"]})
            st.altair_chart(alt.Chart(h).mark_bar(opacity=0.6).encode(
                alt.X("days:Q", bin=alt.Bin(maxbins=25), title="days from invitation to award (comparable awarded tenders)"),
                alt.Y("count()", title="tenders")) + alt.Chart(marks).mark_rule(strokeWidth=2, strokeDash=[5, 3]).encode(
                x="days:Q", color=alt.Color("line:N", title=None)), use_container_width=True)
        st.markdown(f"- On {est['n_test']} later tenders it never saw, the P50 was off by **{est['model_mae']:.0f} days** on "
                    f"average (historical benchmark {est['benchmark_mae']:.0f}, single median {est['median_mae']:.0f}).\n"
                    f"- The P80 covered **{est['p80_coverage']:.0%}** of actual times (80% is perfect calibration).\n"
                    f"- The award-amendment model is not used: it tested at AUC {est['amendment_auc']:.2f}, close to chance.")
    with st.expander("🏪 Market snapshot: suppliers and recent comparable contracts"):
        o = ins.plan_overview(country, item, method)
        x1, x2 = st.columns(2)
        x1.dataframe(o["suppliers"], hide_index=True, use_container_width=True, key="mk_s",
                     column_config={"value (US$)": USD(format="$%,.0f")})
        x2.dataframe(o["methods"].reset_index(), hide_index=True, use_container_width=True, key="mk_m")
        st.dataframe(o["examples"], hide_index=True, use_container_width=True, key="mk_e",
                     column_config={"project page": LINK("project page", display_text="open ↗"),
                                    "value (US$)": USD(format="$%,.0f")})
    with st.expander("🧾 Cited brief (every sentence verified against its evidence)"):
        from procurement_demo.copilot import build_brief
        brief = build_brief(pkg, bot.kb)
        sec = None
        for stmt in brief["statements"]:
            if stmt["section"] != sec:
                sec = stmt["section"]
                st.markdown(f"**{sec}**")
            cites = " ".join(f"`{c}`" for c in stmt["citations"])
            st.markdown("- " + linkify(f"{stmt['text']} {cites}", brief["evidence"]))
        show_sources(brief["evidence"], key="plan_src")

# ── 💬 Ask ──────────────────────────────────────────────────────────────────────────────────────
FOLLOW_UP = re.compile(r"^\s*(and|what about|how about|same for|now|and in|for)\b", re.I)

with chat_tab:
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("ctx", None)
    ctx = st.session_state.ctx
    st.caption(f"In context: **{ins.name(ctx['country'])} · {ctx['item_type']}** (follow up with “and in Nigeria?” or "
               "“what about microscopy?”)" if ctx else
               "No purchase in context. Ask about a purchase to plan, or anything about suppliers, prices, timelines, "
               "lessons or relationships.")
    if not st.session_state.history and "pending" not in st.session_state:
        st.markdown("**Example questions**")
        cols = st.columns(2)
        examples = ["I'm planning to purchase laboratory analyzers in Kenya. What should I expect?", *EXAMPLES[:5]]
        for i, ex in enumerate(examples):
            if cols[i % 2].button(ex, key=f"chatq{i}", use_container_width=True):
                st.session_state.pending = ex
                st.rerun()

    def route(q):
        """Planning questions and follow-ups ('and in Nigeria?') → lifecycle card; everything else → chatbot."""
        parsed = bot.parse(q)
        req = planning_request(q, parsed)
        prev = st.session_state.ctx
        if not req and prev and FOLLOW_UP.search(q) and (parsed.get("countries") or parsed.get("item_types")):
            req = {"country": (parsed.get("countries") or [prev["country"]])[0],
                   "item_type": (parsed.get("item_types") or [prev["item_type"]])[0]}
        if req:
            st.session_state.ctx = req
        return req

    for n, turn in enumerate(st.session_state.history):
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            if turn.get("plan"):
                lifecycle_card(turn["plan"]["country"], turn["plan"]["item_type"], key=f"h{n}", compact=True)
            else:
                show_answer(turn, key=f"h{n}")
    q = st.chat_input("Ask about a purchase to plan, suppliers, prices, timelines, lessons or relationships…")
    q = q or st.session_state.pop("pending", None)
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Working it out from contracts, tenders, models and reports…"):
                req = route(q)
                if req:
                    lifecycle_card(req["country"], req["item_type"], key=f"h{len(st.session_state.history)}", compact=True)
                    st.session_state.history.append({"question": q, "plan": req})
                else:
                    ans = bot.ask(q)
                    show_answer(ans, key=f"h{len(st.session_state.history)}")
                    st.session_state.history.append(ans)

# ── 📋 My plan ──────────────────────────────────────────────────────────────────────────────────
with my_tab:
    plan = st.session_state.my_plan
    st.markdown("#### My procurement plan")
    if not plan:
        st.info("Nothing saved yet. Use **➕ Add to my plan** in Plan my purchase or on any lifecycle answer.")
    else:
        table = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in plan])
        st.dataframe(table, hide_index=True, use_container_width=True)
        tls = pd.concat([r["_timeline"].assign(package=f"{i + 1}. {r['package']}") for i, r in enumerate(plan)])
        st.markdown("**Portfolio timeline**")
        st.altair_chart(alt.Chart(tls).mark_bar(cornerRadius=4).encode(
            x=alt.X("start:T", title=None), x2="end:T", y=alt.Y("package:N", title=None, axis=alt.Axis(labelLimit=320)),
            color=alt.Color("step:N", title=None), tooltip=["package", "step", alt.Tooltip("start:T", format="%d %b %Y"),
                                                             alt.Tooltip("end:T", format="%d %b %Y"), "note"])
                        .properties(height=60 + 34 * len(plan)), use_container_width=True)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            table.to_excel(xw, sheet_name="Plan", index=False)
            tls.to_excel(xw, sheet_name="Timeline", index=False)
            for i, r in enumerate(plan, start=1):
                c_iso, it = r["_key"]
                sc = score(build_package(c_iso, it, r["method"], r["estimated value (US$)"], projects=ins.projects))
                figures_table(lc.figures(c_iso, it, r["method"], r["estimated value (US$)"], sc)).drop(
                    columns=["links"]).to_excel(xw, sheet_name=f"Evidence {i}", index=False)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download plan (Excel, with evidence sheets)", buf.getvalue(), "labprocure_plan.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        if d2.button("🗑️ Clear plan", use_container_width=True):
            st.session_state.my_plan = []
            st.rerun()

# ── 🌍 Countries ────────────────────────────────────────────────────────────────────────────────
with ctry_tab:
    st.markdown("#### Compare countries")
    st.caption("India, Kenya and Nigeria by default; add or remove countries to compare others.")
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
                   "subset alone is too small), censoring-aware; shown only with at least 5 timed tenders.")
        notes = []
        t = table.dropna(subset=["value (US$M)"])
        if len(t) > 1:
            top = t.loc[t["value (US$M)"].idxmax()]
            notes.append(f"**{top['country']}** spent the most (US${top['value (US$M)']:,.1f}M across {top['contracts']} contracts).")
        timed = table.dropna(subset=["median days to award"])
        if len(timed) > 1:
            fast, slow = timed.loc[timed["median days to award"].idxmin()], timed.loc[timed["median days to award"].idxmax()]
            notes.append(f"Goods tenders in these projects were awarded fastest in **{fast['country']}** (median "
                         f"{fast['median days to award']:.0f} days) and slowest in **{slow['country']}** "
                         f"({slow['median days to award']:.0f} days).")
        sb = table.dropna(subset=["single-bid share"])
        if len(sb) > 1:
            hi = sb.loc[sb["single-bid share"].idxmax()]
            notes.append(f"Competition was thinnest in **{hi['country']}**: {hi['single-bid share']:.0%} of tenders drew a single bid.")
        notes += [f"**{r['country']}** mostly used {r['most common method']}." for _, r in table.dropna(subset=["most common method"]).iterrows()]
        if notes:
            st.markdown(md("**What this shows**\n" + "\n".join(f"- {n}" for n in notes)))
        g1, g2 = st.columns(2)
        g1.markdown("**Value by equipment type (US$M)**")
        vt = ins.value_by_type(picks).reset_index().melt("item_type", var_name="country", value_name="US$M")
        g1.altair_chart(alt.Chart(vt).mark_bar().encode(
            y=alt.Y("item_type:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)), x=alt.X("US$M:Q"),
            color="country:N", yOffset="country:N", tooltip=["country", "item_type", "US$M"]), use_container_width=True)
        g2.markdown("**Procurement methods (share of lab tenders)**")
        ms = ins.method_share_by_country(picks, cmp_item).reset_index().melt("country_iso3", var_name="method", value_name="share")
        g2.altair_chart(alt.Chart(ms).mark_bar().encode(
            y=alt.Y("country_iso3:N", title=None), x=alt.X("share:Q", axis=alt.Axis(format="%")), color="method:N",
            tooltip=["country_iso3", "method", alt.Tooltip("share:Q", format=".0%")]), use_container_width=True)
        st.markdown("**Largest suppliers per country**")
        for col, iso3 in zip(st.columns(len(picks)), picks):
            col.markdown(f"_{ins.name(iso3)}_")
            col.dataframe(ins.top_suppliers(iso3, cmp_item, k=5)[["supplier", "value (US$)"]], hide_index=True,
                          use_container_width=True, column_config={"value (US$)": USD(format="$%,.0f")})

# ── 🏭 Suppliers ────────────────────────────────────────────────────────────────────────────────
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
                x=alt.X("US$M:Q"), y=alt.Y("country:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
                color="supplier:N", yOffset="supplier:N", tooltip=["supplier", "country", "US$M"]), use_container_width=True)
            for k in chosen:
                with st.expander(f"Contracts: {names[k]}"):
                    st.dataframe(ins.supplier_detail(k)["contracts"], hide_index=True, use_container_width=True,
                                 column_config={"project page": LINK("project page", display_text="open ↗"),
                                                "value (US$)": USD(format="$%,.0f")})

# ── 📄 Documents ────────────────────────────────────────────────────────────────────────────────
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
    docs = load_docs(CODE_VERSION)
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
            try:
                with st.spinner(f"Searching {countries[iso3]} documents…"):
                    res = docs.answer(question, iso3, None if category == "All categories" else category)
            except Exception as e:              # e.g. the embedding model could not be downloaded on this host
                st.error(f"Document search is unavailable right now ({str(e)[:140]}). The 💬 Ask tab still answers "
                         "from verified lessons and report passages.")
                res = {"note": "", "answer": [], "sources": []}       # keep rendering the other tabs
            if res["note"]:
                st.info(res["note"])
            by_id = {src["id"]: src for src in res["sources"]}
            if res["answer"]:
                def cite(sid):
                    src = by_id.get(sid)
                    return f"[\\[{sid}, p. {src['pages']}\\]]({src['url']})" if src and src.get("url") else f"**[{sid}]**"
                st.markdown("\n".join(f"- {md(a['text'])} {cite(a['source'])}" for a in res["answer"]))
                st.caption("Each sentence is taken from the cited document; click a citation to open the document.")
            st.markdown(f"**Sources ({len(res['sources'])})**")
            st.dataframe(pd.DataFrame([{"id": src["id"], "document": f"{src['doc_type']} · {src['title'][:80]}",
                                        "project": f"{src['project_id']} · {src['project_name']}", "page": src["pages"],
                                        "date": src["doc_date"], "section": src["section"], "link": src["url"]}
                                       for src in res["sources"]]),
                         hide_index=True, use_container_width=True, column_config={"link": LINK("open", display_text="open ↗")})
            for src in res["sources"]:
                with st.expander(f"[{src['id']}] {src['doc_type']} · {src['title'][:90]} · p. {src['pages']}"):
                    st.markdown(f"Project {src['project_id']} {src['project_name']} · {src['doc_date']} · section: "
                                f"{src['section']}  \n[Open the document]({src['url']})")
                    st.write(src["text"])

# ── 📊 Explore ──────────────────────────────────────────────────────────────────────────────────
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
    st.markdown("**Contracts**")
    st.dataframe(sel.sort_values("amount_usd", ascending=False)[
        ["wb_contract_number", "supplier_name", "supplier_country", "amount_usd", "signing_date", "country_iso3",
         "project_id", "item_type", "procurement_method", "description"]].head(500), use_container_width=True, hide_index=True)

# ── 🏗️ How it's built & tested ──────────────────────────────────────────────────────────────────
with build_tab:
    st.markdown("#### Tested, not just built")
    ret = evals.get("retrieval", {})
    cb = evals.get("chatbot", {})
    rs = rag_eval.get("summary", {})
    sbm = ins.metrics.get("single_bid", {})
    le = evals.get("lesson_extraction", {})
    score_rows = [
        ("Guardrails", "Planted wrong numbers / fake quotes caught",
         f"{g.get('caught', 0)} of {g.get('injected_hallucinations', 0)}", "25 briefs, 5 planted errors each"),
        ("Guardrails", "Statements that carry a citation", f"{g.get('kept_with_citation_share', 0):.1%}",
         f"{g.get('statements_kept', 0)} statements"),
        ("Lessons", "Extracted lesson quotes found on the cited page", f"{le.get('verification_rate', 0):.0%}",
         f"{le.get('extracted', 0)} lessons"),
        ("Retrieval", "Lesson search precision@5 (vs random)",
         f"{ret.get('topic_macro', {}).get('precision@5', 0):.2f} vs {ret.get('topic_macro', {}).get('random_precision', 0):.2f}",
         "4 procurement topics, labelled passages"),
        ("Retrieval", "Project documents: hit@10 / MRR@10",
         f"{ret.get('project_link', {}).get('hit@10', 0):.2f} / {ret.get('project_link', {}).get('mrr@10', 0):.2f}",
         f"{ret.get('project_link', {}).get('queries', 0)} queries"),
        ("Document Q&A", "Right document in the top 5", f"{rs.get('hit_rate_top5', 0):.0%}",
         f"{rs.get('questions', 0)} questions; {rs.get('correctly_declined', 0)} correctly declined"),
        ("Chatbot", "Questions routed to the right tool", f"{cb.get('route_accuracy', 0):.0%}", f"{cb.get('questions', 0)} questions"),
        ("Chatbot", "Exact answers correct", f"{cb.get('exact_accuracy', 0):.0%}", f"{cb.get('exact_checks', 0)} checked against the data"),
        ("Models", "Award-time error (P50) on later tenders", f"{m.get('mae_days_model_p50', 0):.0f} days",
         f"vs {m.get('mae_days_global_median', 0):.0f} for a single median; n={m.get('n_test', 0)}"),
        ("Models", "P80 coverage", f"{m.get('p80_coverage', 0):.0%}", "80% = perfectly calibrated"),
        ("Models", "Single-bid AUC / lift in top decile", f"{sbm.get('auc', 0):.2f} / {sbm.get('lift_top_decile', 0):.1f}x",
         "out-of-time split at " + str(ins.metrics.get("split_date", ""))),
    ]
    st.dataframe(pd.DataFrame(score_rows, columns=["area", "test", "result", "on"]), hide_index=True, use_container_width=True)
    st.caption("From the evaluation scripts in the repository (procurement_demo/evaluate.py, procurement_rag/evaluate.py, "
               "procurement_demo/models.py), re-run whenever the data is rebuilt.")
    st.markdown("#### How it's built")
    st.markdown(
        "One pipeline, two runtimes. **This app** runs the local Python version: it reads processed tables (Parquet), "
        "saved models and a ChromaDB vector index from the repository, and does no data processing at request time. "
        "The **Databricks pipeline** (notebooks 00 and 20-34) implements the same steps on a lakehouse and was run end to "
        "end on Databricks Free Edition; it is a separate deployment. Both import the same parsing, taxonomy and guardrail "
        "code (`notebooks/20_proc_lib.py`). A **FastAPI service** (`api/`) exposes the planner, optimizer and pre-mortem "
        "as a REST API, packaged with a Dockerfile.")
    st.graphviz_chart(ARCHITECTURE_DOT, use_container_width=True)
    st.markdown("#### Data model")
    st.caption("Region → Country → Project → Tender → Contract → Supplier → Equipment type, with documents, report "
               "passages, lessons and ratings hanging off the project. Every arrow is a row type in gold.kg_edges.")
    st.graphviz_chart(SCHEMA_DOT, use_container_width=True)
    counts = bot.edges.groupby(["src_type", "rel", "dst_type", "method"]).size().rename("edges").reset_index()
    st.dataframe(counts.sort_values("edges", ascending=False), use_container_width=True, hide_index=True)
    st.markdown("#### Links")
    st.markdown("- Full report: https://dmlaishu.github.io/procurement-intelligence/report.html\n"
                "- Code: https://github.com/dmlaishu/procurement-intelligence")

st.caption(f"{ui.BRAND} is an independent demonstration built on World Bank open data. It is not a World Bank Group "
           f"product. Lifecycle stages: {' → '.join(STAGES)}.")
