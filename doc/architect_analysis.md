# AI Architect's Analysis — University Admissions Voice Assistant

**Date:** 2026-07-25
**Purpose:** Bridge the vision documents with the current codebase reality for a time-constrained MVP demo.

---

## 1. Executive Summary

This project exists at **two very different tiers of completion**. Tier 1 — a working Streamlit text-chat RAG bot — is demo-ready today. It loads a university profile PDF, indexes it with ChromaDB, and answers admissions questions via Qwen 2.5 7B running locally through Ollama. It can be shared publicly through a Cloudflare tunnel. Tier 2 — the full Pipecat voice-to-voice pipeline with Twilio telephony described in the architecture and development-plan documents — is an aspirational target requiring 6 new build phases, none of which have been started. For a tight demo timeline, **the recommended strategy is to polish and present Tier 1** while framing Tier 2 as the roadmap. This document inventories what exists, maps every gap between the vision docs and the codebase, and provides concrete go/no-go recommendations.

---

## 2. Current State Inventory

### 2.1 What Is Built and Working

| File | Type | Status |
|---|---|---|
| `app.py` | Streamlit web UI (chat interface) | ✅ Working — text-in/text-out RAG chatbot |
| `admissions_bot.py` | CLI chatbot | ✅ Working — same RAG pipeline, interactive or single-query |
| `chroma_local_db/` | ChromaDB vector store | ✅ Pre-built — 50 chunks from the source PDF, persisted on disk |
| `launch.bat` | Windows launcher (local) | ✅ Working — health checks + starts Streamlit |
| `launch_tunnel.bat` | Windows launcher (public) | ✅ Working — health checks + Streamlit + Cloudflare tunnel |
| `content/sample_data/` | Source documents | ✅ UMD+FDU profile PDF (15 pages) + DOCX variant |

### 2.2 Tech Stack (Actual — Tier 1)

```
User Browser (localhost:8501 or trycloudflare.com)
        │
        ▼
┌─── Streamlit UI (app.py) ─────────────────────────────┐
│   - Chat input/output                                  │
│   - @st.cache_resource for RAG chain (loads once)      │
└───────────────────┬────────────────────────────────────┘
                    │ invoke({"input": question})
                    ▼
┌─── LangChain RAG Chain ───────────────────────────────┐
│   Retriever: ChromaDB (k=3, cosine similarity)         │
│   Embeddings: nomic-embed-text (via Ollama)             │
│   LLM: Qwen 2.5 7B (via Ollama, temp=0.0)              │
│   Chain: create_stuff_documents_chain + retrieval       │
└───────────────────┬────────────────────────────────────┘
                    │
                    ▼
┌─── Ollama Server (localhost:11434) ───────────────────┐
│   Models: qwen2.5:7b, nomic-embed-text                  │
│   GPU: NVIDIA CUDA (≥6 GB)                              │
└────────────────────────────────────────────────────────┘
```

### 2.3 Ports and Services

| Port | Service | Used By |
|---|---|---|
| 8501 | Streamlit web UI | `app.py` |
| 11434 | Ollama API | LLM inference + embeddings |

### 2.4 Models Pulled

| Model | Purpose | VRAM |
|---|---|---|
| `qwen2.5:7b` | LLM reasoning + answer generation | ~4.0 GB |
| `nomic-embed-text` | Document embedding for ChromaDB vector search | CPU/light |

### 2.5 ChromaDB Contents

- **Collection:** default LangChain collection
- **Source:** `UMD_and_FDU_University_Profile_Report.pdf` (15 pages)
- **Chunks:** 50 (chunk_size=800, overlap=150)
- **Content domains:** UMD and FDU admissions requirements, tuition fees, program listings, deadlines, international student requirements
- **Location:** `./chroma_local_db/` (persisted, does not rebuild on restart)

---

## 3. Document Gap Analysis

### 3.1 Vision vs. Reality: Architecture

