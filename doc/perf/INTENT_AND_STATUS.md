# Intent, Current State, Done and Pending

**As of:** 2026-09-19 (rev 3) · **Branch:** `perf/phase-a` · **Commit:** `849d94f` (local, **not pushed**) · **Companions:** `PLAN.md`, `IMPLEMENTATION_STATUS.md`, `doc/sdlc/`

*Rev 3 supersedes rev 2: the ≥100-sample baseline **completed**, `US-007` shipped, the keepalive drop stopped reproducing, `MOD-05` was recorded out of scope, and the story Definition-of-Done checklists were counted for the first time. Rev 2's pre/post figures survive unchanged.*

---

## 1. The intent

Three layers, all still live. Only the first was explicit at the start.

**Layer 1 — the original ask.** Reduce turn latency for the admissions voice assistant: caller stops speaking → first syllable heard, targeting **p50 ≤ 700 ms / p95 ≤ 1,200 ms**, and establish what hardware and configuration that requires.

**Layer 2 — the constraint that reshaped it.** **Two simultaneous voice callers, entirely local, with no loss of answer quality.** That turned "make one call fast" into "make two concurrent calls work on one box", and made every speed change conditional on an evaluation that did not exist.

**Layer 3 — the process.** Full SDLC plan before implementation, then story-by-story, least dependent to most, with a status table and evidence per story.

**Explicitly not:** a rewrite · a model-downsizing project · a cloud migration (student PII) · a plan that assumes the current architecture was right.

---

## 2. Where we are now

| | |
|---|---|
| **Plan** | Complete. `doc/sdlc/` — 36 files, validator **752 passed / 0 failed** |
| **Quality gate** | **BLOCKED.** 161-case golden set built; **0 of 137 ground truths approved**. All 12 critical intents report BLOCKED |
| **Stories** | **18 written · 5 implemented · 0 complete.** 1 IN PROGRESS · 2 AWAITING SIGN-OFF · 1 PARTIAL · 5 BLOCKED · 4 NOT STARTED |
| **Baseline (N=2, warm)** | **ESTABLISHED** — p50 **3,609 ms**, p95 **6,044 ms**, n=**108**, 0 drops, 0 timeouts |
| **Against the cap** | **NOT MET** — 96 of 108 turns exceed 3,000 ms |
| **Commit** | `849d94f`, 108 files, **local only — the push was denied by the auto-mode classifier** |

**One sentence:** the last unexplained latency term was a hostname worth ~2.5 s a turn, fixing it also removed the run-killing instability, and the stack now has its first honest measurement — which says it is roughly 1.8× over the cap at p95.

### The baseline, and what it settles

The 55-turn × 2-session run **completed**: 110 turns played, **108 warm samples, zero dropped sessions, zero timeouts**. It is the first run in this engagement long enough to report a tail.

| | pre-fix, died at 28 samples | **post-fix, 108 samples** |
|---|---|---|
| warm p50 | 3,078 (n=28) | **3,609** |
| warm p95 | 6,557 | **6,044** |
| sessions completed | 0 of 2 | **2 of 2** |

**The drop is gone.** The previous failure killed both sessions at turn 15 of 55; this run went the distance. That is consistent with the same 2,066 ms stall having starved the keepalive path, though it is one run and not a proof.

The run is still flagged `DISCARDED` — by the harness's own turn-cap gate, because 96 of 108 turns exceed 3,000 ms. **That is the gate working: the number is reportable, the run is not a pass.** `TAC-1` also still fails on frame framing (64 frames over 5 ms, the known Windows `time.monotonic()` granularity).

### ⚠️ Correction carried forward from rev 2

Rev 1 quoted **"N=2 warm p50 1,999 ms best run"** — the luckiest small-n draw presented as *the* result. Across successive runs the same hour the harness p50 read **1,570 · 2,000 · 2,781 · 3,078 · 4,578** at n=4–28. At those sizes two samples land in each tail and p50 swings 3×. **The 108-sample figure above is the first one quoted here that is worth anything.** Everything earlier was noise, including the 4,125 ms in rev 2's table.

---

## 3. Done — with evidence

### Stories — two counts, because they differ

