"""Shadow execution at the existing answer seam.

Shadow mode runs the NGA planner, executor and evidence assembler on a copy of
the question, but **never changes the answer the caller receives**. It writes
one JSONL record per sampled turn so retrieval/answer deltas can be reviewed
before the live path is enabled.

Shadow is active only with ``NGA_ENABLED=true`` and ``NGA_MODE=shadow``. It is
bounded by worker count, sample rate, step/deadline/byte budgets, and it uses
the local graph only — no web fetch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from app.agent.answer import answer_from_execution
from app.agent.evidence import assemble_evidence
from app.agent.executor import execute_plan
from app.agent.facts import FactStore
from app.agent.planner import Planner
from app.config import settings
from app.context_bundle import ContextBundle
from app.sitegraph.tools import SiteToolServer

logger = logging.getLogger("agent.shadow")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shadow_enabled() -> bool:
    """The master flag and the mode must both select shadow."""
    return bool(
        settings.NGA_ENABLED
        and (settings.NGA_MODE or "").strip().lower() == "shadow"
    )


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _static_summary(bundle: ContextBundle | None) -> dict:
    if bundle is None:
        return {}
    return {
        "kind": bundle.kind,
        "node_ids": list(bundle.node_ids),
        "fact_ids": list(bundle.fact_ids),
        "notice_ids": list(bundle.notice_ids),
        "source_urls": list(bundle.source_urls),
        "valid_until": bundle.valid_until,
        "chars": len(bundle.text or ""),
    }


class ShadowRunner:
    """Run and log shadow turns without touching the caller's answer."""

    def __init__(
        self,
        graph_path: str | Path,
        log_path: str | Path,
        *,
        sample_rate: float = 1.0,
        generate: bool = False,
        log_queries: bool = False,
        deadline_ms: int = 8000,
        max_steps: int = 8,
        max_chars: int = 3000,
        max_workers: int = 2,
        pii_guard: bool | None = None,
        llm=None,
    ) -> None:
        self.graph_path = _resolve(str(graph_path))
        self.log_path = _resolve(str(log_path))
        self.sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self.generate = bool(generate)
        self.log_queries = bool(log_queries)
        self.deadline_ms = max(1, int(deadline_ms))
        self.max_steps = max(1, int(max_steps))
        self.max_chars = max(1, int(max_chars))
        self.pii_guard = (
            settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
        )
        self.llm = llm
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="nga-shadow",
        )
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._futures: set[Future] = set()

    def run(self, query: str, static_bundle: ContextBundle | None = None) -> dict:
        """Run one shadow turn synchronously and append its record."""
        started = _now()
        facts = FactStore()
        server = SiteToolServer(self.graph_path, pii_guard=self.pii_guard)
        plan = Planner().plan(query)
        execution = execute_plan(
            server,
            plan,
            max_steps=self.max_steps,
            deadline_ms=self.deadline_ms,
        )
        bundle = assemble_evidence(
            execution,
            query=query,
            facts=facts,
            max_chars=self.max_chars,
            pii_guard=self.pii_guard,
        )
        answer_result = None
        if self.generate:
            answer_result = answer_from_execution(
                execution,
                query=query,
                facts=facts,
                llm=self.llm,
                pii_guard=self.pii_guard,
            )
        static = _static_summary(static_bundle)
        static_ids = set(static.get("node_ids") or []) | set(
            static.get("fact_ids") or []
        ) | set(static.get("notice_ids") or [])
        shadow_ids = {item.node_id for item in bundle.items if item.node_id}

        execution_dict = execution.to_dict()
        evidence_dict = bundle.to_dict()
        answer_dict = answer_result.to_dict() if answer_result else None
        if not self.log_queries:
            # The query may contain a phone number or other personal data.
            # Keep the plan shape and evidence, but not the raw question.
            evidence_dict["query"] = None
            for result in execution_dict.get("results") or []:
                data = result.get("data") or {}
                if "normalized_query" in data:
                    data["normalized_query"] = None
            if answer_dict is not None:
                answer_dict["query"] = None
                if isinstance(answer_dict.get("evidence"), dict):
                    answer_dict["evidence"]["query"] = None

        record = {
            "timestamp": started,
            "source": "answer_seam",
            "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
            "query": query if self.log_queries else None,
            "query_chars": len(query),
            "sampled": True,
            "static": static,
            "shadow": {
                "plan": {
                    "intent": plan.intent.value,
                    "steps": [
                        (
                            {"tool": step.tool, "args": step.args}
                            if self.log_queries else {"tool": step.tool}
                        )
                        for step in plan.steps
                    ],
                },
                "execution": execution_dict,
                "evidence": evidence_dict,
                "answer": answer_dict,
            },
            "delta": {
                "static_count": len(static_ids),
                "shadow_count": len(shadow_ids),
                "new_ids": sorted(shadow_ids - static_ids)[:20],
                "overlap_ids": sorted(shadow_ids & static_ids)[:20],
                "only_static_ids": sorted(static_ids - shadow_ids)[:20],
            },
            "latency_ms": execution.elapsed_ms,
        }
        self._append(record)
        return record

    def submit(self, query: str, static_bundle: ContextBundle | None = None) -> bool:
        """Schedule a shadow turn; returns False when sampled out or closed."""
        if not (query or "").strip():
            return False
        if self.sample_rate < 1.0 and random.random() > self.sample_rate:
            return False
        try:
            future = self._pool.submit(self._run_and_log, query, static_bundle)
        except RuntimeError:
            logger.warning("shadow pool is closed; dropping turn")
            return False
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._forget)
        return True

    def _run_and_log(self, query: str, static_bundle: ContextBundle | None) -> None:
        try:
            self.run(query, static_bundle)
        except Exception:  # noqa: BLE001 - shadow must never affect the turn
            logger.exception("shadow turn failed")

    def _forget(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    def flush(self, timeout: float = 15.0) -> None:
        with self._lock:
            pending = list(self._futures)
        if pending:
            wait(pending, timeout=timeout)

    def close(self) -> None:
        self.flush()
        self._pool.shutdown(wait=True)

    def _append(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


_default_lock = threading.Lock()
_default: ShadowRunner | None = None
_default_config: tuple | None = None


def _config_signature() -> tuple:
    return (
        settings.NGA_GRAPH_SQLITE_PATH,
        settings.NGA_SHADOW_LOG_PATH,
        settings.NGA_SHADOW_SAMPLE_RATE,
        settings.NGA_SHADOW_GENERATE,
        settings.NGA_SHADOW_LOG_QUERIES,
        settings.NGA_SHADOW_DEADLINE_MS,
        settings.NGA_SHADOW_MAX_STEPS,
        settings.NGA_SHADOW_MAX_CHARS,
        settings.NGA_SHADOW_MAX_WORKERS,
        settings.NGA_PII_GUARD_ENABLED,
    )


def get_shadow_runner() -> ShadowRunner:
    global _default, _default_config
    signature = _config_signature()
    with _default_lock:
        if _default is None or _default_config != signature:
            _default = ShadowRunner(
                settings.NGA_GRAPH_SQLITE_PATH,
                settings.NGA_SHADOW_LOG_PATH,
                sample_rate=settings.NGA_SHADOW_SAMPLE_RATE,
                generate=settings.NGA_SHADOW_GENERATE,
                log_queries=settings.NGA_SHADOW_LOG_QUERIES,
                deadline_ms=settings.NGA_SHADOW_DEADLINE_MS,
                max_steps=settings.NGA_SHADOW_MAX_STEPS,
                max_chars=settings.NGA_SHADOW_MAX_CHARS,
                max_workers=settings.NGA_SHADOW_MAX_WORKERS,
                pii_guard=settings.NGA_PII_GUARD_ENABLED,
            )
            _default_config = signature
        return _default


def reset_shadow_runner() -> None:
    """Close and forget the process-wide runner (tests and shutdown)."""
    global _default, _default_config
    with _default_lock:
        runner = _default
        _default = None
        _default_config = None
    if runner is not None:
        runner.close()


def submit_shadow(query: str, static_bundle: ContextBundle | None = None) -> bool:
    """Submit a shadow turn iff shadow mode is enabled. Never raises."""
    try:
        if not shadow_enabled():
            return False
        return get_shadow_runner().submit(query, static_bundle)
    except Exception:  # noqa: BLE001 - shadow must never affect the answer
        logger.exception("shadow submission failed")
        return False


__all__ = [
    "ShadowRunner",
    "get_shadow_runner",
    "reset_shadow_runner",
    "shadow_enabled",
    "submit_shadow",
]
