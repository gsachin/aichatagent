# Performance Report — University Admissions Voice Stack

**Date:** 2026-09-21 · **Repo:** `D:\project\universityDemo` · **Branch:** `sdlc/us011-tac1-config-migration`
**Scope:** what has measurably improved, what is still unmet, and how critical the remainder is.

> **How to read the numbers.** Every figure below is tagged with its evidence grade.
> `[Measured]` = produced by a run or probe on this box. `[Estimated]` / `[Predicted]` = arithmetic or a
> plan projection, **not** an observation. The two are never mixed in the tables. Where a doc states a
> severity, it is quoted; where it does not, this report says "severity not stated" rather than inventing
> one. Line 5 of the drive status is the standing rule for this program: *"Set ≠ live" is the single most
> reliable defect class in this codebase* (`doc/perf/INTENT_AND_STATUS.md:191`).

---

## 1. Headline

| | |
|---|---|
| **The target** | `BRD-02`: p50 ≤ 700 ms, p95 ≤ 1,200 ms. `BRD-05`: **no single turn > 3,000 ms** under two concurrent callers (`doc/sdlc/01-brd.md:60-61`, `:80-81`) |
| **Is the 3,000 ms cap met?** | **No.** Best measured on record: **7% of turns over** (`doc/sdlc/DEMO-PARK-2026-09-20.md:20-23`) |
| **Is p50 ≤ 700 / p95 ≤ 1,200 achievable?** | **Not on this hardware.** The program's own clause permits stating the tightest defensible SLO instead — proposed ~2,300 ms p50, **still unapproved** (`doc/sdlc/08-coverage-verification.md:141`) |
| **How far has it come?** | **p50 ~5,141 → ~2,063 ms live**, and **over-cap 92.4% → 15.2%**, across 3,626 real turns (`logs/perf_turns.jsonl`, window 09-19 04:25 → 09-20 12:02) |
| **The single most critical open item** | `DEF-001` — MCP retrieval intermittently hangs for the full read timeout. **The only item in the doc set carrying an explicit severity: "Critical"** (`doc/sdlc/defects/DEF-001-mcp-retrieval-timeout.md:6`) |

**The honest one-sentence summary:** the stack got roughly **2.5× faster at p50** and cut cap breaches from
~9 in 10 turns to ~1 in 7, through a small number of large, well-evidenced fixes — and it is still **not**
meeting the 3,000 ms per-turn ceiling, because the remaining tail is dominated by one deferred defect and
by contention under two concurrent callers.

---

## 2. Delivered — the large measured wins

Ordered by size of effect. All `[Measured]` unless tagged otherwise.

### 2.1 `num_ctx` thrashing eliminated — **24× on the warm turn**
Four different context sizes (`8192/2048/1024/512`) across call sites forced Ollama to rebuild the runner
and wipe the KV cache on nearly every switch. Pinning every chat call to one size (`SMALL_TASK_NUM_CTX=8192`)
fixed it.

| | Before | After |
|---|---|---|
| `load_ms` (warm) | 6,133 ms | **4.7 ms** |
| Warm turn, first audio | 8,609 ms | **360 ms** |

Root-cause ladder, alternating `num_ctx` on the same box: 8192 → `load_ms` **3**; 2048 → **6,677**; 8192 → **6,423**; 1024 → **6,658**; 8192 → **6,215**.
Source: `doc/perf/IMPLEMENTATION_STATUS.md:214-233`.

### 2.2 Kokoro TTS moved CPU → CUDA — **~10× synthesis, ~12× on the N=2 term**
A coexisting CPU `onnxruntime` wheel was shadowing the GPU build; the app logged "CUDA GPU enabled" while
synthesising on CPU.

| | Before | After |
|---|---|---|
| Session providers | `CPUExecutionProvider` | **`CUDAExecutionProvider,CPUExecutionProvider`** |
| RTF (isolated) | 0.63 | **0.061** |
| TTS term at N=2 | 12,125–15,938 ms | **797–1,390 ms** |

Source: `doc/perf/IMPLEMENTATION_STATUS.md:310-335`. Rollback: `pip install onnxruntime-gpu==1.28.0 onnxruntime==1.24.4`.

### 2.3 The `localhost` IPv6 stall removed — **~2 s per turn, lossless**
`OLLAMA_URL=http://localhost:11434` made every turn connect to a closed `::1:11434` first and pay SYN
retransmits before falling back. Deterministic and repeated:

| Probe | Cost |
|---|---|
| `GET http://localhost:11434/api/tags` | **2,034 / 2,024 / 2,032 ms** |
| `GET http://127.0.0.1:11434/api/tags` | **5 / 28 / 18 ms** |
| `socket.create_connection(('::1', 11434))` | **2,066 ms** → `ConnectionRefusedError` |
| `socket.create_connection(('127.0.0.1', 11434))` | **0.8 ms** → connected |

Effect on per-turn terms (N=2 warm): `llm_queue_ms` p50 **2,521 → 91**; `retrieval_ms` p95 **2,562 → 937**,
max **8,375 → 1,046**. Invariant controls that must *not* move did not: `prefill_ms` p50 775 → 795,
`generation_ms` p50 1,186 → 1,207. **And the run stopped dying.**
Source: `doc/perf/INTENT_AND_STATUS.md:86`, `:143-147`, `:161-168`.

### 2.4 MCP timeout retuned 2.5 → 6.0 s — **p95 7,850 → 5,538 ms**
The 2.5 s timeout fired on a service that answers in milliseconds (its embedding queued behind generation),
and the fallback then paid a *second* embedding. Measured, N=2, n=18, same fixture:

| | Before | After |
|---|---|---|
| retrieval across 17 of 18 turns | 500–1,156 ms | **484–609 ms** |
| p50 | 2,781 ms | **1,999 ms** |
| p95 | 7,850 ms | **5,538 ms** |
| worst | 10,718 ms | **7,344 ms** |

Source: `doc/perf/IMPLEMENTATION_STATUS.md:484-497`. Residual: one 9,579 ms retrieval, identified in-source
as a genuine ERC stall rather than the timeout pattern.

### 2.5 Model swap `qwen2.5:14b` → `llama3.2:3b` — **p50 −34%, over-cap 74% → 48%**
Three N=2 warm runs, 100 turns × 2 sessions. The smaller model is both faster **and** better on the screen:
verdict-level passes 7/30 (14B) vs 12/30 (3B).

| Run | first-audio p50 | p95 | turns over 3,000 ms |
|---|---|---|---|
| 17:10:42Z | 4,828.5 ms | 9,866.2 ms | 198/198 |
| 18:53:50Z | 4,461.5 ms | 10,934.0 ms | 146/198 |
| 20:10:44Z | **2,953.5 ms** | 8,593.3 ms | **95/198** |

Hardware level: prefill p50 **604 → 146 ms (4.1×)**; generation p50 **990 → 184 ms (5.4×)**; N=2 batching
PARTIAL → **BATCHED** (131.2 tok/s); VRAM **69% → 26%**. The 14B "rambles 2.3× as often" (37 vs 16,
p = 0.0002). `OLLAMA_NUM_PREDICT=192` truncates **0 of 83** answers on either model (p95 was 106 / 82 tokens).
Source: `doc/perf/us015-model-decision.md:16-19`, `:74-110`. **Evidence grade SCREEN-GRADE** — no case carries
PO approval. **Attribution caveat, stated in-source:** model change, `num_predict`, prompt scoping and the
event-loop fixes all landed in one restart, so the STT and retrieval gains cannot be split between them.

### 2.6 Streaming LLM (US-004) and streaming TTS (US-005) — **cap breaches 27% → 6%**
Both behind flags; both **default-OFF**, adoption blocked on `DG-03`.

| Configuration | first-audio p50 | p90 | over-cap |
|---|---|---|---|
| Batch (baseline) | 2,453 ms | 3,891 ms | 27% |
| + streaming TTS only | 2,250 ms | 3,360 ms | 13% |
| + streaming LLM too | **1,625 ms** | **2,015 ms** | **6%** |
| + freeze fix (60 turns/session) | 1,579 ms | 2,562 ms | 8% |

TTFT (`retrieval_done → llm_first_token`) p50 **281 ms** → **266 ms**; 120/120 turns streamed with zero
session errors. Sources: `doc/sdlc/stories/US-005-stream-tts-synthesis.md:5`, `US-004-stream-llm-generation.md:5`,
commits `43a3fb9` and `96f3407`. **Known limit:** Kokoro's own stream batches a whole short answer into one
chunk, so streaming synthesises *per sentence*, not in true parallel.

