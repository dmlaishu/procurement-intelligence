"""Builds deliverables/share/index.html: the assessment report as one shareable web page (published as an Artifact).

    python src/build_deliverables.py     # first: writes deliverables/04_REPORT.md and er_diagram.svg
    python src/build_share_page.py

Content comes from 04_REPORT.md and the Gold tables, so the page and the PDF always carry the same numbers.
"""
import html
import re
import sys
from datetime import date
from pathlib import Path

import markdown
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from procurement_demo.common import CFG, GOLD  # noqa: E402

DEL = ROOT / "deliverables"
OUT = DEL / "share"

STYLE = """
/* Layout: one reading column (about 72ch) with full-width bands for the figures strip, chart and diagram. */
:root {
  --paper: #f5f7f8; --sheet: #ffffff; --ink: #18232b; --muted: #55656f; --line: #d6dee2;
  --accent: #0b6e69; --accent-soft: #dcefed; --flag: #9a5b00; --code: #eef2f4;
  --display: "Spectral", Georgia, "Times New Roman", serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", Consolas, "Courier New", monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #10171b; --sheet: #172127; --ink: #e3eaee; --muted: #9aaab3; --line: #2b3a42;
  --accent: #4fc2b8; --accent-soft: #173733; --flag: #e3a84a; --code: #1d2a31; color-scheme: dark } }
:root[data-theme="dark"] {
  --paper: #10171b; --sheet: #172127; --ink: #e3eaee; --muted: #9aaab3; --line: #2b3a42;
  --accent: #4fc2b8; --accent-soft: #173733; --flag: #e3a84a; --code: #1d2a31; color-scheme: dark }
body { background: var(--paper); color: var(--ink); font-family: var(--body); font-size: 16px; line-height: 1.6;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.page { max-width: 980px; margin: 0 auto; padding-block: 40px 64px; }
.col { max-width: 72ch; }
header.top { display: grid; gap: 14px; padding-bottom: 28px; border-bottom: 1px solid var(--line); }
.eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
h1 { font-family: var(--display); font-weight: 600; font-size: clamp(30px, 5vw, 46px); line-height: 1.1; margin: 0; text-wrap: balance; }
.lede { font-size: 18px; color: var(--muted); margin: 0; max-width: 64ch; }
.figures { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: 6px; overflow: hidden; margin: 28px 0 8px; }
.figures div { background: var(--sheet); padding: 14px 16px; display: grid; gap: 2px; }
.figures b { font-family: var(--mono); font-size: 24px; font-weight: 500; font-variant-numeric: tabular-nums; }
.figures span { font-size: 13px; color: var(--muted); }
@media (max-width: 640px) { .figures { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
nav.toc { margin: 28px 0 8px; display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 14px; }
nav.toc a { color: var(--accent); text-decoration: none; border-bottom: 1px solid transparent; }
nav.toc a:hover, nav.toc a:focus-visible { border-bottom-color: var(--accent); outline: none; }
.deliv { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }
.deliv div { border: 1px solid var(--line); background: var(--sheet); border-radius: 6px; padding: 14px 16px; min-width: 0; }
.deliv b { display: block; font-family: var(--display); font-size: 19px; margin-bottom: 4px; }
.deliv p { margin: 0; font-size: 14px; color: var(--muted); }
.deliv code { font-size: 12px; }
@media (max-width: 640px) { .deliv { grid-template-columns: 1fr; } }
main h2 { font-family: var(--display); font-weight: 600; font-size: 28px; line-height: 1.2; margin: 48px 0 12px;
  padding-top: 16px; border-top: 1px solid var(--line); text-wrap: balance; }
main h3 { font-family: var(--display); font-weight: 600; font-size: 21px; margin: 32px 0 8px; }
main h4 { font-size: 15px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: 22px 0 6px; }
main p, main ul, main ol { max-width: 72ch; }
main li { margin: 4px 0; }
a { color: var(--accent); }
code { font-family: var(--mono); font-size: 13px; background: var(--code); padding: 1px 5px; border-radius: 3px; }
pre { background: var(--code); padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
pre code { padding: 0; background: none; }
.tbl { overflow-x: auto; margin: 14px 0; border: 1px solid var(--line); border-radius: 6px; background: var(--sheet); }
table { border-collapse: collapse; width: 100%; font-size: 14px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; vertical-align: top; padding: 8px 12px; border-bottom: 1px solid var(--line); }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); font-weight: 600; background: var(--code); }
tr:last-child td { border-bottom: 0; }
blockquote { margin: 14px 0; padding: 10px 16px; border-left: 3px solid var(--accent); background: var(--accent-soft);
  border-radius: 0 6px 6px 0; max-width: 80ch; }
blockquote p { margin: 6px 0; }
.chart { display: grid; gap: 8px; margin: 18px 0 6px; }
.bar { display: grid; grid-template-columns: minmax(0, 15rem) minmax(0, 1fr) 6.5rem; gap: 12px; align-items: center; font-size: 14px; }
.bar .lab { overflow-wrap: anywhere; }
.bar .track { height: 12px; background: var(--code); border-radius: 2px; }
.bar .fill { height: 100%; background: var(--accent); border-radius: 2px; }
.bar .val { font-family: var(--mono); font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 640px) { .bar { grid-template-columns: minmax(0, 1fr) 5.5rem; } .bar .track { grid-column: 1 / -1; order: 3; } }
.caption { font-size: 13px; color: var(--muted); }
.diagram { overflow-x: auto; background: #ffffff; border: 1px solid var(--line); border-radius: 6px; padding: 12px; margin: 14px 0; }
.diagram svg { min-width: 760px; width: 100%; height: auto; display: block; }
footer { margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--line); font-size: 13px; color: var(--muted); display: grid; gap: 6px; }
@media (prefers-reduced-motion: no-preference) { nav.toc a { transition: border-color .15s; } }
"""


