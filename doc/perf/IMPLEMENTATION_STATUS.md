> **Branch:** `perf/phase-a` · **Started:** 2026-09-18 · **Rule:** a story is `DONE` only with runnable evidence, never on inspection.

# Implementation Status — Admissions Voice Assistant Program

## Status vocabulary

| Status | Means |
|---|---|
| `NOT STARTED` | No code written |
| `IN PROGRESS` | Code written, evidence incomplete |
| `DONE` | Acceptance tests pass **and** the evidence is recorded below |
| `BLOCKED` | Cannot proceed — names the blocker |
| `AWAITING SIGN-OFF` | Engineering complete; a human decision is required |

**Class** is from `01-brd.md` §5. **Class A** changes no behaviour and needs no quality gate. **Class B** changes what the model sees or says — gated by `DG-03`. **Class C** changes user-perceivable behaviour — needs PO sign-off.

## Dependency order and status

| # | Story | Module | Class | Depends on | Status | Evidence |
|---|---|---|---|---|---|---|
| **US-001** | Turn tracing marks + records | MOD-06 | **A** | — | **DONE** | `doc/perf/tools/test_us001_tracer.py` → **15 passed, 0 failed**. Files: `app/perf_trace.py` (re-based per `REC-12`), `app/voice_handler.py`, `app/main.py`, `app/rag.py`, `app/llm_backend.py` |
| **US-011** | Single config source of truth | MOD-07 | **A** | — | **DONE** | `doc/perf/tools/test_us011_config_truth.py` → **16 passed, 0 failed**. New: `app/config_truth.py`. **Found 3 inert keys the plan did not know about** |
| **US-007** | Boot warm state verifiable | MOD-07 | A | — | NOT STARTED | — |
| **US-006** | Hold model residency (serving path) | MOD-03 | **A** | — | **DONE** | `doc/perf/tools/test_us006_residency.py` → **11 passed, 0 failed**. External evidence: `ollama ps` shows `Forever`, not the 5-min default. **Test caught a bug that would have broken every call** |
| **US-002** | N=1/N=2 load harness | MOD-06 | A | US-001 | **IN PROGRESS — runs, 2 gaps** | `doc/perf/tools/load_harness.py` drives the live `/ws/twilio` and measured first audio at **1,875 ms**. Gaps: **TAC-1 framing FAILS** on this box; **synthetic fixtures transcribe to empty**, so the LLM path is not exercised |
| **US-013** | Bounded calls + half-open probe | MOD-02 | A | — | NOT STARTED | — |
| **US-016** | Two-caller admission control | MOD-01 | A/B | US-002 | NOT STARTED | — |
| **US-017** | Background-load priority | MOD-01 | A/B | US-002 | NOT STARTED | — |
| **US-012** | TTS cache isolation | MOD-04 | B | US-002 | NOT STARTED | — |
| **US-008** | De-serialize retrieval | MOD-02 | **A/B** | US-002 | **DONE** | Both ERC legs off-loaded from the event loop (`chroma_vector.py`, `bm25_memory.py`). Retrieval mean 7,164 → ~2,227 ms measured. **Also exposed `llm_queue_ms` as the true N=2 bottleneck** |
| **US-009** | Relevance floor, one round trip | MOD-02 | **B** | US-002, `DG-03` | **BLOCKED** | `DG-03` — changes retrieved context |
| **US-010** | Consolidate to one store | MOD-02 | **B** | `DG-03` | **BLOCKED** | `DG-03` — changes answers |
| **US-018** | RAG baseline + optimized | MOD-02 | **B** | `DG-03` | **BLOCKED** | `DG-03`; baseline characterisation can start |
| **US-004** | Stream LLM generation | MOD-03 | **B** | US-001, US-002 | **BLOCKED** | `DG-03` — changes what the model returns |
| **US-005** | Stream TTS synthesis | MOD-04 | **B** | US-002 | **BLOCKED** | `DG-03` — changes what the caller hears |
| **US-003** | Frozen golden set + eval runner | MOD-06 | A | — | **AWAITING SIGN-OFF** | `eval/` built: 161 cases, 28/28 intents, `checks.py` exit 0. **137 of 161 ground truths PENDING. 12/12 critical intents BLOCKED.** Cannot freeze without PO approval |
| **US-014** | Interruption decision | MOD-01 | **C** | — | **AWAITING SIGN-OFF** | `doc/perf/decisions/US-014-caller-interruption.md` — 3 options costed, recommendation recorded, nothing adopted |
| **US-015** | Model/runtime benchmark | MOD-03 | C | `DG-03` | **PARTIAL** | `doc/perf/us015-benchmark-results.md` — latency leg measured; **quality leg blocked**. Two plan assumptions falsified |

## Unblocked work queue (Class A, no `DG-03` dependency)

US-001 ✅ → **US-011** → **US-007** → **US-006** → **US-002** → **US-013** → US-016, US-017 → US-012.
Everything from US-009 onward is quality-gated or PO-gated.

