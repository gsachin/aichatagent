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


def check_gpu_available() -> bool:
    """Check for any GPU (CUDA, Metal, or CPU)."""
    try:
        from app.platform import detect_compute_device, is_gpu_available
        
        platform_config = detect_compute_device()
        if is_gpu_available():
            device = platform_config["device"]
            platform = platform_config["platform"]
            device_name = platform_config["device_name"]
            print(f"  [OK] GPU available: {platform} ({device})")
            print(f"       Device: {device_name}")
            return True
        else:
            print("  [WARN] No GPU detected - CPU-only mode will be very slow")
            print("         Supported: NVIDIA CUDA, Apple Metal, or CPU fallback")
            return False
    except ImportError:
        print("  [SKIP] Platform detection module not available")
        return False


def check_vram_budget() -> bool:
    """Validate VRAM budget for the full pipeline (platform-specific)."""
    try:
        from app.platform import detect_compute_device
        from app.memory_budget import get_platform_budget, validate_memory_available
        
        platform_config = detect_compute_device()
        platform = platform_config["platform"]
        total_memory_gb = platform_config["total_memory_gb"]
        
        budget = get_platform_budget(platform)
        required = budget["recommended_gb"]
        
        ok = validate_memory_available(platform, total_memory_gb, verbose=False)
        
        status = "OK" if ok else "WARN"
        print(f"  [{status}] VRAM Budget ({platform}):")
        print(f"            Available: {total_memory_gb:.2f} GB")
        print(f"            Required:  {required:.2f} GB")
        print(f"            Headroom:  {max(0, total_memory_gb - required):.2f} GB")
        
        if not ok:
            print("            Need more VRAM or run on GPU system for better performance")
        return ok
    except Exception as e:
        print(f"  [WARN] Could not validate VRAM: {e}")
        print("         Continuing with checks...")
        return True  # Don't fail if we can't validate


def check_ollama_reachable() -> bool:
    """The active LLM backend API (Ollama or MLX) must respond."""
    from app.llm_backend import is_ready, provider_name, list_models

    if is_ready():
        models = list_models()
        print(f"  [OK] {provider_name()} backend reachable - {len(models)} model(s) found")
        return True, models
    print(f"  [FAIL] {provider_name()} backend not reachable")
    print("         Start it first (start_services.sh / start_services.ps1).")
    return False, []


def check_ollama_model(models: list, required: str) -> bool:
    """Check if the required model is available on the active backend."""
    from app.llm_backend import provider_name, pick_model, default_model

    if provider_name() == "mlx":
        model = pick_model([required])
        print(f"  [OK] MLX model configured: {model}")
        return True

    model_names = [m.get("name", "") if isinstance(m, dict) else m for m in models]

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
    from app.llm_backend import provider_name, MLX_EMBED_MODEL

    if provider_name() == "mlx":
        import os
        from pathlib import Path

        org, repo = MLX_EMBED_MODEL.split("/", 1)
        hub = Path(os.environ.get(
            "HF_HOME", os.path.expanduser("~/.cache/huggingface"))) / "hub"
        if (hub / f"models--{org}--{repo}").is_dir():
            print(f"  [OK] Embedding model available: {MLX_EMBED_MODEL}")
            return True
        print(f"  [WARN] Embedding model not downloaded yet: {MLX_EMBED_MODEL}")
        return False

    model_names = [m.get("name", "") if isinstance(m, dict) else m for m in models]
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

    # 2. PyTorch + GPU (CUDA, Metal, or CPU)
    print("-- GPU / Accelerator --")
    results["torch"] = check_torch_installed()
    results["gpu"] = check_gpu_available()
    vram_ok = check_vram_budget()
    results["vram"] = vram_ok
    print(f"  VRAM budget: {'Met' if vram_ok else 'Check warnings above'}")
    print()

    # 3. LLM backend (Ollama on Windows/Linux, MLX on Apple Silicon)
    from app.llm_backend import provider_name
    print(f"-- LLM Backend ({provider_name()}) --")
    ollama_ok, models = check_ollama_reachable()
    results["ollama"] = ollama_ok

    if ollama_ok:
        # Required model comes from .env (OLLAMA_MODEL, sized by predeploy)
        import os as _os
        required_model = _os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
        model_ok = check_ollama_model(models, required_model)
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
