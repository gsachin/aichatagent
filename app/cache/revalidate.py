"""Conditional revalidation for cached, time-sensitive answers.

A cache entry may be fresh by TTL but its source may have changed. Before
serving a time-sensitive or near-expiry entry, the answer path can issue a
conditional GET (ETag / Last-Modified). A 304 extends the entry; a 200
invalidates it so retrieval/refresh can run. A fetch failure is not treated as
freshness.

The fetcher is injected so this module is testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.cache.answer_cache import CacheEntry, RequestContext, _aware


@dataclass(frozen=True)
class RevalidationResult:
    valid: bool
    checked: bool
    reason: str
    expires_at: datetime | None = None


def source_urls(entry: CacheEntry, *, limit: int = 1) -> tuple[str, ...]:
    """Exact URLs that grounded the cached answer, in citation order."""
    urls: list[str] = []
    for citation in entry.citations or ():
        url = str(citation.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            urls.append(url)
    if not urls and entry.evidence:
        for item in entry.evidence.get("items") or ():
            url = str(item.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                urls.append(url)
    return tuple(dict.fromkeys(urls))[: max(0, int(limit))]


def should_revalidate(
    entry: CacheEntry,
    request: RequestContext,
    *,
    now: datetime,
    window_s: int = 3600,
) -> bool:
    if request.time_scope is not None:
        return True
    return _aware(entry.expires_at) - _aware(now) <= timedelta(
        seconds=max(0, int(window_s))
    )


def revalidate_entry(
    entry: CacheEntry,
    fetcher,
    *,
    now: datetime,
    ttl_seconds: int,
    state_lookup: Callable[[str], dict] | None = None,
    max_urls: int = 1,
    max_bytes: int | None = None,
) -> RevalidationResult:
    """Return a validity decision after conditional GETs.

    ``valid=True, checked=True`` means every checked source returned 304.
    ``valid=False, checked=False`` means there was no source URL to check: an
    answer without a source cannot be revalidated, so it must not be served as
    fresh. Any 200/error invalidates the entry.
    """
    urls = source_urls(entry, limit=max_urls)
    if not urls:
        return RevalidationResult(False, False, "no_source")

    for url in urls:
        state = state_lookup(url) if state_lookup is not None else {}
        etag = str((state or {}).get("etag") or "")
        last_modified = str((state or {}).get("last_modified") or "")
        try:
            if url.lower().endswith(".pdf"):
                result = fetcher.fetch_document(
                    url,
                    etag=etag,
                    last_modified=last_modified,
                    max_bytes=max_bytes,
                )
            else:
                result = fetcher.fetch(
                    url, etag=etag, last_modified=last_modified
                )
        except Exception as exc:  # noqa: BLE001 - report, never serve stale
            return RevalidationResult(
                False, True, f"fetch_failed:{type(exc).__name__}"
            )

        if getattr(result, "not_modified", False):
            continue
        status = getattr(result, "status", None)
        if status == 200:
            return RevalidationResult(False, True, "source_changed")
        return RevalidationResult(False, True, f"http_{status}")

    return RevalidationResult(
        True,
        True,
        "not_modified",
        expires_at=_aware(now) + timedelta(seconds=max(0, int(ttl_seconds))),
    )


__all__ = [
    "RevalidationResult",
    "revalidate_entry",
    "should_revalidate",
    "source_urls",
]
