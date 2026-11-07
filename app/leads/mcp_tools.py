"""
MCP tool implementations for lead management.

Each function is a thin wrapper around app.leads.models / app.leads.service
that provides a clean interface suitable for MCP tool registration.

All functions are synchronous (they'll be called via asyncio.run() by the
MCP framework).  They return dict / list results that serialise to JSON.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("leads.mcp_tools")


def _run(coro):
    """Helper: run an async coroutine synchronously."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already inside an event loop — create a new one
            import concurrent.futures
            import threading

            future = concurrent.futures.Future()

            def _runner():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    future.set_result(new_loop.run_until_complete(coro))
                except Exception as exc:
                    future.set_exception(exc)
                finally:
                    new_loop.close()

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            return future.result(timeout=30)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Tool: add_lead ───────────────────────────────────────────────────


def add_lead(
    phone_number: str,
    name: str = "",
    email: str = "",
    program_interest: str = "",
    source: str = "manual",
    notes: str = "",
) -> dict:
    """
    Add a new lead to the system (or return existing lead by phone number).

    Args:
        phone_number: The lead's phone number (required).
        name: Full name (optional).
        email: Email address (optional).
        program_interest: Program they're interested in, e.g. "Computer Science" (optional).
        source: Where the lead came from — "manual", "whatsapp", "streamlit", etc.
        notes: Free-text notes.

    Returns:
        The lead record as a dict with id, status, and all fields.
    """
    from app.leads.models import create_lead, get_lead_by_phone

    existing = _run(get_lead_by_phone(phone_number))
    if existing:
        logger.info(f"add_lead: lead already exists for {phone_number}")
        return existing

    result = _run(
        create_lead(
            phone_number=phone_number,
            name=name,
            email=email,
            program_interest=program_interest,
            source=source,
            notes=notes,
        )
    )
    return result or {"error": "Failed to create lead — database unavailable"}


# ── Tool: update_lead ────────────────────────────────────────────────


def update_lead(
    lead_id: str,
    name: str | None = None,
    email: str | None = None,
    program_interest: str | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Update fields on an existing lead.  Only provided (non-None) fields are changed.

    Args:
        lead_id: The UUID of the lead to update.
        name: New name.
        email: New email.
        program_interest: New program interest.
        status: New status — "pending", "in_progress", "completed", "failed", "unreachable".
        notes: New or appended notes.

    Returns:
        The updated lead record.
    """
    from app.leads.models import update_lead as _update

    kwargs = {}
    if name is not None:
        kwargs["name"] = name
    if email is not None:
        kwargs["email"] = email
    if program_interest is not None:
        kwargs["program_interest"] = program_interest
    if status is not None:
        kwargs["status"] = status
    if notes is not None:
        kwargs["notes"] = notes

    if not kwargs:
        from app.leads.models import get_lead
        return _run(get_lead(lead_id)) or {"error": f"Lead {lead_id} not found"}

    result = _run(_update(lead_id, **kwargs))
    return result or {"error": f"Failed to update lead {lead_id}"}


# ── Tool: trigger_call ───────────────────────────────────────────────


def trigger_call(lead_id: str) -> dict:
    """
    Queue an immediate outbound call to a lead.

    The call will be picked up by the OutboundCallWorker within 10 seconds.

    Args:
        lead_id: The UUID of the lead to call.

    Returns:
        The call_queue entry dict with status "queued".
    """
    from app.leads.models import add_to_call_queue, get_lead

    lead = _run(get_lead(lead_id))
    if not lead:
        return {"error": f"Lead {lead_id} not found"}

    result = _run(add_to_call_queue(lead_id=lead_id))
    return result or {"error": "Failed to queue call — database unavailable"}


# ── Tool: view_conversations ─────────────────────────────────────────


def view_conversations(
    lead_id: str,
    limit: int = 10,
    channel: str | None = None,
) -> list[dict]:
    """
    Retrieve conversation history for a lead.

    Args:
        lead_id: The UUID of the lead.
        limit: Max number of conversations to return (default 10).
        channel: Optional filter — "whatsapp", "streamlit", "inbound_call", "outbound_call".

    Returns:
        List of conversation dicts, newest first.
    """
    from app.leads.models import get_conversations

    return _run(get_conversations(lead_id=lead_id, channel=channel, limit=limit))


# ── Tool: schedule_follow_up ─────────────────────────────────────────


def schedule_follow_up(
    lead_id: str,
    scheduled_at: str,
    type: str = "call",
    notes: str = "",
) -> dict:
    """
    Schedule a follow-up action for a lead.

    Args:
        lead_id: The UUID of the lead.
        scheduled_at: ISO 8601 datetime string, e.g. "2026-08-01T14:00:00Z".
        type: "call" or "message" (default "call").
        notes: Reason or context for the follow-up.

    Returns:
        The follow_up entry dict.
    """
    from app.leads.models import schedule_follow_up as _schedule

    result = _run(_schedule(lead_id=lead_id, scheduled_at=scheduled_at, type=type, notes=notes))
    return result or {"error": "Failed to schedule follow-up — database unavailable"}


# ── Tool: check_lead_status ──────────────────────────────────────────


def check_lead_status(
    lead_id: str | None = None,
    phone_number: str | None = None,
) -> dict:
    """
    Get the current status and full details for a lead.

    Provide either lead_id or phone_number.

    Args:
        lead_id: UUID of the lead.
        phone_number: Phone number of the lead.

    Returns:
        Lead record dict with status, last_called_at, next_follow_up, etc.
    """
    from app.leads.models import get_lead, get_lead_by_phone

    if lead_id:
        result = _run(get_lead(lead_id))
    elif phone_number:
        result = _run(get_lead_by_phone(phone_number))
    else:
        return {"error": "Provide either lead_id or phone_number"}

    return result or {"error": "Lead not found"}


# ── Tool: list_leads ─────────────────────────────────────────────────


def list_leads(
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    List leads, optionally filtered by status.

    Args:
        status: Optional filter — "pending", "in_progress", "completed", "failed", "unreachable".
        limit: Max number of leads to return (default 50).

    Returns:
        List of lead dicts, newest first.
    """
    from app.leads.models import list_leads as _list

    return _run(_list(status=status, limit=limit))
