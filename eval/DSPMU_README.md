# DSPMU evaluation — draft golden set

This is the DSPMU counterpart to the archived Meridian 161-case set. It is
**draft and not university-approved**. Every critical case is
`PENDING_PO_SIGNOFF` with `approved_by: null`, so the checker reports the
critical intents as **BLOCKED**; no number from this set is adoption-grade.

## Files

| File | What it is |
|---|---|
| `dspmu_golden_set.jsonl` | 103 DSPMU cases, generated from the committed facts and prompt corpus. |
| `dspmu_heldout.txt` | Frozen 20-case held-out split (19.4%; every fifth case in sorted id order). Never tune against it. |
| `dspmu_kb.md` | Combined evaluation KB: core cards, site map, entity corpus, about/glance/vision/syllabus nodes. Used only for deterministic fact traceability. |
| `DSPMU_SIGNOFF_PACKET.md` | Review packet for the 76 pending cases; regenerate after changes. |
| `../scripts/build_dspmu_eval.py` | Regenerates the KB, case set and held-out split. |

## Run

```bash
.venv/bin/python eval/checks.py \
  --golden eval/dspmu_golden_set.jsonl \
  --heldout eval/dspmu_heldout.txt \
  --kb eval/dspmu_kb.md \
  --kb-label dspmu_kb.md
```

Regenerate after a DSPMU fee, date, programme or prompt change:

```bash
.venv/bin/python scripts/build_dspmu_eval.py
```

## Sign-off packet

```bash
.venv/bin/python eval/signoff_packet.py \
  --golden eval/dspmu_golden_set.jsonl \
  --kb eval/dspmu_kb.md \
  --kb-label dspmu_kb.md \
  --packet eval/DSPMU_SIGNOFF_PACKET.md \
  --packet-title "DSPMU admissions — ground-truth sign-off packet"
```

Approval is recorded only with a named `--by`; rejected drafts stay
`PENDING_PO_SIGNOFF` with a `rejection_note` so the checker can still validate
the fixture.

## Current status

- **103 cases**, **20 held out (19.4%)**.
- **27 KB-verified** non-critical fact cases.
- **76 PENDING_PO_SIGNOFF**; this includes every critical intent, every
  behavioural policy case and the notice-tail cases.
- **All 12 critical intents are BLOCKED** until the university/registrar signs
  their facts and policy expectations.
- **24 of 28 intents are underpowered** at the existing 8-case floor. The set is
  wide enough to expose coverage, not deep enough for fine-grained adoption
  decisions.

Covered DSPMU areas include UG programme facts, the full UG fee sheet (including
long-tail programmes), UG counselling and PG dates, documents, portal,
eligibility, contacts, campus, history, vision, notice-tail checks, PG
programme/fee out-of-scope behaviour, and the deterministic boundary cases.

## Sign-off rule

The sign-off tool now accepts `--golden`, `--kb`, `--kb-label` and `--packet`;
the DSPMU packet is generated from those paths. DSPMU sign-off still needs a
university-reviewed decision per critical intent. Until then:

- do not promote a DSPMU case from `verified` to `PO_APPROVED`;
- do not treat a blocked or underpowered intent as a pass;
- do not use this set to justify a production switch.

## Known limits

- Fee/date/eligibility cases are intentionally blocked rather than scored.
- PG programme/fee/seat/eligibility questions expect the deterministic
  university-contact boundary; PG admission dates remain answerable.
- The deterministic stage does not judge paraphrase, tone or factual claims it
  cannot match mechanically; those need the later rubric stage.
- The combined evaluation KB is generated for traceability, not a second
  production corpus.
