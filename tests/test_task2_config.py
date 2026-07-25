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
        """AUDIO_SAMPLE_RATE must be a positive integer."""
        from app.config import settings
        assert isinstance(settings.AUDIO_SAMPLE_RATE, int)
        assert settings.AUDIO_SAMPLE_RATE > 0

    def test_audio_channels_is_mono(self):
        """AUDIO_CHANNELS should be 1 (mono) for telephony."""
        from app.config import settings
        assert settings.AUDIO_CHANNELS == 1, (
            f"Expected 1 channel (mono), got {settings.AUDIO_CHANNELS}"
        )

    def test_audio_sample_width_bytes(self):
        """AUDIO_SAMPLE_WIDTH should be 2 (16-bit)."""
        from app.config import settings
        assert settings.AUDIO_SAMPLE_WIDTH == 2

    def test_config_is_idempotent(self):
        """Importing settings twice returns the same object (singleton or module-level)."""
        from app.config import settings as s1
        from app.config import settings as s2
        assert s1 is s2
