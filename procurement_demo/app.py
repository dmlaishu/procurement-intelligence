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
                                       evidence_rows, project_url)
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
.findings{margin:14px 0 10px;font-size:1rem;line-height:1.55}
.findings .lab{display:block;font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#1d5fe0;margin-bottom:2px}
.findings .kh{font-weight:700;color:#0f1f3d;margin:10px 0 2px}
.findings ul.kf{margin:0;padding-left:1.2rem}
.findings ul.kf li{margin:3px 0}
.findings a{color:#1d5fe0;text-decoration:none;font-size:.85rem}
.sources{margin:4px 0 12px;font-size:.9rem;color:#374151;border-top:1px solid #e3e8ef;padding-top:10px}
.sources .lab{display:block;font-size:.78rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#1d5fe0;margin-bottom:2px}
.sources ul{list-style:none;padding:0;margin:0}.sources li{margin:3px 0}.sources a{color:#1d5fe0}
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
.step .basis{font-size:.84rem;color:#6b7280;margin-top:2px}
.step .note2{font-size:.86rem;color:#0f1f3d;background:#f5f8ff;border-radius:8px;padding:4px 10px;margin-top:4px;display:inline-block}
.step details{margin-top:4px}
.step summary{cursor:pointer;color:#1d5fe0;font-size:.86rem;font-weight:600}
.step .evd{font-size:.88rem;color:#374151;border-left:2px solid #cfdcf3;padding:4px 0 2px 12px;margin-top:6px}
.step .evd p{margin:0 0 6px}.step .evd ul{margin:0 0 4px;padding-left:1.1rem}.step .evd a{color:#1d5fe0}
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


DOC_TYPES = {"ICR": "Implementation Completion and Results Report", "ICRR": "Implementation Completion Report Review",
             "PAD": "Project Appraisal Document", "ISR": "Implementation Status and Results Report", "PPR": "Project Paper",
             "PP": "Procurement Plan", "PPAR": "Project Performance Assessment Report"}
BENCH_SCOPE = {"country+category+method": "in the selected country", "item_type+method": "across all countries",
               "item_type": "across all countries", "category+method": "across all countries", "category": "across all countries"}


def millions(text):
    """US$11,421,911 → US$11.42M in short findings (the exact figure stays in the table)."""
    return re.sub(r"US\$(\d{1,3}(?:,\d{3}){2,})(?!\d)", lambda m_: f"US${int(m_.group(1).replace(',', '')) / 1e6:.2f}M", text)


def cites(ids, evidence):
    out = []
    for i in ids:
        link = evidence_link(evidence[i]) if i in evidence else None
        out.append(f'<a href="{esc(link)}" target="_blank">[{esc(i)}]</a>' if link else f"[{esc(i)}]")
    return " ".join(out)


def names(statements, k=3):
    """The labels before the colon in breakdown lines: 'Reagents and cartridges: 48 contracts, …' → 'reagents and cartridges'."""
    out = [re.split(r":| \(", st_["text"], maxsplit=1)[0].strip() for st_ in statements[:k]]
    return [n if n.isupper() else n[:1].lower() + n[1:] for n in out]


def joined(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def project_country(pid):
    c = ins.projects.loc[ins.projects.project_id == pid, "country_iso3"]
    if len(c) and isinstance(c.iloc[0], str):
        return ins.name(c.iloc[0])
    # no country on the project record: report titles start with it ("Serbia - Second Serbia Health Project")
    titles = bot.kb.chunks.loc[bot.kb.chunks.project_id == pid, "title"].dropna()
    prefix = str(titles.iloc[0]).split(" - ")[0].strip() if len(titles) and " - " in str(titles.iloc[0]) else ""
    if prefix in set(bot.country_name.values()):
        return prefix
    return f"{prefix} (regional project)" if prefix else "a regional project"


def friendly_scope(scope):
    return scope.replace("health lab & diagnostics", "laboratory and diagnostic equipment")


def source_label(e, scope):
    """(description, link) for one evidence record: what it is, which records, and where to open it."""
    kind = e["id"][0]
    pid = e.get("project_id")
    if "label" in e:                   # records built by the app itself (model, benchmark) carry their own description
        return e["label"], None
    if kind == "D":
        if "inputs" in e or str(e.get("text", "")).startswith("Derived:"):
            return (f"Calculated from {', '.join(e.get('inputs', [])) or 'the records above'}: "
                    f"{str(e['text']).replace('Derived: ', '').replace('Derived from ', '')[:180]}", None)
        return (f"World Bank Contract Awards: {friendly_scope(scope)}. Statistics calculated from the matching contract "
                "records (table and calculation under More details).", None)
    if kind == "C":
        return (f"World Bank Contract Award: contract {e.get('contract', '')}, project {pid} "
                f"({ins.project_name.get(pid, '')[:70]}).", project_url(pid))
    if kind == "B":
        lvl = re.search(r"benchmark \(([^)]+)\)", str(e.get("text", "")))
        where = BENCH_SCOPE.get(lvl.group(1), "") if lvl else ""
        what = re.search(r"for ([^:]+):", str(e.get("text", "")))
        n = re.search(r"n=(\d+)", str(e.get("text", "")))
        return (f"World Bank tender notices: time from invitation to award for {what.group(1) if what else 'comparable tenders'}"
                f" {where}{', ' + n.group(1) + ' tenders' if n else ''}. Tenders still open are counted, not dropped.", None)
    if kind in "LP":
        doc = str(e.get("source", "")).split(" ", 1)
        dtype = DOC_TYPES.get(doc[0], doc[0])
        return (f"World Bank {dtype}: project {pid} ({project_country(pid)}), page {e.get('page', '?')}"
                + (" (quote checked on the page)" if kind == "L" else "") + ".", e.get("url"))
    if kind == "R":
        return (f"World Bank project ratings: supervision and completion ratings for project {pid} "
                f"({project_country(pid)}).", project_url(pid))
    if kind == "T":
        return f"World Bank tender notice: tender {e.get('tender_id', '')}, project {pid}.", project_url(pid)
    if kind == "M":
        return e.get("label", e.get("source", "Model")), None
    return e.get("source", "World Bank records"), evidence_link(e)


def present_bot(ans, cname):
    """Answer, key findings and sources from the chatbot's verified statements."""
    stm = [x for x in ans["statements"] if not x.get("abstention")]
    if not stm:
        return None
    sections = {}
    for x in stm:
        sections.setdefault(x["section"], []).append(x)
    titles = list(sections)
    route, ev, scope = ans["route"], ans["evidence"], ans.get("scope", "")
    if route == "lessons":
        lessons_ = sections.get(titles[0], [])[:3]
        where = joined(dict.fromkeys(project_country(ev[x["citations"][0]].get("project_id")) for x in lessons_
                                     if x["citations"] and x["citations"][0] in ev))
        summary = (f"Completion and appraisal reports of past World Bank health projects document these lessons; the "
                   f"{len(lessons_)} most relevant come from projects in {where}. Each is quoted below with its report and page.")
        head_ids = []
        findings = [("Main lessons (quoted from the reports)", lessons_)]
        extra = [x for t in titles for x in sections[t] if x not in lessons_]
    else:
        head = sections[titles[0]]
        summary = " ".join(re.sub(r"\s+", " ", x["text"]).strip() for x in head[:2])
        findings = [(t, sections[t]) for t in titles[1:]]
        if route == "aggregate" and findings:
            summary += f" The largest by value are {joined(names(findings[0][1]))}."
        elif route == "suppliers" and findings:
            summary += f" The largest are {joined(names(findings[0][1]))}."
        head_ids = list(dict.fromkeys(c for x in head[:2] for c in x["citations"]))
        extra = []
        if route == "timeline":
            b = next((ev[i] for i in head_ids if i.startswith("B")), None)
            lvl = re.search(r"benchmark \(([^)]+)\)", str(b.get("text", ""))) if b else None
            if lvl and BENCH_SCOPE.get(lvl.group(1)) == "across all countries":
                findings.insert(0, ("Scope", [{"text": f"This figure uses comparable tenders across all countries: "
                                                       f"{cname} alone has too few tenders with both dates.", "citations": head_ids}]))
    return {"summary": millions(summary), "head_ids": head_ids, "findings": findings, "extra": extra, "evidence": ev,
            "scope": scope, "table": ans.get("table"), "route": route, "removed": len(ans["guardrail_log"])}


def present_docs(res, cname):
    """Answer, findings and sources from the document search (quoted sentences, each with its document and page)."""
    if not res["answer"]:
        return None
    first, others = res["answer"][0], res["answer"][1:]
    ev = {x["id"]: {"id": x["id"], "url": x.get("url"), "project_id": x.get("project_id"), "page": x.get("pages"),
                    "source": f"{x['doc_type']} {x['title']}", "text": x.get("text", ""), "doc": x} for x in res["sources"]}
    findings = [("What the reports say", [{"text": a["text"], "citations": [a["source"]]} for a in others])] if others else []
    return {"summary": f"From {cname}'s World Bank project documents: “{first['text']}”", "head_ids": [first["source"]],
            "findings": findings, "extra": [], "evidence": ev, "docs": res["sources"], "scope": cname,
            "table": None, "route": "docs", "removed": 0}


PURCHASED = re.compile(r"\b(what|which)\b.*\b(purchased|bought|procured)\b", re.I)
WHEN_AWARD = re.compile(r"\bwhen\b.*\b(award|awarded|expect|contract)\b|\bexpect\b.*\baward", re.I)


def present_plan(country, item, cname):
    """'When should I expect … to be awarded?': the planner's model estimate, with its accuracy and historical basis."""
    value = ins.value_stats(country, item).get("median") or 100_000
    meth = usual_method(country, item, value)
    od = date.today() + timedelta(days=90)
    s_ = score(build_package(country, item, meth, value, planned_open_date=od, projects=ins.projects))
    tl_ = lc.timeline(country, item, od, s_)
    d50_, d80_ = tl_.iloc[1].end, tl_.iloc[2].end
    bench_ = ins.duration(country, item, meth)
    bscope = bench_["scope"] + ("" if cname in bench_["scope"] or "all countries" in bench_["scope"] else " across all countries")
    ev = {"M1": {"id": "M1", "label": (f"LabProcure award-time model (gradient-boosted quantile regression), trained on "
                                       f"{m.get('n_train', 0):,} World Bank tenders opened before {ins.metrics.get('split_date', '')} "
                                       f"and tested on {m.get('n_test', 0)} later tenders. A model estimate, not a World Bank record.")},
          "B1": {"id": "B1", "label": (f"World Bank tender notices: time from invitation to award, {bscope}, "
                                       f"{bench_['n']} tenders. Tenders still open are counted, not dropped.")}}
    findings = [
        ("Model estimate and its accuracy", [
            {"text": f"Typical error on later tenders: about ±{m.get('mae_days_model_p50', 0):.0f} days; the safer date "
                     f"held in {m.get('p80_coverage', 0):.0%} of tests (it aims for about 80%).", "citations": ["M1"]},
            {"text": f"Assumed for this estimate: bids invited on {fmt(od)}, {METHODS[meth]} (the usual method at this "
                     f"budget), a budget of US${value:,.0f} (the typical contract value) and a 30-day bid window. Change them "
                     "in the Plan tab.", "citations": ["M1"]}]),
        ("Historical basis", [
            {"text": f"Comparable tenders took a median of {bench_['median']:.0f} days from invitation to award; 80% within "
                     f"{bench_['p80']:.0f} days ({bscope}, {bench_['n']} tenders).", "citations": ["B1"]}]),
    ] if bench_["median"] is not None else []
    return {"summary": (f"If bids are invited on {fmt(od)}, a contract for {item.lower()} in {cname} is most likely to be awarded "
                        f"around {fmt(d50_)}. For safer planning, allow until {fmt(d80_)}. These are estimates, not "
                        "guaranteed dates."),
            "head_ids": ["M1"], "findings": findings, "extra": [], "evidence": ev, "scope": cname, "route": "plan",
            "table": pd.DataFrame([{"Bids invited": fmt(od), "Method": METHODS[meth], "Budget (US$)": f"{value:,.0f}",
                                    "Likely award": fmt(d50_), "Safer planning date": fmt(d80_),
                                    "Days to likely award": f"{s_['cycle_p50']:.0f}", "Days to safer date": f"{s_['cycle_p80']:.0f}"}]),
            "removed": 0}


def ask(question, iso3, cname, category):
    parsed = bot.parse(question)
    if WHEN_AWARD.search(question):   # a planning question: the model answers it, as in the Plan tab
        c_ = parsed["countries"][0] if parsed["countries"] else iso3
        items = parsed["item_types"] or ([category] if category in ITEMS else [])
        if c_ in ins.countries() and len(items) == 1:
            return present_plan(c_, items[0], ins.name(c_))
    if PURCHASED.search(question):    # "what was bought" is a question for the contract tables, answered exactly
        where = parsed["countries"][0] if parsed["countries"] else None
        question = f"Breakdown of laboratory equipment spending by equipment type in {ins.name(where) if where else cname}"
        parsed = bot.parse(question)
    if bot.intent(question, parsed) in DATA_INTENTS:
        q = question if parsed["countries"] else f"{question} in {cname}"
        if category and category != "Procurement process and contracts" and not parsed["item_types"]:
            q += f" ({category.lower()})"
        return present_bot(bot.ask(q), cname)
    docs = load_docs()
    if docs is not None:
        try:
            return present_docs(docs.answer(question, iso3, category), cname)
        except Exception:             # e.g. the embedding model could not be loaded here: fall back to the chatbot
            pass
    q = question if parsed["countries"] else f"{question} in {cname}"
    return present_bot(bot.ask(q), cname)


TABLES = {   # route → (columns to show, display names)
    "suppliers": (["supplier", "base", "contracts", "total_usd", "countries"],
                  ["Supplier", "Based in", "Contracts", "Total value (US$)", "Countries"]),
    "rank_low": (["wb_contract_number", "supplier_name", "supplier_country", "amount_usd", "signing_date", "project_id", "description"],
                 ["Contract", "Supplier", "Supplier country", "Value (US$)", "Signed", "Project", "Description"]),
    "timeline": (["item_type", "n", "n_awarded", "median_days", "p80_days"],
                 ["Equipment type", "Tenders", "Awarded", "Median days to award", "80% awarded within (days)"]),
}
TABLES["rank_high"] = TABLES["rank_low"]


def detail_table(p):
    t = p["table"]
    if t is None or not len(t):
        return None
    t = t.copy()
    if p["route"] == "aggregate":
        key = t.columns[0]
        t = t.rename(columns={key: {"item_type": "Equipment type", "country_iso3": "Country", "region": "Region", "year": "Year"}.get(key, key),
                              "contracts": "Contracts", "total_usd": "Total value (US$)", "suppliers": "Suppliers"})
        if "Country" in t:
            t["Country"] = t["Country"].map(bot.country_name).fillna(t["Country"])
    elif p["route"] in TABLES:
        cols, labels = TABLES[p["route"]]
        cols = [c for c in cols if c in t]
        t = t[cols].rename(columns=dict(zip(TABLES[p["route"]][0], labels)))
    for c in t.columns:
        if "US$" in c and t[c].dtype.kind in "fi":
            t[c] = t[c].map(lambda v: f"{v:,.0f}")
        elif t[c].dtype.kind == "f":
            t[c] = t[c].map(lambda v: "" if pd.isna(v) else f"{v:,.0f}")
        elif t[c].dtype.kind == "M":
            t[c] = t[c].dt.strftime("%d %b %Y")
    return t


def clean(text):
    return re.sub(r"\s+-\s+note:.*$", "", millions(re.sub(r"\s+", " ", text)))       # rating notes stay in the sources


def show_answer(p, cname, category):
    if p is None:
        st.markdown(f'<div class="answer"><span class="lab">Answer</span>The {esc(cname)} records do not answer this '
                    f'directly{" for " + esc(category.lower()) if category != "All categories" else ""}. Try another '
                    'category or rephrase the question.</div>', unsafe_allow_html=True)
        return
    ev, used = p["evidence"], list(p["head_ids"])
    st.markdown(f'<div class="answer"><span class="lab">Answer</span>{esc(p["summary"])} '
                f'{cites(p["head_ids"], ev)}</div>', unsafe_allow_html=True)
    if p["findings"]:
        blocks = []
        for title, items in p["findings"]:
            li = []
            for x in items[:6]:
                used += x["citations"]
                li.append(f"<li>{esc(clean(x['text']))} {cites(x['citations'], ev)}</li>")
            title = {"By item type": "By equipment type"}.get(title, title)
            blocks.append(f'<p class="kh">{esc(title)}</p><ul class="kf">{"".join(li)}</ul>')
        st.markdown('<div class="findings"><span class="lab">Key findings</span>' + "".join(blocks) + "</div>",
                    unsafe_allow_html=True)
    # the sources behind everything shown above, each described and linked where a link exists
    rows = []
    for i in dict.fromkeys(used):
        if i not in ev:
            continue
        label, link = source_label(ev[i], p["scope"]) if p["route"] != "docs" else (
            f"World Bank {DOC_TYPES.get(ev[i]['doc']['doc_type'], ev[i]['doc']['doc_type'])}: {ev[i]['doc']['title'][:80]}, "
            f"project {ev[i]['project_id']}, page {ev[i]['page']}.", ev[i]["url"])
        rows.append(f'<li><b>[{esc(i)}]</b> {esc(label)}'
                    + (f' <a href="{esc(link)}" target="_blank">open ↗</a>' if link else "") + "</li>")
    if rows:
        st.markdown('<div class="sources"><span class="lab">Sources</span><ul>' + "".join(rows) + "</ul></div>",
                    unsafe_allow_html=True)
    with st.expander("More details and all sources"):
        t = detail_table(p)
        if t is not None:
            st.dataframe(t, use_container_width=True, hide_index=True)
        if p["extra"]:
            st.markdown("**Further passages**")
            for x in p["extra"]:
                st.markdown(md(f"- {clean(x['text'])} [{', '.join(x['citations'])}]"))
        if p["route"] == "docs":
            st.markdown("**All retrieved documents**")
            for x in p["docs"]:
                st.markdown(md(f"- **[{x['id']}]** {x['doc_type']} · {x['title'][:90]} · page {x['pages']} · "
                               f"project {x['project_id']} · [open the document]({x['url']})"))
        elif p["route"] == "plan":
            st.caption("The Plan tab shows the full journey, the inputs you can change and the supporting evidence.")
        else:
            st.markdown("**All records and calculations**")
            for r in evidence_rows(ev, ins.project_name).itertuples():
                label, link = source_label(ev[r.id], p["scope"])
                st.markdown(md(f"- **[{r.id}]** {label}" + (f" [open]({link})" if link else "")
                               + f"  \n  <small>{esc(str(ev[r.id].get('text', ''))[:260])}</small>"), unsafe_allow_html=True)
        if p["removed"]:
            st.caption(f"{p['removed']} statement(s) were removed by the verifier because their figures or quotes were "
                       "not in the cited record.")


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
    for i, s_ in enumerate(samples):
        if cols[i].button(s_, key=f"sample{i}", use_container_width=True):
            st.session_state.ask_q = s_
    question = st.text_input("Your question", key="ask_q", placeholder=f"Ask about laboratory procurement in {cname}…")
    if question.strip():
        with st.spinner("Searching the records…"):
            p = ask(question.strip(), iso3, cname, None if category == "All categories" else category)
        show_answer(p, cname, category)

# ── Plan ────────────────────────────────────────────────────────────────────────────────────────
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
    s_risks, lessons = lc.premortem(country, item, s)
    HIST, MODEL, ASSUMED, QUOTE = ("t-hist", "historical"), ("t-model", "model"), ("t-assumed", "assumption"), ("t-quote", "report excerpt")
    NA = '<div class="fig small">Not available in public data.</div>'
    cname_p = ins.name(country)

    def tag(t):
        return f'<span class="tag {t[0]}">{t[1]}</span>'

    def based_on(n, label):
        """'Laboratory analyzers in Kenya' → '29 historical laboratory analyzer tenders in Kenya' (scope always stated)."""
        it = item.lower().rstrip("s")
        if label == f"{item} in {cname_p}":
            txt = f"{n:,} historical {it} tenders in {cname_p}"
        elif label == f"{item}, all countries":
            txt = f"{n:,} historical {it} tenders across all countries"
        elif label == f"all lab tenders in {cname_p}":
            txt = f"{n:,} historical lab and diagnostic tenders in {cname_p}"
        elif label == "all lab tenders":
            txt = f"{n:,} historical lab and diagnostic tenders across all countries"
        else:
            txt = f"{n:,} historical tenders ({label})"
        return txt + (" (a small sample: treat as indicative)" if n < 30 else "")

    def scoped(cond):
        return lc._scoped(lc.t, country, item, cond=cond)

    DOC = {"ICR": "Implementation Completion and Results Report", "ICRR": "Implementation Completion Report Review",
           "PAD": "Project Appraisal Document", "ISR": "Implementation Status and Results Report", "PPR": "Project Paper",
           "PP": "Procurement Plan", "PPAR": "Project Performance Assessment Report"}
    NOTICE = "https://search.worldbank.org/api/v2/procnotices?format=json&id={}"     # the API the notices were loaded from

    # 1 Planning: project-level statistic, scope stated
    ft = lc.first_tender_stats(country)
    if ft["n"]:
        ctry = ", ".join(f"{k} ({v})" for k, v in ft["countries"].head(5).items())
        ex_p = "".join(f'<li><a href="{esc(project_url(r.project_id))}" target="_blank">{esc(r.project_id)}</a> '
                       f'{esc(str(r.name)[:70])}: {r.months:.0f} months</li>' for r in ft["projects"].iloc[
                           [0, len(ft["projects"]) // 4, len(ft["projects"]) // 2, 3 * len(ft["projects"]) // 4, -1]].itertuples())
        planning = (
            f'<div class="fig">Historical finding: in past projects, the first laboratory tender started about '
            f'{ft["months"]:.0f} months after the overall project was approved.{tag(HIST)}</div>'
            '<div class="note2">This is a project-level planning statistic, not the time your individual purchase will take.</div>'
            f'<div class="basis">Based on: {ft["n"]} World Bank health projects '
            f'{"in " + esc(cname_p) if ft["country_specific"] else "across all countries (" + str(ft["n_countries"]) + " countries)"}.</div>'
            '<details><summary>View evidence</summary><div class="evd">'
            '<p><b>How it is calculated.</b> For each health project with lab or diagnostic tenders: the number of months from '
            'the project\'s Board approval date (World Bank Projects API) to the opening date of its earliest lab or diagnostic '
            'tender notice (World Bank procurement notices). The figure is the <b>median</b> across projects.</p>'
            f'<p><b>Projects used:</b> {ft["n"]}. '
            + ("" if ft["country_specific"] else f'{esc(cname_p)} has fewer than five such projects, so all countries are used. ')
            + f'<b>Countries with the most projects:</b> {esc(ctry)}.</p>'
            f'<p><b>Examples (shortest to longest):</b></p><ul>{ex_p}</ul></div></details>')
    else:
        planning = NA

    # 2 Tender design, 3 Bidding: historical, with sample and scope
    bw, bw_scope = scoped(lambda d: d.bid_window_days.between(1, 180))
    design = (f'<div class="fig">Typical bidding period: {bw.bid_window_days.median():.0f} days (median){tag(HIST)}</div>'
              f'<div class="basis">Based on: {based_on(len(bw), bw_scope)}. Source: World Bank invitation-for-bids notices '
              '(published and closing dates).</div>') if len(bw) else NA
    bd, bd_scope = scoped(lambda d: d.n_bidders.notna())
    med_b = bd.n_bidders.median() if len(bd) else 0
    bidding = (f'<div class="fig">Typical number of bids: {med_b:g} (median){tag(HIST)}</div>'
               f'<div class="basis">Based on: {based_on(len(bd), bd_scope)}. Source: World Bank contract award notices '
               '(bids received).</div>') if len(bd) and med_b >= 1 else NA

    # 4 Award: model, 5 Delivery: assumption
    award = (f'<div class="fig">Likely award: {fmt(d50)} · safer date: {fmt(d80)}{tag(MODEL)}</div>'
             f'<div class="basis">Model estimate, not a World Bank record: trained on {m.get("n_train", 0):,} tenders opened before '
             f'{ins.metrics.get("split_date", "")}, tested on {m.get("n_test", 0)} later ones (typical error ±{mae:.0f} days).</div>')
    dl, dl_scope = scoped(lambda d: d.contract_duration_months.between(0.01, 60))
    delivery = (f'<div class="fig">Estimated: {fmt(ddel)}{tag(ASSUMED)}</div>'
                f'<div class="basis">Assumption: the safer award date plus the usual contracted delivery period ({months} '
                f'month{"s" if months != 1 else ""}'
                + (f', median of {based_on(len(dl), dl_scope)}' if len(dl) else "")
                + '). Actual delivery dates are not in public data.</div>')

    # 6 Monitoring: revised or additional award notices, precisely defined
    an = lc.award_notice_stats(country, item)
    if an["n"]:
        ex_n = "".join(
            f'<li>{esc(e["description"])} · project <a href="{esc(project_url(e["project"]))}" target="_blank">{esc(e["project"])}</a>'
            + "".join(f' · <a href="{esc(NOTICE.format(nid))}" target="_blank">notice {esc(nid)} (record)</a>' for nid in e["notices"])
            + "</li>" for e in an["examples"])
        monitoring = (
            f'<div class="fig">Historical finding: {an["p"]:.0%} of similar tenders had a revised or additional award notice '
            f'recorded.{tag(HIST)}</div>'
            f'<div class="basis">Based on: {based_on(an["n"], an["scope"])}.</div>'
            '<details><summary>View evidence</summary><div class="evd">'
            '<p><b>Definition.</b> A tender counts when the World Bank published a later version of its contract award notice, '
            'or more than one award notice for the same tender. This can reflect a correction, a change to the award, or '
            'separate lots; it does not by itself show that the signed contract was amended.</p>'
            f'<p><b>Tenders analysed:</b> {an["n"]} awarded tenders · <b>meeting the condition:</b> {an["k"]} · '
            f'<b>filters:</b> {esc(an["scope"])}.</p>'
            '<p><b>Source:</b> World Bank procurement notices (contract award notices and their versions), loaded from the '
            'World Bank search API; each example links to its notice record there.</p>'
            f'<p><b>Examples:</b></p><ul>{ex_n}</ul></div></details>')
    else:
        monitoring = NA

    # 7 Learning: a labelled excerpt; the full verified quote and its source one click away
    verified = lc.lessons[lc.lessons.quote_verified.astype(bool)]
    lsn = next((x for x in lessons if x["quote"] in set(verified.evidence_quote)), None)
    if lsn:
        row = verified[verified.evidence_quote == lsn["quote"]].iloc[0]
        words = lsn["quote"].split()
        excerpt = " ".join(words[:18]) + ("…" if len(words) > 18 else "")
        doc = DOC.get(lsn["doc"], lsn["doc"])
        pname = ins.project_name.get(lsn["project"], "")
        pctry = project_country(lsn["project"])
        learning = (
            f'<div class="fig">Lesson from a past project (short excerpt): “{esc(excerpt)}”{tag(QUOTE)}</div>'
            f'<div class="basis">Source: World Bank {esc(doc)}, project {esc(lsn["project"])} ({esc(pctry)}), page {lsn["page"]}.</div>'
            '<details><summary>View full lesson and source</summary><div class="evd">'
            f'<p>“{esc(lsn["quote"])}”</p>'
            f'<p><b>Document:</b> {esc(str(row.title))} ({esc(doc)})<br><b>Project:</b> {esc(pname)} ({esc(lsn["project"])}), '
            f'{esc(pctry)}<br><b>Page:</b> {lsn["page"]}<br><b>Quote checked:</b> found word for word on the cited page '
            'of the report.<br>'
            f'<a href="{esc(lsn["url"])}" target="_blank">Open the report on documents.worldbank.org</a> (go to page '
            f'{lsn["page"]})</p></div></details>')
    else:
        learning = NA

    stages = [
        ("Planning", "Define the equipment needed, estimated budget, and procurement requirements.", planning),
        ("Tender design", "Choose the procurement method and bid window.", design),
        ("Bidding & evaluation", "Receive and compare supplier bids.", bidding),
        ("Award & contract", "Select the supplier and finalize the contract.", award),
        ("Delivery", "Receive and check the equipment.", delivery),
        ("Monitoring", "Track delays, contract changes, and possible procurement risks.", monitoring),
        ("Learning", "Use lessons from previous projects to avoid repeating common procurement problems.", learning),
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
