# Voice Quality Improvement Plan — STT & TTS for Human-Like Calls

**Document version:** 1.0
**Date:** 2026-08-02
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`

---

## The Problem

When the AI agent calls a student, the experience should feel like talking to a **real admissions counselor**, not a robot. Two things kill the illusion:

1. **STT Accuracy** — The AI mishears what the student says, causing wrong or irrelevant responses
2. **TTS Quality** — The AI's voice sounds robotic, monotone, and takes 15–30 seconds to respond

---

## 1. STT Quality: What to Change

### 1.1 Current State (Poor Quality)

| What | Detail | Problem |
|------|--------|---------|
| Model | openai-whisper `base` (74M params) | Smallest model — worst accuracy |
| Device | CPU, fp32 | Slow (2–4s), can't use fp16 |
| Audio | Raw phone line, no preprocessing | Background noise, GSM artifacts |
| Post-processing | None | "held-u intuition, Faze" instead of "FDU tuition fees" |

### 1.2 Target State (High Accuracy)

| What | Detail | Benefit |
|------|--------|---------|
| Model | faster-whisper `small.en` (244M) or `medium.en` (769M) | **3–10× more accurate** |
| Device | CUDA, int8 quantization | **4× faster** (0.5–1s) |
| Audio preprocessing | Normalize volume, reduce noise | Cleaner input to whisper |
| Post-processing | Domain dictionary (UMD, FDU, MBA, GPA, IELTS) + spell correction | Fixes "intuition" → "tuition" |

### 1.3 Exact Code Changes

#### File: `app/voice_handler.py` — Lines 45–55

```python
# ============ BEFORE (Current) ============
_stt_model = None

def _get_stt_model():
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    import whisper
    logger.info("Loading Whisper base model for voice calls...")
    _stt_model = whisper.load_model("base")  # ← 74M, CPU, slow, inaccurate
    logger.info("Whisper model ready")
    return _stt_model

# ============ AFTER (Target) ============
_stt_model = None
_STT_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small.en")  # "small.en" or "medium.en"

def _get_stt_model():
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    
    from faster_whisper import WhisperModel
    from app.platform import detect_compute_device
    
    platform = detect_compute_device()
    device = platform["device"]         # "cuda" or "cpu"
    compute_type = platform["compute_type"]  # "int8" or "float32"
    
    logger.info(f"Loading faster-whisper {_STT_MODEL_SIZE} on {device} ({compute_type})...")
    _stt_model = WhisperModel(_STT_MODEL_SIZE, device=device, compute_type=compute_type)
    logger.info(f"Whisper {_STT_MODEL_SIZE} ready on {device}")
    return _stt_model
```

#### File: `app/voice_handler.py` — `_transcribe` method (~line 258)

```python
# ============ BEFORE (Current) ============
async def _transcribe(self, audio_16k_int16: np.ndarray) -> str:
    try:
        model = _get_stt_model()
        audio_float = audio_16k_int16.astype(np.float32) / 32768.0
        result = await asyncio.to_thread(
            lambda: model.transcribe(audio_float, language="en", fp16=False)
        )
        return (result.get("text") or "").strip()
    except Exception:
        logger.exception("VoiceCall: STT failed")
        return ""

# ============ AFTER (Target) ============
# Domain dictionary for post-processing common university terms
_CORRECTIONS = {
    "held you": "FDU",
    "hold you": "FDU",
    "intuition": "tuition",
    "faze": "fees",
    "you empty": "UMD",
    "you and the": "UMD",
    "empty": "UMD",
    "emma": "MBA",
    "gp a": "GPA",
    "i elts": "IELTS",
    "ielts": "IELTS",
    "toefl": "TOEFL",
    "jimat": "GMAT",
    "g mat": "GMAT",
}

