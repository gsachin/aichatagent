# University Admissions Voice AI — Project Summary, Pipeline & Model Inventory

**Product:** Meridian University Admissions Voice AI Assistant
**Repo:** `D:\project\universityDemo`
**Audience:** anyone who needs to explain what this system does, how a call flows
through it, and exactly which AI models are running.
**Verified against:** the code in `app/` and the live `.env` on 2026-10-06. Where a
document and the code disagree, the code is stated and the discrepancy is flagged
(see §9).

---

## 1. What the project is

An **AI voice agent that answers and places admissions phone calls for a
university**, end to end — from the phone line to the CRM record.

A caller dials in, an AI agent named "Alex" holds a natural spoken conversation,
answers questions from the university's own knowledge base (programs, fees,
deadlines, accreditation, scholarships), collects the caller's details, and books
the call as a lead. The same agent can place **outbound** calls, and the same
knowledge base answers **chat** and **WhatsApp** questions.

Around the conversation it does the admissions back-office work:

| Capability | Where |
|---|---|
| Inbound voice calls (Twilio) | `app/main.py` `/ws/twilio`, `app/voice_handler.py` |
| Outbound call campaigns | `app/outbound/caller.py`, `app/outbound/scheduler.py` |
| Lead capture & extraction | `app/leads/`, `app/database.py` |
| Sentiment scoring of leads | `app/sentiment/` |
| Offer-letter generation (PDF) + email | `app/offers/`, `app/emailer.py` |
| Customer record sync (Salesforce) | `app/crm/` |
| Admin dashboard (Streamlit) | `app/dashboard/` |
| MCP tool server for external agents | `app/mcp/` |

**Design stance:** everything AI runs **locally**. No call audio, transcript, or
student record leaves the host — the LLM, speech-to-text, text-to-speech, and
embeddings are all self-hosted. The only outbound traffic is Twilio (the phone
carrier) and the university's own CRM.

---

## 2. The pipeline at a glance

```
                 ┌─────────────────────── Twilio Media Streams (µ-law 8 kHz, 20 ms frames)
                 ▼
   ┌──────────────────────────┐
   │ 1. Ingest + Echo Gate    │  RMS per frame; caller admitted, agent's own voice rejected
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 2. VAD / End of Speech   │  silence counter closes the turn (600 ms)
   │    + Speculative STT     │  transcribe-so-far starts 220 ms early
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 3. Speech-to-Text        │  faster-whisper small.en (CTranslate2, CUDA/CPU)
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 4. Noise / Quality Gate  │  logprob, no-speech probability, min chars — drops noise
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 5. Retrieval (RAG)       │  enterprise-rag-core over MCP  →  local ChromaDB fallback
   │                          │  hybrid: dense + BM25, RRF fusion, ONNX rerank
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 6. Prompt Assembly       │  voice system prompt + retrieved context + history + profile
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 7. LLM Generation        │  Ollama (llama3.2:3b) — streamed, cut into clauses
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │ 8. Text-to-Speech        │  Kokoro ONNX (voice af_heart) → PCM → µ-law
   └────────────┬─────────────┘
                ▼
              audio back to the caller ──▶ (loop back to step 1, listening)

   After the call: transcript → lead extraction → PostgreSQL → CRM sync → sentiment
```

A human turn is roughly: **caller stops talking → ~600 ms later the system
answers**. The STT pass is hidden inside that pause by speculative transcription.

---

## 3. Pipeline stages in detail

### Stage 1 — Audio ingest and the echo gate

Twilio sends the call as **µ-law, 8 kHz, mono, 20 ms frames** over a WebSocket
(`/ws/twilio`). Everything internal is 16-bit linear PCM; conversions funnel
through `app/audio_format.py`.

Because a phone line has no echo cancellation, the agent would otherwise hear its
own voice and interrupt itself. `VoiceCallSession.admit_inbound_frame()` runs a
per-frame RMS comparison against a decaying envelope of what was just played out:

