# Databricks notebook source
# MAGIC %md
# MAGIC # 20_proc_lib - shared procurement helpers (pure Python)
# MAGIC Loaded by the procurement notebooks with `%run ./20_proc_lib`. No Spark here, so every function is
# MAGIC unit-tested locally (`python src/test_proc_lib.py`) against real World Bank notices and an ICR.
# MAGIC
# MAGIC | Area | Functions |
# MAGIC |---|---|
# MAGIC | Entity resolution | `norm_ref`, `norm_supplier`, `name_similarity`, `parse_wb_date` |
# MAGIC | Contract award notices (HTML) | `html_to_text`, `parse_award_notice`, `is_rebid` |
# MAGIC | Long reports (ICR / PAD / ISR / PPAR) | `strip_running_lines`, `split_sections`, `section_chunks`, `parse_icr_datasheet` |
# MAGIC | Survival benchmarks | `kaplan_meier` |
# MAGIC | Guardrails | `quote_in_source`, `numbers_in`, `unsupported_numbers`, `resolve_rating_conflict` |
# MAGIC | Domain taxonomy | `ITEM_TYPES_LAB`, `LAB_DOMAIN_ITEMS`, `classify_item` (lab and diagnostic equipment) |
# MAGIC | IEG ratings | `parse_icrr_ratings`, `parse_ppar_rating` |

# COMMAND ----------

import datetime as _dt
import html as _html
import math as _math
import re as _re
from collections import Counter as _Counter
from difflib import SequenceMatcher as _SequenceMatcher

# ── Entity resolution ──────────────────────────────────────────────────────────────────────────

def _s(x):
    """Text or '' (pandas hands missing strings over as NaN floats, Spark as None)."""
    return x if isinstance(x, str) else ""


def norm_ref(ref):
    """Bid/contract reference → join key. 'KE-MOWASCO-16771-CW-RFB' and 'ke mowasco 16771 cw/rfb' match."""
    key = _re.sub(r"[^A-Z0-9]", "", _s(ref).upper())
    return key if len(key) >= 5 else None   # 'N/A', '1', '' are not usable keys


_LEGAL_FORMS = (r"\b(limited|ltd|plc|llc|inc|incorporated|corp|corporation|co|company|sa|sarl|sas|srl|spa|gmbh|ag|bv|nv"
                r"|pvt|private|pte|pty|jsc|ojsc|cjsc|ooo|llp|lp|ag|kg|ab|as|oy|sl|cv|de|of|the|and|group|holdings?)\b")


def norm_supplier(name):
    """Supplier name → resolution key: drops M/S, legal forms, punctuation, joint-venture partner order.
    'M/S. AIRES INGEGNERIA S.r.l.' → 'aires ingegneria'; 'China Wu Yi Co., Ltd' → 'china wu yi'."""
    if not _s(name):
        return None
    s = name.lower()
    s = _re.sub(r"\(\s*\d+\s*\)", " ", s)                  # trailing supplier ids '(1133796)'
    s = _re.sub(r"^\s*m\s*/\s*s\.?\s*", " ", s)             # 'M/S.' prefix
    s = s.replace("&", " and ")
    s = _re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", s)        # 's.r.l.' → 'srl'
    s = _re.sub(r"[^a-z0-9 ]", " ", s)
    s = _re.sub(_LEGAL_FORMS, " ", s)
    s = " ".join(s.split())
    if " jv " in f" {s} " or "joint venture" in s:          # JV partners in any order → same key
        parts = [p.strip() for p in _re.split(r"\bjv\b|joint venture|\band\b", s) if p.strip()]
        s = "jv " + " + ".join(sorted(parts))
    return s or None


def name_similarity(a, b):
    """0-1 similarity of two normalised supplier names (token-set + sequence ratio, max of the two)."""
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb)
    return max(jacc, _SequenceMatcher(None, a, b).ratio())


def parse_wb_date(s):
    """World Bank APIs use '20-Oct-2026', '2026-12-10T00:00:00Z', '12/31/2029 12:00:00 AM' and '2026/08/27'."""
    if not isinstance(s, (str, _dt.date)) or not s:
        return None
    if isinstance(s, _dt.date):
        return s if not isinstance(s, _dt.datetime) else s.date()
    s = s.strip()
    for fmt, n in (("%d-%b-%Y", 11), ("%Y-%m-%d", 10), ("%m/%d/%Y", 10), ("%Y/%m/%d", 10)):
        try:
            return _dt.datetime.strptime(s[:n].strip(), fmt).date()
        except ValueError:
            pass
    m = _re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return _dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None

# ── Contract award notices ─────────────────────────────────────────────────────────────────────

def html_to_text(h):
    """Notice HTML → plain text lines (keeps line structure from <br>, <p>, <div>)."""
    if not _s(h):
        return ""
    t = _re.sub(r"(?i)<\s*(br|/p|/div|/h\d|/li|/tr)\s*/?>", "\n", h)
    t = _re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t).replace("\xa0", " ")
    lines = [" ".join(l.split()) for l in t.split("\n")]
    return "\n".join(l for l in lines if l)


_BIDDER_RE = _re.compile(r"^(?P<name>.+?)\s*\((?P<id>\d{3,})\)\s*$")
_AMOUNT_RE = r"([A-Z]{3})\s*([\d,]+(?:\.\d+)?)"


