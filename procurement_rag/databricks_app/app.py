"""Lab and diagnostic equipment procurement: document chat (Databricks App).

Pick a country and a category, ask a question; the answer comes only from that country's World Bank project documents
and lists its sources. Retrieval: the Vector Search index built by notebooks 30-33 (hybrid, filtered by country and
category). Answer: the sentences closest in meaning to the question, each with its source (rules shared with the
notebooks and the local app through proc_lib.py, a copy of notebooks/20_proc_lib.py made by deploy.py), or, if chosen,
a short written answer that is shown only when every citation exists and every number is in the cited sources.
"""
import importlib.util
import json
import os
import re
from pathlib import Path

import streamlit as st
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

_spec = importlib.util.spec_from_file_location("proc_lib", Path(__file__).with_name("proc_lib.py"))
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

INDEX = os.environ.get("RAG_INDEX", "proc_demo.ai.rag_chunks_index")
CATALOG = INDEX.split(".")[0]
WAREHOUSE = os.environ["WAREHOUSE_ID"]
EMBEDDING = os.environ.get("EMBEDDING_ENDPOINT", "databricks-gte-large-en")
LLM = os.environ.get("LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
MIN_SIM, MIN_SIM_NO_OVERLAP = float(os.environ.get("RAG_MIN_SIM", 0.65)), float(os.environ.get("RAG_MIN_SIM_NO_OVERLAP", 0.75))
CAT_COLS = {n: lib.rag_category_key(n) for n in lib.RAG_CATEGORIES}
COLUMNS = ["chunk_id", "country", "doc_id", "doc_type", "title", "doc_date", "project_id", "project_name", "url", "section",
           "page_start", "page_end", "text"]
DOC_TYPES = {"PAD": "Project Appraisal Document", "PPR": "Project Paper", "PP": "Procurement Plan",
             "ISR": "Implementation Status and Results Report", "ICR": "Implementation Completion and Results Report",
             "ICRR": "IEG ICR Review", "PPAR": "IEG Project Performance Assessment"}
EXAMPLES = ["What laboratory equipment is being procured, and for which laboratories?",
            "What problems were found with the maintenance of laboratory or diagnostic equipment?",
            "Which procurement methods and thresholds apply to goods?",
            "What lessons were learned about strengthening public health laboratories?"]

st.set_page_config(page_title="Lab Equipment Procurement Q&A", page_icon="🔬", layout="wide")
w = WorkspaceClient()
# The pay-per-token embedding endpoint has a low per-workspace rate limit (Free Edition): give up quickly instead of
# retrying for five minutes, and fall back to word-overlap selection for that answer.
w_fast = WorkspaceClient(config=Config(retry_timeout_seconds=20))


def sql(statement):
    r = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE, statement=statement, wait_timeout="50s")
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (r.result.data_array or [])]


@st.cache_data(ttl=3600, show_spinner="Loading countries…")
def countries():
    rows = sql(f"""SELECT c.country, r.country_name, count(DISTINCT c.doc_id) AS documents, count(*) AS passages,
                          count(DISTINCT c.project_id) AS projects
                   FROM {CATALOG}.gold.rag_chunks c JOIN {CATALOG}.ops.rag_countries r ON r.iso3 = c.country
                   GROUP BY 1, 2 ORDER BY 2""")
    return {r["country"]: r for r in rows}


def embed(texts, batch=8):
    """Embeddings in batches of 8: on Free Edition one request to the pay-per-token endpoint is rejected (rate limit)
    above about 12 texts, while batches of 8 go through back to back."""
    out = []
    for i in range(0, len(texts), batch):
        out += [d.embedding for d in w_fast.serving_endpoints.query(name=EMBEDDING, input=texts[i:i + batch]).data]
    return out


def pick_by_overlap(question, cands, n=4, per_source=2):
    """Fallback when the embedding endpoint is busy: sentences sharing the most content words with the question."""
    qt = set(lib.rag_tokens(question))
    out, per, seen = [], {}, set()
    for sid, sent in sorted(cands, key=lambda c: -len(qt & set(lib.rag_tokens(c[1])))):
        overlap = len(qt & set(lib.rag_tokens(sent)))
        if overlap < 2 or per.get(sid, 0) >= per_source or sent[:60] in seen:
            continue
        per[sid] = per.get(sid, 0) + 1
        seen.add(sent[:60])
        out.append({"text": sent, "source": sid})
        if len(out) >= n:
            break
    return out


def retrieve(question, iso3, category, k=8, per_doc=2):
    filters = {"country": iso3}
    if category:
        filters[CAT_COLS[category]] = 1
    res = w_fast.vector_search_indexes.query_index(index_name=INDEX, columns=COLUMNS, query_text=question, num_results=40,
                                                   filters_json=json.dumps(filters), query_type="HYBRID")
    cols = [c.name for c in res.manifest.columns]
    picked, per = [], {}
    for row in res.result.data_array or []:
        r = dict(zip(cols, row))
        if per.get(r["doc_id"], 0) >= per_doc:
            continue
        per[r["doc_id"]] = per.get(r["doc_id"], 0) + 1
        picked.append(r)
        if len(picked) >= k:
            break
    return [{"id": f"S{i}", "doc_type": r["doc_type"], "title": r["title"], "project_id": r["project_id"],
             "project_name": r["project_name"] or "", "doc_date": str(r["doc_date"] or "")[:10], "section": r["section"],
             "url": r["url"], "text": r["text"],
             "pages": f"{int(r['page_start'])}" + (f"-{int(r['page_end'])}" if r["page_end"] != r["page_start"] else "")}
            for i, r in enumerate(picked, start=1)]


