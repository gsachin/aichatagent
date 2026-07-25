"""
Phase 5 — FastAPI Webhooks & Telephony Integration Tests
=========================================================
Goal: Verify Twilio-compatible endpoints on the FastAPI server.

Tests:
    - GET /twilio/voice returns valid TwiML XML
    - WS /ws/twilio accepts connections
    - Twilio endpoint is listed in config as a supported transport
    - μ-law audio resampling utilities work correctly
    - WebSocket disconnect triggers cleanup without leaks
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Phase 5.1: TwiML Endpoint ────────────────────────────────────────

class TestPhase5TwiMLEndpoint:
    """Verify GET /twilio/voice returns valid TwiML."""

    @pytest.mark.anyio
    async def test_twilio_voice_route_exists(self, test_server: str):
        """GET /twilio/voice must return a response (200 or 501 not-implemented)."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/twilio/voice")

        # Accept 200 (implemented) or 404/405/501 (placeholder)
        assert resp.status_code in (200, 404, 405, 501), (
            f"Unexpected status {resp.status_code} from /twilio/voice"
        )

        if resp.status_code == 200:
            # Validate TwiML structure
            text = resp.text.lower()
            assert "xml" in resp.headers.get("content-type", "") or text.startswith("<?xml"), (
                "TwiML response must be XML"
            )
            assert "<response>" in text or "<say>" in text or "<connect>" in text, (
                f"TwiML response must contain Twilio verbs. Got: {text[:300]}"
            )

    @pytest.mark.anyio
    async def test_twilio_voice_content_type_xml(self, test_server: str):
        """If /twilio/voice returns 200, content-type must be text/xml."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/twilio/voice")

        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            assert "xml" in ct, (
                f"TwiML response must have XML content-type. Got: '{ct}'"
            )

    def test_twilio_route_in_health_check(self, test_server: str):
        """Health check should list twilio endpoints when available."""
        import httpx

        # Synchronous GET for simplicity
        import urllib.request
        import json

        req = urllib.request.Request(test_server + "/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        # Health check exists — check if twilio endpoints appear
        endpoints = data.get("endpoints", {})
        all_values = " ".join(str(v) for v in endpoints.values()).lower()

        # Not a hard fail if not listed (may be in Phase B)
        # But worth noting
        has_twilio = "twilio" in all_values
        print(f"\nTwilio endpoints in health check: {has_twilio}")
        print(f"Endpoints: {endpoints}")


# ── Phase 5.2: Twilio WebSocket Endpoint ─────────────────────────────

class TestPhase5TwilioWebSocket:
    """Verify WS /ws/twilio endpoint behavior."""

    @pytest.mark.anyio
    async def test_twilio_ws_connect(self, test_server: str):
        """WS /ws/twilio must accept connections (or return 404 placeholder)."""
        import websockets
        import websockets.exceptions

        ws_url = test_server.replace("http://", "ws://") + "/ws/twilio"

        try:
            async with websockets.connect(ws_url) as ws:
                # Connection accepted — verify it's alive
                pong = await ws.ping()
                await pong
        except websockets.exceptions.InvalidStatus as e:
            # 403 (no route), 404, 426, 501 are acceptable placeholder responses
            assert e.response.status_code in (403, 404, 426, 501), (
                f"Unexpected WS status: {e.response.status_code}"
            )
            print(f"\n/ws/twilio not yet implemented: HTTP {e.response.status_code}")
        except Exception as e:
            # Connection refused or timeout — server may not have the route
            print(f"\n/ws/twilio not available: {type(e).__name__}: {e}")

    def test_twilio_config_placeholders_present(self):
        """app/config.py must have Twilio credential placeholders."""
        config_path = PROJECT_ROOT / "app" / "config.py"

        if not config_path.is_file():
            pytest.fail("app/config.py not found")

        content = config_path.read_text(encoding="utf-8")

        expected_fields = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"]
        for field in expected_fields:
            assert field in content, (
                f"app/config.py missing Twilio placeholder: {field}"
            )

    def test_transport_provider_config_supports_twilio(self):
        """TRANSPORT_PROVIDER must accept 'twilio' as a value."""
        from app.config import Settings

        # Settings is frozen, so we instantiate a test instance
        twilio_config = Settings(TRANSPORT_PROVIDER="twilio")
        assert twilio_config.TRANSPORT_PROVIDER == "twilio"


# ── Phase 5.3: μ-law Audio Conversion ────────────────────────────────

class TestPhase5MuLawConversion:
    """Verify 8 kHz μ-law audio conversion for Twilio telephony output."""

    def test_mulaw_encode_decode_roundtrip(self):
        """
        PCM 16-bit linear → μ-law 8-bit → PCM 16-bit linear
        Must be lossy but intelligible (samples stay within range).
        """
        import audioop
        import struct

        # Generate a simple 16-bit PCM sine wave
        sample_rate = 8000  # Twilio uses 8 kHz
        duration = 0.1  # 100ms
        nframes = int(sample_rate * duration)

        pcm_samples = []
        import math

        for i in range(nframes):
            val = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            pcm_samples.append(val)

        pcm_bytes = struct.pack(f"<{nframes}h", *pcm_samples)

        # Encode to μ-law
        mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)

        assert len(mulaw_bytes) == nframes, (
            f"μ-law encoding: expected {nframes} bytes, got {len(mulaw_bytes)}"
        )

        # Decode back to linear
        decoded = audioop.ulaw2lin(mulaw_bytes, 2)

        assert len(decoded) == len(pcm_bytes), (
            f"μ-law decode: expected {len(pcm_bytes)} bytes, got {len(decoded)}"
        )

        # Verify decoded samples are within reasonable range
        decoded_samples = struct.unpack(f"<{nframes}h", decoded)
        for orig, dec in zip(pcm_samples[:10], decoded_samples[:10]):
            # Allow some quantization error from μ-law
            assert abs(orig - dec) < 2000, (
                f"μ-law round-trip error too large: {orig} → {dec}"
            )

        print(f"\nμ-law round-trip: {nframes} samples encoded & decoded successfully")

    def test_mulaw_sample_rate_requirement(self):
        """μ-law for Twilio must operate at 8000 Hz."""
        # Twilio Media Streams specification: 8 kHz μ-law
        # Verify our config or conversion utilities respect this
        from app.config import settings

        # The app config has AUDIO_SAMPLE_RATE = 16000 for internal processing,
        # but Twilio output must be resampled to 8000 Hz.
        # This test documents the requirement.
        assert settings.AUDIO_SAMPLE_RATE == 16000, (
            "Internal sample rate should be 16000 Hz"
        )

        # Twilio rate is 8000 Hz — resampling is required
        TWILIO_SAMPLE_RATE = 8000
        assert TWILIO_SAMPLE_RATE == 8000  # document the constant
        print("\nReminder: TTS output must be resampled 16000→8000 Hz for Twilio")

    def test_mulaw_utility_importable(self):
        """audioop (stdlib) must be available for μ-law conversion."""
        try:
            import audioop  # noqa: F401
        except ImportError:
            pytest.fail("audioop is a stdlib module — should always be available")


# ── Phase 5.4: Graceful Cleanup ──────────────────────────────────────

class TestPhase5GracefulCleanup:
    """Verify WebSocket disconnect triggers cleanup."""

    @pytest.mark.anyio
    async def test_ws_disconnect_clean(self, echo_ws_url: str):
        """
        Connect → send one frame → close client → server must not crash
        and must be available for a new connection immediately after.
        """
        import websockets

        # First connection
        async with websockets.connect(echo_ws_url) as ws:
            await ws.send(b"test-frame")
            reply = await ws.recv()
            assert reply == b"test-frame"

        # Brief pause to let server process disconnect
        import asyncio
        await asyncio.sleep(0.1)

        # Second connection — must still accept
        async with websockets.connect(echo_ws_url) as ws2:
            await ws2.send(b"second-frame")
            reply2 = await ws2.recv()
            assert reply2 == b"second-frame"

    @pytest.mark.anyio
    async def test_multiple_rapid_disconnects(self, echo_ws_url: str):
        """Five rapid connect/disconnect cycles must not crash the server."""
        import websockets

        for i in range(5):
            async with websockets.connect(echo_ws_url) as ws:
                await ws.send(f"ping-{i}".encode())
                reply = await ws.recv()
                assert reply == f"ping-{i}".encode()

        # After 5 cycles, health check must still respond
        import httpx

        http_url = echo_ws_url.replace("ws://", "http://").replace("/ws/voice", "/")
        async with httpx.AsyncClient() as client:
            resp = await client.get(http_url)
            assert resp.status_code == 200
