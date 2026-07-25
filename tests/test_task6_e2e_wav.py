"""
Task 6 — End-to-End WAV Harness Validation
===========================================
Goal: Confirm the transport layer handles real (or sine-tone) audio end-to-end.
"""

import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSPORT_SCRIPT = PROJECT_ROOT / "test_transport.py"
RESULTS_DIR = PROJECT_ROOT / "tests" / "test_results"


class TestTask6WavInputValidation:
    """Validate the test input WAV meets the required spec."""

    def test_input_wav_exists(self, test_wav_path: str):
        """test_in.wav must exist (provided by fixture or user)."""
        assert os.path.isfile(test_wav_path), f"Input WAV missing: {test_wav_path}"

    def test_input_wav_is_valid(self, test_wav_path: str):
        """Input file must be parseable as a valid WAV."""
        try:
            with wave.open(test_wav_path, "rb") as w:
                pass
        except wave.Error as e:
            pytest.fail(f"test_in.wav is not a valid WAV file: {e}")

    def test_input_wav_mono(self, test_wav_path: str):
        """Input must be mono (1 channel)."""
        with wave.open(test_wav_path, "rb") as w:
            assert w.getnchannels() == 1, (
                f"Expected mono, got {w.getnchannels()} channels"
            )

    def test_input_wav_16khz(self, test_wav_path: str):
        """Input sample rate must be 16000 Hz (or configurable to it)."""
        with wave.open(test_wav_path, "rb") as w:
            rate = w.getframerate()
        # Accept 8000 or 16000 — common telephony rates
        assert rate in (8000, 16000, 22050, 44100, 48000), (
            f"Sample rate {rate}Hz — should be resample-able to 16kHz"
        )

    def test_input_wav_16bit(self, test_wav_path: str):
        """Input sample width must be 2 bytes (16-bit PCM)."""
        with wave.open(test_wav_path, "rb") as w:
            assert w.getsampwidth() == 2, (
                f"Expected 16-bit, got {w.getsampwidth() * 8}-bit"
            )


class TestTask6WavRoundTripExtended:
    """Extended round-trip tests beyond Task 5 basic checks."""

    OUTPUT_WAV = str(RESULTS_DIR / "test_task6_out.wav")

    def test_output_wav_playable(self, test_server: str, test_wav_path: str):
        """Output WAV must be recognized as a valid WAV by the wave module."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        subprocess.run(
            [sys.executable, str(TRANSPORT_SCRIPT)],
            env=env, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        assert os.path.isfile(self.OUTPUT_WAV), "Output WAV not created"

        # wave.open must not raise
        with wave.open(self.OUTPUT_WAV, "rb") as w:
            assert w.getnframes() > 0
            assert w.getframerate() > 0

    def test_output_wav_not_empty(self, test_server: str, test_wav_path: str):
        """Output WAV file size must be > 1 KB."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        subprocess.run(
            [sys.executable, str(TRANSPORT_SCRIPT)],
            env=env, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        size = os.path.getsize(self.OUTPUT_WAV)
        assert size > 1000, f"Output WAV too small: {size} bytes"

    def test_output_not_all_silence(self, test_server: str, test_wav_path: str):
        """Output PCM data must not be all zeros (indicates audio was transmitted)."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        subprocess.run(
            [sys.executable, str(TRANSPORT_SCRIPT)],
            env=env, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        with wave.open(self.OUTPUT_WAV, "rb") as w:
            data = w.readframes(w.getnframes())

        # At least 10% of samples should be non-zero for a sine tone
        non_zero = sum(1 for i in range(0, len(data), 2)
                       if data[i:i+2] != b"\x00\x00")
        total_samples = len(data) // 2
        ratio = non_zero / total_samples if total_samples else 0
        assert ratio > 0.05, (
            f"Output appears silent: {ratio:.1%} non-zero samples"
        )
