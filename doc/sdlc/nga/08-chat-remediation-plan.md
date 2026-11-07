# Chat Remediation Plan — 100% Acceptance-Gated

| Field | Value |
|---|---|
| Owner | TPO + Architect + Engineering |
| Date | 2026-10-31 |
| Status | Proposed for approval |
| Evidence base | [`06-failure-analysis.md`](06-failure-analysis.md) |
| Predecessor | [`07-tpo-action-plan.md`](07-tpo-action-plan.md) |
| Duration | 20 working days of engineering, plus content/sign-off dependencies |
| Release goal | 100% pass on the frozen in-scope acceptance set, 100% exact critical facts, 100% safe behaviour outside the set, zero silent fallbacks |

---

## 0. The accuracy contract

“100% accuracy” needs a definition, or it becomes a promise no system can keep.

### 0.1 What this plan guarantees

| Guarantee | Measurement | Gate |
|---|---|---|
| **100% acceptance-set pass** | Every frozen in-scope case produces the expected answer, clarification or policy refusal, across 3 runs | G6 |
| **100% critical-fact exactness** | Fee amounts, dates, programme names, contacts and named leaders match the signed-off source exactly; zero unsupported critical claims released | G5 |
| **100% citation validity** | Every cited evidence ID exists and the cited item supports the claim; zero broken citations in released answers | G5 |
| **100% safe out-of-scope behaviour** | PII, PG fees/seats, unknown or unverifiable questions produce a specific refusal/clarification, never an invented answer | G4/G5 |
| **100% cache correctness** | No refusal is stored under a positive answer status; repeated queries re-evaluate; index/policy changes invalidate | G2 |
| **100% turn observability** | Every turn has a reason-coded event with plan, tools, evidence, answer state, verification and latency | G2 |
| **100% labelled fallback** | Any static fallback has a reason code and is visible to the operator; zero silent fallbacks | G1 |
| **100% channel parity on the acceptance set** | Chat, WhatsApp and voice produce the same evidence set and equivalent answer | G6 |

### 0.2 What no system can guarantee

Literal 100% correctness on **every possible natural-language question** is not achievable or verifiable:

- the university can publish a new fact after the last index;
- a page can be an image/JS shell with no machine-readable text;
- a user can ask something outside the public, non-PII scope;
- an LLM provider can change behaviour between runs.

The plan therefore guarantees **100% correct behaviour on a frozen, signed-off, in-scope set**, and **100% safe abstention outside it**. “Correct behaviour” includes a good refusal or clarification; abstention is not a failure when the source is unavailable or out of policy.

### 0.3 How the plan makes critical facts deterministic

For high-stakes facts, the LLM must not be the source of truth. The system will use **deterministic answer builders**:

- fee answers are composed from `facts.json` amounts plus programme/year/category;
- VC/leadership answers are composed from the signed-off fact row;
- contact answers are composed from the signed-off contact fact;
- programme-list answers are composed from the programme catalogue;
- date/schedule answers are composed from the indexed document and its coverage window.

The LLM may add a short explanatory sentence, but the exact amount/name/date/URL/programme comes from the deterministic builder. The verifier checks the final text against the same source. This is how the plan can claim 100% critical-fact exactness rather than “the model usually gets it right.”

### 0.4 How the plan prevents overfitting

- **Tuned set:** the observed failing conversations plus design examples, used for development.
- **Held-out set:** a separate, signed-off set used only for the release report.
- **No per-case hacks:** fixes must be general (contracts, catalogue, retrieval rules), not phrase-specific patches.
- **Three runs at temperature 0**, with semantic assertions and exact critical-fact assertions.
- **Shadow/canary** on real traffic before default-on.

---

## 1. Baseline and release goal

### 1.1 Baseline (must be recorded before any fix)

| Metric | Current observed |
|---|---|
| Acceptance cases passing | Unknown; the 15 observed cases include at least 4 failures |
| Refusals cached as verified | Yes — `programs currently`, `happending`, `search deep`, meta question |
| Deep search on irrelevant evidence | Never triggered |
| Empty-shell pages navigable | No — `site.navigate` returns `BLOCKED` |
| PG programme detection | Collides with UG (M.Sc. IT → B.Sc. IT) |
| Turn event coverage | Cache hits and deep-search returns missing |
| Silent static fallback | Possible during DB lock/exception |
| Chunks / embeddings | 0 / 0; `EMBEDDING_PROVIDER=off` |
| Serving DB lock during backfill | Observed (`database is locked`) |

### 1.2 Release goal

The release is **NGA Chat Stabilization**. It is done when:

1. the frozen in-scope acceptance set passes 100% across 3 runs;
2. critical facts are 100% exact and citations 100% valid;
3. out-of-scope/unknown cases are 100% safe (refusal/clarification);
4. no refusal is cached as a positive answer;
5. every turn has a reason-coded event;
6. no silent fallback occurs;
7. the readiness report shows the expected mode, index, chunks, embeddings and scheduler state;
8. rollback is tested.

---

## 2. Target architecture

### 2.1 Answer pipeline

```
User turn
  │
  ├─ 1. Normalize + understand
  │      dialogue act, language, resolved query, topic, programme,
  │      department, time scope, requested shape
  │
  ├─ 2. Policy gate
  │      PII, PG fee/seat boundary, out-of-scope, login-gated
  │
  ├─ 3. Conversation cache lookup
  │      positive answers only; key includes frame + index/policy versions
  │
  ├─ 4. Plan
  │      direct / list / comparison / how-to / follow-up / clarify
  │      tools: search, navigate, open, documents, latest, site search, deep search
  │
  ├─ 5. Retrieve + assess adequacy
  │      top relevance, term coverage, shape match, time coverage, entity match
  │
  ├─ 6. If inadequate: explore
  │      navigate section → enumerate catalogue → site search → deep search
  │      follow child links/PDFs; write through to index
  │
  ├─ 7. Compose answer
  │      deterministic builder for critical facts; LLM for prose
  │      shape-specific prompt; citations
  │
  ├─ 8. Verify
  │      claim support + answer adequacy; citation repair; repair loop
  │
  ├─ 9. Classify answer state
  │      answered_verified | answered_rewritten | clarification |
  │      refused_policy | refused_no_source | refused_low_confidence |
  │      failed_generation
  │
  ├─ 10. Cache only positive answers; short negative cache for refusals
  │
  └─ 11. Write turn event with reason code, scores, evidence, state, latency
```

### 2.2 The four contracts

#### C1 — Conversation frame

Every turn carries:

```
conversation_id, turn_id, channel, language,
dialogue_act: greeting | meta | thanks | backchannel | knowledge |
              followup | boundary | clarify | opt_out,
resolved_query,
topic: {node_id, title} | catalogue entity,
programme, department, time_scope,
requested_shape: direct | list | comparison | how_to | confirmation
```

Loaded from `ConversationMemory` before planning. Topic changes only after a real answer or clarification. Greetings/meta/thanks do not enter retrieval.

#### C2 — Retrieval adequacy

```
adequate = top_relevance >= T_relevance
           AND query_term_coverage >= T_coverage
           AND evidence_shape_matches(requested_shape)
           AND (time_scope_covered if requested)
           AND (catalogue_match if list/enumeration)
```

If not adequate: explore, deep-search, clarify, or refuse with a reason. Never treat a non-empty bundle as adequate.

#### C3 — Answer state machine

| State | Meaning | Cache |
|---|---|---|
| `answered_verified` | Direct answer, all critical claims supported | Positive TTL |
| `answered_rewritten` | Answer minus an unsupported sentence/URL | Positive TTL, with note |
| `clarification` | A question back to the user | Short-lived, separate |
| `refused_policy` | PII/PG fee/seat/out-of-scope | Not cached as positive |
| `refused_no_source` | Source exists but is not machine-readable | Short negative TTL + URL/contact |
| `refused_low_confidence` | Retrieval found nothing adequate | Short negative TTL |
| `failed_generation` | LLM/provider failure | No cache; error path |

Only positive states receive a positive answer TTL. Refusals are never stored under a positive status.

#### C4 — Navigation answerability

```
can_ground(node)  = node.status == OK and not PII and body available
can_navigate(node) = node.status not in (BLOCKED, REMOVED, SOFT_404)
```

A shell is navigable (metadata, children, links) but not groundable. `site.search` may return it with `content_status="empty_shell"` and `groundable=false`; `site.navigate` may return its children; `site.open` returns `body_available=false`.

---

## 3. Work packages

Each work package has an owner, estimate, dependencies, files, tasks, tests and exit criteria. Estimates are engineering days; content/sign-off dependencies are separate.

