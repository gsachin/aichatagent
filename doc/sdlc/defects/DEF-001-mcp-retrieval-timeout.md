> **Lens:** Architect (root cause) / TPO (severity) · **Engagement:** Brownfield — `D:\project\universityDemo`
> **Raised:** 2026-09-19 · **Raised by:** performance work on `US-015` / `BRD-05`

# DEF-001 — MCP retrieval intermittently hangs for the full read timeout

- **Severity:** **Critical** — it is the single largest contributor to `BRD-05`
  failures and it is invisible in the logs.
- **Status:** **DEFERRED** — to be worked after all 18 user stories are complete.
  This is a deliberate deferral, not a backlog drop: the evidence below is the
  work, and it is recorded now because it was expensive to obtain and would be
  expensive to rediscover.
- **Component:** `MOD-02` (retrieval) — `app/rag_mcp.py` primary path.
- **Symptom class:** latency only. **No correctness impact has been observed.**

---

## Summary

On some turns the primary MCP retrieval **does not return at all**. The client
waits out its full read ceiling, falls back to local Chroma, and the turn
proceeds — roughly **6.6 seconds late**. The caller hears the right answer; they
hear it eight seconds after they stopped speaking.

The failure is **bimodal**. Normal retrieval is ~740 ms. Failing retrieval is
~6,600 ms. **There is nothing in between.**

---

## Critical findings

**C1 — It is a hang, not a slowdown.** The retrieval durations on failing turns
cluster at 6,515–6,735 ms (spread ±1.7%). A retrieval that had merely become
slow under load would spread smoothly from ~750 ms upward. A tight cluster
pinned to a constant means the request never came back and something *ended the
wait* — the read timeout. This is the finding that changes the diagnosis: it
rules out compute contention and points at transport or session handling.

**C2 — The constant decomposes exactly.** `RAG_MCP_TIMEOUT=6.0`, with the
serving path capped at `RAG_RETRIEVAL_BUDGET - RAG_FALLBACK_RESERVE`
(8.0 − 1.5 = 6.5), so the effective read ceiling is **6.0 s**. Measured failure
cluster ≈ **6.0 s primary + ~0.6 s local Chroma fallback**. The arithmetic
accounts for the observed value to within ~10%.

**C3 — It is the dominant cause of the `BRD-05` breach.** A normal turn costs
~2.2 s app-side (~2.8 s first-audio). A timeout turn costs ~8.0 s app-side
(~8.6 s first-audio). The stable 8.1 s p95 observed across three separate N=2
runs — 8,079.5 / 8,235.6 / 8,116.5 ms, stable to ±2% — **is this timeout, not a
property of any session.** It was initially misattributed to one session,
because the session that draws more timeouts is the session whose p95 lands on
the constant.

**C4 — It is silent.** Zero log lines mention a timeout, a breaker opening, or a
fallback, across 300 live calls. `mcp_rag_status()` exists and carries
`last_error`, `breaker_opens` and `session_ok`, but **is not exposed on any
endpoint** — `/api/perf/policy` does not surface it. An operator cannot see this
happening, which is why it survived to production-shaped load.

**C5 — The fallback is ~10× faster than the timeout it waits for.** Local Chroma
answers in ~600 ms and succeeded on **every** failure. The system waits six
seconds to fall back to something that answers in a tenth of that.

**C6 — Grounding is preserved, which is why it went unnoticed.** All 300 calls
returned non-empty context (`context_chars` never 0). Answers stayed grounded
and correct; only the clock suffered. A latency defect that produces correct
answers is exactly the kind that hides.

**C7 — One session's p95 can be an artifact of another's.** The aggregate p95
across two sessions is a **mixture**: with one tight and one timeout-heavy
session, the combined 95th percentile belongs to neither. See the investigation
log, phase 2 — this produced a false confirmation of an unrelated hypothesis and
was caught only by reading the per-session rows.

---

## Investigation log

Recorded in full, including the wrong turns. Four distinct hypotheses were
raised; **three were refuted**. The refutations are the expensive part and are
the reason this document exists.

