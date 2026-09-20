> **Lens:** TPO + Architect · **Decided by:** TPO (resolve-now vs accept) + Architect (drift and coupling) · **Inputs:** all prior stages + the live codebase · **Engagement:** Brownfield · **Defines:** REC-01 … REC-13

> **Evidence pinning — read this before trusting any line number below.**
> Every `file:line` reference in this document was read at **one commit and one runtime state**: branch `perf/phase-a`, working tree at 2026-09-18, with 8 services running and `qwen2.5:14b` evicted (default 5-minute keep-alive).
> **A line number is only valid for that snapshot.** After any commit, re-verify by symbol name, not by line. Each `REC-xx` cites a symbol or file where it makes a claim that matters.
> Runtime versions at capture: Python 3.11 (project `.venv`), Ollama on `:11434`, faster-whisper `small.en` (CUDA/int8), Kokoro ONNX, Chroma local + ERC, Postgres/Redis via Docker, driver 610.88 / CUDA 13.3.
> **Measured figures carry their own timestamp** (all 2026-09-18) and are not re-derived by this document — see `01-brd.md` §6 Evidence Register.

# Brownfield Reconciliation — Admissions Voice Assistant Performance & Concurrency Program

## 1. Asset Classification

| Asset | Location | Class | Action |
|---|---|---|---|
| `VoiceCallSession` turn loop | `app/voice_handler.py` | **Refactor** | Add streaming consumption + stage marks; the serial structure is the defect |
| RMS endpoint detector | `voice_handler.py:341,367` | **Reusable** | Correct for its purpose; only its *value* is in question (`BRD-04`) |
| Noise gate + closing detection | `voice_handler.py:451,469` | **Reusable** | Deterministic short-circuits; they save whole model calls |
| Conversation history | `voice_handler.py:301,605` | **Reusable** | Correctly windowed to 6; not the prefill problem |
| `app/main.py` WS handler | `main.py:495–690` | **Refactor** | Owns the AEC guard (`CV-01`) and must emit the first-audio mark |
| AEC / `MUTE_STT_DURING_TTS` guard | `main.py:87–91,624–627` | **Debt** | Works around a missing carrier feature; blocks barge-in (`BRD-19`) — decision owned by `UC-09` |
| `llm_backend.chat` / `_chat_ollama` | `llm_backend.py:135–175` | **Refactor** | Non-streaming is the largest single latency term; add streaming + counter capture |
| `pick_model()` per-call `/api/tags` | `llm_backend.py:180–204` | **Debt** | Uncached HTTP round trip per utterance — **resolve now** (trivial, Class A) |
| RAG dispatcher + fallback | `app/rag.py:93–205` | **Reusable** | The MCP-first/legacy-fallback seam is sound; keep it |
| MCP client | `app/rag_mcp.py` | **Refactor** | New `httpx.Client` per request; binary breaker with no half-open probe |
| Legacy Chroma retrieval | `app/rag_legacy.py` | **Refactor** | Rebuilds a `PersistentClient` per call; the store itself is `DG-01`'s subject |
| ERC service | `D:\project\enterprise-rag-core` | **Refactor** | Single event loop; synchronous "parallel" legs — the `BRD-07` blocker |
| Reranker (ONNX) | ERC `config.py:196–198` | **Debt** | 23.2 MB loaded at boot, never invoked — **accept or remove** (`UC-10` decides wiring) |
| Semantic cache | ERC `cache.py` | **Debt** | Configured, unreachable from `retrieve_context` — same call |
| TTS `kokoro.create()` | `voice_handler.py:673` | **Refactor** | Batch API; `create_stream()` exists in the installed library |
| TTS cache | `voice_handler.py:655` | **Debt** | Process-wide and shared across callers — needs the `DG-06` isolation test |
| Lead/CRM pipeline | `app/leads/`, `app/crm/`, `database.py` | **Reusable** | Correctly post-call; deliberately untouched |
| `start_services.ps1` | repo root | **Refactor** | Add preload + warm; reconcile config writers (`DG-05`) |
| Pipecat 1.6.0 | `requirements.txt:33` | **Debt** | Installed, pinned, entirely bypassed — see `REC-01` |
| Per-stage tracing | — | **Net-new** | `MOD-06`; does not exist |
| Load harness | — | **Net-new** | `MOD-06`; does not exist |
| Golden set | — | **Net-new** | `MOD-06`; does not exist |
| Call/interruption state machines | — | **Net-new** | Derived from code in Stage 4; no explicit SM exists in the codebase |