def _section(text, label):
    """Text after 'Awarded Bidder(s):' / 'Awarded Firm(s):' (goods/works vs consultants) up to the next bidder section."""
    m = _re.search(rf"{label} (?:Bidder|Firm)\(s\):", text)
    if not m:
        return ""
    nxt = _re.search(r"(?:Awarded|Evaluated|Rejected) (?:Bidder|Firm)\(s\):", text[m.end():])
    return text[m.end():m.end() + nxt.start()] if nxt else text[m.end():]


def parse_award_notice(notice_html):
    """Structured fields from a 'Contract Award' notice body.

    Returns notification_date, contract_duration_months, signed_currency, signed_amount, awarded / evaluated /
    rejected bidder lists (name + WB supplier id), n_bidders, rejection_reasons, notice_version."""
    text = html_to_text(notice_html)
    out = {"notification_date": None, "contract_duration_months": None, "signed_currency": None,
           "signed_amount": None, "awarded": [], "evaluated": [], "rejected": [], "rejection_reasons": [],
           "n_bidders": 0, "notice_version": None}
    if not text:
        return out
    m = _re.search(r"Date Notification of Award Issued.*?(\d{4}/\d{2}/\d{2})", text, _re.S)
    if m:
        out["notification_date"] = parse_wb_date(m.group(1))
    m = _re.search(r"Duration of Contract\s*(\d+(?:\.\d+)?)\s*(Month|Week|Day|Year)", text, _re.I)
    if m:
        per_month = {"month": 1, "week": 12 / 52, "day": 12 / 365, "year": 12}[m.group(2).lower()]
        out["contract_duration_months"] = round(float(m.group(1)) * per_month, 1)
    m = _re.search(r"Signed Contract price\s*" + _AMOUNT_RE, text, _re.I)
    if m:
        out["signed_currency"], out["signed_amount"] = m.group(1), float(m.group(2).replace(",", ""))
    m = _re.search(r"Notice Version No:\s*(\d+)", text)
    if m:
        out["notice_version"] = int(m.group(1))

    for key, label in (("awarded", "Awarded"), ("evaluated", "Evaluated"), ("rejected", "Rejected")):
        sec = _section(text, label)
        for line in sec.split("\n"):
            b = _BIDDER_RE.match(line.strip())
            if b:
                out[key].append({"name": b.group("name").strip(), "supplier_id": b.group("id")})
        if key == "rejected":
            out["rejection_reasons"] = [r.strip() for r in _re.findall(r"Reason for Rejection\s*\n([^\n]+)", sec)]
    ids = {b["supplier_id"] for k in ("awarded", "evaluated", "rejected") for b in out[k]}
    out["n_bidders"] = len(ids)
    return out


_REBID_RE = _re.compile(r"\bre[\s-]?(bid|tender|advertis\w*|launch\w*|invit\w*|issu\w*)\b|\b(second|2nd|third|3rd) (call|invitation)\b",
                        _re.I)


def is_rebid(*texts):
    """True if a notice title/reference says the package is being re-tendered."""
    return any(_s(t) and _REBID_RE.search(t) for t in texts)

# ── Long reports: structure-aware parsing ──────────────────────────────────────────────────────

def strip_running_lines(pages, min_share=0.3):
    """Remove running headers/footers: short lines that repeat on ≥30% of pages ('The World Bank',
    'Project (P153349) ICR DOCUMENT', 'Page 16'). pages: list of (page_num, text) → same shape."""
    def key(line):
        return _re.sub(r"\d+", "#", " ".join(line.split()).lower())
    counts = _Counter()
    for _, t in pages:
        counts.update({key(l) for l in t.split("\n") if 0 < len(l.strip()) <= 90})
    n = max(1, len(pages))
    running = {k for k, c in counts.items() if c / n >= min_share and len(pages) >= 5}
    running |= {"page #", "#"}
    return [(p, "\n".join(l for l in t.split("\n") if key(l) not in running)) for p, t in pages]


_HEADING_RE = _re.compile(
    r"^\s*(?P<num>(?:[IVX]{1,5}|[A-H]|\d{1,2}(?:\.\d{1,2})?)\.)?\s*(?P<title>[A-Z][A-Z0-9 ,&/()'’:-]{6,90}?)\s*$")
_NOT_HEADINGS = {"TABLE OF CONTENTS", "ABBREVIATIONS AND ACRONYMS", "CURRENCY EQUIVALENTS", "FISCAL YEAR"}


_ANNEX_RE = _re.compile(r"^\s*ANNEX\s+(\d{1,2}|[IVX]{1,4}|[A-H])\b[.:]?\s*(?P<title>[A-Z][A-Z0-9 ,&/()'’:-]{3,90})?\s*$")


def _heading(line):
    """(level, title) if the line looks like a report heading, else None.
    Level 1: 'V. LESSONS AND RECOMMENDATIONS' / 'ANNEX 3. ...' / unnumbered all-caps; level 2: 'B. KEY FACTORS ...'."""
    a = _ANNEX_RE.match(line)
    if a:
        return 1, " ".join(line.split())
    m = _HEADING_RE.match(line)
    if not m:
        return None
    title = " ".join(m.group("title").split())
    letters = _re.sub(r"[^A-Za-z]", "", title)
    if len(letters) < 6 or not letters.isupper() or title in _NOT_HEADINGS or sum(c.isdigit() for c in title) > 4:
        return None
    num = (m.group("num") or "").rstrip(".")
    if _re.fullmatch(r"[A-H]", num):
        return 2, f"{num}. {title}"
    if title.startswith("ANNEX") or _re.fullmatch(r"[IVX]+", num):
        return 1, f"{num + '. ' if num else ''}{title}"
    if num:
        return 2, f"{num}. {title}"
    return 1, title


