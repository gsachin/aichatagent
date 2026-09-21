"""
CRUD functions for the lead-management subsystem.

All functions use raw SQL via psycopg2 (matching the existing pattern
in app/database.py).  Every public function is async and returns
``None`` / ``False`` / ``[]`` on database errors so callers don't crash
when PostgreSQL is unreachable.

Usage:
    from app.leads.models import (
        create_lead, get_lead, update_lead, list_leads,
        create_conversation, get_conversations,
        schedule_follow_up, get_due_follow_ups,
        add_to_call_queue, get_next_queued_call, update_call_queue_status,
        get_call_queue_by_sid,
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("leads.models")

# ── Database connection helpers ──────────────────────────────────────
# Resolved through app/config.py, not read from os.environ here: this module
# does not load .env, so a direct read made the target depend on import order.


def _connection_string() -> str:
    from app.config import database_dsn

    return database_dsn()


@contextmanager
def _get_db():
    """
    Context manager that yields a psycopg2 connection and ensures
    it is closed on exit.  Yields None when PostgreSQL is unavailable.
    """
    conn = None
    try:
        import psycopg2
        conn = psycopg2.connect(_connection_string())
        yield conn
    except Exception:
        logger.warning("PostgreSQL not available — lead operations disabled")
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Leads CRUD ───────────────────────────────────────────────────────


async def create_lead(
    phone_number: str,
    name: str = "",
    email: str = "",
    program_interest: str = "",
    source: str = "manual",
    notes: str = "",
    status: str = "pending",
) -> dict | None:
    """
    Create a new lead.  Returns the lead dict or None on failure.
    If a lead with the same phone_number already exists the existing
    record is returned unchanged (upsert semantic).
    """
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at "
                    "FROM leads WHERE phone_number = %s",
                    (phone_number,),
                )
                existing = cur.fetchone()
                if existing:
                    return _row_to_lead_dict(existing)

                lead_id = str(uuid.uuid4())
                now = _now_iso()
                cur.execute(
                    "INSERT INTO leads (id, phone_number, name, email, "
                    "program_interest, status, source, notes, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at",
                    (lead_id, phone_number, name, email, program_interest,
                     status, source, notes, now, now),
                )
                row = cur.fetchone()
            result = _row_to_lead_dict(row)
            logger.info(f"Lead created: id={lead_id}, phone={phone_number}")
            return result
        except Exception:
            logger.exception("Failed to create lead")
            return None


async def get_lead(lead_id: str) -> dict | None:
    """Get a single lead by ID."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at "
                    "FROM leads WHERE id = %s",
                    (lead_id,),
                )
                row = cur.fetchone()
            return _row_to_lead_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get lead")
            return None


async def get_lead_by_phone(phone_number: str) -> dict | None:
    """Get a lead by phone number."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at "
                    "FROM leads WHERE phone_number = %s",
                    (phone_number,),
                )
                row = cur.fetchone()
            return _row_to_lead_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get lead by phone")
            return None


async def get_call_queue_by_sid(call_sid: str) -> dict | None:
    """Find a call_queue entry by its Twilio Call SID."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, lead_id, status, call_sid, scheduled_at, "
                    "started_at, completed_at, error_message, created_at "
                    "FROM call_queue WHERE call_sid = %s",
                    (call_sid,),
                )
                row = cur.fetchone()
            return _row_to_call_queue_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get call queue by sid")
            return None


async def update_lead(lead_id: str, **kwargs) -> dict | None:
    """
    Update lead fields.  Only the keyword arguments provided are changed.

    Accepted keys: name, email, program_interest, status, source, notes,
                   call_attempts, last_called_at, next_follow_up.
    """
    allowed = {
        "name", "email", "program_interest", "status", "source",
        "notes", "call_attempts", "last_called_at", "next_follow_up",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return await get_lead(lead_id)

    with _get_db() as conn:
        if conn is None:
            return None
        try:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            values = list(updates.values())
            set_clause += ", updated_at = %s"
            values.append(_now_iso())
            values.append(lead_id)

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE leads SET {set_clause} WHERE id = %s "
                    "RETURNING id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at",
                    values,
                )
                row = cur.fetchone()
            return _row_to_lead_dict(row) if row else None
        except Exception:
            logger.exception("Failed to update lead")
            return None


