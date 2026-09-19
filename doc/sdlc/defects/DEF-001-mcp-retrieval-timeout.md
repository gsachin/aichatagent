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
`last_error`, `breaker_opens` and `coverage`, but **is not exposed on any
endpoint** — `/api/perf/policy` does not surface it. An operator cannot see this
happening, which is why it survived to production-shaped load.

**C5 — The fallback is ~10× faster than the timeout it waits for.** Local Chroma
answers in ~600 ms and succeeded on **every** failure. The system waits six
seconds to fall back to something that answers in a tenth of that.

**C6 — Grounding is preserved, which is why it went unnoticed.** All 300 calls
returned non-empty context (`context_chars` never 0). Answers stayed grounded
and correct; only the clock suffered. A latency defect that produces correct
answers is exactly the kind that hides.

---

## Evidence

Measured on this box, N=2 warm, 100 turns × 2 sessions, 2026-09-19:

| run | timeouts / 200 | retrieval values on failing turns |
|---|---:|---|
| 20:36:10 | 14 | 6,515 6,531 6,547 6,562 6,562 6,578 6,578 6,641 6,672 6,672 6,672 6,687 (+2,625, 8,140) |
| 20:49:43 | 16 | 6,547 6,547 6,547 6,563 6,593 6,609 6,609 6,610 6,610 6,640 6,656 6,672 6,672 6,703 6,735 (+7,656) |
| 21:24:10 | 10 | 6,531 6,547 6,562 6,562 6,578 6,625 6,664 6,688 6,719 (+7,484) |

N=1 warm, 100 turns: **≤4 failures in 99 turns (<4%)**, p95 3,516.6 ms — the p95
sitting below the timeout constant proves the failure count stayed under 5%.

Stage decomposition of failing turns, both sessions, two runs — every other
stage is flat or *faster*:

| stage | failing turns | normal turns | delta |
|---|---:|---:|---:|
| retrieval | ~6,600 ms | ~740 ms | **+5,880** |
| queue | 39 | 54 | −16 |
| prefill | 208 | 183 | +25 |
| generation | 313 | 337 | −23 |
| TTS synthesis | 907 | 625 | +282 |

**Rate rises modestly with concurrency** — <4% at N=1, 5–8% at N=2. At these
counts that is noisy, and it is a contributor at most, not the cause.

**Reproduce:** run the N=2 harness, then plot
`seg_llm_sent__retrieval_done_ms` from `logs/perf_turns.jsonl`. The signature is
a bimodal split — mass at ~740 ms, mass at ~6,600 ms, an empty gap between.

```bash
.venv/Scripts/python.exe doc/perf/tools/load_harness.py --n 2 --turns 100 --json
```

---

## Established vs hypothesis

The distinction is load-bearing here: the deferral is only defensible if what is
being deferred is clearly scoped.

**Established by measurement** — C1, C2, C3, C4, C5, C6 above. The cluster
value, the ceiling arithmetic, the bimodality, the rates, the preserved
grounding and the silence are all observed, not inferred.

**Hypothesis — NOT verified:**
- **H1.** The hang is in *session or transport handling* (e.g. a request sent on
  a session the server no longer holds receives neither a response nor a clean
  close, so only the read timeout ends the wait). Consistent with bimodality and
  with the MCP service answering a bare request in 10 ms, but **untested**.
- **H2.** Why concurrency raises the rate. Unverified; the rate difference is
  within noise at current counts.
- **H3.** Whether the 6.0 s ceiling is ever reached by a *legitimately* slow but
  successful query. If it is, shortening the leash converts good answers into
  fallbacks. **Unknown — the MCP service's own latency distribution has not been
  measured.**

H1 and H3 are the two questions that must be answered before the fix is chosen.

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
**One setting, no code change.** Gated on H3: if genuinely-slow-but-successful
MCP queries exist above 2 s, this trades correctness-preserving latency for
fallback load, and the right ceiling is set from that distribution.

**S3 — Observability, independently worth doing.** Surface `mcp_rag_status()` on
`/api/perf/policy` and log a warning on every fallback. C4 is the reason this
defect was invisible; that is a defect in its own right regardless of S1/S2.

**Note on ordering:** S3 should land first, because it is cheap, it is
independently correct, and it makes S1 and S2 measurable. S1 and S2 both want
the same missing input — the MCP service's own latency distribution.

---

## Why this is deferred, and what would un-defer it

Deferred as instructed: to be addressed after all 18 user stories are complete.

It is safe to defer **only because C6 holds** — the failure preserves grounding
and correctness, so no caller receives a wrong answer. **If any evidence appears
that a fallback ever returns empty or degraded context, this defect stops being
deferrable** and becomes an active correctness issue. That condition should be
checked whenever the golden set is next re-run.

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