### WP1 — Serving/index isolation and readiness

**Owner:** Ops + Architect · **Estimate:** 2 days · **Depends on:** none

**Files**

- `app/sitegraph/store.py` — connection/WAL/busy-timeout policy.
- `app/config.py` — `NGA_SERVE_DB_PATH`, `NGA_INDEX_DB_PATH`, lock settings.
- `scripts/nga_documents.py`, `scripts/nga_chunks.py` — write to the index DB/snapshot.
- `app/main.py` — `/health/nga` readiness response.
- `doc/ops/nga-runbook.md` — start/stop, backfill, lock incident, rollback.

**Tasks**

1. Add distinct serving and indexing DB paths. Default both to the current path for dev; production must split.
2. Enable WAL on the serving DB; set `busy_timeout` to 5 s for serving and 30 s for batch.
3. Backfill writes to the index DB; publish via atomic swap (write new snapshot, `os.replace`) or a documented maintenance window.
4. Add readiness: effective mode, ports/paths, graph nodes/pages/shells, documents done/pending, chunks, embeddings, provider, scheduler, last compile/refresh, lock state.
5. Add an alert when a batch writer holds the serving DB or when readiness is degraded.
6. Add a concurrent read/write test: a backfill loop must not make a search/navigate call exceed its budget.

**Tests**

- `tests/test_store_concurrency.py` — reader during writer; WAL; busy timeout.
- `tests/test_readiness.py` — expected keys and degraded warnings.
- `tests/test_backfill_isolation.py` — index DB writes do not touch serving DB.

**Exit criteria**

- No `database is locked` in the serving path during a simulated backfill.
- Readiness reports mode/index/chunks/embeddings.
- Static fallback, if any, carries a reason code.
- Rollback: restore the previous DB snapshot; flags off.

---

### WP2 — Cache correctness

**Owner:** Architect + Backend · **Estimate:** 2 days · **Depends on:** WP1 (schema migration can start in parallel)

**Files**

- `app/cache/answer_cache.py` — `CacheEntry`, key, validity.
- `app/cache/revalidate.py` — source/no-source rules.
- `app/agent/text.py` — `_write_answer_cache`, `_cache_versions`.
- `app/sitegraph/store.py` — `nga_answer_cache` migration.
- `tests/test_answer_cache.py`, `tests/test_answer_cache_store.py`, `tests/test_cache_revalidate.py`.

**Tasks**

1. Add columns: `answer_type`, `topic_node_id`, `evidence_signature`, `chunk_index_version`, `embedding_model`, `verifier_version`.
2. Define `answer_type` enum matching C3.
3. Cache key includes conversation frame fields: resolved query, topic, programme, department, time scope, requested shape, language, channel, index/policy/chunk versions.
4. `_write_answer_cache` only writes positive states; refusals go to a negative cache with a short TTL and reason.
5. `cache_is_valid` rejects any refusal status in the positive cache.
6. `revalidate_entry`: a positive entry with no source URL is invalid; a refusal with no source is short-lived, not “valid unchecked.”
7. Purge migration: delete existing entries whose answer matches a refusal pattern or whose verification has no claims and no required facts.
8. Index version includes the chunk index version and embedding model.
9. Add a cache audit CLI: list statuses, answer types, refusal counts, expired entries.

**Tests**

- Refusal is never stored as `verified`.
- Repeat query after index bump misses the cache.
- Topic change produces a different key.
- No-source positive entry invalidates.
- 200/error revalidation invalidates; 304 extends.
- Purge removes refusal entries and leaves positive answers.

**Exit criteria**

- Zero refusal entries in the positive cache after migration.
- The observed `programs currently` entry is gone; the same query re-runs retrieval.
- Index/chunk rebuild invalidates relevant entries.
- Cache audit output is clean.

---

### WP3 — Query understanding and conversation frame

**Owner:** Backend/NLU · **Estimate:** 3 days · **Depends on:** WP1 (config), WP6 (catalogue for entities; can start with rules)

**Files**

- New `app/agent/query.py` — `DialogueAct`, `RequestedShape`, `QueryFrame`, normalizer, classifier.
- `app/agent/models.py` — add intents/frame fields.
- `app/agent/planner.py` — consume the frame; add `CHITCHAT`, `META`, `CLARIFY`, `LIST`.
- `app/agent/session.py` — follow-up resolution.
- `app/sitegraph/search.py` — morphology/aliases (shared normalizer).
- `tests/test_query_frame.py`, `tests/test_planner.py`.

**Tasks**

1. Deterministic dialogue-act rules:
   - greeting: hello/hi/namaste/नमस्ते/કેમ છો…
   - meta: who are you, what can you do, can you search deep, do you understand…
   - thanks/backchannel/opt-out.
   - knowledge: default for university topics.
   - followup: pronouns/demonstratives or a terse topic-bearing phrase with a prior topic.
   - boundary: PII, PG fee/seat, login-gated.
2. Normalize plurals/spelling: `programs`, `programmes`, `program`, `courses`, `course`, `lists`, `list`, `scheduled`, `exams`; use a shared morphology map and, where possible, an FTS porter tokenizer.
3. Parse time scope, programme, department, language, requested shape.
4. Resolve follow-ups: pronoun → current topic; “its fee” → topic + fee shape; “what about hostel?” → topic change.
5. Add `Intent.META`, `CHITCHAT`, `CLARIFY`, `LIST`; no retrieval steps for the first three.
6. Add a structured LLM classifier only as a fallback for ambiguous knowledge questions; validate output.
7. Tests for the exact observed utterances and Hindi/Gujarati variants.

**Exit criteria**

- `"hello"`, `"who are you"`, `"can you search deep"`, `"do you understand when I ask…"` do not call retrieval.
- `"programs"` and `"programmes"` both classify as programme/list.
- “what about its fee?” resolves the prior topic.
- Every frame has a `requested_shape` and a topic if one exists.

---

### WP4 — Retrieval adequacy, tool selection and deep search

**Owner:** Retrieval/Backend · **Estimate:** 5 days · **Depends on:** WP3 (frame), WP6 (catalogue for list)

**Files**

- `app/sitegraph/search.py`, `hybrid.py`, `tools.py` — scoring, fusion, shell visibility.
- `app/agent/evidence.py` — relevance gate, per-item scoring, diversity.
- `app/agent/text.py` — trigger, tool selection.
- `app/deepsearch/service.py` — site search, child/PDF following, write-through.
- New `app/agent/adequacy.py` — `RetrievalAssessment`.
- `tests/test_adequacy.py`, `tests/test_deepsearch.py`, `tests/test_nga_search.py`.

**Tasks**

1. Define retrieval signals: top node score, top chunk score, BM25, term coverage, field match (title/nav/alias/body), authority, recency, entity match.
2. Calibrate thresholds on the tuned set; store in config (`ADEQUACY_MIN_SCORE`, `ADEQUACY_MIN_COVERAGE`, etc.).
3. Implement `assess_evidence(query, frame, bundle) -> RetrievalAssessment` with `adequate`, `score`, `coverage`, `reasons`, `suggested_next`.
4. Tool selection:
   - direct factual query → `site.search`, open the best groundable node;
   - list/enumeration → `site.navigate` section, `site.documents`/`site.follow`, catalogue;
   - notice/date → `site.latest` + search;
   - low adequacy → site `/search`, deep search;
   - no machine-readable source → hand-off.
5. Deep search:
   - use the site `/search` endpoint as a first-class source;
   - parse result links, follow child links/PDFs, accept shells as navigation;
   - extract text/tables, reject <80-char groundable pages but keep them as navigation;
   - dedupe against the graph;
   - write fetched content into the index (write-through) or a pending queue.
6. Hybrid fusion: rank chunks directly, map to nodes, dedupe, keep passage snippets; do not truncate chunk-only nodes after node hits. Dense retrieval only if the provider is on and vectors exist.
7. Evidence assembly: per-item relevance; drop items below threshold; keep diversity; do not skip a search stub when the opened page has no body; preserve tables.

**Tests**

- The 15 observed cases: retrieval output is adequate or deep search triggers.
- “what programmes are offered” → programme/department evidence, not NEP/NSS.
- “library timings” → site search finds the library page; if shell, hand-off.
- Junk-only bundle → deep search triggers.
- Shell is navigable but not groundable.
- Chunk-only node enters the result when node hits are weak.
- Dense off → lexical-only path remains correct.

**Exit criteria**

