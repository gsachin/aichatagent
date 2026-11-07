#!/usr/bin/env python3
"""Run the frozen DSPMU acceptance set against the live NGA text seam.

Each case is data, not code: the runner loads ``eval/acceptance/cases.jsonl``
(and, only for a release run, ``heldout.jsonl``), executes the same
``build_text_answer`` seam the chat channel uses, and checks the observable
contract:

* the answer state and answer type;
* required and forbidden facts (substring containment, numeric commas ignored);
* required and forbidden citation URLs;
* citations are valid official URLs;
* no retrieval on conversational turns;
* no negative cache entry for an answered turn;
* a turn event was recorded;
* the channel and language variants agree;
* latency stays inside the published budget.

The runner takes a consistent SQLite snapshot by default, so a baseline run
never changes the serving database. ``--no-snapshot`` is for tests and for an
operator who explicitly wants to exercise the live cache.

Usage::

    .venv/bin/python scripts/run_acceptance.py --ci-only
    .venv/bin/python scripts/run_acceptance.py --runs 3 --report /tmp/acceptance.json
    .venv/bin/python scripts/run_acceptance.py --llm live --release --include-heldout

The held-out set is refused unless ``--release --include-heldout`` is passed:
it must not be used while developing a fix.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.facts import FactStore  # noqa: E402
from app.agent.text import build_text_answer  # noqa: E402
from app.config import settings  # noqa: E402
from app.sitegraph.search import normalize_query  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402

DEFAULT_CASES = PROJECT_ROOT / "eval/acceptance/cases.jsonl"
DEFAULT_HELDOUT = PROJECT_ROOT / "eval/acceptance/heldout.jsonl"
DEFAULT_ADVERSARIAL = PROJECT_ROOT / "eval/acceptance/adversarial.jsonl"
VALID_OUTCOMES = frozenset(
    {"answer", "handoff", "clarification", "refusal", "error", "fallback"}
)
VALID_LLM_MODES = ("none", "fake", "live")
_OFFICIAL_PORTALS = frozenset({"jharkhanduniversities.nic.in"})


class AcceptanceError(RuntimeError):
    """A malformed case file or an unusable run configuration."""


@dataclass
class TurnResult:
    case_id: str
    group: str
    query: str
    channel: str
    language: str | None
    run_index: int
    repeat_index: int
    variant_index: int
    expected_outcomes: tuple[str, ...]
    required_facts: tuple[str, ...]
    required_topic: bool
    outcome: str
    status: str | None
    answer_type: str | None
    intent: str | None
    builder: str | None
    plan_filters: dict
    answer: str
    citations: tuple[str, ...]
    verification_status: str | None
    verification_failures: list[dict]
    verification_notes: list[str]
    cache_hit: bool
    deep_search: dict | None
    retrieval: dict | None
    builder_declines: list[dict]
    latency_ms: float
    event_present: bool
    negative_cache: bool
    llm_called: bool
    fallback_reason: str | None
    error: str | None
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "query": self.query,
            "channel": self.channel,
            "language": self.language,
            "run": self.run_index,
            "repeat": self.repeat_index,
            "variant": self.variant_index,
            "expected_outcomes": list(self.expected_outcomes),
            "required_facts": list(self.required_facts),
            "required_topic": self.required_topic,
            "outcome": self.outcome,
            "status": self.status,
            "answer_type": self.answer_type,
            "intent": self.intent,
            "builder": self.builder,
            "plan_filters": dict(self.plan_filters),
            "passed": self.passed,
            "failures": list(self.failures),
            "latency_ms": round(self.latency_ms, 1),
            "citation_urls": list(self.citations),
            "verification_status": self.verification_status,
            "verification_failures": list(self.verification_failures),
            "verification_notes": list(self.verification_notes),
            "cache_hit": self.cache_hit,
            "deep_search": dict(self.deep_search) if self.deep_search else None,
            "retrieval": dict(self.retrieval) if self.retrieval else None,
            "builder_declines": [dict(item) for item in self.builder_declines],
            "event_present": self.event_present,
            "negative_cache": self.negative_cache,
            "llm_called": self.llm_called,
            "fallback_reason": self.fallback_reason,
            "error": self.error,
            "answer_preview": self.answer[:500],
        }


@dataclass
class CaseResult:
    case_id: str
    group: str
    passed: bool
    failures: list[str]
    turns: list[TurnResult]

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "passed": self.passed,
            "failures": list(self.failures),
            "turns": [turn.to_dict() for turn in self.turns],
        }


@dataclass
class SuiteResult:
    cases: list[CaseResult]
    llm_mode: str
    runs: int
    source_db: str
    snapshot: bool
    data_state: dict = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    @property
    def summary(self) -> dict:
        turns = [turn for case in self.cases for turn in case.turns]
        passed_cases = sum(1 for case in self.cases if case.passed)
        passed_turns = sum(1 for turn in turns if turn.passed)
        return {
            "cases": len(self.cases),
            "cases_passed": passed_cases,
            "cases_failed": len(self.cases) - passed_cases,
            "turns": len(turns),
            "turns_passed": passed_turns,
            "turns_failed": len(turns) - passed_turns,
            "case_pass_rate": round(
                passed_cases / len(self.cases), 4
            ) if self.cases else 0.0,
            "turn_pass_rate": round(
                passed_turns / len(turns), 4
            ) if turns else 0.0,
        }

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "source_db": self.source_db,
            "snapshot": self.snapshot,
            "llm_mode": self.llm_mode,
            "runs": self.runs,
            "data_state": dict(self.data_state),
            "summary": self.summary,
            "cases": [case.to_dict() for case in self.cases],
        }


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _as_text_list(value: Any, field_name: str) -> list[str]:
    rows = _as_list(value)
    for item in rows:
        if not isinstance(item, str) or not item.strip():
            raise AcceptanceError(
                f"{field_name} must contain non-empty strings"
            )
    return [item.strip() for item in rows]


def load_cases(path: str | Path) -> list[dict]:
    """Load a JSONL case file, preserving line numbers in errors."""
    target = Path(path)
    if not target.is_file():
        raise AcceptanceError(f"case file not found: {target}")
    cases: list[dict] = []
    for line_no, line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AcceptanceError(
                f"{target}:{line_no}: invalid JSON: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise AcceptanceError(
                f"{target}:{line_no}: case must be a JSON object"
            )
        row["_line"] = line_no
        cases.append(row)
    return cases


def validate_cases(cases: Sequence[dict]) -> None:
    """Reject a case file that cannot be scored unambiguously."""
    issues: list[str] = []
    ids: set[str] = set()
    for case in cases:
        where = f"line {case.get('_line', '?')}"
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            issues.append(f"{where}: missing id")
            continue
        if case_id in ids:
            issues.append(f"{where}: duplicate id {case_id}")
        ids.add(case_id)
        queries = case.get("variants") or [case.get("query")]
        if not queries or not all(
            isinstance(query, str) and query.strip() for query in queries
        ):
            issues.append(f"{where} ({case_id}): missing query/variants")
        expect = case.get("expect")
        if not isinstance(expect, dict):
            issues.append(f"{where} ({case_id}): missing expect object")
            continue
        outcomes = _as_list(expect.get("outcomes") or expect.get("outcome"))
        if not outcomes:
            issues.append(f"{where} ({case_id}): missing expected outcome")
        for outcome in outcomes:
            if outcome not in VALID_OUTCOMES:
                issues.append(
                    f"{where} ({case_id}): unknown outcome {outcome!r}"
                )
        try:
            required = _as_text_list(
                expect.get("required"), "required"
            )
            forbidden = _as_text_list(
                expect.get("forbidden"), "forbidden"
            )
            _as_text_list(expect.get("required_urls"), "required_urls")
            _as_text_list(expect.get("forbidden_urls"), "forbidden_urls")
            _as_text_list(case.get("prelude"), "prelude")
        except AcceptanceError as exc:
            issues.append(f"{where} ({case_id}): {exc}")
            continue
        overlap = {
            _norm_text(item) for item in required
        } & {
            _norm_text(item) for item in forbidden
        }
        if overlap:
            issues.append(
                f"{where} ({case_id}): required and forbidden overlap: "
                + ", ".join(sorted(overlap))
            )
        if case.get("repeat") is not None:
            repeat = case.get("repeat")
            if not isinstance(repeat, int) or not 1 <= repeat <= 10:
                issues.append(
                    f"{where} ({case_id}): repeat must be 1..10"
                )
        channels = case.get("channels")
        if channels is not None and (
            not isinstance(channels, list)
            or not channels
            or not all(isinstance(item, str) and item for item in channels)
        ):
            issues.append(
                f"{where} ({case_id}): channels must be a non-empty list"
            )
        if case.get("ci") is not None and not isinstance(case.get("ci"), bool):
            issues.append(f"{where} ({case_id}): ci must be boolean")
    if issues:
        raise AcceptanceError("\n".join(issues))


def _norm_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = text.replace("’", "'").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _contains(answer: str, needle: str) -> bool:
    """Case-insensitive containment; ignore thousands separators."""
    haystack = _norm_text(answer)
    target = _norm_text(needle)
    if not target:
        return True
    if target in haystack:
        return True
    return target.replace(",", "") in haystack.replace(",", "")


def _citation_urls(result: Any) -> tuple[str, ...]:
    urls: list[str] = []
    for citation in getattr(result, "citations", None) or []:
        if isinstance(citation, dict):
            url = citation.get("url")
        else:
            url = getattr(citation, "url", None)
        if url:
            urls.append(str(url))
    return tuple(dict.fromkeys(urls))


def classify_outcome(result: Any) -> str:
    if result is None:
        return "fallback"
    answer_type = getattr(result, "answer_type", None)
    status = getattr(result, "status", None)
    error = str(getattr(result, "error", "") or "")
    # A shell hand-off is not a refusal: it carries the official URL and the
    # contact route. The answer_type stays "refusal" for compatibility, but
    # the acceptance report must not count it as a false refusal.
    if status == "no_evidence" and error.startswith("handoff:"):
        return "handoff"
    if answer_type in {"refusal", "clarification", "error"}:
        return answer_type
    if answer_type == "answer" and status in {
        "verified", "rewritten", "conversational"
    }:
        return "answer"
    return "error"


def _cache_negative(db: str | Path, normalized_query: str) -> bool:
    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            row = conn.execute(
                "select count(*) from nga_answer_cache "
                "where normalized_query = ? and negative = 1",
                (normalized_query,),
            ).fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return False


def _cache_clear(db: str | Path) -> None:
    with sqlite3.connect(str(db), timeout=10) as conn:
        try:
            conn.execute("delete from nga_answer_cache")
            conn.commit()
        except sqlite3.Error:
            pass


def _snapshot_db(source: str | Path) -> tuple[Path, Path]:
    """Copy the graph with SQLite's backup API, WAL included."""
    source_path = Path(source)
    if not source_path.is_file():
        raise AcceptanceError(f"graph database not found: {source}")
    tempdir = Path(tempfile.mkdtemp(prefix="nga-acceptance-"))
    target = tempdir / "graph.sqlite3"
    try:
        with sqlite3.connect(str(source_path), timeout=10) as src:
            with sqlite3.connect(str(target), timeout=10) as dst:
                src.backup(dst)
    except sqlite3.Error as exc:
        shutil.rmtree(tempdir, ignore_errors=True)
        raise AcceptanceError(
            f"could not snapshot {source}: {exc}"
        ) from exc
    return target, tempdir


