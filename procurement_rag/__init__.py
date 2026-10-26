"""Country-scoped document RAG for laboratory and diagnostic equipment procurement (World Bank project documents).

    fetch    → find projects, download PDFs into data/<ISO3>/pdf/<doc type>/
    process  → extract text, section-aware chunks, category tags
    index    → embeddings (BAAI/bge-small-en-v1.5) into a ChromaDB collection, incremental per country
    search   → hybrid retrieval (vector + BM25) filtered by country and category, answer with cited sources

Run: python -m procurement_rag.pipeline --countries KEN IND
"""
