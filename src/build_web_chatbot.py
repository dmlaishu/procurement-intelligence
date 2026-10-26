"""Builds deliverables/web_chatbot/index.html: the procurement chatbot as one self-contained web page (published as an
Artifact, so it can be shared by link). The page carries its own data and runs the engine in the browser
(src/web_chatbot/engine.js, a port of procurement_demo/chatbot.py). Where the host provides a language model the page
also offers AI answers through the engine's tools; on a plain web host (deliverables/site/) it answers by exact rules.

    python -m procurement_demo.run_all        # Gold tables
    python src/build_deliverables.py          # er_diagram.svg
    python src/build_web_chatbot.py
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from procurement_demo.chatbot import OUT_OF_DOMAIN_WORDS, ProcurementChat  # noqa: E402
from procurement_demo.common import CFG, GOLD  # noqa: E402

SRC = ROOT / "src" / "web_chatbot"
OUT = ROOT / "deliverables" / "web_chatbot"


def clean(x):
    return None if x is None or (isinstance(x, float) and pd.isna(x)) else x


def export():
    bot = ProcurementChat()
    c = bot.contracts[bot.contracts.in_domain & bot.contracts.amount_usd.notna()].copy()
    c["d"] = c.signing_date.dt.strftime("%Y-%m-%d").fillna("")
    contracts = [{"n": r.wb_contract_number, "s": r.supplier_name, "sk": r.supplier_key, "sc": clean(r.supplier_country) or "",
                  "a": round(float(r.amount_usd), 2), "d": r.d, "c": r.country_iso3, "r": clean(r.region) or "",
                  "p": r.project_id, "t": r.item_type, "m": r.procurement_method or "", "v": clean(r.review_type) or "",
                  "x": " ".join(str(r.description).split())[:200]} for r in c.itertuples()]
    present = set(c.country_iso3)
    country_name = {k: v for k, v in bot.country_name.items() if k in present}
    alias = sorted(([a, i] for a, i in bot.country_alias.items() if i in present), key=lambda x: -len(x[0]))
    domain_keys = set(c.supplier_key)
    supplier_keys = sorted(([k, key] for k, key in bot.supplier_keys if key in domain_keys), key=lambda x: -len(x[0]))
    P = bot.projects
    projects = [{"id": r.project_id, "name": clean(r.project_name) or "", "iso3": clean(r.country_iso3) or "REG",
                 "region": (clean(r.region) or "").strip(), "status": clean(r.status) or "",
                 "approved": r.approval_date.strftime("%Y-%m-%d") if pd.notna(r.approval_date) else "n/a",
                 "commitment": clean(r.commitment_usd), "url": clean(r.project_url) or ""} for r in P.itertuples()]
    rt = pd.read_parquet(GOLD / "project_ratings_resolved.parquet")
    ratings = [{"project_id": r.project_id, "resolved": r.resolved_rating, "from": r.resolved_from, "conflict": bool(r.conflict),
                "worst": clean(r.worst_isr), "trajectory": "; ".join(f"{s} {d}: {v}" for s, d, v in json.loads(r.trajectory))}
               for r in rt.itertuples()]
    docs = [{"id": r.doc_id, "type": r.doc_type_code, "project": r.project_id, "title": r.title, "date": r.doc_date,
             "url": r.landing_url} for r in bot.docs.itertuples()]
    L = bot.kb.lessons
    lessons = [{"doc": r.doc_id, "doc_type": r.doc_type_code, "project": r.project_id, "page": int(r.page_start),
                "quote": r.evidence_quote, "detail": r.detail[:900], "title": r.title, "url": r.landing_url,
                "iso3": r.country_iso3} for r in L.itertuples()]
    ch = bot.kb.chunks
    sel = ch[ch.section_kind.isin(["lessons", "key_factors", "procurement_fiduciary", "risk", "results", "design"])
             & (ch.content_type != "table") & (ch.doc_type_code != "PP")]
    passages = [{"doc": r.doc_id, "doc_type": r.doc_type_code, "project": r.project_id, "page": int(r.page_start),
                 "section": r.section_path.split(" > ")[-1][:80], "text": r.chunk_text[:1400], "title": r.title,
                 "url": r.landing_url, "iso3": r.country_iso3} for r in sel.itertuples()]
    b = pd.read_parquet(GOLD / "tender_benchmarks.parquet")
    b = b[b.level.isin(["item_type", "category"])]
    benchmarks = [{"level": r.level, "item_type": clean(getattr(r, "item_type", None)), "category": clean(r.procurement_category),
                   "n": int(r.n), "n_awarded": int(r.n_awarded), "median": float(r.median_days), "p80": clean(r.p80_days),
                   "naive": float(r.naive_median_awarded_only)} for r in b.itertuples()]
    edges = bot.edges.groupby(["src_type", "rel", "dst_type"]).size().sort_values(ascending=False)
    edge_counts = [[f"{a} {r} {d}", int(n)] for (a, r, d), n in edges.items()]
    er = (ROOT / "deliverables" / "er_diagram.svg").read_text(encoding="utf-8")
    regions = sorted({p["region"] for p in projects if p["region"]})
    return {
        "contracts": contracts, "countryName": country_name, "countryAlias": alias, "regions": regions,
        "itemTypes": [[n, p, n in CFG["domain_items"]] for n, p in CFG["item_types"]],
        "outOfDomainWords": OUT_OF_DOMAIN_WORDS, "domainItems": sorted(CFG["domain_items"]),
        "supplierKeys": supplier_keys, "projects": projects, "ratings": ratings, "docs": docs, "lessons": lessons,
        "passages": passages, "benchmarks": benchmarks, "edgeCounts": edge_counts, "erSvg": er,
        "stats": {"contracts": len(contracts), "value": float(c.amount_usd.sum()), "countries": int(c.country_iso3.nunique()),
                  "projects": int(c.project_id.nunique()), "suppliers": int(c.supplier_key.nunique()),
                  "docs": int(ch.doc_id.nunique()), "passages": len(passages), "built": date.today().strftime("%d %B %Y")},
    }


STYLE = r"""
/* Layout: one centred conversation column; header with the data summary, sticky ask bar at the bottom. */
:root {
  --paper: #f4f6f7; --sheet: #ffffff; --ink: #17222a; --muted: #56656f; --line: #d5dde1; --code: #edf1f3;
  --accent: #0b6e69; --accent-ink: #ffffff; --accent-soft: #ddefed; --warn: #9a5b00; --warn-soft: #fbf0dc;
  --g-proj: #dbe8f7; --g-sup: #dcefdf; --g-ctry: #f8ead2; --g-eq: #ebe3f6; --g-doc: #f5e1d6; --g-line: #8796a0;
  --display: "Spectral", Georgia, "Times New Roman", serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", Consolas, "Courier New", monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0f161a; --sheet: #162027; --ink: #e2e9ed; --muted: #9babb4; --line: #2a3941; --code: #1c2830;
  --accent: #4fc2b8; --accent-ink: #062b28; --accent-soft: #163632; --warn: #e6ab4f; --warn-soft: #33270f;
  --g-proj: #1f3048; --g-sup: #1e3a27; --g-ctry: #3d3018; --g-eq: #2f2742; --g-doc: #3d2a20; --g-line: #6d7d86; color-scheme: dark } }
:root[data-theme="dark"] {
  --paper: #0f161a; --sheet: #162027; --ink: #e2e9ed; --muted: #9babb4; --line: #2a3941; --code: #1c2830;
  --accent: #4fc2b8; --accent-ink: #062b28; --accent-soft: #163632; --warn: #e6ab4f; --warn-soft: #33270f;
  --g-proj: #1f3048; --g-sup: #1e3a27; --g-ctry: #3d3018; --g-eq: #2f2742; --g-doc: #3d2a20; --g-line: #6d7d86; color-scheme: dark }
html, body { height: 100%; }
body { background: var(--paper); color: var(--ink); font-family: var(--body); font-size: 15.5px; line-height: 1.55;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.wrap { max-width: 920px; margin: 0 auto; padding-block: 28px 140px; }
header { display: grid; gap: 10px; }
.eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
h1 { font-family: var(--display); font-weight: 600; font-size: clamp(28px, 4.6vw, 40px); line-height: 1.12; margin: 0; text-wrap: balance; }
.lede { margin: 0; color: var(--muted); max-width: 66ch; }
.stats { display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 13.5px; color: var(--muted); margin-top: 4px; }
.stats b { font-family: var(--mono); font-weight: 500; color: var(--ink); font-variant-numeric: tabular-nums; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-top: 18px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--sheet); }
.seg button { font: 500 13.5px var(--body); border: 0; background: transparent; color: var(--ink); padding: 7px 14px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--accent-ink); }
.seg button:disabled { color: var(--muted); cursor: not-allowed; }
.seg button:focus-visible, .example:focus-visible, .chip:focus-visible, #send:focus-visible, #stop:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
#mode-note { font-size: 13px; color: var(--muted); flex: 1 1 280px; min-width: 0; }
.examples { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 6px; }
.example { font: 13.5px var(--body); text-align: left; border: 1px solid var(--line); background: var(--sheet); color: var(--ink);
  border-radius: 999px; padding: 6px 13px; cursor: pointer; }
.example:hover { border-color: var(--accent); }
#thread { display: grid; gap: 22px; margin-top: 22px; }
.turn { display: grid; gap: 10px; min-width: 0; }
.you { margin: 0; justify-self: end; max-width: 80%; background: var(--accent-soft); padding: 9px 14px; border-radius: 12px 12px 2px 12px; }
.who { display: none; }
.ans { background: var(--sheet); border: 1px solid var(--line); border-radius: 10px; padding: 14px 18px; min-width: 0; }
.ans p { margin: 6px 0; } .ans ul { margin: 4px 0 8px; padding-left: 20px; } .ans li { margin: 4px 0; }
.sec { font-size: 12px !important; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; margin-top: 12px !important; }
.thinking { color: var(--muted); font-style: italic; }
.opener { font-weight: 500; margin-top: 0 !important; }
.lead { font-size: 16px; }
.hello p:first-child { margin-top: 0; }
.more { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--line); font-size: 13px; color: var(--muted); }
.example.follow { font-size: 13px; padding: 4px 11px; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; margin-bottom: 6px; }
.badge { font-size: 12px; font-weight: 600; padding: 3px 9px; border-radius: 999px; }
.badge.ok { background: var(--accent-soft); color: var(--accent); }
.badge.warn { background: var(--warn-soft); color: var(--warn); }
.scope { font-size: 12.5px; color: var(--muted); }
.note { color: var(--warn); font-size: 13.5px; }
.chip { font: 600 11.5px var(--mono); border: 1px solid var(--accent); color: var(--accent); background: transparent; border-radius: 4px;
  padding: 0 5px; margin-left: 2px; cursor: pointer; vertical-align: 1px; }
