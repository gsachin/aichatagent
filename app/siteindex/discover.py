"""Discovery helpers for the DSPMU site index.

The current site is a thin Bootstrap CMS. The old site's ``News.aspx`` is the
working notice manifest: it carries dates, titles and ``pdf/News`` links that
resolve. The current site's ``/archive-notice`` carries the same rows but its
PDF links are dead on the new host, so it is used only as a cross-check by the
report, not as the primary manifest.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

# The current archive page derives its filters from these title patterns. They
# are copied from the page's own JavaScript so the build groups notices the
# same way a human using the site would.
CATEGORY_PATTERNS: dict[str, str] = {
    "Admission": (
        r"admission|selection list|merit list|cut off|fcfs|seat matrix|"
        r"counselling|cuet"
    ),
    "Examination": (
        r"examination|exam|programme|schedule|admit card|carryover|"
        r"practical notice|centre|backlog|attendance|b\.ed"
    ),
    "Result": r"result|score|marks|grade",
    "Convocation": r"convocation|gold medalist|scarf|degree certificate|ceremony",
    "Holiday": r"holiday|closure|suspended|closed",
    "Tender": r"tender|quotation|procurement|rate contract|rfp",
    "DSW-Office": (
        r"dsw|scholarship|union|sports|election|nss|hostel|induction|azim premji"
    ),
}

#: Categories that carry student names, roll numbers or merit positions.
PII_CATEGORIES = frozenset({"Admission", "Result"})

#: Extra title check. A "Miscellaneous" notice can still be a result sheet, and
#: a convocation notice can contain a merit list.
_PII_TITLE = re.compile(
    r"\b(?:merit list|selection list|selected candidates?|shortlisted|"
    r"waiting list|result|roll\s*(?:no|number)|cut[- ]?off)\b",
    re.IGNORECASE,
)

#: A six-or-more digit run in a title is a roll number or similar identifier,
#: not a year. Four-digit years are deliberately allowed.
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass(frozen=True)
class Notice:
    date: str  # ISO date when parseable, otherwise ""
    date_text: str
    title: str
    url: str
    category: str
    pii: bool
    source: str = "old-site News.aspx"

    @property
    def id(self) -> str:
        return "notice-" + hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:10]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "date_text": self.date_text,
            "title": self.title,
            "url": self.url,
            "category": self.category,
            "pii": self.pii,
            "source": self.source,
        }


@dataclass(frozen=True)
class NavNode:
    label: str
    url: str
    parent: str
    depth: int


@dataclass(frozen=True)
class PdfLink:
    title: str
    url: str


def classify_title(title: str) -> str:
    """Return the site's own title category, or ``Miscellaneous``."""
    for category, pattern in CATEGORY_PATTERNS.items():
        if re.search(pattern, title, re.IGNORECASE):
            return category
    return "Miscellaneous"


def looks_pii(title: str, category: str | None = None) -> bool:
    """Conservative PII triage for a notice title.

    The category filter is the first gate. The title pattern catches a result
    or merit list filed under another category. The long-number check catches
    roll numbers that appear in a title. This is a heuristic, not a legal PII
    audit; the build also refuses to put any PII-category title in the prompt.
    """
    category = category or classify_title(title)
    return (
        category in PII_CATEGORIES
        or bool(_PII_TITLE.search(title))
        or bool(_LONG_NUMBER.search(title))
    )


def normalise_date(text: str) -> str:
    """Best-effort ISO date from the site's display formats."""
    raw = (text or "").strip()
    if not raw:
        return ""
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    # Manual fallback for a locale with a non-English month abbreviation.
    match = re.match(r"^(\d{1,2})[-/ ]([A-Za-z]{3,})[-/ ](\d{4})$", raw)
    if match:
        month = _MONTHS.get(match.group(2)[:3].lower())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1))).isoformat()
            except ValueError:
                pass
    return ""


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    # The old site appends "(507.04 KB)" to the link text.
    return re.sub(r"\s*\(\s*[\d.]+\s*(?:KB|MB)\s*\)\s*$", "", title, flags=re.I)


def _safe_url(base: str, href: str) -> str:
    url = urljoin(base, href.strip())
    return quote(url, safe=":/?&=#%+;,")