@contextlib.contextmanager
def _offline(enabled: bool) -> Iterator[None]:
    """Disable network-dependent deep search/revalidation for CI runs."""
    previous = (
        settings.DEEP_SEARCH_ENABLED,
        settings.CACHE_REVALIDATE_ENABLED,
    )
    if enabled:
        # Settings is a frozen dataclass shared by every module. Mutate the one
        # object for the duration of the run and restore it in ``finally``.
        object.__setattr__(settings, "DEEP_SEARCH_ENABLED", False)
        object.__setattr__(settings, "CACHE_REVALIDATE_ENABLED", False)
    try:
        yield
    finally:
        object.__setattr__(
            settings, "DEEP_SEARCH_ENABLED", previous[0]
        )
        object.__setattr__(
            settings, "CACHE_REVALIDATE_ENABLED", previous[1]
        )


def _llm_for(
    mode: str,
    case: dict,
    called: list[bool],
) -> Callable[[list[dict]], str]:
    def llm(messages: list[dict]) -> str:
        called.append(True)
        if case.get("llm_raises"):
            raise RuntimeError("scripted LLM failure")
        if case.get("llm_response") is not None:
            # A scripted response is the point of the case; it must not be
            # replaced by a live model, even in --llm live mode.
            return str(case["llm_response"])
        if mode == "none":
            raise RuntimeError(
                "deterministic acceptance run forbids an LLM call"
            )
        if mode == "fake":
            return "DSPMU offers undergraduate and postgraduate programmes."
        if mode == "live":
            from app.llm_backend import chat

            return chat(
                messages,
                model=settings.LLM_MODEL,
                num_ctx=8192,
                temperature=0,
            )
        raise AcceptanceError(f"unknown LLM mode: {mode}")

    return llm


