"""Analytics behind the app's planning, comparison and estimate views.

Pure pandas over the gold tables, so every figure on screen can be traced to rows and tested without Streamlit:

  * plan_overview      what to expect when buying an equipment type in a country (duration, value, method, suppliers)
  * country_compare    the same indicators side by side for several countries
  * supplier_table     one row per supplier: contracts, value, reach and the competition seen in their tenders
  * build_package      turns a few planning inputs into the feature dict models.score() and copilot.build_brief() need
  * duration_estimate  model P50/P80 next to the historical benchmark and the model's measured test accuracy
  * linkify / evidence_rows   clickable citations and a source table for chatbot answers

Small samples are never hidden: every statistic carries its n, and benchmarks fall back to a wider scope (all countries,
then all methods) when the narrow one has too few tenders, saying which scope was used.
"""
import json
import re

import numpy as np
import pandas as pd

from .common import GOLD, lib

MIN_N = 15            # fewest tenders for a benchmark without a wider-scope fallback (same as the copilot)
MIN_TIMED = 5         # fewest timed tenders for a country-level duration in the comparison
PROJECT_URL = "https://projects.worldbank.org/en/projects-operations/project-detail/{}"


def km(tenders):
    """Censoring-aware median and P80 days from invitation to award (still-open tenders count as censored)."""
    t = tenders[tenders.cycle_days.notna() & (tenders.cycle_days >= 0)]
    if t.empty:
        return {"n": 0, "n_awarded": 0, "median": None, "p80": None}
    r = lib.kaplan_meier(t.cycle_days.tolist(), t.award_observed.astype(bool).tolist())
    return {"n": r["n"], "n_awarded": r["n_events"], "median": r["median"], "p80": r["p80"]}


def project_url(pid):
    return PROJECT_URL.format(pid) if isinstance(pid, str) and pid else None


