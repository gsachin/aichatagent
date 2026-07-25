"""
Platform-Specific Memory Budget Configuration
==============================================

Defines VRAM/RAM requirements and component budgets per platform.
Used by environment validation and memory checks.

Usage:
    from app.memory_budget import get_platform_budget, validate_memory_available
    
    budget = get_platform_budget('apple_silicon')
    if validate_memory_available('apple_silicon'):
        print("Sufficient memory for full pipeline")
"""

from typing import Dict, Literal

# Platform identifiers
PlatformType = Literal["nvidia", "apple_silicon", "intel_mac", "cpu"]

# Memory budgets in GB
PLATFORM_BUDGETS: Dict[PlatformType, Dict[str, float]] = {
    # ── NVIDIA CUDA: Optimized with INT8 quantization ────────────────
    "nvidia": {
        "description": "NVIDIA CUDA GPU (e.g., RTX 3060, RTX 4090)",
        "min_required_gb": 5.5,
        "recommended_gb": 6.0,
        "whisper_model_gb": 0.8,      # small.en, INT8
        "qwen_llm_gb": 4.0,            # Q4_K_M quantization (Ollama)
        "kokoro_tts_gb": 0.35,         # ONNX Runtime optimized
        "chromadb_gb": 0.05,           # Vector DB (CPU-based)
        "ollama_overhead_gb": 0.5,     # Server memory
        "peak_total_gb": 5.7,          # Sum of above
        "safe_threshold_percent": 95,  # Alert if >95% used
    },
    
    # ── Apple Silicon: FP16 Whisper (no INT8 support) ───────────────
    "apple_silicon": {
        "description": "Apple Silicon GPU (M1/M2/M3/M3Max)",
        "min_required_gb": 8.0,        # Larger due to FP16 Whisper
        "recommended_gb": 16.0,        # M3 Max has 36 GB, plenty
        "whisper_model_gb": 1.2,       # small.en, FP16 (vs 0.8 for INT8)
        "qwen_llm_gb": 4.0,            # Ollama Metal (native)
        "kokoro_tts_gb": 0.5,          # ONNX Runtime on Metal
        "chromadb_gb": 0.05,           # Vector DB (CPU-based)
        "ollama_overhead_gb": 0.5,     # Server memory
        "peak_total_gb": 6.25,         # Sum of above (1.2 + 4.0 + 0.5 + 0.05 + 0.5)
        "safe_threshold_percent": 90,  # More conservative for memory sharing
    },
    
    # ── Intel Mac with Metal: Similar to Apple Silicon ──────────────
    "intel_mac": {
        "description": "Intel Mac with Metal GPU support",
        "min_required_gb": 8.0,        # Similar to Apple Silicon
        "recommended_gb": 16.0,
        "whisper_model_gb": 1.2,       # small.en, FP16
        "qwen_llm_gb": 4.0,            # Ollama Metal
        "kokoro_tts_gb": 0.5,          # ONNX Runtime
        "chromadb_gb": 0.05,
        "ollama_overhead_gb": 0.5,
        "peak_total_gb": 6.25,
        "safe_threshold_percent": 90,
    },
    
    # ── CPU-only: Large memory requirement (slow inference) ────────
    "cpu": {
        "description": "CPU-only (no GPU)",
        "min_required_gb": 12.0,       # Much larger (FP32 all models)
        "recommended_gb": 32.0,        # Ideal for CPU inference
        "whisper_model_gb": 2.4,       # small.en, FP32 (slow)
        "qwen_llm_gb": 4.0,            # CPU load (very slow)
        "kokoro_tts_gb": 1.0,          # ONNX Runtime on CPU
        "chromadb_gb": 0.1,
        "ollama_overhead_gb": 1.0,
        "peak_total_gb": 8.5,          # Approximate
        "safe_threshold_percent": 80,  # Very conservative
    },
}

# Component-specific recommendations
COMPONENT_BUDGETS: Dict[str, Dict[PlatformType, float]] = {
    "whisper_small_en": {
        "nvidia": 0.8,           # INT8 quantization
        "apple_silicon": 1.2,    # FP16 (no INT8 support)
        "intel_mac": 1.2,        # FP16
        "cpu": 2.4,              # FP32
    },
    "qwen2_5_6b_q4": {
        "nvidia": 4.0,           # Via Ollama
        "apple_silicon": 4.0,    # Via Ollama Metal
        "intel_mac": 4.0,        # Via Ollama Metal
        "cpu": 4.0,              # Via Ollama CPU (very slow)
    },
    "kokoro_82m": {
        "nvidia": 0.35,          # ONNX Runtime
        "apple_silicon": 0.5,    # ONNX Runtime on Metal
        "intel_mac": 0.5,
        "cpu": 1.0,              # ONNX Runtime on CPU
    },
}

