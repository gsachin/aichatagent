# 🔬 Audio Format Analysis: WAV vs MP3 for TTS Output

**Date:** 2026-07-26
**Context:** Streamlit TTS audio playback for university admissions chatbot
**Current format:** WAV (PCM int16, 24 kHz, mono)
**TTS engine:** Kokoro ONNX (CPU-only)

---

## 1. Benchmarked Data (Real Measurements)

| Answer length | Chars | Audio duration | TTS time | WAV size | Base64 size | RT factor |
|---|---|---|---|---|---|---|
| Short | 168 | 12.6s | 12.7s | 592 KB | 789 KB | 0.99x |
| Medium | 371 | 27.1s | 18.6s | 1,269 KB | 1,692 KB | 1.45x |
| Long | 652 | 39.9s | 28.3s | 1,872 KB | 2,496 KB | 1.41x |

**WAV formula:** `size = duration_seconds × 48,000 bytes/sec` (24 kHz × 2 bytes/sample × 1 channel)
**Base64 overhead:** +33% (3 bytes encode 4 base64 characters)

---

## 2. Theoretical MP3 Comparison

| Bitrate | Compression vs WAV | 35s answer size | Base64 size | Quality |
|---|---|---|---|---|
| 64 kbps (speech-grade) | **6.0x smaller** | 273 KB | 365 KB | Good for speech, slight artifacts |
| 96 kbps (good) | **4.0x smaller** | 410 KB | 547 KB | Transparent for speech |
| 128 kbps (high) | **3.0x smaller** | 547 KB | 729 KB | Near-transparent for all audio |

---

## 3. Multi-Factor Analysis

### Factor A: User-Perceived Latency

```
Timeline for a text question (current WAV approach):
┌──────────────┬──────────────────────┬───────────────────────┐
│  RAG + LLM   │   TTS Generation     │   Page Render + Play  │
│   ~3-8 sec   │   ~13-28 sec (CPU)   │   ~0.1 sec (WAV)     │
│              │                      │   ~0.2 sec (MP3+enc)  │
└──────────────┴──────────────────────┴───────────────────────┘
Total: 16-36 seconds
```

**Key insight:** TTS generation dominates at 13-28 seconds. The format choice affects only the last ~0.1 second of page render time. **The format has negligible impact on total user-perceived latency.**

### Factor B: Localhost vs Cloudflare Tunnel

| Scenario | WAV payload | MP3 payload | Impact |
|---|---|---|---|
| **Localhost** (launch.bat) | 1-2.5 MB | 0.3-0.7 MB | **None** — memory transfer, <1ms |
| **Cloudflare Tunnel** (launch_tunnel.bat) | 1-2.5 MB | 0.3-0.7 MB | **Noticeable** — internet transfer, 1-5s difference on slow connections |

For the primary use case (localhost), format size is irrelevant. For the tunnel use case, MP3 would give a better experience for remote users on slow connections.

### Factor C: Dependencies & Complexity

| | WAV | MP3 |
|---|---|---|
| **Python deps** | `wave` (built-in, zero install) | `pydub` + `ffmpeg` binary, or `lameenc` |
| **System deps** | None | `ffmpeg` CLI (~80 MB download) |
| **Current availability** | ✅ Working | ❌ Nothing installed |
| **Setup burden** | Zero | Must install system package |
| **Cross-platform** | 100% (pure Python) | Requires per-OS ffmpeg install |
| **Docker compatibility** | Zero extra deps | Must add `RUN apt install ffmpeg` |

### Factor D: Encoding Overhead

| | WAV | MP3 |
|---|---|---|
| **Encoding time (35s audio)** | ~0.01s (memcpy) | ~0.5-2s (ffmpeg encode) |
| **CPU usage** | Negligible | Moderate (but parallelizable) |
| **Added to total pipeline** | 0% | ~1-5% |

MP3 encoding adds ~1-2 seconds, but this is trivial compared to the 13-28 second TTS generation time on CPU.

### Factor E: Audio Quality

- **WAV:** Lossless. Exact 24 kHz int16 PCM from Kokoro.
- **MP3 64 kbps:** Speech-grade. Imperceptible quality loss for voice. Used by podcasts and audiobooks.
- **MP3 128 kbps:** Transparent. Indistinguishable from WAV for speech.

For a university admissions chatbot, even 64 kbps MP3 is more than adequate. The Kokoro `af_heart` voice at 24 kHz is already band-limited — MP3 compression artifacts at 64 kbps are below the noise floor for speech.

### Factor F: Browser & Streamlit Compatibility

- Both WAV and MP3 have **universal browser support**.
- Streamlit's `st.audio()` accepts both formats via data URI (`data:audio/wav;base64,...` or `data:audio/mpeg;base64,...`).
- Both autoplay equally (subject to browser autoplay policies).
- **No difference** in Streamlit behavior.

---

## 4. Summary Matrix

| Factor | WAV | MP3 (64 kbps) | Winner |
|---|---|---|---|
| **Total user latency impact** | Baseline | +1-2s encoding | WAV (negligible margin) |
| **File size (35s answer)** | 1,641 KB | 273 KB | **MP3 (6x)** |
| **Dependencies** | Zero | ffmpeg (~80 MB) | **WAV** |
| **Setup simplicity** | Already works | Must install tools | **WAV** |
| **Audio quality** | Lossless | Good for speech | WAV (negligible margin) |
| **Cloudflare Tunnel UX** | Slower page load | Faster page load | **MP3** |
| **Maintainability** | Pure Python | External binary dependency | **WAV** |

---

## 5. Recommendation

### Verdict: **Stay with WAV — the format is NOT the bottleneck.**

**Evidence-based reasoning:**

1. **TTS generation time (13-28 seconds on CPU) is the dominant bottleneck**, not format encoding or payload transfer. Switching to MP3 saves ~0.1-2 seconds in a 16-36 second pipeline — a ~1-5% improvement that's imperceptible to users.

2. **MP3 requires ffmpeg installation** which adds 80 MB of system dependencies and per-platform setup complexity. This contradicts the project's "just double-click launch.bat" philosophy.

3. **For localhost usage (primary case), WAV payload size is irrelevant** — the 1-2.5 MB base64 blob transfers over the loopback interface in under 1 millisecond.

4. **The code fix already applied** (truncating TTS text to 500 chars) limits WAV to ~1,200 KB, which is a manageable size even for the Cloudflare tunnel.

### If MP3 is desired in the future:

The cleanest approach:
```bash
# One-time setup
winget install ffmpeg   # or: choco install ffmpeg
```

```python
# In app.py - encode via subprocess
import subprocess, tempfile

def wav_to_mp3(wav_bytes: bytes, bitrate: str = "64k") -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        wav_path = f.name
    mp3_path = wav_path.replace(".wav", ".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-b:a", bitrate, mp3_path],
        capture_output=True, check=True
    )
    with open(mp3_path, "rb") as f:
        mp3_bytes = f.read()
    os.unlink(wav_path); os.unlink(mp3_path)
    return mp3_bytes
```

### The right fix is to address the real bottleneck: TTS generation speed

Priority order for improving audio UX:

| Priority | Fix | Impact |
|---|---|---|
| **1** | Install `onnxruntime-gpu` for CUDA-accelerated Kokoro | 10-20x faster TTS |
| **2** | Truncate text to 500 chars (already applied) | Keeps TTS under ~20s |
| **3** | Cache TTS results with `@st.cache_data` | Instant replay for repeated answers |
| **4** | Switch to MP3 format | 6x smaller payload (optional) |

The format choice is a distant 4th priority — the real win is GPU-accelerated TTS.