## What is blocked and why

| Blocker | Blocks | Owner |
|---|---|---|
| `DG-03` — 12 critical-intent ground truths unapproved | US-003 freeze, US-004, US-005, US-009, US-010, US-018, US-015 quality leg | **PO** |
| `US-014` Class C decision | US-014 adoption, prompt alignment | **PO** |

---

## US-001 — evidence detail

**Delivered.** A re-based tracer (`REC-12` required re-basing, not rewriting) with a **retrieval mark** and a **first-token stage** added, engine-counter capture, and an explicit `stages_seen`.

**Acceptance test:** `./.venv/Scripts/python.exe doc/perf/tools/test_us001_tracer.py` → **15 passed, 0 failed**.

| Criterion | Result |
|---|---|
| TAC-3 no `app.*` imports (structural, not tested-by-hope) | PASS — AST scan |
| TAC-4 no caller PII in the record | PASS |
| TAC-6 absent is not zero; `stages_expected` always present | PASS |
| T-2 ≥6 stages, consecutive segments reconcile to `total_ms` within 5 ms | PASS |
| T-7 `retrieval_ms` derived from engine counters | PASS — **this is the metric `REC-12` found uncomputable** |
| T-8 counters missing → `retrieval_ms` **omitted**, not guessed | PASS |
| T-10 concurrent traces isolated (`BRD-06`) | PASS |
| T-11 misuse never raises | PASS |
| T-12 negative retrieval **flagged**, not recorded as fact | PASS |

**Two deliberate non-implementations, both honesty requirements rather than omissions:**

1. **`llm_first_token` is never marked.** The LLM call is non-streaming (`llm_backend.py:145`), so no first-token event exists. Marking `llm_sent`'s instant would fabricate a TTFT. Its absence is the honest signal that TTFT is not yet measurable — and it starts appearing when `US-004` lands.
2. **`retrieval_ms` is omitted when the engine counters do not arrive**, rather than reported as 0.

**Not yet proven (requires a live call):** TAC-1 (tracing overhead ≤5 ms p95), TAC-5 (N=2 soak stability), and the end-to-end propagation test — whether the `ContextVar` actually survives `asyncio.to_thread` into the RAG worker. The unit test proves isolation and marking in-process; only a real call proves the thread hop. **This is the first item on the US-002 harness.**

**Operational note:** the 8 running services still execute the pre-change modules. The tracing is inert until they are restarted.

---

## US-011 — evidence detail

**Delivered.** `app/config_truth.py`, runnable as `python -m app.config_truth`. Reports every key's effective value **and its provenance**, sweeps inert keys, validates types at start, and reports direct `os.environ` reads.

**Acceptance test:** `./.venv/Scripts/python.exe doc/perf/tools/test_us011_config_truth.py` → **16 passed, 0 failed**.

| Criterion | Result |
|---|---|
| AC-1 `.env` is the authority, every key carries provenance | PASS |
| TAC-2 inert keys found and named | PASS |
| TAC-4 **no secret value** in the report (values scanned, not assumed) | PASS — sensitive keys render as `<present>` |
| TAC-5 malformed values named, never silently defaulted | PASS |
| TAC-1 direct env reads **reported, not claimed fixed** | PASS — 18 files, target is zero |

### What this found that the plan did not have

The plan recorded **one** inert key (`FASTAPI_WORKERS`, `REC-02`) among "six instances of set ≠ live". The sweep finds **four** genuinely inert:

| Key | Why it is inert |
|---|---|
| `FASTAPI_WORKERS` | Known (`REC-02`). Uvicorn is launched with no `--workers` |
| **`KOKORO_SPEED`** | **New.** `voice_handler.py:131` and `:707` both hardcode `speed=1.0`. A voice-tuning knob that does nothing |
| **`LOG_FILE`** | **New.** Written in `.env`; read by no Python code. Log destination is not configurable despite appearing to be |
| **`LOG_LEVEL`** | **New.** Same |

**Seven further keys are consumed by *other* processes** — `PYTHON*` by the interpreter, `STREAMLIT_*` by Streamlit, `FASTAPI_HOST`/`PORT` by the PowerShell launcher. A naive "not referenced in `app/`" sweep calls these inert, which would have buried the four real findings among false positives. They are reported **separately** for that reason, and a test asserts they are not miscounted.

**TAC-1 is reported as unmet, not claimed.** 18 files read `os.environ` directly rather than through `app/config.py`. The refactor is not part of US-011, and asserting compliance without doing it would be the exact "set is not live" failure this module exists to catch. The report states the target rather than implying it is met.

**Note on `TAC-3`.** It asks that `FASTAPI_WORKERS` "resolve to the truth or be removed/renamed". The key is now *reported* as inert by name, which satisfies the reporting half. Removing it is a config edit to a file `scripts/predeploy.py` generates — deliberately not done here, and flagged for the PO.

---

## US-006 — evidence detail

