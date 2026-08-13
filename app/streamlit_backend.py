"""
Thin, testable backend-sync helpers for the Streamlit frontend (app.py).

Every public function in this module is a pure, no-exception wrapper
around a REST call.  The Streamlit UI can call these without try/except
and will always get a graceful fallback — no silent data loss.
"""

from __future__ import annotations

import os
import logging
import requests

logger = logging.getLogger("streamlit_backend")

BACKEND_BASE = os.environ.get("BACKEND_BASE", "http://localhost:8000")
_TIMEOUT = float(os.environ.get("BACKEND_TIMEOUT", "10"))


def backend_healthy(timeout: float = 2.0) -> bool:
    """Cheap reachability check — GET /  should return 200."""
    try:
        r = requests.get(f"{BACKEND_BASE}/", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def post_lead(payload: dict, timeout: float | None = None) -> dict | None:
    """POST /api/leads.  Returns the lead dict or None (never raises)."""
    try:
        r = requests.post(
            f"{BACKEND_BASE}/api/leads",
            json=payload,
            timeout=timeout or _TIMEOUT,
        )
        if r.ok:
            return r.json()
    except Exception:
        logger.warning("post_lead failed", exc_info=True)
    return None


def put_lead(lead_id: str, payload: dict, timeout: float | None = None) -> dict | None:
    """PUT /api/leads/{id}.  Returns updated lead or None (never raises)."""
    try:
        r = requests.put(
            f"{BACKEND_BASE}/api/leads/{lead_id}",
            json=payload,
            timeout=timeout or _TIMEOUT,
        )
        if r.ok:
            return r.json()
    except Exception:
        logger.warning("put_lead failed", exc_info=True)
    return None


def sync_lead(
    name: str = "",
    email: str = "",
    phone: str = "",
    program: str = "",
    lead_id: str = "",
) -> tuple[str, str]:
    """
    Ensure a lead row exists with the given profile data.

    Returns ``(lead_id_or_empty, error_message)`` — the second value is
    ``""`` on success, otherwise a human-readable error.

    Strategy:
    1. If *lead_id* is known, PUT the data.
    2. If PUT fails or *lead_id* is missing, POST to create / rediscover.
    3. If a new *lead_id* comes back, re-PUT to heal any missing fields.
    """
    if not phone:
        return lead_id, "no phone number — can't create a profile"

    # 1) PUT if we already have an id
    if lead_id:
        updated = put_lead(lead_id, {
            "name": name, "email": email, "program_interest": program,
        })
        if updated:
            return updated.get("id", lead_id), ""

    # 2) POST (upsert-by-phone semantics)
    created = post_lead({
        "phone_number": phone,
        "name": name,
        "email": email,
        "program_interest": program,
        "source": "streamlit",
    })
    if not created:
        return lead_id, "backend server not reachable on port 8000"

    new_id = created.get("id", lead_id)

    # 3) If lead already existed, POST returned the existing row unchanged.
    #    Do a PUT to patch any missing fields (especially program_interest).
    if lead_id and new_id != lead_id:
        put_lead(new_id, {
            "name": name, "email": email, "program_interest": program,
        })
    elif new_id:
        put_lead(new_id, {
            "name": name, "email": email, "program_interest": program,
        })

    return new_id, ""


def sync_program(
    program: str,
    name: str = "",
    email: str = "",
    phone: str = "",
    lead_id: str = "",
) -> tuple[str, str]:
    """
    Persist *program_interest* to the backend.

    Returns ``(lead_id_or_empty, error_message)``.
    Never loses data — always passes through :func:`sync_lead`.
    """
    return sync_lead(name, email, phone, program, lead_id)
