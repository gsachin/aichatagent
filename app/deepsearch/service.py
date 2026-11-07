"""Bounded live-site search used only after an eligible refusal.

The service never runs from the voice path. It fetches the official search
page, follows a small number of result URLs, extracts text/tables and returns
``site.open``-shaped evidence that the normal answer/verifier pipeline can
consume. It does not bypass PII or prompt-injection rules.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from app.pii import scan_text
from app.sitegraph.extract import extract_html
from app.sitegraph.policy import title_pii_class
from app.siteindex.extract import extract_pdf

if TYPE_CHECKING:
    from app.agent.models import Plan

logger = logging.getLogger("deepsearch")

_STUDENT_TOKENS = frozenset({
    "result", "results", "merit", "selection", "selected", "waiting",
    "answer", "admit", "roll", "registration", "topper", "toppers",
})
_IMAGE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)
_WORD_RE = re.compile(r"[a-z0-9]+")

_DEEP_SEARCH_MESSAGES = {
    "en": "I checked the official university website for the latest details. ",
    "hi": "मैंने विश्वविद्यालय की आधिकारिक वेबसाइट पर नवीनतम जानकारी जाँची। ",
    "gu": "મેં યુનિવર્સિટીની અધિકૃત વેબસાઇટ પર નવીનતમ માહિતી તપાસી. ",
}


def deep_search_message(language: str | None) -> str:
    """Localized note for a completed deep search."""
    code = (language or "en").strip().lower().split("-", 1)[0]
    return _DEEP_SEARCH_MESSAGES.get(code, _DEEP_SEARCH_MESSAGES["en"])


@dataclass(frozen=True)
class DeepSearchOptions:
    base_url: str = "https://dspmuranchi.ac.in"
    allowed_hosts: tuple[str, ...] = (
        "dspmuranchi.ac.in",
        "www.dspmuranchi.ac.in",
    )
    max_results: int = 5
    max_fetches: int = 3
    max_bytes: int = 20_000_000
    max_pages: int = 40
    timeout_s: int = 20
    pii_guard: bool = True
    ocr: bool = False


@dataclass
class DeepSearchResult:
    query: str
    status: str  # ok | no_results | error
    urls: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched: int = 0
    message: str = ""


def _blocked(url: str, title: str, options: DeepSearchOptions) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return True
    if (parsed.hostname or "").lower() not in options.allowed_hosts:
        return True
    if parsed.path.rstrip("/").lower() == "/search":
        # Facet links ("All 2", "Faculty 2") point back to the search page.
        return True
    if parsed.path.lower().endswith(_IMAGE_SUFFIXES):
        return True
    if options.pii_guard and title_pii_class(title) is not None:
        return True
    words = set(_WORD_RE.findall((url or "").lower()))
    if options.pii_guard and words & _STUDENT_TOKENS:
        return True
    return False


def _query_tokens(query: str) -> set[str]:
    return set(_WORD_RE.findall((query or "").lower()))


def _search_urls(
    query: str,
    fetcher,
    options: DeepSearchOptions,
) -> list[tuple[str, str]]:
    search_url = (
        f"{options.base_url.rstrip('/')}/search?q={quote_plus(query)}"
    )
    try:
        response = fetcher.fetch(search_url)
    except Exception:  # noqa: BLE001 - deep search is best effort
        logger.exception("site search fetch failed")
        return []
    if getattr(response, "status", 0) != 200:
        return []
    html = getattr(response, "text", "") or ""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    tokens = _query_tokens(query)
    found: dict[str, tuple[float, str]] = {}

    # The site's search page renders a ranked list of result cards. Parse the
    # cards instead of every anchor on the page: the page's navigation and
    # mega-menu contain hundreds of links, and scanning them made a generic
    # About/Contact link outrank the actual search result.
    cards = soup.select(".search-result-card")
    for position, card in enumerate(cards):
        link = card.select_one(".result-title a[href]")
        if link is None:
            link = card.find("a", href=True)
        if link is None:
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(
            getattr(response, "final_url", "") or options.base_url,
            href,
        ).split("#", 1)[0]
        title = " ".join(link.get_text(" ").split()) or url
        if len(title) < 4 or _blocked(url, title, options):
            continue
        card_text = " ".join(card.get_text(" ").split())
        lower = f"{title} {card_text} {url}".lower()
        overlap = sum(1 for token in tokens if token in lower)
        # Preserve the site's own ranking: earlier cards dominate, with a
        # small topical bonus for exact query terms.
        score = float((len(cards) - position) * 10 + overlap)
        previous = found.get(url)
        if previous is None or score > previous[0]:
            found[url] = (score, title)

    if not found:
        # Older site templates (or a future redesign) may not use the card
        # markup. Keep the anchor scan as a fallback.
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            url = urljoin(
                getattr(response, "final_url", "") or options.base_url,
                href,
            ).split("#", 1)[0]
            title = " ".join(anchor.get_text(" ").split()) or url
            if len(title) < 4 or _blocked(url, title, options):
                continue
            lower = f"{title} {url}".lower()
            overlap = sum(1 for token in tokens if token in lower)
            score = float(overlap)
            if any(part in url for part in ("/uploads/", "/cms/", "/page/", "/news/")):
                score += 1.0
            if url.lower().endswith(".pdf"):
                score += 0.5
            previous = found.get(url)
            if previous is None or score > previous[0]:
                found[url] = (score, title)

    ordered = sorted(
        found.items(), key=lambda item: (-item[1][0], item[0])
    )
    return [(url, title) for url, (_score, title) in ordered][
        : max(1, int(options.max_results))
    ]


def _node_id(url: str) -> str:
    return "ondemand." + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _fetch_one(
    url: str,
    title: str,
    fetcher,
    options: DeepSearchOptions,
) -> tuple[dict | None, str]:
    try:
        if url.lower().endswith(".pdf"):
            response = fetcher.fetch_document(
                url, max_bytes=options.max_bytes
            )
            body = bytes(getattr(response, "body", b"") or b"")
            if b"%PDF-" not in body[:1024]:
                return None, f"not_pdf:{url}"
            extracted = extract_pdf(
                body,
                ocr=options.ocr,
                max_pages=options.max_pages,
            )
            text = (extracted.text or "").strip()
            tables = extracted.tables
            authority = "official_document"
        else:
            response = fetcher.fetch(url)
            if getattr(response, "status", 0) != 200:
                return None, f"http_{getattr(response, 'status', 0)}:{url}"
            extracted = extract_html(
                getattr(response, "text", "") or "",
                getattr(response, "final_url", "") or url,
            )
            text = (extracted.text or "").strip()
            tables = extracted.tables
            authority = "official_page"
    except Exception as exc:  # noqa: BLE001
        logger.exception("deep-search fetch failed for %s", url)
        return None, f"{type(exc).__name__}:{url}"

    if options.pii_guard and scan_text(text).flagged:
        return None, f"pii_blocked:{url}"

    node = {
        "node_id": _node_id(url),
        "title": title or url,
        "url": url,
        "status": "ok",
        "authority": authority,
        "content_type": "pdf" if url.lower().endswith(".pdf") else "html",
    }

    if len(text) < 80:
        # The page exists and is the site's own match, but its content is an
        # image/JS shell (or a scanned PDF). Return it as a navigation/hand-off
        # result instead of discarding it and letting a later, unrelated page
        # become the answer.
        return {
            "tool": "site.open",
            "status": "ok",
            "data": {
                "node": node,
                "sections": [],
                "tables": [],
                "body_available": False,
                "content_status": "empty_shell",
                "handoff": True,
            },
        }, f"empty:{url}"

    return {
        "tool": "site.open",
        "status": "ok",
        "data": {
            "node": node,
            "sections": [{"heading": title or url, "text": text}],
            "tables": list(tables) if tables else [],
            "body_available": True,
            "content_status": "indexed",
        },
    }, ""


def deep_search(
    query: str,
    fetcher,
    *,
    options: DeepSearchOptions | None = None,
) -> DeepSearchResult:
    options = options or DeepSearchOptions()
    result = DeepSearchResult(query=query, status="no_results")
    started = time.monotonic()
    urls = _search_urls(query, fetcher, options)
    result.urls = [url for url, _title in urls]
    if not urls:
        result.message = "site_search_no_results"
        return result

    for url, title in urls[: max(1, int(options.max_fetches))]:
        if time.monotonic() - started > max(1, int(options.timeout_s)):
            result.errors.append("timeout")
            break
        fetched, error = _fetch_one(url, title, fetcher, options)
        result.fetched += 1
        if fetched is not None:
            result.results.append(fetched)
        if error:
            result.errors.append(error)

    result.status = "ok" if result.results else "error"
    result.message = "deep_search_ok" if result.results else "deep_search_failed"
    return result


def results_to_execution(
    result: DeepSearchResult,
    plan: "Plan",
):
    # Lazy import: app.agent's package __init__ imports app.agent.text,
    # which imports this module. Keeping the import here avoids a cycle.
    from app.agent.executor import ExecutionResult

    return ExecutionResult(
        plan=plan,
        results=list(result.results),
        status="ok" if result.results else "degraded",
        steps_used=len(result.results),
        errors=[{"tool": "deep_search", "status": "error", "error": error}
                for error in result.errors],
    )


__all__ = [
    "DeepSearchOptions",
    "DeepSearchResult",
    "deep_search",
    "deep_search_message",
    "results_to_execution",
]
