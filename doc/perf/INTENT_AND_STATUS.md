# Intent, Current State, Done and Pending

**As of:** 2026-09-19 · **Branch:** `perf/phase-a` · **Companion docs:** `PLAN.md`, `IMPLEMENTATION_STATUS.md`, `us015-benchmark-results.md`, `doc/sdlc/`

This is the one document to read to understand what was asked for, what exists now, and what is left. The narrative behind each finding lives in `IMPLEMENTATION_STATUS.md`.

---

## 1. The intent

The engagement layered three goals on top of each other. All three are still live; only the first was explicit at the start.

### Layer 1 — the original ask
Reduce **turn latency** for the admissions voice assistant: caller stops speaking → first syllable heard, targeting **p50 ≤ 700 ms / p95 ≤ 1,200 ms**, and find out what hardware and configuration that actually requires. The starting artefact was a prompt (`doc/LOW_LATENCY_SIZING_PROMPT.md`) meant to be polled across several LLMs.

### Layer 2 — the constraint that reshaped it
The hard requirement became **two simultaneous voice callers, entirely local, with no loss of answer quality**. That changed the problem from "make one call fast" to "make two concurrent calls work on one box" — and "no quality loss" made every speed change conditional on an evaluation that did not yet exist.

### Layer 3 — the process
Produce a full SDLC plan (BRD, TRD, user stories) before implementing, then implement **story by story, least dependent to most**, maintaining a status table with evidence per story.

### What the intent was *not*
Not a rewrite. Not a model-downsizing project. Not a cloud migration — student PII keeps inference local. And explicitly **not** a plan that assumes the current architecture was the right one: the solution-neutrality rule (`doc/sdlc/00-product-intent.md` §6b) requires every component to earn its place against measured alternatives.

---

## 2. Where we are now — one page

| | |
|---|---|
| **Plan** | Complete. `doc/sdlc/` — 36 files, validator **752 passed / 0 failed**. 18 stories, 26 technical requirements, 13 reconciliation notes. |
| **Quality gate** | **Blocked.** Golden set built (`eval/`, 161 cases, 28/28 intents) but **0 ground truths approved**. All 12 critical intents report BLOCKED. |
| **Implementation** | **6 of 18 stories done.** All Class A; nothing touching behaviour has shipped. |
| **N=2 p50** | **1,570 ms** (was 7,617 ms) — **below the 3,000 ms per-turn cap for the first time** |
| **N=2 p95** | **5,544 ms** — still ~1.8× over the cap |
| **Sample size** | n=10. The requirement needs **≥100**. Every figure here is flagged UNDERPOWERED. |
| **Original 700 ms target** | Still not reachable. The tightest defensible SLO derived from measurement is ~2,300 ms p50, and it needs PO approval. |

**In one sentence:** the system is roughly 5× faster than when we started, the median now clears the cap, and the tail plus the missing quality gate are what remain.

---

## 3. Done — with evidence

### Planning artefacts
| Deliverable | Evidence |
|---|---|
| Full SDLC plan (`doc/sdlc/`) | Validator: **752 passed, 0 failed**, exit 0 |
| Intent catalog — 28 intents, 12 critical | `doc/sdlc/09-intent-catalog.md` |
| Brownfield reconciliation — 13 plan-vs-code conflicts | `doc/sdlc/07-brownfield-reconciliation.md` |
| Golden set + eval harness | `eval/` — 161 cases; `checks.py` exit 0, self-testing |

### Stories implemented
| Story | Status | Evidence |
|---|---|---|
| **US-001** per-turn tracing | DONE | `test_us001_tracer.py` → **17/17**. Proven end-to-end on a live call |
| **US-011** config source of truth | DONE | `test_us011_config_truth.py` → **16/16** |
| **US-006** model residency | DONE | `test_us006_residency.py` → **11/11**; `ollama ps` → `Forever` |
| **US-008** de-serialize retrieval | DONE | ERC legs off the event loop; retrieval mean 7,164 → ~2,227 ms |
| **US-003** golden set | BUILT, AWAITING SIGN-OFF | 161 cases; **137 of 161 ground truths PENDING** |
| **US-002** load harness | IN PROGRESS | Drives live `/ws/twilio`; two gaps below |
| **US-015** model benchmark | PARTIAL | Latency leg measured; quality leg blocked |

