# `eval/` — the frozen golden set and its deterministic checks (MOD-06, `DAT-08`)

This directory is the engineering half of **`DG-03`** — the only `block` decision in Stage 4 —
delivered against `US-003` and `TRD-23`.

> ## ⚠ The set is NOT frozen and NOT approved
>
> Every ground truth in `golden_set.jsonl` is **drafted, not approved**. The six critical
> intents the intent catalog marks *PO sign-off required* — **fees, scholarships, important
> dates, admission process/eligibility, escalation/handoff, interruption/barge-in** — carry
> `ground_truth_status: "PENDING_PO_SIGNOFF"` and `approved_by: null` on every case.
>
> **The set cannot be frozen, hashed as a baseline or scored against a candidate until the
> Product Owner verifies those ground truths.** `checks.py` therefore reports those intents as
> **BLOCKED** — never as a pass, never as a score of zero (`US-003` AC-2, `BRD-08`, `BRD-09`).
>
> Until that sign-off happens, no number produced from this set is adoption-grade.

## Files

| File | What it is |
|---|---|
| `golden_set.jsonl` | One JSON case per line. Editable by hand, diffable, hashable. |
| `heldout.txt` | The frozen 20% held-out split, one case id per line. **Never used for tuning.** |
| `checks.py` | Deterministic, model-free checks + fixture validation + coverage report. |
| `README.md` | This file. |

`checks.py` is standard-library only and deliberately imports nothing from `app.*` — the same
constraint `MOD-06` imposes on itself, so the evaluator cannot fail because the application is
mid-refactor.

## How to run

```powershell
# validate the fixture, print per-intent coverage, run the checker self-test
.venv/Scripts/python.exe eval/checks.py

# score a candidate's answers (one JSON object per line: {"case_id": ..., "answer": ...})
.venv/Scripts/python.exe eval/checks.py --answers eval/answers.jsonl

# other flags
.venv/Scripts/python.exe eval/checks.py --min-cases 8 --json eval/checks_report.json --no-self-test
```

Exit codes: `0` no problems · `1` fixture error · `2` at least one FAIL · `3` at least one
BLOCKED (ground truth missing or unapproved). **`3` is not an error** — it is the designed
behaviour of `BRD-08`/`DG-03`, and it is the exit code this set reports today for every
critical intent.

There is no model, no network and no judge in this stage. A hosted judge is prohibited
(`AS-03`); the rubric stage that needs a judge is a later stage of the same runner and is not
in this file.

## Case schema

One JSON object per line, immutable once hashed. `US-003`'s LLD mapping is noted in brackets.

| Field | Meaning |
|---|---|
| `case_id` | Stable id. `"<intent-slug>-<nnn>"`; the id is an id space, the catalog's row numbering is **not**. |
| `intent` | The intent **by name**, exactly as `doc/sdlc/09-intent-catalog.md` names it (one of 28). |
| `critical` | True for the rows the catalog table marks Critical. |
| `approved_by` | The PO approver. **`null` on every case in this build** — see the warning above. A critical case with `null` blocks rather than scores (`TAC-7`). |
| `ground_truth_status` | `"verified"` or `"PENDING_PO_SIGNOFF"`. See the policy below. |
| `ground_truth_source` | The KB section (`§ <heading>`) or the code/prompt path the expectation comes from. Checked mechanically for `verified` cases. |
| `split` | `"tuned"` or `"held_out"` — mirrors `heldout.txt`. |
| `utterances` | The turn chain: `[{"role": "caller"\|"agent", "text": ...}]`. The last entry is always the caller turn being answered; `agent` turns record the conversation state the case is scored in. Single-turn cases have one entry. |
| `question` | The final caller utterance (`[expected]` LLD compat). |
| `expected.kind` | `answer` · `clarify` · `escalate` · `refuse` · `capture-lead`. |
| `expected.marker_any` | Acceptable phrasings for the behaviour, when the default vocabulary for `kind` is too narrow. |
| `expected.no_question` | True when the turn must **not** ask anything (sign-offs). |
| `expected.notes` | Why this expectation exists; where the trap is. |
| `required_facts` | What the answer must contain (`[expected.must_contain]`). |
| `forbidden_claims` | Literal phrases that must not appear (`[expected.must_not_contain]`). |
| `forbidden_patterns` | Regexes that must not match — e.g. `"re:\\$\\s?\\d"` catches *any* invented dollar figure. |
| `spoken_format` | `no_markdown`, `no_lists`, `max_sentences`, `max_words`. |
| `tags` | `multi_turn` · `noisy_asr` · `adversarial` · `interruption`. |

### Fact-matching syntax

`required_facts` and `any_of` members are matched against the candidate answer normalised to
lower case, with straight apostrophes, hyphens for every dash form, and thousands separators
removed:

```jsonc
"required_facts": [
  "$18,500",                                        // literal, normalised containment
  {"any_of": ["$60", "$75"]},                       // any one of these
  "re:555[)\\s\\-]*204[\\s\\-]*7890",               // regex, prefixed with "re:"
  {"re": "...", "kb_quote": "+1 (555) 204-7890"}    // regex, with the KB literal it encodes
]
```