def _official_host(host: str) -> bool:
    host = (host or "").lower()
    if host in _OFFICIAL_PORTALS:
        return True
    allowed = {
        item.strip().lower()
        for item in settings.NGA_ALLOWED_HOSTS.split(",")
        if item.strip()
    }
    if host in allowed:
        return True
    return host == "dspmuranchi.ac.in" or host.endswith(
        ".dspmuranchi.ac.in"
    )


def _check_turn(
    case: dict,
    *,
    result: Any,
    outcome: str,
    answer: str,
    urls: tuple[str, ...],
    latency_ms: float,
    event_present: bool,
    negative_cache: bool,
    llm_called: bool,
    fallback_reason: str | None,
    default_max_latency_ms: float | None,
) -> list[str]:
    expect = case.get("expect") or {}
    failures: list[str] = []
    expected_outcomes = tuple(
        _as_list(expect.get("outcomes") or expect.get("outcome"))
    )
    if outcome not in expected_outcomes:
        failures.append(
            f"outcome {outcome!r} not in {list(expected_outcomes)!r}"
        )
    if outcome == "fallback" and fallback_reason:
        failures.append(f"fallback: {fallback_reason}")

    for needle in _as_text_list(expect.get("required"), "required"):
        if not _contains(answer, needle):
            failures.append(f"required fact missing: {needle!r}")
    for needle in _as_text_list(expect.get("forbidden"), "forbidden"):
        if _contains(answer, needle):
            failures.append(f"forbidden claim present: {needle!r}")

    searchable = answer + "\n" + "\n".join(urls)
    for needle in _as_text_list(
        expect.get("required_urls"), "required_urls"
    ):
        if not _contains(searchable, needle):
            failures.append(f"required URL missing: {needle!r}")
    for needle in _as_text_list(
        expect.get("forbidden_urls"), "forbidden_urls"
    ):
        if _contains(searchable, needle):
            failures.append(f"forbidden URL present: {needle!r}")

    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            failures.append(f"invalid citation URL: {url!r}")
        elif not _official_host(parsed.netloc):
            failures.append(f"non-official citation host: {url!r}")

    min_citations = expect.get("min_citations")
    if min_citations is not None and len(urls) < int(min_citations):
        failures.append(
            f"only {len(urls)} citation(s); expected at least {min_citations}"
        )
    if expect.get("no_citations") and urls:
        failures.append(f"citations present on a no-citation turn: {urls!r}")

    plan = getattr(result, "plan", None) if result is not None else None
    if expect.get("no_retrieval"):
        steps = list(getattr(plan, "steps", ()) or ())
        if steps:
            failures.append(
                "retrieval ran on a conversational turn: "
                + ", ".join(str(getattr(step, "tool", step)) for step in steps)
            )

    expected_intent = expect.get("intent")
    if expected_intent:
        actual = getattr(getattr(plan, "intent", None), "value", None)
        if actual != expected_intent:
            failures.append(
                f"intent {actual!r}; expected {expected_intent!r}"
            )

    if expect.get("require_topic"):
        filters = getattr(plan, "filters", {}) or {}
        if not filters.get("topic_node_id"):
            failures.append("follow-up did not resolve a topic node")

    if expect.get("deep_search"):
        metadata = (
            getattr(result, "deep_search", None)
            if result is not None else None
        )
        if not metadata or not metadata.get("triggered"):
            failures.append("deep search did not trigger")

    expected_builder = expect.get("builder")
    if expected_builder:
        actual_builder = (
            getattr(result, "builder", None) if result is not None else None
        )
        if actual_builder != expected_builder:
            failures.append(
                f"builder {actual_builder!r}; expected {expected_builder!r}"
            )

    max_latency = expect.get("max_latency_ms", default_max_latency_ms)
    if max_latency is not None and latency_ms > float(max_latency):
        failures.append(
            f"latency {latency_ms:.0f} ms > {float(max_latency):.0f} ms"
        )

    if (
        outcome != "fallback"
        and expect.get("require_event", True)
        and not event_present
    ):
        failures.append("no turn event recorded")
    if (
        outcome == "answer"
        and expect.get("no_negative_cache", True)
        and negative_cache
    ):
        failures.append("negative cache entry exists for an answered turn")
    if llm_called and expect.get("no_llm", False):
        failures.append("LLM was called when the case forbids it")
    return failures


