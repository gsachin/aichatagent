"""
Async CRUD functions for the offer-letter subsystem.

All functions use raw SQL via a connection helper that connects to the
PostgreSQL instance configured in app.config.settings.DATABASE_URL.

Pattern mirrors app/leads/models.py:
- Every function swallows exceptions, logs them, and returns a safe
  fallback (None, [], or False) so a DB outage doesn't crash the app.

Usage:
    from app.offers.models import create_course, list_courses, ...
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone

logger = logging.getLogger("offers.models")


# ── Internal helpers ────────────────────────────────────────────────────

def _now_iso() -> str:
    """ISO-8601 timestamp string for the current instant in UTC."""
    return datetime.now(timezone.utc).isoformat()


def _ts_to_str(ts) -> str:
    """Convert a DB timestamp to an ISO string, or return '' on None."""
    if ts is None:
        return ""
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def _get_db():
    """Return a psycopg2 connection (autocommit on).  Returns None if psycopg2
    is unavailable or the DB is unreachable."""
    try:
        import psycopg2
        from app.config import settings

        conn_str = settings.DATABASE_URL
        if not conn_str:
            # Fall back to component parts
            conn_str = (
                f"host={settings.DB_HOST} port={settings.DB_PORT} "
                f"dbname={settings.DB_NAME} user={settings.DB_USER} "
                f"password={settings.DB_PASSWORD}"
            )
        conn = psycopg2.connect(conn_str)
        conn.autocommit = True
        return conn
    except Exception:
        logger.exception("Failed to connect to database")
        return None


# ── Courses ─────────────────────────────────────────────────────────────

async def create_course(
    name: str,
    duration: str = "",
    fees: str = "",
    intake: str = "",
    description: str = "",
    payment_link: str = "",
) -> dict | None:
    """Create a new course. Returns the course dict or None on failure."""
    course_id = str(_uuid.uuid4())
    now = _now_iso()
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO courses (id, name, duration, fees, intake, "
                "description, payment_link, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, name, duration, fees, intake, description, "
                "is_active, payment_link, created_at",
                (course_id, name, duration, fees, intake, description, payment_link, now),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "duration": row[2] or "",
                "fees": row[3] or "",
                "intake": row[4] or "",
                "description": row[5] or "",
                "is_active": row[6],
                "payment_link": row[7] or "",
                "created_at": _ts_to_str(row[8]),
            }
        return None
    except Exception:
        logger.exception("create_course failed")
        return None


async def get_course(course_id: str) -> dict | None:
    """Get a single course by ID."""
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, duration, fees, intake, description, "
                "is_active, payment_link, created_at FROM courses WHERE id = %s",
                (course_id,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "duration": row[2] or "",
                "fees": row[3] or "",
                "intake": row[4] or "",
                "description": row[5] or "",
                "is_active": row[6],
                "payment_link": row[7] or "",
                "created_at": _ts_to_str(row[8]),
            }
        return None
    except Exception:
        logger.exception("get_course failed")
        return None


async def get_course_by_name(name: str) -> dict | None:
    """Find a course by name (ILIKE match)."""
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, duration, fees, intake, description, "
                "is_active, payment_link, created_at FROM courses "
                "WHERE is_active = TRUE AND name ILIKE %s LIMIT 1",
                (f"%{name}%",),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "duration": row[2] or "",
                "fees": row[3] or "",
                "intake": row[4] or "",
                "description": row[5] or "",
                "is_active": row[6],
                "payment_link": row[7] or "",
                "created_at": _ts_to_str(row[8]),
            }
        return None
    except Exception:
        logger.exception("get_course_by_name failed")
        return None


async def list_courses(include_inactive: bool = False) -> list:
    """List courses, newest first. Excludes inactive by default."""
    try:
        conn = _get_db()
        if not conn:
            return []
        with conn.cursor() as cur:
            if include_inactive:
                cur.execute(
                    "SELECT id, name, duration, fees, intake, description, "
                    "is_active, payment_link, created_at FROM courses ORDER BY created_at DESC"
                )
            else:
                cur.execute(
                    "SELECT id, name, duration, fees, intake, description, "
                    "is_active, payment_link, created_at FROM courses "
                    "WHERE is_active = TRUE ORDER BY created_at DESC"
                )
            rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "name": r[1],
                "duration": r[2] or "",
                "fees": r[3] or "",
                "intake": r[4] or "",
                "description": r[5] or "",
                "is_active": r[6],
                "payment_link": r[7] or "",
                "created_at": _ts_to_str(r[8]),
            }
            for r in rows
        ]
    except Exception:
        logger.exception("list_courses failed")
        return []


async def update_course(course_id: str, **kwargs) -> dict | None:
    """Update course fields. Accepted: name, duration, fees, intake, description, is_active."""
    allowed = {"name", "duration", "fees", "intake", "description", "is_active", "payment_link"}
    patch = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not patch:
        return await get_course(course_id)
    try:
        conn = _get_db()
        if not conn:
            return None
        set_parts = []
        values = []
        for k, v in patch.items():
            set_parts.append(f"{k} = %s")
            values.append(v)
        values.append(course_id)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE courses SET {', '.join(set_parts)} WHERE id = %s "
                "RETURNING id, name, duration, fees, intake, description, "
                "is_active, created_at",
                values,
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "name": row[1],
                "duration": row[2] or "",
                "fees": row[3] or "",
                "intake": row[4] or "",
                "description": row[5] or "",
                "is_active": row[6],
                "payment_link": row[7] or "",
                "created_at": _ts_to_str(row[8]),
            }
        return None
    except Exception:
        logger.exception("update_course failed")
        return None


# ── Documents ───────────────────────────────────────────────────────────

async def add_document(
    lead_id: str,
    filename: str,
    stored_path: str,
    doc_type: str = "other",
    mime_type: str = "",
    size_bytes: int = 0,
) -> dict | None:
    """Record a new document for a lead."""
    doc_id = str(_uuid.uuid4())
    now = _now_iso()
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lead_documents (id, lead_id, filename, stored_path, "
                "doc_type, mime_type, size_bytes, uploaded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, lead_id, filename, stored_path, doc_type, "
                "mime_type, size_bytes, uploaded_at",
                (doc_id, lead_id, filename, stored_path, doc_type, mime_type, size_bytes, now),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "lead_id": row[1],
                "filename": row[2],
                "stored_path": row[3],
                "doc_type": row[4],
                "mime_type": row[5] or "",
                "size_bytes": row[6] or 0,
                "uploaded_at": _ts_to_str(row[7]),
            }
        return None
    except Exception:
        logger.exception("add_document failed")
        return None


async def get_document(doc_id: str) -> dict | None:
    """Get a single document by ID."""
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lead_id, filename, stored_path, doc_type, "
                "mime_type, size_bytes, uploaded_at "
                "FROM lead_documents WHERE id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0],
                "lead_id": row[1],
                "filename": row[2],
                "stored_path": row[3],
                "doc_type": row[4],
                "mime_type": row[5] or "",
                "size_bytes": row[6] or 0,
                "uploaded_at": _ts_to_str(row[7]),
            }
        return None
    except Exception:
        logger.exception("get_document failed")
        return None


async def list_documents(lead_id: str) -> list:
    """List all documents for a lead, newest first."""
    try:
        conn = _get_db()
        if not conn:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lead_id, filename, stored_path, doc_type, "
                "mime_type, size_bytes, uploaded_at "
                "FROM lead_documents WHERE lead_id = %s "
                "ORDER BY uploaded_at DESC",
                (lead_id,),
            )
            rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "lead_id": r[1],
                "filename": r[2],
                "stored_path": r[3],
                "doc_type": r[4],
                "mime_type": r[5] or "",
                "size_bytes": r[6] or 0,
                "uploaded_at": _ts_to_str(r[7]),
            }
            for r in rows
        ]
    except Exception:
        logger.exception("list_documents failed")
        return []


async def delete_document(doc_id: str) -> bool:
    """Delete a document row. Returns True on success."""
    try:
        conn = _get_db()
        if not conn:
            return False
        with conn.cursor() as cur:
            cur.execute("DELETE FROM lead_documents WHERE id = %s", (doc_id,))
        conn.close()
        return True
    except Exception:
        logger.exception("delete_document failed")
        return False


# ── Offer letters ───────────────────────────────────────────────────────

async def create_offer_letter(
    lead_id: str,
    program: str = "",
    course_id: str = "",
    pdf_path: str = "",
    offer_date: str = "",
    valid_until: str = "",
    terms: str = "",
    sent_via: str = "whatsapp",
) -> dict | None:
    """Create an offer letter record (status='sent')."""
    offer_id = str(_uuid.uuid4())
    now = _now_iso()
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO offer_letters (id, lead_id, course_id, program, "
                "status, pdf_path, offer_date, valid_until, terms, sent_via, "
                "sent_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 'sent', %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, lead_id, course_id, program, status, pdf_path, "
                "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                "email_id, sent_at, response_at, created_at, updated_at",
                (
                    offer_id, lead_id, course_id or None, program,
                    pdf_path, offer_date or None, valid_until or None, terms,
                    sent_via, now, now, now,
                ),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "lead_id": row[1], "course_id": row[2] or "",
                "program": row[3] or "", "status": row[4],
                "pdf_path": row[5] or "",
                "offer_date": str(row[6]) if row[6] else "",
                "valid_until": str(row[7]) if row[7] else "",
                "terms": row[8] or "", "sent_via": row[9] or "",
                "whatsapp_sid": row[10] or "", "email_id": row[11] or "",
                "sent_at": _ts_to_str(row[12]),
                "response_at": _ts_to_str(row[13]),
                "created_at": _ts_to_str(row[14]),
                "updated_at": _ts_to_str(row[15]),
            }
        return None
    except Exception:
        logger.exception("create_offer_letter failed")
        return None


async def get_offer_letter(offer_id: str) -> dict | None:
    """Get a single offer letter by ID."""
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lead_id, course_id, program, status, pdf_path, "
                "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                "email_id, sent_at, response_at, created_at, updated_at "
                "FROM offer_letters WHERE id = %s",
                (offer_id,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "lead_id": row[1], "course_id": row[2] or "",
                "program": row[3] or "", "status": row[4],
                "pdf_path": row[5] or "",
                "offer_date": str(row[6]) if row[6] else "",
                "valid_until": str(row[7]) if row[7] else "",
                "terms": row[8] or "", "sent_via": row[9] or "",
                "whatsapp_sid": row[10] or "", "email_id": row[11] or "",
                "sent_at": _ts_to_str(row[12]),
                "response_at": _ts_to_str(row[13]),
                "created_at": _ts_to_str(row[14]),
                "updated_at": _ts_to_str(row[15]),
            }
        return None
    except Exception:
        logger.exception("get_offer_letter failed")
        return None


async def list_offer_letters(lead_id: str = "") -> list:
    """List offer letters, newest first. Optionally filter by lead_id."""
    try:
        conn = _get_db()
        if not conn:
            return []
        with conn.cursor() as cur:
            if lead_id:
                cur.execute(
                    "SELECT id, lead_id, course_id, program, status, pdf_path, "
                    "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                    "email_id, sent_at, response_at, created_at, updated_at "
                    "FROM offer_letters WHERE lead_id = %s "
                    "ORDER BY created_at DESC",
                    (lead_id,),
                )
            else:
                cur.execute(
                    "SELECT id, lead_id, course_id, program, status, pdf_path, "
                    "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                    "email_id, sent_at, response_at, created_at, updated_at "
                    "FROM offer_letters ORDER BY created_at DESC"
                )
            rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0], "lead_id": r[1], "course_id": r[2] or "",
                "program": r[3] or "", "status": r[4],
                "pdf_path": r[5] or "",
                "offer_date": str(r[6]) if r[6] else "",
                "valid_until": str(r[7]) if r[7] else "",
                "terms": r[8] or "", "sent_via": r[9] or "",
                "whatsapp_sid": r[10] or "", "email_id": r[11] or "",
                "sent_at": _ts_to_str(r[12]),
                "response_at": _ts_to_str(r[13]),
                "created_at": _ts_to_str(r[14]),
                "updated_at": _ts_to_str(r[15]),
            }
            for r in rows
        ]
    except Exception:
        logger.exception("list_offer_letters failed")
        return []


async def update_offer_letter_status(
    offer_id: str,
    status: str,
    message_sid: str = "",
    email_id: str = "",
    sent_at: str = "",
    response_at: str = "",
) -> dict | None:
    """Update offer letter status and tracking fields."""
    allowed_statuses = {"sent", "accepted", "rejected"}
    if status not in allowed_statuses:
        return None
    now = _now_iso()
    try:
        conn = _get_db()
        if not conn:
            return None
        set_parts = ["status = %s", "updated_at = %s"]
        values = [status, now]
        if message_sid:
            set_parts.append("whatsapp_sid = %s")
            values.append(message_sid)
        if email_id:
            set_parts.append("email_id = %s")
            values.append(email_id)
        if sent_at:
            set_parts.append("sent_at = %s")
            values.append(sent_at)
        if response_at:
            set_parts.append("response_at = %s")
            values.append(response_at)
        elif status in ("accepted", "rejected"):
            set_parts.append("response_at = %s")
            values.append(now)
        values.append(offer_id)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE offer_letters SET {', '.join(set_parts)} WHERE id = %s "
                "RETURNING id, lead_id, course_id, program, status, pdf_path, "
                "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                "email_id, sent_at, response_at, created_at, updated_at",
                values,
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "lead_id": row[1], "course_id": row[2] or "",
                "program": row[3] or "", "status": row[4],
                "pdf_path": row[5] or "",
                "offer_date": str(row[6]) if row[6] else "",
                "valid_until": str(row[7]) if row[7] else "",
                "terms": row[8] or "", "sent_via": row[9] or "",
                "whatsapp_sid": row[10] or "", "email_id": row[11] or "",
                "sent_at": _ts_to_str(row[12]),
                "response_at": _ts_to_str(row[13]),
                "created_at": _ts_to_str(row[14]),
                "updated_at": _ts_to_str(row[15]),
            }
        return None
    except Exception:
        logger.exception("update_offer_letter_status failed")
        return None


async def get_recent_offer_for_lead(lead_id: str, within_hours: int = 24) -> dict | None:
    """Check if an offer was recently sent to this lead (idempotency guard)."""
    try:
        conn = _get_db()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lead_id, course_id, program, status, pdf_path, "
                "offer_date, valid_until, terms, sent_via, whatsapp_sid, "
                "email_id, sent_at, response_at, created_at, updated_at "
                "FROM offer_letters "
                f"WHERE lead_id = %s AND sent_at > NOW() - INTERVAL '{int(within_hours)} hours' "
                "ORDER BY sent_at DESC LIMIT 1",
                (lead_id,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "lead_id": row[1], "course_id": row[2] or "",
                "program": row[3] or "", "status": row[4],
                "pdf_path": row[5] or "",
                "offer_date": str(row[6]) if row[6] else "",
                "valid_until": str(row[7]) if row[7] else "",
                "terms": row[8] or "", "sent_via": row[9] or "",
                "whatsapp_sid": row[10] or "", "email_id": row[11] or "",
                "sent_at": _ts_to_str(row[12]),
                "response_at": _ts_to_str(row[13]),
                "created_at": _ts_to_str(row[14]),
                "updated_at": _ts_to_str(row[15]),
            }
        return None
    except Exception:
        logger.exception("get_recent_offer_for_lead failed")
        return None