def split_sections(pages):
    """Walk the report and return [(section_path, [(page_num, line), ...])] in reading order.
    section_path is 'V. LESSONS AND RECOMMENDATIONS' or 'III. KEY FACTORS ... > B. KEY FACTORS DURING IMPLEMENTATION'.
    Lines keep their page number so chunks spanning a page break still cite the right pages."""
    blocks, h1, h2, buf = [], "FRONT MATTER", None, []

    def flush():
        if any(l.strip() for _, l in buf):
            blocks.append((h1 + (f" > {h2}" if h2 else ""), list(buf)))

    for page_num, text in pages:
        for line in text.split("\n"):
            h = _heading(line)
            if h:
                flush()
                buf = []
                if h[0] == 1:
                    h1, h2 = h[1], None
                else:
                    h2 = h[1]
                continue
            buf.append((page_num, line))
    flush()
    return blocks


def _is_table_like(text):
    """Heuristic: a block where most lines are short and number-heavy is a table (ratings, costs, results)."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 4:
        return False
    numeric = sum(1 for l in lines if len(_re.findall(r"\d[\d,.]*", l)) >= 2 and len(l) < 140)
    return numeric / len(lines) >= 0.5


def section_chunks(pages, max_words=450, overlap_words=60, min_words=30):
    """Hierarchical chunking for 100+ page project reports.

    Chunks never cross a section boundary, carry their section path and page span, and are typed as
    narrative / table / annex - so retrieval can filter (e.g. only 'LESSONS' sections, or skip annex tables)
    and citations point at the right section and pages."""
    out = []
    for path, lines in split_sections(strip_running_lines(pages)):
        words = [(w, p) for p, line in lines for w in line.split()]
        block = "\n".join(line for _, line in lines)
        kind = "annex" if path.upper().startswith("ANNEX") else ("table" if _is_table_like(block) else "narrative")
        step = max_words - overlap_words
        i = 0
        while i < len(words):
            win = words[i:i + max_words]
            if len(win) >= min_words or (i == 0 and len(win) >= 8):
                out.append({"section_path": path, "section_top": path.split(" > ")[0], "content_type": kind,
                            "page_start": win[0][1], "page_end": win[-1][1],
                            "text": " ".join(w for w, _ in win), "n_words": len(win)})
            if i + max_words >= len(words):
                break
            i += step
    return out


_ISR_ROW_RE = _re.compile(
    r"^(?P<seq>\d{2})\s+(?P<date>\d{2}-[A-Z][a-z]{2}-\d{4})\s+(?P<rest>.+?)\s+(?P<disb>[\d,]+(?:\.\d+)?)\s*$")
_RATING_WORDS = r"(Highly Satisfactory|Moderately Satisfactory|Satisfactory|Moderately Unsatisfactory|Highly Unsatisfactory|Unsatisfactory)"
RATING_SCALE = {"highly satisfactory": 6, "satisfactory": 5, "moderately satisfactory": 4,
                "moderately unsatisfactory": 3, "unsatisfactory": 2, "highly unsatisfactory": 1,
                "high": 2, "substantial": 3, "modest": 4, "negligible": 5}   # risk-style words (lower = worse)


def _clean_glyphs(text):
    """PDF bullet glyphs (private-use U+F06C etc.) sit between labels and ratings in ISRs - drop them."""
    return _re.sub(r"[-•●▪■]", " ", text)


def parse_icr_datasheet(text):
    """From an ICR's data sheet: ISR rating history, key dates (planned vs actual) and the final ratings.
    These give the time series for conflict resolution (mid-term ISR vs final ICR) and project-level features
    (restructurings, closing-date extension) without an LLM."""
    flat = "\n".join(" ".join(l.split()) for l in _clean_glyphs(text).split("\n"))
    # ISR rows can wrap ('Moderately\nUnsatisfactory 0.60'): join a line that starts with a rating word to the previous
    flat = _re.sub(r"\b(Moderately|Highly)\n(Unsatisfactory|Satisfactory)\b", r"\1 \2", flat)
    isr = []
    for line in flat.split("\n"):
        m = _ISR_ROW_RE.match(line)
        if not m:
            continue
        ratings = _re.findall(_RATING_WORDS, m.group("rest"))
        if len(ratings) >= 2:
            isr.append({"seq": int(m.group("seq")), "date": parse_wb_date(m.group("date")),
                        "do_rating": ratings[0], "ip_rating": ratings[1],
                        "disbursed_usd_m": float(m.group("disb").replace(",", ""))})
    dates = {}
    for label in ("Approval", "Effectiveness", "Signing", "Mid-Term Review", "Operation Closing/Cancellation", "Closing"):
        m = _re.search(rf"^{_re.escape(label)}\s+(\S+-\S+-\d{{4}}|Not Applicable|--)?\s*(\d{{2}}-\w{{3}}-\d{{4}})?",
                       flat, _re.M)
        if m:
            dates[label] = {"planned": parse_wb_date(m.group(1)), "actual": parse_wb_date(m.group(2))}
    restructurings = len(_re.findall(r"^Restructuring Sequence\.\d+", flat, _re.M))
    final = {}
    # 'RATINGS SUMMARY' (older template) or 'KEY RATINGS' (newer), possibly with blank lines in between
    m = _re.search(r"(?:RATINGS SUMMARY|KEY RATINGS)\s*Outcome Bank Performance M&E Quality\s*\n\s*(.+)", flat)
    if m:
        words = _re.findall(_RATING_WORDS + r"|(High|Substantial|Modest|Negligible)", m.group(1))
        words = [a or b for a, b in words]
        for k, v in zip(("outcome", "bank_performance", "me_quality"), words):
            final[k] = v
    closing = dates.get("Operation Closing/Cancellation") or dates.get("Closing") or {}
    return {"isr_ratings": isr, "key_dates": dates, "restructurings": restructurings, "final_ratings": final,
            "closing_planned": closing.get("planned"), "closing_actual": closing.get("actual")}

_RISK_WORDS = r"(High|Substantial|Moderate|Low)"


def parse_isr_ratings(text):
    """'3. OVERALL RATINGS' block of an Implementation Status and Results Report (previous and current ratings)."""
    flat = "\n".join(" ".join(l.split()) for l in _clean_glyphs(text or "").split("\n"))
    flat = _re.sub(r"\b(Moderately|Highly)\n(Unsatisfactory|Satisfactory)\b", r"\1 \2", flat)
    out = {"isr_date": None, "seq": None}
    m = _re.search(r"Archived\s*(?:on)?\s*(\d{2}-[A-Z][a-z]{2}-\d{4})", flat)
    if m:
        out["isr_date"] = parse_wb_date(m.group(1))
    m = _re.search(r"Seq No:\s*(\d+)", flat)
    if m:
        out["seq"] = int(m.group(1))
    for key, label in (("do", r"Progress towards achievement of PDO"), ("ip", r"Overall Implementation Progress \(IP\)")):
        m = _re.search(label + r"\s+" + _RATING_WORDS + r"\s+" + _RATING_WORDS, flat)
        out[f"{key}_previous"], out[f"{key}_current"] = (m.group(1), m.group(2)) if m else (None, None)
    m = _re.search(r"Overall Risk Rating\s+" + _RISK_WORDS + r"\s+" + _RISK_WORDS, flat)
    out["risk_previous"], out["risk_current"] = (m.group(1), m.group(2)) if m else (None, None)
    return out

# ── Section typing, lesson candidates, stage / risk tagging ───────────────────────────────────────

SECTION_KINDS = [   # (kind, pattern on the section path) - first match wins
    ("lessons", r"LESSON"),
    ("key_factors", r"KEY FACTORS|IMPLEMENTATION AND OUTCOME|FACTORS AFFECTING"),
    ("procurement_fiduciary", r"PROCUREMENT|FIDUCIARY|FINANCIAL MANAGEMENT"),
    ("risk", r"RISK"),
    ("ratings", r"RATING|KEY DATES|ISR"),
    ("results", r"OUTCOME|RESULTS|EFFICIENCY|EFFICACY"),
    ("design", r"CONTEXT|DESIGN|DEVELOPMENT OBJECTIVE|DESCRIPTION|APPRAISAL|IMPLEMENTATION ARRANGEMENTS"),
]

STAGE_TERMS = {
    "planning": r"procurement plan|packag|market (analysis|approach|sounding)|cost estimate|engineer.?s estimate|readiness|design (delay|readiness)|feasibility",
    "tender_design": r"specification|terms of reference|\btor\b|qualification|bidding document|standard bidding|lot(s|ting)?\b|eligib",
    "evaluation_award": r"evaluat|award|non-?responsive|re-?tender|re-?bid|bid(s|ders)? (exceed|above)|single bid|complain|no objection",
    "contract_management": r"contractor|supervis|variation|amendment|claim|extension of time|disburse|payment|performance of (the )?(contractor|supplier|firm)",
}
RISK_TERMS = {
    "delay": r"delay|slow|late|behind schedule|extension|lengthy|took (longer|more)",
    "cost_overrun": r"cost overrun|exceed(ed)? (the )?(estimate|budget)|price escalation|above (the )?estimate|additional financing",
    "low_competition": r"single bid|few bid|limited competition|lack of (bidders|competition)|non-?responsive|re-?tender|re-?bid",
    "capacity": r"capacity|staff(ing)?|turnover|inexperienc|piu|implementing agency|training",
    "specification": r"specification|design (flaw|change|error)|terms of reference|scope (change|creep)",
    "fiduciary": r"fiduciary|fraud|corruption|audit|misprocurement|complaint|financial management",
    "safeguards": r"resettlement|land acquisition|safeguard|environmental and social|e&s|compensation",
}
PROC_SIGNAL = r"procure|tender|bid|contract|supplier|contractor|consultan|award"


def classify(text, table):
    t = (text or "").lower()
    hits = [k for k, pat in table.items() if _re.search(pat, t)]
    return hits


def section_kind(path):
    p = (path or "").upper()
    if p.startswith("ANNEX"):
        return "annex"
    for kind, pat in SECTION_KINDS:
        if _re.search(pat, p):
            return kind
    return "other"


_PARA_RE = _re.compile(r"(?:(?<=\s)|^)(\d{1,3})\.\s+(?=[A-Z])")


def lesson_paragraphs(text):
    """Split a LESSONS section into numbered paragraphs ('19. The lead farmer model ... 20. Delivering TA ...')."""
    parts = _PARA_RE.split(text)
    out = []
    for k in range(1, len(parts) - 1, 2):
        body = parts[k + 1].strip()
        if len(body.split()) >= 12:
            out.append((int(parts[k]), body))
    if not out and len(text.split()) >= 25:
        out = [(None, text.strip())]
    return out


def first_sentence(s):
    m = _re.match(r"(.{20,320}?[.!?])(\s|$)", s)
    return m.group(1) if m else s[:300]



# ── Survival benchmarks (tender cycle times with open tenders as censored observations) ────────────

def kaplan_meier(durations, observed):
    """Kaplan-Meier survival curve. durations: days; observed: True if the tender reached award, False if
    still open (censored). Returns dict with median, p80 (day by which 80% are awarded), n, n_events, curve."""
    data = sorted(zip(durations, observed))
    n_at_risk, s, curve = len(data), 1.0, []
    i = 0
    while i < len(data):
        t = data[i][0]
        d = c = 0
        while i < len(data) and data[i][0] == t:
            d += bool(data[i][1])
            c += not data[i][1]
            i += 1
        if d:
            s *= 1 - d / n_at_risk
            curve.append((t, s))
        n_at_risk -= d + c

    def quantile(q):   # first time the share NOT yet awarded drops to ≤ 1-q
        return next((t for t, sv in curve if sv <= 1 - q), None)

    return {"n": len(data), "n_events": sum(1 for _, o in data if o), "median": quantile(0.5), "p80": quantile(0.8),
            "curve": curve}

# ── Guardrails ─────────────────────────────────────────────────────────────────────────────────

def _norm_text(s):
    return " ".join(_re.sub(r"[^a-z0-9%.]", " ", (s or "").lower()).split())


def quote_in_source(quote, source, min_ratio=0.9):
    """Is the evidence quote really in the source passage? Exact match after normalisation, else the best
    fuzzy window must reach min_ratio (tolerates PDF hyphenation / spacing noise, rejects paraphrase)."""
    q, s = _norm_text(quote), _norm_text(source)
    if not q or not s:
        return False
    if q in s:
        return True
    qw, sw = q.split(), s.split()
    if len(qw) < 4:
        return False
    best = 0.0
    for i in range(0, max(1, len(sw) - len(qw) + 1)):
        r = _SequenceMatcher(None, q, " ".join(sw[i:i + len(qw)])).ratio()
        best = max(best, r)
        if best >= min_ratio:
            return True
    return False


def numbers_in(text):
    """Numbers stated in a text, normalised ('1,250' → 1250.0, '18%' → 18.0). Years and citation ids are kept;
    the caller decides which ones matter."""
    return [float(x.replace(",", "")) for x in _re.findall(r"(?<![\w\[])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w\]])", text or "")]


def unsupported_numbers(answer, evidence_texts, rel_tol=0.02):
    """Numbers in the answer that appear in none of the evidence (tool outputs or passages) - the main
    hallucination signal for quantitative recommendations. Rounding within rel_tol is accepted."""
    pool = [n for t in evidence_texts for n in numbers_in(t)]
    bad = []
    for x in numbers_in(_re.sub(r"\[[^\]]*\]", " ", answer or "")):   # ignore [S3] citation markers
        if x < 10 and float(x).is_integer():
            continue   # small counts / list numbering
        if not any(abs(x - p) <= max(rel_tol * abs(p), 0.051) for p in pool):
            bad.append(x)
    return sorted(set(bad))


# ── Evidence registry and the faithfulness gate (used by the copilot) ───────────────────────────

class Evidence:
    """Registry of everything a statement may cite. ids: B=benchmark, M=model, T=similar tender, L=lesson,
    P=report passage, R=rating trajectory, D=derived calculation (shows its inputs)."""

    def __init__(self):
        self.items = {}
        self.n = {}

    def add(self, kind, text, **meta):
        self.n[kind] = self.n.get(kind, 0) + 1
        eid = f"{kind}{self.n[kind]}"
        self.items[eid] = {"id": eid, "text": text, **meta}
        return eid


def verify_statements(statements, ev):
    """The faithfulness gate. A statement survives only if:
      1. it cites at least one evidence id and every id exists (abstentions with no ids are allowed and labelled),
      2. every number in it appears in the text of the evidence it cites (rounding tolerated),
      3. every quoted span (“...”) occurs verbatim in the cited evidence,
      4. every page number and project id in its attribution belongs to the evidence it cites.
    Known limit: a fabricated number that happens to occur elsewhere in the cited evidence passes check 2;
    production adds number-unit binding and an entailment judge for that case."""
    kept, log = [], []
    for sec, text, ids in statements:
        problems = []
        abstain = not ids and _re.search(r"not enough|no verified|no model risk", text, _re.I)
        if not ids and not abstain:
            problems.append("no citation")
        missing = [i for i in ids if i not in ev.items]
        if missing:
            problems.append(f"unknown citation {missing}")
        cited = [ev.items[i]["text"] for i in ids if i in ev.items]
        if ids:
            # page numbers ('p. 33') and project ids ('P153349') are attribution, not claims
            claims = _re.sub(r"\bpp?\.\s?\d+(?:\s?-\s?\d+)?|\bP\d{6,7}\b", " ", text)
            bad = unsupported_numbers(claims, cited)
            if bad:
                problems.append(f"unsupported numbers {bad}")
            for q in _re.findall(r"“([^”]+)”", text):
                if not any(quote_in_source(q, c) for c in cited):
                    problems.append(f"quote not in source: {q[:60]}")
            # 4. attribution must match the cited record: page numbers and project ids are checked, not trusted
            items = [ev.items[i] for i in ids if i in ev.items]
            pages = {str(it.get("page")) for it in items if it.get("page") is not None}
            for pg in _re.findall(r"\bp\.\s?(\d+)", text):
                if pages and pg not in pages:
                    problems.append(f"page {pg} not in cited evidence")
            for pid in _re.findall(r"\bP\d{6,7}\b", text):
                if not any(pid == it.get("project_id") or pid in it.get("text", "") for it in items):
                    problems.append(f"project {pid} not in cited evidence")
        if problems:
            log.append({"section": sec, "text": text, "problems": problems, "action": "dropped"})
        else:
            kept.append({"section": sec, "text": text, "citations": ids, "abstention": bool(abstain)})
    return kept, log



SOURCE_AUTHORITY = {   # higher wins when evidence of equal recency disagrees
    "PPAR": 5,      # IEG Project Performance Assessment Report - independent, after closing
    "ICRR": 4,      # IEG ICR Review - independent validation of the ICR
    "ICR": 3,       # Implementation Completion and Results Report - self-evaluation at closing
    "ISR": 2,       # Implementation Status and Results Report - periodic supervision rating
    "PAD": 1,       # Project Appraisal Document - expectations at design
}


def resolve_rating_conflict(evidence):
    """Reconcile disagreeing ratings about the same project and dimension.

    evidence: list of {source_type, date, rating, source_id}. Rule: (1) completed-project evaluations outrank
    in-flight supervision (ICR/IEG > ISR) because they see the full outcome; (2) independent > self-evaluation;
    (3) within the same authority, the most recent wins. The losing evidence is NOT discarded: the result keeps
    the full trajectory and a 'conflict' flag so the user sees e.g. 'ISR mid-term: Moderately Unsatisfactory →
    ICR: Satisfactory (turnaround after restructuring)'."""
    ev = [e for e in evidence if e.get("rating")]
    if not ev:
        return None
    scored = sorted(ev, key=lambda e: (SOURCE_AUTHORITY.get(e["source_type"], 0), e.get("date") or _dt.date.min),
                    reverse=True)
    winner = scored[0]
    scores = {RATING_SCALE.get(e["rating"].lower()) for e in ev} - {None}
    spread = (max(scores) - min(scores)) if scores else 0
    satisfactory_side = {e["rating"].lower() in ("highly satisfactory", "satisfactory", "moderately satisfactory")
                         for e in ev}
    return {"resolved_rating": winner["rating"], "resolved_from": winner["source_type"],
            "resolved_source_id": winner.get("source_id"),
            "conflict": len(satisfactory_side) > 1,          # evidence on both sides of the satisfactory line
            "spread": spread,
            "trajectory": [(e["source_type"], str(e.get("date")), e["rating"])
                           for e in sorted(ev, key=lambda e: e.get("date") or _dt.date.min)]}


