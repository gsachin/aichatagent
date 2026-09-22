# Performance workstream — resume point

**Written:** 2026-09-21 · **Repo:** `D:\project\universityDemo` · **Branch:** `sdlc/us011-tac1-config-migration`
**Purpose:** the performance task has run four days and is not complete. This file is the place to
restart it from. It says what is done, what is left, **which of the remainder actually moves
latency**, and why the task is not finished.

**Read with:** `doc/perf/PERFORMANCE_REPORT_2026-09-21.md` (the evidence-graded status; this file does
not repeat its tables) · `doc/sdlc/defects/DEF-001-mcp-retrieval-timeout.md` ·
`doc/sdlc/DEMO-PARK-2026-09-20.md` (why the runs stopped).

> **Evidence grades are kept.** `[Measured]` = a run or probe on this box. `[Estimated]`/`[Predicted]`
> = arithmetic, not observation. The two are never mixed. Where a severity exists in a source doc it
> is quoted; where none exists this file says so rather than inventing one.

---

## 1. The one-screen answer

| | |
|---|---|
| **The goal** | `BRD-05`: **zero** turns over 3,000 ms at N=2. `BRD-02`: p50 ≤ 700 / p95 ≤ 1,200 ms |
| **Is it met?** | **No.** Best ever measured: **8 breaches in 120 turns (6.7%)** against a criterion of zero `[Measured]` |
| **Is `BRD-02` reachable?** | **Not on this hardware.** The program's own clause allows stating the tightest defensible SLO instead; ~2,300 ms was proposed, **retracted as optimistic by ~650 ms**, and **still unapproved** `[Measured p50 2,953.5 ms]` |
| **What was won** | p50 **5,141 → 2,063 ms** live, over-cap **92.4% → 15.2%**, across 3,626 real turns `[Measured]` |
| **What is left** | **~44% of the remaining tail is one deferred defect. ~56% is not yet explained.** Plus a measurement environment and two PO decisions |
| **The honest reason it is not done** | **Not a shortage of optimizations.** The measured wins are large and landed. What remains is a *quiet box*, a *PO decision*, and *one defect* — see §5 |

---

## 2. What is done

Condensed from `PERFORMANCE_REPORT_2026-09-21.md` §2, which carries the before/after tables and
sources. These are the large, evidenced wins, in order of effect:

| # | Win | Effect | Grade |
|---|---|---|---|
| 1 | `num_ctx` thrashing eliminated (one context size everywhere) | warm-turn first audio **8,609 → 360 ms**; `load_ms` 6,133 → **4.7 ms** | Measured |
| 2 | Kokoro TTS moved CPU → CUDA (a CPU `onnxruntime` wheel was shadowing the GPU build) | RTF 0.63 → **0.061**; N=2 TTS term 12,125 → **797 ms** | Measured |
| 3 | `localhost` → `127.0.0.1` on the Ollama URL (IPv6 `::1` stall) | **~2 s/turn**, lossless; `llm_queue_ms` p50 2,521 → **91**; the run stopped dying | Measured |
| 4 | MCP read timeout retuned 2.5 → 6.0 s | p95 **7,850 → 5,538 ms** | Measured |
| 5 | Model `qwen2.5:14b` → `llama3.2:3b` | p50 **−34%**, over-cap 74% → 48%; prefill 4.1×, generation 5.4× | Measured, **SCREEN-GRADE** |
| 6 | Streaming LLM (US-004) + streaming TTS (US-005) | over-cap **27% → 6%** | Measured, **both default-OFF** |
| 7 | Event-loop blocking removed (7 blocking calls, incl. 4 unconditional DB polls) | `/health` max **2,300 → 23 ms** | Measured |
| 8 | US-007 pre-warm uses the real voice prompt, not `"ping"` | prefill **2,001 → 29 ms** | Measured |
| 9 | US-008 retrieval de-serialised (`asyncio.to_thread`) + shared `httpx.Client` | retrieval leg p50 **812 → 531 ms** | Measured |
| 10 | A measurement error corrected — `retrieval_ms` was derived, not measured | read **5,432 ms where truth was 2,227**; the wrong number had been steering decisions | Measured |
| 11 | Process hygiene — orphaned Ollama runners reaped | RAM **97% → 31%**, 25.7 GB reclaimed; **voided an hour of prior measurements** | Measured |
| 12 | 2026-09-21: retrieval circuit breaker actually wired; config provenance made real | outage cost now bounded per window, not per request | Measured |

**Two of these are lessons, not just wins:**