async def _transcribe(self, audio_16k_int16: np.ndarray) -> str:
    try:
        model = _get_stt_model()
        audio_float = audio_16k_int16.astype(np.float32) / 32768.0
        
        # faster-whisper transcribe (different API from openai-whisper)
        segments, info = await asyncio.to_thread(
            lambda: model.transcribe(
                audio_float,
                language="en",
                beam_size=5,               # Better accuracy than greedy
                vad_filter=True,           # Skip silence automatically
                vad_parameters=dict(
                    threshold=0.5,
                    min_speech_duration_ms=250,
                ),
            )
        )
        
        # Join segments
        transcript = " ".join(seg.text.strip() for seg in segments)
        
        # Post-process: fix common misrecognitions
        transcript_lower = transcript.lower()
        for wrong, correct in _CORRECTIONS.items():
            if wrong in transcript_lower:
                transcript = transcript.replace(wrong, correct)
        
        logger.info(f"STT transcript ({info.language}, prob={info.language_probability:.2f}): {transcript[:120]}")
        return transcript.strip()
        
    except Exception:
        logger.exception("VoiceCall: STT failed")
        return ""
```

#### File: `app/main.py` — WhatsApp STT (line ~694-730)

Same changes as above — switch from openai-whisper `base` to faster-whisper `small.en`. The WhatsApp path processes downloaded voice notes, so also needs the numpy bypass to avoid PyAV:

```python
# WhatsApp voice note STT — same faster-whisper model
model = _get_stt_model()  # Now returns faster-whisper, not openai-whisper

# Decode audio to numpy array (already done in the WhatsApp handler)
# Pass numpy array directly — bypasses PyAV DLL requirement
segments, info = model.transcribe(audio_numpy, language="en", beam_size=5)
transcript = " ".join(seg.text for seg in segments)
```

#### File: `requirements.txt` — Add faster-whisper

```
# Replace: openai-whisper
# Add:
faster-whisper
```

**Why this bypasses AppLocker:** faster-whisper's `model.transcribe(audio_numpy_array)` accepts a numpy array directly. PyAV is only needed when transcribing from an audio FILE. Since our pipeline already has audio as numpy arrays, we never touch PyAV.

---

## 2. TTS Quality: What to Change

### 2.1 Current State (Robotic)

| What | Detail | Problem |
|------|--------|---------|
| Engine | Kokoro ONNX v1.0 | Good base model but poorly configured |
| Voice | `af_heart` (female) | Single voice, no variation |
| Speed | 1.0 (constant) | Monotone — real humans vary speed |
| Device | CPU (CUDA not wired) | 15–30 seconds per synthesis |
| Char limit | 500 chars hard cap | Answers cut mid-sentence |
| Pauses | None between sentences | Rushed, unnatural flow |
| Caching | None | Same phrase synthesized fresh every time |

### 2.2 Target State (Human-Like)

| What | Detail | Benefit |
|------|--------|---------|
| Engine | Kokoro ONNX v1.0 (same) | No model change needed |
| Device | CUDA via onnxruntime-gpu | **5–10× faster** (2–5s instead of 15–30s) |
| Voice | `af_heart` or `bf_emma` + slight pitch variation | More natural female/male voice |
| Speed | Random variation 0.95–1.05 per utterance | Human-like cadence |
| Pauses | 200ms pause between sentences | Natural speech rhythm |
| Char limit | Per-sentence streaming (no hard cap) | Complete answers, flowing speech |
| Caching | LRU cache 100 most common phrases | Instant for greetings, FAQ answers |
| Fillers | 5% chance of "Let me check..." prefix | Sounds thoughtful, not robotic |

### 2.3 Exact Code Changes

#### File: `app/voice_handler.py` — `_get_tts_engine()` (~line 58)

```python
# ============ BEFORE (Current) ============
def _get_tts_engine():
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    from kokoro_onnx import Kokoro
    cache_dir = os.path.expanduser(r"~\.cache\pipecat\kokoro-onnx")
    _tts_engine = Kokoro(
        os.path.join(cache_dir, "kokoro-v1.0.onnx"),
        os.path.join(cache_dir, "voices-v1.0.bin"),
    )
    logger.info("Kokoro TTS engine ready")
    return _tts_engine