def safe_div(a, b):
    return a / b if b else None


def log1p_safe(x):
    return _math.log1p(x) if x is not None and x >= 0 else None


# ── Domain taxonomy: laboratory and diagnostic equipment ──────────────────────────────────────────
# What was bought, one level beneath the procurement category (first match wins). English, French, Spanish and
# Portuguese terms; "." stands in for accents that arrive garbled in the award feed. The first ten types are the domain.
ITEM_TYPES_LAB = [
    ("Molecular diagnostics (PCR, GeneXpert)", r"\bq?RT-?PCR\b|\bPCR\b|gene ?xpert|molecular|mol.culaire|nucleic|nucl.ique|"
                                               r"RNA extract|\bDNA\b|\bADN\b|thermocycl|sequenc|s.quen.age|genom|g.nom|primer|amorce"),
    ("Diagnostic imaging", r"x-?ray|radiograph|radiolog|rayos? x|rayons? x|ultraso|sonograph|.chograph|ecograf|\bCT\b|"
                           r"computed tomograph|tomograf|tomodensit|\bMRI\b|\bIRM\b|magnetic resonance|mammograph|mamograf|"
                           r"fluoroscop|c-?arm|imaging|imagerie|linear accelerator|angiograph|echocardiograph"),
    ("Rapid tests and test kits", r"rapid (diagnostic )?tests?|\bRDTs?\b|\bTDRs?\b|test kits?|testing kits?|antigen|antig.n|"
                                  r"antibod|anticorps|serolog|s.rolog|self-?test|diagnostic kits?|pruebas? r.pidas?|"
                                  r"tests? (de diagnostic )?rapides?|tests? de d.pistage|kits? de (test|d.pistage|diagn)"),
    ("Reagents and cartridges", r"reagent|\br.{0,2}acti[fv]|cartridge|cartouche|calibrator|culture media|milieux? de culture|"
                                r"laboratory consumable|lab consumable|consommables? de laboratoire|consumibles? de laboratorio|"
                                r"microbiolog|agarose|viral transport|\bVTM\b|swab|.couvillon|hisopo"),
    ("Laboratory analyzers", r"analy[sz]er|analy[sz]eur|analizador|hematolog|haematolog|h.matolog|biochem|biochimi|bioqu.mic|"
                             r"chemistry|immunoassay|inmunoqu|immunochimi|ELISA|coagulat|blood gas|electrolyte|\bCD4\b|"
                             r"viral load|charge virale|carga viral|blood (grouping|bank)|banque de sang|banco de sangre|urinaly"),
    ("Microscopy", r"microscop|microtome"),
    ("Biosafety, sterilization and cold chain", r"biosafety|bios.curit|bioseguridad|biological safety|safety cabinet|"
                                                r"autoclave|steriliz|st.rilis|esteriliz|(ultra[- ]?low|deep) freezer|"
                                                r"cong.lateur|congelador|laboratory (fridge|refrigerat)|centrifug|incubat|"
                                                r"incubad|fume hood|hotte|laminar"),
    ("Laboratory information systems", r"\bLIMS\b|laborator(y|ies) information|lab information|SIL\b"),
    ("Mobile and modular laboratories", r"mobile lab|modular lab|laboratoire mobile|laboratorio m.vil|"
                                        r"container(ised|ized)? lab|BSL-?[23]"),
    ("General laboratory and diagnostic equipment", r"laborator|laboratoi|laboratorio|\blab\b|diagnost|diagn.stico|"
                                                    r"pathology|patholog|anatomo|testing equipment|specialised testing"),
    # outside the domain (kept so the market context and models see the whole health-goods picture)
    ("Medicines and vaccines", r"medicine|drug|vaccin|vacuna|pharmac|m.dicament|medicamento|antiretro|insulin|tablet|"
                               r"injection|\d+ ?mg\b|folic|ferrous|plumpy|contracepti"),
    ("PPE and medical consumables", r"\bPPE\b|\bEPI\b|personal protect|mask|masque|mascarilla|glove|gant|guante|gown|"
                                    r"coverall|goggle|face shield|visi.re|syringe|seringue|jeringa|sanitiz|disinfect|"
                                    r"d.sinfect|gel hydro|soap|savon|jab.n|hygien|hand ?wash|lavage des mains"),
    ("Other medical equipment", r"medical equipment|.quipements? (m.dicaux|biom.dicaux)|mat.riels? m.dicaux|"
                                r"equipamientos? m.dicos?|equipos? m.dicos?|ventilat|oxyg|monitor|hospital bed|\bbeds?\b|"
                                r"\blits?\b|camas?|anaesthe|anesthe|infusion|surgical|chirurgi|quir.rgic|dental|\bICU\b|"
                                r"theatre|delivery|neonatal|n.onat|h.modialys|dialys|medical gas|stent|cat.ter|catheter|"
                                r"simulat|mattress|matelas|incinerat|ambulance"),
    ("IT, furniture and vehicles", r"computer|ordinateur|computador|laptop|server|serveur|software|logiciel|IT equipment|"
                                   r"furniture|mobilier|mobiliario|vehicle|v.hicule|veh.culo|moto|printer|imprimante|"
                                   r"firewall|CCTV|internet|office|bureau"),
]
LAB_DOMAIN_ITEMS = {name for name, _ in ITEM_TYPES_LAB[:10]}

