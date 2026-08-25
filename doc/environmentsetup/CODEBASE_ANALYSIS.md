# Codebase Analysis — Current State (Production Re-Architecture Input)

**Analysis date:** 2026-08-23
**Method:** 5 parallel analysis agents over the full repo — voice/telephony, RAG/LLM,
domain modules/data, frontends/infra, and the ~60-doc markdown corpus.
**Coverage:** ~21.8k Python LOC, 218 tracked files, 79 commits (2026-07-25 → 2026-08-22).
**Purpose:** Durable record of *verified current state*, to be embedded into the multi-LLM
architecture RFP prompt. Every claim below is cited `file:line`.

> **Provenance note.** Findings marked **[code]** were verified by reading source.
> Findings marked **[docs]** come from the repo's own markdown and are claims, not
> measurements. Where the two disagree, code wins. See §15 for doc contradictions.

---

## 1. What the product is

A university admissions AI assistant with five channels over one backend:

1. **Inbound phone calls** — Twilio Media Streams → STT → RAG → LLM → TTS
2. **Outbound phone calls** — queue + worker + scheduler, retries, status callbacks
3. **WhatsApp** — text Q&A, document upload, offer-letter delivery, ACCEPT/DECLINE
4. **Streamlit web chat** — text + mic input, lead-capture state machine
5. **Staff dashboard** — KPIs, leads, conversations, follow-up scheduler, course CRUD

Plus: auto-generated branded PDF **offer letters** (WhatsApp + email + payment link),
**sentiment/lead scoring** on every conversation, and an **MCP server** (see §9.4).

**Institution:** "Meridian University" (pivoted 2026-08-14 from an earlier UMD/FDU dataset).
All fees/deadlines/contacts are **demo data**, not real institutional figures.

---

## 2. Stack (pinned, `requirements.txt`)

| Layer | Components |
|---|---|
| Web/API | fastapi 0.140.0, uvicorn[standard] 0.51.0, streamlit 1.59.2, websockets 15.0.1, httpx 0.28.1 |
| STT | faster-whisper 1.2.1 (`small.en`, int8), openai-whisper 20250625 |
| TTS | kokoro-onnx 0.5.0 (`af_heart`), onnxruntime-gpu 1.28.0 (Win/Linux) / onnxruntime 1.24.3 (Darwin) |
| Voice framework | pipecat-ai 1.6.0 — **present but NOT used by the live voice path** (§4.5) |
| LLM | ollama 0.6.2 (`qwen2.5:7b-instruct-q3_K_M` default; live `.env` = `qwen2.5:14b`); MLX on Apple Silicon |
| RAG | chromadb 1.5.9, langchain 1.3.14 (+community/text-splitters/ollama/classic), `nomic-embed-text` 768-dim |
| DB | psycopg2-binary 2.9.12 — **synchronous driver**, no ORM |
| Integrations | twilio 9.10.9, fpdf2 2.8.7, stdlib smtplib |
| MCP | mcp 2.0.0 — installed, detected, deliberately bypassed (§9.4) |

**Runtime constraints:** Python 3.11 (3.13 removes `audioop`, which the voice path uses).
`torch` is range-pinned `>=2.7.0,<2.8` — the **only** non-exact pin; on NVIDIA Windows the
bootstrap replaces it with `torch==2.7.1+cu128` from download.pytorch.org outside
requirements (`bootstrap_services.py:672-679`). Two torch variants in one repo =
reproducibility risk. `pipecat-ai` drags CPU `onnxruntime~=1.24.3` next to the GPU wheel,
requiring a force-reinstall workaround (`bootstrap_services.py:681-688`).

**`requirements.txt` declares no pytest** — the test suite cannot run in the shipped environment.

---

## 3. Scale and hotspots

| File | LOC | Commits touched (of 79) |
|---|---|---|
| `app/main.py` | **2,793** | **35 (44%)** |
| `bootstrap_services.py` | 1,115 | 4 |
| `app.py` (Streamlit chat) | 897 | 22 |
| `app/leads/models.py` | 741 | 3 |
| `app/voice_system_prompt.py` | 714 | — |
| `app/voice_handler.py` | 714 | 8 |
| `app/sentiment/scorer.py` | 689 | 7 |
| `app/offers/models.py` | 622 | — |
| `app/hardware_profile.py` | 588 | — |
| `app/rag.py` | 567 | 7 |

