# Infrastructure & Model Upgrade Plan — Real-Time Human-Like Voice AI

**Document version:** 1.0
**Date:** 2026-08-02
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`

---

## Executive Summary

The admissions voice assistant works functionally but feels like talking to a **laggy robot**, not a human. The core problem: audio pipeline latency is **15–35 seconds per utterance**. For human-like conversation, we need **under 2 seconds** to the first word.

This document covers:
1. **Current latency breakdown** — where time is actually spent
2. **Hardware utilization** — RTX 2060 (6 GB VRAM) is underutilized
3. **Model upgrades** — what to change per component and why
4. **Streaming architecture** — how to go from turn-based to real-time
5. **Infrastructure hardening** — reliability for production use
6. **Scaling guide** — from demo (1 call) to production (50 calls)

---

## 1. Current State — Latency Audit

### 1.1 Hardware

| Component | Spec | Status |
|-----------|------|--------|
| GPU | NVIDIA RTX 2060 (6 GB VRAM) | **Underutilized** — only Ollama LLM uses it |
| CPU | AMD Ryzen 5 3500 (6-core, 3.6 GHz) | Carrying STT + TTS load that should be on GPU |
| RAM | 32 GB | Plenty |
| OS | Windows 11 Pro | AppLocker blocks some DLLs |
| CUDA | 13.0, driver 581.57 | Available |

### 1.2 Voice Pipeline — Per-Utterance Timing

```
User stops speaking
  │
  ├── 600ms — VAD trailing silence (fixed, 30 frames × 20ms)
  │
  ├── 2–4s — STT (openai-whisper base, 74M params, CPU float32, no fp16)
  │          ⚠ Runs on CPU despite CUDA available
  │
  ├── 0.3–1s — RAG retrieval (ChromaDB + Ollama nomic-embed-text)
  │            ⚠ Embedding model serialized with LLM on same Ollama
  │            ⚠ HTTP /api/tags probe on EVERY query (extra round-trip)
  │
  ├── 5–10s — LLM inference (qwen2.5:7b Q3_K_M, num_ctx=2048, non-streaming)
  │           ⚠ Q3_K_M is aggressive 3-bit quantization — lower quality
  │           ⚠ Model cold-loads on first query (not pre-warmed in Ollama)
  │           ⚠ No token streaming — waits for full response
  │
  ├── 15–30s — TTS synthesis (Kokoro ONNX, CPU ONLY)
  │            🔴 BIGGEST BOTTLENECK (~80% of total latency)
  │            🔴 onnxruntime-gpu IS installed but Kokoro NEVER uses CUDA
  │            🔴 No caching — same phrase synthesized fresh every time
  │            🔴 Full response synthesized before first byte sent
  │
  └── TOTAL: 18–35 seconds of dead silence before caller hears anything
```

### 1.3 Current Model Inventory

| Stage | Model | Size | Device | Speed |
|-------|-------|------|--------|-------|
| STT (calls/WA) | openai-whisper `base` (74M) | 139 MB | **CPU** (fp32) | 2–4s |
| STT (Streamlit) | faster-whisper `small.en` (244M) | 464 MB | CUDA (int8) | 0.5–1s |
| STT (Pipecat, unused) | faster-whisper `small.en` | — | CUDA | — |
| LLM | qwen2.5:7b Q3_K_M | 3.8 GB | Ollama/CUDA | 5–10s |
| Embedding | nomic-embed-text | 274 MB | Ollama/CUDA | 2–3s |
| TTS | Kokoro ONNX v1.0 | 325 MB | **CPU** | 15–30s |

**Key finding:** The GPU (RTX 2060) sits mostly idle. Only Ollama uses it for LLM. STT (openai-whisper) and TTS (Kokoro) both run on CPU despite CUDA being available.

---

## 2. Model Upgrade Plan

### 2.1 STT: openai-whisper `base` → faster-whisper `small.en`

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| Model | openai-whisper base (74M) | faster-whisper small.en (244M) | 3.3× larger, better accuracy |
| Speed | 2–4s (CPU fp32) | 0.5–1s (CUDA int8) | **4× faster** |
| VRAM | 0 (CPU) | ~0.8 GB | Fits in budget |
| Accuracy | Poor on real mobile audio | Significantly better | Fewer hallucinated responses |

**Why it's currently blocked:** faster-whisper depends on PyAV for audio file loading. The PyAV DLL is blocked by Windows AppLocker on this machine. The code falls back to openai-whisper which uses pure PyTorch.

**Fix:** faster-whisper's `model.transcribe()` accepts a **numpy array directly** — no PyAV needed. Our `voice_handler.py` already processes audio as numpy arrays. The workaround:
```python
# Instead of model.transcribe("audio.wav") which needs PyAV:
model.transcribe(audio_numpy_array, language="en")
```
This bypasses PyAV entirely. No AppLocker changes needed.

### 2.2 LLM: Q3_K_M → Q4_K_M + Streaming + GPU Preload

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| Model | qwen2.5:7b Q3_K_M (3-bit) | qwen2.5:7b Q4_K_M (4-bit) | Better response quality |
| Size | 3.8 GB | 4.4 GB | Still fits RTX 2060 |
| Speed | 5–10s (cold start common) | 2–4s (preloaded, GPU-resident) | **2–3× faster** |
| Streaming | No (ollama.chat blocking) | Yes (stream=True) | First token in ~0.5s |
| Context | num_ctx=2048 | num_ctx=4096 | More conversation memory |
| Pre-warm | Not loaded until first query | `ollama pull` + keep-alive | No cold start |

**Implementation:**
```python
# Current (blocking, no streaming)
response = ollama.chat(model="qwen2.5:7b-instruct-q3_K_M",
                        messages=[...], options={"num_ctx": 2048})