# "Vehicles for the national laboratory" is not laboratory equipment: these words veto the catch-all type
NOT_EQUIPMENT = _re.compile(r"vehicle|v.hicule|veh.culo|pick ?-?up|motorcycle|\bmotos?\b|CCTV|access control|printing|"
                            r"impression|manuals?\b|construction|r.habilitation|works\b|travaux|obras", _re.I)
_OTHER_BY_CATEGORY = {"Works": "Other works", "Goods": "Other goods", "Consultant Services": "Other consultancy",
                      "Non-consulting Services": "Other services"}


def classify_item(description, category=None, item_types=ITEM_TYPES_LAB):
    """Classify what a contract or tender buys into the item taxonomy ('Other <category>' when nothing matches)."""
    d = description or ""
    for name, pat in item_types:
        if name.startswith("General") and NOT_EQUIPMENT.search(d):
            continue
        if _re.search(pat, d, _re.I):
            return name
    return _OTHER_BY_CATEGORY.get(category, "Other")


def parse_icrr_ratings(text):
    """IEG ICR Review: the independent outcome rating ("Outcome Rating <IEG>") and, from the summary table
    ("Outcome <ICR> <IEG>"), the Bank's own ICR rating beside it. Returns (ieg_rating, icr_rating), either may be None."""
    flat = " ".join((text or "")[:60000].split())
    ieg = _re.search(rf"\bOutcome Rating {_RATING_WORDS}", flat)
    tab = _re.search(rf"\bOutcome {_RATING_WORDS} {_RATING_WORDS}", flat)
    return (ieg.group(1) if ieg else (tab.group(2) if tab else None)), (tab.group(1) if tab else None)


