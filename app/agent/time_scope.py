"""Time-scope parsing and coverage checks for cache validity.

A cached schedule for December 2026 must never answer a January 2027
question, even if the cache TTL has not expired. This module turns the
user's wording (month/year/session/relative) into a typed ``TimeScope`` and
checks whether an evidence window actually covers it.

Dates are interpreted in ``Asia/Kolkata`` for relative terms such as
``today``/``tomorrow``/``upcoming``. All returned boundaries are inclusive.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_DAY_MONTH_YEAR_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})[a-z]*\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b({_MONTH_NAMES})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(
    rf"\b({_MONTH_NAMES})[a-z]*\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_SESSION_RE = re.compile(r"\b(20\d{2})\s*-\s*(\d{2}|20\d{2})\b")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_RELATIVE_RE = re.compile(
    r"\b(today|tomorrow|yesterday|upcoming|next\s+month|this\s+month|"
    r"next\s+week|this\s+week)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TimeScope:
    raw: str
    start: date | None
    end: date | None
    granularity: str  # day|month|year|session|range|relative|unknown
    label: str
    session: str | None = None
    relative: str | None = None


def _ist_today(now: datetime | None = None) -> date:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).date()


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _session_bounds(start_year: int, end_year: int) -> tuple[date, date]:
    """Academic session: July 1 of the first year to June 30 of the last."""
    return date(start_year, 7, 1), date(end_year, 6, 30)


def parse_time_scope(
    text: str,
    *,
    now: datetime | None = None,
) -> TimeScope | None:
    """Return the time period mentioned in *text*, or ``None``.

    ``None`` means the question is not time-bound; callers should not block a
    cache hit merely because a coverage window is absent. Callers that need a
    strict answer for a specific period must pass that period explicitly.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return None
    value = raw.lower().replace("–", "-").replace("—", "-")
    today = _ist_today(now)

    # An explicit ISO date is a day scope and must not be read as a session.
    match = _ISO_DATE_RE.search(value)
    if match:
        try:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        return TimeScope(raw, start, start, "day", raw, relative=None)

    match = _DAY_MONTH_YEAR_RE.search(value)
    if match:
        day, month, year = int(match.group(1)), _MONTHS[match.group(2).lower()], int(match.group(3))
        try:
            start = date(year, month, day)
        except ValueError:
            return None
        return TimeScope(raw, start, start, "day", raw)

    match = _MONTH_DAY_YEAR_RE.search(value)
    if match:
        month, day, year = _MONTHS[match.group(1).lower()], int(match.group(2)), int(match.group(3))
        try:
            start = date(year, month, day)
        except ValueError:
            return None
        return TimeScope(raw, start, start, "day", raw)

    match = _MONTH_YEAR_RE.search(value)
    if match:
        month, year = _MONTHS[match.group(1).lower()], int(match.group(2))
        start, end = _month_bounds(year, month)
        return TimeScope(raw, start, end, "month", raw)

    match = _SESSION_RE.search(value)
    if match:
        first = int(match.group(1))
        second_raw = match.group(2)
        second = int(second_raw)
        if len(second_raw) == 2:
            second += (first // 100) * 100
            if second < first:
                second += 100
        if second < first:
            return None
        start, end = _session_bounds(first, second)
        label = f"session {first}-{str(second)[-2:]}"
        return TimeScope(raw, start, end, "session", label, session=label)

    match = _YEAR_RE.search(value)
    if match:
        year = int(match.group(1))
        start, end = _year_bounds(year)
        return TimeScope(raw, start, end, "year", raw)

    match = _RELATIVE_RE.search(value)
    if match:
        term = re.sub(r"\s+", " ", match.group(1).lower())
        if term == "today":
            return TimeScope(raw, today, today, "relative", raw, relative=term)
        if term == "tomorrow":
            day = today + timedelta(days=1)
            return TimeScope(raw, day, day, "relative", raw, relative=term)
        if term == "yesterday":
            day = today - timedelta(days=1)
            return TimeScope(raw, day, day, "relative", raw, relative=term)
        if term == "upcoming":
            return TimeScope(raw, today, None, "relative", raw, relative=term)
        if term in ("next month", "this month"):
            year = today.year
            month = today.month + (1 if term == "next month" else 0)
            if month > 12:
                year += 1
                month = 1
            start, end = _month_bounds(year, month)
            return TimeScope(raw, start, end, "relative", raw, relative=term)
        if term in ("next week", "this week"):
            start = today + timedelta(days=7 if term == "next week" else 0)
            end = start + timedelta(days=6)
            return TimeScope(raw, start, end, "relative", raw, relative=term)

    return None


def scope_covers(
    coverage_start: date | None,
    coverage_end: date | None,
    request: TimeScope | None,
) -> bool:
    """True when the evidence window fully covers the requested period."""
    if request is None or request.start is None:
        return True
    if coverage_start is None or coverage_end is None:
        return False
    if request.granularity == "relative" and request.relative == "upcoming":
        # A future schedule answers "upcoming" even if it starts later than
        # today. The evidence must still contain a date at or after today.
        return coverage_end >= request.start
    if request.granularity == "day" or (
        request.granularity == "relative" and request.end == request.start
    ):
        return coverage_start <= request.start <= coverage_end
    if request.granularity == "month":
        # An event anywhere in the requested month covers that month. This
        # still rejects Dec 2026 for Jan 2027 (no overlap at all).
        end = request.end or request.start
        return coverage_start <= end and coverage_end >= request.start
    # Session/year/range: require the evidence to cover the whole period.
    if coverage_start > request.start:
        return False
    if request.end is None:
        return coverage_end >= request.start
    return coverage_end >= request.end


__all__ = ["IST", "TimeScope", "parse_time_scope", "scope_covers"]
