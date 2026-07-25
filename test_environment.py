"""
Environment Verification Script
================================
Checks GPU CUDA availability, VRAM, and Ollama service readiness
for the University Admissions Voice AI Assistant.

Usage:
    python test_environment.py

Exit code 0 = all checks passed (ready for Phase 2).
"""

import json
import sys
import urllib.request


def check_python_version() -> bool:
    """Python 3.10+ required."""
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] Python {major}.{minor} (need 3.10+)")
    return ok


def check_torch_installed() -> bool:
    """PyTorch must be importable."""
    try:
        import torch
        print(f"  [OK] PyTorch {torch.__version__} installed")
        return True
    except ImportError:
        print("  [FAIL] PyTorch not installed. Run: pip install torch")
        return False


def check_cuda_available() -> bool:
    """Check CUDA GPU is visible to PyTorch."""
    try:
        import torch
        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            name = torch.cuda.get_device_name(0)
            print(f"  [OK] CUDA available: {count} device(s) - {name}")
            return True
        else:
            print("  [WARN] CUDA not available - GPU inference won't work")
            print("         CPU-only mode is possible but will be very slow")
            return False
    except ImportError:
        print("  [SKIP] PyTorch not installed")
        return False


def check_vram_budget() -> bool:
    """GPU must have ≥5.5 GB total VRAM for the full pipeline."""
    try:
        import torch
        if not torch.cuda.is_available():
            print("  [SKIP] No CUDA GPU")
            return False

        total_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        used_gb = torch.cuda.memory_allocated(0) / (1024**3)
        free_gb = total_gb - used_gb

        ok = total_gb >= 5.5
        status = "OK" if ok else "WARN"
        print(f"  [{status}] VRAM: {total_gb:.2f} GB total, {used_gb:.2f} GB used, {free_gb:.2f} GB free")
        if not ok:
            print("         Need ≥5.5 GB for Qwen 2.5 6B + Whisper + Kokoro")
        return ok
    except ImportError:
        print("  [SKIP] PyTorch not installed")
        return False


def check_ollama_reachable() -> bool:
    """Ollama API must respond on localhost:11434."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if "models" in data:
                print(f"  [OK] Ollama API reachable - {len(data['models'])} model(s) found")
                return True, data.get("models", [])
            else:
                print("  [FAIL] Ollama responded but response is malformed")
                return False, []
    except Exception as e:
        print(f"  [FAIL] Ollama not reachable: {e}")
        print("         Start Ollama and try again.")
        return False, []


def check_ollama_model(models: list, required: str) -> bool:
    """Check if the required model is pulled."""
    model_names = [m.get("name", "") for m in models]

    # Exact match
    if required in model_names:
        print(f"  [OK] Model '{required}' is pulled")
        return True

    # Check for base model variants
    base = required.split(":")[0]
    variants = [m for m in model_names if base in m]
    if variants:
        print(f"  [OK] Found Qwen variant(s): {variants}")
        return True

    print(f"  [FAIL] Required model '{required}' not found")
    print(f"         Available models: {model_names or '(none)'}")
    print(f"         Run: ollama pull {required}")
    return False


def check_embed_model(models: list) -> bool:
    """Check if an embedding model is available for ChromaDB."""
    model_names = [m.get("name", "") for m in models]
    embed_models = [m for m in model_names if "nomic" in m or "embed" in m]

    if embed_models:
        print(f"  [OK] Embedding model(s) available: {embed_models}")
        return True
    else:
        print("  [WARN] No embedding model found (nomic-embed-text)")
        print("         Run: ollama pull nomic-embed-text")
        return False


# ── Main ─────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("  University Admissions Voice Assistant")
    print("  Environment Verification")
    print("=" * 60)
    print()

    results: dict[str, bool] = {}

    # 1. Python version
    print("-- Python --")
    results["python"] = check_python_version()
    print()

    # 2. PyTorch + CUDA
    print("-- GPU / CUDA --")
    results["torch"] = check_torch_installed()
    results["cuda"] = check_cuda_available()
    vram_ok = check_vram_budget()
    results["vram"] = vram_ok
    print(f"  VRAM budget: {'Met' if vram_ok else 'Check warnings above'}")
    print()

    # 3. Ollama
    print("-- Ollama --")
    ollama_ok, models = check_ollama_reachable()
    results["ollama"] = ollama_ok

    if ollama_ok:
        model_ok = check_ollama_model(models, "qwen2.5:6b-instruct-q4_K_M")
        results["llm_model"] = model_ok
        embed_ok = check_embed_model(models)
        results["embed_model"] = embed_ok
    else:
        results["llm_model"] = False
        results["embed_model"] = False
    print()

    # 4. Summary
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    all_ok = all(results.values())

    for name, ok in results.items():
        print(f"  {'[PASS]' if ok else '[FAIL]'} {name}")

    print()
    if all_ok:
        print(f"  ALL CHECKS PASSED ({passed}/{total})")
        print("  Ready to proceed to Phase 2 (STT + TTS).")
        return 0
    else:
        failed_count = total - passed
        print(f"  {failed_count} CHECK(S) FAILED ({passed}/{total} passed)")
        print("  Fix the issues above before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
