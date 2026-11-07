# TPO Action Plan — NGA Chat Stabilization

| Field | Value |
|---|---|
| Owner | TPO (Technical Product Owner) |
| Date | 2026-10-31 |
| Status | Proposed; decisions D1–D3, D5, D8 within TPO/architect authority; D4, D6, D7 require PO sign-off |
| Evidence base | [`06-failure-analysis.md`](06-failure-analysis.md) |
| Release name | NGA Chat Stabilization |
| Release goal | The observed failing conversations produce the right answer, clarification or policy refusal end to end, with no silent fallback and no cached non-answer |

---

## 1. TPO stance

The analysis shows a **contract mismatch cascade**, not a missing retrieval layer. The next release is therefore a stabilization release, not a feature release.

**Decision:** freeze new retrieval work — embeddings, reranking, chunk expansion, further deep-search features and further archive backfill — until the four contracts are fixed and the acceptance suite passes. Existing retrieval work stays behind flags.

**Definition of done:** a capability is done only when it is:

1. wired into the live channel seam;
2. enabled in the target environment;
3. populated (graph, page text, documents, chunks as applicable);
4. verified by the end-to-end acceptance suite;
5. rollback-tested.

“Coded” is not “done.” “Implemented” without E2E evidence is not accepted.

---

## 2. Decisions

| # | Decision | Recommendation | Authority | Deadline |
|---|---|---|---|---|
| D1 | Serving/indexing isolation | Stop the document backfill against the serving SQLite DB; use a copy or a separate store; enable WAL; add a lock/health alarm | TPO + Ops | Day 0 |
| D2 | Cache correctness | Never cache a refusal as `verified`; add `answer_type`, topic, chunk-index version and embedding-model version; purge existing refusal entries | TPO + Architect | Day 0–1 |
| D3 | Evidence adequacy | Add a relevance/sufficiency predicate; deep search triggers on inadequate evidence, not only an empty bundle | TPO + Architect | Day 1–2 |
| D4 | PG/PhD programme scope | Answer programme **lists** from public pages; keep PG fees/seats out of scope; build a programme catalogue separate from the UG fee table | PO sign-off | Day 1 decision; Day 6–8 build |
| D5 | Navigation answerability | Separate `can_navigate(node)` from `can_ground(node)`; `empty_shell` is navigable but not groundable | Architect + TPO | Day 1 decision; Day 6–8 build |
| D6 | Image/JS shell content strategy | No browser in the live path; choose OCR, curated catalogue, old site/PDF/department pages, or explicit URL/contact hand-off per page | PO + Content owner | Day 2 decision |
| D7 | Lead capture placement | Answer knowledge questions first; capture contact only for callback/offer; make it skippable without hijacking short messages | PO + TPO | Day 3–5 |
| D8 | Release gate | No phase is “done” without wired + enabled + populated + E2E pass + rollback test | TPO | Immediate |

### D4 recommendation in detail

The current contradiction is:

- the intent promises “any published detail”;
- the evaluation catalogue includes “Postgraduate programs” and “Doctoral / PhD programs”;
- the programme detector is UG-fee-only and maps PG names to UG canonical names;
- some PG queries are intercepted by the deterministic boundary.

Recommended product position:

- **In scope:** public PG/PhD programme names, departments, duration, eligibility, page/document links.
- **Out of scope:** PG fees, seats and admission cut-offs until a verified PG fact layer exists.
- **UX:** when a PG fee/seat is asked, say it is outside the verified profile and give the registrar contact — do not pretend it was a retrieval miss.
- **Data:** build a programme catalogue with `level` (UG/PG/PhD), `department`, `page_url`, `document_urls`, `verified_at`, `source`. Never derive programme identity from the fee table alone.

### D6 recommendation in detail

The live Academic Programmes, UG/PG, Hostel and Central Library pages are image/JS shells: the server HTML has navigation and images, not the content a browser renders. Chunking and embeddings over the same HTML cannot fix that.

Recommended order:

1. check the old site, department pages and PDFs for the same facts;
2. OCR only the specific published images that contain the fact (bounded, reviewed);
3. if neither is possible, return a labelled hand-off: the published URL, what the page contains, and the contact — not a generic “I could not verify”;
4. keep browser automation out of the live voice/chat path.

---

## 3. Ten-working-day plan

### Day 0–2 — Stop the bleeding

**Work**