**Delivered.** `REC-11`'s fix on the **serving path**: `_chat_ollama` now sends `keep_alive` on every generation request, resolved from `OLLAMA_KEEP_ALIVE` (added to `.env`, backed up first as `.env.bak-pre-us006`). The trace also carries `keep_alive` and a `residency_lapse` flag, so an eviction that happens anyway is visible rather than silent.

**Acceptance test:** `./.venv/Scripts/python.exe doc/perf/tools/test_us006_residency.py` → **11 passed, 0 failed**.

**External evidence (TAC-4 — this is the criterion that matters):**

```
ollama ps -> qwen2.5:14b  7cdf5a0187d5  9.1 GB  100% GPU  2048  Forever
```

The `UNTIL` column reads **`Forever`**. Unchanged, it would read the 5-minute server default — which is the residency lapse `REC-11` describes and the mechanism behind the measured **32,919 ms** cold load. Residency is observed from the engine's own report, **not** from a success line the boot script printed.

### A bug the test caught that inspection would not have

The first implementation was the obvious one:

```python
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "-1")
```

**Every `.env` value is a string, and Ollama's duration parser rejects `"-1"`** with `ResponseError: time: missing unit in duration "-1"` — HTTP 400. The naive fix would not have *degraded* residency; it would have **thrown on every single chat request**, taking the voice line down completely. `REC-11`'s description ("pass keep_alive") reads as a one-liner; it is not.

Fixed by `_resolve_keep_alive()`, which sends an integer as an int and keeps a duration like `"24h"` as a string — with a regression test asserting both.

This is the second time in this implementation phase that a plausible-looking change was wrong in a way only execution revealed (the benchmark's warm-cache illusion was the first).

---

## US-002 — evidence detail

**Delivered.** `doc/perf/tools/load_harness.py` — drives the live `/ws/twilio` WebSocket at 8 kHz µ-law / 20 ms framing, N=1 and N=2, versioned fixtures, 7 named network profiles (delay, jitter, loss, reorder, disconnect/reconnect, composite), run summaries with fixture sha256, and an explicit `CARRIER BOUNDARY: Twilio round trip EXCLUDED` line.

**Live run (N=1, 1 turn, `fees-01`):** exit 0. **First audio 1,875.0 ms**, 0 turns over 3,000 ms, records separated, correctly self-labelled **UNDERPOWERED** (1 sample vs the ≥100 `BRD-02` needs).

**Fixture/app contract verified:** the harness's own `silence_threshold_frames=30`, `max_utterance_frames=300`, `MIN_UTTERANCE_FRAMES=15` and RMS gate `80` all match the running app.

### The tracer is proven end-to-end

A real record was emitted from a live call:

```json
{"call_id":"MZc59c8e0b12f2131eff5c9a4e717228","turn_id":0,
 "vad_end_ms":0.0,"stt_done_ms":32.0,"tts_done_ms":1860.0,
 "first_audio_sent_ms":1860.0,"total_ms":1860.0,
 "stages_seen":4,"stages_expected":8,
 "vad_frames":130,"endpoint_ms":600,"stt_chars":0}
```

`stages_seen: 4` of 8 — and that is **correct behaviour, not a defect.** `stt_chars: 0` shows the transcript was empty, so the turn took the noise-gate path and the LLM was never called. The LLM, retrieval and first-token stages are **absent rather than zero**, which is exactly the `TRD-21` honesty contract working in production.

### Two gaps that block use as the Phase A baseline instrument

1. **`TAC-1` framing audit FAILS on this box.** 170 of 250 frames deviate more than 5 ms from their 20 ms slot, and 1 ms timer resolution is unavailable. This is a Windows timer-granularity limitation, reported by the harness's own self-test rather than hidden. It does not invalidate latency measurement (the app buffers and endpoints over 30 frames), but the harness is **not** a faithful carrier emulation and `TAC-1` is unmet.
2. **The synthetic fixtures transcribe to nothing.** They pass the RMS gate but are not real speech, so Whisper returns an empty transcript every time and the turn short-circuits at the noise gate. **The harness currently measures endpointing and TTS, but cannot exercise the retrieval, LLM or first-token path at all.** That is precisely the path `US-004`/`US-005` need measured, and it is why `US-001`'s thread-propagation question remains **unproven** — the LLM worker hop never ran.

**Consequence:** the harness is a working instrument for the endpointing/TTS half of the turn, and a placeholder for the rest.

### Fixture fix — applied, and it works

`doc/perf/tools/render_fixtures.py` renders each turn's scripted text through Kokoro (the agent's own voice) and down the carrier path: 24 kHz → 8 kHz µ-law. 9 turns across 4 fixtures, each with a sha256 manifest.

**Verified round trip:** text → Kokoro → 8 kHz µ-law → 16 kHz → Whisper = **0.98 similarity** to source. (The first test showed 0.2-ish similarity; it was wrong because it skipped the 8 kHz→16 kHz upsample the app performs.)