def parse_news_manifest(html: str, base_url: str) -> list[Notice]:
    """Parse the old site's News.aspx table into dated notice records."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    found: dict[str, Notice] = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        anchor = cells[2].find("a", href=True)
        if anchor is None:
            continue
        href = anchor["href"].strip()
        if not href.lower().endswith(".pdf"):
            continue
        title = _clean_title(anchor.get_text(" ", strip=True))
        if not title:
            continue
        date_text = cells[1].get_text(" ", strip=True)
        url = _safe_url(base_url, href)
        category = classify_title(title)
        notice = Notice(
            date=normalise_date(date_text),
            date_text=date_text,
            title=title,
            url=url,
            category=category,
            pii=looks_pii(title, category),
        )
        previous = found.get(url)
        if previous is None or notice.date > previous.date:
            found[url] = notice

    return sorted(
        found.values(),
        key=lambda n: (n.date or "0000-00-00", n.title.lower()),
        reverse=True,
    )


def parse_nav(html: str, base_url: str) -> list[NavNode]:
    """Return the server-rendered Bootstrap navigation as a flat tree."""
    soup = BeautifulSoup(html, "lxml")
    nav = soup.find("nav")
    if nav is None:
        return []

    out: list[NavNode] = []
    seen: set[str] = set()

    def walk(ul, depth: int, parent: str) -> None:
        for li in ul.find_all("li", recursive=False):
            anchor = li.find("a", recursive=False)
            label = ""
            href = ""
            if anchor is not None:
                label = anchor.get_text(" ", strip=True)
                href = (anchor.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                url = _safe_url(base_url, href)
                if url not in seen:
                    seen.add(url)
                    out.append(NavNode(label=label, url=url, parent=parent, depth=depth))
            child_ul = li.find("ul", recursive=False)
            if child_ul is not None:
                walk(child_ul, depth + 1, label or parent)

    root = nav.find("ul")
    if root is not None:
        walk(root, 0, "")
    return out


def parse_nav_links(html: str, base_url: str) -> list[NavNode]:
    """All navigation anchors, including cards outside a nested ``ul``."""
    soup = BeautifulSoup(html, "lxml")
    nav = soup.find("nav")
    if nav is None:
        return []
    out: list[NavNode] = []
    seen: set[str] = set()
    for anchor in nav.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = _safe_url(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        out.append(
            NavNode(
                label=anchor.get_text(" ", strip=True),
                url=url,
                parent="",
                depth=0,
            )
        )
    return out


def parse_sitemap(xml_text: str) -> list[dict[str, str]]:
    """Return sitemap entries. The sitemap is supplemental, not authoritative."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    out: list[dict[str, str]] = []
    for node in root.findall("s:url", namespace):
        loc = node.find("s:loc", namespace)
        lastmod = node.find("s:lastmod", namespace)
        if loc is not None and loc.text:
            out.append({"loc": loc.text.strip(), "lastmod": (lastmod.text or "").strip() if lastmod is not None else ""})
    return out


def parse_pdf_links(html: str, base_url: str) -> list[PdfLink]:
    """Every PDF link on a page, de-duplicated by resolved URL."""
    soup = BeautifulSoup(html, "lxml")
    out: list[PdfLink] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        url = _safe_url(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        title = _clean_title(anchor.get_text(" ", strip=True)) or url.rsplit("/", 1)[-1]
        out.append(PdfLink(title=title, url=url))
    return out


def find_pdf(links: list[PdfLink], needle: str) -> PdfLink | None:
    """Find the first PDF whose title contains ``needle`` (case-insensitive)."""
    wanted = needle.lower()
    for link in links:
        if wanted in link.title.lower():
            return link
    return None


def latest_non_pii(notices: list[Notice], limit: int) -> list[Notice]:
    cleaned = [n for n in notices if not n.pii]
    cleaned.sort(key=lambda n: (n.date or "0000-00-00", n.title.lower()), reverse=True)
    return cleaned[: max(0, limit)]


def pii_counts(notices: list[Notice]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for notice in notices:
        if notice.pii:
            counts[notice.category] = counts.get(notice.category, 0) + 1
    return dict(sorted(counts.items()))


def is_old_site_host(url: str) -> bool:
    return urlparse(url).netloc == "oldsite.dspmuranchi.ac.in"
