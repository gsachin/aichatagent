"""Background content refresh for the NGA graph.

Fetches due HTML pages, extracts their main text, stores a page version,
updates the body FTS index and records freshness state. It is never called
from the live answer path. A failure is recorded on the node but never
removes the last good content.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.sitegraph.extract import extract_html
from app.sitegraph.fetch import FetchError, SiteFetcher, soft_404_reason
from app.sitegraph.models import NodeStatus
from app.sitegraph.store import GraphStore

logger = logging.getLogger("sitegraph.refresh")


@dataclass(frozen=True)
class RefreshOptions:
    ttl_s: int = 86_400
    limit: int = 10
    force: bool = False
    min_chars: int = 120
    node_ids: tuple[str, ...] = ()
    section: str | None = None
    now: str | None = None
    failure_backoff_s: int = 300


@dataclass
class RefreshStats:
    checked: int = 0
    fetched: int = 0
    changed: int = 0
    unchanged: int = 0
    not_modified: int = 0
    empty: int = 0
    soft_404: int = 0
    errors: int = 0
    documents: int = 0
    canonical_duplicates: int = 0
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "fetched": self.fetched,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "not_modified": self.not_modified,
            "empty": self.empty,
            "soft_404": self.soft_404,
            "errors": self.errors,
            "documents": self.documents,
            "canonical_duplicates": self.canonical_duplicates,
            "details": self.details,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_seconds(iso_timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=max(0, int(seconds)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def dedupe_canonical(store: GraphStore) -> int:
    """Mark identical-title, identical-content pages as duplicates.

    The best node is the shallowest, then the shortest path, then the node id.
    Returns the number of duplicate nodes hidden from search.
    """
    store.reset_canonical_ids()
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for node in store.load_nodes():
        if node.content_hash and node.status is NodeStatus.OK:
            groups[(node.title.strip().lower(), node.content_hash)].append(node)
    hidden = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda node: (node.depth, len(node.canonical_path), node.node_id))
        canonical = group[0]
        store.set_canonical_group(
            [node.node_id for node in group], canonical.node_id
        )
        hidden += len(group) - 1
    return hidden


def refresh_graph(
    store: GraphStore,
    fetcher: SiteFetcher,
    options: RefreshOptions,
) -> RefreshStats:
    """Refresh the nodes that are due, then recompute canonical duplicates."""
    now = options.now or _now()
    due = store.due_nodes(
        limit=options.limit,
        force=options.force,
        node_ids=options.node_ids or None,
        section=options.section,
        now=now,
    )
    stats = RefreshStats(checked=len(due))
    next_due = _add_seconds(now, options.ttl_s)

    for node in due:
        state = store.fetch_state(node.node_id) or {}
        etag = str(state.get("etag") or "")
        last_modified = str(state.get("last_modified") or "")
        try:
            result = fetcher.fetch(
                node.url, etag=etag, last_modified=last_modified
            )
            if result.not_modified:
                store.record_not_modified(
                    node.node_id,
                    checked_at=now,
                    next_due_at=next_due,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    http_status=result.status,
                )
                stats.not_modified += 1
                stats.details.append(
                    {"node_id": node.node_id, "status": "not_modified"}
                )
                continue

            reason = soft_404_reason(result.text, result.content_type)
            if reason:
                store.record_status(
                    node.node_id,
                    status=NodeStatus.SOFT_404,
                    checked_at=now,
                    next_due_at=next_due,
                    http_status=result.status,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    error=reason,
                )
                stats.soft_404 += 1
                stats.details.append(
                    {"node_id": node.node_id, "status": "soft_404"}
                )
                continue

            extracted = extract_html(result.text, result.final_url or node.url)
            if extracted.char_count < max(1, int(options.min_chars)):
                store.record_status(
                    node.node_id,
                    status=NodeStatus.EMPTY_SHELL,
                    checked_at=now,
                    next_due_at=next_due,
                    http_status=result.status,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    error=f"empty shell ({extracted.char_count} chars)",
                )
                stats.empty += 1
                stats.details.append(
                    {"node_id": node.node_id, "status": "empty_shell"}
                )
                continue

            previous = store.page_version(node.node_id)
            stats.fetched += 1
            if previous is not None and previous.content_hash == extracted.content_hash:
                # A 200 with the same content is a revalidation, not a new
                # version. Do not grow nga_page_version on every refresh.
                store.record_not_modified(
                    node.node_id,
                    checked_at=now,
                    next_due_at=next_due,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    http_status=result.status,
                )
                stats.unchanged += 1
                stats.details.append(
                    {
                        "node_id": node.node_id,
                        "status": "unchanged",
                        "version": previous.version,
                        "chars": extracted.char_count,
                    }
                )
                continue

            version = store.record_page(
                node.node_id,
                text=extracted.text,
                markdown=extracted.markdown,
                tables=extracted.tables,
                sections=extracted.sections,
                links=extracted.links,
                documents=extracted.documents,
                language=extracted.language,
                content_hash=extracted.content_hash,
                fetched_at=now,
                verified_at=now,
                size_bytes=len(result.body),
                etag=result.etag,
                last_modified=result.last_modified,
                http_status=result.status,
                next_due_at=next_due,
            )
            stats.changed += 1
            stats.documents += len(extracted.documents)
            stats.details.append(
                {
                    "node_id": node.node_id,
                    "status": "changed",
                    "version": version,
                    "chars": extracted.char_count,
                    "documents": len(extracted.documents),
                }
            )
        except FetchError as exc:
            if "soft_404" in str(exc):
                store.record_status(
                    node.node_id,
                    status=NodeStatus.SOFT_404,
                    checked_at=now,
                    next_due_at=next_due,
                    http_status=exc.status,
                    error=str(exc),
                )
                stats.soft_404 += 1
                stats.details.append(
                    {"node_id": node.node_id, "status": "soft_404"}
                )
            else:
                failures = int(state.get("failure_count") or 0) + 1
                backoff = min(
                    options.failure_backoff_s * (2 ** min(failures, 3)),
                    900,
                )
                store.record_fetch_failure(
                    node.node_id,
                    checked_at=now,
                    next_due_at=_add_seconds(now, backoff),
                    http_status=exc.status,
                    error=str(exc),
                )
                stats.errors += 1
                stats.details.append(
                    {
                        "node_id": node.node_id,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one page must not stop a run
            logger.exception("refresh failed for %s", node.url)
            failures = int(state.get("failure_count") or 0) + 1
            backoff = min(
                options.failure_backoff_s * (2 ** min(failures, 3)),
                900,
            )
            store.record_fetch_failure(
                node.node_id,
                checked_at=now,
                next_due_at=_add_seconds(now, backoff),
                error=str(exc),
            )
            stats.errors += 1
            stats.details.append(
                {
                    "node_id": node.node_id,
                    "status": "error",
                    "error": str(exc),
                }
            )

    stats.canonical_duplicates = dedupe_canonical(store)
    return stats
