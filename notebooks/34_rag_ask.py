# Databricks notebook source
# MAGIC %md
# MAGIC # Step 34 - Document Q&A: ask a question about one country's documents
# MAGIC
# MAGIC Pick a **country** and a **category** in the dropdowns at the top, type a **question**, and run the notebook (or
# MAGIC just the answer cell). The answer comes only from that country's documents and lists its sources.
# MAGIC
# MAGIC 1. **Retrieve**: hybrid Vector Search (meaning + exact words) on `ai.rag_chunks_index`, filtered by
# MAGIC    `country` and the category column; at most two passages per document.
# MAGIC 2. **Answer** (`answer_style`):
# MAGIC    * `Cited sentences`: the sentences from those passages closest in meaning to the question
# MAGIC      (`rag_candidate_sentences` / `rag_pick_sentences` from `20_proc_lib`, the same rules as the local app),
# MAGIC      each followed by its source. Nothing in the answer is written by a model.
# MAGIC    * `Written (checked)`: the chat model writes a short answer from the same passages; it is shown only if every
# MAGIC      citation exists and every number appears in the cited sources, otherwise the cited sentences are shown.
# MAGIC
# MAGIC The last cell runs the evaluation questions and reports how often the expected source is in the top 5.

# COMMAND ----------

# MAGIC %run ./00_proc_config

# COMMAND ----------

# MAGIC %run ./20_proc_lib

# COMMAND ----------

import json, re, time, html
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
CAT_COLS = {n: rag_category_key(n) for n in RAG_CATEGORIES}
COLUMNS = ["chunk_id", "country", "doc_id", "doc_type", "title", "doc_date", "project_id", "project_name", "url", "section",
           "content_type", "page_start", "page_end", "text"]
built = [r.country for r in spark.sql("SELECT DISTINCT country FROM gold.rag_chunks ORDER BY 1").collect()]
names = {r.iso3: r.country_name for r in spark.table("ops.rag_countries").collect()}
dbutils.widgets.dropdown("country", built[0], built, "Country")
dbutils.widgets.dropdown("category", "All categories", ["All categories", *RAG_CATEGORIES], "Category")
dbutils.widgets.text("question", "What laboratory equipment does the project plan to procure?", "Question")
dbutils.widgets.dropdown("answer_style", "Cited sentences", ["Cited sentences", "Written (checked)"], "Answer style")
LLM = resolve_llm_endpoint()


def embed(texts):
    """databricks-gte-large-en embeddings through ai_query (on serverless, SQL AI functions reach the model serving
    endpoints reliably; direct SDK calls from a notebook time out on Free Edition)."""
    df = spark.createDataFrame(list(enumerate(texts)), "i int, t string")
    rows = df.selectExpr("i", f"ai_query('{EMBEDDING_ENDPOINT}', t) AS v").collect()
    return [list(r.v) for r in sorted(rows, key=lambda r: r.i)]


def chat(prompt, max_tokens=400):
    """One completion from the chat model through ai_query."""
    return spark.sql(f"SELECT ai_query('{LLM}', :p, modelParameters => named_struct('temperature', 0.0, 'max_tokens', {max_tokens})) AS r",
                     args={"p": prompt}).first().r


def shortlist(question, cands, n=40):
    """The n candidate sentences sharing the most words with the question (keeps embedding calls small)."""
    qt = set(rag_tokens(question))
    return sorted(cands, key=lambda c: -len(qt & set(rag_tokens(c[1]))))[:n]


