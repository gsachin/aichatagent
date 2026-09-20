# US-015 — LLM model and parameter decision

**Date:** 2026-09-19
**Hardware:** NVIDIA RTX 5060 Ti, 16,311 MiB VRAM, 31.9 GB RAM (tier `nvidia_high`)
**Evidence grade:** SCREEN-GRADE. No case in the golden set carries Product-Owner
approval (`DG-03`: 137 of 161 ground truths are `PENDING_PO_SIGNOFF`). Nothing
here is an adoption decision under BRD-09. It is a measured operating choice,
and it is reversible by one setting.

---

## Decision

| setting | was | now | where it is enforced |
|---|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:14b` | **`llama3.2:3b`** | `hardware_profile.py` tier `nvidia_high`, `.env`, `.env.example` |
| `OLLAMA_NUM_PREDICT` | *(unset — unbounded)* | **192** | code default in `app/rag.py`; voice answer path only |
| `OLLAMA_NUM_CTX` | 8192 | 8192 (unchanged) | — |
| `OLLAMA_TEMPERATURE` | 0.3 | 0.3 (unchanged) | — |

Reversal: set `OLLAMA_MODEL=qwen2.5:14b`, or drop `OLLAMA_NUM_PREDICT`. Both are
settings, not code paths.

---

## Why the model changed

Screened with `doc/perf/tools/us015_quality_screen.py`, same prompt, same
options (`num_ctx=8192`, `temperature=0.3`), same 83 cases, both models Q4_K_M.

### Verdict-level result is INCONCLUSIVE

| run | qwen2.5:14b | llama3.2:3b | McNemar exact |
|---|---|---|---|
| base | 7/30 | 12/30 | p = 0.125 (7 discordant) |
| + prompt fix | 9/30 | 13/30 | p = 0.219 (6 discordant) |
| + census pass | 10/30 | 14/30 | — |

**53 of 83 cases are `blocked`** — `check_answer` refuses to score a `critical`
case with no `approved_by`. Only 30 cases yield a verdict, and at n=30 a gap of
this size is consistent with chance. A model must not be chosen on this table.

Note also the run-to-run spread at `temperature=0.3`: llama3.2:3b scored 12, 13,
then 14 across three identical-configuration runs. That is the size of the noise
the verdict comparison is working against.

### The defect census DOES separate them

Every case carries mechanical sub-checks — forbidden claims, required facts,
spoken format — which do not depend on the PO approving the *expected
behaviour*. Counting those as **defects** (never as verdicts) uses all 83 cases:

| defect | 14B | 3B | A-only | B-only | McNemar |
|---|---:|---:|---:|---:|---:|
| `format_sentences` | 37 | **16** | 26 | 5 | **p = 0.0002** |
| `format_words` | 15 | **6** | 11 | 2 | **p = 0.0225** |
| `fact_missing` | 28 | 28 | 7 | 7 | p = 1.0000 |
| `forbidden_claim` | 3 | 2 | 1 | 0 | p = 1.0000 |

- **Factual accuracy is identical.** 28 cases each, and they disagree about
  *which* ones exactly as often as chance predicts (7-7, p = 1.00). The earlier
  impression that the 14B "knows more" was an artifact of n=30.
- **The 14B rambles 2.3× as often**, and that difference is real.

The rambling is an appended closing courtesy question — "Is there anything else
you'd like to know…?", "How can I assist you further…?". It is RLHF
helpfulness leaking into a voice agent, it breaks the golden set's
`max_sentences: 2`, and it costs a full extra turn of speech on every answer.

### Hardware and latency

From `us015_model_benchmark.py` and this screen:

| | qwen2.5:14b | llama3.2:3b |
|---|---|---|
| prefill p50 | 604 ms | **146 ms** (4.1×) |
| generation p50 | 990 ms | **184 ms** (5.4×) |
| N=2 batching | PARTIAL (38.6 vs 39.2 tok/s) | **BATCHED** (131.2 tok/s) |
| VRAM | 69% | **26%** |

**Measured, not predicted** — N=2 warm, 100 turns × 2 sessions, 2026-09-19T20:10:44Z:

| | 17:10:42Z | 18:53:50Z | **20:10:44Z** |
|---|---:|---:|---:|
| first-audio p50 | 4,828.5 ms | 4,461.5 ms | **2,953.5 ms** |
| first-audio p95 | 9,866.2 ms | 10,934.0 ms | **8,593.3 ms** |
| turns over 3,000 ms | 198/198 | 146/198 | **95/198** |

p50 **−34%**, p95 **−21%**, over-cap turns 74% → 48%. The run is nevertheless
**DISCARDED**: 95 turns still breach BRD-05's 3,000 ms ceiling, so no latency
claim may be taken from it (a discarded run is not a passing run). The
improvement is real; the acceptance criterion is still not met.

The stage decomposition (`logs/perf_turns.jsonl`, 200 rows, all
`model_used=llama3.2:3b`, `residency_lapse=0`) shows the LLM leg is no longer
the bottleneck:

| stage | before | now (p50) |
|---|---:|---:|
| endpointing (fixed) | 600 ms | 600 ms |
| VAD-end → STT done | 329 ms | 203 ms |
| retrieval | 990 ms | 750 ms |
| LLM queue | 768 ms | **51 ms** |
| prefill | 773 ms | **208 ms** |
| generation | 1,202 ms | **392 ms** |
| TTS synthesis | *(not isolated)* | **790 ms** |
| TTS done → first audio | 23 ms | 16 ms |
| app-side total | — | **2,484 ms** |

LLM leg: **2,743 → 651 ms (−76%)**, as predicted. The remaining time is now
retrieval (750), TTS synthesis (790) and the fixed 600 ms endpointing — the
last three to attack, and none of them is the model.

Caveat on attribution: the model change, `num_predict`, the prompt scoping fix
and the event-loop fixes all went live in one restart, so the LLM-leg reduction
is clearly the model but the STT and retrieval gains cannot be split between the
loop fixes and the freed VRAM (the 14B's 10 GB is no longer resident).

**Do not quote the 2,300 ms figure this document previously predicted.** The
measured p50 is 2,953.5 ms; the arithmetic was optimistic by ~650 ms.

---

## Why `OLLAMA_NUM_PREDICT=192`

Production sent **no output ceiling at all** — generation was bounded only by
the model's own appetite, so the worst case on a live call was not a decision
anybody made. 192 comes from the measured distribution, not from taste:

| cap | 14B truncated | 3B truncated |
|---|---|---|
| 96 | 5/83 | 3/83 |
| 128 | 2/83 | 1/83 |
| **192** | **0/83** | **0/83** |

p95 was 106 tokens (14B) and 82 (3B). A ceiling of 192 truncates **none** of the
83 measured answers; it buys a bounded worst case and changes nothing that was
actually produced.

It is applied to the **voice/RAG answer path only** (`app/rag.py`), never as a
module default in `llm_backend.chat`, because `app/database.py` uses the same
function for lead extraction — structured JSON that a 192-token ceiling would
truncate mid-field.

It is defaulted **in code**, so a rebuild cannot silently remove it. Note that
an `OLLAMA_NUM_PREDICT` line placed inside the `.env` MACHINE PROFILE block will
be stripped by the next `predeploy` run; the code default is what survives.

---

## The prompt fix, and what it did not do

`app/voice_system_prompt.py` stated its only numeric length rule — *"Keep all
turns under 2 sentences"* — inside **"# Call Termination, Noise, and Opt-Out"**,
while **"# Response Length"** said only "prefer short conversational turns". The
golden set enforces `max_sentences: 2` on every turn. The rule existed but was
scoped to ending calls, so a model that respects instruction scope (the 14B) did
not apply it to an ordinary FAQ answer.

A global statement was added to "# Response Length". It moved the 14B from 7 to
9 of 30 and the 3B from 12 to 13 — **consistent in direction, not significant at
this n, and it did not fix the 14B.** The model difference survives the prompt
fix, which is why the fix is not the answer to this question.

The change is a *scoping* correction of a rule the prompt already carried, not a
new requirement, and it was applied identically to both models so the comparison
stays fair.

---

## What this decision does NOT establish

- **Behavioural quality is unmeasured.** All 83 graded cases are
  `expected.kind == "answer"`. The clarify / escalate / refuse / capture-lead
  behaviours — the dimension where a larger model would most plausibly win —
  sit in the 78 cases this grade excludes, and behind `DG-03`. If the PO
  approves the ground truth and the 14B turns out better at those, this decision
  should be revisited.
- **One prompt, one temperature, one machine.** `temperature=0.3` with no fixed
  seed; the run-to-run spread is documented above.
- The **78 ungraded cases** were not scored at all.

## What would overturn it

1. PO signoff on the 137 pending ground truths (`DG-03`), then a re-screen at
   n=161 with approval in place. If the 14B wins the behavioural kinds by a
   margin that survives an exact test, re-open this.
2. A measured turn-latency regression from switching — the prediction above
   (≈4,684 → ≈2,300 ms) is arithmetic, not a measurement. It should be confirmed
   by a load run before it is quoted as a result.

## Reproducing

```bash
.venv/Scripts/python.exe doc/perf/tools/us015_quality_screen.py \
    --models qwen2.5:14b,llama3.2:3b --temperature 0.3 \
    --tag base --out doc/perf/us015-screen-census.json
.venv/Scripts/python.exe doc/perf/tools/us015_pairwise.py \
    doc/perf/us015-screen-census.json --a qwen2.5:14b --b llama3.2:3b
```

Artifacts: `doc/perf/us015-screen-base.json`,
`doc/perf/us015-screen-promptfix.json`, `doc/perf/us015-screen-census.json`.
