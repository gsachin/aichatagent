# University Admissions AI Assistant — Project Document

**What this document is:** a single consolidated description of the application — what it does, how it works end to end, and the tools and technologies it is built from.

**Repository:** `D:\project\universityDemo`
**Compiled:** 2026-10-15

> **How to read the evidence in this document.** The repo contains an unusually large
> documentation set, and not all of it matches the code — earlier iterations of this
> project answered questions about *UMD* and *Fairleigh Dickinson*, and some older
> documents still describe that system. Where this document makes a factual claim about
> behaviour, the claim is taken from the repo's own verified records
> (`doc/PROJECT_AI_PIPELINE.md` §"Documentation drift", `doc/sdlc/07-brownfield-reconciliation.md`)
> or from direct inspection of the code and configuration. Section 11 lists the things
> that are known **not** to be true today, because several of them are the ones a
> reader is most likely to assume.

---

## 1. Executive summary

The project is a **local-first AI voice agent for university admissions**. A prospective
student or parent calls a phone number; an AI agent answers, holds a natural spoken
conversation, and answers questions about programmes, fees, deadlines, eligibility and
scholarships from a curated knowledge base. The same assistant is available over a web
chat UI and over WhatsApp. After the call, the system extracts lead details, writes them
to a database, and synchronises them to a CRM.

The defining constraint is that **everything neural runs on one machine**. No OpenAI,
Anthropic or other hosted inference API sits anywhere in the request path — admissions
calls carry names, contact details, grades and identity documents, so local inference is
a data-protection decision as much as a latency one (`AS-03`).

The whole stack is engineered around a single number: **the time between a caller
finishing a sentence and hearing the first syllable of the reply.** The project has an
extensive measurement programme around that number, whose measured results are in
Section 10.

---

## 2. Product context

| | |
|---|---|
| **Domain** | University admissions enquiry handling |
| **Demo persona** | "Meridian University" — the knowledge base, the spoken lines, the programme catalogue and the contact details are all Meridian |
| **Primary user** | Prospective student or parent, calling in |
| **Secondary users** | Admissions counsellor (receives warm handoffs), operator (runs the stack), developer |
| **Deployment** | Single Windows 11 box, 8 co-resident services |
| **Concurrency target** | **Two simultaneous callers** — and explicitly not more (see §4.6) |
| **Hard constraint** | Local inference only; student PII never leaves the host |

### Hardware target

Detected profile (`.machine_profile.json`, tier `nvidia_high`):

| Resource | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 5060 Ti, **15.9 GB VRAM** (448 GB/s bandwidth) |
| CPU | AMD Ryzen 5 3500, 6 cores / 6 threads |
| RAM | 31.9 GB |
| OS | Windows 11 Pro |
| Python | 3.11 |

The stack deliberately runs a **single uvicorn worker**: the process holds model
residency (Whisper and Kokoro live inside it), so a second worker would duplicate that
footprint for no throughput gain.

---

## 3. Feature catalogue

### 3.1 Inbound voice calls — the primary feature

The caller dials a Twilio number, hears an IVR prompt, presses a digit, and is connected
to the AI agent over a media stream. From that point the conversation is spoken, turn by
turn, with the agent answering from the knowledge base.

The behavioural surface is formally catalogued as **28 caller intents** in
`doc/sdlc/09-intent-catalog.md`. **Twelve are classified Critical** — the ones where a
wrong answer is materially harmful:

| Group | Intents |
|---|---|
| **Knowledge intents** (retrieval + LLM + TTS) | Undergraduate, postgraduate and doctoral programmes; **fees**; **scholarships**; **important dates**; **admission process & eligibility**; campus & hostel; **accreditation & rankings**; leadership & history; contact information; FAQ |
| **Conversational intents** (LLM + TTS) | **Lead capture**; **escalation to a human**; **callback / appointment change**; outbound call handling; **opt-out**; **call termination**; noise & unintelligible speech; STT error correction; multiple requests in one turn; **interruption / barge-in**; backchannel; caller silence; topic change; distressed caller; out-of-scope questions; **consequential-action confirmation** |

Beyond pure question answering, the voice agent implements a set of **deterministic
short-circuits** — paths that never invoke the model at all, because they are cheaper,
faster and more reliable than asking a language model to decide:

- **Sign-off detection** — "bye", "goodbye", "that's all" match a regex and end the call
  with a fixed closing line.
- **Noise rejection** — fragments below a length threshold, or transcripts with poor STT
  confidence, get a fixed clarification line rather than a model call.
- **STT domain correction** — a `_CORRECTIONS` map rewrites known mis-transcriptions
  ("meridian", "tuition", "MBA", "GPA", "IELTS") before the prompt is built.
- **Pre-synthesised fixed responses** — used when generation or synthesis fails, read
  from disk so they work even when the models that would produce them are the things that
  broke.

### 3.2 Grounding and anti-fabrication boundary

The single most heavily developed feature of the system, and the one with the most
engineering history, is the **grounding boundary** (`app/voice_grounding.py`). For an
admissions line, an invented fee or an invented programme is a commercial and
reputational risk, not merely a quality defect.

What it does:

- **Claim extraction** — pulls numbers, durations, currency figures and entity names out
  of the generated answer.
- **Entity indexing** — `EntityIndex` reads the programme catalogue out of the corpus
  itself, so "does this programme exist?" is answered by the knowledge base rather than
  by a hard-coded list. It reads `{programme: school}` from the corpus with no curation.
- **Corpus judgement** — a name is judged by whether the corpus contains it, not by
  whether it is capitalised (a lower-case "our mechanical engineering program" is still a
  claim).
- **School-consistency rule** — a degree claimed in another school's subject
  ("Master of Science **in Computer Science**", where Computer Science is a different
  school from Science) is treated as invented. This catches the model's habit of
  *recombining* two real names into a plausible third.
- **Fail-open on absent evidence** — when the boundary has no evidence to check against,
  it declines to judge rather than condemning everything. This is a hard-won lesson: an
  earlier version failed closed and refused correct answers in production (see §11).

### 3.3 Caller interruption (barge-in)

Implemented as **US-014** and controlled by the flag `BARGE_IN_ENABLED` — which is set to
**`1` in the deployed `.env`**, so caller interruption is live. (The code default is 0.
This gap between code default and live value is the project's signature defect class —
see §8.)

The carrier's media stream has no echo cancellation, so the agent's own speech returns to
the microphone. The system therefore needs a gate that can distinguish the caller's voice
from the agent's echo. That gate is the **single inbound-frame decision**
(`VoiceCallSession.admit_inbound_frame`), which returns one of four verdicts:

| Verdict | Meaning |
|---|---|
| `muted` | The agent is speaking and the gate is off — the caller's frame is discarded |
| `echo` | The frame is the agent's own playback returning |
| `interruption` | The caller spoke above the echo — the agent stops, flushes the carrier buffer with `clear`, and the interrupting words become the next turn |
| `listening` | An ordinary caller frame |

The gate decides only when an interruption **starts**; end-of-turn remains a separate,
single decision (the 600 ms silence rule). With the flag off, the prompt is rewritten to
describe what the platform can actually do — an instruction to "yield immediately" would
otherwise describe a capability the running system does not have.

`BARGE_IN_ECHO_GAIN=0.5` is a **declared assumption, not a measurement** — the carrier's
acoustic return path cannot be measured locally.

### 3.4 Admission control — refusing a third caller

Implemented as **US-016**. The system is sized for two concurrent voice calls. When a
third call arrives, it is **refused at the carrier-facing endpoint**, not queued:

- the busy TwiML plays a **pre-synthesised** asset and connects **no media stream**;
- therefore no session, no conversation history and no KV-cache allocation is created;
- the refusal costs no model and no synthesiser — which is exactly what lets it hold when
  those are the components that failed.

A queue was considered and explicitly rejected: a queue would hold a caller who has
already been answered by the carrier, with no way to tell them why they are waiting.

The ceiling is `MAX_CONCURRENT_CALLS` (**.env: 2**) and the switch is `ADMISSION_ENABLED`
(**.env: true**). A refused caller who redials into a freed slot is admitted as a fresh
session. Outcomes are tracked on **separate axes** — `served` / `degraded` / `failed` /
`refused` — so a degraded turn is never reported as a served one, or a refusal as a
failure.

Independently of admission, every turn carries a **30-second backstop timeout**
(`TURN_TIMEOUT_S`), derived from measurement: live p95 first-audio was 7.8 s and the worst
of 3,781 traced turns was 27.7 s. On timeout the turn is cancelled, the carrier buffer is
flushed, and a fixed response is spoken rather than leaving silence.

### 3.5 Background-work priority