class Insights:
    def __init__(self, contracts, tenders, projects, country_name=None, metrics=None):
        self.c = contracts[contracts.in_domain].copy()
        self.c["country_iso3"] = self.c["country_iso3"].fillna("REG")
        self.t_all = tenders
        self.t = tenders[tenders.in_domain].copy()
        self.projects = projects
        self.country_name = country_name or {}
        self.metrics = metrics or {}
        self.project_name = dict(zip(projects.project_id, projects.project_name))

    @classmethod
    def load(cls, gold=GOLD, country_name=None):
        metrics_path = gold / "model_metrics.json"
        return cls(pd.read_parquet(gold / "fact_contract.parquet"), pd.read_parquet(gold / "fact_tender.parquet"),
                   pd.read_parquet(gold / "dim_project.parquet"), country_name,
                   json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {})

    # ── choices for the dropdowns ────────────────────────────────────────────────────────────────
    def name(self, iso3):
        return self.country_name.get(iso3, iso3)

    def countries(self):
        """Countries with lab/diagnostic contracts, most contracts first."""
        return [k for k in self.c.country_iso3.value_counts().index if k != "REG"]

    def item_types(self):
        return self.c.item_type.value_counts().index.tolist()

    def methods(self):
        """Goods methods seen in the lab tenders, most common first, with their full names."""
        t = self.t[self.t.procurement_category == "Goods"]
        names = t.groupby("method_code").procurement_method.first()
        return [(m, names[m]) for m in t.method_code.value_counts().index if m in names]

    # ── scoped selections with fallback ──────────────────────────────────────────────────────────
    def _tenders(self, country=None, item_type=None, method=None):
        t = self.t
        if country:
            t = t[t.country_iso3 == country]
        if item_type:
            t = t[t.item_type == item_type]
        if method:
            t = t[t.method_code == method]
        return t

    def duration(self, country, item_type, method=None):
        """Most specific benchmark with at least MIN_N timed tenders: country+type+method → country+type → type+method
        → type → all lab tenders. Returns the KM figures plus a plain-language scope."""
        n = self.name(country)
        levels = [(dict(country=country, item_type=item_type, method=method), f"{item_type}, {method}, {n}"),
                  (dict(country=country, item_type=item_type), f"{item_type} in {n} (all methods)"),
                  (dict(item_type=item_type, method=method), f"{item_type}, {method}, all countries"),
                  (dict(item_type=item_type), f"{item_type}, all countries and methods"),
                  (dict(country=country), f"all lab and diagnostic tenders in {n}"),
                  ({}, "all lab and diagnostic tenders")]
        for kw, scope in levels:
            if "method" in kw and not method:
                continue
            r = km(self._tenders(**kw))
            if r["n"] >= MIN_N and r["median"] is not None:
                return {**r, "scope": scope, "narrowest": kw == levels[0][0]}
        return {"n": 0, "n_awarded": 0, "median": None, "p80": None, "scope": "no comparable tenders", "narrowest": False}

    def _contracts(self, country=None, item_type=None):
        c = self.c
        if country:
            c = c[c.country_iso3 == country]
        if item_type:
            c = c[c.item_type == item_type]
        return c

    def value_stats(self, country, item_type):
        """Typical contract value; falls back to all countries when the country has fewer than 5 contracts."""
        c, scope = self._contracts(country, item_type), f"{item_type} in {self.name(country)}"
        if len(c) < 5:
            c, scope = self._contracts(None, item_type), f"{item_type}, all countries"
        if c.empty:
            return {"n": 0, "scope": scope}
        a = c.amount_usd
        return {"n": len(c), "scope": scope, "median": float(a.median()), "p25": float(a.quantile(0.25)),
                "p75": float(a.quantile(0.75)), "total": float(a.sum()), "suppliers": int(c.supplier_key.nunique())}

    def method_mix(self, country, item_type):
        t = self._tenders(country, item_type)
        if len(t) < 5:
            t = self._tenders(None, item_type)
        return (t.procurement_method.value_counts(normalize=True).rename("share").to_frame()
                .assign(tenders=t.procurement_method.value_counts()))

    def competition(self, country, item_type):
        """Bidder counts and single-bid share in comparable tenders (with fallback)."""
        t, scope = self._tenders(country, item_type), f"{item_type} in {self.name(country)}"
        t = t[t.n_bidders.notna()]
        if len(t) < MIN_N:
            t, scope = self._tenders(None, item_type), f"{item_type}, all countries"
            t = t[t.n_bidders.notna()]
        if t.empty:
            return {"n": 0, "scope": scope}
        return {"n": len(t), "scope": scope, "median_bidders": float(t.n_bidders.median()),
                "single_bid_share": float(t.single_bid_flag.mean())}

    def top_suppliers(self, country=None, item_type=None, k=8):
        c = self._contracts(country, item_type)
        if c.empty:
            return pd.DataFrame(columns=["supplier", "supplier country", "contracts", "value (US$)"])
        g = (c.groupby("supplier_key").agg(supplier=("supplier_name", "first"), supplier_country=("supplier_country", "first"),
                                           contracts=("wb_contract_number", "nunique"), value=("amount_usd", "sum"))
             .sort_values("value", ascending=False).head(k))
        return g.rename(columns={"supplier_country": "supplier country", "value": "value (US$)"}).reset_index(drop=True)

    def examples(self, country, item_type, k=6):
        """Recent comparable contracts (the country first, then elsewhere), with a link to the project page."""
        c = self._contracts(country, item_type)
        if len(c) < k:
            c = pd.concat([c, self._contracts(None, item_type).drop(c.index, errors="ignore")])
        c = c.sort_values("signing_date", ascending=False).head(k)
        return pd.DataFrame({
            "contract": c.wb_contract_number.astype(str), "signed": c.signing_date.dt.date,
            "country": c.country_iso3.map(self.name), "supplier": c.supplier_name,
            "value (US$)": c.amount_usd.round(0), "method": c.procurement_method, "description": c.description,
            "project": c.project_id + " · " + c.project_id.map(self.project_name).fillna(""),
            "project page": c.project_id.map(project_url)}).reset_index(drop=True)

    def plan_overview(self, country, item_type, method=None):
        return {"duration": self.duration(country, item_type, method), "value": self.value_stats(country, item_type),
                "competition": self.competition(country, item_type), "methods": self.method_mix(country, item_type),
                "suppliers": self.top_suppliers(country, item_type), "examples": self.examples(country, item_type)}

    # ── country comparison ───────────────────────────────────────────────────────────────────────
    def country_compare(self, countries, item_type=None):
        rows = []
        for iso3 in countries:
            c, t = self._contracts(iso3, item_type), self._tenders(iso3, item_type)
            # only tenders with an invitation notice have a measurable time to award, and few lab tenders do: time it
            # over every goods tender in the same health projects and blank it out below MIN_TIMED tenders
            goods = self.t_all[(self.t_all.country_iso3 == iso3) & (self.t_all.procurement_category == "Goods")]
            d = km(goods)
            if d["n"] < MIN_TIMED:
                d["median"] = d["p80"] = None
            bid = t[t.n_bidders.notna()]
            top_m = t.procurement_method.value_counts()
            top_s = c.groupby("supplier_name").amount_usd.sum().sort_values(ascending=False)
            rows.append({
                "country": self.name(iso3), "contracts": len(c), "value (US$M)": round(c.amount_usd.sum() / 1e6, 1),
                "median contract (US$)": round(c.amount_usd.median()) if len(c) else None,
                "suppliers": c.supplier_key.nunique(), "projects": c.project_id.nunique(), "tenders": len(t),
                "median days to award": d["median"], "P80 days to award": d["p80"], "timed goods tenders (n)": d["n"],
                "single-bid share": round(float(bid.single_bid_flag.mean()), 2) if len(bid) else None,
                "most common method": f"{top_m.index[0]} ({top_m.iloc[0] / len(t):.0%})" if len(t) else None,
                "largest supplier": top_s.index[0] if len(top_s) else None})
        return pd.DataFrame(rows)

    def method_share_by_country(self, countries, item_type=None):
        t = self._tenders(item_type=item_type)
        t = t[t.country_iso3.isin(countries)]
        p = pd.crosstab(t.country_iso3.map(self.name), t.procurement_method, normalize="index")
        return p.round(3)

    def value_by_type(self, countries):
        c = self.c[self.c.country_iso3.isin(countries)]
        return (c.pivot_table(index="item_type", columns=c.country_iso3.map(self.name), values="amount_usd", aggfunc="sum",
                              fill_value=0) / 1e6).round(2)

    # ── supplier comparison ──────────────────────────────────────────────────────────────────────
    def supplier_table(self, item_type=None, country=None):
        """One row per supplier. Competition indicators come from the tenders their contracts were linked to
        (about three quarters of lab contracts link to a tender notice)."""
        c = self._contracts(country, item_type)
        if c.empty:
            return pd.DataFrame()
        tl = self.t_all.drop_duplicates("tender_id").set_index("tender_id")[["n_bidders", "single_bid_flag", "cycle_days"]]
        c = c.join(tl, on="tender_id")
        g = c.groupby("supplier_key").agg(
            supplier=("supplier_name", "first"), based_in=("supplier_country", "first"),
            contracts=("wb_contract_number", "nunique"), value_usd=("amount_usd", "sum"),
            median_contract_usd=("amount_usd", "median"), countries_served=("country_iso3", "nunique"),
            projects=("project_id", "nunique"), equipment_types=("item_type", "nunique"),
            first_year=("signing_date", lambda s: s.dt.year.min()), last_year=("signing_date", lambda s: s.dt.year.max()),
            linked_tenders=("n_bidders", "count"), median_bidders=("n_bidders", "median"),
            single_bid_share=("single_bid_flag", "mean"), median_days_to_award=("cycle_days", "median"))
        g["single_bid_share"] = g.single_bid_share.where(g.linked_tenders > 0).round(2)
        return g.sort_values("value_usd", ascending=False).reset_index()

    def supplier_detail(self, supplier_key, k=10):
        c = self.c[self.c.supplier_key == supplier_key].sort_values("signing_date", ascending=False)
        return {"by_country": c.groupby(c.country_iso3.map(self.name)).amount_usd.agg(["count", "sum"])
                .sort_values("sum", ascending=False),
                "by_type": c.groupby("item_type").amount_usd.agg(["count", "sum"]).sort_values("sum", ascending=False),
                "contracts": self.examples_for(c.head(k))}

    def examples_for(self, c):
        return pd.DataFrame({"contract": c.wb_contract_number.astype(str), "signed": c.signing_date.dt.date,
                             "country": c.country_iso3.map(self.name), "value (US$)": c.amount_usd.round(0),
                             "description": c.description, "project page": c.project_id.map(project_url)})

    # ── duration estimate ────────────────────────────────────────────────────────────────────────
    def duration_estimate(self, package, scores):
        """Model P50/P80 next to the benchmark for the same tender profile, plus the model's measured accuracy."""
        bench = self.duration(package["country_iso3"], package["item_type"], package["method_code"])
        cyc = self.metrics.get("cycle", {})
        sb = self.metrics.get("single_bid", {})
        comparable = self._tenders(package["country_iso3"], package["item_type"])
        comparable = comparable[comparable.award_observed & comparable.observed_days.notna()]
        if len(comparable) < MIN_N:
            comparable = self._tenders(None, package["item_type"])
            comparable = comparable[comparable.award_observed & comparable.observed_days.notna()]
        return {"p50": scores["cycle_p50"], "p80": scores["cycle_p80"], "benchmark": bench,
                "comparable_days": comparable.observed_days.astype(float).tolist(),
                "model_mae": cyc.get("mae_days_model_p50"), "median_mae": cyc.get("mae_days_global_median"),
                "benchmark_mae": cyc.get("mae_days_km_benchmark"), "p80_coverage": cyc.get("p80_coverage"),
                "n_test": cyc.get("n_test"), "split_date": self.metrics.get("split_date"),
                "single_bid_prob": scores.get("single_bid_prob"), "single_bid_base": scores.get("single_bid_base_rate"),
                "single_bid_auc": sb.get("auc"), "amendment_auc": self.metrics.get("amendment", {}).get("auc")}