`app/main.py` is the god-integrator: ~50 REST routes, 4 WebSocket endpoints, the entire
WhatsApp state machine, TwiML templates, and ~60 lazy imports. **Nothing in `app/` imports
it** (a prior `main → offers/service → main` cycle was broken by extracting
`app/messaging.py`), so it is a god *module*, not a dependency cycle — which makes
decomposition tractable.

---

## 4. Voice / telephony subsystem

### 4.1 Endpoint inventory (`app/main.py`)

**WebSocket:**
| Line | Path | Purpose |
|---|---|---|
| 339 | `/ws/voice` | Browser mic, raw PCM — **echo only, pipeline commented out** (`main.py:359-368`) |
| 390 | `/ws/voice/text` | Text → RAG+LLM, bypasses STT/TTS. The only Pipecat consumer. |
| 453 | `/ws/twilio` | **Inbound** — full STT→RAG→LLM→TTS loop |
| 633 | `/ws/twilio-outbound` | **Outbound** — same loop, `direction="outbound"` |

**REST:** ~50 routes spanning voice, whatsapp, leads, sentiment, outbound, dashboard,
offers, admin, MCP, health. Notable: `/twilio/voice` (934), `/twilio/whatsapp` (1353),
`/api/quick-call` (1660), `/api/calls/live` with SSE (2105), `/api/demo/reset` (2271),
`/api/offers/{id}/pdf` (2551), `/mcp/sse` (2588), `/` health (2619).

**No `X-Twilio-Signature` validation exists anywhere in the repo.** No auth on any `/api/*`.

### 4.2 Inbound call trace

1. `GET /twilio/voice` (934) → `TWIML_IVR_TEMPLATE` (303-320): hardcoded Meridian IVR,
   `<Gather numDigits="1" timeout="3">` — **3s floor before the AI is ever reached**
2. `GET /twilio/voice/connect` (946) → `<Connect><Stream>` to `/ws/twilio`; host from
   `_resolve_tunnel_host()` (273-289)
3. `WS /ws/twilio` (453-628):
   - `start` → `generate_ulaw_greeting()` (516 → `voice_handler.py:124-144`) — Kokoro TTS +
     scipy resample **inline in the async handler, not `to_thread`**; first call also pays
     ONNX engine cold load
   - `media` → AEC gate: if `MUTE_STT_DURING_TTS` (default on) and `tts_playing`, the chunk
     is **dropped** and `reset_utterance()` called (547-550) → **no barge-in**
   - VAD: RMS energy threshold `<80` on 20ms chunks; fires at 30 trailing silent frames
     (~600ms) or 300 frames (~6s max) (`voice_handler.py:282-283`)
   - `process_utterance` (`voice_handler.py:374-510`), strictly serial:
     µ-law→PCM → 8k→16k resample → **STT** (`to_thread`, greedy `beam_size=1`,
     `condition_on_previous_text=False`) → **LLM** → `scrub_meta_leak` → **TTS**
     (`to_thread`) → 24k→8k resample → 20ms µ-law chunks
   - `_query_llm` → `run_rag_query_sync` → `rag.query_rag`, which makes **three network hops
     per utterance**: (A) `GET /api/tags` model discovery — *every single turn*
     (`rag.py:555` → `llm_backend.py:193-208`); (B) embedding HTTP call for retrieval;
     (C) `ollama.chat(stream=False)`
4. Disconnect → `_handle_disconnect` (2710-2792), **awaited inline in the WS `finally`**:
   chains `extract_lead_from_transcript` (LLM) + DB upsert + `post_call_handler` +
   `handle_post_interaction` (LLM again) + `score_transcript` (LLM again) =
   **3–4 sequential 7B inferences blocking the event loop on every hangup**

### 4.3 Outbound trace

`FollowUpScheduler` (30s poll) / REST / batch → `call_queue` → `OutboundCallWorker._poll_loop`
(10s poll, `caller.py:116-124`) → `get_next_queued_call` uses `FOR UPDATE SKIP LOCKED`
(`leads/models.py:584-611` — **the one genuinely replica-safe piece**) → sync
`twilio.Client().calls.create()` **not** wrapped in `to_thread` (`caller.py:197-214`) →
`/twilio/outbound-voice` → `/ws/twilio-outbound`. Status callback (840-929) maps Twilio
states and re-queues up to `MAX_CALL_ATTEMPTS=3`.

**The worker processes exactly one call at a time** (`caller.py:84-86`) — a 5-minute call
blocks every other queued call.

### 4.4 WhatsApp trace