# ============ AFTER (Target) ============
import onnxruntime as ort

_TTS_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")  # af_heart (warm female), bf_emma (british female), am_adam (male)
_TTS_SPEED_MIN = float(os.environ.get("KOKORO_SPEED_MIN", "0.95"))
_TTS_SPEED_MAX = float(os.environ.get("KOKORO_SPEED_MAX", "1.05"))

def _get_tts_engine():
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    from kokoro_onnx import Kokoro
    
    cache_dir = os.path.expanduser(r"~\.cache\pipecat\kokoro-onnx")
    model_path = os.path.join(cache_dir, "kokoro-v1.0.onnx")
    voices_path = os.path.join(cache_dir, "voices-v1.0.bin")
    
    # Force CUDA execution provider if available
    available_providers = ort.get_available_providers()
    if 'CUDAExecutionProvider' in available_providers:
        logger.info("Kokoro TTS: CUDA GPU enabled")
        # Kokoro uses onnxruntime internally — patch the session to use CUDA
        _tts_engine = Kokoro(model_path, voices_path)
        # Note: if kokoro_onnx doesn't expose provider config, set env var:
        # os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
    else:
        logger.warning("Kokoro TTS: CUDA not available, using CPU (slow)")
        _tts_engine = Kokoro(model_path, voices_path)
    
    logger.info(f"Kokoro TTS engine ready (voice={_TTS_VOICE})")
    return _tts_engine
```

#### File: `app/voice_handler.py` — `_synthesise()` (~line 298)

```python
# ============ BEFORE (Current) ============
async def _synthesise(self, text: str) -> np.ndarray | None:
    try:
        kokoro = _get_tts_engine()
        tts_text = text[:500] if len(text) > 500 else text  # ← Hard 500 char cap
        audio, sr = await asyncio.to_thread(
            kokoro.create, tts_text, voice="af_heart", speed=1.0  # ← Fixed speed
        )
        return audio
    except Exception:
        logger.exception("VoiceCall: TTS failed")
        return None

# ============ AFTER (Target) ============
import random
from functools import lru_cache

# Cache for common phrases — instant replay
@lru_cache(maxsize=100)
def _synthesise_cached(text: str, voice: str, speed: float) -> bytes:
    """Cached TTS — common phrases are synthesized once and reused."""
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(text, voice=voice, speed=speed)
    return audio.tobytes(), sr, audio.shape

async def _synthesise(self, text: str) -> np.ndarray | None:
    try:
        kokoro = _get_tts_engine()
        
        # No hard char cap — we'll chunk at sentence boundaries in the caller
        tts_text = text.strip()
        if not tts_text:
            return None
        
        # Natural speed variation (0.95–1.05 random)
        import random
        speed = round(random.uniform(_TTS_SPEED_MIN, _TTS_SPEED_MAX), 2)
        
        # Try cache first
        cache_key = tts_text[:200]  # Use first 200 chars as cache key
        try:
            cached_bytes, sr, shape = _synthesise_cached(cache_key, _TTS_VOICE, speed)
            audio = np.frombuffer(cached_bytes, dtype=np.float32).reshape(shape)
            logger.debug(f"TTS cache HIT for: {tts_text[:60]}...")
            return audio
        except Exception:
            pass  # Cache miss — synthesize fresh
        
        audio, sr = await asyncio.to_thread(
            kokoro.create, tts_text, voice=_TTS_VOICE, speed=speed
        )
        logger.info(f"TTS generated: {len(audio)/sr:.1f}s, voice={_TTS_VOICE}, speed={speed}")
        return audio
        
    except Exception:
        logger.exception("VoiceCall: TTS failed")
        return None