```
admit if  rms(frame) > max(BARGE_IN_ABS_FLOOR, reference_envelope × ECHO_GAIN × MARGIN)
          for BARGE_IN_MIN_FRAMES consecutive frames
```

| Setting | Value | Meaning |
|---|---|---|
| `BARGE_IN_ENABLED` | `1` | Caller may interrupt; the agent yields the floor |
| `BARGE_IN_ABS_FLOOR` | `150` | Absolute RMS floor — above VAD silence (80), below quiet speech (~157) |
| `BARGE_IN_ECHO_GAIN` | `0.5` | Assumed −6 dB return of our own signal (**declared assumption, not measured**) |
| `BARGE_IN_MARGIN` | `2.0` | How far above the echo the caller must be |
| `BARGE_IN_MIN_FRAMES` | `6` | 120 ms of evidence — filters clicks and lip smacks |
| `BARGE_IN_REF_DECAY` | `0.95` | Playback envelope decay (~270 ms of memory) |

This is **signal processing, not machine learning** — no model is involved. Only
when a frame is admitted does it reach the VAD and the turn machinery. Measured
behaviour is recorded in `doc/perf/us014-echo-guard-tuning.md`.

### Stage 2 — End-of-speech detection (the VAD) and speculative STT

There is **exactly one live end-of-speech decision** in the system
(`BRD-04`): a silence counter in `voice_handler.py`, not a neural VAD.

| Setting | Value | Meaning |
|---|---|---|
| `VAD_SILENCE_MS` | `600` | Silence that closes the caller's turn (30 frames) |
| `VAD_SPECULATIVE_ADVANCE_MS` | `220` | Start transcribing while the remaining pause is still counting |
| `MIN_UTTERANCE_FRAMES` | `15` | 300 ms — anything shorter is a noise burst |

The 600 ms is a deliberate pause tolerance, sized so callers who hesitate
mid-sentence (the load fixtures encode a 240 ms hesitation) are not cut off.

**Speculative STT** is the key latency trick: the transcript of the audio-so-far
is produced *during* the silence window, so the ~200 ms STT pass is not added to
the caller's wait. It is used **only if the buffered audio did not change** — if
the caller resumed speaking, the speculative result is discarded and the
transcript is identical to the non-speculative path *by construction*. The caller
hears no difference; nothing is guessed.

> A neural VAD (Silero) is available in the Pipecat dependency and was once
> constructed in `app/pipeline.py`, but was **deliberately removed**: it was never
> attached to anything, and it presented a second, contradictory end-of-speech
> delay (500 ms vs 600 ms). See the removal note at `app/pipeline.py:172-189`.

### Stage 3 — Speech-to-text

**faster-whisper** (CTranslate2 runtime, not stock PyTorch Whisper).

- Model: `WHISPER_MODEL=small.en` (English-only, small variant)
- Device: CUDA when `CUDA_VISIBLE_DEVICES` is set, else CPU
- Compute type: `int8` on CUDA, `float32` on CPU
- Threads: `WHISPER_NUM_THREADS=4` (machine-sized by `scripts/predeploy.py`)
- Loaded **once per process** and shared by every concurrent call

Apple Silicon has no CTranslate2 Metal backend, so the platform layer maps
`mps → cpu + int8` (NEON-accelerated) rather than failing.

### Stage 4 — Noise and quality gate (pre-LLM guardrails)

Cheap deterministic filters that stop noise from ever reaching the model — this is
what keeps a cough from costing a full generation:

| Setting | Value | Rejects |
|---|---|---|
| `STT_MIN_AVG_LOGPROB` | `−4.0` | Low-confidence transcriptions |
| `STT_MAX_NO_SPEECH_PROB` | `0.8` | Whisper's own "this is not speech" verdict |
| `STT_MIN_CHARS` | `3` | One- and two-character fragments |
| `MIN_UTTERANCE_FRAMES` | `15` | Sub-300 ms bursts |

Plus `scrub_meta_leak()` and `is_noise_fragment()` — regex rules that strip
prompt/meta text the model may emit and catch filler words ("uh", "hmm").

