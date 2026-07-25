"""
Transport configuration for the University Admissions Voice Assistant.

All settings are in one place so swapping transports (local WAV harness,
browser mic, or Twilio telephony) is a single-line change.

Loads from .env file if present (python-dotenv), with defaults for development.
"""

import os
from dataclasses import dataclass, field

# Load .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
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

    # ── PostgreSQL database ─────────────────────────────────────────
    DATABASE_URL: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    DB_HOST: str = field(default_factory=lambda: _env("DB_HOST", "localhost"))
    DB_PORT: str = field(default_factory=lambda: _env("DB_PORT", "5432"))
    DB_NAME: str = field(default_factory=lambda: _env("DB_NAME", "admissions"))
    DB_USER: str = field(default_factory=lambda: _env("DB_USER", "postgres"))
    DB_PASSWORD: str = field(default_factory=lambda: _env("DB_PASSWORD", ""))


# Module-level singleton
settings = Settings()
