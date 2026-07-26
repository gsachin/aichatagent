# 🔍 Root Cause Analysis: Voice Assistant Bugs

**Date:** 2026-07-26
**File under analysis:** `app.py` (Streamlit web UI)
**Severity:** Critical — app unusable for voice input

---

## Bug #1 (CRITICAL): Infinite Response Loop — Same Answer Repeats Endlessly

### Symptom

After recording a voice question, the app generates the same answer repeatedly in an infinite loop. The user had to close the browser tab to stop it. The chat history filled with duplicate Q&A pairs:

```
🎤 Tell me about FDU tuition fees. Please stop the button.
Sure! The full-time tuition for undergraduate students at FDU is...
🎤 Tell me about FDU tuition fees. Please stop the button.
Sure! The full-time tuition for undergraduate students at FDU is...
🎤 Tell me about FDU tuition fees. Please stop the button.
Sure! The full-time tuition for undergraduate students at FDU is...
... (repeats forever)
```

### Root Cause

**`st.audio_input` retains its recorded data across `st.rerun()` calls**, behaving identically to `st.file_uploader`. Once the user records audio, the widget holds that data persistently in Streamlit's frontend session state. On every script rerun, `st.audio_input()` returns the same `BytesIO` object with the full audio data — it never resets to `None` on its own.

### Evidence: Execution Trace

#### Run 1 (user records audio and clicks stop):
```
Line 190: audio_value = st.audio_input(...)
          → Returns BytesIO with recorded audio ✅

Line 192: if audio_value is not None:
          → Condition is True, enters block ✅

Line 198: audio_bytes = audio_value.read()
          → Reads full PCM audio data ✅

Lines 202-214: Transcription via Whisper
          → transcript = "Tell me about FDU tuition fees..." ✅

Line 234: Appends {"role": "user", "content": "🎤 Tell me about..."}
          → st.session_state.messages now has user message ✅

Lines 236-245: Runs RAG chain (Ollama + ChromaDB)
          → answer = "Sure! The full-time tuition..." ✅

Line 245: Appends {"role": "assistant", "content": answer}
          → st.session_state.messages now has assistant message ✅

Line 247: st.rerun()
          → ⚡ SCRIPT RESTARTS FROM TOP ⚡
```

#### Run 2 (after rerun — THE BUG):
```
Line 190: audio_value = st.audio_input(...)
          → Returns BytesIO WITH SAME AUDIO DATA 🔴
          → Widget state PERSISTS across reruns

Line 192: if audio_value is not None:
          → Condition is STILL True 🔴

Line 198: audio_bytes = audio_value.read()
          → Returns FULL AUDIO DATA again (fresh BytesIO copy) 🔴

Lines 202-214: Transcription via Whisper
          → Same transcript: "Tell me about FDU tuition fees..." 🔴

Line 234: Appends DUPLICATE user message 🔴

Lines 236-245: Same RAG response 🔴

Line 245: Appends DUPLICATE assistant message 🔴

Line 247: st.rerun()
          → ⚡ RUN 3, RUN 4, ... INFINITE LOOP ⚡
```

### Why the text input (line 250) does NOT have this problem

```python
if prompt := st.chat_input("Ask about admissions..."):
```

`st.chat_input` is designed differently — its value is consumed on read and **auto-clears** after the script run. The walrus operator (`:=`) assigns the value once, and on the next rerun it returns `None`. This is the correct behavior for a chat input widget.

`st.audio_input`, like `st.file_uploader`, is a **file-like widget** that retains its content until explicitly cleared or replaced by a new upload/recording.

### The 3-line chain reaction that causes the loop

```
st.audio_input persists data (by design)
    → audio_value is never None on rerun
        → transcript is never empty
            → st.rerun() is always called
                → INFINITE LOOP
```

---

## Bug #2 (MEDIUM): TTS Audio Output Never Produced

### Symptom

The assistant responds with text only. No audio file is generated or played back for the user to hear the answer spoken aloud. The sidebar advertises "🔊 Responses can be read aloud (TTS coming soon)" but no audio is ever produced.

### Root Cause

**The TTS service is loaded but never invoked.** The `load_tts_service()` function (lines 138-144) loads Kokoro TTS, but it is a `@st.cache_resource` function that is **never called anywhere in the main execution flow**. There is no code path that:
1. Calls `load_tts_service()` to get a TTS instance
2. Passes the answer text through TTS to generate audio
3. Renders the audio with `st.audio()`

### Evidence

**What exists (dead code):**
```python
# Lines 137-144: TTS service loader — defined but NEVER CALLED
@st.cache_resource(show_spinner=False)
def load_tts_service():
    """Load Kokoro TTS service for text-to-speech output."""
    try:
        from pipecat.services.kokoro.tts import KokoroTTSService
        return KokoroTTSService(voice_id="af_heart")
    except Exception:
        return None
```

**What the answer output actually does (lines 244, 263):**
```python
st.markdown(answer)   # <-- Text only. No TTS call. No st.audio().
```

**Grep result — `load_tts_service` is never called anywhere:**
```
$ grep -rn "load_tts_service" D:\university_project_demo\
app.py:138: def load_tts_service():
# No call sites found — function is defined but unused
```

### Sidebar makes a false promise

The sidebar at line 41 says:
```python
st.markdown("🔊 Responses can be read aloud (TTS coming soon).")
```

This is misleading — the TTS code was imported and a loader was written, but the integration was never completed. The "coming soon" note suggests this was intentionally deferred.

---

## Summary

| Bug | Severity | Root Cause | Location | Impact |
|-----|----------|------------|----------|--------|
| Infinite response loop | **CRITICAL** | `st.audio_input` persists data across `st.rerun()`, causing endless reprocessing | `app.py:190-247` | Voice input completely unusable |
| No TTS audio output | **MEDIUM** | `load_tts_service()` is dead code — never called, no audio playback integration | `app.py:138-144` + lines 244,263 | Voice responses are text-only |

---

## Recommended Fixes

### Fix for Bug #1: Add session-state guard with dynamic widget key

Replace the current voice input section (lines 189-247) with a guarded version that prevents reprocessing:

```python
# ── Voice input (audio_input) with loop guard ────────────────────
if "voice_key" not in st.session_state:
    st.session_state.voice_key = 0

audio_value = st.audio_input(
    "🎤 Click to ask by voice",
    key=f"voice_{st.session_state.voice_key}"
)

if audio_value is not None:
    # ... [transcription and RAG processing — same as current lines 193-245] ...

    st.session_state.voice_key += 1  # ← Destroy old widget, create fresh one
    st.rerun()
```

**How this works:** After processing, incrementing `voice_key` changes the widget's key (e.g., `voice_0` → `voice_1`). On rerun, Streamlit creates a brand-new `audio_input` widget with no recorded data, so it returns `None`. The loop is broken.

### Fix for Bug #2: Integrate TTS into the response flow

After generating the answer text, synthesize and play audio:

```python
# After line 244 (or 263 for text input):
tts = load_tts_service()
if tts:
    with st.spinner("🔊 Generating audio..."):
        try:
            audio_output = tts.synthesize(answer)
            st.audio(audio_output, format="audio/wav", autoplay=True)
        except Exception:
            pass  # Fall back to text-only silently
```

**Caveat:** This depends on `KokoroTTSService.synthesize()` returning audio bytes. Verify the exact pipecat Kokoro TTS API before implementing — the method name and return type may differ.
