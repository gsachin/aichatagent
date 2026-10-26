"""HTML extraction for the NGA content index.

Phase 3 indexes HTML pages only. PDF extraction remains a later phase; PDF
links are recorded as document metadata, not fetched. The extractor keeps the
main content (``main`` / ``article`` / ``body``), strips navigation and chrome,
and produces a stable SHA-256 over the normalized text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

_SKIP = {
    "script", "style", "noscript", "svg", "canvas", "form", "iframe",
    "nav", "footer", "header",
}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass(frozen=True)
class ExtractedPage:
    url: str
    title: str
    text: str
    markdown: str
    sections: tuple[dict, ...]
    tables: tuple[dict, ...]
    links: tuple[dict, ...]
    documents: tuple[dict, ...]
    language: str
    content_hash: str
    char_count: int


def _clean(text: str) -> str:
    text = (text or "").replace("\xa0", " ").replace("​", "")
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


def _language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    if re.search(r"[઀-૿]", text):
        return "gu"
    return "en"


def _title(soup: BeautifulSoup) -> str:
    node = soup.find("h1") or soup.title
    title = node.get_text(" ", strip=True) if node else ""
    return re.split(r"\s+[|·—]\s+", title)[0].strip()[:200]


def _tables(main: Tag) -> tuple[dict, ...]:
    out: list[dict] = []
    for table in main.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [
                re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                for cell in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)
        if rows:
            out.append({"rows": rows})
    return tuple(out)


def _links(main: Tag, base_url: str) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    links: list[dict] = []
    documents: list[dict] = []
    seen: set[str] = set()
    for anchor in main.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        title = anchor.get_text(" ", strip=True) or url
        item = {"title": title[:200], "url": url}
        links.append(item)
        if parsed.path.lower().endswith(".pdf"):
            documents.append(item)
    return tuple(links), tuple(documents)


def extract_html(html: str, url: str) -> ExtractedPage:
    soup = BeautifulSoup(html or "", "lxml")
    title = _title(soup)
    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        text = ""
        tables: tuple[dict, ...] = ()
        links: tuple[dict, ...] = ()
        documents: tuple[dict, ...] = ()
    else:
        for tag in main.find_all(list(_SKIP)):
            tag.decompose()
        text = _clean(main.get_text("\n", strip=True))
        tables = _tables(main)
        links, documents = _links(main, url)

    sections: tuple[dict, ...] = ()
    if text:
        sections = ({"heading": title or "Page", "text": text},)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ExtractedPage(
        url=url,
        title=title,
        text=text,
        markdown=text,
        sections=sections,
        tables=tables,
        links=links,
        documents=documents,
        language=_language(text),
        content_hash=content_hash,
        char_count=len(text),
    )