def retrieve(question, iso3, category=None, k=8, per_doc=2):
    filters = {"country": iso3}
    if category:
        filters[CAT_COLS[category]] = 1
    res = w.vector_search_indexes.query_index(index_name=RAG_INDEX, columns=COLUMNS, query_text=question, num_results=40,
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
    return [{"id": f"S{i}", "country": r["country"], "doc_type": r["doc_type"], "title": r["title"],
             "project_id": r["project_id"], "project_name": r["project_name"], "doc_date": str(r["doc_date"]),
             "section": r["section"], "url": r["url"], "text": r["text"],
             "pages": f"{int(r['page_start'])}" + (f"-{int(r['page_end'])}" if r["page_end"] != r["page_start"] else "")}
            for i, r in enumerate(picked, start=1)]


def written(question, sources, country_name):
    ctx = "\n\n".join(f"[{s['id']}] {s['doc_type']} {s['title']}, p. {s['pages']}:\n{s['text'][:1800]}" for s in sources[:6])
    prompt = (f"Answer the question using only the sources from {country_name} below. After every sentence, cite the "
              f"source id(s) in square brackets, e.g. [S2]. If the sources do not answer it, say so. At most 120 words.\n\n"
              f"Question: {question}\n\nSources:\n{ctx}")
    try:
        text = chat(prompt).strip()
    except Exception as e:
        print("writing model unavailable:", str(e)[:120])
        return None
    by_id = {s["id"]: s for s in sources}
    cited = set(re.findall(r"\[(S\d+)\]", text))
    if not cited or cited - set(by_id) or unsupported_numbers(re.sub(r"\[S\d+\]", " ", text), [by_id[c]["text"] for c in cited]):
        return None                                   # a citation or a number the sources do not support
    return [{"text": re.sub(r"\s*\[S\d+\]", "", p), "source": ", ".join(re.findall(r"\[(S\d+)\]", p))}
            for p in re.split(r"(?<=\])\s+", text) if p.strip()]


def answer(question, iso3, category=None, style="Cited sentences"):
    name = names.get(iso3, iso3)
    sources = retrieve(question, iso3, category)
    if not sources:
        return {"answer": [], "sources": [], "note": f"No {name} documents are tagged with this category yet."}
    out = written(question, sources, name) if style.startswith("Written") else None
    method = "written" if out else "extractive"
    if not out:
        cands = shortlist(question, rag_candidate_sentences(sources, category))
        out = rag_pick_sentences(question, embed([question])[0], cands, embed([c[1] for c in cands]), 4,
                                 RAG_MIN_SIM, RAG_MIN_SIM_NO_OVERLAP) if cands else []
    note = None if out else (f"The {name} documents don't answer this directly" + (f" for {category}" if category else "")
                             + ". The closest passages are below.")
    out, sources = rag_renumber(out, sources)               # S1, S2... in the order the answer cites them
    return {"answer": out, "sources": sources, "note": note, "method": method}

# COMMAND ----------

# MAGIC %md ## Answer

# COMMAND ----------

q, iso3 = dbutils.widgets.get("question"), dbutils.widgets.get("country")
cat = None if dbutils.widgets.get("category") == "All categories" else dbutils.widgets.get("category")
t0 = time.time()
res = answer(q, iso3, cat, dbutils.widgets.get("answer_style"))
e = html.escape
page = f"<h3>{e(q)}</h3><p style='color:#666'>{e(names.get(iso3, iso3))} · {e(cat or 'All categories')} · " \
       f"{res.get('method', '')} · {time.time() - t0:.1f}s</p>"
if res["note"]:
    page += f"<p><i>{e(res['note'])}</i></p>"
page += "<ul>" + "".join(f"<li>{e(a['text'])} <b>[{e(a['source'] or '')}]</b></li>" for a in res["answer"]) + "</ul>"
page += "<h4>Sources</h4>" + "".join(
    f"<details><summary><b>[{s['id']}]</b> {e(s['doc_type'])} · {e(s['title'][:100])} · p. {s['pages']}</summary>"
    f"<p>Project {e(s['project_id'])} {e(s['project_name'] or '')} · {e(s['doc_date'])} · {e(s['section'])} · "
    f"<a href='{e(s['url'])}' target='_blank'>open the document</a></p><p>{e(s['text'])}</p></details>"
    for s in res["sources"])
displayHTML(page)
log_event("rag_ask", "answer", country=iso3, category=cat, sentences=len(res["answer"]), method=res.get("method"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Evaluation (same questions as `procurement_rag/evaluate.py`)
# MAGIC If good answers are being declined, lower `RAG_MIN_SIM` in `00_proc_config`; if unrelated sentences appear, raise it.

# COMMAND ----------

CASES = [
    ("KEN", None, "What laboratory equipment will be procured under the COVID-19 emergency response?", "keyword", r"laborator"),
    ("KEN", None, "How were the East Africa public health laboratories strengthened?", "project", "P111556"),
    ("KEN", "Procurement process and contracts", "Which procurement methods and thresholds apply to goods?", "keyword", r"threshold|method"),
    ("KEN", None, "What lessons were learned from the laboratory networking project?", "doc_type", {"ICR", "ICRR"}),
    ("KEN", "Diagnostic imaging", "Is x-ray or imaging equipment being purchased?", "abstain", None),
    ("IND", None, "What diagnostic equipment is planned for tuberculosis elimination?", "keyword", r"tubercul|\bTB\b"),
    ("IND", None, "How will public health laboratories be strengthened for pandemic preparedness?", "project", "P175676"),
    ("IND", None, "What were the results of the Uttar Pradesh health systems strengthening project?", "project", "P100304"),
    ("IND", "Procurement process and contracts", "What does the procurement plan say about medical equipment packages?", "doc_type", {"PP"}),
    ("IND", "Molecular diagnostics (PCR, GeneXpert)", "Where are molecular diagnostic tests such as GeneXpert or PCR used?",
     "keyword", r"gene ?xpert|PCR|CBNAAT|molecular"),
]
rows = []
for iso3, cat, q, kind, value in [c for c in CASES if c[0] in built]:
    t0 = time.time()
    r = answer(q, iso3, cat)
    top = r["sources"][:5]
    ok = (not r["answer"] if kind == "abstain" else any(s["project_id"] == value for s in top) if kind == "project"
          else any(s["doc_type"] in value for s in top) if kind == "doc_type" else any(re.search(value, s["text"], re.I) for s in top))
    rows.append((iso3, cat or "All", q, kind, bool(ok), len(r["answer"]), all(s["country"] == iso3 for s in r["sources"]),
                 round(time.time() - t0, 1), "; ".join(f"{a['similarity']:.2f}" for a in r["answer"] if "similarity" in a)))
ev = spark.createDataFrame(rows, "country string, category string, question string, expectation string, passed boolean, "
                                 "answer_sentences int, sources_in_country boolean, seconds double, similarities string")
display(ev)
from pyspark.sql import functions as F
ev.withColumn("run_ts", F.current_timestamp()).write.mode("append").option("mergeSchema", "true") \
    .saveAsTable("ops.rag_eval_results")                     # history of evaluation runs (e.g. after changing thresholds)
print(f"passed {sum(r[4] for r in rows)}/{len(rows)}; all sources in the selected country: {all(r[6] for r in rows)}")