### Stage 5 — Retrieval (RAG)

**MCP-first, with automatic fallback.** `app/rag.py` is a dispatcher: context
comes from the standalone **enterprise-rag-core** service over streamable HTTP
(`http://127.0.0.1:8010/mcp`), and falls back to a local **ChromaDB** store when
the service is unreachable.

| Setting | Value | Meaning |
|---|---|---|
| `USE_MCP_RAG` | `auto` | MCP first; on failure, warn + fall back to local |
| `RAG_SEARCH_MODE` | `hybrid` | Dense vectors **+** BM25 keyword, combined by RRF |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model (768-dim), served by Ollama |
| `RAG_TOP_K` | `5` | Chunks passed to the LLM |
| `RAG_FETCH_K` | `20` | Candidate pool before reranking |
| `RAG_SIMILARITY_THRESHOLD` | `0.0` | Distance gate **disabled** (measured distances overlap between valid and off-topic questions) |
| `RAG_RETRIEVAL_BUDGET` | `8.0 s` | Whole operation, fallback included |
| `RAG_FALLBACK_RESERVE` | `1.5 s` | Reserved so the fallback always has time to run |
| `RAG_BREAKER_MODE` | `probe` | Half-open circuit admits **one** cheap probe per cooldown window |
| `RAG_MCP_COOLDOWN` | `30 s` | Breaker cooldown |
| `RAG_MCP_PROBE_FRACTION` | `0.25` | Probe budget as a fraction of the 6.0 s read timeout |

A **circuit breaker** wraps the primary: when enterprise-rag-core is down, the
turn does not pay the full timeout on every request. Cost is bounded *per window*,
not *per turn served* — the whole retrieval is charged to the caller at most once
per cooldown. Every turn is attributed (`retrieval_rung`, `retrieval_breaker`,
`retrieval_dead_dependency_ms`) so the cost of a dead dependency is **measured,
not inferred**.

Retrieval quality defences: markdown-aware chunking with ingestion validation,
context guarding, and a similarity gate that is available but deliberately off.

### Stage 6 — Prompt assembly

Two distinct system prompts, so voice and chat behave differently:

- **Voice** (`app/voice_system_prompt.py`, ~3.5k tokens) — spoken-output rules,
  turn-taking, interruption handling, no Markdown, no lists read aloud.
- **Chat** (`app/rag_legacy.py` `SYSTEM_PROMPT`) — Markdown-oriented.

The assembled prompt is: `voice system prompt + retrieved context + student
profile + recent history + the caller's question`.

`retrieval_query` is **decoupled from the question**: the caller's actual question
is embedded for retrieval, while the instruction boilerplate stays in the prompt
tail — otherwise the embedding vector is dominated by the system prompt and
retrieval degrades.

### Stage 7 — LLM generation

**Ollama**, at a literal IPv4 address (`127.0.0.1`, never `localhost`).

| Setting | Value | Why |
|---|---|---|
| `OLLAMA_MODEL` | `llama3.2:3b` | The live answering model |
| `OLLAMA_NUM_CTX` | `8192` | Voice prompt (~3.5k tokens) + RAG context |
| `OLLAMA_NUM_PREDICT` | `192` | Output ceiling — the 95th percentile of 83 golden-set answers is 106 tokens, so 192 truncates none but bounds the worst case on a live call |
| `OLLAMA_TEMPERATURE` | `0.3` | Low, for factual consistency |
| `OLLAMA_KEEP_ALIVE` | `-1` | Never unload — the model stays resident |
| `SMALL_TASK_NUM_CTX` | `8192` | **Pinned to match the serving path** (see below) |

Two hard-won constraints are encoded here:

1. **One model, one context size.** Ollama keeps one runner per model and tears it
   down whenever a request arrives with a *different* `num_ctx` — a measured
   ~6.4 s reload, with a fresh, empty KV cache. The app previously ran four context
   sizes against the same model for different tasks, so its own background calls
   kept evicting the voice path's runner. Every call site now resolves to the same
   value.