def _run_turn(
    case: dict,
    query: str,
    *,
    channel: str,
    language: str | None,
    variant_index: int,
    run_index: int,
    repeat_index: int,
    db: str | Path,
    facts: FactStore,
    llm_mode: str,
    pii_guard: bool | None,
    default_max_latency_ms: float | None,
) -> TurnResult:
    case_id = str(case["id"])
    group = str(case.get("group") or "uncategorised")
    expect = case.get("expect") or {}
    expected_outcomes = tuple(
        _as_list(expect.get("outcomes") or expect.get("outcome"))
    )
    conversation_id = (
        f"accept-{case_id}-{channel}-{uuid.uuid4().hex[:10]}"
    )
    store = GraphStore(db)
    events_before = store.turn_event_count(conversation_id)
    called: list[bool] = []
    llm = _llm_for(llm_mode, case, called)

    for prelude in _as_text_list(case.get("prelude"), "prelude"):
        try:
            build_text_answer(
                prelude,
                graph_path=str(db),
                pii_guard=pii_guard,
                llm=llm,
                language=language,
                facts=facts,
                conversation_id=conversation_id,
                channel=channel,
            )
        except Exception:  # noqa: BLE001 - the observed turn is what matters
            pass
    events_mid = store.turn_event_count(conversation_id)

    started = time.perf_counter()
    result = None
    fallback_reason: str | None = None
    try:
        result = build_text_answer(
            query,
            graph_path=str(db),
            pii_guard=pii_guard,
            llm=llm,
            history=case.get("history"),
            language=language,
            facts=facts,
            conversation_id=conversation_id,
            channel=channel,
            use_fact_answer=not bool(case.get("force_llm")),
        )
        if result is None:
            fallback_reason = "agentic_path_returned_none"
    except Exception as exc:  # noqa: BLE001 - report it, do not stop the suite
        fallback_reason = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000
    events_after = store.turn_event_count(conversation_id)

    outcome = classify_outcome(result)
    answer = str(getattr(result, "answer", "") or "")
    urls = _citation_urls(result)
    normalized_query = (
        normalize_query(query).text or query.strip().lower()
    )
    negative_cache = _cache_negative(db, normalized_query)
    event_present = events_after > events_mid
    failures = _check_turn(
        case,
        result=result,
        outcome=outcome,
        answer=answer,
        urls=urls,
        latency_ms=latency_ms,
        event_present=event_present,
        negative_cache=negative_cache,
        llm_called=bool(called),
        fallback_reason=fallback_reason,
        default_max_latency_ms=default_max_latency_ms,
    )
    if llm_mode == "none" and called:
        failures.append("LLM was called in deterministic (--llm none) mode")
    plan = getattr(result, "plan", None) if result is not None else None
    verification = getattr(result, "verification", None) if result else None
    return TurnResult(
        case_id=case_id,
        group=group,
        query=query,
        channel=channel,
        language=language,
        run_index=run_index,
        repeat_index=repeat_index,
        variant_index=variant_index,
        expected_outcomes=expected_outcomes,
        required_facts=tuple(
            _as_text_list(expect.get("required"), "required")
        ),
        required_topic=bool(expect.get("require_topic")),
        outcome=outcome,
        status=getattr(result, "status", None),
        answer_type=getattr(result, "answer_type", None),
        intent=getattr(getattr(plan, "intent", None), "value", None),
        builder=getattr(result, "builder", None),
        plan_filters=dict(getattr(plan, "filters", {}) or {}),
        answer=answer,
        citations=urls,
        verification_status=getattr(verification, "status", None),
        verification_failures=list(
            getattr(verification, "failures", []) or []
        ),
        verification_notes=list(
            getattr(verification, "notes", []) or []
        ),
        cache_hit="cache_hit" in list(
            getattr(verification, "notes", []) or []
        ),
        deep_search=(
            dict(getattr(result, "deep_search"))
            if result is not None and getattr(result, "deep_search", None)
            else None
        ),
        retrieval=(
            dict(getattr(result, "retrieval"))
            if result is not None and getattr(result, "retrieval", None)
            else None
        ),
        builder_declines=(
            [dict(item) for item in getattr(result, "builder_declines", [])]
            if result is not None
            else []
        ),
        latency_ms=latency_ms,
        event_present=event_present,
        negative_cache=negative_cache,
        llm_called=bool(called),
        fallback_reason=fallback_reason,
        error=getattr(result, "error", None),
        failures=failures,
    )


