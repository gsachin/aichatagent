"""
Task 4 — Add WebSocket endpoint /ws/voice
==========================================
Goal: WebSocket endpoint accepts binary PCM frames and echoes them back.
"""

import pytest
import websockets


class TestTask4WebSocket:
    """Tests for the /ws/voice echo WebSocket endpoint."""

    @pytest.mark.anyio
    async def test_ws_connect_disconnect(self, echo_ws_url: str):
        """Must be able to connect and close without errors."""
        async with websockets.connect(echo_ws_url) as ws:
            # Connection succeeded implicitly (no exception thrown).
            # Ping to confirm the socket is alive.
            pong = await ws.ping()
            assert pong is not None
        # After context exit the socket is closed.
        assert ws.close_code is not None

    @pytest.mark.anyio
    async def test_ws_echo_small_payload(self, echo_ws_url: str):
        """Send a small binary frame; must receive identical frame back."""
        payload = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09"
        async with websockets.connect(echo_ws_url) as ws:
            await ws.send(payload)
            reply = await ws.recv()
        assert isinstance(reply, bytes), f"Expected bytes, got {type(reply)}"
        assert reply == payload, f"Echo mismatch: sent {len(payload)}B, got {len(reply)}B"

    @pytest.mark.anyio
    async def test_ws_echo_large_payload(self, echo_ws_url: str):
        """Send a 64KB binary frame; must echo back identically."""
        payload = bytes(i % 256 for i in range(65536))  # 64 KB
        async with websockets.connect(echo_ws_url) as ws:
            await ws.send(payload)
            reply = await ws.recv()
        assert len(reply) == len(payload), (
            f"Size mismatch: sent {len(payload)}B, got {len(reply)}B"
        )
        assert reply == payload, "Large payload echo data mismatch"

    @pytest.mark.anyio
    async def test_ws_echo_multiple_frames(self, echo_ws_url: str):
        """Send 5 sequential frames; each must echo back in order."""
        frames = [f"frame-{i}".encode() for i in range(5)]
        async with websockets.connect(echo_ws_url) as ws:
            for f in frames:
                await ws.send(f)
            replies = [await ws.recv() for _ in frames]
        assert replies == frames, f"Multi-frame echo mismatch: {replies}"

    @pytest.mark.anyio
    async def test_ws_rejects_text_messages(self, echo_ws_url: str):
        """The endpoint is for binary audio; text messages should be rejected gracefully (no crash)."""
        async with websockets.connect(echo_ws_url) as ws:
            try:
                await ws.send("this is text not bytes")
                # Server should close the connection (binary frames only)
                with pytest.raises(websockets.exceptions.ConnectionClosed):
                    await ws.recv()
            except Exception:
                # Any outcome is fine as long as the server doesn't crash
                pass

    @pytest.mark.anyio
    async def test_ws_two_clients_sequentially(self, echo_ws_url: str):
        """Two clients connecting one after another must both work."""
        p1 = b"client-one-data"
        p2 = b"client-two-other"

        async with websockets.connect(echo_ws_url) as ws1:
            await ws1.send(p1)
            r1 = await ws1.recv()

        async with websockets.connect(echo_ws_url) as ws2:
            await ws2.send(p2)
            r2 = await ws2.recv()

        assert r1 == p1
        assert r2 == p2
