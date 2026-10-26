"""Single-writer refresh scheduler for the NGA graph (Phase 10).

The scheduler is a separate background process from the API. It holds an
advisory file lock for the duration of one refresh cycle, runs the Phase 3
refresh, writes the run report and history, then evaluates the deterministic
alert rules. It never runs on the answer path.

``NGA_SCHEDULER_ENABLED=false`` keeps it inert even when the answer-path
``NGA_ENABLED`` flag is on.
"""

from __future__ import annotations

import logging
import os
import random
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.sitegraph.alerts import (
    SEVERITY_CRITICAL,
    Alert,
    AlertThresholds,
    emit_alerts,
    evaluate_alerts,
)
from app.sitegraph.documents import (
    DocumentOptions,
    DocumentStats,
    refresh_documents,
)
from app.sitegraph.metrics import (
    append_jsonl,
    atomic_write_json,
    build_run_record,
    read_recent_runs,
    utc_now,
)
from app.sitegraph.refresh import RefreshOptions, RefreshStats, refresh_graph
from app.sitegraph.store import GraphStore

try:  # POSIX deployment target
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback below
    fcntl = None  # type: ignore[assignment]
try:  # pragma: no cover - only used when fcntl is unavailable
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]

logger = logging.getLogger("sitegraph.scheduler")


class LockHeld(RuntimeError):
    """Another refresh worker already owns the graph lock."""


class RefreshLock:
    """Non-blocking advisory lock around one refresh cycle."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - neither backend exists
                raise RuntimeError("no advisory file-lock backend available")
        except OSError as exc:
            os.close(fd)
            raise LockHeld(f"refresh lock is held: {self.path}") from exc
        self._fd = fd
        try:
            self._write_owner()
        except OSError:
            # The lock itself is held; the owner metadata is best-effort.
            logger.warning("could not write owner metadata to %s", self.path)

    def _write_owner(self) -> None:
        if self._fd is None:
            return
        payload = (
            f"pid={os.getpid()} host={socket.gethostname()} "
            f"acquired_at={utc_now()}\n"
        ).encode("utf-8")
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, payload)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            try:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
            except OSError:
                logger.warning("could not clear the refresh lock file %s", self.path)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            # Closing the descriptor below still releases an flock.
            logger.warning("could not unlock %s cleanly", self.path)
        finally:
            os.close(fd)

    def __enter__(self) -> RefreshLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


@dataclass
class CycleResult:
    record: dict | None = None
    alerts: list[Alert] = field(default_factory=list)
    skipped: bool = False
    error: str | None = None

    @property
    def had_errors(self) -> bool:
        if not self.record:
            return False
        try:
            return int((self.record.get("stats") or {}).get("errors") or 0) > 0
        except (TypeError, ValueError):
            return False

    @property
    def critical_alerts(self) -> list[Alert]:
        return [item for item in self.alerts if item.severity == SEVERITY_CRITICAL]


@dataclass
class RefreshScheduler:
    """One-cycle-at-a-time refresh worker with a latest report and history."""

    store: GraphStore
    fetcher: object
    options: RefreshOptions
    report_path: str | Path
    run_log_path: str | Path
    alert_log_path: str | Path
    lock_path: str | Path
    thresholds: AlertThresholds = field(default_factory=AlertThresholds)
    pii_guard_enabled: bool = False
    document_options: DocumentOptions | None = None
    stale_grace_s: float = 7200
    reference_window_s: float = 3600
    interval_s: float = 900
    jitter_s: float = 60
    max_cycles: int = 0
    history_limit: int = 100
    now_fn: Callable[[], str] = utc_now
    random_fn: Callable[[], float] = random.random

    def _now(self) -> str:
        return str(self.now_fn())

    def _scheduler_metadata(self) -> dict:
        return {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "interval_s": self.interval_s,
            "jitter_s": self.jitter_s,
            "documents_enabled": self.document_options is not None,
        }

    def run_once(self) -> CycleResult:
        """Run one refresh cycle. A held lock skips the cycle, not the loop."""
        lock = RefreshLock(self.lock_path)
        try:
            lock.acquire()
        except LockHeld:
            logger.warning("refresh skipped: another worker holds %s", self.lock_path)
            return CycleResult(skipped=True)

        started_at = self._now()
        error: str | None = None
        document_stats: dict | None = None
        try:
            stats = refresh_graph(self.store, self.fetcher, self.options)
            if self.document_options is not None:
                try:
                    document_stats = refresh_documents(
                        self.store,
                        self.fetcher,
                        self.document_options,
                        pii_guard=self.pii_guard_enabled,
                    ).to_dict()
                except Exception as exc:  # noqa: BLE001 - page work is complete
                    logger.exception("document refresh cycle failed")
                    error = str(exc)
                    document_stats = DocumentStats(
                        errors=1,
                        details=[{"status": "run_error", "error": str(exc)}],
                    ).to_dict()
        except Exception as exc:  # noqa: BLE001 - the loop must survive one cycle
            logger.exception("refresh cycle failed")
            error = str(exc)
            stats = RefreshStats(
                errors=1,
                details=[{"status": "run_error", "error": str(exc)}],
            )
        finally:
            lock.release()

        finished_at = self._now()
        record = build_run_record(
            self.store,
            stats,
            started_at=started_at,
            finished_at=finished_at,
            options=self.options,
            pii_guard_enabled=self.pii_guard_enabled,
            failure_streak=max(1, self.thresholds.node_failure_streak),
            stale_grace_s=self.stale_grace_s,
            scheduler=self._scheduler_metadata(),
            document_stats=document_stats,
            reference_window_s=self.reference_window_s,
        )
        try:
            atomic_write_json(self.report_path, record)
            append_jsonl(self.run_log_path, record)
        except OSError:
            logger.exception("could not persist the refresh report or history")

        history = [
            row
            for row in read_recent_runs(self.run_log_path, limit=self.history_limit)
            if row.get("run_id") != record["run_id"]
        ]
        alerts = evaluate_alerts(
            record,
            history=history,
            thresholds=self.thresholds,
            now=finished_at,
        )
        try:
            emit_alerts(self.alert_log_path, alerts)
        except OSError:
            logger.exception("could not append to the alert log")

        for alert in alerts:
            if alert.severity == SEVERITY_CRITICAL:
                logger.error("%s", alert.message)
            else:
                logger.warning("%s", alert.message)
        return CycleResult(record=record, alerts=alerts, error=error)

    def run_forever(
        self,
        stop_event: threading.Event | None = None,
        on_cycle: Callable[[CycleResult], None] | None = None,
    ) -> list[CycleResult]:
        """Run until stopped, ``max_cycles`` or the stop event fires."""
        stop_event = stop_event or threading.Event()
        results: list[CycleResult] = []
        while not stop_event.is_set():
            result = self.run_once()
            results.append(result)
            if on_cycle is not None:
                on_cycle(result)
            if self.max_cycles and len(results) >= self.max_cycles:
                break
            delay = max(0.0, float(self.interval_s))
            delay += max(0.0, float(self.jitter_s)) * float(self.random_fn())
            if stop_event.wait(delay):
                break
        return results