def run_case(
    case: dict,
    *,
    db: str | Path,
    facts: FactStore,
    llm_mode: str,
    runs: int,
    pii_guard: bool | None,
    default_max_latency_ms: float | None,
) -> CaseResult:
    queries = _as_text_list(
        case.get("variants") or [case.get("query")], "variants"
    )
    channels = _as_text_list(
        case.get("channels") or [case.get("channel") or "chat"],
        "channels",
    )
    language = case.get("language")
    repeat = int(case.get("repeat") or 1)
    turns: list[TurnResult] = []
    for run_index in range(runs):
        for variant_index, query in enumerate(queries):
            for channel in channels:
                for repeat_index in range(repeat):
                    turns.append(
                        _run_turn(
                            case,
                            query,
                            channel=channel,
                            language=language,
                            variant_index=variant_index,
                            run_index=run_index,
                            repeat_index=repeat_index,
                            db=db,
                            facts=facts,
                            llm_mode=llm_mode,
                            pii_guard=pii_guard,
                            default_max_latency_ms=default_max_latency_ms,
                        )
                    )

    failures = [
        f"run {turn.run_index + 1} variant {turn.variant_index + 1} "
        f"channel {turn.channel} repeat {turn.repeat_index + 1}: {failure}"
        for turn in turns
        for failure in turn.failures
    ]

    # Channel parity: the same query must produce the same outcome and the same
    # citation set in every channel. Voice remains a separate latency/render
    # variant, but not a different evidence universe.
    if len(channels) > 1:
        groups: dict[tuple[int, int, int], list[TurnResult]] = {}
        for turn in turns:
            groups.setdefault(
                (turn.run_index, turn.variant_index, turn.repeat_index),
                [],
            ).append(turn)
        for (_, variant_index, repeat_index), group in sorted(
            groups.items()
        ):
            outcomes = {turn.outcome for turn in group}
            citation_sets = {
                tuple(sorted(set(turn.citations))) for turn in group
            }
            if len(outcomes) > 1 or len(citation_sets) > 1:
                failures.append(
                    f"variant {variant_index + 1} repeat "
                    f"{repeat_index + 1}: channel parity mismatch "
                    f"(outcomes={sorted(outcomes)}, "
                    f"citation sets={len(citation_sets)})"
                )

    return CaseResult(
        case_id=str(case["id"]),
        group=str(case.get("group") or "uncategorised"),
        passed=not failures,
        failures=failures,
        turns=turns,
    )