- **#10 and #11 are the same lesson twice.** A derived metric read 2.4× high, and a machine thrashing
  its page file was being read as engine queueing — *"`NUM_PARALLEL` was never the lever; I was
  measuring a machine thrashing its page file and calling it engine queueing."* Both are
  **misclassification under a broken measurement**, and both steered real decisions before they were
  caught. §3.3 says why that matters for what is left.
- **#5's attribution is not separable** — model, `num_predict`, prompt scoping and the event-loop
  fixes landed in one restart. The STT and retrieval gains cannot be split between them `[stated in-source]`.

---

## 3. What is pending — ranked by what actually moves latency

Ranked by **impact ÷ (effort × risk)**, which is also the order to work them. Tier 1 is what to do
first.

### Tier 1 — the measurement that unblocks everything else

**R1. Decompose the 86 unexplained cap breaches. `[Effort: hours · Risk: none · Class A]`**

The tail has been split once and only once (*`doc/sdlc/unblock-plan.md:28-29`*): of **154** breaches
in a 3-hour live sample, **68 are the `DEF-001` timeout** (retrieval 5.8–7.2 s) and **86 are not**.
The non-timeout group has a **healthy retrieval p50 of 766 ms** — so the majority of the tail is
**not** the defect everyone is focused on, and **nobody has said what it is.**