async def upsert_lead_by_phone(
    phone_number: str,
    name: str = "",
    email: str = "",
    program_interest: str = "",
    source: str = "manual",
) -> dict | None:
    """
    Create or update a lead by phone number (dedup guard).
    If the lead exists, update blank fields with new data.  If not, create it.
    """
    existing = await get_lead_by_phone(phone_number)
    if existing:
        patch = {}
        if not existing.get("name") and name:
            patch["name"] = name
        if not existing.get("email") and email:
            patch["email"] = email
        if not existing.get("program_interest") and program_interest:
            patch["program_interest"] = program_interest
        if patch:
            return await update_lead(existing["id"], **patch)
        return existing
    return await create_lead(
        phone_number=phone_number, name=name, email=email,
        program_interest=program_interest, source=source,
    )


async def list_leads(
    status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List leads, optionally filtered by status, source, and/or text search."""
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            query = (
                "SELECT id, phone_number, name, email, program_interest, "
                "status, source, notes, call_attempts, last_called_at, "
                "next_follow_up, created_at, updated_at FROM leads"
            )
            conditions = []
            params: list = []
            if status:
                conditions.append("status = %s")
                params.append(status)
            if source:
                conditions.append("source = %s")
                params.append(source)
            if search:
                conditions.append(
                    "(phone_number ILIKE %s OR name ILIKE %s OR email ILIKE %s OR program_interest ILIKE %s)"
                )
                like = f"%{search}%"
                params.extend([like, like, like, like])
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
            return [_row_to_lead_dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to list leads")
            return []


async def get_leads_due_for_follow_up() -> list[dict]:
    """Return leads whose next_follow_up is in the past."""
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone_number, name, email, program_interest, "
                    "status, source, notes, call_attempts, last_called_at, "
                    "next_follow_up, created_at, updated_at "
                    "FROM leads "
                    "WHERE next_follow_up IS NOT NULL "
                    "AND next_follow_up <= NOW() "
                    "AND status NOT IN ('completed', 'unreachable') "
                    "ORDER BY next_follow_up ASC LIMIT 10"
                )
                rows = cur.fetchall()
            return [_row_to_lead_dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to get leads due for follow-up")
            return []


async def get_lead_stats() -> dict:
    """Return aggregate stats for the dashboard overview."""
    with _get_db() as conn:
        if conn is None:
            return {}
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status"
                )
                by_status = {row[0]: row[1] for row in cur.fetchall()}

                cur.execute(
                    "SELECT COUNT(*) FROM conversations "
                    "WHERE created_at::date = CURRENT_DATE"
                )
                calls_today = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM follow_ups "
                    "WHERE status = 'scheduled' AND scheduled_at >= NOW()"
                )
                upcoming_follow_ups = cur.fetchone()[0]

            return {
                "total_leads": sum(by_status.values()),
                "by_status": by_status,
                "calls_today": calls_today,
                "upcoming_follow_ups": upcoming_follow_ups,
            }
        except Exception:
            logger.exception("Failed to get lead stats")
            return {}


# ── Conversations CRUD ───────────────────────────────────────────────


async def create_conversation(
    lead_id: str,
    phone_number: str = "",
    channel: str = "whatsapp",
    transcript: str = "",
    summary: str = "",
    call_duration_seconds: int = 0,
    outcome: str = "",
    follow_up_needed: bool = False,
    follow_up_reason: str = "",
    extracted_lead: dict | None = None,
    conversation_id: str | None = None,
    crm_user_id: str | None = None,
) -> dict | None:
    """
    Log a conversation against a lead.

    ``conversation_id`` is the logical conversation this row belongs to, minted
    at session start by ``app.crm.session`` and shared across every row of a
    multi-turn conversation (WhatsApp writes one row per message). When it is
    not supplied — the pre-existing call sites — the row falls back to its own
    id, which is correct for a one-row-per-call voice conversation.
    """
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conv_id = str(uuid.uuid4())
            session_id = conversation_id or conv_id
            lead_json = json.dumps(extracted_lead or {})

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (id, lead_id, phone_number, channel, "
                    "transcript, summary, call_duration_seconds, outcome, "
                    "follow_up_needed, follow_up_reason, extracted_lead, "
                    "conversation_id, crm_user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, lead_id, phone_number, channel, transcript, "
                    "summary, call_duration_seconds, outcome, follow_up_needed, "
                    "follow_up_reason, extracted_lead, created_at, conversation_id",
                    (conv_id, lead_id, phone_number, channel, transcript, summary,
                     call_duration_seconds, outcome, follow_up_needed,
                     follow_up_reason, lead_json, session_id, crm_user_id or None),
                )
                row = cur.fetchone()
            logger.info(
                f"Conversation saved: id={conv_id}, conversation={session_id}, "
                f"lead={lead_id}, channel={channel}"
            )
            return _row_to_conversation_dict(row)
        except Exception:
            logger.exception("Failed to save conversation")
            return None


async def get_conversations(
    lead_id: str | None = None,
    channel: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """List conversations, optionally filtered by lead_id and/or channel."""
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            query = (
                "SELECT id, lead_id, phone_number, channel, transcript, summary, "
                "call_duration_seconds, outcome, follow_up_needed, follow_up_reason, "
                "extracted_lead, created_at, conversation_id FROM conversations"
            )
            conditions = []
            params: list = []
            if lead_id:
                conditions.append("lead_id = %s")
                params.append(lead_id)
            if channel:
                conditions.append("channel = %s")
                params.append(channel)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
            return [_row_to_conversation_dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to get conversations")
            return []


async def get_lead_crm_user_id(lead_id: str) -> str:
    """
    Read just the CRM link for a lead, or "" if there is none.

    Deliberately not folded into ``_row_to_lead_dict``: that mapper is
    positional, its sentiment columns already occupy 13-16, and five separate
    lead queries feed it. Adding a column there would mean touching all five and
    getting the offsets right in each. A primary-key lookup is cheaper than that
    risk, and ``crm_user_id`` is only needed on the one path that syncs to the
    CRM.
    """
    if not lead_id:
        return ""

    with _get_db() as conn:
        if conn is None:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT crm_user_id FROM leads WHERE id = %s", (lead_id,))
                row = cur.fetchone()
            return (row[0] or "") if row else ""
        except Exception:
            logger.exception("Failed to read crm_user_id from lead")
            return ""


async def set_lead_crm_user_id(lead_id: str, crm_user_id: str) -> bool:
    """
    Record the Salesforce userId on a lead.

    Written once per conversation and cached thereafter: ``link_conversation``
    checks it before calling the CRM, so this column is what keeps a chatty
    WhatsApp thread from re-asking the API on every message.
    """
    if not lead_id or not crm_user_id:
        return False

    with _get_db() as conn:
        if conn is None:
            return False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    # crm_application_no is cleared here on purpose: it is
                    # derived from crm_user_id, so a new link makes the cached
                    # value a lie. Nothing else writes this column.
                    "UPDATE leads SET crm_user_id = %s, crm_application_no = NULL, "
                    "crm_synced_at = NOW(), updated_at = NOW() WHERE id = %s",
                    (crm_user_id, lead_id),
                )
            return True
        except Exception:
            logger.exception("Failed to set crm_user_id on lead")
            return False


async def get_lead_crm_application_no(lead_id: str) -> str:
    """
    Read the cached CRM application number for a lead, or "" if not resolved yet.

    This is the ``Application_No__c`` the admission API's document routes address
    — *not* the userId (see ``app/crm/documents.py`` for why they differ). Cached
    purely to save a round trip per offer; a miss is normal and expected.
    """
    if not lead_id:
        return ""

    with _get_db() as conn:
        if conn is None:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT crm_application_no FROM leads WHERE id = %s", (lead_id,)
                )
                row = cur.fetchone()
            return (row[0] or "") if row else ""
        except Exception:
            logger.exception("Failed to read crm_application_no from lead")
            return ""


async def set_lead_crm_application_no(lead_id: str, application_no: str) -> bool:
    """
    Cache a resolved CRM application number. A hint, never a source of truth.

    Passing "" clears it, which is how a stale value is discarded after the CRM
    reports that the application no longer exists — without that, a lead whose
    cached number has gone bad would be skipped forever.
    """
    if not lead_id:
        return False

    with _get_db() as conn:
        if conn is None:
            return False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE leads SET crm_application_no = %s, updated_at = NOW() "
                    "WHERE id = %s",
                    (application_no or None, lead_id),
                )
            return True
        except Exception:
            logger.exception("Failed to cache crm_application_no on lead")
            return False


async def get_lead_sentiment_category(lead_id: str) -> str:
    """
    The categorizer's category for a lead ("Hot"/"Warm"/…), or "" if never scored.

    A dedicated accessor because ``get_lead`` does **not** select the sentiment
    columns — its SELECT stops at ``updated_at`` — so reading the category off a
    lead dict silently yields nothing. That is exactly how the first version of
    the CRM sentiment push failed: it looked like it worked and pushed nothing.
    """
    if not lead_id:
        return ""

    with _get_db() as conn:
        if conn is None:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_category FROM leads WHERE id = %s", (lead_id,))
                row = cur.fetchone()
            return (row[0] or "") if row else ""
        except Exception:
            logger.exception("Failed to read current_category from lead")
            return ""


async def get_lead_crm_course(lead_id: str) -> str:
    """The program we last told the CRM about, or "" if we never have."""
    if not lead_id:
        return ""

    with _get_db() as conn:
        if conn is None:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT crm_course FROM leads WHERE id = %s", (lead_id,))
                row = cur.fetchone()
            return (row[0] or "") if row else ""
        except Exception:
            logger.exception("Failed to read crm_course from lead")
            return ""


async def set_lead_crm_course(lead_id: str, course: str) -> bool:
    """
    Remember the program the CRM now holds.

    Only written after a successful push, so a failed write leaves the cache
    stale and the next turn tries again.
    """
    if not lead_id:
        return False

    with _get_db() as conn:
        if conn is None:
            return False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE leads SET crm_course = %s, updated_at = NOW() WHERE id = %s",
                    (course or None, lead_id),
                )
            return True
        except Exception:
            logger.exception("Failed to cache crm_course on lead")
            return False


async def get_recent_conversation_for_phone(
    phone_number: str,
    within_seconds: float,
    channel: str = "whatsapp",
) -> dict | None:
    """
    The most recent conversation for a phone number inside the time window.

    Used by the WhatsApp session resolver. WhatsApp has no session object and no
    end event — every message is an independent webhook — so "is this still the
    same conversation?" can only be answered by how long ago the last message
    arrived. Reading it from the database rather than the in-process registry
    means a restart mid-conversation does not split one conversation in two.

    ``channel`` matters: without it, a voice call from the same number an hour
    ago would make the next WhatsApp message resume the *call's* conversation.
    """
    if not phone_number:
        return None

    with _get_db() as conn:
        if conn is None:
            return None
        try:
            query = (
                "SELECT id, lead_id, phone_number, channel, transcript, summary, "
                "call_duration_seconds, outcome, follow_up_needed, follow_up_reason, "
                "extracted_lead, created_at, conversation_id FROM conversations "
                "WHERE phone_number = %s "
                "  AND created_at >= NOW() - (INTERVAL '1 second' * %s) "
            )
            params: list = [phone_number, within_seconds]
            if channel:
                query += "  AND channel = %s "
                params.append(channel)
            query += "ORDER BY created_at DESC LIMIT 1"

            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                row = cur.fetchone()
            return _row_to_conversation_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get recent conversation for phone")
            return None


# ── Follow-ups CRUD ──────────────────────────────────────────────────


async def schedule_follow_up(
    lead_id: str,
    scheduled_at: str,
    type: str = "call",
    notes: str = "",
) -> dict | None:
    """Schedule a follow-up action for a lead."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            fu_id = str(uuid.uuid4())
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO follow_ups (id, lead_id, scheduled_at, type, notes) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "RETURNING id, lead_id, scheduled_at, status, type, notes, "
                    "created_at, completed_at",
                    (fu_id, lead_id, scheduled_at, type, notes),
                )
                row = cur.fetchone()

            # Also update the lead's next_follow_up field
            await update_lead(lead_id, next_follow_up=scheduled_at)

            logger.info(
                f"Follow-up scheduled: id={fu_id}, lead={lead_id}, at={scheduled_at}"
            )
            return _row_to_follow_up_dict(row)
        except Exception:
            logger.exception("Failed to schedule follow-up")
            return None