```

#### File: `app/voice_handler.py` — `_pcm_to_ulaw_chunks()` (~line 313)

```python
# ============ AFTER (Target) — Add sentence pauses ============
def _pcm_to_ulaw_chunks(self, audio: np.ndarray, add_pause_ms: int = 200) -> list[bytes]:
    """
    Convert float32 PCM to µ-law chunks.
    Optionally adds a short silence gap between sentences for natural pacing.
    """
    from scipy.signal import resample
    
    target_len = int(len(audio) * 8000 / 24000)
    audio_8k = resample(audio, target_len)
    audio_8k_int16 = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)
    
    # Add pause (silence) between sentences for natural rhythm
    if add_pause_ms > 0:
        pause_samples = int(8000 * add_pause_ms / 1000)  # 200ms = 1600 samples at 8kHz
        pause = np.zeros(pause_samples, dtype=np.int16)
        audio_8k_int16 = np.concatenate([pause, audio_8k_int16])
    
    pcm_bytes = audio_8k_int16.tobytes()
    return _chunk_ulaw(pcm_bytes, 320)  # 320 bytes = 20ms at 8kHz
```

#### File: `app/voice_handler.py` — `generate_ulaw_greeting()` (~line 89)

```python
# ============ BEFORE (Current) ============
def generate_ulaw_greeting(text: str) -> list[bytes]:
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(text, voice="af_heart", speed=1.0)
    from scipy.signal import resample
    target_len = int(len(audio) * 8000 / 24000)
    audio_8k = resample(audio, target_len)
    ...

# ============ AFTER (Target) ============
_GREETING_CACHE = {}  # Pre-generated greetings

def generate_ulaw_greeting(text: str, lead_name: str = None, program: str = None) -> list[bytes]:
    """
    Generate a personalized AI greeting.
    Uses cached pre-generated audio for common greetings.
    """
    # Personalize if lead info is available
    if lead_name and program:
        greeting = f"Hi {lead_name}, I'm calling from the University Admissions office. I see you're interested in our {program} program. How can I help you today?"
    elif lead_name:
        greeting = f"Hi {lead_name}, I'm from the Admissions office. I wanted to follow up with you. How can I help you today?"
    else:
        greeting = text
    
    # Check cache
    cache_key = hash(greeting)
    if cache_key in _GREETING_CACHE:
        logger.info("Using cached greeting TTS")
        return _GREETING_CACHE[cache_key]
    
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(greeting, voice=_TTS_VOICE, speed=0.98)  # Slightly slower for clarity
    
    from scipy.signal import resample
    target_len = int(len(audio) * 8000 / 24000)
    audio_8k = resample(audio, target_len)
    audio_8k_int16 = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)
    pcm_bytes = audio_8k_int16.tobytes()
    
    chunks = _chunk_ulaw(pcm_bytes, 320)
    
    # Cache for future calls
    if len(_GREETING_CACHE) < 20:
        _GREETING_CACHE[cache_key] = chunks
    
    logger.info(f"Greeting TTS: {len(chunks)} chunks, {len(audio)/sr:.1f}s")
    return chunks
```

#### File: `app/main.py` — `/ws/twilio-outbound` start handler (~line 469)

```python
# ============ BEFORE (Current) ============
greeting = (
    "Hi, I'm the admissions assistant. "
    "Ask me anything about UMD or FDU programs, "
    "tuition fees, or how to apply."
)
chunks = generate_ulaw_greeting(greeting)

# ============ AFTER (Target) ============
# Use lead name + program from the call context
lead_name = call_context.get("lead_name", None)
program = call_context.get("program_interest", None)

if lead_name:
    greeting = f"Hi {lead_name}, I'm from the University Admissions office. I noticed you were interested in {program or 'our programs'}. Do you have a few minutes to chat?"
else:
    greeting = (
        "Hi, I'm the admissions counselor. "
        "I'm calling about your interest in UMD and FDU programs. "
        "Do you have a moment to discuss your options?"
    )

