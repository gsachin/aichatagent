"""
Outbound call worker — polls the call_queue table and initiates outbound
calls via the Twilio REST API, one at a time.

Runs as a background asyncio task inside the FastAPI process.

Usage:
    from app.outbound.caller import OutboundCallWorker

    worker = OutboundCallWorker()
    await worker.start()
    # ... app runs ...
    worker.stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("outbound.caller")


def _resolve_host() -> str:
    """
    Resolve the public tunnel host from environment or file.

    Returns just the hostname (no scheme), e.g. "foo.trycloudflare.com".
    """
    tunnel_host = os.environ.get("TUNNEL_HOST", "")
    if tunnel_host:
        return tunnel_host

    tunnel_file = Path(__file__).resolve().parent.parent.parent / ".whatsapp_tunnel"
    if tunnel_file.is_file():
        return tunnel_file.read_text().strip()

    # Last resort: try NGROK_HOST (legacy env var name)
    return os.environ.get("NGROK_HOST", "localhost:8000")


class OutboundCallWorker:
    """
    Background worker that polls the ``call_queue`` table and initiates
    outbound calls via the Twilio REST API.

    Processes calls sequentially (one at a time) to avoid overwhelming
    the local LLM / TTS pipeline.  A new call is only started after the
    previous one completes.

    Parameters:
        poll_interval: Seconds between queue polls (default 10).
    """

    def __init__(self, poll_interval: int = 10):
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
            "OutboundCallWorker started (poll every %ds)", self._poll_interval
        )

    def stop(self) -> None:
        """Stop the polling loop and cancel the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("OutboundCallWorker stopped")

    # ── Internal ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._process_next_call()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("OutboundCallWorker: unhandled error in poll loop")
            await asyncio.sleep(self._poll_interval)

    async def _process_next_call(self) -> None:
        """
        Pick the next queued call, claim it atomically, and initiate
        a Twilio outbound voice call.
        """
        from app.leads.models import (
            add_to_call_queue,
            get_lead,
            get_next_queued_call,
            update_call_queue_status,
            update_lead,
        )
        from app.outbound.twiml import outbound_connect_twiml
        from app.config import settings

        entry = await get_next_queued_call()
        if not entry:
            return  # Queue is empty — nothing to do

        lead_id = entry["lead_id"]
        logger.info(
            "OutboundCallWorker: picked call_queue entry %s for lead %s",
            entry["id"],
            lead_id,
        )

        # Get lead details
        lead = await get_lead(lead_id)
        if not lead:
            logger.warning(f"Lead {lead_id} not found — marking call as failed")
            await update_call_queue_status(
                entry["id"], "failed", error_message="Lead not found"
            )
            return

        phone_number = lead.get("phone_number", "")
        if not phone_number:
            logger.warning(f"Lead {lead_id} has no phone number — marking call as failed")
            await update_call_queue_status(
                entry["id"], "failed", error_message="No phone number"
            )
            return

        # Check Twilio credentials
        if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
            logger.warning("Twilio credentials not configured — cannot make outbound call")
            await update_call_queue_status(
                entry["id"], "failed", error_message="Twilio not configured"
            )
            return

        if not settings.TWILIO_PHONE_NUMBER:
            logger.warning("TWILIO_PHONE_NUMBER not set — cannot make outbound call")
            await update_call_queue_status(
                entry["id"], "failed", error_message="TWILIO_PHONE_NUMBER not set"
            )
            return

        # Initiate the call
        try:
            from twilio.rest import Client

            host = _resolve_host()
            twiml = outbound_connect_twiml(host)

            client = Client(
                settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
            )

            logger.info(f"Outbound call — host: {host}, phone: {phone_number}")

            call = client.calls.create(
                to=phone_number,
                from_=settings.TWILIO_PHONE_NUMBER,
                url=f"https://{host}/twilio/outbound-voice",
                status_callback=(
                    f"https://{host}/twilio/outbound/status"
                ),
                status_callback_event=[
                    "initiated",
                    "ringing",
                    "answered",
                    "completed",
                    "failed",
                    "busy",
                    "no-answer",
                ],
                status_callback_method="POST",
            )

            logger.info(
                "Outbound call initiated: SID=%s, to=%s, lead=%s",
                call.sid,
                phone_number,
                lead_id,
            )

            # Update queue entry + lead
            await update_call_queue_status(
                entry["id"], "ringing", call_sid=call.sid
            )
            await update_lead(
                lead_id,
                status="in_progress",
                call_attempts=(lead.get("call_attempts", 0) + 1),
            )

        except Exception as exc:
            logger.exception(f"Failed to initiate outbound call for lead {lead_id}")
            await update_call_queue_status(
                entry["id"],
                "failed",
                error_message=str(exc),
            )
            # Re-queue if under max attempts
            attempts = lead.get("call_attempts", 0) + 1
            max_attempts = settings.MAX_CALL_ATTEMPTS
            if attempts < max_attempts:
                logger.info(
                    f"Lead {lead_id}: attempt {attempts}/{max_attempts} — re-queuing"
                )
                await update_lead(lead_id, call_attempts=attempts, status="pending")
                await add_to_call_queue(lead_id=lead_id)
            else:
                logger.info(
                    f"Lead {lead_id}: max attempts ({max_attempts}) reached — marking failed"
                )
                await update_lead(lead_id, call_attempts=attempts, status="failed")