`POST /twilio/whatsapp` (1353-1560). **Voice notes are explicitly rejected** with a canned
"I can't listen to voice notes" (1381-1387) — despite docs claiming the feature works.
Images/PDFs → background task → download → save → readiness check → **sync** Twilio send.
Text runs a state machine (greeting → name → email → program → intent → ACCEPT/DECLINE →
RAG fallback) with `_detect_admission_intent_whatsapp` making a **blocking LLM call inline
in the async webhook** (1305-1325).

### 4.5 STT/TTS specifics

- **STT** lazy module-global `_stt_model`, **pre-warmed** at startup (`main.py:199-221`).
  Device via `platform.get_whisper_device_config()`: CUDA→int8, MPS→**cpu/int8**
  (CTranslate2 has no Metal backend), CPU→float32. Domain correction dict hardcodes
  "maridian"/"miridian"→"Meridian" (`voice_handler.py:514-522`).
- **TTS** lazy module-global `_tts_engine`, **NOT pre-warmed** — first greeting of the
  process pays ONNX load inline in the WS handler. Class-level cache keyed by `hash(text)`,
  max 50 entries, **shared across all concurrent sessions**.
- **No streaming in either direction.** STT runs once per complete utterance; TTS
  synthesizes the **entire answer** before the first byte ships (`main.py:577`).
- `audioop` (deprecated, removed in Python 3.13) does µ-law conversion, duplicated in
  `voice_handler.py:111-118` and `main.py:325-334`.
- **Pipecat is dead weight on the live path** — `app/pipeline.py` (SileroVAD, WhisperSTTService,
  KokoroTTSService, OLLamaLLMService) is used only by `/ws/voice/text`, tests, and
  `run_pipeline_test.py`. The production loop is the hand-rolled RMS-gate VAD.

### 4.6 Latency bottlenecks (ranked)

1. **Blocking sync I/O inside `async def`** — psycopg2, `ollama.chat`, Twilio REST, SMTP all
   called directly from async handlers. `ollama.chat` has **no timeout at all**
   (`llm_backend.py:135-146`); the MLX path has `httpx.Client(timeout=180)`.
2. **No streaming anywhere** — LLM `stream=False`, TTS full-buffer before first byte.
3. **Call-teardown chain** — 3–4 sequential LLM calls inline in the WS `finally`.
4. **`/api/tags` HTTP round-trip on every utterance** — pure overhead, never cached.
5. **Serial per-turn chain** — STT → discovery → retrieval → LLM → TTS, nothing overlaps.
6. **Fixed floors** — 600ms VAD trailing silence + 3s IVR `<Gather>` timeout.
7. **Cold starts** — TTS engine not pre-warmed; `chromadb.PersistentClient` reopened per
   hybrid query (`rag.py:403`).
8. **Polling everywhere** — outbound 10s, follow-up 30s, SSE 1s, MCP keepalive 30s,
   CLI/HTML pollers 2s.
9. **No connection pooling** — a fresh `psycopg2.connect()` per function call.

**No timing instrumentation exists in the voice path.** No `perf_counter`, no elapsed
logging. The structured `voice_events` logger emits `EVENT <call_id> turn=N ...` lines
**without timestamps** (`voice_handler.py:41-43`).

---

## 5. RAG and LLM

### 5.1 Architecture

| Aspect | Value |
|---|---|
| Vector store | ChromaDB `PersistentClient`, single collection hardcoded as `"langchain"`, **28 embeddings live** |
| Index location | `chroma_local_db/` — **deliberately git-tracked** as a release artifact |
| Metric | cosine (HNSW `ef_construction=100, M=16, ef_search=100`) |
| Embeddings | `nomic-embed-text` 768-dim via Ollama HTTP; MLX path uses `nomic-ai/nomic-embed-text-v1.5` locally |
| Chunking | `RecursiveCharacterTextSplitter` **600/90**, pre-split per `## ` markdown section |
| Retrieval | MMR **k=5, fetch_k=20, λ=0.5** |
| Hybrid | dense + hand-rolled `_MiniBM25` + RRF (k=60) — Chroma's native Search API raises `NotImplementedError` on the local backend |
| Reranker | none (deferred on latency grounds) |
| Similarity threshold | env-gated, **OFF (0.0)** — measured overlap: valid query 0.35 vs off-topic 0.32 |