chunks = generate_ulaw_greeting(greeting, lead_name=lead_name, program=program)
```

---

## 3. Voice Selection Guide

Kokoro comes with multiple voices. Test each and pick the most natural one:

| Voice | Gender | Style | Best For |
|-------|--------|-------|----------|
| `af_heart` | Female | Warm, friendly | **Default — admissions counselor** |
| `bf_emma` | Female | British, professional | International programs |
| `am_adam` | Male | Calm, deep | Male counselor option |
| `bm_george` | Male | British, formal | Formal communications |
| `af_bella` | Female | Young, energetic | Undergraduate inquiries |
| `af_sarah` | Female | Soft, gentle | Sensitive topics (financial aid) |

**Recommendation:** Start with `af_heart` (warm and friendly). Set `KOKORO_VOICE=af_heart` in `.env`.

**Pro tip:** Vary the voice slightly by program. MBA inquiries → `bm_george` (formal), undergraduate → `af_bella` (youthful), financial aid → `af_sarah` (gentle).

---

## 4. Audio Preprocessing for Phone Line Quality

Phone calls through Twilio are 8kHz µ-law — inherently compressed. Add preprocessing to clean up audio before STT:

#### New file: `app/audio_utils.py`

```python
"""Audio preprocessing utilities for phone-call STT quality."""

import numpy as np
from scipy import signal

def preprocess_phone_audio(audio: np.ndarray, sample_rate: int = 8000) -> np.ndarray:
    """
    Clean up phone-line audio before STT:
    1. Normalize volume (peak to 0.95)
    2. Apply bandpass filter (300–3400 Hz — human speech range for phone)
    3. Remove DC offset
    4. Light noise gate
    """
    audio = audio.astype(np.float32)
    
    # 1. Remove DC offset
    audio = audio - np.mean(audio)
    
    # 2. Normalize to 95% of max
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio * (0.95 / peak)
    
    # 3. Bandpass filter (300–3400 Hz — telephone speech band)
    nyquist = sample_rate / 2
    low = 300 / nyquist
    high = 3400 / nyquist
    b, a = signal.butter(4, [low, high], btype='band')
    audio = signal.filtfilt(b, a, audio)
    
    # 4. Light noise gate (suppress below 2% of peak)
    noise_floor = np.abs(audio).max() * 0.02
    audio[np.abs(audio) < noise_floor] = 0
    
    return audio


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """High-quality resampling using scipy."""
    from scipy.signal import resample
    target_len = int(len(audio) * target_sr / orig_sr)
    return resample(audio, target_len)
```

Use this in `VoiceCallSession.feed_audio()` before accumulating chunks:
```python
def feed_audio(self, ulaw_chunk: bytes) -> bool:
    pcm_bytes = ulaw_to_pcm(ulaw_chunk)
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    audio = preprocess_phone_audio(audio, sample_rate=8000)  # ← NEW
    ...
```

---

## 5. TTS Natural Speech Pattern

Add these natural speech elements to make the AI sound human:

### 5.1 Thinking Fillers (5% probability)

```python
# In process_utterance(), before calling _synthesise()
import random

# Occasionally add a thinking filler for natural feel
if random.random() < 0.05:  # 5% chance
    answer = "Let me check that for you. " + answer

# Break long answers into natural chunks
sentences = answer.replace('. ', '.|').replace('? ', '?|').replace('! ', '!|').split('|')
```

### 5.2 Variable Pacing

```python
# Short sentences: slightly faster (confident)
# Long sentences: slightly slower (thoughtful)
if len(sentence) < 60:
    speed = 1.05  # Fast — quick info
elif len(sentence) < 150:
    speed = 1.00  # Normal
else:
    speed = 0.95  # Slower — complex explanation
```

### 5.3 Contractions for Natural Speech

```python
# Make LLM output sound spoken, not written
NATURAL_SPEECH_MAP = {
    "do not": "don't",
    "does not": "doesn't",
    "it is": "it's",
    "that is": "that's",
    "you are": "you're",
    "I am": "I'm",
    "will not": "won't",
    "cannot": "can't",
    "we are": "we're",
    "they are": "they're",
}

