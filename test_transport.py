"""
WAV File WebSocket Transport Test Client.

Streams a WAV file in PCM chunks over WebSocket to /ws/voice,
receives echo/TTS audio frames back, and writes the assembled
output to a WAV file.

Usage:
    python test_transport.py                          # uses defaults
    python test_transport.py input.wav output.wav     # custom files

Environment variables (for automated testing):
    TEST_WS_URL     — WebSocket URL (default: ws://127.0.0.1:8000/ws/voice)
    TEST_IN_WAV     — input WAV path  (default: test_in.wav)
    TEST_OUT_WAV    — output WAV path (default: test_out.wav)
"""

import asyncio
import os
import sys
import wave

import websockets

# ── Configuration ───────────────────────────────────────────────────

WS_URL = os.environ.get("TEST_WS_URL", "ws://127.0.0.1:8000/ws/voice")
CHUNK_MS = 20  # chunk duration in milliseconds


def _input_path() -> str:
    """Resolve input WAV path: env var > CLI arg 1 > default."""
    if "TEST_IN_WAV" in os.environ:
        return os.environ["TEST_IN_WAV"]
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "test_in.wav"


def _output_path() -> str:
    """Resolve output WAV path: env var > CLI arg 2 > default."""
    if "TEST_OUT_WAV" in os.environ:
        return os.environ["TEST_OUT_WAV"]
    if len(sys.argv) > 2:
        return sys.argv[2]
    return "test_out.wav"


# ── Stream client ───────────────────────────────────────────────────

async def stream_wav(input_path: str, output_path: str) -> int:
    """
    Stream *input_path* WAV over WebSocket, write echoed audio to *output_path*.

    Returns total bytes received.
    """
    # ---- read WAV metadata ------------------------------------------
    with wave.open(input_path, "rb") as wav_in:
        sample_rate = wav_in.getframerate()
        channels = wav_in.getnchannels()
        sample_width = wav_in.getsampwidth()
        total_frames = wav_in.getnframes()
        raw_pcm = wav_in.readframes(total_frames)

    chunk_frames = int(sample_rate * CHUNK_MS / 1000)  # frames per 20ms
    chunk_bytes = chunk_frames * channels * sample_width
    total_bytes = len(raw_pcm)

    print(f"Input:  {input_path}")
    print(f"        {sample_rate} Hz, {channels} ch, {sample_width * 8}-bit")
    print(f"        {total_frames} frames, {total_bytes} bytes")
    print(f"Chunks: {chunk_bytes} bytes each (~{CHUNK_MS} ms)")
    print(f"Server: {WS_URL}")

    # ---- connect and stream -----------------------------------------
    received_data = bytearray()

    async with websockets.connect(WS_URL) as ws:
        bytes_sent = 0

        # Send PCM in chunks
        for offset in range(0, total_bytes, chunk_bytes):
            chunk = raw_pcm[offset:offset + chunk_bytes]
            if not chunk:
                break
            await ws.send(chunk)
            bytes_sent += len(chunk)

        print(f"Sent:    {bytes_sent} bytes ({bytes_sent / total_bytes:.0%})")

        # Receive echoed frames back
        bytes_received = 0
        while bytes_received < bytes_sent:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            if isinstance(frame, bytes):
                received_data.extend(frame)
                bytes_received += len(frame)

        print(f"Received: {bytes_received} bytes ({bytes_received / bytes_sent:.0%})")

    # ---- write output WAV -------------------------------------------
    with wave.open(output_path, "wb") as wav_out:
        wav_out.setnchannels(channels)
        wav_out.setsampwidth(sample_width)
        wav_out.setframerate(sample_rate)
        wav_out.writeframes(received_data)

    out_size = os.path.getsize(output_path)
    print(f"Output:  {output_path}")
    print(f"        {len(received_data) // (channels * sample_width)} frames, {out_size} bytes")

    return bytes_received


# ── Entry point ─────────────────────────────────────────────────────

def main() -> int:
    input_path = _input_path()
    output_path = _output_path()

    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}")
        print("Create one with: python -c \"import wave,struct,math; ...\"")
        return 1

    try:
        bytes_received = asyncio.run(stream_wav(input_path, output_path))
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    if bytes_received == 0:
        print("WARNING: received 0 bytes — check server is running")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