**The LLM path is now exercised.** A live turn produced `stages_seen: 7` with `stt_chars: 63`, `model_used: qwen2.5:14b`, `prompt_eval_count: 4410` — retrieval, prefill and generation all measured for the first time.

### Three tracer bugs found by running it — all mine, none visible to inspection

| Bug | Symptom | Fix |
|---|---|---|
| `STAGES` order wrong | `seg_retrieval_done__llm_sent = -2625 ms` — a negative segment | Retrieval happens *inside* the blocking LLM call, so `retrieval_done` fires **after** `llm_sent`. Reordered |
| `retrieval_ms` ignored model load | Read **11,668 ms** | The `llm_sent..llm_done` window contains FOUR things — load, retrieval, prefill, generation. Subtracting only three inflated the largest number in the record. Now 3,173 ms |
| `tts_done` missing on the cache-hit path | Segment `llm_done→tts_done` absent | Marked only the synthesis return, not the cache return — **exactly the error the `retrieve_context` wrapper was written to avoid**, repeated one file over |

All three were invisible until a live call produced a real record. The `retrieval_ms` one is the most instructive: it was the **largest figure in the trace** and it was an artefact.

### RESOLVED — the model was reloading because of `num_ctx` thrashing

**Root cause found and fixed.** A controlled ladder (`doc/perf/tools/diag_residency_interleave.py`) **falsified** the leading hypothesis — `chat → embed → chat` caches perfectly (load 4 ms, prefill 166 ms), so the embedding call was never the evictor — and then isolated the real one.

**Ollama's runner has a fixed context size, and a request carrying a DIFFERENT `num_ctx` forces the runner to be torn down and rebuilt.** Measured on `qwen2.5:14b`, alternating context sizes:

| `num_ctx` | `load_ms` |
|---|---|
| 8192 (voice answer) | 3 |
| 2048 (lead extraction) | **6,677** |
| 8192 again | **6,423** |
| 1024 (WhatsApp intent) | **6,658** |
| 8192 again | **6,215** |

The app ran **four** context sizes against the same model — 8192 for the voice answer, 2048 for lead extraction, 1024 for WhatsApp intent, 512 for sentiment — so **its own background calls evicted the voice path's runner and wiped the KV cache.** That is the `cached n_tokens = 0` in the server log, and that is the 6.4 s. **`keep_alive` was never the problem:** `ollama ps` reported `Forever` throughout, and the US-006 fix was correct but insufficient — it pinned a model that something else kept tearing down.

**Fix:** `SMALL_TASK_NUM_CTX=8192` in `.env`, pinning every chat call to one context size. One runner, one KV allocation, shared by all callers. Pinning *high* costs nothing extra here; pinning low would truncate voice answers.

**Result — measured, same fixture, same box:**

| | Before | After |
|---|---|---|
| `load_ms` (warm) | 6,133 ms | **4.7 ms** |
| Warm turn, first audio | 8,609 ms | **360 ms** |

**A 24× improvement on the warm turn**, from a one-line config change. `US-004`/`US-005` now have a stable baseline to measure against.

### Remaining on the cold turn — the real latency budget

| Stage | Cold turn |
|---|---|
| STT | 797 ms |
| Retrieval | 3,367 ms |
| Prefill | 2,490 ms |
| Generation | 811 ms |
| **TTS synthesis** | **6,141 ms** |

TTS synthesis is now the largest single term, and retrieval is second. Neither was visible before this diagnosis.

### One open issue

**Turn 1 of a 2-turn run still emits no app-side trace**, while turn 0 does — although the harness measures it from its own socket (360 ms). Unexplained; the measurement is not lost, but the trace is.

---

## N=2 CONCURRENCY — `BRD-05` currently FAILS, and the cause is not the LLM

Measured after all three fixes above. **N=2 warm p50 = 7,617 ms, p95 = 20,482 ms** against the 3,000 ms per-turn cap and `BRD-05`'s 1.5× interference budget. N=1 warm is 360 ms, so this is a **21× degradation**, not 1.5×.

**Both callers ARE now served** — `first-audio samples: 2`, `records separated: True`. An earlier harness observation that session B received nothing within 8 s was the *pre-`num_ctx`-fix* state and no longer reproduces.

**Decomposition of the failure:**

| Session | Turn | Total | `llm_done → tts_done` |
|---|---|---|---|
| A | 0 | 4,265 ms | 0 (cache hit) |
| B | 0 | 10,875 ms | 0 (cache hit) |
| A | 1 | 16,891 ms | **12,125 ms** |
| B | 1 | 23,406 ms | **15,938 ms** |

**TTS synthesis is the N=2 bottleneck — 12–16 s under concurrency**, against ~0 ms cached and ~6 s single-session. Retrieval is second at 3.0–8.8 s and is also highly variable. Prefill is 33–813 ms and is *not* the problem.

**Root-cause class: GPU contention between three co-resident models** — the 14B LLM, faster-whisper and Kokoro ONNX — plus the embedding model on the same device. Under one caller they take turns; under two they queue behind each other on a single 448 GB/s bus.

