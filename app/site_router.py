"""Deterministic selective context for ``CONTEXT_MODE=site``.

The compact prompt is three files (``core_cards``, ``site_map``,
``recent_notices``). This router keeps the stable ``core_cards`` prefix and
adds only the fact, node or non-PII notices that the turn actually needs:

* fee/programme question -> the exact fee row from ``facts.json``
* about/vision/glance -> the matching extracted page node
* syllabus -> the matching links from the extracted syllabus page
* notices -> the recent tail plus title-matched non-PII notices
* anything unrecognised -> the full compact bundle, so routing never makes
  the prompt narrower than the safe default

There are no embeddings and no network calls. Matching is on whole words
(plus the programme aliases in ``app.programs``), and every selected item
carries its fact/node/notice ID and source URL into the ``ContextBundle``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.context_bundle import ContextBundle
from app.doc_context import document_bundle
from app.programs import DSPMU_PROGRAM_ALIASES, detect_program

logger = logging.getLogger("site_router")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_lock = threading.Lock()
_text_cache: dict[str, tuple[tuple[int, int], str]] = {}
_facts_cache: dict | None = None
_notices_cache: dict | None = None

#: A route may only include these extracted nodes. Department/faculty pages
#: carry staff names and phone numbers; the router does not put them in a
#: prompt. They need a separate PII review before becoming routable.
_SAFE_NODES = frozenset({
    "page-about",
    "page-glance",
    "page-vision",
    "page-syllabus",
})

_URL_RE = re.compile(r"https?://[^\s)>\]]+")

#: Result/merit/selection lists are excluded at build time. Keep a second
#: title guard here so a future ingestion change cannot route one by accident.
_PII_TITLE_RE = re.compile(
    r"\b(?:roll|merit list|selection list|admission list|selected candidates|"
    r"admit card|answer key|results?)\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did can could would should "
    "i me my we our you your it this that these those of to in on at for from "
    "with by and or not no yes what which when where who whom how why about "
    "please tell show give any some latest new news notice notification "
    "information details detail programme program examination exam semester "
    "sem".split()
)

_FEE_WORDS = (
    "fee", "fees", "tuition", "cost", "price", "charge", "charges",
    "शुल्क", "फीस", "ફી", "tasas", "frais", "tasse", "tarifa", "mensalidade",
    "学费", "費用",
)
_DOCUMENT_WORDS = (
    "document", "documents", "checklist", "marksheet", "mark sheet", "aadhaar",
    "aadhar", "certificate", "certificates", "passport", "photograph",
    "photographs", "migration", "character certificate", "dokument",
    "दस्तावेज", "दस्तावेज़", "દસ્તાવેજ", "documentos", "documents",
)
_DATE_WORDS = (
    "date", "dates", "deadline", "last date", "counselling", "counseling",
    "schedule", "calendar", "when", "तारीख", "कब", "समय", "તારીખ", "ક્યારે",
    "fecha", "fechas", "date limite", "data", "scadenza", "日期", "締切",
)
_CONTACT_WORDS = (
    "contact", "phone", "email", "e-mail", "call", "address", "registrar",
    "helpline", "संपर्क", "फोन", "ईमेल", "पता", "સંપર્ક", "ફોન", "ઈમેલ",
    "સરનામું", "teléfono", "correo", "téléphone", "telefono", "電話", "电话",
)
_ABOUT_WORDS = (
    "about", "history", "established", "founded", "university", "dspmu",
    "campus", "vision", "mission", "glance", "students", "departments",
    "इतिहास", "स्थापना", "विश्वविद्यालय", "પરિચય", "ઇતિહાસ", "યુનિવર્સિટી",
    "historia", "université", "università", "大学", "关于", "關於",
)

#: "University", "DSPMU", "campus" and similar words name the institution
#: itself. They must beat a bare subject alias such as "history", which would
#: otherwise turn "Tell me about the university's history" into a BA History
#: fee lookup.
_STRONG_ABOUT_WORDS = (
    "university", "dspmu", "established", "founded", "campus", "vision",
    "mission", "glance", "how many", "history of", "about the university",
    "विश्वविद्यालय", "स्थापना", "યુનિવર્સિટી", "સ્થાપના",
)

_DEPARTMENT_WORDS = (
    "department", "faculty", "faculties", "विभाग", "संकाय", "વિભાગ",
    "département", "departamento",
)

#: A degree marker makes a programme mention specific enough to beat a
#: generic "university" word. "university history" should open the about page;
#: "university BA History" should open the BA History fact.
_DEGREE_MARKERS = (
    "ba", "b.a", "bsc", "b.sc", "bcom", "b.com", "bba", "bca", "bfsc",
    "b.f.sc", "bachelor",
)
_NOTICE_WORDS = (
    "notice", "notices", "notification", "circular", "announcement",
    "convocation", "holiday", "timetable", "exam schedule",
    "examination programme", "admit card", "अधिसूचना", "सूचना", "પરિપત્ર",
    "સૂચના", "aviso", "notification", "annonce", "avviso", "お知らせ", "通知",
)
_SYLLABUS_WORDS = (
    "syllabus", "curriculum", "course structure", "programme structure",
    "पाठ्यक्रम", "સિલેબસ",
)
_ADMISSION_WORDS = (
    "admission", "admissions", "apply", "application", "eligibility",
    "qualification", "प्रवेश", "एडमिशन", "आवेदन", "पात्रता", "પ્રવેશ",
    "દાખલો", "અરજી", "admisión", "admission", "iscrizione", "入学", "招生",
)
_BOUNDARY_WORDS = (
    "merit list", "selection list", "result", "results", "roll number",
    "roll no", "admit card", "answer key", "selected candidates",
    "admission list", "मेरिट लिस्ट", "चयन सूची", "परिणाम", "રોલ નંબર",
    "પરિણામ",
)


def _resolve(path: str) -> Path:
    p = Path(path.strip())
    return p if p.is_absolute() else PROJECT_ROOT / p


def _doc_paths() -> list[Path]:
    raw = (settings.DOC_PATHS or "").strip()
    if not raw:
        return []
    return [_resolve(part) for part in raw.split(",") if part.strip()]


def _read_text(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    key = str(path)
    identity = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _text_cache.get(key)
        if cached and cached[0] == identity:
            return cached[1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with _lock:
        _text_cache[key] = (identity, text)
    return text


def _core_text() -> str:
    """The stable prefix. It is the configured ``core_cards.md`` path."""
    for path in _doc_paths():
        if path.stem == "core_cards":
            return _read_text(path)
    return ""


def _recent_notices_text() -> str:
    for path in _doc_paths():
        if path.stem == "recent_notices":
            return _read_text(path)
    return ""


def _load_json(path: Path) -> dict:
    text = _read_text(path)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        logger.warning("site router: %s is not valid JSON", path)
        return {}
    return data if isinstance(data, dict) else {}


def _facts() -> dict:
    global _facts_cache
    path = _resolve(settings.SITE_FACTS_PATH)
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    identity = (key, stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _facts_cache and _facts_cache.get("identity") == identity:
            return _facts_cache["data"]
    data = _load_json(path)
    with _lock:
        _facts_cache = {"identity": identity, "data": data}
    return data


def _notices() -> list[dict]:
    global _notices_cache
    path = _resolve(settings.SITE_NOTICES_PATH)
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return []
    identity = (key, stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _notices_cache and _notices_cache.get("identity") == identity:
            return _notices_cache["rows"]
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    with _lock:
        _notices_cache = {"identity": identity, "rows": rows}
    return rows


def _node(node_id: str) -> tuple[str, str | None]:
    if node_id not in _SAFE_NODES:
        return "", None
    path = _resolve(settings.SITE_NODES_DIR) / f"{node_id}.md"
    text = _read_text(path)
    if not text:
        return "", None
    match = re.search(r"^Source:\s*(\S+)\s*$", text, re.MULTILINE)
    return text, match.group(1) if match else None


def _urls(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        url.rstrip(".,;:") for url in _URL_RE.findall(text or "")
    ))


def _tokens(text: str) -> tuple[str, ...]:
    # Strip dots before splitting so "Ph.D." and "PhD" both become "phd".
    cleaned = re.sub(
        r"[^a-z0-9ऀ-ॿ]+", " ", (text or "").lower().replace(".", "")
    )
    return tuple(
        token for token in cleaned.split()
        if token and token not in _STOPWORDS
    )


def _has_any(text: str, words: Iterable[str]) -> bool:
    for word in words:
        if re.search(
            rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])",
            text,
            re.IGNORECASE,
        ):
            return True
    return False


def _detected_programme(query: str) -> str:
    # The router is part of the DSPMU profile; use its catalogue even when the
    # test suite has pinned the archived Meridian catalogue for old fixtures.
    return detect_program(query, DSPMU_PROGRAM_ALIASES)


def _fee_key(text: str) -> tuple[str, ...]:
    return tuple(sorted(_tokens(text)))


def _fee_for(programme: str) -> dict | None:
    if not programme:
        return None
    wanted = _fee_key(programme)
    if not wanted:
        return None
    for fee in _facts().get("fees") or []:
        if not isinstance(fee, dict):
            continue
        if _fee_key(str(fee.get("programme") or "")) == wanted:
            return fee
    return None


def _rupees(value: object) -> str:
    try:
        return f"₹{int(value):,}"
    except (TypeError, ValueError):
        return "not published"


def _render_fee(fee: dict) -> str:
    programme = str(fee.get("programme") or "Programme")
    lines = [
        f"## Programme fee fact — {programme}",
        f"- School: {fee.get('school') or 'not published'}",
        (
            "- Annual fee: "
            f"{_rupees(fee.get('annual_fee_gen'))} (GEN); "
            f"{_rupees(fee.get('annual_fee_reserved'))} (ST/SC/BC I & II)"
        ),
        (
            "- Semester course fee: "
            f"{_rupees(fee.get('semester_course_fee_gen'))} (GEN); "
            f"{_rupees(fee.get('semester_course_fee_bc'))} (BC I & II); "
            f"{_rupees(fee.get('semester_course_fee_sc'))} (ST/SC)"
        ),
        f"- Source: {fee.get('source_url') or 'not published'}",
        (
            f"- Valid: {fee.get('valid_from') or '?'} to "
            f"{fee.get('valid_until') or '?'}"
        ),
        "- Status: draft; confirm with the registrar before payment.",
    ]
    return "\n".join(lines)


def _notice_terms(query: str) -> set[str]:
    return set(_tokens(query))


def _matching_notices(query: str) -> list[dict]:
    terms = _notice_terms(query)
    if not terms:
        return []
    scored: list[tuple[int, str, dict]] = []
    for row in _notices():
        title = str(row.get("title") or "")
        if not title or row.get("pii") or _PII_TITLE_RE.search(title):
            continue
        title_terms = set(_tokens(title))
        score = sum(2 for term in terms if term in title_terms)
        score += sum(
            1 for term in terms
            if term not in title_terms
            and len(term) >= 4
            and any(word.startswith(term) for word in title_terms)
        )
        if score:
            scored.append((score, str(row.get("date") or ""), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    limit = max(0, min(20, int(settings.SITE_ROUTER_MAX_NOTICES)))
    return [row for _score, _date, row in scored[:limit]]


def _recent_notice_ids() -> tuple[str, ...]:
    """IDs for the compact recent-notices tail, when the archive has them."""
    recent = _recent_notices_text()
    if not recent:
        return ()
    wanted: set[tuple[str, str]] = set()
    for line in recent.splitlines():
        if not line.startswith("- "):
            continue
        parts = [part.strip() for part in line[2:].split("·")]
        if len(parts) >= 2:
            wanted.add((parts[0], parts[1]))
    if not wanted:
        return ()
    ids: list[str] = []
    for row in _notices():
        key = (str(row.get("date") or ""), str(row.get("title") or ""))
        if key in wanted and row.get("id"):
            ids.append(str(row["id"]))
    return tuple(dict.fromkeys(ids))


def _render_notices(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = ["## Matching non-PII notices"]
    for row in rows:
        date = row.get("date") or row.get("date_text") or ""
        category = row.get("category") or ""
        title = row.get("title") or ""
        url = row.get("url") or ""
        line = f"- {date} — {title}"
        if category:
            line += f" ({category})"
        if url:
            line += f" — {url}"
        lines.append(line)
    return "\n".join(lines)


def _about_node(query: str) -> str:
    if _has_any(query, ("vision", "mission", "दृष्टि", "लक्ष्य", "દ્રષ્ટિ")):
        return "page-vision"
    if _has_any(
        query,
        (
            "glance", "how many departments", "departments", "programmes",
            "programs", "कितने विभाग", "ગણતરી",
        ),
    ):
        return "page-glance"
    return "page-about"


def _syllabus_text(query: str, programme: str) -> str:
    node, _source = _node("page-syllabus")
    if not node:
        return ""
    fee = _fee_for(programme)
    subject = str(fee.get("subject") or "") if fee else ""
    terms = {term for term in _tokens(subject) if len(term) >= 3} if subject else set()
    if terms:
        lines = [
            line for line in node.splitlines()
            if any(term in line.lower() for term in terms)
        ]
        if lines:
            return "## Syllabus links\n" + "\n".join(lines[:20])
    # No programme matched: keep the heading and the first download links,
    # never the whole page.
    lines = node.splitlines()
    return "\n".join(lines[:30])


def _base_bundle() -> ContextBundle | None:
    core = _core_text()
    if not core.strip():
        return None
    facts = _facts()
    return ContextBundle(
        text=core,
        node_ids=("core_cards",),
        source_urls=_urls(core),
        valid_until=str(facts.get("valid_until") or "") or None,
    )


def route_query(query: str = "") -> ContextBundle:
    """Return the selective bundle for *query*.

    A recognised route always starts with the stable core cards. An
    unrecognised query returns the full configured bundle so the router can
    only narrow what it is confident about.
    """
    text = (query or "").strip().lower()
    base = _base_bundle()
    if base is None:
        return document_bundle()

    programme = _detected_programme(text)

    # A result/merit-list question is a boundary, not a notice lookup.
    if _has_any(text, _BOUNDARY_WORDS):
        logger.info("site router: boundary")
        return base

    if _has_any(text, _NOTICE_WORDS):
        recent = _recent_notices_text()
        recent_ids = _recent_notice_ids()
        recent_set = set(recent_ids)
        rows = [
            row for row in _matching_notices(text)
            if str(row.get("id") or "") not in recent_set
        ]
        parts = [base.text]
        if recent:
            parts.append(recent)
        rendered = _render_notices(rows)
        if rendered:
            parts.append(rendered)
        notice_ids = tuple(dict.fromkeys(
            (*recent_ids, *(str(row.get("id")) for row in rows if row.get("id")))
        ))
        urls = base.source_urls + tuple(
            str(row.get("url")) for row in rows if row.get("url")
        )
        logger.info("site router: notices (%d matched)", len(rows))
        return ContextBundle(
            text="\n\n".join(part for part in parts if part.strip()),
            fact_ids=base.fact_ids,
            node_ids=("core_cards", "recent_notices"),
            notice_ids=notice_ids,
            source_urls=tuple(dict.fromkeys(urls)),
            valid_until=base.valid_until,
        )

    if _has_any(text, _FEE_WORDS):
        fee = _fee_for(programme)
        if fee is None:
            logger.info("site router: fees (core)")
            return base
        rendered = _render_fee(fee)
        fact_id = str(fee.get("fact_id") or "")
        logger.info("site router: fee fact")
        return ContextBundle(
            text=f"{base.text}\n\n{rendered}",
            fact_ids=(fact_id,) if fact_id else (),
            node_ids=base.node_ids,
            source_urls=tuple(dict.fromkeys(
                (*base.source_urls, str(fee.get("source_url") or ""))
            )),
            valid_until=str(fee.get("valid_until") or "") or base.valid_until,
        )

    if _has_any(text, _DOCUMENT_WORDS):
        logger.info("site router: documents (core)")
        return base
    if _has_any(text, _DATE_WORDS):
        logger.info("site router: dates (core)")
        return base
    if _has_any(text, _CONTACT_WORDS):
        logger.info("site router: contact (core)")
        return base
    if _has_any(text, _SYLLABUS_WORDS):
        rendered = _syllabus_text(text, programme)
        if rendered:
            logger.info("site router: syllabus")
            return ContextBundle(
                text=f"{base.text}\n\n{rendered}",
                fact_ids=base.fact_ids,
                node_ids=("core_cards", "page-syllabus"),
                source_urls=tuple(dict.fromkeys(
                    (*base.source_urls, "https://dspmuranchi.ac.in/syllabus")
                )),
                valid_until=base.valid_until,
            )
    if _has_any(text, _ADMISSION_WORDS):
        logger.info("site router: admissions (core)")
        return base
    if (
        _has_any(text, _STRONG_ABOUT_WORDS)
        and not (programme and _has_any(text, _DEGREE_MARKERS))
    ):
        node_id = _about_node(text)
        node, source = _node(node_id)
        if node:
            logger.info("site router: %s", node_id)
            return ContextBundle(
                text=f"{base.text}\n\n{node.strip()}",
                fact_ids=base.fact_ids,
                node_ids=("core_cards", node_id),
                source_urls=tuple(dict.fromkeys(
                    (*base.source_urls, *((source,) if source else ()))
                )),
                valid_until=base.valid_until,
            )
    if _has_any(text, _DEPARTMENT_WORDS):
        logger.info("site router: department fallback")
        return document_bundle()

    if programme:
        fee = _fee_for(programme)
        if fee is not None:
            rendered = _render_fee(fee)
            fact_id = str(fee.get("fact_id") or "")
            logger.info("site router: programme fact")
            return ContextBundle(
                text=f"{base.text}\n\n{rendered}",
                fact_ids=(fact_id,) if fact_id else (),
                node_ids=base.node_ids,
                source_urls=tuple(dict.fromkeys(
                    (*base.source_urls, str(fee.get("source_url") or ""))
                )),
                valid_until=str(fee.get("valid_until") or "") or base.valid_until,
            )


    if _has_any(text, _ABOUT_WORDS):
        node_id = _about_node(text)
        node, source = _node(node_id)
        if node:
            logger.info("site router: %s", node_id)
            return ContextBundle(
                text=f"{base.text}\n\n{node.strip()}",
                fact_ids=base.fact_ids,
                node_ids=("core_cards", node_id),
                source_urls=tuple(dict.fromkeys(
                    (*base.source_urls, *((source,) if source else ()))
                )),
                valid_until=base.valid_until,
            )

    logger.info("site router: full fallback")
    return document_bundle()


def clear_cache() -> None:
    """Tests and the benchmark tool use this after changing router inputs."""
    global _facts_cache, _notices_cache
    with _lock:
        _text_cache.clear()
        _facts_cache = None
        _notices_cache = None