# ── planning package ─────────────────────────────────────────────────────────────────────────────
def build_package(country, item_type, method, contract_usd, *, description=None, review_type="Post",
                  planned_open_date=None, bid_window_days=30, category="Goods", major_sector="Health", projects=None):
    """The inputs a planner knows before a tender opens, in the shape models.score() and copilot.build_brief() use.
    Project-level features (environment category, commitment) take the country's typical health project when known."""
    description = description or f"Supply and installation of {item_type.lower()} for public health laboratories"
    od = pd.Timestamp(planned_open_date) if planned_open_date else None
    pkg = {"description": description, "procurement_category": category, "method_code": method,
           "country_iso3": country, "item_type": item_type, "open_notice_type": "Invitation for Bids",
           "review_type": review_type, "state": None, "contract_usd": float(contract_usd),
           "log_contract_usd": float(np.log1p(contract_usd)), "bid_window_days": bid_window_days,
           "open_month": od.month if od is not None else np.nan, "major_sector": major_sector,
           "planned_open_date": od.strftime("%Y-%m-%d") if od is not None else None,
           "desc_words": len(description.split()), "kw_works_civil": int(category == "Works"),
           "kw_supply": int(bool(re.search(r"supply|deliver|procure|purchase|equipment", description, re.I))),
           "kw_lot": int(bool(re.search(r"\blot\b|lots", description, re.I)))}
    if projects is not None:
        p = projects[(projects.country_iso3 == country) & (projects.major_sector == major_sector)]
        if len(p):
            pkg["env_category"] = p.env_category.mode().iloc[0] if p.env_category.notna().any() else None
            pkg["es_risk_rating"] = p.es_risk_rating.mode().iloc[0] if p.es_risk_rating.notna().any() else None
            if p.commitment_usd.notna().any():
                pkg["log_commitment"] = float(np.log1p(p.commitment_usd.median()))
    return pkg