# VRAM check thresholds
VRAM_THRESHOLDS: Dict[PlatformType, Dict[str, float]] = {
    "nvidia": {
        "warning_gb": 5.5,      # Warn if below this
        "critical_gb": 4.0,     # Fail if below this
    },
    "apple_silicon": {
        "warning_gb": 8.0,
        "critical_gb": 6.0,
    },
    "intel_mac": {
        "warning_gb": 8.0,
        "critical_gb": 6.0,
    },
    "cpu": {
        "warning_gb": 12.0,
        "critical_gb": 8.0,
    },
}


def get_platform_budget(platform: PlatformType) -> Dict[str, float]:
    """
    Get the memory budget dictionary for a platform.
    
    Args:
        platform: One of 'nvidia', 'apple_silicon', 'intel_mac', 'cpu'
    
    Returns:
        Dictionary with min_required_gb, component budgets, etc.
    
    Raises:
        ValueError: If platform is not recognized.
    """
    if platform not in PLATFORM_BUDGETS:
        raise ValueError(
            f"Unknown platform: {platform}. "
            f"Valid: {', '.join(PLATFORM_BUDGETS.keys())}"
        )
    return PLATFORM_BUDGETS[platform]


def get_component_budget(component: str, platform: PlatformType) -> float:
    """
    Get the memory budget for a specific component on a platform.
    
    Args:
        component: One of 'whisper_small_en', 'qwen2_5_6b_q4', 'kokoro_82m'
        platform: Platform type
    
    Returns:
        Memory budget in GB
    
    Raises:
        ValueError: If component or platform is not recognized.
    """
    if component not in COMPONENT_BUDGETS:
        raise ValueError(
            f"Unknown component: {component}. "
            f"Valid: {', '.join(COMPONENT_BUDGETS.keys())}"
        )
    if platform not in COMPONENT_BUDGETS[component]:
        raise ValueError(
            f"No budget defined for {component} on {platform}"
        )
    return COMPONENT_BUDGETS[component][platform]


def validate_memory_available(
    platform: PlatformType,
    available_gb: float,
    verbose: bool = False
) -> bool:
    """
    Check if available memory meets minimum requirements for a platform.
    
    Args:
        platform: Platform type
        available_gb: Available memory in GB
        verbose: If True, print warnings/info
    
    Returns:
        True if available_gb >= minimum required
    """
    budget = get_platform_budget(platform)
    min_required = budget["min_required_gb"]
    
    if available_gb >= min_required:
        if verbose:
            print(f"✓ {platform}: {available_gb:.1f} GB >= {min_required} GB required")
        return True
    else:
        if verbose:
            print(
                f"✗ {platform}: {available_gb:.1f} GB < {min_required} GB required. "
                f"Full pipeline may not work."
            )
        return False


def print_budgets() -> None:
    """Print all platform budgets in a readable format."""
    print("\n" + "="*70)
    print("  Platform Memory Budgets (in GB)")
    print("="*70)
    
    for platform, budget in PLATFORM_BUDGETS.items():
        print(f"\n  {platform.upper()}")
        print(f"  {budget['description']}")
        print(f"  ─" * 35)
        print(f"    Whisper (STT):     {budget['whisper_model_gb']:.2f} GB")
        print(f"    Qwen LLM:          {budget['qwen_llm_gb']:.2f} GB")
        print(f"    Kokoro (TTS):      {budget['kokoro_tts_gb']:.2f} GB")
        print(f"    ChromaDB:          {budget['chromadb_gb']:.2f} GB")
        print(f"    Ollama overhead:   {budget['ollama_overhead_gb']:.2f} GB")
        print(f"    ─" * 35)
        print(f"    Peak Total:        {budget['peak_total_gb']:.2f} GB")
        print(f"    Min Required:      {budget['min_required_gb']:.2f} GB")
        print(f"    Recommended:       {budget['recommended_gb']:.2f} GB")
        print(f"    Safe Threshold:    {budget['safe_threshold_percent']}%")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    print_budgets()