def parse_ppar_rating(text):
    """IEG Project Performance Assessment Report: the outcome rating from its ratings table, or None."""
    m = _re.search(rf"\bOutcome\b[^\n]{{0,40}}?\b{_RATING_WORDS}", (text or "")[:30000])
    return m.group(1) if m else None


# ── Document Q&A (RAG): categories and answer sentences ───────────────────────────────────────────
# Shared by procurement_rag/ (local, ChromaDB) and notebooks 30-34 (Databricks, Vector Search).
# Passage categories: the ten lab and diagnostic types plus the procurement process. Report text is noisier than
# contract descriptions ("CT" is also HIV counselling and testing; PDF extraction splits words, "proje ct"), so CT and
# C-arm must appear as imaging terms.
RAG_CATEGORIES = {name: pat for name, pat in ITEM_TYPES_LAB[:10]}
RAG_CATEGORIES["Diagnostic imaging"] = (RAG_CATEGORIES["Diagnostic imaging"]
                                        .replace(r"\bCT\b", r"\bCT[- ]?(scan|scanner|machine)s?\b")
                                        .replace(r"c-?arm", r"\bC-arm\b"))
RAG_CATEGORIES["Procurement process and contracts"] = (r"procure|tender|bidding|bidder|contract award|contract(s|or)? |"
                                                        r"supplier|request for (bids|quotations|proposals)|no objection")


