"""Time-scope parsing and coverage tests (cache correctness)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.agent.time_scope import parse_time_scope, scope_covers


def test_month_year_scope():
    scope = parse_time_scope("exam schedule in Jan 2027")

    assert scope is not None
    assert scope.granularity == "month"
    assert scope.start == date(2027, 1, 1)
    assert scope.end == date(2027, 1, 31)


def test_december_does_not_cover_january():
    december = parse_time_scope("Dec 2026")
    january = parse_time_scope("Jan 2027")

    assert december is not None and january is not None
    assert not scope_covers(december.start, december.end, january)
    assert scope_covers(january.start, january.end, january)


def test_academic_session_bounds():
    scope = parse_time_scope("session 2026-27")

    assert scope is not None
    assert scope.granularity == "session"
    assert scope.start == date(2026, 7, 1)
    assert scope.end == date(2027, 6, 30)
    assert scope.session == "session 2026-27"


def test_year_range_is_treated_as_an_academic_session():
    scope = parse_time_scope("2024-2025")

    assert scope is not None
    assert scope.start == date(2024, 7, 1)
    assert scope.end == date(2025, 6, 30)


def test_multi_year_range():
    scope = parse_time_scope("2024-2026")

    assert scope is not None
    assert scope.start == date(2024, 7, 1)
    assert scope.end == date(2026, 6, 30)


def test_session_covers_a_month_inside_it():
    session = parse_time_scope("session 2026-27")
    january = parse_time_scope("Jan 2027")

    assert session is not None and january is not None
    assert scope_covers(session.start, session.end, january)


def test_month_does_not_cover_a_session():
    december = parse_time_scope("Dec 2026")
    session = parse_time_scope("session 2026-27")

    assert december is not None and session is not None
    assert not scope_covers(december.start, december.end, session)


def test_iso_date_is_a_day_scope_not_a_session():
    scope = parse_time_scope("exam on 2027-01-15")

    assert scope is not None
    assert scope.granularity == "day"
    assert scope.start == scope.end == date(2027, 1, 15)


def test_today_uses_ist_at_the_utc_day_boundary():
    # 18:30 UTC on 31 Oct is 00:00 IST on 1 Nov; "tomorrow" is 2 Nov.
    now = datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)
    today = parse_time_scope("today", now=now)
    tomorrow = parse_time_scope("tomorrow", now=now)

    assert today is not None and tomorrow is not None
    assert today.start == today.end == date(2026, 11, 1)
    assert tomorrow.start == tomorrow.end == date(2026, 11, 2)


def test_upcoming_is_open_ended_from_today():
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    scope = parse_time_scope("upcoming exam", now=now)

    assert scope is not None
    assert scope.granularity == "relative"
    assert scope.start == date(2026, 10, 31)
    assert scope.end is None


def test_invalid_date_is_not_guessed():
    assert parse_time_scope("31 Feb 2027") is None


def test_month_request_is_covered_by_an_event_inside_the_month():
    month = parse_time_scope("Oct 2026")

    assert month is not None
    assert scope_covers(date(2026, 10, 13), date(2026, 10, 23), month)
    assert scope_covers(date(2026, 10, 15), date(2026, 10, 15), month)


def test_upcoming_is_covered_by_a_future_schedule():
    upcoming = parse_time_scope(
        "upcoming",
        now=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert upcoming is not None
    assert scope_covers(date(2027, 1, 10), date(2027, 1, 20), upcoming)


def test_no_time_expression_returns_none():
    assert parse_time_scope("who is the vice chancellor") is None