- Isolate the serving DB from the backfill; enable WAL; add a readiness health endpoint/report.
- Purge refusal cache entries; deploy the refusal-cache guard; add `answer_type` and index/chunk version to the cache schema.
- Add the relevance/sufficiency gate; change the deep-search trigger to fire on inadequate evidence.
- Fix the PG→UG programme-detector collision (or gate the programme claim behind a catalogue check).
- Stop rendering `blocked` as `no_evidence`; add a reason code to the turn event.
- Stand up the E2E harness with the 15 acceptance cases; publish the baseline.

**Exit criteria**

- Repeated “which programs currently university” does not return a cached refusal.
- Deep search fires when the evidence is irrelevant.
- A legitimate M.Sc. IT answer is not blocked by a B.Sc. IT alias.
- No live DB lock during a concurrent backfill; if the static fallback occurs, it is labelled.
- Baseline metrics recorded (pass/fail per case, false-refusal rate, p95).

### Day 3–5 — Conversation contract

**Work**

- Wire `ConversationMemory.load/plan` into `build_text_answer`; load state by `conversation_id`; pass history to the planner and generator; record an event on every return path.
- Add `history` and `conversation_id` handling to `query_rag_stream`; let the server load history rather than trusting the client.
- Add `CHITCHAT`, `META` and `CLARIFY` intents that do not call retrieval.
- Move the Streamlit profile gate out of the answer path; answer first, capture later, skippable.
- Add the refusal taxonomy (`policy`, `no_machine_readable_source`, `low_confidence`, `ambiguous`, `stale`, `error`).

**Exit criteria**

- “hello”, “who are you”, “thanks”, “can you search deep” and “do you understand when I ask…” are conversational.
- “what about its fee?” after a hostel/grievance topic resolves the topic and opens the node.
- Every turn — including cache hit and deep search — writes a turn event with a reason code.
- No knowledge question is answered with “That’s a bit long for a name.”

### Day 6–8 — Navigation and knowledge

**Work**

- Implement `can_navigate` vs `can_ground`; return shells in search/navigate with `groundable=false`; expose children/links; `site.open` returns `body_available=false`.
- Use the site’s `/search` as a first-class retrieval tool; follow child links/PDFs; dedupe against the graph; write deep-search results back into the index.
- Build programme/department catalogue v0 from department pages, PDFs and facts; label UG/PG/PhD.
- Add a planner list/enumerate path using navigate/documents/follow; make the LLM planner the default for text or add a deterministic department/programme route.
- Switch the verifier’s programme check to the catalogue, not the UG fee aliases.

**Exit criteria**

- “what programmes are offered” and “which programs currently university” return a catalogue-based answer or a clarifying question — never a generic no-verification refusal.
- “list of postgraduate courses” returns a PG list or the explicit PG-fee/seat policy answer.
- `site.navigate("cms.academic-programmes")` returns the node and its children, even though it is a shell.
- The correct department/programme pages are reachable from the query.

### Day 9–10 — Answer quality and release

**Work**

- Add the answer state machine: `answered_verified`, `answered_rewritten`, `clarification`, `refused_policy`, `refused_no_machine_text`, `refused_low_confidence`, `failed_generation`.
- Add a repair loop: one re-prompt with the verifier failure and the cited evidence before refusal.
- Make citations non-fatal when the claim is supported elsewhere in the bundle.
- Run the full acceptance suite in shadow/canary; publish metrics; test rollback.

**Exit criteria**

- 15/15 acceptance cases pass, or each exception has a signed-off known limitation.
- No unsupported critical claim released; no legitimate answer blocked by a detector collision.
- p95 within the channel budget; false-refusal rate and irrelevant-evidence rate published.
- Rollback (flags + cache purge + DB snapshot) tested.

---

## 4. Workstream ownership

| Workstream | Owner | Deliverables |
|---|---|---|
| Scope, sequencing, gates | TPO | Decision log, release gates, go/no-go, stakeholder comms |
| Contracts and architecture | Architect | D1–D8 design decisions, ADRs, cache/state schema, navigation model |
| Retrieval and planner | Backend/retrieval | Adequacy gate, deep-search trigger, site-search tool, shell navigation, list/enumerate plan |
| Programme catalogue and content | Data/content | Programme/department catalogue v0, department/PDF ingestion, image/JS shell strategy |
| Verification and answer quality | Backend/verification | Answer state machine, repair loop, catalogue-based programme check, refusal taxonomy |
| Evaluation and QA | QA/evaluation | E2E harness, golden conversations, nightly run, metrics dashboard |
| Operations | Ops | DB isolation/WAL, readiness, alerts, rollback |
| Product decisions | PO | D4, D6, D7 sign-off; promise wording; university liaison |