## 2. Reconciliation Notes

### REC-01 — Pipecat is pinned, installed, and entirely bypassed
- **Conflict:** the stack description (and `requirements.txt:33`) presents Pipecat as the voice framework; the live Twilio path never constructs a pipeline. `create_local_voice_pipeline` has exactly one caller — `run_pipeline_test.py`. The `VADParams` block at `pipeline.py:174–180` builds an analyzer that is never referenced again.
- **Decision:** **follow the codebase** — the plan targets the hand-rolled loop. Pipecat is *not* adopted in this program. Its `KokoroTTSService` already uses `create_stream()`, so it remains a live candidate for `UC-10` if hand-rolled streaming proves insufficient.
- **Lens:** TPO (effort: wiring Pipecat in is a re-architecture, not a config change) + Architect (risk: it would replace a working, measured path with an unmeasured one).
- **Affected artifacts:** `01-brd.md` (`BRD-04`, `BRD-19`), `05-modularization.md` (`MOD-01`, `MOD-04`), `06-architecture.md` §4.

### REC-02 — `FASTAPI_WORKERS=4` is written but never read
- **Conflict:** the machine profile and `.env:183` both state 4 workers; `start_services.ps1:559` launches uvicorn with no `--workers` argument, so the app runs as **one** process. Every repo reference to the key is on the writer side (`hardware_profile.py`).
- **Decision:** **follow the codebase** — the plan assumes one process and one event loop. The key is classified as debt under `DG-05`; it is removed or wired, never trusted.
- **Lens:** TPO — this caused a wrong capacity conclusion earlier in the program; "set ≠ live" is now a standing rule.
- **Affected artifacts:** `01-brd.md` (Evidence Register), `06-architecture.md` §5 (SPOF).

### REC-03 — Two divergent knowledge stores
- **Conflict:** the plan (and the docs) imply one knowledge base. There are two: local Chroma collection `langchain` (~47 records, LangChain splitter) and ERC Chroma collection `meridian-kb` (37 chunks, custom paragraph packer), both derived from the same 11,214-byte source with **different chunk boundaries and different citation formatting**.
- **Decision:** **diverge deliberately** — consolidate to one authoritative store (`DG-01`). The fallback path currently changes not just the chunks but whether section labels appear at all (`rag_legacy.py:443` vs `rag_mcp.py:169–178`).
- **Lens:** TPO (highest-effort item; sequenced independently of streaming) + Architect (correctness over redundancy).
- **Affected artifacts:** `03-data-state-analysis.md` (`DAT-02`, `DAT-03`, `DG-01`), `05-modularization.md` (`MOD-02`).

### REC-04 — Reranker and semantic cache are loaded and unreachable
- **Conflict:** ERC constructs a 23.2 MB INT8 cross-encoder ONNX session at boot and supports a Redis semantic cache; both are consulted **only** inside `execute_agent_context`, which appears nowhere in this repo. The app calls exactly one tool, `retrieve_context`, which bypasses both.
- **Decision:** **follow the codebase** — the plan does not rely on either. They are classified as debt: remove from boot, or wire in — `UC-10` owns the call, and it cannot be made without the frozen golden set.
- **Lens:** TPO — they are pure cost today (boot time, memory) with zero benefit.
- **Affected artifacts:** `06-architecture.md` §4 (Build vs Buy).