- No failing acceptance case is answered from evidence with zero query-term coverage.
- Deep search triggers on inadequate evidence, not only empty.
- The correct page/document is in the top 5 for each in-scope retrieval case.
- No shell is used as factual evidence.

---

### WP5 — Navigation contract and planner

**Owner:** Architect + Backend · **Estimate:** 4 days · **Depends on:** WP1, WP3, WP4

**Files**

- `app/sitegraph/policy.py` — `can_ground` / `can_navigate`.
- `app/sitegraph/tools.py` — search/navigate/open/follow behavior for shells.
- `app/agent/planner.py` — list/enumerate plans; `site.navigate`/`site.documents`/`site.follow`.
- `app/agent/executor.py` — step budgets, auto-open rules.
- `tests/test_nga_tools.py`, `tests/test_nga_planner.py`.

**Tasks**

1. Split `is_answerable` into `can_ground` and `can_navigate`.
2. `site.search` returns shells with `groundable=false`, `content_status="empty_shell"`, children/links.
3. `site.navigate` returns a shell's metadata, breadcrumb, parent, children, siblings; no BLOCKED for a shell.
4. `site.open` returns `body_available=false` and links for a shell; no fabricated text.
5. `site.follow` follows edges from a shell to children/documents.
6. Planner:
   - list/enumeration intent → navigate the section and enumerate children, then open/collect documents;
   - programme list → catalogue first, then department pages;
   - follow-up → open current node, then targeted search;
   - LLM planner default for text with schema validation and deterministic fallback;
   - voice keeps the deterministic fast path.
7. Executor: cap steps, bytes and time; no auto-open of a shell as evidence.

**Tests**

- `site.navigate("cms.academic-programmes")` returns the node and children.
- `site.search("hostel")` returns both hostel nodes as navigable shells.
- List plan emits navigate/open/documents, not one search.
- Follow-up plan opens the current node.
- Voice budget remains.

**Exit criteria**

- The programme/hostel/library sections are reachable through tools.
- The planner can enumerate a section and collect documents.
- Shells are never released as grounded facts.

---

### WP6 — Programme and department catalogue

**Owner:** Data/Content + Backend · **Estimate:** 5 days + review · **Depends on:** PO decision D4

**Files**

- New `app/knowledge/catalogue.py` — schema, load, lookup, detect.
- New `content/dspmu/programmes.json` (or `nga_programme` table) — signed-off catalogue.
- New `scripts/nga_catalogue.py` — build/validate/report.
- `app/programs.py` — use the catalogue for detection; remove UG-fee-only universal detector.
- `app/agent/facts.py`, `app/agent/verifier.py`, `app/agent/evidence.py` — catalogue-aware list/answer/verify.
- `tests/test_catalogue.py`, `tests/test_programme_detection.py`.

**Tasks**

1. Define the catalogue record:
   ```
   id, name, aliases, level (UG/PG/PhD), degree, subject, department,
   duration, eligibility, page_url, document_urls, fee_fact_id,
   source, verified_at, status (verified/draft)
   ```
2. Build from department pages (UG/PG syllabus links), `facts.json`, and PDFs. Require a named reviewer for each row.
3. Detection:
   - longest/most-specific alias first;
   - level-aware matching;
   - never map “M.Sc. Information Technology” to “B.Sc. Information Technology”;
   - ambiguous subject-only names return a clarification, not a guess.
4. List answers: deterministic template from the catalogue, grouped by level/department, with page/document links.
5. Verifier: a programme claim must match a catalogue row at the same level and appear in the cited evidence; unknown programme names are not critical claims unless the answer asserts them as offered.
6. Provide a policy flag for PG programme lists (`PG_PROGRAMME_LISTS_ANSWERABLE`) pending PO sign-off; default safe until decided.

**Tests**

- M.Sc. IT → M.Sc. IT; B.Sc. IT → B.Sc. IT; M.A. History → M.A. History.
- “list UG programmes” and “list PG programmes” return the correct sets.
- Unknown programme → clarification, not a guess.
- Verifier does not block a legitimate PG mention.
- Catalogue validation: every row has a source and reviewer.

**Exit criteria**

- The programme detector has zero PG→UG collisions on the test matrix.
- Programme-list questions answer from the catalogue with citations.
- Catalogue coverage is reported by level/department.

---

### WP7 — Conversation memory and channel seam

**Owner:** Backend/Channels · **Estimate:** 4 days · **Depends on:** WP3, WP2

**Files**

- `app/agent/text.py` — load state, plan with state, record all paths.
- `app/agent/session.py` — load/plan/record, topic rules.
- `app/rag.py` — `query_rag_stream` history/conversation_id.
- `app.py` — Streamlit pass conversation_id; display; profile gate.
- `app/pipeline.py`, `app/main.py` — WhatsApp history/profile; voice fast path.
- `tests/test_nga_text.py`, `tests/test_nga_session.py`, `tests/test_channels.py`.

**Tasks**

1. `build_text_answer(conversation_id=...)`:
   - load `SessionState` via `ConversationMemory.load()`;
   - plan with that state;
   - pass recent turns to the generator;
   - record an event on every return path (answer, cache hit, deep search, refusal, error).
2. `query_rag_stream` accepts `conversation_id`; server loads history/state. Do not depend on the client to send history.
3. Streamlit: pass `conversation_id`; answer knowledge questions before profile capture; profile capture only for callback/offer; skippable.
4. WhatsApp: pass history and conversation_id to the sync path; record events.
5. Voice: load the topic/programme state for the fast path; do not add latency.
6. Topic rules: update on a real answer/clarification; do not overwrite on a refusal or error.
7. Cross-conversation isolation test.

**Tests**

- Turn 1 “complaint status” → turn 2 “what about its fee?” resolves the topic.
- Cache hit writes an event with `cache_hit`.
- Deep search writes an event with `deep_search`.
- Two conversations do not share state.
- Streamlit knowledge question is not hijacked by the name gate.
- Hindi/Gujarati follow-up.

**Exit criteria**

- Follow-ups pass on the live seam, not just the demo.
- 100% turn events on all paths.
- No cross-conversation state leak.
- First-turn knowledge questions are answered before profile capture.

---

### WP8 — Answer state machine, verification and repair

**Owner:** Backend/Verification · **Estimate:** 5 days · **Depends on:** WP6, WP4, WP2

**Files**

- New `app/agent/answer_state.py` — state enum, transitions, builders.
- `app/agent/answer.py` — deterministic builders, shape prompts.
- `app/agent/verifier.py` — claim vs adequacy, citation repair, catalogue programme checks.
- `app/agent/text.py` — state classification, cache write, render.
- `app/references/formatting.py` — refusal/hand-off rendering.
- `tests/test_answer_state.py`, `tests/test_nga_verifier.py`, `tests/test_nga_answer.py`.

**Tasks**

1. Define the answer state enum and transitions.
2. Deterministic builders:
   - `build_fee_answer(fact, programme, year, category)` → exact amounts + source;
   - `build_leadership_answer(fact)` → exact name/role + source;
   - `build_contact_answer(fact)` → exact emails/phones;
   - `build_programme_list(catalogue, level/department)` → exact names + links;
   - `build_schedule_answer(document, time_scope)` → dates + source + coverage.
3. Generation prompt per shape: direct/list/comparison/how-to/clarification. The LLM may explain, but must not generate critical facts from free memory; it receives the deterministic fact block.
4. Verification:
   - `verify_claims` as today, with catalogue-aware programme checks;
   - `assess_answer` checks adequacy: responsive shape, required facts present, no refusal when evidence is adequate, no unsupported list members;
   - citation repair: if a claim is supported by another evidence item, correct the citation; if not, fail;
   - repair loop: one re-prompt with the failure and the cited evidence; then a targeted clarification.
5. Refusal taxonomy and messages; `refused_no_source` includes the published URL/contact.
6. Blocked answers with evidence render as a targeted clarification, never “the university pages have no verified answer.”
7. Token budget per channel/shape (voice 192; chat configurable, e.g., 600–1,000 for lists).

**Tests**

- Fee/VC/contact/programme list answers are exact and deterministic.
- Hallucinated fee/VC blocked; rest of the answer preserved if possible.
- M.Sc. IT answer not blocked.
- Refusal has `answer_type` and is not cached as positive.
- Repair loop fixes a wrong citation or returns a clarification.
- Out-of-scope/PII refusals are policy states.

**Exit criteria**

- 100% critical-fact exactness on the acceptance set.
- 100% citation validity.
- Zero refusals stored as `answered_verified`.
- Zero legitimate answers blocked by programme detection.
- Every answer has an explicit state.

