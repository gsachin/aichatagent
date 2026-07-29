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

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("leads.models")

# ── Database connection helpers ──────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "admissions")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def _connection_string() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    return (
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
        f"user={DB_USER} password={DB_PASSWORD}"
    )


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
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List leads, optionally filtered by status and/or source."""
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
) -> dict | None:
    """Log a conversation against a lead."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conv_id = str(uuid.uuid4())
            lead_json = json.dumps(extracted_lead or {})

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (id, lead_id, phone_number, channel, "
                    "transcript, summary, call_duration_seconds, outcome, "
                    "follow_up_needed, follow_up_reason, extracted_lead) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, lead_id, phone_number, channel, transcript, "
                    "summary, call_duration_seconds, outcome, follow_up_needed, "
                    "follow_up_reason, extracted_lead, created_at",
                    (conv_id, lead_id, phone_number, channel, transcript, summary,
                     call_duration_seconds, outcome, follow_up_needed,
                     follow_up_reason, lead_json),
                )
                row = cur.fetchone()
            logger.info(
                f"Conversation saved: id={conv_id}, lead={lead_id}, channel={channel}"
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
                "extracted_lead, created_at FROM conversations"
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
    """Return follow-ups that are scheduled and due now."""
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
    """
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
    return {
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


def _row_to_conversation_dict(row) -> dict:
    return {
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
