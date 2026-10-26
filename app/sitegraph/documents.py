"""Background PDF indexing for the NGA navigation graph (Phase 11).

Documents linked from indexed pages are fetched and extracted only in the
background worker. The live answer path reads the stored text; it never
fetches or OCRs a PDF. OCR text is stored with ``needs_review`` and is not
indexed until a reviewer approves it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.pii import scan_text
from app.sitegraph.fetch import FetchError
from app.sitegraph.models import DocumentStatus, SiteNode
from app.sitegraph.policy import is_answerable, title_pii_class
from app.siteindex.extract import extract_pdf
from app.sitegraph.store import GraphStore

logger = logging.getLogger("sitegraph.documents")


@dataclass(frozen=True)
class DocumentOptions:
    ttl_s: int = 604_800
    limit: int = 5
    force: bool = False
    node_ids: tuple[str, ...] = ()
    max_bytes: int = 10_000_000
    max_pages: int = 40
    min_chars: int = 80
    ocr: bool = True
    ocr_max_pages: int = 8
    ocr_langs: tuple[str, ...] = ()
    now: str | None = None
    failure_backoff_s: int = 3_600
    allowed_hosts: tuple[str, ...] = ()
    blocked_path_tokens: tuple[str, ...] = ()
    discover: bool = True


@dataclass
class DocumentStats:
    checked: int = 0
    discovered: int = 0
    fetched: int = 0
    indexed: int = 0
    unchanged: int = 0
    not_modified: int = 0
    needs_review: int = 0
    empty: int = 0
    errors: int = 0
    blocked: int = 0
    pages: int = 0
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "discovered": self.discovered,
            "fetched": self.fetched,
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "not_modified": self.not_modified,
            "needs_review": self.needs_review,
            "empty": self.empty,
            "errors": self.errors,
            "blocked": self.blocked,
            "pages": self.pages,
            "details": self.details,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_seconds(iso_timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=max(0, int(seconds)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _pdf_url(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme.lower() != "https":
        return False
    if allowed_hosts and (parsed.hostname or "").lower() not in allowed_hosts:
        return False
    return parsed.path.lower().endswith(".pdf")


def _blocked_url(url: str, title: str, tokens: tuple[str, ...]) -> bool:
    if title_pii_class(title) is not None:
        return True
    if not tokens:
        return False
    words = set(re.findall(r"[a-z0-9]+", (url or "").lower()))
    return bool(words & set(tokens))


def discover_documents(
    store: GraphStore,
    *,
    allowed_hosts: tuple[str, ...] = (),
    blocked_path_tokens: tuple[str, ...] = (),
    pii_guard: bool = True,
) -> int:
    """Ensure a document node for every PDF linked from an indexed page."""
    parents: dict[str, SiteNode | None] = {}
    seen: set[tuple[str, str]] = set()
    linked = 0
    for page_id, documents in store.latest_document_links():
        if page_id not in parents:
            parents[page_id] = store.get_node(page_id)
        parent = parents[page_id]
        if parent is None or not is_answerable(parent, pii_guard):
            continue
        for doc in documents:
            url = str(doc.get("url") or "").strip()
            title = str(doc.get("title") or "").strip()
            key = (page_id, url)
            if not url or key in seen:
                continue
            if not _pdf_url(url, allowed_hosts):
                continue
            if pii_guard and _blocked_url(url, title, blocked_path_tokens):
                continue
            seen.add(key)
            store.ensure_document_node(url=url, title=title, parent=parent)
            linked += 1
    return linked


def refresh_documents(
    store: GraphStore,
    fetcher: object,
    options: DocumentOptions,
    *,
    pii_guard: bool = True,
) -> DocumentStats:
    """Index due PDFs. A failure never removes previously indexed text."""
    now = options.now or _now()
    next_due = _add_seconds(now, options.ttl_s)
    stats = DocumentStats()
    if options.discover:
        stats.discovered = discover_documents(
            store,
            allowed_hosts=options.allowed_hosts,
            blocked_path_tokens=options.blocked_path_tokens,
            pii_guard=pii_guard,
        )

    due = store.due_documents(
        limit=options.limit,
        force=options.force,
        node_ids=options.node_ids or None,
        now=now,
    )
    stats.checked = len(due)

    for node in due:
        stats = _refresh_one(
            store, fetcher, options, pii_guard, stats, node, now, next_due
        )
    return stats


def _refresh_one(
    store: GraphStore,
    fetcher: object,
    options: DocumentOptions,
    pii_guard: bool,
    stats: DocumentStats,
    node: SiteNode,
    now: str,
    next_due: str,
) -> DocumentStats:
    store.ensure_document_row(node)
    state = store.fetch_state(node.node_id) or {}
    etag = str(state.get("etag") or "")
    last_modified = str(state.get("last_modified") or "")
    fetch_document = getattr(fetcher, "fetch_document", None)

    try:
        if callable(fetch_document):
            result = fetch_document(
                node.url,
                etag=etag,
                last_modified=last_modified,
                max_bytes=options.max_bytes,
            )
        else:  # pragma: no cover - test fakes may expose only fetch()
            result = fetcher.fetch(node.url, etag=etag, last_modified=last_modified)
    except FetchError as exc:
        stats.errors += 1
        failures = int(state.get("failure_count") or 0) + 1
        backoff = min(
            options.failure_backoff_s * (2 ** min(failures, 3)), 86_400
        )
        store.record_fetch_failure(
            node.node_id,
            checked_at=now,
            next_due_at=_add_seconds(now, backoff),
            http_status=exc.status,
            error=str(exc),
        )
        store.set_document_error(node.node_id, str(exc))
        stats.details.append(
            {"node_id": node.node_id, "status": "error", "error": str(exc)}
        )
        return stats
    except Exception as exc:  # noqa: BLE001 - one document must not stop a run
        logger.exception("document fetch failed for %s", node.url)
        stats.errors += 1
        store.record_fetch_failure(
            node.node_id,
            checked_at=now,
            next_due_at=_add_seconds(now, options.failure_backoff_s),
            error=str(exc),
        )
        store.set_document_error(node.node_id, str(exc))
        stats.details.append(
            {"node_id": node.node_id, "status": "error", "error": str(exc)}
        )
        return stats

    if getattr(result, "not_modified", False):
        store.record_not_modified(
            node.node_id,
            checked_at=now,
            next_due_at=next_due,
            etag=getattr(result, "etag", "") or etag,
            last_modified=getattr(result, "last_modified", "") or last_modified,
            http_status=getattr(result, "status", 304),
        )
        stats.not_modified += 1
        stats.details.append({"node_id": node.node_id, "status": "not_modified"})
        return stats

    content_type = str(getattr(result, "content_type", "") or "").lower()
    body = bytes(getattr(result, "body", b"") or b"")
    if "pdf" not in content_type and "octet-stream" not in content_type:
        error = f"unsupported document content type {content_type!r}"
        store.record_fetch_failure(
            node.node_id,
            checked_at=now,
            next_due_at=_add_seconds(now, options.failure_backoff_s),
            error=error,
        )
        store.set_document_error(node.node_id, error)
        stats.errors += 1
        stats.details.append(
            {"node_id": node.node_id, "status": "error", "error": error}
        )
        return stats
    if b"%PDF-" not in body[:1024]:
        error = "response is not a PDF"
        store.record_fetch_failure(
            node.node_id,
            checked_at=now,
            next_due_at=_add_seconds(now, options.failure_backoff_s),
            error=error,
        )
        store.set_document_error(node.node_id, error)
        stats.errors += 1
        stats.details.append(
            {"node_id": node.node_id, "status": "error", "error": error}
        )
        return stats

    stats.fetched += 1
    content_hash = hashlib.sha256(body).hexdigest()
    previous = store.document(node.node_id)
    if (
        previous is not None
        and previous.content_hash == content_hash
        and previous.extraction_status in (
            DocumentStatus.DONE.value,
            DocumentStatus.EMPTY.value,
        )
    ):
        store.record_document_not_modified(
            node.node_id,
            checked_at=now,
            next_due_at=next_due,
            etag=getattr(result, "etag", "") or etag,
            last_modified=getattr(result, "last_modified", "") or last_modified,
            http_status=getattr(result, "status", 200),
            verified_at=now if previous.answerable else None,
        )
        stats.unchanged += 1
        stats.details.append(
            {
                "node_id": node.node_id,
                "status": "unchanged",
                "needs_review": previous.needs_review,
            }
        )
        return stats

    try:
        extracted = extract_pdf(
            body,
            ocr=options.ocr,
            max_ocr_pages=options.ocr_max_pages,
            ocr_langs=options.ocr_langs or None,
            max_pages=options.max_pages,
        )
    except Exception as exc:  # noqa: BLE001 - one document must not stop a run
        logger.exception("document extraction failed for %s", node.url)
        store.record_fetch_failure(
            node.node_id,
            checked_at=now,
            next_due_at=_add_seconds(now, options.failure_backoff_s),
            error=str(exc),
        )
        store.set_document_error(node.node_id, str(exc))
        stats.errors += 1
        stats.details.append(
            {"node_id": node.node_id, "status": "error", "error": str(exc)}
        )
        return stats
    stats.pages += extracted.pages
    text = (extracted.text or "").strip()
    char_count = len(re.sub(r"\W", "", text))
    mime = content_type.split(";", 1)[0].strip() or "application/pdf"

    if pii_guard:
        scan = scan_text(text)
        if scan.flagged:
            store.record_document_blocked(
                node.node_id,
                url=node.url,
                error=f"pii_blocked: {scan.reason_text}",
                content_hash=content_hash,
                size_bytes=len(body),
                fetched_at=now,
                pages=extracted.pages,
            )
            stats.blocked += 1
            stats.details.append(
                {
                    "node_id": node.node_id,
                    "status": "blocked",
                    "reasons": list(scan.reasons),
                }
            )
            return stats

    needs_review = bool(extracted.needs_review)
    if char_count < max(1, int(options.min_chars)):
        status = DocumentStatus.EMPTY
        verified_at = None
    else:
        status = DocumentStatus.DONE
        verified_at = None if needs_review else now

    store.record_document(
        node.node_id,
        url=node.url,
        mime=mime,
        pages=extracted.pages,
        status=status,
        text=text,
        tables=extracted.tables,
        ocr=extracted.ocr,
        ocr_lang=extracted.ocr_lang,
        ocr_quality=extracted.ocr_quality,
        needs_review=needs_review,
        pii_flagged=False,
        content_hash=content_hash,
        size_bytes=len(body),
        fetched_at=now,
        verified_at=verified_at,
        error=extracted.error,
        etag=getattr(result, "etag", "") or etag,
        last_modified=getattr(result, "last_modified", "") or last_modified,
        http_status=getattr(result, "status", 200),
        next_due_at=next_due,
    )

    if status is DocumentStatus.EMPTY:
        stats.empty += 1
    elif needs_review:
        stats.needs_review += 1
    else:
        stats.indexed += 1
    stats.details.append(
        {
            "node_id": node.node_id,
            "status": status.value,
            "pages": extracted.pages,
            "chars": char_count,
            "ocr": extracted.ocr,
            "needs_review": needs_review,
        }
    )
    return stats