Implemented as **US-017**. The realistic peak for this box is two voice calls **plus**
one background chat/admin request. Background work is held off while any caller's turn is
in flight and runs at most one at a time, so the class of work with no human waiting on a
live audio stream degrades before anything a caller can hear.

### 3.6 Web chat

A **Streamlit** chat UI (port `8501`) provides a text chat interface with the same RAG
pipeline. It supports typed questions and browser-microphone recording. It is the
development and demo surface, and the admin UI.

### 3.7 WhatsApp and messaging

A Twilio WhatsApp webhook (`POST /twilio/whatsapp`) answers messages through the same RAG
pipeline, with a multi-step conversation state machine: name → email → programme →
admission intent → RAG answer → ACCEPT/DECLINE → done.

**What each media type does today:**

| Media type | Behaviour |
|---|---|
| Text | Full RAG answer, through the same retrieval and grounding path as voice |
| **Audio / voice notes** | **Explicitly rejected** with a canned reply: "I can't listen to voice notes. Please type your question here" |
| Images and PDFs | Accepted, downloaded from Twilio, saved under `data/documents/{lead_id}/`, recorded against the lead, and mirrored to the CRM |

There is **no SMS channel** — Twilio is used for voice and WhatsApp only.

WhatsApp is the channel where **offer letters are delivered**, so it depends on the
Cloudflare tunnel in two ways: Twilio must reach the webhook, and it must be able to
*fetch* the offer PDF over public HTTPS. If the tunnel host resolves to `localhost`, the
WhatsApp leg of offer delivery is skipped and the student receives email only.

### 3.8 Outbound calling

The system can place outbound calls, not only receive them. The voice path carries a
direction flag, and outbound calls run the same turn pipeline with outbound framing. A
`follow_ups` table records scheduled callbacks.

### 3.9 Lead capture and CRM synchronisation

Runs **strictly post-call, off the hot path** — this isolation is deliberate and is what
keeps CRM failure out of the caller's turn.

1. At call end, the transcript is sent to an LLM with a structured-JSON extraction prompt
   for name, email and programme of interest.
2. The lead is **upserted by phone number** — a returning caller updates, never duplicates.
3. Fields are persisted to PostgreSQL.
4. Sync to the Salesforce-compatible CRM is attempted as a fire-and-forget background task.

The CRM integration is engineered to be **fail-safe toward the caller**: timeouts are 2 s
connect / 5 s read, sized so a slow CRM gives up rather than delaying a live phone call.
A circuit breaker (`CRM_BREAKER_THRESHOLD=5`, 60 s reset) protects against a persistently
failing CRM, and an **outbox** holds *transient* failures — permanent failures are dropped,
because a deleted record is permanent.

**The CRM is an external service, not a library.** The code in `app/crm/` is a client for
a separate "Salesforce Admission API" whose own source is **not in this repository** —
only an OpenAPI snapshot (`salesforceAPISchema`), the plans under `doc/salesforce/`, and
three live-verification scripts. It is consumed at `http://127.0.0.1:8098` under what the
code calls **a frozen external contract**.

**Auth model.** This is *not* a connected-app OAuth integration — there is no Salesforce
OAuth, JWT or username/password flow anywhere in `app/crm/`. Authentication is an optional
static header (`X-API-Key`), sent only when `CRM_API_KEY` is non-empty, with the code
comment noting the API has no auth today and this is where a shared secret would go.

**What is written.** Not raw Salesforce `Lead`/`Contact`/`Case`, but a `Customer` object
fronted by that API. Profile writes go through a **strict allowlist of ten fields**
(`Course__c`, `Conversation_ID__c`, `Sentiment__c`, `Lead_Category__c`, `Offer_Status__c`,
`Offer_Sent_At__c`, `Offer_Letter_Released__c`, `Offer_Letter_Accepted__c`,
`Admission_Status__c`, `Testing_Record__c`); a key outside the allowlist raises, and an
explicit `null` is dropped rather than sent, because on that route a null **clears** the
field. Values are mapped through enumerated picklist vocabularies, and anything
out-of-vocabulary maps to `None` rather than sending a doomed write. Documents upload in
three chunked calls. Several upstream API defects are documented and defended against in
the client.

`CRM_ENABLED` is the master switch; with it off, every entry point returns before touching
the network — that is deliberate "silently disabled" behaviour, and one of the §8 traps.

### 3.10 Offers and documents

An offers subsystem (`app/offers/`) generates offer letters as PDFs (via `fpdf2`), stores
them under `data/offers/`, and delivers them by SMTP email. Uploaded documents are stored
under `data/documents/`, keyed by UUID.

### 3.11 Sentiment analysis and lead scoring

`app/sentiment/` categorises caller sentiment and conversation outcomes using a hybrid
approach: a deterministic rule engine (`rules-v1`) plus LLM extraction. Output feeds lead
scoring, so counsellors can prioritise.

### 3.12 Dashboards and front ends

There are **three independent front ends**, which is worth stating explicitly because
"the dashboard" is ambiguous in this repository:

| Front end | Entry point | Port / route | What it is |
|---|---|---|---|
| **Streamlit chat UI** | `app.py` | `8501` | The student-facing chat interface with browser-microphone recording |
| **Streamlit admin dashboard** | `dashboard.py` → `app/dashboard/*` | `8502` | Operator UI: Overview (KPIs + sentiment counters), Leads (filter, search, add, score, documents, offers), Conversations (transcript viewer), Scheduler (follow-ups), Courses (CRUD) |
| **FastAPI-served command cockpit** | `app/static/dashboard.html` + `dashboard.js` | `/dashboard` | Single-page live cockpit: stat cards, live-call monitor, pipeline board, lead table, demo-data controls |

The Streamlit dashboard never imports the application — it talks to the FastAPI backend
over REST. The cockpit polls `/api/calls/live` rather than streaming it (a deliberate
change, per the commit history).

### 3.13 MCP server

`app/mcp/` exposes lead tools to external agents over the Model Context Protocol, served
at `/mcp/sse`. It is **optional** — the `mcp` package is not required for the voice, chat
or UI paths.

### 3.14 Evaluation harness

A **161-case frozen golden set** (`eval/golden_set.jsonl`) with `tuned` / held-out splits,
per-intent coverage, and per-case ground truth traced to a named knowledge-base section.