2. **Keep-alive is sent on every request, not just at boot.** Ollama resets a
   model's residency to the 5-minute server default on any request that omits it —
   which silently undid the boot pre-warm and produced a measured 32.9 s cold load
   *with working pre-warm code in the repo*.

**Streaming** (`LLM_STREAM`) is available and the generation is consumed as token
deltas, cut into complete clauses by `ClauseCutter` so TTS can start on the first
clause while the model is still decoding. A clause ends on sentence punctuation
followed by whitespace and a capital, and never right after a digit — the *same*
boundary rule the TTS sentence splitter uses, so a clause is never re-split.

### Stage 8 — Text-to-speech

**Kokoro v1.0 ONNX** (`kokoro_onnx`), run on `onnxruntime`.

- Voice: `af_heart` (configurable), speed `1.0`, clamped to `[0.5, 2.0]`
- Execution provider: **requests CUDA, then reports what the session actually
  got** — this matters, because the CUDA provider silently falls back to CPU on
  Windows if CUDA/cuDNN aren't resolvable, and the original code logged a 5–10×
  speedup that was never happening. `ort.preload_dlls()` reuses torch's bundled
  CUDA 12.8 + cuDNN 9. Measured RTF: **0.63 on CPU vs 0.06 on GPU** — the
  difference between a ~6 s synthesis and a usable call.
- **TTS cache**: `TTS_CACHE_SCOPE=per_call`. This was set **by measurement, not
  preference** — with a process-wide shared cache, two concurrent callers
  produced 30 observed cases of caller B being served audio synthesised during
  caller A's session. The cache is now bounded to one session.
- Streaming TTS (`TTS_STREAM`) is behind a flag: first audio chunk is sent while
  the rest is still synthesising.

Audio is converted PCM → µ-law and written back to the Twilio socket.

### After the call

The transcript is handed to a post-call handler that extracts structured lead
data (name, email, program, intent) via the LLM, writes the call record and lead
to **PostgreSQL**, syncs to the **CRM**, and computes sentiment.

### Concurrency and admission control

| Setting | Value | Meaning |
|---|---|---|
| `MAX_CONCURRENT_CALLS` | `2` | Sized for this box |
| `ADMISSION_ENABLED` | `true` | A third inbound call is refused with a pre-synthesised busy message — better than degrading all three |

A **work-priority gate** (`app/work_priority.py`) arbitrates the model between
live voice turns and background work. A voice turn holds the gate for its whole
duration; background units (lead extraction, sentiment, chat) wait for the line to
be clear and run at most one at a time. This is the single point where "the caller
in front of us wins" is decided. Background work that waits past its budget is
refused rather than interleaved — and that refusal is explicitly **not** a
caller-facing failure.

---

## 4. What we use in AI — by function

| Function | Technology | Kind |
|---|---|---|
| Speech recognition | faster-whisper (CTranslate2) | Neural, encoder-decoder transformer |
| Language understanding & answer generation | Ollama-served LLM (llama3.2:3b) | Neural, decoder-only transformer |
| Knowledge retrieval | enterprise-rag-core: hybrid dense + BM25, RRF fusion | Vector search + lexical ranking |
| Reranking | cross-encoder MiniLM (INT8 ONNX) | Neural cross-encoder |
| Embeddings | nomic-embed-text (768-dim) | Neural, served by Ollama |
| Speech synthesis | Kokoro v1.0 ONNX | Neural TTS |
| End-of-speech detection | RMS energy counter | **Deterministic** — no model |
| Echo / barge-in gate | RMS ratio vs decaying playback envelope | **Deterministic** — no model |
| Noise rejection | logprob + probabilities + regex | **Deterministic** |
| Sentiment categorisation | rule engine (`rules-v1`) + LLM extraction | Hybrid |
| Lead extraction | LLM with structured JSON output | Neural |

**Everything neural is self-hosted.** There is no OpenAI, Anthropic, or other
third-party inference API in the request path — inference is local, on this
machine's GPU.

---

## 5. Model inventory

