"""
Task 9 — Create requirements.txt + Final Integration Check
===========================================================
Goal: Formalize dependencies and confirm all packages are importable.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"


class TestTask9RequirementsFile:
    """Verify requirements.txt is complete and correct."""

    def test_requirements_exists(self):
        """requirements.txt must be a regular file in the project root."""
        assert REQUIREMENTS_PATH.is_file(), (
            f"requirements.txt not found at {REQUIREMENTS_PATH}"
        )

    def test_requirements_contains_fastapi(self):
        """Must list fastapi."""
        content = REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        assert "fastapi" in content, "requirements.txt missing: fastapi"

    def test_requirements_contains_uvicorn(self):
        """Must list uvicorn (ASGI server)."""
        content = REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        assert "uvicorn" in content, "requirements.txt missing: uvicorn"

    def test_requirements_contains_websockets(self):
        """Must list websockets."""
        content = REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        assert "websockets" in content, "requirements.txt missing: websockets"

    def test_requirements_parseable(self):
        """Every non-comment, non-empty line must be a valid package spec."""
        lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Valid pip requirement: package_name or package_name==version or package_name>=version
            assert " " not in stripped, (
                f"Line {i}: requirement contains space — may be invalid: '{stripped}'"
            )


class TestTask9PackageImports:
    """Verify all required packages are actually importable."""

    REQUIRED = ["fastapi", "uvicorn", "websockets"]

    @pytest.mark.parametrize("package", REQUIRED)
    def test_package_importable(self, package: str):
        """Each required package must import without error."""
        try:
            importlib.import_module(package)
        except ImportError as e:
            pytest.fail(f"Cannot import '{package}': {e}\nRun: pip install -r requirements.txt")


class TestTask9FullIntegration:
    """Run the WAV harness end-to-end as a final integration check."""

    OUTPUT_WAV = str(PROJECT_ROOT / "tests" / "test_results" / "test_task9_integration.wav")

    def test_full_pipeline_wav_roundtrip(self, test_server: str, test_wav_path: str):
        """Complete integration: server up → WAV streamed → output validated."""
        ws_url = test_server.replace("http://", "ws://") + "/ws/voice"

        env = os.environ.copy()
        env["TEST_WS_URL"] = ws_url
        env["TEST_IN_WAV"] = test_wav_path
        env["TEST_OUT_WAV"] = self.OUTPUT_WAV

        transport = PROJECT_ROOT / "test_transport.py"
        result = subprocess.run(
            [sys.executable, str(transport)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        assert result.returncode == 0, (
            f"Integration test failed (exit {result.returncode})\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
        assert os.path.isfile(self.OUTPUT_WAV), "Integration output WAV not created"
        assert os.path.getsize(self.OUTPUT_WAV) > 1000, "Integration output WAV too small"

    def test_health_and_ws_both_available(self, test_server: str, echo_ws_url: str):
        """HTTP and WS must both be reachable at the same time."""
        import httpx
        import websockets

        # HTTP
        async def check_http():
            async with httpx.AsyncClient() as c:
                r = await c.get(test_server + "/")
                assert r.status_code == 200

        # WS
        async def check_ws():
            async with websockets.connect(echo_ws_url) as ws:
                await ws.send(b"integration-test")
                reply = await ws.recv()
                assert reply == b"integration-test"

        import asyncio
        asyncio.run(check_http())
        asyncio.run(check_ws())
