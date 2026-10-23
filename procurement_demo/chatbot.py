"""Step 7 - the procurement chatbot: natural-language questions answered from the connected knowledge base, with
every sentence cited and verified.

    question ─▶ UNDERSTAND: entities (country, region, equipment type, supplier, project, years) + intent
             ─▶ ROUTE to the tool that can answer it exactly:
                  ranking / supplier / spend questions  → pandas over gold.fact_contract (no LLM arithmetic)
                  "how long" questions                  → Kaplan-Meier benchmarks (gold.tender_benchmarks)
                  "why / lessons / risks" questions      → hybrid retrieval over lessons and report passages (RAG)
                  "how are X and Y connected / ERD"      → knowledge-graph neighbourhood (gold.kg_edges) as a diagram
                  a project id or supplier name          → entity profile across all sources
             ─▶ COMPOSE statements, each citing evidence ids ([C1] contract row, [B1] benchmark, [L1] lesson, ...)
             ─▶ VERIFY with the same gate as the copilot (citations exist, numbers and quotes are in the cited evidence)
             ─▶ optional LLM wording (PROC_CHAT_LLM=1): may rephrase, is re-checked, and is discarded if it adds a number

    python -m procurement_demo.chatbot "Which supplier won the cheapest GeneXpert contract?"
    python -m streamlit run procurement_demo/app.py          # the chat UI
"""
import json
import os
import re
import sys

import numpy as np
import pandas as pd

from .common import CFG, GOLD, country_reference, in_domain, lib
from .copilot import _best_sentence, rating_note
from .retrieval import BM25, KnowledgeBase

Evidence, verify = lib.Evidence, lib.verify_statements
CONTRACT_SOURCE = "World Bank Major Contract Awards (FinancesOne DS00005)"
INTENTS = [   # (intent, pattern) - first match wins
    ("graph", r"relationship|related|connect|linked|link between|network|graph|entity[- ]relationship|\berd\b|diagram|"
              r"knowledge graph|map of"),
    ("rank_low", r"cheapest|lowest|smallest|least expensive|minimum|lowest[- ]value"),
    ("rank_high", r"largest|biggest|highest|most expensive|maximum|top \d+ contracts|largest contracts"),
    ("suppliers", r"which (vendor|supplier|compan|firm|manufacturer)|who (supplied|won|got|received|sold|delivered)|"
                  r"top (vendor|supplier)s?|\bvendors\b|\bsuppliers\b|market share|concentrat"),
    ("timeline", r"how long|how many days|timeline|duration|time to award|cycle time|lead time|days to award|"
                 r"how fast|how quickly"),
    ("aggregate", r"how many|how much|total|spend|spent|count|number of|breakdown|by country|by year|by region|trend|"
                  r"overview|summary of"),
    ("lessons", r"why|lesson|learn|problem|challenge|issue|risk|fail|success|recommend|went wrong|cause|delay|"
                r"maintenance|after-sales|warranty|what should"),
]
GENERAL_DOMAIN_WORDS = r"\blab|laborator|diagnost|equipment"
OUT_OF_DOMAIN_WORDS = {"Medicines and vaccines": r"medicine|drug|vaccine|pharmac",
                       "PPE and medical consumables": r"\bPPE\b|mask|glove|gown|syringe",
                       "Other medical equipment": r"ventilator|oxygen|hospital bed|surgical|other medical equipment",
                       "IT, furniture and vehicles": r"computer|laptop|vehicle|furniture|ambulance"}


def money(x):
    return f"US${x:,.0f}"


def plural(n, one, many=None):
    n = int(n)
    return f"{n} {one if n == 1 else (many or one + 's')}"


