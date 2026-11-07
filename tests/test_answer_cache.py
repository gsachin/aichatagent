"""Cache key and time-scope validity tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.agent.time_scope import parse_time_scope
from app.cache.answer_cache import (
    CacheEntry,
    RequestContext,
    cache_is_valid,
    cache_key,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
INDEX = "index-v1"
POLICY = "policy-v1"


def _entry(**overrides) -> CacheEntry:
    values = dict(
        cache_key="k",
        status="verified",
        index_version=INDEX,
        policy_version=POLICY,
        expires_at=datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
        coverage_start=date(2026, 12, 1),
        coverage_end=date(2026, 12, 31),
    )
    values.update(overrides)
    return CacheEntry(**values)


def _request(text: str | None, **overrides) -> RequestContext:
    values = dict(
        time_scope=parse_time_scope(text) if text else None,
    )
    values.update(overrides)
    return RequestContext(**values)


def test_december_entry_does_not_answer_a_january_question():
    entry = _entry()
    request = _request("Jan 2027")

    assert not cache_is_valid(
        entry, request, now=NOW, index_version=INDEX, policy_version=POLICY
    )


def test_january_entry_answers_a_january_question():
    entry = _entry(
        coverage_start=date(2027, 1, 1),
        coverage_end=date(2027, 1, 31),
    )

    assert cache_is_valid(
        entry,
        _request("Jan 2027"),
        now=NOW,
        index_version=INDEX,
        policy_version=POLICY,
    )


def test_expired_entry_is_invalid():
    entry = _entry(
        expires_at=datetime(2026, 10, 31, 11, 59, tzinfo=timezone.utc)
    )

    assert not cache_is_valid(
        entry, _request("Dec 2026"), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_index_version_mismatch_is_invalid():
    assert not cache_is_valid(
        _entry(), _request("Dec 2026"), now=NOW,
        index_version="index-v2", policy_version=POLICY,
    )


def test_policy_version_mismatch_is_invalid():
    assert not cache_is_valid(
        _entry(), _request("Dec 2026"), now=NOW,
        index_version=INDEX, policy_version="policy-v2",
    )


def test_programme_mismatch_is_invalid():
    entry = _entry(programme="M.Com")
    request = _request("Dec 2026", programme="LL.M")

    assert not cache_is_valid(
        entry, request, now=NOW, index_version=INDEX, policy_version=POLICY
    )


def test_unknown_coverage_is_invalid_for_a_scoped_request():
    entry = _entry(coverage_unknown=True)

    assert not cache_is_valid(
        entry, _request("Dec 2026"), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_no_time_constraint_can_use_an_unknown_coverage_entry():
    entry = _entry(coverage_unknown=True)

    assert cache_is_valid(
        entry, RequestContext(), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_session_coverage_answers_a_month_inside_it():
    entry = _entry(
        coverage_start=date(2026, 7, 1),
        coverage_end=date(2027, 6, 30),
    )

    assert cache_is_valid(
        entry, _request("Jan 2027"), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_past_event_is_invalid_for_an_upcoming_request():
    entry = _entry(
        coverage_start=date(2026, 7, 1),
        coverage_end=date(2027, 6, 30),
        event_dates=(date(2026, 10, 1),),
    )

    assert not cache_is_valid(
        entry, _request("upcoming", after_event=True), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_future_event_is_valid_for_an_upcoming_request():
    entry = _entry(
        coverage_start=date(2026, 7, 1),
        coverage_end=date(2027, 6, 30),
        event_dates=(date(2027, 1, 15),),
    )

    assert cache_is_valid(
        entry, _request("upcoming", after_event=True), now=NOW,
        index_version=INDEX, policy_version=POLICY,
    )


def test_cache_key_changes_with_the_time_scope():
    first = cache_key(
        normalized_query="exam schedule", language="en", channel="chat",
        time_scope=parse_time_scope("Dec 2026"),
        index_version=INDEX, policy_version=POLICY,
    )
    second = cache_key(
        normalized_query="exam schedule", language="en", channel="chat",
        time_scope=parse_time_scope("Jan 2027"),
        index_version=INDEX, policy_version=POLICY,
    )

    assert first != second
    assert len(first) == 64


def test_cache_key_is_stable_for_the_same_scope():
    args = dict(
        normalized_query="exam schedule", language="en", channel="chat",
        time_scope=parse_time_scope("Jan 2027"),
        index_version=INDEX, policy_version=POLICY,
    )

    assert cache_key(**args) == cache_key(**args)