### Currently installed and live (Ollama)

| Model | Size on disk | Role | Used by |
|---|---|---|---|
| `llama3.2:3b` | 2.02 GB | **Live answering LLM** — voice, chat, WhatsApp, lead extraction, sentiment | `app/llm_backend.py` → RAG, voice, sentiment |
| `qwen2.5:14b` | 8.99 GB | Installed, GPU-tier preferred model; **not selected by the live voice path** | Available to `pick_model()` |
| `nomic-embed-text:latest` | 0.27 GB | Embeddings for retrieval (768-dim) | Chroma, enterprise-rag-core |

### Other models in the stack

| Model | Role | Runtime | Where |
|---|---|---|---|
| `faster-whisper small.en` | Speech-to-text | CTranslate2 (CUDA int8 / CPU float32) | `app/voice_handler.py` |
| Kokoro `kokoro-v1.0.onnx` + `voices-v1.0.bin` | Text-to-speech, voice `af_heart` | onnxruntime (CUDA → CPU fallback) | `app/voice_handler.py` |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` (INT8 ONNX, 22 MiB) | Reranking retrieved chunks | onnxruntime | `enterprise-rag-core` |
| `mlx-community/Qwen2.5-14B-Instruct-4bit` | **macOS Apple Silicon alternative** to Ollama | `mlx_lm.server` | `app/llm_backend.py` |
| `nomic-ai/nomic-embed-text-v1.5` | macOS embedding alternative | transformers / sentence-transformers | `app/llm_backend.py` |

### Configured but overridden

| Setting | `app/config.py` default | Live `.env` value |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct-q3_K_M` | `llama3.2:3b` |

### How the model is actually chosen

`pick_model()` resolves in this order:

1. **Exact match** against the caller's preference list — and that list always
   begins with `OLLAMA_MODEL`, so the configured model wins whenever it is
   installed.
2. Otherwise, the first installed tag containing `qwen`.
3. Otherwise, `OLLAMA_MODEL` regardless.

Because `llama3.2:3b` is both configured *and* installed, **`llama3.2:3b` is what
answers a live call on this box.** The `qwen2.5:14b` install is a leftover from
the GPU-tier profile and is reachable only if `OLLAMA_MODEL` is pointed at it.

### Why these models

- **3B-class LLM:** a live phone call needs bounded latency far more than
  maximum reasoning depth. The answers are grounded in retrieved context, so the
  model's job is to phrase what it was given, not to know it. The 192-token
  ceiling is derived from the measured answer-length distribution, not taste.
- **`small.en` Whisper:** English-only admissions calls; the `.en` variant is
  more accurate per unit of compute than the multilingual model at the same size,
  and STT latency sits directly inside the caller's pause.
- **Nomic embeddings:** 768-dim, strong on retrieval, and small enough to stay
  resident alongside the chat model in 16 GB of VRAM.
- **Kokoro:** the fastest usable neural TTS available locally at this quality —
  and it must be, because synthesis is the dominant cost of a two-caller turn.
- **Local-only:** admissions calls carry names, contact details, grades, and
  identity documents. Keeping inference on-host is a data-protection decision as
  much as a latency one.

---

## 6. Hardware target

Detected profile (`\.machine_profile.json`, tier `nvidia_high`):

| Resource | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti, **15.9 GB VRAM** |
| CPU | 6 cores |
| RAM | 31.9 GB |
| OS | Windows 11 Pro |
| Python | 3.11 |

`scripts/predeploy.py` sizes the AI settings to the detected tier
(`nvidia_high` → `qwen2.5:14b`, 8192 ctx, `small.en`, 6 Whisper threads) and
writes them into the `.env` machine-profile block. The stack deliberately runs a
**single uvicorn worker** — it holds model residency (Whisper, Kokoro), and a
second worker would duplicate that footprint.

Minimal-GPU and CPU-only paths exist: `int8` STT compute, CPU Kokoro (RTF ~0.6),
and the platform layer maps Apple Silicon to CPU-for-Whisper + MLX-for-LLM.

