"""Persistence round-trip for the time-scoped answer cache."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.cache.answer_cache import CacheEntry
from tests.nga_helpers import build_store


def _entry(**overrides) -> CacheEntry:
    values = dict(
        cache_key="cache-key",
        status="verified",
        index_version="index-v1",
        policy_version="policy-v1",
        expires_at=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        normalized_query="exam schedule",
        language="en",
        channel="chat",
        coverage_start=date(2026, 10, 13),
        coverage_end=date(2026, 10, 23),
        coverage_unknown=False,
        programme="LL.M",
        event_dates=(date(2026, 10, 13), date(2026, 10, 23)),
        source_content_hash="abc",
        answer="The schedule is in October 2026.",
        evidence={"query": "exam schedule", "items": []},
        citations=({"node_id": "doc.1", "title": "Schedule"},),
        verification={"status": "verified", "verified": True},
        builder="notice",
    )
    values.update(overrides)
    return CacheEntry(**values)


def test_cache_entry_round_trips_through_sqlite(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    entry = _entry()

    store.put_answer_cache(
        entry.to_row(), created_at="2026-10-31T12:00:00+00:00"
    )
    loaded = CacheEntry.from_row(store.get_answer_cache(entry.cache_key))

    assert loaded.status == "verified"
    assert loaded.normalized_query == "exam schedule"
    assert loaded.coverage_start == date(2026, 10, 13)
    assert loaded.coverage_end == date(2026, 10, 23)
    assert loaded.event_dates == (date(2026, 10, 13), date(2026, 10, 23))
    assert loaded.programme == "LL.M"
    assert loaded.answer == entry.answer
    assert loaded.citations == entry.citations
    assert loaded.evidence == entry.evidence
    assert loaded.builder == "notice"


def test_touch_updates_last_hit(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    entry = _entry()
    store.put_answer_cache(
        entry.to_row(), created_at="2026-10-31T12:00:00+00:00"
    )

    store.touch_answer_cache(
        entry.cache_key, now="2026-10-31T12:05:00+00:00"
    )

    assert (
        store.get_answer_cache(entry.cache_key)["last_hit_at"]
        == "2026-10-31T12:05:00+00:00"
    )


def test_expired_cache_rows_are_purged(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    store.put_answer_cache(
        _entry(expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc)).to_row(),
        created_at="2026-09-30T12:00:00+00:00",
    )

    removed = store.purge_expired_answer_cache(
        now="2026-10-31T12:00:00+00:00"
    )

    assert removed == 1
    assert store.get_answer_cache("cache-key") is None
