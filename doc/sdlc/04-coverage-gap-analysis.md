> **Lens:** BA + TPO + PO · **Decided by:** BA (coverage), TPO (fix feasibility), PO (accepted-gap sign-off) · **Inputs:** `00-product-intent.md`, `01-brd.md`, `02-use-cases-workflows.md`, `03-data-state-analysis.md` · **Engagement:** Brownfield · **Defines:** CV-01 … CV-04

# Coverage & Gap Analysis — Admissions Voice Assistant Performance & Concurrency Program

## 1. Requirement → Use Case Coverage Matrix

| Requirement | Use Cases | Workflows | Stories | Tests | Coverage |
|---|---|---|---|---|---|
| BRD-01 | UC-07 | WF-01 | planned | planned | UC-covered |
| BRD-02 | UC-01, UC-02 | WF-01 | planned | planned | UC-covered |
| BRD-03 | UC-01 | WF-01 | planned | planned | UC-covered |
| BRD-04 | UC-01 | WF-01 | planned | planned | UC-covered |
| BRD-05 | UC-03 | WF-02 | planned | planned | UC-covered |
| BRD-06 | UC-03 | WF-02 | planned | planned | UC-covered |
| BRD-07 | UC-03 | WF-02 | planned | planned | UC-covered |
| BRD-08 | UC-08, UC-10 | WF-03 | planned | planned | UC-covered |
| BRD-09 | UC-02, UC-08, UC-10 | WF-03 | planned | planned | UC-covered |
| BRD-10 | UC-02 | WF-01 | planned | planned | UC-covered |
| BRD-11 | UC-03, UC-10 | WF-02 | planned | planned | UC-covered |
| BRD-12 | UC-03 | WF-02 | planned | planned | UC-covered |
| BRD-13 | UC-01, UC-04 | WF-01 | planned | planned | UC-covered |
| BRD-14 | UC-02 | WF-01 | planned | planned | UC-covered |
| BRD-15 | UC-06, UC-09 | WF-03 | planned | planned | UC-covered |
| BRD-16 | UC-06 | WF-03 | planned | planned | UC-covered |
| BRD-17 | UC-06 | WF-01 | planned | planned | UC-covered |
| BRD-18 | UC-01, UC-02, UC-04 | WF-01 | planned | planned | UC-covered |
| BRD-19 | UC-09 | WF-01 | planned | planned | UC-covered |
| BRD-20 | UC-03 | WF-02 | planned | planned | UC-covered |
| BRD-21 | UC-02, UC-08, UC-10 | WF-01, WF-03 | planned | planned | UC-covered |

## 2. Actor Coverage

| Actor | Use Cases | Coverage |
|---|---|---|
| Caller | UC-01, UC-02, UC-03, UC-04 | ✓ |
| Admissions counselor | UC-04 | ✓ |
| Operator | UC-06, UC-07 | ✓ |
| Developer | UC-05, UC-07, UC-08, UC-10 | ✓ |
| Twilio | participant in UC-01, UC-03 | ✓ (non-initiating — resolved in Stage 3 completeness note) |
| Inference engine | participant in UC-02, UC-03, UC-10 | ✓ |
| Retrieval service | participant in UC-02, UC-03 | ✓ |
| CRM | participant in UC-04, UC-05 | ✓ |
| Stores | participant in UC-05 | ✓ |

## 3. MVP Scope Coverage

| MVP In item (from 00 §5) | BRD-xx / UC-xx | Coverage |
|---|---|---|
| Per-stage instrumentation of the live voice path | BRD-01, BRD-16, UC-07 | ✓ |
| Prefix-cache behaviour applied to prompt design | BRD-02, UC-02 | ✓ |
| Streaming the LLM call and the TTS synthesis | BRD-02, UC-01, UC-02 | ✓ |
| `OLLAMA_KEEP_ALIVE` + boot preload (cold-start) | BRD-03, BRD-17, UC-06 | ✓ |
| De-serializing ERC retrieval for 2 callers | BRD-05, BRD-07, UC-03 | ✓ |
| Frozen golden set + quality gate | BRD-08, BRD-09, UC-08 | ✓ |
| Reproducible N=1/N=2 load harness | BRD-01, BRD-05, UC-07 | ✓ |
| Model/quantization sweep **only if** streaming + concurrency do not meet the gates | BRD-11, UC-10 | ✓ |
| 2 voice + 1 background chat/admin as an acceptance condition (`BRD-20`) | BRD-20, UC-03 | ✓ |
| Staged RAG delivery: baseline now, optimized on evidence (`BRD-21`) | BRD-21, UC-02, UC-08 | ✓ |

**Scope-creep check:** no item from `00-product-intent.md` §6 (Non-goals) appears in the plan. Barge-in is listed as an MVP "In" only as a *decision* (`UC-09`), not as an implementation commitment — consistent with §6.

## 4. Scenario Coverage Rollup

