"""Request-time retrieval from the public DSPMU website.

The static prompt corpus under ``content/dspmu`` is a build-time snapshot.
This module adds a bounded live path for questions that snapshot does not
cover: it resolves the question against the site's sitemap, fetches at most a
couple of HTML pages, extracts their main text, and returns a context block
with the source URL and fetch time.

Design constraints, all deliberate:

* allowlisted hosts only, robots.txt checked, one request at a time;
* short timeouts and no retries -- a live question must not stall the turn;
* the site's soft-404 (HTTP 200 plus "page not found") is rejected;
* result, merit, selection and staff-directory pages are never fetched;
* the sitemap and pages are cached under a TTL, so repeats are free;
* no PDFs: the current host serves ``/pdf/`` links as HTML soft-404s and PDF
  extraction is too slow for the request path. Curated PDF facts stay in
  ``facts.json``.

The answer pipeline calls :func:`retrieve_live_context`. The class is
injectable so tests run against fixture HTML with no network.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from app.notice_store import is_likely_pii_title

logger = logging.getLogger("live_site")

#: Build-time seed list, used only when the sitemap cannot be read.
_SEED_PATHS = (
    "/",
    "/syllabus",
    "/news-and-announcements",
    "/examination-notifications",
    "/contact-us",
    "/cms/about-the-university",
    "/cms/fee-structure",
    "/cms/admissions",
    "/cms/academic-programmes",
    "/cms/examination-notifications",
    "/cms/examination-calendar",
    "/cms/scheme-of-examination-nep-2020-",
    "/cms/holiday-list",
    "/cms/hostel",
    "/cms/central-library",
    "/cms/digital-library",
    "/cms/facilities",
    "/cms/scholarships-student-freeship",
    "/cms/training-placement",
    "/cms/placement-cell",
    "/cms/grievance-redressal-rti",
    "/cms/vision-mission",
    "/cms/history-legacy",
    "/cms/university-at-a-glance",
    "/cms/campus",
    "/cms/convocation",
    "/cms/transcript",
    "/cms/migration-clc",
    "/cms/bonafide",
    "/cms/prospectus",
    "/cms/academic-calendar",
    "/cms/ph.d.-guidelines",
    "/cms/ph.d.-regulations",
)

#: Words that carry no URL signal. Kept small on purpose: a topical word such
#: as "fee" or "hostel" must survive.
_QUERY_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did can could would should "
    "i me my we our you your it this that these those of to in on at for from "
    "with by and or not no yes what which when where who whom how why about "
    "please tell show give any some latest new information details detail "
    "university dspmu ranchi jharkhand official website page site cms online "
    "current available need want know".split()
)

#: Hindi/Gujarati question words mapped to the English slug vocabulary. The
#: site's URLs are English, so without this a Devanagari question scores zero
#: against every sitemap entry.
_ALIASES: dict[str, str] = {
    # Hindi
    "फीस": "fee", "शुल्क": "fee", "पाठ्यक्रम": "syllabus", "सिलेबस": "syllabus",
    "प्रवेश": "admission", "एडमिशन": "admission", "संपर्क": "contact",
    "फोन": "contact", "ईमेल": "contact", "पता": "contact", "छात्रावास": "hostel",
    "पुस्तकालय": "library", "परीक्षा": "examination", "परिणाम": "result",
    "अवकाश": "holiday", "छुट्टी": "holiday", "छात्रवृत्ति": "scholarship",
    "प्लेसमेंट": "placement", "नौकरी": "career", "सुविधा": "facilities",
    "कैंपस": "campus", "परिसर": "campus", "इतिहास": "history",
    "दृष्टि": "vision", "लक्ष्य": "mission", "संकाय": "faculty",
    "विभाग": "department", "खेल": "sports", "पुस्तक": "library",
    # Gujarati
    "ફી": "fee", "શુલ્ક": "fee", "અભ્યાસક્રમ": "syllabus", "પ્રવેશ": "admission",
    "સંપર્ક": "contact", "ફોન": "contact", "ઈમેલ": "contact", "સરનામું": "contact",
    "છાત્રાલય": "hostel", "ગ્રંથાલય": "library", "પરીક્ષા": "examination",
    "પરિણામ": "result", "રજા": "holiday", "શિષ્યવૃત્તિ": "scholarship",
    "કારકિર્દી": "career", "રમત": "sports", "સુવિધા": "facilities",
    "કેમ્પસ": "campus", "ઇતિહાસ": "history", "દ્રષ્ટિ": "vision",
    "વિભાગ": "department", "ફેકલ્ટી": "faculty",
}
_ALIAS_KEYS = tuple(sorted(_ALIASES, key=len, reverse=True))

#: Topical hints that bridge a question word to the slug the site actually
#: uses. The URL token match handles most queries; hints cover synonyms
#: ("holiday" -> holiday-list) and dotted slugs ("phd" -> ph.d.-guidelines).
_HINTS: dict[str, tuple[str, ...]] = {
    "fee": ("fee-structure",),
    "fees": ("fee-structure",),
    "admission": ("admissions", "academic-programmes", "prospectus"),
    "admissions": ("admissions", "academic-programmes", "prospectus"),
    "syllabus": ("syllabus",),
    "exam": (
        "examination-notifications", "examination-calendar",
        "scheme-of-examination", "online-examination",
    ),
    "examination": (
        "examination-notifications", "examination-calendar",
        "scheme-of-examination", "online-examination",
    ),
    "notice": ("news-and-announcements", "examination-notifications"),
    "notification": ("news-and-announcements", "examination-notifications"),
    "holiday": ("holiday-list", "news-and-announcements"),
    "hostel": ("hostel",),
    "library": ("central-library", "digital-library"),
    "scholarship": ("scholarships-student-freeship",),
    "placement": (
        "training-placement", "placement-cell", "placement-statistics",
        "recruiters", "placement-partners",
    ),
    "career": ("career-counseling-cell", "career-guidance", "training-placement"),
    "contact": ("contact-us",),
    "grievance": ("grievance-redressal-rti",),
    "vision": ("vision-mission",),
    "mission": ("vision-mission",),
    "history": ("history-legacy",),
    "glance": ("university-at-a-glance",),
    "campus": ("campus", "campus-map", "virtual-tour"),
    "facilities": ("facilities",),
    "sports": ("sports", "sports-complex", "sports-control-board"),
    "iqac": ("iqac", "about-iqac", "aqar", "ssr"),
    "naac": ("naac", "accreditation-rankings", "aqar"),
    "nirf": ("nirf",),
    "convocation": (
        "convocation", "1st-convocation-graduation-ceremony",
        "2nd-convocation", "3rd-convocation",
    ),
    "transcript": ("transcript",),
    "migration": ("migration-clc",),
    "bonafide": ("bonafide",),
    "prospectus": ("prospectus",),
    "calendar": ("academic-calendar", "examination-calendar"),
    "research": ("research", "research-innovation"),
    "phd": ("ph.d.-guidelines", "ph.d.-regulations"),
    "ph": ("ph.d.-guidelines", "ph.d.-regulations"),
    "canteen": ("canteen",),
    "bank": ("bank-payment-gateway",),
    "wifi": ("wi-fi-campus",),
    "laboratory": ("laboratories",),
    "laboratories": ("laboratories",),
    "auditorium": ("auditorium",),
    "guest": ("guest-house",),
    "medical": ("medical-unit", "health-care-centre"),
    "health": ("medical-unit", "health-care-centre"),
    "council": ("academic-council",),
    "syndicate": ("syndicate",),
    "senate": ("senate",),
    "registrar": ("registrar",),
    "chancellor": ("chancellor",),
    "vice": ("vice-chancellor", "bio-sketch-of-vc", "former-vice-chancellors"),
}

#: Path tokens that never enter the prompt. These are the project's PII
#: boundary (result/merit/admission lists) plus staff directories, which the
#: static router also keeps out until a separate review approves them.
_DEFAULT_BLOCKED_TOKENS = frozenset({
    "result", "results", "merit", "selection", "selected", "waiting",
    "answer", "admit", "archive", "awarded", "departments", "faculties",
    "staff", "faculty", "teaching", "nonteaching",
})

_SKIP_TAGS = ("script", "style", "noscript", "svg", "canvas", "form", "iframe",
              "nav", "footer", "header")
_PATH_TOKEN_RE = re.compile(r"[a-z0-9]+")
_QUERY_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ॿ઀-૿]{3,}")
_CF_EMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
_TITLE_SPLIT_RE = re.compile(r"\s+[|·—]\s+")


class LiveFetchError(RuntimeError):
    """A live source could not be read, or failed a content check."""


@dataclass(frozen=True)
class LiveSiteConfig:
    base_url: str
    sitemap_url: str
    allowed_hosts: tuple[str, ...]
    timeout_s: float = 6.0
    delay_s: float = 0.25
    max_pages: int = 2
    max_chars: int = 3000
    cache_ttl_s: float = 600.0
    sitemap_ttl_s: float = 3600.0
    user_agent: str = (
        "DSPMU-Admissions-Assistant/1.0 "
        "(+https://dspmuranchi.ac.in; contact: registrar@dspmuranchi.ac.in)"
    )
    blocked_tokens: tuple[str, ...] = tuple(sorted(_DEFAULT_BLOCKED_TOKENS))
    respect_robots: bool = True


@dataclass(frozen=True)
class LivePage:
    url: str
    title: str
    text: str
    fetched_at: str


@dataclass(frozen=True)
class LiveSiteResult:
    text: str
    urls: tuple[str, ...]
    titles: tuple[str, ...]
    fetched_at: str


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _tokenize_query(query: str) -> tuple[str, ...]:
    """Content tokens with Hindi/Gujarati words mapped to English slugs."""
    text = (query or "").lower()
    for source in _ALIAS_KEYS:
        if source in text:
            text = text.replace(source, f" {_ALIASES[source]} ")
    tokens = [
        token for token in _QUERY_TOKEN_RE.findall(text)
        if token not in _QUERY_STOPWORDS
    ]
    return tuple(dict.fromkeys(tokens))


def _hint_slugs(tokens: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for token in tokens:
        out.extend(_HINTS.get(token, ()))
    return tuple(dict.fromkeys(out))


def _parse_sitemap(xml_text: str) -> list[str]:
    """URLs from a sitemap document, without pulling in the build package."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    for node in root.findall("s:url", namespace):
        loc = node.find("s:loc", namespace)
        if loc is not None and (loc.text or "").strip():
            urls.append(loc.text.strip())
    return urls