Single choke point: everything converges on `rag.query_rag` (`rag.py:506`) /
`get_retriever` (`rag.py:298`) — **except Streamlit**, which builds its own chain against
`get_retriever()` directly (`app.py:203-237`).

### 5.2 Ingestion

**Markdown only.** `SOURCES` pins exactly one file:
`content/meridian/meridian_knowledge_base.md` (11 KB → 28 chunks) as a **Python constant**
(`rag.py:47-52`). PDF/docx loaders were removed 2026-08-14. Ingestion is **script-only**
(`scripts/rebuild_rag_index.py`) — no endpoint, no UI. Refuses a non-empty target unless
`FORCE=1`. Index swap is a **manual operator directory rename**.

Chunk metadata: `section`, `source_file`, `doc_type`, `ingested_at`. **No tenant field.**
**No `where=` filter is ever applied at retrieval.**

### 5.3 LLM backend

Ollama (Win/Linux) or MLX (Apple Silicon), `LLM_PROVIDER=auto`. **No cloud fallback
anywhere.** `num_ctx` 8192 default (tiered down to 4096/2048 by hardware profile),
temperature 0.3 live. `stream=False` at every production call site. **No retry logic.**
`json_mode` is accepted as a parameter but never actually sends `format="json"`
(`llm_backend.py:166-169`) — parsing falls back to regex.

### 5.4 Prompts

| Prompt | Location | Size |
|---|---|---|
| Voice | `app/voice_system_prompt.py:33-694` | **~660 lines, ~3.5k tokens** |
| Chat | `app/rag.py:105-125` | ~300 tokens |
| WhatsApp intent | `app/main.py:1307-1324` | small, `num_ctx=1024` |

The 660-line voice prompt is `.format()`-templated with `agent_name`/`company_name`/`context`,
but the grounding line at `:590` hardcodes "Meridian University admissions" outside the template.

---

## 6. University-specific hardcoding (the "any university" blocker)

**This is worse than a string-replacement problem.**

### 6.1 The hard blocker

`app/rag.py:78` — `EXPECTED_MARKERS = ["meridian university"]` is a **build-time validation
gate**. The rebuild script exits 1 and writes nothing if the corpus does not contain that
literal string (`rag.py:214-217`, `rebuild_rag_index.py:84-88`). `BLOCKED_MARKERS`
(`rag.py:81-84`) additionally bans `university of maryland, terrapin, fafsa, mddcs, mhec,
fairleigh dickinson, umd, fdu, ...`. **Ingesting any other university's data fails outright.**

The rebuild script's smoke tests and negative canary are equally tenant-specific
("What undergraduate programs does Meridian offer?", "What is the Terrapin Commitment?").

### 6.2 Hardcoding inventory (~40 sites)

| Category | Sites |
|---|---|
| **System prompts** | `rag.py:106` ("…Advisor for Meridian University"), `rag.py:113-114` (citation example `(§ Fees Structure)`), `voice_system_prompt.py:590`, `admissions_bot.py:61` |
| **Canned replies** | `voice_handler.py:255` (`CLOSING_REPLY`), `main.py:307` (IVR), `main.py:511-515` (greeting), `main.py:1415/1440/1452/1546` (WhatsApp), `rag.py:530/543` (not-found text) |
| **Domain logic** | `main.py:1118-1136` `_MERIDIAN_PROGRAMS` alias map driving lead capture; `main.py:1151-1155` KB keywords; `voice_handler.py:516-517` STT spelling corrections |
| **Config defaults** | `config.py:82` `UNIVERSITY_NAME`, `:85` `OFFER_EMAIL="admissions@university.edu"`, `:94` `DEFAULT_PAYMENT_LINK="https://pay.university.edu/admissions"`; `.env:100` `COMPANY_NAME` |
| **PDF** | `offers/pdf.py:190` hardcoded signatory "Dr. A. Chancellor, Dean of Admissions" |
| **DB seed** | `seed_demo_data.py:54-132` — 8 transcripts with KB facts **as literal strings**; `:169-200` — 14-row course catalog with exact fees, commented *"must match meridian_knowledge_base.md exactly (offer letters read these rows)"* |
| **Tests** | ~10 files asserting `$15,000`/`$18,500`/"august" — a corpus swap breaks them even when retrieval is correct |
| **UI copy** | `app.py:108/194/200`, `static/index.html:35,73`, `static/voice_client.html:123` |

### 6.3 Multi-tenancy gaps