# ── chatbot transparency ─────────────────────────────────────────────────────────────────────────
KIND = {"C": "Contract", "B": "Benchmark", "L": "Lesson", "P": "Report passage", "R": "Rating", "D": "Derived figure",
        "T": "Past tender", "M": "Model", "G": "Graph", "S": "Source document"}


def evidence_link(e):
    """Best link for one evidence record: the document (opened at its page when it is a PDF) or the project page."""
    url = e.get("url")
    if url:
        return url + (f"#page={e['page']}" if e.get("page") and str(url).lower().endswith(".pdf") else "")
    return project_url(e.get("project_id"))


def linkify(answer_md, evidence):
    """`C1`-style citations in the answer become links to their source, so a reader can open the evidence."""
    answer_md = (answer_md or "").replace("$", "\\$")      # "US$1 ... US$2" must not render as LaTeX

    def sub(m):
        eid = m.group(1)
        e = evidence.get(eid)
        if not e:
            return m.group(0)
        link = evidence_link(e)
        return f"[\\[{eid}\\]]({link})" if link else f"**[{eid}]**"
    return re.sub(r"`([A-Z]\d+)`", sub, answer_md)


def evidence_rows(evidence, project_name=None):
    """A table of the evidence behind an answer: what it is, the project, page, contract number and link."""
    project_name = project_name or {}
    rows = []
    for e in evidence.values():
        pid = e.get("project_id")
        rows.append({"id": e["id"], "type": KIND.get(e["id"][:1], "Evidence"),
                     "project": f"{pid} · {project_name.get(pid, '')}".strip(" ·") if pid else "",
                     "page": e.get("page") or "", "contract": e.get("contract") or "",
                     "source": e.get("source", ""), "excerpt": str(e.get("text", ""))[:220],
                     "link": evidence_link(e) or ""})
    return pd.DataFrame(rows)


