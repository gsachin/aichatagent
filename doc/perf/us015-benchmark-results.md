> **Lens:** Architect (measurement) + TPO (feasibility) · **Inputs:** `doc/perf/tools/us015_model_benchmark.py` (v3, verified) · **Date:** 2026-09-18 · **Status:** **LATENCY LEG ONLY — quality leg blocked by `DG-03`** · **Implements:** `US-015` (partial), `TRD-22`

# `US-015` Benchmark Results — Model / Runtime Pareto (latency leg)

## 1. The result table

Measured on this box, from Ollama's own counters. Context **varies per call**, so the prefix cache breaks at `{context}` the way it does in a real turn — this is the realistic case, not a warm-cache best case.

| Model | Per-turn prefill | N=1 decode | **N=2 per-stream** | N=2 aggregate | **N=2 wall ×** | VRAM @ N=2 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `llama3.2:3b` | **100 ms** | 131.0 tok/s | **131.2 tok/s** | 130–205 tok/s | **1.13×** | **26%** | **BATCHED** — both callers served together |
| `qwen2.5:14b` | **411 ms** | 39.2 tok/s | **38.6 tok/s** | 39–62 tok/s | **1.42×** | **69%** | **PARTIAL** — some overlap, some queueing |

Reference figures, reported separately because they are a different condition:
fully-cached prefill (identical prompt) is **35 ms** (3B) and **75 ms** (14B).

## 2. What this falsifies

### 2.1 "Decode halves per added stream" — **falsified, in the benign direction**

| | Assumed by the plan (then withdrawn) | **Measured** |
|---|---|---|
| 14B N=2 per-stream | ~19 tok/s | **38.6 tok/s** |
| Degradation vs N=1 | −50% | **−1.5%** |

Per-stream decode is **essentially unchanged** at N=2 on both models. The plan's first capacity claim — "concurrent calls split 448 GB/s, so each gets ~15 tok/s" — and its half of my own review (`~19 tok/s each`) were both wrong by roughly **2×**, in the pessimistic direction.

`TRD-22` was right to make this a measurement rather than a requirement, and `MOD-03` A.5 criterion 3 was right to withdraw the `≥ 18 tok/s` floor. **The withdrawn figure is not reinstated — it is now superseded by a measured value.**

### 2.2 "VRAM ~90% at N=2" — **falsified**

| | Projected | **Measured (14B, N=2)** |
|---|---|---|
| Peak VRAM | ~14.7 / 16.3 GB = 90% | **11,272 MiB = 69%** |

`BRD-11`'s 90% ceiling is met with **~5 GB of headroom**, not breached. The projection assumed two full 1.5 GiB KV sequences; the actual allocation is materially smaller. `REC-13`'s stale `memory_budget.py` guard (alert at 95%) remains a real defect, but it is guarding a ceiling that is not currently near.

### 2.3a CORRECTION (2026-09-18, later the same day) — the 411 ms prefill figure does NOT hold on the live path

**This document reports per-turn prefill at 411 ms. The live application path measures 2,490 ms.** The benchmark was not wrong about what it measured, but what it measured was not production.

The benchmark called the chat model repeatedly at **one** context size, so the runner was never rebuilt and the static prefix stayed cached. The app calls the same model at **four** context sizes (8192 / 2048 / 1024 / 512), and every context change tears down the runner — wiping the KV cache with it. Server log: `cached n_tokens = 0`.

So the 411 ms describes a cache that production did not have. The cause is fixed (`SMALL_TASK_NUM_CTX=8192`, see `doc/perf/IMPLEMENTATION_STATUS.md`), and once measured again on a warm turn the figure should be revisited. **Until re-measured, treat 411 ms as an isolated-benchmark figure, not a production one.** The decode, VRAM and N=2 concurrency results in §1 are unaffected — those did not depend on cache retention.

### 2.3 "Prefill 1,588–1,990 ms" — **was a cold-cache figure, not the per-turn cost**