**This reframes `US-005`.** Streaming TTS was scoped as a 300–600 ms refinement. It is in fact **the largest single term in the turn, and the dominant failure mode of the hard requirement.** `US-005` should be re-scoped before it is implemented, and the sequencing note in `doc/sdlc/stories/US-005-stream-tts-synthesis.md` (which places it after `US-004`) is probably wrong — the measurement says TTS first.

## ROOT CAUSE — **Kokoro TTS runs on the CPU, and the app logs that it does not**

I first reported the opposite here, based on `ort.get_available_providers()`. **That was wrong, and it was wrong in exactly the way this codebase keeps being wrong:** `get_available_providers()` lists providers **compiled into** the wheel, not providers that **actually instantiate**.

The real session, measured (`doc/perf/tools/diag_tts_synthesis.py`):

```
Failed to create CUDAExecutionProvider. Require cuDNN 9.* and CUDA 13.*, and the latest MSVC runtime.
session providers: ['CPUExecutionProvider']
```

**Kokoro has been synthesising on the CPU the entire time.**

### The app's own guard has the same bug

`voice_handler.py:90–96` does:

```python
available = ort.get_available_providers()
if "CUDAExecutionProvider" in available:
    os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
    logger.info("Kokoro TTS: CUDA GPU enabled")      # <-- logged while on CPU
```

It tests **availability**, never **instantiation**. So the log says the GPU is enabled while the CPU does the work. **This is the tenth "set ≠ live" finding in this codebase, and the most consequential** — it is the only one that has been actively lying about the performance characteristic that dominates the hard requirement.

### Why the provider fails: a CUDA version mismatch

| Component | Ships |
|---|---|
| `onnxruntime-gpu 1.28.0` | requires **CUDA 13.\*** |
| `torch 2.7.1+cu128` | ships **CUDA 12.8** runtime |

cuDNN 9 DLLs *are* present in `torch/lib/` (`cudnn64_9.dll` and siblings), and adding that directory to the DLL search path **does not help** — the blocker is the CUDA major version, not cuDNN. There is no system CUDA toolkit on this box.

### The measured cost of running on CPU

| Text | Audio | Synthesis | RTF |
|---|---|---|---|
| Greeting | 1.96 s | 1.19 s | 0.61 |
| Typical answer | 8.98 s | 5.63 s | 0.63 |
| Long answer | 23.36 s | 13.24 s | 0.57 |

**RTF ~0.6 on CPU, uncontended.** That is the ~6 s single-session figure, exactly. Under N=2, two CPU syntheses plus Whisper plus everything else contend for **6 CPU cores**, which is the 12–16 s.

**So the N=2 failure is a CPU-contention problem wearing a GPU costume.** The 14B LLM was never the N=2 bottleneck, and neither was GPU bandwidth.

### FIXED — and it works

**#2 applied:** `_get_tts_engine` now reads the providers **back from the session it built** and logs the truth. It cannot claim a GPU it does not have again.

**#1 applied:** removed the CPU `onnxruntime` wheel (which coexisted with the GPU one), pinned `onnxruntime-gpu==1.26.0` (built against **CUDA 12.8 + cuDNN 9**, matching torch), and added `ort.preload_dlls()` after importing torch so the provider can actually resolve its DLLs.

**Before → after, measured:**

| | Before | After |
|---|---|---|
| Session providers | `CPUExecutionProvider` | **`CUDAExecutionProvider,CPUExecutionProvider`** |
| RTF (isolated) | 0.63 | **0.061** |
| TTS term at N=2 | 12,125–15,938 ms | **797–1,390 ms** |

**~10× on synthesis, ~12× on the N=2 TTS term.** Rollback: `pip install onnxruntime-gpu==1.28.0 onnxruntime==1.24.4`.

### The next bottleneck is the one the plan predicted

N=2 warm p50 moved **7,617 → 5,265 ms**. Still over the 3,000 ms cap, but the composition changed completely:

| Stage | Before TTS fix | After |
|---|---|---|
| TTS | 12,125–15,938 ms | 797–1,390 ms |
| **Retrieval** | 3,031–8,787 ms | **mean 7,164 ms, max 10,947** |
| Prefill | 33–813 ms | 555 ms |
| Generation | 811–1,543 ms | 1,178 ms |

**Retrieval is now the dominant term**, and it is `BRD-07` — the retrieval-serialization requirement the plan identified from code reading before anything was measured. `MOD-02`'s single event loop with synchronous hybrid legs was named the first non-resource bottleneck in `06-architecture.md` §5. That prediction is now confirmed by measurement, and **`US-008` is the fix.**

The sequence is worth stating plainly: two defects I found by measurement (`num_ctx` thrashing, TTS on CPU) had been **masking** the bottleneck the plan had already predicted from static analysis. Fixing them did not invalidate the plan's analysis — it revealed it.

---

## US-008 — evidence detail