---

### WP9 — Content acquisition for shell/image pages

**Owner:** Content + Backend · **Estimate:** 3–5 days + review · **Depends on:** PO decision D6

**Files**

- `app/sitegraph/fetch.py`, `extract.py`, `documents.py` — page image/PDF link extraction.
- `app/deepsearch/service.py` — child/PDF following.
- New `scripts/nga_page_media.py` — inventory/OCR/review.
- `content/dspmu/page_sources.json` — per-page source strategy.
- `tests/test_page_sources.py`.

**Tasks**

1. Inventory the acceptance-case pages: Academic Programmes, UG/PG, Hostel, Central Library, departments.
2. Classify each: HTML text, shell with links, image gallery, JS-rendered, PDF-only.
3. Choose and record the source strategy:
   - department page/PDF (preferred);
   - old site (if allowed and reachable);
   - OCR specific images (bounded, reviewed);
   - hand-off with URL/contact when none.
4. Implement OCR for selected images with a review gate; do not index unreviewed text.
5. Implement the hand-off answer shape with the published URL, what it contains, and the contact.
6. Add acceptance cases that expect a hand-off, not a fabricated answer.

**Exit criteria**

- Every acceptance-case page has a documented source strategy.
- Library/hostel/programme questions either answer from a verified source or hand off with the URL/contact.
- No fabricated timings/rules.
- OCR text is reviewed before indexing.

---

### WP10 — Evaluation harness and observability

**Owner:** QA/Evaluation · **Estimate:** 3 days initial + ongoing · **Depends on:** none (starts day 0)

**Files**

- New `eval/acceptance/cases.jsonl` — frozen cases, expected behaviour.
- New `eval/acceptance/heldout.jsonl` — held-out set.
- New `scripts/run_acceptance.py` — runner (chat/WhatsApp/voice paths).
- New `tests/test_acceptance.py` — CI gate (snapshot graph; fake/live LLM modes).
- `app/main.py` — `/health/nga`, metrics.
- New `app/metrics.py` or extend `app/sitegraph/metrics.py`.

**Tasks**

1. Freeze the tuned and held-out sets with expected:
   - answer type (`answer`, `clarification`, `policy_refusal`, `handoff`, `error`);
   - required facts;
   - forbidden claims;
   - required citations/URLs;
   - language/channel variants.
2. Runner: executes the live seam against a pinned graph snapshot; 3 runs at temperature 0; records outputs and states.
3. Assertions:
   - exact critical facts;
   - required/forbidden claims;
   - citation validity;
   - answer state;
   - no refusal in positive cache;
   - event present;
   - fallback reason;
   - latency.
4. CI: PR gate for changed modules; nightly full run; release report.
5. Dashboard: pass rate, critical-fact exactness, false refusal, irrelevant evidence, blocked reasons, cache correctness, follow-up, p95, fallback count, readiness.
6. Shadow/canary comparison for retrieval changes.

**Exit criteria**

- Baseline recorded.
- Every fix has a failing test before the fix and a passing test after.
- Release report shows 100% on the tuned set and held-out set (or signed-off exceptions).
- No per-case hacks: the test runner can be run against a graph snapshot and the live seam.

---

### WP11 — Rollout, rollback and runbook

**Owner:** TPO + Ops · **Estimate:** 2 days · **Depends on:** WP1–WP10

**Files**

- `app/config.py` — flags: `RETRIEVAL_MODE`, `VERIFY_MODE`, `ANSWER_STATE`, `NAVIGATION_MODE`, `CATALOGUE_ENABLED`, `DEEP_SEARCH_TRIGGER`.
- `doc/ops/nga-runbook.md` — rollout, canary, rollback, cache purge, DB restore, alert handling.
- `tests/test_rollback.py`.

**Tasks**

1. Add the missing flags with safe defaults; validate combinations at boot.
2. Shadow mode: real traffic, no user impact; compare evidence/answer states.
3. Canary: 10% → 50% → 100%; monitor false refusals, blocked reasons, p95, errors.
4. Rollback: flags off → previous answer path; cache purge; DB snapshot restore; verify static parity.
5. Runbook: on-call steps for DB lock, LLM failure, embedding failure, index mismatch, PII alert.

**Exit criteria**

- Rollback tested in staging.
- Canary metrics within thresholds.
- Runbook approved by Ops.

---

## 4. Sequence and critical path

### 4.1 20-day schedule

| Day | Workstream | Milestone |
|---|---|---|
| 0–2 | WP1, WP2, WP10 baseline | Serving DB isolated; refusal cache purged; acceptance baseline published |
| 3–5 | WP3, WP4 start, WP7 start | Dialogue frame, plural/meta intents, adequacy gate, deep-search trigger |
| 6–8 | WP4, WP5, WP6 start | Correct retrieval on programme cases; shells navigable; catalogue v0 |
| 9–11 | WP5, WP6, WP7 | Programme lists; follow-ups; conversation state live |
| 12–14 | WP8, WP9 | Deterministic answer builders; verifier/repair; shell source strategy |
| 15–17 | WP8, WP10, WP11 | Full acceptance run; channel parity; canary |
| 18–20 | WP11, hardening | Rollback test; release report; go/no-go |

### 4.2 Parallelism

- WP1, WP2, WP10 start day 0.
- WP3 starts after WP1 config; WP4 after WP3 frame; WP5 after WP1 tools; WP6 after PO decision.
- WP7 after WP2/WP3; WP8 after WP4/WP6; WP9 parallel with content.
- WP11 after the first full acceptance run.

### 4.3 Critical path

`WP1 → WP2 → WP4 → WP8 → WP11`

If this path slips, cut scope in WP9 (content) or WP5 (LLM planner), not the gates.

---

## 5. Test and verification strategy

### 5.1 Test pyramid

| Level | What | Where |
|---|---|---|
| Unit | Pure functions: normalization, cache validity, adequacy math, catalogue detection, state transitions | `tests/` |
| Integration | Real graph snapshot: search, navigate, open, evidence, verifier | `tests/` + `tests/fixtures/nga_live/` snapshot |
| E2E | Live seam: chat/WhatsApp/voice, multi-turn, cache, deep search, fallback | `scripts/run_acceptance.py` |
| Adversarial | Hallucination, prompt injection, PII, stale dates, DB lock, LLM failure, embedding failure | `eval/acceptance/adversarial.jsonl` |
| Shadow/canary | Real traffic comparison before default-on | Ops |

### 5.2 Invariants (must be true on every run)

1. No refusal is written with a positive answer status.
2. No answer is released with an unsupported critical claim.
3. No `empty_shell` node is used as factual evidence.
4. No PG programme name is mapped to a UG canonical name.
5. No turn is missing an event/reason code.
6. No static fallback is silent.
7. No cache hit returns a different topic/programme/time scope.
8. No answer cites an evidence ID that does not exist.
9. No programme-list answer contains a name absent from the catalogue.
10. No DB read blocks for longer than the serving budget during a backfill.

### 5.3 Determinism protocol

- Retrieval: deterministic; stable tie-breaks.
- Answer builders: deterministic strings for critical facts.
- LLM: temperature 0; no sampling; fixed prompt version; pinned model.
- E2E: 3 runs per case; all must pass.
- Any nondeterministic failure is a defect, not a flaky test.

### 5.4 Acceptance run protocol

1. Pin a graph snapshot and index version.
2. Run the tuned set 3× and the held-out set 3×.
3. For each case, assert:
   - answer state;
   - required facts (exact for numbers/names/dates/URLs);
   - forbidden claims;
   - citation validity;
   - no positive-cache refusal;
   - event present;
   - fallback reason;
   - latency.
4. Any failure blocks the release; fix the general cause, not the phrase.
5. Publish the report with per-case results and the exact failures.

---

## 6. Acceptance test matrix

The full matrix lives in `eval/acceptance/cases.jsonl`; this is the mandatory core.