| | Count |
|---|---|
| Written (planning artifact, validates) | **18 / 18** |
| Implemented with a passing acceptance suite | **5 / 18** |
| **Fully Definition-of-Done complete** | **0 / 18** — best is US-007 at 5/8 |

| Story | Status | Evidence |
|---|---|---|
| **US-001** per-turn tracing | IMPLEMENTED | `test_us001_tracer.py` → **17/17**; proven on a live call · DoD 2/6 |
| **US-006** model residency | IMPLEMENTED | `test_us006_residency.py` → **11/11**; `ollama ps` → `Forever` · DoD 3/8 |
| **US-007** boot warm verifiable | IMPLEMENTED | `test_us007_readiness.py` → **30/30**; live gate 3 of 4 clauses · DoD 5/8 |
| **US-011** config source of truth | IMPLEMENTED | `test_us011_config_truth.py` → **16/16** · DoD 3/8 |
| **US-008** de-serialize retrieval | IMPLEMENTED — **no acceptance test** | ERC legs off the event loop; retrieval 7,164 → ~2,227 ms · DoD 1/7 |
| **US-002** load harness | IN PROGRESS | Drives live `/ws/twilio`; two gaps (§4) |
| **US-003** golden set | AWAITING SIGN-OFF | 161 cases, 28/28 intents; **137 PENDING** |
| **US-014** interruption decision | AWAITING SIGN-OFF | Package written; Class C, decision is yours |
| **US-015** model benchmark | PARTIAL | Latency leg measured; quality leg blocked |

**`US-008` is the outlier.** It is the only implemented story with no suite over its ACs or TACs — the evidence is a measurement, not a passing test. Treat it as weaker than the four beside it.

**And "implemented" is not "done".** Four items recur across all five and none is a coding task: the LLD test mapping (T-1…Tn), the story-specific load/soak gates, a *demonstrated* `BRD-15` rollback, and module-doc reconciliation. Concretely, `MOD-06:101` still documents the **pre-fix** stage set — it names "no retrieval mark" as the gap US-001 closed — and `MOD-01` contains no `perf_trace` emitting call sites at all.

### Defects found and fixed — none of which were in the plan

| Finding | Effect |
|---|---|
| **`OLLAMA_URL=localhost` → 2,066 ms IPv6 stall on every turn** (see below) | `llm_queue` p50 **2,521 → 91 ms**; retrieval p95 **2,562 → 937**, max **8,375 → 1,046**; **and the run stopped dying** |
| **`num_ctx` thrashing** — four context sizes on one model rebuilt the runner per switch | warm turn **8,609 → 360 ms** (largest single win; invisible to code reading) |
| **Kokoro TTS on CPU** while the app logged "CUDA GPU enabled" | RTF **0.63 → 0.061**; N=2 TTS term **12–16 s → 0.8–1.4 s** |
| **25.7 GB of orphaned Ollama runners** paged out — caused by my own restarts | RAM **97% → 31%** |
| **`retrieval_ms` was derived, not measured** — absorbed load and queue time | read 5,432 ms; truth 2,227 ms |
| **MCP timeout double-payment** — ERC's embedding queues behind Ollama generation, so the 2.5 s timeout fired on a service answering in milliseconds, then the fallback paid again | **p95 7,850 → 5,538 ms**; retrieval spikes 2 → 1 |
| **The pre-warm sent the literal prompt `"ping"`** — warmed weights, not the voice prefix; a skipped pre-warm was a warning, not a failure | `US-007`: real prompt, engine-confirmed. Live **2,001 → 29 ms** prefill |
| **4 config keys are set and read by nothing** — `FASTAPI_WORKERS`, `KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL` | Now a **non-zero exit** from the boot gate. First time these have ever failed anything |
| **`BRD-12` had no observer** | RAM reached 97% unnoticed; harness now asserts it per run |
| `OLLAMA_NUM_PARALLEL=2` | applied **then reverted** — did not earn its 1.5 GiB. **That verdict is now suspect**: it was measured at RAM 97% with `num_ctx` thrashing and TTS on CPU, and it predates the removal of the 2,066 ms stall. See §6. |

### Also delivered

