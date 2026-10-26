"""Retrieval check: each question has what a correct top-5 must contain (a project, a document type or a keyword).
Also checks that every source belongs to the selected country and records answer coverage and latency.

    python -m procurement_rag.evaluate            # writes procurement_rag/eval_results.json
"""
import json
import re
import time

from .config import PKG
from .search import DocSearch

CASES = [
    # (country, category, question, expectation kind, expected value)
    ("KEN", None, "What laboratory equipment will be procured under the COVID-19 emergency response?", "keyword", r"laborator"),
    ("KEN", None, "How were the East Africa public health laboratories strengthened?", "project", "P111556"),
    ("KEN", "Procurement process and contracts", "Which procurement methods and thresholds apply to goods?", "keyword", r"threshold|method"),
    ("KEN", None, "What lessons were learned from the laboratory networking project?", "doc_type", {"ICR", "ICRR"}),
    # the Kenya documents do not discuss buying imaging equipment: the right answer is to say so, not to guess
    ("KEN", "Diagnostic imaging", "Is x-ray or imaging equipment being purchased?", "abstain", None),
    ("IND", None, "What diagnostic equipment is planned for tuberculosis elimination?", "keyword", r"tubercul|\bTB\b"),
    ("IND", None, "How will public health laboratories be strengthened for pandemic preparedness?", "project", "P175676"),
    ("IND", None, "What were the results of the Uttar Pradesh health systems strengthening project?", "project", "P100304"),
    ("IND", "Procurement process and contracts", "What does the procurement plan say about medical equipment packages?", "doc_type", {"PP"}),
    ("IND", "Molecular diagnostics (PCR, GeneXpert)", "Where are molecular diagnostic tests such as GeneXpert or PCR used?",
     "keyword", r"gene ?xpert|PCR|CBNAAT|molecular"),
]


def met(sources, kind, value, answer=None):
    if kind == "abstain":
        return not answer
    if kind == "project":
        return any(s["project_id"] == value for s in sources)
    if kind == "doc_type":
        return any(s["doc_type"] in value for s in sources)
    return any(re.search(value, s["text"], re.I) for s in sources)


def main():
    ds = DocSearch()
    rows = []
    for iso3, cat, q, kind, value in CASES:
        t0 = time.time()
        res = ds.answer(q, iso3, cat)
        ms = int((time.time() - t0) * 1000)
        top = res["sources"][:5]
        rows.append({"country": iso3, "category": cat or "All", "question": q, "expected": f"{kind}: {sorted(value) if isinstance(value, set) else value}",
                     "hit_top5": met(top, kind, value, res["answer"]), "answer_sentences": len(res["answer"]),
                     "sources_in_country": all(s["country"] == iso3 for s in res["sources"]),
                     "latency_ms": ms, "first_source": f"{top[0]['doc_type']} {top[0]['title'][:70]}" if top else None})
        print(f"{'PASS' if rows[-1]['hit_top5'] else 'MISS'} {iso3} {ms:>5} ms  {len(res['answer'])} sentences  {q}")
    summary = {"questions": len(rows), "hit_rate_top5": round(sum(r["hit_top5"] for r in rows) / len(rows), 3),
               "answered": sum(r["answer_sentences"] > 0 for r in rows),
               "correctly_declined": sum(r["answer_sentences"] == 0 and r["hit_top5"] for r in rows),
               "all_sources_in_selected_country": all(r["sources_in_country"] for r in rows),
               "median_latency_ms": sorted(r["latency_ms"] for r in rows)[len(rows) // 2]}
    (PKG / "eval_results.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