**Delivered.** `BRD-07`'s fix in the ERC service: both retrieval legs were `async def` wrappers around **synchronous** calls — `chroma_vector.search` called `collection.query` inline, `bm25_memory.search` ran a pure-Python full-corpus scan inline — so `asyncio.gather` never overlapped them and each occupied the single uvicorn event loop for its whole duration. Both are now `await asyncio.to_thread(...)`.

**Verified the diagnosis before fixing:** a direct MCP timing probe (initialize + 3 × `tools/call`) returned in **0–18 ms**, so ERC was never slow to compute — it was slow to *be asked twice at once*.

**Result:** retrieval mean 7,164 → **~2,227 ms** measured; N=2 warm p50 5,265 → ~4,046 ms (run-to-run variance is high at n=4).

### A measurement bug of mine that this uncovered

`retrieval_ms` was **derived** by subtracting the engine's counters from the `llm_sent..llm_done` window. That is wrong twice over: it omitted model **load** (already found once), and it omitted Ollama **queue** time — which is large under two-caller load, because the engine's counters measure compute only.

It read **5,432 ms** when the true figure was **2,227 ms**. Derivation-by-subtraction keeps absorbing whatever it was not told about.

**Fixed properly:** `retrieval_ms` is now **measured** from the `retrieval_done` segment (`app/rag.py` marks it on every return path), and the leftover in the LLM window is named **`llm_queue_ms`** with its own `llm_queue_derivable` flag. US-001 tests updated to the corrected contract: **17 passed, 0 failed**.

### The N=2 bottleneck is now `llm_queue_ms` — Ollama engine queueing

| Stage | N=2 cost |
|---|---|
| **`llm_queue_ms`** | **3,098–3,627 ms — consistent across every turn** |
| retrieval (measured) | 735–796 ms (one 6,078 ms outlier = MCP timeout + fallback) |
| generation | 804–1,516 ms |
| prefill | 108–787 ms |

**Two callers wait ~3.2 s for the engine before any compute starts.** That is the dominant term, and it is a scheduling question, not a compute one — `OLLAMA_NUM_PARALLEL` is unset on this box. The US-015 benchmark showed the engine *can* batch (N=2 wall 1.13–1.42× rather than 2×), so the lever exists; it is evidently not being applied on the serving path.

### `OLLAMA_NUM_PARALLEL=2` — applied, and it did NOT fix the queueing

KV budget checked first: 2 × 1.50 GiB slots + 9.1 GB weights + 0.32 GB embed = **12.42 of 16.31 GB**, 3.89 GB headroom. Set at User scope and Ollama restarted.

**A direct concurrent probe confirms batching now works:**

```
stream 0: wall=2,257 ms  (prefill 27 ms, gen 23 ms)
stream 1: wall=2,204 ms  (prefill 38 ms, gen 28 ms)
BOTH wall: 2,259 ms   <- genuinely parallel, not serialized
```

**But the queueing did not go away: `llm_queue` mean 3,098–3,627 → 3,667 ms.** N=2 warm p50 went 4,046 → 5,078 ms.

**The probe explains why, and reframes the term.** Those two calls each took **~2,200 ms for ~50 ms of compute** — on a trivial "Say OK" prompt, with no concurrency effect (both walls are the same as the combined wall). So the ~2.2 s is **not** queueing behind another caller, and `NUM_PARALLEL` was never going to remove it. `llm_queue_ms` is misnamed: it is a **per-request fixed overhead between the HTTP call and the engine's first compute**, present even single-request and even trivially.

**Not yet diagnosed.** Candidates, in order: a runner reload per request; the scheduler re-evaluating slot allocation; HTTP/serialisation overhead in the client path. **Do not assume** — this is the same shape of unexplained gap that produced three wrong conclusions earlier in this document.

**Reverting is one variable:** `OLLAMA_NUM_PARALLEL` was unset before; unsetting it restores the prior behaviour. I would keep it only if a later measurement shows it helps, and the evidence so far does not.

---

## MEMORY PRESSURE — found, root-caused, fixed. And it invalidates the hour before it.

**`BRD-12` was breached and nothing in the program noticed** — because nothing measures system RAM.

**Symptom:** RAM went 57% → **97% used** (1,027 MB free of 32,702). Commit free fell to 2,507 MB of 61,781; the page file held **12.9 GB**.

**Root cause — mine.** Three orphaned Ollama `llama-server` runners, committing **25.7 GB between them with working sets of 4 MB / 1 MB / 4 MB** — almost entirely paged out. Repeated Ollama restarts during the `num_ctx` and CUDA work left runners behind; each held a full 14B model, and old ones were never reaped while new ones loaded on top.

**This explains the term I could not account for.** The unexplained ~2.2 s per-request overhead on a trivial prompt is exactly what page-faulting a 12 GB runner back in looks like. `NUM_PARALLEL` was never the lever; I was measuring a machine thrashing its page file and calling it engine queueing.

**Fixed:** all `llama-server` and `ollama` processes stopped and verified gone, Ollama restarted clean.

