"""Build the split DSPMU prompt corpus and entity corpus.

Run from the repository root::

    .venv/bin/python scripts/build_site_index.py
    .venv/bin/python scripts/build_site_index.py --allow-network

The default build is offline and reads ``content/dspmu/cache``. The cache is
populated from public pages and PDFs; it is not committed. The generated
markdown, facts and notice manifest are committed so the app can run without a
network crawl.

The prompt is split deliberately:

* ``core_cards.md`` — the stable, high-signal facts prefix
* ``site_map.md`` — compact orientation and source notes, no 43-entry fee dump
* ``recent_notices.md`` — volatile latest notices, loaded last
* ``voice_kb.md`` — the full programme entity corpus, used only by
  ``app.kb_entities`` via ``VOICE_KB_PATH``; never loaded into the prompt
* ``facts.json`` — structured facts with source and validity
* ``notices.jsonl`` — non-PII notice metadata
* ``build_report.json`` — provenance, counts, OCR status and real prompt budgets
* ``nodes/<id>.md`` — extracted page/PDF text for auditing

The build fails unless ``build_voice_system_prompt(...)`` plus the question and
output reserve fits the full voice budget. It does not rely on the document
token count alone.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
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


def _render_site_map(
    *,
    fees: list[ProgrammeFee],
    contacts: dict,
    departments: list[tuple[str, str]],
    sources: dict[str, FetchResult],
    fee_url: str,
) -> str:
    """Compact prompt orientation. The full entity corpus is voice_kb.md."""
    annual_min, annual_max, reserved_min, reserved_max = _fee_range(fees)
    email = contacts["emails"][0] if contacts["emails"] else "registrar@dspmuranchi.ac.in"
    blocks: list[str] = [
        "# DSPMU Site Knowledge",
        (
            "> Draft orientation for the admissions agent. Fees and dates must be "
            "confirmed with the university. The prompt carries the common facts and "
            "recent notices; the full fact and notice indexes are maintained outside it."
        ),
        "## University",
        "Dr. Shyama Prasad Mukherjee University (DSPMU), Ranchi, Jharkhand.",
        f"Source: {sources['about'].url}",
        "## Admissions 2026-27",
        (
            "UG and PG applications are made through the Chancellor Portal "
            "(https://jharkhanduniversities.nic.in). Eligibility is programme-specific; "
            "the public pages reviewed do not publish a consolidated eligibility table. "
            "The document checklist and UG counselling dates are in the core cards."
        ),
        f"Source: {sources['merit'].url}",
        "## Fees 2026-27",
        (
            f"The 2026 fee sheet covers {len(fees)} UG programmes. Annual fee: "
            f"₹{annual_min:,}–₹{annual_max:,} (GEN), ₹{reserved_min:,}–₹{reserved_max:,} "
            "(ST/SC/BC I & II). Semester course fee: ₹0–₹18,000 (GEN), depending on programme. "
            "Common programmes are in the core cards; if a programme is not listed, say the "
            "fee needs confirmation and give the registrar contact."
        ),
        f"Source: {fee_url}. Valid {FEE_VALID_FROM} to {FEE_VALID_UNTIL}.",
        "## Important Dates",
        (
            "UG counselling dates and the document checklist are in the core cards. "
            "PG admission dates are in the core cards and are marked for OCR review."
        ),
        f"Source: {sources['pgadm2026'].url}",
        "## Departments",
        (
            f"{len(departments)} department pages are in the sitemap. This prompt includes "
            "History and the Faculty of Science; the sitemap lists the rest."
        ),
        f"Source: {CURRENT}/sitemap.xml",
        "## Results and Merit Lists",
        (
            "Result sheets, merit lists, selection lists and applicant lists are excluded "
            "because they contain student names and roll numbers. Use the official pages: "
            "https://dspmuranchi.ac.in/news/admission-merit-list and "
            "https://dspmuranchi.ac.in/archive-notice."
        ),
        "## Contacts",
        f"- Phone: {contacts['phone']}",
        f"- Email: {email}",
        f"- Website: {contacts['website']}",
        f"- Contact page: {contacts['contact_page']}",
        "## Source Notes",
        f"- Current site: {CURRENT}",
        f"- Sitemap: {CURRENT}/sitemap.xml",
        f"- Old-site notice manifest: {sources['news'].url}",
        f"- Current archive page: {CURRENT}/archive-notice",
        f"- Admission notices: {CURRENT}/news/admission-merit-list",
        f"- UG counselling PDF: {sources['counsel2026'].url}",
        f"- UG documents PDF: {sources['docs2026'].url}",
        f"- Fee PDF: {fee_url}",
    ]
    return "\n\n".join(block.strip() for block in blocks if block and block.strip()) + "\n"


def _render_voice_kb(fees: list[ProgrammeFee]) -> str:
    """Entity corpus for app.kb_entities. Never loaded into the prompt."""
    blocks: list[str] = [
        "# DSPMU Voice Knowledge",
        (
            "> Entity corpus for the grounding index. This file is not loaded into the "
            "prompt; the prompt uses core_cards.md, site_map.md and recent_notices.md."
        ),
        "## Undergraduate Programs",
    ]
    for fee in fees:
        blocks.append(programme_entry(fee).strip())
    return "\n\n".join(blocks) + "\n"


def _render_recent_notices(notices: list[Notice], source_url: str) -> str:
    """Volatile tail: latest non-PII notices. Loaded last in DOC_PATHS."""
    blocks: list[str] = [
        "# Recent DSPMU Notices",
        f"> Latest non-PII notices. Source: {source_url}",
        (
            "Full archive: https://dspmuranchi.ac.in/archive-notice. "
            "Results and merit lists are excluded."
        ),
    ]
    if notices:
        for notice in notices:
            date = notice.date or notice.date_text or "date unknown"
            blocks.append(f"- {date} · {notice.title} · {notice.category}")
    else:
        blocks.append("- No dated non-PII notices were readable from the manifest.")
    return "\n".join(blocks) + "\n"


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
            f"The full {len(fees)}-programme table is maintained outside this prompt; "
            f"this card lists the common programmes. If a programme is not listed, say the "
            f"fee needs confirmation. Source: {fee_url}. "
            f"Valid {FEE_VALID_FROM} to {FEE_VALID_UNTIL}."
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
        "- PG programme lists, fees, seats and eligibility details are out of scope for this verified profile. When asked, tell the caller to contact the university for the latest copy; do not infer or quote them.",
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
    max_doc_tokens: int = 4000,
    chat_budget: int = 30000,
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
        fees=fees,
        contacts=contacts,
        departments=departments,
        sources=loaded,
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
    voice_kb = _render_voice_kb(fees)
    recent_notices = _render_recent_notices(latest, old_news.url)
    document_text = (
        "\n\n".join(
            piece.strip()
            for piece in (core_cards, site_map, recent_notices)
            if piece.strip()
        )
        + "\n"
    )

    core_tokens = _count_tokens(core_cards)
    site_tokens = _count_tokens(site_map)
    recent_tokens = _count_tokens(recent_notices)
    document_tokens = _count_tokens(document_text)
    voice_kb_tokens = _count_tokens(voice_kb)

    # The 8k gate is the REAL prompt, not just the document files. The voice
    # wrapper is ~4k tokens; a document-only budget would let the full prompt
    # exceed the latency target without the build noticing.
    from app.config import settings
    from app.rag import SYSTEM_PROMPT
    from app.voice_system_prompt import build_voice_system_prompt

    probe_question = "What is the annual fee for Bachelor of Arts in Political Science?"
    output_reserve = int(settings.LLM_MAX_TOKENS or 192)
    voice_prompt = build_voice_system_prompt(document_text)
    chat_prompt = SYSTEM_PROMPT.format(context=document_text)
    voice_prompt_tokens = _count_tokens(voice_prompt)
    chat_prompt_tokens = _count_tokens(chat_prompt)
    question_tokens = _count_tokens(probe_question)
    voice_total = voice_prompt_tokens + question_tokens + output_reserve
    chat_total = chat_prompt_tokens + question_tokens + output_reserve

    if document_tokens > max_doc_tokens:
        raise BuildError(
            f"document context is {document_tokens} tokens, over the {max_doc_tokens}-token "
            f"document budget (core={core_tokens}, site={site_tokens}, notices={recent_tokens})"
        )
    if voice_total > max_tokens:
        raise BuildError(
            f"full voice prompt is {voice_total} tokens, over the {max_tokens}-token budget "
            f"(wrapper={voice_prompt_tokens - document_tokens}, document={document_tokens}, "
            f"question+output={question_tokens + output_reserve})"
        )
    if chat_total > chat_budget:
        raise BuildError(
            f"full chat prompt is {chat_total} tokens, over the {chat_budget}-token budget "
            f"(wrapper={chat_prompt_tokens - document_tokens}, document={document_tokens})"
        )

    # Parser and guard checks. This is the reason the file exists. The entity
    # corpus is voice_kb.md; the prompt/evidence corpus is document_text. A
    # claim may be grounded only if it is in the prompt; the index knows the
    # full programme list. The runtime site router injects the exact fee fact
    # for a non-top programme before the guard judges the turn.
    from app.kb_entities import EntityIndex
    from app.voice_grounding import is_grounded, ungrounded_claims

    index = EntityIndex(voice_kb)
    if not index.names:
        raise BuildError("kb_entities found no programme names in voice_kb.md")
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
    if not is_grounded(probe_claim, document_text, index):
        raise BuildError(
            "today's grounding guard rejected a correct DSPMU fee; "
            f"ungrounded={ungrounded_claims(probe_claim, document_text, index)}"
        )
    if "meridian" in (site_map + core_cards + recent_notices + voice_kb).lower():
        raise BuildError("generated files still mention Meridian")
    if "## Undergraduate Programs" not in voice_kb:
        raise BuildError("voice_kb.md is missing the ## Undergraduate Programs section")
    pii_leaks = [
        notice.title
        for notice in notices
        if notice.pii and notice.title and notice.title in recent_notices
    ]
    if pii_leaks:
        raise BuildError(f"PII notice leaked into the prompt tail: {pii_leaks[:3]}")

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

    fact_ids = (
        [fee["fact_id"] for fee in facts["fees"]]
        + (["admissions.documents.2026"] if documents else [])
        + (["admissions.counselling.2026"] if counselling else [])
        + [
            f"admissions.pg.{item['date']}"
            for item in pg_dates
            if item.get("date")
        ]
    )
    prompt_manifest = {
        "bundle_id": "dspmu-site-v1",
        "generated_at": facts["generated_at"],
        "valid_until": FEE_VALID_UNTIL,
        "prompt_order": ["core_cards.md", "site_map.md", "recent_notices.md"],
        "node_ids": ["core_cards", "site_map", "recent_notices"],
        "fact_ids": list(dict.fromkeys(fact_ids)),
        "notice_ids": [notice.id for notice in latest],
        "source_urls": sorted({result.url for result in loaded.values()}),
    }
    if not prompt_manifest["fact_ids"]:
        raise BuildError("prompt manifest has no fact IDs")

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
        "prompt_order": ["core_cards.md", "site_map.md", "recent_notices.md"],
        "tokens": {
            "core_cards": core_tokens,
            "site_map": site_tokens,
            "recent_notices": recent_tokens,
            "document": document_tokens,
            "voice_kb": voice_kb_tokens,
            "voice_prompt": voice_prompt_tokens,
            "voice_total": voice_total,
            "chat_prompt": chat_prompt_tokens,
            "chat_total": chat_total,
            "doc_budget": max_doc_tokens,
            "voice_budget": max_tokens,
            "chat_budget": chat_budget,
        },
        "validation": {
            "parser": "ok",
            "probe": probe,
            "probe_numbers": sorted(owned),
            "no_meridian": True,
            "no_pii_titles": True,
            "prompt_split": True,
            "doc_budget_ok": True,
            "voice_budget_ok": True,
            "chat_budget_ok": True,
        },
        "prompt_manifest": "prompt_manifest.json",
        "warnings": warnings,
        "current_archive_note": (
            "The current /archive-notice page lists 4,325 rows, but its PDF links "
            "return the site's 404 page on the new host. The old-site News.aspx "
            "manifest is the working source."
        ),
    }

    (output_path / "site_map.md").write_text(site_map, encoding="utf-8")
    (output_path / "core_cards.md").write_text(core_cards, encoding="utf-8")
    (output_path / "voice_kb.md").write_text(voice_kb, encoding="utf-8")
    (output_path / "recent_notices.md").write_text(recent_notices, encoding="utf-8")
    (output_path / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_path / "prompt_manifest.json").write_text(
        json.dumps(prompt_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_path / "notices.jsonl").open("w", encoding="utf-8") as handle:
        for notice in non_pii:
            handle.write(json.dumps(notice.to_dict(), ensure_ascii=False) + "\n")

    # The JSONL is the source of truth; the FTS5 store is a rebuildable index
    # beside it. A SQLite build without FTS5 must not fail the whole site build:
    # the router falls back to the title scan.
    try:
        from app.notice_store import build_store

        notice_store_stats = build_store(
            output_path / "notices.jsonl",
            output_path / "notices.sqlite3",
        )
        report["notice_store"] = notice_store_stats.to_dict()
    except Exception as exc:                                    # noqa: BLE001
        report["notice_store"] = {"built": False, "error": str(exc)}
        warnings.append(f"notice FTS5 store not built: {exc}")

    (output_path / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info(
        "built core=%d site=%d recent=%d document=%d voice_total=%d voice_kb=%d",
        core_tokens,
        site_tokens,
        recent_tokens,
        document_tokens,
        voice_total,
        voice_kb_tokens,
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
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="full voice prompt budget (wrapper + document + question + output)",
    )
    parser.add_argument(
        "--max-doc-tokens",
        type=int,
        default=4000,
        help="document-only budget for DOC_PATHS",
    )
    parser.add_argument(
        "--chat-budget",
        type=int,
        default=30000,
        help="full chat prompt budget",
    )
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
            max_doc_tokens=args.max_doc_tokens,
            chat_budget=args.chat_budget,
        )
    except (BuildError, FileNotFoundError, FetchError) as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1

    tokens = report["tokens"]
    print(f"core_cards.md    {tokens['core_cards']:>6} tokens")
    print(f"site_map.md      {tokens['site_map']:>6} tokens")
    print(f"recent_notices.md{tokens['recent_notices']:>6} tokens")
    print(f"document         {tokens['document']:>6} / {tokens['doc_budget']} tokens")
    print(
        f"voice prompt     {tokens['voice_total']:>6} / {tokens['voice_budget']} tokens "
        f"(wrapper {tokens['voice_prompt'] - tokens['document']})"
    )
    print(
        f"chat prompt      {tokens['chat_total']:>6} / {tokens['chat_budget']} tokens "
        f"(wrapper {tokens['chat_prompt'] - tokens['document']})"
    )
    print(f"voice_kb.md      {tokens['voice_kb']:>6} tokens (entity index, not in prompt)")
    print(
        "notices          "
        f"{report['notices']['manifest_rows']} rows, "
        f"{report['notices']['non_pii']} non-PII, "
        f"{report['notices']['pii_excluded']} excluded"
    )
    store = report.get("notice_store") or {}
    if store.get("built"):
        print(
            "notice store     "
            f"{store['rows']} rows, {store['pii_excluded']} PII excluded, "
            f"{store['db_bytes']:,} bytes (FTS5)"
        )
    else:
        print(f"notice store     not built: {store.get('error', 'unknown error')}")
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
