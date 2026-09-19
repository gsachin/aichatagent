# Performance Program — Master Plan

**Target system:** universityDemo admissions voice assistant (Twilio + Pipecat-lite + Whisper + Ollama + RAG + Kokoro), local Windows box
**Hard requirement:** 2 simultaneous voice callers, no quality loss
**Status:** Phase 0 (recon) complete. No system changes made.
**Created:** 2026-09-18

---

## 0. How this plan was built

Three lenses, applied in order:

| Lens | What it contributed | Where it shows up |
|---|---|---|
| **AI Performance Engineer** | Measure-first discipline, bottleneck classification, quality classes A/B/C, sizing math, 11-section analysis, hard rules | §1-§4, §6-§9 |
| **Planning** | Sequencing by impact ÷ (effort × risk), phase gates, experiment budget, decision log, exit criteria | §5, §10 |
| **Prompt Enricher** | Turning the program into a cold-start-safe handoff spec with checkable done-state | §11 |

---

## 1. Executive Summary

**The system is not slow because the model is too big. It is slow because nothing is pipelined, and the framework that would pipeline it is dead code.**

The live turn is a strictly serial chain: a **600 ms hand-rolled RMS energy gate** → full Whisper transcription → an uncached `/api/tags` HTTP call → retrieval → a **non-streaming** `ollama.chat` → **batch** Kokoro synthesis → resample → send. First audio cannot begin until the LLM has finished generating *and* TTS has finished synthesising.