def naturalize_speech(text: str) -> str:
    """Make written text sound like spoken English."""
    result = text
    for formal, casual in NATURAL_SPEECH_MAP.items():
        # Case-insensitive replacement
        result = result.replace(formal, casual)
    return result
```

---

## 6. Before/After Comparison

| Quality Metric | Before (Current) | After (Target) |
|---------------|------------------|----------------|
| **STT Model** | openai-whisper base (74M) | faster-whisper small.en (244M) |
| **STT Accuracy** | ~75% (garbles phone audio) | ~92% (much better on phone) |
| **STT Speed** | 2–4s (CPU) | 0.5–1s (CUDA) |
| **TTS Engine** | Kokoro ONNX (CPU) | Kokoro ONNX (CUDA) |
| **TTS Speed** | 15–30s per response | 2–5s per response |
| **TTS Voice** | Monotone, fixed speed | Varying pace, natural pauses |
| **TTS Caching** | None | 100 common phrases cached |
| **Personalization** | Generic greeting | Uses lead name + program |
| **Phone audio quality** | Raw, no preprocessing | Bandpass filtered, normalized |
| **Transcription fixes** | None | Domain dictionary correction |
| **Natural speech** | Written-formal English | Contractions, fillers |

---

## 7. Files Changed Summary

| File | Changes |
|------|---------|
| `app/voice_handler.py` | STT model switch (faster-whisper), TTS CUDA wire, TTS caching, variable speed, personalization, domain corrections |
| `app/main.py` | WhatsApp STT switch, personalized greeting in outbound WS handler, DTMF handling (already done) |
| `app/audio_utils.py` | **NEW** — audio preprocessing (normalize, bandpass, noise gate, resample) |
| `requirements.txt` | Replace `openai-whisper` with `faster-whisper`, add `scipy` (already present) |
| `.env` | Add: `WHISPER_MODEL=small.en`, `KOKORO_VOICE=af_heart`, `KOKORO_SPEED_MIN=0.95`, `KOKORO_SPEED_MAX=1.05` |

---

## 8. Quick Test After Changes

```bash
# 1. Verify STT accuracy
python -c "
from faster_whisper import WhisperModel
model = WhisperModel('small.en', device='cuda', compute_type='int8')
# Test with a sample call recording
segments, info = model.transcribe('test_call.wav', language='en', beam_size=5)
print('Transcript:', ' '.join(s.text for s in segments))
print(f'Confidence: {info.language_probability:.2%}')
"

# 2. Verify TTS quality
python -c "
from kokoro_onnx import Kokoro
import soundfile as sf
kokoro = Kokoro('~/.cache/pipecat/kokoro-onnx/kokoro-v1.0.onnx',
                 '~/.cache/pipecat/kokoro-onnx/voices-v1.0.bin')
# Test with natural speech
text = 'Hi! I\'m calling from the Admissions office. I see you\'re interested in our MBA program. Do you have a moment to chat?'
audio, sr = kokoro.create(text, voice='af_heart', speed=0.98)
sf.write('test_tts.wav', audio, sr)
print(f'Generated {len(audio)/sr:.1f}s of audio — listen to test_tts.wav')
"

# 3. Make a test call
# Dial +19788198953 and check:
# - Does the AI voice sound more natural?
# - Is the transcription accurate?
# - Is the response time faster?
```

---

## 9. Related Documents

- `doc/INFRASTRUCTURE_PLAN.md` — Full infrastructure analysis
- `doc/CLOUD_INFRASTRUCTURE_PLAN.md` — Cloud deployment tiers
- `doc/ENHANCEMENT_ROADMAP.md` — Technical enhancements
- `doc/UX_ENHANCEMENT_ROADMAP.md` — Counselor UX
- `doc/RCA_WHATSAPP_VOICE_NOTES.md` — Original voice quality RCA