1. Single global store, collection name `"langchain"` hardcoded in every access path
2. Corpus path is a Python constant, `doc_type` is tenant-named (`"meridian_profile"`)
3. Validation gate actively rejects other institutions (§6.1)
4. `courses` table has **no university column** — two catalogs cannot coexist
5. BM25 cache is a single-corpus global keyed `"bm25"`
6. Git-tracked binary vector store — tenant corpora would collide in version control
7. **Asset:** exactly one retrieval seam (`query_rag`/`get_retriever`) — a tenant-aware
   refactor has a single natural insertion point

---

## 7. Data layer

- **No ORM. No migrations. No pooling.** Zero SQLAlchemy/Alembic anywhere.
- Schema created by `CREATE TABLE IF NOT EXISTS` on **every startup** (`database.py:77-115`),
  plus scattered `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` strings as pseudo-migrations
  (`offers/schema.py:73-75`, `sentiment/schema.py:44-50`). No versioning, no rollback.
- All CRUD is `async def` wrapping **synchronous psycopg2** — every DB call blocks the loop.
- **A fresh `psycopg2.connect()` per function call**, `autocommit=True`. A single request can
  open 5+ connections (`generate_and_send_offer` opens ~6).
- **Connection leak:** `offers/models.py:38-58` `_get_db` is **not** a context manager —
  `conn.close()` runs only on the happy path; any exception leaks the connection until GC.
  (`leads/models.py:49-68` and `sentiment/models.py:40-56` *are* context managers.)
- **Four independent DB config sources**: `leads/models.py:32-37` and
  `sentiment/models.py:23-28` read `os.environ` directly (bypassing `app.config`),
  `offers/models.py:43-53` uses settings, and `main.py:1958-1974` re-inlines raw psycopg2.

**Tables:** `leads` (phone is the dedup key but has **no unique constraint**), `conversations`,
`follow_ups`, `call_queue`, `courses` (fees stored as **VARCHAR** display strings like
`"$14,500/year"`), `lead_documents`, `offer_letters`, `sentiment_scores`, plus an orphaned
legacy `lead_calls`. Sentiment columns are bolted onto `leads` by a different module
(`sentiment/schema.py:44-50`) and positionally parsed by `leads/models.py:674-679` — a
cross-module schema dependency.

**DB-less mode** = total exception-swallowing. Every CRUD function returns `None`/`[]` on
failure; writes are silently dropped; callers frequently treat that as success.

---

## 8. State and concurrency — the single-instance ceiling

**Process-local module globals that break with >1 worker:**
`_active_call_sids` (2087), `_transcript_events` (2086), `_batch_jobs` (2010),
`_machine_profile_cache` (138), `_db_available` (81), plus the worker/scheduler singletons.

Consequences at 2+ workers: `/api/calls/live` SSE and dashboard counts see only that
worker's calls; `/api/quick-call/batch/{id}` 404s across workers; duplicate schedulers poll
the DB. Only `get_next_queued_call`'s `FOR UPDATE SKIP LOCKED` is replica-safe.

**Model singletons per process:** `_stt_model`, `_tts_engine`, `_vector_store`,
`_st_embed_model`, `_bm25_cache` — memory is amplified per worker, so scaling out multiplies
VRAM/RAM.

**Per-call state** (`VoiceCallSession` audio buffer + conversation history) is RAM-only and
lost on WS drop. WhatsApp state is read-modify-write on the lead row with **no locks** —
concurrent messages can race name/email writes. Thread-safety of concurrent faster-whisper /
Kokoro calls from multiple sessions' `to_thread`s is **unguarded**.

`FASTAPI_WORKERS` is written into `.env` by the hardware profiler but **never passed to
uvicorn** — the app always runs single-process.

---

## 9. Domain modules

### 9.1 Coupling

Every cross-module import in `app/` is **lazy (inside functions)** except
`offers/pdf.py:19`. No import-time cycles today — but module boundaries are enforced only
by convention. `main.py` calls `leads.models.*` directly ~25 times, largely **bypassing the
`leads.service` layer that exists**.

**Private-internals reach-in:** `main.py:1779` and `:1851` import
`app.sentiment.scorer._compute_session_aggregate` (a `_`-private); `main.py:965` reaches
`voice_handler._get_stt_model`.

**Stale export:** `app/sentiment/__init__.py:15` references `categorize_lead`, which does
not exist (the function is `categorize`) — any `from app.sentiment import *` raises ImportError.

### 9.2 Sentiment — blocking the request path

