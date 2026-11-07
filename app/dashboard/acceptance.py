"""Pure helpers for the dashboard's acceptance cards.

The dashboard reads the JSON report written by ``scripts/run_acceptance.py``.
Keeping the calculations here (rather than inside the Streamlit renderer)
makes the numbers testable without a running dashboard.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_report(path: str | Path) -> dict | None:
    """Load a runner report, or return None when it is missing/malformed."""
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(
        0,
        min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))),
    )
    return ordered[index]


def summarize(report: dict) -> dict:
    """Derive the TPO-facing metrics from one runner report."""
    cases = list(report.get("cases") or [])
    turns = [
        turn
        for case in cases
        for turn in (case.get("turns") or [])
        if isinstance(turn, dict)
    ]
    passed_cases = sum(1 for case in cases if case.get("passed"))
    passed_turns = sum(1 for turn in turns if turn.get("passed"))

    false_refusals = 0
    negative_cache_flags = 0
    handoffs = 0
    fact_turns = 0
    fact_failures = 0
    cache_hits = 0
    cache_hits_passed = 0
    followup_turns = 0
    followup_resolved = 0
    fallbacks = 0
    retrieval_turns = 0
    inadequate_turns = 0
    builder_decline_turns = 0
    builder_decline_reasons: Counter[str] = Counter()
    builder_turns = 0
    builder_counts: Counter[str] = Counter()
    handoff_reasons: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()

    for turn in turns:
        expected = set(turn.get("expected_outcomes") or [])
        outcome = str(turn.get("outcome") or "unknown")
        outcome_counts[outcome] += 1
        # A refusal is only a false refusal when the case did not also allow
        # one (A03/A09/A10 permit a hand-off or refusal as the honest outcome).
        if (
            "answer" in expected
            and outcome in {"refusal", "clarification", "error", "fallback"}
            and outcome not in expected
        ):
            false_refusals += 1
        if turn.get("negative_cache") and outcome == "answer":
            negative_cache_flags += 1
        if outcome == "handoff":
            handoffs += 1
            reason = (
                turn.get("error")
                or turn.get("fallback_reason")
                or turn.get("status")
                or "handoff"
            )
            handoff_reasons[str(reason)] += 1
        if outcome in {"refusal", "handoff", "error", "fallback"}:
            reason = (
                turn.get("error")
                or turn.get("fallback_reason")
                or turn.get("status")
                or outcome
            )
            blocked_reasons[str(reason)] += 1
        if turn.get("cache_hit"):
            cache_hits += 1
            if turn.get("passed"):
                cache_hits_passed += 1
        if turn.get("required_topic"):
            followup_turns += 1
            filters = turn.get("plan_filters") or {}
            if turn.get("passed") and filters.get("topic_node_id"):
                followup_resolved += 1
        if outcome == "fallback" or turn.get("fallback_reason"):
            fallbacks += 1
        retrieval = turn.get("retrieval")
        if isinstance(retrieval, dict) and "adequate" in retrieval:
            retrieval_turns += 1
            if not retrieval.get("adequate"):
                inadequate_turns += 1
        declines = turn.get("builder_declines") or []
        if declines:
            builder_decline_turns += 1
            for decline in declines:
                if not isinstance(decline, dict):
                    continue
                builder_decline_reasons[
                    f"{decline.get('builder', '?')}:"
                    f"{decline.get('reason', '?')}"
                ] += 1
        builder = turn.get("builder")
        if builder:
            builder_turns += 1
            builder_counts[str(builder)] += 1
        required = turn.get("required_facts") or []
        if required:
            fact_turns += 1
            failures = [str(item) for item in (turn.get("failures") or [])]
            if any("required fact" in item for item in failures):
                fact_failures += 1

    latencies = [
        float(turn.get("latency_ms") or 0.0) for turn in turns
    ]
    failed_cases = [
        {
            "case_id": case.get("case_id") or "?",
            "group": case.get("group") or "",
            "failures": list(case.get("failures") or []),
        }
        for case in cases
        if not case.get("passed")
    ]
    case_count = len(cases)
    turn_count = len(turns)
    heldout_cases = sum(
        1 for case in cases if str(case.get("case_id") or "").startswith("H")
    )
    adversarial_cases = sum(
        1
        for case in cases
        if str(case.get("case_id") or "").startswith("ADV")
    )
    return {
        "generated_at": report.get("generated_at"),
        "llm_mode": report.get("llm_mode"),
        "runs": report.get("runs"),
        "source_db": report.get("source_db"),
        "snapshot": report.get("snapshot"),
        "cases": case_count,
        "cases_passed": passed_cases,
        "cases_failed": case_count - passed_cases,
        "case_pass_rate": (
            passed_cases / case_count if case_count else 0.0
        ),
        "turns": turn_count,
        "turns_passed": passed_turns,
        "turns_failed": turn_count - passed_turns,
        "turn_pass_rate": (
            passed_turns / turn_count if turn_count else 0.0
        ),
        "p95_ms": percentile(latencies, 0.95),
        "false_refusals": false_refusals,
        "negative_cache_flags": negative_cache_flags,
        "handoffs": handoffs,
        "handoff_reasons": dict(
            sorted(handoff_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "blocked_reasons": dict(
            sorted(blocked_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "outcome_counts": dict(
            sorted(outcome_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "cache_hits": cache_hits,
        "cache_hits_passed": cache_hits_passed,
        "followup_turns": followup_turns,
        "followup_resolved": followup_resolved,
        "fallbacks": fallbacks,
        "heldout_cases": heldout_cases,
        "adversarial_cases": adversarial_cases,
        "retrieval_turns": retrieval_turns,
        "inadequate_turns": inadequate_turns,
        "inadequate_rate": (
            inadequate_turns / retrieval_turns if retrieval_turns else 0.0
        ),
        "builder_decline_turns": builder_decline_turns,
        "builder_decline_reasons": dict(
            sorted(
                builder_decline_reasons.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "builder_turns": builder_turns,
        "builder_counts": dict(
            sorted(builder_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "fact_turns": fact_turns,
        "fact_failures": fact_failures,
        "fact_pass_rate": (
            (fact_turns - fact_failures) / fact_turns
            if fact_turns else 0.0
        ),
        "failed_cases": failed_cases,
        "report_ok": case_count > 0 and passed_cases == case_count,
    }


__all__ = ["load_report", "percentile", "summarize"]
