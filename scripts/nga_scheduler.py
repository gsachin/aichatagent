#!/usr/bin/env python3
"""Run the NGA refresh worker and evaluate its ops alerts (Phase 10).

The worker is a separate background process. It refreshes due pages within the
Phase 3 budgets, writes ``nga_refresh_report.json`` plus a JSONL run history,
and appends deterministic alerts to ``logs/nga_alerts.jsonl``. It is never
called from the answer path.

``NGA_SCHEDULER_ENABLED=false`` keeps it inert even when ``NGA_ENABLED=true``.

Usage:
    .venv/bin/python scripts/nga_scheduler.py --status
    .venv/bin/python scripts/nga_scheduler.py --allow-network --once
    .venv/bin/python scripts/nga_scheduler.py --allow-network --max-cycles 2
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.sitegraph.alerts import Alert, AlertThresholds, evaluate_alerts  # noqa: E402
from app.sitegraph.documents import DocumentOptions  # noqa: E402
from app.sitegraph.fetch import SiteFetcher  # noqa: E402
from app.sitegraph.metrics import (  # noqa: E402
    build_snapshot_record,
    read_recent_runs,
    read_report,
    utc_now,
)
from app.sitegraph.refresh import RefreshOptions  # noqa: E402
from app.sitegraph.scheduler import CycleResult, RefreshScheduler  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the NGA refresh worker and evaluate ops alerts (Phase 10)."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--status", action="store_true", help="print status and alerts")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    parser.add_argument(
        "--max-cycles", type=int, default=settings.NGA_REFRESH_MAX_CYCLES
    )
    parser.add_argument(
        "--interval", type=float, default=settings.NGA_REFRESH_INTERVAL_S
    )
    parser.add_argument("--jitter", type=float, default=settings.NGA_REFRESH_JITTER_S)
    parser.add_argument("--limit", type=int, default=settings.NGA_REFRESH_LIMIT)
    parser.add_argument("--ttl", type=int, default=settings.NGA_TTL_PAGE_S)
    parser.add_argument("--min-chars", type=int, default=settings.NGA_MIN_PAGE_CHARS)
    parser.add_argument("--max-bytes", type=int, default=settings.NGA_MAX_BYTES)
    parser.add_argument("--timeout", type=float, default=settings.NGA_FETCH_TIMEOUT_S)
    parser.add_argument("--delay", type=float, default=settings.NGA_FETCH_DELAY_S)
    parser.add_argument("--allowed-hosts", default=settings.NGA_ALLOWED_HOSTS)
    parser.add_argument("--user-agent", default=settings.NGA_FETCH_USER_AGENT)
    parser.add_argument("--no-robots", action="store_true")
    parser.add_argument("--report-path", default=settings.NGA_REFRESH_REPORT_PATH)
    parser.add_argument("--run-log", default=settings.NGA_REFRESH_LOG_PATH)
    parser.add_argument("--alert-log", default=settings.NGA_ALERT_LOG_PATH)
    parser.add_argument("--lock-path", default=settings.NGA_REFRESH_LOCK_PATH)
    parser.add_argument(
        "--no-documents",
        action="store_true",
        help="skip the background PDF/OCR index for this run",
    )
    parser.add_argument(
        "--pdf-limit", type=int, default=settings.NGA_PDF_REFRESH_LIMIT
    )
    parser.add_argument(
        "--pdf-max-bytes", type=int, default=settings.NGA_PDF_MAX_BYTES
    )
    parser.add_argument(
        "--pdf-max-pages", type=int, default=settings.NGA_PDF_MAX_PAGES
    )
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument(
        "--ocr-langs",
        default=settings.NGA_PDF_OCR_LANGS,
        help="comma-separated Tesseract languages; empty uses eng,hin",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even when NGA_SCHEDULER_ENABLED is false",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def thresholds_from_settings() -> AlertThresholds:
    return AlertThresholds(
        coverage_min=settings.NGA_COVERAGE_MIN,
        coverage_drop=settings.NGA_ALERT_COVERAGE_DROP,
        graph_stale_hours=settings.NGA_ALERT_GRAPH_STALE_HOURS,
        run_failure_streak=max(1, settings.NGA_ALERT_RUN_FAILURE_STREAK),
        node_failure_streak=max(1, settings.NGA_ALERT_NODE_FAILURE_STREAK),
        error_rate=settings.NGA_ALERT_ERROR_RATE,
        empty_shell_rate=settings.NGA_ALERT_EMPTY_SHELL_RATE,
        soft_404_rate=settings.NGA_ALERT_SOFT_404_RATE,
        document_error_rate=settings.NGA_ALERT_DOCUMENT_ERROR_RATE,
        review_backlog=max(1, settings.NGA_ALERT_REVIEW_BACKLOG),
        reference_failures=max(1, settings.NGA_ALERT_REFERENCE_FAILURES),
        template_required=max(1, settings.NGA_ALERT_TEMPLATE_REQUIRED),
        stale_nodes=max(1, settings.NGA_ALERT_STALE_NODES),
    )


def _alert_payload(alerts: list[Alert]) -> list[dict]:
    return [alert.to_dict() for alert in alerts]


def _print_alert_list(alerts: list[Alert]) -> None:
    if not alerts:
        print("  alerts:        none")
        return
    print("  alerts:")
    for alert in alerts:
        print(f"    [{alert.severity.upper()}] {alert.code}: {alert.message}")


def _print_status(record: dict, alerts: list[Alert], *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {"record": record, "alerts": _alert_payload(alerts)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    stats = record.get("stats") or {}
    graph = record.get("graph") or {}
    freshness = record.get("freshness") or {}
    coverage = graph.get("coverage_pct")
    coverage_text = f"{coverage:.1%}" if isinstance(coverage, (int, float)) else "n/a"
    gate = "PASS" if graph.get("meets_gate") else "FAIL"
    print("NGA refresh status")
    if record.get("snapshot"):
        print("  last run:      none (offline snapshot)")
    else:
        print(
            f"  last run:      {record.get('finished_at', 'n/a')} "
            f"(checked {stats.get('checked', 0)}, errors {stats.get('errors', 0)})"
        )
    print(
        f"  graph:         {graph.get('nodes', 0)} nodes, coverage {coverage_text} "
        f"(gate {gate}), compiled {graph.get('compiled_at', 'n/a')}"
    )
    print(
        "  freshness:     "
        f"{freshness.get('tracked', 0)} tracked, "
        f"{freshness.get('checked', 0)} checked, "
        f"{freshness.get('failing', 0)} failing, "
        f"{freshness.get('stale', 0)} stale, "
        f"{freshness.get('due_now', 0)} due"
    )
    documents = record.get("document_index") or {}
    print(
        "  documents:     "
        f"{documents.get('indexed', 0)} indexed, "
        f"{documents.get('pending', 0)} pending, "
        f"{documents.get('review_pending', 0)} review, "
        f"{documents.get('errors', 0)} error, "
        f"{documents.get('blocked', 0)} blocked "
        f"(index {'on' if settings.NGA_PDF_INDEX_ENABLED else 'off'})"
    )
    references = record.get("reference_delivery") or {}
    reference_window = record.get("reference_delivery_window") or {}
    print(
        "  references:    "
        f"{references.get('sent', 0)} sent, "
        f"{references.get('failed', 0)} failed, "
        f"{references.get('template_required', 0)} template, "
        f"{references.get('skipped', 0)} skipped; last hour: "
        f"{reference_window.get('sent', 0)} sent, "
        f"{reference_window.get('failed', 0)} failed"
    )
    _print_alert_list(alerts)


def _print_cycle(result: CycleResult, *, as_json: bool) -> None:
    if result.skipped:
        message = "refresh skipped: another worker holds the lock"
        print(json.dumps({"skipped": True, "message": message}) if as_json else message)
        return
    record = result.record or {}
    stats = record.get("stats") or {}
    documents = record.get("documents") or {}
    if as_json:
        print(
            json.dumps(
                {
                    "run_id": record.get("run_id"),
                    "finished_at": record.get("finished_at"),
                    "stats": {
                        key: stats.get(key, 0)
                        for key in (
                            "checked",
                            "fetched",
                            "changed",
                            "errors",
                            "empty",
                            "soft_404",
                        )
                    },
                    "documents": {
                        key: documents.get(key, 0)
                        for key in (
                            "checked",
                            "indexed",
                            "needs_review",
                            "errors",
                        )
                    },
                    "alerts": [alert.code for alert in result.alerts],
                },
                ensure_ascii=False,
            )
        )
        return
    suffix = ""
    if result.alerts:
        codes = ", ".join(alert.code for alert in result.alerts)
        suffix = f" alerts=[{codes}]"
    docs = (
        f" docs={documents.get('indexed', 0)}"
        f"/{documents.get('needs_review', 0)}review"
        if record.get("documents") is not None else ""
    )
    print(
        f"[{record.get('finished_at')}] checked={stats.get('checked', 0)} "
        f"changed={stats.get('changed', 0)} errors={stats.get('errors', 0)}"
        f"{docs}{suffix}"
    )


def _status(args: argparse.Namespace) -> int:
    store = GraphStore(args.db)
    thresholds = thresholds_from_settings()
    record = read_report(args.report_path)
    if record is None:
        record = build_snapshot_record(
            store,
            pii_guard_enabled=settings.NGA_PII_GUARD_ENABLED,
            failure_streak=thresholds.node_failure_streak,
            stale_grace_s=settings.NGA_ALERT_STALE_GRACE_S,
            reference_window_s=settings.NGA_ALERT_REFERENCE_WINDOW_S,
        )
    history = [
        row
        for row in read_recent_runs(args.run_log, limit=100)
        if row.get("run_id") != record.get("run_id")
    ]
    alerts = evaluate_alerts(
        record,
        history=history,
        thresholds=thresholds,
        now=utc_now(),
    )
    _print_status(record, alerts, as_json=args.json)
    return 1 if any(alert.severity == "critical" for alert in alerts) else 0


def _document_options(args: argparse.Namespace) -> DocumentOptions | None:
    if args.no_documents or not settings.NGA_PDF_INDEX_ENABLED:
        return None
    ocr_langs = tuple(
        item.strip() for item in (args.ocr_langs or "").split(",") if item.strip()
    )
    return DocumentOptions(
        ttl_s=max(60, settings.NGA_TTL_DOCUMENT_S),
        limit=max(1, min(50, int(args.pdf_limit))),
        max_bytes=max(1024, int(args.pdf_max_bytes)),
        max_pages=max(1, int(args.pdf_max_pages)),
        min_chars=max(1, settings.NGA_PDF_MIN_CHARS),
        ocr=settings.NGA_PDF_OCR_ENABLED and not args.no_ocr,
        ocr_max_pages=max(1, settings.NGA_PDF_OCR_MAX_PAGES),
        ocr_langs=ocr_langs,
        failure_backoff_s=max(60, settings.NGA_PDF_FAILURE_BACKOFF_S),
        allowed_hosts=tuple(
            host.strip()
            for host in args.allowed_hosts.split(",")
            if host.strip()
        ),
        blocked_path_tokens=tuple(
            token.strip()
            for token in settings.NGA_BLOCKED_PATH_TOKENS.split(",")
            if token.strip()
        ),
    )


def _build_scheduler(args: argparse.Namespace) -> RefreshScheduler:
    hosts = tuple(
        host.strip() for host in args.allowed_hosts.split(",") if host.strip()
    )
    fetcher = SiteFetcher(
        hosts,
        user_agent=args.user_agent,
        timeout_s=args.timeout,
        delay_s=args.delay,
        max_bytes=args.max_bytes,
        respect_robots=not args.no_robots,
    )
    options = RefreshOptions(
        ttl_s=max(60, int(args.ttl)),
        limit=max(1, min(50, int(args.limit))),
        min_chars=max(1, int(args.min_chars)),
    )
    max_cycles = 1 if args.once else max(0, int(args.max_cycles))
    return RefreshScheduler(
        store=GraphStore(args.db),
        fetcher=fetcher,
        options=options,
        report_path=args.report_path,
        run_log_path=args.run_log,
        alert_log_path=args.alert_log,
        lock_path=args.lock_path,
        thresholds=thresholds_from_settings(),
        pii_guard_enabled=settings.NGA_PII_GUARD_ENABLED,
        document_options=_document_options(args),
        stale_grace_s=settings.NGA_ALERT_STALE_GRACE_S,
        reference_window_s=settings.NGA_ALERT_REFERENCE_WINDOW_S,
        interval_s=max(0.0, float(args.interval)),
        jitter_s=max(0.0, float(args.jitter)),
        max_cycles=max_cycles,
    )


def _run(args: argparse.Namespace) -> int:
    if not settings.NGA_SCHEDULER_ENABLED and not args.force:
        print(
            "error: NGA_SCHEDULER_ENABLED is false; set it to true or pass "
            "--force",
            file=sys.stderr,
        )
        return 2
    if not args.allow_network:
        print(
            "error: pass --allow-network to refresh, or --status for an "
            "offline report",
            file=sys.stderr,
        )
        return 2

    scheduler = _build_scheduler(args)
    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    if scheduler.max_cycles == 1:
        result = scheduler.run_once()
        _print_cycle(result, as_json=args.json)
        results = [result]
    else:
        if not args.json:
            print(
                f"NGA refresh scheduler: interval {scheduler.interval_s:g}s "
                f"(+0-{scheduler.jitter_s:g}s jitter), "
                f"max cycles {scheduler.max_cycles or 'unlimited'}"
            )
        results = scheduler.run_forever(
            stop_event,
            on_cycle=lambda result: _print_cycle(result, as_json=args.json),
        )
        if not args.json:
            print(f"stopped after {len(results)} cycle(s)")

    failed = any(result.had_errors for result in results) or any(
        result.critical_alerts for result in results
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.status:
        return _status(args)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