| Use case | Scenarios covered | Missing |
|---|---|---|
| UC-01 | 14/14 | — |
| UC-02 | 14/14 | — |
| UC-03 | 14/14 | — |
| UC-04 | 14/14 | — |
| UC-05 | 14/14 | — |
| UC-06 | 14/14 | — |
| UC-07 | 14/14 | — |
| UC-08 | 14/14 | — |
| UC-09 | 14/14 | — |
| UC-10 | 14/14 | — |

## 5. Contradiction Detection

| ID | Statement A | Statement B | Why they conflict | Resolution |
|---|---|---|---|---|
| CV-01 | `voice_system_prompt.py` §211/§502 instruct the agent to immediately yield when the caller speaks ("treat the caller's new utterance as the highest-priority current input") | `main.py:624–627` discards every inbound frame while `tts_playing`, and resets the partial buffer | The prompt describes a capability the architecture prevents; ~300–500 prompt tokens describe unreachable behaviour | Resolved by `UC-09` — a decision is now owned. Until decided, `BRD-19` is unmet |
| CV-02 | `BRD-02`: p50 ≤ 700 ms | `BRD-04`: measured endpointing floor is 600 ms | 600 ms consumes 86% of the budget, leaving ~100 ms for STT + retrieval + prefill + generation + synthesis + carrier RTT — not achievable with a serial pipeline | **Carried** — `BRD-02`'s evidence clause governs: propose the tightest defensible SLO with arithmetic and obtain PO approval. Not silently relaxed |
| CV-03 | MVP excludes hosted inference on residency grounds (`AS-03`) | The latency target may only be reachable with a hosted model | If local arithmetic cannot meet `BRD-02`, the only remaining lever is prohibited | **Carried** — the honest outcome is a relaxed SLO, not a hosted API. If the PO ever reclassifies, `UC-10` is the vehicle |
| CV-04 | `BRD-16`: configuration has a single source of truth | `scripts/predeploy.py` writes the machine block into `.env` **and** writes `.machine_profile.json`; `check_drift()` compares only `detected` fields | Two writers, no reconciliation; four keys are never read at all (`FASTAPI_WORKERS`, Pipecat VAD, reranker, semantic cache) | **Carried to Stage 8** as `DG-05` — establish one source; the plan does not depend on which |

## 6. Gap List

| Gap | Found where | State |
|---|---|---|
| BRD-19 had no use case that owns the interruption decision — only `UC-01` A2 describing current behaviour | §1 matrix, iteration 1 | **resolved in iteration 2** — `UC-09` added |
| MVP item "Model/quantization sweep only if gates demand" had no use case | §3 matrix, iteration 1 | **resolved in iteration 2** — `UC-10` added |
| CV-01 — prompt instructs behaviour the architecture prevents | §5, iteration 1 | **resolved in iteration 2** — ownership assigned to `UC-09` |
| `DG-03` — golden set absent; critical-intent ground truth needs PO approval (`AS-05`) | `03-data-state-analysis.md` A.4 | **partially resolved — build complete, sign-off outstanding.** `eval/golden_set.jsonl` now exists: **161 cases, 28/28 intents, 24 verified mechanically, 137 PENDING_PO_SIGNOFF, 0 approved**, with `eval/checks.py` self-testing green. The engineering half is done; the human half is not. All 12 critical intents report **BLOCKED**. Owner: **PO** — the twelve critical-intent ground truths require approval before the set can freeze |
| `SM-02` has no per-stage timeout below the client limit — a hung generation has no ceiling | `03-data-state-analysis.md` B.2 Q10 | **accepted** — owner: TPO. Rationale: `BRD-14` already requires bounded timeouts; the ceiling is set when streaming lands and the call becomes cancellable |
| `SM-01` has no session-level idle timeout | `03-data-state-analysis.md` B.2 Q10 | **accepted** — owner: PO. Rationale: the max-utterance cap forces a turn; an idle caller costs a held session but no inference. Revisit if N>2 |
| `SM-03` HANDOFF_PENDING never ages out | `03-data-state-analysis.md` B.2 Q10 | **accepted** — owner: PO. Rationale: counselor follow-up is outside this system |
| `UC-01` duplicate-request scenario: no dedupe on carrier retry | §4 rollup | **accepted** — owner: TPO. Rationale: a retry is a new call with a new session; dedupe would risk merging distinct callers |

## 7. Iteration Log

| Iteration | What was added | Where |
|---|---|---|
| 1 | First full pass. Four gaps found: BRD-19 uncovered; the conditional model-sweep MVP item uncovered; CV-01 contradiction detected; DG-03 confirmed as the only `block` decision | — |
| 2 | Added `UC-09` (decide interruption behaviour) and `UC-10` (evaluate a model/quantization change), each with a 14-row scenario table; re-pointed BRD-19 → UC-09, and the model-sweep MVP item and BRD-08/09/11 → UC-10; CV-01 ownership assigned | `02-use-cases-workflows.md` |
| 3 | Accepted-gap sign-offs recorded with owners for DG-03 and the three state-machine timeout gaps; CV-02/CV-03/CV-04 confirmed as carried, not open | `04-coverage-gap-analysis.md` |