`eval/checks.py` scores a candidate's answers with **deterministic, model-free checks** —
required facts present, forbidden claims absent, forbidden regexes (e.g. "any invented
dollar figure"), and spoken-format rules (no markdown, no lists, sentence/word caps). It
imports nothing from `app.*`, so the evaluator cannot fail because the application is
mid-refactor.

Exit codes: `0` clean · `1` fixture error · `2` a FAIL · `3` at least one **BLOCKED**
(ground truth missing or unapproved). **Exit code 3 is the designed behaviour today.**
Counted from the file: **128 cases `PENDING_PO_SIGNOFF`, 24 `verified`, 9 `PO_APPROVED`**
out of 161 — and only a `PO_APPROVED` case can clear a critical-intent block.

The set also carries a **frozen held-out split** (`heldout.txt`, ~20%, taken as every
fifth case id in sorted order so it is reproducible rather than chosen), which is never
used for tuning.

A self-test gives every scoreable case a synthesised correct answer (must pass) and two
mutations — a forbidden claim and a markdown list (must fail) — which is the property that
a well-formatted but factually wrong answer cannot pass.

**The runner is half-built, and the project says so.** The deterministic stage exists; the
rubric/judge stage does not, and the documented run-record store (`eval/runs/`) is not
implemented.

---

## 4. How it works

### 4.1 The voice turn, end to end

**Three surfaces exist, and they are not the same code.** This distinction matters,
because conflating them is the easiest mistake to make about this repository:

| Surface | Endpoint | What it is |
|---|---|---|
| **The live call path** | `/ws/twilio` (+ `/ws/twilio-outbound`) | `VoiceCallSession` in `app/voice_handler.py` — a hand-written Twilio Media Streams handler. **This is the system.** |
| Pipecat assembly path | `/ws/voice/text` | `app/pipeline.py` — a Pipecat 1.6.0 pipeline used for the text test surface and by shared helpers (`build_rag_prompt_parts`, `post_call_handler`) |
| Browser-audio stub | `/ws/voice` | An **echo stub** (`response = data`), kept for transport validation. `app/audio_format.py` documents the 16 kHz raw-PCM format it would speak. |

The carrier leg is text JSON frames over a WebSocket, carrying 8 kHz mono **µ-law**
base64-encoded in `media.payload`, at **20 ms per frame** (160 samples / 160 bytes).
Internally audio is converted to 16 kHz mono float for Whisper, and Kokoro's ~24 kHz
output is resampled back down to 8 kHz and re-framed for the carrier. Outbound audio is
written frame by frame, and a flush is a separate `{"event":"clear"}` so an interrupting
caller does not keep hearing buffered speech.

The receive loop **never blocks**: each turn runs as its own `asyncio.Task`, so audio
continues to be read (and an interruption can still be heard) while the previous turn is
generating or speaking.

```
Caller speaks
    │
    ▼
Carrier WebSocket — 20 ms µ-law frames, 8 kHz
    │
    ▼
One inbound-frame decision (admit_inbound_frame)
    │   muted / echo / interruption / listening
    ▼
End-of-speech: 600 ms sustained trailing silence (RMS energy gate)
    │
    ▼
Transcribe — faster-whisper small.en, CUDA int8
    │
    ▼
Noise & closing gate  ──► fixed reply, model never invoked
    │
    ▼
Retrieve — hybrid dense + BM25, RRF fusion, k=5, fetch_k=20
    │   MCP rung (:8010)  ──fallback──►  local Chroma rung
    ▼
Assemble prompt — static instructions + retrieved context + last 6 history turns + question
    │
    ▼
Generate — Ollama, llama3.2:3b, non-streaming by default
    │
    ▼
Grounding boundary — claims checked against the turn's retrieved corpus
    │
    ▼
Synthesise — Kokoro ONNX, CUDA (CPU fallback)
    │
    ▼
Resample 24 kHz → 8 kHz, chunk, write frames to carrier
    │
    ▼
Caller hears the reply
```

**Measured stage costs on this box** (from `doc/perf/`, `[Measured]`): prefill 146 ms,
generation 184 ms, TTS 797–1,390 ms at N=2. The 600 ms endpointing floor is paid before
any of that runs — it alone is 86% of the original 700 ms target.

### 4.2 Retrieval and grounding

Retrieval turns a question into grounded context, and decides when the context is not good
enough to answer from.

- **The dispatcher** (`app/rag.py`) chooses the rung per call from `USE_MCP_RAG`
  (**.env: `auto`**): `auto` prefers the MCP service and falls back. The breaker decision
  is made **before** the call, not after a failure.
- **The primary rung** is `enterprise-rag-core` (a separate package at
  `D:\project\enterprise-rag-core`, reached through the client in `app/rag_mcp.py`) served
  over HTTP JSON-RPC on `:8010`. Its collection is `meridian_kb__v1` — **1,144 documents**:
  71 whole cards plus 1,073 question keys, with **no text splitter at all**. It performs
  hybrid retrieval: a dense Chroma leg and a BM25 leg, fused by **weighted Reciprocal Rank
  Fusion** (α = 0.3 dense / 0.7 sparse, `rrf_k = 60`), with a key-boost term that requires
  sparse corroboration.
- **The fallback rung** is a local Chroma store (`app/rag_legacy.py`), collection
  `langchain`, **35 chunks** at 768 dimensions in cosine space. Its hybrid search is
  **hand-written** — dense Chroma plus a stdlib `_MiniBM25` (k1=1.5, b=0.75) over the whole
  corpus, RRF-fused — because chromadb 1.5.9's native search API raises
  `NotImplementedError` on the local persistent client.
- **Ingestion** is owned exclusively by `scripts/rebuild_rag_index.py`, never by the
  runtime. It refuses to build into a non-empty directory without `FORCE=1`, then runs
  positive smoke queries, a **negative canary** ("What is the Terrapin Commitment?" — a
  UMD question that must return nothing), and a store-wide scan for blocked markers.
- **A circuit breaker** with a bounded probe protects the primary rung: one admission point
  (`rag_mcp.admit_primary()`) routes all primary call sites, with three states
  (`closed`/`open`/`half_open`) and **exactly one probe per cooldown window**, so an outage
  costs one trip rather than a full timeout on every turn.

The two rungs are a known correctness hazard, and the analysis found it is worse than
"different chunks": **the two stores are not in the same embedding regime.** The ERC store
applies nomic task prefixes (`search_document:` / `search_query:`) on both sides; the local
path applies **none**. For identical text that is a cosine difference of roughly 0.21 —
material, not cosmetic. Meanwhile the repo's own parity check compares **model name and
dimension only**, so it would pass while the regime differs. The two stores are also not
chunk-equivalent (35 concatenated 600-character chunks versus 1,144 whole-card documents),
so consolidation remains an open item.

**Live retrieval parameters** (RAG-Baseline, recorded in `eval/rag_baseline.json` with a
corpus hash): `top_k = 5`, `fetch_k = 20` on the local rung (the MCP rung fetches
`top_k × 2`), chunking 600/90, `RAG_SEARCH_MODE=hybrid`,
`RAG_SIMILARITY_THRESHOLD=0.0` — **the relevance gate is disabled**, and deliberately so:
measured score distributions overlap between valid and off-topic questions (a valid
"hostel fee?" at 0.35 versus an off-topic "weather?" at 0.32), so a global threshold would
reject valid questions. The system relies on the system prompt and the output grounding
boundary instead.

The relevance-floor work (`US-009`) that would replace it is **not implemented**. What
exists is the naive gate, and it is the exact anti-pattern the story names: enabling it
issues a **second retrieval call at `top_k=1`**, doubling per-turn retrieval traffic.

**No reranking and no metadata filtering are in force** on either rung. The cross-encoder
reranker exists on disk (23.2 MB INT8 ONNX) but the frozen configuration records it as
`off`, and the retrieval sweep is why: it *reduced* key recall@1 from 1.000 to 0.730. MMR
is used only on the local non-hybrid branch, never on the primary path.

### 4.3 Inference serving and prompt assembly

- **The model is Ollama-served**, called over local HTTP on `:11434`. A local HTTP boundary
  is what makes a model swap a configuration change rather than a code change.
- **`pick_model()` resolves in order:** exact match against a preference list that begins
  with `OLLAMA_MODEL` (so the configured model wins whenever it is installed) → the first
  installed tag containing `qwen` → `OLLAMA_MODEL` regardless.
- **Prompt structure matters as much as content.** The static prefix is ~3,050 tokens and
  the `{context}` insertion point sits at line 600 of `app/voice_system_prompt.py`.
  Measured: identical repeated prompts go from 2,909 ms to 50 ms with a prefix-cache hit,
  and the per-turn cost is everything from `{context}` onward — which is why prompt
  ordering is treated as a first-class latency lever.
- **`num_ctx` must not be varied.** Four different context sizes across call sites forced
  Ollama to rebuild the runner and wipe the KV cache on nearly every switch. Pinning every
  chat call to `SMALL_TASK_NUM_CTX=8192` reduced warm `load_ms` from 6,133 ms to 4.7 ms.
- **Streaming is implemented and enabled in the deployed configuration.** `LLM_STREAM=1`
  and `TTS_STREAM=1` in the live `.env`, and both were measured before being turned on
  (see §10: over-cap turns 27% → 6%). The LLM stream runs a blocking `ollama.chat(stream=True)`
  in a daemon thread and crosses deltas to the event loop via `call_soon_threadsafe`; text
  is cut into speakable clauses so synthesis can begin before generation finishes.
  **Formal adoption remains gated** on the frozen golden-set quality sign-off — the flags
  being on is a deployment state, not a closed quality decision.
- **TTS streams per sentence, not per answer.** Kokoro's own `create_stream` batches a
  typical short answer into a single chunk, so streaming the whole answer would buy
  nothing; the answer is split at sentence boundaries and each sentence is streamed.
- **Instruction role.** The prompt is delivered as a `system` message. This was a
  significant fix: with the entire prompt sent as a `user` message, the model read it as a
  role-play brief — reciting the instructions back to the caller, narrating its own
  compliance with style rules, and announcing "Here's a response to the student's
  question". Moving instructions to the `system` role eliminated all three defect classes
  (measured 109/114 → 112/114 clean over 38 real caller turns, and verified on a live
  call).

### 4.4 Speech services

| Stage | Engine | Detail |
|---|---|---|
| **STT** | `faster-whisper` `small.en` | CTranslate2, CUDA int8. English-only is more accurate per unit of compute at this size, and STT latency sits directly inside the caller's pause. Its internal VAD runs *after* endpointing and cannot delay turn detection. |
| **Endpointing** | RMS energy counter | **Deterministic, no model.** 600 ms of sustained trailing silence, or a max-utterance cap. This is the single live end-of-speech decision. |
| **TTS** | Kokoro v1.0 ONNX, voice `af_heart` | `onnxruntime`, CUDA execution provider with CPU fallback. Synthesis is the dominant cost of a two-caller turn. |
| **TTS cache** | In-process, keyed by `(text, voice, speed)` | Scope is set by `TTS_CACHE_SCOPE`; the deployed `.env` sets **`per_call`**, so each session has its own cache. Keyed by text hash, never by caller speech or identity. |
| **Speculative STT** | Early transcription | `VAD_SPECULATIVE_ADVANCE_MS=220` starts transcription *before* the turn formally closes, and the result is consumed only if both the turn epoch **and** the exact audio bytes match. It does **not** create a second end-of-speech decision. |

The Kokoro execution provider is a **verified trap**: a coexisting CPU `onnxruntime` wheel
silently shadows the GPU build, so the app logs "CUDA GPU enabled" while synthesising on
CPU. When that happened, real-time factor was 0.63; after fixing it, **0.061** — roughly
10× faster, and ~12× on the N=2 term.

### 4.5 Post-call pipeline

Call ends → transcript persisted to PostgreSQL → lead extracted → lead upserted by phone →
CRM sync attempted → sentiment/outcome categorised → follow-up scheduled if the caller
asked for one. All of this is off the hot path and unconstrained by latency.

### 4.6 Concurrency model

Two callers, one GPU. The requirement is decomposed three ways, because "caller B
unaffected by caller A" is not achievable on one shared GPU and would not be a testable
requirement:

| Dimension | Requirement | Nature |
|---|---|---|
| **Isolation** | No caller receives another's history, context, prompt, cached audio or session metadata | **Absolute** — binary, zero tolerance |
| **Performance interference** | p95 at N=2 ≤ 1.5× p95 at N=1 per caller; no turn > 3 s | Bounded degradation |
| **Resource interference** | No OOM, no sysmem spill, no core pinned by the event loop, no caller starved in a queue | Threshold |

Two predictions were **measured and falsified**: VRAM peaks at **26–69%** at N=2, not the
~90% projected, and per-stream decode does not fall at all (39.2 → 38.6 tok/s on the 14B)
because the engine **batches** rather than serialises. GPU memory is therefore *not* the
binding constraint; retrieval is.

### 4.7 Failure and degradation

"Polite spoken fallback" is not achievable for every failure — if TTS itself failed,
nothing can speak. The system is therefore specified as a **failure-capability matrix**
rather than an absolute guarantee:

| Failed component | Fallback | Can still speak? |
|---|---|---|
| Retrieval (MCP) | Local Chroma store, then keyword-only | Yes |
| Vector store | Keyword-only retrieval; ungrounded answer with disclosure | Yes |
| Inference engine | Deterministic fixed response | Yes |
| Speech synthesis | Alternate execution provider (CPU), then pre-synthesised critical audio | Only via pre-synthesised audio |
| Speech recognition | Fixed clarification prompt | Yes |
| LLM **and** TTS | Pre-synthesised critical audio only; otherwise clean termination | Only pre-synthesised |
| Database / cache | Continue the call; queue persistence | Yes |
| CRM | Local outbox; retry | Yes |
| Carrier / whole app | Call termination | No |

The **degradation ladder** is: (1) retrieval degrades to keyword-only → (2) to local store
→ (3) TTS degrades to CPU → (4) generation queues → (5) new calls are refused with a
spoken message. Every rung has a named recovery.

---

## 5. Architecture

### 5.1 Module map

The system is decomposed into **seven modules**, which follow the code's existing seams
rather than imposing a new topology. Deployment independence buys nothing on one box —
what is needed is **fault and performance isolation**.

| Module | Responsibility |
|---|---|
| **MOD-01 Voice Turn Path** | Owns one caller's turn from end-of-speech to first audio frame, and the session lifecycle around it |
| **MOD-02 Retrieval & Grounding** | Turns a question into grounded context, and decides when context is not good enough |
| **MOD-03 Inference Serving & Prompt Assembly** | Assembles the prompt, holds the model resident, serves generation to all callers |
| **MOD-04 Speech Services** | Transcribes caller audio and synthesises agent speech |
| **MOD-05 Lead & CRM** | Captures, identifies and hands off a caller as a lead (post-call only) |
| **MOD-06 Observability & Evaluation** | Per-stage traces, load harness, frozen golden set. **Net-new, and deliberately dependency-free** |
| **MOD-07 Configuration & Boot** | A single honest source of runtime configuration, and a stack that is warm before the first call |

**MOD-06 is a best-effort event sink, not a runtime dependency.** Runtime modules *emit*
to it and proceed regardless of whether anyone is listening. The design rule is stated
plainly in the architecture record: *if MOD-06 is deleted entirely, every voice call still
works.* Every tracing method swallows exceptions, and a missing stage is reported as
*absent*, never as a zero — a sink that can fabricate a measurement is worse than no sink.

### 5.2 Orchestration strategy

**Synchronous in-process orchestration on the turn path, with post-call work decoupled to
background tasks.**

The caller is waiting on a live audio stream, so the interaction is inherently
request/response and every added hop lands directly in a latency budget that measurement
shows is already tight. The architecture keeps the hot path synchronous and same-process,
and pushes lead extraction and CRM sync off it. What the hot path was missing was not a
different orchestration pattern but **streaming, bounded timeouts, and cancellability
within the synchronous model**.

Rejected alternatives, with reasons: microservices (adds hops to a latency-bound path and
the box cannot host more processes); event-driven choreography (a broker hop obscures
per-stage timing); saga (no distributed transaction exists); workflow engine (operational
overhead for a 2-caller system).

### 5.3 Services and ports

| Service | Port | Protocol / role |
|---|---|---|
| FastAPI backend | `8000` | `/ws/twilio`, `/ws/voice`, `/api/*`, `/mcp/sse` — the application |
| PostgreSQL 16 + pgvector | `5432` | System of record: leads, calls, offers, documents (Docker: `elearning-postgres`) |
| Streamlit dashboard | `8501` | Admin / chat UI |
| Streamlit dashboard (2nd) | `8502` | Second UI surface |
| enterprise-rag-core (MCP) | `8010` | Streamable HTTP JSON-RPC — primary retrieval |
| Salesforce admission API (CRM) | `8098` | Frozen contract, loopback dev deployment |
| Ollama | `11434` | LLM + embeddings |
| MLX server | `1234` | macOS Apple Silicon alternative LLM backend |
| Redis Stack | `6379` | Docker; cache/session support |
| Cloudflare tunnel | — | Exposes the local FastAPI server to Twilio |

Entry points: `/voice` (browser microphone), `/call` (quick call), `/dashboard` (admin),
`/ws/twilio` (carrier media stream). Twilio reaches the app through a Cloudflare tunnel
whose hostname is resolved by `app/config.py::tunnel_host()`.

### 5.4 Key architectural decisions

| Decision | Rationale |
|---|---|
| Keep one deployable, split by responsibility not deployability | One box, co-residency required for latency; the need is fault/perf isolation |
| MOD-06 dependency-free and built first | Every other change is only adoptable once it can be measured; tracing that can fail a call is worse than no tracing |
| Retrieval is its own module | It is the named 2-caller bottleneck; it must be fixable without touching turn logic |
| Lead/CRM isolated and lowest priority | Post-call and latency-unconstrained; isolating it keeps CRM failure out of the call path |
| Config/boot is a module, not a script | Two writers were producing divergent values silently |
| Build vs buy: bundled JSONL tracer, bundled harness, local judge | Correct at this scale; OpenTelemetry/Prometheus over-engineered for one box and two callers, and hosted eval is prohibited on residency grounds |

---

## 6. Data and knowledge base

### 6.1 The knowledge base

`content/meridian/meridian_knowledge_base.md` — a 15.6 KB structured markdown file with
**14 `##` domain sections and 71 `###` entries**:

| Domain section | Example section ids |
|---|---|
| Admission Overview | `apply-steps`, `decision-timeline`, `documents`, `international`, `ug-eligibility`, `pg-eligibility`, `phd-apply` |
| Fees Structure | `tuition-range`, `application-fee`, `hostel-fee`, `exam-fee`, `refund-policy`, `payment-plans` |
| Important Dates | `intakes`, … |
| Doctoral Programs | `phd-eng-comp`, `phd-bus-econ`, `phd-nat-sci`, `phd-humanities` |
| Frequently Asked Questions | `faq-cs-programs`, `faq-program-families`, `curriculum-detail` |
| Campus Facilities | `facilities`, `transport`, `relocation`, `campus-visit` |
| Contact Information | `contact` |

Those `###` ids are load-bearing: the golden set's `ground_truth_source` cites them, and
the grounding boundary's `[§ section]` labels come from them. **A knowledge-base edit
silently changes ground truth** — the KB is unversioned, which the eval README records as
a known limitation.

> ### ⚠ The corpus is dummy data
>
> The knowledge base header states it plainly: **"Dummy data — replace with real
> institutional data before production use."** The ERC manifest carries the same marker
> (`"data_status": "dummy"`), and the source material is a synthetic "Meridian University"
> reference document. **The fees, deadlines, programme catalogue, addresses and phone
> numbers are invented for demonstration.** They are presented here as the system's
> content, not as institutional fact.

**Provenance.** The original source is a 12-page PDF, and **pages 8–12 belong to a
different institution** (US financial-aid and housing material). The corpus was rebuilt on
2026-09-20 from 71 structured "cards" (18 program / 30 fact / 14 FAQ / 9 unknown), and the
ingestion gate actively blocks the earlier contamination: the build validates that
`meridian university` is present and **aborts** if any of a blocked-marker list appears —
including `university of maryland`, `fairleigh dickinson`, `umd`, `fdu`, `terrapin`,
`fafsa`, and the OCR variants `maradian`/`marradian` that appeared in the contaminated PDF.

Legacy sample material (`content/sample_data/`, the UMD and FDU profile PDF/DOCX) remains
in the repository from the project's earlier scope, but is **not ingested**.

### 6.2 Entity and state model

| Entity | States |
|---|---|
| **Call session** | `OPENING → GREETING → LISTENING ⇄ PROCESSING → SPEAKING → … → CLOSING → ENDED` |
| **Turn** | `ACCUMULATING → ENDPOINTED → TRANSCRIBED → GATED → RETRIEVING → GENERATING → SYNTHESISING → SENDING → EMITTED` |
| **Lead** | `UNSEEN → PARTIAL → IDENTIFIED → HANDOFF_PENDING → HANDED_OFF` |

Notable properties: calls have **no resume** — a dropped call ends and the caller redials
into a fresh session. Turn cancellation was impossible on the batch path and became
possible under streaming. Lead upsert by phone is idempotent. A `HANDOFF_PENDING` lead
never ages out (a recorded gap).

### 6.3 Storage

**PostgreSQL 16 + pgvector** is the system of record, reached through `psycopg2`
(synchronous, called via `asyncio.to_thread` where latency matters). **Migrations are
inline, idempotent, and run on every startup** — there is no Alembic or Flyway. The CRM
schema deliberately runs last because it `ALTER`s tables the offers schema creates.

| Table | Holds |
|---|---|
| `leads` | The caller: phone, name, email, programme interest, status, call attempts, follow-up dates, and denormalised sentiment columns |
| `conversations` | One row per interaction, with the **full turn-by-turn transcript** and the extracted lead JSON |
| `lead_calls` | Legacy call record (superset: transcript + extracted lead) |
| `call_queue` | The serialised outbound call queue: status, CallSid, schedule, error |
| `follow_ups` | Scheduled callbacks and their type |
| `courses` | Programme catalogue: duration, fees, intake, payment link |
| `lead_documents` | Uploaded files (WhatsApp media, web uploads) with storage path and CRM upload status |
| `offer_letters` | Generated offers: programme, PDF path, validity, delivery channel, response |
| `sentiment_scores` | Per-transcript sentiment extraction, composite score, category, decision trace |
| `crm_sync_outbox` | Durable retry queue for CRM writes that failed transiently |

If the database is unavailable at startup the app **runs without a database** rather than
refusing to start, and every consumer yields `None` instead of raising.

**Non-database state** (deliberately in-process): the live-call registry, the CRM
conversation-session registry, and the work-priority gate.

**Files**: `data/offers/` (generated offer PDFs), `data/documents/{lead_id}/` (uploads,
UUID-keyed), `logs/perf_turns.jsonl` (turn traces), `chroma_local_db/` (vectors).

---

## 7. Technology stack

Pinned in `requirements.txt` with exact versions (`# Pinned 2026-08-15 — exact versions
from the verified-working environment`).

### 7.1 Web, API and UI

| Technology | Version | Role |
|---|---|---|
| FastAPI | 0.140.0 | HTTP + WebSocket application |
| Uvicorn | 0.51.0 | ASGI server (single worker, by design) |
| Streamlit | 1.59.2 | Chat UI and dashboards |
| websockets | 15.0.1 | Carrier media streams |
| httpx | 0.28.1 | HTTP client (Ollama, MCP) |
| python-multipart | 0.0.32 | Form/file uploads |
| requests | 2.34.2 | Sync HTTP |
| python-dotenv | 1.2.2 | Configuration loading |

### 7.2 ML and audio

| Technology | Version | Role |
|---|---|---|
| torch | ≥2.7.0,<2.8 | Tensor runtime |
| onnxruntime-gpu | 1.28.0 | Kokoro TTS execution (CUDA) |
| onnxruntime | 1.24.3 | macOS build only (`onnxruntime-gpu` has no macOS wheels) |
| numpy | 2.4.6 | Audio buffers |
| scipy | 1.17.1 | Signal processing |
| soundfile | 0.14.0 | Audio I/O |
| faster-whisper | 1.2.1 | **Speech-to-text** (CTranslate2) |
| kokoro-onnx | 0.5.0 | **Text-to-speech** |
| openai-whisper | 20250625 | Present; not the live STT path |
| pipecat-ai[whisper,kokoro] | 1.6.0 | Pinned and installed but **bypassed on the live path** |

> **GPU note from the pin file:** `torch>=2.7.0,<2.8` on PyPI is the CPU build. On Windows
> NVIDIA boxes, `bootstrap_services.py` installs `torch==2.7.1+cu128` from the PyTorch
> CUDA wheel index first — **required** for RTX 50-series (Blackwell, sm_120) GPUs, which
> torch 2.6.x/cu124 cannot use.

### 7.3 RAG and LLM

| Technology | Version | Role |
|---|---|---|
| ollama (python client) | 0.6.2 | LLM + embeddings client |
| chromadb | 1.5.9 | Vector store |
| langchain | 1.3.14 | Chain/loader framework |
| langchain-community | 0.4.2 | Loaders (PDF/DOCX) |
| langchain-text-splitters | 1.1.2 | Chunking |
| langchain-ollama | 1.1.0 | Ollama integration |
| langchain-classic | 1.0.8 | Legacy chains |
| pypdf | 6.14.2 | PDF loading |
| pandas | 3.0.3 | Data handling |
| pyarrow | 24.0.0 | Columnar interchange |

**macOS Apple Silicon path** (replaces Ollama for LLM): `mlx-lm` 0.31.3, `mlx-whisper`
0.4.3, plus `langchain-openai` / `openai` for the MLX server's OpenAI-compatible endpoint.

### 7.4 Data and integrations

| Technology | Version | Role |
|---|---|---|
| psycopg2-binary | 2.9.12 | PostgreSQL driver |
| twilio | 9.10.9 | Telephony, media streams, WhatsApp |
| fpdf2 | 2.8.7 | Offer-letter PDF generation |
| mcp | 2.0.0 | MCP server (optional) |

### 7.5 Models

| Model | Size | Role |
|---|---|---|
| `llama3.2:3b` | 2.02 GB | **The live answering LLM** — voice, chat, WhatsApp, lead extraction, sentiment |
| `qwen2.5:14b` | 8.99 GB | Installed, GPU-tier preferred; not selected by the live voice path |
| `nomic-embed-text` | 0.27 GB | Embeddings, 768-dim — used for both ingest and query |
| `faster-whisper small.en` | — | Speech-to-text (CUDA int8) |
| Kokoro `kokoro-v1.0.onnx` + `voices-v1.0.bin` | — | TTS, voice `af_heart` |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` (INT8 ONNX, 23.2 MB) | — | Reranker — shipped but **switched off** (the sweep measured it reducing recall) |
| `mlx-community/Qwen2.5-14B-Instruct-4bit` | — | macOS alternative |

**Why a 3B model answers a live call:** a phone call needs bounded latency far more than
maximum reasoning depth, and the answers are grounded in retrieved context — the model's
job is to phrase what it was given, not to know it. The 192-token output ceiling
(`OLLAMA_NUM_PREDICT=192`) is derived from the measured answer-length distribution, and
truncates 0 of 83 answers on either model.

**Model selection is an evidence-driven decision, not a preference.** A gated comparison
(`US-015`) against named alternatives is the standing rule: no architecture component is
final until it has passed a comparative evaluation against alternatives or been eliminated
by a documented feasibility constraint.

---

## 8. Configuration

`app/config.py` is the single source of runtime configuration, and `app/config_truth.py`
enforces honesty about it. The governing rule for the whole project is:

> **"Set ≠ live" is the single most reliable defect class in this codebase.**

Four configuration keys were once written and never read (`FASTAPI_WORKERS`,
`KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL`); `OLLAMA_KEEP_ALIVE` was set nowhere while the
pre-warm it was supposed to support existed and ran; and two writers (`.env` and
`.machine_profile.json`) produced divergent values with no reconciliation. All of these
have been addressed, and configuration provenance is now computed and reported — 122 rows
with a reported origin, which is what surfaced three real `.env`-versus-artifact
divergences.

Significant configuration groups in `.env` / `.env.example`:

| Group | Representative keys | Purpose |
|---|---|---|
| Service endpoints | `OLLAMA_URL`, `MLX_BASE_URL`, `MLX_PORT`, `FASTAPI_PORT`, `STREAMLIT_PORT`, `DATABASE_URL`, `RAG_MCP_URL`, `CRM_BASE_URL` | Where each service lives |
| Model selection | `OLLAMA_MODEL`, `SMALL_TASK_NUM_CTX`, `OLLAMA_NUM_PREDICT` | What answers, with how much context |
| Retrieval | `USE_MCP_RAG`, `TOP_K`, `FETCH_K`, `RAG_SEARCH_MODE`, `RAG_SIMILARITY_THRESHOLD`, `RAG_MAX_CONTEXT_CHARS`, `CHROMA_DB_PATH` | Retrieval rung and behaviour |
| Speech | `WHISPER_MODEL`, `WHISPER_NUM_THREADS`, `KOKORO_*`, `MUTE_STT_DURING_TTS` | STT/TTS engines and the echo guard |
| Turn-taking | `BARGE_IN_ENABLED`, `BARGE_IN_ECHO_GAIN`, endpointing constants | Interruption behaviour |
| Streaming | `LLM_STREAM`, `TTS_STREAM` | The two measured-but-default-OFF latency levers |
| CRM | `CRM_ENABLED`, `CRM_BREAKER_THRESHOLD` | Salesforce integration and its breaker |
| Integrations | Twilio credentials, SMTP settings | Telephony and email |

> **Three traps worth naming, because each has cost real time on this project.**
>
> **1. `localhost` versus `127.0.0.1`.** `OLLAMA_URL=http://localhost:11434` costs roughly
> **2,066 ms per turn** on this box: `localhost` resolves to `::1` first, the connection
> fails slowly, and only then does it fall back to IPv4. The literal `127.0.0.1` connects
> in under a millisecond (measured: 2,034 ms vs 5 ms). The same trap applies to
> `DATABASE_URL` — where it also delayed uvicorn's bind by 21 seconds at startup. The
> readiness probe deliberately uses literal IPv4 for the same reason.
>
> **2. Boolean flags do not coerce consistently.** `MCP_ENABLED` and `CRM_ENABLED` accept
> **only** the literal `true`; `MUTE_STT_DURING_TTS` accepts **only** `1`; the other five
> flags (`TTS_STREAM`, `LLM_STREAM`, `GROUNDING_ENABLED`, `GROUNDING_NAMES_ENABLED`,
> `BARGE_IN_ENABLED`) accept `1`/`true`/`yes`/`on`. So `CRM_ENABLED=1` leaves the entire
> CRM integration **off**, silently — and `CRM_ENABLED`'s own code default is `false`.
>
> **3. `.env.example` and `app/config.py` disagree on two defaults.** `TTS_CACHE_SCOPE` is
> `shared` in code but `per_call` in the template (the template reflects the N=2 isolation
> decision); `RAG_SEARCH_MODE` is `mmr` in code but `hybrid` in the template. Reading only
> one of the two files gives the opposite answer.
>
> `OLLAMA_KEEP_ALIVE` is a further trap of a different kind: it is consumed **twice, by two
> different processes**, and the two are not the same setting. The `.env.example` file
> labels it `← THE TRAP`, and the measured 32,919 ms cold load came from exactly this
> confusion.

---

## 9. Operations

### 9.1 Boot

The stack is started by `start_services.ps1` (Windows) / `start_services.sh` (POSIX), with
`bootstrap_services.py` handling first-time environment setup and dependency installation.

Boot sequence: dependency services (Postgres, Redis via Docker) → Ollama → **model
preload** → FastAPI → Streamlit → tunnels → health verification.

**What "warm" means concretely — four clauses, all required** (`app/boot_readiness.py`):

1. **Services listening** — TCP connect to FastAPI, Ollama, ERC MCP and Postgres. Only
   FastAPI and Ollama block; MCP and Postgres are *reported* with the capability they
   degrade.
2. **Model resident, confirmed by the engine** — read from Ollama's own `/api/ps`
   (`size_vram`), never inferred.
3. **Prompt prefix warm, confirmed by the engine's counters** — the **real voice prompt**
   is submitted **twice** with the same `num_ctx`/`temperature`/`keep_alive` the serving
   path uses, and warmth means the second prefill collapsed (≤400 ms, or ≤50% of the
   first). Measured basis: cold prefill ≈2,900 ms, warm ≈50 ms.
4. **Every managed config key has a reader** — delegates to `config_truth`, and also
   fails on *malformed* values rather than falling back silently.

Two extra gates sit beyond the four: an NVIDIA card still reporting its **idle memory
clock** fails the residency clause ("the model is resident but the card is parked"), and
call-asset drift warns if the TTS voice or speed changed since the pre-synthesised
busy/refusal audio was built.

So "warm" is three separately-verified things: weights in VRAM, the **system-prompt prefix
already in the KV cache**, and the GPU not idle-clocked. Weight-residency alone was the
old, insufficient definition — the pre-warm used to send the literal string `"ping"`,
which loads weights and nothing else. The cold load this prevents is **32,919 ms**.

Exit codes `0` ready / `2` not ready, runnable as `python -m app.boot_readiness`.

### 9.2 Health

A `/health` endpoint reports service status, and boot readiness is verifiable
(`app/boot_readiness.py`). A half-started stack must be **detected and reported, never
silent**. Event-loop hygiene is enforced by a test
(`doc/perf/tools/test_event_loop_hygiene.py`) that asserts no blocking call runs on the
loop — `/health` went from a 2,300 ms worst case to a 23 ms maximum once the blocking
calls were removed.

### 9.3 Platform support

| Platform | LLM backend | STT | TTS |
|---|---|---|---|
| Windows (NVIDIA) | Ollama, CUDA | faster-whisper CUDA int8 | Kokoro CUDA |
| Linux | Ollama, CUDA | faster-whisper CUDA int8 | Kokoro CUDA |
| macOS (Apple Silicon) | **MLX** (`mlx_lm.server`), OpenAI-compatible | `mlx-whisper` / CPU float32 | Kokoro CPU (RTF ~0.6) |

`app/hardware_profile.py` detects the tier (GPU/VRAM/RAM/Apple Silicon) with a
first-match-wins table that writes ten managed keys into `.env`; `app/memory_budget.py`
documents per-platform memory budgets. Detection is **stdlib-only by design** (subprocess
probes rather than torch), because it runs before the virtualenv exists.

| Tier | Model chosen | Context | Whisper | RAG top-k / fetch-k |
|---|---|---|---|---|
| `nvidia_high` (≥12 GB VRAM) | **`llama3.2:3b`** | 8192 | `small.en` | 5 / 20 |
| `nvidia_mid` (6–11 GB) | `qwen2.5:7b-instruct-q3_K_M` | 4096 | `small.en` | 5 / 20 |
| `nvidia_low` (<6 GB) | `qwen2.5:3b` | 2048 | `base.en` | 3 / 12 |
| `apple_high` (≥32 GB) | MLX 14B-4bit | 8192 | `small.en` | 5 / 20 |
| `apple_low` (8–15 GB) | MLX **7B**-4bit | 4096 | `base.en` | 4 / 16 |
| `cpu_local` / `cloud_linux` | Ollama q3_K_M | 4096 | `base.en` | 4 / 16 |

Two things worth noting here. First, **the model choice is a measured decision, not a
heuristic**: the `nvidia_high` tier previously chose `qwen2.5:14b` on the assumption that a
bigger card should carry a bigger model. The US-015 screen found the 14B and the 3B miss
required facts *identically* (28 each, McNemar p = 1.00) while the 14B breaks the
two-sentence spoken limit 2.3× as often (p = 0.0002) — so the 3B was chosen. Second, the
`.env` merge only manages a **marker-wrapped block**; keys outside it are treated as
deliberate manual overrides.

### 9.4 Containers

`docker-compose.yml` defines five services — `postgres` (pgvector 16, container
`elearning-postgres`), `ollama`, `fastapi`, `streamlit` and `dashboard` — with health-gated
`depends_on` ordering. The `Dockerfile` is a multi-stage `python:3.11-slim` build.

On Windows, the compose file is used for **Postgres only**: running the compose `ollama`
container alongside the native Ollama application makes the two fight over port 11434.

### 9.5 Scale-out path — documented, not deployed

`doc/cloudDeployment/` describes a production plan that is **not what runs today**: vLLM
serving an AWQ 4-bit Qwen2.5-7B-Instruct on RunPod GPUs, behind a Cloudflare named tunnel,
justified by continuous batching and PagedAttention versus Ollama's sequential serving.
Today's deployment is Ollama on the local box, sized for two concurrent callers.

---

## 10. Quality engineering

### 10.1 Observability

Every voice turn emits a machine-readable trace to `logs/perf_turns.jsonl` (currently
~4,000 records) containing at minimum the elapsed time for **endpointing, transcription,
retrieval, generation, synthesis and first-audio-to-carrier**, plus the inference engine's
**own counters** (`prompt_eval_count`, `eval_count`, `load_duration`). Using the engine's
counters is what makes prefill and generation separable, and a residency lapse visible on
the trace rather than silent.

The traced stage marks, in order, are: `vad_end` → `stt_done` → `llm_sent` →
`retrieval_done` → `llm_first_token` → `llm_done` → `tts_first_chunk` → `tts_done` →
`first_audio_sent`. (Note that `retrieval_done` legitimately fires *after* `llm_sent` —
retrieval happens inside the blocking LLM call. Listing it earlier produced a negative
segment on the first real trace, and the ordering comment records that.)

Four contracts are enforced by construction: the tracer is **stdlib-only and imports
nothing from `app.*`**, so tracing can never fail an import; **absent ≠ zero**, so a stage
that did not run is absent from the record rather than reported as 0 ms; **no caller PII**
— no transcript text, no phone number, only the carrier's opaque stream id; and every
public method swallows its own exceptions. The active trace is a `ContextVar`, so each
carrier WebSocket task gets an isolated trace.

A structured call timeline (`EVENT <call_id> turn=<n> <NAME> key=value`) lets a whole call
be reconstructed from logs. There is **no Prometheus or OpenTelemetry exporter anywhere** —
this is a deliberate "correct at this scale" choice, not an omission.

**One canonical clock.** The headline metric is defined exactly once:

```
TURN_E2E_MS = first_audio_frame_written_to_carrier - caller_speech_end_reference
            = endpointing_ms + processing_ms
```

Every other artifact measures *processing* time from the end-of-speech trigger, which
excludes the ~600 ms endpointing floor. That is a legitimate sub-metric, but the two must
never be reported under the same name.

### 10.2 Measured performance

The project measures rather than estimates, and tags every figure with its evidence grade
(`[Measured]` vs `[Estimated]`/`[Predicted]`). Headline results on this box:

| Change | Before | After |
|---|---|---|
| `num_ctx` thrashing eliminated | warm turn 8,609 ms | **360 ms** (load_ms 6,133 → 4.7 ms) |
| Kokoro TTS moved CPU → CUDA | RTF 0.63; N=2 term 12,125–15,938 ms | **RTF 0.061**; N=2 term 797–1,390 ms |
| `localhost` IPv6 stall removed | ~2,066 ms per turn; LLM queue p50 2,521 ms | **~0 ms**; LLM queue p50 91 ms |
| MCP timeout retuned 2.5 → 6.0 s | p95 7,850 ms | **p95 5,538 ms** |
| Model swap 14B → 3B | prefill p50 604 ms, generation p50 990 ms | **146 ms / 184 ms**; VRAM 69% → 26% |
| Streaming LLM + TTS (now enabled in `.env`) | over-cap 27% | **6% over-cap** at p50 1,625 ms |
| Pre-warm with the real prompt | prefill 2,001 ms | **29 ms** |
| Event-loop blocking removed | `/health` max 2,300 ms | **max 23 ms** |
| Process hygiene (orphaned runners) | RAM 97% | **31%** |

**Live trace trend** over 3,626 real turns: p50 **5,141 → 2,063 ms**, and turns over the
3,000 ms cap **92.4% → 15.2%**.

### 10.3 Test suite
| | |
|---|---|
| Unit/integration tests | **48 files, ~630 test functions** under `tests/` |
| Largest group | CRM / Salesforce (14 files) — reflecting that the integration is the highest-risk external dependency |
| Also covered | Barge-in and interruption paths, grounding and entity rules, conversation replay, turn timeout, RAG dispatcher and MCP adapter, WhatsApp flows, machine profile, dashboard |
| Config | `pytest.ini` sets `testpaths = tests` so a bare `pytest` cannot collect the standalone gate scripts in `doc/perf/tools/` (which `sys.exit()` and would abort collection) |
| Gate scripts | **29 tools in `doc/perf/tools/`**, run directly rather than through pytest — the load harness, the per-story gates, the prefix-cache probe, and the documentation-truth checks |

Two honest notes: the suite is **not all green** — five tests need `pytest-asyncio`, which
is not installed (the venv has `anyio`) — and `tests/conftest.py` forces `USE_MCP_RAG=off`
"to keep the suite hermetic", which is exactly the habit that once caused the grounding
gates to validate the *local* retrieval rung while production ran the *MCP* one (see
BUG-28). The gates that matter for production now print the rung they resolved and fail if
it is not `auto`.

**There is no CI.** `.github/` contains only editor-assistant instruction files — no
workflows directory, nothing runs on push or pull request.

### 10.4 The honest position

- **The original target (p50 ≤ 700 ms, p95 ≤ 1,200 ms) is not achievable on this
  hardware.** The endpointing floor alone is 600 ms. The project's own requirement clause
  permits stating the tightest defensible SLO instead, with arithmetic.
- **The 3,000 ms per-turn cap is not met.** Best measured: 7% of turns over, against a
  criterion of zero.
- **The largest measured latency levers are enabled but not formally adopted.** Streaming
  LLM and streaming TTS are both implemented, both measured (27% → 6% over-cap), and both
  switched on in the deployed `.env` — but formal adoption still requires the frozen
  golden-set quality gate to pass first, and that gate is blocked on human sign-off.
- **97% of harness runs are discarded**, and every N=2 run carries `harness_fault: true`
  — so every absolute N=2 figure carries that caveat.
- **The carrier round trip is excluded from every local figure.** It is assumed at 150 ms
  and is described in the project's own records as "the highest-uncertainty term".

---

## 11. Known limitations and open items

This section exists because several of these are things a reader would otherwise
reasonably assume to be fine.

### 11.1 Documentation that no longer matches the code

| Older claim | Verified reality |
|---|---|
| Answers questions about **UMD / Fairleigh Dickinson** | **Meridian University** — every spoken line and the entire knowledge base |
| **Silero VAD**, triggers on 0.5 s silence | Deliberately removed; one RMS silence counter at **600 ms** |
| Voice retrieval is plain similarity, k=2–3 | **Hybrid (dense + BM25 + RRF), k=5**, fetch_k=20 |
| `num_ctx: 2048` | **8192** |
| FastAPI runs **2–4 workers** | **Single worker by design**; the key is sizing metadata |
| Vector store polluted, 4,618 chunks | **Resolved** — the live collection holds ~35 chunks |
| `app/pipeline.py` is the voice pipeline | It is **not** the live path; the live path is `voice_handler.py` |
| WhatsApp handles voice notes | The webhook **rejects audio** and asks the sender to type |

### 11.2 Open defects of significance

| Ref | Severity | Summary |
|---|---|---|
| **BUG-07 / BUG-29** | CRITICAL | **Fabricated academic content.** The agent invents programmes, durations, curricula and requirements — usually by recombining real facts plausibly. A school-consistency rule now catches the demonstrated crossing case (`Master of Science in Computer Science`), but the class is **not closed**: a recombination inside one school is still undetected (`Master of Computer Science` was spoken five times on one call). |
| **BUG-10 / BUG-20** | CRITICAL | **Commitments the platform cannot honour.** "We'll get back to you within 24 hours", "I've scheduled it on my calendar", "your application is under review" — no such capability exists. |
| **BUG-25 / BUG-26** | CRITICAL / HIGH | **Fabrication promoted into the CRM.** A fabricated programme was stored as the caller's *name*; the literal string `"null"` was stored as a name and email; `conversations` and `lead_calls` disagree about the same call. |
| **BUG-30** | CRITICAL | **A refusal becomes an assertion of absence.** "We don't have a specific computer science program" — said about a programme the same agent describes correctly 24 seconds later. |
| **BUG-05 / BUG-13** | HIGH | **Empty turns.** 22–26% of agent turns produce no audio, with no recovery line. Root cause not established; it is provably *not* the same family as the prompt-role defects. |
| **BUG-08** | HIGH | **Repetition.** 32 of 92 agent turns duplicated on one call, after the caller said "Don't repeat" twice. |
| **BUG-14** | MEDIUM | **Closing cues ignored.** A caller said "can you cut the call?" four times; no closing path fired. |
| **DEF-001** | **Critical** (the only item in the doc set with an explicit severity) | **MCP retrieval intermittently hangs** for the full read timeout. Deferred, conditionally. |
| **BUG-28** | HIGH | **The production retrieval rung returns ~40% of the content** of the local rung — thin enough to lose facts the corpus holds, causing the grounding boundary to refuse *correct* answers in production that it never refused offline. |
| **BUG-15** | LOW | `call_duration_seconds` is always 0 — written, never with a real value. |
| **BUG-21** | HIGH | A callback requested for "tomorrow at 2 p.m." is booked at `now + 24h` — the wrong day and the wrong hour. |

**A pattern worth recording.** The grounding boundary has been through three rounds of
fix-and-deploy. Each round passed every gate and produced a new defect of the same family,
one level out: the fix for false refusals stopped catching fabrications; the fix for that
introduced a spoken loop; the fix for the loop pinned the loop's *subject*. The register's
own conclusion is the right one to carry forward: **every gate tested turns, and all the
defects were properties of a conversation.** A call-level replay gate
(`doc/voice-demo/replay_call.py`) now exists for that reason.

### 11.3 Structural limitations

- **Two divergent knowledge stores.** The MCP rung and the local Chroma rung return
  different chunks for the same question and format citations differently. Consolidation
  is planned and not done; today it is a correctness hazard, not redundancy.
- **The relevance gate is disabled** (`RAG_SIMILARITY_THRESHOLD=0.0`). The system relies on
  the system prompt, not retrieval thresholds, to keep out-of-scope questions honest. Do
  not describe it as rejecting out-of-scope questions at retrieval.
- **Context is never trimmed** (`RAG_MAX_CONTEXT_CHARS=0` — warn only).
- **The reranker is on disk but not in force.** Its 23.2 MB INT8 ONNX model is shipped and
  loadable, but the frozen retrieval configuration records it as `off` — because the
  retrieval sweep measured it *reducing* key recall@1 from 1.000 to 0.730. The remaining
  cost is boot time and disk.
- **The semantic cache is configured but unreachable** from the retrieval path.
- **Retrieval is the first non-resource bottleneck** and the only one that degrades a
  caller with no resource near exhaustion.
- **Quality adoption is blocked on a human.** 128 of 161 golden-set ground truths are
  still `PENDING_PO_SIGNOFF` (24 are `verified`, 9 `PO_APPROVED`). No agent can clear this,
  and it gates every behaviour-changing change — including formal adoption of the
  streaming flags.
- **Compliance:** student leads and CRM data are personal data, and call transcripts are
  persisted to Postgres. **Retention and consent policy for call transcripts is not stated
  anywhere in the repository** — it is recorded as an open item rather than invented.

### 11.4 Security and operational observations

Verified from the code, and stated here because they are the kind of thing a reader would
otherwise assume is handled:

- **No authentication on any inbound endpoint.** The Twilio webhooks are **not
  signature-validated** (grepping for `X-Twilio-Signature` / `validate_request` finds only
  the *outbound* Basic-auth header used when downloading WhatsApp media), and the `/api/*`
  surface is unauthenticated. On a loopback-only demo box this is defensible; on anything
  reachable it is not.
- **A live `.env` containing real Twilio credentials and database values sits in the
  working tree**, alongside three `.env.bak-*` backups. They are gitignored, but they are
  on disk — and one of them is untracked-but-present per `git status`.
- **`MCP_ENABLED=false` does not disable the MCP routes.** The flag is read nowhere that
  would guard registration; the endpoints stay live.
- **The readiness gate runs on only one of four launchers.** `app/boot_readiness` is
  invoked from `start_services.ps1` only; `start_services.sh`, `start.sh` and
  `bootstrap_services.py` still warm with the literal `"ping"` prompt — the pre-fix
  behaviour the module itself criticises. On POSIX, therefore, the stack can report itself
  started with a cold prompt prefix.
- **`app/memory_budget.py` is not wired into the runtime.** Only tests and the
  platform-readiness validator import it; it documents budgets and does not gate startup
  or model loading. Its `safe_threshold_percent: 95` also sits *above* the 90% VRAM ceiling
  the requirements set.
- **Port configurability is only half-applied.** The PowerShell launcher reads
  `FASTAPI_PORT` / `STREAMLIT_PORT` / `RAG_MCP_PORT` from `.env`; the tunnel helpers, the
  POSIX launcher and the bootstrap script hardcode 8000 / 8501 / 8502.
- **`.machine_profile.json` in the working tree is stale** relative to both the current
  hardware tiers and the live `.env` (it records `qwen2.5:14b` and `FASTAPI_WORKERS=4`).
  Nothing reads its `applied` block at runtime, so it cannot cause a failure — but it is a
  live instance of the "written, not in force" pattern the project otherwise polices.
- **A configuration key with a reader is not the same as a key in effect.**
  `FASTAPI_WORKERS` was honoured on exactly one launcher, which ran *four* uvicorn workers
  — four copies of every model — when the key was unset. That path was removed in
  September 2026.

---

## 12. Repository map

```
universityDemo/
├── app/
│   ├── main.py                  FastAPI application — HTTP, WebSocket, webhooks
│   ├── voice_handler.py         VoiceCallSession — the live voice turn loop (~125 KB)
│   ├── voice_grounding.py       The grounding boundary: claim extraction + entity index
│   ├── voice_system_prompt.py   The agent's behaviour specification and prompt assembly
│   ├── pipeline.py              Pipecat assembly path — NOT the live call path
│   ├── rag.py                   Retrieval dispatcher (MCP-first, local fallback)
│   ├── rag_mcp.py               MCP client + circuit breaker
│   ├── rag_legacy.py            Local Chroma retrieval + hybrid search
│   ├── rag_config.py            RAG baseline/optimized configuration
│   ├── llm_backend.py           Ollama / MLX abstraction, model selection, message roles
│   ├── kb_entities.py           Corpus index for the grounding guard: names, programme→school, number ownership
│   ├── programs.py              Programme-name detector (aliases → canonical) for lead capture, not grounding
│   ├── config.py                Single source of runtime configuration
│   ├── config_truth.py          Configuration provenance enforcement
│   ├── boot_readiness.py        Boot warm-state verification
│   ├── hardware_profile.py      Hardware detection and tier sizing
│   ├── memory_budget.py         Memory ceiling guard
│   ├── perf_trace.py            Per-turn stage tracing (dependency-free)
│   ├── database.py              Postgres access, lead extraction, transcripts
│   ├── crm/                     Salesforce integration, outbox, breaker
│   ├── leads/                   Lead lifecycle and models
│   ├── offers/                  Offer-letter generation and delivery
│   ├── outbound/                Outbound calling
│   ├── sentiment/               Sentiment categorisation and lead scoring
│   ├── dashboard/               Dashboard surfaces
│   ├── mcp/                     MCP server exposing lead tools
│   └── static/                  Served static assets
├── content/meridian/            The knowledge base (15.6 KB, section-id structured)
├── eval/                        Golden set (161 cases), deterministic checks, sign-off packet
├── tests/                       Test suite
├── doc/
│   ├── sdlc/                    Product intent, BRD, use cases, architecture, module specs,
│   │   │                        the 28-intent catalog, and US-001…US-018 user stories
│   │   └── defects/             DEF-001 and the defect register
│   ├── perf/                    Performance reports, decisions, 73 run artifacts, 30+ gate tools
│   ├── voice-demo/              Live-call reviews, defect register (BUG-01…BUG-30),
│   │                            measurement and replay harnesses
│   ├── salesforce/              CRM integration plans and status
│   └── cloudDeployment/         The documented, not-deployed scale-out plan
├── logs/perf_turns.jsonl        Turn traces — the live measurement record
├── chroma_local_db/             Vector store
├── data/                        Offers, uploaded documents
├── requirements.txt             Exact pinned versions
├── start_services.ps1/.sh       Stack launcher and pre-warm
├── bootstrap_services.py        First-time environment setup
└── .env / .env.example          Runtime configuration
```

---

## 13. One-paragraph summary

> It is a local-first AI voice agent for university admissions. A call comes in over
> Twilio; the system separates the caller's voice from its own echo, waits for the caller
> to finish, transcribes the speech with Whisper, retrieves the relevant passages from a
> university knowledge base using hybrid dense-plus-keyword search, assembles a grounded
> prompt, generates an answer with a small local language model, checks every number and
> programme name in that answer against the retrieved evidence, and speaks the result back
> in a neural voice. Everything runs on one Windows box with an RTX 5060 Ti, because
> admissions calls carry personal data and no inference may leave the machine. The same
> pipeline answers web chat and WhatsApp messages; after the call it extracts the caller's
> details, writes them to Postgres, and syncs them to a CRM. The entire stack is
> instrumented so that every turn's latency is measured per stage rather than inferred,
> and the project's own records — including a defect register of thirty findings from real
> calls — are unusually candid about what still does not work. The university it speaks
> for, Meridian, is a synthetic institution: the fees, programmes and deadlines in the
> knowledge base are dummy data marked for replacement before production use.