| | Before | After |
|---|---|---|
| RAM used | **97%** | **31%** |
| Free RAM | 1,027 MB | 22,433 MB |
| `llama-server` orphans | 3 (25.7 GB commit) | **0** |

**`OLLAMA_NUM_PARALLEL=2` reverted.** It did not earn its 1.5 GiB: the direct probe showed batching did begin, but the queue term did not fall, and the measurement underneath it was contaminated. Unsetting restores the prior behaviour; the key is not in `.env`.

### `BRD-12` now has an observer

The harness asserts system RAM per run: `system_ram_pct()` via `GlobalMemoryStatusEx`, compared against `RAM_CEILING_PCT` (default 80, overridable). Three outcomes, and the middle one matters:

- **met** → `BRD-12 RAM: 39% (ceiling 80%) - met`
- **breached** → the run is *kept* but flagged `NOT baseline-grade`, naming orphaned `llama-server` runners as the first thing to check
- **unreadable** → `UNKNOWN - the ceiling was not measured, which is not the same as met`

An unmeasured ceiling is not a met ceiling. That is why `None` is not silently reported as a pass.

### Larger sample — first time the per-turn cap is met on p50

| | n | p50 | p95 | worst |
|---|---|---|---|---|
| **N=1 warm** | 19 | **1,906 ms** | 4,420 ms | 4,547 ms |
| **N=2 warm** | 10 | **1,570 ms** | 5,544 ms | 6,015 ms |

**N=2 p50 is below the 3,000 ms per-turn cap** — the first time the hard requirement has been met on the median. `BRD-12 RAM` reported met (39–43%) on both runs.

**Still not a baseline.** Both runs are flagged `UNDERPOWERED` against the ≥100 samples `BRD-02` requires, and **p95 (5,544 ms) is far above the cap** — the median passing while the tail does not is the shape of a system with a periodic stall, not a system that meets an SLO.

**And a stability finding:** an N=2 run at **20 turns produced zero samples** (both sessions failed), while 4- and 6-turn runs succeeded. Longer runs appear to saturate something. Not diagnosed — recorded because it will silently convert a long soak into a no-data run, and a soak is exactly what `TAC-4` needs.

### Clean re-baseline

| | N=1 warm | N=2 warm |
|---|---|---|
| **p50** | **2,219 ms** (n=3) | **3,570 ms** (n=6) |
| p95 | 6,339 ms | 10,652 ms |
| **Interference ratio** | — | **1.6×** |

Both callers served; records separated. N=2 stage breakdown: `llm_queue` 2,099–7,047 ms, retrieval 500–671 ms (one 4,891 ms MCP-timeout outlier), prefill 32–2,456 ms, generation 804–1,699 ms.

**The interference ratio of 1.6× is close to `BRD-05`'s 1.5× budget** — the closest the program has come. But p50 is still above the 3,000 ms per-turn cap, and **`llm_queue` persists at ~3,600 ms on clean memory**, so memory pressure was *a* cause, not the only one. That remains unexplained.

### Every measurement in the hour before this is void

The `llm_queue` figures, the `NUM_PARALLEL` verdict, and the MCP-client verdict were all taken against a box paging 12.9 GB. They are superseded by the re-baseline above. Left in place rather than deleted, so the record shows what was measured under what conditions.

**Gap to close:** `BRD-12` has no observer. Nothing in the harness or the app samples system RAM, so a `BRD-12` breach is invisible until a background task gets reaped. The harness should assert it per run.

---

## P95 STALL — mechanism found and the dominant cause fixed

**p95 was the right target.** `BRD-02` wants p95 ≤ 1,200 ms and `BRD-05` caps any turn at 3,000 ms. At p95 5,544 ms, one caller in twenty hears 5.5 seconds of silence — for a voice agent the tail *is* the experience.

**The stall was not one thing.** Sorting 18 traces by total exposed two distinct modes:

| Turn | retrieval | llm_queue | Mode |
|---|---|---|---|
| 9,250 ms | **4,860** | 2,110 | MCP timeout |
| 9,797 ms | **4,906** | 3,138 | MCP timeout |
| 10,234 ms | 500 | **6,623** | engine queue |

### Mechanism — a dependency inversion

ERC answers in **2–229 ms** when probed idle, yet the app logged **7 timeout fallbacks**. The reason: ERC's retrieval calls Ollama for the query embedding, and under N=2 Ollama is saturated *generating*. So the embed request queues behind generation, ERC's handler exceeds the 2.5 s read timeout, the app falls back to local Chroma — **and the fallback needs another embedding from the same saturated Ollama.**

**Retrieval cannot complete while the LLM is busy, and the fallback doubles the cost.** That is the 2.5 s timeout + 2.4 s re-retrieval behind both 4,900 ms spikes.

### Fix — `RAG_MCP_TIMEOUT` 2.5 → 6.0

The timeout was mis-tuned: it was chosen before anyone knew the under-load latency, and it converted a slow-but-successful retrieval into a *twice-paid* one. Waiting beats timing out then re-retrieving.

