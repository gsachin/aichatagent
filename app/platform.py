"""
Platform Detection & Device Abstraction Layer
==============================================

Provides a unified interface for detecting and configuring compute devices
across NVIDIA CUDA, Apple Metal, and CPU backends.

Usage:
    from app.platform import detect_compute_device, get_device_config
    
    config = detect_compute_device()
    print(f"Device: {config['device']}, Platform: {config['platform']}")
    
    # Use in model loading:
    device = config['device']           # 'cuda', 'mps', or 'cpu'
    compute_type = config['compute_type']  # 'int8', 'float16', or 'float32'
    dtype = config['torch_dtype']       # torch.float32, torch.float16, etc.
"""

import logging
import os
import platform as platform_module
import sys
from typing import Dict, Literal, Optional, TypedDict

try:
    import torch
except ImportError:
    torch = None

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("platform_detection")


class DeviceConfig(TypedDict):
    """Device configuration dictionary returned by detect_compute_device()."""
    device: Literal["cuda", "mps", "cpu"]
    platform: Literal["nvidia", "apple_silicon", "intel_mac", "cpu"]
    device_name: str
    total_memory_gb: float
    supports_int8: bool
    supports_fp16: bool
    compute_type: Literal["int8", "float16", "float32"]
    torch_dtype: Optional["torch.dtype"]


# Global cache (populated on first call)
_DEVICE_CONFIG: Optional[DeviceConfig] = None


def _get_system_memory_gb() -> float:
    """Get total system RAM in GB."""
    if psutil:
        return psutil.virtual_memory().total / (1024**3)
    
    # Fallback: estimate from common platforms
    if torch is not None:
        try:
            return torch.tensor([]).device.total_memory / (1024**3)
        except Exception:
            pass
    
    return 0.0  # Unknown


def detect_compute_device() -> DeviceConfig:
    """
    Detect the best available compute device and return platform-specific
    configuration.
    
    Priority order:
        1. NVIDIA CUDA (if available)
        2. Apple Metal (if available)
        3. CPU (fallback)
    
    Returns:
        DeviceConfig dictionary with device, platform, memory, and type info.
    
    Raises:
        RuntimeError: If PyTorch is not installed.
    """
    global _DEVICE_CONFIG
    
    if _DEVICE_CONFIG is not None:
        return _DEVICE_CONFIG
    
    if torch is None:
        raise RuntimeError(
            "PyTorch not installed. Run: pip install torch"
        )
    
    # ── 1. NVIDIA CUDA Detection ──────────────────────────────────
    if torch.cuda.is_available():
        try:
            device_count = torch.cuda.device_count()
            device_name = torch.cuda.get_device_name(0)
            device_props = torch.cuda.get_device_properties(0)
            total_memory = device_props.total_memory / (1024**3)
            
            _DEVICE_CONFIG = DeviceConfig(
                device="cuda",
                platform="nvidia",
                device_name=f"{device_name} ({device_count} GPU(s))",
                total_memory_gb=total_memory,
                supports_int8=True,
                supports_fp16=True,
                compute_type="int8",  # INT8 optimal for CUDA
                torch_dtype=torch.float32,
            )
            
            logger.info(
                f"✓ NVIDIA CUDA detected: {device_name} "
                f"({total_memory:.1f} GB VRAM)"
            )
            return _DEVICE_CONFIG
        
        except Exception as e:
            logger.warning(f"CUDA detection failed: {e}, trying Metal...")
    
    # ── 2. Apple Metal Detection ──────────────────────────────────
    # Check for Metal GPU (Apple Silicon or Intel Mac with Metal)
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        try:
            system_ram = _get_system_memory_gb()
            system_name = platform_module.processor()
            
            # Determine if Apple Silicon or Intel Mac
            if "Apple" in system_name or "arm64" in platform_module.machine():
                platform_type = "apple_silicon"
                platform_name = "Apple Silicon"
            else:
                platform_type = "intel_mac"
                platform_name = "Intel Mac with Metal"
            
            _DEVICE_CONFIG = DeviceConfig(
                device="mps",
                platform=platform_type,
                device_name=f"{platform_name} Metal GPU",
                total_memory_gb=system_ram,
                supports_int8=False,  # Metal doesn't support INT8
                supports_fp16=True,   # FP16 is well-supported
                compute_type="float16",  # FP16 for Metal (smaller than FP32)
                torch_dtype=torch.float16,
            )
            
            logger.info(
                f"✓ Apple Metal detected: {platform_name} "
                f"({system_ram:.1f} GB system RAM available)"
            )
            return _DEVICE_CONFIG
        
        except Exception as e:
            logger.warning(f"Metal detection failed: {e}, falling back to CPU...")
    
    # ── 3. CPU Fallback ───────────────────────────────────────────
    system_ram = _get_system_memory_gb()
    
    _DEVICE_CONFIG = DeviceConfig(
        device="cpu",
        platform="cpu",
        device_name="CPU",
        total_memory_gb=system_ram,
        supports_int8=False,
        supports_fp16=False,
        compute_type="float32",
        torch_dtype=torch.float32,
    )
    
    logger.warning(
        f"⚠ No GPU detected, using CPU (system RAM: {system_ram:.1f} GB). "
        f"Performance will be significantly slower."
    )
    return _DEVICE_CONFIG