answer = response["message"]["content"]

# Target (streaming tokens)
stream = ollama.chat(model="qwen2.5:7b-instruct-q4_K_M",
                      messages=[...], stream=True,
                      options={"num_ctx": 4096, "temperature": 0.7})
for chunk in stream:
    token = chunk["message"]["content"]
    yield token  # Process sentence-by-sentence
```

**Pre-warming Ollama:** Send a dummy query at startup so the model is GPU-resident before any real call comes in.

### 2.3 TTS: Kokoro CPU → CUDA + Caching + Chunked Streaming

**🔴 This is the #1 bottleneck (15–30s, ~80% of total latency)**

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| Device | CPU (onnxruntime-gpu installed but unused) | CUDA (GPUExecutionProvider) | **5–10× faster** |
| Synthesis | Full response at once | Sentence-by-sentence chunks | First audio in ~1s |
| Caching | None | LRU cache for top 50 phrases | Instant for common responses |
| Truncation | 500 chars (hard cap) | 300 chars per chunk, streaming | Natural flow |

**Fix A — Wire CUDA to Kokoro:**
The current code creates Kokoro without specifying execution providers:
```python
# Current — CPU only
kokoro = Kokoro(model_path, voices_path)

# Target — Force CUDA
import onnxruntime as ort
kokoro = Kokoro(model_path, voices_path)
# Patch: set preferred execution provider
ort_session = ort.InferenceSession(model_path,
    providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
```

If the `kokoro_onnx` library version doesn't expose provider control, use a raw ONNX Runtime session for TTS inference or upgrade to a CUDA-aware version.

**Fix B — TTS Response Caching:**
```python
from functools import lru_cache

@lru_cache(maxsize=50)
def _synthesise_cached(text: str) -> bytes:
    """Cache common phrases — instant replay."""
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(text, voice="af_heart", speed=1.0)
    # ... convert to µ-law bytes ...
    return ulaw_bytes
```

Common phrases that repeat across calls (greetings, program descriptions, fee answers) would be instant on second use.

**Fix C — Sentence-Level Streaming:**
Instead of synthesizing the entire LLM response, split at sentence boundaries and synthesize each sentence independently. Stream the first sentence's audio while the second sentence is being synthesized.

### 2.4 Embedding: Move off Ollama → Local `all-MiniLM-L6-v2`

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| Model | nomic-embed-text (via Ollama) | all-MiniLM-L6-v2 (sentence-transformers) | No Ollama bottleneck |
| Speed | 2–3s (serialized with LLM generation) | 0.2–0.5s (direct, independent) | **6× faster** |
| VRAM | Competes with LLM on Ollama | ~0.5 GB (independent) | Independent scaling |

nomic-embed-text served by Ollama is a serialization point — every RAG query competes with LLM inference on the same server. A local embedding model completely removes this bottleneck.

### 2.5 VRAM Budget — RTX 2060 (6 GB)

| Component | VRAM | Notes |
|-----------|------|-------|
| faster-whisper small.en (INT8) | ~0.8 GB | CTranslate2, int8 quantized |
| qwen2.5:7b Q4_K_M | ~4.8 GB | 28 layers GPU, 4-bit quantized |
| Kokoro ONNX (CUDA) | ~0.35 GB | 325 MB onnx + runtime |
| all-MiniLM-L6-v2 embedding | ~0.1 GB | 22M params, tiny |
| Overhead / buffers | ~0.1 GB | CUDA context, audio buffers |
| **Total** | **~6.0 GB** | **Tight fit. Exactly at 6 GB limit.** |

**Fallback if OOM:** Use `qwen2.5:3b-instruct-q5_K_M` (~2.5 GB) instead of 7B. Quality is slightly lower but fits comfortably within 4 GB total.

---

## 3. Streaming Architecture

### 3.1 Current: Turn-Based (Blocking)

```
User speaks → [600ms silence wait] → STT (2–4s) → RAG (0.5–1s)
  → LLM (5–10s, full response) → TTS (15–30s, full response) → Audio plays
                                                                    ↑
                                            Caller hears THIS after 18–35s
```

Every stage blocks the next. The caller sits in dead silence for up to 35 seconds.

### 3.2 Target: Streaming Pipeline

```
User speaks → [300ms VAD] → STT (0.5–1s GPU) → RAG (0.2s)
  → LLM begins streaming tokens...
     Token 1 (0.5s): "UMD"  → accumulate
     Token N (1.0s): "MBA tuition is $45,000 per year." → SENTENCE COMPLETE
       → TTS chunk 1 (1s GPU) → stream audio to caller ✨ FIRST AUDIO AT ~2.5s
     Token M (1.5s): "Scholarships are available..." → SENTENCE COMPLETE
       → TTS chunk 2 (0.5s, cached!) → stream audio
     ...
     Token Z (3.0s): "...any other questions?" → SENTENCE COMPLETE
       → TTS chunk 3 (1s) → stream audio
```

**Key changes:**
1. LLM streams tokens → accumulate into sentence buffer
2. On sentence boundary (`.`, `?`, `!`), trigger TTS for that sentence
3. TTS chunks stream to Twilio as they complete (no waiting for full response)
4. Caller hears first words in **~2.5 seconds** instead of 18–35 seconds
5. TTS cache makes repeated sentences/phrases instant

### 3.3 VAD Optimization

| Parameter | Current | Target | Reason |
|-----------|---------|--------|--------|
| Silence threshold | 600ms (30 frames) | 300ms (15 frames) | Less dead air after speech |
| Max utterance | 6s (300 frames) | 8s (400 frames) | Allow longer questions |
| VAD method | RMS energy (<80) | Silero VAD (neural) | More accurate endpoint detection |

Silero VAD is already loaded by the Pipecat pipeline (`app/pipeline.py`). It just isn't wired into the live WebSocket path. Switching to Silero gives smarter endpoint detection that distinguishes speech from background noise.

### 3.4 Barge-In / Interrupt (V2)

Allow the caller to interrupt the AI mid-response by speaking over it:
1. While TTS is playing, continue monitoring input audio energy
2. If caller speech detected above threshold → immediately stop TTS → switch to listening mode
3. This makes the conversation feel natural — like a real human who stops talking when interrupted

---

## 4. Infrastructure Hardening

### 4.1 Cloudflare Named Tunnel (Permanent URL)

**Current problem:** Ephemeral quick tunnel. URL changes on every restart. Twilio webhooks must be re-configured. DNS failures observed (exit code 127).

**Fix:** Create a named Cloudflare tunnel with a permanent hostname:
```bash
cloudflared tunnel create admissions-tunnel
cloudflared tunnel route dns admissions-tunnel admissions.university-demo.com
```
Store the tunnel token in `.env` as `CLOUDFLARED_TUNNEL_TOKEN`.

Benefits:
- Permanent URL — no Twilio reconfiguration needed after restart
- More stable (authenticated, not "best effort" quick tunnel)
- Can add load balancing for multiple instances later

### 4.2 Ollama Model Pre-Warming

**Current problem:** First call after server start pays a 5–10s cold-load penalty while the 3.8 GB LLM loads into GPU.

**Fix:** At server startup (in the lifespan handler), send a dummy "ping" query to Ollama:
```python
ollama.chat(model="qwen2.5:7b-instruct-q4_K_M",
             messages=[{"role": "user", "content": "ping"}],
             keep_alive="24h")
```
This forces the model into GPU VRAM and keeps it there for 24 hours. No cold start on the first real call.

### 4.3 WhatsApp — All Processing Async

**Current problem:** Text messages run synchronous RAG inside the 15s Twilio webhook window. If Ollama is slow, Twilio drops the reply.

**Fix:** Move ALL WhatsApp processing (text AND voice) to `BackgroundTasks`:
1. Webhook returns acknowledgment TwiML immediately (<1s)
2. Background task runs STT/RAG/LLM/TTS
3. Reply sent via Twilio REST API

This eliminates the 15s webhook timeout risk entirely.

### 4.4 PostgreSQL Connection Pooling

**Current problem:** Raw psycopg2 connections with `autocommit=True`. Some paths create duplicate connections (`api_get_call_queue_status`). No connection reuse.

**Fix:** Use `psycopg2.pool.ThreadedConnectionPool` with 5 connections:
```python
from psycopg2 import pool
_pool = pool.ThreadedConnectionPool(2, 5, dsn=DATABASE_URL)
```

### 4.5 Docker Compose — Add Missing Services

The current `docker-compose.yml` is missing PostgreSQL (relies on a separate `e-learning-backend` project). Add:
- PostgreSQL 16 with pgvector
- Named Cloudflare tunnel service
- Volume mounts for model cache persistence (whisper models, kokoro models, ollama models)

---

## 5. Implementation Roadmap

### Phase 1: GPU Migration (4 hours) — Biggest Wins

| Step | File | Effort | Expected Gain |
|------|------|--------|---------------|
| 1.1 Wire CUDA to Kokoro TTS | `app/voice_handler.py`, `app/main.py`, `app.py` | 1h | **15–30s → 2–5s** (5–10×) |
| 1.2 Switch STT to faster-whisper small.en (numpy array bypass) | `app/voice_handler.py`, `app/main.py` | 1h | **2–4s → 0.5–1s** (4×) |
| 1.3 Add TTS caching (LRU, 50 entries) | `app/voice_handler.py` | 30m | **Instant for cached phrases** |
| 1.4 Move embeddings to local all-MiniLM | `app/rag.py` | 30m | **2–3s → 0.2–0.5s** (6×) |
| 1.5 Upgrade LLM to Q4_K_M + GPU preload | `.env`, `app/rag.py`, `app/main.py` lifespan | 45m | **5–10s → 2–4s** (2×) |
| 1.6 Remove per-query /api/tags probe | `app/rag.py` | 15m | **Saves 0.2–0.5s per query** |

**After Phase 1:** 18–35s → **5–10s** per utterance

### Phase 2: Streaming (6 hours) — Human-Like Feel

| Step | File | Effort | Expected Gain |
|------|------|--------|---------------|
| 2.1 LLM token streaming (`stream=True`) | `app/rag.py`, `app/pipeline.py` | 1.5h | First token in 0.5s |
| 2.2 Sentence-level TTS chunking | `app/voice_handler.py` | 2h | First audio in 1–2s |
| 2.3 Reduce VAD silence to 300ms | `app/voice_handler.py` | 15m | Less dead air |
| 2.4 Switch to Silero VAD | `app/voice_handler.py` | 1h | Better endpoint detection |
| 2.5 WhatsApp: background ALL processing | `app/main.py` | 1h | Eliminate timeout risk |
| 2.6 Pre-generate static TTS for common answers | `scripts/pregen_tts.py` (new) | 30m | Instant for FAQs |

**After Phase 2:** Perceived latency **2–3 seconds** to first word, flowing response

### Phase 3: Infrastructure (3 hours) — Reliability

| Step | File | Effort |
|------|------|--------|
| 3.1 Named Cloudflare tunnel | `start_services.ps1`, `.env` | 1h |
| 3.2 PostgreSQL connection pooling | `app/database.py`, `app/leads/models.py` | 45m |
| 3.3 Ollama pre-warming in startup lifespan | `app/main.py` | 30m |
| 3.4 Docker compose update | `docker-compose.yml` | 45m |
| 3.5 Health check improvements | `start_services.ps1` | 30m |

### Phase 4: Polish & Barge-In (4 hours) — Production Feel

| Step | File | Effort |
|------|------|--------|
| 4.1 Caller interrupt during TTS (barge-in) | `app/voice_handler.py`, `app/main.py` WS handlers | 2h |
| 4.2 Conversation history persistence across calls | `app/voice_handler.py`, `app/leads/service.py` | 1h |
| 4.3 Latency monitoring dashboard widget | `app/main.py`, `app/static/dashboard.html` | 1h |

---

## 6. Scaling Guide

### 6.1 Current Machine — 1 Concurrent Call

| Component | Spec | Max Concurrent Calls |
|-----------|------|---------------------|
| GPU | RTX 2060 (6 GB) | **1 call** (full VRAM used by one session) |
| CPU | Ryzen 5 3500 (6-core) | 1–2 calls |
| RAM | 32 GB | Not the bottleneck |

### 6.2 Production Upgrade Paths

| Tier | GPU | VRAM | Concurrent Calls | Cloud Cost (RunPod) |
|------|-----|------|------------------|---------------------|
| **Starter** | RTX 3060 | 12 GB | 2 calls | ~$0.39/hr |
| **Standard** | RTX 4090 | 24 GB | 3–5 calls | ~$0.79/hr |
| **Professional** | A100 SXM | 80 GB | 10–20 calls | ~$1.99/hr |
| **Enterprise** | 2× A100 | 160 GB | 30–50 calls | ~$3.98/hr |

### 6.3 Cloud Deployment (RunPod / AWS)

**Simplest path:** Deploy the Docker Compose stack on RunPod.io with an RTX 4090 GPU pod:
1. Upload `docker-compose.yml` + `.env`
2. Set environment variables (Twilio credentials, DB URL)
3. Start: `docker compose up -d`
4. Point Cloudflare named tunnel to the RunPod IP
5. ~$570/month for 24/7 operation on single RTX 4090

---

## 7. Summary — Before/After

| Metric | Before (Current) | After (Phase 1+2) |
|--------|------------------|-------------------|
| STT speed | 2–4s (CPU) | 0.5–1s (CUDA) |
| LLM speed | 5–10s (Q3, cold start) | 2–4s (Q4, preloaded) |
| TTS speed | 15–30s (CPU) | 2–5s (CUDA + cache) |
| First audio | 18–35s | **2–3s** |
| Flowing response | No (full output then play) | Yes (sentence-by-sentence streaming) |
| Barge-in | No | V2 feature |
| TTS caching | No | Top 50 phrases cached |
| VLAD quality | RMS energy (basic) | Silero VAD (neural) |
| WhatsApp timeout risk | Text messages (sync RAG) | None (all async) |
| Tunnel stability | Ephemeral, URL changes | Named, permanent |
| Model quality | Base whisper, Q3 LLM | Small.en whisper, Q4 LLM |
| **Caller experience** | "Is this thing working?" | "This feels like a real person" |

---

## 8. Files Modified (Complete List)

| File | Phase | Changes |
|------|-------|---------|
| `app/voice_handler.py` | 1, 2, 4 | STT model switch, CUDA TTS, TTS caching, VAD threshold, Silero VAD, streaming TTS chunks, barge-in |
| `app/main.py` | 1, 2, 3 | STT model switch, CUDA TTS, WhatsApp async all, Ollama pre-warm, lifespan changes, DTMF handling (already done) |
| `app/rag.py` | 1, 2 | Local embeddings, remove /api/tags probe, LLM streaming, model upgrade |
| `app/pipeline.py` | 2 | Wire streaming into production WS path |
| `app/config.py` | 1 | Add model/quantization settings |
| `.env` | 1, 3 | Updated model names, GPU settings, tunnel token |
| `requirements.txt` | 1 | Add faster-whisper, sentence-transformers |
| `docker-compose.yml` | 3 | Add PostgreSQL, tunnel service, model volumes |
| `start_services.ps1` | 3 | Named tunnel, GPU health check, Ollama pre-warm |
| `scripts/update_twilio_webhook.py` | 3 | Add WhatsApp sandbox update (already planned) |

---

## 9. Related Documents

- `doc/ENHANCEMENT_ROADMAP.md` — System-level technical enhancements
- `doc/UX_ENHANCEMENT_ROADMAP.md` — Counselor UX improvements
- `doc/COMMAND_COCKPIT_DASHBOARD.md` — Single-page dashboard spec
- `doc/RCA_WHATSAPP_VOICE_NOTES.md` — WhatsApp latency analysis
- `doc/PENDING_IMPROVEMENTS.md` — Older improvement notes
- `doc/ANALYSIS_WAV_VS_MP3.md` — TTS timing benchmarks
