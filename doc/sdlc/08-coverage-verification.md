> **Lens:** all · **Decided by:** BA + TPO + PO + Architect · **Inputs:** the whole artifact tree · **Engagement:** Brownfield

# Coverage Verification — Admissions Voice Assistant Performance & Concurrency Program

## 1. Capability Table

| # | Requested Capability | Addressed In | Owning Lens |
|---|---|---|---|
| 1 | Product Owner lens | `00-product-intent.md` | PO |
| 2 | Business Analysis lens | `01-brd.md`, `02-use-cases-workflows.md` | BA |
| 3 | TPO lens | `05-modularization.md`, `modules/MOD-01-voice-turn-path.md` | TPO |
| 4 | Architect lens | `06-architecture.md`, `07-brownfield-reconciliation.md` | Architect |
| 5 | Intent Descriptions | `00-product-intent.md` | PO |
| 6 | Detailed BRD + Discovery Matrix (13 dimensions) | `01-brd.md` | BA |
| 7 | Use cases & workflows + per-UC scenario coverage | `02-use-cases-workflows.md` | BA |
| 8 | Data & state analysis (inventory, gap decisions, lifecycles) | `03-data-state-analysis.md` | BA + TPO |
| 9 | Scalability & capacity analysis (load model, scale profiles, degradation) | `03-data-state-analysis.md`, `06-architecture.md` | BA + Architect |
| 10 | Coverage & gap analysis with iteration loop | `04-coverage-gap-analysis.md` | BA + TPO + PO |
| 11 | Evidence register & classification | `01-brd.md` | BA |
| 12 | Work modularization | `05-modularization.md` | TPO + Architect |
| 13 | Each module architecture flow chart | `06-architecture.md` | Architect |
| 14 | Overall architecture + workflow/orchestration (best-suited solution) | `06-architecture.md` | Architect + TPO |
| 15 | Each module BRD and TRD | `modules/MOD-01-voice-turn-path.md`, `modules/MOD-02-retrieval-grounding.md`, `modules/MOD-03-inference-serving.md`, `modules/MOD-04-speech-services.md`, `modules/MOD-05-lead-crm.md`, `modules/MOD-06-observability-evaluation.md`, `modules/MOD-07-config-boot.md` | BA / TPO + Architect |
| 16 | User story with HLD & LLD, self-contained | `stories/US-001-turn-tracing-marks-records.md`, `stories/US-002-n1-n2-load-harness.md`, `stories/US-003-frozen-golden-set-eval-runner.md`, `stories/US-004-stream-llm-generation.md`, `stories/US-005-stream-tts-synthesis.md`, `stories/US-006-hold-model-residency-serving-path.md`, `stories/US-007-boot-warm-state-verifiable.md`, `stories/US-008-deserialize-retrieval-concurrency.md`, `stories/US-009-relevance-floor-single-round-trip.md`, `stories/US-010-consolidate-authoritative-store.md`, `stories/US-011-single-config-source-of-truth.md`, `stories/US-012-tts-cache-isolation.md`, `stories/US-013-bounded-dependency-calls-half-open-probe.md`, `stories/US-014-decide-caller-interruption.md` | PO / Architect |
| 17 | Test & validation mapping (story → tests) | `stories/US-001-turn-tracing-marks-records.md`, `stories/US-008-deserialize-retrieval-concurrency.md`, `stories/US-012-tts-cache-isolation.md` | all |
| 18 | Existing/partial codebase context awareness | `07-brownfield-reconciliation.md`, `00-product-intent.md` | TPO + Architect |
| 19 | Semantic scorecard | `08-coverage-verification.md` | all |

## 2. Semantic Scorecard

Percentages are counts taken from the Stage 5 matrices and the Stage 10 mapping — not decorative.

| Category | Covered / Total | % |
|---|---|---|
| Requirements coverage | 19 / 19 `BRD-xx` have UC coverage | 100% |
| Use-case coverage | 10 / 10 UCs at 14/14 scenarios | 100% |
| Workflow coverage | 3 / 3 WFs with failure drill + partial-completion matrix | 100% |
| Actor coverage | 9 / 9 actors have ≥1 UC | 100% |
| Data coverage | 6 / 6 `DG-xx` gaps carry a decision (1 `block`, accepted by PO with owner) | 100% |
| Failure coverage | 3 / 3 `SM-xx` answer failure, recovery and timeout | 100% |
| Scale coverage | 7 / 7 modules carry a named bottleneck + designed degradation mode | 100% |
| Story coverage | 19 / 21 `BRD-xx` traceable into a story | **90%** |
| Test coverage | 14 / 14 stories carry LLD test scenarios | 100% |
| Traceability | references resolve (validator Level 1) | 100% |

