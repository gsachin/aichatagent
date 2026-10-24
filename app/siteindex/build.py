"""Build the DSPMU site map and core cards.

Run from the repository root::

    .venv/bin/python scripts/build_site_index.py
    .venv/bin/python scripts/build_site_index.py --allow-network

The default build is offline and reads ``content/dspmu/cache``. The cache is
populated from public pages and PDFs; it is not committed. The generated
markdown, facts and notice manifest are committed so the app can run without a
network crawl.

Milestone one deliberately creates no request-path code. It writes:

* ``site_map.md`` — the full, parser-compatible knowledge file
* ``core_cards.md`` — the compact voice prefix
* ``facts.json`` — structured facts with source and validity
* ``notices.jsonl`` — non-PII notice metadata
* ``build_report.json`` — provenance, counts, OCR status and token budgets
* ``nodes/<id>.md`` — extracted page/PDF text for auditing
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.siteindex.discover import (
    Notice,
    find_pdf,
    latest_non_pii,
    parse_nav,
    parse_nav_links,
    parse_news_manifest,
    parse_pdf_links,
    parse_sitemap,
    pii_counts,
)
from app.siteindex.extract import (
    HtmlDoc,
    PdfDoc,
    extract_html,
    extract_pdf,
)
from app.siteindex.facts import (
    FEE_VALID_FROM,
    FEE_VALID_UNTIL,
    ProgrammeFee,
    collect_contacts,
    parse_counselling_tables,
    parse_documents,
    parse_fee_tables,
    parse_pg_dates,
    programme_entry,
    slugify,
)
from app.siteindex.fetch import FetchError, FetchResult, SourceBook, SourceSpec

logger = logging.getLogger("siteindex.build")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CURRENT = "https://dspmuranchi.ac.in"
OLD = "https://oldsite.dspmuranchi.ac.in"

SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("home", CURRENT + "/", "current/home.html", "html"),
    SourceSpec("sitemap", CURRENT + "/sitemap.xml", "current/sitemap.xml", "xml"),
    SourceSpec("about", CURRENT + "/cms/about-the-university", "current/about.html", "html"),
    SourceSpec("vision", CURRENT + "/cms/vision-mission", "current/vision.html", "html"),
    SourceSpec("glance", CURRENT + "/university-at-a-glance", "current/glance.html", "html"),
    SourceSpec("contact", CURRENT + "/cms/contact-us", "current/contact.html", "html"),
    SourceSpec("merit", CURRENT + "/news/admission-merit-list", "current/merit.html", "html"),
    SourceSpec("syllabus", CURRENT + "/syllabus", "current/syllabus.html", "html"),
    SourceSpec("dept-history", CURRENT + "/departments/history", "current/dept-history.html", "html"),
    SourceSpec("faculty-science", CURRENT + "/faculties/science", "current/faculty-science.html", "html"),
    SourceSpec("news", OLD + "/News.aspx", "old/news.html", "html"),
    SourceSpec(
        "fee2026",
        CURRENT + "/uploads/news/gallery/1785318304_034b0538cc479c48018a.pdf",
        "pdfs/fee2026.pdf",
        "pdf",
    ),
    SourceSpec(
        "counsel2026",
        CURRENT + "/uploads/news/gallery/1785318278_a631ff259f9905bd2a6e.pdf",
        "pdfs/counsel2026.pdf",
        "pdf",
    ),
    SourceSpec(
        "docs2026",
        CURRENT + "/uploads/news/gallery/1785318254_f3dbe4086bc6fe3257e9.pdf",
        "pdfs/docs2026.pdf",
        "pdf",
    ),
    SourceSpec(
        "pgadm2026",
        CURRENT + "/uploads/news/gallery/1792744350_a0675b680257e2c88191.pdf",
        "pdfs/pgadm2026.pdf",
        "pdf",
    ),
)

SOURCE_BY_KEY = {source.key: source for source in SOURCES}


class BuildError(RuntimeError):
    """The built index failed a correctness or budget check."""


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def _demote_headings(text: str, levels: int = 3) -> str:
    """Push page-level headings below the site-map section that contains them."""
    return re.sub(
        r"^(#{1,6})\s+",
        lambda match: "#" * min(6, len(match.group(1)) + levels) + " ",
        text or "",
        flags=re.M,
    )


def _excerpt(text: str, limit: int = 700) -> str:
    value = re.sub(r"\n{3,}", "\n\n", _demote_headings(text).strip())
    if len(value) <= limit:
        return value
    cut = value[:limit]
    for separator in ("\n\n", ". ", "\n"):
        position = cut.rfind(separator)
        if position > int(limit * 0.6):
            return cut[: position + len(separator)].strip() + " …"
    return cut.strip() + " …"


def _load(book: SourceBook, key: str) -> FetchResult:
    spec = SOURCE_BY_KEY[key]
    return book.load(spec)


def _departments_from_nav(links) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for node in links:
        if "/departments/" not in node.url:
            continue
        label = node.label or node.url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        found.setdefault(node.url, label)
    return sorted(((label, url) for url, label in found.items()), key=lambda item: item[0].lower())


def _departments_from_sitemap(sitemap: list[dict[str, str]]) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for entry in sitemap:
        url = entry.get("loc", "")
        if "/departments/" not in url:
            continue
        label = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        found.setdefault(url, label)
    return sorted(((label, url) for url, label in found.items()), key=lambda item: item[0].lower())


def _notice_lines(notices: list[Notice]) -> str:
    if not notices:
        return "- No dated non-PII notices were readable from the manifest."
    lines = []
    for notice in notices:
        date = notice.date or notice.date_text or "date unknown"
        lines.append(f"- {date} · {notice.title} · {notice.category}")
    return "\n".join(lines)


def _render_site_map(
    *,
    pages: dict[str, HtmlDoc],
    fees: list[ProgrammeFee],
    counselling,
    documents: list[dict],
    pg_dates: list[dict],
    contacts: dict,
    notices: list[Notice],
    latest: list[Notice],
    departments: list[tuple[str, str]],
    sources: dict[str, FetchResult],
    pg_doc: PdfDoc,
    counsel_doc: PdfDoc,
    fee_url: str,
) -> str:
    about = pages.get("about")
    vision = pages.get("vision")
    glance = pages.get("glance")
    history = pages.get("dept-history")
    science = pages.get("faculty-science")

    blocks: list[str] = [
        "# DSPMU Site Knowledge",
        (
            f"> Draft generated from public DSPMU sources on {datetime.now(timezone.utc).date().isoformat()}. "
            "This is not an official university document. Fees and dates must be confirmed with the "
            "university before payment or travel."
        ),
        "## University",
    ]
    if about and about.main_chars:
        blocks += ["### About DSPMU", _excerpt(about.markdown or about.text), f"Source: {about.url}"]
    if vision and vision.main_chars:
        blocks += ["### Vision and mission", _excerpt(vision.markdown or vision.text), f"Source: {vision.url}"]
    if glance and glance.main_chars:
        blocks += ["### University at a glance", _excerpt(glance.markdown or glance.text), f"Source: {glance.url}"]

    blocks += ["## Admissions 2026-27", "### Process"]
    blocks.append(
        "UG and PG applications are made through the Chancellor Portal "
        "(https://jharkhanduniversities.nic.in). The university publishes the "
        "counselling schedule, document checklist and current admission notices "
        "as PDFs linked from /news/admission-merit-list. The current site's fee "
        "and examination-notice HTML pages are empty; use the linked PDFs."
    )
    blocks += ["### Eligibility"]
    blocks.append(
        "The public pages reviewed for this build do not publish a consolidated, "
        "programme-specific eligibility table. Eligibility is programme-specific; "
        "confirm it with the department or admission office before applying. "
        "Check the current admission notice for CUET or other programme requirements."
    )
    if documents:
        blocks += ["### Documents required for UG admission"]
        blocks += [f"- {item['number']}. {item['text']}" for item in documents]
        blocks.append(f"Source: {sources['docs2026'].url}")
    if counselling:
        blocks += ["### UG counselling schedule 2026"]
        blocks += ["| Round | Dates | Publication |", "| --- | --- | --- |"]
        for item in counselling:
            blocks.append(f"| {item.round} | {item.dates or '—'} | {item.publication or '—'} |")
        if "sports certificate" in (counsel_doc.text or "").lower():
            blocks.append(
                "- Sports-category certificate verification: 20–21 July 2026 at 10:30 AM in Jacob Hall."
            )
        blocks.append(f"Source: {sources['counsel2026'].url}")

    # No paragraph may sit between this heading and the first ### entry: the
    # existing kb_entities parser treats the first non-heading text as an
    # entry head. Keep the note above the heading.
    blocks += ["## Fees and Programmes"]
    blocks.append(
        "The 2026 fee sheet lists the programmes below. Degree labels are inferred "
        "from the subject and faculty because the fee PDF names subjects, not "
        "degrees. The fee numbers are copied exactly from the PDF."
    )
    blocks.append("## Undergraduate Programs")
    for fee in fees:
        blocks.append(programme_entry(fee).strip())

    blocks += ["## Important Dates", "### UG counselling 2026"]
    if counselling:
        for item in counselling:
            blocks.append(
                f"- {item.round}: {item.dates or 'date not stated'}"
                + (f"; merit list publication {item.publication}" if item.publication else "")
            )
    blocks += ["### PG admission 2026-28"]
    for item in pg_dates:
        marker = " (needs review: OCR)" if item.get("needs_review") else ""
        blocks.append(f"- {item['activity']}: {item['date_text']}{marker}")
    blocks.append(f"Source: {sources['pgadm2026'].url} (OCR; needs human review)")
    blocks.append(f"- Fee facts are valid from {FEE_VALID_FROM} through {FEE_VALID_UNTIL}; confirm before payment.")

    blocks += ["## Fees Structure"]
    blocks.append(
        "The programme entries under ## Undergraduate Programs carry the 2026-27 "
        "fees: annual fee paid yearly and course fee paid per semester. "
        f"Source: the 2026 fee-structure PDF linked from /news/admission-merit-list ({fee_url})."
    )

    blocks += ["## Departments"]
    if history and history.main_chars:
        blocks += ["### History", _excerpt(history.markdown or history.text, 600), f"Source: {history.url}"]
    if science and science.main_chars:
        blocks += ["### Faculty of Science", _excerpt(science.markdown or science.text, 600), f"Source: {science.url}"]
    if departments:
        blocks.append("### Department pages in the sitemap")
        blocks.append(", ".join(label for label, _ in departments))
        blocks.append(f"Source: {CURRENT}/sitemap.xml")

    blocks += ["## Notices", _notice_lines(latest)]
    blocks.append(
        "The old-site manifest is the working PDF source: "
        f"{sources['news'].url}. The current archive page lists 4,325 rows, but "
        "its PDF links return the site's 404 page on the new host."
    )
    blocks += ["## Results and Merit Lists"]
    blocks.append(
        "Result sheets, merit lists, selection lists and applicant lists are excluded "
        "from this knowledge file because they contain student names and roll numbers. "
        "Use the official pages: https://dspmuranchi.ac.in/news/admission-merit-list "
        "and https://dspmuranchi.ac.in/archive-notice."
    )
    counts = pii_counts(notices)
    if counts:
        blocks.append(
            "Excluded by the category and title PII filters: "
            + ", ".join(f"{name} {count}" for name, count in counts.items())
            + "."
        )

    blocks += ["## Contacts"]
    blocks.append(f"- Address: {contacts['address']}")
    blocks.append(f"- Phone: {contacts['phone']}")
    for email in contacts["emails"]:
        blocks.append(f"- Email: {email}")
    blocks.append(f"- Website: {contacts['website']}")
    blocks.append(f"- Contact page: {contacts['contact_page']}")
    blocks.append(f"- Admission notices: {contacts['admission_page']}")

    blocks += ["## Source Notes"]
    blocks.append(
        "The current site is a thin CMS: several content pages are empty and the "
        "fee and examination-notice pages contain only their titles. The old site's "
        "News.aspx is the working notice manifest and /pdf/News file server."
    )
    blocks.append(f"- Current site: {CURRENT}")
    blocks.append(f"- Sitemap: {CURRENT}/sitemap.xml")
    blocks.append(f"- Old site notice manifest: {sources['news'].url}")
    blocks.append(f"- Current archive page: {CURRENT}/archive-notice")
    blocks.append(f"- Admission notices: {CURRENT}/news/admission-merit-list")
    blocks.append(f"- Fee PDF: {fee_url}")
    if pages.get("syllabus"):
        blocks.append(f"- Syllabus page: {pages['syllabus'].url}")
    if pages.get("contact"):
        blocks.append(f"- Contact page: {pages['contact'].url}")
    return "\n\n".join(block.strip() for block in blocks if block and block.strip()) + "\n"


def _fee_range(fees: list[ProgrammeFee]) -> tuple[int, int, int, int]:
    annual = [fee.annual_gen for fee in fees if fee.annual_gen]
    reserved = [fee.annual_reserved for fee in fees if fee.annual_reserved]
    semester = [fee.semester_gen for fee in fees if fee.semester_gen]
    return (
        min(annual) if annual else 0,
        max(annual) if annual else 0,
        min(reserved) if reserved else 0,
        max(reserved) if reserved else 0,
    )


def _render_core_cards(
    *,
    fees: list[ProgrammeFee],
    counselling,
    documents: list[dict],
    pg_dates: list[dict],
    contacts: dict,
    fee_url: str,
    notice_url: str,
    pg_doc: PdfDoc,
) -> str:
    annual_min, annual_max, reserved_min, reserved_max = _fee_range(fees)
    common_subjects = [
        "Political Science", "History", "Economics", "English", "Physics",
        "Chemistry", "Mathematics", "Botany", "Zoology", "Commerce", "BBA",
        "Computer Application",
    ]
    by_subject = {fee.subject.lower(): fee for fee in fees}
    blocks: list[str] = [
        "# DSPMU Core Cards",
        (
            "> Draft facts for the admissions agent. Fees and dates are public "
            "university data but have not been signed off. Confirm before payment."
        ),
        "## University",
        "Dr. Shyama Prasad Mukherjee University (DSPMU), Ranchi, Jharkhand.",
        f"- Address: {contacts['address']}",
        f"- Phone: {contacts['phone']}",
        f"- Email: {contacts['emails'][0] if contacts['emails'] else 'registrar@dspmuranchi.ac.in'}",
        f"- Website: {contacts['website']}",
        "## Admissions 2026-27",
        "- UG and PG applications: Chancellor Portal, https://jharkhanduniversities.nic.in.",
        "- The current site's fee and examination-notice pages are empty; the PDFs linked from /news/admission-merit-list are the source.",
        "- Eligibility is programme-specific. The public pages reviewed do not publish a consolidated eligibility table; confirm with the department or admission office.",
    ]
    if documents:
        blocks.append("### Documents required for UG admission")
        blocks += [f"- {item['number']}. {item['text']}" for item in documents]
    if counselling:
        blocks.append("### UG counselling 2026")
        for item in counselling:
            blocks.append(
                f"- {item.round}: {item.dates or 'date not stated'}"
                + (f"; merit list publication {item.publication}" if item.publication else "")
            )
    blocks += ["### PG admission 2026-28"]
    for item in pg_dates:
        marker = " (needs review: OCR)" if item.get("needs_review") else ""
        blocks.append(f"- {item['activity']}: {item['date_text']}{marker}")

    blocks += ["## Fees 2026-27"]
    if fees:
        blocks.append(
            f"- Annual fee range: ₹{annual_min:,}–₹{annual_max:,} for the general category; "
            f"₹{reserved_min:,}–₹{reserved_max:,} for ST/SC/BC I & II."
        )
        blocks.append(
            "- Course fee per semester ranges from ₹0 to ₹18,000 for the general category, depending on programme."
        )
        blocks.append("| Programme | Annual GEN | Annual reserved | Semester GEN |")
        blocks.append("| --- | ---: | ---: | ---: |")
        for subject in common_subjects:
            fee = by_subject.get(subject.lower())
            if fee is None:
                continue
            blocks.append(
                f"| {fee.programme} | ₹{fee.annual_gen:,} | "
                f"₹{fee.annual_reserved:,} | ₹{fee.semester_gen:,} |"
            )
        blocks.append(
            f"The full {len(fees)}-programme table and per-programme entries are in site_map.md. "
            f"Source: {fee_url}. Valid {FEE_VALID_FROM} to {FEE_VALID_UNTIL}."
        )
    else:
        blocks.append("- The fee table could not be parsed; do not quote fees.")

    blocks += ["## Contacts", f"- Address: {contacts['address']}", f"- Phone: {contacts['phone']}"]
    for email in contacts["emails"]:
        blocks.append(f"- Email: {email}")
    blocks += [
        "## Boundaries",
        "- Results, merit lists, selection lists and applicant lists are excluded because they contain student names and roll numbers. Point callers to the official pages instead.",
        "- Do not answer an expired fee or date. Fee facts are valid through 2027-06-30; the counselling schedule is valid through 2026-12-31.",
        "- If a fact is not in the knowledge file, say so and give the registrar's contact. Do not infer a fee, date or programme.",
        f"- OCR-derived PG dates are marked; source: {pg_doc.ocr_quality} OCR ({pg_doc.ocr_lang or 'no OCR'}).",
        "## Sources",
        f"- Fee structure 2026: {fee_url}",
        f"- Notice manifest: {notice_url}",
        "- Counselling, documents and PG admission source URLs are in site_map.md.",
    ]
    return "\n\n".join(block.strip() for block in blocks if block and block.strip()) + "\n"


def _write_node(out_dir: Path, node_id: str, title: str, url: str, body: str, *, kind: str, ocr: bool = False, needs_review: bool = False) -> None:
    nodes_dir = out_dir / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    header = [
        f"# {title}",
        f"Source: {url}",
        f"Type: {kind}",
        f"OCR: {'yes' if ocr else 'no'}",
        f"Needs review: {'yes' if needs_review else 'no'}",
        "---",
    ]
    (nodes_dir / f"{slugify(node_id)}.md").write_text(
        "\n".join(header) + "\n\n" + (body or "").strip() + "\n",
        encoding="utf-8",
    )


def build(
    *,
    cache_dir: Path | str = PROJECT_ROOT / "content/dspmu/cache",
    out_dir: Path | str = PROJECT_ROOT / "content/dspmu",
    allow_network: bool = False,
    ocr: bool = True,
    max_notices: int = 12,
    max_tokens: int = 8000,
) -> dict:
    cache_path = Path(cache_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    book = SourceBook(cache_path, allow_network=allow_network)
    loaded: dict[str, FetchResult] = {}

    def load(key: str) -> FetchResult:
        result = _load(book, key)
        loaded[key] = result
        return result

    home = load("home")
    sitemap_result = load("sitemap")
    sitemap = parse_sitemap(sitemap_result.text)
    merit = load("merit")
    old_news = load("news")

    nav = parse_nav(home.text, home.url)
    nav_links = parse_nav_links(home.text, home.url)
    departments = _departments_from_nav(nav_links) or _departments_from_sitemap(sitemap)
    merit_pdfs = parse_pdf_links(merit.text, merit.url)
    notices = parse_news_manifest(old_news.text, old_news.url)
    non_pii = [notice for notice in notices if not notice.pii]
    latest = latest_non_pii(notices, max_notices)

    pages: dict[str, HtmlDoc] = {}
    for key in ("about", "vision", "glance", "contact", "syllabus", "dept-history", "faculty-science"):
        result = load(key)
        pages[key] = extract_html(result.text, result.url, result.url)
        if pages[key].main_chars < 100:
            logger.warning("%s has only %d characters of main text", key, pages[key].main_chars)
        _write_node(
            output_path,
            f"page-{key}",
            pages[key].title or key,
            pages[key].url,
            pages[key].markdown or pages[key].text,
            kind="page",
        )

    fee_result = load("fee2026")
    counsel_result = load("counsel2026")
    docs_result = load("docs2026")
    pg_result = load("pgadm2026")

    fee_doc = extract_pdf(fee_result.body, ocr=False)
    counsel_doc = extract_pdf(counsel_result.body, ocr=False)
    docs_doc = extract_pdf(docs_result.body, ocr=False)
    pg_doc = extract_pdf(pg_result.body, ocr=ocr)

    for key, result, doc in (
        ("fee2026", fee_result, fee_doc),
        ("counsel2026", counsel_result, counsel_doc),
        ("docs2026", docs_result, docs_doc),
        ("pgadm2026", pg_result, pg_doc),
    ):
        _write_node(
            output_path,
            f"pdf-{key}",
            key,
            result.url,
            doc.text,
            kind="pdf",
            ocr=doc.ocr,
            needs_review=doc.needs_review,
        )

    fees = parse_fee_tables(fee_doc.tables, fee_result.url, text=fee_doc.text)
    if not fees:
        raise BuildError("the fee PDF did not yield any programme rows")
    counselling = parse_counselling_tables(counsel_doc.tables, counsel_result.url)
    documents = parse_documents(docs_doc.text)
    pg_dates = parse_pg_dates(pg_doc.text)
    contacts = collect_contacts(pages.get("contact"), home, pg_doc.text)

    warnings: list[str] = []
    for needle, key in (
        ("Fee Structure", "fee2026"),
        ("Counselling", "counsel2026"),
        ("Documents Required", "docs2026"),
        ("PG Admission", "pgadm2026"),
    ):
        if find_pdf(merit_pdfs, needle) is None:
            warnings.append(
                f"merit page did not expose a PDF with {needle!r}; using the cached source for {key}"
            )
    if pg_doc.needs_review:
        warnings.append(
            f"PG admission PDF required OCR ({pg_doc.ocr_quality}, {pg_doc.ocr_lang or 'no language'}); "
            "its dates are marked needs_review"
        )
    if not sitemap:
        warnings.append("sitemap.xml could not be parsed")

    site_map = _render_site_map(
        pages=pages,
        fees=fees,
        counselling=counselling,
        documents=documents,
        pg_dates=pg_dates,
        contacts=contacts,
        notices=notices,
        latest=latest,
        departments=departments,
        sources=loaded,
        pg_doc=pg_doc,
        counsel_doc=counsel_doc,
        fee_url=fee_result.url,
    )
    core_cards = _render_core_cards(
        fees=fees,
        counselling=counselling,
        documents=documents,
        pg_dates=pg_dates,
        contacts=contacts,
        fee_url=fee_result.url,
        notice_url=old_news.url,
        pg_doc=pg_doc,
    )

    site_tokens = _count_tokens(site_map)
    core_tokens = _count_tokens(core_cards)
    combined = site_tokens + core_tokens
    if combined > max_tokens:
        raise BuildError(
            f"combined prompt is {combined} tokens, over the {max_tokens}-token budget; "
            f"site_map={site_tokens}, core_cards={core_tokens}. Trim notices/departments or raise --max-tokens."
        )

    # Parser and guard checks. This is the reason the file exists.
    from app.kb_entities import EntityIndex
    from app.voice_grounding import is_grounded, ungrounded_claims

    index = EntityIndex(site_map)
    if not index.names:
        raise BuildError("kb_entities found no programme names in site_map.md")
    probe = "Bachelor of Arts in Political Science"
    if probe not in index.names:
        raise BuildError(f"kb_entities did not index {probe!r}; names={index.names[:5]}")
    owned = index.programme_numbers_for([probe])
    if "3410" not in owned:
        raise BuildError(
            "kb_entities did not attribute the Political Science fee (3410) to its programme; "
            f"owned={sorted(owned)}"
        )
    probe_claim = (
        "The annual fee for Bachelor of Arts in Political Science is ₹3,410 "
        "for the general category."
    )
    if not is_grounded(probe_claim, site_map, index):
        raise BuildError(
            "today's grounding guard rejected a correct DSPMU fee; "
            f"ungrounded={ungrounded_claims(probe_claim, site_map, index)}"
        )
    if "meridian" in (site_map + core_cards).lower():
        raise BuildError("generated files still mention Meridian")
    if "## Undergraduate Programs" not in site_map:
        raise BuildError("site_map.md is missing the ## Undergraduate Programs section")
    # Notice titles live in one section. Check that section rather than the
    # whole document: a generic title like "Admission" would otherwise match a
    # section heading and produce a false alarm.
    notices_section = site_map.split("## Notices", 1)[-1].split(
        "## Results and Merit Lists", 1
    )[0]
    pii_leaks = [
        notice.title
        for notice in notices
        if notice.pii and notice.title and notice.title in notices_section
    ]
    if pii_leaks:
        raise BuildError(f"PII notice leaked into the site map: {pii_leaks[:3]}")

    facts = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "university": {
            "name": "Dr. Shyama Prasad Mukherjee University",
            "short_name": "DSPMU",
            "address": contacts["address"],
            "phone": contacts["phone"],
            "emails": contacts["emails"],
            "website": contacts["website"],
        },
        "admissions": {
            "portal": "https://jharkhanduniversities.nic.in",
            "note": "Eligibility is programme-specific; the public pages reviewed do not publish a consolidated eligibility table.",
        },
        "fees": [fee.to_dict() for fee in fees],
        "counselling": [item.to_dict() for item in counselling],
        "documents": documents,
        "pg_dates": pg_dates,
        "contacts": contacts,
        "notices": {
            "manifest_rows": len(notices),
            "non_pii": len(non_pii),
            "pii_excluded": len(notices) - len(non_pii),
            "pii_by_category": pii_counts(notices),
            "included_latest": len(latest),
            "source_manifest": old_news.url,
        },
        "sources": {
            key: {
                "url": result.url,
                "from_cache": result.from_cache,
                "content_type": result.content_type,
                "bytes": len(result.body),
            }
            for key, result in loaded.items()
        },
        "status": "draft",
        "approved_by": None,
        "valid_from": FEE_VALID_FROM,
        "valid_until": FEE_VALID_UNTIL,
    }

    report = {
        "generated_at": facts["generated_at"],
        "generator": "app/siteindex",
        "mode": "network" if allow_network else "offline-cache",
        "cache_dir": str(cache_path),
        "output_dir": str(output_path),
        "sources": facts["sources"],
        "sitemap_urls": len(sitemap),
        "nav_nodes": len(nav),
        "nav_links": len(nav_links),
        "departments": len(departments),
        "notices": facts["notices"],
        "fees": {"programmes": len(fees), "source": fee_result.url},
        "counselling_rounds": len(counselling),
        "documents": len(documents),
        "pg_dates": pg_dates,
        "ocr": {
            "used": pg_doc.ocr,
            "available": pg_doc.ocr_available,
            "lang": pg_doc.ocr_lang,
            "quality": pg_doc.ocr_quality,
            "pages": pg_doc.pages,
            "needs_review": pg_doc.needs_review,
        },
        "tokens": {
            "site_map": site_tokens,
            "core_cards": core_tokens,
            "combined": combined,
            "budget": max_tokens,
        },
        "validation": {
            "parser": "ok",
            "probe": probe,
            "probe_numbers": sorted(owned),
            "no_meridian": True,
            "no_pii_titles": True,
        },
        "warnings": warnings,
        "current_archive_note": (
            "The current /archive-notice page lists 4,325 rows, but its PDF links "
            "return the site's 404 page on the new host. The old-site News.aspx "
            "manifest is the working source."
        ),
    }

    (output_path / "site_map.md").write_text(site_map, encoding="utf-8")
    (output_path / "core_cards.md").write_text(core_cards, encoding="utf-8")
    (output_path / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_path / "notices.jsonl").open("w", encoding="utf-8") as handle:
        for notice in non_pii:
            handle.write(json.dumps(notice.to_dict(), ensure_ascii=False) + "\n")
    (output_path / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info(
        "built %s (%d tokens) and %s (%d tokens)",
        output_path / "site_map.md",
        site_tokens,
        output_path / "core_cards.md",
        core_tokens,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default=str(PROJECT_ROOT / "content/dspmu/cache"),
        help="directory holding the cached public sources",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "content/dspmu"),
        help="directory for the generated knowledge files",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help=(
            "fetch missing sources from the live site (robots.txt and content "
            "signals are checked; obtain the university's written permission first)"
        ),
    )
    parser.add_argument("--no-ocr", action="store_true", help="skip the OCR fallback")
    parser.add_argument("--max-notices", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        report = build(
            cache_dir=args.cache_dir,
            out_dir=args.out_dir,
            allow_network=args.allow_network,
            ocr=not args.no_ocr,
            max_notices=args.max_notices,
            max_tokens=args.max_tokens,
        )
    except (BuildError, FileNotFoundError, FetchError) as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    tokens = report["tokens"]
    print(f"site_map.md      {tokens['site_map']:>6} tokens")
    print(f"core_cards.md    {tokens['core_cards']:>6} tokens")
    print(f"combined         {tokens['combined']:>6} / {tokens['budget']} tokens")
    print(
        "notices          "
        f"{report['notices']['manifest_rows']} rows, "
        f"{report['notices']['non_pii']} non-PII, "
        f"{report['notices']['pii_excluded']} excluded"
    )
    print(f"fees             {report['fees']['programmes']} programmes")
    print(f"documents        {report['documents']} items")
    print(
        "OCR              "
        f"used={report['ocr']['used']} lang={report['ocr']['lang'] or 'none'} "
        f"quality={report['ocr']['quality']}"
    )
    print(f"validation       {report['validation']}")
    if report["warnings"]:
        print("warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
