"""Unit tests for notebooks/20_proc_lib.py (pure Python, runs without Databricks).

    python src/test_proc_lib.py                       # inline fixtures taken from real World Bank notices/ICRs
    python src/test_proc_lib.py <icr.txt> <awards.json>   # also run against a full ICR text dump and an API page
"""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("proc_lib", ROOT / "notebooks" / "20_proc_lib.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

AWARD_HTML = """<div class='row col-sm-12'><h4>Contract Award</h4><p><b>Project:</b>P179293-East Africa Girls' Empowerment
and Resilience<br/><b>Bid/Contract Reference No:</b>MZ-SEJE-574355-NC-RFB<br/><b>Notice Version No:</b>0</p><br/></div>
<div class='row'><div class='col-sm-4'><b>Date Notification of Award Issued</b><br/>(YYYY/MM/DD)<br/>2026/08/27<br/></div>
<div class='col-sm-4'><b>Duration of Contract</b><br/><br/>18 Month(s)<br/></div></div>
<div class='row'><div class='col-sm-12'><u><b>Awarded Bidder(s):</b></u></div><div class='row col-sm-12'><div class='col-sm-5'>
<div class='row col-sm-12'><b>RB CORPORATION AND SERVICES (1133796)</b><br/>Maputo<br/>Country: Mozambique<br/></br></div></div>
<div class='col-sm-7'><div class='row col-sm-12'>Bid Price at Opening<br/>MZN<br/><br/>Evaluated Bid Price<br/>MZN 23362750.00</div></br></div>
<div class='col-sm-7'>Signed Contract price<br/>MZN 23362750.00</div><br/></div></div>
<div class='row'><div class='col-sm-12'><u><b>Evaluated Bidder(s):</b></u></div>
<div class='row col-sm-12'><b>INSPIRACOES MOZ (1133786)</b><br/>Maputo<br/>Country: Mozambique<br/></div>
<div class='row col-sm-12'><b>RENO EXECUTIVO (1133787)</b><br/>Maputo<br/></div></div>
<div class='row'><u><b>Rejected Bidder(s):</b></u><div><b>KENSEL LIMITED (1080023)</b><br/>P.O. Box 311-00206 Kiserian<br/>
Bid Price at Opening<br/>KES<br/><br/>Reason for Rejection<br/>Non Responsive<br/></div></div>"""

ICR_TEXT = """KEY DATES
Key Events Planned Date Actual Date
Approval 23-Aug-2016 23-Aug-2016
Effectiveness 31-Jul-2017 28-Jul-2017
Restructuring Sequence.01 Not Applicable 25-Jul-2017
Restructuring Sequence.02 Not Applicable 18-Jan-2022
Operation Closing/Cancellation 31-Mar-2023 31-Mar-2024
RATINGS SUMMARY
Outcome Bank Performance M&E Quality
Satisfactory Satisfactory Substantial
ISR RATINGS
No. Date ISR Archived DO Rating IP Rating Actual Disbursements
01 22-Dec-2016 Moderately Satisfactory Moderately Satisfactory 0.47
02 22-Jun-2017 Moderately Unsatisfactory Moderately
Unsatisfactory 0.60
14 28-Jun-2023 Satisfactory Satisfactory 197.23
"""

ISR_TEXT = """EASTERN AND SOUTHERN AFRICA | Kenya | IBRD/IDA | Investment Project Financing (IPF) | FY 2021 | Seq No: 10 | Archived
on 02-Mar-2025 | ISR02924
3. OVERALL RATINGS
Name Previous Current
Progress towards achievement of PDO Moderately Satisfactory Moderately Unsatisfactory
Overall Implementation Progress (IP) Moderately Satisfactory Moderately
Unsatisfactory
Overall Risk Rating High High
"""

REPORT_PAGES = [
    (1, "The World Bank\nKenya Roads Project (P1) ICR DOCUMENT\nPage 1\nI. PROJECT CONTEXT AND DEVELOPMENT OBJECTIVES\nA. CONTEXT AT APPRAISAL\n"
        + "Context words " * 60),
    (2, "The World Bank\nKenya Roads Project (P1) ICR DOCUMENT\nPage 2\nIII. KEY FACTORS THAT AFFECTED IMPLEMENTATION AND OUTCOME\n"
        "B. KEY FACTORS DURING IMPLEMENTATION\n" + "Procurement of civil works was delayed by 14 months because bids exceeded the estimate. " * 8),
    (3, "The World Bank\nKenya Roads Project (P1) ICR DOCUMENT\nPage 3\n" + "Re-tendering added cost. " * 20
        + "\nV. LESSONS AND RECOMMENDATIONS\n" + "Realistic engineer estimates reduce re-bidding. " * 10),
    (4, "The World Bank\nKenya Roads Project (P1) ICR DOCUMENT\nPage 4\nANNEX 1. RESULTS FRAMEWORK AND KEY OUTPUTS\n"
        + "Indicator 2016 2020 12.5 14.0\n" * 8),
    (5, "The World Bank\nKenya Roads Project (P1) ICR DOCUMENT\nPage 5\n" + "Annex text " * 50),
]


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    return bool(cond)


def run_inline():
    ok = True
    ok &= check("norm_ref joins reference variants",
                L.norm_ref("KE-MOWASCO-16771-CW-RFB") == L.norm_ref("ke mowasco 16771 cw/rfb") and L.norm_ref("N/A") is None)
    ok &= check("norm_supplier drops M/S and legal forms",
                L.norm_supplier("M/S. AIRES INGEGNERIA S.r.l.") == "aires ingegneria", L.norm_supplier("M/S. AIRES INGEGNERIA S.r.l."))
    ok &= check("norm_supplier JV order-insensitive",
                L.norm_supplier("Sinohydro JV Kenya Builders Ltd") == L.norm_supplier("Kenya Builders Limited JV Sinohydro"),
                f'{L.norm_supplier("Sinohydro JV Kenya Builders Ltd")} vs {L.norm_supplier("Kenya Builders Limited JV Sinohydro")}')
    ok &= check("name_similarity tolerates typos", L.name_similarity("china wu yi", "china wuyi") > 0.85)
    ok &= check("parse_wb_date formats", L.parse_wb_date("20-Oct-2026") == dt.date(2026, 10, 20)
                and L.parse_wb_date("2026-12-10T00:00:00Z") == dt.date(2026, 12, 10)
                and L.parse_wb_date("12/31/2029 12:00:00 AM") == dt.date(2029, 12, 31)
                and L.parse_wb_date("2026/08/27") == dt.date(2026, 8, 27))

    a = L.parse_award_notice(AWARD_HTML)
    ok &= check("award: notification date", a["notification_date"] == dt.date(2026, 8, 27), a["notification_date"])
    ok &= check("award: duration", a["contract_duration_months"] == 18.0, a["contract_duration_months"])
    ok &= check("award: signed price", (a["signed_currency"], a["signed_amount"]) == ("MZN", 23362750.0),
                (a["signed_currency"], a["signed_amount"]))
    ok &= check("award: bidders", len(a["awarded"]) == 1 and len(a["evaluated"]) == 2 and len(a["rejected"]) == 1
                and a["n_bidders"] == 4, {k: len(a[k]) for k in ("awarded", "evaluated", "rejected")})
    ok &= check("award: rejection reason", a["rejection_reasons"] == ["Non Responsive"], a["rejection_reasons"])
    ok &= check("award: version", a["notice_version"] == 0)
    ok &= check("is_rebid", L.is_rebid("Re-tender of Lot 2 water works") and L.is_rebid(None, "KE-RE-ADVERTISEMENT")
                is not None and not L.is_rebid("Rehabilitation of roads", "KE-CW-RFB"))

    d = L.parse_icr_datasheet(ICR_TEXT)
    ok &= check("icr: ISR rows incl. wrapped rating", len(d["isr_ratings"]) == 3
                and d["isr_ratings"][1]["ip_rating"] == "Moderately Unsatisfactory", d["isr_ratings"])
    ok &= check("icr: restructurings", d["restructurings"] == 2)
    ok &= check("icr: closing extension", d["closing_planned"] == dt.date(2023, 3, 31) and d["closing_actual"] == dt.date(2024, 3, 31),
                (d["closing_planned"], d["closing_actual"]))
    ok &= check("icr: final ratings", d["final_ratings"].get("outcome") == "Satisfactory"
                and d["final_ratings"].get("me_quality") == "Substantial", d["final_ratings"])

    isr = L.parse_isr_ratings(ISR_TEXT)
    ok &= check("isr: ratings + date", isr["do_current"] == "Moderately Unsatisfactory" and isr["ip_previous"] == "Moderately Satisfactory"
                and isr["risk_current"] == "High" and isr["isr_date"] == dt.date(2025, 3, 2) and isr["seq"] == 10, isr)

    isr2 = L.parse_isr_ratings("3. OVERALL RATINGS\nName Previous Current\nProgress towards achievement of PDO Satisfactory "
                               "Satisfactory\nOverall Implementation Progress (IP) Satisfactory Moderately Satisfactory\n"
                               "Overall Risk Rating Moderate Moderate\n")
    ok &= check("isr: bullet glyphs between ratings", isr2["ip_current"] == "Moderately Satisfactory" and isr2["risk_current"] == "Moderate", isr2)
    d2 = L.parse_icr_datasheet("KEY RATINGS \n \n \nOutcome Bank Performance M&E Quality \nSatisfactory Satisfactory Substantial \n"
                               "RATINGS OF PROJECT PERFORMANCE IN ISRs\nNo. Date ISR Archived DO Rating IP Rating\n"
                               "01 09-Sep-2016 Satisfactory Satisfactory 0 \n02 17-Mar-2017 Satisfactory Satisfactory 17.50 \n")
    ok &= check("icr: newer 'KEY RATINGS' template + integer disbursement", d2["final_ratings"].get("outcome") == "Satisfactory"
                and len(d2["isr_ratings"]) == 2, d2)
    ev = L.Evidence()
    lid = ev.add("L", "Delays in contracting works delayed implementation.", page=24, project_id="P128276")
    kept, log = L.verify_statements([
        ("x", "“Delays in contracting works delayed implementation.” (ICR, P128276, p. 24)", [lid]),
        ("x", "“Delays in contracting works delayed implementation.” (ICR, P128276, p. 65)", [lid]),
        ("x", "“Delays in contracting works delayed implementation.” (ICR, P999999, p. 24)", [lid]),
        ("x", "Works take 245 days.", [lid]), ("x", "Uncited claim.", []), ("x", "Real text", ["L9"])], ev)
    ok &= check("verifier: keeps the faithful statement, drops wrong page / project / number / uncited / unknown id",
                len(kept) == 1 and len(log) == 5, [g["problems"] for g in log])

    chunks = L.section_chunks(REPORT_PAGES, max_words=120, overlap_words=20)
    paths = [c["section_path"] for c in chunks]
    ok &= check("chunks: running headers removed", not any("ICR DOCUMENT" in c["text"] for c in chunks))
    ok &= check("chunks: hierarchical section path",
                "III. KEY FACTORS THAT AFFECTED IMPLEMENTATION AND OUTCOME > B. KEY FACTORS DURING IMPLEMENTATION" in paths, set(paths))
    lessons = [c for c in chunks if c["section_top"].startswith("V. LESSONS")]
    ok &= check("chunks: lessons section isolated", lessons and all("Realistic engineer" in c["text"] for c in lessons)
                and all(c["page_start"] == 3 for c in lessons))
    kf = [c for c in chunks if "KEY FACTORS DURING" in c["section_path"]]
    ok &= check("chunks: page span across page break", any(c["page_start"] == 2 and c["page_end"] == 3 for c in kf),
                [(c["page_start"], c["page_end"]) for c in kf])
    ok &= check("chunks: annex typed", any(c["content_type"] == "annex" for c in chunks))

    km = L.kaplan_meier([30, 60, 60, 90, 120, 200, 45], [True, True, True, True, True, False, False])
    ok &= check("kaplan_meier median / p80", km["median"] == 90 and km["p80"] == 120 and km["n_events"] == 5, km)

    src = "Procurement of civil works was delayed by 14 months because bids exceeded the engineer's estimate."
    ok &= check("quote_in_source exact", L.quote_in_source("delayed by 14 months because bids exceeded", src))
    ok &= check("quote_in_source tolerates PDF noise", L.quote_in_source("delayed by 14 mon ths because bids exceeded", src))
    ok &= check("quote_in_source rejects paraphrase", not L.quote_in_source("works took over a year longer than planned", src))
    ok &= check("unsupported_numbers",
                L.unsupported_numbers("Expect 182 days [S1]; P80 is 240 days; 37% of tenders re-bid.", ["p50=181.6 p80=240", "rate 0.12"])
                == [37.0], L.unsupported_numbers("Expect 182 days [S1]; P80 is 240 days; 37% of tenders re-bid.", ["p50=181.6 p80=240"]))

    r = L.resolve_rating_conflict([
        {"source_type": "ISR", "date": dt.date(2018, 6, 29), "rating": "Moderately Unsatisfactory", "source_id": "isr04"},
        {"source_type": "ICR", "date": dt.date(2025, 1, 30), "rating": "Satisfactory", "source_id": "icr"},
        {"source_type": "ISR", "date": dt.date(2023, 6, 28), "rating": "Satisfactory", "source_id": "isr14"}])
    ok &= check("conflict: ICR outranks ISR, conflict flagged", r["resolved_from"] == "ICR" and r["conflict"]
                and r["trajectory"][0][2] == "Moderately Unsatisfactory", r)
    r2 = L.resolve_rating_conflict([
        {"source_type": "ICR", "date": dt.date(2020, 1, 1), "rating": "Satisfactory"},
        {"source_type": "PPAR", "date": dt.date(2022, 1, 1), "rating": "Moderately Unsatisfactory"}])
    ok &= check("conflict: independent IEG outranks self-evaluation", r2["resolved_from"] == "PPAR")
    return ok


def run_files(icr_txt: str, awards_json: str):
    ok = True
    text = Path(icr_txt).read_text(encoding="utf-8")
    pages = []
    for part in text.split("=====PAGE ")[1:]:
        num, _, body = part.partition("=====\n")
        pages.append((int(num), body))
    d = L.parse_icr_datasheet("\n".join(t for _, t in pages[:15]))
    print(f"ICR file: {len(d['isr_ratings'])} ISR rows, restructurings={d['restructurings']}, final={d['final_ratings']}, "
          f"closing {d['closing_planned']} → {d['closing_actual']}")
    ok &= check("file icr: 14 ISR rows", len(d["isr_ratings"]) == 14, len(d["isr_ratings"]))
    chunks = L.section_chunks(pages)
    tops = []
    for c in chunks:
        if c["section_top"] not in tops:
            tops.append(c["section_top"])
    print(f"ICR file: {len(pages)} pages → {len(chunks)} chunks; top sections: {tops[:14]}")
    lessons = [c for c in chunks if "LESSONS" in c["section_top"]]
    ok &= check("file icr: lessons chunks found", lessons, tops)
    if lessons:
        print("  lessons p.", lessons[0]["page_start"], "-", lessons[0]["page_end"], ":", lessons[0]["text"][:160])

    notices = json.loads(Path(awards_json).read_text(encoding="utf-8"))["procnotices"]
    parsed = [L.parse_award_notice(n.get("notice_text")) for n in notices]
    have_date = sum(p["notification_date"] is not None for p in parsed)
    have_bidders = sum(p["n_bidders"] > 0 for p in parsed)
    print(f"Award file: {len(parsed)} notices, notification date {have_date}, bidders {have_bidders}, "
          f"single-bidder {sum(p['n_bidders'] == 1 for p in parsed)}")
    ok &= check("file awards: ≥90% have a notification date", have_date >= 0.9 * len(parsed), have_date)
    ok &= check("file awards: ≥90% have bidders", have_bidders >= 0.9 * len(parsed), have_bidders)
    return ok


if __name__ == "__main__":
    ok = run_inline()
    if len(sys.argv) == 3:
        ok &= run_files(sys.argv[1], sys.argv[2])
    print("\nALL PASSED" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)