### Story-coverage gap (90%) — accepted, with owner and rationale

| Gap | Owner | Rationale |
|---|---|---|
| `MOD-05` (Lead & CRM) has no story — `UC-04`, `UC-05`, `SM-03` unimplemented | **PO — accepted 2026-09-19** | **Out of scope for this program.** `06-architecture.md` §60 puts `MOD-05` strictly post-call and off the turn path, and `01-brd.md` §2 excludes new user-facing features and the chat/WhatsApp path. A performance program has no change to make here. Recorded now because the gap was previously invisible: this table counts `BRD-xx` ownership only, and `BRD-13`/`BRD-18` are cited by `US-014`/`US-016` (voice-path stories), so the requirement-level count read as covered while the owning module had no work item. Re-open if `MOD-05` ever moves onto the turn path. |
| `BRD-04` (endpointing reconciliation) has no story | PO | Deliberate. The 600 ms floor is a **measured config value owned by requirement**, not an optimisation target. Reducing it is Class C and belongs to `US-014`'s decision. `TRD-02` (MOD-01) specifies the single-live-decision contract; a separate story would manufacture an optimisation the evidence does not support. |
| `BRD-12` (CPU/RAM budget) has no story | TPO | Deliberate. It is a **verification** requirement, not a build item: `US-002`'s harness measures CPU/RAM at N=1 and N=2 and its TAC carries the 80% ceilings. Recorded as a measurement inside `US-002` rather than a standalone change. |
| `BRD-20` (background-load priority) | TPO | **Resolved** — `US-017` owns the enforcement mechanism (a work gate that defers background requests rather than interleaving them) and the measurable priority invariant. |
| `BRD-21` (staged RAG configuration) | TPO | **Resolved** — `US-018` owns both configurations: `RAG-Baseline` characterised and frozen as the incumbent, `RAG-Optimized` screened on retrieval metrics then gated end-to-end by the golden set. |

**Overall: PASS** — every category at 100%, except story coverage at 89%, whose two gaps are explicitly accepted above with named owners and rationales.

> **This PASS is a statement about the plan's internal consistency, not about the system.** It is compatible with a blocker: `DG-03` remains open — the golden set does not exist and critical-intent ground truth is the PO's to approve (`US-003`). No quality-gated change can ship until it closes. A green scorecard must not be read as clearance to deploy.

```
╔══════════════════════════════════════╗
║       SDLC PLAN VALIDATION            ║
╠══════════════════════════════════════╣
║ Requirements coverage       100%      ║
║ Use-case coverage           100%      ║
║ Workflow coverage           100%      ║
║ Actor coverage              100%      ║
║ Data coverage               100%      ║
║ Failure coverage            100%      ║
║ Scale coverage              100%      ║
║ Story coverage               89%      ║
║ Test coverage               100%      ║
║ Traceability                100%      ║
╠══════════════════════════════════════╣
║ SDLC ARTIFACT VALIDATION:      PASS   ║
║ SYSTEM IMPLEMENTATION VALIDATION:     ║
║                          NOT YET RUN  ║
╠══════════════════════════════════════╣
║ Blockers outside plan scope:  DG-03   ║
║                               CV-02   ║
║                               US-014  ║
╚══════════════════════════════════════╝
```

> **Read the two lines separately.** `SDLC ARTIFACT VALIDATION: PASS` means the documents are internally consistent, cross-referenced and complete. It says **nothing** about whether the system meets its requirements. `SYSTEM IMPLEMENTATION VALIDATION` has not run, because no implementation exists yet. The validator's exit code — the thing that reads as a green light — measures the first and only the first.

## 3. Planning Gates

