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

    # Wait until the server is accepting connections
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, free_port), timeout=0.5)
            s.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Server did not start on port {free_port} within 10s")

    yield f"http://{host}:{free_port}"

    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def echo_ws_url(test_server: str) -> str:
    """Return the full ws:// URL for the echo endpoint."""
    # Replace http:// with ws://
    return test_server.replace("http://", "ws://") + "/ws/voice"
