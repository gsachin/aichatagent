"""Time-scoped answer caching primitives.

The DB/Redis backends are added in a later phase; this package currently
contains the pure key/validity logic so it can be tested without I/O.
"""

from app.cache.answer_cache import (
    CacheEntry,
    RequestContext,
    cache_is_valid,
    cache_key,
    cache_ttl_seconds,
    coverage_from_text,
    extract_event_dates,
)

__all__ = [
    "CacheEntry",
    "RequestContext",
    "cache_is_valid",
    "cache_key",
    "cache_ttl_seconds",
    "coverage_from_text",
    "extract_event_dates",
]
