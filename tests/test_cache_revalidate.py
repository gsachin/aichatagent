"""Conditional cache revalidation tests (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.agent.time_scope import parse_time_scope
from app.cache.answer_cache import CacheEntry, RequestContext
from app.cache.revalidate import (
    revalidate_entry,
    should_revalidate,
    source_urls,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


@dataclass
class Result:
    status: int = 200
    not_modified: bool = False


class FakeFetcher:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or Result()
        self.error = error
        self.calls: list[str] = []

    def fetch(self, url, **_kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.result

    def fetch_document(self, url, **_kwargs):
        return self.fetch(url)


def _entry(**overrides) -> CacheEntry:
    values = dict(
        cache_key="k",
        status="verified",
        index_version="i",
        policy_version="p",
        expires_at=datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc),
        coverage_start=date(2026, 10, 13),
        coverage_end=date(2026, 10, 23),
        citations=(
            {"url": "https://dspmuranchi.ac.in/schedule.pdf"},
            {"url": "https://dspmuranchi.ac.in/schedule.pdf"},
            {"url": "https://dspmuranchi.ac.in/other.pdf"},
        ),
        answer="Schedule",
        evidence={},
    )
    values.update(overrides)
    return CacheEntry(**values)


def test_source_urls_are_deduplicated_and_limited():
    urls = source_urls(_entry(), limit=2)

    assert urls == (
        "https://dspmuranchi.ac.in/schedule.pdf",
        "https://dspmuranchi.ac.in/other.pdf",
    )


def test_304_keeps_the_cache_valid_and_extends_expiry():
    result = revalidate_entry(
        _entry(),
        FakeFetcher(Result(status=304, not_modified=True)),
        now=NOW,
        ttl_seconds=3600,
    )

    assert result.valid is True
    assert result.checked is True
    assert result.reason == "not_modified"
    assert result.expires_at == datetime(
        2026, 10, 31, 13, 0, tzinfo=timezone.utc
    )


def test_200_invalidates_the_cache():
    result = revalidate_entry(
        _entry(),
        FakeFetcher(Result(status=200)),
        now=NOW,
        ttl_seconds=3600,
    )

    assert result.valid is False
    assert result.reason == "source_changed"


def test_fetch_failure_is_not_treated_as_fresh():
    result = revalidate_entry(
        _entry(),
        FakeFetcher(error=TimeoutError()),
        now=NOW,
        ttl_seconds=3600,
    )

    assert result.valid is False
    assert result.reason == "fetch_failed:TimeoutError"


def test_entry_without_a_source_is_not_revalidated():
    entry = _entry(citations=(), evidence={})

    result = revalidate_entry(
        entry,
        FakeFetcher(Result(status=200)),
        now=NOW,
        ttl_seconds=3600,
    )

    assert result.valid is False
    assert result.checked is False
    assert result.reason == "no_source"


def test_time_scoped_requests_always_revalidate():
    request = RequestContext(time_scope=parse_time_scope("Jan 2027"))
    entry = _entry(
        expires_at=datetime(2026, 11, 30, tzinfo=timezone.utc)
    )

    assert should_revalidate(entry, request, now=NOW) is True


def test_far_expiry_is_not_revalidated_for_an_untimed_request():
    entry = _entry(
        expires_at=datetime(2026, 11, 30, tzinfo=timezone.utc)
    )

    assert should_revalidate(
        entry, RequestContext(), now=NOW, window_s=3600
    ) is False