### REC-05 — `.machine_profile.json` is never read at runtime
- **Conflict:** the plan's early assumptions treated the machine profile as authoritative config. It is read only by `check_drift()`, which compares `detected` hardware fields only — never the `applied` block. So `WHISPER_NUM_THREADS` 4 (`.env`) vs 6 (profile) is invisible and has **zero runtime effect**.
- **Decision:** **follow the codebase** — `.env` is the runtime truth; the profile is a detection artifact. `cpu_threads` is additionally inert on the CUDA path.
- **Lens:** TPO — closes an investigation thread that would otherwise consume budget.
- **Affected artifacts:** `03-data-state-analysis.md` (`DAT-09`, `DG-05`), `01-brd.md` Evidence Register.

### REC-06 — `doc/model_vram_analysis.md` describes a different machine
- **Conflict:** that document budgets for a **6 GB** GPU and recommends `qwen2.5:7b-instruct-q3_K_M` at `num_ctx=2048`. The live machine has **16 GB** and runs `qwen2.5:14b` at `num_ctx=8192`.
- **Decision:** **follow the codebase** — the document is stale and must not be used as evidence. Flagged so a future session does not re-derive from it.
- **Lens:** TPO — documentation drift that already risks a wrong model decision.
- **Affected artifacts:** `00-product-intent.md` (evidence base note), `06-architecture.md` §7.

### REC-07 — `doc/RAGPIPLINE/RAG_Pipeline_Report.md` carries wrong figures
- **Conflict:** it claims a `top_k=2` in `pipeline.py` (no such value exists anywhere in `app/`), calls `RAG_SIMILARITY_THRESHOLD` a "DEAD CONFIG — never read" (it is read at `rag_legacy.py:78` and gated at `rag.py:163`), and states `OLLAMA_NUM_CTX` defaults to 2048 (actual default 8192). It predates the three-module RAG split.
- **Decision:** **follow the codebase** — the document is excluded as an evidence source for this program.
- **Lens:** TPO.
- **Affected artifacts:** `01-brd.md` Evidence Register.

### REC-08 — The relevance gate is disabled, and enabling it doubles retrieval traffic
- **Conflict:** `RAG_SIMILARITY_THRESHOLD=0.0` disables the gate at `rag.py:163`. The plan's `BRD-10` requires a floor. The implementation detail the plan must respect: `_threshold_distance` issues a **second** MCP `tools/call` with `top_k=1` — so enabling the gate naively doubles per-turn retrieval cost.
- **Decision:** **refactor** — `BRD-10` stands, but the implementation must not pay a second round trip per turn. Fold the distance check into the existing retrieval response.
- **Lens:** Architect — a requirement whose obvious implementation contradicts a performance goal.
- **Affected artifacts:** `01-brd.md` (`BRD-10`), `05-modularization.md` (`MOD-02`).

### REC-09 — `/ws/voice` is an echo stub; a second text path shares the RAG code
- **Conflict:** `main.py:402–410` echoes input back ("transport validation"). Separately, `/ws/voice/text` (`main.py:466`) calls `build_rag_prompt, test_pipeline_with_text` and shares `run_rag_query_sync` with the voice path.
- **Decision:** **follow the codebase** — the plan targets `/ws/twilio` only. The text path shares `MOD-02`/`MOD-03`, so changes there must not regress it; the echo stub is out of scope.
- **Lens:** Architect — a shared dependency that makes "voice-only" changes non-isolated.
- **Affected artifacts:** `02-use-cases-workflows.md` (Stage 3 Data/Workflows gap), `05-modularization.md` (`MOD-02`, `MOD-03`).

### REC-10 — No explicit call or turn state machine exists in code
- **Conflict:** the plan's `SM-01`/`SM-02` describe states the code never names. Current state is implicit in control flow and a `_silence_count` integer.
- **Decision:** **diverge deliberately** — the state machines are analytical instruments for this program, not a refactor mandate. They are not required to become code. If streaming lands, `SM-02` becomes partially real (cancellable states), and that is recorded then.
- **Lens:** Architect — inventing a state enum where none is needed is scope creep.
- **Affected artifacts:** `03-data-state-analysis.md` Part B.