async def get_due_follow_ups() -> list[dict]:
    """Return follow-ups that are scheduled and due now.

    Off the event loop for the same reason as `get_next_queued_call`: the
    follow-up scheduler polls this every 30 s with or without work to do.
    """
    return await asyncio.to_thread(_get_due_follow_ups_sync)


def _get_due_follow_ups_sync() -> list[dict]:
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, lead_id, scheduled_at, status, type, notes, "
                    "created_at, completed_at FROM follow_ups "
                    "WHERE status = 'scheduled' AND scheduled_at <= NOW() "
                    "ORDER BY scheduled_at ASC LIMIT 10"
                )
                rows = cur.fetchall()
            return [_row_to_follow_up_dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to get due follow-ups")
            return []


async def update_follow_up_status(
    follow_up_id: str,
    status: str,
) -> dict | None:
    """Update a follow-up's status (e.g. 'completed', 'cancelled')."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            completed_at = _now_iso() if status == "completed" else None
            conn.autocommit = True
            with conn.cursor() as cur:
                if completed_at:
                    cur.execute(
                        "UPDATE follow_ups SET status = %s, completed_at = %s "
                        "WHERE id = %s "
                        "RETURNING id, lead_id, scheduled_at, status, type, notes, "
                        "created_at, completed_at",
                        (status, completed_at, follow_up_id),
                    )
                else:
                    cur.execute(
                        "UPDATE follow_ups SET status = %s WHERE id = %s "
                        "RETURNING id, lead_id, scheduled_at, status, type, notes, "
                        "created_at, completed_at",
                        (status, follow_up_id),
                    )
                row = cur.fetchone()
            return _row_to_follow_up_dict(row) if row else None
        except Exception:
            logger.exception("Failed to update follow-up status")
            return None


# ── Call Queue CRUD ──────────────────────────────────────────────────


async def add_to_call_queue(
    lead_id: str,
    scheduled_at: str | None = None,
) -> dict | None:
    """Enqueue a lead for outbound calling."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            entry_id = str(uuid.uuid4())
            now = _now_iso()
            sched = scheduled_at or now

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO call_queue (id, lead_id, scheduled_at) "
                    "VALUES (%s, %s, %s) "
                    "RETURNING id, lead_id, status, call_sid, scheduled_at, "
                    "started_at, completed_at, error_message, created_at",
                    (entry_id, lead_id, sched),
                )
                row = cur.fetchone()
            logger.info(f"Call queued: id={entry_id}, lead={lead_id}")
            return _row_to_call_queue_dict(row)
        except Exception:
            logger.exception("Failed to add to call queue")
            return None