LLM extraction (temp 0.1) + weighted formula + EWMA + sigmoid heuristic. The "LightGBM
predictive layer" is a placeholder gated on `MIN_LABELED_OUTCOMES=100`.
`score_transcript` makes **up to 3 sequential LLM calls**. `POST /api/interactions/log`
awaits it inline; voice hangup triggers 3–4 (§4.2).

### 9.3 Email / messaging — blocking, unverified

`emailer.py` stdlib smtplib, **synchronous**, called inline from async
(`offers/service.py:247-268`). `messaging.py` Twilio WhatsApp, **synchronous**, no timeout,
no retry, called inline from async in 3 places. Both block the event loop on network I/O.
**Email has never actually sent** — SMTP creds empty, silently skipped.

### 9.4 MCP — not actually MCP

The `mcp==2.0.0` SDK is installed and detected, but `handle_sse_request` **deliberately
falls through** to a hand-rolled REST imitation (`server.py:62-65`). The SSE stream emits
one `data:` line then 30s keepalives forever and never speaks the MCP protocol.
`MCP_ENABLED` (`config.py:75-77`) is read **nowhere** except the health echo — a dead switch.
No auth on `/mcp/*`. 7 leads-only tools, each spawning a thread with a 30s timeout.

### 9.5 Offer letters — host-coupled

PDF → `{DATA_DIR}/offers/{offer_id}.pdf`, served unauthenticated at `/api/offers/{id}/pdf`.
The WhatsApp media URL is built from a resolved tunnel host, and **if that resolves to
`localhost:8000` the WhatsApp send is skipped entirely** (`offers/service.py:206-207`).
`OFFER_GUARD_MINUTES` defaults to **1** while its docstring claims 24h — off by 1440×.

---

## 10. Frontends

**Four surfaces, one backend, heavily duplicated.** KPI overview, lead pipeline,
conversation search, quick-call and follow-up calendar are each implemented **twice**
(Streamlit dashboard + static "Command Cockpit" SPA). Voice input exists in **three** forms.

**Streamlit chat (`app.py`)** — the chat brain runs **in-process**, not via the API: it
builds its own LangChain RAG chain against `get_retriever()` + `get_chat_model()`
(`app.py:203-237`) and hits Ollama directly. REST is used only for lead sync, uploads, and
logging. **Split-brain backend URL**: `streamlit_backend.BACKEND_BASE` honours an env var,
but uploads and logging POSTs **hardcode `http://localhost:8000`** (`app.py:84,155,856`).
Multipart bodies are hand-built with string-concatenated boundaries, duplicated twice.
**Zero streaming** — one blocking `invoke()` then one `st.markdown`. `sync_lead` does up to
**3 sequential HTTP calls** (PUT→POST→re-PUT) and is re-invoked on nearly every flow step.

**Streamlit dashboard** — **no caching at all** (zero `st.cache_*` matches across
`app/dashboard/`). Every widget click = full rerun = every API call re-executed. Real N+1:
one sentiment HTTP call **per lead per render** (`leads_page.py:113`). The scheduler page
issues two overlapping lead fetches (`limit=100` and `limit=200`).

**Static SPA** — polls `/api/dashboard/summary` (10s), `/api/leads` (30s),
`/api/conversations` (30s), calendar (60s); live calls via SSE; up to 20 sequential
per-lead score/sentiment fetches per render.

---

## 11. Platform / hardware abstraction (already built — reuse, don't rebuild)

Three genuinely useful layers already exist:

- **`app/hardware_profile.py`** (588 lines, stdlib-only, runs pre-venv) — detects GPU/VRAM
  via nvidia-smi, RAM, container, cloud. A **9-tier sizing table** (`:224-288`) decides
  `LLM_PROVIDER`, `OLLAMA_MODEL`, `MLX_MODEL`, `OLLAMA_NUM_CTX` (8192/4096/2048),
  `WHISPER_MODEL`, `WHISPER_NUM_THREADS`, `FASTAPI_WORKERS`, `RAG_TOP_K`, `RAG_FETCH_K`.
  Writes a marker-wrapped `.env` block with manual-override protection + drift detection.
- **`app/platform.py`** — torch-based device detection; CUDA→int8, MPS→float16,
  CPU→float32; the MPS→cpu Whisper mapping (`:242-254`).
- **`app/memory_budget.py`** — per-platform VRAM/RAM budgets and thresholds.
- **`scripts/predeploy.py`** — CLI driver, exit 2 on drift.

---

## 12. Deployment and networking

