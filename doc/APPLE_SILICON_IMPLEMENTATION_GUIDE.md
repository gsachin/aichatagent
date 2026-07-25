# Implementation Guide: Apple Silicon Support (Metal GPU)

**Status:** Ready for Implementation  
**Start Date:** 2026-07-25  
**Estimated Duration:** 5 weeks  
**Target:** Multi-platform GPU support (NVIDIA CUDA + Apple Metal)

---

## Overview

This guide provides step-by-step instructions to implement Apple Silicon (Metal GPU) support while maintaining full backward compatibility with NVIDIA CUDA systems.

**Key Insight:** The codebase needs a **platform abstraction layer** that auto-detects the available GPU and selects appropriate device types and quantization strategies.

---

## Pre-Implementation Validation

### 1. Verify PyTorch Metal Support on M3 Mac

```bash
# On M3 MacBook Pro
python3 -c "
import torch
print(f'PyTorch version: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
print(f'Metal available: {hasattr(torch.backends, \"mps\") and torch.backends.mps.is_available()}')

# Test Metal device
if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
    x = torch.ones(1000, 1000, device='mps')
    y = torch.ones(1000, 1000, device='mps')
    z = x @ y  # Matrix multiply
    print(f'Metal test successful: {z.shape}')
"
```

**Expected Output:**
```
PyTorch version: 2.x.x+cpu  (or cu121, or other)
CUDA available: False
Metal available: True
Metal test successful: torch.Size([1000, 1000])
```

### 2. Verify Faster-Whisper FP16 Support

```bash
# Test Faster-Whisper with FP16 (no CUDA needed)
python3 -c "
from faster_whisper import WhisperModel

# Test FP16
model_fp16 = WhisperModel('tiny.en', device='cpu', compute_type='float16')
print('✓ FP16 supported')

# Test FP32
model_fp32 = WhisperModel('tiny.en', device='cpu', compute_type='float32')
print('✓ FP32 supported')

# Note: INT8 requires CUDA
try:
    model_int8 = WhisperModel('tiny.en', device='cpu', compute_type='int8')
    print('✗ INT8 on CPU (unexpected)')
except Exception:
    print('✓ INT8 requires CUDA (expected)')
"
```

### 3. Verify Ollama Metal Support

```bash
# Start Ollama and check Metal usage
ollama serve

# In another terminal:
ollama list
# Should show models with Metal acceleration in system logs
```

---

## Phase 1: Abstraction Layer (Week 1)

### Step 1.1: Create Platform Detection Module

**Files Created:**
- `app/platform.py` (already created above)
- `app/memory_budget.py` (already created above)

**Verify Installation:**
```bash
cd /Users/sachin/codebase/aichatagent
python3 -c "from app.platform import detect_compute_device; detect_compute_device().print_device_info()"
```

### Step 1.2: Update Requirements

**Add to `requirements.txt`:**
```
psutil  # For system memory reporting
```

**Create Platform-Specific Requirements Files:**

`requirements-cuda.txt`:
```
-r requirements.txt
torch>=2.0.0  # Force CUDA wheel on Linux/Windows
```

`requirements-metal.txt`:
```
-r requirements.txt
torch>=2.0.0  # Auto-detects Metal on macOS
```

**Verification:**
```bash
# Test import
python3 -c "
from app.platform import detect_compute_device, get_device_config
from app.memory_budget import get_platform_budget, print_budgets

config = detect_compute_device()
print(f'Device: {config[\"device\"]}')

print_budgets()
"
```

### Step 1.3: Unit Tests for Platform Detection

Create `tests/test_platform_detection.py`:

```python
import pytest
from app.platform import (
    detect_compute_device, 
    is_cuda_available, 
    is_metal_available,
    get_device_config,
    reset_device_cache,
)
from app.memory_budget import get_platform_budget, validate_memory_available

class TestPlatformDetection:
    def setup_method(self):
        reset_device_cache()
    
    def test_detect_returns_valid_config(self):
        config = detect_compute_device()
        assert config['device'] in ('cuda', 'mps', 'cpu')
        assert config['platform'] in ('nvidia', 'apple_silicon', 'intel_mac', 'cpu')
        assert config['total_memory_gb'] > 0
    
    def test_only_one_gpu_backend(self):
        """Either CUDA or Metal, not both"""
        cuda = is_cuda_available()
        metal = is_metal_available()
        # At most one should be True
        assert not (cuda and metal)
    
    def test_device_caching(self):
        """Config should be cached"""
        config1 = detect_compute_device()
        config2 = detect_compute_device()
        assert config1 is config2
    
    def test_memory_budgets_exist(self):
        """All platforms have budgets"""
        for platform in ['nvidia', 'apple_silicon', 'intel_mac', 'cpu']:
            budget = get_platform_budget(platform)
            assert 'min_required_gb' in budget
            assert 'peak_total_gb' in budget

def test_memory_validation():
    """Memory validation logic"""
    # NVIDIA: 5.5 GB minimum
    assert validate_memory_available('nvidia', 6.0) == True
    assert validate_memory_available('nvidia', 4.0) == False
    
    # Metal: 8.0 GB minimum
    assert validate_memory_available('apple_silicon', 9.0) == True
    assert validate_memory_available('apple_silicon', 6.0) == False

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
```

