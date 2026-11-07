"""Pure cache-key and validity rules for time-scoped answers.

The rule is deliberately strict: a fresh TTL is not enough. The entry must
also cover the requested time period and entities, match the current index
and policy versions, and (for "upcoming" questions) still have a future
event date. A December 2026 schedule therefore cannot answer a January 2027
question, even if the entry was written one minute ago.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.time_scope import TimeScope

_CACHE_STATUSES = frozenset({"verified", "rewritten", "not_found"})


@dataclass(frozen=True)
class RequestContext:
    time_scope: TimeScope | None = None
    programme: str | None = None
    exam_type: str | None = None
    mode: str | None = None
    after_event: bool = False


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    status: str
    index_version: str
    policy_version: str
    expires_at: datetime
    normalized_query: str = ""
    language: str = "en"
    channel: str = "chat"
    coverage_start: date | None = None
    coverage_end: date | None = None
    coverage_unknown: bool = False
    programme: str | None = None
    exam_type: str | None = None
    mode: str | None = None
    event_dates: tuple[date, ...] = ()
    evidence_valid_until: date | None = None
    source_content_hash: str = ""
    answer: str = ""
    evidence: dict | None = None
    citations: tuple[dict, ...] = ()
    verification: dict | None = None
    negative: bool = False
    #: The deterministic builder that produced the cached answer, if any.
    #: Cached provenance must survive so a critical-fact case can still prove
    #: that the model was not the source on a repeat turn.
    builder: str = ""

    def to_row(self) -> dict:
        return {
            "cache_key": self.cache_key,
            "normalized_query": self.normalized_query,
            "language": self.language,
            "channel": self.channel,
            "programme": self.programme or "",
            "exam_type": self.exam_type or "",
            "mode": self.mode or "",
            "coverage_start": (
                self.coverage_start.isoformat() if self.coverage_start else ""
            ),
            "coverage_end": (
                self.coverage_end.isoformat() if self.coverage_end else ""
            ),
            "coverage_unknown": 1 if self.coverage_unknown else 0,
            "event_dates": json.dumps(
                [value.isoformat() for value in self.event_dates]
            ),
            "source_content_hash": self.source_content_hash,
            "index_version": self.index_version,
            "policy_version": self.policy_version,
            "status": self.status,
            "answer": self.answer,
            "evidence": json.dumps(self.evidence or {}, ensure_ascii=False),
            "citations": json.dumps(
                list(self.citations), ensure_ascii=False
            ),
            "verification": json.dumps(
                self.verification or {}, ensure_ascii=False
            ),
            "negative": 1 if self.negative else 0,
            "builder": self.builder,
            "expires_at": _aware(self.expires_at).isoformat(),
        }

    @classmethod
    def from_row(cls, row) -> "CacheEntry":
        def _date(value) -> date | None:
            return date.fromisoformat(value) if value else None

        def _json(value, default):
            try:
                return json.loads(value) if value else default
            except (TypeError, ValueError):
                return default

        expires = row["expires_at"]
        if expires and expires.endswith("Z"):
            expires = expires[:-1] + "+00:00"
        event_dates = _json(row.get("event_dates"), [])
        return cls(
            cache_key=row["cache_key"],
            status=row["status"],
            index_version=row["index_version"],
            policy_version=row["policy_version"],
            expires_at=datetime.fromisoformat(expires),
            normalized_query=row.get("normalized_query") or "",
            language=row.get("language") or "en",
            channel=row.get("channel") or "chat",
            coverage_start=_date(row.get("coverage_start")),
            coverage_end=_date(row.get("coverage_end")),
            coverage_unknown=bool(row.get("coverage_unknown")),
            programme=row.get("programme") or None,
            exam_type=row.get("exam_type") or None,
            mode=row.get("mode") or None,
            event_dates=tuple(date.fromisoformat(value) for value in event_dates),
            source_content_hash=row.get("source_content_hash") or "",
            answer=row.get("answer") or "",
            evidence=_json(row.get("evidence"), {}),
            citations=tuple(_json(row.get("citations"), [])),
            verification=_json(row.get("verification"), {}),
            negative=bool(row.get("negative")),
            builder=row.get("builder") or "",
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_PATTERNS = (
    (re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy"),
    (
        re.compile(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})[a-z]*\s+(\d{{4}})\b",
            re.IGNORECASE,
        ),
        "d_month_y",
    ),
    (
        re.compile(
            rf"\b({_MONTH_NAMES})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
            re.IGNORECASE,
        ),
        "month_d_y",
    ),
)


def extract_event_dates(
    text: str,
    *,
    now: datetime | None = None,
) -> tuple[date, ...]:
    """Extract plausible event dates from evidence text.

    Only dates within a few years of now are kept, so room numbers and
    unrelated identifiers are not mistaken for schedule dates.
    """
    moment = _aware(now or datetime.now(timezone.utc))
    low_year, high_year = moment.year - 3, moment.year + 5
    found: set[date] = set()
    for pattern, kind in _DATE_PATTERNS:
        for match in pattern.finditer(text or ""):
            groups = match.groups()
            try:
                if kind == "ymd":
                    year, month, day = (
                        int(groups[0]), int(groups[1]), int(groups[2])
                    )
                elif kind == "dmy":
                    day, month, year = (
                        int(groups[0]), int(groups[1]), int(groups[2])
                    )
                elif kind == "d_month_y":
                    day, month, year = (
                        int(groups[0]), _MONTHS[groups[1].lower()], int(groups[2])
                    )
                else:
                    month, day, year = (
                        _MONTHS[groups[0].lower()], int(groups[1]), int(groups[2])
                    )
                value = date(year, month, day)
            except (ValueError, KeyError, IndexError):
                continue
            if low_year <= value.year <= high_year:
                found.add(value)
    return tuple(sorted(found))


def coverage_from_text(
    text: str,
    *,
    now: datetime | None = None,
) -> tuple[date | None, date | None, tuple[date, ...], bool]:
    """Return ``(coverage_start, coverage_end, event_dates, unknown)``."""
    dates = extract_event_dates(text, now=now)
    if not dates:
        return None, None, (), True
    return dates[0], dates[-1], dates, False


def cache_ttl_seconds(
    time_scope: TimeScope | None,
    *,
    negative: bool = False,
) -> int:
    if negative:
        return 10 * 60
    if time_scope is not None:
        return 60 * 60
    return 24 * 60 * 60


def cache_key(
    *,
    normalized_query: str,
    language: str,
    channel: str,
    time_scope: TimeScope | None,
    programme: str | None = None,
    exam_type: str | None = None,
    mode: str | None = None,
    index_version: str,
    policy_version: str,
) -> str:
    """Deterministic key; the time scope and entities are part of the key."""
    parts = [
        normalized_query,
        language,
        channel,
        programme or "",
        exam_type or "",
        mode or "",
        time_scope.start.isoformat() if time_scope and time_scope.start else "",
        time_scope.end.isoformat() if time_scope and time_scope.end else "",
        index_version,
        policy_version,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def cache_is_valid(
    entry: CacheEntry,
    request: RequestContext,
    *,
    now: datetime,
    index_version: str,
    policy_version: str,
) -> bool:
    """Return True only when the entry may answer *request* right now."""
    if entry.status not in _CACHE_STATUSES:
        return False
    if entry.index_version != index_version:
        return False
    if entry.policy_version != policy_version:
        return False
    if _aware(entry.expires_at) <= _aware(now):
        return False
    today = _aware(now).date()
    if entry.evidence_valid_until and entry.evidence_valid_until < today:
        return False

    if request.time_scope is not None:
        # Lazy import: app.agent's package __init__ imports app.agent.text,
        # which imports this module. Importing app.agent.time_scope at module
        # load would therefore be circular.
        from app.agent.time_scope import scope_covers

        if (
            entry.coverage_unknown
            or entry.coverage_start is None
            or entry.coverage_end is None
        ):
            return False
        if not scope_covers(
            entry.coverage_start, entry.coverage_end, request.time_scope
        ):
            return False

    for field in ("programme", "exam_type", "mode"):
        requested = getattr(request, field)
        if requested and _norm(requested) != _norm(getattr(entry, field)):
            return False

    if request.after_event:
        if not entry.event_dates:
            return False
        if max(entry.event_dates) < today:
            return False

    return True


__all__ = [
    "CacheEntry",
    "RequestContext",
    "cache_is_valid",
    "cache_key",
    "cache_ttl_seconds",
    "coverage_from_text",
    "extract_event_dates",
]