### 2.7 Event-loop blocking removed — the class that was killing calls
Three blocking calls (`generate_ulaw_greeting`, `_detect_admission_intent_whatsapp`, lazy model loads) ran on
the loop; a later commit found four more (queue/follow-up/outbox/offers polls).

- `/health` over 14 samples: **max 23 ms, median 15 ms** — *"(was 2,300 ms on a request that does no work)"*
- uvicorn's bind waited **21 s** behind four polls at startup (`4.1 + 4.1 + 8.1 + 4.1 s` in a 20.8 s window)
- Root cause of the slow connect: `DATABASE_URL` names `localhost` → ~4.1 s per failed connect (~2.0 s × 2), measured 12/12 samples

Sources: commits `7c19426`, `13d204e`; `doc/sdlc/sdlc-completion-drive-status.md:40`. Now enforced by
`doc/perf/tools/test_event_loop_hygiene.py` — **3 passed, 0 failed** (6 blocking names, both-direction controls), verified 2026-09-21.

### 2.8 US-007 pre-warm: placeholder `"ping"` → the real voice prompt — **prefill 2,001 → 29 ms**
The launcher warmed with `"ping"`, which loads weights and nothing else; the voice prompt prefix stayed cold.
Now the real prompt is submitted twice and engine counters compared, so a skipped warm is a *failed clause*
rather than a warning an operator scrolls past. The cold load this prevents: **32,919 ms** `[Measured]`
(`doc/perf/PLAN.md:62`). Sources: `doc/perf/IMPLEMENTATION_STATUS.md:572`, `:587-595`.

### 2.9 Retrieval de-serialised and the MCP client re-merged (US-008)
Both ERC retrieval legs were synchronous calls inside `async def` wrappers; both moved to `asyncio.to_thread`.
Retrieval mean **7,164 → ~2,227 ms**; N=2 warm p50 **5,265 → ~4,046 ms**. The shared `httpx.Client` then took
the retrieval leg p50 **812 → 531 ms**. Sources: `doc/perf/IMPLEMENTATION_STATUS.md:43`, `:344`, `:360`;
commit `a791272`. **Caveat:** the 7,164 ms mean was itself later shown to be a *derived* figure, contaminated
by the measurement error in 2.10 — and **this story has no acceptance test at all** (DoD 1/7).

### 2.10 A measurement error corrected — `retrieval_ms` 5,432 → 2,227 ms
Not an optimisation: `retrieval_ms` was *derived* by subtracting engine counters from the LLM window, which
omitted model **load** and Ollama **queue** time. It read **5,432 ms when the truth was 2,227 ms**. It is now
measured directly from a `retrieval_done` mark on every return path. This one matters because the wrong number
was steering decisions. Sources: `doc/perf/IMPLEMENTATION_STATUS.md:366`, `doc/perf/INTENT_AND_STATUS.md:90`.

### 2.11 Process hygiene — RAM **97% → 31%**, 25.7 GB reclaimed
Three orphaned Ollama `llama-server` runners (25.7 GB commit, working sets of 4 MB — almost entirely paged
out) from repeated restarts. This **invalidated an earlier conclusion**: the unexplained ~2.2 s per-request
overhead was page-faulting a 12 GB runner back in, not engine queueing. *"`NUM_PARALLEL` was never the lever;
I was measuring a machine thrashing its page file and calling it engine queueing."*
Source: `doc/perf/IMPLEMENTATION_STATUS.md:407-421`. Every measurement in the hour before is marked **void**.

### 2.12 This session (2026-09-21) — outage cost made real, and a defect closed
- **The retrieval circuit breaker gated nothing.** `rag._use_mcp()` had no caller in `app/` — only tests. During an outage every turn called the dead service at the full 6.0 s timeout, `claim_probe()` never ran, and `breaker_probes` could only read 0. `TAC-4`'s *"bounded per window"* was untrue as wired. Now one decision (`rag_mcp.admit_primary()`) routes all three primary call sites. Verified non-vacuous: reverting one file reports **"10 trips for 10 turns — cost grows with turns"**; with the fix, 1.
- **Four unconditional DB reads left the event loop** (`13d204e`).
- **Config provenance was a constant** — 58 of 110 settings had no reported origin. Now 122 rows with computed provenance, surfacing three real `.env`-vs-artifact divergences.

---

