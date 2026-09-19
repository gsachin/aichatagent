> **Last updated:** 2026-09-18 · **Engagement:** Brownfield — `D:\project\universityDemo` (Twilio + faster-whisper + Ollama + RAG + Kokoro, 8 services, one Windows box)

# Plan State — Admissions Voice Assistant Performance & Concurrency Program

## Stage status
| Stage | Status | Evidence |
|---|---|---|
| 0 Engagement | done | Stage 0 block in 00-product-intent.md |
| 1 Intent | done | 00-product-intent.md — AS-01…08 |
| 2 Requirements | done | 01-brd.md — BRD-01…21, 13/13 Discovery Matrix |
| 3 Use cases | done | 02-use-cases-workflows.md — UC-01…10, WF-01…03 |
| 4 Data & state | done | 03-data-state-analysis.md — DAT-01…14, DG-01…06, SM-01…03 |
| 5 Coverage gate | done | 04-coverage-gap-analysis.md — STATUS: COMPLETE, 3 iterations |
| 6 Modularization | done | 05-modularization.md — MOD-01…07 |
| 7 Architecture | done | 06-architecture.md |
| 7b Reconciliation | done | 07-brownfield-reconciliation.md — REC-01…13 |
| 8 Module docs | done | modules/ — MOD-01…07, TRD-01…26 |
| 9 Stories | done | stories/ — US-001…US-018 (US-017 background-load priority, `BRD-20`; US-018 RAG-Baseline/RAG-Optimized, `BRD-21`) |
| 10 Mapping | done | 08-coverage-verification.md |
| 11 Verification | done | 08-coverage-verification.md — validator run |

## ID registry
AS-01…08 · BRD-01…21 · UC-01…10 · WF-01…03 · DAT-01…14 · DG-01…06 · SM-01…03 · CV-01…04 · MOD-01…07 · REC-01…13 · TRD-01…26 · US-001…US-018 · DEF-001

## Defects — deferred past story completion

| ID | Severity | Component | One line | Status |
|---|---|---|---|---|
| `DEF-001` | Critical | `MOD-02` retrieval (`app/rag_mcp.py`) | The MCP primary retrieval intermittently hangs for the full 6.0 s read ceiling, then falls back to local Chroma in 600 ms — costing ~6.6 s on ~5–8% of turns. Latency only; grounding is preserved. Silent in the logs. | **DEFERRED** to after all 18 stories |

`DEF-001` carries its own evidence, so it does not need re-deriving. It is safe
to defer **only** while its finding C6 holds — that grounding is preserved. If a
fallback is ever observed returning empty or degraded context, it stops being
deferrable.

## Story completion — 18 stories, counted 2026-09-19

Every story file now carries a `**Status:**` line and a ticked Definition of Done. Two counts, because they differ and conflating them is how a program reports progress it does not have.

| | Count | Means |
|---|---|---|
| **Written** | **18 / 18** | The planning artifact exists, is self-contained, and validates (`752/0`) |
| **Implemented, ACs verified** | **8 / 18** | A named acceptance suite passes: US-001 (17/17), US-006 (11/11), US-007 (30/30), US-011 (17/17), US-012 (36/36), US-013 (39/39), US-016 (56/56), US-017 (48/48) |
| **Partial, no acceptance suite** | **1 / 18** | US-008 — its `app/`-side change (the shared `httpx.Client`) was **deliberately reverted** pending load evidence, so the app side is already rolled back; the ERC side is in a separate repository |
| **Fully Definition-of-Done complete** | **0 / 18** | **No story has every DoD box ticked.** Best is US-007 at 6/8 |
| **DoD boxes ticked, all 18 stories** | **33 / 147** | Counted mechanically from the checklists, not estimated. Was 14/147 before the Task 3.1 pass, 30/147 before the 2026-09-19 module reconciliation |

**Recount as of 2026-09-19** (mechanical, `\[( |x)\]` over each story's DoD
section): US-001 4/6 · US-002 0/7 · US-003 0/7 · US-004 0/7 · US-005 0/7 ·
US-006 5/8 · US-007 6/8 · US-008 1/7 · US-009 0/7 · US-010 0/8 · US-011 4/8 ·
US-012 4/8 · US-013 2/8 · US-014 0/9 · US-015 0/12 · US-016 3/10 · US-017 4/10 ·
US-018 0/10.

**The four recurring blockers**, unchanged and none of them a coding task:

1. **LLD test mapping (T-1…Tn)** — unmet in every implemented story. The suites
   pass, but they are not tagged to the story's own LLD scenario IDs, so a
   reviewer cannot see T-4 → which test.
2. **The story-specific load/soak gates** — `US-001`'s 30-minute N=2 soak,
   `US-006`'s TAC-1/TAC-2/TAC-5, `US-008`'s three consecutive N=2 runs. None has
   been run as specified. Note the ≥100-sample baseline now exists but every
   N=2 figure carries the `harness_fault` caveat (`doc/perf/runs/`).