The `re:` form is deliberately checked against the **normalised** answer, and `kb_quote` lets a
regex-based fact still be traced to the knowledge base.

## Ground-truth status policy

`verified` is a **narrow, mechanical** category. A case is `verified` only when **both** hold:

1. its intent is **not** one of the six PO sign-off rows, and
2. its expected behaviour is a plain `answer` whose every required fact is a **direct quotation
   from `content/meridian/meridian_knowledge_base.md`**, with no inference, disambiguation,
   correction, refusal, escalation or capture behaviour attached.

Everything else is `PENDING_PO_SIGNOFF` — including, deliberately:

* **all six PO sign-off intents** (fees, scholarships, dates, admission process, escalation,
  interruption);
* every behavioural intent, whose ground truth comes from `app/voice_system_prompt.py`,
  `app/voice_handler.py` or `app/leads/` rather than from the knowledge base;
* every misheard-ASR case, because resolving `"MDA"` → MBA is an inference even when the figure
  it lands on is a KB quotation;
* every refusal, trap and correction case, even where the knowledge base states the correct
  behaviour.

`verified` means **"transcribed from the knowledge base and mechanically checkable"**. It does
**not** mean approved, reviewed, or safe to freeze. Nothing in this directory is approved.

The knowledge base is the only factual source. A drafted ground truth that cannot be pointed at
a KB section is a guess, and is marked `PENDING` rather than promoted. `checks.py` enforces
this in both directions: a `verified` case whose source names no existing KB `##` heading, or
whose required facts do not appear in the knowledge base, is a **fixture error** and the run
refuses to start.

**No fee, date, percentage or programme name in this set was invented.** Where the knowledge
base is silent — the PhD tuition, the minimum aggregate mark, a scholarship deadline, a
meal-plan price — the expected behaviour is the "I don't have that specific information" /
Admissions-Office path, and the case carries a `forbidden_patterns` trap that catches a
fabricated figure.

## The held-out rule

`heldout.txt` is the frozen 20% held-out split (32 of 161 cases; every 5th case id in sorted
order, so the split is reproducible rather than chosen). It is **never used for tuning, prompt
iteration or threshold selection**, and it is scored once at the end (`UC-08` A1, `BRD-09`).
A run that tunes against it invalidates itself for that split. `checks.py` cross-checks the file
against each case's `split` field in both directions and refuses a split outside 15–25%.

## What the checks decide, and what they do not

Deterministic, model-free, and final:

* required facts present, forbidden claims and patterns absent;
* spoken-format compliance — no markdown, headings, tables, code, emoji, lists or enumerations,
  plus the per-case sentence and word limits;
* the behaviour itself: a `clarify` case must actually ask; an `escalate` case must offer a
  human; a `capture-lead` case must request or confirm a contact detail; a `refuse` case must
  take a no-information / acceptance / escalation path; a sign-off must not ask a follow-up
  question.

`--no-self-test` off (the default) also runs a **self-test with no model at all**: every
scoreable case is given a synthesized answer built from its own ground truth (which must pass)
and two mutations of it — a forbidden claim and a markdown list (which must fail). That is the
`UC-08` E2 / `US-003` AC-3 property: a well-formatted but factually wrong answer cannot be
carried by the format check.

Deliberately **not** decided here: factual correctness of a paraphrase the facts list does not
name, tone, and anything requiring judgement. Those are the rubric stage's job, and the
deterministic stage never returns a fourth verdict — uncertainty is `blocked`, not `pass`.

### Known limitations

1. **Numbers written as words fail the deterministic stage.** `$18,500` is matched literally, so
   an answer that says only "eighteen thousand five hundred dollars" fails the fact check even
   though it is correct. The rubric stage is what recognises that; the deterministic stage is
   intentionally strict. (`con-002` accepts the spoken email form as a worked exception.)
2. **`underpowered` is honest, not a bug.** `BRD-09` says an intent too small to support a
   confidence interval may not justify adoption, so `checks.py` reports it. 15 of 28 intents are
   underpowered in this build; all 12 critical ones are at or above the 8-case floor, but at
   n = 8 the worst-case 95% interval half-width is still ±28.5 pt, which detects only gross
   regressions. The set needs to grow before fine-grained comparison is meaningful.
3. **Interruption cases encode an aspiration, not a capability.** `CV-01` discards caller audio,
   so the expected behaviour in `Interruption / barge-in` is unreachable today and the catalog
   records it as such. The five interruption cases exist so the decision `US-014` has to make is
   visible and testable — they are `PENDING_PO_SIGNOFF` and that intent reports BLOCKED.
4. **The criticality count needs a PO/BA ruling.** The intent catalog's table marks **12** rows
   Critical; its own prose (and `BRD-18`) says **eleven**. This set covers every row the table
   marks, so it satisfies either reading, but the artifacts disagree with each other.
5. **The knowledge base is unversioned.** A KB edit silently changes ground truth (`US-003`
   provenance note; `US-010` should add a KB version so a golden set can be tied to the content
   it was scored against). The content hash `checks.py` prints is the set's identity, not the
   KB's.