| Concern | Vision Docs (doc/*.md) | Actual Codebase | Gap |
|---|---|---|---|
| **Orchestration framework** | Pipecat (streaming pipeline) | LangChain (batch RAG chain) | Complete replacement needed |
| **UI layer** | FastAPI + WebSockets | Streamlit (HTTP request/response) | Different paradigm (real-time vs. request/response) |
| **Transport** | Twilio Media Streams (WebSocket, 8kHz μ-law) | Browser HTTP (text) | No telephony integration exists |
| **Speech-to-Text** | Faster-Whisper `small.en`, CUDA INT8 (~0.8 GB VRAM) | None | Not started |
| **Text-to-Speech** | Kokoro-82M ONNX (~0.35 GB VRAM) | None | Not started |
| **Voice Activity Detection** | Silero VAD | None | Not started |
| **Database** | PostgreSQL (lead_calls, transcripts, extracted_lead JSONB) | None | Not started |
| **LLM serving** | Ollama (same) | Ollama (same) | ✅ Consistent |

### 3.2 Vision vs. Reality: Model Versions

| Component | Vision Docs Spec | Actual Codebase | Impact |
|---|---|---|---|
| **LLM Model** | `qwen2.5:6b-instruct-q4_K_M` (quantized, ~4.0 GB) | `qwen2.5:7b` (likely 7B FP16 or Q4_0, higher VRAM) | Vision docs use a smaller quantized variant to fit 6 GB budget. The 7B model may exceed budget when Whisper and Kokoro are also loaded. |
| **Embedding Model** | Not explicitly specified | `nomic-embed-text` | Works fine. Vision docs mention ChromaDB but leave embeddings implicit. |
| **Ollama Context** | `num_ctx: 2048` (explicit cap) | Default (likely 2048) | Vision docs explicitly cap context for VRAM safety. Current code doesn't set this — it relies on Ollama defaults. |

### 3.3 Vision vs. Reality: Phase-by-Phase

| Phase | Vision Doc Deliverable | Built? | Notes |
|---|---|---|---|
| Phase 1 | `requirements.txt` + `test_environment.py` | **No** | No `requirements.txt` exists. Packages installed ad-hoc via batch scripts. |
| Phase 2 | `test_audio_local.py` (Whisper + Kokoro) | **No** | No audio processing code exists anywhere. |
| Phase 3 | `test_rag_llm.py` (ChromaDB + Ollama RAG test) | **Partially** | `admissions_bot.py` and `app.py` implement this, but use LangChain not raw ChromaDB/Ollama calls. The vision script would be a lower-level implementation. |
| Phase 4 | `app/pipeline.py` + `run_pipeline_test.py` (Pipecat) | **No** | No Pipecat code exists. No `app/` directory exists. |
| Phase 5 | `app/main.py` (FastAPI + Twilio WebSocket) | **No** | No FastAPI server. Current server is Streamlit's built-in Tornado. |
| Phase 6 | `app/database.py` (PostgreSQL + lead extraction) | **No** | No database code. No transcript logging. |

### 3.4 Discrepancies Summary

1. **LLM model mismatch:** Vision docs say `qwen2.5:6b-instruct-q4_K_M`; code uses larger `qwen2.5:7b`. The quantized variant is critical for the voice pipeline's 6 GB VRAM budget — with the current 7B model, loading Whisper (~0.8 GB) and Kokoro (~0.35 GB) simultaneously would exceed budget.

2. **Completely different framework:** The vision docs assume Pipecat as the orchestrator. The codebase is pure LangChain. Migrating means rewriting the entire application layer.

3. **No audio infrastructure:** Zero audio processing code exists — no microphone capture, no WAV file handling, no streaming PCM frames. The voice pipeline requires building this from scratch.

4. **No telephony integration:** Twilio webhooks, TwiML, μ-law audio resampling, and WebSocket media streaming are all absent.

5. **No persistence layer:** No PostgreSQL schema, no transcript storage, no lead extraction. The current bot is stateless — each query is independent with no conversation memory.

6. **No `requirements.txt` or `test_environment.py`:** Dependencies are managed ad-hoc through the batch launchers' `pip install` commands.

---

## 4. MVP Demo Recommendation

### 4.1 The Constraint

> **"MVP for the demo with a tight timeline"** — the user's exact words.

This means: build nothing new unless it's low-risk, fast, and directly improves the demo. The existing Streamlit bot already delivers the core value proposition (AI-powered admissions Q&A). Breaking it now by attempting the voice pipeline would risk having nothing to show.

### 4.2 Recommendation: Two-Tier Demo Strategy

#### Tier A — Deliver with Confidence (The Streamlit Text Bot)

**What to show:**
- Live text-chat demo using the existing `launch_tunnel.bat`
- Ask pre-prepared questions covering UMD and FDU admissions, tuition, deadlines
- The bot answers from the PDF context, demonstrating RAG in action
- Share the `trycloudflare.com` URL so stakeholders can try it on their own devices

**What to polish before the demo:**

| # | Action | Effort | Risk |
|---|---|---|---|
| 1 | Verify ChromaDB is populated and returning correct chunks | 10 min | Low |
| 2 | Test the Cloudflare tunnel end-to-end from an external device | 5 min | Low |
| 3 | Prepare a list of 6-8 demo questions with expected answers | 15 min | Low |
| 4 | Add a "sample questions" sidebar with clickable prompts in `app.py` | 20 min | Low |
| 5 | Verify Ollama starts cleanly and the full boot sequence completes under 60s | 10 min | Low |

**Total Tier A polish effort:** ~1 hour. Risk: near zero.

#### Tier B — Stretch Goal (Show Voice Readiness)

If time permits after Tier A is locked down, add these to demonstrate architectural vision without building the full pipeline:

| # | Action | Effort | Risk |
|---|---|---|---|
| 1 | Create `requirements.txt` from the vision docs for credibility | 10 min | Low |
| 2 | Create `test_environment.py` (Phase 1) — GPU check + Ollama health | 30 min | Low |
| 3 | Create a FastAPI "hello world" WebSocket echo server at `app/main.py` that accepts audio frames and echoes them back, to show the transport pattern | 45 min | Medium |

**Total Tier B effort:** ~1.5 hours. Risk: medium (FastAPI is new to this codebase).

#### Tier C — Do NOT Attempt for MVP

| Phase | Why Skip |
|---|---|
| Phase 2 (Whisper + Kokoro) | Requires pulling ~2 GB of new models, configuring ONNX CUDA, debugging audio drivers. High risk of GPU out-of-memory on 6 GB. |
| Phase 4 (Full Pipecat pipeline) | Pipecat is a complex real-time framework. Building a working pipeline from zero audio infrastructure is days of work, not hours. |
| Phase 5 (Twilio integration) | Requires a Twilio account, phone number provisioning, ngrok setup, and debugging real-time media streaming. Adds external dependencies and cost. |
| Phase 6 (PostgreSQL) | Requires PostgreSQL installation, schema creation, and async lead extraction. Valuable long-term but zero demo impact — the demo audience won't query a database. |

### 4.3 Why Tier A Is the Right Demo

1. **It works right now.** There is zero build risk.
2. **It demonstrates the core AI value.** RAG over real university documents, local LLM, zero cloud dependency — these are the selling points.
3. **It's interactive.** Stakeholders can type their own questions and see real-time responses.
4. **It tells the full story.** You can present the architecture diagram from `architecture_overview.md` as the roadmap and then show the working Streamlit bot as "Phase 1 delivered — voice pipeline is next."

---

## 5. Architecture Diagrams

### 5.1 Current Architecture (Tier A — Working Today)

```
┌─────────────────────────────────────────────────────────────┐
│                     USER INTERFACE                           │
│   Web Browser ──► http://localhost:8501                      │
│            or ──► https://xxxx.trycloudflare.com              │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (text)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                 STREAMLIT APPLICATION (app.py)                │
│                                                              │
│   ┌──────────┐    ┌───────────────┐    ┌──────────────┐     │
│   │ Chat UI  │───►│ LangChain RAG │───►│ Chat Message │     │
│   │ (input)  │    │ Chain (cached)│    │ (rendered)   │     │
│   └──────────┘    └───────┬───────┘    └──────────────┘     │
│                           │                                   │
└───────────────────────────┼───────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
┌──────────────────────┐    ┌──────────────────────────┐
│   CHROMADB (local)    │    │   OLLAMA (localhost:11434) │
│   ./chroma_local_db/  │    │                            │
│   50 chunks, k=3      │    │  qwen2.5:7b  (~4.0 GB)    │
│   embeddings via       │    │  nomic-embed-text          │
│   nomic-embed-text     │    │  num_ctx: default          │
└──────────────────────┘    └──────────────────────────┘

STATUS: ✅ FULLY OPERATIONAL
VRAM: ~4.0 GB / 6.0 GB (single model loaded)
LATENCY: ~2-5 seconds per response (PDF indexing + LLM generation)
```

### 5.2 Target Architecture (Tier 2 — Aspirational, from vision docs)

```
┌─────────────────────────────────────────────────────────────┐
│                     USER INTERFACES                          │
│   📞 Twilio Voice   │   💬 WhatsApp API                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS / WebSocket (8kHz μ-law PCM)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               FASTAPI APPLICATION (app/main.py)              │
│   GET /twilio/voice → TwiML                                  │
│   WS /ws/twilio → Pipecat FastAPIWebsocketTransport          │
└──────────────────────────┬──────────────────────────────────┘
                           │ Audio frames
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  PIPECAT AI FRAMEWORK                        │
│                                                              │
│   Audio In ─► Silero VAD ─► Whisper STT ─► RAG Router       │
│                                                 │            │
│                    ┌────────────────────────────┘            │
│                    ▼                                         │
│   ChromaDB Vector Search ─► Qwen 2.5 6B ─► Kokoro TTS       │
│                    (k=2-3)     (Q4_K_M)       (ONNX)         │
│                                                 │            │
│                    └────────────────────────────┘            │
│                                               ▼              │
│                          Audio Out ─► Transport.output()     │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                PERSISTENCE LAYER                             │
│   PostgreSQL: lead_calls (UUID, transcript, extracted_lead)   │
│   ChromaDB: admissions knowledge embeddings (persisted)       │
└─────────────────────────────────────────────────────────────┘

STATUS: ❌ NOT STARTED (0 of 6 phases complete)
PEAK VRAM: ~5.65 GB / 6.00 GB (all models loaded)
LATENCY TARGET: ~200ms STT + ~500ms RAG/LLM + ~200ms TTS ≈ 1 second
```

### 5.3 Gap Map

```
Current (Tier A)                    Target (Tier 2)
═══════════════                      ═══════════════

Streamlit UI          ──replace──►  FastAPI + WebSocket
HTTP request/response ──replace──►  Streaming pipeline
Text input            ──extend───►  Audio input (Whisper STT)
None                  ──add──────►  Silero VAD
LangChain RAG chain   ──migrate──►  Pipecat context processor
None                  ──add──────►  Kokoro-82M TTS
None                  ──add──────►  Twilio Media Streams
None                  ──add──────►  PostgreSQL lead capture
qwen2.5:7b            ──swap─────►  qwen2.5:6b-instruct-q4_K_M
Default num_ctx        ──pin──────►  num_ctx: 2048
```

---

## 6. Next-Step Action Items (Pre-Demo)

In priority order:

### 6.1 Critical (Do Before Demo)

1. **Verify ChromaDB health**
   ```bash
   python -c "from langchain_community.vectorstores import Chroma; from langchain_ollama import OllamaEmbeddings; v=Chroma(persist_directory='./chroma_local_db', embedding_function=OllamaEmbeddings(model='nomic-embed-text')); print(f'Chunks: {v._collection.count()}')"
   ```
   Expected: `Chunks: 50`. If 0, rebuild by running `admissions_bot.py` once.

2. **End-to-end tunnel test**
   - Double-click `launch_tunnel.bat`
   - Copy the `trycloudflare.com` URL
   - Open it on a phone or another device (not localhost)
   - Ask "What is the tuition fee at UMD?" and confirm an answer arrives
   - Close all windows

3. **Prepare demo question script** (6-8 questions covering key domains):
   - Tuition: "What is the undergraduate tuition at UMD?"
   - Deadlines: "When is the application deadline for FDU?"
   - Requirements: "What GPA does UMD Computer Science require?"
   - International: "What TOEFL score do international students need?"
   - Programs: "What programs does FDU offer?"
   - Comparison: "Compare UMD and FDU admissions requirements"
   - Edge case: "Do you offer a nursing program?" (tests "I don't have that information" response)
   - Edge case: "Can I get a full scholarship?" (tests hallucination resistance)

### 6.2 Nice-to-Have (If Time Permits)

4. **Add sample question buttons to Streamlit sidebar**
   - Modify `app.py` to show clickable example questions
   - Keeps the demo flowing without typing

5. **Create `requirements.txt`**
   - Formalize the current dependencies for documentation credibility

### 6.3 Post-Demo Roadmap

6. **Phase 1 from development plan** — `test_environment.py` for GPU/Ollama verification
7. **Phase 3 adapted** — `test_rag_llm.py` using the existing ChromaDB but with lower-level Ollama calls (no LangChain dependency for the voice pipeline)
8. **Phase 5 lite** — FastAPI WebSocket skeleton to prove the transport pattern
9. **Phase 2 + 4** — Only attempt after verifying GPU headroom with the quantized 6B model

---

## 7. Key Technical Decisions to Revisit

These decisions from the vision docs need confirmation before starting Tier 2:

1. **Pipecat vs. LangChain for orchestration:** Pipecat handles real-time audio streaming natively; LangChain is batch-oriented. The documents are correct to use Pipecat for voice, but the current LangChain RAG chain must be rewritten as a Pipecat context processor. This is non-trivial.

2. **LLM model swap:** The current `qwen2.5:7b` must be replaced with `qwen2.5:6b-instruct-q4_K_M` to fit Whisper and Kokoro onto the 6 GB GPU. Pull and benchmark the quantized model before building anything dependent on it.

3. **PostgreSQL necessity:** For an MVP demo, PostgreSQL adds deployment complexity (install, configure, run) with zero audience-visible benefit. Consider SQLite or a JSON file for demo transcript logging, and defer PostgreSQL to post-demo.

4. **Twilio dependency:** Twilio requires a paid account, phone number, and webhook configuration. For a demo, a pre-recorded audio file piped through the pipeline achieves the same visual proof without the external dependency.

---

## 8. Twilio Alternatives — Unbiased Transport-Layer Analysis

### 8.1 The Core Question

> *Twilio requires credentials and a paid subscription. Can we test the voice pipeline without it, and plug Twilio in later through a config file?*

**Short answer:** Yes. The transport layer (how audio gets in/out) is independent of the AI pipeline (STT → RAG → LLM → TTS). We should build the pipeline first with a zero-cost local transport, then swap in Twilio via a config file when credentials arrive.

### 8.2 Architecture Principle: Transport Agnosticism

The vision documents already hint at this — Pipecat's `transport.input()` and `transport.output()` are abstract interfaces. The pipeline doesn't care whether audio comes from a phone call, a browser microphone, or a WAV file on disk.

```
                    ┌─────────────────────────────┐
                    │     PIPECAT PIPELINE          │
                    │  (unchanged regardless of     │
                    │   transport choice)           │
                    │                               │
                    │  VAD → STT → RAG → LLM → TTS │
                    └───────────┬───────────────────┘
                                │
                ┌───────────────┴───────────────┐
                │                               │
                ▼                               ▼
    ┌───────────────────┐           ┌───────────────────┐
    │  TEST TRANSPORT    │           │  PROD TRANSPORT    │
    │  (free, local)     │           │  (Twilio, paid)    │
    │                    │           │                    │
    │  • WAV file stream │           │  • Twilio Media    │
    │  • Browser mic     │           │    Streams WS      │
    │  • PyAudio loopback│           │  • WhatsApp API    │
    └───────────────────┘           └───────────────────┘
         NOW: build & test               LATER: swap via config
```

### 8.3 Option Matrix — Evaluated Objectively

| # | Option | Cost | Credential Required | Audio I/O | Demo Quality | Setup Effort | Windows Compat |
|---|---|---|---|---|---|---|---|
| 1 | **Local WAV File Harness** | Free | None | Pre-recorded `.wav` → saved `.wav` | Medium (no live mic) | ⭐ Very Low | ✅ Native |
| 2 | **Browser Microphone + WebSocket** | Free | None | Live browser mic → browser speaker | High (real voice demo) | ⭐⭐ Low-Medium | ✅ Any browser |
| 3 | **PyAudio Loopback** | Free | None | System mic → system speaker | High (native feel) | ⭐⭐⭐ Medium | ⚠️ Driver issues |
| 4 | **Discord Bot (Voice API)** | Free | Discord App (free) | Discord voice channel | Medium (requires Discord) | ⭐⭐⭐ Medium | ✅ |
| 5 | **Twilio (target)** | Paid | Twilio SID + Token + Phone# | PSTN/SIP phone call | Production-grade | ⭐⭐⭐⭐ High | ✅ |
| 6 | **Local SIP (Asterisk/FreeSWITCH)** | Free | None | Softphone app | Low (complex setup) | ⭐⭐⭐⭐⭐ Very High | ❌ Poor |

### 8.4 Unbiased Recommendation: Two-Phase Transport Strategy

#### Phase A — Build & Test Now (Zero Cost, Zero Credentials)

**Primary recommendation: Local WAV File Harness**

This is the fastest path to a working pipeline test. Already implied by Phase 4 of the development plan (`run_pipeline_test.py`).

How it works:
```
test_in.wav  ──► WebSocket Client ──► FastAPI /ws/test ──► Pipecat Pipeline ──► test_out.wav
                    (streams PCM          (accepts audio           (processes)           (saves TTS
                     chunks)               frames)                                        output)
```

Files needed:
- `test_in.wav` — a 5-10 second 16kHz mono WAV file with a spoken question (e.g., "What is the tuition fee at UMD?")
- `test_transport.py` — a Python script using `websockets` library that:
  1. Reads `test_in.wav` in PCM chunks
  2. Sends chunks over WebSocket to the FastAPI endpoint
  3. Receives TTS audio chunks back
  4. Assembles and saves as `test_out.wav`
- `app/main.py` — FastAPI server with a `/ws/test` endpoint (same endpoint works for both test and Twilio transports — just different clients)

**Pros:**
- Zero external dependencies (no accounts, no API keys, no driver installs)
- 100% reproducible (same input always produces same output — great for debugging)
- Fastest to build (15-30 minutes)
- Works even on machines without a microphone

**Cons:**
- Not a "live" demo experience — pre-recorded questions only
- Less impressive for stakeholders expecting a real-time voice conversation

**Verdict:** ✅ Build this first. It validates the entire pipeline end-to-end and serves as the CI test harness forever.

---

**Stretch recommendation: Browser Microphone WebSocket Page**

If the demo needs a *live* voice interaction feel, add a minimal HTML page served by FastAPI that captures the browser microphone and streams it over WebSocket.

How it works:
```
┌──────────────────────────────────────────────────────┐
│  BROWSER (any device on local network)                │
│                                                       │
│  getUserMedia() mic capture                           │
│       │                                               │
│       ▼                                               │
│  AudioContext → ScriptProcessorNode → PCM 16kHz       │
│       │                                               │
│       ▼                                               │
│  WebSocket → ws://localhost:8000/ws/voice             │
│       │                                               │
│       ▼                                               │
│  Receive TTS audio chunks → AudioContext.play()       │
└──────────────────────────────────────────────────────┘
```

Files needed:
- `app/static/voice_client.html` — a single self-contained HTML page (~100 lines) with mic capture + WebSocket + audio playback
- Served by FastAPI at `GET /voice` — open in any browser, click "Start", and talk

**Pros:**
- Gives a live, real-time voice demo experience
- Works from any device on the network (phone, laptop, tablet)
- No installs — just open a URL in Chrome/Edge
- Looks impressive to stakeholders

**Cons:**
- Requires a microphone on the demo device
- Browser autoplay policies may require a user click before audio plays
- ~1 hour to build and debug across browsers

**Verdict:** ✅ Build this second, after the WAV harness confirms the pipeline works. This is the demo-day UI.

---

#### Phase B — Swap to Twilio Later (Config File Pattern)

When Twilio credentials arrive, the switch is surgical:

**Config file: `app/config.py`**
```python
# Transport provider: "websocket" | "twilio" | "browser"
TRANSPORT_PROVIDER = "websocket"  # change to "twilio" when ready

# Twilio credentials (only used when TRANSPORT_PROVIDER="twilio")
TWILIO_ACCOUNT_SID = ""
TWILIO_AUTH_TOKEN = ""
TWILIO_PHONE_NUMBER = ""

# Audio settings
AUDIO_SAMPLE_RATE = 16000  # for local test
TWILIO_SAMPLE_RATE = 8000  # for Twilio μ-law
```

**What changes when you swap to Twilio:**
1. `app/main.py` — add `GET /twilio/voice` (TwiML endpoint) and a second WebSocket route `/ws/twilio` alongside the existing `/ws/test`
2. Transport layer — instantiate `FastAPIWebsocketTransport` with Twilio-specific params (μ-law resampling, stereo→mono)
3. That's it. The pipeline (`app/pipeline.py`) is untouched.

```
Same pipeline ──► Different transports

app/main.py
├── /ws/test        ← WAV harness (Phase A, always available)
├── /voice           ← Browser mic page (Phase A, for live demos)
└── /ws/twilio       ← Twilio Media Streams (Phase B, when credentials arrive)
```

### 8.5 Why NOT the Other Options

| Option | Why Not Recommended for MVP |
|---|---|
| **PyAudio Loopback** | `pyaudio` requires C++ build tools on Windows, often fails to install. `sounddevice` is better but still has ASIO/WASAPI driver issues. The browser approach avoids all driver headaches. |
| **Discord Bot** | Adds an entirely separate SDK to learn. Discord voice requires gateway intents, voice state handling, and opus encoding. Too much surface area for a demo. |
| **Local SIP (Asterisk)** | Massive over-engineering. Setting up a SIP server for a demo is a multi-day project on its own. |
| **Twilio-first (no alternative)** | Blocks all pipeline testing until a paid account is set up. The pipeline can't be validated end-to-end. |

### 8.6 Concrete Implementation Plan

```
Step 1 (now):     Create app/main.py with /ws/test endpoint
                  Create test_transport.py (WAV file streamer)
                  → Run Phase 4 pipeline test end-to-end

Step 2 (warmup):  Add app/static/voice_client.html
                  → Browser mic → pipeline → TTS audio
                  → Live demo without Twilio

Step 3 (later):   When Twilio creds arrive:
                  • Fill in app/config.py
                  • Add /twilio/voice + /ws/twilio routes
                  • Run ngrok, configure Twilio webhook
                  → Phone calls work
```

### 8.7 Bottom Line

> **Build transport-agnostic. Test with WAV files today. Demo with browser mic tomorrow. Swap to Twilio when the credentials are in hand.** The pipeline doesn't care — and neither should the architecture.

---

## Appendix: File Inventory

```
D:\university_project_demo\
├── app.py                          ✅ Streamlit web UI (working)
├── admissions_bot.py               ✅ CLI chatbot (working)
├── launch.bat                      ✅ Local launcher (working)
├── launch_tunnel.bat               ✅ Public launcher (working)
├── launch_Guide.txt                ✅ User instructions
├── PROJECT_REFERENCE.md            ✅ Developer reference
├── project-overview.md             ✅ Project summary
├── University_Projwect_v1.ipynb    📓 Original Colab notebook (source of truth)
├── chroma_local_db/                ✅ Pre-built vector store (50 chunks)
├── content/sample_data/
│   ├── UMD_and_FDU_University_Profile_Report.pdf   ✅ Source document
│   └── UMD_and_FDU_University_Profile_Report.docx  ✅ Source document (DOCX)
├── doc/
│   ├── architecture_overview.md    📋 Vision document (aspirational)
│   ├── development_plan.md         📋 Vision document (aspirational)
│   └── architect_analysis.md       📋 This document
└── app/                            ❌ Does not exist yet (target: pipeline.py, main.py, database.py)
```

**Legend:** ✅ = Working | 📋 = Reference/Documentation | 📓 = Notebook | ❌ = Placeholder/Not Started