---

## 5. Release gates

| Gate | Requirement | Evidence |
|---|---|---|
| G1 Operational | No serving DB lock; no silent fallback; p95 within budget | Readiness report, load test with concurrent backfill |
| G2 Cache | No refusal in positive cache; repeat query re-evaluates; index rebuild invalidates | Cache audit, E2E case #9 |
| G3 Conversation | Meta/small talk and follow-ups work through the live seam | E2E cases #4, #5, #6 |
| G4 Navigation/programme | Programme, hostel and library cases behave per acceptance table | E2E cases #1, #2, #7, #8 |
| G5 Verification | No false block; no unsupported critical claim | E2E cases #11, #12, #14, #15 |
| G6 Evaluation | 15/15 or signed-off exceptions; metrics published | Acceptance report |

The 15 acceptance cases are in [`06-failure-analysis.md`](06-failure-analysis.md) §8.7.

---

## 6. Metrics to publish

- **Grounded answer rate** on the acceptance set.
- **False-refusal rate** (should be zero for in-scope programme questions).
- **Irrelevant-evidence rate** (turns answered from evidence with no query-term coverage).
- **Blocked rate** and block reasons.
- **Cache-hit correctness** (positive answers only; no refusal hits).
- **Follow-up success rate.**
- **p95 latency** for chat and voice.
- **Silent static-fallback count** (target zero without a reason code).
- **Index readiness** (pages with text, documents done/pending, chunks, embeddings).

---

## 7. What we will not do in this release

- No new embedding model, reranker, chunk-size tuning or vector-store work.
- No further archive backfill against the serving DB.
- No new retrieval feature whose only proof is a unit test with a fake LLM.
- No “implemented” status without E2E evidence.
- No promise of “any published detail” until the image/JS shell content strategy is funded; either narrow the promise or fund D6.
- No browser automation in the live chat/voice path.

---

## 8. Risks and escalations

| Risk | Impact | Mitigation | Owner |
|---|---|---|---|
| PG scope undecided | Boundary refusals continue; verifier collisions | D4 decision on Day 1; catalogue v0 by Day 8 | PO + TPO |
| Image/JS shells | Library/hostel/programme questions remain unanswerable from HTML | D6 decision; hand-off message; OCR/curated content | PO + Content |
| Backfill blocks serving | Silent static fallback; inconsistent testing | D1 isolation/WAL; pause backfill | Ops |
| Evaluation ground truth unsigned | Acceptance cannot be authoritative | PO sign-off on the 15-case set and golden facts | PO |
| LLM token budget (192) | Truncated lists/fee tables in chat | Per-channel/per-shape token budget | Architect |
| University permission/legacy access | No old-site fallback for shells | University liaison; document the dependency | PO |
| Cache regression | Bad answer sticks for 24 h | D2 guard + purge + E2E case #9 | Architect |

---

## 9. First 24 hours

1. Announce the stabilization release and feature freeze.
2. Stop or relocate the backfill; snapshot the serving DB; enable WAL; add readiness output.
3. Purge refusal cache entries; deploy the refusal-cache guard.
4. Add the relevance gate; change the deep-search trigger.
5. Fix the PG→UG detector collision; stop rendering `blocked` as no-evidence.
6. Stand up the E2E harness with the 15 cases; publish the baseline.
7. Hold the D4 (PG scope) and D6 (shell content) decision meeting; record decisions in this file.

---

## 10. Decision log

| Date | Decision | Authority | Status |
|---|---|---|---|
| 2026-10-31 | Stabilization release; freeze new retrieval work | TPO | Proposed |
| 2026-10-31 | D1 serving/indexing isolation | TPO + Ops | Proposed |
| 2026-10-31 | D2 refusal-cache guard + schema | TPO + Architect | Proposed |
| 2026-10-31 | D3 adequacy gate + deep-search trigger | TPO + Architect | Proposed |
| 2026-10-31 | D4 PG programme scope | PO | **Awaiting sign-off** |
| 2026-10-31 | D5 shells navigable, not groundable | Architect + TPO | Proposed |
| 2026-10-31 | D6 image/JS shell strategy | PO + Content | **Awaiting sign-off** |
| 2026-10-31 | D7 lead capture after answer | PO + TPO | Proposed |
| 2026-10-31 | D8 E2E release gate | TPO | Proposed |

---

*This plan is the TPO companion to the failure analysis. It does not change production code; it defines the decisions, sequence, owners and gates for the next change.*