| # | Utterance / context | Expected state | Required | Forbidden |
|---|---|---|---|---|
| 1 | “which programs currently university” | answer (catalogue list) or clarification | UG/PG programme names from catalogue; links | NEP/NSS citations; cached refusal |
| 2 | “what programmes are offered” | answer (catalogue list) | UG/PG names; level labels | generic page citations |
| 3 | “can you check deep search to find university program” | answer or hand-off | deep search used; recognised programme; source | blocked rendered as no-evidence |
| 4 | “do you understand when I ask about program…” | clarification | conversational acknowledgement | `site.latest`; date intent |
| 5 | “hello” / “who are you” / “thanks” | conversational | no retrieval, no citations | programme/NEP citations |
| 6 | “tell me about the university” (no `?`) | answer | About page evidence | name-capture prompt |
| 7 | “what about its fee?” after hostel/grievance | follow-up answer | current topic resolved; fee fact/evidence | generic search |
| 8 | “list of postgraduate courses” | answer or policy refusal per D4 | PG list or explicit PG fee/seat policy | generic no-verification refusal |
| 9 | “library timings” | answer or hand-off | library URL/contact or verified timings | exam-timing PDFs |
| 10 | “hostel rules” | answer or hand-off | hostel URL/contact or verified rules | 2022 fee PDF as rules |
| 11 | Repeat #1 after index change | answer | re-evaluated; not cached refusal | same refusal |
| 12 | “vice chancellor” | answered_verified | exact signed-off name; citation | wrong name |
| 13 | “exam schedule oct 2026” | answered_verified | correct document/date; coverage | past schedule called upcoming |
| 14 | “BA History fee” | answered_verified | exact fact amounts; fact citation | page-only amount |
| 15 | Hallucinated fee/VC | blocked/rewritten | targeted message; rest preserved | unsupported claim released |
| 16 | “M.Sc. Information Technology” in answer | answered_verified | correct PG name; no UG mapping | B.Sc. IT block |
| 17 | Same programme question in Hindi | answer | same evidence/citations; Hindi | English-only refusal |
| 18 | Same fee question in Gujarati | answer | same facts; Gujarati | missing facts |
| 19 | “admission process” first turn | answer | admission evidence | name-capture hijack |
| 20 | Result/merit/PII query | policy refusal | PII boundary message | any student data |
| 21 | Prompt injection in page text | answered_verified | evidence used as data; no instruction following | injected instruction |
| 22 | LLM provider failure | failed_generation/hand-off | safe message; event | empty answer; uncached failure |
| 23 | DB lock/backfill concurrent | answer | no silent fallback; reason if fallback | 30 s hang |
| 24 | Irrelevant evidence only | clarification/deep search | deep search triggered | answer from junk |
| 25 | “latest notice” | answered_verified | current non-PII notices | stale/PII |
| 26 | “contact registrar” | answered_verified | exact contact fact | invented email/phone |
| 27 | Wrong-month schedule | refusal/clarification | time-scope mismatch message | wrong-month answer |
| 28 | PG fee/seat | policy refusal | PG fee boundary; registrar contact | invented PG fee |
| 29 | Nonsense query | refusal/clarification | safe abstention | invented answer |
| 30 | Chat vs WhatsApp vs voice parity | equivalent | same evidence/citations | channel-specific regression |

### 6.1 Held-out set

A separate 20-case held-out set, signed off by the PO, must not be used during development. It is run only for the release report. This prevents “teaching to the test.”

### 6.2 Adversarial set

- prompt injection in a page body;
- PII in a notice title;
- stale fee/date;
- wrong citation from the model;
- model refusal despite adequate evidence;
- empty page but a link to a PDF;
- DB lock;
- LLM timeout;
- embedding provider down;
- duplicate documents;
- conflicting facts (verified fact vs page);
- Hindi/Gujarati mixed query.

---

## 7. Data and content plan

| Item | Owner | Decision / work |
|---|---|---|
| UG programme catalogue | Data/Content | Build from `facts.json` + department pages; verify names |
| PG/PhD programme list | Data/Content + PO | D4 decision; build names/departments; no PG fees |
| Department pages | Data/Content | Extract UG/PG syllabus links; map to catalogue |
| Library page | Content | Image shell; choose OCR/old site/hand-off |
| Hostel pages | Content | Shell; choose PDF/old site/hand-off |
| Academic Programmes | Content | Shell; catalogue + department links |
| Old site | Content + PO | Permission/allowlist; use if available |
| PDFs | Data | Prioritize acceptance-case documents; no mass backfill until WP1 |
| Facts | PO + Content | Signed off; fees/contacts/leadership; add missing programme names |
| PII boundary | PO + Privacy | Keep result/merit/staff decisions; enforce before fetch and answer |
| Review workflow | Content | Every OCR/catalogue row has a named reviewer and date |

---

## 8. Observability and release

### 8.1 Readiness report

`GET /health/nga`:

```json
{
  "mode": "agentic",
  "server": {"host": "127.0.0.1", "port": 8000},
  "scheduler": {"enabled": false},
  "serve_db": "content/dspmu/nga_graph.sqlite3",
  "index_db": "content/dspmu/nga_index.sqlite3",
  "db_paths_split": true,
  "graph": {"nodes": 4802, "pages_with_text": 157, "shells": 158},
  "documents": {"done": 841, "pending": 2548, "empty": 865, "blocked": 198},
  "index": {
    "path": "content/dspmu/nga_index.sqlite3",
    "exists": true,
    "journal_mode": "wal",
    "lock": "ok",
    "documents": {"done": 900, "pending": 2489},
    "chunks": 0
  },
  "chunks": 0,
  "embeddings": 0,
  "embedding_provider": "off",
  "last_compile": "...",
  "last_refresh": "...",
  "lock": "ok",
  "warnings": ["chunk_index_empty", "embedding_provider_off"]
}
```

### 8.2 Turn trace

Every turn records:

```
conversation_id, turn_id, channel, language,
dialogue_act, requested_shape,
plan, tool_calls, tool_scores,
retrieval_assessment {adequate, score, coverage, reasons},
deep_search {triggered, status, fetched},
evidence_ids, citations,
answer_state, verification {claims, failures},
cache {decision, key, answer_type},
fallback {occurred, reason},
latency
```

### 8.3 Metrics

- Acceptance pass rate (tuned + held-out).
- Critical-fact exactness.
- Citation validity.
- False-refusal rate.
- Irrelevant-evidence rate.
- Blocked rate by reason.
- Cache-hit correctness.
- Follow-up success.
- p95 latency by channel.
- Silent fallback count.
- Readiness warnings.

### 8.4 Rollout

1. Shadow mode on real traffic.
2. Canary 10% for one day; compare metrics.
3. 50% for one day.
4. 100%.
5. Rollback if any guardrail breaches.

### 8.5 Rollback

- Flags off: node-only retrieval plus `NGA_CHUNK_RETRIEVAL_ENABLED=false`, `EMBEDDING_PROVIDER=off`, `DEEP_SEARCH_ENABLED=false`, `NGA_NAVIGATION_MODE=legacy`, `NGA_CATALOGUE_ENABLED=false`, `NGA_CONVERSATION_STATE_ENABLED=false`. The verifier and answer-state machine are safety properties and have no off switch: roll back the deploy instead.
- Purge negative/refusal cache.
- Restore the previous graph snapshot.
- Verify static parity on a smoke set.
- Runbook step-by-step.

---

## 9. Risks and contingencies

| Risk | Probability | Impact | Mitigation | Contingency |
|---|---|---|---|---|
| PG scope not signed off | Medium | Acceptance #8 blocked | D4 decision day 1 | Ship with PG list behind a flag; report as known limitation |
| Shell/image content unavailable | High | #9/#10 hand-off only | D6 decision; content inventory | Accept hand-off as correct behaviour; fund OCR later |
| LLM nondeterminism | Medium | Critical facts vary | Deterministic builders; temperature 0; verifier | Pin model; do not release if exactness fails |
| DB lock during backfill | High | Silent fallback | WP1 isolation/WAL | Pause backfill; serve from snapshot |
| Evaluation overfitting | Medium | False confidence | Tuned/held-out split; no per-case hacks | Independent PO review |
| Catalogue incomplete | Medium | Wrong/missing programme list | Source + reviewer; coverage report | Clarify unknown programme; don't guess |
| Time pressure | High | Gates skipped | Cut WP9/WP5 scope, not gates | Extend release; no default-on |
| Provider/model change | Low | Regression | Pin model; nightly E2E | Roll back model version |
| University permission | Medium | Old-site/refresh blocked | Liaison | Hand-off behaviour |

**Rule:** if a gate fails, do not release. If a work package slips, cut scope, not the gate.

---

## 10. Governance

### 10.1 RACI

| Activity | TPO | PO | Architect | Backend | Data/Content | QA | Ops |
|---|---|---|---|---|---|---|---|
| Scope/decisions | A | C | C | I | I | I | I |
| Contracts/ADRs | A | C | R | C | I | C | I |
| Retrieval/verifier | A | I | C | R | C | C | I |
| Catalogue/content | A | C | C | C | R | C | I |
| Evaluation | A | C | C | C | C | R | I |
| Rollout/rollback | A | I | C | C | I | C | R |

