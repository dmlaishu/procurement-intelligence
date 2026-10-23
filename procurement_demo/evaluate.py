"""Step 5 - evaluation: models, retrieval, guardrails, conflict resolution (mirrors notebook 29).

    python -m procurement_demo.evaluate
"""
import json
import random
import re

import numpy as np
import pandas as pd

from .common import GOLD, OUT, lib
from .copilot import build_brief, verify, Evidence
from .models import NUM_FEATURES, CAT_FEATURES, SPLIT_DATE
from .retrieval import KnowledgeBase

TOPIC_QUERIES = {   # risk topic → query a procurement officer might type
    "delay": "procurement delays slowed implementation and contract award took longer than planned",
    "low_competition": "few bidders responded, bids were non-responsive and the package had to be re-tendered",
    "capacity": "weak implementing agency capacity and staff turnover in the project implementation unit",
    "cost_overrun": "bids exceeded the cost estimate and prices escalated",
    "safeguards": "land acquisition and resettlement delayed the start of civil works",
    "fiduciary": "financial management, audit findings and fiduciary compliance problems",
    "specification": "poor technical specifications and design changes during implementation",
}


def retrieval_eval(kb):
    res = {}
    # (a) topic precision@5 / recall@20 against the lessons' risk labels
    per_topic = {}
    labels = kb.lessons.risk_category.fillna("")
    for topic, q in TOPIC_QUERIES.items():
        relevant = set(kb.lessons.index[labels.str.contains(topic)])
        if len(relevant) < 3:
            continue
        s = kb.lesson_bm25.scores(q) * np.where(kb.lessons.procurement_related.values, 1.5, 0.6)
        ranked = list(np.argsort(-s))
        p5 = len(set(ranked[:5]) & relevant) / 5
        p10 = len(set(ranked[:10]) & relevant) / 10
        # recall@20 normalised by what is achievable in 20 slots (plain recall is capped at 20/|relevant|)
        r20 = len(set(ranked[:20]) & relevant) / min(20, len(relevant))
        per_topic[topic] = {"precision@5": round(p5, 2), "precision@10": round(p10, 2), "recall@20": round(r20, 2),
                            "n_relevant": len(relevant), "random_precision": round(len(relevant) / len(kb.lessons), 2)}
    res["topic"] = per_topic
    if per_topic:
        res["topic_macro"] = {k: round(float(np.mean([v[k] for v in per_topic.values()])), 3)
                              for k in ("precision@5", "precision@10", "recall@20", "random_precision")}

    # (b) structured → unstructured link: does a project's own procurement history retrieve its own evaluation?
    hits, rr, n = 0, 0.0, 0
    for pid, grp in kb.lessons.groupby("project_id"):
        t = kb.tenders[(kb.tenders.project_id == pid) & kb.tenders.description.notna()]
        if len(t) < 3:
            continue
        q = " ".join(t.description.head(8)) + " " + " ".join(t.procurement_category.dropna().unique())
        s = kb.chunk_bm25.scores(q) * kb.chunks.section_kind.isin(["lessons", "key_factors", "design", "results"]).values
        ranked_docs = []
        for i in np.argsort(-s)[:200]:
            d = kb.chunks.project_id.iloc[i]
            if d not in ranked_docs:
                ranked_docs.append(d)
            if len(ranked_docs) == 10:
                break
        n += 1
        if pid in ranked_docs:
            hits += 1
            rr += 1 / (ranked_docs.index(pid) + 1)
    res["project_link"] = {"queries": n, "hit@10": round(hits / max(n, 1), 3), "mrr@10": round(rr / max(n, 1), 3),
                           "n_projects_with_docs": int(kb.chunks.project_id.nunique())}
    return res