Measured consequence: **prefill is 1,587-1,990 ms and decode runs at 36.6-38.9 tok/s (77% of the card's 448 GB/s bandwidth ceiling)**. The bandwidth ceiling means **no configuration recovers more than ~30% decode headroom** — decode is done being optimised.

**Top recommendation:** stream the LLM and TTS (Class B, predicted **~1.3-3.1 s** off p50), then `OLLAMA_KEEP_ALIVE=-1` (Class A, removes a measured **32.9 s** cold load).

**For the 2-caller requirement specifically**, the binding constraint is **not VRAM and not the model** — it is that the ERC retrieval service runs one uvicorn worker whose "parallel" hybrid legs are synchronous CPU-bound calls that block its only event loop. One caller's retrieval stalls the other's.

---

## 2. Issue Statement

| | |
|---|---|
| **Metric** | Turn latency: caller stops speaking → first audio frame sent to Twilio |
| **Target (G1)** | p50 ≤ 700 ms, p95 ≤ 1,200 ms |
| **Baseline** | Never measured end-to-end. Component-level measurements now exist (§3) |
| **Estimated actual** | **~3.6-5.6 s p50** [Inferred from measured components + code trace] |
| **Concurrency target (G2)** | 2 simultaneous callers, independent — one slow turn must not stall the other |
| **Scope** | Voice path. Chat path shares retrieval but not VAD/telephony |
| **Trigger** | Every turn; worst on first turn after >5 min idle |

**Assumptions stated:** concurrency = 2 for capacity work; N=1 for the latency budget. Think-time and call arrival rate are **not yet measured** — see §7.

---

## 3. Evidence Base

### 3.1 Measured on this machine

| Fact | Value | Label |
|---|---|---|
| GPU | RTX 5060 Ti, 16311 MiB, 448 GB/s, GDDR7 128-bit, PCIe 5.0 x8 | [Measured] |
| GPU at idle | P5, 1000 MiB used, graphics 592/3090 MHz, **memory 405/14001 MHz** | [Measured] |
| CPU | Ryzen 5 3500, **6 cores / 6 threads (no SMT)** | [Measured] |
| RAM | 32.7 GB total, **13.9 GB free at idle** (~57% used) | [Measured] |
| **Cold model load** | **32,919 ms** (`load_duration`) | [Measured] |
| **Cold time-to-first-token** | **67,349 ms** wall clock | [Measured] |
| System prompt (no RAG) | **3,558 tokens** | [Measured] |
| System prompt + 5 RAG chunks | **5,666 tokens** | [Measured] |
| Prefill, 3,558 tok | **1,990 ms** (1,788 tok/s) | [Measured] |
| Prefill, 5,666 tok | **1,587 ms** (3,571 tok/s) | [Measured] |
| Prefill, 7,786 tok | 1,732 ms (4,495 tok/s) | [Measured] |
| **Decode** | **36.6-38.9 tok/s** = **77% of the 49.8 tok/s bandwidth ceiling** | [Measured] |
| Endpointing floor | **600 ms** (30 frames × 20 ms, RMS gate) | [Measured] |
| Services live | 8, all running, single FastAPI process | [Measured] |

### 3.2 Confirmed by code trace

| Finding | Evidence |
|---|---|
| Live endpointing = **hand-rolled RMS gate**, `return rms < 80` | `voice_handler.py:341, 367` |
| **Pipecat `VADParams(stop_secs=0.5)` is dead code** — never executes | `pipeline.py:174-180`; analyzer built, never referenced; `create_local_voice_pipeline` has one caller: `run_pipeline_test.py` |
| **LLM call is non-streaming** | `llm_backend.py:145` `ollama.chat(...)` with no `stream=True` |
| **TTS uses batch `create()`, not `create_stream()`** | `voice_handler.py:673`; streaming API exists in the installed library and is used by Pipecat's own service |
| `/api/tags` HTTP call **per utterance**, uncached | `rag.py:188` → `llm_backend.py:193` |
| **`FASTAPI_WORKERS=4` is never consumed** — single process | `start_services.ps1:559` (no `--workers`); all repo refs are the *writer* side in `hardware_profile.py` |
| **Reranker loaded at boot, never called** (23.2 MB ONNX) | Only invoked by `execute_agent_context`; that symbol appears **nowhere** in this repo (verified by grep) |
| **Semantic cache configured, bypassed** | Same reason; `retrieve_context` skips it |
| **ERC retrieval is a serialization point** | `cli.py:83` single worker; `hybrid.py:141-144` `gather` over two *synchronous CPU-bound* calls; full-corpus BM25 in Python |
| **Barge-in impossible** — caller audio discarded during TTS | `main.py:624-627`, `MUTE_STT_DURING_TTS=1` default |
| Greeting synthesis blocks the event loop | `main.py:593` — sync call in async handler |
| Two divergent KB stores (different chunkers) | local `langchain` ~47 recs vs ERC `meridian-kb` 37 chunks |

### 3.3 Bottleneck classification

- **Prefill:** compute-bound, but only ~1.6-2.0 s — **not** the dominant term.
- **Decode:** memory-bandwidth-bound at **77% of ceiling** — near-optimal; little left.
- **The real cost is *exposure*:** generation and synthesis are each fully completed before the next stage starts. This is a **pipeline-architecture** problem, not a compute problem.
- **Concurrency:** the ERC service is **single-event-loop with blocking work** — a queueing/serialization problem.

---

## 4. Root Causes (ranked, with confidence)

### RC1 — Nothing is pipelined: full generation + full synthesis are exposed before first audio · **Confidence: High**
**Mechanism:** non-streaming `ollama.chat` returns only complete text; batch `create()` returns only complete audio; `_pcm_to_ulaw_chunks` resamples the whole array. First audio = endpointing + STT + generation + synthesis, with zero overlap.
**Supporting:** code trace (§3.2); measured decode rate.
**Contradicting:** none found.
**Impact:** ~1.3-3.1 s of the p50.

### RC2 — 600 ms endpointing floor · **Confidence: High**
**Mechanism:** 30 consecutive frames below RMS 80. Correct for turn-taking (below ~300 ms you cut off slow speakers) but it is **86% of the 700 ms target** before STT runs.
**Supporting:** `voice_handler.py:282, 341, 367`.
**Contradicting:** lowering it is a UX trade, not free.
**Impact:** 600 ms, irreducible without a Class C change.

### RC3 — Cold model load on isolated calls · **Confidence: High**
**Mechanism:** `OLLAMA_KEEP_ALIVE` unset → 5 min default → 9 GB reload.
**Supporting:** measured 32.9 s load; `ollama ps` empty at recon time; env unset.
**Impact:** 32.9 s+ on the first turn after any 5-minute gap. For a sporadic phone line this is likely most calls.

### RC4 — Remote-format assumption corrected; actual cause is one inert config · **Confidence: High**
**RC4 (concurrency): ERC retrieval serializes callers.** Single uvicorn worker; the `gather` fans out to synchronous CPU-bound Chroma and full-corpus BM25 calls that block the only event loop.
**Impact:** directly violates G2's "one call's slow turn must not stall the other".

### RC5 — 3,558-token fixed system prompt + 2,108 tokens of RAG · **Confidence: High**
Every turn re-prefills a large prompt. `{context}` sits mid-template (`voice_system_prompt.py:600`) with ~500 tokens of static text after it, limiting prefix-cache reach.
**Contradicting:** whether Ollama's prefix cache engages at all is **unverified** — this is the highest-value open measurement.

---

## 5. The Plan

Sequenced by **impact ÷ (effort × risk)**. Every experiment carries a **numeric prediction written before the run**.

### Phase A — Instrumentation, baseline, and the golden set (no optimisation)

| ID | Experiment | Deliverable | Exit criterion |
|---|---|---|---|
| A1 | Structured JSON logging at 6 stage boundaries (`call_id`, `turn_id`, monotonic clock) | `doc/perf/tools/` | One real call produces a complete per-stage trace |
| A2 | Twilio media-stream load harness, N=1 and N=2 | `doc/perf/tools/` | Replays scripted multi-turn audio at 8 kHz µ-law |
| A3 | Baseline: per-stage latency N=1/N=2, cold and warm | `doc/perf/00_baseline.md` | ≥100 turns per condition; p50/p95 with sample size |
| A4 | **Prefix-cache probe** — identical prompt twice, compare `prompt_eval_count` | same | Verdict: does caching engage? |
| A5 | Golden set — 28 intents (§8), 60+ cases, 15 multi-turn, 10 noisy-ASR, 10 adversarial | `eval/golden_set.jsonl` | Frozen + hashed; **your sign-off on critical-intent ground truth** |
| A6 | Quality baseline on the frozen set | `01_budget.md` | Scores recorded for the current config |

**Gate to Phase B:** A4 answered and A6 recorded. *Prefix caching changes the ranking below.*

### Phase B — Class A wins (free, reversible, no behaviour change)

| ID | Change | Prediction | Rollback |
|---|---|---|---|
| B1 | `OLLAMA_KEEP_ALIVE=-1` | Removes 32.9 s+ from first turn after idle; VRAM steady-state ~11 GB | unset var, restart Ollama |
| B2 | Cache `/api/tags` result per process | Removes one HTTP RTT per utterance (~5-30 ms) | revert commit |
| B3 | HTTP connection reuse in `rag_mcp._post()` | Removes TCP setup per retrieval | revert commit |
| B4 | Circuit-breaker half-open probe | Avoids repeated 2.5 s timeouts after a trip | revert commit |
| B5 | Boot-time LLM preload + prompt-prefix warm | G3 cold start within G1 p95 | remove from startup |

**Gate to Phase C:** G3 passes; no regression in A3's numbers.

### Phase C — Streaming (largest latency win)

| ID | Change | Prediction | Class |
|---|---|---|---|
| C1 | **Stream the LLM** — `stream=True`, consume incrementally, emit first sentence to TTS | **−1.0 to −2.5 s p50** | **B** |
| C2 | **Stream TTS** — `create_stream()` instead of `create()` | −0.3 to −0.6 s | **B** |
| C3 | Move `{context}` to end of prompt template | Improves prefix-cache reach; −0 to −700 ms *if A4 shows caching* | **B** |
| C4 | Edge-trigger the greeting so it doesn't block the event loop | Removes a first-turn stall | **A** |

**Gate to Phase D:** G1 p50/p95 measured with ≥100 turns; C1+C2 predictions compared to actuals. **If measured gain < 50% of prediction, stop and debug before proceeding.**

### Phase D — Prompt and RAG reduction (quality-gated)

| ID | Change | Prediction | Class |
|---|---|---|---|
| D1 | Trim system prompt — remove the two interruption sections (~300-500 tok) that describe behaviour the architecture prevents | −100 to −200 ms | **B** |
| D2 | `RAG_TOP_K` 5→3 + chunk truncation | −300 to −500 ms | **B** |
| D3 | Enable `RAG_SIMILARITY_THRESHOLD` | Quality up, latency +one MCP call — **evaluate cost** | **B** |
| D4 | Reconcile the two KB stores | Consistency: same question → same chunks | **B** |
| D5 | History strategy: sliding window vs summary | Bound per-turn growth | **B** |

**Every D item runs the full golden set. Any critical-intent regression blocks adoption.**

### Phase E — Concurrency (the hard requirement)

| ID | Change | Prediction | Class |
|---|---|---|---|
| E1 | **De-serialize ERC retrieval** — thread the sync legs, or raise workers | G2: caller B no longer stalls behind caller A | **A/B** |
| E2 | `OLLAMA_NUM_PARALLEL=2` + KV budget check | 2 streams at ~19 tok/s each; VRAM ~90% | **A** |
| E3 | Model candidate sweep: 7B/8B vs 14B, Q4/Q5/Q6, **measured at N=1 and N=2** | 7B ≈ 2× decode → restores single-user feel to each of 2 callers | **C** |
| E4 | Barge-in — requires the AEC trade-off decision | G8 passes | **C — needs your sign-off** |

**Gate to Phase F:** G2 passes on 3 consecutive runs; VRAM ≤ 90%.

### Phase F — Verification and handoff

| ID | Activity | Exit criterion |
|---|---|---|
| F1 | Full gate table G1-G10, 3 consecutive runs | All pass, or Pareto report |
| F2 | 30-minute soak, 2 scripted callers | No crashes, drift < 10%, no memory trend |
| F3 | Failure matrix (Ollama/RAG/DB/TTS down, one caller hangs) | Second caller unaffected |
| F4 | `FINAL_REPORT.md` + startup/preload script + rollback doc | Handover complete |

**Experiment budget: 25 (checkpoint at 10).** Phases B-F total ~22 experiments.

---

## 6. Solution Options — full table

| # | Solution | Layer | Predicted impact | Effort | Risk | Class |
|---|---|---|---|---|---|---|
| C1 | Stream LLM | Inference | −1.0 to −2.5 s | 1-2 d | Med | **B** |
| C2 | Stream TTS | TTS | −0.3 to −0.6 s | 1 d | Low | **B** |
| B1 | Keep-alive | Config | −32.9 s cold | 1 min | None | **A** |
| E1 | De-serialize ERC | Retrieval | G2 pass | 1-2 d | Med | **A/B** |
| D1 | Trim prompt | Prompt | −100 to −200 ms | 2-4 h | Med | **B** |
| D2 | Reduce RAG k | Retrieval | −300 to −500 ms | 1-2 h | Med | **B** |
| E3 | 7B model | Model | ~2× decode | 1-2 d | **Quality** | **C** |
| C3 | Prompt reorder | Prompt | 0 to −700 ms | 2-4 h | Low | **B** |
| E4 | Barge-in | Turn-taking | G8 pass | 2-3 d | **UX/AEC** | **C** |
| B2 | Cache `/api/tags` | Config | −5 to −30 ms | 1 h | None | **A** |
| B3 | HTTP reuse | Retrieval | −10 to −50 ms | 2 h | Low | **A** |
| — | Lower `stop_secs` | VAD | −200 to −300 ms | 10 min | **High (cutoffs)** | **C** |

---

## 7. Capacity and Scaling

**Concurrency estimate (Little's Law):** `concurrent = arrival rate × holding time`. Call arrival rate is **not yet measured** — A3 must capture it.

**Per-call resource budget at N=1:** weights 9.0 + KV 1.5 + Whisper 0.8 + Kokoro 0.35 + CUDA/desktop ~1.5 = **~13.2 of 16.3 GB**.

**At N=2:** +1.5 GB KV → **~14.7 GB = 90%.** G5's 1.5 GB headroom is **not** met. KV quantization or a smaller model is required.

**Binding constraint at N=2: memory bandwidth, not VRAM.**
Measured 38.5 tok/s at 77% of the 49.8 tok/s ceiling. Two streams share 448 GB/s → **~19 tok/s each** [Inferred from measured efficiency].

| Model | 1 stream | 2 streams each | VRAM @ N=2 | Note |
|---|---:|---:|---:|---|
| 14B Q4 | 38.5 tok/s [Measured] | ~19 [Inferred] | ~90% | Risk of tail spikes |
| 7B Q4 | ~73 [Inferred] | ~36 [Inferred] | ~63% | **Not installed — must be measured** |
| 3B | — | — | — | Quality risk vs 28-intent surface |

**Autoscaling: not applicable.** One box, bandwidth-bound. More replicas means more GPUs, not more pods.

**What breaks first at N=3:** the 90% VRAM line, then the shared bandwidth. Expect OOM or severe degradation.

---

## 8. Software and Architecture Modifications

**Technology review — is the current stack appropriate?**

| Component | Verdict |
|---|---|
| Ollama | **Appropriate** at this scale; supports what C1/C2 need |
| Pipecat 1.6.0 | **Dead weight.** Installed, pinned, fully bypassed. Either wire it in (with streaming + interruptions — a real migration) or remove the dependency. Its `KokoroTTSService` already uses `create_stream()` and would have provided C2 for free. |
| faster-whisper (GPU/int8) | Appropriate |
| Kokoro ONNX | Appropriate; `create_stream()` unused |
| ERC service | **Single-worker blocking design is the N=2 blocker.** Reranker (23.2 MB) and semantic cache are loaded/configured but unreachable from this app — either wire them in or stop paying for them at boot. |
| Two KB stores | **Redundant.** Same source, two chunkers, divergent results. Pick one. |

**Do not migrate to vLLM/SGLang yet.** C1/C2 are Class B and worth more than an engine swap; §6 principle #5 and the "never recommend a full platform migration when a config fix addresses the measured bottleneck" rule both apply.

---

## 9. Validation Plan

| Change | Test | Pass threshold |
|---|---|---|
| B* | A3 suite re-run | No regression; G3 passes |
| C1/C2 | ≥100 turns, N=1, cold+warm reported separately | G1 p50/p95 met or best-defensible SLO documented |
| D* | Full golden set, held-out 20% never tuned against | Zero regression on critical intents; ≤2 pt aggregate drop; no hallucination increase |
| E1 | A2 harness, N=2, one caller deliberately slowed | Caller B latency within 1.15× of solo |
| E3 | Golden set **and** latency at N=1/N=2 | Quality gate + G2 |
| E4 | 10 real calls, count false cutoffs | <1 per call |

**Rules:** changes sequential (one variable). Held-out 20% frozen and never used for tuning. Cold and warm reported separately. p50/p95 with sample size and spread — never single runs. **If a result surprises you, stop and explain it before proceeding.**

---

## 10. Monitoring, Decision Log, and Open Risks

### Decision log (append per experiment)

| Config hash | Change | Prediction | Result | Gates | Verdict |
|---|---|---|---|---|---|

### Monitoring to install
Per-stage latency (p50/p95) per turn; GPU VRAM/util/clocks; OLLAMA residency; ERC queue depth; RAM. Alert on p95 drift > 20% and on VRAM > 92%.

### Open risks
1. **Prefix caching unverified** — A4 gates the ranking of C3 and D2.
2. **Call arrival rate unmeasured** — §7 capacity is incomplete until A3.
3. **VRAM at N=2 is 90%** — G5 not met without KV quantization or a smaller model.
4. **Two Class C decisions pending** — model swap and barge-in both need your sign-off.
5. **`.env` may contain more decorative keys.** Four dead settings found so far (Pipecat VAD, `FASTAPI_WORKERS`, reranker, semantic cache). Treat "set" ≠ "live" as a standing rule.

---

## 11. Handoff Spec (Prompt Enricher)

### JTBD
**Job:** Execute Phase A (instrumentation + baseline + golden set) of the performance program for the admissions voice assistant.
**Audience:** A fresh agent session with **no access to this conversation** — it will not see the measurements unless they are inlined.
**Done state:** `doc/perf/00_baseline.md` exists with a complete per-stage latency trace from one real call, the prefix-cache verdict, and ≥100 turns of N=1/N=2 data.

**Assumed:** the agent has shell access to the Windows box and read access to the repo.

### Scorecard — the existing master prompt

| Axis | Score | Evidence |
|---|---|---|
| Analytical depth | 5 | Decomposes into phases, workstreams, gates |
| Architecture | 5 | Explicit Role/Constraints/Gates/Deliverables |
| Visualization | 4 | Specifies tables and formats |
| Articulation | 4 | Precise; but "the review document" is an external referent |
| Intent | 5 | §4 gates + §6 exit conditions are explicit |
| **Conversational** | **2** | **"attach the performance review document" — won't exist in a fresh session** |
| Imagination | 4 | Requires ≥3 alternatives per workstream |
| Solutioning | 5 | Deliverable paths, rollback, change classes |

**Per this skill's own rule ("every axis ≥3 → say it's good, stop"), the structure needs no rewrite.** Fix only the low axes.

### Top fixes (only the low axes, plus one correctness fix)

1. **[Articulation/correctness] Inline the measured baseline — and correct two now-falsified facts.** §3 of the master prompt states the endpointing floor is 500 ms at `pipeline.py:177`. **That is dead code; the live floor is 600 ms at `voice_handler.py:282`.** It also implies 4 FastAPI workers; there is **one**. A fresh agent inheriting these will optimise the wrong file.
2. **[Conversational] Remove the external attachment dependency.** Inline the numbers.
3. **[Intent] Add the experiment budget and the stop rule.** "Iterate until convergence" with no budget produces either stalling or fabrication.

### Rewritten handoff prompt

```
ROLE
You are a performance engineer executing Phase A of a defined program on a
Windows box. You measure; you do not optimise yet.

CONTEXT — verified 2026-09-18, do not re-derive
  System: admissions voice assistant. Twilio -> FastAPI :8000 -> WS /ws/twilio.
  Hardware: RTX 5060 Ti 16 GB (448 GB/s), Ryzen 5 3500 6c/6t, 32 GB RAM.
  Model: qwen2.5:14b (9.0 GB), num_ctx 8192, Ollama :11434.

  MEASURED (Ollama counters, this box):
    system prompt (no RAG) .......... 3,558 tokens
    system prompt + 5 RAG chunks .... 5,666 tokens
    prefill ......................... 1,587-1,990 ms
    decode .......................... 36.6-38.9 tok/s (= 77% of bandwidth ceiling)
    cold load ....................... 32,919 ms; 67,349 ms to first token
    endpointing floor ............... 600 ms

  LIVE PATH (verified by code trace):
    endpointing = hand-rolled RMS gate, voice_handler.py:341,367 (NOT Pipecat)
    Pipecat VADParams at pipeline.py:174-180 is DEAD CODE - do not edit it
    LLM call is NON-STREAMING (llm_backend.py:145)
    TTS uses batch create(), not create_stream() (voice_handler.py:673)
    FastAPI runs ONE process (FASTAPI_WORKERS is never read)

TASK
Instrument the live voice turn path with structured JSON logging, then produce
a baseline measurement document.

CONSTRAINTS
- Do not change config, prompts, services, or the model. Instrumentation only.
- Read-only against production; add logging on a branch.
- Numbers about this machine come from measurement, never recall.
- Label every number [Measured] / [Inferred] / [Assumed].

OUTPUT FORMAT
doc/perf/00_baseline.md containing:
  1. Per-stage latency table (p50/p95, N=1 and N=2, cold and warm separate):
     VAD-end | STT-final | retrieval | LLM TTFT | first sentence | TTS first
     chunk | first frame to Twilio | TOTAL
  2. Prompt token breakdown: static prefix / RAG / history / user turn
  3. Prefix-cache verdict: send an identical prompt twice, compare
     prompt_eval_count and prompt_eval_duration
  4. Resource time series at N=1 and N=2
  5. Sample size for every figure

SUCCESS CRITERIA
- A single real call yields a complete, correlated per-stage trace
- >=100 turns per condition
- The prefix-cache question is answered yes/no with evidence
- Any figure you could not measure is listed as an open question, not estimated
```

**Omitted sections:** no Examples block — the output format and the inlined baseline already calibrate depth; an example would add length without disambiguating.

### Change log

| Change | Axis repaired | Why it matters |
|---|---|---|
| Inlined all measured numbers | Conversational | The prompt now survives the death of this session |
| Corrected endpointing 500→600 ms and workers 4→1 | Articulation | Prevents optimising dead code — the single highest-cost error available |
| Added "do not re-derive" + explicit open-question escape | Intent | Stops a fresh agent re-measuring what's known, or inventing what isn't |
| Added experiment budget and stop rule | Intent | Bounds an otherwise unbounded program |
| Kept the original's gates and change classes verbatim | — | Already correct; churning them would lose information |

### Test prompts

1. **Cold-start test:** run the rewritten prompt in a fresh session with no files attached. *Checks Context + Articulation* — it should produce a baseline doc without asking what the system is or where the 600 ms came from.
2. **Done-state test:** hand §SUCCESS CRITERIA alone to someone who never saw the program. *Checks Intent + Solutioning* — they should be able to judge whether the baseline is complete.
3. **Drift test:** the likely misread is a fresh agent "fixing" `stop_secs` in `pipeline.py:177` because it looks like the endpointing config. *Checks Constraints* — the DEAD CODE warning must prevent it.

---

## 12. Next Actions Executable Today

```powershell
# Class A, zero risk, 1 minute - removes a measured 32.9 s cold load
[Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE','-1','User')
# then restart the Ollama server (PID 12800 owns :11434)
```

Then: approve Phase A (§5) so instrumentation can begin, and answer the four open decisions — `RAG_SIMILARITY_THRESHOLD=0.0` intent, golden-set ground truth, experiment budget, intent depth.