## 8. Discovery Challenge

1. **Which persona has no meaningful use case?** — None. Caller → UC-01…04; counselor → UC-04; operator → UC-06/07; developer → UC-05/07/08/10. Personas are not system actors by nature; resolution recorded in Stage 3 §1.
2. **Which requirement has no workflow?** — None. All 19 BRDs map to WF-01, WF-02 or WF-03 via §1.
3. **Which workflow has no failure path?** — None. WF-01 and WF-02 carry failure drills and partial-completion matrices; WF-03's compensation is revert per `BRD-15`.
4. **Which business rule is never exercised by any use case?** — None. "Critical intents are non-negotiable" → UC-08; "quality gate precedes speed" → UC-08/10; "local inference only" → UC-10 A1; "behaviour-changing work needs sign-off" → UC-09; "one variable at a time" → WF-03; "measurement beats consensus" → UC-07.
5. **Which data element is assumed available but not proven?** — Three: `DAT-07` traces and `DAT-08` golden set do not exist (both owned, `DG-04`/`DG-03`), and `DAT-06` lead completeness depends on post-call extraction succeeding. The third is **accepted** — partial leads are already the designed behaviour (`SM-03` PARTIAL).
6. **Which state has no recovery path?** — `SM-02` GENERATING and SYNTHESISING failures have no compensation (recovery is "caller repeats"). **Gap, accepted** — this is precisely what streaming + cancellability fixes; recorded rather than hidden.
7. **Which external dependency can fail — and what is the plan?** — Twilio (call ends; no mitigation possible), Ollama (no fallback — `BRD-13` unmet today), ERC (falls back to local store), CRM (handoff unrecorded, spoken honestly), Postgres/Redis (post-call data lost). Ollama has **no** degradation path: **gap, accepted**, owned by TPO, addressed by `BRD-13`/`BRD-17`.
8. **What happens during duplicate submission?** — A carrier retry opens a new session; no dedupe. **Accepted** — merging would risk conflating distinct callers.
9. **What happens if the user abandons the workflow?** — Caller hangs up: `SM-01` → ENDED from any state; post-call handling still runs if a transcript exists.
10. **What happens if data is stale?** — `DAT-03` (ERC vectors) may be stale relative to `DAT-01`; `DG-01` decides consolidation. `DAT-09` is stale by observation (`WHISPER_NUM_THREADS` 4 vs 6) with zero runtime effect — `DG-05`.
11. **What happens if two operations occur concurrently?** — `UC-03` is the whole use case; `BRD-07` names the known serialization point; `BRD-06` names the correctness risk.
12. **Which story cannot be independently tested?** — Not yet determinable; stories do not exist. **Carried to Stage 10** — the story→test matrix is where this is answered.
13. **Which architecture decision has no measurable justification?** — None yet; Stage 7 has not run. The plan's standing rule (`BRD-16`, "set ≠ live") and the A4 prefix-cache result already remove the two decisions that would otherwise have been unjustified (prompt trimming, prompt reordering).
14. **Which requirement is impossible to verify?** — `BRD-12` (CPU ≤ 80%) is verifiable but weakly attributable on a 6-thread box shared with 8 services and desktop workloads; measurement will carry high variance. **Accepted** — reported with spread and sample size, never as a single number.
15. **Which module breaks first at 10× load — and what saturates?** — Not yet decomposable into modules; **carried to Stage 7**, where each MOD gets a scale profile. Today's known answer at 2× is the retrieval service's single event loop.
16. **Is there a quantitative load model, or are the numbers assumed?** — Partly assumed. The model is quantified (0.13 turns/s, peak concurrency 2) but `T_cycle ≈ 15 s` and the peak of 2 are **Assumed** (`AS-01`, `AS-06`). **Gap, accepted** — `DG-02` validates during baseline.
17. **What degrades first under overload — and is it a designed degradation mode?** — Today: retrieval serializes, then VRAM saturates, then OOM. **None of it is designed.** This is why `BRD-05`, `BRD-11` and `BRD-13` exist; Stage 7 must name a designed degradation mode per module.
18. **Which requirement/UC/WF/story falls outside the MVP boundary or into a non-goal?** — None. §3 covers every MVP In item; no non-goal appears.

## 9. Freeze Decision

**STATUS:** COMPLETE

Every `BRD-xx` (19/19) has use-case coverage with no empty cells; every actor has at least one use case; all ten use cases carry 14/14 scenario coverage; every data gap has a decision (`DG-01`…`DG-06`), with the single `block` decision (`DG-03`) explicitly accepted by the PO with a named rationale and bounded blast radius (it blocks the quality gate, not the architecture); every state machine carries failure, recovery and timeout answers; the load model is quantified and its Assumed inputs are named; and the Discovery Challenge produced no open concern that is not either resolved or accepted with an owner.

**Carried into later stages (not blocking the freeze):** Stage 7 must give every module a scale profile with a named bottleneck and a designed degradation mode (Q15, Q17); Stage 10 must confirm every story is independently testable (Q12).
