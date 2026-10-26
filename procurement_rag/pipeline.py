"""Run the document pipeline for one or more countries. Every step is incremental: documents already downloaded,
parsed or embedded are skipped, so adding a country (or re-running for new documents) only does the new work.

    python -m procurement_rag.pipeline --countries KEN IND          # fetch → process → index
    python -m procurement_rag.pipeline --add-country NGA            # register a country (codes and name from the World Bank)
    python -m procurement_rag.pipeline --countries NGA              # then build it; Kenya and India are untouched
    python -m procurement_rag.pipeline --countries KEN --steps index   # one step only
"""
import argparse

from .config import load_registry, save_registry
from .fetch import get_json


def add_country(iso3):
    """Register a country from the World Bank country list (ISO-2 code and name); shared defaults do the rest."""
    reg = load_registry()
    iso3 = iso3.upper()
    if iso3 in reg["countries"]:
        print(f"{iso3} already registered")
        return
    d = get_json(f"https://api.worldbank.org/v2/country/{iso3}", {"format": "json"})
    c = d[1][0]
    reg["countries"][iso3] = {"iso2": c["iso2Code"], "name": c["name"].strip(), "extra_projects": []}
    save_registry(reg)
    print(f"registered {iso3} ({c['name'].strip()}); build it with: python -m procurement_rag.pipeline --countries {iso3}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--countries", nargs="*", default=[])
    ap.add_argument("--add-country")
    ap.add_argument("--steps", nargs="*", default=["fetch", "process", "index"], choices=["fetch", "process", "index"])
    a = ap.parse_args()
    if a.add_country:
        add_country(a.add_country)
    for iso3 in [c.upper() for c in a.countries]:
        if "fetch" in a.steps:
            from .fetch import fetch_country
            fetch_country(iso3)
        if "process" in a.steps:
            from .process import process_country
            process_country(iso3)
        if "index" in a.steps:
            from .index import index_country
            index_country(iso3)


if __name__ == "__main__":
    main()
