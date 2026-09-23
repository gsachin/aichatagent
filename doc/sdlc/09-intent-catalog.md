> **Lens:** BA (catalog) + PO (criticality and ground-truth sign-off) · **Decided by:** BA, criticality confirmed by PO · **Inputs:** `01-brd.md` `BRD-18`, `app/voice_system_prompt.py` (28 behaviour sections), `content/meridian/meridian_knowledge_base.md` (15 domain sections) · **Engagement:** Brownfield · **Referenced by:** `US-003` (golden set), `UC-08` (quality evaluation)

# Intent Catalog — Admissions Voice Assistant

## Why this file exists

`BRD-18` requires that "all 28 supported caller intents remain functional after optimisation". Until now **the set was not defined in any artifact** — it existed only in an analysis conversation. A requirement that references an undefined set cannot be verified, and "all 28 intents work" was partly a documentation claim rather than a checkable one. This file is the authoritative definition.

**Provenance.** The behavioural intents (rows 13-28) are derived from the 28 `#` section headings in `app/voice_system_prompt.py`. The domain intents (rows 1-12) are derived from the 15 `##` sections of the knowledge base. Rows are numbered for reference only — this numbering is **not** an ID space and is not part of the plan's traceability registry.

## The catalog

| # | Intent | Expected behaviour | Critical | RAG | LLM | TTS | CRM | Ground truth source |
|---|---|---|---|---|---|---|---|---|
| 1 | Undergraduate programs | State programmes, duration, eligibility from KB | — | ✓ | ✓ | ✓ | — | KB `## Undergraduate Programs` |
| 2 | Postgraduate programs | State programmes, duration, eligibility from KB | — | ✓ | ✓ | ✓ | — | KB `## Postgraduate Programs` |
| 3 | Doctoral / PhD programs | State programmes, requirements from KB | — | ✓ | ✓ | ✓ | — | KB `## Doctoral Programs (PhD)` |
| 4 | **Fees structure** | Exact figures only from KB; never invent a number | **✓ Critical** | ✓ | ✓ | ✓ | — | KB `## Fees Structure` — **PO sign-off required** |
| 5 | Scholarships / financial aid | Award criteria and percentages from KB | **✓ Critical** | ✓ | ✓ | ✓ | — | KB `## Scholarships & Financial Aid` — **PO sign-off required** |
| 6 | **Important dates / deadlines** | Exact dates from KB | **✓ Critical** | ✓ | ✓ | ✓ | — | KB `## Important Dates` — **PO sign-off required** |
| 7 | **Admission process / eligibility** | Steps and thresholds from KB | **✓ Critical** | ✓ | ✓ | ✓ | — | KB `## Admission Overview` — **PO sign-off required** |
| 8 | Campus / hostel / logistics | Facilities and costs from KB | — | ✓ | ✓ | ✓ | — | KB `## Campus Facilities` |
| 9 | Accreditation / rankings / overview | Institution facts from KB | **✓ Critical** | ✓ | ✓ | ✓ | — | KB `## Achievements & Accreditation` |
| 10 | Leadership / history | Named facts from KB | — | ✓ | ✓ | ✓ | — | KB `## Leadership`, `## History & Milestones` |
| 11 | Contact information | Real contact details from KB | — | ✓ | ✓ | ✓ | — | KB `## Contact Information` |
| 12 | FAQ | Answer from KB FAQ section | — | ✓ | ✓ | ✓ | — | KB `## Frequently Asked Questions` |
| 13 | **Lead capture** | Extract and persist caller details | **✓ Critical** | — | ✓ | — | ✓ | `app/leads/` behaviour |
| 14 | **Escalation / handoff to human** | Offer a counselor; record handoff intent | **✓ Critical** | — | ✓ | ✓ | ✓ | `SM-03`, `UC-04` — **PO sign-off required** |
| 15 | Callback / appointment change | Confirm before acting; record | **✓ Critical** | — | ✓ | ✓ | ✓ | `SM-03` |
| 16 | Outbound call handling | Same behaviour, outbound framing | — | ✓ | ✓ | ✓ | ✓ | `voice_handler.py` direction flag |
| 17 | Opt-out / decline | Accept gracefully in one sentence; no pressure | **✓ Critical** | — | ✓ | ✓ | — | prompt §"Opt-out and acceptance" |
| 18 | Call termination (sign-off) | Short closing, end call; no follow-up questions | **✓ Critical** | — | — | ✓ | — | deterministic path, `CLOSING_REPLY` |
| 19 | Noise / fragment / unintelligible | One short check; never guess intent | — | — | — | ✓ | — | noise ladder, `NOISE_REPLY` |
| 20 | STT error correction / clarification | Disambiguate from context; ask at most once | — | ✓ | ✓ | ✓ | — | prompt §"Clarification and STT Error Correction" |
| 21 | Multiple requests in one turn | Address all important requests | — | ✓ | ✓ | ✓ | — | prompt §"Multiple Requests" |
| 22 | **Interruption / barge-in** | Decided and built: the caller may be heard while the agent speaks, the gate admits speech above the echo, and the interrupting words become the next turn (`US-014` Option 2). Ships behind `BARGE_IN_ENABLED=0`, so with the flag off this row is **explicitly unsupported** — the caller's overlapping speech is discarded — and the prompt says so | **✓ Critical** | — | — | ✓ | — | `US-014` — decided 2026-09-19 (Phase 4a ruling 5); thresholds provisional, adoption gated on `DG-03`; see `doc/perf/us014-echo-guard-tuning.md` |
| 23 | Backchannel ("mm-hm") | Treat as continuation, not a turn | — | — | ✓ | ✓ | — | prompt §"Backchannel Handling" |
| 24 | Caller silence | Prompt appropriately; do not hang up | — | — | ✓ | ✓ | — | prompt §"Caller Silence" |
| 25 | Topic change | Follow the caller's new topic | — | ✓ | ✓ | ✓ | — | prompt §"Topic Changes" |
| 26 | Distressed caller | Emotional register; de-escalate | — | — | ✓ | ✓ | — | prompt §"Emotional Intelligence" |
| 27 | Out-of-scope | Answer helpfully, then return to admissions | — | — | ✓ | ✓ | — | prompt §"Unknown Information" |
| 28 | Consequential action confirmation | Confirm before irreversible action | **✓ Critical** | — | ✓ | ✓ | ✓ | prompt §"Consequential Actions" |

