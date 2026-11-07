# RCA — A19 admissions-portal regression

| Field | Value |
|---|---|
| Date | 2026-11-01 |
| Case | A19 — “admission process” |
| Symptom | Three live acceptance runs answered without the portal URL in the answer text |
| Impact | The user-visible answer did not name the official admissions portal; the citation metadata did |
| Status | Fixed, deployed, 3-run acceptance green |

## 1. What happened

A19 requires the answer to contain `jharkhanduniversities.nic.in`. After the
50-document backfill/promotion batch, all three A19 turns failed with:

```
required fact missing: 'jharkhanduniversities.nic.in'
```

The answer cited the portal in its citation list but said only that the
admission process could not be verified. The required-fact check reads the
answer text; a citation-only URL does not satisfy it.

## 2. Immediate fix and why its first test failed

A deterministic `_admission_answer_result()` was added in
`app/agent/text.py`. It built:

```
Admissions at DSPMU are handled through the official portal: <url>.
Eligibility and the exact steps are programme-specific.
For current dates ... contact the registrar: <email/phone>.
```

The unit test used a strict LLM stub that raises on any call. It failed with
`generation_failed` instead of `verified`.

### Causal chain

1. The helper composed the answer from two different sources: the
   `admissions.portal` verified fact in the evidence bundle, and
   `FactStore.contacts()` read directly.
2. The production bundle for a `page_topic` admission query contains the
   portal fact, but **not** the contact fact. Contact evidence is only added
   for `Intent.CONTACT`.
3. `verify_answer()` treats the email and phone as critical claims. With no
   contact evidence in the bundle, it blocked the answer and returned an
   empty text.
4. The helper treated any blocked verification as “not applicable” and
   returned `None`.
5. `build_text_answer()` then fell through to the LLM path. The test’s
   `must_not_call` stub raised; the existing generation wrapper turned that
   into `generation_failed`.

So `generation_failed` was a downstream symptom. The actual defect was an
ungrounded deterministic answer, silently abandoned by the helper.

## 3. Five whys

1. **Why did A19 fail?** The portal URL was not in the answer text.
2. **Why was it omitted?** The answer came from the LLM, which chose to cite
   the portal instead of stating it.
3. **Why was the LLM responsible?** There was no deterministic admission
   answer, despite the portal being a signed-off fact.
4. **Why did the new deterministic answer fail its test?** It added registrar
   contact claims that were not in the bundle; the verifier blocked it and
   the helper returned `None`.
5. **Why was that possible?** The helper composed text from `FactStore`
   directly instead of limiting itself to the bundle it verifies against, and
   the fallback path did not expose a labelled reason.

## 4. Why the regression appeared after the 50-document batch

The new admission documents changed retrieval/LLM context. The answer still
carried the portal citation, but the generated prose stopped restating the
URL. This is exactly the class of critical fact that must not depend on model
phrasing. The earlier passes were a stable-output coincidence, not a
contract.

## 5. Fix

- `_admission_answer_result()` now uses **only** the `admissions.portal`
  evidence item and the bundle. It does not add contacts or any other fact
  that was not assembled for that intent.
- The answer is verified with `verify_answer()` before return.
- New strict test:
  `test_text_agentic_answers_the_admissions_portal_without_an_llm` asserts
  `status=verified`, `answer_type=answer`, and the portal string in the
  answer, with an LLM stub that raises on any call.
- Services restarted on the fixed build.
- A19 live acceptance: **3/3 runs pass**; full tuned set **30/30 cases,
  105/105 turns**.

## 6. Prevention

1. **Builder contract:** every deterministic answer builder must source all
   critical claims from the `EvidenceBundle` it verifies against. Direct
   `FactStore` reads are allowed only if that same fact is inserted into the
   bundle first.
2. **No silent fallback:** a deterministic builder returning `None` must log
   a reason (`not_applicable`, `verification_blocked`, `missing_fact`) and
   tests should assert the reason. `AnswerResult.builder_declines` now records
   it and `TurnEvent.diagnostics` persists it in
   `nga_turn_event.diagnostics`; the acceptance report and dashboard expose
   the counts and reasons.
3. **Acceptance fact placement:** `required` means the fact must be in the
   answer text; `required_urls` means a citation is enough. A spoken/visible
   URL belongs in `required`, as A19 does.
4. **Critical facts are deterministic:** fee, notice, programme and admission
   answers must not depend on the model restating an exact string. Each has a
   strict no-LLM test, and critical acceptance cases assert
   `expect.builder` so a silent model fallback fails the case.
5. **Test the seam, not just the helper:** A19 must run through
   `build_text_answer` with `llm` forbidden, so a helper fallback cannot hide
   behind a generation error.

## 7. Open follow-up

- **Done:** `AnswerResult.builder_declines` now carries
  `{"builder", "reason", "detail"?}` for every applicable deterministic
  builder that declined. `TurnEvent.diagnostics` persists the same list in
  `nga_turn_event.diagnostics` (schema v7), so operators can see a silent
  fallback in the durable turn record.
- **Done:** `tests/test_builder_contract.py` covers the admission, fee,
  notice and programme builders: each answer runs without an LLM, every URL
  it states is present in its evidence bundle, and a forced admission
  decline (`missing_fact` / `admissions.portal`) reaches the turn event.
  The programme builder now verifies through `verify_answer()` against an
  `EvidenceItem` instead of constructing a verified result by hand.
- The full remaining document backfill is running in the background; this RCA
  is independent of that batch.
