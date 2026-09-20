> **Lens:** TPO (sequencing) / Architect (root cause) · **Engagement:** Brownfield — `D:\project\universityDemo`
> **Written:** 2026-09-19 · **Basis:** an 18-story audit against the codebase (three independent code-reading passes) plus suite runs measured today

# Remediation Plan — from 34/147 DoD to a closable programme

## 1. What is actually done

Stated first, because the pending list is long and the delivered list is real.

| Delivered | Evidence |
|---|---|
| 8 of 18 stories implemented | 333 checks pass across 8 story suites + 3 cross-cutting suites, all measured today |
| Model decision | `llama3.2:3b` chosen on a paired defect census; committed, live, reversible by one setting |
| Endpointing | `BRD-04` complied (one live decision, reachable config); the 600 ms window no longer dead time via speculative STT |
| Post-call event-loop freeze | Found by py-spy, fixed, and the guard widened — verified against the bug itself |
| 5 module docs reconciled | `MOD-01`, `MOD-03`, `MOD-06`, `MOD-07`, `MOD-04` + the `US-015` benchmark |
| `DEF-001` raised | The retrieval timeout, with the full forensic trail including three refuted hypotheses |
| 5 status lines corrected | Counts that drifted from their own sections |

**Not one story is fully done (0/18).** The gap is 113 DoD boxes, and the analysis below is about why they are still open rather than what they are.

## 2. Root cause analysis

Five causes. They are ranked by leverage, not by size.

### RC-1 — The quality gate is a human bottleneck, and it is much smaller than it looks

`DG-03` blocks **five stories** (US-004, 005, 009, 010, 018) and the quality leg of US-015. Every one of them changes what the system *says*, so none can be built or judged until the ground truth is approved.

It presents as 137 unapproved cases. It is not:

| Class | Count | Review effort |
|---|---:|---|
| KB transcription — mechanical, bulk-approvable | **40** | one batch approval |
| KB topic, behaviour is the test | 8 | one sitting |
| **Policy decisions — the real review** | **89** | **grouped into 10 intents** |

The 89 policy cases cluster into ten intents (Out-of-scope 9, Lead capture 9, Callback 8, Consequential action 8, Termination 8, Escalation 8, Interruption 8, Opt-out 8, Noise 5, STT correction 4). Cases within an intent share a policy, and `eval/signoff_packet.py --approve-intent` already supports deciding at that level.

**So the real ask is ~10 decisions, not 137.** That is the single highest-leverage action available to the programme, and it is not a technical one.

### RC-2 — Acceptance suites test the implementation, not the specification

This is the most important technical finding, and it is systemic rather than incidental.

Suites were written alongside the code. They therefore encode **what was built**, not **what was specified**. A green suite proves the tests pass; it does not prove the acceptance criteria are met. Five stories demonstrate the gap:

| Story | Claimed | Reality |
|---|---|---|
| US-007 | IMPLEMENTED | a DoD box ticks *"observed resident in nvidia-smi at the moment a call arrives"* — there is **no readiness surface**; the harness hardcodes "no readiness surface exists" |
| US-011 | "ACs verified" | TAC-5 (unparseable config fails the boot, naming the key) is **not implemented**; `validate_types` is called only by `report()` and the test |
| US-013 | IMPLEMENTED | TAC-4/T-11 claim the record carries "the rung, the breaker state and the wall time charged to the dead dependency" — **nothing records any of it** |
| US-015 | PARTIAL | the deliverable — a funnel with a Pareto front — **has no code**; `pareto_front`, `model_funnel.py` do not exist |
| US-017 | "holding" | the cited run is **DISCARDED**, 146/198 turns over the 3,000 ms cap its own TAC requires |

**Nothing checks that every AC and TAC has a corresponding test.** That absence is the cause, and it is fixable mechanically.

### RC-3 — Documents are claims that nothing re-verifies

The audit found: five stale status lines; US-001's tests tagged with **nine wrong scenario IDs**, two of which (`T-10`, `T-11`) read as covered while nothing tests them; `MOD-03` B.3 and B.8 **contradicting each other**; and the plan-state register's status table contradicting its own summary on the same page.

Every instance is the same shape: **a document written as a claim, read forever, and never re-verified against the thing it describes.** Three of the five stale status lines were drift I introduced *today*.

### RC-4 — "BLOCKED" was applied to whole stories, including the unblocked parts

US-018 is marked `BLOCKED - DG-03` and attributes the entire story to the quality gate. But the story itself states its two-configuration definition and baseline characterisation are *"unblocked today and need no golden set"* — and **none of it exists**: no `rag_config.py`, and `recall_at_k`, `ndcg`, `RAG-Baseline` appear in no `.py` file in the repository.

A blanket label stopped work on a half that was never blocked. US-018 may not be unique in this.