def _decode_cloudflare_emails(html: str) -> tuple[str, ...]:
    out: list[str] = []
    for value in _CF_EMAIL_RE.findall(html or ""):
        try:
            key = int(value[:2], 16)
            decoded = "".join(
                chr(int(value[index:index + 2], 16) ^ key)
                for index in range(2, len(value), 2)
            )
        except (ValueError, IndexError):
            continue
        if decoded and decoded not in out:
            out.append(decoded)
    return tuple(out)


def _is_soft_404(html: str, content_type: str) -> bool:
    """The DSPMU host answers missing paths with HTTP 200 and its 404 page."""
    if html.lstrip().startswith("%PDF-"):
        return False
    if "html" not in (content_type or "").lower() and content_type:
        return False
    head = (html or "")[:12000].lower()
    if "404 - page not found" in head:
        return True
    return "page not found" in head and "go back home" in head


def _clean_text(text: str) -> str:
    text = unescape(text or "").replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    kept: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank and kept:
                kept.append("")
            blank = True
            continue
        blank = False
        kept.append(line)
    return "\n".join(kept).strip()


def extract_live_page(html: str, url: str) -> tuple[str, str, tuple[str, ...]]:
    """Return ``(title, text, emails)`` for one fetched HTML page.

    Uses ``main``/``article`` when the CMS provides it and falls back to the
    body. Links, tables and headings become plain text: the model only needs
    the content, and Markdown here would spend tokens on syntax.
    """
    soup = BeautifulSoup(html or "", "lxml")
    heading = soup.find("h1") or soup.title
    title = heading.get_text(" ", strip=True) if heading else ""
    title = _TITLE_SPLIT_RE.split(title)[0].strip()[:160]

    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        return title, "", ()
    for tag in main.find_all(list(_SKIP_TAGS)):
        tag.decompose()

    text = _clean_text(main.get_text("\n", strip=True))
    emails = list(_decode_cloudflare_emails(html))
    # The live page may render the address behind Cloudflare's placeholder.
    text = text.replace("[email&#160;protected]", "").replace(
        "[email protected]", ""
    )
    for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text):
        if email not in emails:
            emails.append(email)
    return title, text, tuple(emails)