def _case_digest(cases: Sequence[dict]) -> str:
    payload = [
        {
            key: value for key, value in case.items()
            if key != "_line"
        }
        for case in cases
    ]
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _data_state(
    db: str | Path,
    store: GraphStore,
    cases: Sequence[dict],
) -> dict:
    run = store.latest_run() or {}
    versions: list[str] = []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
            versions = [
                str(row[0])
                for row in con.execute(
                    "SELECT DISTINCT index_version FROM nga_chunk "
                    "WHERE index_version <> '' ORDER BY index_version"
                ).fetchall()
            ]
    except sqlite3.Error:
        versions = []
    return {
        "graph_hash": str(run.get("graph_hash") or ""),
        "compile_generated_at": str(run.get("generated_at") or ""),
        "chunk_count": int(store.chunk_count()),
        "chunk_index_versions": versions,
        "case_digest": _case_digest(cases),
    }


def run_suite(
    cases: Sequence[dict],
    *,
    db: str | Path,
    llm_mode: str = "none",
    runs: int = 1,
    snapshot: bool = True,
    clean_cache: bool = True,
    offline: bool = True,
    pii_guard: bool | None = None,
    facts: FactStore | None = None,
    default_max_latency_ms: float | None = None,
) -> SuiteResult:
    if llm_mode not in VALID_LLM_MODES:
        raise AcceptanceError(f"unknown LLM mode: {llm_mode}")
    if runs < 1:
        raise AcceptanceError("runs must be at least 1")
    validate_cases(cases)
    source_db = str(Path(db).resolve())
    tempdir: Path | None = None
    run_db: Path
    if snapshot:
        run_db, tempdir = _snapshot_db(db)
    else:
        run_db = Path(db)
    try:
        if clean_cache:
            _cache_clear(run_db)
        store = GraphStore(run_db)
        if not store.latest_run():
            raise AcceptanceError(
                f"graph has no compile run: {run_db}"
            )
        data_state = _data_state(run_db, store, cases)
        with _offline(offline):
            results = [
                run_case(
                    case,
                    db=run_db,
                    facts=facts or FactStore(),
                    llm_mode=llm_mode,
                    runs=runs,
                    pii_guard=pii_guard,
                    default_max_latency_ms=default_max_latency_ms,
                )
                for case in cases
            ]
        return SuiteResult(
            cases=results,
            llm_mode=llm_mode,
            runs=runs,
            source_db=source_db,
            snapshot=snapshot,
            data_state=data_state,
        )
    finally:
        if tempdir is not None:
            shutil.rmtree(tempdir, ignore_errors=True)


