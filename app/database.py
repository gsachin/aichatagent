"""
PostgreSQL database module — University Admissions Voice Assistant.

Manages lead_calls table: stores call transcripts and extracted lead data
(name, email, target_program) after each voice conversation.

Schema:
    lead_calls
        id              UUID PRIMARY KEY
        phone_number    VARCHAR
        transcript      TEXT           — full conversation transcript
        extracted_lead  JSONB          — {name, email, program}
        created_at      TIMESTAMP

Configuration via environment variables:
    DATABASE_URL    — full PostgreSQL connection string (preferred)
    DB_HOST         — defaults to localhost
    DB_PORT         — defaults to 5432
    DB_NAME         — defaults to 'admissions'
    DB_USER         — defaults to 'postgres'
    DB_PASSWORD     — defaults to empty string

Usage:
    from app.database import init_db, save_lead_call
    await init_db()
    await save_lead_call(phone_number="+15551234567",
                         transcript="Full call text...",
                         extracted_lead={"name": "John", "email": "j@e.com", "program": "CS"})
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("voice_db")

# ── Configuration ────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "admissions")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def _connection_string() -> str:
    """Build a PostgreSQL connection string from config."""
    if DATABASE_URL:
        return DATABASE_URL
    return (
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
        f"user={DB_USER} password={DB_PASSWORD}"
    )


# ── Schema ───────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lead_calls (
    id              UUID PRIMARY KEY,
    phone_number    VARCHAR(32),
    transcript      TEXT,
    extracted_lead  JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""


# ── Public API ───────────────────────────────────────────────────────

async def init_db() -> bool:
    """
    Initialize the database: create the lead_calls table if it doesn't exist.

    Returns True on success, False if PostgreSQL is not available
    (allows the app to run without a database in development).
    """
    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 not installed — database disabled")
        return False

    try:
        conn = psycopg2.connect(_connection_string())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.close()
        logger.info("Database initialized: lead_calls table ready")
        return True
    except Exception:
        logger.warning(
            "PostgreSQL not available — running without database. "
            f"Connection: {_connection_string()}"
        )
        return False


async def save_lead_call(
    phone_number: str = "",
    transcript: str = "",
    extracted_lead: dict | None = None,
) -> bool:
    """
    Save a completed call record to the lead_calls table.

    Parameters:
        phone_number:   Caller's phone number (empty for browser/WAV tests).
        transcript:     Full conversation transcript text.
        extracted_lead: Dict with keys: name, email, program (or None).

    Returns True on success, False if database is unavailable.
    """
    try:
        import psycopg2
    except ImportError:
        return False

    lead_json = json.dumps(extracted_lead or {})
    call_id = str(uuid.uuid4())

    try:
        conn = psycopg2.connect(_connection_string())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lead_calls (id, phone_number, transcript, extracted_lead) "
                "VALUES (%s, %s, %s, %s)",
                (call_id, phone_number, transcript, lead_json),
            )
        conn.close()
        logger.info(f"Lead saved: id={call_id}, phone={phone_number}")
        return True
    except Exception:
        logger.exception("Failed to save lead call")
        return False


async def extract_lead_from_transcript(transcript: str) -> dict | None:
    """
    Use the local LLM to extract structured lead data from a transcript.

    Returns a dict with keys: name, email, program (values are strings or null).
    Returns None if extraction fails.

    The LLM is called with a JSON-extraction prompt and the response is
    parsed for structured data.
    """
    import json as _json
    import re
    import urllib.request

    prompt = (
        "Extract candidate Name, Email, and Program from the following "
        "conversation transcript. Return ONLY valid JSON with keys: "
        '"name", "email", "program". If a field is not found, set it to null.'
        f"\n\nTranscript:\n{transcript}"
    )

    # Find available Ollama model
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
        qwen_models = [m for m in models if "qwen" in m.lower()]
        model = qwen_models[0] if qwen_models else "qwen2.5:7b"
    except Exception:
        model = "qwen2.5:7b"

    try:
        import ollama

        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048},
        )
        raw = response["message"]["content"]
        logger.debug(f"LLM extraction raw: {raw}")

        # Try to parse JSON from the response
        # 1. Direct parse
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            pass

        # 2. Extract from ```json block
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if match:
            try:
                return _json.loads(match.group(1))
            except _json.JSONDecodeError:
                pass

        # 3. Extract first { ... } block
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return _json.loads(match.group(0))
            except _json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse JSON from LLM output: {raw[:200]}")
        return None

    except Exception:
        logger.exception("Lead extraction failed")
        return None


async def handle_post_call(
    transcript: str,
    phone_number: str = "",
) -> bool:
    """
    Post-call handler: extract lead data and save to database.

    Call this when a WebSocket voice session disconnects.

    Parameters:
        transcript:   Full conversation transcript.
        phone_number: Caller's phone number (if available).

    Returns True if lead was saved successfully.
    """
    if not transcript or not transcript.strip():
        logger.info("Post-call: empty transcript — nothing to save")
        return False

    logger.info(f"Post-call: processing transcript ({len(transcript)} chars)")

    # Extract lead data via LLM
    lead = await extract_lead_from_transcript(transcript)

    if lead:
        logger.info(f"Post-call: extracted lead — {lead}")

    # Save to database
    saved = await save_lead_call(
        phone_number=phone_number,
        transcript=transcript,
        extracted_lead=lead,
    )

    return saved