class LiveSite:
    """A rate-limited, cached reader for one allowlisted public site."""

    def __init__(self, config: LiveSiteConfig, session: requests.Session | None = None,
                 clock=time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.7",
        })
        self._lock = threading.Lock()
        self._delay_lock = threading.Lock()
        self._last_request = 0.0
        self._sitemap: tuple[float, tuple[str, ...]] | None = None
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._pages: dict[str, tuple[float, LivePage]] = {}

    # ── public API ──────────────────────────────────────────────────

    def retrieve(self, query: str) -> LiveSiteResult | None:
        """Fetch the pages that best match *query*, or ``None``."""
        tokens = _tokenize_query(query)
        if not tokens:
            return None
        hints = _hint_slugs(tokens)
        candidates = self._candidates(tokens, hints)
        if not candidates:
            logger.info("live site: no URL matched %r", query)
            return None

        pages: list[LivePage] = []
        remaining = max(400, int(self.config.max_chars))
        for url in candidates[: max(1, int(self.config.max_pages))]:
            page = self._page(url)
            if page is None:
                continue
            pages.append(page)
            remaining -= len(page.text)
            if remaining <= 400:
                break
        if not pages:
            return None

        fetched_at = pages[0].fetched_at
        budget = max(400, int(self.config.max_chars))
        blocks: list[str] = [
            "## Live university website (fetched at request time)",
            (
                "The text below was fetched from the public university website "
                "for this question. Use it for details not covered by the "
                "static profile; cite the Source URL. For fee amounts and "
                "admission dates, the verified profile above remains the "
                "authoritative source."
            ),
        ]
        used = 0
        for page in pages:
            room = budget - used
            if room <= 0:
                break
            body = page.text[:room]
            used += len(body)
            block = [f"### {page.title or 'DSPMU page'}", f"Source: {page.url}"]
            block.append(body)
            blocks.append("\n".join(block))
        return LiveSiteResult(
            text="\n\n".join(blocks),
            urls=tuple(page.url for page in pages),
            titles=tuple(page.title for page in pages),
            fetched_at=fetched_at,
        )

    def clear_cache(self) -> None:
        with self._lock:
            self._sitemap = None
            self._robots.clear()
            self._pages.clear()

    # ── selection ───────────────────────────────────────────────────

    def _candidates(self, tokens: tuple[str, ...], hints: tuple[str, ...]) -> list[str]:
        urls = self._sitemap_urls()
        blocked = set(self.config.blocked_tokens)
        scored: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc not in self.config.allowed_hosts:
                continue
            path = parsed.path.rstrip("/") or "/"
            if path.rsplit("/", 1)[-1].lower().endswith(
                (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".doc", ".docx")
            ):
                continue
            path_tokens = set(_PATH_TOKEN_RE.findall(path.lower()))
            if path_tokens & blocked:
                continue
            score = 0
            for token in tokens:
                if token in path_tokens:
                    score += 3
                elif len(token) >= 4 and any(
                    len(other) >= 4
                    and (other.startswith(token) or token.startswith(other))
                    for other in path_tokens
                ):
                    score += 1
            for hint in hints:
                if hint in path.lower():
                    score += 3
            if score >= 3:
                scored.append((-score, len(path), url))
        scored.sort()
        return [url for _score, _length, url in scored]

    def _sitemap_urls(self) -> tuple[str, ...]:
        now = self._clock()
        with self._lock:
            cached = self._sitemap
        if cached is not None and now < cached[0]:
            return cached[1]
        urls: tuple[str, ...] = ()
        try:
            response = self._get(self.config.sitemap_url, accept="application/xml")
            if response.status_code < 400:
                urls = tuple(_parse_sitemap(response.text))
        except Exception as exc:  # noqa: BLE001 - cache/seed fallback is the point
            logger.warning("live site: sitemap unavailable (%s)", exc)
        if not urls:
            urls = tuple(
                urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
                for path in _SEED_PATHS
            )
        with self._lock:
            self._sitemap = (now + max(60.0, self.config.sitemap_ttl_s), urls)
        return urls

    # ── fetching ────────────────────────────────────────────────────

    def _page(self, url: str) -> LivePage | None:
        now = self._clock()
        with self._lock:
            cached = self._pages.get(url)
        if cached is not None and now < cached[0]:
            return cached[1]

        page: LivePage | None = None
        try:
            if not self._robots_allowed(url):
                logger.info("live site: robots.txt disallows %s", url)
                return None
            response = self._get(url)
            if response.status_code >= 400:
                raise LiveFetchError(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "")
            html = response.text
            final_url = getattr(response, "url", url) or url
            if _is_soft_404(html, content_type):
                raise LiveFetchError("soft-404 page")
            title, text, emails = extract_live_page(html, final_url)
            if is_likely_pii_title(title):
                logger.info("live site: refusing PII-titled page %s", final_url)
                return None
            if len(text) < 120:
                raise LiveFetchError(f"empty shell ({len(text)} chars)")
            if emails:
                text = f"{text}\n\nContact: {'; '.join(emails[:3])}"
            page = LivePage(
                url=final_url,
                title=title,
                text=text,
                fetched_at=_now_utc(),
            )
        except Exception as exc:  # noqa: BLE001 - one page must not drop the turn
            logger.info("live site: %s unavailable (%s)", url, exc)
            if cached is not None:
                logger.info("live site: using stale cache for %s", url)
                return cached[1]
            return None

        with self._lock:
            self._pages[url] = (now + max(30.0, self.config.cache_ttl_s), page)
        return page

    def _get(self, url: str, accept: str = "text/html"):
        self._respect_delay()
        response = self._session.get(
            url,
            timeout=(3.0, max(1.0, float(self.config.timeout_s))),
            allow_redirects=True,
            headers={"Accept": accept},
        )
        return response

    def _respect_delay(self) -> None:
        delay = max(0.0, float(self.config.delay_s))
        if delay == 0:
            return
        with self._delay_lock:
            wait = delay - (self._clock() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = self._clock()

    def _robots_allowed(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = self._clock()
        with self._lock:
            cached = self._robots.get(origin)
        if cached is not None and now < cached[0]:
            parser = cached[1]
            return parser is None or parser.can_fetch(self.config.user_agent, url)

        parser: RobotFileParser | None = None
        try:
            response = self._session.get(
                origin + "/robots.txt",
                timeout=(3.0, max(1.0, float(self.config.timeout_s))),
                allow_redirects=True,
                headers={"Accept": "text/plain,*/*;q=0.8"},
            )
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code == 200 and "html" not in content_type:
                parser = RobotFileParser()
                parser.set_url(origin + "/robots.txt")
                parser.parse(response.text.splitlines())
            else:
                # A missing robots.txt is not a prohibition.
                parser = None
        except Exception as exc:  # noqa: BLE001 - unavailable robots != deny
            logger.warning("live site: robots.txt unavailable for %s (%s)", origin, exc)
            parser = None
        with self._lock:
            self._robots[origin] = (
                now + max(60.0, self.config.sitemap_ttl_s), parser
            )
        return parser is None or parser.can_fetch(self.config.user_agent, url)


# ── pipeline entry point ────────────────────────────────────────────

def _config_from_settings() -> LiveSiteConfig:
    from app.config import settings

    base = (settings.LIVE_SITE_BASE_URL or "https://dspmuranchi.ac.in").rstrip("/")
    sitemap = settings.LIVE_SITE_SITEMAP_URL.strip() or f"{base}/sitemap.xml"
    hosts = tuple(
        host.strip().lower()
        for host in (settings.LIVE_SITE_ALLOWED_HOSTS or "").split(",")
        if host.strip()
    ) or (urlparse(base).netloc.lower(),)
    blocked = tuple(
        token.strip().lower()
        for token in (settings.LIVE_SITE_BLOCKED_PATH_TOKENS or "").split(",")
        if token.strip()
    ) or tuple(sorted(_DEFAULT_BLOCKED_TOKENS))
    return LiveSiteConfig(
        base_url=base,
        sitemap_url=sitemap,
        allowed_hosts=hosts,
        timeout_s=settings.LIVE_SITE_TIMEOUT_S,
        delay_s=settings.LIVE_SITE_DELAY_S,
        max_pages=settings.LIVE_SITE_MAX_PAGES,
        max_chars=settings.LIVE_SITE_MAX_CHARS,
        cache_ttl_s=settings.LIVE_SITE_CACHE_TTL_S,
        sitemap_ttl_s=settings.LIVE_SITE_SITEMAP_TTL_S,
        user_agent=settings.LIVE_SITE_USER_AGENT,
        blocked_tokens=blocked,
        respect_robots=settings.LIVE_SITE_RESPECT_ROBOTS,
    )


_default_lock = threading.Lock()
_default: tuple[LiveSiteConfig, LiveSite] | None = None


def default_retriever() -> LiveSite:
    """The process-wide retriever, rebuilt when the configuration changes."""
    global _default
    config = _config_from_settings()
    with _default_lock:
        if _default is None or _default[0] != config:
            _default = (config, LiveSite(config))
        return _default[1]


def clear_cache() -> None:
    """Drop the default retriever's sitemap and page caches (tests)."""
    global _default
    with _default_lock:
        if _default is not None:
            _default[1].clear_cache()


def retrieve_live_context(query: str) -> LiveSiteResult | None:
    """Live context for *query*, or ``None``.

    Never raises: a live-site problem must leave the static answer intact.
    """
    try:
        from app.config import settings

        if not settings.LIVE_SITE_ENABLED:
            return None
        return default_retriever().retrieve(query)
    except Exception:  # noqa: BLE001 - the static bundle is the fallback
        logger.exception("live site retrieval failed")
        return None
