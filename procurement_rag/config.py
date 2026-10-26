"""Settings, paths and the country registry for the document RAG pipeline.

Countries live in countries.json. Each entry only needs ISO codes and a name; everything else (which projects, which
documents, how many) comes from the shared defaults, so a new country is one entry, or one command:

    python -m procurement_rag.pipeline --add-country NGA
"""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
COUNTRIES_FILE = PKG / "countries.json"
DATA = PKG / "data"                       # data/<ISO3>/pdf/<doc type>/<doc id>.pdf, text/, manifest.json, chunks.parquet
INDEX_DIR = DATA / "index"                # the vector database (ChromaDB, persistent)
COLLECTION = "procurement_docs"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"    # 384-dim, runs on CPU through ONNX (no GPU, no API)

_spec = importlib.util.spec_from_file_location("proc_lib", ROOT / "notebooks" / "20_proc_lib.py")
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

# Categories for the dropdown: the ten lab and diagnostic equipment types from the shared taxonomy, plus the
# procurement process itself (plans, bidding, contracts). A chunk can belong to several.
CATEGORIES = {name: pat for name, pat in lib.ITEM_TYPES_LAB[:10]}
# Report text is noisier than contract descriptions: "CT" also means HIV counselling and testing, and PDF extraction
# splits words ("proje ct"), so for passages CT and C-arm must appear as imaging terms.
CATEGORIES["Diagnostic imaging"] = (CATEGORIES["Diagnostic imaging"]
                                    .replace(r"\bCT\b", r"\bCT[- ]?(scan|scanner|machine)s?\b")
                                    .replace(r"c-?arm", r"\bC-arm\b"))
CATEGORIES["Procurement process and contracts"] = (r"procure|tender|bidding|bidder|contract award|contract(s|or)? |"
                                                    r"supplier|request for (bids|quotations|proposals)|no objection")


def category_key(name):
    """Metadata field for a category, e.g. 'Molecular diagnostics (PCR, GeneXpert)' → 'cat_molecular_diagnostics_pcr_genexpert'."""
    return "cat_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_registry():
    return json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))


def save_registry(reg):
    COUNTRIES_FILE.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def country(iso3):
    """Settings for one country: its own entry over the shared defaults."""
    reg = load_registry()
    if iso3 not in reg["countries"]:
        raise KeyError(f"{iso3} is not registered; add it with: python -m procurement_rag.pipeline --add-country {iso3}")
    return {**reg["defaults"], **reg["countries"][iso3], "iso3": iso3}


def country_dir(iso3):
    d = DATA / iso3
    d.mkdir(parents=True, exist_ok=True)
    return d
