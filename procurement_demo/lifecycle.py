"""The procurement lifecycle for one purchase: planning → tender design → bidding & evaluation → award → delivery →
monitoring → learning, each figure tagged with how it was obtained.

Every figure is a `Fig` with a basis:
  Derived   a statistic over stored World Bank records, with the number of records it rests on (n)
  Model     a model estimate, with the model's measured accuracy on later tenders it never saw
  Evidence  a quotation or count from project documents, with a link
  Missing   the public data holds nothing for it; said plainly, never filled in from something else

On top of the figures:
  timeline()      invitation → bid deadline → award (P50 and P80) → delivery, as dates
  optimize()      what-if scan over the choices a planner controls (method, bid window, lots), scored by the models
  premortem()     how this purchase is most likely to go wrong, from comparable tenders and verified lessons
"""
from dataclasses import asdict, dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from .insights import MIN_N, build_package, km, project_url
from .models import score

STAGES = ["Planning", "Tender design", "Bidding & evaluation", "Award & contract", "Delivery", "Monitoring", "Learning"]
SATISFACTORY = {"highly satisfactory", "satisfactory", "moderately satisfactory"}


@dataclass
class Fig:
    stage: str
    figure: str
    value: str
    basis: str                      # Derived | Model | Evidence | Missing
    how: str
    n: int | None = None
    links: list = field(default_factory=list)       # [(label, url)]

    def badge(self):
        if self.basis == "Derived":
            return f"Derived · n={self.n:,}" if self.n is not None else "Derived"
        return self.basis


def _days(x):
    return "-" if x is None or pd.isna(x) else f"{int(round(x))} days"


def _usd(x):
    return f"US${x / 1e6:,.2f}M" if x >= 1e6 else f"US${x:,.0f}"