def _select_cases(
    tuned: Sequence[dict],
    heldout: Sequence[dict],
    *,
    adversarial: Sequence[dict] = (),
    case_ids: Sequence[str],
    ci_only: bool,
    include_heldout: bool,
    include_adversarial: bool = False,
    release: bool = False,
) -> list[dict]:
    if (include_heldout or include_adversarial) and not release:
        raise AcceptanceError(
            "held-out/adversarial cases require --release; they must not be "
            "used while developing a fix"
        )
    if ci_only and (include_heldout or include_adversarial):
        raise AcceptanceError(
            "--ci-only cannot be combined with held-out/adversarial cases"
        )
    selected = list(tuned)
    if include_heldout:
        selected.extend(heldout)
    if include_adversarial:
        selected.extend(adversarial)
    if ci_only:
        selected = [case for case in selected if bool(case.get("ci"))]
    if case_ids:
        wanted = set(case_ids)
        selected = [
            case for case in selected if str(case.get("id")) in wanted
        ]
        found = {str(case.get("id")) for case in selected}
        missing = wanted - found
        if missing:
            raise AcceptanceError(
                "unknown case id(s): " + ", ".join(sorted(missing))
            )
    if not selected:
        raise AcceptanceError("no cases selected")
    return selected


def _print_suite(suite: SuiteResult) -> None:
    summary = suite.summary
    print(
        f"Acceptance: {summary['cases_passed']}/{summary['cases']} cases, "
        f"{summary['turns_passed']}/{summary['turns']} turns "
        f"({summary['case_pass_rate'] * 100:.1f}% cases) | "
        f"llm={suite.llm_mode} runs={suite.runs} "
        f"snapshot={suite.snapshot}"
    )
    for case in suite.cases:
        if case.passed:
            first = case.turns[0]
            print(
                f"PASS {case.case_id:<4} {first.outcome:<13} "
                f"{first.latency_ms:6.0f} ms  {first.query[:58]}"
            )
            continue
        print(f"FAIL {case.case_id:<4} {case.group}")
        for failure in case.failures[:8]:
            print(f"     - {failure}")
        if len(case.failures) > 8:
            print(f"     - ... {len(case.failures) - 8} more")


