"""Step 4: answer a question from one country's documents, with the supporting sources.

Retrieval is hybrid: a vector query (meaning) and BM25 (exact words such as "GeneXpert" or "P173820"), both filtered
to the selected country and category, fused with reciprocal-rank fusion, at most two chunks per document.

The answer is built from the retrieved passages themselves: the sentences closest in meaning to the question,
each followed by its source number, so nothing in it can be invented. If a writing model is configured
(RAG_LLM_URL / RAG_LLM_MODEL / RAG_LLM_KEY, any OpenAI-compatible endpoint such as a Databricks serving endpoint), it
writes a short answer from the same sources instead; that answer is kept only if every citation exists and every
number in it appears in the cited sources, otherwise the extractive answer is shown.
"""
import os
import re

import numpy as np
import pandas as pd
import requests

from procurement_demo.retrieval import BM25, rrf

from .config import category_key, country_dir, lib, load_registry
from .index import collection, embed_passages, embed_query

# Thresholds calibrated for BAAI/bge-small-en-v1.5 on the Kenya / India evaluation set
MIN_SIMILARITY = 0.55              # sentence shares a content word with the question
MIN_SIMILARITY_NO_OVERLAP = 0.70   # shares none: must be much closer in meaning


class DocSearch:
    def __init__(self):
        self.col = collection()
        self._chunks, self._bm25 = {}, {}

    def countries(self):
        """Registered countries that have been built, as {ISO3: name}."""
        reg = load_registry()["countries"]
        return {k: v["name"] for k, v in reg.items() if (country_dir(k) / "chunks.parquet").exists()}

    def chunks(self, iso3):
        if iso3 not in self._chunks:
            self._chunks[iso3] = pd.read_parquet(country_dir(iso3) / "chunks.parquet")
        return self._chunks[iso3]

    def stats(self, iso3):
        c = self.chunks(iso3)
        return {"documents": int(c.doc_id.nunique()), "chunks": len(c), "projects": int(c.project_id.nunique()),
                "by_type": c.drop_duplicates("doc_id").doc_type.value_counts().to_dict()}

    def retrieve(self, question, iso3, category=None, k=8, per_doc=2):
        df = self.chunks(iso3)
        where = {"country": iso3}
        if category:
            key = category_key(category)
            df = df[df[key]]
            where = {"$and": [{"country": iso3}, {key: True}]}
        if df.empty:
            return df, None
        qv = embed_query(question)
        res = self.col.query(query_embeddings=[qv.tolist()], n_results=min(40, len(df)), where=where, include=["distances"])
        vec_ids = res["ids"][0]
        bkey = (iso3, category)
        if bkey not in self._bm25:
            self._bm25[bkey] = (BM25((df.title + " " + df.section + " " + df.text).tolist()), df.chunk_id.tolist())
        bm, ids = self._bm25[bkey]
        s = bm.scores(question)
        bm_ids = [ids[i] for i in np.argsort(-s)[:40] if s[i] > 0]
        picked, per = [], {}
        by_id = df.set_index("chunk_id")
        for cid in rrf([vec_ids, bm_ids]):
            doc = by_id.at[cid, "doc_id"]
            if per.get(doc, 0) >= per_doc:
                continue
            per[doc] = per.get(doc, 0) + 1
            picked.append(cid)
            if len(picked) >= k:
                break
        return by_id.loc[picked].reset_index(), qv

    def answer(self, question, iso3, category=None, max_sentences=4):
        name = self.countries().get(iso3, iso3)
        hits, qv = self.retrieve(question, iso3, category)
        if hits.empty:
            return {"answer": [], "sources": [], "note": f"No {name} documents are tagged with this category yet."}
        sources = []
        for i, r in enumerate(hits.to_dict("records"), start=1):
            sources.append({"id": f"S{i}", "country": r["country"], "doc_type": r["doc_type"], "title": r["title"], "project_id": r["project_id"],
                            "project_name": r["project_name"], "doc_date": r["doc_date"], "section": r["section"],
                            "pages": f"{r['page_start']}" + (f"-{r['page_end']}" if r["page_end"] != r["page_start"] else ""),
                            "url": r["url"], "text": r["text"]})
        llm = generate(question, sources, name)
        if llm:
            return llm
        return extractive(question, qv, sources, name, max_sentences, category)


def extractive(question, qv, sources, country_name, max_sentences=4, category=None):
    """The sentences from the retrieved passages that are closest in meaning to the question, each with its source.
    With a category selected, a sentence must itself mention that category. Rules: 20_proc_lib (rag_*)."""
    cands = lib.rag_candidate_sentences(sources, category)
    if not cands:
        return {"answer": [], "sources": sources, "note": "The closest passages are below.", "method": "extractive"}
    vecs = embed_passages([c[1] for c in cands])
    out = lib.rag_pick_sentences(question, qv.tolist(), cands, vecs.tolist(), max_sentences,
                                 MIN_SIMILARITY, MIN_SIMILARITY_NO_OVERLAP)
    note = None if out else (f"The {country_name} documents don't answer this directly"
                             + (f" for {category}" if category else "") + ". The closest passages are below.")
    used = {o["source"] for o in out}
    sources = sorted(sources, key=lambda s: s["id"] not in used)          # cited sources first
    return {"answer": out, "sources": sources, "note": note, "method": "extractive"}


def generate(question, sources, country_name):
    """Optional written answer from an OpenAI-compatible endpoint, kept only if it passes the citation and number checks."""
    url, model = os.environ.get("RAG_LLM_URL"), os.environ.get("RAG_LLM_MODEL")
    if not (url and model):
        return None
    ctx = "\n\n".join(f"[{s['id']}] {s['doc_type']} {s['title']}, p. {s['pages']}:\n{s['text'][:1800]}" for s in sources[:6])
    prompt = (f"Answer the question using only the sources from {country_name} below. After every sentence, cite the "
              f"source id(s) in square brackets, e.g. [S2]. If the sources do not answer it, say so. At most 120 words.\n\n"
              f"Question: {question}\n\nSources:\n{ctx}")
    try:
        r = requests.post(url.rstrip("/") + "/chat/completions", timeout=60,
                          headers={"Authorization": f"Bearer {os.environ.get('RAG_LLM_KEY', '')}"},
                          json={"model": model, "temperature": 0, "max_tokens": 400,
                                "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    by_id = {s["id"]: s for s in sources}
    cited = set(re.findall(r"\[(S\d+)\]", text))
    if not cited or cited - set(by_id):
        return None
    if lib.unsupported_numbers(re.sub(r"\[S\d+\]", " ", text), [by_id[c]["text"] for c in cited]):
        return None
    parts = [p.strip() for p in re.split(r"(?<=\])\s+", text) if p.strip()]
    out = [{"text": re.sub(r"\s*\[S\d+\]", "", p), "source": ", ".join(re.findall(r"\[(S\d+)\]", p)) or None} for p in parts]
    return {"answer": out, "sources": sorted(sources, key=lambda s: s["id"] not in cited), "note": None, "method": "written"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--country", required=True)
    ap.add_argument("--category")
    a = ap.parse_args()
    res = DocSearch().answer(a.question, a.country.upper(), a.category)
    if res["note"]:
        print(res["note"])
    for x in res["answer"]:
        print(f"- {x['text']} [{x['source']}]")
    for s in res["sources"][:5]:
        print(f"[{s['id']}] {s['doc_type']} | {s['title'][:80]} | p. {s['pages']} | {s['url']}")
