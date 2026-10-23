"""Run the whole local prototype end to end on public World Bank data.

    python -m procurement_demo.run_all            # ingest (cached) → build → models → copilot demo → evaluate → report
    python -m procurement_demo.run_all --refresh  # re-download all sources first
    python -m streamlit run procurement_demo/app.py         # then chat with the knowledge base

The scope (which domain is ingested) is set by PROC_SCOPE; default health_lab. See procurement_demo/common.py.
"""
import sys
import time

from . import build, copilot, evaluate, ingest, models, report

if __name__ == "__main__":
    for name, step in (("ingest", lambda: ingest.main(refresh="--refresh" in sys.argv)), ("build", build.main),
                       ("models", models.main), ("copilot", copilot.main), ("evaluate", evaluate.main),
                       ("report", report.main)):
        t0 = time.time()
        print(f"\n=== {name} ===")
        step()
        print(f"=== {name} done in {time.time() - t0:.0f}s ===")
