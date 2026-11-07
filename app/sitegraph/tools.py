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
from app.sitegraph.embeddings import get_embedding_provider
from app.sitegraph.hybrid import search_chunks
from app.sitegraph.indexer import index_version_for
from app.sitegraph.models import ContentType, NodeStatus, SiteDocument, SiteNode
from app.sitegraph.notices import DEFAULT_NOTICES_PATH, NoticeIndex
from app.sitegraph.policy import can_ground, can_navigate
from app.sitegraph.search import (
    node_matches_level,
    node_payload,
    search_nodes,
)
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
    if node.status is NodeStatus.EMPTY_SHELL:
        return "empty_shell"
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
        "site.browse",
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
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.pii_guard = (
            settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
        )
        self.store = GraphStore(
            graph_path, busy_timeout_ms=busy_timeout_ms
        )
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
        if not can_ground(node, self.pii_guard):
            raise ToolError("BLOCKED", f"node {node.node_id!r} is not answerable")
        return node

    def _navigable(self, node: SiteNode) -> SiteNode:
        if not can_navigate(node, self.pii_guard):
            raise ToolError("BLOCKED", f"node {node.node_id!r} is not navigable")
        return node

    def _nav_summary(self, node: SiteNode) -> dict:
        data = _summary(node)
        data["groundable"] = can_ground(node, self.pii_guard)
        data["navigable"] = can_navigate(node, self.pii_guard)
        return data

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
        data = node_payload(self.store, node, pii_guard=self.pii_guard)
        data["groundable"] = can_ground(node, self.pii_guard)
        data["navigable"] = can_navigate(node, self.pii_guard)
        return data

    def _document_summary(
        self, node: SiteNode, document: SiteDocument | None = None
    ) -> dict:
        doc = document
        if doc is None:
            doc = self.store.document(node.node_id)
        data = _summary(node)
        data["groundable"] = can_ground(node, self.pii_guard)
        data["navigable"] = can_navigate(node, self.pii_guard)
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
        level = _str_arg(args, "level", required=False)
        if level is not None:
            level = level.upper()
            if level not in {"UG", "PG", "PhD"}:
                raise ToolError(
                    "INVALID_ARGUMENT",
                    "level must be one of ['UG', 'PG', 'PhD']",
                )
        normalized, hits = search_nodes(
            self.store,
            query or "",
            section=section,
            content_type=content_type,
            limit=limit,
            pii_guard=self.pii_guard,
            level=level,
        )
        # Chunk hits add passage-level recall and a precise snippet. Node
        # hits remain the fallback when the chunk index is empty or a
        # section/content-type filter is requested.
        entries: list[dict] = []
        if (
            settings.NGA_CHUNK_RETRIEVAL_ENABLED
            and not section
            and not content_type
            and self.store.chunk_count() > 0
        ):
            embedder = None
            if settings.EMBEDDING_PROVIDER not in ("", "off", "none"):
                try:
                    embedder = get_embedding_provider()
                except Exception:  # noqa: BLE001 - dense is optional
                    logger.exception("embedding provider unavailable")
            try:
                run = self.store.latest_run() or {}
                chunk_index_version = index_version_for(
                    str(run.get("graph_hash") or "")
                )
                chunk_hits = search_chunks(
                    self.store,
                    query or "",
                    limit=limit,
                    pii_guard=self.pii_guard,
                    embedder=embedder,
                    index_version=chunk_index_version,
                )
            except Exception:  # noqa: BLE001 - node search is the fallback
                logger.exception("chunk retrieval failed")
                chunk_hits = []
            node_by_id = {
                hit.node.node_id: hit for hit in hits
            }
            node_rank = {
                node_id: rank
                for rank, node_id in enumerate(node_by_id, start=1)
            }
            # One representative chunk per node: the first (best) one in the
            # fused chunk order. Its node is the node-level candidate.
            chunk_by_node: dict[str, object] = {}
            chunk_rank: dict[str, int] = {}
            # Chunk search must apply the same navigation/PII policy as node
            # search. Store-level PII filters are not enough: the title policy
            # (e.g. "Merit List...") lives in can_navigate.
            chunk_hits = [
                chunk_hit for chunk_hit in chunk_hits
                if can_navigate(chunk_hit.node, self.pii_guard)
                and node_matches_level(chunk_hit.node, level)
            ]
            for rank, chunk_hit in enumerate(chunk_hits, start=1):
                node_id = chunk_hit.node.node_id
                if node_id in chunk_by_node:
                    continue
                chunk_by_node[node_id] = chunk_hit
                chunk_rank[node_id] = rank

            # Reciprocal-rank fusion of node and chunk candidates. The old
            # implementation emitted every node hit first and only then added
            # chunk-only nodes, then truncated to `limit`; with limit=3 a
            # chunk-only node could never enter. Fusion happens BEFORE the
            # truncation, so a strong passage can outrank a weak title match.
            rrf_k = 60

            def fused_score(node_id: str) -> float:
                score = 0.0
                if node_id in node_rank:
                    score += 1.0 / (rrf_k + node_rank[node_id])
                if node_id in chunk_rank:
                    score += 1.0 / (rrf_k + chunk_rank[node_id])
                return score

            candidates = set(node_by_id) | set(chunk_by_node)
            ordered = sorted(
                candidates,
                key=lambda node_id: (
                    -fused_score(node_id),
                    (
                        node_by_id[node_id].node.title
                        if node_id in node_by_id
                        else chunk_by_node[node_id].node.title
                    ).lower(),
                    node_id,
                ),
            )
            for node_id in ordered[:limit]:
                node_hit = node_by_id.get(node_id)
                chunk_hit = chunk_by_node.get(node_id)
                node = (
                    node_hit.node if node_hit is not None
                    else chunk_hit.node
                )
                entry = {
                    "node": node,
                    "snippet": (
                        chunk_hit.snippet if chunk_hit is not None else None
                    ),
                    # Preserve the node relevance score for node candidates;
                    # fusion only decides order. Thresholds that read score
                    # must not change merely because chunks exist.
                    "score": (
                        node_hit.score if node_hit is not None
                        else round(fused_score(node_id), 8)
                    ),
                    "rank": (
                        node_hit.rank if node_hit is not None
                        else float(chunk_hit.bm25_rank or 0)
                    ),
                    "body_match": (
                        bool(node_hit is not None and node_hit.body_match)
                        or chunk_hit is not None
                    ),
                    "fusion_score": round(fused_score(node_id), 8),
                }
                if chunk_hit is not None:
                    entry.update(
                        {
                            "chunk_id": str(
                                chunk_hit.chunk.get("chunk_id") or ""
                            ),
                            "heading": str(
                                chunk_hit.chunk.get("heading") or ""
                            ),
                            "page": chunk_hit.chunk.get("page"),
                        }
                    )
                entries.append(entry)
        else:
            entries = [
                {
                    "node": hit.node,
                    "snippet": None,
                    "score": hit.score,
                    "rank": hit.rank,
                    "body_match": hit.body_match,
                }
                for hit in hits
            ]

        results = []
        for entry in entries:
            node = entry["node"]
            payload = self._payload(node)
            version = self.store.page_version(node.node_id)
            if version is not None:
                payload.update(
                    {
                        "content_status": "indexed",
                        "content_hash": version.content_hash,
                        "content_version": version.version,
                    }
                )
                snippet = (
                    entry["snippet"]
                    if entry.get("snippet") is not None
                    else _body_snippet(version.text, normalized.tokens)
                )
            elif node.content_type is ContentType.PDF:
                document = self.store.document(node.node_id)
                payload.update(
                    {
                        "content_status": _document_status(document),
                        "content_hash": (
                            (document.content_hash if document else "")
                            or node.content_hash
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
                    entry["snippet"]
                    if entry.get("snippet") is not None
                    else (
                        _body_snippet(document.text, normalized.tokens)
                        if document is not None and document.answerable
                        else _snippet(node)
                    )
                )
            else:
                snippet = (
                    entry["snippet"]
                    if entry.get("snippet") is not None
                    else _snippet(node)
                )
            payload.update(
                {
                    "score": entry["score"],
                    "rank": entry["rank"],
                    "body_match": entry["body_match"],
                    "snippet": snippet,
                }
            )
            if entry.get("chunk_id"):
                payload.update(
                    {
                        "chunk_id": entry["chunk_id"],
                        "heading": entry.get("heading") or "",
                        "page": entry.get("page"),
                    }
                )
            results.append(payload)
        return {
            "results": results,
            "total": len(results),
            "normalized_query": normalized.text,
            "language": normalized.language,
        }

    def _browse_target(self, query: str, hits: list) -> SiteNode:
        value = (query or "").lower()
        if "student life" in value:
            for hit in hits:
                if (
                    hit.node.node_id == "section.student-life"
                    or "student life" in hit.node.title.lower()
                ):
                    return hit.node
        if any(word in value for word in ("department", "faculty", "faculties")):
            for hit in hits:
                if (
                    hit.node.content_type is ContentType.SECTION
                    or hit.node.section.lower() == "departments"
                    or "department" in hit.node.title.lower()
                ):
                    return hit.node
        if any(word in value for word in ("document", "syllabus")):
            for hit in hits:
                version = self.store.page_version(hit.node.node_id)
                if self.store.documents(hit.node.node_id) or (
                    version is not None and version.documents
                ):
                    return hit.node
        return hits[0].node

    def _relevant_siblings(
        self,
        node: SiteNode,
        siblings: list[SiteNode],
        query: str,
    ) -> list[SiteNode]:
        if not siblings:
            return []
        if node.node_id.startswith("academic-programmes"):
            keep = [
                item for item in siblings
                if item.node_id.startswith("academic-programmes")
                or "programme" in item.title.lower()
            ]
            return (keep or siblings)[:20]
        if node.section.lower() == "departments" or "department" in query.lower():
            keep = [
                item for item in siblings
                if item.section.lower() == "departments"
            ]
            return (keep or siblings)[:20]
        return siblings[:10]

    def _department_collection(self, query: str) -> list[SiteNode] | None:
        value = (query or "").lower()
        if "department" not in value and "faculty" not in value:
            return None
        markers = ("list", "all", "what", "which", "how many", "departments", "faculties")
        if not any(marker in value for marker in markers):
            return None
        nodes = [
            node for node in self.store.load_nodes()
            if node.section.lower() == "departments"
            and node.content_type in (ContentType.HTML, ContentType.SECTION)
            and can_navigate(node, self.pii_guard)
        ]
        if not nodes:
            return None
        return sorted(nodes, key=lambda node: node.title.casefold())

    def _tool_browse(self, args: dict) -> dict:
        """Resolve a section/shell and return its navigation surface.

        This is the enumerate primitive: the planner does not need to know a
        node ID in advance. A shell is returned with ``groundable=false`` and
        ``body_available=false``; its children, documents and links are still
        available for the answer/composer.
        """
        raw_query = args.get("query")
        if not isinstance(raw_query, str) or not raw_query.strip():
            raise ToolError("INVALID_ARGUMENT", "query is required")
        limit = _int_arg(args, "limit", default=5, minimum=1, maximum=10)
        normalized, hits = search_nodes(
            self.store,
            raw_query.strip(),
            limit=limit,
            pii_guard=self.pii_guard,
        )
        if not hits:
            raise ToolError(
                "NOT_FOUND", f"no navigable node for {raw_query!r}"
            )
        node = self._browse_target(raw_query, hits)
        self._navigable(node)
        version = self.store.page_version(node.node_id)
        parent = self.store.parent(node.node_id)
        collection_nodes = self._department_collection(raw_query)
        collection = None
        if collection_nodes:
            children = collection_nodes
            collection = {
                "title": "Departments and Faculties",
                "count": len(collection_nodes),
            }
        else:
            children = [
                child for child in self.store.children(node.node_id)
                if can_navigate(child, self.pii_guard)
                and child.content_type in (
                    ContentType.HTML, ContentType.SECTION
                )
            ]
        siblings = self._relevant_siblings(
            node,
            [
                sibling for sibling in self.store.siblings(node.node_id)
                if can_navigate(sibling, self.pii_guard)
            ],
            raw_query,
        )
        if children:
            # A page with its own sections does not need the home-level
            # sibling list; it would be noise in the answer.
            siblings = []

        documents: list[dict] = []
        seen_documents: set[str] = set()
        for doc in self.store.documents(node.node_id):
            if can_ground(doc, self.pii_guard):
                documents.append(self._document_summary(doc))
                seen_documents.add(doc.url)
        if version is not None:
            for doc in version.documents:
                url = str(doc.get("url") or "")
                if not url or url in seen_documents:
                    continue
                seen_documents.add(url)
                doc_node = self.store.get_node_by_url(url)
                if doc_node is not None and can_ground(
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

        links: list[dict] = []
        seen_links: set[str] = set()
        for edge in self.store.outgoing_edges(node.node_id):
            target = self.store.get_node(edge.dst_id)
            if target is None or not can_navigate(target, self.pii_guard):
                continue
            links.append(
                {
                    "edge_id": edge.edge_id,
                    "kind": edge.kind,
                    "title": target.title,
                    "node_id": target.node_id,
                    "url": target.url,
                    "external": target.content_type is ContentType.EXTERNAL,
                }
            )
            seen_links.add(target.url)
        if version is not None:
            for link in version.links:
                url = str(link.get("url") or "")
                title = str(link.get("title") or url)
                if not url or url in seen_links:
                    continue
                if "/cdn-cgi/l/email-protection" in url:
                    continue
                if title.strip().lower() in ("view details", "download / view"):
                    continue
                seen_links.add(url)
                links.append(
                    {
                        "kind": "content",
                        "title": title,
                        "url": url,
                    }
                )

        groundable = can_ground(node, self.pii_guard)
        body_available = bool(
            groundable and version is not None and version.text.strip()
        )
        if node.status is NodeStatus.EMPTY_SHELL:
            content_status = "empty_shell"
        elif version is not None:
            content_status = "indexed"
        else:
            content_status = _content_status(node)

        return {
            "node": self._payload(node),
            "breadcrumb": [
                {"node_id": item.node_id, "title": item.title}
                for item in self.store.breadcrumb(node.node_id)
            ],
            "parent": (
                self._nav_summary(parent)
                if parent and can_navigate(parent, self.pii_guard) else None
            ),
            "children": [
                self._nav_summary(child) for child in children[:100]
            ],
            "siblings": [
                self._nav_summary(sibling) for sibling in siblings
            ],
            "documents": documents[:50],
            "links": links[:30],
            "collection": collection,
            "found_by": "browse",
            "body_available": body_available,
            "content_status": content_status,
            "groundable": groundable,
            "navigable": True,
            "normalized_query": normalized.text,
        }

    def _tool_navigate(self, args: dict) -> dict:
        node, found_by = self._resolve(args)
        self._navigable(node)
        parent = self.store.parent(node.node_id)
        children = [
            child for child in self.store.children(node.node_id)
            if can_navigate(child, self.pii_guard)
        ]
        siblings = [
            sibling for sibling in self.store.siblings(node.node_id)
            if can_navigate(sibling, self.pii_guard)
        ]
        return {
            "node": self._payload(node),
            "breadcrumb": [
                {"node_id": item.node_id, "title": item.title}
                for item in self.store.breadcrumb(node.node_id)
            ],
            "parent": (
                self._nav_summary(parent)
                if parent and can_navigate(parent, self.pii_guard) else None
            ),
            "children": [self._nav_summary(child) for child in children],
            "siblings": [self._nav_summary(sibling) for sibling in siblings],
            "found_by": found_by,
        }

    def _tool_open(self, args: dict) -> dict:
        node, found_by = self._resolve(args)
        self._navigable(node)
        if node.status is NodeStatus.EMPTY_SHELL:
            # A shell has no groundable body, but its metadata, documents and
            # links are still useful for navigation.
            links: list[dict] = []
            seen_links: set[str] = set()
            for edge in self.store.outgoing_edges(node.node_id):
                target = self.store.get_node(edge.dst_id)
                if target is None or not can_navigate(target, self.pii_guard):
                    continue
                links.append(
                    {
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
                )
                seen_links.add(target.url)
            version = self.store.page_version(node.node_id)
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
            documents = [
                self._document_summary(doc)
                for doc in self.store.documents(node.node_id)
                if can_ground(doc, self.pii_guard)
            ]
            return {
                "node": self._payload(node),
                "sections": [],
                "tables": [],
                "links": links,
                "documents": documents,
                "found_by": found_by,
                "body_available": False,
                "content_status": "empty_shell",
                "note": (
                    "This page is a navigation shell; its body is not "
                    "machine-readable. Use its links, siblings or documents."
                ),
            }
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
            if target is None or not can_navigate(target, self.pii_guard):
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
            if not can_ground(doc, self.pii_guard):
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
                    if doc_node is not None and can_ground(
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
        self._navigable(node)
        return {
            "node_id": node.node_id,
            "documents": [
                self._document_summary(doc)
                for doc in self.store.documents(node.node_id)
                if can_ground(doc, self.pii_guard)
            ],
        }

    def _tool_freshness(self, args: dict) -> dict:
        node = self._node(_str_arg(args, "node_id") or "")
        self._navigable(node)
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
        self._navigable(target)
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