### REC-11 — Model pre-warm exists, runs, and is neutralised by the first serving call
- **Conflict:** the plan's `06-architecture.md` §2 (MOD-07) originally stated "Model preload — not present today". The codebase **does** pre-warm: `start_services.ps1:659–702` enumerates `/api/tags` and POSTs a one-token `/api/generate` with `keep_alive=24h` per completion-capable model. An earlier PowerShell 5.1 bug that made this skip silently — the comment at lines 672–675 documents it — **has already been fixed** at line 676 (`curl.exe | Out-String` + `ConvertFrom-Json`, replacing the broken native-to-native pipe into python).
- **The real defect is narrower and sharper:** `_chat_ollama` (`app/llm_backend.py:135–146`) sends only `num_ctx` and `temperature`. Ollama resets a model's keep-alive to the server default on any request that omits it, so **the first real chat after boot discards the 24 h pre-warm and reinstates the 5-minute default.** `OLLAMA_KEEP_ALIVE` is set nowhere in `.env` and read nowhere in `app/`. This is exactly why a 32,919 ms cold load was measured *with working pre-warm code in the repository*.
- **Decision:** **follow the codebase, correct the plan** — `06-architecture.md` §2 and the MOD-07 component flow now describe a pre-warm that exists but is undone. The requirement does not change (`BRD-17`); the fix does: it is not "add a preload" but "**hold residency on the serving path, and make the warm state verifiable**". That is `TRD-12`'s revised scope.
- **Lens:** TPO (a plan claim contradicted by committed code) + Architect (the residency window is a serving-path concern, not a boot concern).
- **Affected artifacts:** `06-architecture.md` §2 (MOD-07 flow), `modules/MOD-03-inference-serving.md` (B.8), `01-brd.md` (`BRD-17`).
- **Pattern note:** this is the **fifth** instance of "set ≠ live" in this codebase, after `REC-01` (Pipecat), `REC-02` (`FASTAPI_WORKERS`), `REC-05` (`.machine_profile.json`) and the pre-warm pipe bug. It is now a standing review rule, not an anecdote.

### REC-12 — An untracked tracing draft exists, and it cannot compute the metric BRD-01 names first
- **Conflict:** `07-brownfield-reconciliation.md` §1 classifies per-stage tracing as **Net-new** and `DAT-07` as **Missing**. In fact `app/perf_trace.py` exists in the working tree — 3,882 bytes, **untracked in git** — written earlier in this program as an aborted first attempt at Phase A instrumentation. It is inert: nothing imports it, `voice_handler.py` was reverted to HEAD, and no trace has ever been emitted. The classification "net-new" remains true of the *committed codebase*; it is false as a statement about the working tree, and a future session would find the file and be misled.
- **Sharper defect in the same file:** its `STAGES` tuple is `vad_end, stt_done, llm_sent, llm_done, tts_done, first_audio_sent` — **there is no retrieval mark**, and no engine counters are captured. `BRD-01`'s *first* success criterion names `retrieval_ms`, and `retrieval_ms` is uncomputable from this emitter. Likewise the measured 1,588–1,990 ms prefill and 36.6–38.9 tok/s decode cannot be separated from trace data, because `llm_sent → llm_done` brackets a single blocking `ollama.chat()` call.
- **Decision:** **correct the plan, keep the file as a starting point.** `TRD-21` states the honesty contract (a stage that was not measured is *absent*, never zero) and `TRD-20`/`TRD-22` add the TTFT mark and counter capture that make retrieval, prefill and decode separable. The draft is a usable skeleton, not a deliverable: it must be re-based onto the branch and completed, not trusted.
- **Lens:** TPO (a plan claim contradicted by the working tree) + Architect (the missing mark is a measurement-design gap, not an oversight in passing).
- **Affected artifacts:** `07-brownfield-reconciliation.md` §1 and §4, `03-data-state-analysis.md` (`DAT-07`, `DG-04`), `modules/MOD-06-observability-evaluation.md` (B.8, TRD-20…23), `01-brd.md` (`BRD-01`).
- **Consequence for Phase A:** the first deliverable is not "write a tracer" but "**finish and re-base the tracer so the metric the BRD names first is actually computable**."