class ProcurementChat:
    def __init__(self, gold=GOLD):
        self.kb = KnowledgeBase(gold)
        self.contracts = pd.read_parquet(gold / "fact_contract.parquet")
        # regional (multi-country) projects have no borrower country: keep them visible as one "country"
        self.contracts["country_iso3"] = self.contracts["country_iso3"].fillna("REG")
        self.projects = self.kb.projects
        self.suppliers = pd.read_parquet(gold / "dim_supplier.parquet")
        self.edges = pd.read_parquet(gold / "kg_edges.parquet")
        self.bench = pd.read_parquet(gold / "tender_benchmarks.parquet")
        self.docs = self.kb.chunks.drop_duplicates("doc_id")[["doc_id", "doc_type_code", "project_id", "title", "landing_url",
                                                              "doc_date"]]
        ref = country_reference().values()
        self.country_name = {c["iso3"]: c["name"] for c in ref} | {"REG": "Regional (multi-country projects)"}
        present = set(self.contracts.country_iso3.dropna())
        self.country_alias = {c["name"].lower(): c["iso3"] for c in ref if c["iso3"] in present}
        for short, iso3 in (("drc", "COD"), ("congo", "COD"), ("vietnam", "VNM"), ("viet nam", "VNM"), ("egypt", "EGY"),
                            ("yemen", "YEM"), ("gambia", "GMB"), ("laos", "LAO"), ("lao", "LAO"), ("kyrgyzstan", "KGZ"),
                            ("turkey", "TUR"), ("russia", "RUS"), ("iran", "IRN"), ("syria", "SYR"), ("venezuela", "VEN"),
                            ("ivory coast", "CIV"), ("cote d'ivoire", "CIV"), ("west bank", "PSE"), ("gaza", "PSE")):
            if iso3 in present:
                self.country_alias[short] = iso3
        for name in list(self.country_alias):          # "Kenya, Rep." style names → also the part before the comma
            base = name.split(",")[0].strip()
            self.country_alias.setdefault(base, self.country_alias[name])
        self.regions = sorted(set(self.projects.region.dropna()))
        keys = self.suppliers.assign(k=self.suppliers.supplier_key.str.split("|").str[0])
        generic = {"medical", "health", "general", "international", "services", "trading", "supplies", "pharma", "global"}
        self.supplier_keys = [(k, key) for k, key in zip(keys.k, keys.supplier_key)
                              if len(k) >= 5 and k not in generic and len(k.split()) <= 6]
        self.contract_bm25 = BM25(self.contracts.description.fillna("").tolist())

    # ── understanding ────────────────────────────────────────────────────────────────────────────
    def parse(self, q):
        ql = q.lower()
        f = {"countries": [], "region": None, "item_types": None, "domain_only": True, "years": None,
             "project_id": None, "supplier_key": None}
        for name, iso3 in sorted(self.country_alias.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{re.escape(name)}\b", ql) and iso3 not in f["countries"]:
                f["countries"].append(iso3)
                ql = ql.replace(name, " ")
        for r in self.regions:
            core = r.strip().lower().replace("&", "and").split(" (")[0]
            words = {"africa": "Sub-Saharan Africa", "latin america": "Latin America & Caribbean",
                     "south asia": "South Asia", "east asia": "East Asia & Pacific", "middle east": "Middle East & North Africa",
                     "europe": "Europe & Central Asia", "central asia": "Europe & Central Asia"}
            hit = core in q.lower() or any(w in q.lower() and words[w] == r.strip() for w in words)
            if hit and not f["countries"]:
                f["region"] = r
        # domain types by their own patterns; out-of-domain types only when named outright ("delivery" or "monitor"
        # in a question must not switch the filter to "other medical equipment")
        specific = [name for name, pat in CFG["item_types"]
                    if (in_domain(name) and re.search(pat, q, re.I) and not name.startswith("General"))
                    or (not in_domain(name) and re.search(OUT_OF_DOMAIN_WORDS.get(name, r"$^"), q, re.I))]
        if specific:
            f["item_types"] = specific
            f["domain_only"] = all(in_domain(s) for s in specific)
        elif re.search(r"all goods|all health|everything|any item", q, re.I):
            f["domain_only"] = False
        m = re.search(r"\b(?:since|after|from) (20\d\d)\b", q)
        if m:
            f["years"] = (int(m.group(1)), 2100)
        m = re.search(r"\b(20\d\d)\s*(?:-|to|and)\s*(20\d\d)\b", q)
        if m:
            f["years"] = (int(m.group(1)), int(m.group(2)))
        elif not f["years"]:
            m = re.search(r"\bin (20\d\d)\b", q)
            if m:
                f["years"] = (int(m.group(1)), int(m.group(1)))
        m = re.search(r"\bP\d{6}\b", q.upper())
        if m:
            f["project_id"] = m.group(0)
        norm = " " + lib.norm_supplier(q) + " "
        best = max(((k, key) for k, key in self.supplier_keys if f" {k} " in norm), key=lambda x: len(x[0]), default=None)
        if best:
            f["supplier_key"] = best[1]
        return f

    def intent(self, q, f):
        for name, pat in INTENTS:
            if re.search(pat, q, re.I):
                if name in ("suppliers", "aggregate") and f["supplier_key"] and not re.search(r"which|top|share", q, re.I):
                    return "supplier"
                return name
        if f["project_id"]:
            return "project"
        if f["supplier_key"]:
            return "supplier"
        return "lessons"

    def scope_text(self, f):
        bits = []
        if f["item_types"]:
            bits.append(" / ".join(f["item_types"]))
        elif f["domain_only"] and CFG.get("domain_items"):
            bits.append(CFG["short"])
        if f["countries"]:
            bits.append("in " + ", ".join(self.country_name.get(c, c) for c in f["countries"]))
        elif f["region"]:
            bits.append(f"in {f['region']}")
        if f["years"]:
            a, b = f["years"]
            bits.append(f"signed {a}" if a == b else f"signed {a}-{b}" if b < 2100 else f"signed {a} onwards")
        return " ".join(bits) or "all contracts"

    def filtered(self, f):
        c = self.contracts
        if f["item_types"]:
            c = c[c.item_type.isin(f["item_types"])]
        elif f["domain_only"]:
            c = c[c.in_domain]
        if f["countries"]:
            c = c[c.country_iso3.isin(f["countries"])]
        elif f["region"]:
            c = c[c.region == f["region"]]
        if f["years"]:
            y = c.signing_date.dt.year
            c = c[(y >= f["years"][0]) & (y <= f["years"][1])]
        if f["project_id"]:
            c = c[c.project_id == f["project_id"]]
        if f["supplier_key"]:
            c = c[c.supplier_key == f["supplier_key"]]
        return c[c.amount_usd.notna()]

    def contract_evidence(self, ev, r):
        date = r.signing_date.date() if pd.notna(r.signing_date) else "n/a"
        return ev.add("C", f"Contract {r.wb_contract_number}: {r.description[:220]} | supplier {r.supplier_name} "
                           f"({r.supplier_country}) | {money(r.amount_usd)} | signed {date} | {r.project_id} "
                           f"{r.project_name} | {self.country_name.get(r.country_iso3, r.country_iso3)} | "
                           f"{r.procurement_method}, {r.review_type} review | item type: {r.item_type}",
                      source=CONTRACT_SOURCE, project_id=r.project_id, contract=r.wb_contract_number)

    # ── answering ────────────────────────────────────────────────────────────────────────────────
    def ask(self, q):
        f = self.parse(q)
        route = self.intent(q, f)
        ev, st = Evidence(), []
        out = {"question": q, "route": route, "filters": f, "scope": self.scope_text(f), "table": None, "dot": None}
        getattr(self, f"_{route}")(q, f, ev, st, out)
        kept, log = verify(st, ev)
        out.update(statements=kept, guardrail_log=log, evidence=ev.items)
        out["answer_md"] = to_markdown(kept)
        if os.environ.get("PROC_CHAT_LLM") == "1" and kept:
            out["llm_answer"] = llm_wording(q, kept, ev.items)
        return out

    def _rank(self, f, ev, st, out, low):
        c = self.filtered(f)
        if c.empty:
            st.append(("Answer", f"Not enough data: no contracts match {self.scope_text(f)}.", []))
            return
        top = c.sort_values("amount_usd", ascending=low).head(5)
        word = "lowest" if low else "highest"
        ids = [self.contract_evidence(ev, r) for r in top.itertuples()]
        r0 = top.iloc[0]
        st.append(("Answer", f"The {word}-value contract for {self.scope_text(f)} went to {r0.supplier_name} "
                             f"({r0.supplier_country}): contract {r0.wb_contract_number}, {money(r0.amount_usd)}, "
                             f"for “{r0.description[:120].rstrip('. ')}”.", [ids[0]]))
        for i, r in enumerate(top.iloc[1:].itertuples(), start=1):
            st.append(("Next in the ranking", f"{r.supplier_name}: {money(r.amount_usd)} (contract {r.wb_contract_number}, "
                                              f"{self.country_name.get(r.country_iso3, r.country_iso3)}).", [ids[i]]))
        did = ev.add("D", f"Derived from gold.fact_contract: {len(c)} contracts match ({self.scope_text(f)}); "
                          f"median value {money(c.amount_usd.median())}.", source="gold.fact_contract")
        st.append(("Context", f"{len(c)} contracts match this question; the median value is {money(c.amount_usd.median())}. "
                              "Note that awards are reported per contract, so a low value can be one lot of a larger tender.",
                   [did]))
        out["table"] = top[["wb_contract_number", "supplier_name", "supplier_country", "amount_usd", "signing_date",
                            "country_iso3", "project_id", "item_type", "description"]]

    def _rank_low(self, q, f, ev, st, out):
        self._rank(f, ev, st, out, low=True)

    def _rank_high(self, q, f, ev, st, out):
        self._rank(f, ev, st, out, low=False)

    def _suppliers(self, q, f, ev, st, out):
        c = self.filtered(f)
        if c.empty:
            st.append(("Answer", f"Not enough data: no contracts match {self.scope_text(f)}.", []))
            return
        g = (c.groupby("supplier_key").agg(supplier=("supplier_name", lambda s: s.value_counts().index[0]),
                                           base=("supplier_country", "first"), contracts=("wb_contract_number", "nunique"),
                                           total_usd=("amount_usd", "sum"), countries=("country_iso3", "nunique"))
             .sort_values("total_usd", ascending=False))
        total = c.amount_usd.sum()
        rows = "; ".join(f"{r.supplier} ({r.base}): {plural(r.contracts, 'contract')}, {money(r.total_usd)}, "
                         f"{r.total_usd / total:.0%} of value, {plural(r.countries, 'country', 'countries')}" for r in g.head(10).itertuples())
        did = ev.add("D", f"Derived from gold.fact_contract grouped by resolved supplier (gold.dim_supplier) for "
                          f"{self.scope_text(f)}: {len(c)} contracts, {g.shape[0]} suppliers, total {money(total)}. "
                          f"Top suppliers: {rows}.", source="gold.fact_contract + gold.dim_supplier")
        st.append(("Answer", f"{g.shape[0]} suppliers won {len(c)} contracts worth {money(total)} for "
                             f"{self.scope_text(f)}.", [did]))
        for r in g.head(5).itertuples():
            st.append(("Top suppliers by value", f"{r.supplier} ({r.base}): {plural(r.contracts, 'contract')}, {money(r.total_usd)} "
                                                 f"({r.total_usd / total:.0%} of value), {plural(r.countries, 'country', 'countries')}.", [did]))
        top5 = g.head(5).total_usd.sum() / total
        did2 = ev.add("D", f"Derived: top 5 suppliers hold {top5:.0%} of contract value ({money(g.head(5).total_usd.sum())} "
                           f"of {money(total)}).", inputs=[did])
        st.append(("Market concentration", f"The top 5 suppliers hold {top5:.0%} of the value, "
                                           f"{'a concentrated market' if top5 > 0.5 else 'a fairly fragmented market'}.",
                   [did2]))
        out["table"] = g.head(25).reset_index()

    def _aggregate(self, q, f, ev, st, out):
        c = self.filtered(f)
        if c.empty:
            st.append(("Answer", f"Not enough data: no contracts match {self.scope_text(f)}.", []))
            return
        by = ("country_iso3" if re.search(r"by country|which countr|per country", q, re.I) else
              "region" if re.search(r"by region|per region", q, re.I) else
              "year" if re.search(r"by year|per year|trend|over time", q, re.I) else "item_type")
        c = c.assign(year=c.signing_date.dt.year)
        g = (c.groupby(by).agg(contracts=("wb_contract_number", "nunique"), total_usd=("amount_usd", "sum"),
                               suppliers=("supplier_key", "nunique")).sort_values("total_usd", ascending=False))
        if by == "year":
            g = g.sort_index()
        label = lambda k: self.country_name.get(k, k) if by == "country_iso3" else str(int(k)) if by == "year" else k  # noqa
        rows = "; ".join(f"{label(k)}: {int(r.contracts)} contracts, {money(r.total_usd)}" for k, r in g.iterrows())
        did = ev.add("D", f"Derived from gold.fact_contract for {self.scope_text(f)}, grouped by {by}: total "
                          f"{len(c)} contracts, {money(c.amount_usd.sum())}, {c.supplier_key.nunique()} suppliers, "
                          f"{c.project_id.nunique()} projects, {c.country_iso3.nunique()} countries. Groups: {rows}.",
                     source="gold.fact_contract")
        st.append(("Answer", f"{len(c)} contracts worth {money(c.amount_usd.sum())} match {self.scope_text(f)}, "
                             f"awarded to {c.supplier_key.nunique()} suppliers in {c.project_id.nunique()} projects and "
                             f"{c.country_iso3.nunique()} countries.", [did]))
        shown = g if by == "year" else g.head(6)
        for k, r in shown.iterrows():
            st.append((f"By {by.replace('_iso3', '').replace('_', ' ')}", f"{label(k)}: {int(r.contracts)} contracts, "
                                                                           f"{money(r.total_usd)}.", [did]))
        out["table"] = g.reset_index()

    def _timeline(self, q, f, ev, st, out):
        b = self.bench
        types = f["item_types"] or sorted(CFG["domain_items"] or [])
        rows = b[(b.level == "item_type") & b.item_type.isin(types)] if "item_type" in b else b.iloc[0:0]
        if f["countries"]:
            cc = b[(b.level == "country+category+method") & b.country_iso3.isin(f["countries"])]
            rows = pd.concat([rows, cc])
        if rows.empty:
            rows = b[b.level == "category"]
        for r in rows.sort_values("median_days").itertuples():
            name = (r.item_type if r.level == "item_type" else
                    f"{r.procurement_category} {getattr(r, 'method_code', '')} in "
                    f"{self.country_name.get(r.country_iso3, r.country_iso3)}" if r.level == "country+category+method"
                    else r.procurement_category)
            p80 = f", P80 {r.p80_days:.0f} days" if pd.notna(r.p80_days) else ""
            bid = ev.add("B", f"Kaplan-Meier benchmark ({r.level}) for {name}: median (50%) {r.median_days:.0f} days from "
                              f"invitation to award{p80 + ' (80% of tenders awarded within it)' if p80 else ''}; n={r.n} "
                              f"tenders, {r.n_awarded} awarded; awarded-only median "
                              f"{r.naive_median_awarded_only:.0f} days.", source="gold.tender_benchmarks")
            st.append(("Time from invitation to award", f"{name}: half of tenders are awarded within {r.median_days:.0f} "
                                                        f"days{p80.replace(', P80', '; 80% within').replace(' days', '') + ' days' if p80 else ''} "
                                                        f"(n={r.n}).", [bid]))
        st.append(("How to read this", "Medians are censoring-aware: tenders still open count as at least their current "
                                       "age, so slow tenders are not silently dropped. Plan milestones on the P80, not the "
                                       "median.", [bid] if rows.shape[0] else []))
        out["table"] = rows

    def _lessons(self, q, f, ev, st, out):
        domain_hint = " laboratory diagnostic equipment" if re.search(GENERAL_DOMAIN_WORDS, q, re.I) else ""
        queries = [q, q + domain_hint + " procurement", q + " lessons implementation"]
        country = f["countries"][0] if len(f["countries"]) == 1 else None
        lessons = self.kb.search_lessons(queries, country=country, k=5, procurement_only=False)
        for r in lessons.itertuples():
            lid = ev.add("L", r.detail, source=f"{r.doc_type_code} {r.title}", doc_id=r.doc_id, chunk_id=r.chunk_id,
                         page=int(r.page_start), url=r.landing_url, project_id=r.project_id)
            line = f"“{r.evidence_quote}” ({r.doc_type_code}, {r.project_id}, p. {int(r.page_start)})"
            cite = [lid]
            rating = self.kb.rating_context(r.project_id)
            if rating and rating.get("conflict"):
                traj = json.loads(rating["trajectory"])
                rid = ev.add("R", f"Rating evidence for {r.project_id}: " + "; ".join(f"{s} {d}: {v}" for s, d, v in traj)
                                  + f". Resolved: {rating['resolved_rating']} (from {rating['resolved_from']}).",
                             source="gold.project_ratings_resolved", project_id=r.project_id)
                line += f" - note: {rating_note(rating)}."
                cite.append(rid)
            st.append(("Lessons from evaluations", line, cite))
        passages = self.kb.search_passages(queries[:2], country=country, k=4,
                                           kinds=("lessons", "key_factors", "procurement_fiduciary", "risk", "results", "other"))
        used = {r.evidence_quote for r in lessons.itertuples()} if len(lessons) else set()
        for r in passages.itertuples():
            sent = _best_sentence(r.chunk_text, q + domain_hint)
            if not sent or any(sent[:80] in u or u[:80] in sent for u in used):
                continue
            pid = ev.add("P", r.chunk_text, source=f"{r.doc_type_code} {r.title}", doc_id=r.doc_id, page=int(r.page_start),
                         section=r.section_path, url=r.landing_url, project_id=r.project_id)
            st.append(("Report passages", f"“{sent}” ({r.doc_type_code}, {r.project_id}, p. {int(r.page_start)})", [pid]))
        if not st:
            st.append(("Answer", "No verified lessons or passages matched this question in the indexed reports.", []))

    def _project(self, q, f, ev, st, out):
        pid = f["project_id"]
        p = self.projects[self.projects.project_id == pid]
        if p.empty:
            st.append(("Answer", f"Not enough data: project {pid} is not in this knowledge base.", []))
            return
        p = p.iloc[0]
        appr = p.approval_date.date() if pd.notna(p.approval_date) else "n/a"
        prid = ev.add("J", f"Project {pid} {p.project_name} | {self.country_name.get(p.country_iso3, p.country_iso3)} | "
                           f"{p.region} | approved {appr} | status {p.status} | commitment "
                           f"{money(p.commitment_usd) if pd.notna(p.commitment_usd) else 'n/a'} | {p.project_url}",
                      source="World Bank Projects API (gold.dim_project)", project_id=pid, url=p.project_url)
        st.append(("Project", f"{pid} {p.project_name} ({self.country_name.get(p.country_iso3, p.country_iso3)}), "
                              f"approved {appr}, status {p.status}.", [prid]))
        c = self.contracts[self.contracts.project_id == pid]
        dom = c[c.in_domain]
        if len(c):
            items = dom.groupby("item_type").amount_usd.sum().sort_values(ascending=False)
            did = ev.add("D", f"Derived from gold.fact_contract for {pid}: {len(c)} health-goods contracts, {money(c.amount_usd.sum())}; "
                              f"{len(dom)} in {CFG['short']}, {money(dom.amount_usd.sum())}; by equipment type: "
                              + "; ".join(f"{k}: {money(v)}" for k, v in items.items()), source="gold.fact_contract",
                          project_id=pid)
            st.append(("Procurement", f"{len(dom)} of its {len(c)} goods contracts are {CFG['short']}, worth "
                                      f"{money(dom.amount_usd.sum())}.", [did]))
            if len(items):
                st.append(("Procurement", f"Largest equipment type: {items.index[0]} ({money(items.iloc[0])}).", [did]))
            for r in dom.sort_values("amount_usd", ascending=False).head(3).itertuples():
                st.append(("Largest domain contracts", f"{r.supplier_name}: {money(r.amount_usd)} for “{r.description[:90]}”.",
                           [self.contract_evidence(ev, r)]))
        rating = self.kb.rating_context(pid)
        if rating:
            traj = json.loads(rating["trajectory"])
            rid = ev.add("R", f"Rating evidence for {pid}: " + "; ".join(f"{s} {d}: {v}" for s, d, v in traj)
                              + f". Resolved: {rating['resolved_rating']} (from {rating['resolved_from']}).",
                         source="gold.project_ratings_resolved", project_id=pid)
            txt = f"Development-objective rating: {rating['resolved_rating']} (from the {rating['resolved_from']})"
            if rating["conflict"]:
                txt += f"; supervision ratings disagreed, reaching {rating['worst_isr']} during implementation"
            st.append(("Outcome", txt + ".", [rid]))
        d = self.docs[self.docs.project_id == pid]
        for r in d.itertuples():
            st.append(("Documents indexed", f"{r.doc_type_code}: {r.title[:90]} ({r.doc_date}).",
                       [ev.add("S", f"{r.doc_type_code} {r.title} {r.doc_date} {r.landing_url}", source="Documents & Reports API",
                               url=r.landing_url, doc_id=r.doc_id, project_id=pid)]))
        out["dot"] = self.graph_dot("PROJECT", pid)

    def _supplier(self, q, f, ev, st, out):
        key = f["supplier_key"]
        s = self.suppliers[self.suppliers.supplier_key == key].iloc[0]
        c = self.contracts[self.contracts.supplier_key == key]
        variants = json.loads(s.name_variants)
        sid = ev.add("V", f"Supplier {s.supplier_name} ({s.supplier_country}) | resolved key {key} | {len(variants)} name "
                          f"variants: {'; '.join(variants)} | {s.n_contracts} contracts | {money(s.total_usd)}",
                     source="gold.dim_supplier (entity resolution)")
        st.append(("Supplier", f"{s.supplier_name} ({s.supplier_country}) won {s.n_contracts} contracts worth "
                               f"{money(s.total_usd)}" + (f", recorded under {len(variants)} name spellings" if len(variants) > 1 else "")
                   + ".", [sid]))
        items = c.groupby("item_type").amount_usd.sum().sort_values(ascending=False)
        ctry = c.groupby("country_iso3").amount_usd.sum().sort_values(ascending=False)
        did = ev.add("D", f"Derived from gold.fact_contract for supplier {key}: by equipment type "
                          + "; ".join(f"{k}: {money(v)}" for k, v in items.items()) + ". By country: "
                          + "; ".join(f"{self.country_name.get(k, k)}: {money(v)}" for k, v in ctry.items())
                          + f". Countries: {len(ctry)}. Projects: {c.project_id.nunique()}.", source="gold.fact_contract")
        st.append(("What and where", f"Mostly {items.index[0]} ({money(items.iloc[0])}); active in {len(ctry)} "
                                     f"countries and {c.project_id.nunique()} projects, largest in "
                                     f"{self.country_name.get(ctry.index[0], ctry.index[0])} ({money(ctry.iloc[0])}).", [did]))
        for r in c.sort_values("amount_usd", ascending=False).head(3).itertuples():
            st.append(("Largest contracts", f"{money(r.amount_usd)} in {self.country_name.get(r.country_iso3, r.country_iso3)} "
                                            f"for “{r.description[:90]}”.", [self.contract_evidence(ev, r)]))
        out["table"] = c.sort_values("amount_usd", ascending=False)[["wb_contract_number", "amount_usd", "signing_date",
                                                                       "country_iso3", "project_id", "item_type", "description"]]
        out["dot"] = self.graph_dot("SUPPLIER", key)

    def _graph(self, q, f, ev, st, out):
        if f["supplier_key"]:
            out["dot"] = self.graph_dot("SUPPLIER", f["supplier_key"])
            self._supplier(q, f, ev, st, out)
        elif f["project_id"]:
            self._project(q, f, ev, st, out)
        elif len(f["countries"]) == 1:
            out["dot"] = self.graph_dot("COUNTRY", f["countries"][0])
            iso3 = f["countries"][0]
            e = self.edges
            pids = e[(e.rel == "LOCATED_IN") & (e.dst_type == "COUNTRY") & (e.dst_id == iso3)].src_id
            c = self.contracts[self.contracts.project_id.isin(pids) & self.contracts.in_domain]
            gid = ev.add("G", f"Knowledge-graph neighbourhood of COUNTRY {iso3} (gold.kg_edges): {len(pids)} projects, "
                              f"{c.supplier_key.nunique()} domain suppliers, {len(c)} domain contracts.", source="gold.kg_edges")
            st.append(("Connections", f"{self.country_name.get(iso3)} links to {len(pids)} projects, which hold {len(c)} "
                                      f"{CFG['short']} contracts from {c.supplier_key.nunique()} suppliers.", [gid]))
            for sk, v in c.groupby("supplier_key").amount_usd.sum().nlargest(3).items():
                rows = c[c.supplier_key == sk]
                sid = ev.add("D", f"Derived from gold.fact_contract: supplier {rows.supplier_name.iloc[0]} in "
                                  f"{self.country_name.get(iso3)}: {len(rows)} contracts, {money(v)}, equipment types "
                                  f"{', '.join(rows.item_type.unique()[:3])}, projects {', '.join(rows.project_id.unique()[:3])}.",
                             source="gold.fact_contract")
                st.append(("Main suppliers", f"{rows.supplier_name.iloc[0]}: {plural(len(rows), 'contract')}, {money(v)} "
                                             f"({', '.join(rows.item_type.unique()[:3])}).", [sid]))
        else:
            out["dot"] = SCHEMA_DOT
            counts = self.edges.groupby(["src_type", "rel", "dst_type"]).size().sort_values(ascending=False)
            gid = ev.add("G", "Edge counts in gold.kg_edges: " + "; ".join(f"{a} {r} {b}: {n}" for (a, r, b), n in counts.items()),
                         source="gold.kg_edges")
            st.append(("Data model", "The diagram shows how the entities connect. Every link is a row in the edge table "
                                     "with the method that created it (exact key, fuzzy name, keyword, temporal) and a "
                                     "confidence.", [gid]))
            for (a, r, b), n in counts.head(8).items():
                st.append(("Largest relationships", f"{a} {r} {b}: {n} links.", [gid]))
            out["table"] = counts.rename("edges").reset_index()

    # ── knowledge-graph neighbourhood as a Graphviz diagram ──────────────────────────────────────
    def graph_dot(self, kind, key, max_nodes=10):
        e, c = self.edges, self.contracts
        lines = ['digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="Helvetica", '
                 'fontsize=10]; edge [fontname="Helvetica", fontsize=9, color="#57606a"];']
        color = {"SUPPLIER": "#d1f0d6", "PROJECT": "#dbeafe", "COUNTRY": "#fde7c8", "REGION": "#fde7c8", "ITEM": "#ede4fb",
                 "DOC": "#fbe3d4"}
        nodes = {}

        def node(nid, label, k):
            if nid not in nodes:
                nodes[nid] = f'"{_esc(nid)}" [label="{_esc(label)}", fillcolor="{color[k]}"];'

        def edge(a, b, label):
            lines.append(f'"{_esc(a)}" -> "{_esc(b)}" [label="{_esc(label)}"];')

        if kind == "SUPPLIER":
            s = self.suppliers[self.suppliers.supplier_key == key].iloc[0]
            node(key, f"SUPPLIER\n{s.supplier_name}\n{s.supplier_country}", "SUPPLIER")
            sc = c[c.supplier_key == key]
            for it, g in sc.groupby("item_type"):
                node(f"I:{it}", f"EQUIPMENT TYPE\n{it}", "ITEM")
                edge(key, f"I:{it}", f"supplied {len(g)}x, {money(g.amount_usd.sum())}")
            for pid, g in sc.groupby("project_id").amount_usd.sum().sort_values(ascending=False).head(max_nodes).items():
                p = self.projects[self.projects.project_id == pid]
                pname = str(p.project_name.iloc[0] or "")[:40] if len(p) else ""
                node(pid, f"PROJECT {pid}\n{pname}", "PROJECT")
                edge(pid, key, f"AWARDED_TO {money(g)}")
                self._project_context(pid, node, edge)
        elif kind == "PROJECT":
            p = self.projects[self.projects.project_id == key].iloc[0]
            node(key, f"PROJECT {key}\n{str(p.project_name or '')[:40]}", "PROJECT")
            self._project_context(key, node, edge)
            pc = c[(c.project_id == key) & c.in_domain]
            for sk, g in pc.groupby("supplier_key").amount_usd.sum().sort_values(ascending=False).head(max_nodes).items():
                sname = pc[pc.supplier_key == sk].supplier_name.iloc[0][:35]
                node(sk, f"SUPPLIER\n{sname}", "SUPPLIER")
                edge(key, sk, f"AWARDED_TO {money(g)}")
                for it in pc[pc.supplier_key == sk].item_type.unique()[:2]:
                    node(f"I:{it}", f"EQUIPMENT TYPE\n{it}", "ITEM")
                    edge(sk, f"I:{it}", "supplied")
            for r in self.docs[self.docs.project_id == key].itertuples():
                node(f"D:{r.doc_id}", f"{r.doc_type_code}\n{r.doc_date}", "DOC")
                edge(f"D:{r.doc_id}", key, "DESCRIBES")
        elif kind == "COUNTRY":
            node(key, f"COUNTRY\n{self.country_name.get(key, key)}", "COUNTRY")
            cc = c[(c.country_iso3 == key) & c.in_domain]
            for pid, g in cc.groupby("project_id").amount_usd.sum().sort_values(ascending=False).head(max_nodes).items():
                p = self.projects[self.projects.project_id == pid]
                node(pid, f"PROJECT {pid}\n{str(p.project_name.iloc[0] or '')[:40] if len(p) else ''}", "PROJECT")
                edge(pid, key, "LOCATED_IN")
                for sk, v in cc[cc.project_id == pid].groupby("supplier_key").amount_usd.sum().nlargest(2).items():
                    node(sk, f"SUPPLIER\n{cc[cc.supplier_key == sk].supplier_name.iloc[0][:35]}", "SUPPLIER")
                    edge(pid, sk, f"AWARDED_TO {money(v)}")
            reg = self.projects[self.projects.country_iso3 == key].region.dropna()
            if len(reg):
                node(f"R:{reg.iloc[0]}", f"REGION\n{reg.iloc[0]}", "REGION")
                edge(key, f"R:{reg.iloc[0]}", "IN_REGION")
        return "\n".join(lines[:1] + list(nodes.values()) + lines[1:]) + "\n}"

    def _project_context(self, pid, node, edge):
        p = self.projects[self.projects.project_id == pid]
        if p.empty or pd.isna(p.country_iso3.iloc[0]):
            return
        iso3 = p.country_iso3.iloc[0]
        node(f"C:{iso3}", f"COUNTRY\n{self.country_name.get(iso3, iso3)}", "COUNTRY")
        edge(pid, f"C:{iso3}", "LOCATED_IN")
        if pd.notna(p.region.iloc[0]):
            node(f"R:{p.region.iloc[0]}", f"REGION\n{p.region.iloc[0]}", "REGION")
            edge(f"C:{iso3}", f"R:{p.region.iloc[0]}", "IN_REGION")


def _esc(s):
    return str(s).replace('"', "'").replace("\\", "/")


SCHEMA_DOT = """digraph ER { rankdir=LR; bgcolor="transparent";
node [shape=record, style="rounded,filled", fillcolor="#f6f8fa", fontname="Helvetica", fontsize=10];
edge [fontname="Helvetica", fontsize=9, color="#57606a"];
REGION [label="{REGION|name}", fillcolor="#fde7c8"];
COUNTRY [label="{COUNTRY|iso3 (PK)\\lname\\lCPIA / WGI by year\\l}", fillcolor="#fde7c8"];
PROJECT [label="{PROJECT|project_id (PK)\\lname, status\\lcommitment_usd\\lsector, region\\l}", fillcolor="#dbeafe"];
NOTICE [label="{NOTICE|notice_id (PK)\\lbid_ref_key (FK)\\lnotice_type, date\\l}"];
TENDER [label="{TENDER|tender_id (PK)\\lproject_id (FK)\\lmethod, item_type\\lcycle_days, n_bidders\\l}"];
CONTRACT [label="{CONTRACT|wb_contract_number (PK)\\ltender_id (FK)\\lsupplier_key (FK)\\lamount_usd, item_type\\l}"];
SUPPLIER [label="{SUPPLIER|supplier_key (PK)\\lname variants\\lsupplier country\\l}", fillcolor="#d1f0d6"];
ITEM [label="{EQUIPMENT TYPE|PCR, imaging, analyzers,\\lreagents, rapid tests ...\\l}", fillcolor="#ede4fb"];
DOCUMENT [label="{DOCUMENT|doc_id (PK)\\lPAD / ISR / ICR / ICRR / PP\\l}", fillcolor="#fbe3d4"];
CHUNK [label="{CHUNK|chunk_id (PK)\\lsection_path, pages\\lembedded text\\l}", fillcolor="#fbe3d4"];
LESSON [label="{LESSON|lesson_id (PK)\\lverified quote, page\\lstage, risk\\l}", fillcolor="#fbe3d4"];
RATING [label="{RATING|ISR / ICR / IEG ratings\\lresolved + conflict flag\\l}", fillcolor="#fbe3d4"];
COUNTRY -> REGION [label="IN_REGION"]; PROJECT -> COUNTRY [label="LOCATED_IN"];
NOTICE -> TENDER [label="ANNOUNCES"]; TENDER -> PROJECT [label="FINANCED_BY"]; TENDER -> CONTRACT [label="RESULTED_IN"];
CONTRACT -> PROJECT [label="FINANCED_BY"]; CONTRACT -> SUPPLIER [label="AWARDED_TO"]; CONTRACT -> ITEM [label="OF_TYPE"];
TENDER -> ITEM [label="OF_TYPE"]; SUPPLIER -> COUNTRY [label="BASED_IN"]; TENDER -> COUNTRY [label="HAS_CONTEXT (year)"];
DOCUMENT -> PROJECT [label="DESCRIBES"]; CHUNK -> DOCUMENT [label="PART_OF"]; LESSON -> CHUNK [label="FROM"];
LESSON -> PROJECT [label="ABOUT"]; RATING -> PROJECT [label="RATES"]; RATING -> DOCUMENT [label="FROM"];
}"""


def to_markdown(statements):
    lines, sec = [], None
    for s in statements:
        if s["section"] != sec:
            sec = s["section"]
            lines.append(f"\n**{sec}**\n")
        lines.append(f"- {s['text']} " + " ".join(f"`{c}`" for c in s["citations"]))
    return "\n".join(lines).strip()


def llm_wording(question, statements, evidence):
    """Optional: Claude rewrites the verified statements into a short answer. Never adds facts: any number not in the
    evidence makes us fall back to the verified statements. Needs `pip install anthropic` and credentials."""
    try:
        import anthropic
    except ImportError:
        return None
    facts = "\n".join(f"- {s['text']} {' '.join('[' + c + ']' for c in s['citations'])}" for s in statements)
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model="claude-opus-5-5", max_tokens=2000, output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system="You answer procurement questions using ONLY the verified statements given. Keep every [id] citation "
                   "next to the claim it supports. Do not add numbers, names or facts that are not in the statements. "
                   "Be concise: at most 6 sentences.",
            messages=[{"role": "user", "content": f"Question: {question}\n\nVerified statements:\n{facts}"}],
        )
    except anthropic.APIError:
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in response.content if b.type == "text")
    pool = [e["text"] for e in evidence.values()] + [s["text"] for s in statements]
    return None if lib.unsupported_numbers(text, pool) else text


