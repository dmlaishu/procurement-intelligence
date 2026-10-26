"""Settings, paths and the country registry for the document RAG pipeline.

Countries live in countries.json. Each entry only needs ISO codes and a name; everything else (which projects, which
documents, how many) comes from the shared defaults, so a new country is one entry, or one command:

    python -m procurement_rag.pipeline --add-country NGA
"""
import importlib.util
import json
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

# Categories for the dropdown (ten lab and diagnostic equipment types + the procurement process) and their metadata
# column names live in 20_proc_lib, so the Databricks notebooks tag passages exactly the same way.
CATEGORIES = lib.RAG_CATEGORIES
category_key = lib.rag_category_key


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
