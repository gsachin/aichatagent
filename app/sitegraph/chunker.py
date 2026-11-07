"""Split indexed pages/documents into retrieval chunks.

Chunks preserve the source node, section, heading and page so an answer can
cite the exact span. HTML pages are chunked by section; PDFs by text window
plus one chunk per extracted table page.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 180


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    node_id: str
    source_kind: str
    title: str
    url: str
    section: str
    heading: str
    page: int | None
    text: str
    token_count: int
    content_hash: str
    authority: str
    pii_class: str
    language: str
    index_version: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "node_id": self.node_id,
            "source_kind": self.source_kind,
            "title": self.title,
            "url": self.url,
            "section": self.section,
            "heading": self.heading,
            "page": self.page,
            "text": self.text,
            "token_count": self.token_count,
            "content_hash": self.content_hash,
            "authority": self.authority,
            "pii_class": self.pii_class,
            "language": self.language,
            "index_version": self.index_version,
        }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _split_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    value = _clean(text)
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]
    chunks: list[str] = []
    start = 0
    length = len(value)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            window = value[start:end]
            boundary = max(
                window.rfind(". "),
                window.rfind("! "),
                window.rfind("? "),
                window.rfind("; "),
            )
            if boundary >= max_chars // 2:
                end = start + boundary + 1
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(start + 1, end - max(0, overlap))
    return chunks


def _chunk_id(
    node_id: str,
    heading: str,
    page: int | None,
    ordinal: int,
    text: str,
) -> str:
    digest = hashlib.sha1(
        f"{node_id}|{heading}|{page}|{ordinal}|{text}".encode("utf-8")
    ).hexdigest()
    return f"chunk.{digest[:16]}"


def _base(
    *,
    node_id: str,
    title: str,
    url: str,
    section: str,
    heading: str,
    page: int | None,
    text: str,
    source_kind: str,
    authority: str,
    pii_class: str,
    language: str,
    index_version: str,
    ordinal: int = 0,
) -> Chunk:
    cleaned = _clean(text)
    return Chunk(
        chunk_id=_chunk_id(node_id, heading, page, ordinal, cleaned),
        node_id=node_id,
        source_kind=source_kind,
        title=title,
        url=url,
        section=section,
        heading=heading,
        page=page,
        text=cleaned,
        token_count=max(1, len(cleaned) // 4),
        content_hash=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
        authority=authority,
        pii_class=pii_class,
        language=language,
        index_version=index_version,
    )


def chunks_for_page(
    row: dict,
    *,
    index_version: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    node_id = str(row["node_id"])
    title = str(row.get("title") or "")
    url = str(row.get("url") or "")
    section = str(row.get("section") or "")
    authority = str(row.get("authority") or "official_page")
    pii_class = str(row.get("pii_class") or "public")
    language = str(row.get("language") or "en")
    try:
        sections = json.loads(row.get("sections") or "[]")
    except (TypeError, ValueError):
        sections = []
    if not sections:
        text = row.get("text") or ""
        sections = [{"heading": title or "Page", "text": text}]
    chunks: list[Chunk] = []
    for section_item in sections:
        if not isinstance(section_item, dict):
            continue
        heading = str(section_item.get("heading") or title or "")
        text = section_item.get("text") or ""
        for piece in _split_text(
            text, max_chars=max_chars, overlap=overlap
        ):
            chunks.append(
                _base(
                    node_id=node_id,
                    title=title,
                    url=url,
                    section=section,
                    heading=heading,
                    page=None,
                    text=piece,
                    source_kind="html",
                    authority=authority,
                    pii_class=pii_class,
                    language=language,
                    index_version=index_version,
                    ordinal=len(chunks),
                )
            )
    return chunks


def _render_rows(table) -> str:
    lines: list[str] = []
    for row in table or ():
        if isinstance(row, (list, tuple)):
            cells = [_clean(str(cell or "")) for cell in row]
            if any(cells):
                lines.append(" | ".join(cells))
        elif str(row).strip():
            lines.append(str(row).strip())
    return "\n".join(lines)


def chunks_for_document(
    row: dict,
    *,
    index_version: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    if str(row.get("extraction_status") or "") != "done":
        return []
    # OCR text under review, and PII-flagged text, must never enter chunk
    # retrieval. The body index already enforces this; the chunk index must
    # use the same gate or unreviewed text becomes answerable again.
    if int(row.get("needs_review") or 0) or int(row.get("pii_flagged") or 0):
        return []
    node_id = str(row["node_id"])
    title = str(row.get("title") or "")
    url = str(row.get("url") or "")
    section = str(row.get("section") or "")
    authority = str(row.get("authority") or "official_document")
    pii_class = str(row.get("pii_class") or "public")
    language = str(row.get("language") or "en")
    chunks: list[Chunk] = []
    for ordinal, piece in enumerate(
        _split_text(
            row.get("text") or "", max_chars=max_chars, overlap=overlap
        )
    ):
        chunks.append(
            _base(
                node_id=node_id,
                title=title,
                url=url,
                section=section,
                heading=title or "Document",
                page=None,
                text=piece,
                source_kind="pdf",
                authority=authority,
                pii_class=pii_class,
                language=language,
                index_version=index_version,
                ordinal=ordinal,
            )
        )
    try:
        pages = json.loads(row.get("tables") or "[]")
    except (TypeError, ValueError):
        pages = []
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, (list, tuple)):
            continue
        for table_index, table in enumerate(page, start=1):
            text = _render_rows(table)
            if not text:
                continue
            chunks.append(
                _base(
                    node_id=node_id,
                    title=title,
                    url=url,
                    section=section,
                    heading=f"Table {table_index}",
                    page=page_index,
                    text=text,
                    source_kind="table",
                    authority=authority,
                    pii_class=pii_class,
                    language=language,
                    index_version=index_version,
                    ordinal=len(chunks),
                )
            )
    return chunks


__all__ = [
    "Chunk",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP",
    "chunks_for_document",
    "chunks_for_page",
]
