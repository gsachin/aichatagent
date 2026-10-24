"""Milestone one: the DSPMU index builds and today's guard accepts it.

These tests are offline. They exercise the parsers with small fixtures and, when
the generated files exist, run the real guard against the generated knowledge
file. The builder is run separately:

    .venv/bin/python scripts/build_site_index.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.kb_entities import EntityIndex  # noqa: E402
from app.siteindex.discover import (  # noqa: E402
    classify_title,
    looks_pii,
    parse_news_manifest,
    parse_pdf_links,
)
from app.siteindex.extract import extract_html  # noqa: E402
from app.siteindex.facts import parse_documents, parse_fee_tables, parse_pg_dates  # noqa: E402
from app.siteindex.fetch import FetchResult, SourceSpec  # noqa: E402
from app.voice_grounding import is_grounded, ungrounded_claims  # noqa: E402

SITE_MAP = ROOT / "content/dspmu/site_map.md"
CORE_CARDS = ROOT / "content/dspmu/core_cards.md"
FACTS = ROOT / "content/dspmu/facts.json"
REPORT = ROOT / "content/dspmu/build_report.json"


def _result(body: bytes, content_type: str) -> FetchResult:
    spec = SourceSpec("test", "https://example.test/source", "test", "html")
    return FetchResult(spec, spec.url, body, content_type, 200, True)


def test_soft_404_is_detected_even_with_http_200():
    page = b"<html><title>Official site</title><main>404 - Page Not Found</main></html>"
    assert _result(page, "text/html").soft_404_reason()


def test_a_real_pdf_is_not_a_soft_404():
    result = _result(b"%PDF-1.7\n...", "application/pdf")
    assert result.is_pdf
    assert result.soft_404_reason() == ""


def test_categories_and_pii_follow_the_site_filters():
    assert classify_title("B.A. History Semester III Result") == "Result"
    assert looks_pii("B.A. History Semester III Result")
    assert classify_title("Admission Merit List 2026") == "Admission"
    assert looks_pii("Admission Merit List 2026")
    assert classify_title("Holiday Notice") == "Holiday"
    assert not looks_pii("Holiday Notice")
    assert looks_pii("Notice for selected candidates")
    assert looks_pii("Roll No. 23B22001")


def test_news_manifest_reads_dates_titles_and_working_old_site_urls():
    html = """
    <table>
      <tr><th>Sl. No.</th><th>Date</th><th>Title</th></tr>
      <tr><td>1</td><td>14-Oct-2026</td>
          <td><a href="pdf/News/notice.pdf">Fee Structure 2026 (507.04 KB)</a></td></tr>
      <tr><td>2</td><td>13-Oct-2026</td>
          <td><a href="pdf/News/result.pdf">B.A. Result 2026</a></td></tr>
    </table>
    """
    notices = parse_news_manifest(
        html, "https://oldsite.dspmuranchi.ac.in/News.aspx"
    )
    assert len(notices) == 2
    first = notices[0]
    assert first.date == "2026-10-14"
    assert first.title == "Fee Structure 2026"
    assert first.url == "https://oldsite.dspmuranchi.ac.in/pdf/News/notice.pdf"
    assert not first.pii
    result = next(n for n in notices if n.category == "Result")
    assert result.pii


def test_fee_table_parsing_keeps_the_exact_numbers():
    table = (
        ("Sr. No.", "Subject", "Fee (yearly)", "", "Course fee", "", ""),
        ("", "", "GEN", "ST/SC/BC", "GEN", "BC", "ST/SC"),
        ("1", "Political Science", "3410", "3185", "0", "0", "0"),
    )
    fees = parse_fee_tables(((table,),), "https://example.test/fee.pdf")
    assert len(fees) == 1
    fee = fees[0]
    assert fee.annual_gen == 3410
    assert fee.annual_reserved == 3185
    assert fee.semester_gen == 0
    assert "Bachelor of Arts" in fee.programme
    assert "Social Sciences" in fee.school

    # pdfplumber loses row 33 at the page break; the text fallback must add it.
    with_fallback = parse_fee_tables(
        ((table,),),
        "https://example.test/fee.pdf",
        text="33 Electronics 3675 3200 8000 8000 8000",
    )
    electronics = next(fee for fee in with_fallback if fee.subject == "Electronics")
    assert electronics.annual_gen == 3675
    assert electronics.semester_gen == 8000


def test_documents_and_pg_dates_parse_without_inventing_dates():
    docs = "\n".join(
        [
            "1. Chancellor's portal application form.",
            "2. Two passport-size photographs.",
            "3. Class 10 mark sheet.",
        ]
    )
    assert [item["number"] for item in parse_documents(docs)] == [1, 2, 3]

    pg_text = (
        "Memo dated 15.06.2026. Last Date of Application 25.10.2026. "
        "Publication of First Selection List 27.10.2026. "
        "Last date for payment of online fees - First Selection List 30.10.2026."
    )
    dates = parse_pg_dates(pg_text)
    assert [item["date"] for item in dates] == [
        "2026-10-25",
        "2026-10-27",
        "2026-10-30",
    ]
    assert all(item["needs_review"] for item in dates)


def test_html_extraction_ignores_navigation_and_keeps_the_main_text():
    html = """
    <html><head><title>About | DSPMU</title></head>
    <body>
      <nav><a href="/">Home</a><a href="/admission">Admission</a></nav>
      <main><h1>About the University</h1><p>DSPMU is a state university in Ranchi.</p></main>
      <footer>Footer text</footer>
    </body></html>
    """
    doc = extract_html(html, "https://dspmuranchi.ac.in/cms/about", "https://dspmuranchi.ac.in/cms/about")
    assert "state university" in doc.text
    assert "Home" not in doc.text
    assert doc.title == "About the University"


def test_pdf_links_are_resolved_and_deduplicated():
    html = '<a href="/uploads/a.pdf">A</a><a href="/uploads/a.pdf">A again</a>'
    links = parse_pdf_links(html, "https://dspmuranchi.ac.in/news/admission-merit-list")
    assert len(links) == 1
    assert links[0].url == "https://dspmuranchi.ac.in/uploads/a.pdf"


def _generated() -> tuple[str, str, dict]:
    if not (SITE_MAP.exists() and CORE_CARDS.exists() and FACTS.exists()):
        pytest.skip("generated DSPMU files are missing; run scripts/build_site_index.py")
    return (
        SITE_MAP.read_text(encoding="utf-8"),
        CORE_CARDS.read_text(encoding="utf-8"),
        json.loads(FACTS.read_text(encoding="utf-8")),
    )


def test_generated_files_are_parser_compatible_and_clean():
    site_map, core_cards, facts = _generated()
    assert "## Undergraduate Programs" in site_map
    assert "Meridian" not in site_map
    assert "Meridian" not in core_cards
    assert facts["status"] == "draft"
    assert facts["approved_by"] is None
    assert facts["fees"]
    assert facts["notices"]["pii_excluded"] > 0

    index = EntityIndex(site_map)
    probe = "Bachelor of Arts in Political Science"
    assert index.is_known(probe)
    assert "3410" in index.programme_numbers_for([probe])


def test_guard_accepts_a_real_fee_and_rejects_a_wrong_one():
    site_map, _, _ = _generated()
    index = EntityIndex(site_map)
    good = "The annual fee for Bachelor of Arts in Political Science is ₹3,410 for the general category."
    bad = "The annual fee for Bachelor of Arts in Political Science is ₹14,600."
    assert is_grounded(good, site_map, index)
    assert not is_grounded(bad, site_map, index)
    assert ungrounded_claims(bad, site_map, index)


def test_guard_rejects_an_invented_programme_and_a_roll_number():
    site_map, _, _ = _generated()
    index = EntityIndex(site_map)
    assert not is_grounded(
        "The university offers a Master of Mechanical Engineering.", site_map, index
    )
    assert not is_grounded(
        "The selected candidate's roll number is 22001.", site_map, index
    )


def test_generated_prompt_fits_the_voice_budget():
    _, _, facts = _generated()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["tokens"]["combined"] <= report["tokens"]["budget"]
    assert report["notices"]["included_latest"] > 0
    assert report["ocr"]["needs_review"] in (True, False)
