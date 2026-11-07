"""
The background worker that drains the CRM outbox, and retires lapsed offers.

``flush_outbox`` has existed since Phase 2 with a docstring saying "called by the
scheduler" — and no scheduler was ever written, so every transient CRM failure
queued a row that nothing would ever replay. This is that scheduler.

It does two jobs on one loop:

* **Drain** queued status/profile writes, with the backoff ``outbox.drain``
  already implements.
* **Expire** offers that passed their validity date while still marked as
  offered, so the CRM stops showing a live offer that lapsed weeks ago.

Pure asyncio, no scheduler library, started from the FastAPI lifespan exactly
like ``FollowUpScheduler`` and ``OutboundCallWorker``.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.crm import outbox
from app.crm.sync import enabled, flush_outbox

logger = logging.getLogger("crm.worker")


class CrmOutboxWorker:
    """
    Drains failed CRM writes and expires stale offers.

    Both jobs are best-effort and individually guarded: a failure in one must not
    stop the other, and neither may escape the loop — a worker that dies silently
    is worse than no worker, because the queue looks healthy while nothing moves.
    """

    def __init__(self, poll_interval: int = 60) -> None:
        self._poll_interval = max(5, int(poll_interval))
        self._running = False
        self._task: asyncio.Task | None = None
        self.last_drain: dict[str, int] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"CRM outbox worker started (poll every {self._poll_interval}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("CRM outbox worker stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                # Never let one bad tick kill the loop.
                logger.exception("CRM outbox worker: tick failed")
            await asyncio.sleep(self._poll_interval)

    async def _tick(self) -> None:
        if not enabled():
            return

        depth = await outbox.depth()
        if depth:
            logger.info(f"CRM outbox: {depth} queued write(s) — draining")
            self.last_drain = await flush_outbox()

        await self._expire_stale_offers()

    async def _expire_stale_offers(self) -> None:
        """
        Mark offers past ``valid_until`` as Expired in the CRM.

        Only offers the CRM was actually told about are considered: one whose
        release was never pushed is not "expired", it is unknown to Salesforce,
        and pushing an expiry for it would invent a history that never happened.
        """
        try:
            from app.leads.models import get_lead_crm_user_id
            from app.offers.models import list_expired_offers, mark_offer_expired
            from app.crm.status import push_offer_expired

            for offer in await list_expired_offers(limit=20):
                lead_id = str(offer.get("lead_id") or "")
                user_id = await get_lead_crm_user_id(lead_id) if lead_id else ""
                if not user_id:
                    continue
                if await push_offer_expired(user_id):
                    await mark_offer_expired(offer["id"])
                    logger.info(
                        f"CRM outbox: offer {offer['id']} lapsed and was marked Expired "
                        f"for {user_id}"
                    )
        except Exception:
            logger.exception("CRM outbox: could not sweep expired offers")
