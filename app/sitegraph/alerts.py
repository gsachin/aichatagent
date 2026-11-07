"""Deterministic alert rules for the background refresh worker (Phase 10).

Alerts are evaluated from a refresh run record plus the recent run history.
They never depend on a live request, an LLM or an external service: the
scheduler appends them to a JSONL log and logs critical rules at ERROR level.
An external monitor can tail that file without reading application logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.sitegraph.metrics import append_jsonl, parse_iso, utc_now

ALERT_KIND = "nga_alert"
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class AlertThresholds:
    """Tunable bounds; the CLI loads these from ``NGA_ALERT_*`` settings."""

    coverage_min: float = 0.95
    coverage_drop: float = 0.02
    graph_stale_hours: float = 168.0
    run_failure_streak: int = 3
    node_failure_streak: int = 3
    error_rate: float = 0.5
    empty_shell_rate: float = 0.35
    soft_404_rate: float = 0.25
    document_error_rate: float = 0.5
    review_backlog: int = 25
    reference_failures: int = 3
    template_required: int = 3
    min_checked_for_rates: int = 5
    stale_nodes: int = 50


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str
    run_id: str | None = None
    observed: object | None = None
    threshold: object | None = None
    context: dict = field(default_factory=dict)
    created_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": ALERT_KIND,
            "schema_version": 1,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "run_id": self.run_id,
            "observed": self.observed,
            "threshold": self.threshold,
            "context": dict(self.context),
            "created_at": self.created_at
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def _alert(
    code: str,
    severity: str,
    message: str,
    *,
    record: dict,
    observed: object | None = None,
    threshold: object | None = None,
    context: dict | None = None,
    now: str | None = None,
) -> Alert:
    return Alert(
        code=code,
        severity=severity,
        message=message,
        run_id=record.get("run_id"),
        observed=observed,
        threshold=threshold,
        context=context or {},
        created_at=now,
    )


def _stats(record: dict) -> dict:
    value = record.get("stats")
    return value if isinstance(value, dict) else {}


def _errors(record: dict) -> int:
    try:
        return int(_stats(record).get("errors") or 0)
    except (TypeError, ValueError):
        return 0


def _checked(record: dict) -> int:
    try:
        return int(_stats(record).get("checked") or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _previous_coverage(history: list[dict], compile_run_id: object) -> dict | None:
    for prior in reversed(history):
        graph = prior.get("graph") or {}
        if graph.get("coverage_pct") is None:
            continue
        if compile_run_id is not None and graph.get("compile_run_id") == compile_run_id:
            continue
        return graph
    return None


def _failure_streak(record: dict, history: list[dict]) -> int:
    if _errors(record) <= 0:
        return 0
    streak = 1
    for prior in reversed(history):
        if _errors(prior) > 0:
            streak += 1
        else:
            break
    return streak


def _graph_age_hours(compiled_at: object, now: datetime) -> float | None:
    parsed = parse_iso(str(compiled_at) if compiled_at else None)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def evaluate_alerts(
    record: dict,
    *,
    history: list[dict] | tuple[dict, ...] = (),
    thresholds: AlertThresholds | None = None,
    now: str | None = None,
) -> list[Alert]:
    """Evaluate every deterministic rule against one run record.

    ``history`` is oldest-to-newest and must not contain the current record.
    """
    limits = thresholds or AlertThresholds()
    history = list(history)
    now_iso = now or record.get("finished_at") or utc_now()
    now_dt = parse_iso(now_iso) or datetime.now(timezone.utc)
    graph = record.get("graph") or {}
    freshness = record.get("freshness") or {}
    stats = _stats(record)
    alerts: list[Alert] = []

    if not graph.get("exists") or not graph.get("compiled_at"):
        alerts.append(
            _alert(
                "graph_missing",
                SEVERITY_CRITICAL,
                "No compiled navigation graph is available; run the graph compiler.",
                record=record,
                now=now_iso,
            )
        )
    else:
        coverage = _as_float(graph.get("coverage_pct"))
        gate = _as_float(graph.get("coverage_min"))
        if gate is None:
            gate = limits.coverage_min
        if coverage is not None and coverage < gate:
            alerts.append(
                _alert(
                    "coverage_below_gate",
                    SEVERITY_CRITICAL,
                    f"Sitemap coverage {coverage:.1%} is below the {gate:.1%} gate.",
                    record=record,
                    observed=coverage,
                    threshold=gate,
                    now=now_iso,
                )
            )
        previous = _previous_coverage(history, graph.get("compile_run_id"))
        if coverage is not None and previous is not None:
            prior_coverage = _as_float(previous.get("coverage_pct"))
            if prior_coverage is not None:
                drop = prior_coverage - coverage
                if drop >= limits.coverage_drop:
                    alerts.append(
                        _alert(
                            "coverage_drop",
                            SEVERITY_WARNING,
                            (
                                f"Sitemap coverage dropped {drop:.1%} "
                                f"({prior_coverage:.1%} to {coverage:.1%})."
                            ),
                            record=record,
                            observed=coverage,
                            threshold=prior_coverage - limits.coverage_drop,
                            context={"previous_coverage_pct": prior_coverage},
                            now=now_iso,
                        )
                    )
                previous_sections = set(previous.get("top_level_sections") or [])
                current_sections = set(graph.get("top_level_sections") or [])
                missing_sections = sorted(previous_sections - current_sections)
                if missing_sections:
                    alerts.append(
                        _alert(
                            "section_missing",
                            SEVERITY_WARNING,
                            (
                                "Top-level navigation section(s) disappeared: "
                                + ", ".join(missing_sections)
                            ),
                            record=record,
                            observed=missing_sections,
                            context={
                                "previous_sections": sorted(previous_sections),
                                "current_sections": sorted(current_sections),
                            },
                            now=now_iso,
                        )
                    )
        age_hours = _graph_age_hours(graph.get("compiled_at"), now_dt)
        if age_hours is not None and age_hours > limits.graph_stale_hours:
            alerts.append(
                _alert(
                    "graph_stale",
                    SEVERITY_WARNING,
                    (
                        f"The navigation graph was compiled {age_hours:.0f} hours "
                        f"ago (limit {limits.graph_stale_hours:.0f}h)."
                    ),
                    record=record,
                    observed=round(age_hours, 1),
                    threshold=limits.graph_stale_hours,
                    context={"compiled_at": graph.get("compiled_at")},
                    now=now_iso,
                )
            )

    streak = _failure_streak(record, history)
    if streak >= max(1, limits.run_failure_streak):
        alerts.append(
            _alert(
                "refresh_failure_streak",
                SEVERITY_CRITICAL,
                f"{streak} consecutive refresh runs reported page errors.",
                record=record,
                observed=streak,
                threshold=limits.run_failure_streak,
                now=now_iso,
            )
        )

    failing_nodes = _as_int(freshness.get("failure_streak"))
    if failing_nodes > 0:
        alerts.append(
            _alert(
                "node_failure_streak",
                SEVERITY_WARNING,
                (
                    f"{failing_nodes} page(s) failed at least "
                    f"{limits.node_failure_streak} consecutive fetches."
                ),
                record=record,
                observed=failing_nodes,
                threshold=limits.node_failure_streak,
                now=now_iso,
            )
        )

    checked = _checked(record)
    if checked >= max(1, limits.min_checked_for_rates):
        rate_rules = (
            (
                "refresh_error_rate",
                _as_int(stats.get("errors")) / checked,
                limits.error_rate,
                "of checked pages failed to fetch",
                SEVERITY_WARNING,
            ),
            (
                "empty_shell_spike",
                _as_int(stats.get("empty")) / checked,
                limits.empty_shell_rate,
                "of checked pages were empty shells",
                SEVERITY_WARNING,
            ),
            (
                "soft_404_spike",
                _as_int(stats.get("soft_404")) / checked,
                limits.soft_404_rate,
                "of checked pages were soft 404s",
                SEVERITY_WARNING,
            ),
        )
        for code, rate, limit, wording, severity in rate_rules:
            if rate >= limit:
                alerts.append(
                    _alert(
                        code,
                        severity,
                        f"{rate:.0%} {wording} (limit {limit:.0%}).",
                        record=record,
                        observed=round(rate, 4),
                        threshold=limit,
                        context={"checked": checked},
                        now=now_iso,
                    )
                )

    documents = record.get("documents")
    if isinstance(documents, dict):
        doc_checked = _as_int(documents.get("checked"))
        doc_errors = _as_int(documents.get("errors"))
        if (
            doc_checked >= max(1, limits.min_checked_for_rates)
            and doc_errors / doc_checked >= limits.document_error_rate
        ):
            alerts.append(
                _alert(
                    "document_error_rate",
                    SEVERITY_WARNING,
                    (
                        f"{doc_errors / doc_checked:.0%} of checked documents "
                        "failed fetch or extraction "
                        f"(limit {limits.document_error_rate:.0%})."
                    ),
                    record=record,
                    observed=round(doc_errors / doc_checked, 4),
                    threshold=limits.document_error_rate,
                    context={"checked": doc_checked, "errors": doc_errors},
                    now=now_iso,
                )
            )

    document_index = record.get("document_index")
    if isinstance(document_index, dict):
        review = _as_int(
            document_index.get("review_pending")
            if document_index.get("review_pending") is not None
            else document_index.get("needs_review")
        )
        if review >= max(1, limits.review_backlog):
            alerts.append(
                _alert(
                    "document_review_backlog",
                    SEVERITY_WARNING,
                    (
                        f"{review} OCR document(s) are awaiting human review; "
                        "their text is not answerable."
                    ),
                    record=record,
                    observed=review,
                    threshold=limits.review_backlog,
                    now=now_iso,
                )
            )

    reference_window = record.get("reference_delivery_window")
    if isinstance(reference_window, dict):
        failures = _as_int(reference_window.get("failed"))
        if failures >= max(1, limits.reference_failures):
            alerts.append(
                _alert(
                    "reference_send_failures",
                    SEVERITY_WARNING,
                    (
                        f"{failures} WhatsApp reference send(s) failed in the "
                        "alert window."
                    ),
                    record=record,
                    observed=failures,
                    threshold=limits.reference_failures,
                    now=now_iso,
                )
            )
        template_required = _as_int(
            reference_window.get("template_required")
        )
        if template_required >= max(1, limits.template_required):
            alerts.append(
                _alert(
                    "reference_template_required",
                    SEVERITY_WARNING,
                    (
                        f"{template_required} reference send(s) needed an "
                        "approved template outside the 24-hour window."
                    ),
                    record=record,
                    observed=template_required,
                    threshold=limits.template_required,
                    now=now_iso,
                )
            )

    stale = _as_int(freshness.get("stale"))
    if stale >= max(1, limits.stale_nodes):
        alerts.append(
            _alert(
                "stale_backlog",
                SEVERITY_WARNING,
                (
                    f"{stale} page(s) are overdue by more than the grace period; "
                    "the refresh worker is falling behind."
                ),
                record=record,
                observed=stale,
                threshold=limits.stale_nodes,
                now=now_iso,
            )
        )

    if graph.get("pii_guard_mismatch"):
        alerts.append(
            _alert(
                "pii_guard_mismatch",
                SEVERITY_WARNING,
                (
                    "The graph was compiled with a different PII guard setting "
                    "than the runtime uses; recompile or change the flag."
                ),
                record=record,
                observed={
                    "compiled": graph.get("compiled_pii_guard"),
                    "runtime": (record.get("pii_guard") or {}).get(
                        "runtime_enabled"
                    ),
                },
                now=now_iso,
            )
        )

    return alerts


def emit_alerts(path: str | Path, alerts: list[Alert] | tuple[Alert, ...]) -> Path | None:
    """Append alerts to the JSONL alert log; returns the path when written."""
    if not alerts:
        return None
    target = Path(path)
    for alert in alerts:
        append_jsonl(target, alert.to_dict())
    return target


def render_alert(alert: Alert) -> str:
    return f"[{alert.severity.upper()}] {alert.code}: {alert.message}"