3. **`BRD-15` rollback** — demonstrated for six stories by
   `test_brd15_rollback.py` (33/33); still *asserted* rather than demonstrated
   for the rest.
4. **Module-doc reconciliation** — `MOD-01`, `MOD-03`, `MOD-06`, `MOD-07` and
   the `US-015` benchmark reconciled 2026-09-19. **`MOD-04` remains** (US-012's
   TTS cache scope and voice/speed key), and it is the last one.

### What the Task 3.1 pass closed, and what it found

Four stories implemented (US-012, US-013, US-016, US-017) and the DoD gap worked
on for nine. The pass found **six defects**, none of which the ACs would have
caught — every one came from a scenario or a checklist that asks a question the
acceptance criteria do not:

| Found | By |
|---|---|
| The admission record stored the caller's **phone number** (`call_sid` is the carrier's `From`) | US-016 LLD T-7 |
| On defer-timeout the work gate **started the unit anyway**, making US-017's TAC-2 invariant merely advisory | US-017 LLD T-9 |
| `/ws/voice/text` called the model **directly**, bypassing the gate — the one route into inference the priority policy could not see | driving the 2+1 window |
| The TTS cache key omitted **voice and speed**, so editing either would have kept serving audio in the old voice | US-012, while making them configurable |
| The isolation test reported **PASS on zero keys** — a green tick from no data | running it |
| The cache key was recorded only on the **hit** path, so no key could be attributed to its owner | same run |

The pattern is the program's own: **the instrument lied more often than the
system did.** Four of the six were in the code written to *measure* the thing,
not in the thing itself.

### `BRD-15` — demonstrated, not asserted

`doc/perf/tools/test_brd15_rollback.py` (33/33) reverts six stories and watches
the prior behaviour return: US-012, US-013, US-016, US-017, US-006, US-011. The
program's audit had found the same gap in every story — each *asserted* its
change was revertible and none had been demonstrated by reverting it.

**Two entries could not be covered and are recorded as such rather than
implied:** US-007's gate is a module plus a launcher call, so its revert is
commit-level; US-008's change spans two repositories. And **US-006's DoD named
the wrong revert** — it said *removing* the setting restores the defect, but the
code default is `-1`, so unsetting the key keeps the fix. The revert that works
is to set the pre-fix value, which is what the demonstration does.

| Status | n | Stories |
|---|---|---|
| IMPLEMENTED (ACs verified) | 8 | US-001, US-006, US-007, US-011, US-012, US-013, US-016, US-017 |
| IMPLEMENTED (no test) | 1 | US-008 |
| IN PROGRESS | 1 | US-002 |
| AWAITING SIGN-OFF | 2 | US-003, US-014 |
| PARTIAL | 1 | US-015 |
| BLOCKED (`DG-03`) | 5 | US-004, US-005, US-009, US-010, US-018 |
| NOT STARTED | 0 | — *(this table was the pre-Task-3.1 count and contradicted the summary above it; corrected 2026-09-19)* |

### What actually stands between "implemented" and "done"

The same four items recur across the implemented stories, and none of them is a coding task:

1. **LLD test mapping (T-1…Tn) — unmet in all five.** The suites pass, but they are not tagged to the story's own LLD scenario IDs. `US-001` is done (2026-09-19) and the pass found the tags were not merely missing but **wrong** — nine checks wore a `T-n` belonging to a different scenario, and `T-10`/`T-11` read as covered while nothing tests them. US-002/003/006/007 remain unmapped.
2. **The load/soak tests.** `US-001`'s 30-minute N=2 soak, `US-006`'s TAC-1/TAC-2/TAC-5 thresholds, `US-008`'s three consecutive N=2 runs. The ≥100-sample baseline now exists (p50 3,609 / p95 6,044 ms) but none of these story-specific gates has been run against it.
3. **`BRD-15` rollback demonstration — unmet in all five.** Every story asserts its change is revertible; none has been demonstrated *by reverting it*.
4. **Module-doc reconciliation.** The module docs are the *design*, not the as-built record. Concretely: `MOD-06:101` still documents the **pre-fix** stage set (it describes "no retrieval mark" as the gap US-001 closed), and `MOD-01` contains no `perf_trace` emitting call sites at all.

**`US-008` is the outlier and should be treated as such.** It is `IMPLEMENTED` in the status file on the strength of a measurement, with no acceptance suite — a weaker evidentiary basis than the four stories beside it. See its DoD note.

## Lifecycle status

The stage table above tracks **artifact production**. It says `done` for every stage, which is true of the *documents* and false of the *system*. Those are different things, so they are tracked separately here.

**Vocabulary:** `NOT STARTED` · `PLANNED` · `IN PROGRESS` · `BLOCKED` · `COMPLETE` · `ACCEPTED`