## 3. Where it stands against the targets

**Live trace trend** — 3,626 real turns, split into equal thirds `[Measured]`, `logs/perf_turns.jsonl`:

| Window | n | p50 | p95 | over 3,000 ms |
|---|---:|---:|---:|---:|
| 09-19 04:25 → 10:56 | 1,208 | 5,141 ms | 12,547 ms | **92.4%** |
| 09-19 10:56 → 14:27 | 1,209 | 2,812 ms | 11,562 ms | **45.2%** |
| 09-19 14:27 → 09-20 12:02 | 1,209 | **2,063 ms** | 7,734 ms | **15.2%** |

**The harness run store** — 73 artifacts, **71 discarded, 2 not** (both single-turn cold runs: 2,000 ms and 1,875 ms).
**All 18 powered runs** (≥100 turns) are discarded for exactly one reason: `turn cap exceeded (>3000 ms first audio)`.

| Run | cond | turns | p50 | p95 | worst | over cap |
|---|---|---:|---:|---:|---:|---:|
| 20260919T145025Z | N=2 warm | 194 | 7,969 ms | 14,234 ms | 23,875 ms | **192** |
| 20260919T185350Z | N=2 warm | 200 | 4,461 ms | 10,934 ms | 24,891 ms | 146 |
| 20260920T034826Z | N=2 warm | 200 | 3,039 ms | 8,782 ms | 11,219 ms | 106 |
| **20260920T051936Z** | **N=2 warm** | **120** | **1,835 ms** | **7,395 ms** | 9,437 ms | **8** |

**Read that honestly:** best-ever is **8 breaches in 120 turns (6.7%)**, against a criterion of **zero**.
Neither `BRD-02` nor `BRD-05` is met, and **no qualifying run exists to certify either way**.

The app's own 3-hour live sample (n=1,195) reads p50 2,219 · p90 3,218 · p95 7,641 ms with **154 (13%) over
cap**, and decomposes those breaches: **68 are the `DEF-001` timeout (retrieval 5.8–7.2 s), and 86 are not**
— the non-timeout group has a *healthy* retrieval p50 of 766 ms, so the tail is not all one cause
(`doc/sdlc/unblock-plan.md:28-29`). A direct MCP service probe supports that: 40 back-to-back calls at
p50 **788 ms**, p95 **889 ms**, max 3,355 ms (the cold first call), with **0 of 40** reaching the 6.0 s
ceiling — so the service is fast when it answers, and the problem is that it sometimes does not.

---

## 4. What is pending — ranked by consequence

### Tier 1 — Critical

**P1. `DEF-001` — MCP retrieval intermittently hangs for the full read timeout.**
The **only** item in the doc set with an explicit severity: *"**Critical** — it is the single largest
contributor to `BRD-05` failures and it is invisible in the logs."* Status: **DEFERRED** — *"to be worked
after all 18 user stories are complete"* (`doc/sdlc/defects/DEF-001-mcp-retrieval-timeout.md:6-9`).
Measured: ~6.6 s late; rate N=1 ≤4/99 (<4%), N=2 14/200 (7%), 16/200 (8%), 10/200 (5%).
The deferral is explicitly conditional: *"safe to defer **only because C6 holds** … if any evidence appears
that a fallback ever returns empty or degraded context, this defect stops being deferrable."*

**Its counter-evidence is on record and must travel with it:** fixing it is not sufficient.
*"Replacing every timeout's retrieval with the median successful retrieval (757 ms) moves the harness clock
62 → 53 breaches … **It halves the p95 and leaves the cap unmet.**"* (`doc/sdlc/unblock-plan.md:40-51`).
So **P1 alone does not close `BRD-05`** — but it is the largest single lever, and it is invisible in logs.

### Tier 2 — Blocking, but on a person rather than on code

**P2. `DG-03` — 137 of 161 golden-set ground truths unapproved.** *"Blocks 5 stories and every
behaviour-changing change. **No agent can do this.**"* (`doc/perf/INTENT_AND_STATUS.md:109`). Explicitly
*"the designed outcome, not a defect"*.

**P3. The `BRD-02` SLO sign-off — and its number is stale.** The ~2,300 ms figure was **estimated**, not
measured; the same document now says *"**Do not quote the 2,300 ms figure this document previously
predicted.** The measured p50 is 2,953.5 ms; the arithmetic was optimistic by ~650 ms."*
(`doc/perf/us015-model-decision.md:119-120`). Needs a PO decision, not another optimisation pass.