def rag_category_key(name):
    """Column / metadata name for a category: 'Molecular diagnostics (PCR, GeneXpert)' → 'cat_molecular_diagnostics_pcr_genexpert'."""
    return "cat_" + _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def rag_tags(text):
    """{category column: 1/0} for one passage."""
    return {rag_category_key(n): int(bool(_re.search(p, text or "", _re.I))) for n, p in RAG_CATEGORIES.items()}


_RAG_SENT = _re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"“])")
_RAG_NOT_SENTENCE = _re.compile(r"^(annex|table|figure|section|chapter|page|source|note)\b|et al\.|\(\d{4}\)|\bdoi\b|"
                                r"journal|plos|health affairs|lancet", _re.I)       # headings and bibliography entries
_RAG_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with which "
                "their there been not but they into than these those also such other more most only over under per any all "
                "each project projects bank world what how does did do why when where who".split())


def rag_tokens(text):
    out = []
    for w in _re.findall(r"[a-z][a-z0-9\-]{1,}", (text or "").lower()):
        if w in _RAG_STOP:
            continue
        for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)] + rep
                break
        out.append(w)
    return out


def rag_clean_sentence(sent):
    sent = sent.replace("\ufffd", " ").strip()
    sent = _re.sub(r"^\(?[0-9ivx]{1,4}[.)]\s+", "", sent)          # leading paragraph number "12. " / "(iii) "
    sent = _re.sub(r"\s+\d{1,3}\.$", ".", sent)                      # trailing number of the next paragraph
    return " ".join(sent.split())