| Workstream | Status | Blocked by / next |
|---|---|---|
| Planning (artifacts) | **COMPLETE** | — |
| Implementation | **IN PROGRESS** | Branch `perf/phase-a`, commit `849d94f` (local, unpushed). **4 stories DONE**, 1 in progress |
| Instrumentation (`US-001`, `US-007`) | `US-001` **DONE** (`test_us001_tracer.py` 17/17, proven on a live call) · `US-007` **DONE** (`test_us007_readiness.py` 30/30; live gate 3/4 clauses) | `US-007` replaced the `"ping"` pre-warm with the real voice prompt and added the four-clause readiness gate. **The harness's `readiness: assumed` can now be replaced by the gate's verdict** |
| Load harness (`US-002`) | **IN PROGRESS** | Drives live `/ws/twilio`; two gaps open. **First ≥100-sample run completed 2026-09-19** (110 turns, 108 warm samples, zero drops) |
| Golden set (`US-003`) | **BUILT — AWAITING SIGN-OFF** | `eval/golden_set.jsonl`: 161 cases, 28/28 intents, 24 verified mechanically, **137 PENDING_PO_SIGNOFF**, **0 approved**. Built and self-tested (`eval/checks.py`, exit 0); it cannot freeze until a human approves the 12 critical-intent ground truths |
| Quality gate | **BLOCKED** | All 12 critical intents report BLOCKED — no approved ground truth exists. This is the designed outcome, not a defect |
| Model/runtime selection (`US-015`) | **PARTIAL — latency leg measured** | **Result: two plan assumptions falsified.** Per-stream decode does *not* halve at N=2 (39.2 → 38.6 tok/s) and VRAM peaks at 69%, not 90%. Quality screen still blocked by `DG-03`. See `doc/perf/us015-benchmark-results.md` |
| RAG optimization (`BRD-21` / `RAG-Optimized`) | **BLOCKED** | Quality gate; retrieval-only metrics can start now |
| RAG baseline characterisation (`BRD-21` / `US-018`) | **PLANNED** | Owner is `US-018`; needs the trace's retrieval mark (`US-001`/`US-007`) before retrieval latency is reportable, and store consolidation (`US-010`) before a baseline number stops being a property of the route (see `08-coverage-verification.md` §2) |
| Concurrency work (`US-008`, `US-012`, `US-016`) | `US-008` **DONE** · `US-012`, `US-016` **NOT STARTED** | `US-008`: both ERC legs moved off the event loop (`asyncio.to_thread`) |
| Config truth (`US-011`) | **DONE** | `test_us011_config_truth.py` 16/16; found 3 inert keys the plan did not know about (`KOKORO_SPEED`, `LOG_FILE`, `LOG_LEVEL`) |
| Model residency (`US-006`) | **DONE** | `test_us006_residency.py` 11/11; `ollama ps` reports `Forever` |
| Baseline measurement (`BRD-02`) | **ESTABLISHED — CAP NOT MET** | 2026-09-19, N=2 warm: **p50 3,609 ms · p95 6,044 ms · n=108**, 0 timeouts, 0 drops. **96 of 108 turns exceed the 3,000 ms cap.** The run is still `DISCARDED` by the harness's own turn-cap gate — the number is reportable, the run is not a pass |
| Interruption decision (`US-014`) | **BLOCKED** | Class C — PO sign-off required |
| Production validation | **NOT STARTED** | Requires implementation |
| Production acceptance | **NOT STARTED** | — |

**Summary line:** `PLANNING_COMPLETE · IMPLEMENTATION_IN_PROGRESS (4/18 DONE) · BASELINE_ESTABLISHED (CAP NOT MET) · QUALITY_GATE_BLOCKED (DG-03) · PRODUCTION_NOT_ACCEPTED`

## Coverage gap — `MOD-05` has no story (ACCEPTED as out of scope, 2026-09-19)

Found 2026-09-19 while confirming story creation. **`MOD-05` (Lead & CRM) owns `UC-04` (escalate to a human), `UC-05` (capture and persist a lead) and `SM-03` (lead lifecycle), and satisfies `BRD-13` and `BRD-18` — and no story implements any of it.** The only mention of `MOD-05` in `stories/` is a passing reference in `US-007`'s body; it appears in no traceability block.

**PO decision 2026-09-19: accepted, out of scope for this program.** Rationale: `06-architecture.md` §60 puts `MOD-05` strictly post-call and off the turn path, and `01-brd.md` §2 excludes new user-facing features and the chat/WhatsApp path — a performance program has no change to make here. Recorded in `08-coverage-verification.md` §"Story-coverage gap". **Re-open if `MOD-05` ever moves onto the turn path**, since that would make it a latency subject.

The gap had been invisible because that table counts `BRD-xx` ownership only: `BRD-13`/`BRD-18` are cited by `US-014`/`US-016` (voice-path stories), so the requirement-level count read as covered while the owning module had no work item.

## Next action
`US-007` (boot warm state) is the unblocked first move — the harness currently reports `readiness: assumed`, so no run can certify the stack was warm. Then re-test `OLLAMA_NUM_PARALLEL=2`, whose earlier rejection predates three fixes. Everything quality-gated waits on `DG-03`: six critical-intent ground truths, PO-approved. `US-014` additionally needs PO sign-off as a Class C change.
