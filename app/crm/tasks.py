"""
Fire-and-forget coroutines for CRM work that must not block a live path.

Three places now need the same thing: a WhatsApp message, a phone call turn and
an offer generation are all latency-sensitive, and none of them should wait on a
network call to Salesforce. Each spawns its work here instead.

The one subtlety is reference keeping. ``asyncio.create_task`` returns a task the
event loop only holds a *weak* reference to, so a task with no other referrer can
be garbage collected mid-flight — silently dropping the work. Holding it in a
module-level set until it finishes is the standard fix, and the done-callback
discards it so the set cannot grow without bound.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger("crm.tasks")

# Strong references to in-flight tasks. See the module docstring.
_INFLIGHT: set[asyncio.Task] = set()

# Callers pass a coroutine; some (the offer upload) never raise, others are
# wrapped here so a bug surfaces in the log instead of vanishing with the task.
__all__ = ["spawn", "inflight_count"]


def inflight_count() -> int:
    """How many spawned tasks are still running. For tests and diagnostics."""
    return len(_INFLIGHT)


def spawn(coro: Coroutine[Any, Any, Any], *, label: str) -> asyncio.Task | None:
    """
    Run ``coro`` in the background. Returns its task, or None if it could not start.

    Never raises and never blocks: a caller on a live path uses this precisely so
    that nothing about the student's experience depends on the outcome. A raised
    exception is logged against ``label`` rather than propagating — an unhandled
    task exception would otherwise only be visible as a warning at shutdown.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop: a sync caller, or a script. Nothing to schedule on, and
        # inventing one here would run the work in a loop nobody owns.
        logger.debug(f"crm.tasks: no running event loop — {label} not scheduled")
        return None

    try:
        task = loop.create_task(coro)
    except Exception:
        logger.exception(f"crm.tasks: could not schedule {label}")
        return None

    _INFLIGHT.add(task)
    task.add_done_callback(_INFLIGHT.discard)
    task.add_done_callback(lambda t: _report_failure(t, label))
    logger.debug(f"crm.tasks: scheduled {label}")
    return task


def _report_failure(task: asyncio.Task, label: str) -> None:
    if task.cancelled():
        logger.warning(f"crm.tasks: {label} was cancelled before it finished")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"crm.tasks: {label} raised outside its error handling: {exc!r}",
                     exc_info=exc)
