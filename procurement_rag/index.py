"""Step 3: embed chunks and keep them in the vector database (ChromaDB, persistent in data/index/).

Incremental per country: only chunks not yet in the collection are embedded, and chunks whose document disappeared
are removed. Other countries are never touched, so adding a country does not rebuild the index.
"""
import numpy as np
import pandas as pd

from .config import CATEGORIES, COLLECTION, DATA, EMBED_MODEL, INDEX_DIR, category_key, country_dir

_model = None
_client = None
META_COLS = ["country", "doc_id", "doc_type", "title", "doc_date", "project_id", "project_name", "url", "section",
             "section_kind", "content_type", "page_start", "page_end"]


def embedder():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(EMBED_MODEL, cache_dir=str(DATA / "models"))
    return _model


def embed_passages(texts, batch_size=64):
    return np.array(list(embedder().passage_embed(list(texts), batch_size=batch_size)), dtype=np.float32)


def embed_query(text):
    return np.array(list(embedder().query_embed([text]))[0], dtype=np.float32)


def collection():
    global _client
    import chromadb
    if _client is None:
        _client = chromadb.PersistentClient(path=str(INDEX_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
    return _client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def passage_text(r):
    """What gets embedded: a short header (document type, title, section) and the chunk text."""
    return f"{r['doc_type']} | {r['title']} | {r['section']}\n{r['text']}"


def stored_vectors(iso3):
    """{chunk_id: vector} saved next to the passages (embeddings.npz), or {} if there is none."""
    f = country_dir(iso3) / "embeddings.npz"
    if not f.exists():
        return {}
    z = np.load(f)
    return dict(zip(z["ids"].tolist(), z["vectors"].astype(np.float32)))


def export_vectors(iso3):
    """Save the country's embeddings next to its passages, so another machine (e.g. a hosted copy of the app) can load
    the index without embedding anything. float16 halves the size; cosine ranking is unaffected at this precision."""
    chunks = pd.read_parquet(country_dir(iso3) / "chunks.parquet")
    col = collection()
    ids, vecs = [], []
    for i in range(0, len(chunks), 1000):
        got = col.get(ids=chunks.chunk_id.iloc[i:i + 1000].tolist(), include=["embeddings"])
        ids += got["ids"]
        vecs += list(got["embeddings"])
    np.savez_compressed(country_dir(iso3) / "embeddings.npz", ids=np.array(ids), vectors=np.array(vecs, dtype=np.float16))
    print(f"{iso3}: {len(ids):,} vectors saved to embeddings.npz")


def index_country(iso3, batch=128):
    chunks = pd.read_parquet(country_dir(iso3) / "chunks.parquet")
    saved = stored_vectors(iso3)                   # vectors computed earlier (or on another machine): no re-embedding
    col = collection()
    have = set(col.get(where={"country": iso3}, include=[])["ids"])
    stale = sorted(have - set(chunks.chunk_id))
    for i in range(0, len(stale), 1000):
        col.delete(ids=stale[i:i + 1000])
    new = chunks[~chunks.chunk_id.isin(have)].reset_index(drop=True)
    cat_cols = [category_key(c) for c in CATEGORIES]
    # chunks already embedded whose category tags changed (e.g. a refined rule): update metadata, no re-embedding
    old = chunks[chunks.chunk_id.isin(have)]
    retagged = 0
    for i in range(0, len(old), 1000):
        part = old.iloc[i:i + 1000]
        got = col.get(ids=part.chunk_id.tolist(), include=["metadatas"])
        meta = dict(zip(got["ids"], got["metadatas"]))
        ids, metas = [], []
        for r in part.to_dict("records"):
            m = meta.get(r["chunk_id"]) or {}
            flags = {c: bool(r[c]) for c in cat_cols}
            if any(m.get(c) != v for c, v in flags.items()):
                ids.append(r["chunk_id"])
                metas.append({**m, **flags})
        if ids:
            col.update(ids=ids, metadatas=metas)
            retagged += len(ids)
    for i in range(0, len(new), batch):
        part = new.iloc[i:i + batch]
        if all(cid in saved for cid in part.chunk_id):
            vecs = np.array([saved[cid] for cid in part.chunk_id])
        else:
            vecs = embed_passages([passage_text(r) for r in part.to_dict("records")])
        metas = []
        for r in part.to_dict("records"):
            m = {c: ("" if r[c] is None else r[c]) for c in META_COLS}
            m.update({c: bool(r[c]) for c in cat_cols})
            m["page_start"], m["page_end"] = int(m["page_start"]), int(m["page_end"])
            metas.append(m)
        col.upsert(ids=part.chunk_id.tolist(), embeddings=vecs.tolist(), documents=part.text.tolist(), metadatas=metas)
        print(f"  {iso3}: indexed {min(i + batch, len(new)):,} / {len(new):,}", flush=True)
    total = len(col.get(where={"country": iso3}, include=[])["ids"])
    print(f"{iso3}: {len(new):,} new chunks indexed, {retagged:,} re-tagged, {len(stale):,} removed, {total:,} in the index")
    if len(new) or len(stale) or not saved:
        export_vectors(iso3)
    return total


def ensure_index(countries):
    """Load any country whose passages exist but which is missing from the vector database (a fresh machine), from
    its saved vectors. Returns the countries that were loaded."""
    col = collection()
    loaded = []
    for iso3 in countries:
        if (country_dir(iso3) / "chunks.parquet").exists() and not col.get(where={"country": iso3}, limit=1, include=[])["ids"]:
            index_country(iso3)
            loaded.append(iso3)
    return loaded