| Condition | 14B |
|---|---|
| Fully cached (identical prompt) | 75 ms |
| **Realistic (context changes per turn)** | **411 ms** |
| Cold cache, first call of a session (measured earlier) | ~1,990 ms |

The plan's headline prefill number described the **first** call of a conversation. Steady-state per-turn prefill is **411 ms** for the 14B and **100 ms** for the 3B. Prefill is roughly **4× smaller** than the plan modelled, which shrinks the prefill lever (D2/D1) proportionally — reducing RAG context still helps, but by ~400 ms at most, not ~1,600 ms.

## 3. The concurrency question, answered

The wall-time ratio is the signal that distinguishes *batching* from *queueing* — per-stream rate alone cannot, because both preserve it.

- **3B → 1.13×.** The engine genuinely batches. Two callers are served together for a 13% wall-time cost. `BRD-05` is comfortably satisfiable.
- **14B → 1.42×.** Partial overlap. Better than the 2× a queue would cost, worse than the 3B. Callers are **not** strictly serialized, but the second caller pays real contention.

**This is the first direct evidence for `BRD-05` and `BRD-07`,** and it is better news than the architecture assumed: the concurrency requirement is not obviously at risk from the engine, and `MOD-02`'s retrieval serialization (`BRD-07`) remains the more likely N=2 bottleneck — it has not yet been measured.

## 4. The Pareto picture so far

| | `llama3.2:3b` | `qwen2.5:14b` |
|---|---|---|
| Decode | **3.3× faster** | baseline |
| Prefill | **4× faster** | baseline |
| VRAM | **26%** | 69% |
| N=2 behaviour | **batches cleanly** | partial contention |
| **Quality** | **UNKNOWN** | current production baseline |

**No recommendation is made.** The 3B wins on every latency and resource axis measured — which is exactly the shape of result that tempts a premature model swap. But quality is the axis that decides this, and **`DG-03` is not frozen**, so the quality column is empty and the comparison is incomplete. A 3B model answering questions about fees, deadlines and eligibility from an 11 KB knowledge base is precisely where small-model regression would show up, and it is precisely what cannot yet be tested.

**The honest statement is: on latency and resources the incumbent loses; on quality the incumbent is unmeasured-against. The decision waits for `DG-03`.**

## 5. Method — and the three flaws corrected to get here

The first two runs of this benchmark produced **wrong numbers**, and the corrections are recorded because the same errors are easy to repeat:

1. **Warm-cache illusion.** v1 reused one identical prompt, so the prefix cache was always warm and "prefill" read 12–37 ms. Production breaks the cache at `{context}` every turn. v2 varies the context per call and reports cached and cache-break cases separately.
2. **An impossible aggregate.** v1 divided a concurrent aggregate by 2, reporting aggregate *below* per-stream — impossible if the streams overlap. v2 brackets the aggregate between the serial and batched bounds.
3. **Model co-residency.** v2 read 14,379 MiB for **both** models because Ollama kept the previous candidate resident. v3 unloads other candidates before measuring, giving 26% and 69%.

**A fourth limitation remains and is not fixed:** the harness is local, so the carrier round trip is excluded, and N=2 here means two concurrent *requests*, not two PSTN calls. Carrier behaviour is validated with real calls (`US-002` TAC-9).

## 6. What remains for `US-015`

| Leg | Status |
|---|---|
| Quantization × runtime matrix | **NOT STARTED** — only two models measured; no quantization sweep, no runtime comparison (llama.cpp server, vLLM) |
| 7–9B class | **NOT MEASURED** — not installed on this box; would need a pull |
| Quality screen | **BLOCKED** — `DG-03` |
| Pareto selection | **CANNOT COMPLETE** — one axis is empty |

`US-015` is **partially delivered**: the N=1/N=2 latency and resource legs are measured and have already falsified two plan assumptions. It cannot close until the golden set exists.
