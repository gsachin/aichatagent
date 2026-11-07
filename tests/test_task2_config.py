"""
Task 2 — Create app/config.py (Transport Configuration)
========================================================
Goal: Single configuration file with transport settings and Twilio placeholders.
"""

import os
from pathlib import Path


class TestTask2Config:
    """Verify config.py exists, exports settings, and has correct defaults."""

    def test_config_file_exists(self):
        """app/config.py must be a regular file."""
        cfg = Path(__file__).resolve().parent.parent / "app" / "config.py"
        assert cfg.is_file(), f"config.py not found at {cfg}"

    def test_settings_importable(self):
        """settings object must be importable from app.config."""
        from app.config import settings
        assert settings is not None

    def test_transport_provider_default(self):
        """Default TRANSPORT_PROVIDER must be 'websocket'."""
        from app.config import settings
        assert settings.TRANSPORT_PROVIDER == "websocket", (
            f"Expected 'websocket', got '{settings.TRANSPORT_PROVIDER}'"
        )

    def test_host_is_localhost(self):
        """HOST should bind to localhost by default."""
        from app.config import settings
        assert settings.HOST in ("127.0.0.1", "localhost", "0.0.0.0")

    def test_port_is_integer(self):
        """PORT must be an integer in the valid range."""
        from app.config import settings
        assert isinstance(settings.PORT, int), f"PORT is {type(settings.PORT)}, expected int"
        assert 1024 <= settings.PORT <= 65535, f"PORT {settings.PORT} out of valid range"

    def test_audio_sample_rate_is_positive(self):
        """The PCM path's rate must be a positive integer (app/audio_format.py)."""
        from app.audio_format import SAMPLE_RATE
        assert isinstance(SAMPLE_RATE, int)
        assert SAMPLE_RATE > 0

    def test_audio_channels_is_mono(self):
        """CHANNELS must be 1 — audioop's u-law conversions are mono only."""
        from app.audio_format import CHANNELS
        assert CHANNELS == 1, f"Expected 1 channel (mono), got {CHANNELS}"

    def test_audio_sample_width_bytes(self):
        """SAMPLE_WIDTH must be 2 — the pipeline's 16-bit linear PCM."""
        from app.audio_format import SAMPLE_WIDTH
        assert SAMPLE_WIDTH == 2

    def test_chunk_frames_follows_the_rate(self):
        """CHUNK_FRAMES is 20 ms of audio — derived, so it cannot desync from the rate."""
        from app.audio_format import CHUNK_FRAMES, CHUNK_MS, SAMPLE_RATE
        assert CHUNK_FRAMES == SAMPLE_RATE * CHUNK_MS // 1000

    def test_config_is_idempotent(self):
        """Importing settings twice returns the same object (singleton or module-level)."""
        from app.config import settings as s1
        from app.config import settings as s2
        assert s1 is s2
