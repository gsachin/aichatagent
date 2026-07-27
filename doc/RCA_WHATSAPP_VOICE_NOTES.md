# 🔍 RCA: WhatsApp Voice Notes Not Returning Replies

**Date:** 2026-07-27
**Severity:** High — voice notes produce no reply on WhatsApp

---

## Symptom

- **Text messages on WhatsApp**: ✅ Reply received within 5-10 seconds
- **Voice notes on WhatsApp**: ❌ No reply at all — user sees nothing

---

## Evidence: Server Logs

The FastAPI server successfully processes voice notes and returns HTTP 200. Here is the exact log for the latest voice note:

```
INFO:voice_api:WhatsApp voice note from whatsapp:+917757057985 (audio/ogg)
INFO:voice_api:Downloading audio: https://api.twilio.com/2010-04-01/Accounts/...
INFO:voice_api:Downloaded 11768 bytes of audio
INFO:voice_api:Audio: 8.4s @ 16kHz
INFO:voice_api:Transcribed (36 chars): What is the tuition fees of the FDU?...
INFO:voice_api:WhatsApp from whatsapp:+917757057985: What is the tuition fees of the FDU?
INFO:rag_module:RAG query: model=qwen2.5:7b-instruct-q3_K_M, context_chars=1248
INFO:voice_api:WhatsApp voice reply: text (236 chars)
INFO:voice_api:WhatsApp response (236 chars): The full-time tuition fee...
INFO:     3.86.187.27:0 - "POST /twilio/whatsapp HTTP/1.1" 200 OK
```

**The server processes everything correctly and returns HTTP 200 with valid TwiML.**

---

## Root Cause: Twilio 15-Second Webhook Timeout

### Timing Analysis

| Step | Estimated Time |
|------|---------------|
| 1. Twilio POSTs to our webhook | 0s — timer starts |
| 2. Download audio from Twilio (11 KB) | ~1-2s |
| 3. soundfile decode + scipy resample to 16kHz | ~0.5s |
| 4. Whisper base model transcribe (8.4s audio) | ~2-4s |
| 5. ChromaDB embedding + retrieval | ~2-3s |
| 6. Ollama LLM (Qwen 2.5 7B) generate answer | ~5-8s |
| 7. Build TwiML + return HTTP response | ~0.1s |
| **TOTAL** | **~11-18s** |

**Twilio's webhook timeout is 15 seconds.** When the Ollama LLM takes longer than ~7 seconds (which happens intermittently), the total exceeds 15 seconds. Twilio closes the connection before our HTTP 200 arrives, and the message is never delivered to WhatsApp.

### Why Text Messages Work

Text messages skip steps 2-4 (no audio download or transcription), saving ~4-7 seconds:

| Step | Time |
|------|------|
| Receive text + RAG + respond | ~5-10s |

This reliably fits within 15 seconds.

### Why It's Intermittent

- Ollama LLM response time varies (5-8s depending on system load)
- Twilio audio download speed varies (network-dependent)
- When both are fast: ~11s total → reply delivered ✅
- When either is slow: ~16-18s total → Twilio drops it ❌

### Additional Issue: Whisper Transcription Degradation

The `base` model produces lower quality transcriptions on real-world audio:

| Spoken | Transcribed |
|--------|-------------|
| "Tell me about the FDU tuition fees" | "Tell me about the held-u intuition, Faze" |
| "What are the tuition fees of FDU?" | "What is the tuition fees of the FDU?" |

The garbled transcription feeds incorrect terms to the RAG, causing the LLM to hallucinate (e.g., "Fudan University" instead of "Fairleigh Dickinson University").

---

## Secondary Issue: No Audio Reply for Voice Notes

TTS audio replies were removed in commit `e5e865c` because Kokoro TTS generation takes 5-15 seconds on GPU, which alone exceeds the Twilio timeout. Voice notes currently reply with text only. The original plan to send text + audio together (`<Message><Body>` + `<Message><Media>`) was deferred.

---

## Summary

| # | Root Cause | Impact |
|---|-----------|--------|
| **1** | Voice note pipeline (download + STT + RAG) takes 11-18s, exceeding Twilio's 15s webhook timeout | No reply delivered when LLM is slow |
| **2** | `base` Whisper model inaccurate on real-world mobile audio | Garbled transcript → wrong RAG context → hallucination |
| **3** | TTS audio replies disabled (took too long) | Voice notes get text-only reply, no spoken answer |

---

## Recommended Fixes (for approval)

### Fix 1: Return Text Immediately, Process Voice Note Asynchronously

Instead of doing everything in the webhook handler:
1. **Immediately return HTTP 200** with "Processing your voice note..." (fits in <1s)
2. Download, transcribe, RAG in background
3. Send the answer via Twilio REST API as a separate message

**Impact:** Guarantees Twilio timeout is never hit. Voice notes always get a reply.
**Effort:** Medium — requires Twilio Python SDK for sending async messages.

### Fix 2: Upgrade Whisper Model to `small` or `small.en`

The `small.en` model (466M params) has ~2x better accuracy than `base` (74M params).

**Impact:** Much better transcription → more accurate RAG answers.
**Effort:** Low — change `"base"` to `"small.en"` in `_get_stt_model()`.
**Trade-off:** Slower transcription (~4-6s vs ~2-3s) — needs Fix 1 first.

### Fix 3: Re-enable TTS Audio Replies (After Fix 1)

Once responses are sent asynchronously (not during the webhook), TTS can generate audio without timeout pressure and send it as a follow-up WhatsApp message.

**Impact:** Voice notes get spoken audio replies.
**Effort:** Low — code already written, just needs to be reconnected.

---

## Implementation Order

```
Fix 1 (async replies) → Fix 2 (better Whisper) → Fix 3 (TTS audio)
```

Fix 1 must come first — it creates the async foundation that makes Fixes 2 and 3 possible without hitting timeouts.
