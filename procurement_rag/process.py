"""Step 2: extract text from each PDF, split it into section-aware chunks and tag each chunk.

Text is extracted once per document and cached in data/<ISO3>/text/<doc id>.json (page by page). Chunks never cross a
report section and keep their section path and pages, so every answer can cite "ICR, p. 14, Lessons Learned".
Each chunk is tagged with the categories it mentions (equipment types, procurement process) for the dropdown filter.
"""
import json
import re

import pandas as pd

from .config import country_dir, lib

_SURROGATES = re.compile(r"[\ud800-\udfff]")


def extract_pages(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages.append((i, _SURROGATES.sub("", re.sub(r"(\w)-\n(\w)", r"\1\2", t))))
    return pages


def tag(text):
    return {k: bool(v) for k, v in lib.rag_tags(text).items()}


def process_country(iso3):
    folder = country_dir(iso3)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    (folder / "text").mkdir(exist_ok=True)
    rows, new = [], 0
    for d in manifest["documents"]:
        if not d.get("file"):
            continue
        cache = folder / "text" / f"{d['doc_id']}.json"
        if cache.exists():
            pages = [tuple(p) for p in json.loads(cache.read_text(encoding="utf-8"))]
        else:
            try:
                pages = extract_pages(folder / d["file"])
            except Exception as e:
                print(f"  skip {d['doc_id']}: {str(e)[:80]}")
                continue
            cache.write_text(json.dumps(pages), encoding="utf-8")
            new += 1
        for k, c in enumerate(lib.section_chunks(pages)):
            text = " ".join(c["text"].split())
            rows.append({"chunk_id": f"{d['doc_id']}_{k:04d}", "country": iso3, "doc_id": d["doc_id"],
                         "doc_type": d["doc_type"], "title": d["title"], "doc_date": d["doc_date"],
                         "project_id": d["project_id"], "project_name": d.get("project_name", ""),
                         "url": d.get("landing_url") or d["pdf_url"], "section": c["section_path"][:200],
                         "section_kind": lib.section_kind(c["section_path"]), "content_type": c["content_type"],
                         "page_start": int(c["page_start"]), "page_end": int(c["page_end"]), "n_words": c["n_words"],
                         "text": text, **tag(text)})
    chunks = pd.DataFrame(rows)
    chunks.to_parquet(folder / "chunks.parquet", index=False)
    print(f"{iso3}: {new} documents parsed, {len(chunks):,} chunks from {chunks.doc_id.nunique() if len(chunks) else 0} documents")
    return chunks