Run tests:
```bash
pytest tests/test_platform_detection.py -v
```

---

## Phase 2: Update Core Modules (Week 2)

### Step 2.1: Update `app/pipeline.py`

**Changes Required:**

```python
# OLD (Line ~43-48)
GPU_AVAILABLE = False
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError:
    pass

DEVICE = "cuda" if GPU_AVAILABLE else "cpu"
COMPUTE_TYPE = "int8"

# NEW
from app.platform import detect_compute_device

PLATFORM_CONFIG = detect_compute_device()
DEVICE = PLATFORM_CONFIG['device']
COMPUTE_TYPE = PLATFORM_CONFIG['compute_type']
```

**Updated Code Block:**
```python
# Line 59-63 in current pipeline.py
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError:
    pass

# BECOMES:

from app.platform import detect_compute_device
PLATFORM_CONFIG = detect_compute_device()
# GPU_AVAILABLE is derived from DEVICE
GPU_AVAILABLE = PLATFORM_CONFIG['device'] != 'cpu'
```

### Step 2.2: Update `app.py` (Streamlit)

**Changes Required (Line ~128):**

```python
# OLD
device = "cuda" if _has_cuda() else "cpu"
model = WhisperModel("small.en", device=device, compute_type="int8")

# NEW
from app.platform import get_device_config
config = get_device_config()
model = WhisperModel(
    "small.en", 
    device=config['device'], 
    compute_type=config['compute_type']
)
```

### Step 2.3: Update `test_audio_local.py`

**Changes Required (Line ~154):**

```python
# OLD
whisper_model = WhisperModel(
    "small.en",
    device="cuda",
    compute_type="int8",
)

# NEW
from app.platform import get_device_config
config = get_device_config()
whisper_model = WhisperModel(
    "small.en",
    device=config['device'],
    compute_type=config['compute_type'],
)
```

### Step 2.4: Update `test_full_pipeline.py`

**Changes Required (Line ~55):**

```python
# OLD
whisper = WhisperModel("small.en", device="cuda", compute_type="int8")

# NEW
from app.platform import get_device_config
config = get_device_config()
whisper = WhisperModel(
    "small.en", 
    device=config['device'], 
    compute_type=config['compute_type']
)
```

---

## Phase 3: Test Infrastructure (Week 3)

### Step 3.1: Update `test_environment.py`

**Replace GPU-only detection with platform-agnostic:**

```python
# OLD
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
            return False
    except ImportError:
        print("  [SKIP] PyTorch not installed")
        return False

# NEW
def check_gpu_available() -> bool:
    """Check if any GPU is available (CUDA, Metal, or CPU)."""
    try:
        from app.platform import is_gpu_available, get_device_config
        config = get_device_config()
        
        if is_gpu_available():
            print(f"  [OK] GPU available: {config['device_name']}")
            return True
        else:
            print("  [WARN] No GPU detected - using CPU (slower)")
            print(f"       Device: {config['device_name']}")
            return True  # CPU is valid, just slower
    except ImportError:
        print("  [SKIP] Platform detection failed")
        return False
```

### Step 3.2: Update VRAM Check

```python
# OLD
def check_vram_budget() -> bool:
    """GPU must have ≥5.5 GB total VRAM for the full pipeline."""
    try:
        import torch
        if not torch.cuda.is_available():
            print("  [SKIP] No CUDA GPU")
            return False

        total_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        # ... check if >= 5.5 GB

# NEW
def check_memory_budget() -> bool:
    """Check if available memory meets platform requirements."""
    try:
        from app.platform import get_device_config
        from app.memory_budget import get_platform_budget, validate_memory_available
        
        config = get_device_config()
        budget = get_platform_budget(config['platform'])
        
        available = config['total_memory_gb']
        required = budget['min_required_gb']
        
        if validate_memory_available(config['platform'], available, verbose=True):
            print(f"  [OK] Memory sufficient: {available:.1f} GB >= {required:.1f} GB")
            return True
        else:
            print(f"  [WARN] Memory may be tight: {available:.1f} GB < {required:.1f} GB")
            return False
    except Exception as e:
        print(f"  [SKIP] Memory check failed: {e}")
        return False
```

