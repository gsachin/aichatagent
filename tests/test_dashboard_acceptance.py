"""Pure metrics behind the dashboard acceptance cards."""

from __future__ import annotations

import json

from app.dashboard.acceptance import load_report, percentile, summarize


def _turn(
    *,
    passed: bool,
    outcome: str,
    expected: list[str],
    latency_ms: float,
    required=("3410",),
    negative_cache: bool = False,
    error: str | None = None,
    failures: list[str] | None = None,
    required_topic: bool = False,
    cache_hit: bool = False,
    plan_filters: dict | None = None,
    retrieval: dict | None = None,
    builder_declines: list[dict] | None = None,
    builder: str | None = None,
) -> dict:
    return {
        "passed": passed,
        "outcome": outcome,
        "expected_outcomes": expected,
        "latency_ms": latency_ms,
        "required_facts": list(required),
        "required_topic": required_topic,
        "cache_hit": cache_hit,
        "plan_filters": plan_filters or {},
        "retrieval": retrieval,
        "builder": builder,
        "builder_declines": builder_declines or [],
        "negative_cache": negative_cache,
        "error": error,
        "failures": failures or [],
    }


def _report() -> dict:
    return {
        "generated_at": "2026-11-01T00:00:00Z",
        "llm_mode": "live",
        "runs": 3,
        "source_db": "content/dspmu/nga_graph.sqlite3",
        "snapshot": True,
        "cases": [
            {
                "case_id": "A01",
                "group": "programme list",
                "passed": True,
                "failures": [],
                "turns": [
                    _turn(
                        passed=True,
                        outcome="answer",
                        expected=["answer"],
                        latency_ms=100,
                        negative_cache=True,
                        cache_hit=True,
                        retrieval={"adequate": True, "score": 0.9},
                        builder_declines=[
                            {"builder": "admission", "reason": "missing_fact"}
                        ],
                        builder="programme_list",
                    )
                ],
            },
            {
                "case_id": "A14",
                "group": "fees",
                "passed": False,
                "failures": ["required fact missing: '3410'"],
                "turns": [
                    _turn(
                        passed=False,
                        outcome="refusal",
                        expected=["answer"],
                        latency_ms=300,
                        required_topic=True,
                        retrieval={"adequate": False, "score": 0.2},
                        failures=["required fact missing: '3410'"],
                    )
                ],
            },
            {
                "case_id": "A09",
                "group": "library",
                "passed": True,
                "failures": [],
                "turns": [
                    _turn(
                        passed=True,
                        outcome="handoff",
                        expected=["answer", "handoff"],
                        latency_ms=200,
                        required=(),
                        error="handoff:empty_shell",
                    )
                ],
            },
        ],
    }


def test_summary_reports_pass_rates_and_false_refusals():
    summary = summarize(_report())

    assert summary["cases"] == 3
    assert summary["cases_passed"] == 2
    assert summary["case_pass_rate"] == 2 / 3
    assert summary["turns"] == 3
    assert summary["turns_passed"] == 2
    assert summary["false_refusals"] == 1
    assert summary["negative_cache_flags"] == 1
    assert summary["handoffs"] == 1
    assert summary["handoff_reasons"] == {"handoff:empty_shell": 1}
    assert summary["fact_turns"] == 2
    assert summary["fact_failures"] == 1
    assert summary["p95_ms"] == 300
    assert summary["cache_hits"] == 1
    assert summary["cache_hits_passed"] == 1
    assert summary["followup_turns"] == 1
    assert summary["followup_resolved"] == 0
    assert summary["fallbacks"] == 0
    assert summary["heldout_cases"] == 0
    assert summary["adversarial_cases"] == 0
    assert summary["retrieval_turns"] == 2
    assert summary["inadequate_turns"] == 1
    assert summary["inadequate_rate"] == 0.5
    assert summary["builder_decline_turns"] == 1
    assert summary["builder_decline_reasons"] == {"admission:missing_fact": 1}
    assert summary["builder_turns"] == 1
    assert summary["builder_counts"] == {"programme_list": 1}
    assert summary["blocked_reasons"]
    assert summary["outcome_counts"]["answer"] == 1
    assert summary["report_ok"] is False
    assert summary["failed_cases"][0]["case_id"] == "A14"


def test_percentile_is_nearest_rank_and_empty_safe():
    assert percentile([], 0.95) == 0.0
    assert percentile([10], 0.95) == 10
    assert percentile([10, 20, 30, 40, 50], 0.95) == 50
    assert percentile([10, 20, 30, 40, 50], 0.5) == 30


def test_missing_and_malformed_reports_are_none(tmp_path):
    assert load_report(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_report(broken) is None
    valid = tmp_path / "report.json"
    valid.write_text(json.dumps({"cases": []}), encoding="utf-8")
    assert load_report(valid) == {"cases": []}