---

## 7. Services and ports

| Service | Port | Notes |
|---|---|---|
| FastAPI backend | `8000` | `/ws/twilio`, `/ws/voice`, `/api/*`, `/mcp/sse` |
| PostgreSQL 16 + pgvector | `5432` | Leads, calls, offers, documents (Docker: `elearning-postgres`) |
| Streamlit dashboard | `8501` | Admin UI |
| enterprise-rag-core (MCP) | `8010` | Streamable HTTP JSON-RPC |
| Salesforce admission API (CRM) | `8098` | Frozen contract, loopback dev deployment |
| Ollama | `11434` | LLM + embeddings |
| MLX server (macOS only) | `1234` | Alternative LLM backend |

Entry points: `/voice` (browser mic), `/call` (quick call), `/dashboard`
(admin), `/ws/twilio` (carrier). Twilio reaches the app through a Cloudflare
tunnel whose hostname is resolved by `app/config.py::tunnel_host()`.

---

## 8. Integrations

| Integration | Purpose | Notes |
|---|---|---|
| **Twilio** | Inbound/outbound calls, WhatsApp | Media Streams; no native echo cancellation |
| **Salesforce** | Lead and offer sync | Master switch `CRM_ENABLED=true`; circuit breaker (`CRM_BREAKER_THRESHOLD=5`, 60 s reset); an **outbox** holds only *transient* failures — permanent ones are dropped, because a deleted record is permanent |
| **PostgreSQL + pgvector** | System of record | |
| **SMTP** | Offer-letter email | |
| **MCP** | Exposes lead tools to external agents | `app/mcp/` |

The CRM integration is **fail-safe toward the caller**: timeouts are 2 s connect /
5 s read, sized so a slow CRM gives up rather than delaying a live phone call.

---

## 9. Quality engineering, evaluation, and known discrepancies

### Evaluation

- **161-case golden set** (`eval/golden_set.jsonl`) with `tuned` / `held-out`
  splits, per-intent coverage, verified ground truth with a named source section,
  and `critical` flags.
- Scored by `eval/rag_metrics.py`; sign-off packet generation in
  `eval/signoff_packet.py` (DG-03).
- A large `tests/` suite plus `doc/perf/` measurement records for every
  latency/quality claim.

### Observability

- `app/perf_trace.py` records per-turn marks (retrieval, LLM prefill vs
  generation, TTS) with the engine's **own counters** (`prompt_eval_count`,
  `eval_count`, `load_duration`) — so prefill and generation are separable, and a
  residency lapse is visible on the trace rather than silent.
- Structured call timeline: `EVENT <call_id> turn=<n> <NAME> key=value`, so a
  whole call can be reconstructed from logs.

### Discrepancies found while writing this — worth knowing before you present

1. **`\.machine_profile.json` says `applied.OLLAMA_MODEL = qwen2.5:14b`, but the
   live `.env` says `llama3.2:3b`.** The profile file is from 2026-08-21; the
   `.env` is newer (2026-10-05) and is the authority (`app/config.py`). **State
   `llama3.2:3b` as the live model.** If the 14B model is what you intend to
   demo, set `OLLAMA_MODEL=qwen2.5:14b` *above* the machine-profile block in
   `.env` — otherwise the next `predeploy` run overwrites it.
2. **`app/voice_handler.py`'s module docstring is stale.** It claims
   `openai-whisper (base)` for STT; the code uses **faster-whisper** with
   `WHISPER_MODEL=small.en`. It also says it "reuses the same components that
   power WhatsApp voice notes" — the `/twilio/whatsapp` webhook currently
   **rejects audio** and asks the sender to type. Several RCAs in `doc/` describe
   the earlier WhatsApp-voice behaviour, so older documents may not match today's
   code.
3. **The Pipecat pipeline in `app/pipeline.py` is not the live call path.** It is
   a harness/assembly path; the live Twilio call runs through
   `VoiceCallSession` in `app/voice_handler.py`. Both are real, but if you
   describe "the pipeline", describe `voice_handler.py`.