### Defects found and fixed — none of which were in the original plan
| Finding | Effect |
|---|---|
| **`num_ctx` thrashing** — four context sizes on one model tore down the runner per switch | Warm turn **8,609 → 360 ms** |
| **Kokoro TTS running on CPU** while the app logged "CUDA GPU enabled" | RTF **0.63 → 0.061**; the N=2 TTS term **12–16 s → 0.8–1.4 s** |
| **25.7 GB of orphaned Ollama runners**, paged out — caused by my own restarts | RAM **97% → 31%** |
| **`retrieval_ms` was derived, not measured** — absorbed load and queue time | Read 5,432 ms; true value 2,227 ms |
| **`BRD-12` had no observer** | RAM reached 97% with nothing noticing |
| `OLLAMA_NUM_PARALLEL=2` | **Applied then reverted** — did not earn its 1.5 GiB |

### Cumulative effect on the hard requirement

| | N=2 warm p50 |
|---|---|
| Session start | 7,617 ms |
| After `num_ctx` + TTS + US-008 | 3,570 ms |
| **After memory fix + larger sample** | **1,570 ms** |

---

## 4. Pending

### Blocked on you
| Item | What is needed |
|---|---|
| **`DG-03`** — golden-set ground truth | **12 critical-intent ground truths need approval.** 137 of 161 cases are PENDING. Nothing quality-gated can ship until this closes. `US-003` cannot be completed by any agent. |
| **`US-014`** — interruption decision | Class C. Options, costs and a recommendation are written (`doc/perf/decisions/US-014-caller-interruption.md`); the decision is yours. |
| **`BRD-02` SLO approval** | 700 ms is arithmetically unreachable behind a 600 ms endpointing floor. The tightest defensible SLO (~2,300 ms p50) needs your sign-off under the requirement's own evidence clause. |

### Blocked on `DG-03`
`US-004` (stream LLM) · `US-005` (stream TTS) · `US-009` (relevance floor) · `US-010` (consolidate stores) · `US-018` (RAG baseline/optimized) · `US-015` quality leg — **every change that alters what the model sees or says.**

### Ready to implement, no blocker
`US-007` (boot warm state) · `US-013` (timeouts + half-open probe) · `US-012` (TTS cache isolation) · `US-016` (admission control) · `US-017` (background priority)

### Open technical questions — measured, not yet explained
| Question | Why it matters |
|---|---|
| **The p95 stall** — median 1,570 ms but p95 5,544 ms | Half the callers are fine; the ones who hit the stall are not. The p95 exists to protect exactly them. |
| **`llm_queue` ~3,600 ms** — survives the memory fix | The largest unexplained term. Memory pressure was *a* cause, not *the* cause. |
| **Long-run saturation** — a 20-turn N=2 run produced **zero samples** while 6-turn runs succeeded | A soak is what `TAC-4` needs. A soak that silently yields no data looks like a clean run. |
| **`TAC-1` framing** — fails on this box | Windows `time.monotonic()` advances in 15/16 ms ticks. Root-caused; not fixable in-process. |
| **MCP timeout double-payment** — 2.5 s timeout *plus* a full local re-retrieval | Client reuse was applied; effect not confirmed under load. |

### Not yet done at all
**A ≥100-sample baseline.** Every latency figure in this document is `UNDERPOWERED`. Four separate conclusions today needed revising after a cleaner measurement, so no number here should be treated as settled until that run exists.

---

## 5. What this engagement actually taught

Recorded because it is the most transferable output.

**Static analysis predicted one bottleneck correctly, and missed three bigger ones.** `06-architecture.md` §5 named `MOD-02`'s event loop as the first non-resource bottleneck, from code reading alone — and measurement confirmed it. But `num_ctx` thrashing, TTS-on-CPU, and 25.7 GB of orphaned runners were **all invisible to reading** and each was larger than the predicted one.

**"Set ≠ live" is now ten separate findings.** Pipecat, `FASTAPI_WORKERS`, `.machine_profile.json`, the pre-warm pipe bug, the reranker, the semantic cache, `KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL`, and — the worst — a log line asserting GPU acceleration while running on CPU. The plan's standing rule is that *"the value is set"* and *"the value matters"* are different claims, and this codebase proves it repeatedly.

**My own instrumentation lied three times, each plausibly.** The `480` soak figure, a derived `retrieval_ms` that absorbed whatever it wasn't told about, and a `llm_queue` explanation that fit the evidence and was wrong once conditions were controlled. **Every one was caught by a live measurement contradicting a number that looked fine.**

---

## 6. What needs a decision

1. **Approve ground truth for the 12 critical intents**, or descope the quality gate. This is the single largest unblocker — six stories sit behind it.
2. **Approve the revised SLO**, or state that 700 ms remains the target and accept it is unreachable locally.
3. **Decide `US-014`** (interruption) — Class C.
4. **Choose the next technical target:** the p95 stall, the long-run saturation, or a ≥100-sample baseline first. **My recommendation is the baseline** — the other two cannot be judged against numbers that are still underpowered.
