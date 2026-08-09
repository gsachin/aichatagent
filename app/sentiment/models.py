"""
CRUD functions for the sentiment-analysis subsystem.

All functions use raw SQL via psycopg2 (matching the existing pattern
in app/leads/models.py).  Every public function is async and returns
``None`` / ``False`` / ``[]`` on database errors so callers don't crash
when PostgreSQL is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("sentiment.models")

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
    """Context manager yielding a psycopg2 connection, or None if unavailable."""
    conn = None
    try:
        import psycopg2
        conn = psycopg2.connect(_connection_string())
        yield conn
    except Exception:
        logger.warning("PostgreSQL not available — sentiment operations disabled")
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Sentiment Score CRUD ─────────────────────────────────────────────


async def save_sentiment_score(
    lead_id: str,
    conversation_id: str = "",
    *,
    s_call: float = 0.0,
    primary_emotion: str = "",
    buying_intent_score: float = 0.0,
    friction_score: float = 0.0,
    objections: list | None = None,
    s_lead: float = 0.0,
    p_convert: float | None = None,
    trajectory: str = "Stable",
    category: str = "Nurture",
    extraction_raw: dict | None = None,
    model_version: str = "rules-v1",
    weights_used: dict | None = None,
    transcript_snippet: str = "",
) -> dict | None:
    """Persist a sentiment score for a lead."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            score_id = str(uuid.uuid4())
            objections_json = json.dumps(objections or [])
            extraction_json = json.dumps(extraction_raw or {})
            weights_json = json.dumps(weights_used or {})

            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sentiment_scores (id, lead_id, conversation_id, "
                    "s_call, primary_emotion, buying_intent_score, friction_score, "
                    "objections, s_lead, p_convert, trajectory, category, "
                    "extraction_raw, model_version, weights_used, transcript_snippet) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, lead_id, conversation_id, s_call, primary_emotion, "
                    "buying_intent_score, friction_score, objections, s_lead, p_convert, "
                    "trajectory, category, extraction_raw, model_version, weights_used, "
                    "transcript_snippet, created_at",
                    (
                        score_id, lead_id,
                        conversation_id or None,
                        s_call, primary_emotion, buying_intent_score, friction_score,
                        objections_json, s_lead, p_convert, trajectory, category,
                        extraction_json, model_version, weights_json,
                        transcript_snippet[:500],
                    ),
                )
                row = cur.fetchone()
            logger.info(
                f"Sentiment saved: id={score_id}, lead={lead_id}, "
                f"s_lead={s_lead:.2f}, category={category}"
            )
            return _row_to_sentiment_dict(row)
        except Exception:
            logger.exception("Failed to save sentiment score")
            return None


async def get_lead_sentiment(lead_id: str) -> dict | None:
    """Get the most recent sentiment score for a lead."""
    with _get_db() as conn:
        if conn is None:
            return None
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, lead_id, conversation_id, s_call, primary_emotion, "
                    "buying_intent_score, friction_score, objections, s_lead, p_convert, "
                    "trajectory, category, extraction_raw, model_version, weights_used, "
                    "transcript_snippet, created_at "
                    "FROM sentiment_scores "
                    "WHERE lead_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (lead_id,),
                )
                row = cur.fetchone()
            return _row_to_sentiment_dict(row) if row else None
        except Exception:
            logger.exception("Failed to get lead sentiment")
            return None


async def get_lead_sentiment_history(
    lead_id: str,
    limit: int = 10,
) -> list[dict]:
    """Get historical sentiment scores for a lead, newest first."""
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, lead_id, conversation_id, s_call, primary_emotion, "
                    "buying_intent_score, friction_score, objections, s_lead, p_convert, "
                    "trajectory, category, extraction_raw, model_version, weights_used, "
                    "transcript_snippet, created_at "
                    "FROM sentiment_scores "
                    "WHERE lead_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (lead_id, limit),
                )
                rows = cur.fetchall()
            return [_row_to_sentiment_dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to get sentiment history")
            return []


async def update_lead_sentiment_fields(
    lead_id: str,
    current_category: str = "",
    overall_sentiment_score: float = 0.0,
    sentiment_trajectory: str = "",
    conversion_probability: float | None = None,
) -> bool:
    """Sync the latest sentiment summary onto the leads table."""
    with _get_db() as conn:
        if conn is None:
            return False
        try:
            now = _now_iso()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE leads SET "
                    "current_category = %s, "
                    "overall_sentiment_score = %s, "
                    "sentiment_trajectory = %s, "
                    "conversion_probability = %s, "
                    "last_sentiment_at = %s, "
                    "updated_at = %s "
                    "WHERE id = %s",
                    (
                        current_category,
                        overall_sentiment_score,
                        sentiment_trajectory,
                        conversion_probability,
                        now, now,
                        lead_id,
                    ),
                )
            logger.info(
                f"Lead {lead_id} sentiment fields updated: "
                f"category={current_category}, s_lead={overall_sentiment_score:.2f}"
            )
            return True
        except Exception:
            logger.exception("Failed to update lead sentiment fields")
            return False


# ── Row → dict helper ────────────────────────────────────────────────


def _row_to_sentiment_dict(row) -> dict:
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "lead_id": str(row[1]) if row[1] else "",
        "conversation_id": str(row[2]) if row[2] else "",
        "s_call": float(row[3]) if row[3] is not None else 0.0,
        "primary_emotion": row[4] or "",
        "buying_intent_score": float(row[5]) if row[5] is not None else 0.0,
        "friction_score": float(row[6]) if row[6] is not None else 0.0,
        "objections": _safe_json(row[7]),
        "s_lead": float(row[8]) if row[8] is not None else 0.0,
        "p_convert": float(row[9]) if row[9] is not None else None,
        "trajectory": row[10] or "Stable",
        "category": row[11] or "Nurture",
        "extraction_raw": _safe_json(row[12]),
        "model_version": row[13] or "rules-v1",
        "weights_used": _safe_json(row[14]),
        "transcript_snippet": row[15] or "",
        "created_at": row[16].isoformat() if hasattr(row[16], "isoformat") else str(row[16]) if row[16] else "",
    }


def _safe_json(val):
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {} if val.startswith("{") else []
    return {}
