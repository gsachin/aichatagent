"""Read-only Phase 2 tool server for the navigation graph.

The tool contract is the one in ``doc/sdlc/nga/03-lld.md`` §4, implemented
against SQLite and the committed notice JSONL. There is no network call in
this module: page fetching is a later phase. The envelope, limits and error
codes are stable so the executor can be built against them.

Tools:
    site.search, site.navigate, site.open, site.latest,
    site.documents, site.freshness, site.follow
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from app.config import settings
from app.sitegraph.models import ContentType, SiteDocument, SiteNode
from app.sitegraph.notices import DEFAULT_NOTICES_PATH, NoticeIndex
from app.sitegraph.policy import is_answerable
from app.sitegraph.search import node_payload, search_nodes
from app.sitegraph.store import GraphStore

logger = logging.getLogger("sitegraph.tools")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTENT_TYPES = {"html", "pdf", "section", "external"}


class ToolError(RuntimeError):
    """A typed tool failure that is safe to return to the executor."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _str_arg(args: dict, key: str, *, required: bool = True,
             default: str | None = None) -> str | None:
    value = args.get(key, default)
    if value is None:
        if required:
            raise ToolError("INVALID_ARGUMENT", f"{key} is required")
        return None
    if not isinstance(value, str):
        raise ToolError("INVALID_ARGUMENT", f"{key} must be a string")
    value = value.strip()
    if required and not value:
        raise ToolError("INVALID_ARGUMENT", f"{key} must not be empty")
    return value or None