**P4. Streaming LLM/TTS adoption.** Both measured, both **default-OFF**, both blocked on `DG-03`. This is
the single largest *measured* cap improvement available (27% → 6% over-cap) sitting switched off.

### Tier 3 — Blocked on a quiet box (measurement, not code)

**P5. The qualifying runs — `Phase 2.3`.** The bar is two consecutive ≥100-turn warm N=2 runs with **zero**
over-cap. Currently **7% over**. Blocked because *"the box was not quiet (a live WhatsApp demo with tunnels
up, and the app, MCP, Ollama, CRM and both Streamlit UIs running)"*. It also *"now needs re-planning: the
stack has since moved to the `meridian_kb__v1` collection and the C3/C5 RAG work landed, so the retrieval leg
those runs would measure is not the one the predictions were made against."*
(`doc/sdlc/DEMO-PARK-2026-09-20.md:79`, `session-handoff-2026-09-20.md:13-25`)

**P6. `harness_fault: true` on every N=2 run.** *"A run with `harness_fault: false` **and** `n > 0` has
never happened.* Until one does, **every N=2 absolute number carries the caveat**."* (`doc/sdlc/unblock-plan.md:159`).
This is why §3 above quotes the run store with its discard status attached.

**P7. The per-story load/soak gates — none has been run as specified.** `US-001`'s 30-min N=2 soak,
`US-006`'s TAC-1/2/5, `US-008`'s three consecutive N=2 runs, `US-012`'s ≥100 turns × 3, `US-013`'s outage
run (TAC-4/6/10), `US-016`'s N=3 window, `US-017`'s 2+1 run (the cited run is **discarded**, 146/198 over
cap), `US-011`'s TAC-8 no-regression pair. Estimated ~1 day plus a quiet box (`doc/sdlc/unblock-plan.md:148`).
**Note:** `US-013`'s are now *measurable for the first time* — until 2026-09-21 the circuit gated nothing, so
an outage run would have measured the absence of a mechanism.

### Tier 4 — Real defects, lower immediate impact

| # | Item | Status / consequence |
|---|---|---|
| P8 | `Twilio RTT` unmeasured — *"the one hop the local harness cannot see"*, assumed 150 ms, *"the highest-uncertainty term"* | *"If RTT is materially higher, the p95 target moves and no amount of local work recovers it."* |
| P9 | `US-002` TAC-1 framing **FAILS** — Windows `time.monotonic()` 15/16 ms granularity; 64 of 18,120 frames over 5 ms | *"the harness is **not** a faithful carrier emulation."* Does not invalidate latency measurement. |
| P10 | ~~`pick_model()`'s uncached per-utterance `/api/tags` round trip~~ | **CLOSED — this row was stale.** `app/llm_backend.py:304` resolves the tag list once per process through `_cached_tags()` (US-004 / TRD-10), and the two surviving call sites — `list_models()` and `is_ready()` — are not on the turn path. Corrected 2026-09-21; see `PERFORMANCE_RESUME_2026-09-21.md` §3 R4 |
| P11 | `OLLAMA_NUM_PARALLEL=2` — rejected, now **suspect** | Rejected on a measurement taken at RAM 97% with `num_ctx` thrashing and TTS on CPU — **all three since fixed**. *"The case for a second slot is stronger than when it was rejected."* |
| P12 | `REC-13` `memory_budget.py` `safe_threshold_percent: 95` sits **above** `BRD-11`'s 90% ceiling | *"a real defect, but it is guarding a ceiling that is not currently near"* |
| P13 | `BRD-12` RAM not sampled *during* a run — asserted only at summary time | `BRD-12` was breached at 97% unnoticed once already |
| P14 | `.env.example:26` still instructs `localhost` | *"The same trap is baked into the template. One-word fix, not yet applied."* |
| P15 | Call arrival rate / peak concurrency unmeasured (`AS-01`, `AS-06`) | peak concurrency is an assumption |
| P16 | `US-018` RAG baseline — story status `BLOCKED - DG-03` | **The "What exists: nothing" note on record is stale.** `app/rag_config.py` exists (174 lines), `test_us018_rag_baseline.py` passes **14/14** and `test_us018_rag_metrics.py` **22/22** as of 2026-09-21. (The 1 failure quoted here when this report was compiled — *"section ids are heading text, so they match the retrieval path's `[§ …]`"* — asserted the heading `Achievements & Accreditation`, which the C5 rebuild to `meridian_kb__v1` restructured into `### accreditation` + `### achievements`. The literal was replaced by the property it stood for.) The **adoption** half is what `DG-03` blocks; the baseline instrumentation is largely built |