def spend_chart():
    c = pd.read_parquet(GOLD / "fact_contract.parquet")
    d = c[c.in_domain].groupby("item_type").amount_usd.sum().sort_values(ascending=False)
    top = d.iloc[0]
    rows = [f'<div class="bar"><span class="lab">{html.escape(k)}</span><span class="track"><span class="fill" '
            f'style="display:block;width:{v / top * 100:.1f}%"></span></span><span class="val">US${v / 1e6:,.1f}M</span></div>'
            for k, v in d.items()]
    return ('<div class="chart" role="img" aria-label="Contract value by equipment type">' + "".join(rows) + "</div>"
            '<p class="caption">Contract value by equipment type, all countries, FY2015 onwards. Source: World Bank Major '
            'Contract Awards, classified by the prototype.</p>')


def main():
    md = (DEL / "04_REPORT.md").read_text(encoding="utf-8")
    md = md.split("\n", 1)[1]                                    # drop the "Deliverable 4" H1; the page has its own
    md = re.sub(r"^\*\*AI-powered procurement intelligence[^\n]*\n[^\n]*\*\*\n", "", md, flags=re.M)
    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    body = body.replace("<table>", '<div class="tbl"><table>').replace("</table>", "</table></div>")
    # give each section an anchor for the contents list
    toc = []

    def anchor(m):
        title = re.sub(r"<[^>]+>", "", m.group(1))
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
        toc.append((slug, re.sub(r"^\d+\.\s*", "", title)))
        return f'<h2 id="{slug}">{m.group(1)}</h2>'
    body = re.sub(r"<h2>(.*?)</h2>", anchor, body)
    # the spend chart goes right under the 4.1 heading
    body = body.replace("<h3>4.1 Spend by equipment type</h3>", "<h3>4.1 Spend by equipment type</h3>" + spend_chart(), 1)
    svg = (DEL / "er_diagram.svg").read_text(encoding="utf-8")

    c = pd.read_parquet(GOLD / "fact_contract.parquet")
    dom = c[c.in_domain]
    import json
    ev = json.loads((ROOT / "procurement_demo" / "output" / "health_lab" / "eval_results.json").read_text(encoding="utf-8"))
    chat = ev.get("chatbot", {})
    figures = [(f"{len(dom):,}", "lab and diagnostic contracts"), (f"US${dom.amount_usd.sum() / 1e9:.2f}B", "contract value"),
               (f"{dom.country_iso3.nunique()}", "countries"),
               (f"{chat.get('route_accuracy', 0) * chat.get('questions', 0):.0f}/{chat.get('questions', 0)}", "chatbot questions routed correctly")]
    fig_html = "".join(f"<div><b>{v}</b><span>{html.escape(k)}</span></div>" for v, k in figures)
    toc_html = "".join(f'<a href="#{s}">{html.escape(t)}</a>' for s, t in toc)

    page = f"""<title>Lab Diagnostics Procurement Intelligence</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&family=Spectral:wght@500;600&display=swap">
<style>{STYLE}</style>
<div class="page">
  <header class="top">
    <span class="eyebrow">Technical assessment · AI-powered procurement intelligence</span>
    <h1>Procurement intelligence for laboratory and diagnostic equipment</h1>
    <p class="lede">{html.escape(CFG['label'])}: what was built, what the World Bank's public data shows, and what comes next.
    Report generated {date.today():%d %B %Y} from a working prototype run on public World Bank data.</p>
  </header>
  <div class="figures">{fig_html}</div>
  <section class="col" aria-labelledby="deliverables">
    <h2 id="deliverables" style="font-family:var(--display);font-size:24px;margin:32px 0 4px">The four deliverables</h2>
  </section>
  <div class="deliv">
    <div><b>1 · Data model</b><p>How contracts, notices, projects, suppliers, equipment types, governance and evaluation
      reports connect. The diagram is below; the full dictionary and table definitions are in <code>01_DATA_MODEL.pdf</code>.</p></div>
    <div><b>2 · Approach</b><p>The technical design for the four assessment modules, the equipment taxonomy and the
      chatbot, in <code>02_APPROACH.pdf</code>.</p></div>
    <div><b>3 · Prototype</b><p>The working pipeline and a chatbot that answers with cited evidence. It runs locally:
      <code>python -m streamlit run procurement_demo/app.py</code></p></div>
    <div><b>4 · Report</b><p>This page: findings, measured results, limitations and next steps
      (also as <code>04_REPORT.pdf</code>).</p></div>
  </div>
  <nav class="toc" aria-label="Contents">{toc_html}<a href="#data-model">Data model diagram</a></nav>
  <main>
    {body}
    <h2 id="data-model">Data model diagram</h2>
    <p>Arrows point from the many side to the one side; dashed lines are derivations. Every link is also stored as a row
    in the knowledge-graph edge table, with the method that created it and a confidence.</p>
    <div class="diagram">{svg}</div>
  </main>
  <footer>
    <span>Data: World Bank Major Contract Awards, Procurement Notices, Projects &amp; Operations, Documents &amp; Reports,
    and WDI governance indicators (CPIA, WGI). All public.</span>
    <span>Every figure on this page comes from the prototype run; none is typed by hand.</span>
  </footer>
</div>
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    print(f"wrote {OUT / 'index.html'} ({len(page) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