async def get_next_queued_call() -> dict | None:
    """
    Atomically claim the next queued call (status='queued' -> 'ringing').
    Uses UPDATE ... RETURNING with FOR UPDATE SKIP LOCKED for concurrency.

    The `_sync` core runs in a worker thread, not on the event loop. This is
    polled every 10 s whether or not there is any traffic, so a blocking
    connect here froze the entire server: py-spy put the MainThread in
    `psycopg2.connect <- _get_db <- get_next_queued_call <- _poll_loop`
    (2026-09-21). One failed connect to a dead Postgres costs ~2 s per address
    family on this box, so the poll burned ~4 s of every 14 with the whole loop
    stopped -- `/health` answered in 2.3 s having done no work at all.
    """
    return await asyncio.to_thread(_get_next_queued_call_sync)


def _get_next_queued_call_sync() -> dict | None:
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE call_queue SET status = 'ringing', started_at = %s "
                    "WHERE id = ("
                    "  SELECT id FROM call_queue "
                    "  WHERE status = 'queued' "
                    "  ORDER BY scheduled_at ASC LIMIT 1"
                    "  FOR UPDATE SKIP LOCKED"
                    ") "
                    "RETURNING id, lead_id, status, call_sid, scheduled_at, "
                    "started_at, completed_at, error_message, created_at",
                    (_now_iso(),),
                )
                row = cur.fetchone()
            return _row_to_call_queue_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get next queued call")
            return None