def corrupt(statements, ev, rng):
    """Red-team the verifier with three classic hallucination patterns injected into real statements."""
    cited = [s for s in statements if s["citations"]]
    if not cited:
        return []
    s = rng.choice(cited)
    # fake claim number: change a number in the claim (not in the attribution) or append a fabricated figure
    claim_num = re.search(r"(?<!p\. )(?<![P\d])\b\d{2,}\b(?![^()]*\))", s["text"])
    fake_num = (s["section"], s["text"][:claim_num.start()] + str(int(claim_num.group()) * 3 + 17) + s["text"][claim_num.end():]
                if claim_num else s["text"] + " This typically adds 245 days.", s["citations"])
    fake_quote = (s["section"], "“Contractors consistently delivered ahead of schedule across all lots.”", s["citations"])
    uncited = (s["section"], "Framework agreements always reduce procurement time by half.", [])
    unknown = (s["section"], s["text"], ["L999"])
    out = [fake_num, fake_quote, uncited, unknown]
    attributed = [x for x in cited if re.search(r"\bp\. \d+\)", x["text"])]
    if attributed:   # fake attribution: wrong page number on a quoted lesson / passage
        a = rng.choice(attributed)
        out.append((a["section"], re.sub(r"\bp\. (\d+)\)", lambda m: f"p. {int(m.group(1)) + 41})", a["text"]), a["citations"]))
    return out


def guardrail_eval(kb, n=25, seed=7):
    feats = pd.read_parquet(GOLD / "tender_features.parquet")
    test = feats[(feats.open_date >= SPLIT_DATE) & feats.award_observed & feats.description.notna()
                 & feats.procurement_category.isin(["Works", "Goods", "Consultant Services", "Non-consulting Services"])]
    test = test.sample(min(n, len(test)), random_state=seed)
    rng = random.Random(seed)
    kept = dropped = cited = abst = 0
    injected = caught = 0
    covered, abs_err = [], []
    examples = []
    for _, r in test.iterrows():
        pkg = {c: r[c] for c in CAT_FEATURES + NUM_FEATURES if c in r}
        pkg.update({"description": r.description, "planned_open_date": str(r.open_date.date())})
        brief = build_brief(pkg, kb)
        kept += len(brief["statements"])
        dropped += len(brief["guardrail_log"])
        cited += sum(1 for s in brief["statements"] if s["citations"])
        abst += sum(1 for s in brief["statements"] if s["abstention"])
        covered.append(r.observed_days <= brief["scores"]["cycle_p80"])
        abs_err.append(abs(r.observed_days - brief["scores"]["cycle_p50"]))
        ev = Evidence()
        ev.items = brief["evidence"]
        bad = corrupt(brief["statements"], ev, rng)
        k2, log2 = verify(bad, ev)
        injected += len(bad)
        caught += len(log2)
        if len(examples) < 2:
            examples.append({"package": r.description[:100], "statements": len(brief["statements"]),
                             "dropped": brief["guardrail_log"], "injected_caught": log2})
    return {
        "briefs": len(test), "statements_kept": kept, "statements_dropped_by_verifier": dropped,
        "kept_with_citation_share": round(cited / max(kept, 1), 3), "abstentions": abst,
        "injected_hallucinations": injected, "caught": caught, "catch_rate": round(caught / max(injected, 1), 3),
        "p80_coverage_on_briefs": round(float(np.mean(covered)), 3) if covered else None,
        "mae_days_on_briefs": round(float(np.mean(abs_err)), 1) if abs_err else None,
        "examples": examples,
    }


def main():
    kb = KnowledgeBase(GOLD)
    build = json.loads((GOLD / "build_stats.json").read_text(encoding="utf-8"))
    models = json.loads((GOLD / "model_metrics.json").read_text(encoding="utf-8"))
    all_lessons = pd.read_parquet(GOLD / "lessons.parquet")
    ratings = pd.read_parquet(GOLD / "project_ratings_resolved.parquet")
    results = {
        "build": build,
        "models": models,
        "lesson_extraction": {"extracted": len(all_lessons), "quote_verified": int(all_lessons.quote_verified.sum()),
                              "verification_rate": round(float(all_lessons.quote_verified.mean()), 3) if len(all_lessons) else None,
                              "procurement_related": int(all_lessons.procurement_related.sum())},
        "conflicts": {"projects_with_rating_evidence": len(ratings), "with_conflict": int(ratings.conflict.sum()),
                      "examples": ratings[ratings.conflict].head(3)[["project_id", "resolved_rating", "resolved_from",
                                                                     "worst_isr", "trajectory"]].to_dict("records")},
        "retrieval": retrieval_eval(kb),
        "guardrails": guardrail_eval(kb),
    }
    if (GOLD / "fact_contract.parquet").exists():
        from .chatbot import chatbot_eval
        results["chatbot"] = chatbot_eval()
    (OUT / "eval_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items() if k not in ("build",)}, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
