"""LabProcure REST API: the lifecycle planner, what-if optimizer, pre-mortem and chatbot over HTTP.

    uvicorn api.main:app --reload            # http://127.0.0.1:8000/docs
    docker build -t labprocure-api . && docker run -p 8000:8000 labprocure-api

Every figure carries its basis (Derived with n, Model with tested accuracy, Evidence with a link, or Missing), the same
contract the Streamlit app shows. Optional API-key check: set LABPROCURE_API_KEY and send it as X-API-Key.
"""
import os
import sys
from dataclasses import asdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from procurement_demo.chatbot import ProcurementChat  # noqa: E402
from procurement_demo.common import GOLD  # noqa: E402
from procurement_demo.insights import Insights, build_package  # noqa: E402
from procurement_demo.lifecycle import Lifecycle  # noqa: E402
from procurement_demo.models import score  # noqa: E402

app = FastAPI(title="LabProcure API", version="1.0",
              description="Procurement intelligence for laboratory and diagnostic equipment in World Bank health projects.")


@lru_cache(maxsize=1)
def engine():
    bot = ProcurementChat()
    ins = Insights.load(country_name=bot.country_name)
    return bot, ins, Lifecycle(ins, bot.kb, GOLD)


def api_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("LABPROCURE_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="missing or wrong X-API-Key")


class Package(BaseModel):
    country: str = Field(..., examples=["KEN"], description="ISO3 country code")
    item_type: str = Field(..., examples=["Laboratory analyzers"])
    method: str = Field("RFB", examples=["RFB"], description="procurement method code")
    value_usd: float = Field(100_000, gt=0)
    open_date: date | None = Field(None, description="planned invitation date (default: today + 90 days)")
    review: str = Field("Post", pattern="^(Post|Prior)$")
    bid_window_days: int = Field(30, ge=1, le=180)


class Question(BaseModel):
    question: str = Field(..., min_length=3, examples=["Which supplier won the cheapest GeneXpert or PCR contract?"])


def _plain(d):
    """numpy / pandas scalars → JSON-safe Python values, floats rounded to 3 places."""
    out = {}
    for k, v in d.items():
        v = v.item() if hasattr(v, "item") else v
        out[k] = round(v, 3) if isinstance(v, float) else v
    return out


def _check(p: Package):
    _, ins, _ = engine()
    if p.country not in ins.countries():
        raise HTTPException(404, f"no lab or diagnostic contracts for country {p.country}")
    if p.item_type not in ins.item_types():
        raise HTTPException(404, f"unknown equipment type; one of: {ins.item_types()}")
    return p.open_date or (date.today() + timedelta(days=90))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/options", dependencies=[Depends(api_key)])
def options():
    _, ins, _ = engine()
    return {"countries": {k: ins.name(k) for k in ins.countries()}, "item_types": ins.item_types(),
            "methods": dict(ins.methods())}


@app.post("/lifecycle", dependencies=[Depends(api_key)])
def lifecycle(p: Package):
    od = _check(p)
    _, ins, lc = engine()
    pkg = build_package(p.country, p.item_type, p.method, p.value_usd, review_type=p.review, planned_open_date=od,
                        bid_window_days=p.bid_window_days, projects=ins.projects)
    s = score(pkg)
    tl = lc.timeline(p.country, p.item_type, od, s)
    return {"scores": _plain({k: v for k, v in s.items() if "amendment" not in k}),
            "timeline": [{**r, "start": r["start"].date().isoformat(), "end": r["end"].date().isoformat()}
                         for r in tl.to_dict("records")],
            "figures": [{**asdict(f), "badge": f.badge()} for f in lc.figures(p.country, p.item_type, p.method, p.value_usd, s)]}


@app.post("/optimize", dependencies=[Depends(api_key)])
def optimize(p: Package, top: int = 5):
    od = _check(p)
    _, _, lc = engine()
    df, base = lc.optimize(p.country, p.item_type, p.method, p.value_usd, od, p.review, p.bid_window_days)
    return {"yours": None if base is None else _plain(base.to_dict()),
            "best": [_plain(r) for r in df[df["within past use"]].head(top).to_dict("records")]}


@app.post("/premortem", dependencies=[Depends(api_key)])
def premortem(p: Package):
    od = _check(p)
    _, ins, lc = engine()
    s = score(build_package(p.country, p.item_type, p.method, p.value_usd, planned_open_date=od, projects=ins.projects))
    risks, lessons = lc.premortem(p.country, p.item_type, s)
    return {"risks": [_plain(r) for r in risks.to_dict("records")], "lessons": lessons}


@app.post("/ask", dependencies=[Depends(api_key)])
def ask(q: Question):
    bot, _, _ = engine()
    a = bot.ask(q.question)
    return {"route": a["route"], "answer_md": a["answer_md"], "statements": a["statements"],
            "evidence": a["evidence"], "removed_by_verifier": a["guardrail_log"]}
