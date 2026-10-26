"""Phase 10: refresh scheduler, metrics, alerts and ops status."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.sitegraph.alerts import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    AlertThresholds,
    emit_alerts,
    evaluate_alerts,
)
from app.sitegraph.documents import DocumentOptions, DocumentStats
from app.sitegraph.fetch import FetchResult
from app.sitegraph.metrics import (
    append_jsonl,
    atomic_write_json,
    build_run_record,
    read_recent_runs,
    read_report,
)
from app.sitegraph.refresh import RefreshOptions, RefreshStats
from app.sitegraph.scheduler import (
    LockHeld,
    RefreshLock,
    RefreshScheduler,
)
from tests.nga_helpers import build_store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = "https://dspmuranchi.ac.in"
HOSTEL = BASE + "/cms/hostel"
HOSTEL_NODE = "cms.hostel"
NOW = "2026-10-24T12:00:00Z"


class FakeFetcher:
    def __init__(self, pages: dict[str, dict | Exception]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str, *, etag: str = "", last_modified: str = ""):
        self.calls.append(url)
        value = self.pages.get(url)
        if value is None:
            raise RuntimeError(f"no fake page for {url}")
        if isinstance(value, Exception):
            raise value
        body = str(value.get("body") or "").encode("utf-8")
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            body=body,
            content_type="text/html; charset=utf-8",
            etag=str(value.get("etag") or ""),
            last_modified=str(value.get("last_modified") or ""),
        )


def _page(body: str = "Boys and girls hostels with a warden office.") -> dict:
    return {
        "body": (
            "<html><head><title>Hostel</title></head><body>"
            f"<main><h1>Hostel</h1><p>{body}</p></main></body></html>"
        ),
        "etag": '"v1"',
    }


def _options(**overrides) -> RefreshOptions:
    values = dict(
        ttl_s=3600,
        limit=5,
        min_chars=20,
        node_ids=(HOSTEL_NODE,),
        now="2026-10-24T10:00:00Z",
    )
    values.update(overrides)
    return RefreshOptions(**values)


def _record(
    *,
    run_id: str = "run-1",
    coverage: float = 1.0,
    compile_id: int | None = 1,
    compiled_at: str | None = "2026-10-24T10:00:00Z",
    exists: bool = True,
    errors: int = 0,
    checked: int = 10,
    empty: int = 0,
    soft_404: int = 0,
    failing_nodes: int = 0,
    stale: int = 0,
    pii_mismatch: bool = False,
    sections: tuple[str, ...] = ("Admissions", "Student Life"),
    finished_at: str = "2026-10-24T12:00:00Z",
) -> dict:
    return {
        "kind": "nga_refresh_run",
        "run_id": run_id,
        "started_at": "2026-10-24T11:59:00Z",
        "finished_at": finished_at,
        "stats": {
            "checked": checked,
            "errors": errors,
            "empty": empty,
            "soft_404": soft_404,
            "changed": 1,
        },
        "graph": {
            "exists": exists,
            "compile_run_id": compile_id,
            "compiled_at": compiled_at,
            "coverage_pct": coverage,
            "coverage_min": 0.95,
            "meets_gate": coverage >= 0.95,
            "top_level_sections": list(sections),
            "pii_guard_mismatch": pii_mismatch,
            "compiled_pii_guard": bool(pii_mismatch),
        },
        "freshness": {
            "tracked": 300,
            "checked": 250,
            "failing": failing_nodes,
            "failure_streak": failing_nodes,
            "stale": stale,
            "due_now": 10,
        },
        "pii_guard": {"runtime_enabled": False, "mismatch": pii_mismatch},
    }


def _codes(alerts) -> set[str]:
    return {alert.code for alert in alerts}


def test_build_run_record_contains_graph_and_freshness(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    record = build_run_record(
        store,
        RefreshStats(checked=2, changed=1),
        started_at=NOW,
        finished_at=NOW,
        options=_options(),
        pii_guard_enabled=False,
    )

    assert record["kind"] == "nga_refresh_run"
    assert record["stats"]["checked"] == 2
    assert record["graph"]["exists"] is True
    assert record["graph"]["coverage_pct"] == 1.0
    assert record["graph"]["top_level_sections"]
    assert record["graph"]["nodes"] > 0
    assert record["graph"]["pii_guard_mismatch"] is False
    assert record["freshness"]["tracked"] > 0
    assert record["freshness"]["never_checked"] == record["freshness"]["tracked"]
    assert record["options"]["limit"] == 5
    assert record["options"]["node_ids"] == [HOSTEL_NODE]


def test_fetch_summary_tracks_failures_and_staleness(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    store.record_fetch_failure(
        HOSTEL_NODE,
        checked_at="2026-10-24T09:00:00Z",
        next_due_at="2026-10-24T09:05:00Z",
        error="connection reset",
    )
    store.record_fetch_failure(
        HOSTEL_NODE,
        checked_at="2026-10-24T09:10:00Z",
        next_due_at="2026-10-24T09:15:00Z",
        error="connection reset",
    )
    store.record_fetch_failure(
        HOSTEL_NODE,
        checked_at="2026-10-24T09:20:00Z",
        next_due_at="2026-10-24T09:25:00Z",
        error="connection reset",
    )

    summary = store.fetch_summary(
        now=NOW,
        failure_streak=3,
        stale_before="2026-10-24T11:00:00Z",
    )

    assert summary["failing"] == 1
    assert summary["failure_streak"] == 1
    assert summary["stale"] == 1
    assert summary["due_now"] == 1
    assert summary["checked"] == 1
    assert summary["oldest_checked_at"] == "2026-10-24T09:20:00Z"


def test_report_is_atomic_and_history_skips_bad_lines(tmp_path):
    report = tmp_path / "refresh.json"
    history = tmp_path / "refresh.jsonl"
    record = _record()

    atomic_write_json(report, record)
    append_jsonl(history, record)
    with history.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
        handle.write(json.dumps({"kind": "other"}) + "\n")
        handle.write(json.dumps(_record(run_id="run-2")) + "\n")

    assert read_report(report) == record
    recent = read_recent_runs(history)
    assert [row["run_id"] for row in recent] == ["run-1", "run-2"]
    assert not list(tmp_path.glob(".*.tmp"))

    for index in range(20):
        append_jsonl(history, _record(run_id=f"run-tail-{index}"))
    assert [row["run_id"] for row in read_recent_runs(history, limit=3)] == [
        "run-tail-17",
        "run-tail-18",
        "run-tail-19",
    ]


def test_healthy_run_has_no_alerts():
    alerts = evaluate_alerts(_record(), history=[])

    assert alerts == []


def test_coverage_below_gate_is_critical():
    alerts = evaluate_alerts(_record(coverage=0.90))

    assert "coverage_below_gate" in _codes(alerts)
    alert = next(item for item in alerts if item.code == "coverage_below_gate")
    assert alert.severity == SEVERITY_CRITICAL
    assert alert.observed == 0.9


def test_coverage_drop_against_the_previous_compile_run():
    alerts = evaluate_alerts(
        _record(run_id="run-2", coverage=0.96, compile_id=2),
        history=[_record(run_id="run-1", coverage=0.99, compile_id=1)],
    )

    assert "coverage_drop" in _codes(alerts)
    alert = next(item for item in alerts if item.code == "coverage_drop")
    assert alert.severity == SEVERITY_WARNING
    assert alert.context["previous_coverage_pct"] == 0.99


def test_same_compile_run_does_not_alert_coverage_drop():
    alerts = evaluate_alerts(
        _record(run_id="run-2", coverage=0.96, compile_id=1),
        history=[_record(run_id="run-1", coverage=0.99, compile_id=1)],
    )

    assert "coverage_drop" not in _codes(alerts)


def test_missing_top_level_section_is_alerted():
    alerts = evaluate_alerts(
        _record(
            run_id="run-2",
            compile_id=2,
            sections=("Admissions",),
        ),
        history=[
            _record(
                run_id="run-1",
                compile_id=1,
                sections=("Admissions", "Student Life"),
            )
        ],
    )

    alert = next(item for item in alerts if item.code == "section_missing")
    assert alert.observed == ["Student Life"]


def test_missing_and_stale_graph_alerts():
    missing = evaluate_alerts(_record(exists=False, compiled_at=None))
    stale = evaluate_alerts(
        _record(compiled_at="2026-10-01T12:00:00Z"),
        thresholds=AlertThresholds(graph_stale_hours=24),
    )

    assert "graph_missing" in _codes(missing)
    assert "graph_stale" in _codes(stale)


def test_run_failure_streak_is_critical():
    alerts = evaluate_alerts(
        _record(run_id="run-3", errors=1),
        history=[
            _record(run_id="run-1", errors=1),
            _record(run_id="run-2", errors=1),
        ],
    )

    alert = next(item for item in alerts if item.code == "refresh_failure_streak")
    assert alert.severity == SEVERITY_CRITICAL
    assert alert.observed == 3


def test_node_failure_streak_and_rate_alerts():
    failed = evaluate_alerts(_record(failing_nodes=2))
    empty = evaluate_alerts(_record(empty=4))
    soft = evaluate_alerts(_record(soft_404=3))
    high_errors = evaluate_alerts(_record(errors=6))

    assert "node_failure_streak" in _codes(failed)
    assert "empty_shell_spike" in _codes(empty)
    assert "soft_404_spike" in _codes(soft)
    assert "refresh_error_rate" in _codes(high_errors)


def test_stale_backlog_and_pii_guard_mismatch():
    stale = evaluate_alerts(_record(stale=60))
    mismatch = evaluate_alerts(_record(pii_mismatch=True))

    assert "stale_backlog" in _codes(stale)
    assert "pii_guard_mismatch" in _codes(mismatch)


def test_document_error_rate_and_review_backlog():
    record = _record()
    record["documents"] = {"checked": 10, "errors": 6}
    record["document_index"] = {"review_pending": 30}
    alerts = evaluate_alerts(record)

    assert {"document_error_rate", "document_review_backlog"} <= _codes(alerts)


def test_scheduler_records_document_stats(tmp_path, monkeypatch):
    store, _db = build_store(tmp_path, pii_guard=False)
    scheduler = RefreshScheduler(
        store=store,
        fetcher=object(),
        options=_options(),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
        document_options=DocumentOptions(discover=False),
    )
    monkeypatch.setattr(
        "app.sitegraph.scheduler.refresh_graph",
        lambda *args, **kwargs: RefreshStats(checked=0),
    )
    monkeypatch.setattr(
        "app.sitegraph.scheduler.refresh_documents",
        lambda *args, **kwargs: DocumentStats(
            checked=2, indexed=1, needs_review=1
        ),
    )

    result = scheduler.run_once()

    assert result.record["documents"]["indexed"] == 1
    assert result.record["documents"]["needs_review"] == 1
    assert "document_index" in result.record


def test_alert_log_is_append_only_jsonl(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alerts = evaluate_alerts(_record(errors=6))
    emit_alerts(path, alerts)
    emit_alerts(path, evaluate_alerts(_record(run_id="run-9", stale=60)))

    rows = [json.loads(line) for line in path.read_text().splitlines()]

    assert rows
    assert all(row["kind"] == "nga_alert" for row in rows)
    assert {row["code"] for row in rows} >= {"refresh_error_rate", "stale_backlog"}


def test_refresh_lock_is_exclusive_and_releases(tmp_path):
    path = tmp_path / "refresh.lock"
    first = RefreshLock(path)
    first.acquire()
    with pytest.raises(LockHeld):
        RefreshLock(path).acquire()
    first.release()

    second = RefreshLock(path)
    second.acquire()
    second.release()


def test_scheduler_writes_report_history_and_clears_lock(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    scheduler = RefreshScheduler(
        store=store,
        fetcher=FakeFetcher({HOSTEL: _page()}),
        options=_options(),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
        thresholds=AlertThresholds(),
        pii_guard_enabled=False,
        interval_s=0,
        jitter_s=0,
    )

    result = scheduler.run_once()

    assert result.record is not None
    assert result.record["stats"]["checked"] == 1
    assert result.record["stats"]["changed"] == 1
    assert result.alerts == []
    assert read_report(tmp_path / "report.json") == result.record
    assert read_recent_runs(tmp_path / "runs.jsonl") == [result.record]
    assert (tmp_path / "refresh.lock").read_text() == ""


def test_scheduler_emits_alert_records(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    scheduler = RefreshScheduler(
        store=store,
        fetcher=FakeFetcher({HOSTEL: _page("tiny")}),
        options=_options(min_chars=120),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
        thresholds=AlertThresholds(
            min_checked_for_rates=1,
            empty_shell_rate=0.5,
        ),
        interval_s=0,
        jitter_s=0,
    )

    result = scheduler.run_once()

    assert "empty_shell_spike" in _codes(result.alerts)
    rows = [
        json.loads(line)
        for line in (tmp_path / "alerts.jsonl").read_text().splitlines()
    ]
    assert rows[0]["kind"] == "nga_alert"
    assert rows[0]["code"] == "empty_shell_spike"


def test_scheduler_skips_when_the_lock_is_held(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    scheduler = RefreshScheduler(
        store=store,
        fetcher=FakeFetcher({HOSTEL: _page()}),
        options=_options(),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
    )
    lock = RefreshLock(tmp_path / "refresh.lock")
    lock.acquire()
    try:
        result = scheduler.run_once()
    finally:
        lock.release()

    assert result.skipped is True
    assert result.record is None
    assert not (tmp_path / "report.json").exists()


def test_scheduler_survives_a_cycle_exception(tmp_path, monkeypatch):
    store, _db = build_store(tmp_path, pii_guard=False)
    scheduler = RefreshScheduler(
        store=store,
        fetcher=FakeFetcher({HOSTEL: _page()}),
        options=_options(),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
    )
    monkeypatch.setattr(
        "app.sitegraph.scheduler.refresh_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    result = scheduler.run_once()

    assert result.error == "db down"
    assert result.record["stats"]["errors"] == 1
    assert result.had_errors is True
    lock = RefreshLock(tmp_path / "refresh.lock")
    lock.acquire()
    lock.release()


def test_scheduler_loop_runs_max_cycles_and_calls_back(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    seen: list[str] = []
    scheduler = RefreshScheduler(
        store=store,
        fetcher=FakeFetcher({HOSTEL: _page()}),
        options=_options(force=True),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
        interval_s=0,
        jitter_s=0,
        max_cycles=2,
    )

    results = scheduler.run_forever(
        on_cycle=lambda result: seen.append(result.record["run_id"])
    )

    assert len(results) == 2
    assert len(seen) == 2
    assert len(read_recent_runs(tmp_path / "runs.jsonl")) == 2


def test_status_cli_reads_a_report_offline(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    record = build_run_record(
        store,
        RefreshStats(),
        started_at=NOW,
        finished_at=NOW,
        options=None,
        pii_guard_enabled=False,
    )
    report = tmp_path / "report.json"
    atomic_write_json(report, record)
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_scheduler.py"),
            "--status",
            "--db",
            str(db),
            "--report-path",
            str(report),
            "--run-log",
            str(tmp_path / "runs.jsonl"),
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["record"]["run_id"] == record["run_id"]
    assert payload["alerts"] == []


def test_status_cli_works_without_a_report(tmp_path):
    _store, db = build_store(tmp_path, pii_guard=False)
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_scheduler.py"),
            "--status",
            "--db",
            str(db),
            "--report-path",
            str(tmp_path / "missing.json"),
            "--run-log",
            str(tmp_path / "missing.jsonl"),
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["record"]["snapshot"] is True
    assert payload["alerts"] == []


def test_run_cli_requires_the_scheduler_flag_and_network(tmp_path):
    _store, db = build_store(tmp_path, pii_guard=False)
    base = [
        sys.executable,
        str(PROJECT_ROOT / "scripts/nga_scheduler.py"),
        "--db",
        str(db),
    ]
    guarded = subprocess.run(
        base + ["--once", "--allow-network"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    no_network = subprocess.run(
        base + ["--force", "--once"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert guarded.returncode == 2
    assert "NGA_SCHEDULER_ENABLED" in guarded.stderr
    assert no_network.returncode == 2
    assert "--allow-network" in no_network.stderr
