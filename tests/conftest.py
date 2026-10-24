"""
Shared pytest fixtures for the transport-layer test suite.

Fixtures provided:
    anyio_backend          — forces asyncio backend for all async tests
    test_wav_path          — path to a generated 16kHz mono test WAV (auto-created)
    test_wav_stereo_path   — path to a generated stereo WAV (for resampling edge cases)
    test_server            — starts uvicorn on a random port, yields the URL
    echo_ws_url            — full ws:// URL to the echo endpoint
"""

from __future__ import annotations

import math
import os
import struct
import socket
import sys
import wave
import threading
import time
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so "app" is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Keep the existing suite hermetic: the RAG dispatcher must not touch the MCP
# service in tests (test_rag_dispatcher.py sets the mode per test).
os.environ.setdefault("USE_MCP_RAG", "off")

# The demo .env is branded for DSPMU. The existing assertions, fixtures and
# transcripts were written against Meridian, so pin the archived brand and
# programme catalogue for the suite. New DSPMU tests call the brand helper or
# pass DSPMU_PROGRAM_ALIASES explicitly; they do not depend on .env.
os.environ.setdefault("COMPANY_NAME", "Meridian University")
os.environ.setdefault("COMPANY_SHORT_NAME", "Meridian")
os.environ.setdefault("UNIVERSITY_NAME", "Meridian University")
os.environ.setdefault("OFFER_EMAIL", "admissions@meridian.edu")
os.environ.setdefault("ADMISSIONS_EMAIL", "admissions@meridian.edu")
os.environ.setdefault("ADMISSIONS_PHONE", "+1 (555) 204-7890")
os.environ.setdefault("PROGRAM_PROFILE", "meridian")

# The demo .env sets CONTEXT_MODE=doc (full-document prompting). The existing
# retrieval/dispatcher tests were written against the retrieval path, so the
# suite pins the code default. A test that exercises doc mode overrides this
# with the `override_settings` fixture or monkeypatch.
os.environ.setdefault("CONTEXT_MODE", "rag")

# Milestone 1 points the demo .env at content/dspmu. The existing guard tests
# use the Meridian fixtures, so pin the document and entity sources to the
# Meridian KB for the suite. The new DSPMU tests construct EntityIndex directly.
os.environ.setdefault("VOICE_KB_PATH", "content/meridian/meridian_knowledge_base.md")
os.environ.setdefault("DOC_PATHS", "content/meridian/meridian_knowledge_base.md")
os.environ.setdefault("DOC_MAX_TOKENS", "500000")
# Do not attach the DSPMU prompt manifest's fact/node IDs to the Meridian
# fixture. A new bundle test overrides this explicitly.
os.environ.setdefault("CONTEXT_MANIFEST_PATH", "")

# The demo .env also sets LLM_PROVIDER=openai. Tests must stay hermetic and
# must not require LLM_API_KEY; the OpenAI adapter tests inject a fake client
# and set the provider explicitly. Pre-setting auto here wins over .env because
# load_dotenv does not override an existing process variable.
os.environ.setdefault("LLM_PROVIDER", "auto")

# The demo .env sets STT_PROVIDER=sarvam. Tests must never call a paid API or
# load the 600M ONNX fallback; provider-specific tests patch the adapter. This
# must win over .env, and load_dotenv does not override an existing variable.
os.environ.setdefault("STT_PROVIDER", "whisper")
os.environ.setdefault("STT_FALLBACK_PROVIDERS", "")

# The demo .env routes Gujarati TTS to Sarvam. Tests must not call the paid
# TTS API; keep the suite on Kokoro and let Sarvam-specific tests override.
os.environ.setdefault("TTS_PROVIDER", "kokoro")
os.environ.setdefault("SARVAM_TTS_LANGUAGES", "")

# The greeting warmup is background work for a live box. Tests must not start
# it: it would load Kokoro or call Sarvam from a TestClient lifespan.
os.environ.setdefault("GREETING_WARMUP", "0")