### RC-5 — The DoD's recurring items are evidence-work that nobody owns

The same four items appear across every implemented story: LLD test mapping, story-specific load/soak gates, `BRD-15` demonstrations, module-doc reconciliation. They are not coding tasks, so no coding pass closes them, and no role in the plan is assigned to produce them. The result is predictable: implementation finishes, the story stalls, and the box stays unticked indefinitely.

## 3. The plan

Sequenced by leverage. Columns say who can do it.

### Track A — Unblock the programme *(needs the PO)*

| # | Action | Owner | Effort |
|---|---|---|---|
| A1 | **Review 10 intents, not 137 cases.** Use `eval/signoff_packet.py --summary` then `--approve-intent` per intent | **PO** | ~10 decisions |
| A2 | Bulk-approve the 40 KB-transcription cases in one batch | **PO** | 1 action |
| A3 | Decide the 8 "behaviour is the test" cases | **PO** | one sitting |

**A1–A3 release five stories and the US-015 quality leg.** Nothing else in this plan unlocks as much.

### Track B — Close the specification gap *(I can do this)*

| # | Action | Why |
|---|---|---|
| B1 | **Build an AC→test traceability check.** Parse every story's AC/TAC ids; fail when an id has no test citing it | This is RC-2's structural fix. It would have caught all five overstatements above, including the three in stories marked IMPLEMENTED |
| B2 | Fix the five overstatements found: US-007's impossible box, US-011's TAC-5, US-013's record fields, US-015's funnel, US-017's cap claim | Each is a decision — implement it, or re-word the claim down to what exists. **Re-wording is legitimate; leaving it is not** |

### Track C — Make documentation self-checking *(I can do this)*

| # | Action | Why |
|---|---|---|
| C1 | **Build a doc-vs-artifact check**: every `N/N` in a status line must equal the suite's actual output; DoD counts must equal a mechanical count | RC-3's structural fix. Would have caught all five stale lines, including my own drift |
| C2 | Extend it to module docs: fail when a `file.py:LINE` citation no longer points at the symbol it names | `MOD-03`'s self-contradiction and US-012's stale line numbers are both this |

### Track D — Finish the recurring DoD work *(mostly me; D3 needs you)*

| # | Action | Owner | Note |
|---|---|---|---|
| D1 | LLD test mapping for US-002/003/006/007 | me | Same shape as US-001, which found nine wrong tags — expect more |
| D2 | `BRD-15` demonstrations for the remaining stories | me | `test_brd15_rollback.py` covers six; extend by reusing its pattern |
| D3 | **The story load/soak gates** — US-001's 30-min N=2 soak, US-006's TAC-1/2/5, US-008's three consecutive runs | **you + me** | Needs a quiet box: VS Code, Docker and Chrome closed. Every N=2 figure otherwise carries `harness_fault: true` — measured on all five runs today |
| D4 | Un-block US-018's unblocked half (config definition, baseline record, relevance set, metric definitions) | me | Verify the story's "unblocked today" claim before starting |

### Track E — Housekeeping *(I can do this)*

| # | Action |
|---|---|
| E1 | Remove or wire the dead code: `emit_current()` (defined, no call site) and `_get_client()` (defined, never called) |
| E2 | Reconcile `STAGES`: the LLD names 11 stages, the code has 8, and `llm_first_token` is declared but emitted by nothing |
| E3 | `DEF-001` — **stays deferred** by your instruction, and its safe-to-defer condition (grounding preserved) still holds |

## 4. Where I need your help

**1. The DG-03 decisions (A1–A3).** This is the one thing I cannot do, and it is the highest-leverage item in the plan. Ten intent-level decisions release five blocked stories. I can prepare the packet so each intent is one page — the intent, its cases, the KB evidence, and what the current code does — but the decision is the PO's.

**2. A quiet box for the load gates (D3).** `harness_fault: true` on every N=2 run today. The box has 6 logical cores running VS Code, Docker Desktop, two Chromes, llama-server, the app stack, and me. Closing the first three would very likely clear it, and without that every load figure carries a caveat.

**3. Your priority call.** If only one track runs this week, my recommendation is **A then B1**: A unblocks five stories, B1 stops the next five overstatements from being written. C1 is close behind because it is cheap and it already caught five real drifts.

## 5. What I would not do

- **Not relax a `TAC` to match the code.** Where a claim overstates, the choice is implement it or re-word it down and say so — never quietly move the threshold. `US-006`'s withdrawn `≥ 18 tok/s` is the precedent: it was superseded by a measurement, not deleted.
- **Not start the blocked stories.** US-004/005/009/010 change what the system says; building them before `DG-03` means building against unapproved ground truth and measuring against it.
- **Not quote an N=2 absolute number** without the `harness_fault` caveat until D3 lands.