**Measured, N=2, n=18, same fixture:**

| | Before | After |
|---|---|---|
| retrieval across 17 of 18 turns | 500–1,156 ms | **484–609 ms** |
| retrieval spikes | **2 turns at ~4,900 ms** | **1 turn at 9,579 ms** |
| **p50** | 2,781 ms | **1,999 ms** |
| **p95** | 7,850 ms | **5,538 ms** |
| worst | 10,718 ms | 7,344 ms |

---

## THE ≥100-SAMPLE BASELINE IS BLOCKED — by a stability defect, not by willingness

Attempted at N=2, 55 turns (~108 expected samples). **Both sessions were dropped at 28 samples:**

```
session A: ConnectionClosedError: 1011 (internal error) keepalive ping timeout
session B: ConnectionClosedError: 1011 (internal error) keepalive ping timeout
```

**The app stops answering WebSocket keepalive pings.** That is the "long-run saturation" previously recorded as unexplained, now with a mechanism: **the event loop blocks long enough that it cannot service a ping**, so the carrier-side connection is torn down and the run ends mid-flight.

This is the same class of defect as `BRD-07` — work occupying the event loop — and it is now known to have **two** consequences: a second caller waits, *and* a long call is dropped.

**Consequence for the plan:** the baseline cannot be taken until this is fixed. It is not a matter of running longer; a longer run loses more samples, not fewer. The order is therefore **fix the blocking, then baseline** — the reverse of what I recommended before this run.

**Partial numbers from the aborted run** (28 warm samples, `DISCARDED` by the harness's own turn-cap rule, so **not** a baseline):

| | n=28 |
|---|---|
| p50 | 3,078 ms |
| p95 | 6,557 ms |
| worst | 9,094 ms |

### What is left, and it is now the only consistent term

**p50 1,999 ms passes the 3,000 ms cap. p95 5,538 ms does not**, and the residual is:

1. **`llm_queue` ranging 2,062–4,871 ms on every turn** — the consistent floor, still unexplained, and now the single largest contributor to the tail.
2. **One 9,579 ms retrieval** — a genuine ERC stall, *not* the timeout pattern (the timeout is fixed). Different event, not yet diagnosed.

So the p95 work is now **the `llm_queue` term**, not retrieval. That is the next target and it is a narrower question than the one we started with.

### The MCP client fix — applied, effect not yet confirmed

`app/rag_mcp.py` now uses one module-level `httpx.Client` (keep-alive, pooled) instead of building a new client — and a new TCP connection — per request. Measured standalone: `retrieve_context` at 923 ms.

**The 4,859 ms retrieval outlier still appeared in the N=2 run**, so the timeout-and-fallback double-payment is **not** confirmed fixed. It may be contention rather than connection setup. Not yet re-measured after the change under load.

### Secondary finding — the MCP timeout is being paid on top of a successful retrieval

Under load the app logs `MCP retrieval failed (timed out) — falling back to local Chroma`. ERC answers in **milliseconds** when probed directly, so the 2.5 s read timeout is firing on a service that is not slow — it is contended, or the app's per-request `httpx.Client` construction adds enough to cross 2.5 s. The cost is paid **twice**: 2.5 s of timeout *plus* a full local re-retrieval (~2.9 s). That is the 6,078 ms outlier.

### Fix options, in order of leverage

| # | Option | Effect | Class |
|---|---|---|---|
| 1 | **Pin `onnxruntime-gpu` to a CUDA-12 build** matching torch's runtime | Should restore the CUDA provider, taking TTS to well under RTF 0.1 | A (dependency pin) |
| 2 | Fix the app's guard to check the **session's** providers, not availability | Stops the false log line; makes option 1 verifiable | A |
| 3 | Install CUDA 13 + cuDNN 9 system-wide | Same as 1, heavier | system change |
| 4 | Stream synthesis (`US-005`) | Removes the caller's *wait*, not the CPU cost | B |

**Option 2 is required regardless** — without it, no fix can be verified, because the only signal the app emits about TTS placement is a log line that is currently false.

**And `US-005`'s value changes:** streaming on a CPU-bound synthesiser under two-caller contention is worth much more than the 300–600 ms the plan predicted, but it does not remove the contention. Options 1+2 change the N=2 result; option 4 changes what the caller experiences.

### Latency measured so far — and it is far worse than the benchmark predicted

| | Measured |
|---|---|
| Cold turn | **12,719 ms** |
| Warm turn | **8,609 ms** |
| Warm prefill | **2,541 ms** (benchmark said 411 ms) |
| Model load | 6,133 ms |
| Generation | 818 ms |
| TTS synthesis | ~6,000 ms |

Both turns exceed the 3,000 ms cap, so the harness correctly **DISCARDED** the run. The 411 ms prefill in `us015-benchmark-results.md` assumed the static prefix stayed cached; the server log says it does not. **The benchmark's prefill figure is therefore optimistic relative to the live path, and that discrepancy is the open question above.**