Full SDLC plan · intent catalog (28 intents, 12 critical) · 13 brownfield reconciliation notes · load harness with 7 network profiles · Kokoro-rendered speech fixtures (0.98 transcription fidelity) · `app/config_truth.py` · `app/boot_readiness.py` + the four-clause boot gate wired into `start_services.ps1` · **40 run summaries** as raw evidence.

---

## 4. Pending

### Blocked on you

| Item | Needed |
|---|---|
| **`DG-03`** | **12 critical-intent ground truths.** 137 of 161 cases PENDING. Blocks 5 stories and every behaviour-changing change. No agent can do this. |
| **The 4 unread config keys** | `FASTAPI_WORKERS`, `KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL` are set and read by nothing. The boot gate now reports **NOT READY (exit 2)** until each is implemented or removed. `KOKORO_SPEED` is one line at `voice_handler.py:131,707`. |
| **`US-014`** | Class C interruption decision. Package ready. |
| **`BRD-02` SLO** | 700 ms is unreachable behind a 600 ms endpointing floor. The measured defensible SLO (~2,300 ms) needs your sign-off — **and the baseline now says p50 is 3,609 ms, so even that figure needs revisiting.** |
| **Push** | `git push -u origin perf/phase-a` — denied by the classifier; run it yourself with `!` |

### Blocked on `DG-03` (5)

`US-004` stream LLM · `US-005` stream TTS · `US-009` relevance floor · `US-010` consolidate stores · `US-018` RAG baseline/optimized

### Ready to implement, no blocker (4)

`US-013` timeouts + half-open probe · `US-012` TTS cache isolation · `US-016` admission control · `US-017` background priority

### The `llm_queue` root cause — found 2026-09-19, fixed, Class A

**The last unexplained term was a hostname.**

`app/llm_backend.pick_model()` resolves the model name by calling Ollama's
`/api/tags` over HTTP — **on every voice turn and every embedding**. It builds
that URL from `OLLAMA_URL`, which `.env` set to `http://localhost:11434`.
`app/rag_legacy.py:59` and `app/pipeline.py:38` build `OLLAMA_BASE_URL` the same way.

On this box:

- `localhost` resolves to **`::1` (IPv6) first**, then `127.0.0.1`.
- **Ollama binds IPv4 only** — `::1:11434` is closed.
- A connect to a closed port on `::1` **does not fail fast under Windows**: it burns
  **2,066 ms** in SYN retransmits before returning `WinError 10061`. Then Python
  falls back to `127.0.0.1` in **0.5 ms**.

Measured, repeatedly, deterministic:

| call | wall |
|---|---|
| `GET http://localhost:11434/api/tags` | **2,034 / 2,024 / 2,032 ms** |
| `GET http://127.0.0.1:11434/api/tags` | **5 / 28 / 18 ms** |
| `socket.create_connection(('::1', 11434))` | **2,066 ms** → `ConnectionRefusedError` |
| `socket.create_connection(('127.0.0.1', 11434))` | **0.8 ms** → connected |

**Why it hid for so long.** The *inference* call never paid it: the `ollama` Python
library defaults to `127.0.0.1` and does not read `OLLAMA_URL`. So the model
answered fast while the app spent two seconds per turn asking a different URL
which model to use. The cost was also paid by the **embedding** path
(`OllamaEmbeddingFunction(url=OLLAMA_BASE_URL)`), which is why the local-fallback
retrievals spiked to 8,375 ms.

**Fix:** `.env` → `OLLAMA_URL=http://127.0.0.1:11434`. **Class A (lossless)** —
same server, same model, same bytes; it only skips a connect attempt to a port
where nothing is listening. Docker is unaffected (`docker-compose.yml` sets
`OLLAMA_URL=http://ollama:11434`).

**Evidence the fix is inert and effective** (both runs N=2, warm, per-turn traces):

| term | pre-fix | post-fix |
|---|---|---|
| `llm_queue_ms` p50 | 2,521 | **91** |
| `retrieval_ms` p95 / max | 2,562 / 8,375 | **937 / 1,046** |
| `prefill_ms` p50 | 775 | 795 ← unchanged, as it must be |
| `generation_ms` p50 | 1,186 | 1,207 ← unchanged, as it must be |

Prefill and generation not moving is the proof this is Class A: the model did the
same work before and after. Only the wasted two seconds left.

### Open technical questions