## How this catalog is used

- **`US-003`** (golden set) builds ≥8 cases per intent from this table. The **12** Critical intents get full depth; the remaining 16 get coverage cases.

> **Count correction.** Earlier prose in this file and in `BRD-18` said "eleven". The table marks **twelve** rows Critical (4, 5, 6, 7, 9, 13, 14, 15, 17, 18, 22, 28). The table is authoritative; the count is twelve. The golden set was built against the table and gives all twelve ≥8 cases, so the set is correct either way — but the discrepancy is recorded rather than quietly fixed, because a protected-intent list is not a place for an off-by-one.
- **Critical-intent ground truth is a PO sign-off item.** The six rows marked "**PO sign-off required**" (4, 5, 6, 7, 14, 22) are the ones where a wrong answer is materially harmful — a wrong fee, a wrong deadline, a missed handoff, an unsupported capability. This is `DG-03`'s human dependency, made concrete.
- **`UC-08`** scores per intent, never only in aggregate: a candidate that improves the mean while regressing row 4 or 6 is rejected (`BRD-09`).
- **Row 22 is no longer a known-false capability.** It was: the prompt instructed the agent to yield to interruptions while the platform could not deliver one (`CV-01`). `US-014` has now decided the behaviour *and* the prompt is assembled from the mechanism actually in force, so the two agree in both configurations while the feature is off. Row 22 remains a **PO sign-off item** for its ground truth (`DG-03`), which is a different thing from the capability being false — the golden set's interruption cases stay `PENDING_PO_SIGNOFF` and the intent still reports BLOCKED until that sign-off exists.

## Provenance limits

- Rows 13-28's "expected behaviour" is read from the prompt text and the deterministic short-circuits in `app/voice_handler.py`; it describes **instructed** behaviour, not verified behaviour. Nothing in this catalog has been tested end-to-end — that is precisely what `US-003` exists to establish.
- The KB section names in the "ground truth source" column are `##` headings from a single 11,214-byte file. A KB edit silently changes ground truth; `US-010` (store consolidation) should add a KB version concept so a golden set can be tied to the content it was scored against.
