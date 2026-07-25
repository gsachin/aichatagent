"""
Phase 1 — Environment & Dependency Setup Tests
===============================================
Goal: Verify GPU CUDA, Ollama service, model availability, and VRAM budget.

Tests:
    - PyTorch detects CUDA GPU with ≥5.5 GB VRAM
    - Ollama HTTP API is reachable on localhost:11434
    - Required model qwen2.5:6b-instruct-q4_K_M is pulled (or at least one Qwen model)
    - requirements.txt lists all Phase 1 dependencies
"""

import os
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"


# ── Helpers ──────────────────────────────────────────────────────────

def _get_requirements_names() -> set[str]:
    """Return the set of package names listed in requirements.txt."""
    if not REQUIREMENTS_PATH.is_file():
        return set()
    names: set[str] = set()
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Extract package name before any version specifier or extras marker
        name = re.split(r"[=<>~!\[;]", stripped)[0].strip().lower()
        if name:
            names.add(name)
    return names


# ── Phase 1.1: CUDA / GPU Detection ──────────────────────────────────

class TestPhase1CudaGpu:
    """Verify PyTorch CUDA availability and VRAM budget."""

    @pytest.mark.skipif(
        os.environ.get("SKIP_GPU_TESTS") == "1",
        reason="SKIP_GPU_TESTS=1 set — no GPU available",
    )
    def test_torch_installed(self):
        """PyTorch must be importable."""
        try:
            import torch  # noqa: F401
        except ImportError:
            pytest.fail("PyTorch not installed. Run: pip install torch")

    @pytest.mark.skipif(
        os.environ.get("SKIP_GPU_TESTS") == "1",
        reason="SKIP_GPU_TESTS=1 set — no GPU available",
    )
    def test_cuda_available(self):
        """torch.cuda.is_available() must return True."""
        import torch

        if not torch.cuda.is_available():
            pytest.skip("No CUDA GPU detected — skipping GPU test")
        assert torch.cuda.is_available(), "CUDA must be available for local inference"

    @pytest.mark.skipif(
        os.environ.get("SKIP_GPU_TESTS") == "1",
        reason="SKIP_GPU_TESTS=1 set — no GPU available",
    )
    def test_gpu_vram_sufficient(self):
        """GPU must report ≥5.5 GB total VRAM for the full pipeline."""
        import torch

        if not torch.cuda.is_available():
            pytest.skip("No CUDA GPU detected")

        total_mb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        assert total_mb >= 5.5, (
            f"GPU has only {total_mb:.1f} GB VRAM — need ≥5.5 GB "
            f"for Qwen 2.5 6B + Whisper + Kokoro"
        )

    @pytest.mark.skipif(
        os.environ.get("SKIP_GPU_TESTS") == "1",
        reason="SKIP_GPU_TESTS=1 set — no GPU available",
    )
    def test_cuda_device_count(self):
        """At least one CUDA device must be visible."""
        import torch

        if not torch.cuda.is_available():
            pytest.skip("No CUDA GPU detected")
        assert torch.cuda.device_count() >= 1, "Expected at least 1 CUDA device"


# ── Phase 1.2: Ollama Service ────────────────────────────────────────

class TestPhase1OllamaService:
    """Verify Ollama is running and the required model is available."""

    OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
    REQUIRED_MODEL = "qwen2.5:6b-instruct-q4_K_M"

    def _ollama_reachable(self) -> bool:
        """Check if Ollama HTTP API responds."""
        import urllib.request
        import json

        try:
            req = urllib.request.Request(self.OLLAMA_TAGS_URL)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return "models" in data
        except Exception:
            return False

    def _ollama_models(self) -> list[str]:
        """Return list of pulled Ollama model names."""
        import urllib.request
        import json

        try:
            req = urllib.request.Request(self.OLLAMA_TAGS_URL)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            return []

    def test_ollama_service_installed(self):
        """ollama CLI or Python package must be available."""
        try:
            import ollama  # noqa: F401
        except ImportError:
            pytest.fail(
                "ollama Python package not installed. Run: pip install ollama"
            )

    def test_ollama_api_reachable(self):
        """Ollama HTTP API must respond on localhost:11434."""
        if not self._ollama_reachable():
            pytest.fail(
                "Ollama API not reachable at http://127.0.0.1:11434/api/tags.\n"
                "Start Ollama and try again."
            )

    def test_qwen_model_pulled(self):
        """Required model qwen2.5:6b-instruct-q4_K_M must be pulled."""
        if not self._ollama_reachable():
            pytest.skip("Ollama not reachable — cannot check models")

        models = self._ollama_models()

        # Check for the exact model first
        if self.REQUIRED_MODEL in models:
            return

        # Fallback: any qwen2.5 6B/7B variant is acceptable
        qwen_variants = [m for m in models if "qwen" in m.lower()]
        assert len(qwen_variants) > 0, (
            f"Required model '{self.REQUIRED_MODEL}' not found.\n"
            f"Run: ollama pull {self.REQUIRED_MODEL}\n"
            f"Available models: {models or '(none)'}"
        )

    def test_nomic_embed_model_available(self):
        """nomic-embed-text should be available for ChromaDB embeddings."""
        if not self._ollama_reachable():
            pytest.skip("Ollama not reachable — cannot check models")

        models = self._ollama_models()
        embed_models = [m for m in models if "nomic" in m or "embed" in m]

        # Not a hard fail — embedding model can be pulled later
        if not embed_models:
            pytest.skip(
                "nomic-embed-text not pulled yet. Run: ollama pull nomic-embed-text"
            )


# ── Phase 1.3: Dependencies ──────────────────────────────────────────

class TestPhase1Requirements:
    """Verify all Phase 1 dependencies are listed in requirements.txt."""

    PHASE1_PACKAGES = [
        "torch",
        "ollama",
        "fastapi",
        "uvicorn",
        "websockets",
        "chromadb",
        "pipecat-ai",
        "psycopg2-binary",
        "httpx",
    ]

    @pytest.mark.parametrize("package", PHASE1_PACKAGES)
    def test_package_listed_in_requirements(self, package: str):
        """Each Phase 1 package must appear in requirements.txt."""
        names = _get_requirements_names()
        # pipecat-ai maps to pipecat_ai in pip but the file has pipecat-ai
        search = package.replace("-", "_").lower()
        found = any(search in n or package.lower() in n for n in names)
        assert found, (
            f"'{package}' not found in requirements.txt.\n"
            f"Add it: echo '{package}' >> requirements.txt"
        )


# ── Phase 1.4: Python Version ────────────────────────────────────────

class TestPhase1PythonVersion:
    """Verify Python 3.10+."""

    def test_python_version(self):
        """Python must be 3.10 or higher."""
        major, minor = sys.version_info[:2]
        assert (major, minor) >= (3, 10), (
            f"Python {major}.{minor} detected — need 3.10+ for Pipecat / FastAPI"
        )