**Docker story is incomplete.** `docker-compose.yml` defines 5 services (postgres, ollama,
fastapi, streamlit, dashboard) — but on the operative Windows path **only Postgres actually
runs in Docker**. FastAPI, both Streamlit apps and Ollama run on the host from `.venv`.
The compose `fastapi` service bind-mounts `.:/app` and runs uvicorn **with `--reload`** —
dev convenience, not a deployment topology. The Dockerfile is `python:3.11-slim` with
**no GPU stage, no CUDA base image, no nvidia runtime**. **No Kubernetes, Terraform, or Helm
anywhere.**

**Public URLs are ephemeral cloudflared quick tunnels.** Hostnames are **regex-scraped from
cloudflared log files in `%TEMP%`** and cached to flat files (`.whatsapp_tunnel`,
`.tunnel_8000/8501/8502`). URLs change every restart; every start kills all cloudflared
processes; Twilio webhooks must be re-pointed each time; fresh hostnames need 30–60s DNS
warm-up. `.tunnel_*` files are **dockerignored**, so containers cannot resolve them.
Host resolution is re-implemented in **four** places.

**Cold start:** Docker Desktop 30–60s + FastAPI up to 60s + tunnel ≤45s + warm-up ~60s,
plus a first-load model download of several GB on first-ever run.

**No CI.** `.github/` contains only `copilot-instructions.md`.

---

## 13. Observability — effectively none

- `logging.basicConfig(level=logging.INFO)` and one module logger (`main.py:63-64`). No
  formatters, no rotation. The `LOG_FILE` env key exists but **nothing reads it**.
- **No metrics, no tracing, no APM, no error tracking.** Zero Prometheus/OpenTelemetry/
  Sentry/Datadog anywhere.
- Errors are **systematically swallowed by design** — `except: pass` in `app.py:88-89`,
  wrappers returning `None`, `try/except → []` throughout `leads/models.py`.
- **No request-id or trace correlation.** The `voice_events` logger has no timestamps.
- There is no way to observe request volume, latency, or failure rates in production.

---

## 14. Performance baseline

### 14.1 Measured **[docs]** — from real runs/RCAs

| Metric | Value |
|---|---|
| WhatsApp voice-note round trip | **11–18s** vs Twilio's **15s** webhook timeout — *already over* |
| TTS real-time factor (CPU Kokoro) | **0.69x** — 779 chars → 53.2s audio took 36.6s to generate |
| TTS benchmarks | 168ch→12.7s; 371ch→18.6s; 652ch→28.3s |
| Cosine distances | valid query **0.35** vs off-topic **0.32** — overlap too large for a global threshold |
| VRAM (qwen2.5:7b q3_K_M) | 3.46 GB model, **5.11 / 6.00 GB peak (85%)** |
| Vector store | 4,618 → 5,346 polluted chunks → **28 clean** after rebuild |
| Startup | FastAPI 30–60s; Streamlit first load ~15s; cloud cold start 15–20 min |

### 14.2 Estimated **[docs]** — design targets, not measurements

| Metric | Value |
|---|---|
| **Voice turn today** | **12–35s** (VAD 600ms + STT 2–4s + RAG 0.3–1s + LLM 5–10s + TTS 15–30s) |
| **TTS share of latency** | **~80%** |
| **Design goal** | ~200ms STT + ~500ms RAG/LLM + ~200ms TTS ≈ **~1s/turn** |
| Ollama vs vLLM | ~10 tok/s → **50–100 tok/s**; GPU util 40% → 90% |
| Kokoro CPU → CUDA | 15–30s → **2–5s** |
| Whisper base → small.en | 2–4s → **0.5–1s** |
| Platform totals (NVIDIA/Metal/CPU) | 0.31 / 0.39 / 2.80s |
| Cloud VRAM formula | `6.65 + (users × 0.75) GB` |
| Cloud cost tiers | $337/mo (5–7 users) → $816 → $1,618 → $3,236/mo (50+) |

**Hardware baseline:** RTX 2060 **6 GB**, Ryzen 5 3500 6-core, 32 GB RAM, Windows 11 Pro,
CUDA 13.0. The 6 GB VRAM ceiling is the hardest constraint in the system — it is *why* the
model is quantized to `q3_K_M` with accepted accuracy loss. Apple Silicon: M3 Max 36 GB
via MLX (Qwen2.5-14B-Instruct-4bit).

---

## 15. Known defects and doc contradictions

### 15.1 Open defects

