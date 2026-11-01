"""HTML pieces for the Streamlit app: brand styles, evidence badges, the lifecycle table and risk cards.

Kept apart from app.py so they can be tested without Streamlit. Colours work on light and dark themes (translucent
fills, inherited text colour).
"""
import html

BRAND = "LabProcure"
TAGLINE = "Procurement intelligence for laboratory and diagnostic equipment in World Bank health projects"

CSS = """
<style>
.lp-hero{border-radius:18px;padding:26px 30px 22px;margin:4px 0 14px;
  background:linear-gradient(120deg,rgba(79,70,229,.14),rgba(16,185,129,.12) 60%,rgba(14,165,233,.10));
  border:1px solid rgba(79,70,229,.25)}
.lp-kicker{letter-spacing:.14em;font-size:.72rem;font-weight:700;color:#4f46e5;text-transform:uppercase}
.lp-title{font-size:2.35rem;font-weight:800;line-height:1.1;margin:.15rem 0 .4rem}
.lp-title span{background:linear-gradient(90deg,#4f46e5,#10b981);-webkit-background-clip:text;background-clip:text;color:transparent}
.lp-sub{font-size:1.02rem;opacity:.85;max-width:980px}
.lp-stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.lp-stat{background:rgba(127,127,127,.08);border:1px solid rgba(127,127,127,.18);border-radius:12px;padding:8px 14px;min-width:120px}
.lp-stat b{display:block;font-size:1.35rem;line-height:1.2}
.lp-stat small{opacity:.75}
.lp-badge{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.74rem;font-weight:600;white-space:nowrap}
.b-derived{background:rgba(16,185,129,.16);color:#059669;border:1px solid rgba(16,185,129,.35)}
.b-model{background:rgba(79,70,229,.14);color:#4f46e5;border:1px solid rgba(79,70,229,.35)}
.b-evidence{background:rgba(245,158,11,.15);color:#b45309;border:1px solid rgba(245,158,11,.4)}
.b-missing{background:transparent;color:#dc2626;border:1px dashed rgba(220,38,38,.6)}
.lp-table{width:100%;border-collapse:collapse;font-size:.88rem}
.lp-table th{text-align:left;font-weight:600;opacity:.7;padding:6px 8px;border-bottom:1px solid rgba(127,127,127,.3)}
.lp-table td{padding:7px 8px;border-bottom:1px solid rgba(127,127,127,.15);vertical-align:top}
.lp-table td.v{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600;white-space:nowrap}
.lp-table tr.stage td{font-weight:700;padding-top:12px;border-bottom:none;opacity:.9}
.lp-table a{text-decoration:none}
.lp-card{border:1px solid rgba(127,127,127,.22);border-radius:14px;padding:12px 16px;margin-bottom:10px}
.lp-card h4{margin:0 0 4px;font-size:1rem}
.lp-bar{height:8px;border-radius:6px;background:rgba(127,127,127,.18);overflow:hidden;margin:6px 0}
.lp-bar>div{height:100%;background:linear-gradient(90deg,#f59e0b,#dc2626)}
.lp-step{border-left:4px solid #4f46e5;padding:6px 12px;margin:6px 0;border-radius:0 10px 10px 0;background:rgba(79,70,229,.06)}
.lp-win{border:1px solid rgba(16,185,129,.45);background:rgba(16,185,129,.08);border-radius:14px;padding:12px 16px}
</style>
"""

BADGE_CLASS = {"Derived": "b-derived", "Model": "b-model", "Evidence": "b-evidence", "Missing": "b-missing"}


def esc(s):
    return html.escape(str(s), quote=True)


def badge(text, basis=None):
    basis = basis or text.split(" ")[0]
    return f'<span class="lp-badge {BADGE_CLASS.get(basis, "b-evidence")}">{esc(text)}</span>'


def hero(stats_pairs, sub=TAGLINE):
    stats = "".join(f'<div class="lp-stat"><b>{esc(v)}</b><small>{esc(k)}</small></div>' for k, v in stats_pairs)
    return (f'<div class="lp-hero"><div class="lp-kicker">Procurement intelligence · World Bank open data</div>'
            f'<div class="lp-title">Lab<span>Procure</span></div><div class="lp-sub">{esc(sub)}</div>'
            f'<div class="lp-stats">{stats}</div></div>')


def legend():
    return ("<br>".join([badge("Derived · n", "Derived") + " a statistic over stored records, with how many",
                      badge("Model") + " a model estimate with its tested accuracy",
                      badge("Evidence") + " quoted or counted from project documents",
                      badge("Missing") + " not in public data, and we say so"]))


def figures_html(figs):
    """Lifecycle figures grouped by stage: figure | value | basis badge | how it was worked out (+ links)."""
    rows, stage = [], None
    for f in figs:
        if f.stage != stage:
            stage = f.stage
            rows.append(f'<tr class="stage"><td colspan="3">{esc(stage)}</td></tr>')
        links = " ".join(f'<a href="{esc(u)}" target="_blank">↗ {esc(lbl)}</a>' for lbl, u in f.links if u)
        value = esc(f.value) if f.basis != "Missing" else badge("Missing")
        b = "" if f.basis == "Missing" else badge(f.badge(), f.basis) + " "
        rows.append(f"<tr><td>{esc(f.figure)}</td><td class='v'>{value}</td>"
                    f"<td>{b}{esc(f.how)}{('<br>' + links) if links else ''}</td></tr>")
    return ('<table class="lp-table"><thead><tr><th>Stage and figure</th><th>Estimate</th><th>Worked out from</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def risk_card(rank, r):
    pct = max(0.0, min(1.0, float(r["how often"])))
    signal = " · <i>" + esc(r["signal"]) + "</i>" if r.get("signal") else ""
    n_badge = badge("Derived · n={:,}".format(int(r["n"])), "Derived")
    return ('<div class="lp-card"><h4>{}. {} — {:.0%} of comparable tenders</h4>'
            '<div class="lp-bar"><div style="width:{:.0f}%"></div></div>'
            '{} <small>{}{}</small><br><small><b>What helps:</b> {}</small></div>').format(
        rank, esc(r["risk"]), pct, pct * 100, n_badge, esc(r["scope"]), signal, esc(r["watch for"]))


def lesson_card(lsn):
    return (f'<div class="lp-step">“{esc(lsn["quote"])}”<br><small>{badge("Evidence")} '
            f'<a href="{esc(lsn["url"])}" target="_blank">{esc(lsn["doc"])} · {esc(lsn["project"])} · p. {lsn["page"]} ↗</a>'
            f' · stage: {esc(str(lsn["stage"]).replace("_", " "))}</small></div>')