def _write_report(path: str | Path, suite: SuiteResult) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(
        json.dumps(suite.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen NGA acceptance set."
    )
    parser.add_argument("--db", default=settings.serve_db_path)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--heldout", default=str(DEFAULT_HELDOUT))
    parser.add_argument(
        "--adversarial", default=str(DEFAULT_ADVERSARIAL)
    )
    parser.add_argument("--case", action="append", default=[], dest="case_ids")
    parser.add_argument("--ci-only", action="store_true")
    parser.add_argument(
        "--release",
        action="store_true",
        help="unlock the held-out set for a release report",
    )
    parser.add_argument("--include-heldout", action="store_true")
    parser.add_argument("--include-adversarial", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--llm", choices=VALID_LLM_MODES, default="none")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="keep existing cache entries in the run database",
    )
    parser.add_argument(
        "--no-offline",
        action="store_true",
        help="allow deep search and cache revalidation network calls",
    )
    guard = parser.add_mutually_exclusive_group()
    guard.add_argument(
        "--pii-guard", dest="pii_guard", action="store_true"
    )
    guard.add_argument(
        "--no-pii-guard", dest="pii_guard", action="store_false"
    )
    parser.set_defaults(pii_guard=None)
    parser.add_argument("--max-latency-ms", type=float, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report", default="")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.include_heldout and not args.release:
        raise AcceptanceError(
            "--include-heldout requires --release; the held-out set must not "
            "be used during development"
        )
    tuned = load_cases(args.cases)
    heldout = load_cases(args.heldout) if args.include_heldout else []
    adversarial = (
        load_cases(args.adversarial) if args.include_adversarial else []
    )
    cases = _select_cases(
        tuned,
        heldout,
        adversarial=adversarial,
        case_ids=args.case_ids,
        ci_only=args.ci_only,
        include_heldout=args.include_heldout,
        include_adversarial=args.include_adversarial,
        release=args.release,
    )
    suite = run_suite(
        cases,
        db=args.db,
        llm_mode=args.llm,
        runs=args.runs,
        snapshot=not args.no_snapshot,
        clean_cache=not args.keep_cache,
        offline=not args.no_offline,
        pii_guard=args.pii_guard,
        default_max_latency_ms=args.max_latency_ms,
    )
    if args.report:
        _write_report(args.report, suite)
    if args.json:
        print(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_suite(suite)
    return 0 if suite.summary["cases_failed"] == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceError as exc:
        print(f"acceptance error: {exc}", file=sys.stderr)
        raise SystemExit(1)