4. **`BARGE_IN_ECHO_GAIN=0.5` is a declared assumption, not a measurement** — the
   code says so explicitly, since the carrier's acoustic return path cannot be
   measured locally. The tuning record documents the sweep it was chosen from.
5. `RAG_SIMILARITY_THRESHOLD=0.0` means the **retrieval relevance gate is
   disabled** in production — measured distances overlap between valid and
   off-topic questions, so a global gate would reject valid ones. Do not describe
   the system as "rejecting out-of-scope questions at retrieval"; it relies on
   the system prompt for that.

### Documentation drift — do not quote the older docs

A sweep of the existing `doc/` set turned up claims that no longer match the code.
These were each checked against the live system; the values in this file are the
verified ones.

| Older doc claim | Verified reality |
|---|---|
| Answers questions about **UMD / Fairleigh Dickinson** | **Meridian University** — `COMPANY_NAME`, `UNIVERSITY_NAME`, `content/meridian/`, and every spoken line in `app/admission.py` / `app/main.py` |
| **Silero VAD**, triggers on 0.5 s silence | Deliberately removed; one RMS silence counter at **600 ms** (`BRD-04`) |
| Voice retrieval is **plain similarity, k=2 or k=3** | **Hybrid (dense + BM25 + RRF), k=5**, fetch_k=20 |
| `num_ctx: 2048` | **8192** |
| LLM is **Qwen** | Config default is Qwen, but the **live `.env` runs `llama3.2:3b`** |
| `phi3:mini` installed | **Not installed** — the box has only `llama3.2:3b`, `qwen2.5:14b`, `nomic-embed-text` |
| Vector store polluted, **4,618 chunks vs ~50 expected** | **Resolved** — the live collection holds **35 chunks** |
| FastAPI runs **2 uvicorn workers** | **Single worker by design** — the stack holds model residency; `FASTAPI_WORKERS` is sizing metadata that `_reconcile_workers` reports as *not in effect* |
| Hybrid BM25 "not implemented" | Implemented — `app/rag_legacy.py::retrieve_context_hybrid`, and `RAG_SEARCH_MODE=hybrid` is live |

Older latency numbers in `doc/` are also untrustworthy: they mix CPU-only,
6 GB-VRAM, and Apple Silicon budgets, and some quote 12–30 s end-to-end voice
turns that describe a CPU fallback rather than this machine. **Measure before
quoting any latency figure.**

### Scale-out path (documented, not deployed)

`doc/cloudDeployment/` describes a production plan that is **not** what runs
today: **vLLM** serving `Qwen/Qwen2.5-7B-Instruct` (AWQ 4-bit, `--max-model-len
4096`, `--gpu-memory-utilization 0.75`, `--max-num-seqs 20`) on **RunPod** GPUs
(RTX 4090 24 GB, ~5–15 users; A100 80 GB, 10–50 users), with a Docker Compose
stack of postgres → vLLM → FastAPI → Streamlit behind a Cloudflare named tunnel.
The justification is sound — continuous batching and PagedAttention for
concurrency, versus Ollama's sequential serving. If you are asked "how would this
scale to N callers?", that plan is the honest answer; today's deployment is
Ollama on the local box, sized for **two concurrent callers**.

---

## 10. One-paragraph summary you can say out loud

> It's a local-first AI voice agent for university admissions. A call comes in
> over Twilio; the system separates the caller's voice from its own echo, waits
> for them to finish, and transcribes with a local Whisper model — starting the
> transcription during the pause so it isn't added to their wait. The transcript
> retrieves grounded context from the university's knowledge base through a
> hybrid vector-plus-keyword search with a neural reranker, that context is
> composed into a spoken-style prompt, and a 3-billion-parameter model served by
> Ollama writes the answer. A local neural TTS voices it back in under the time
> it takes to read. Every model runs on this machine — no audio, transcript, or
> student record goes to a third party — and afterwards the call becomes a
> structured lead, a CRM record, and a sentiment score.
