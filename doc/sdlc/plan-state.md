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
AS-01…08 · BRD-01…21 · UC-01…10 · WF-01…03 · DAT-01…14 · DG-01…06 · SM-01…03 · CV-01…04 · MOD-01…07 · REC-01…13 · TRD-01…26 · US-001…US-018

## Lifecycle status

The stage table above tracks **artifact production**. It says `done` for every stage, which is true of the *documents* and false of the *system*. Those are different things, so they are tracked separately here.

**Vocabulary:** `NOT STARTED` · `PLANNED` · `IN PROGRESS` · `BLOCKED` · `COMPLETE` · `ACCEPTED`

| Workstream | Status | Blocked by / next |
|---|---|---|
| Planning (artifacts) | **COMPLETE** | — |
| Implementation | **NOT STARTED** | Nothing written; no branch beyond `perf/phase-a` |
| Instrumentation (`US-001`, `US-007`) | **PLANNED** | First build item; `REC-12` requires re-basing the existing draft |
| Load harness (`US-002`) | **PLANNED** | Add 2-voice + 1-chat per `BRD-20` |
| Golden set (`US-003`) | **BUILT — AWAITING SIGN-OFF** | `eval/golden_set.jsonl`: 161 cases, 28/28 intents, 24 verified mechanically, **137 PENDING_PO_SIGNOFF**, **0 approved**. Built and self-tested (`eval/checks.py`, exit 0); it cannot freeze until a human approves the 12 critical-intent ground truths |
| Quality gate | **BLOCKED** | All 12 critical intents report BLOCKED — no approved ground truth exists. This is the designed outcome, not a defect |
| Model/runtime selection (`US-015`) | **PARTIAL — latency leg measured** | **Result: two plan assumptions falsified.** Per-stream decode does *not* halve at N=2 (39.2 → 38.6 tok/s) and VRAM peaks at 69%, not 90%. Quality screen still blocked by `DG-03`. See `doc/perf/us015-benchmark-results.md` |
| RAG optimization (`BRD-21` / `RAG-Optimized`) | **BLOCKED** | Quality gate; retrieval-only metrics can start now |
| RAG baseline characterisation (`BRD-21` / `US-018`) | **PLANNED** | Owner is `US-018`; needs the trace's retrieval mark (`US-001`/`US-007`) before retrieval latency is reportable, and store consolidation (`US-010`) before a baseline number stops being a property of the route (see `08-coverage-verification.md` §2) |
| Concurrency work (`US-008`, `US-012`, `US-016`) | **PLANNED** | Needs the harness to verify |
| Config truth (`US-011`) | **PLANNED** | Class A/B, low risk, could run early |
| Interruption decision (`US-014`) | **BLOCKED** | Class C — PO sign-off required |
| Production validation | **NOT STARTED** | Requires implementation |
| Production acceptance | **NOT STARTED** | — |

**Summary line:** `PLANNING_COMPLETE · IMPLEMENTATION_NOT_STARTED · QUALITY_GATE_BLOCKED (DG-03) · PRODUCTION_NOT_ACCEPTED`

## Next action
Implementation has not begun. The unblocked first move is `US-011` (config truth, low risk) and the retrieval-only half of `BRD-21`/`US-015`, neither of which needs the golden set. Everything quality-gated waits on `DG-03`: six critical-intent ground truths, PO-approved. `US-014` additionally needs PO sign-off as a Class C change.