def _int_arg(args: dict, key: str, *, default: int, minimum: int,
             maximum: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool):
        raise ToolError("INVALID_ARGUMENT", f"{key} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError("INVALID_ARGUMENT", f"{key} must be an integer") from None
    return max(minimum, min(maximum, number))


def _content_status(node: SiteNode) -> str:
    return {
        ContentType.HTML: "not_indexed",
        ContentType.SECTION: "section",
        ContentType.PDF: "metadata_only",
        ContentType.EXTERNAL: "external",
    }.get(node.content_type, "unknown")


def _document_status(document: SiteDocument | None) -> str:
    if document is None:
        return "metadata_only"
    if document.extraction_status == "blocked":
        return "blocked"
    if document.needs_review:
        return "needs_review"
    if document.extraction_status == "done":
        return "indexed"
    return document.extraction_status


def _summary(node: SiteNode) -> dict:
    return {
        "node_id": node.node_id,
        "title": node.title,
        "nav_label": node.nav_label,
        "url": node.url,
        "canonical_path": node.canonical_path,
        "section": node.section,
        "depth": node.depth,
        "content_type": node.content_type.value,
        "authority": node.authority.value,
        "status": node.status.value,
        "pii_class": node.pii_class.value,
        "lastmod": node.lastmod,
        "verified_at": node.verified_at,
        "canonical_id": node.canonical_id or node.node_id,
        "content_status": _content_status(node),
    }


def _body_snippet(text: str, tokens: tuple[str, ...], limit: int = 240) -> str:
    lowered = (text or "").lower()
    positions = [
        lowered.find(token) for token in tokens
        if token and lowered.find(token) >= 0
    ]
    if not positions:
        return _clean_snippet(text[:limit])
    start = max(0, min(positions) - 80)
    end = min(len(text), start + limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + _clean_snippet(text[start:end]) + suffix


def _clean_snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _snippet(node: SiteNode) -> str:
    if node.content_type is ContentType.PDF:
        return f"Document: {node.title}"
    if node.content_type is ContentType.SECTION:
        return f"Section: {node.title} ({node.section})"
    if node.content_type is ContentType.EXTERNAL:
        return f"External link: {node.title}"
    return f"{node.section} — {node.title}"


class SiteToolServer:
    """Serve the ``site.*`` tools over one compiled graph."""

    TOOLS = (
        "site.search",
        "site.navigate",
        "site.open",
        "site.latest",
        "site.documents",
        "site.freshness",
        "site.follow",
    )

    def __init__(
        self,
        graph_path: str | Path,
        *,
        notices_path: str | Path | None = DEFAULT_NOTICES_PATH,
        pii_guard: bool | None = None,
    ) -> None:
        self.pii_guard = (
            settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
        )
        self.store = GraphStore(graph_path)
        self.notices = NoticeIndex(notices_path, pii_guard=self.pii_guard)

    def call(self, tool: str, args: dict | None = None) -> dict:
        """Dispatch one tool call and return the common envelope."""
        started = time.monotonic()
        args = args or {}
        policy = {"allowlist": "graph-only", "pii": "pass"}
        try:
            if not isinstance(tool, str) or tool not in self.TOOLS:
                raise ToolError("INVALID_TOOL", f"unknown tool {tool!r}")
            handler = getattr(self, f"_tool_{tool.replace('site.', '')}", None)
            if handler is None:
                raise ToolError("INVALID_TOOL", f"unknown tool {tool!r}")
            if not isinstance(args, dict):
                raise ToolError("INVALID_ARGUMENT", "args must be an object")
            data = handler(args)
            status = "ok"
            error = None
        except ToolError as exc:
            data = {}
            status = exc.code
            error = exc.message
            if exc.code == "BLOCKED":
                policy["pii"] = "block"
        except Exception as exc:  # noqa: BLE001 - tool boundary
            logger.exception("site tool failed: %s", tool)
            data = {}
            status = "UPSTREAM_ERROR"
            error = str(exc)
        return {
            "status": status,
            "tool": tool,
            "data": data,
            "error": error,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "policy": policy,
        }

    # ── node resolution ──────────────────────────────────────────

    def _node(self, node_id: str) -> SiteNode:
        node = self.store.get_node(node_id)
        if node is None:
            raise ToolError("NOT_FOUND", f"no node {node_id!r}")
        return node

    def _answerable(self, node: SiteNode) -> SiteNode:
        if not is_answerable(node, self.pii_guard):
            raise ToolError("BLOCKED", f"node {node.node_id!r} is not answerable")
        return node

    def _resolve(self, args: dict) -> tuple[SiteNode, str]:
        node_id = _str_arg(args, "node_id", required=False)
        path = _str_arg(args, "path", required=False)
        breadcrumb = _str_arg(args, "breadcrumb", required=False)
        if node_id:
            return self._node(node_id), "node_id"
        if path:
            node = self.store.get_node_by_path(path)
            if node is None:
                raise ToolError("NOT_FOUND", f"no node at path {path!r}")
            return node, "path"
        if breadcrumb:
            return self._resolve_breadcrumb(breadcrumb), "breadcrumb"
        raise ToolError(
            "INVALID_ARGUMENT", "one of node_id, path or breadcrumb is required"
        )

    def _resolve_breadcrumb(self, breadcrumb: str) -> SiteNode:
        parts = [part.strip() for part in re.split(r"\s*>\s*", breadcrumb) if part.strip()]
        if not parts:
            raise ToolError("INVALID_ARGUMENT", "breadcrumb must not be empty")
        current = self.store.get_node("home")
        if current is None:
            raise ToolError("NOT_FOUND", "home node is missing")
        for part in parts:
            if part.lower() in (current.title.lower(), current.nav_label.lower()):
                continue
            children = self.store.children(current.node_id)
            match = next(
                (
                    child for child in children
                    if part.lower() in (
                        child.title.lower(), child.nav_label.lower()
                    )
                ),
                None,
            )
            if match is None:
                raise ToolError(
                    "NOT_FOUND",
                    f"breadcrumb segment {part!r} is not under {current.title!r}",
                )
            current = match
        return current

    def _payload(self, node: SiteNode) -> dict:
        return node_payload(self.store, node)

    def _document_summary(
        self, node: SiteNode, document: SiteDocument | None = None
    ) -> dict:
        doc = document
        if doc is None:
            doc = self.store.document(node.node_id)
        data = _summary(node)
        data.update(
            {
                "mime": doc.mime if doc else "application/pdf",
                "pages": doc.pages if doc else 0,
                "extraction_status": (
                    doc.extraction_status if doc else "pending"
                ),
                "needs_review": doc.needs_review if doc else False,
                "ocr": doc.ocr if doc else False,
                "ocr_lang": doc.ocr_lang if doc else "",
                "content_hash": (
                    (doc.content_hash if doc else "") or node.content_hash
                ),
                "content_status": _document_status(doc),
                "verified_at": (
                    doc.verified_at if doc is not None else node.verified_at
                ),
                "reviewed_at": doc.reviewed_at if doc else None,
                "reviewed_by": doc.reviewed_by if doc else "",
                "pii_flagged": doc.pii_flagged if doc else False,
                "snippet": (
                    _body_snippet(doc.text, (), 240)
                    if doc is not None and doc.answerable else ""
                ),
            }
        )
        return data

    # ── tools ────────────────────────────────────────────────────

    def _tool_search(self, args: dict) -> dict:
        raw_query = args.get("query")
        if raw_query is None:
            raise ToolError("INVALID_ARGUMENT", "query is required")
        if not isinstance(raw_query, str):
            raise ToolError("INVALID_ARGUMENT", "query must be a string")
        # An empty query is a valid empty search, not an argument error.
        query = raw_query.strip()
        section = _str_arg(args, "section", required=False)
        content_type = _str_arg(args, "content_type", required=False)
        content_type = content_type.lower() if content_type else None
        if content_type and content_type not in _CONTENT_TYPES:
            raise ToolError(
                "INVALID_ARGUMENT",
                f"content_type must be one of {sorted(_CONTENT_TYPES)}",
            )
        limit = _int_arg(args, "limit", default=10, minimum=1, maximum=10)
        normalized, hits = search_nodes(
            self.store,
            query or "",
            section=section,
            content_type=content_type,
            limit=limit,
            pii_guard=self.pii_guard,
        )
        results = []
        for hit in hits:
            payload = self._payload(hit.node)
            version = self.store.page_version(hit.node.node_id)
            if version is not None:
                payload.update(
                    {
                        "content_status": "indexed",
                        "content_hash": version.content_hash,
                        "content_version": version.version,
                    }
                )
                snippet = _body_snippet(version.text, normalized.tokens)
            elif hit.node.content_type is ContentType.PDF:
                document = self.store.document(hit.node.node_id)
                payload.update(
                    {
                        "content_status": _document_status(document),
                        "content_hash": (
                            (document.content_hash if document else "")
                            or hit.node.content_hash
                        ),
                        "pages": document.pages if document else 0,
                        "extraction_status": (
                            document.extraction_status if document else "pending"
                        ),
                        "needs_review": (
                            document.needs_review if document else False
                        ),
                    }
                )
                snippet = (
                    _body_snippet(document.text, normalized.tokens)
                    if document is not None and document.answerable
                    else _snippet(hit.node)
                )
            else:
                snippet = _snippet(hit.node)
            payload.update(
                {
                    "score": hit.score,
                    "rank": hit.rank,
                    "body_match": hit.body_match,
                    "snippet": snippet,
                }
            )
            results.append(payload)
        return {
            "results": results,
            "total": len(results),
            "normalized_query": normalized.text,
            "language": normalized.language,
        }

    def _tool_navigate(self, args: dict) -> dict:
        node, found_by = self._resolve(args)
        self._answerable(node)
        parent = self.store.parent(node.node_id)
        children = [
            child for child in self.store.children(node.node_id)
            if is_answerable(child, self.pii_guard)
        ]
        siblings = [
            sibling for sibling in self.store.siblings(node.node_id)
            if is_answerable(sibling, self.pii_guard)
        ]
        return {
            "node": _summary(node),
            "breadcrumb": [
                {"node_id": item.node_id, "title": item.title}
                for item in self.store.breadcrumb(node.node_id)
            ],
            "parent": (
                _summary(parent)
                if parent and is_answerable(parent, self.pii_guard) else None
            ),
            "children": [_summary(child) for child in children],
            "siblings": [_summary(sibling) for sibling in siblings],
            "found_by": found_by,
        }

    def _tool_open(self, args: dict) -> dict:
        node, found_by = self._resolve(args)
        self._answerable(node)
        section_filter = _str_arg(args, "section", required=False)
        offset = _int_arg(args, "offset", default=0, minimum=0, maximum=1_000_000)
        max_chars = _int_arg(
            args, "max_chars", default=4000, minimum=1, maximum=8192
        )
        if node.content_type is ContentType.PDF:
            return self._open_document(
                node,
                section=section_filter,
                offset=offset,
                max_chars=max_chars,
                found_by=found_by,
            )
        version = self.store.page_version(node.node_id)

        links: list[dict] = []
        seen_links: set[str] = set()
        for edge in self.store.outgoing_edges(node.node_id):
            target = self.store.get_node(edge.dst_id)
            if target is None or not is_answerable(target, self.pii_guard):
                continue
            item = {
                "edge_id": edge.edge_id,
                "kind": edge.kind,
                "title": target.title,
                "node_id": target.node_id,
                "external": target.content_type is ContentType.EXTERNAL,
                **(
                    {"external_url": target.url}
                    if target.content_type is ContentType.EXTERNAL else {}
                ),
            }
            links.append(item)
            seen_links.add(target.url)

        documents: list[dict] = []
        seen_documents: set[str] = set()
        for doc in self.store.documents(node.node_id):
            if not is_answerable(doc, self.pii_guard):
                continue
            documents.append(self._document_summary(doc))
            seen_documents.add(doc.url)

        if version is not None:
            for link in version.links:
                url = str(link.get("url") or "")
                if url and url not in seen_links:
                    seen_links.add(url)
                    links.append(
                        {
                            "kind": "content",
                            "title": str(link.get("title") or url),
                            "url": url,
                        }
                    )
            for doc in version.documents:
                url = str(doc.get("url") or "")
                if url and url not in seen_documents:
                    seen_documents.add(url)
                    doc_node = self.store.get_node_by_url(url)
                    if doc_node is not None and is_answerable(
                        doc_node, self.pii_guard
                    ):
                        documents.append(self._document_summary(doc_node))
                    else:
                        documents.append(
                            {
                                "title": str(doc.get("title") or url),
                                "url": url,
                                "content_type": "pdf",
                                "content_status": "metadata_only",
                            }
                        )

            source_sections = list(version.sections) or [
                {"heading": node.title or "Page", "text": version.text}
            ]
            if section_filter:
                selected = [
                    item for item in source_sections
                    if section_filter.lower() in str(
                        item.get("heading") or ""
                    ).lower()
                ]
                if not selected:
                    raise ToolError(
                        "NOT_FOUND",
                        f"section {section_filter!r} is not in {node.node_id!r}",
                    )
            else:
                selected = source_sections
            selected_text = "\n\n".join(
                str(item.get("text") or "") for item in selected
            )
            chunk = selected_text[offset:offset + max_chars]
            sections_out = (
                [{
                    "heading": (
                        selected[0].get("heading")
                        if len(selected) == 1 else node.title
                    ),
                    "text": chunk,
                }]
                if chunk else []
            )
            return {
                "node": self._payload(node),
                "sections": sections_out,
                "tables": list(version.tables),
                "links": links,
                "documents": documents,
                "found_by": found_by,
                "body_available": bool(selected_text.strip()),
                "content_status": "indexed",
                "content_hash": version.content_hash,
                "content_version": version.version,
                "fetched_at": version.fetched_at,
                "verified_at": node.verified_at,
                "offset": offset,
                "max_chars": max_chars,
                "truncated": offset + max_chars < len(selected_text),
            }

        return {
            "node": self._payload(node),
            "sections": [],
            "tables": [],
            "links": links,
            "documents": documents,
            "found_by": found_by,
            "body_available": False,
            "content_status": _content_status(node),
            "note": "Page body has not been indexed for this node.",
        }

    def _open_document(
        self,
        node: SiteNode,
        *,
        section: str | None,
        offset: int,
        max_chars: int,
        found_by: str,
    ) -> dict:
        if section:
            raise ToolError(
                "NOT_FOUND",
                f"document {node.node_id!r} has no heading sections",
            )
        document = self.store.document(node.node_id)
        summary = self._document_summary(node, document)
        status = _document_status(document)
        base = {
            "node": summary,
            "sections": [],
            "tables": [],
            "links": [],
            "documents": [],
            "found_by": found_by,
            "body_available": False,
            "content_status": status,
            "pages": document.pages if document else 0,
            "extraction_status": (
                document.extraction_status if document else "pending"
            ),
            "needs_review": document.needs_review if document else False,
            "ocr": document.ocr if document else False,
            "content_hash": (
                (document.content_hash if document else "") or node.content_hash
            ),
            "fetched_at": document.fetched_at if document else None,
            "verified_at": document.verified_at if document else None,
            "error": document.error if document else "",
        }
        if document is None:
            base["note"] = "Document metadata only; no extracted text."
            return base
        if status == "blocked":
            base["note"] = "Document text is blocked by the PII policy."
            return base
        if status == "needs_review":
            base["note"] = "Document text is awaiting human review."
            return base
        if status != "indexed":
            base["note"] = f"Document text is not available ({status})."
            return base

        text = document.text
        chunk = text[offset:offset + max_chars]
        if chunk:
            base["sections"] = [
                {"heading": node.title or "Document", "text": chunk}
            ]
        base["tables"] = list(document.tables)[:20]
        base["body_available"] = bool(chunk.strip())
        base["offset"] = offset
        base["max_chars"] = max_chars
        base["truncated"] = offset + max_chars < len(text)
        return base

    def _tool_latest(self, args: dict) -> dict:
        if not self.notices.available:
            raise ToolError("NOT_AVAILABLE", "notice catalogue is not built")
        since = _str_arg(args, "since", required=False)
        if since and not _DATE_RE.match(since):
            raise ToolError("INVALID_ARGUMENT", "since must be YYYY-MM-DD")
        limit = _int_arg(args, "limit", default=10, minimum=1, maximum=10)
        categories_value = args.get("categories", [])
        if categories_value is None:
            categories: list[str] = []
        elif isinstance(categories_value, str):
            categories = [categories_value]
        elif isinstance(categories_value, list):
            categories = [str(item) for item in categories_value]
        else:
            raise ToolError("INVALID_ARGUMENT", "categories must be a list or string")
        items = self.notices.latest(since=since, limit=limit, categories=categories)
        return {
            "items": [notice.to_dict() for notice in items],
            "total": len(items),
            "since": since,
            "categories": categories,
        }

    def _tool_documents(self, args: dict) -> dict:
        node = self._node(_str_arg(args, "node_id") or "")
        self._answerable(node)
        return {
            "node_id": node.node_id,
            "documents": [
                self._document_summary(doc)
                for doc in self.store.documents(node.node_id)
                if is_answerable(doc, self.pii_guard)
            ],
        }

    def _tool_freshness(self, args: dict) -> dict:
        node = self._node(_str_arg(args, "node_id") or "")
        self._answerable(node)
        state = self.store.fetch_state(node.node_id) or {}
        version = self.store.page_version(node.node_id)
        document = self.store.document(node.node_id)
        payload = {
            "node_id": node.node_id,
            "lastmod": node.lastmod,
            "fetched_at": node.fetched_at,
            "verified_at": node.verified_at,
            "status": node.status.value,
            "authority": node.authority.value,
            "content_type": node.content_type.value,
            "content_status": (
                "indexed" if version
                else _document_status(document)
                if node.content_type is ContentType.PDF
                else _content_status(node)
            ),
            "content_hash": (
                version.content_hash if version
                else (document.content_hash if document else node.content_hash)
            ),
            "content_version": version.version if version else None,
            "checked_at": state.get("checked_at"),
            "next_due_at": state.get("next_due_at"),
            "http_status": state.get("http_status"),
            "etag": state.get("etag") or "",
            "last_modified": state.get("last_modified") or "",
            "failure_count": int(state.get("failure_count") or 0),
            "error": state.get("error") or "",
            "ttl_s": None,
        }
        if node.content_type is ContentType.PDF:
            payload.update(
                {
                    "pages": document.pages if document else 0,
                    "extraction_status": (
                        document.extraction_status if document else "pending"
                    ),
                    "needs_review": document.needs_review if document else False,
                    "ocr": document.ocr if document else False,
                    "reviewed_at": document.reviewed_at if document else None,
                    "reviewed_by": document.reviewed_by if document else "",
                }
            )
        return payload

    def _tool_follow(self, args: dict) -> dict:
        edge_id = _str_arg(args, "edge_id")
        edge = self.store.get_edge(edge_id or "")
        if edge is None:
            raise ToolError("NOT_FOUND", f"no edge {edge_id!r}")
        target = self.store.get_node(edge.dst_id)
        if target is None:
            raise ToolError("NOT_FOUND", f"edge {edge_id!r} has no target")
        self._answerable(target)
        if target.content_type is ContentType.EXTERNAL:
            return {
                "edge_id": edge.edge_id,
                "external": True,
                "external_url": target.url,
                "title": target.title,
            }
        return {
            "edge_id": edge.edge_id,
            "external": False,
            "node": (
                self._document_summary(target)
                if target.content_type is ContentType.PDF
                else _summary(target)
            ),
        }
