"""
Transport configuration for the University Admissions Voice Assistant.

All settings are in one place so swapping transports (local WAV harness,
browser mic, or Twilio telephony) is a single-line change.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # ── Transport provider ──────────────────────────────────────────
    # "websocket" = local WAV harness / browser mic (current default)
    # "twilio"    = Twilio Media Streams (requires credentials below)
    TRANSPORT_PROVIDER: str = "websocket"

    # ── FastAPI server ──────────────────────────────────────────────
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ── Audio format (PCM) ──────────────────────────────────────────
    AUDIO_SAMPLE_RATE: int = 16000   # 16 kHz
    AUDIO_CHANNELS: int = 1          # mono
    AUDIO_SAMPLE_WIDTH: int = 2      # 16-bit

    # ── Chunk size for streaming (in frames) ────────────────────────
    CHUNK_FRAMES: int = 320          # 20 ms at 16 kHz

    # ── Twilio credentials (Phase B — fill in when ready) ───────────
    # TWILIO_ACCOUNT_SID: str = ""
    # TWILIO_AUTH_TOKEN: str = ""
    # TWILIO_PHONE_NUMBER: str = ""

    # ── PostgreSQL database (Phase 6 — optional for lead capture) ────
    # DATABASE_URL: str = ""  # Full connection string (takes precedence)
    # DB_HOST: str = "localhost"
    # DB_PORT: int = 5432
    # DB_NAME: str = "admissions"
    # DB_USER: str = "postgres"
    # DB_PASSWORD: str = ""


# Module-level singleton — importing again returns the same frozen instance.
settings = Settings()
