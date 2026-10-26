"""Typed records for the DSPMU navigation graph.

The graph is a tree of navigation sections and pages plus cross-link and
document edges. It is deliberately relational: no graph database, no vector
column, no embedding. See ``doc/sdlc/nga/02-hld.md`` ADR-011.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NodeStatus(str, Enum):
    OK = "ok"
    EMPTY_SHELL = "empty_shell"
    SOFT_404 = "soft_404"
    BLOCKED = "blocked"
    EXTERNAL = "external"
    UNREACHABLE = "unreachable"
    REMOVED = "removed"


class Authority(str, Enum):
    VERIFIED_FACT = "verified_fact"
    OFFICIAL_PAGE = "official_page"
    OFFICIAL_DOCUMENT = "official_document"
    EXTERNAL = "external"
    DERIVED = "derived"


class PiiClass(str, Enum):
    PUBLIC = "public"
    STAFF_DIRECTORY = "staff_directory"
    STUDENT_LIST = "student_list"
    BLOCKED = "blocked"


class ContentType(str, Enum):
    SECTION = "section"
    HTML = "html"
    PDF = "pdf"
    EXTERNAL = "external"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    EMPTY = "empty"
    ERROR = "error"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SiteNode:
    """One navigation point or content object in the graph."""

    node_id: str
    url: str
    canonical_path: str
    parent_id: str | None
    depth: int
    section: str
    title: str
    nav_label: str
    aliases: tuple[str, ...]
    languages: tuple[str, ...]
    content_type: ContentType
    authority: Authority
    pii_class: PiiClass
    status: NodeStatus
    lastmod: str | None
    content_hash: str
    fetched_at: str
    verified_at: str | None
    size_bytes: int
    #: The node that owns this content when a duplicate page was detected.
    #: Empty or equal to ``node_id`` means this node is canonical.
    canonical_id: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "url": self.url,
            "canonical_path": self.canonical_path,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "section": self.section,
            "title": self.title,
            "nav_label": self.nav_label,
            "aliases": list(self.aliases),
            "languages": list(self.languages),
            "content_type": self.content_type.value,
            "authority": self.authority.value,
            "pii_class": self.pii_class.value,
            "status": self.status.value,
            "lastmod": self.lastmod,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
            "verified_at": self.verified_at,
            "size_bytes": self.size_bytes,
            "canonical_id": self.canonical_id or self.node_id,
        }


@dataclass(frozen=True)
class PageVersion:
    """One extracted version of a page body."""

    node_id: str
    version: int
    text: str
    markdown: str
    tables: tuple[dict, ...]
    sections: tuple[dict, ...]
    links: tuple[dict, ...]
    documents: tuple[dict, ...]
    language: str
    content_hash: str
    fetched_at: str
    valid_from: str
    valid_until: str | None = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "version": self.version,
            "text": self.text,
            "markdown": self.markdown,
            "tables": list(self.tables),
            "sections": list(self.sections),
            "links": list(self.links),
            "documents": list(self.documents),
            "language": self.language,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
        }


@dataclass(frozen=True)
class SiteDocument:
    """Background-extracted text for one linked PDF."""

    node_id: str
    url: str
    mime: str = "application/pdf"
    pages: int = 0
    extraction_status: str = DocumentStatus.PENDING.value
    source_page_id: str = ""
    text: str = ""
    tables: tuple = ()
    ocr: bool = False
    ocr_lang: str = ""
    ocr_quality: str = ""
    needs_review: bool = False
    content_hash: str = ""
    size_bytes: int = 0
    fetched_at: str | None = None
    verified_at: str | None = None
    error: str = ""
    reviewed_at: str | None = None
    reviewed_by: str = ""
    pii_flagged: bool = False

    @property
    def answerable(self) -> bool:
        return (
            self.extraction_status == DocumentStatus.DONE.value
            and not self.needs_review
            and not self.pii_flagged
        )

    def to_dict(self, *, include_text: bool = False) -> dict:
        data = {
            "node_id": self.node_id,
            "url": self.url,
            "mime": self.mime,
            "pages": self.pages,
            "extraction_status": self.extraction_status,
            "source_page_id": self.source_page_id,
            "tables": self.tables,
            "ocr": self.ocr,
            "ocr_lang": self.ocr_lang,
            "ocr_quality": self.ocr_quality,
            "needs_review": self.needs_review,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "fetched_at": self.fetched_at,
            "verified_at": self.verified_at,
            "error": self.error,
            "reviewed_at": self.reviewed_at,
            "reviewed_by": self.reviewed_by,
            "pii_flagged": self.pii_flagged,
        }
        if include_text:
            data["text"] = self.text
        return data


@dataclass(frozen=True)
class SiteEdge:
    """A directed edge between two nodes."""

    edge_id: str
    src_id: str
    dst_id: str
    kind: str  # "parent_of" | "links_to" | "documents" | "mentions"
    anchor: str
    ordinal: int

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "kind": self.kind,
            "anchor": self.anchor,
            "ordinal": self.ordinal,
        }
