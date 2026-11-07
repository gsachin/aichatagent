"""
Follow-up scheduler — polls the ``follow_ups`` table and moves due
items into the ``call_queue`` so they are picked up by the outbound
call worker.

Runs as a background asyncio task inside the FastAPI process.

Usage:
    from app.outbound.scheduler import FollowUpScheduler

    scheduler = FollowUpScheduler()
    await scheduler.start()
    # ... app runs ...
    await scheduler.stop()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("outbound.scheduler")


class FollowUpScheduler:
    """
    Background worker that polls the ``follow_ups`` table and enqueues
    any due follow-ups into the ``call_queue``.

    Designed to work without apscheduler (pure asyncio sleep loop) so
    that it runs even on AppLocker-constrained Windows machines.

    Parameters:
        poll_interval: Seconds between follow-up checks (default 30).
    """

    def __init__(self, poll_interval: int = 30):
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the polling loop as a background asyncio task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "FollowUpScheduler started (poll every %ds)", self._poll_interval
        )

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("FollowUpScheduler stopped")

    # ── Internal ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._process_due_follow_ups()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("FollowUpScheduler: unhandled error")
            await asyncio.sleep(self._poll_interval)

    async def _process_due_follow_ups(self) -> None:
        """
        Find follow-ups that are due now and move them to the call queue.
        """
        from app.leads.models import (
            add_to_call_queue,
            get_due_follow_ups,
            update_follow_up_status,
        )

        due = await get_due_follow_ups()
        if not due:
            return

        logger.info(f"FollowUpScheduler: found {len(due)} due follow-up(s)")

        for fu in due:
            fu_id = fu["id"]
            lead_id = fu["lead_id"]
            fu_type = fu.get("type", "call")

            try:
                if fu_type == "call":
                    # Add to outbound call queue
                    result = await add_to_call_queue(
                        lead_id=lead_id,
                        scheduled_at=fu.get("scheduled_at"),
                    )
                    if result:
                        await update_follow_up_status(fu_id, "completed")
                        logger.info(
                            f"Follow-up {fu_id}: call enqueued for lead {lead_id}"
                        )
                    else:
                        logger.warning(
                            f"Follow-up {fu_id}: failed to enqueue call for lead {lead_id}"
                        )
                elif fu_type == "message":
                    # For 'message' type follow-ups we just mark them
                    # completed and rely on the WhatsApp pipeline
                    await update_follow_up_status(fu_id, "completed")
                    logger.info(
                        f"Follow-up {fu_id}: message action marked complete (lead {lead_id})"
                    )
                else:
                    logger.warning(
                        f"Follow-up {fu_id}: unknown type '{fu_type}' — skipping"
                    )
            except Exception:
                logger.exception(
                    f"Failed to process follow-up {fu_id} (lead {lead_id})"
                )