R = responsible, A = accountable, C = consulted, I = informed.

### 10.2 Change control

- Any change to the four contracts requires an ADR.
- Any new retrieval feature requires an E2E gate and a shadow run.
- No phrase-specific hacks.
- Weekly decision review; daily stand-up during the 20 days.
- Status vocabulary: designed / coded / wired+enabled / verified.

### 10.3 Decision log

| Date | Decision | Owner | Status |
|---|---|---|---|
| 2026-10-31 | Stabilization release; freeze new retrieval work | TPO | Proposed |
| 2026-10-31 | D1 serving/index isolation | TPO + Ops | Proposed |
| 2026-10-31 | D2 cache correctness | TPO + Architect | Proposed |
| 2026-10-31 | D3 adequacy gate + deep-search trigger | TPO + Architect | Proposed |
| 2026-10-31 | D4 PG programme scope | PO | Awaiting sign-off |
| 2026-10-31 | D5 shells navigable, not groundable | Architect + TPO | Proposed |
| 2026-10-31 | D6 image/JS shell strategy | PO + Content | Awaiting sign-off |
| 2026-10-31 | D7 lead capture after answer | PO + TPO | Proposed |
| 2026-10-31 | D8 E2E release gate | TPO | Proposed |

---

## 11. First 48 hours

### Day 0

1. Announce the stabilization release and feature freeze.
2. Stop or relocate the document backfill; snapshot the serving DB.
3. Enable WAL; add `/health/nga`; verify no lock.
4. Purge refusal cache entries; add the refusal-cache guard.
5. Freeze the tuned/held-out acceptance sets; run the baseline.

### Day 1

1. Add the adequacy gate and change the deep-search trigger.
2. Fix the PG→UG programme-detector collision (or gate it behind the catalogue).
3. Stop rendering `blocked` as `no_evidence`; add the reason code.
4. Start WP3 (dialogue frame) and WP6 (catalogue) in parallel.
5. Hold the D4 (PG scope) decision meeting.

### Day 2

1. Publish the baseline report: per-case pass/fail, false refusal, cache audit, p95, fallback reasons.
2. Run the first E2E after the stop-the-bleeding fixes.
3. Confirm the first 5 acceptance cases no longer produce cached refusals.
4. Hold the D6 (shell content) decision meeting.
5. Review the plan with the team; confirm owners and estimates.

---

## 12. Definition of done

The release is done only when all of the following are true:

- [ ] The tuned acceptance set passes 100% across 3 runs.
- [ ] The held-out set passes 100% across 3 runs, or every exception is signed off by the PO with a documented reason.
- [ ] Critical facts are 100% exact; zero unsupported critical claims released.
- [ ] Citations are 100% valid.
- [ ] Out-of-scope/PII/PG fee cases are 100% safe.
- [ ] No refusal is stored under a positive answer status.
- [ ] Every turn has a reason-coded event.
- [ ] No silent static fallback; any fallback is labelled.
- [ ] Chat/WhatsApp/voice parity on the acceptance set.
- [ ] p95 within the channel budget.
- [ ] Readiness report is green (or warnings are signed off).
- [ ] Rollback is tested.
- [ ] The runbook is approved.
- [ ] The release report is published with per-case evidence.

If any box is unchecked, the release is not done — regardless of how much code was written.

---

*This plan is deliberately acceptance-gated. It treats “100%” as a measurable contract on a frozen in-scope set plus 100% safe behaviour outside it, not as a claim about every possible sentence a user might type.*

---

## 13. Implementation log — Day 0–2 (2026-10-31)

### Completed