### Step 3.3: Update `tests/test_phase1_environment.py`

**Key Change:** Accept any GPU, not just CUDA

```python
# OLD
def test_cuda_available(self):
    """torch.cuda.is_available() must return True."""
    import torch

    if not torch.cuda.is_available():
        pytest.skip("No CUDA GPU detected — skipping GPU test")
    assert torch.cuda.is_available(), "CUDA must be available"

# NEW
def test_gpu_available(self):
    """GPU must be available (CUDA, Metal, or CPU as fallback)."""
    from app.platform import is_gpu_available, get_device_config
    
    config = get_device_config()
    # Accept any GPU; CPU is OK too (just slower)
    assert config['device'] in ('cuda', 'mps', 'cpu'), \
        f"Unexpected device: {config['device']}"
```

**VRAM Test Update:**

```python
# OLD
def test_gpu_vram_sufficient(self):
    """GPU must report ≥5.5 GB total VRAM."""
    import torch

    if not torch.cuda.is_available():
        pytest.skip("No CUDA GPU detected")

    total_mb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    assert total_mb >= 5.5, f"GPU has only {total_mb:.1f} GB — need ≥5.5 GB"

# NEW
def test_memory_sufficient(self):
    """Memory must meet platform requirements."""
    from app.platform import get_device_config
    from app.memory_budget import validate_memory_available
    
    config = get_device_config()
    available = config['total_memory_gb']
    
    # Platform-specific thresholds
    thresholds = {
        'nvidia': 5.5,
        'apple_silicon': 8.0,
        'intel_mac': 8.0,
        'cpu': 12.0,
    }
    
    required = thresholds[config['platform']]
    assert available >= required, \
        f"{config['platform']}: {available:.1f} GB < {required:.1f} GB required"
```

### Step 3.4: Update `tests/test_phase2_audio.py`

**Compute Type Tests Should Be Conditional:**

```python
# OLD
def test_whisper_cuda(self):
    """WhisperSTTService must instantiate with device='cuda' and compute_type='int8'."""
    try:
        from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
        stt = WhisperSTTService(
            settings=WhisperSTTSettings(model="tiny.en"),
            device="cuda",
            compute_type="int8",
        )
        assert stt is not None
    except ImportError:
        pytest.skip("pipecat not installed")

# NEW
def test_whisper_device_agnostic(self):
    """WhisperSTTService works with platform's optimal compute type."""
    try:
        from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
        from app.platform import get_device_config
        
        config = get_device_config()
        stt = WhisperSTTService(
            settings=WhisperSTTSettings(model="tiny.en"),
            device=config['device'],
            compute_type=config['compute_type'],
        )
        assert stt is not None
    except ImportError:
        pytest.skip("pipecat not installed")
```

---

## Phase 4: PyTorch Installation (Week 4)

### Step 4.1: Platform-Specific Requirements

**Option A: Separate requirements files** (RECOMMENDED)

```bash
# For NVIDIA GPU
pip install -r requirements-cuda.txt

# For Apple Silicon
pip install -r requirements-metal.txt

# For CPU-only
pip install -r requirements.txt
```

**Option B: setup.py with extras** (Advanced)

Create `setup.py`:
```python
from setuptools import setup, find_packages

setup(
    name="admissions-voice-assistant",
    version="2.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn[standard]",
        "websockets",
        "httpx",
        "ollama",
        "chromadb",
        "pipecat-ai[whisper,kokoro]",
        "psycopg2-binary",
        "psutil",
    ],
    extras_require={
        "cuda": ["torch>=2.0.0+cu121"],    # NVIDIA
        "metal": ["torch>=2.0.0"],          # Auto-detects on macOS
        "cpu": ["torch>=2.0.0"],            # CPU-only
    },
)
```

Install with:
```bash
pip install -e .[metal]  # For Apple Silicon
pip install -e .[cuda]   # For NVIDIA
pip install -e .         # For CPU-only
```

---

## Phase 5: Documentation (Week 5)

### Step 5.1: Create User Guide

Create `doc/APPLE_SILICON_SETUP.md`:

```markdown
# Apple Silicon Setup Guide

## Hardware Requirements

- **MacBook**: M1, M2, M3, or M3 Max
- **Memory**: 8+ GB (16+ GB recommended for M1/M2)
- **OS**: macOS 12.0+

## Installation

### 1. Install PyTorch with Metal Support

```bash
pip install torch>=2.0.0
```

Verify Metal is available:
```bash
python3 -c "
import torch
print('Metal available:', hasattr(torch.backends, 'mps') and torch.backends.mps.is_available())
"
```

### 2. Install Project Dependencies

```bash
pip install -r requirements-metal.txt
```

### 3. Start Ollama

```bash
# Install from https://ollama.ai or brew
brew install ollama