def rag_candidate_sentences(sources, category=None, max_sources=6):
    """[(source id, sentence)] that can stand alone as an answer sentence: complete, not a heading, table row,
    bibliography entry or fragment, and (with a category selected) mentioning that category."""
    cat_re = _re.compile(RAG_CATEGORIES[category], _re.I) if category else None
    out = []
    for s in sources[:max_sources]:
        for sent in _RAG_SENT.split(s["text"]):
            sent = rag_clean_sentence(sent)
            words = sent.split()
            digits = sum(ch.isdigit() for ch in sent)
            numbers = sum(bool(_re.fullmatch(r"[\d.,%()-]+", w)) for w in words)
            if (8 <= len(words) <= 70 and digits < len(sent) * 0.15 and numbers <= 3
                    and not _RAG_NOT_SENTENCE.search(sent) and not _re.match(r"^[a-z]", sent)
                    and _re.search(r"[.!?)\"”]$", sent) and (cat_re is None or cat_re.search(sent))):
                out.append((s["id"], sent))
    return out


def _unit(v):
    n = _math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def rag_pick_sentences(question, question_vec, cands, cand_vecs, max_sentences=4, min_sim=0.55,
                       min_sim_no_overlap=0.70, max_duplicate=0.90, per_source=2):
    """Choose the answer sentences: closest in meaning to the question (cosine of the embeddings), a higher bar for
    sentences sharing no content word with the question, at most two per source, near-duplicates dropped
    (the same statement repeated in an original and an additional-financing document)."""
    q = _unit(question_vec)
    units = [_unit(v) for v in cand_vecs]
    qt = set(rag_tokens(question))
    sims = [sum(a * b for a, b in zip(u, q)) for u in units]
    overlap = [len(qt & set(rag_tokens(c[1]))) for c in cands]
    order = sorted(range(len(cands)), key=lambda i: -(sims[i] + 0.01 * overlap[i]))
    out, per, chosen = [], {}, []
    for i in order:
        sid, sent = cands[i]
        if sims[i] < (min_sim if overlap[i] else min_sim_no_overlap) or per.get(sid, 0) >= per_source:
            continue
        if any(sum(a * b for a, b in zip(units[i], units[j])) > max_duplicate for j in chosen):
            continue
        per[sid] = per.get(sid, 0) + 1
        chosen.append(i)
        out.append({"text": sent, "source": sid, "similarity": round(sims[i], 3)})
        if len(out) >= max_sentences:
            break
    return out