def get_device_config() -> DeviceConfig:
    """
    Get the cached device configuration. Calls detect_compute_device()
    on first invocation.
    
    Returns:
        DeviceConfig dictionary (cached from detect_compute_device).
    """
    return detect_compute_device()


def reset_device_cache() -> None:
    """
    Clear the cached device configuration. Useful for testing.
    Next call to detect_compute_device() will re-detect.
    """
    global _DEVICE_CONFIG
    _DEVICE_CONFIG = None


def is_cuda_available() -> bool:
    """Return True if NVIDIA CUDA GPU is available."""
    config = detect_compute_device()
    return config["device"] == "cuda"


def is_metal_available() -> bool:
    """Return True if Apple Metal GPU is available."""
    config = detect_compute_device()
    return config["device"] == "mps"


def is_gpu_available() -> bool:
    """Return True if any GPU (CUDA or Metal) is available."""
    config = detect_compute_device()
    return config["device"] in ("cuda", "mps")


def supports_int8() -> bool:
    """Return True if INT8 quantization is supported."""
    return detect_compute_device()["supports_int8"]


def supports_fp16() -> bool:
    """Return True if FP16 quantization is supported."""
    return detect_compute_device()["supports_fp16"]


def get_device() -> str:
    """Get device string for PyTorch model.to(device)."""
    return detect_compute_device()["device"]


def get_compute_type() -> str:
    """Get compute_type for Whisper model initialization."""
    return detect_compute_device()["compute_type"]


def get_torch_dtype() -> Optional["torch.dtype"]:
    """Get PyTorch dtype for model quantization."""
    return detect_compute_device()["torch_dtype"]


def get_device_name() -> str:
    """Get human-readable device name."""
    return detect_compute_device()["device_name"]


def get_total_memory_gb() -> float:
    """Get total GPU/system memory in GB."""
    return detect_compute_device()["total_memory_gb"]


def print_device_info() -> None:
    """Print device configuration to stdout (for debugging)."""
    config = detect_compute_device()
    
    print("\n" + "="*60)
    print("  Device Configuration")
    print("="*60)
    print(f"  Device:          {config['device']}")
    print(f"  Platform:        {config['platform']}")
    print(f"  Device Name:     {config['device_name']}")
    print(f"  Total Memory:    {config['total_memory_gb']:.1f} GB")
    print(f"  INT8 Support:    {config['supports_int8']}")
    print(f"  FP16 Support:    {config['supports_fp16']}")
    print(f"  Compute Type:    {config['compute_type']}")
    print("="*60 + "\n")


# ── Module-level initialization logging ───────────────────────

if __name__ == "__main__":
    # Allow running as: python app/platform.py
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    detect_compute_device()
    print_device_info()