### Explicitly NOT pending — closed this session
The four previously-inert config keys (`FASTAPI_WORKERS`, `KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL`) now all
have readers; the boot gate's `NOT READY (exit 2)` is cleared. The breaker wiring and config provenance were
closed on 2026-09-21 (§2.12).

---

## 5. The evidence base, and its limits

Stated plainly, because the numbers above are only as good as this:

1. **The run store is 97% discarded.** 73 artifacts, 71 discarded, and the 2 survivors are **single-turn cold
   runs**. There is no powered, harness-clean run in the store.
2. **`harness_fault: true` on every N=2 run** (P6). Absolute N=2 numbers carry that caveat by rule.
3. **The carrier hop is excluded from every harness figure.** *"The Twilio round trip … is EXCLUDED from every
   figure in this summary"* (run `disclosures.carrier_boundary`). Fixtures are **synthetic and not
   intelligible speech** — timing is representative, content is not.
4. **Do not pool across conditions.** *"Do not pool a figure from this run with a figure from a different
   network_profile, a different thermal bucket, or a different fixture sha256"* (`disclosures.pooling`).
5. **Some earlier numbers are void.** An hour of measurements was voided by the orphaned-runner discovery
   (§2.11); the 4,125 ms and 1,999 ms p50 figures were withdrawn as small-n noise (*"the luckiest small-n
   draw presented as *the* result"*). The `BRD-02` estimate is explicitly retracted (P3).
6. **Attribution is not separable for the model swap.** Model, `num_predict`, prompt scoping and event-loop
   fixes landed in one restart (§2.5).
7. **`SCREEN-GRADE` is not adoption-grade.** The model decision and both streaming flags carry *"no case
   carries PO approval (DG-03)"*.

**Corrections to earlier documents found while compiling this report** — recorded so they are not
double-counted: `plan-state.md:43` (8/18) contradicts its own summary line `:174` (4/18);
`remediation-plan.md:36` says "ten intents" where the tool reports 16; `session-handoff-2026-09-21.md:96`
says "1 pre-existing failure" where its own finding 5 documents six; `decisions-awaiting-po.md` D1 is
superseded by Phase 1.1's `/ready`; and the same file's US-018 entry ("**What exists:** nothing") is stale —
`app/rag_config.py` and two passing-by-majority suites now exist (§4 P16).

**Two figures circulated during compilation were checked and removed** rather than published: a stage-delta
decomposition of the 154 cap breaches, and a US-018 "nothing exists" claim. Both traced to dated documents
or were unsupported at the cited line. This is recorded because the same failure mode — a number that reads
as measured but is not — is the subject of §5.5.

---

## 6. Sources

| Area | Documents |
|---|---|
| Master status | `doc/perf/IMPLEMENTATION_STATUS.md`, `doc/perf/INTENT_AND_STATUS.md`, `doc/perf/PLAN.md` |
| Program state | `doc/sdlc/plan-state.md`, `doc/sdlc/unblock-plan.md`, `doc/sdlc/sdlc-completion-drive-status.md`, `doc/sdlc/DEMO-PARK-2026-09-20.md` |
| Requirements | `doc/sdlc/01-brd.md` (BRD-02, BRD-05, BRD-11, BRD-12), `doc/sdlc/08-coverage-verification.md` |
| Defects | `doc/sdlc/defects/DEF-001-mcp-retrieval-timeout.md` |
| Model decision | `doc/perf/us015-model-decision.md`, `doc/perf/us015-benchmark-results.md`, `doc/perf/us015-quality-screen.json` |
| Raw evidence | `doc/perf/runs/` (73 artifacts), `logs/perf_turns.jsonl` (3,626 turns) |
| Live gates | `doc/perf/tools/test_event_loop_hygiene.py`, `load_harness.py --self-test` |

*Compiled 2026-09-21. Where this report and a source document disagree, the source document wins — but two
of the sources disagreed with themselves, and those are recorded in §5.*