### Phase 1 — How it surfaced

Origin was `US-015` model-selection work. The model change cut the LLM leg from
2,743 ms to 651 ms, but the N=2 runs stayed **DISCARDED** on `BRD-05`'s
3,000 ms ceiling — 95/198, then 58/198, then 77/198 turns over. The median had
improved; the ceiling was still breached. The question asked was *"what if we
run N=1?"*.

| condition | n | p50 | p95 | worst | over 3,000 ms |
|---|---:|---:|---:|---:|---:|
| N=2 (three runs) | 198 | 2,679.5 / 2,781.0 / 2,672.5 | 8,104.7 / 8,038.0 / 4,355.7 | 14,594 / 10,547 / 9,219 | 58 / 77 / 66 |
| N=1 | 99 | 2,532.0 | **3,516.6** | **4,234** | 28 |

**The first clue was in this table and was missed.** N=1's p95 of 3,516.6 ms
meanwhile N=2's sat at ~8,100 ms. A p95 that lands on a *round constant* in one
condition and far below it in another is a signature, not a coincidence — it
means a fixed cost is firing often enough in one condition to occupy the 95th
percentile, and not in the other.

### Phase 2 — The false lead (REFUTED)

Immediately after the N=1 run the service **hung**, which was diagnosed
separately and correctly: `py-spy` caught the MainThread — the event loop —
inside a synchronous socket read, and four direct calls to the synchronous
Ollama client were found in `async def`s on the post-call path
(`app/database.py`, `app/leads/service.py`). Those were wrapped in
`asyncio.to_thread` and the hygiene guard widened. **That fix was correct and
is independent of this defect.**

It also produced a plausible story for the N=2 tail: the two sessions start
~5–6 s apart but **finish 115–145 s apart**, so the earlier caller's post-call
ran while the other was still taking live turns — and while blocked, it froze
the loop.

**Hypothesis H0: the blocking post-call inflates the other session's tail.**

An N=2 re-run appeared to confirm it: aggregate p95 fell 8,038 → 4,356 ms.

**REFUTED.** The per-session split shows session A's p95 at **8,079.5 / 8,235.6
/ 8,116.5** across the three runs — stable to ±2% and **completely unmoved by
the fix**. The entire apparent improvement came from session B, whose p95 drifted
8,186.7 → 5,307.9 → 3,821.6:

| run | sess | p50 | p95 | worst | over 3,000 |
|---|---|---:|---:|---:|---:|
| 20:36:10 | A | 2,640.0 | 8,079.5 | 14,594 | 26 |
| 20:36:10 | B | 2,734.0 | 8,186.7 | 9,234 | 32 |
| 20:49:43 | A | 2,672.0 | 8,235.6 | 10,547 | 35 |
| 20:49:43 | B | 2,906.0 | 5,307.9 | 8,985 | 42 |
| 21:24:10 | A | 2,547.0 | 8,116.5 | 9,219 | 27 |
| 21:24:10 | B | 2,844.0 | 3,821.6 | 8,719 | 39 |

The aggregate was a **mixture artifact** (C7). With A's p95 ~8,100 and B's
~3,800, the combined 95th percentile lands between them and describes neither.
Had this not been checked, an unrelated fix would have been credited with a
4,000 ms improvement it did not make.

**Secondary hypothesis in the same phase — the harness was to blame.** The runs
report `harness_fault: true` (155 frames late >5 ms, 83 unexplained on the last
run). **Refuted for this defect**, see phase 4.

### Phase 3 — The actual cause

Rather than run again, the existing trace was interrogated: *for each slow turn,
which stage is inflated?* Failing turns were classified as `total_ms > 4000` and
compared against the rest, per session, across two runs:

| stage | failing turns | normal turns | delta |
|---|---:|---:|---:|
| **retrieval** | **~6,600 ms** | ~740 ms | **+5,880** |
| STT | 0 | 0 | 0 |
| queue | 39 | 54 | −16 |
| prefill | 208 | 183 | +25 |
| generation | 313 | 337 | −23 |
| TTS synthesis | 907 | 625 | +282 |