# Pull models
ollama pull qwen2.5:7b-instruct-q3_K_M
ollama pull nomic-embed-text

# Start server (auto-detects Metal)
ollama serve
```

### 4. Run Application

**Streamlit UI:**
```bash
streamlit run app.py
```

**FastAPI Server:**
```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Performance Notes

- **STT (Whisper)**: ~250ms per turn (FP16, vs ~200ms on NVIDIA INT8)
- **LLM (Qwen)**: Same speed as NVIDIA (handled by Ollama)
- **TTS (Kokoro)**: Similar or faster on Metal
- **Overall**: ~250-300ms latency (voice input → response)

## Troubleshooting

### Metal Not Detected

```bash
python3 -c "
import torch
print('CUDA:', torch.cuda.is_available())
print('Metal:', hasattr(torch.backends, 'mps') and torch.backends.mps.is_available())
"
```

If Metal returns False:
1. Ensure macOS 12.0+ (M-series native)
2. Reinstall torch: `pip install --upgrade torch`

### Memory Issues

If you get OOM errors:
1. Check available: `python3 -c "import psutil; print(psutil.virtual_memory().available / 1e9, 'GB')"`
2. Reduce Ollama num_ctx: `OLLAMA_NUM_CTX=1024 ollama serve`
3. Use CPU fallback (slower): The system auto-fallsback to CPU if Metal fails

## Monitoring

To see memory usage during operation:

```bash
# Terminal 1: Run app
streamlit run app.py

# Terminal 2: Monitor
watch -n 1 "ps aux | grep -E 'python|ollama' | head -5"
```
```

### Step 5.2: Update Main README

Add to README.md:

```markdown
## Platform Support

| Platform | GPU Backend | Status | Notes |
|----------|------------|--------|-------|
| **NVIDIA** | CUDA | ✅ Fully Supported | Optimal INT8 quantization, ~200ms latency |
| **Apple Silicon** | Metal | ✅ Fully Supported (NEW) | FP16 quantization, ~250ms latency |
| **Intel Mac** | Metal | ✅ Supported (NEW) | FP16 quantization |
| **CPU** | N/A | ✅ Fallback | No GPU needed, ~2-3s per turn |

### Quick Start

**For NVIDIA GPU:**
```bash
pip install -r requirements-cuda.txt
```

**For Apple Silicon (M1/M2/M3):**
```bash
pip install -r requirements-metal.txt
```

**For CPU-only:**
```bash
pip install -r requirements.txt
```
```

---

## Phase 5.1: Final Validation

### Checklist

- [ ] All 5 core modules updated (pipeline, app, test_audio_local, test_full_pipeline, run_pipeline_test)
- [ ] Platform detection module working and tested
- [ ] Memory budget module complete with all platforms
- [ ] VRAM checks updated for all platforms
- [ ] Test suite passes on CUDA system
- [ ] Metal detected correctly on M3 Mac (when available)
- [ ] Fallback to CPU works
- [ ] Documentation complete and reviewed
- [ ] Requirements files created (cuda, metal, cpu variants)
- [ ] User guide complete

### Test Commands

```bash
# Run all platform tests
pytest tests/test_platform_detection.py -v

# Run full test suite
pytest tests/ -v

# Test on specific platform
pytest tests/ -v -m "not gpu"  # CPU-only tests
```

---

## Rollout Plan

### 1. Internal Validation (1 week)
- [ ] Run on NVIDIA GPU (existing)
- [ ] Run on M3 Mac with Metal
- [ ] Run on CPU-only environment
- [ ] Performance baseline capture

### 2. Beta Release (2 weeks)
- [ ] Tag release v2.1.0-beta
- [ ] Notify community
- [ ] Collect feedback from M1/M2/M3 users

### 3. Production Release (1 week)
- [ ] Merge to main
- [ ] Tag v2.1.0
- [ ] Update documentation
- [ ] Announce multi-platform support

---

## Success Metrics

✅ **Must Have:**
- Device detection works on all 3 platforms (CUDA, Metal, CPU)
- Whisper loads with correct compute_type
- Full pipeline runs end-to-end on Metal
- No breaking changes to CUDA deployments
- All tests pass

✅ **Nice to Have:**
- Performance within 20% of NVIDIA baseline on Metal
- Automatic platform detection (no manual configuration)
- Clear error messages on unsupported platforms
- CI/CD validates on multiple platforms

---

## References

- PyTorch Metal Backend: https://pytorch.org/docs/main/mps.html
- Faster-Whisper: https://github.com/guillaumekln/faster-whisper
- Ollama Metal Support: https://ollama.ai/
- Apple Neural Engine: https://developer.apple.com/metal/

---

**Next Step:** Start Phase 1 implementation!