def written(question, sources, country_name):
    ctx = "\n\n".join(f"[{s['id']}] {s['doc_type']} {s['title']}, p. {s['pages']}:\n{s['text'][:1800]}" for s in sources[:6])
    prompt = (f"Answer the question using only the sources from {country_name} below. After every sentence, cite the "
              f"source id(s) in square brackets, e.g. [S2]. If the sources do not answer it, say so. At most 120 words.\n\n"
              f"Question: {question}\n\nSources:\n{ctx}")
    try:
        r = w.serving_endpoints.query(name=LLM, messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
                                      temperature=0.0, max_tokens=400)
        text = r.choices[0].message.content.strip()
    except Exception:
        return None
    by_id = {s["id"]: s for s in sources}
    cited = set(re.findall(r"\[(S\d+)\]", text))
    if not cited or cited - set(by_id) or lib.unsupported_numbers(re.sub(r"\[S\d+\]", " ", text), [by_id[c]["text"] for c in cited]):
        return None
    return [{"text": re.sub(r"\s*\[S\d+\]", "", p), "source": ", ".join(re.findall(r"\[(S\d+)\]", p))}
            for p in re.split(r"(?<=\])\s+", text) if p.strip()]


def answer(question, iso3, country_name, category, style):
    try:
        sources = retrieve(question, iso3, category)
    except Exception:
        return {"answer": [], "sources": [], "note": "The search service is busy right now (the workspace's request limit was "
                                                     "reached). Please ask again in a minute."}
    if not sources:
        return {"answer": [], "sources": [], "note": f"No {country_name} documents are tagged with {category} yet."}
    out = written(question, sources, country_name) if style == "Written summary (checked)" else None
    busy = False
    if not out:
        qt = set(lib.rag_tokens(question))
        cands = sorted(lib.rag_candidate_sentences(sources, category), key=lambda c: -len(qt & set(lib.rag_tokens(c[1]))))[:24]
        if cands:
            try:
                vecs = embed([question] + [c[1] for c in cands])
                out = lib.rag_pick_sentences(question, vecs[0], cands, vecs[1:], 4, MIN_SIM, MIN_SIM_NO_OVERLAP)
            except Exception:
                busy = True
                out = pick_by_overlap(question, cands)
    note = None if out else (f"The {country_name} documents don't answer this directly" + (f" for {category}" if category else "")
                             + ". The closest passages are below; try a more specific question.")
    if busy and out:
        note = "The meaning-matching service is busy, so these sentences were chosen by the words they share with your question."
    used = {x for o in out for x in (o.get("source") or "").split(", ")}
    return {"answer": out, "sources": sorted(sources, key=lambda s: s["id"] not in used), "note": note}


def show(res):
    if res["note"]:
        st.info(res["note"])
    if res["answer"]:
        st.markdown("\n".join(f"- {a['text']} **[{a['source']}]**" for a in res["answer"]))
    if res["sources"]:
        st.caption("Sources (each sentence above is taken from the source it cites):")
        for s in res["sources"]:
            with st.expander(f"[{s['id']}] {DOC_TYPES.get(s['doc_type'], s['doc_type'])} · {s['title'][:90]} · p. {s['pages']}"):
                st.markdown(f"Project **{s['project_id']}** {s['project_name']} · {s['doc_date']} · section: {s['section']}  \n"
                            f"[Open the document]({s['url']})")
                st.write(s["text"])


# ── page ─────────────────────────────────────────────────────────────────────────────────────────
ctry = countries()
with st.sidebar:
    st.markdown("### Lab equipment procurement")
    st.caption("Questions answered from World Bank project documents: appraisal documents, procurement plans, "
               "supervision, completion and evaluation reports.")
    iso3 = st.selectbox("Country", list(ctry), format_func=lambda k: ctry[k]["country_name"])
    category = st.selectbox("Category", ["All categories", *lib.RAG_CATEGORIES])
    style = st.radio("Answer style", ["Cited sentences", "Written summary (checked)"],
                     help="Cited sentences are quoted from the documents. A written summary is checked against the sources "
                          "and replaced by cited sentences if anything in it is not supported.")
    c = ctry[iso3]
    st.caption(f"{c['documents']} documents from {c['projects']} projects, {int(c['passages']):,} indexed passages.")
    if st.button("Clear chat", use_container_width=True):
        st.session_state.chat = []

name = ctry[iso3]["country_name"]
cat = None if category == "All categories" else category
st.markdown(f"#### Ask about laboratory and diagnostic equipment procurement in {name}")
st.session_state.setdefault("chat", [])
typed = st.chat_input(f"Ask a question about {name}…")
if not st.session_state.chat and not typed and "pending" not in st.session_state:   # only before the first question
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLES):
        if cols[i % 2].button(q, key=f"ex{i}", use_container_width=True):
            st.session_state.pending = q
            st.rerun()
for turn in st.session_state.chat:
    with st.chat_message("user"):
        st.markdown(turn["question"])
        st.caption(f"{turn['country']} · {turn['category']}")
    with st.chat_message("assistant"):
        show(turn["result"])

q = typed or st.session_state.pop("pending", None)
if q:
    with st.chat_message("user"):
        st.markdown(q)
        st.caption(f"{name} · {category}")
    with st.chat_message("assistant"):
        with st.spinner(f"Searching {name} documents…"):
            res = answer(q, iso3, name, cat, style)
        show(res)
    st.session_state.chat.append({"question": q, "country": name, "category": category, "result": res})