# ── planning questions in free text ──────────────────────────────────────────────────────────────
PLANNING = re.compile(r"\bplan(ning)?\b|\bplan to\b|going to (buy|purchase|procure)|want to (buy|purchase|procure)|"
                      r"\bwe need to (buy|purchase|procure)|what should (i|we) expect|what to expect", re.I)


def planning_request(question, parsed, require_intent=True):
    """{country, item_type} when a question describes a purchase to plan in one country for one equipment type
    (the chatbot's parser supplies the entities); None otherwise."""
    if require_intent and not PLANNING.search(question or ""):
        return None
    countries, items = parsed.get("countries") or [], parsed.get("item_types") or []
    if len(countries) != 1 or len(items) != 1 or not parsed.get("domain_only", True):
        return None
    return {"country": countries[0], "item_type": items[0]}


# ── architecture (the "How it's built" view) ─────────────────────────────────────────────────────
ARCHITECTURE_DOT = """digraph A { rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.45;
node [shape=box, style="rounded,filled", fillcolor="#f6f8fa", fontname="Helvetica", fontsize=10];
edge [fontname="Helvetica", fontsize=9, color="#57606a"];
api [label="World Bank public APIs\\ncontract awards · procurement notices\\nprojects · project documents (PDF)\\nCPIA / WGI indicators", fillcolor="#eaeef2"];
lib [label="notebooks/20_proc_lib.py\\nshared taxonomy, parsers,\\nentity resolution, guardrails", fillcolor="#ede4fb"];
subgraph cluster_local { label="Deployed app (local Python, served on Streamlit Community Cloud)"; style=rounded; color="#0969da"; fontname="Helvetica"; fontsize=11;
  l_ing [label="procurement_demo\\ningest → build", fillcolor="#dbeafe"];
  l_gold [label="Gold Parquet tables\\n+ knowledge-graph edges", fillcolor="#dbeafe"];
  l_models [label="scikit-learn models\\nKM benchmarks, quantile, risk", fillcolor="#dbeafe"];
  l_rag [label="procurement_rag\\nChromaDB + bge-small\\nBM25 + rank fusion", fillcolor="#dbeafe"];
  l_app [label="Streamlit app\\nchat · plan · estimate\\ncompare · documents", fillcolor="#c8e1ff"];
  l_ing -> l_gold -> l_models -> l_app; l_gold -> l_app; l_rag -> l_app; }
subgraph cluster_dbx { label="Databricks pipeline (separate batch deployment, notebooks 00, 20-34)"; style=rounded; color="#cf222e"; fontname="Helvetica"; fontsize=11;
  d_bronze [label="Bronze Delta\\nraw pulls, PDFs in a volume", fillcolor="#fde7c8"];
  d_silver [label="Silver Delta\\nclassified, resolved, linked", fillcolor="#fde7c8"];
  d_gold [label="Gold Delta · Unity Catalog\\nkg_edges, lessons, ratings", fillcolor="#fde7c8"];
  d_vs [label="Mosaic AI Vector Search\\nhybrid, gte-large", fillcolor="#fde7c8"];
  d_ml [label="MLflow + UC registry\\nchampion gate → Lakeflow\\ndrift + retrain job", fillcolor="#fde7c8"];
  d_app [label="Databricks App\\ndocument Q&A", fillcolor="#fbd3b0"];
  d_bronze -> d_silver -> d_gold -> d_vs -> d_app; d_gold -> d_ml; }
site [label="GitHub Pages\\nstatic chatbot + report", fillcolor="#d1f0d6"];
api -> l_ing; api -> l_rag [label="PDFs"]; api -> d_bronze;
lib -> l_ing [style=dashed, label="imported"]; lib -> d_silver [style=dashed, label="%run"];
l_gold -> site [label="exported JSON"];
}"""