.chip:hover { background: var(--accent-soft); }
code { font-family: var(--mono); font-size: 13px; background: var(--code); padding: 1px 4px; border-radius: 3px; }
details { margin-top: 10px; } summary { cursor: pointer; font-size: 13.5px; color: var(--accent); font-weight: 600; }
.evidence ol { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; }
.evidence li { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr); gap: 8px; padding: 8px; border-radius: 6px; background: var(--code); }
.evidence li.flash { outline: 2px solid var(--accent); }
.eid { font: 600 12px var(--mono); color: var(--accent); }
.esrc { font-size: 12.5px; color: var(--muted); }
.etext { font-size: 13px; overflow-wrap: anywhere; }
.dropped li { font-size: 13px; color: var(--muted); }
.tbl { overflow-x: auto; margin: 10px 0; border: 1px solid var(--line); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--code); font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
tr:last-child td { border-bottom: 0; }
.bars { display: grid; gap: 6px; margin: 10px 0; }
.bar { display: grid; grid-template-columns: minmax(0, 13rem) minmax(0, 1fr) 5.5rem; gap: 10px; align-items: center; font-size: 13px; }
.bar .lab { overflow-wrap: anywhere; } .bar .track { height: 10px; background: var(--code); border-radius: 2px; }
.bar .fill { display: block; height: 100%; background: var(--accent); border-radius: 2px; }
.bar .val { font-family: var(--mono); font-size: 12.5px; text-align: right; }
.graph { overflow-x: auto; margin: 10px 0; border: 1px solid var(--line); border-radius: 6px; padding: 6px; background: var(--paper); }
.graph svg { display: block; max-width: none; }
.graph.er { background: #ffffff; } .graph.er svg { min-width: 760px; width: 100%; height: auto; }
.gcol { font: 600 10.5px var(--mono); letter-spacing: .06em; fill: var(--muted); }
.gedge { fill: none; stroke: var(--g-line); stroke-width: 1.4; } .gedge:hover { stroke: var(--accent); stroke-width: 2.4; }
.gnode rect { stroke: var(--g-line); stroke-width: .8; fill: var(--sheet); }
.gnode text { font: 11.5px var(--body); fill: var(--ink); }
.t-project rect { fill: var(--g-proj); } .t-supplier rect { fill: var(--g-sup); } .t-country rect, .t-region rect { fill: var(--g-ctry); }
.t-equipment rect { fill: var(--g-eq); } .t-report rect { fill: var(--g-doc); }
.hint { font-size: 12px; color: var(--muted); margin: 0; }
.bar-ask { position: fixed; left: 0; right: 0; bottom: 0; background: var(--paper); border-top: 1px solid var(--line);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 12px calc(12px + env(safe-area-inset-bottom, 0px)); }
#ask { max-width: 920px; margin: 0 auto; display: flex; gap: 8px; align-items: flex-end; }
#q { flex: 1; min-width: 0; resize: none; font: 15px var(--body); color: var(--ink); background: var(--sheet); border: 1px solid var(--line);
  border-radius: 10px; padding: 10px 12px; max-height: 120px; }