**One stage. In both sessions. In both runs.** Every other stage is flat or
*faster* on the slow turns — which is what a fixed stall looks like: the rest of
the pipeline is unchanged, and one stage absorbs the entire excess.

The distribution of the failing values settled it:

| run | failing retrieval values (ms) |
|---|---|
| 20:36:10 | 6515 6531 6547 6562 6562 6578 6578 6641 6672 6672 6672 6687 (+2625, 8140) |
| 20:49:43 | 6547 6547 6547 6563 6593 6609 6609 6610 6610 6640 6656 6672 6672 6703 6735 (+7656) |
| 21:24:10 | 6531 6547 6562 6562 6578 6625 6641 6688 6719 (+7484) |

**Bimodal, with an empty gap between ~750 ms and ~6,500 ms.** Then the
arithmetic (C2) matched the cluster to the configured ceiling.

### Phase 4 — Ruling out the alternatives

| Candidate | Test | Result |
|---|---|---|
| The MCP service is down or broken | Bare HTTP request to `:8010/mcp` | **Refuted** — responds in **10 ms** (HTTP 400, i.e. alive and rejecting a malformed request) |
| GPU / compute contention | Stage decomposition | **Refuted** — prefill, generation and TTS are flat or *faster* on failing turns; only retrieval moves |
| Session A is a defective session | Cross-run per-session p95 | **Refuted** — the pattern appears in **both** sessions; A merely draws more timeouts |
| The harness's framing fault | Stage decomposition | **Refuted for this defect** — the trace accounts for every millisecond and the inflated stage is app-side. The harness fault is real but separate |
| The bug is logged and we missed it | `grep -icE "timeout|breaker|fallback"` over 300 calls | **Confirmed absent** — 0 matches (this became finding C4) |

### Phase 5 — Corrections made to my own claims

Recorded because each was stated before it was checked:

1. **"Concurrency roughly doubles the timeout rate."** Wrong. N=1's p95 of
   3,516.6 ms proves **at most 4 failures in 99 turns (<4%)**, because a p95
   below the constant cannot coexist with ≥5 timeouts. N=2 is 5–8%. Higher, but
   4% → ~6% is not a doubling, and at these counts it is noisy. Concurrency is a
   contributor **at most**.
2. **"Session A's 8.1 s p95."** A misnomer. It is the *timeout rate* that
   decides whether a session's p95 lands on the constant. A drew 7–12 per 99
   turns and landed on it; B drew 3–6 and sometimes fell below.
3. **An earlier latency prediction of ~2,300 ms**, made from arithmetic and
   corrected in `doc/perf/us015-model-decision.md`, was optimistic by ~650 ms.

---

## Evidence

Measured on this box, N=2 warm, 100 turns × 2 sessions, 2026-09-19. Run
artifacts under `doc/perf/runs/`.

**Rate:** N=1 ≤4/99 (<4%) · N=2 14/200 (7%), 16/200 (8%), 10/200 (5%).

**Per-turn cost:** normal ≈ 2.2 s app-side / ~2.8 s first-audio; failing
≈ 8.0 s app-side / ~8.6 s first-audio.

**Grounding:** `context_chars` never 0 across 300 calls.

---

## Methods

Everything below is reproducible from the artifacts in the repository.

**Signature — the bimodal split:**

```python
import json, glob
d = [json.load(open(p, encoding="utf-8"))
     for p in glob.glob("doc/perf/runs/*<RUNID>*.json")][0]
ids = {sid: s["label"] for s in d["per_session"] for sid in s["stream_sids"]}
rows = [json.loads(l) for l in open("logs/perf_turns.jsonl", encoding="utf-8") if l.strip()]
vals = sorted(r["seg_llm_sent__retrieval_done_ms"] for r in rows
              if r["call_id"] in ids and r.get("seg_llm_sent__retrieval_done_ms"))
# expect: mass at ~740, mass at ~6600, nothing between 750 and 6500
```

**Attribution — which stage absorbs the excess:**

