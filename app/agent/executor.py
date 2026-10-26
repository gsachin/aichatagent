"""Bounded execution of an NGA plan against the read-only tool server.

The executor enforces step, byte and deadline budgets. It may add one
``site.open`` after a successful ``site.search`` so the evidence assembler has
page text, but it never invents a tool call or a URL. The synchronous path is
used by the voice fast path and tests; the asynchronous path runs each tool in
a worker thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from app.agent.models import Intent, Plan, PlanStep
from app.agent.planner import MAX_STEPS, PlanError, validate_plan
from app.sitegraph.tools import SiteToolServer

logger = logging.getLogger("agent.executor")


@dataclass
class ExecutionResult:
    plan: Plan
    results: list[dict] = field(default_factory=list)
    status: str = "ok"  # ok | degraded | blocked | deadline | budget
    errors: list[dict] = field(default_factory=list)
    steps_used: int = 0
    elapsed_ms: float = 0.0
    deadline_exceeded: bool = False
    synthetic_steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intent": self.plan.intent.value,
            "status": self.status,
            "steps_used": self.steps_used,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "deadline_exceeded": self.deadline_exceeded,
            "errors": self.errors,
            "synthetic_steps": self.synthetic_steps,
            "results": self.results,
        }


def _serialized_size(result: dict) -> int:
    try:
        return len(json.dumps(result, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 0


def _auto_open_step(result: dict) -> PlanStep | None:
    if result.get("tool") != "site.search" or result.get("status") != "ok":
        return None
    data = result.get("data") or {}
    results = data.get("results") or []
    for item in results:
        if not item.get("node_id"):
            continue
        content_type = item.get("content_type")
        if content_type == "html" or (
            content_type == "pdf" and item.get("content_status") == "indexed"
        ):
            return PlanStep(
                "site.open",
                {"node_id": item["node_id"], "max_chars": 4000},
            )
    return None


def _classify_status(results: list[dict], errors: list[dict], deadline: bool) -> str:
    if deadline:
        return "deadline"
    if any(error.get("status") == "BLOCKED" for error in errors):
        return "blocked"
    if errors:
        return "degraded"
    return "ok" if results else "degraded"


def execute_plan(
    server: SiteToolServer,
    plan: Plan,
    *,
    max_steps: int = MAX_STEPS,
    deadline_ms: int = 8000,
    max_bytes: int = 16000,
    auto_open: bool = True,
    clock=time.monotonic,
) -> ExecutionResult:
    """Execute *plan* synchronously within hard budgets."""
    plan = validate_plan(plan, max_steps=max_steps)
    started = clock()
    deadline = started + max(0.0, deadline_ms / 1000)
    steps = list(plan.steps)
    has_open = any(step.tool == "site.open" for step in steps)
    result = ExecutionResult(plan=plan)
    used_bytes = 0

    def run(step: PlanStep, synthetic: bool = False) -> dict:
        nonlocal used_bytes
        if clock() >= deadline:
            result.deadline_exceeded = True
            return {}
        called = server.call(step.tool, step.args)
        result.steps_used += 1
        used_bytes += _serialized_size(called)
        result.results.append(called)
        if synthetic:
            result.synthetic_steps.append(
                {"tool": step.tool, "args": step.args, "reason": "auto_open"}
            )
        if called.get("status") != "ok":
            result.errors.append(
                {
                    "tool": step.tool,
                    "status": called.get("status"),
                    "error": called.get("error"),
                }
            )
        return called

    for step in steps:
        if result.deadline_exceeded or used_bytes > max_bytes:
            break
        called = run(step)
        if not called:
            break
        if called.get("status") != "ok":
            if called.get("status") in (
                "BLOCKED", "INVALID_TOOL", "INVALID_ARGUMENT", "TIMEOUT"
            ):
                break
            continue
        if used_bytes > max_bytes:
            break
        if (
            auto_open
            and not has_open
            and step.tool == "site.search"
        ):
            open_step = _auto_open_step(called)
            if open_step is not None:
                has_open = True
                run(open_step, synthetic=True)

    result.elapsed_ms = (clock() - started) * 1000
    result.status = _classify_status(
        result.results, result.errors, result.deadline_exceeded
    )
    if (
        plan.intent is Intent.BOUNDARY
        and not result.results
        and not result.errors
        and not result.deadline_exceeded
    ):
        result.status = "ok"
    if used_bytes > max_bytes and result.status == "ok":
        result.status = "budget"
    return result


async def async_execute_plan(
    server: SiteToolServer,
    plan: Plan,
    *,
    max_steps: int = MAX_STEPS,
    deadline_ms: int = 8000,
    max_bytes: int = 16000,
    auto_open: bool = True,
    clock=time.monotonic,
) -> ExecutionResult:
    """Execute *plan* with each sync tool call in a worker thread."""
    plan = validate_plan(plan, max_steps=max_steps)
    started = clock()
    deadline = started + max(0.0, deadline_ms / 1000)
    steps = list(plan.steps)
    has_open = any(step.tool == "site.open" for step in steps)
    result = ExecutionResult(plan=plan)
    used_bytes = 0

    async def run(step: PlanStep, synthetic: bool = False) -> dict:
        nonlocal used_bytes
        remaining = deadline - clock()
        if remaining <= 0:
            result.deadline_exceeded = True
            return {}
        try:
            called = await asyncio.wait_for(
                asyncio.to_thread(server.call, step.tool, step.args),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            result.deadline_exceeded = True
            result.errors.append(
                {"tool": step.tool, "status": "TIMEOUT", "error": "deadline"}
            )
            return {}
        result.steps_used += 1
        used_bytes += _serialized_size(called)
        result.results.append(called)
        if synthetic:
            result.synthetic_steps.append(
                {"tool": step.tool, "args": step.args, "reason": "auto_open"}
            )
        if called.get("status") != "ok":
            result.errors.append(
                {
                    "tool": step.tool,
                    "status": called.get("status"),
                    "error": called.get("error"),
                }
            )
        return called

    for step in steps:
        if result.deadline_exceeded or used_bytes > max_bytes:
            break
        called = await run(step)
        if not called:
            break
        if called.get("status") != "ok":
            if called.get("status") in (
                "BLOCKED", "INVALID_TOOL", "INVALID_ARGUMENT", "TIMEOUT"
            ):
                break
            continue
        if used_bytes > max_bytes:
            break
        if auto_open and not has_open and step.tool == "site.search":
            open_step = _auto_open_step(called)
            if open_step is not None:
                has_open = True
                await run(open_step, synthetic=True)

    result.elapsed_ms = (clock() - started) * 1000
    result.status = _classify_status(
        result.results, result.errors, result.deadline_exceeded
    )
    if (
        plan.intent is Intent.BOUNDARY
        and not result.results
        and not result.errors
        and not result.deadline_exceeded
    ):
        result.status = "ok"
    if used_bytes > max_bytes and result.status == "ok":
        result.status = "budget"
    return result


#: A plan with no steps is valid for a deterministic boundary/refusal.
def is_empty_plan(plan: Plan) -> bool:
    return plan.intent in (Intent.BOUNDARY, Intent.UNKNOWN) and not plan.steps


__all__ = [
    "ExecutionResult",
    "PlanError",
    "async_execute_plan",
    "execute_plan",
    "is_empty_plan",
]