**This is the highest-value item on the list**, for one reason: every optimization below is a guess
until this is answered, and this box has already been burned twice by optimising against a
misread bottleneck (§2, #10 and #11). It is pure measurement — it changes no behaviour and cannot
regress quality.

*How to start:* the trace already carries per-turn marks. Bucket the 86 by which segment is over its
own p99, not by end-to-end time; the segment that dominates names the class. Candidate classes, in
the order this stack's history suggests:

| Class | Mechanism | Why it is a live candidate here |
|---|---|---|
| **Memory-capacity-bound** | Page-file thrashing reads as queueing | **Already happened once** (§2 #11) and was misdiagnosed for an hour |
| **Queueing-bound** | N=2 serialising in the single Ollama instance | `OLLAMA_NUM_PARALLEL=2` was rejected — on a measurement since invalidated (§3.3 R4) |
| **Scheduler/CPU-bound** | Whisper + Kokoro in-process competing with the API process | Both are held in-process by design; the GIL and CPU are shared |
| **Config-bound** | Prefix cache invalidated per turn → repeated prefill | A probe exists and has not been run against the current stack (`a4_prefix_cache_probe.py`) |

### Tier 2 — the largest single known lever

**R2. `DEF-001` — MCP retrieval intermittently hangs for the full read timeout.**
**Severity: "Critical" — the only explicit severity in the whole doc set.** *"The single largest
contributor to `BRD-05` failures and it is invisible in the logs."* Status **DEFERRED** — *"to be
worked after all 18 user stories are complete."* `[Measured: ~6.6 s late; rate 4–8% of turns]`

**Carry its counter-evidence with it — it is on record and it matters:**
> *"Replacing every timeout's retrieval with the median successful retrieval (757 ms) moves the
> harness clock 62 → 53 breaches … **It halves the p95 and leaves the cap unmet.**"*
> (`doc/sdlc/unblock-plan.md:40-51`)

So **R2 alone does not close `BRD-05`** — it is 44% of the tail, not all of it. Two further facts
belong in any decision to pull it forward:

- The deferral is **explicitly conditional**: *"safe to defer **only because C6 holds** … if any
  evidence appears that a fallback ever returns empty or degraded context, this defect stops being
  deferrable."* **Re-test that condition before re-affirming the deferral** — it is a check, not an assumption.
- **Symptom class is latency only; no correctness impact has been observed.** The service answers in
  p50 **788 ms** / p95 **889 ms** when it answers, with **0 of 40** back-to-back probes reaching the
  6.0 s ceiling `[Measured]`. The problem is that it *sometimes does not answer*, which makes this an
  **I/O-bound** wall-clock wait, not a slow-compute problem — so the fix is a **deadline and a
  fallback**, not a faster retrieval.

> **Constraint on the obvious fix — read before shortening the leash.** `unblock-plan.md:52` records
> that **the fallback has its own contention tail**: observed cluster values of **7,484 / 7,656 /
> 8,140 ms**, so S2's claimed "worst case 2.6 s" is really **2.6–4.1 s**. And the mechanism is
> self-reinforcing: *"shortening the leash raises the fallback rate, which raises embed load on the
> same saturated Ollama, which raises the timeout rate."* A **shorter timeout is therefore not the
> fix** — it converts a slow-but-correct answer into a fast-but-also-slow fallback and adds load to
> the thing already struggling. The fix has to be a fallback that does **not** re-enter the same
> saturated Ollama (a pre-warmed or cached retrieval path), or the defect stays where it is.

**Both independent decompositions agree the timeout is the minority cause** `[Measured]`:

| Source | Not-the-timeout share |
|---|---:|
| Architect pass, four runs, harness clock | **78–85%** |
| This plan, current config, app clock, n=1,195 | **56%** |

*"The timeout is ~6% of turns. It occupies the p95 slot — 7–16 timeouts per 100 turns straddle the
95th percentile — but it does not cause most breaches."* **This is the strongest argument for doing
R1 before R2.**

**Quality class: A (lossless)** if the change only decides *when to stop waiting* and returns the
same fallback that exists today. It becomes **Class B** the moment the fallback's *content* changes —
which is what `C6` above is guarding.

### Tier 3 — cheap, and one of them is a decision already made on bad data

**R3. Re-test `OLLAMA_NUM_PARALLEL=2`. `[Effort: one run · Class B — batching changes decode interleaving]`**

It was **rejected**, and the rejection is now **suspect**: it was taken at **RAM 97%**, with
**`num_ctx` thrashing**, and **TTS on CPU** — *all three since fixed* (§2 #1, #2, #11). The source
says plainly: *"the case for a second slot is stronger than when it was rejected."*

If R1 lands on **queueing-bound**, this is the fix, and it is one config value. Class B because
concurrent decode batches requests and batch-size-dependent numerics can change individual tokens —
needs the `DG-03` eval check, which is exactly what R6 is for.

**R3b. The three engine-level knobs are all at stock defaults and none was chosen for this workload.**
`.env` sets **none** of the `OLLAMA_*` engine keys, so Ollama runs on its own defaults — and these are
the highest-leverage untried configuration tier in the workstream, because they cost one restart each
and address the *memory-capacity* and *queueing* classes directly `[Measured: `.env` sets no
`OLLAMA_*` key; defaults confirmed in `.env.example:85-97`]`:

| Key | Live value | Lever | Class |
|---|---|---|---|
| `OLLAMA_FLASH_ATTENTION` | `false` (default) | Faster prefill, **lower KV memory** — directly relevant given §2 #11's page-file thrashing | **B** (attention numerics change) |
| `OLLAMA_KV_CACHE_TYPE` | `f16` (default) | `q8_0` **halves KV memory** at ctx 8192 | **B/C** (quantises the cache; long-context quality risk) |
| `OLLAMA_NUM_PARALLEL` | `1` (default) | One decode slot — at N=2 the two callers **serialise**. This is R3 | **B** (batching) |

**Why this is worth listing separately from R3:** the earlier rejection of `NUM_PARALLEL=2` was made
at **RAM 97% with `num_ctx` thrashing and TTS on CPU**. `FLASH_ATTENTION` and `KV_CACHE_TYPE` attack
**exactly those conditions** — they are the reason the box was thrashing. Testing `NUM_PARALLEL` alone,
on a box whose memory pressure is unchanged, risks reproducing the original negative result for the
original wrong reason. **Change the memory knobs first, then re-test concurrency** — one variable at a
time, per the sequence in §6.

**R4. Promoted from the perf report — `P10` is already closed.** `pick_model()`'s per-utterance
`/api/tags` round trip **no longer exists**: `llm_backend.py:304` resolves the tag list once per
process through `_cached_tags()`, and the two surviving call sites (`list_models()`, `is_ready()`)
are not on the turn path. `PERFORMANCE_REPORT_2026-09-21.md` §4 lists it as *"still open … remains a
real per-turn cost"* — **that line is stale and has been corrected.** Do not spend time on it.

**R5. `.env.example:26` still teaches the `localhost` trap.** One-word fix; worth ~2 s/turn to anyone
who follows the template. `Class A`.

### Tier 4 — blocked on a person, not on code

**R6. `DG-03` — 137 of 161 golden-set ground truths unapproved.** *"Blocks 5 stories and every
behaviour-changing change. **No agent can do this.**"* Explicitly *"the designed outcome, not a
defect."* This is what gates R3 and R7.

**R7. Streaming adoption (US-004 + US-005) — the single largest *measured* cap improvement, switched off.**
**27% → 6% over-cap** is the largest measured move available anywhere in this workstream, and both
flags are `=0` in `.env.example`. Adoption is blocked on `DG-03`, not on engineering.
`Class C` by the program's own ruling — the story records it as *"changes what the caller hears"*.

> **A cheaper move worth considering:** the qualifying runs (R8) could be *measured* with streaming
> ON without *adopting* it. If that configuration reaches **zero** over-cap, the workstream's position
> changes from "cap not met" to "**cap met, pending one PO approval**" — which is a materially
> different thing to take to a decision. Confirm the run is labelled as a candidate configuration,
> not a default, so the number cannot be mistaken for the shipped one.

**R8. The `BRD-02` SLO sign-off — and the number is stale.** The ~2,300 ms proposal was an estimate,
since **retracted**: *"Do not quote the 2,300 ms figure this document previously predicted. The
measured p50 is 2,953.5 ms; the arithmetic was optimistic by ~650 ms."* Needs a PO ruling, not
another optimization pass.

### Tier 5 — blocked on a quiet box (measurement conditions, not code)

**R9. The qualifying runs — `Phase 2.3`.** Bar: **two consecutive ≥100-turn warm N=2 runs with zero
over-cap.** Blocked because *"the box was not quiet (a live WhatsApp demo with tunnels up, and the
app, MCP, Ollama, CRM and both Streamlit UIs running)"*. Also **needs re-planning**: the stack moved
to the `meridian_kb__v1` collection and the C3/C5 RAG work landed, *"so the retrieval leg those runs
would measure is not the one the predictions were made against."*

**R10. `harness_fault: true` on every N=2 run.** *"A run with `harness_fault: false` **and** `n > 0`
has never happened."* Until one does, **every** N=2 absolute number carries that caveat by rule.

**R11. The per-story load/soak gates — none has been run as specified.** US-001's 30-min soak, US-006
TAC-1/2/5, US-008's three N=2 runs, US-012's ≥100 turns ×3, US-013's outage run, US-016's N=3 window,
US-017's 2+1, US-011's TAC-8 pair. *Estimated ~1 day plus a quiet box.* **US-013's are now measurable
for the first time** — until 2026-09-21 the circuit gated nothing, so an outage run would have
measured the absence of a mechanism.

### Tier 6 — real, lower immediate impact

| # | Item | Consequence |
|---|---|---|
| R12 | `Twilio RTT` unmeasured — *"the one hop the local harness cannot see"*, assumed 150 ms | *"If RTT is materially higher, the p95 target moves and no amount of local work recovers it."* **Every harness figure excludes this hop** |
| R13 | US-002 TAC-1 framing FAILS — Windows `time.monotonic()` 15/16 ms granularity | *"the harness is **not** a faithful carrier emulation."* Does not invalidate latency measurement |
| R14 | `REC-13` `memory_budget.py` threshold 95% sits above `BRD-11`'s 90% ceiling | *"a real defect, guarding a ceiling that is not currently near"* |
| R15 | `BRD-12` RAM not sampled *during* a run | It was breached at 97% unnoticed once already |
| R16 | Call arrival rate / peak concurrency unmeasured (`AS-01`, `AS-06`) | Peak concurrency is an assumption — and **N=2 is the only concurrency anything has been measured at** |
| R17 | US-008 has **no acceptance test** (DoD 1/7) despite carrying §2 #9's wins | The retrieval gains rest on a mean later shown to be derived (see §2 #10) |

---

## 4. If you only do one thing

**Do R1.** Bucket the 86 non-timeout breaches by segment.

It is hours of work, carries no risk, changes no behaviour, and it is the difference between the next
optimization being *targeted* and being *a guess*. The two most expensive mistakes in this
workstream's history — a derived metric read 2.4× high, and page-file thrashing read as engine
queueing — were both **misclassification**, not bad engineering. Both were found by going back to
the measurement, not by trying another lever.

The second thing to do is **R9's precondition**: get a quiet box. Until `harness_fault: false` has
happened once, no result from this workstream can be certified — which is why four days of work has
produced large measured wins and **no qualifying run**.

---

## 5. Why this is not finished

Stated plainly, because the question deserves a direct answer.

1. **The remaining work is mostly not optimization.** The large levers were found and landed (§2).
   What is left is: **one defect (R2)**, **a measurement environment (R9, R10)**, and **two PO
   decisions (R6/R8)**. None of those is "try harder at tuning".
2. **The cap is a criterion of zero.** 6.7% over-cap is a good result and a failing one at the same
   time. `BRD-05` admits no breaches, so partial progress cannot be banked.
3. **`BRD-02` is not reachable on this hardware** by the program's own analysis, and the fallback
   SLO that would replace it has been proposed, retracted as optimistic, and not re-issued.
4. **Nothing can be certified yet.** Run store: **73 artifacts, 71 discarded**; the 2 survivors are
   single-turn cold runs; `harness_fault: true` on every N=2 run. The measurements that exist are
   real and were expensive, but **not one of them is a qualifying run.**
5. **The tail is more than half unexplained** (R1) — which is the actual technical blocker, and the
   reason R1 is first.

---

## 6. How to restart — concrete first moves

```bash
cd /d/project/universityDemo

# 1. Is the tree healthy? One command, one scoreboard (Phase 6, added 2026-09-21).
.venv/Scripts/python.exe doc/perf/tools/run_all_gates.py

# 2. R3b -- confirm the engine knobs are still at stock defaults:
grep -nE "OLLAMA_(NUM_PARALLEL|FLASH_ATTENTION|KV_CACHE_TYPE)" .env || echo "all defaults"

# 3. R1 -- decompose the tail. The per-turn marks live here:
#    logs/perf_turns.jsonl  (3,626 turns). Bucket by SEGMENT over its own p99.

# 4. R4/R5 -- verify the closed items stay closed:
grep -n "_cached_tags\|api/tags" app/llm_backend.py

# 5. R9 -- before any run, confirm the box is quiet. This stack is RAM/VRAM
#    heavy by design (whisper + Kokoro held in-process).
```

**Experiment order, one variable at a time.** The box has already produced one negative result that
was really a memory artifact (R3b), so the sequence matters:

1. **Measure only** (R1) — no config change. Establishes which class the 86 breaches belong to.
2. **Memory knobs** (R3b) — `FLASH_ATTENTION=true`, then `KV_CACHE_TYPE=q8_0`. Independent variables;
   measure each.
3. **Concurrency** (R3) — `NUM_PARALLEL=2`, now on a box whose memory pressure has actually changed.
4. **Candidate adoption** (R7) — streaming on, measured, *labelled as a candidate*, not a default.

Each step: same fixture sha256, same network profile, same thermal bucket, or the numbers cannot be
pooled (§8.4).

**Before starting a run, re-read** `DEMO-PARK-2026-09-20.md:79` — the reason the last attempt was
abandoned — and check the collection is `meridian_kb__v1` (R9's re-planning note).

**Do not restart the stack casually.** Memory on this box is short: the previous session's FastAPI
instance was killed by Claude Code's memory-pressure reaper while idle, and a full-board run was
killed the same way on 2026-09-21. Bring the stack up deliberately.

---

## 7. Quality guardrails — what must not be traded silently

Every recommendation above carries a class. None is Class A-except-one: **R1 is the only change with
no quality surface at all**, which is part of why it goes first.

- **Class A** (lossless): R1 (measurement), R4, R5.
- **Class B** (near-lossless, needs an eval check): R2's fallback-content variant, R3.
- **Class C** (behaviour-changing, needs sign-off): R7 (streaming adoption — the program's own ruling).

**The gate that makes Class B/C admissible is `DG-03` + the US-003 eval runner.** It is not built
yet: **137 of 161 ground truths are unapproved**, and `eval/runner.py` (with `RubricJudge`,
`RunReport`, `.eval_in_progress`) is specified but not written. **Until it exists, no Class B or C
change can be adopted** — which is a second, independent reason the workstream is stuck on a person
rather than on code.

---

## 8. Limits of the evidence — carry these with any number you quote

1. **The run store is 97% discarded** (73 → 71). No powered, harness-clean run exists.
2. **`harness_fault: true` on every N=2 run** (R10) — absolute N=2 figures carry that caveat by rule.
3. **The carrier hop is excluded from every harness figure.** Fixtures are **synthetic and not
   intelligible speech** — timing is representative, content is not.
4. **Do not pool across conditions** — network profile, thermal bucket and fixture sha256 all bind.
5. **Some earlier numbers are void** — one hour voided by the orphan-runner discovery (§2 #11); the
   4,125 ms and 1,999 ms p50 figures withdrawn as small-n noise; the `BRD-02` estimate retracted.
6. **Attribution is not separable** for §2 #5 (one restart, four changes).
7. **`SCREEN-GRADE` is not adoption-grade** — the model decision and both streaming flags carry no PO approval.

---

*This report supersedes nothing; `PERFORMANCE_REPORT_2026-09-21.md` remains the detailed evidence
record. This file is the resume point. Where the two disagree, this one carries the correction and
names it — the `P10` correction in §3 R4 is the first such case, made because a resume point that
sends the next session after a closed item is worse than no resume point.*