async def update_call_queue_status(
    entry_id: str,
    status: str,
    call_sid: str | None = None,
    error_message: str | None = None,
) -> dict | None:
    """Update a call_queue entry's status and optional fields."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            now = _now_iso()
            conn.autocommit = True
            with conn.cursor() as cur:
                if status in ("completed", "failed"):
                    cur.execute(
                        "UPDATE call_queue SET status = %s, "
                        "call_sid = COALESCE(%s, call_sid), "
                        "error_message = COALESCE(%s, error_message), "
                        "completed_at = %s "
                        "WHERE id = %s "
                        "RETURNING id, lead_id, status, call_sid, scheduled_at, "
                        "started_at, completed_at, error_message, created_at",
                        (status, call_sid, error_message, now, entry_id),
                    )
                else:
                    cur.execute(
                        "UPDATE call_queue SET status = %s, "
                        "call_sid = COALESCE(%s, call_sid), "
                        "error_message = COALESCE(%s, error_message) "
                        "WHERE id = %s "
                        "RETURNING id, lead_id, status, call_sid, scheduled_at, "
                        "started_at, completed_at, error_message, created_at",
                        (status, call_sid, error_message, entry_id),
                    )
                row = cur.fetchone()
            return _row_to_call_queue_dict(row) if row else None
        except Exception:
            logger.exception("Failed to update call queue status")
            return None


# ── Row -> dict helpers ───────────────────────────────────────────────

def _row_to_lead_dict(row) -> dict:
    result = {
        "id": str(row[0]),
        "phone_number": row[1] or "",
        "name": row[2] or "",
        "email": row[3] or "",
        "program_interest": row[4] or "",
        "status": row[5] or "pending",
        "source": row[6] or "",
        "notes": row[7] or "",
        "call_attempts": row[8] or 0,
        "last_called_at": _ts_to_str(row[9]),
        "next_follow_up": _ts_to_str(row[10]),
        "created_at": _ts_to_str(row[11]),
        "updated_at": _ts_to_str(row[12]),
    }
    # Add sentiment columns if present in the row (columns 13-16)
    if len(row) > 13:
        result["current_category"] = row[13] or ""
        result["overall_sentiment_score"] = float(row[14]) if row[14] is not None else 0.0
        result["sentiment_trajectory"] = row[15] or ""
        result["conversion_probability"] = float(row[16]) if row[16] is not None else None
    return result


def _row_to_conversation_dict(row) -> dict:
    result = {
        "id": str(row[0]),
        "lead_id": str(row[1]) if row[1] else "",
        "phone_number": row[2] or "",
        "channel": row[3] or "",
        "transcript": row[4] or "",
        "summary": row[5] or "",
        "call_duration_seconds": row[6] or 0,
        "outcome": row[7] or "",
        "follow_up_needed": bool(row[8]),
        "follow_up_reason": row[9] or "",
        "extracted_lead": _safe_json(row[10]),
        "created_at": _ts_to_str(row[11]),
    }
    # conversation_id was added by the CRM migration. Tolerate the shorter row
    # shape so a query that predates it degrades to the row id rather than
    # raising IndexError.
    result["conversation_id"] = (row[12] if len(row) > 12 and row[12] else result["id"])
    return result


def _row_to_follow_up_dict(row) -> dict:
    return {
        "id": str(row[0]),
        "lead_id": str(row[1]) if row[1] else "",
        "scheduled_at": _ts_to_str(row[2]),
        "status": row[3] or "scheduled",
        "type": row[4] or "call",
        "notes": row[5] or "",
        "created_at": _ts_to_str(row[6]),
        "completed_at": _ts_to_str(row[7]),
    }


def _row_to_call_queue_dict(row) -> dict:
    return {
        "id": str(row[0]),
        "lead_id": str(row[1]) if row[1] else "",
        "status": row[2] or "queued",
        "call_sid": row[3] or "",
        "scheduled_at": _ts_to_str(row[4]),
        "started_at": _ts_to_str(row[5]),
        "completed_at": _ts_to_str(row[6]),
        "error_message": row[7] or "",
        "created_at": _ts_to_str(row[8]),
    }


def _ts_to_str(val) -> str:
    if val is None:
        return ""
    return val.isoformat() if hasattr(val, "isoformat") else str(val)


def _safe_json(val):
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