| Gate | Name | Status | Evidence |
|---|---|---|---|
| G1 | Context understood | ✓ | Stage 0 block in `00-product-intent.md` |
| G2 | Requirements discovered | ✓ | 13/13 Discovery Matrix; Evidence Register covers AS-01…08 |
| G3 | Use cases discovered | ✓ | every actor ≥1 UC; 10 UCs at 14/14; 3 WFs drilled |
| G4 | Data/state analysis completed | ✓ | DAT-01…14, DG-01…06 all decided, SM-01…03, Load & Capacity Model filled |
| G5 | Coverage analysis completed | ✓ | all 19 `BRD-xx` in the §1 matrix, no empty Use Cases cells |
| G6 | Gaps resolved or accepted | ✓ | gap list: 4 resolved, 5 accepted with owners; `DG-03` accepted by PO |
| G7 | Architecture traceable | ✓ | 7/7 modules carry a flow + scale profile with bottleneck and degradation mode |
| G8 | Stories traceable | ✓ | US-001…US-014 each cite `MOD-xx` + `UC-xx`/`TRD-xx` |
| G9 | Tests traceable | ✓ | 14/14 stories carry LLD test scenarios; hot-path stories carry load TACs |
| G10 | Final semantic validation PASS | ✓ | validator result in §4 |

## 4. Validator Result

```
node <skill-dir>/scripts/validate-artifacts.mjs doc/sdlc
=> 752 passed, 0 failed   (exit code 0)
```

Structural + semantic validation across the tree: required files present, ID uniqueness and
format, every cross-reference resolving, per-UC scenario tables ≥14 rows, Discovery Matrix
≥13 dimensions, data inventory + gap decisions, Load & Capacity Model, `stateDiagram`
presence, per-module bottleneck/degradation rows, the 19-row capability table, the semantic
scorecard, and the manifest cross-checks (every `done` stage's evidence file exists; the ID
registry resolves against the tree in both directions).

**Iteration history within this run** (the Stage 5 budget allows 3; the same budget applies
to validation findings):

Iteration 1 — 418 passed / 44 failed. Fixed: `DAT-01…11` each defined three times across
three Stage-4 tables; the MVP scope row was not verbatim against `00-product-intent.md` §5.

Iteration 2 — 532 / 4. Fixed: `REC-11` recorded (model pre-warm claimed absent, contradicted
by committed code); `REC-12` and `REC-13` recorded after two further plan-vs-code conflicts
surfaced by the module agents.

Iteration 3 — 689 / 4. Fixed: three stories missing the "As a … I want … so that" statement
form; a `# DAT-11` comment inside a code fence read as a duplicate ID definition.

Iteration 4 — 693 / 0.

Iteration 5 — **752 / 0**. Final. Added `US-017` (`BRD-20`) and `US-018` (`BRD-21`), which restored story coverage from 81% to 90%. Also in this pass: evaluator independence and network-condition injection strengthened in `MOD-06`; the `480`-row arithmetic error corrected in **eleven** locations across six files (it had been fixed in one and assumed propagated); `MOD-02` gained the index-versioning ownership it lacked; `MOD-01` adopted the canonical `TURN_E2E_MS` / `processing_ms` split; `MOD-04`'s CPU fallback was made subject to `BRD-12`; and `US-005`'s TAC-2 was reframed from a threshold to a prediction.

> **The lesson this run keeps teaching, stated once.** Every defect above — the eleven arithmetic instances, the unowned contract, the two clocks, the prediction-as-requirement — was found by *reading across files*, and **not one of them was caught by the validator**, which was green throughout. The validator proves references resolve and structure is complete. It cannot prove the documents agree with each other, and it has never been able to.

Four of these were defects in artifacts written by this planning run, not by the agents: the
triple-defined `DAT-xx` rows, the drifted MVP condition, the mermaid/prose `keep_alive`
mismatch, and the pre-warm claim that `REC-11` corrects.

## 5. What This Plan Does Not Claim

- **It does not claim the 700 ms p50 target is reachable.** `CV-02` carries the arithmetic conflict; the tightest defensible SLO derived from measured components is p50 ≈ 2,300 ms / p95 ≈ 2,900 ms, which requires the PO's approval under `BRD-02`'s evidence clause.
- **It does not claim the quality gate can run.** `DG-03` is open; `US-003` cannot be completed by any agent.
- **It does not claim the codebase matches its documentation.** `REC-01`…`REC-13` record thirteen plan-vs-code conflicts, six of them instances of the "set ≠ live" pattern.
- **It does not claim barge-in works.** `CV-01` — `US-014` is a decision, not an implementation.