class Lifecycle:
    """Builds the lifecycle for (country, equipment type) from the Insights tables and the knowledge base."""

    def __init__(self, ins, kb, gold):
        self.ins, self.kb = ins, kb
        self.t_all, self.t = ins.t_all, ins.t
        self.projects = ins.projects
        self.ratings = pd.read_parquet(gold / "project_ratings_resolved.parquet")
        self.outcomes = pd.read_parquet(gold / "project_outcomes.parquet")
        self.lessons = kb.lessons
        self.chunks = kb.chunks

    # ── scoped tender sets, widest-needed fallback, with a label ─────────────────────────────────
    def _scoped(self, df, country, item_type, min_n=MIN_N, cond=None):
        name = self.ins.name(country)
        for label, mask in ((f"{item_type} in {name}", (df.country_iso3 == country) & (df.item_type == item_type)),
                            (f"{item_type}, all countries", df.item_type == item_type),
                            (f"all lab tenders in {name}", df.country_iso3 == country),
                            ("all lab tenders", pd.Series(True, index=df.index))):
            sel = df[mask]
            if cond is not None:
                sel = sel[cond(sel)]
            if len(sel) >= min_n:
                return sel, label
        return df.iloc[0:0], "no comparable tenders"

    def _country_projects(self, country):
        p = self.projects[(self.projects.country_iso3 == country)]
        return (p, self.ins.name(country)) if len(p) >= 5 else (self.projects, "all countries")

    # ── the figures ──────────────────────────────────────────────────────────────────────────────
    def figures(self, country, item_type, method, value_usd, scores=None):
        F = []
        name = self.ins.name(country)

        # Planning
        p, scope = self._country_projects(country)
        length = ((p.closing_date - p.approval_date).dt.days / 30.44).dropna()
        if len(length):
            F.append(Fig("Planning", "Planned project length (approval → closing)", f"{length.median():.0f} months",
                         "Derived", f"Median over health projects with lab purchases, {scope}.", len(length)))
        lab = self.t.merge(self.projects[["project_id", "approval_date"]], on="project_id", how="left")
        first = (lab.groupby("project_id").agg(c=("country_iso3", "first"), first=("open_date", "min"),
                                               appr=("approval_date", "first")).dropna())
        first["days"] = (first["first"] - first["appr"]).dt.days
        first = first[first.days >= 0]
        sel = first[first.c == country] if (first.c == country).sum() >= 5 else first
        if len(sel):
            F.append(Fig("Planning", "Project approval → first lab tender", f"{sel.days.median() / 30.44:.0f} months",
                         "Derived", f"Median per project, {'in ' + name if sel is not first else 'all countries'}: "
                                    "how long projects took to put their first lab or diagnostic tender on the market.", len(sel)))
        pp = self.chunks[(self.chunks.doc_type_code == "PP") & (self.chunks.country_iso3 == country)].drop_duplicates("doc_id")
        if len(pp):
            F.append(Fig("Planning", "Procurement plans on file", f"{len(pp)} plan(s)", "Evidence",
                         f"Procurement plans for {name} projects; they set the method thresholds and prior-review limits.",
                         links=[(r.title[:60], r.landing_url) for r in pp.head(3).itertuples()]))
        else:
            F.append(Fig("Planning", "Procurement plans on file", "Missing", "Missing",
                         f"No procurement plan for {name} was among the documents read."))

        # Tender design
        bw, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.bid_window_days.between(1, 180))
        if len(bw):
            F.append(Fig("Tender design", "Bid window (invitation → deadline)", _days(bw.bid_window_days.median()),
                         "Derived", f"Median, {scope}; middle half {bw.bid_window_days.quantile(.25):.0f}-"
                                    f"{bw.bid_window_days.quantile(.75):.0f} days.", len(bw)))
        mt, scope = self._scoped(self.t, country, item_type, min_n=5)
        if len(mt):
            vc = mt.procurement_method.value_counts()
            F.append(Fig("Tender design", "Most used method", f"{vc.index[0]} ({vc.iloc[0] / len(mt):.0%})", "Derived",
                         f"Share of tenders, {scope}.", len(mt)))
        v = self.ins.value_stats(country, item_type)
        if v["n"]:
            F.append(Fig("Tender design", "Typical contract value", f"{_usd(v['median'])}", "Derived",
                         f"Median, {v['scope']}; middle half {_usd(v['p25'])}-{_usd(v['p75'])}. "
                         f"Your estimate: {_usd(value_usd)}.", v["n"]))
        lots, scope = self._scoped(self.t, country, item_type, min_n=5)
        if len(lots):
            share = lots.description.str.contains(r"\blots?\b", case=False, na=False).mean()
            F.append(Fig("Tender design", "Tenders split into lots", f"{share:.0%}", "Derived", f"Share whose description "
                         f"names lots, {scope}.", len(lots)))
        F.append(Fig("Tender design", "Bid security, qualification and evaluation criteria", "Missing", "Missing",
                     "Not published in World Bank notices; they sit in the bidding documents, which are not public data."))

        # Bidding & evaluation
        bd, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.n_bidders.notna())
        if len(bd):
            F.append(Fig("Bidding & evaluation", "Bids received per tender", f"{bd.n_bidders.median():g} (median)", "Derived",
                         f"{scope}; {bd.single_bid_flag.mean():.0%} received a single bid.", len(bd)))
            rej = bd.rejection_reasons.fillna("").str.len().gt(0)
            reasons = (bd.rejection_reasons[rej].str.split(";").explode().str.strip().str.capitalize().value_counts())
            top = ", ".join(f"{k.lower()} ({v})" for k, v in reasons.head(3).items())
            F.append(Fig("Bidding & evaluation", "Tenders with a rejected bid", f"{rej.mean():.0%}", "Derived",
                         f"{scope}. Most common reasons: {top or 'none recorded'}.", len(bd)))
        d = self.ins.duration(country, item_type, method)
        if d["median"] is not None:
            F.append(Fig("Bidding & evaluation", "Invitation → award (history)", f"{_days(d['median'])} · P80 {_days(d['p80'])}",
                         "Derived", f"Censoring-aware (tenders still open count), {d['scope']}.", d["n"]))
        if scores:
            m = self.ins.metrics.get("cycle", {})
            F.append(Fig("Bidding & evaluation", "Invitation → award (your package)",
                         f"{_days(scores['cycle_p50'])} · P80 {_days(scores['cycle_p80'])}", "Model",
                         f"Quantile model on your inputs; on {m.get('n_test', '?')} later tenders it was off by "
                         f"{m.get('mae_days_model_p50', float('nan')):.0f} days on average and its P80 covered "
                         f"{m.get('p80_coverage', float('nan')):.0%}."))

        # Award & contract
        aw, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.award_observed.astype(bool))
        if len(aw):
            F.append(Fig("Award & contract", "Awards later amended", f"{aw.award_amended_flag.astype(bool).mean():.0%}",
                         "Derived", f"Share of awarded tenders with an amended award notice, {scope}.", len(aw)))
        rb, scope = self._scoped(self.t, country, item_type)
        if len(rb):
            F.append(Fig("Award & contract", "Re-tendered", f"{rb.rebid_flag.astype(bool).mean():.1%}", "Derived",
                         f"Share of tenders marked as a re-bid, {scope}.", len(rb)))

        # Delivery
        dl, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.contract_duration_months.between(0.01, 60))
        if len(dl):
            F.append(Fig("Delivery", "Contract duration (delivery period)", f"{dl.contract_duration_months.median():.1f} months",
                         "Derived", f"Median from award notices, {scope}; middle half "
                                    f"{dl.contract_duration_months.quantile(.25):.1f}-{dl.contract_duration_months.quantile(.75):.1f} months.",
                         len(dl)))
        F.append(Fig("Delivery", "On-time delivery, installation and after-sales", "Missing", "Missing",
                     "Not in public data: delivery and maintenance records stay with the implementing agency."))

        # Monitoring
        r = self.ratings
        if len(r):
            dipped = r.worst_isr.fillna("").str.lower().map(lambda x: bool(x) and x not in SATISFACTORY)
            F.append(Fig("Monitoring", "Projects whose supervision rating fell below satisfactory", f"{dipped.mean():.0%}",
                         "Derived", f"Of lab-purchasing projects with rating evidence (all countries); "
                                    f"{r.conflict.sum()} had sources that disagree.", len(r)))
        o = self.outcomes.dropna(subset=["closing_extension_days"])
        if len(o):
            ext = o.closing_extension_days > 0
            how = (f"median extension when extended {o.loc[ext, 'closing_extension_days'].median() / 30.44:.0f} months"
                   if ext.any() else "none of these projects moved its closing date")
            F.append(Fig("Monitoring", "Closing date extended", f"{ext.mean():.0%} of projects", "Derived",
                         f"From completion reports read so far (all countries); {how}.", len(o)))
        ro = self.outcomes.dropna(subset=["restructurings"])
        if len(ro):
            F.append(Fig("Monitoring", "Restructurings per project", f"{ro.restructurings.median():.0f} (median)", "Derived",
                         f"From completion reports (all countries); {(ro.restructurings > 0).mean():.0%} restructured at least once.",
                         len(ro)))

        # Learning
        L = self.lessons[self.lessons.quote_verified.astype(bool)]
        mine = L[L.project_id.isin(self.projects.loc[self.projects.country_iso3 == country, "project_id"])]
        F.append(Fig("Learning", "Verified lessons", f"{len(L)} in total, {len(mine)} from {name}", "Evidence",
                     "Lessons extracted from appraisal and completion reports; each quote is checked against the page.",
                     links=[(f"{x.doc_type_code} {x.project_id} p.{x.page_start}", x.landing_url)
                            for x in (mine if len(mine) else L).drop_duplicates(["doc_id", "page_start"]).head(3).itertuples()]))
        cats = L.risk_category.str.split(";").explode().value_counts()
        F.append(Fig("Learning", "Most common lesson themes", ", ".join(f"{k} ({v})" for k, v in cats.head(3).items()),
                     "Derived", "Risk categories of the verified lessons (all countries).", len(L)))
        return F

    # ── timeline ─────────────────────────────────────────────────────────────────────────────────
    def timeline(self, country, item_type, open_date, scores):
        od = pd.Timestamp(open_date)
        bw, _ = self._scoped(self.t, country, item_type, cond=lambda d: d.bid_window_days.between(1, 180))
        window = float(bw.bid_window_days.median()) if len(bw) else 30.0
        dl, _ = self._scoped(self.t, country, item_type, cond=lambda d: d.contract_duration_months.between(0.01, 60))
        months = float(dl.contract_duration_months.median()) if len(dl) else 3.0
        p50, p80 = od + timedelta(days=round(scores["cycle_p50"])), od + timedelta(days=round(scores["cycle_p80"]))
        deadline = od + timedelta(days=round(window))
        return pd.DataFrame([
            {"step": "1 Bidding period", "start": od, "end": deadline, "kind": "Derived", "note": f"median bid window {window:.0f} days"},
            {"step": "2 Evaluation → award (likely)", "start": deadline, "end": max(p50, deadline), "kind": "Model", "note": "to the P50 award date"},
            {"step": "3 Award risk window", "start": max(p50, deadline), "end": max(p80, deadline), "kind": "Model", "note": "P50 → P80: plan for this"},
            {"step": "4 Delivery (after P80 award)", "start": max(p80, deadline),
             "end": max(p80, deadline) + timedelta(days=round(months * 30.44)), "kind": "Derived",
             "note": f"median contract duration {months:.1f} months"},
        ])

    # ── what-if optimizer ────────────────────────────────────────────────────────────────────────
    def optimize(self, country, item_type, method, value_usd, open_date, review="Post", bid_window=30):
        """Score the choices a planner controls. Review type is set by the Bank's thresholds, so it is held fixed."""
        methods = [m for m in ("RFB", "RFQ", "ICB") if m in dict(self.ins.methods())]
        rows = []
        for m in methods:
            for w in (21, 30, 45, 60):
                for lots in (1, 2, 3):
                    desc = (f"Supply and installation of {item_type.lower()} for public health laboratories"
                            + (f", {lots} lots" if lots > 1 else ""))
                    pkg = build_package(country, item_type, m, value_usd / lots, description=desc, review_type=review,
                                        planned_open_date=open_date, bid_window_days=w, projects=self.projects)
                    s = score(pkg)
                    rows.append({"method": m, "bid window (days)": w, "lots": lots, "value per lot": value_usd / lots,
                                 "P50 days": s["cycle_p50"], "P80 days": s["cycle_p80"],
                                 "single-bid risk": s.get("single_bid_prob", np.nan),
                                 "base rate": s.get("single_bid_base_rate", np.nan)})
        df = pd.DataFrame(rows)
        # a method is only a realistic choice inside the contract sizes it has actually been used for (RFQ for small,
        # off-the-shelf purchases, RFB above that): the 10th-90th percentile of awarded goods tenders by method. A method
        # with fewer than 30 sized awards (ICB under the old guidelines) has no evidence and is not recommended.
        rng = self.method_ranges()
        df["within past use"] = [m in rng and rng[m][0] <= v <= rng[m][1] for m, v in zip(df.method, df["value per lot"])]
        n_hist = self.t.groupby("method_code").size()
        df["history (n tenders)"] = df.method.map(n_hist).fillna(0).astype(int)
        # rank: slow-case time first, single-bid risk as tie-break (both lower is better)
        df["score"] = df["P80 days"].rank() + df["single-bid risk"].rank()
        df = df.sort_values(["within past use", "score", "P80 days"], ascending=[False, True, True]).reset_index(drop=True)
        base = df[(df.method == method) & (df["bid window (days)"] == min((21, 30, 45, 60), key=lambda x: abs(x - bid_window)))
                  & (df.lots == 1)]
        return df, (base.iloc[0] if len(base) else None)

    def method_ranges(self, min_n=30):
        g = self.t_all[(self.t_all.procurement_category == "Goods") & self.t_all.contract_usd.notna()]
        q = g.groupby("method_code").contract_usd.quantile([0.1, 0.9]).unstack()
        n = g.groupby("method_code").size()
        return {m: (float(r[0.1]), float(r[0.9])) for m, r in q.iterrows() if n.get(m, 0) >= min_n}

    # ── pre-mortem ───────────────────────────────────────────────────────────────────────────────
    def premortem(self, country, item_type, scores, k_lessons=3):
        """Ranked ways this purchase could go wrong, each with how often it happened and the evidence."""
        out = []
        bd, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.n_bidders.notna())
        if len(bd):
            sb = bd.single_bid_flag.mean()
            rel = scores.get("single_bid_prob", sb) / max(scores.get("single_bid_base_rate", sb), 1e-9)
            out.append({"risk": "Only one responsive bid", "how often": sb, "scope": scope, "n": len(bd),
                        "signal": "higher than usual" if rel >= 1.5 else ("lower than usual" if rel <= .67 else "about usual"),
                        "watch for": "Short bid windows and narrow specifications; market sounding and brand-neutral "
                                     "specifications widen competition."})
            rej = bd.rejection_reasons.fillna("").str.len().gt(0)
            reasons = bd.rejection_reasons[rej].str.split(";").explode().str.strip().str.lower().value_counts()
            out.append({"risk": "Bids rejected at evaluation", "how often": rej.mean(), "scope": scope, "n": len(bd),
                        "signal": f"top reason: {reasons.index[0]}" if len(reasons) else "",
                        "watch for": "Non-responsiveness is the usual cause: clear compliance checklists and a "
                                     "pre-bid meeting reduce it."})
        tm, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.award_observed.astype(bool) & d.observed_days.notna())
        if len(tm):
            slow = (tm.observed_days > 180).mean()
            out.append({"risk": "Award takes more than 6 months", "how often": slow, "scope": scope, "n": len(tm),
                        "signal": f"model P80 {scores['cycle_p80']:.0f} days",
                        "watch for": "Prior-review steps and evaluation committee scheduling; plan the award milestone "
                                     "on the P80, not the median."})
        aw, scope = self._scoped(self.t, country, item_type, cond=lambda d: d.award_observed.astype(bool))
        if len(aw):
            out.append({"risk": "Award amended after signing", "how often": aw.award_amended_flag.astype(bool).mean(),
                        "scope": scope, "n": len(aw), "signal": "",
                        "watch for": "Quantities and delivery sites that change after award; fix them in the plan."})
        risks = pd.DataFrame(out).sort_values("how often", ascending=False).reset_index(drop=True) if out else pd.DataFrame()
        q = [f"{item_type} procurement", "procurement delays evaluation award", "equipment delivery installation maintenance"]
        L = self.kb.search_lessons(q, country=country, k=k_lessons)
        lessons = [{"quote": r.evidence_quote, "doc": r.doc_type_code, "project": r.project_id, "page": int(r.page_start),
                    "url": r.landing_url, "stage": r.procurement_stage, "risk": r.risk_category}
                   for r in L.itertuples()] if len(L) else []
        return risks, lessons


def figures_table(figs):
    return pd.DataFrame([{**asdict(f), "badge": f.badge()} for f in figs])


def plan_record(country_name, item_type, method, value_usd, open_date, scores, timeline):
    """One row for 'My plan' and its export."""
    award80 = timeline.loc[timeline.step.str.startswith("3"), "end"].iloc[0]
    delivered = timeline.loc[timeline.step.str.startswith("4"), "end"].iloc[0]
    return {"package": f"{item_type} · {country_name}", "method": method, "estimated value (US$)": round(value_usd),
            "invitation": pd.Timestamp(open_date).date(), "award P50 (days)": round(scores["cycle_p50"]),
            "award P80 (days)": round(scores["cycle_p80"]), "award by (P80)": award80.date(),
            "delivered by": delivered.date(), "single-bid risk vs usual":
                f"{scores.get('single_bid_prob', float('nan')):.0%} vs {scores.get('single_bid_base_rate', float('nan')):.0%}"}


__all__ = ["Lifecycle", "Fig", "STAGES", "figures_table", "plan_record", "project_url", "km"]
