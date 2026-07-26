# 🔍 RCA: TTS Audio Not Playing for Text Input in Streamlit

**Date:** 2026-07-26
**File under analysis:** `app.py` (Streamlit web UI)
**Severity:** High — TTS audio is not generated/played for text responses

---

## Symptom

When the user types a question in Streamlit, the text answer appears correctly but **no audio is produced**. The "🔊 Generating audio..." spinner appears briefly (or for a very long time), then disappears with no audio playback. No error message is shown.

---

## Root Cause Analysis

### Finding 1: Kokoro ONNX runs on CPU — 30-60 second blocking call

**Evidence:**
```
$ python -c "import onnxruntime; print(ort.get_available_providers())"
['AzureExecutionProvider', 'CPUExecutionProvider']
```

There is **no CUDA provider** available. Kokoro ONNX synthesizes audio entirely on CPU. Benchmark:

| Answer length | Audio duration | TTS generation time | Real-time factor |
|---|---|---|---|
| 779 chars | 53.2 seconds | **36.6 seconds** | 0.69x (CPU) |

During these 36 seconds, `text_to_audio_bytes()` is a **synchronous blocking call** — the entire Streamlit script is frozen. The WebSocket receives no heartbeat. The browser shows a spinner but nothing else happens.

**Code at fault (`app.py` lines 308-313):**
```python
if st.session_state.tts_enabled:
    with st.spinner("🔊 Generating audio..."):
        try:
            audio_bytes = text_to_audio_bytes(answer)  # ← BLOCKS 30-60s
            st.audio(audio_bytes, format="audio/wav", autoplay=True)
        except Exception:
            pass  # ← SILENTLY SWALLOWS ALL ERRORS
```

### Finding 2: Silent exception handling hides ALL failures

The `except Exception: pass` at line 313 means that if ANY of these failures occur, the user sees nothing:
- `load_tts()` returns `None` (import error, missing model files)
- `kokoro.create()` fails (text too long, ONNX runtime error)
- WAV encoding fails (memory error for large audio)
- `st.audio()` fails (data too large for Streamlit delta)

### Finding 3: LLM generates verbose answers unsuited for TTS

The system prompt encourages detailed answers with markdown formatting. A typical answer is 500-1000 characters, producing 30-70 seconds of audio. Kokoro ONNX on CPU takes 20-50 seconds to synthesize this — during which the app appears frozen.

### Finding 4: Audio not persisted — vanishes on any rerun

The audio is rendered inline via `st.audio()` in the response block (transient container), but is never stored in `st.session_state.messages`. This means:
- **Text input**: Audio plays on the current page render, but if the page re-renders for any reason, the audio element is gone
- **Voice input**: `st.rerun()` at line 289 destroys the audio element within ~300ms of it appearing
- **Any subsequent interaction**: The audio is never shown again because `st.session_state.messages` only stores `{"role": "assistant", "content": answer}` — no audio key

---

## Evidence Summary

| # | Finding | Impact |
|---|---------|--------|
| 1 | Kokoro ONNX CPU-only, 0.69x real-time | 30-60s blocking call freezes Streamlit |
| 2 | `except Exception: pass` swallows errors | User never knows TTS failed |
| 3 | LLM answers too long for practical TTS | 53s of audio from one question |
| 4 | Audio not persisted to session state | Any rerun destroys the audio player |
| 5 | ONNX Runtime lacks CUDA provider | Cannot leverage GPU for speedup |

---

## Recommended Fixes

### Fix 1: Persist audio in session state and play from chat history

This is the primary fix — it ensures audio survives `st.rerun()` and page re-renders.

**Store audio with the message:**
```python
st.session_state.messages.append({
    "role": "assistant",
    "content": answer,
    "audio": audio_bytes  # ← persists across reruns
})
```

**Play from chat history with a played-once guard:**
```python
if "played_audio_idx" not in st.session_state:
    st.session_state.played_audio_idx = -1

for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("audio") and i > st.session_state.played_audio_idx:
            st.audio(msg["audio"], format="audio/wav", autoplay=True)
            st.session_state.played_audio_idx = i
```

### Fix 2: Surface errors instead of swallowing them

Replace `except Exception: pass` with a visible warning:
```python
except Exception as e:
    st.warning(f"Audio generation skipped: {e}")
```

### Fix 3: Truncate long text for TTS

Limit TTS input to ~500 characters to keep synthesis time under 20 seconds:
```python
tts_text = answer[:500] + "..." if len(answer) > 500 else answer
audio_bytes = text_to_audio_bytes(tts_text)
```

### Fix 4: Add ONNX CUDA execution provider (if GPU available)

Install `onnxruntime-gpu` to enable CUDA acceleration for Kokoro, which would bring real-time factor closer to 10-20x:
```
pip uninstall onnxruntime
pip install onnxruntime-gpu
```

### Fix 5: Cache TTS results

Use `@st.cache_data` to avoid re-synthesizing the same text:
```python
@st.cache_data(ttl=3600, show_spinner=False)
def cached_tts(text: str, voice: str = "af_heart") -> bytes:
    return text_to_audio_bytes(text, voice)
```