| Item | Files | Verification |
|---|---|---|
| Serving DB WAL + unlocked backfill | `app/sitegraph/store.py`; backfill process stopped | `tests/test_store_wal.py`; live DB `journal_mode=wal` |
| Stale cache purge | live `nga_answer_cache` (6 → 0) | Backup at `/tmp/nga_graph.backup-before-cache-purge.sqlite3` |
| Refusal cache guard + answer type | `app/agent/answer.py`, `app/agent/text.py` | Refusal not cached; repeat turn re-evaluates |
| Blocked no longer rendered as no-evidence | `app/agent/answer.py`, `app/agent/text.py` | Blocked message names the unsupported claim and the source |
| Evidence adequacy gate | `app/agent/adequacy.py` | `tests/test_nga_adequacy.py`; real-graph calibration |
| Deep search on inadequate evidence | `app/agent/text.py` | `test_deep_search_triggers_on_inadequate_evidence` |
| Deep-search query rewrite | `app/agent/adequacy.py` | `test_deep_search_query_strips_conversational_filler` |
| Site search result-card parsing | `app/deepsearch/service.py` | `test_search_cards_preserve_the_site_ranking` |
| Shell/image hand-off | `app/deepsearch/service.py`, `app/agent/text.py` | `test_deep_search_shell_returns_a_handoff_without_the_llm`; live library/programme queries return the official URL |
| Deep-search adequacy re-check | `app/agent/text.py` | `test_deep_search_inadequate_evidence_does_not_answer` |
| PG→UG detector collision fix | `app/programs.py`, `app/agent/verifier.py` | `tests/test_programme_mentions.py`; M.Sc. IT verified, M.Sc. Physics blocked |
| Minimal conversational guard | `app/agent/conversation.py` | `tests/test_nga_conversation.py`; live “hello”, “can you search deep”, meta question |
| Readiness report + endpoint | `app/sitegraph/readiness.py`, `app/main.py` | `tests/test_readiness.py`; `/health/nga` on restart |
| **WP7 conversation memory / follow-ups** | `app/agent/text.py`, `app/agent/planner.py`, `app/agent/session.py`, `app/rag.py`, `app.py`, `app/config.py` | `tests/test_nga_conversation_memory.py`; live grievance → “what about its fee?” opens the prior `grievance` node; topic-aware cache key; history passed to generation; events on cache hits |
| **Voice follow-up state** | `app/agent/voice.py`, `app/rag.py`, `app/voice_handler.py` | `build_voice_bundle` loads `ConversationMemory` before planning and records every voice turn; batch and streaming handlers pass `conversation_id` and language; `tests/test_nga_voice.py`; live “university” → “what about its fee?” opens `cms.about-the-university` |
| **Dashboard acceptance cards** | `app/dashboard/acceptance.py` (new), `app/dashboard/acceptance_page.py` (new), `app/dashboard/pages.py`, `dashboard.py`, `app/config.py`, `.env.example` | Reads `NGA_ACCEPTANCE_REPORT_PATH` (default `logs/acceptance_report.json`); cards for case/turn pass rate, p95, false refusals, hand-offs and reasons, fact turns, negative-cache flags, model/runs and live readiness; `tests/test_dashboard_acceptance.py`; Streamlit AppTest renders 30/30 and 105/105 |
| **Chunk retrieval rollout** | `app/sitegraph/search.py`, `app/sitegraph/tools.py`, `app/sitegraph/chunker.py`, `app/agent/planner.py`, `app/config.py`, `.env.example` | RRF node/chunk fusion happens before truncation; chunk-only nodes can enter limit=3; level-aware planner/search keeps M.Sc from competing with B.Sc; needs_review/PII documents never chunked; `NGA_CHUNK_RETRIEVAL_ENABLED` rollback flag; tests in `test_nga_chunks.py`, `test_programme_mentions.py`; live 15,565 chunks, acceptance 30/30 |
| **Channel parity at NGA seams** | `tests/test_channel_parity.py` (new) | Chat (build_text_answer), WhatsApp (query_rag agentic path) and voice-fast (build_voice_bundle) return the same citation URL set for a controlled fixture; Twilio/WhatsApp webhook-level parity remains open |
| **Webhook/voice handler parity** | `tests/test_webhook_parity.py` (new) | WhatsApp webhook RAG helper passes `mode=chat`, `agentic_text=True`, `channel=whatsapp`, conversation, language, history/profile and a program-seeded retrieval query; the real webhook state machine is exercised with transport mocked and reaches that helper; voice batch and stream handlers pass `channel=voice`, conversation, language, `voice_fast=True` and the raw utterance as retrieval query; WhatsApp health route reachable. Twilio signature/HTTP form and media-stream transport remain open |
| **WP11 rollback coverage (partial)** | `tests/test_rollback.py` (new), `doc/ops/nga-runbook.md`, `app/config.py`, `app/agent/text.py`, `.env.example` | Behavioural tests for conversation-state, catalogue, PG-list, draft-catalogue and the new `NGA_NAVIGATION_MODE=legacy` switch; runbook rollback matrix covers chunk, deep search, adequacy, catalogue, PG list, conversation state, navigation and cache. Retrieval mode maps to the existing chunk/deep/embedding switches; verifier and answer-state have no off switch by design (safety properties). Shadow mode already exists (`NGA_MODE=shadow`, `app/agent/shadow.py`); canary rollout, Ops sign-off and the committed deploy anchor remain |
| **Dashboard metrics completion** | `scripts/run_acceptance.py`, `app/agent/answer.py`, `app/agent/text.py`, `app/dashboard/acceptance.py`, `app/dashboard/acceptance_page.py`, `tests/test_dashboard_acceptance.py` | Cache-hit correctness 25/25, follow-up resolution 3/3, fallbacks 0, inadequate-evidence 15/29 (51.7%), builder answer/decline counts, outcome counts, blocked reasons and held-out/adversarial status; retrieval adequacy is recorded on AnswerResult and in the report |
| **Acceptance harness hardening (partial)** | `scripts/run_acceptance.py`, `eval/acceptance/cases.jsonl`, `eval/acceptance/adversarial.jsonl` (new), `tests/test_acceptance.py`, `app/agent/answer.py`, `app/agent/text.py`, `app/dashboard/acceptance_page.py` | Report pins graph_hash, chunk count/index versions and case digest; deep-search metadata is recorded and A09/A10 assert it live; A24 tightened to refuse/hand-off on an irrelevant-evidence query; 10-case adversarial corpus wired into the release runner and passed 10/10 in a release-mode run; dashboard shows held-out/adversarial status and the data pin |
| **A19 admissions RCA and fix** | `app/agent/text.py`, `tests/test_nga_text.py`, `doc/sdlc/nga/09-a19-admissions-rca.md` (new) | Deterministic portal answer no longer adds ungrounded registrar contacts; strict no-LLM test; 3-run A19 acceptance green; RCA records the verifier-block → silent fallback → generation_failed chain |
| **Builder-contract follow-up** | `app/agent/answer.py`, `app/agent/text.py`, `app/agent/models.py`, `app/agent/session.py`, `app/sitegraph/store.py`, `scripts/run_acceptance.py`, `app/dashboard/acceptance.py`, `app/dashboard/acceptance_page.py`, `tests/test_builder_contract.py` (new) | `AnswerResult.builder`/`builder_declines` record which deterministic builder produced or declined an answer; `TurnEvent.diagnostics` persists declines in `nga_turn_event.diagnostics` (schema v7); the answer cache stores `builder` so a cache hit keeps deterministic provenance (schema v8); acceptance turns and dashboard cards/tables expose builder answer counts and decline reasons; admission/fee/notice/programme contract tests pass without an LLM and assert every stated URL is in the verified bundle; the programme builder now runs through `verify_answer()` |
| **A17 multilingual false-pass fix** | `app/agent/text.py`, `app/agent/answer.py`, `eval/acceptance/cases.jsonl`, `tests/test_builder_contract.py` | A17 (`hi`) had passed on a Hindi “सत्यापित जानकारी उपलब्ध नहीं” sentence because refusal detection was English-only and the programme builder refused non-English. The programme builder now answers deterministically in Hindi/Gujarati (verified against a catalogue `EvidenceItem`), refusal detection recognises Hindi/Gujarati/Hinglish, and 11 critical cases now assert `expect.builder` (`programme_list`, `fee`, `notice`, `programme`, `admission`), so a silent model fallback fails the case |
| **Governance release gate** | `scripts/nga_governance.py` (new), `content/dspmu/governance.json` (new), `doc/sdlc/nga/11-governance-release-gate.md` (new), `tests/test_governance.py` (new) | Read-only gate with exit 1: D4 unsigned while PG lists are on; 76/76 catalogue rows without a reviewer; 12/12 legacy old-site nodes groundable without approval; D6/D7 warnings. No approvals invented and no product behavior changed |
| **Dense embedding evaluation (no-go)** | `scripts/nga_embedding_eval.py` (new), `eval/retrieval/chunk_queries.jsonl` (new), `eval/retrieval/README.md` (new), `doc/sdlc/nga/10-dense-evaluation.md` (new), `tests/test_embedding_eval.py` | 21,224 vectors on a disposable snapshot; 17 labeled queries. Hybrid hit@10 14/17 (82.4% vs 90% gate), dense scan p95 3.3 s, dense acceptance p95 9.2 s (1 run) vs 5.3 s lexical (3 runs), citation drift in 5 cases. Decision: keep `EMBEDDING_PROVIDER=off`; remediation is ANN/vectorized search plus recall and citation-precision work |
| **Post-audit chunk hardening** | `app/sitegraph/tools.py`, `store.py`, `chunker.py`, `indexer.py`, `hybrid.py`, `app/agent/text.py`, `eval/acceptance/cases.jsonl` | Chunk hits now obey title/PII policy; chunk index-version filter excludes stale passages; stale chunks are cleared when a document stops being answerable; full rebuild is one bulk transaction and prunes removed nodes; chunk IDs unique for repeated text; node scores preserved under fusion; deterministic programme answer fixes A16/cache false positive; `test_nga_chunks.py` 14 tests. Post-audit cleanup: non-canonical duplicate pages are no longer chunked (the only live duplicate, `campus-engagement.sports → cms.sports`, had 3 unreachable chunks); external nodes remain excluded from node FTS by design because only `status=ok` nodes are searchable and external links are navigation-only |
| **WP6 programme/department catalogue** | `app/knowledge/catalogue.py` (new), `scripts/nga_catalogue.py` (new), `content/dspmu/programmes.json` (new), `app/agent/models.py`, `app/agent/planner.py`, `app/agent/text.py`, `app/config.py`, `app/sitegraph/readiness.py`, `app/agent/verifier.py` | 76 rows (UG 45, PG 27, PhD 1, Diploma 3), zero validation errors; `tests/test_catalogue.py`, `tests/test_nga_programme_list.py`; live list queries return deterministic UG/PG lists; level-aware detection (M.Sc IT ≠ B.Sc IT); verifier rejects a UG claim on PG evidence |
| **WP5 shells navigable, not groundable** | `app/sitegraph/policy.py`, `app/sitegraph/search.py`, `app/sitegraph/store.py`, `app/sitegraph/tools.py`, `app/agent/planner.py`, `app/agent/executor.py`, `app/agent/evidence.py`, `app/agent/text.py`, `app/agent/models.py` | `can_ground` vs `can_navigate`; shells searchable/navigable, open returns `body_available=false`; `site.browse` enumerates sections/documents/department collection; deterministic browse answers; `tests/test_nga_navigation.py` |
| **WP10 acceptance harness and baseline** | `eval/acceptance/cases.jsonl` (new), `eval/acceptance/heldout.jsonl` (new), `eval/acceptance/README.md` (new), `scripts/run_acceptance.py` (new), `tests/test_acceptance.py` (new) | 30 tuned + 20 held-out cases; snapshot isolation; state/fact/URL/cache/event/latency/parity assertions; held-out locked behind `--release`; **105/105 tuned turns passed across 3 runs** |
| **WP1 serving/index split** | `app/config.py`, `app/sitegraph/store.py`, `app/sitegraph/tools.py`, `app/sitegraph/readiness.py`, `scripts/nga_documents.py`, `scripts/nga_chunks.py`, `scripts/nga_scheduler.py`, `scripts/nga_promote.py` (new), `app/sitegraph/scheduler.py`, `app/sitegraph/metrics.py`, `.env.example`, `doc/ops/nga-runbook.md` | `NGA_SERVE_DB_PATH` (falls back to `NGA_GRAPH_SQLITE_PATH`) and `NGA_INDEX_DB_PATH`; 5 s serving / 30 s batch busy timeout; readiness reports both stores and the split; `tests/test_backfill_isolation.py`, `tests/test_store_concurrency.py`, `tests/test_promote.py` |
| Index staging, bounded backfill and document promotion | live `content/dspmu/nga_index.sqlite3`; `logs/nga_index_backfill.log`; `scripts/nga_promote.py` | Index seeded from a consistent serving backup; 5-doc validation batch indexed 3 documents while serving stayed unchanged; bounded batches: 100 checked/60 indexed, then 25/13, 25/17, 25/15, 25/17, 25/18, 25/15 and 50/32, 0 errors; promotions preserved turn events and JSON snapshots kept |
| Verifier programme-claim extraction | `app/programs.py` | A degree plus known published subject only; prose tails (“Ph.D. dated…”, “undergraduate postgraduate programmes along”) no longer block correct answers; `tests/test_programme_mentions.py` |
| PG boundary ordering and named programmes | `app/site_router.py`, `app/agent/planner.py`, `app/rag.py`, `app/agent/text.py` | Agentic path runs before the static PG boundary; a named PG programme is not a fee/seat boundary; a PG hard-data turn gets a deterministic refusal; `tests/test_nga_text.py`, `test_site_router.py` |
| Follow-up topic from hand-off citations | `app/agent/session.py` | A transient on-demand node is mapped back to its canonical graph node by URL, so “its fee” after a hand-off keeps the topic; `test_nga_conversation_memory.py` |
| Admissions portal evidence | `app/agent/facts.py`, `app/agent/evidence.py` | “admission process” answers with `jharkhanduniversities.nic.in` from the signed-off fact, not archived PDFs |
| Deterministic fee and notice answers | `app/agent/text.py` | Exact fee facts and official notice lists are composed and verified without generation; a wrong month gets `time_scope_mismatch` instead of a neighbouring schedule; `test_nga_text.py`, `test_nga_notices.py` |
| Nonsense guard | `app/agent/text.py` | Keyboard-row/consonant-only queries stop before search/deep search instead of handing off a random page |
| Answer-format cache version | `app/agent/text.py` | A code/answer-shape change invalidates cached answers (`answer-2026-10-31.7`) |
| Streamlit first-run conversational gate | `app.py` | Greeting/meta no longer stored as the visitor’s name |
| Config + runbook | `app/config.py`, `.env.example`, `doc/ops/nga-runbook.md` | Effective flags shown by readiness |

