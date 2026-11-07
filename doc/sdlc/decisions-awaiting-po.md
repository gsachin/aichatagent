> **Lens:** PO · **Written:** 2026-09-19 · **Companion to:** `eval/DG-03-signoff-packet.md`
> **Purpose:** the decisions that are **not** among the 137 ground truths but need the same person, so one conversation closes all of them.

# Decisions awaiting the Product Owner

`eval/DG-03-signoff-packet.md` already sets out the 137 ground truths, sorted by
decision kind, with the recording commands. **That packet is complete and this
document does not repeat it.** What follows is what the 2026-09-19 audit found
that the packet does not cover: four decisions that are not ground truth, that no
engineer can take, and that are cheap to take now and expensive to take later.

All four sit in the same conversation as the sign-off. Three of them would be
*changed by* the sign-off if taken after it.

---

## D1 · Should a caller-facing readiness surface exist?

**The question.** Four places in two stories require the model's residency or the
GPU clock to be read **"at the instant a call arrives"**. None of them can be
satisfied by what exists.

| Where | Requires |
|---|---|
| `US-006` LLD T-11 | Residency observed in `nvidia-smi` at the instant a call arrives |
| `US-006` LLD T-16 | The GPU is not idle-clocked at call arrival |
| `US-007` LLD T-17 | After a successful gate, a call placed immediately shows no cold-load signature and the GPU is not idle-clocked |
| `US-007` DoD box | "the model resident in `nvidia-smi` at the moment a call arrives, and the GPU not idle-clocked" — **unticked 2026-09-19 for exactly this reason** |

**Why none can pass.** `gpu_clock_state()` is called at `app/boot_readiness.py:400`,
**inside the boot gate**. The gate is a CLI the operator runs (`python -m
app.boot_readiness`, invoked at `start_services.ps1:728`). The application
exposes **no readiness route at all** — verified: zero matches for a `/health` or
`/ready` decorator in `app/main.py`. So nothing at call time can ask whether the
stack is warm.

**The two options, and what each costs.**

- **(a) Build a readiness surface.** Expose the gate's verdict on an endpoint, or
  have the turn path consult it. Roughly a day. It also unblocks the US-002
  harness, whose scenario T-9 ("refuse to start against a stack reporting
  not-ready") is a **gap for the same reason** — the harness built a refusal path
  and has nothing to refuse against. That is a fifth dependent, and the harness
  says so in its own output today.
- **(b) Re-word the four claims** to boot-time verification, which is what
  actually happens and is genuinely useful — it catches a cold stack *before* a
  caller arrives rather than during their call.

**Recommendation: (a).** It converts five unsatisfiable claims into working
checks, it is the only way the "observed externally at call arrival" language in
three acceptance criteria ever becomes true, and the harness dependency means the
cost is already partly paid. **(b) is defensible and cheaper, but it should be a
recorded decision rather than a silent edit** — the sentence is a PO acceptance
criterion in two stories, and re-wording it is yours, not an engineer's.

---

## D2 · Is the model decision good enough to stand?

**The question.** On 2026-09-19 the voice model was changed from `qwen2.5:14b` to
`llama3.2:3b`. The evidence is **screen-grade only** — it rests on a defect
census over 83 golden-set cases, and **no case carries PO approval**, because
`DG-03` is exactly what is pending.

**What the evidence actually says.** The two models miss required facts
**identically** (28 cases each, paired discordance 7–7, McNemar p = 1.00) while
the 14B breaks the two-sentence spoken limit **2.3× as often** (37 vs 16, paired
26–5, p = 0.0002). The 3B is also 4.1× faster on prefill and 5.4× on generation.
Full working: `doc/perf/us015-model-decision.md`.

**Why it needs you.** The decision is reversible by one setting and is
deliberately recorded as provisional. But the *moment* the 137 ground truths are
approved, this comparison becomes adoption-grade — and it should be re-run, not
assumed to still hold. **Taking the sign-off first makes the model question
answerable properly rather than approximately.**

**Recommendation:** approve the ground truth first (Track A), then have the
screen re-run at n=161. The reversal condition is already written into the
decision document, so nothing needs deciding now except the ordering.

---

## D3 · Should `BRD-15` be demonstrated for `US-007`?

**The question.** `BRD-15` requires that every adopted change be revertible *and
that the revert be demonstrated*. Seven stories are now demonstrated by
`test_brd15_rollback.py` (42/42). **`US-007` and `US-008` remain uncovered, and
for `US-007` the exclusion is a *reason*, not a demonstration.**

**The reason given:** its revert is "remove the boot gate", a commit-level change
rather than a setting. That is true and it is also **not an obstacle** — a
commit-level revert can be demonstrated the same way any other can: revert the
commit, observe the prior behaviour (a stack that starts without checking
readiness), restore, verify. The suite simply has not done it.

**Why it matters more than it looks.** The programme's own audit found that
*every* story asserted its change was revertible and none had demonstrated it.
Seven are now demonstrated. Leaving the eighth on a stated reason reintroduces
the exact pattern the demonstration exists to break — a claim that reads as
verified because the reason for not verifying it sounds technical.

**Recommendation:** demonstrate it. It is a small piece of work and it closes
`BRD-15` completely except for `US-008`, which genuinely spans two repositories.

---

## D4 · What closes `US-018`'s unblocked half?

**The question.** `US-018` is marked `BLOCKED - DG-03` and attributes the whole
story to the ground-truth gate. **Its own text says otherwise:** the
two-configuration definition and the baseline characterisation are *"unblocked
today and need no golden set"*.

**What exists:** nothing. `app/rag_config.py` does not exist; `RAG-Baseline`,
`rag_optimized`, `recall_at_k`, `ndcg` and `tokens_injected` appear in **no `.py`
file in the repository**; `eval/rag_baseline.json` is absent. `DAT-14` is still
recorded as *"Missing — no versioning concept exists"*.

**Why it needs you.** Two readings, and the choice is a scope decision:

- **The story is blocked and the label is right** — then its text is wrong and
  should be corrected, because a story that says half of itself is unblocked
  while being labelled wholly blocked will never be started.
- **The unblocked half is real work** — then it can be scoped and started now,
  with only the *comparison* waiting for `DG-03`.

**Recommendation: the second.** A named baseline configuration and a stated
metric set are useful the moment they exist, and having them ready means the
`DG-03` approval is immediately followed by an answer rather than by a build.

---

## Summary

| # | Decision | Blocks | Cost if deferred |
|---|---|---|---|
| **D1** | Build a call-time readiness surface, or re-word four claims | `US-006` T-11/T-16, `US-007` T-17 + a DoD box, `US-002` T-9 | Five unsatisfiable scenarios across three stories stay unsatisfiable, and the re-word gets harder as more documents cite them |
| **D2** | Ordering: sign-off first, then re-run the model screen | `US-015`'s adoption-grade verdict | A provisional model decision stays provisional indefinitely |
| **D3** | Demonstrate `US-007`'s commit-level revert | `BRD-15` completeness | The last BRD-15 gap stays a *reason* rather than a demonstration |
| **D4** | Confirm `US-018`'s unblocked half, or correct its text | `US-018` | A story that says half of it is startable is never started |

**D1 is the one with the deadline.** It is the only item here that the `DG-03`
sign-off makes *worse*: approving the 137 freezes the golden set, and four
scenarios plus an acceptance criterion in two stories currently cannot pass
against it.