### REC-13 — The memory-budget guard is stale and its threshold sits above the ceiling the BRD sets
- **Conflict:** `app/memory_budget.py:22–33` sizes the `"nvidia"` platform for a **6 GB-class** machine: `min_required_gb: 5.5`, `recommended_gb: 6.0`, `qwen_llm_gb: 4.0`, `peak_total_gb: 5.7`. The live machine has **16,311 MiB** and runs a **~9.0 GiB** model — the LLM figure is understated by more than 2×. This extends `REC-06` (the stale `model_vram_analysis.md`) into executable code.
- **The sharp part:** `safe_threshold_percent: 95` sets the alert threshold **above** `BRD-11`'s 90% VRAM ceiling. The guard therefore cannot fire until after the ceiling the requirement defines has already been breached — a monitoring control that is structurally incapable of protecting its own requirement.
- **Decision:** **follow the requirement, fix the guard** — `BRD-11` owns the 90% ceiling; the threshold derives from it, not the reverse. Recorded, not changed: no code was modified.
- **Lens:** TPO (resolve-now, low effort) + Architect (the guard encodes an assumed machine that no longer exists).
- **Affected artifacts:** `modules/MOD-04-speech-services.md` (B.8), `modules/MOD-07-config-boot.md` (TRD-25 inert-key disposition), `01-brd.md` (`BRD-11`, `BRD-12`).

## 3. Architectural Drift Summary

| Drift | Docs claim | Code does | Consequence for the plan |
|---|---|---|---|
| Voice framework | Pipecat pipeline | Hand-rolled loop | `REC-01` — target the loop |
| Worker count | 4 workers | 1 process | `REC-02` — one event loop, not four |
| Knowledge stores | one KB | two divergent stores | `REC-03` — correctness issue |
| Retrieval features | reranking, caching | loaded, unreachable | `REC-04` — pure cost |
| Config authority | machine profile | `.env` (profile unread) | `REC-05` — `.env` is truth |
| Model sizing | 6 GB / 7B / ctx 2048 | 16 GB / 14B / ctx 8192 | `REC-06` — stale doc |
| RAG parameters | `top_k=2`, threshold dead | `top_k=5`, threshold read-but-0.0 | `REC-07`, `REC-08` |
| Endpointing | 500 ms (Pipecat) | 600 ms (RMS gate) | Zero-cost dead code; `BRD-04` |
| Model preload | "not present" (plan) | Step 6 pre-warms with `keep_alive=24h`, then the first chat resets it to 5 min | `REC-11` — fix is on the serving path, not at boot |
| Tracing capability | "net-new" (plan) | `app/perf_trace.py` exists untracked; inert, and cannot compute `retrieval_ms` | `REC-12` — re-base and complete, don't rewrite |
| VRAM guard | budget sized for this box | `memory_budget.py` sizes a 6 GB machine; alert at 95% vs BRD-11's 90% ceiling | `REC-13` — the guard cannot protect its own requirement |

## 4. Debt: Resolve Now vs Accept

| Debt | Call | Rationale |
|---|---|---|
| Uncached `/api/tags` per utterance (`REC-02`-adjacent) | **Resolve now** | Trivial, Class A, pure win |
| An identical prompt string is rebuilt per turn | **Resolve now** | Falls out of the streaming refactor |
| Reranker + semantic cache loaded at boot (23.2 MB) | **Resolve now** (remove) | Zero benefit today; re-add only when `UC-10` justifies wiring |
| Four inert config keys | **Resolve now** | "Set ≠ live" already produced one wrong conclusion |
| Two divergent stores | **Resolve in `MOD-02`** | Highest-effort correctness item; sequenced separately |
| Pipecat dependency | **Accept for this program** | Removing or wiring it is a re-architecture; `REC-01` defers it |
| AEC guard blocking barge-in | **Accept pending `UC-09`** | The trade (agent hearing itself) needs a PO decision, not an engineering default |
| No `SM-xx` in code | **Accept** | Analytical instruments; not a refactor mandate (`REC-10`) |
| No session idle timeout | **Accept** | Costs a held session, not inference (`SM-01` Q10) |
