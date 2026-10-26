"""Operational records for the background NGA refresh worker (Phase 10).

Every refresh cycle produces one dict with the run statistics, graph coverage,
freshness counters and the effective options. The scheduler writes it as an
atomic "latest" JSON file and appends the same record to a JSONL history used
by the alert rules. Nothing here is read by the live answer path.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.sitegraph.refresh import RefreshOptions, RefreshStats
from app.sitegraph.store import GraphStore

SCHEMA_VERSION = 1
RECORD_KIND = "nga_refresh_run"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse the store's UTC ``...Z`` timestamps without raising."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_before(now_iso: str, seconds: float) -> str:
    """Return ``now_iso`` minus ``seconds`` as a store-format timestamp."""
    parsed = parse_iso(now_iso)
    if parsed is None:
        return now_iso
    return datetime.fromtimestamp(
        parsed.timestamp() - max(0.0, float(seconds)), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration_s(started_at: str, finished_at: str) -> float | None:
    start = parse_iso(started_at)
    finish = parse_iso(finished_at)
    if start is None or finish is None:
        return None
    return round(max(0.0, (finish - start).total_seconds()), 3)


def _graph_snapshot(store: GraphStore, pii_guard_enabled: bool) -> dict:
    latest = store.latest_run()
    coverage = (latest or {}).get("coverage") or {}
    compiled_guard = coverage.get("pii_guard")
    mismatch = (
        None
        if compiled_guard is None
        else bool(compiled_guard) != bool(pii_guard_enabled)
    )
    return {
        "exists": latest is not None,
        "compile_run_id": (latest or {}).get("run_id"),
        "compiled_at": (latest or {}).get("generated_at"),
        "source_mode": (latest or {}).get("source_mode"),
        "graph_hash": (latest or {}).get("graph_hash"),
        "nodes": (latest or {}).get("nodes") or store.node_count(),
        "edges": (latest or {}).get("edges") or store.edge_count(),
        "node_status": store.node_status_counts(),
        "coverage_pct": coverage.get("coverage_pct"),
        "coverage_min": coverage.get("min_coverage"),
        "meets_gate": coverage.get("meets_gate"),
        "top_level_sections": list(coverage.get("top_level_sections") or []),
        "missing_sitemap_urls": len(coverage.get("missing_sitemap_urls") or []),
        "compiled_pii_guard": compiled_guard,
        "pii_guard_mismatch": mismatch,
    }


def build_run_record(
    store: GraphStore,
    stats: RefreshStats,
    *,
    started_at: str,
    finished_at: str,
    options: RefreshOptions | None,
    pii_guard_enabled: bool = False,
    failure_streak: int = 3,
    stale_grace_s: float = 7200,
    scheduler: dict | None = None,
    document_stats: dict | None = None,
    reference_window_s: float = 3600,
) -> dict:
    """Assemble one refresh run record from the store and the run stats."""
    freshness = store.fetch_summary(
        now=finished_at,
        failure_streak=failure_streak,
        stale_before=iso_before(finished_at, stale_grace_s),
    )
    graph = _graph_snapshot(store, pii_guard_enabled)
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "run_id": f"{started_at}-{uuid.uuid4().hex[:8]}",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": _duration_s(started_at, finished_at),
        "stats": stats.to_dict(),
        "graph": graph,
        "freshness": freshness,
        "pii_guard": {
            "runtime_enabled": bool(pii_guard_enabled),
            "compiled_enabled": graph["compiled_pii_guard"],
            "mismatch": graph["pii_guard_mismatch"],
        },
        "documents": document_stats,
        "document_index": store.document_summary(),
        "reference_delivery": store.reference_delivery_summary(),
        "reference_delivery_window": store.reference_delivery_summary(
            since_iso=iso_before(finished_at, reference_window_s)
        ),
    }
    if options is not None:
        record["options"] = {
            "ttl_s": options.ttl_s,
            "limit": options.limit,
            "force": options.force,
            "min_chars": options.min_chars,
            "node_ids": list(options.node_ids),
            "section": options.section,
        }
    if scheduler:
        record["scheduler"] = dict(scheduler)
    return record


def build_snapshot_record(
    store: GraphStore,
    *,
    pii_guard_enabled: bool = False,
    failure_streak: int = 3,
    stale_grace_s: float = 7200,
    reference_window_s: float = 3600,
    now: str | None = None,
) -> dict:
    """A no-run record for the offline status command."""
    now = now or utc_now()
    record = build_run_record(
        store,
        RefreshStats(),
        started_at=now,
        finished_at=now,
        options=None,
        pii_guard_enabled=pii_guard_enabled,
        failure_streak=failure_streak,
        stale_grace_s=stale_grace_s,
        reference_window_s=reference_window_s,
    )
    record["snapshot"] = True
    record["run_id"] = f"snapshot-{now}"
    return record


def atomic_write_json(path: str | Path, payload: dict) -> Path:
    """Write the latest report with a same-directory rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return target


def append_jsonl(path: str | Path, row: dict) -> Path:
    """Append one JSON object to a JSONL file, creating its directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
    return target


def _tail_lines(path: Path, limit: int, max_bytes: int = 5_000_000) -> list[str]:
    """Read at most ``limit`` lines from the end without loading the file."""
    chunk_size = 64 * 1024
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        position = end
        data = b""
        while position > 0 and data.count(b"\n") <= limit:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size) + data
            if len(data) >= max_bytes:
                break
    text = data.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def read_recent_runs(path: str | Path, limit: int = 20) -> list[dict]:
    """Read the newest run records, ignoring malformed or foreign lines.

    ``limit > 0`` reads only the file tail: the alert rules need a short
    history, not a month of runs.
    """
    target = Path(path)
    if not target.is_file():
        return []
    try:
        if limit > 0:
            lines = _tail_lines(target, limit)
        else:
            lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("kind", RECORD_KIND) != RECORD_KIND:
            continue
        rows.append(row)
    if limit > 0:
        return rows[-limit:]
    return rows


def read_report(path: str | Path) -> dict | None:
    """Read the pretty-printed latest report (not the JSONL history)."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