| # | Defect |
|---|---|
| D1 | WhatsApp **voice notes rejected outright** in code (`main.py:1381-1387`) despite docs claiming the feature works |
| D2 | Email never sends — SMTP creds empty, silently skipped |
| D3 | Voice-call sentiment saved with `lead_id=""`; Streamlit conversations never logged at all |
| D4 | Inbound caller phone number never threaded through the WebSocket → post-call follow-up impossible for inbound |
| D5 | Combined `pytest tests/` crashes on Windows (pandas/pyarrow heap corruption); per-file runs pass |
| D6 | Connection leak in `offers/models.py:38-58` |
| D7 | `OFFER_GUARD_MINUTES=1` vs docstring's "24h" |
| D8 | Offer PDF signed "Dr. A. Chancellor" (should be Dr. Helena Cross) |
| D9 | `app/sentiment/__init__.py:15` exports a nonexistent name |

### 15.2 Security gaps

| # | Gap |
|---|---|
| S1 | **No Twilio webhook signature validation anywhere** — inbound calls, WhatsApp messages and status callbacks are all forgeable |
| S2 | **No auth on any `/api/*`** — including `/api/demo/reset`, which deletes all tables |
| S3 | Student documents and offer PDFs served **unauthenticated** at guessable URLs |
| S4 | No auth on `/mcp/*` |
| S5 | **Hardcoded DB credentials in source** (`seed_demo_data.py:7-10`, `docker-compose.yml`) |
| S6 | A **live tunnel hostname is committed** to the repo in `.whatsapp_tunnel` |
| S7 | Health endpoint leaks the Twilio phone number and DB state |
| S8 | Twilio error text leaks account state publicly (`outbound/caller.py:34-58`) |
| S9 | No PII retention policy, no encryption at rest beyond DB default, no audit log |

### 15.3 Doc corpus warnings

The ~60-doc markdown corpus is **~2/3 stale by design** — most of it describes the retired
UMD/FDU dataset and was deliberately left as history. **Do not quote pre-2026-08-14 docs as
current behavior.** Specific contradictions found:

- "PRODUCTION READY" claims (`PROJECT_COMPLETE.md`, `README_DEPLOYMENT.md`) coexist with
  contemporaneous docs listing unfixed defects. **The RCAs are the honest record.**
- Model identity: planning docs say `qwen2.5:6b-instruct-q4_K_M`; every runtime doc says
  `7b-instruct-q3_K_M`; live `.env` says `qwen2.5:14b`.
- Chunking: three different numbers documented (800/150, 1500/100, 600/90). Code says **600/90**.
- Test counts: 42 / 127 / 132 / 44+33 / 201 across five docs — no reconciliation.
- MCP: one doc calls it a "REST stub", others describe working MCP tools. **Code says REST stub.**
- `doc/Sentiment_analysis/INTEGRATION_GUIDE.md` documents a Kafka/Temporal/Deepgram/Hume/
  LightGBM platform that **appears nowhere in this repo** — unresolved whether it is a real
  system, a plan, or a pitch. **Needs clarification from the owner.**
- `.env.example` documents `RAG_SIMILARITY_THRESHOLD=0.5`; it is live at **0.0 (off)** with
  measured justification. Do not cargo-cult 0.5.

**Best current-state docs:** `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md` (2026-08-13)
and `doc/RAGPIPLINE/RAG_Update_Status_Done_Pending.md` (2026-08-14/15).

---

## 16. Assets worth preserving in any rewrite

Not everything here is debt. These are good and should survive:

1. `app/hardware_profile.py` + `platform.py` + `memory_budget.py` — a real, working
   cross-platform capability-detection and model-sizing layer (§11).
2. **One retrieval choke point** (`query_rag`/`get_retriever`) — a single natural seam for
   tenant-awareness (§6.3).
3. `FOR UPDATE SKIP LOCKED` claim in `get_next_queued_call` — already replica-safe.
4. The RAG **ingestion validation gate** pattern — the Meridian-specific markers are wrong,
   but build-time corpus validation with smoke tests and a negative canary is genuinely good
   design worth generalizing per tenant.
5. `app/messaging.py` — extracted specifically to break a real import cycle; the pattern works.
6. Anti-hallucination work: `scrub_meta_leak`, `condition_on_previous_text=False`, the
   grounding rules, and the measured evidence for keeping the similarity threshold off.
7. The RCA corpus — six genuine root-cause analyses with real numbers.
