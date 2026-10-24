"""
Conversation session registry — stable conversation identity from the first moment.

Why this exists
---------------
The Salesforce user API takes a ``conversationId`` when a conversation *starts*
(``POST /users/lookup-or-create``), and that call is the only place a ``userId``
is ever handed out — there is no ``GET /users/{id}``. So the id has to exist
before the conversation begins.

Today it does not: ``create_conversation()`` mints a UUID at *write* time, after
a call has already ended (``app/leads/models.py``). Anything that needs to name
a live conversation — which is everything the CRM integration does — has nothing
to name it with.

This module mints that id at session start and holds it until the conversation
is written.

Scope
-----
**No network I/O, and no knowledge of the CRM.** This is purely local session
identity, which is what makes it safe to enable while ``CRM_ENABLED=false``.

Channel keys
------------
========================  =========================  ==============================
Channel                   Key                        Lifecycle
========================  =========================  ==============================
``inbound_call``          ``stream_sid``             explicit start / end (Twilio)
``outbound_call``         ``stream_sid``             explicit start / end (Twilio)
``whatsapp``              ``phone_number``           idle-window expiry (no end event)
``chat``                  —                          Streamlit ``session_state``; see app.py
========================  =========================  ==============================

The registry is process-local, matching the existing ``_active_call_sids``
pattern in ``app/main.py``. A miss is not an error — it simply mints a new id —
so running a second uvicorn worker degrades to per-worker sessions rather than
breaking. WhatsApp is the one channel that must survive a restart, so
:func:`resolve_whatsapp_session` falls back to the database.

Usage:
    from app.crm import session

    # Twilio Media Streams — explicit lifecycle
    s = session.start("inbound_call", stream_sid, phone_number=from_number)
    ...
    session.end("inbound_call", stream_sid)

    # WhatsApp — resume within the idle window, else start fresh
    s = await session.resolve_whatsapp_session(phone_number)
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.languages import normalize_language_code

logger = logging.getLogger("crm.session")

# Channels with an explicit end event. WhatsApp is deliberately absent — it
# expires by idle window instead.
CHANNELS = ("inbound_call", "outbound_call", "whatsapp")


@dataclass
class ConversationSession:
    """One live conversation, from its first moment to the point it is written."""

    conversation_id: str
    channel: str
    key: str
    phone_number: str = ""
    lead_id: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Filled in by Phase 2 once lookup-or-create returns it. Carried here so the
    # session is the single place a conversation's CRM identity lives.
    crm_user_id: str = ""
    # What the bot's last outbound message asked for, if it expects a specific
    # kind of reply: "", "name", "email", "program", "update_field". Keyword
    # handlers consult this before acting — a bare "yes" used to be taken as
    # program confirmation no matter what had actually been asked.
    awaiting: str = ""
    # The answer language for this conversation. WhatsApp sets it from an
    # in-chat command or the first-contact menu and it is restored from the
    # conversations row after a restart; voice calls carry the language
    # selected at call start.
    language: str = ""
    # Why the language is what it is. "selected" means the student explicitly
    # chose it (WhatsApp menu or command); empty means it is a legacy/default
    # value. Restored with the language so an explicit choice is not re-asked.
    language_source: str = ""
    # WhatsApp language-menu state. The first message is held here while the
    # student picks a language, then replayed through the normal state machine
    # in that language. Both fields are process-local and deliberately not
    # persisted: a restart loses only the held message, and a valid menu reply
    # is still recognized by its content.
    pending_language_text: str = ""
    language_menu_prompts: int = 0
    # Has the menu been answered (or deliberately skipped by the two-invalid-
    # replies fallback) in THIS process? False on every session creation,
    # including one resumed from the database after a restart — so the menu is
    # offered once at the start of each WhatsApp conversation while the saved
    # language still answers the held message. It is set on a successful
    # choice, a language command, or the fallback, and is never re-asked for
    # the life of the session.
    language_menu_done: bool = False

    def touch(self) -> None:
        """Mark activity now — resets the idle window for channels without an end event."""
        self.last_activity = datetime.now(timezone.utc)

    def idle_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.last_activity).total_seconds()

    def is_expired(self, idle_window_seconds: float) -> bool:
        return self.idle_seconds() > idle_window_seconds

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


# ── Registry ─────────────────────────────────────────────────────────────────

# Keyed by (channel, key) so a phone number can never collide with a stream sid.
_sessions: dict[tuple[str, str], ConversationSession] = {}
_lock = threading.Lock()


def _new_id() -> str:
    return str(uuid.uuid4())


def start(
    channel: str,
    key: str,
    *,
    phone_number: str = "",
    lead_id: str = "",
    conversation_id: str | None = None,
    language: str = "",
    language_source: str = "",
) -> ConversationSession:
    """
    Begin a conversation, or return the one already running under this key.

    Idempotent: calling twice with the same (channel, key) returns the same
    session, so a duplicated Twilio ``start`` event cannot fork a conversation.
    """
    if not key:
        # A blank key would make every anonymous conversation share one session.
        key = _new_id()
        logger.warning(f"crm.session: blank key for {channel} — generated {key}")

    with _lock:
        existing = _sessions.get((channel, key))
        if existing is not None:
            existing.touch()
            if phone_number and not existing.phone_number:
                existing.phone_number = phone_number
            if lead_id and not existing.lead_id:
                existing.lead_id = lead_id
            if language and not existing.language:
                existing.language = normalize_language_code(language)
            if language_source and not existing.language_source:
                existing.language_source = language_source
            logger.debug(
                f"crm.session: resumed {channel}/{key} -> {existing.conversation_id}"
            )
            return existing

        session = ConversationSession(
            conversation_id=conversation_id or _new_id(),
            channel=channel,
            key=key,
            phone_number=phone_number,
            lead_id=lead_id,
            language=normalize_language_code(language),
            language_source=(language_source or "").strip().lower(),
        )
        _sessions[(channel, key)] = session

    logger.info(
        f"crm.session: started {channel} conversation {session.conversation_id} "
        f"(key={key}, phone={phone_number or '—'})"
    )
    return session


def get(channel: str, key: str) -> ConversationSession | None:
    """Return the live session for this key, or None."""
    with _lock:
        return _sessions.get((channel, key))


def end(channel: str, key: str) -> ConversationSession | None:
    """
    Close a conversation and return it, so the caller can persist its id.

    Returning the session (rather than just removing it) is the point: the
    disconnect handler needs the id to write against, and this is the last
    moment it exists.
    """
    with _lock:
        session = _sessions.pop((channel, key), None)

    if session is not None:
        logger.info(
            f"crm.session: ended {channel} conversation {session.conversation_id} "
            f"after {session.age_seconds():.0f}s"
        )
    return session


def touch(channel: str, key: str) -> None:
    with _lock:
        session = _sessions.get((channel, key))
    if session is not None:
        session.touch()


def set_crm_user_id(channel: str, key: str, crm_user_id: str) -> None:
    """Attach the CRM userId to a live session (used by Phase 2)."""
    with _lock:
        session = _sessions.get((channel, key))
    if session is not None:
        session.crm_user_id = crm_user_id


def active(channel: str | None = None) -> list[ConversationSession]:
    """Live sessions, newest first. Useful for the dashboard and for tests."""
    with _lock:
        sessions = list(_sessions.values())
    if channel:
        sessions = [s for s in sessions if s.channel == channel]
    return sorted(sessions, key=lambda s: s.started_at, reverse=True)


def purge_expired(idle_window_seconds: float, channel: str | None = None) -> int:
    """
    Drop sessions idle beyond the window. Returns how many were dropped.

    Only meaningful for channels without an end event; voice sessions are ended
    explicitly. Called opportunistically rather than on a timer, so the registry
    cannot grow without bound if a WhatsApp conversation is abandoned.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=idle_window_seconds)
    with _lock:
        stale = [
            k
            for k, s in _sessions.items()
            if s.last_activity < cutoff and (channel is None or s.channel == channel)
        ]
        for k in stale:
            _sessions.pop(k, None)

    if stale:
        logger.info(f"crm.session: purged {len(stale)} idle session(s)")
    return len(stale)


