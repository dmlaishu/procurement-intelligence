"""LabProcure - the simple app: Ask | Plan | How it's built.

    python -m streamlit run procurement_demo/app.py          # this app
    python -m streamlit run procurement_demo/app_full.py     # the full workspace with every tab

Front end only: the chatbot, document search, models, lifecycle figures and verifier are the same modules as before.
"""
import html
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from procurement_demo.chatbot import SCHEMA_DOT, ProcurementChat  # noqa: E402
from procurement_demo.common import GOLD, OUT  # noqa: E402
from procurement_demo.insights import (ARCHITECTURE_DOT, Insights, build_package, evidence_link,  # noqa: E402
                                       evidence_rows, linkify)
from procurement_demo.lifecycle import Lifecycle  # noqa: E402
from procurement_demo.models import score  # noqa: E402

st.set_page_config(page_title="LabProcure", page_icon="🔬", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],[data-testid="collapsedControl"]{display:none!important}
.block-container{max-width:1080px;padding-top:2.2rem;padding-bottom:3rem}
html,body,[class*="css"],.stMarkdown{font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.lp-top{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:.2rem}
.lp-top b{font-size:1.9rem;font-weight:800;letter-spacing:-.03em;color:#0f1f3d}
.lp-top b span{color:#1d5fe0}
.lp-top p{margin:0;color:#4b5563;font-size:1rem;max-width:720px}
.stTabs [data-baseweb="tab-list"]{gap:6px;border-bottom:1.5px solid #dbe5f3}
.stTabs [data-baseweb="tab"]{font-size:1.02rem;font-weight:600;padding:10px 18px}
.answer{border-left:4px solid #1d5fe0;background:#f5f8ff;border-radius:10px;padding:14px 18px;margin:8px 0 4px;font-size:1.05rem;line-height:1.55}
.answer .lab{display:block;font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#1d5fe0;margin-bottom:4px}
.src{font-size:.92rem;color:#4b5563;margin:6px 0 0}
.src a{color:#1d5fe0}
.dates{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:10px 0 8px}
@media (max-width:700px){.dates{grid-template-columns:1fr}}
.dcard{border:1.5px solid #dbe5f3;border-radius:14px;padding:16px 18px;background:#fff}
.dcard.main{border-color:#1d5fe0;background:#f5f8ff}
.dcard small{display:block;font-size:.86rem;font-weight:600;color:#4b5563}
.dcard b{display:block;font-size:1.75rem;letter-spacing:-.02em;color:#0f1f3d;margin:4px 0 2px}
.dcard span{font-size:.82rem;color:#6b7280}
.tag{display:inline-block;font-size:.72rem;font-weight:700;border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:2px}
.t-model{background:#e8f0fe;color:#1d5fe0}.t-hist{background:#e6f6ef;color:#0f7a4f}.t-assumed{background:#f1f3f6;color:#4b5563;border:1px dashed #9ca3af}
.t-quote{background:#fff4e0;color:#9a5b00}
.explain{font-size:1.05rem;line-height:1.6;color:#1f2937;margin:10px 0 2px}
.small{font-size:.86rem;color:#6b7280}
.journey{position:relative;margin:6px 0 0;padding-left:30px}
.journey::before{content:"";position:absolute;left:11px;top:6px;bottom:6px;width:2px;background:#cfdcf3}
.step{position:relative;padding:0 0 16px}
.step::before{content:attr(data-n);position:absolute;left:-30px;top:0;width:24px;height:24px;border-radius:50%;background:#1d5fe0;color:#fff;
  font-size:.78rem;font-weight:700;display:grid;place-items:center}
.step b{font-size:1rem;color:#0f1f3d}
.step p{margin:2px 0 0;color:#4b5563;font-size:.95rem}
.step .fig{margin-top:4px;font-size:.92rem;color:#0f1f3d}
.step .fig a{color:#1d5fe0}
.ev{font-size:.94rem;line-height:1.55}
.ev a{color:#1d5fe0}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading the document index…")
def load_docs():
    from procurement_rag.search import DocSearch
    try:
        return DocSearch()
    except Exception:                  # index not built on this machine
        return None


@st.cache_resource(show_spinner="Loading the knowledge base…")
def load():
    bot = ProcurementChat()
    ins = Insights.load(country_name=bot.country_name)
    lc = Lifecycle(ins, bot.kb, GOLD)
    ev_path = OUT / "eval_results.json"
    evals = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    rag_path = Path(__file__).resolve().parent.parent / "procurement_rag" / "eval_results.json"
    rag_eval = json.loads(rag_path.read_text(encoding="utf-8")) if rag_path.exists() else {}
    return bot, ins, lc, evals, rag_eval


bot, ins, lc, evals, rag_eval = load()
g, m = evals.get("guardrails", {}), ins.metrics.get("cycle", {})
esc = html.escape


def md(s):
    return str(s).replace("$", "\\$")


def fmt(d):
    d = pd.Timestamp(d)
    return f"{d.day} {d:%b %Y}"


# ── header and the three tabs ───────────────────────────────────────────────────────────────────
st.markdown('<div class="lp-top"><b>Lab<span>Procure</span></b><p>Explore World Bank laboratory procurement data, ask '
            'questions about past projects, and estimate timelines for future purchases.</p></div>', unsafe_allow_html=True)

TABS = ["Ask", "Plan", "How it's built"]
qp = st.query_params
view = str(qp.get("view", "")).lower()
default_tab = {"plan": "Plan", "design": "Plan", "anticipate": "Plan", "built": "How it's built"}.get(view, "Ask")
try:
    ask_tab, plan_tab, build_tab = st.tabs(TABS, default=default_tab)
except TypeError:                     # Streamlit without st.tabs(default=...)
    ask_tab, plan_tab, build_tab = st.tabs(TABS)

# ── Ask ─────────────────────────────────────────────────────────────────────────────────────────
# Data questions (suppliers, prices, totals, timelines) are answered exactly from the contract tables by the chatbot;
# everything else is answered from the country's project documents. Both cite their sources and are verified.
DATA_INTENTS = {"rank_low", "rank_high", "suppliers", "supplier", "aggregate", "timeline", "graph", "project"}


def short_from_bot(ans):
    """Answer: the first 1-3 verified statements. Source: the records they cite."""
    stm = [s for s in ans["statements"] if not s.get("abstention")]
    if not stm:
        return None, [], ans
    first = [s for s in stm if s["section"] == stm[0]["section"]][:3]
    if len(first) == 1:                    # e.g. a total followed by its breakdown: take the next two lines too
        first = stm[:3]
    text = " ".join(re.sub(r"\s+", " ", s["text"]).strip() for s in first)
    ids = list(dict.fromkeys(c for s in first for c in s["citations"]))[:2]
    srcs = []
    for i in ids:
        e = ans["evidence"].get(i)
        if not e:
            continue
        row = evidence_rows({i: e}, ins.project_name).iloc[0]
        label = row["project"] or row["source"]
        if "fact_contract" in label or "dim_supplier" in label:
            label = "World Bank contract awards (calculated from the records)"
        label += f", contract {row['contract']}" if row["contract"] else ""
        label += f", p. {row['page']}" if row["page"] else ""
        srcs.append((label if label.startswith("World Bank") else f"{row['type']}: {label}", evidence_link(e)))
    return text, srcs, ans


def short_from_docs(res):
    """Answer: up to two sentences quoted from the documents. Source: the document and page they come from."""
    if not res["answer"]:
        return None, [], res
    by_id = {s["id"]: s for s in res["sources"]}
    first = res["answer"][:2]
    text = " ".join(a["text"] for a in first)
    srcs = []
    for sid in dict.fromkeys(a["source"] for a in first):
        s = by_id.get(sid)
        if s:
            srcs.append((f"{s['doc_type']}, {s['title'][:70]}, p. {s['pages']}", s.get("url")))
    return text, srcs, res


PURCHASED = re.compile(r"\b(what|which)\b.*\b(purchased|bought|procured)\b", re.I)


def ask(question, iso3, cname, category):
    parsed = bot.parse(question)
    if PURCHASED.search(question):    # "what was bought" is a question for the contract tables, answered exactly
        where = parsed["countries"][0] if parsed["countries"] else None
        question = f"Breakdown of laboratory equipment spending by equipment type in {ins.name(where) if where else cname}"
        parsed = bot.parse(question)
    if bot.intent(question, parsed) in DATA_INTENTS:
        q = question if parsed["countries"] else f"{question} in {cname}"
        if category and category != "Procurement process and contracts" and not parsed["item_types"]:
            q += f" ({category.lower()})"
        return "data", *short_from_bot(bot.ask(q))
    docs = load_docs()
    if docs is not None:
        try:
            return "docs", *short_from_docs(docs.answer(question, iso3, category))
        except Exception:             # e.g. the embedding model could not be loaded here: fall back to the chatbot
            pass
    q = question if parsed["countries"] else f"{question} in {cname}"
    return "data", *short_from_bot(bot.ask(q))


def show_detail(kind, full):
    if kind == "docs":
        rest = full["answer"][2:]
        if rest:
            st.markdown("\n".join(f"- {md(a['text'])} [{a['source']}]" for a in rest))
        for s in full["sources"][:6]:
            st.markdown(f"- **[{s['id']}]** {esc(s['doc_type'])} · {md(esc(s['title'][:90]))} · p. {s['pages']} · "
                        f"[open]({s['url']})")
    else:
        st.markdown(linkify(full["answer_md"], full["evidence"]))
        if full.get("table") is not None and len(full["table"]):
            st.dataframe(full["table"].head(10), use_container_width=True, hide_index=True)
        if full["guardrail_log"]:
            st.caption(f"{len(full['guardrail_log'])} statement(s) removed by the verifier because their figures or "
                       "quotes were not in the cited record.")


with ask_tab:
    docs = load_docs()
    countries = docs.countries() if docs else {}
    countries = countries or {"KEN": "Kenya", "IND": "India", "NGA": "Nigeria"}
    order = [k for k in ("KEN", "IND") if k in countries] + [k for k in countries if k not in ("KEN", "IND")]
    from procurement_rag.config import CATEGORIES
    c1, c2 = st.columns(2)
    iso3 = c1.selectbox("Country", order, format_func=lambda k: countries[k], key="ask_country")
    category = c2.selectbox("Category", ["All categories", *CATEGORIES], key="ask_cat")
    cname = countries[iso3]
    samples = [f"What laboratory equipment was purchased in {cname}?", "Which suppliers received laboratory contracts?",
               "What caused procurement delays?", "What lessons can we learn from previous projects?"]
    if str(qp.get("q", "")) and not st.session_state.get("q_used") and view in ("", "ask", "chat"):
        st.session_state.q_used = True
        st.session_state.ask_q = str(qp.get("q"))[:300]
    st.caption("Try a question")
    cols = st.columns(4)
    for i, s in enumerate(samples):
        if cols[i].button(s, key=f"sample{i}", use_container_width=True):
            st.session_state.ask_q = s
    question = st.text_input("Your question", key="ask_q", placeholder=f"Ask about laboratory procurement in {cname}…")
    if question.strip():
        with st.spinner("Searching the records…"):
            kind, text, srcs, full = ask(question.strip(), iso3, cname,
                                         None if category == "All categories" else category)
        if text:
            src_html = " · ".join(f'<a href="{esc(u)}" target="_blank">{esc(lbl)}</a>' if u else esc(lbl) for lbl, u in srcs)
            st.markdown(f'<div class="answer"><span class="lab">Answer</span>{esc(text)}</div>'
                        f'<p class="src"><b>Source:</b> {src_html or "World Bank records"}</p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="answer"><span class="lab">Answer</span>The {esc(cname)} records do not answer this '
                        f'directly{" for " + esc(category.lower()) if category != "All categories" else ""}. Try another '
                        'category or rephrase the question.</div>', unsafe_allow_html=True)
        with st.expander("More detail and all sources"):
            show_detail(kind, full)

# ── Plan ────────────────────────────────────────────────────────────────────────────────────────
PLAN_COUNTRIES = [k for k in ("KEN", "IND") if k in ins.countries()]
ITEMS = ins.item_types()
METHODS = dict(ins.methods())


def usual_method(country, item, value):
    """The competitive method most used for this purchase whose usual contract sizes include the value."""
    rng = lc.method_ranges()
    names = {v: k for k, v in METHODS.items()}
    for name in ins.method_mix(country, item).index:
        code = names.get(name)
        if code in rng and rng[code][0] <= value <= rng[code][1]:
            return code
    return "RFB" if "RFB" in METHODS else next(iter(METHODS))


# the website can hand over its inputs (?view=plan&country=KEN&item=...&value=...&date=...)
if qp.get("country") and not st.session_state.get("plan_link_used"):
    st.session_state.plan_link_used = True
    c_in, i_in = str(qp.get("country")).upper(), str(qp.get("item", ""))
    if c_in in PLAN_COUNTRIES and i_in in ITEMS:
        st.session_state.p_country, st.session_state.p_item = c_in, i_in
        try:
            st.session_state.p_value = max(1_000, int(float(qp.get("value", qp.get("budget", 100_000)))))
            st.session_state.p_date = date.fromisoformat(str(qp.get("date")))
        except (TypeError, ValueError):
            pass

with plan_tab:
    c1, c2, c3, c4 = st.columns([1, 1.6, 1, 1])
    country = c1.selectbox("Country", PLAN_COUNTRIES, format_func=ins.name, key="p_country")
    item = c2.selectbox("Equipment type", ITEMS, key="p_item",
                        index=ITEMS.index("Laboratory analyzers") if "Laboratory analyzers" in ITEMS else 0)
    value = c3.number_input("Estimated budget (US$)", min_value=1_000, value=100_000, step=10_000, key="p_value")
    open_date = c4.date_input("Planned invitation date", value=date.today() + timedelta(days=90), key="p_date")
    rec = usual_method(country, item, value)
    with st.expander("Advanced options"):
        a1, a2, a3 = st.columns(3)
        mlist = list(METHODS)
        method = a1.selectbox("Procurement method", mlist, index=mlist.index(rec), format_func=lambda k: METHODS[k],
                              key=f"p_method_{country}_{item}_{rec}", help="Defaults to the method usually used at this budget.")
        window = a2.slider("Bid window (days)", 7, 90, 30, key="p_window")
        review = a3.radio("World Bank review", ["Post", "Prior"], horizontal=True, key="p_review",
                          help="Prior review adds the Bank's no-objection steps before award.")

    # same calculation as before: build the package, score it, lay out the timeline
    pkg = build_package(country, item, method, value, review_type=review, planned_open_date=open_date,
                        bid_window_days=window, projects=ins.projects)
    s = score(pkg)
    tl = lc.timeline(country, item, open_date, s)
    d50, d80, ddel = tl.iloc[1].end, tl.iloc[2].end, tl.iloc[3].end
    months = round((ddel - d80).days / 30.44)
    bench = ins.duration(country, item, method)

    st.markdown(f"""<div class="dates">
      <div class="dcard"><small>Likely award<span class="tag t-model">model</span></small><b>{fmt(d50)}</b>
        <span>Half of similar tenders are awarded by then.</span></div>
      <div class="dcard main"><small>Safer planning date<span class="tag t-model">model</span></small><b>{fmt(d80)}</b>
        <span>About 4 in 5 similar tenders are awarded by then.</span></div>
      <div class="dcard"><small>Estimated delivery<span class="tag t-assumed">assumption</span></small><b>{fmt(ddel)}</b>
        <span>Not a prediction: the usual contracted delivery period added to the safer date.</span></div>
    </div>""", unsafe_allow_html=True)
    mae = m.get("mae_days_model_p50", 0)
    safer = (f"For safer planning, allow until {d80:%B %Y}." if (d80 - d50).days >= 7 else
             f"For this purchase the safer estimate is the same date, so allow the typical margin of about {mae:.0f} days.")
    st.markdown(f'<p class="explain">Based on similar World Bank tenders, your contract could be awarded around '
                f'{d50:%B %Y}. {safer} Delivery may take another {months} month{"s" if months != 1 else ""}.</p>',
                unsafe_allow_html=True)
    st.markdown(f'<p class="small">Based on {bench["n"]:,} comparable historical tenders ({esc(bench["scope"])}) · '
                f'Typical prediction error: ±{mae:.0f} days</p>', unsafe_allow_html=True)

    # ── your procurement journey: seven stages, one figure each where the data supports it ──
    figs = {f.figure: f for f in lc.figures(country, item, method, value, s)}
    risks, lessons = lc.premortem(country, item, s)

    def fig(name, text, tag):
        f = figs.get(name)
        if not f or f.basis == "Missing" or str(f.value).startswith("0"):
            return '<div class="fig small">Not available in public data.</div>'
        return f'<div class="fig">{text.format(v=esc(str(f.value)))}<span class="tag {tag[0]}">{tag[1]}</span></div>'

    HIST, MODEL, ASSUMED, QUOTE = ("t-hist", "historical"), ("t-model", "model"), ("t-assumed", "assumption"), ("t-quote", "report quote")
    top_risk = (f'<div class="fig">Most common problem: {esc(risks.iloc[0]["risk"].lower())} '
                f'({risks.iloc[0]["how often"]:.0%} of {int(risks.iloc[0]["n"])} similar tenders)'
                f'<span class="tag {HIST[0]}">{HIST[1]}</span></div>') if len(risks) else '<div class="fig small">Not available in public data.</div>'
    if lessons:
        lsn = lessons[0]
        q = lsn["quote"] if len(lsn["quote"]) <= 150 else lsn["quote"][:147].rsplit(" ", 1)[0] + "…"
        lesson = (f'<div class="fig">“{esc(q)}” <a href="{esc(lsn["url"])}" target="_blank">{esc(lsn["doc"])} '
                  f'{esc(lsn["project"])}, p. {lsn["page"]}</a><span class="tag {QUOTE[0]}">{QUOTE[1]}</span></div>')
    else:
        lesson = '<div class="fig small">Not available in public data.</div>'
    stages = [
        ("Planning", "Define your equipment needs and budget.",
         fig("Project approval → first lab tender", "Projects typically took {v} from approval to their first lab tender.", HIST)),
        ("Tender design", "Choose the procurement method and bid window.",
         fig("Bid window (invitation → deadline)", "Typical bidding period: {v}.", HIST)),
        ("Bidding & evaluation", "Receive and compare supplier bids.",
         fig("Bids received per tender", "Typical number of bids: {v}.", HIST)),
        ("Award & contract", "Select the supplier and finalize the contract.",
         f'<div class="fig">Likely award: {fmt(d50)}<span class="tag {MODEL[0]}">{MODEL[1]}</span></div>'),
        ("Delivery", "Receive and check the equipment.",
         f'<div class="fig">Estimated: {fmt(ddel)}<span class="tag {ASSUMED[0]}">{ASSUMED[1]}</span></div>'),
        ("Monitoring", "Watch for delays and contract changes.", top_risk),
        ("Learning", "Use lessons from previous projects.", lesson),
    ]
    st.markdown("#### Your procurement journey")
    st.caption("Seven simple stages, from planning to lessons learned")
    st.markdown('<div class="journey">' + "".join(
        f'<div class="step" data-n="{i}"><b>{t}</b><p>{p}</p>{f}</div>' for i, (t, p, f) in enumerate(stages, start=1))
        + "</div>", unsafe_allow_html=True)

    with st.expander("See supporting evidence"):
        ex = ins.examples(country, item, k=3)
        st.markdown(
            f'<div class="ev"><b>Sample size.</b> The historical figures come from {bench["n"]:,} comparable tenders '
            f'({esc(bench["scope"])}).<br><b>Model accuracy.</b> The award-date model was trained on tenders opened before '
            f'{ins.metrics.get("split_date", "")} and tested on {m.get("n_test", 0)} later tenders it had not seen: its likely '
            f'date was off by {m.get("mae_days_model_p50", 0):.0f} days on average (a single overall average date: '
            f'{m.get("mae_days_global_median", 0):.0f} days), and {m.get("p80_coverage", 0):.0%} of awards came by the safer '
            f'date. Delivery dates are not published, so the delivery estimate is an assumption.<br><br>'
            '<b>Comparable contracts</b></div>', unsafe_allow_html=True)
        for _, r in ex.iterrows():
            st.markdown(md(f"- {r['supplier']} · US${r['value (US$)']:,.0f} · signed {r['signed']} · {r['description'][:90]} "
                           f"· [{r['project'].split(' · ')[0]}]({r['project page']})"))
        if lessons:
            st.markdown(md(f"- Lesson source: [{lessons[0]['doc']} {lessons[0]['project']}, page {lessons[0]['page']}]"
                           f"({lessons[0]['url']})"))

# ── How it's built ──────────────────────────────────────────────────────────────────────────────
with build_tab:
    st.markdown("#### Evaluation results")
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
    st.markdown(
        "**Scope.** The contract data covers laboratory and diagnostic equipment bought in World Bank health projects in "
        "91 countries. The demonstrations focus on Kenya and India: the planner covers those two countries, and the "
        "document search covers their project reports (and Nigeria's).")
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

st.caption("LabProcure is an independent demonstration built on World Bank open data. It is not a World Bank Group product.")
