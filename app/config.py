"""
Transport configuration for the University Admissions Voice Assistant.

All settings are in one place so swapping transports (local WAV harness,
browser mic, or Twilio telephony) is a single-line change.

Loads from .env file if present (python-dotenv), with defaults for development.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Load the project .env from the repo root — anchored to this file so it
# works regardless of the process CWD (e.g. Streamlit launched elsewhere).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    """Get an environment variable with a default."""
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    # ── Transport provider ──────────────────────────────────────────
    # "websocket" = local WAV harness / browser mic (current default)
    # "twilio"    = Twilio Media Streams (requires credentials below)
    TRANSPORT_PROVIDER: str = field(default_factory=lambda: _env("TRANSPORT_PROVIDER", "websocket"))

    # ── FastAPI server ──────────────────────────────────────────────
    HOST: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    PORT: int = field(default_factory=lambda: int(_env("PORT", "8000")))

    # ── Audio format (PCM) ──────────────────────────────────────────
    AUDIO_SAMPLE_RATE: int = 16000   # 16 kHz
    AUDIO_CHANNELS: int = 1          # mono
    AUDIO_SAMPLE_WIDTH: int = 2      # 16-bit

    # ── Chunk size for streaming (in frames) ────────────────────────
    CHUNK_FRAMES: int = 320          # 20 ms at 16 kHz

    # ── Twilio credentials ──────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID", ""))
    TWILIO_AUTH_TOKEN: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN", ""))
    TWILIO_PHONE_NUMBER: str = field(default_factory=lambda: _env("TWILIO_PHONE_NUMBER", ""))
    TWILIO_WHATSAPP_NUMBER: str = field(default_factory=lambda: _env("TWILIO_WHATSAPP_NUMBER", ""))

    # ── PostgreSQL database ─────────────────────────────────────────
    DATABASE_URL: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    DB_HOST: str = field(default_factory=lambda: _env("DB_HOST", "localhost"))
    DB_PORT: str = field(default_factory=lambda: _env("DB_PORT", "5432"))
    DB_NAME: str = field(default_factory=lambda: _env("DB_NAME", "admissions"))
    DB_USER: str = field(default_factory=lambda: _env("DB_USER", "postgres"))
    DB_PASSWORD: str = field(default_factory=lambda: _env("DB_PASSWORD", ""))

    # ── Outbound call engine ────────────────────────────────────────
    OUTBOUND_POLL_INTERVAL: int = field(
        default_factory=lambda: int(_env("OUTBOUND_POLL_INTERVAL", "10"))
    )
    MAX_CALL_ATTEMPTS: int = field(
        default_factory=lambda: int(_env("MAX_CALL_ATTEMPTS", "3"))
    )

    # ── Follow-up scheduler ─────────────────────────────────────────
    FOLLOW_UP_POLL_INTERVAL: int = field(
        default_factory=lambda: int(_env("FOLLOW_UP_POLL_INTERVAL", "30"))
    )

    # ── MCP server ──────────────────────────────────────────────────
    MCP_ENABLED: bool = field(
        default_factory=lambda: _env("MCP_ENABLED", "true").lower() == "true"
    )

    # ── Offer letter / documents ────────────────────────────────────
    DATA_DIR: str = field(default_factory=lambda: _env("DATA_DIR", "data"))
    UNIVERSITY_NAME: str = field(
        default_factory=lambda: _env("UNIVERSITY_NAME", "Meridian University")
    )
    OFFER_EMAIL: str = field(
        default_factory=lambda: _env("OFFER_EMAIL", "admissions@university.edu")
    )
    OFFER_VALID_DAYS: int = field(
        default_factory=lambda: int(_env("OFFER_VALID_DAYS", "30"))
    )
    OFFER_GUARD_MINUTES: int = field(
        default_factory=lambda: int(_env("OFFER_GUARD_MINUTES", "1"))
    )
    DEFAULT_PAYMENT_LINK: str = field(
        default_factory=lambda: _env("DEFAULT_PAYMENT_LINK", "https://pay.university.edu/admissions")
    )

    # ── Email / SMTP ───────────────────────────────────────────────
    SMTP_HOST: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.gmail.com"))
    SMTP_PORT: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    SMTP_USER: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    SMTP_PASS: str = field(default_factory=lambda: _env("SMTP_PASS", ""))

    # ── Salesforce user API ────────────────────────────────────────
    # Master switch. OFF by default: with it off, nothing in the app calls
    # Salesforce and behaviour is identical to today. Phases 3-7 are only
    # reachable when this is true.
    CRM_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_ENABLED", "false").lower() == "true"
    )
    # Loopback dev deployment; see the plan's decision D2.
    CRM_BASE_URL: str = field(
        default_factory=lambda: _env("CRM_BASE_URL", "http://127.0.0.1:8098")
    )
    # WhatsApp has no session and no end event, so "one conversation" is defined
    # by an idle window. Decision D4 in the plan; this is the proposed default.
    CRM_IDLE_WINDOW_HOURS: float = field(
        default_factory=lambda: float(_env("CRM_IDLE_WINDOW_HOURS", "6"))
    )
    # The API has no authentication today (plan risk R8); if a shared secret is
    # ever added in front of it, this is sent as X-API-Key. Unset = no header.
    CRM_API_KEY: str = field(default_factory=lambda: _env("CRM_API_KEY", ""))
    # Deliberately short: this sits behind a live phone call. A slow CRM must
    # give up quickly rather than delay the caller.
    CRM_TIMEOUT_CONNECT_S: float = field(
        default_factory=lambda: float(_env("CRM_TIMEOUT_CONNECT_S", "2"))
    )
    CRM_TIMEOUT_READ_S: float = field(
        default_factory=lambda: float(_env("CRM_TIMEOUT_READ_S", "5"))
    )
    CRM_MAX_RETRIES: int = field(
        default_factory=lambda: int(_env("CRM_MAX_RETRIES", "3"))
    )
    # Consecutive failures before the breaker opens and we stop calling out.
    CRM_BREAKER_THRESHOLD: int = field(
        default_factory=lambda: int(_env("CRM_BREAKER_THRESHOLD", "5"))
    )
    # How long the breaker stays open before allowing a probe request.
    CRM_BREAKER_RESET_S: float = field(
        default_factory=lambda: float(_env("CRM_BREAKER_RESET_S", "60"))
    )
    # A queued write is abandoned after this many attempts. Rare — the outbox
    # only ever holds *transient* failures, since permanent ones are dropped.
    CRM_OUTBOX_MAX_ATTEMPTS: int = field(
        default_factory=lambda: int(_env("CRM_OUTBOX_MAX_ATTEMPTS", "10"))
    )
    # How often the worker drains the outbox and sweeps lapsed offers. Slower
    # than the other loops on purpose: nothing here is time-critical, and each
    # tick is a database query plus, when there is work, a CRM call.
    CRM_OUTBOX_POLL_INTERVAL: int = field(
        default_factory=lambda: int(_env("CRM_OUTBOX_POLL_INTERVAL", "60"))
    )

    # ── Offer-letter document upload ───────────────────────────────
    # Separate switch from CRM_ENABLED. The document upload is the one CRM
    # write that leaves residue we cannot remove — the API has no delete route
    # for a ContentVersion — so an operator needs to be able to stop it alone
    # while status sync keeps running.
    CRM_OFFER_UPLOAD_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_OFFER_UPLOAD_ENABLED", "true").lower() == "true"
    )
    # The student's own documents — transcript, ID proof. A separate switch
    # because these carry grades and identity documents, and an operator may want
    # the generated offer letter in the CRM while these stay on this host until
    # someone signs off on that egress.
    CRM_DOCUMENT_UPLOAD_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_DOCUMENT_UPLOAD_ENABLED", "true").lower() == "true"
    )
    # These land in the ContentVersion fields Document_Type__c / Source__c. The
    # API validates neither (both are free strings), but both are restricted
    # picklists in the dev org, so an out-of-vocabulary value fails as a 500
    # (R16) rather than a 400 — which is why they are settings, not constants.
    #
    # "Other" is deliberate, and not the intent: the org's Document_Type__c has
    # no offer-letter value (verified against Salesforce field metadata, see
    # scripts/verify_offer_document_upload.py --describe-only). "Offer Letter"
    # becomes correct — and should replace this default — once an admin adds it
    # to the picklist. Until then "Other" is the only honest fit among
    # {Academic Transcript, Resume, Financial Document, ID Document, Admission
    # Call Recording, Chatbot Conversation Record, Test Score, Recommendation
    # Letter, Other, 10th/12th/Diploma Marksheet, Bachelor's Last-Semester
    # Result, IELTS Score, Diploma Last-Semester Result}.
    CRM_OFFER_DOCUMENT_TYPE: str = field(
        default_factory=lambda: _env("CRM_OFFER_DOCUMENT_TYPE", "Other")
    )
    # The only Source__c value the dev org uses today, and it is allowed.
    CRM_OFFER_DOCUMENT_SOURCE: str = field(
        default_factory=lambda: _env("CRM_OFFER_DOCUMENT_SOURCE", "Internal Upload")
    )
    # Offer PDFs are ~2.4 KB, so this is one chunk in practice. The cap exists so
    # a future large document still uploads rather than failing the size check.
    CRM_UPLOAD_CHUNK_BYTES: int = field(
        default_factory=lambda: int(_env("CRM_UPLOAD_CHUNK_BYTES", str(1024 * 1024)))
    )
    # Per-request override for the three upload calls. The shared read timeout
    # above is sized for a phone call; /complete performs a synchronous
    # ContentVersion create (base64-encoding the file and re-fetching an OAuth
    # token first), which does not fit in 5s.
    CRM_UPLOAD_TIMEOUT_S: float = field(
        default_factory=lambda: float(_env("CRM_UPLOAD_TIMEOUT_S", "30"))
    )


# Module-level singleton
settings = Settings()