EXAMPLES = [
    "Which supplier won the cheapest GeneXpert or PCR contract?",
    "Who are the top suppliers of diagnostic imaging equipment in Sub-Saharan Africa?",
    "How much was spent on laboratory equipment by country?",
    "How long does it take to award a laboratory analyzer tender?",
    "What problems did projects have with laboratory equipment maintenance?",
    "Show the entity relationship diagram",
    "How is Kenya connected to its lab equipment suppliers?",
    "What are the largest rapid test kit contracts since 2020?",
]


def chatbot_eval(bot=None):
    """Labelled questions: is each routed to the right tool, are ranked/aggregate answers exactly what an independent
    pandas query returns, and is every answer sentence cited and verified?"""
    import time
    bot = bot or ProcurementChat()
    c = bot.contracts[bot.contracts.amount_usd.notna()]
    T = {name: [name] for name, _ in CFG["item_types"]}
    mol, img, rea, ana = (T["Molecular diagnostics (PCR, GeneXpert)"], T["Diagnostic imaging"], T["Reagents and cartridges"],
                          T["Laboratory analyzers"])
    dom = c[c.in_domain]
    top_project = dom.groupby("project_id").amount_usd.sum().idxmax()
    nameable = {key for _, key in bot.supplier_keys}           # suppliers whose name the parser can recognise
    top_sup = dom[dom.supplier_key.isin(nameable)].groupby("supplier_key").amount_usd.sum().idxmax()
    top_sup_name = bot.suppliers.set_index("supplier_key").supplier_name[top_sup]
    nga_rea = c[c.item_type.isin(rea) & (c.country_iso3 == "NGA")]

    def first_contract(a):
        ids = a["statements"][0]["citations"] if a["statements"] else []
        return a["evidence"].get(ids[0], {}).get("contract") if ids else None

    def lowest(sub):
        return lambda a: first_contract(a) == sub.sort_values("amount_usd").iloc[0].wb_contract_number

    def highest(sub):
        return lambda a: first_contract(a) == sub.sort_values("amount_usd").iloc[-1].wb_contract_number

    def top_supplier(sub):
        name = sub.groupby("supplier_key").agg(n=("supplier_name", lambda s: s.value_counts().index[0]),
                                               v=("amount_usd", "sum")).sort_values("v").iloc[-1].n
        return lambda a: len(a["statements"]) > 1 and a["statements"][1]["text"].startswith(name)

    def total(sub):
        return lambda a: money(sub.amount_usd.sum()) in a["statements"][0]["text"]

    tests = [
        ("Which supplier won the cheapest GeneXpert or PCR contract?", "rank_low", lowest(c[c.item_type.isin(mol)])),
        ("What is the largest diagnostic imaging contract?", "rank_high", highest(c[c.item_type.isin(img)])),
        ("What are the lowest value laboratory reagent contracts in Nigeria?", "rank_low",
         lowest(nga_rea) if len(nga_rea) else None),
        ("Who are the top suppliers of laboratory analyzers?", "suppliers", top_supplier(c[c.item_type.isin(ana)])),
        ("Which vendors supplied microscopes since 2019?", "suppliers", None),
        ("How much was spent on lab and diagnostic equipment by country?", "aggregate", total(dom)),
        ("What is the total value of rapid test kit contracts?", "aggregate",
         total(c[c.item_type == "Rapid tests and test kits"])),
        ("How long does it take to award a PCR tender?", "timeline", None),
        ("How long do laboratory analyzer tenders take from invitation to award?", "timeline", None),
        ("Why were laboratory equipment deliveries delayed?", "lessons", None),
        ("What lessons did projects learn about maintaining diagnostic equipment?", "lessons", None),
        ("What risks should I plan for when procuring X-ray machines?", "lessons", None),
        ("Show the entity relationship diagram", "graph", None),
        ("How is Kenya connected to its lab equipment suppliers?", "graph", None),
        (f"Tell me about project {top_project}", "project", None),
        (f"What did {top_sup_name} supply?", "supplier", None),
    ]
    rows, t0 = [], time.time()
    for q, route, check in tests:
        a = bot.ask(q)
        rows.append({"question": q, "expected_route": route, "route": a["route"], "route_ok": a["route"] == route,
                     "exact_ok": None if check is None else bool(check(a)) if a["statements"] else False,
                     "statements": len(a["statements"]), "cited": sum(1 for s in a["statements"] if s["citations"]),
                     "abstentions": sum(1 for s in a["statements"] if s["abstention"]),
                     "dropped": len(a["guardrail_log"]), "answered": any(s["citations"] for s in a["statements"])})
    df = pd.DataFrame(rows)
    ex = df.exact_ok.dropna()
    return {"questions": len(df), "route_accuracy": round(float(df.route_ok.mean()), 3),
            "exact_checks": int(len(ex)), "exact_accuracy": round(float(ex.mean()), 3) if len(ex) else None,
            "answered_share": round(float(df.answered.mean()), 3),
            "statements": int(df.statements.sum()),
            "cited_share": round(float((df.cited + df.abstentions).sum() / max(df.statements.sum(), 1)), 3),
            "dropped_by_verifier": int(df.dropped.sum()),
            "seconds_per_question": round((time.time() - t0) / len(df), 2), "rows": rows}


def main():
    if sys.argv[1:] == ["--eval"]:
        print(json.dumps(chatbot_eval(), indent=1, default=str))
        return
    bot = ProcurementChat()
    for q in sys.argv[1:] or EXAMPLES:
        a = bot.ask(q)
        print(f"\n### {q}\n(route: {a['route']}; scope: {a['scope']}; verifier dropped {len(a['guardrail_log'])})")
        print(a["answer_md"])


if __name__ == "__main__":
    main()