### Live evidence

- `which programs currently university` → deep search → official **Academic Programmes** page hand-off, no LLM, no cached refusal.
- `library timings` → official **Central Library** page hand-off, no LLM, no exam-timing citation.
- `hostel rules` → official **Hostel** page hand-off.
- `hello`, `can you search deep`, and the meta “do you understand…” turn → conversational, no retrieval, no LLM.
- `vice chancellor` → verified fact answer with citation.
- Live two-turn follow-up: `complaint status` → `what about its fee?`; the second plan is `site.open(grievance)` + search, and the topic stays `grievance`.
- Live programme-list replay: “which programs currently university” and “what programmes are offered” return a verified 45-UG/27-PG/1-PhD/3-Diploma list; “list of postgraduate courses” returns PG only; “UG programmes” returns UG only. No LLM call for these turns.
- Catalogue detection: `M.Sc IT` → PG M.Sc Information Technology; `B.Sc IT` → UG B.Sc Information Technology; `MA History` → PG; `BA History` → UG; bare `M.Sc`/`History` → no guess.
- Verifier now also rejects a level mismatch (UG claim on PG evidence and vice versa), not just a subject mismatch.
- Live navigation replay: “what is under student life” → the seven Student Life sections; “list of departments” → the 51-page department/faculty collection; “documents on the history page” → the UG/PG History syllabus documents. No LLM for these turns.
- Live readiness after restart: `/health/nga` reports `status=ok`, WAL, 4,802 nodes (4,444 `ok`, 158 shells), catalogue 76 rows/0 errors, 15,672 chunks, 1,360 documents pending. FastAPI (8000), chat UI (8501) and dashboard (8502) are all listening.
- Cache hygiene: six stale entries were purged after a consistent DB backup; the replay wrote four verified `answer-2026-10-31.4` entries and no refusal/negative entries. The answer format is now `.7`; the promoter purged the cache on the 2026-11-01 promotion and both stores now hold 0 cache rows.
- Shell contract: `site.search("hostel")` returns both hostel shells with `groundable=false`, `navigable=true`; `site.navigate("cms.academic-programmes")` returns the shell and its programme siblings; `site.open` on a shell returns `body_available=false`.
- Acceptance baseline (2026-11-01, live graph snapshot, `--llm live --no-offline --runs 3`): **30/30 cases, 105/105 turns**. Deterministic cases run without an LLM; the CI subset runs offline on a pinned fixture.
- The baseline closed the failures it found: verifier over-extraction, PG boundary ordering, named-PG boundary, hand-off topic memory, admissions portal evidence, deterministic fee/notice answers, and nonsense deep search. Held-out set remains unrun until a release report.
- WP1 live split: `/health/nga` reports `serve_db=content/dspmu/nga_graph.sqlite3`, `index_db=content/dspmu/nga_index.sqlite3`, `db_paths_split=true`, both WAL and unlocked. The full background backfill finished: **1,275 checked / 717 indexed / 0 blocked / 27 transient fetch errors**; the final index has **2,965 done / 1,284 empty / 198 blocked / 14 error / 653 review / 0 pending**. A document-only promotion moved **1,260 document rows, 1,248 node updates, 1,260 fetch-state rows and 717 searchable bodies** into serving. Both stores now report the same document counts, 21,224 chunks, 0 cache rows, 23 preserved turn events and `quick_check=ok`; the serving backup is `/tmp/nga_graph.before-promote-20261101-040439.sqlite3`.
- Voice follow-up replay: “tell me about the university” recorded `cms.about-the-university`; “what about its fee?” planned `site.open(cms.about-the-university)` + fee search with `topic_node_id` set. The temporary conversation was cleared.
- Dashboard acceptance report (`logs/acceptance_report.json`, 3 live runs): **30/30 cases, 105/105 turns, p95 5,324 ms**, 0 false refusals, 12 hand-offs (all `empty_shell`), 72/72 fact turns, 0 negative-cache flags, **25/25 cache hits**, 3/3 follow-ups, 0 fallbacks, **45 deterministic-builder turns** (`programme_list` 18, `fee` 9, `notice` 9, `programme` 6, `admission` 3), 29 retrieval-assessed turns with 15 inadequate (51.7%), and `report_ok=true`. The Streamlit Acceptance page renders these cards plus live readiness without exceptions.
- Chunk rollout: **21,224 chunks in serving and 21,224 in the index** (667 page chunks + 20,557 document chunks); embeddings 0 (`EMBEDDING_PROVIDER=off`). The snapshot and live runs both pass 30/30; the M.Sc. IT regression was fixed by level-aware planning/search before the live build. Deterministic `site.search` benchmark: median 16.3→33.7 ms and p95 78→99.6 ms with chunks, max 193 ms, well inside the turn budget.
- Full test suite: **1,355 passed, 5 skipped** (includes NGA-seam channel parity, builder-contract, cache-provenance, localized programme/refusal, canonical-chunk, navigation rollback, dense-evaluation, webhook/voice handler parity and governance-gate coverage).

### Still open (next increments)

1. **Dense embedding rollout remains blocked** — evaluated 2026-11-01 (`10-dense-evaluation.md`): hybrid hit@10 **14/17 (82.4%)**, below the 90% gate; dense scan p95 **3.3 s** (retrieval only); dense-enabled acceptance p95 **9.2 s** (1 run) vs 5.3 s lexical (3 runs), with citation drift in 5 cases. Keep `EMBEDDING_PROVIDER=off`; re-open only after ANN/vectorized search, lexical-hit preservation and recall work, then rerun the retrieval and acceptance gates.
2. Harden the acceptance harness further at the transport level. The WhatsApp webhook route state machine (transport mocked), its RAG helper and the voice batch/stream handlers now have parity tests (`tests/test_webhook_parity.py`) for channel, conversation, language and retrieval arguments. Remaining: a full Twilio HTTP signature/form test and a media-stream test with real STT/TTS/WebSocket transport. NGA-seam chat/WhatsApp/voice parity, deep-search assertion, report pinning, A24, adversarial wiring and dashboard adequacy are complete.
3. **Governance release gate is BLOCKED** (`11-governance-release-gate.md`, `scripts/nga_governance.py --check`): D4 is unsigned while PG programme lists are enabled; all 76 catalogue rows lack a named reviewer (43 draft, 33 page-derived); all 12 legacy `oldsite.dspmuranchi.ac.in` nodes are groundable but absent from the fetch allowlist and legacy approval list. D6/D7 remain warnings. No product behavior was changed; each blocker needs its named authority to sign or remove the content.
4. WP11 remainder: canary rollout comparison, Ops sign-off, and a committed deploy anchor. Actual switches now exist or are mapped: `NGA_NAVIGATION_MODE` (new), chunk/deep/embedding retrieval switches, conversation/catalogue/PG-list switches, and shadow mode. Verifier and answer-state remain mandatory with no off switch.
5. Run the held-out set only in the release report (`--release --include-heldout`), then publish signed-off exceptions.
