"""Builds deliverables/site/: the chatbot and the report as an ordinary two-page website for any static host
(Netlify, GitHub Pages, a university server). Upload the folder as it is.

    python src/build_web_chatbot.py && python src/build_share_page.py
    python src/build_site.py

    site/index.html    the chatbot (exact, cited answers; runs entirely in the browser, no server needed)
    site/report.html   the assessment report
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEL = ROOT / "deliverables"
SITE = DEL / "site"

BASE_CSS = ("html{-webkit-text-size-adjust:100%}:root{padding-top:env(safe-area-inset-top,0px);"
            "padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}")


def standalone(page, link_html, anchor):
    """Wrap an artifact body (title, meta, links and style first) into a complete HTML document."""
    page = re.sub(r'<meta name="claude:[^"]*"[^>]*>\s*', "", page)
    page = re.sub(r"[ \t]*/\* @hosted \*/.*?/\* @end-hosted \*/\n", "", page, flags=re.S)   # model-hosting code
    page = re.sub(r'\s*<div class="controls" hidden>.*?<span id="mode-note"></span>\s*</div>', "", page, count=1, flags=re.S)
    split = page.index(anchor)
    head, body = page[:split], page[split:]
    body = body.replace(anchor, anchor + link_html, 1) if link_html else body
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"<style>{BASE_CSS}</style>\n{head.strip()}\n</head>\n<body>\n{body.strip()}\n</body>\n</html>\n")


def main():
    SITE.mkdir(parents=True, exist_ok=True)
    chat = (DEL / "web_chatbot" / "index.html").read_text(encoding="utf-8")
    report = (DEL / "share" / "index.html").read_text(encoding="utf-8")
    nav_css = ("<style>.sitenav{max-width:920px;margin:0 auto;padding-top:14px;font:14px 'IBM Plex Sans',system-ui,sans-serif}"
               ".sitenav a{color:var(--accent)}</style>")
    (SITE / "index.html").write_text(standalone(
        chat.replace("</style>", "</style>" + nav_css, 1),
        '<nav class="sitenav"><a href="report.html">Read the full assessment report &rarr;</a></nav>', '<div class="wrap">'),
        encoding="utf-8")
    (SITE / "report.html").write_text(standalone(
        report.replace("</style>", "</style>" + nav_css.replace("920px", "980px"), 1),
        '<nav class="sitenav"><a href="index.html">&larr; Ask the procurement assistant</a></nav>', '<div class="page">'),
        encoding="utf-8")
    left = [f.name for f in SITE.glob("*.html") if re.search(r"claude|anthropic|@hosted|mode-ai", f.read_text(encoding="utf-8"), re.I)]
    print(f"wrote {SITE}: index.html (chatbot), report.html; files with hosting references left: {left or 'none'}")


if __name__ == "__main__":
    main()