# The six-language feature is on by default; pin it so a developer's .env
# cannot change what the suite exercises. Tests that need it off override the
# setting and call `configured_foreign_languages.cache_clear()` if needed.
os.environ.setdefault("FOREIGN_LANGUAGES", "es,fr,it,ja,pt,zh")

# The WhatsApp start-of-chat menu changes what a first message does (it asks
# for a language instead of falling into the name/RAG flow). The existing
# WhatsApp tests were written against that flow, so keep it off by default;
# tests for the menu override the setting on.
os.environ.setdefault("WHATSAPP_LANGUAGE_MENU", "0")

# ...and keep it out of the LIVE CALL LOG. `app.main` calls `_configure_logging()`
# at import (main.py:110), which attaches a FileHandler to whatever LOG_FILE
# names -- so simply importing the app in a test made the suite append to the
# log a live call is being forensically read from. A test's deliberate
# `TURN_TIMEOUT` then appears in that record as a real production stall, which
# is exactly the wrong thing to find while preparing a demo. Forced rather than
# defaulted: a test run must never write there, whatever the shell has set.
os.environ["LOG_FILE"] = ""

# ── helpers ──────────────────────────────────────────────────────────

def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _generate_sine_wav(path: str, duration_s: float = 3.0,
                       sample_rate: int = 16000, channels: int = 1,
                       sample_width: int = 2, frequency: float = 440.0) -> None:
    """Write a sine-tone WAV file to *path*."""
    nframes = int(sample_rate * duration_s)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(16000 * math.sin(2 * math.pi * frequency * t / sample_rate)))
            for t in range(nframes)
        )
        w.writeframes(frames)


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def override_settings():
    """
    Temporarily change a ``Settings`` field, restoring it afterwards.

    ``app.config.Settings`` is a frozen dataclass, so ``monkeypatch.setattr``
    raises FrozenInstanceError on it; the values are written through
    ``object.__setattr__`` and put back by hand. Shared by the CRM tests, which
    need to flip ``CRM_ENABLED`` on for most cases and off for the gate checks.
    """
    from app.config import settings

    saved: dict[str, object] = {}

    def override(**kwargs):
        for key, value in kwargs.items():
            saved.setdefault(key, getattr(settings, key))
            object.__setattr__(settings, key, value)

    yield override

    for key, value in saved.items():
        object.__setattr__(settings, key, value)


@pytest.fixture(scope="session")
def test_wav_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Session-scoped: create a 16kHz-mono-16bit sine WAV once per test run."""
    path = str(tmp_path_factory.mktemp("audio") / "test_in.wav")
    _generate_sine_wav(path, duration_s=2.0)
    return path


@pytest.fixture(scope="session")
def test_wav_stereo_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Session-scoped: stereo variant for edge-case testing."""
    path = str(tmp_path_factory.mktemp("audio") / "test_stereo.wav")
    _generate_sine_wav(path, duration_s=1.5, channels=2)
    return path


@pytest.fixture(scope="session")
def free_port() -> int:
    return _find_free_port()


@pytest.fixture(scope="session")
def test_server(free_port: int):
    """Start the FastAPI app via uvicorn in a background thread. Yields base URL."""
    import uvicorn
    from app.main import app

    host = "127.0.0.1"

    config = uvicorn.Config(app, host=host, port=free_port, log_level="warning")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait until the server is accepting connections.
    # The app lifespan pre-warms ChromaDB + Whisper (CUDA) before serving,
    # which takes ~20-60s on first load (model check against Hugging Face).
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, free_port), timeout=0.5)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Server did not start on port {free_port} within 90s")

    yield f"http://{host}:{free_port}"

    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def echo_ws_url(test_server: str) -> str:
    """Return the full ws:// URL for the echo endpoint."""
    # Replace http:// with ws://
    return test_server.replace("http://", "ws://") + "/ws/voice"
