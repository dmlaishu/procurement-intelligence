"""Hybrid retrieval over the procurement knowledge base (local stand-in for Vector Search hybrid queries).

BM25 lexical scoring + metadata filters and boosts (country, sector, procurement relevance, section kind)
+ reciprocal-rank fusion across sub-queries + per-document diversity. On Databricks the same plan runs against
the Vector Search index (semantic + keyword) with the same filters - see notebook 27.
"""
import math
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
which their there been not but they into than these those also such other more most only over under per any all each
project projects bank world""".split())


def stem(w):
    for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + rep
    return w


def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z][a-z0-9\-]{1,}", (text or "").lower()) if w not in STOP]


class BM25:
    def __init__(self, texts, k1=1.4, b=0.75):
        self.docs = [Counter(tokens(t)) for t in texts]
        self.len = np.array([sum(d.values()) for d in self.docs], dtype=float)
        self.avg = self.len.mean() if len(self.len) else 1.0
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b
        self.inv = defaultdict(list)
        for i, d in enumerate(self.docs):
            for w, c in d.items():
                self.inv[w].append((i, c))

    def scores(self, query):
        s = np.zeros(len(self.docs))
        for w in set(tokens(query)):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i, c in self.inv[w]:
                s[i] += idf * c * (self.k1 + 1) / (c + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


def rrf(rankings, k=60):
    """Reciprocal rank fusion of several ranked id lists."""
    score = defaultdict(float)
    for ranked in rankings:
        for r, i in enumerate(ranked):
            score[i] += 1 / (k + r + 1)
    return sorted(score, key=lambda i: -score[i])


class KnowledgeBase:
    def __init__(self, gold_dir):
        self.lessons = pd.read_parquet(gold_dir / "lessons.parquet")
        self.lessons = self.lessons[self.lessons.quote_verified].reset_index(drop=True)   # guardrail: only verified
        self.chunks = pd.read_parquet(gold_dir / "proj_doc_chunks.parquet")
        self.tenders = pd.read_parquet(gold_dir / "fact_tender.parquet")
        self.projects = pd.read_parquet(gold_dir / "dim_project.parquet")
        self.ratings = pd.read_parquet(gold_dir / "project_ratings_resolved.parquet")
        sector = self.projects.set_index("project_id")["major_sector"]
        self.lessons["major_sector"] = self.lessons.project_id.map(sector)
        self.chunks["major_sector"] = self.chunks.project_id.map(sector)
        self.lesson_bm25 = BM25((self.lessons.lesson + " " + self.lessons.detail).tolist())
        self.chunk_bm25 = BM25(self.chunks.search_text.tolist())
        t = self.tenders[self.tenders.description.notna()].reset_index(drop=True)
        self.tender_rows = t
        self.tender_bm25 = BM25(t.description.tolist())

    def _boost(self, df, country=None, sector=None):
        w = np.ones(len(df))
        if country is not None:
            w *= np.where(df.country_iso3.values == country, 1.3, 1.0)
        if sector is not None and "major_sector" in df:
            w *= np.where(df.major_sector.values == sector, 1.2, 1.0)
        return w

    def search_lessons(self, queries, country=None, sector=None, k=8, per_doc=2, procurement_only=True):
        ranked = []
        for q in queries:
            s = self.lesson_bm25.scores(q) * self._boost(self.lessons, country, sector)
            if procurement_only:
                s *= np.where(self.lessons.procurement_related.values, 1.5, 0.6)
            ranked.append([i for i in np.argsort(-s)[:50] if s[i] > 0])
        out, per = [], Counter()
        for i in rrf(ranked):
            row = self.lessons.iloc[i]
            if per[row.doc_id] >= per_doc:          # diversity: at most per_doc lessons from one report
                continue
            per[row.doc_id] += 1
            out.append(row)
            if len(out) >= k:
                break
        return pd.DataFrame(out)

    def search_passages(self, queries, kinds=("lessons", "key_factors", "procurement_fiduciary", "risk"), country=None,
                        sector=None, k=6, exclude_doc_types=("PP",)):
        # procurement plans are tables rendered as PDF text: useful as data, unreadable as a quoted passage
        mask = (self.chunks.section_kind.isin(kinds).values & (self.chunks.content_type != "table").values
                & ~self.chunks.doc_type_code.isin(exclude_doc_types).values)
        ranked = []
        for q in queries:
            s = self.chunk_bm25.scores(q) * self._boost(self.chunks, country, sector) * mask
            ranked.append([i for i in np.argsort(-s)[:50] if s[i] > 0])
        out, per = [], Counter()
        for i in rrf(ranked):
            row = self.chunks.iloc[i]
            if per[row.doc_id] >= 2:
                continue
            per[row.doc_id] += 1
            out.append(row)
            if len(out) >= k:
                break
        return pd.DataFrame(out)

    def similar_tenders(self, description, category, country=None, k=12):
        t = self.tender_rows
        s = self.tender_bm25.scores(description)
        s *= np.where(t.procurement_category.values == category, 1.0, 0.0)
        s *= self._boost(t, country)
        s *= np.where(t.award_observed.values & t.open_date.notna().values, 1.0, 0.7)
        idx = [i for i in np.argsort(-s)[:k] if s[i] > 0]
        return t.iloc[idx].assign(score=s[idx])

    def rating_context(self, project_id):
        r = self.ratings[self.ratings.project_id == project_id]
        return None if r.empty else r.iloc[0].to_dict()