```python
slow = [r for r in rows if (r.get("total_ms") or 0) > 4000]
fast = [r for r in rows if (r.get("total_ms") or 0) <= 4000]
# compare medians of each *_ms stage between slow and fast
```

**The trap — the aggregate p95 hides the per-session split.** Always read
`per_session` before quoting an aggregate p95 from a two-session run.

**Coverage gap in this investigation.** The MCP service's own latency
distribution — never measured. It is the input that decides H3 below.

---

## Established vs hypothesis

The distinction is load-bearing here: the deferral is only defensible if what is
being deferred is clearly scoped.

**Established by measurement** — C1–C7 above. The cluster value, the ceiling
arithmetic, the bimodality, the rates, the preserved grounding, the silence, and
the mixture artifact are all observed, not inferred.

**Hypothesis — NOT verified:**
- **H1.** The hang is in *session or transport handling* (e.g. a request sent on
  a session the server no longer holds receives neither a response nor a clean
  close, so only the read timeout ends the wait). Consistent with bimodality and
  with the service answering a bare request in 10 ms, but **untested**. A
  `session_ok` flag exists in `mcp_rag_status()` and is not exposed — wiring it
  up is the first step to testing this.
- **H2.** Why concurrency raises the rate. Unverified, within noise at current
  counts.
- **H3.** Whether the 6.0 s ceiling is ever reached by a *legitimately* slow but
  successful query. If it is, shortening the leash converts good answers into
  fallbacks. **Unknown — the MCP service's latency distribution has not been
  measured.** This is the single missing input for S2.

H1 and H3 must be answered before a fix is chosen.

---

## Proposed solutions

**S1 — Root cause (preferred).** Diagnose why the primary read never returns.
Instrument the MCP client to distinguish "server returned an error" from "no
response at all" from "session rejected", then reproduce. Requires H1 answered.

**S2 — Mitigation, independent of S1.** Reduce `RAG_MCP_TIMEOUT` from 6.0 to
~2.0. The fallback works in 600 ms, so the current ceiling is six seconds of
waiting to reach a path that answers in a tenth of that. This would bound
worst-case retrieval at ~2.6 s instead of ~6.6 s — turning the 8.1 s p95 into
roughly 4.1 s and pulling most failing turns back under the 3,000 ms ceiling.
**One setting, no code change.** Gated on H3.

**S3 — Observability, independently worth doing.** Surface `mcp_rag_status()` on
`/api/perf/policy` and log a warning on every fallback. C4 is the reason this
defect was invisible; that is a defect in its own right regardless of S1/S2.

**Ordering:** S3 first. It is cheap, independently correct, and it makes S1 and
S2 measurable. S1 and S2 both want the same missing input — the MCP service's
own latency distribution.

---

## Why this is deferred, and what would un-defer it

Deferred as instructed: to be addressed after all 18 user stories are complete.

It is safe to defer **only because C6 holds** — the failure preserves grounding
and correctness, so no caller receives a wrong answer. **If any evidence appears
that a fallback ever returns empty or degraded context, this defect stops being
deferrable** and becomes an active correctness issue. That condition should be
checked whenever the golden set is next re-run.

**Open at time of writing:** a harness-clean N=2 run (`harness_fault: false`) was
in flight to confirm the framing fault does not bias the latency figures
generally. It is not needed to establish anything above — the stage trace is
app-side and independent of harness framing — but it remains the one measurement
this defect does not have.

## References

- `BRD-04` (endpointing) and `BRD-05` (two-caller ceiling) — the requirements
  this defect's impact is measured against.
- `US-013` — bounded dependency calls and the half-open probe; the breaker in
  `app/rag_mcp.py` was built for this class of failure, and C4 shows it is not
  wired to anything an operator can see.
- `US-018` — RAG-Baseline / RAG-Optimized. If the baseline captures retrieval
  latency, the bimodal signature belongs in it.
- `doc/perf/us015-model-decision.md` — the measured chain this defect was found
  underneath, and the stage decomposition that isolated it.
- `doc/perf/tools/load_harness.py` — the rig; `--n 2 --turns 100` reproduces.