#q:focus { outline: 2px solid var(--accent); outline-offset: 0; border-color: transparent; }
#send, #stop { font: 600 14px var(--body); border-radius: 10px; padding: 10px 16px; cursor: pointer; border: 1px solid var(--accent); }
#send { background: var(--accent); color: var(--accent-ink); } #send:disabled { opacity: .55; cursor: wait; }
#stop { background: transparent; color: var(--accent); }
footer { margin-top: 36px; font-size: 12.5px; color: var(--muted); display: grid; gap: 4px; }
@media (max-width: 600px) { .you { max-width: 92%; } .bar { grid-template-columns: minmax(0, 1fr) 5rem; } .bar .track { grid-column: 1 / -1; order: 3; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

EXAMPLES = [
    "Which supplier won the cheapest GeneXpert or PCR contract?",
    "Who are the top suppliers of diagnostic imaging equipment in Sub-Saharan Africa?",
    "How much was spent on lab equipment by country?",
    "How long does it take to award a laboratory analyzer tender?",
    "What problems did projects have with laboratory equipment maintenance?",
    "How is Kenya connected to its lab equipment suppliers?",
    "What are the largest rapid test kit contracts since 2020?",
    "Show the entity relationship diagram",
]


def nan_to_null(x):
    """JSON has no NaN: missing values (e.g. regional projects with no country) become null."""
    if isinstance(x, float) and x != x:
        return None
    if isinstance(x, dict):
        return {k: nan_to_null(v) for k, v in x.items()}
    if isinstance(x, list):
        return [nan_to_null(v) for v in x]
    return x


def main():
    data = nan_to_null(export())
    json.dumps(data, allow_nan=False)            # fails loudly if anything non-JSON is left
    s = data["stats"]
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    examples = "".join(f'<button type="button" class="example">{q}</button>' for q in EXAMPLES)
    page = f"""<title>Lab Diagnostics Procurement Assistant</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&family=Spectral:wght@600&display=swap">
<style>{STYLE}</style>
<div class="wrap">
  <header>
    <span class="eyebrow">Procurement intelligence · World Bank open data</span>
    <h1>Ask about laboratory and diagnostic equipment procurement</h1>
    <p class="lede">Suppliers, prices, spend, tender times, lessons from project evaluations and how it all connects, for
    lab and diagnostic equipment bought in World Bank-financed health projects. Every answer shows where it comes from.</p>
    <div class="stats"><span><b>{s['contracts']:,}</b> contracts</span><span><b>US${s['value'] / 1e9:.2f}B</b> value</span>
      <span><b>{s['countries']}</b> countries</span><span><b>{s['projects']}</b> projects</span>
      <span><b>{s['suppliers']:,}</b> suppliers</span><span><b>{s['docs']}</b> project reports</span></div>
  </header>
  <div class="controls" hidden>
    <div class="seg" role="group" aria-label="Answer mode">
      <button type="button" id="mode-ai" aria-pressed="false">AI answers</button>
      <button type="button" id="mode-exact" aria-pressed="true">Exact</button>
    </div>
    <span id="mode-note"></span>
  </div>
  <div class="examples" aria-label="Example questions">{examples}</div>
  <section id="thread" aria-live="polite"></section>
  <footer>
    <span>Data: World Bank Major Contract Awards, Procurement Notices, Projects &amp; Operations and Documents &amp; Reports
    (public), FY2015 onwards, classified and linked by the prototype on {s['built']}.</span>
    <span>Every answer links to the contracts and reports it comes from, so you can check it yourself.</span>
  </footer>
</div>
<div class="bar-ask">
  <form id="ask" autocomplete="off">
    <label for="q" class="who">Your question</label>
    <textarea id="q" rows="1" placeholder="Ask about suppliers, prices, spend, tender times, lessons or relationships…"></textarea>
    <button type="button" id="stop" hidden>Stop</button>
    <button type="submit" id="send">Ask</button>
  </form>
</div>
<script type="application/json" id="data">{blob}</script>
<script>{(SRC / "engine.js").read_text(encoding="utf-8")}</script>
<script>{(SRC / "ui.js").read_text(encoding="utf-8")}</script>
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")   # for the Node test
    print(f"wrote {OUT / 'index.html'} ({len(page) / 1e6:.1f} MB): {s['contracts']} contracts, {len(data['lessons'])} lessons, "
          f"{len(data['passages'])} passages")


if __name__ == "__main__":
    main()