def reset() -> None:
    """Clear the registry. For tests only."""
    with _lock:
        _sessions.clear()


# ── WhatsApp: the one channel that must survive a restart ────────────────────

async def resolve_whatsapp_session(
    phone_number: str,
    *,
    idle_window_hours: float,
) -> ConversationSession:
    """
    Find the live WhatsApp conversation for this number, or start a new one.

    WhatsApp has no session and no end event — every message is an independent
    webhook. So "the conversation" is defined as *messages from this number
    within the idle window*, and there are three places the answer can come
    from, in order:

    1. The in-process registry (fast path, the common case).
    2. The most recent ``conversations`` row for this number inside the window,
       so a restart does not split one conversation into two.
    3. A fresh session.

    The idle window is decision D4 in the plan; it comes from config so it can
    be tuned without a code change.
    """
    idle_window_seconds = idle_window_hours * 3600

    with _lock:
        existing = _sessions.get(("whatsapp", phone_number))
    if existing is not None:
        if not existing.is_expired(idle_window_seconds):
            existing.touch()
            return existing
        # Expired — close it out so the caller persists the finished conversation.
        end("whatsapp", phone_number)

    purge_expired(idle_window_seconds)

    resumed = await _recent_conversation_from_db(phone_number, idle_window_seconds)
    resumed_id = (resumed or {}).get("conversation_id") or None
    resumed_language = (resumed or {}).get("language") or ""
    resumed_source = (resumed or {}).get("language_source") or ""
    session = start("whatsapp", phone_number, phone_number=phone_number,
                    conversation_id=resumed_id, language=resumed_language,
                    language_source=resumed_source)
    if resumed_id:
        logger.info(
            f"crm.session: resumed WhatsApp conversation {resumed_id} for {phone_number} "
            f"from the database (registry was cold)"
        )
    return session


async def _recent_conversation_from_db(
    phone_number: str, idle_window_seconds: float
) -> dict | None:
    """
    The most recent conversation row for this number inside the idle window.
    Returns None when absent or when the DB is down.

    The row carries both ``conversation_id`` (so a restart does not split the
    conversation) and ``language`` (so an in-chat Spanish/French/... command
    survives the restart too).

    Never raises: running without Postgres is a supported mode in this app.
    """
    if not phone_number:
        return None
    try:
        from app.leads.models import get_recent_conversation_for_phone

        return await get_recent_conversation_for_phone(
            phone_number, within_seconds=idle_window_seconds
        )
    except Exception:
        logger.exception("crm.session: could not resume WhatsApp conversation from DB")
        return None
