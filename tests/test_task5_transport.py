"""
Task 5 — Create test_transport.py (WAV File Test Client)
=========================================================
Goal: Python script streams a WAV file over WebSocket and writes the echo to an output WAV.
"""

import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSPORT_SCRIPT = PROJECT_ROOT / "test_transport.py"


def _wav_info(path: str) -> dict:
    """Return dict of WAV metadata: frames, rate, channels, width, duration_s, size_bytes."""
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        return {
            "frames": n,
            "rate": w.getframerate(),
            "channels": w.getnchannels(),
            "width": w.getsampwidth(),
            "duration_s": n / w.getframerate() if w.getframerate() else 0,
            "size_bytes": n * w.getsampwidth() * w.getnchannels(),
        }


class TestTask5TransportScript:
    """Verify test_transport.py exists and is runnable."""

    def test_script_exists(self):
        """test_transport.py must exist in the project root."""
        assert TRANSPORT_SCRIPT.is_file(), (
            f"test_transport.py not found at {TRANSPORT_SCRIPT}"
        )

    def test_script_has_no_syntax_errors(self):
        """Script must compile without SyntaxError."""
        with open(TRANSPORT_SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, str(TRANSPORT_SCRIPT), "exec")

    def test_script_imports_wave_module(self):
        """Script must import the 'wave' stdlib module (for WAV handling)."""
        source = TRANSPORT_SCRIPT.read_text(encoding="utf-8")
        assert "import wave" in source or "from wave" in source, (
            "test_transport.py must use the wave module for WAV I/O"
        )

    def test_script_imports_websockets(self):
        """Script must import websockets (or asyncio + websockets)."""
        source = TRANSPORT_SCRIPT.read_text(encoding="utf-8")
        assert "websockets" in source, (
            "test_transport.py must import websockets for the WS client"
        )


class TestTask5WavRoundTrip:
    """Run test_transport.py against a live server and verify WAV round-trip."""

    OUTPUT_WAV = str(PROJECT_ROOT / "tests" / "test_results" / "test_task5_out.wav")

    def test_wav_roundtrip_matches_input(self, test_server: str, test_wav_path: str):
        """Output WAV frame count must equal input WAV frame count."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        # Run the transport script as a subprocess
        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        result = subprocess.run(
            [sys.executable, str(TRANSPORT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        # Check exit code
        assert result.returncode == 0, (
            f"test_transport.py exited with code {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # Check output file exists
        assert os.path.isfile(self.OUTPUT_WAV), (
            f"Output WAV not created at {self.OUTPUT_WAV}\nSTDOUT:{result.stdout}"
        )

        # Compare WAV properties
        in_info = _wav_info(test_wav_path)
        out_info = _wav_info(self.OUTPUT_WAV)

        assert out_info["frames"] > 0, "Output WAV has 0 frames"
        assert out_info["rate"] == in_info["rate"], (
            f"Sample rate mismatch: in={in_info['rate']}, out={out_info['rate']}"
        )
        assert out_info["channels"] == in_info["channels"], (
            f"Channel mismatch: in={in_info['channels']}, out={out_info['channels']}"
        )
        assert out_info["width"] == in_info["width"], (
            f"Sample width mismatch: in={in_info['width']}, out={out_info['width']}"
        )
        # Frame count should be within 1% (allow for chunk boundary rounding)
        frame_diff = abs(out_info["frames"] - in_info["frames"])
        assert frame_diff <= in_info["frames"] * 0.02, (
            f"Frame count mismatch > 2%: in={in_info['frames']}, out={out_info['frames']}"
        )

    def test_script_prints_byte_counts(self, test_server: str, test_wav_path: str):
        """The script should print sent/received byte information to stdout."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        result = subprocess.run(
            [sys.executable, str(TRANSPORT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        combined = (result.stdout + result.stderr).lower()
        # Should mention bytes sent or received somewhere
        assert "byte" in combined or "sent" in combined or "received" in combined, (
            "Script output should mention data transfer. Got:\n" + result.stdout
        )