| Question | State |
|---|---|
| **The keepalive drop** | **Resolved in practice.** 110 turns, both sessions completed, zero drops — against a previous failure at turn 15. The mechanism (the stall starving the ping path) is plausible but unproven; one run is not a mechanism. |
| **The 9,579 ms retrieval outlier** | **Likely the same cause, not confirmed.** Post-fix retrieval max fell 8,375 → 1,046 ms on a comparable run, which fits — but the specific instance was never re-measured. [Inferred], not [Measured]. |
| **`TAC-1` framing** | Fails on this box — Windows `time.monotonic()` advances in 15/16 ms ticks. Root-caused, not fixable in-process. 64 of 18,120 frames over 5 ms. |
| **Whether my `_post()` client change is safe** | **No longer suspected, still unproven.** The 03:41 run reproduced the drop *with the change reverted*, so the shared client was not the cause. It stays reverted until something argues for it. |
| **`BRD-12` RAM is not sampled during a run** | Asserted at summary time only; a breach *during* a run is still invisible. |
| **`.env.example:26` still says `localhost`** | The same trap is baked into the template. One-word fix, not yet applied. |
| **The GPU-clock check depends on placement** | Sampled right after the warm it reads 13,801 of 14,001 MHz; 30 minutes later, 405 MHz — and that is *correct* behaviour, not a defect. Sampling elsewhere would make the gate cry wolf. |

---

## 5. What this engagement taught

**Static analysis found one bottleneck; measurement found four bigger ones.** `06-architecture.md` §5 named `MOD-02`'s event loop from code reading alone — and was right. But `num_ctx` thrashing, TTS-on-CPU, 25.7 GB of orphaned runners and a two-second hostname stall were invisible to reading, and each was larger.

**"Set ≠ live" now has eleven instances.** Pipecat · `FASTAPI_WORKERS` · `.machine_profile.json` · the pre-warm pipe bug · the reranker · the semantic cache · `KOKORO_SPEED` · `LOG_FILE` · `LOG_LEVEL` · a log line asserting GPU acceleration while running on CPU · and now `US-007`'s placeholder pre-warm. **It is the single most reliable defect class in this codebase**, and it is now the one thing the boot gate is built to catch.

**My own instrumentation lied four times, each plausibly.** The `480` soak figure · a derived `retrieval_ms` · a `llm_queue` explanation that fit and was wrong · and a "Kokoro is on GPU" conclusion drawn from `get_available_providers()` — the same mistake the application makes. **Each was caught only by a live measurement contradicting a number that looked fine.**

**And I invalidated an hour of my own work** by not checking system RAM, which nothing was measuring.

**The plan wrote Definition-of-Done checklists for all 18 stories and then nobody counted them.** The result: **5 stories are implemented and 0 are complete**, and the gap is not code — it is test-scenario mapping, soak gates, rollback demonstrations and doc reconciliation. A checklist that is never read is a checklist that is never met, and this is what it looks like from the outside: a program that reports five done and has finished none.

---

## 6. Recommended next step

**Close the DoD gap on the five implemented stories before starting a sixth.** Every one of them is `IMPLEMENTED` and none is `DONE`, for four recurring reasons that are all cheap: map the existing suites to the LLD scenario IDs, run the story-specific soak gates against the baseline that now exists, *actually revert* each change once to demonstrate `BRD-15`, and reconcile the module docs with what was built. Starting `US-013` now would take the count to six implemented and still zero complete.

**Then decide the SLO against the real number.** `BRD-02`'s 700 ms was never reachable; the defensible figure was estimated at ~2,300 ms; **the baseline measures p50 at 3,609 ms.** That number needs a PO decision, not another optimisation pass.

**Then re-test `OLLAMA_NUM_PARALLEL=2`.** It was rejected on a measurement taken at RAM 97%, with `num_ctx` thrashing and TTS on CPU — all three since fixed. Removing the 2,066 ms stall also changed the arrival pattern: both callers now hit Ollama's single generation slot almost simultaneously instead of being staggered by a random two-second pause. The case for a second slot is *stronger* than when it was rejected.

**And resolve the four unread keys**, because the boot gate will keep reporting NOT READY — correctly — until someone either implements them or deletes them from `.env`.
