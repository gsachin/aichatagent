# DSPMU Agent — Failure Analysis

**Why the recent solutions did not fix the chat**

| Field | Value |
|---|---|
| Date | 2026-10-31 |
| Scope | NGA text/chat/WhatsApp path; retrieval, evidence, verifier, cache, conversation wiring |
| Method | Read the intent/BRD/HLD/LLD set and the 2026-10-31 amendment; reviewed the uncommitted diff; inspected a copy of the live graph, the answer cache and the turn events; re-ran planner/search/evidence/verifier against the snapshot; probed the live DSPMU site and its own `/search` endpoint; checked effective runtime settings and running processes |
| Code changes | None. This is an analysis, not a fix |
| Bottom line | The agent is not failing because it needs another retrieval layer. It is failing because four implicit contracts are broken, and every recent fix sits downstream of them. |

---

## 0. Executive summary

The system is a **retrieve-then-generate question answerer with a citation verifier**, not a conversational assistant. That is a legitimate design, but it does not match the product promise ("a user can ask any published detail of the university and receive a real-time, grounded, cited answer in the channel and language") or the user's expectation of a chat.

Four contracts are broken:

1. **Conversation contract.** Every message is treated as a standalone knowledge query. There is no small-talk, meta, clarification or follow-up state in the live path. `ConversationMemory` exists but is only written, never loaded for planning. The Streamlit stream path does not even pass conversation history.
2. **Navigation contract.** The graph's central promise is to navigate the site hierarchy. In practice the deterministic planner only ever calls `site.search` (plus `site.latest` for dates). It never calls `site.navigate`, `site.documents`, `site.follow` or `site.freshness`. Worse, `is_answerable()` marks every `empty_shell` node unanswerable, so the exact "Academic Programmes", "Hostel" and "Central Library" pages are removed from search and navigation entirely.
3. **Answer contract.** "Verified" currently means "the verifier found no unsupported claim". A refusal has no claims, so a refusal is marked `verified`, cached for a day, and returned again. The system cannot distinguish an answer from a non-answer.
4. **Evidence contract.** Any non-empty bundle counts as evidence. There is no relevance or sufficiency gate. Irrelevant FTS hits therefore suppress deep search, which only runs when the bundle is completely empty. This is why the escape hatch built for exactly this situation never fires.

The recent solutions are technically reasonable in isolation, but they are compensation layers built on top of these contracts:

- **Hybrid chunks/embeddings**: the live chunk table was empty (`nga_chunk = 0`), the embedding provider was `off`, and the tool still puts node hits first and truncates chunk-only hits away. Even after the backfill completes, it cannot change which nodes rank.
- **Deep search**: only triggered by `not bundle.items`. A query that returns three irrelevant pages never reaches it. When it does run, it fetches the site's `/search` results, rejects pages with less than 80 characters of text (which is exactly the shell pages), and does not write the result back into the index.
- **Time-scoped cache**: correctly protects a December schedule from a January question, but it also caches refusals as `verified`, and its index version does not include the chunk index. Rebuilding the index does not invalidate the bad answer.
- **Span-level verifier**: correctly checks citations, but `detect_program()` is built only from the 43 UG fee programmes. It maps "M.Sc. Information Technology" to "B.Sc. Information Technology", so a legitimate answer that names the PG programme in the evidence is blocked. A refusal with no claims passes as verified.
- **Archive/document backfill**: improves coverage, but 2,548 documents were still pending, 865 were empty and 198 blocked at snapshot time. It runs in the same SQLite database as the live answer path and held the write lock during analysis, which can force the answer path into its static fallback.

The one-line answer to "why didn't the fixes work?":

> **Each fix improved a component that the live path never reached, or reached only after an upstream gate had already decided the turn was complete. The upstream gates are the defect.**

The observed conversation is the proof: the user asked for a university programme list, received a verified refusal, and the system cached that refusal for 24 hours; a meta question ("do you understand when I ask about program…") was routed to the date/notice path because of the word "when"; and the explicit request "can you check deep search…" was blocked by the verifier without deep search ever running.

---

## 1. What the work was meant to do

The design set is unusually clear about intent. The relevant statements are:

- The problem is a **solution-design failure, not a website-availability failure**: the runtime has no path to most of the live site (`00-intent.md` §1).
- The product intent is: **"Make any published DSPMU website detail answerable, in real time, in the user's channel and language, with a grounded and cited response."** (`00-intent.md` §2).
- The agent must know the site hierarchy, **navigate** it through constrained tools, combine verified facts with live page content, keep a durable conversation record that improves follow-up retrieval, and degrade to the static answer when the live site or a tool fails (`00-intent.md` §2).
- Jobs include JTBD-02 "Navigate the hierarchy when the exact page is unknown" and JTBD-06 "Resolve follow-ups from conversation state" (`00-intent.md` §4).
- The North Star is M-01: **≥ 90% grounded live-answer rate** on a frozen evaluation set. M-02 requires **100% critical-fact accuracy**. M-09 requires **100% of turns** to have an event with plan, tools, evidence, answer, verifier result and latency (`00-intent.md` §5).
- The design evidence for the failure was concrete: the library-timings, Vice-Chancellor, hostel and Grievance Redressal questions could not reach the live pages (`00-intent.md` §1).

The 2026-10-31 amendment extended the intent with hybrid retrieval, a chunk corpus, a time-scoped cache and a deep-search fallback. Its executive summary says the current path "misses facts that exist on the live site but are not indexed" and "can reuse a cached answer outside the time period the user asked about" (`05-hybrid-retrieval-deep-search-hld-lld.md`, header).

So the intent is not ambiguous. The assistant is supposed to be a **conversational, live-site navigation agent**. The implementation is a **one-shot lexical search plus an evidence-constrained LLM**.

---

## 2. What was actually implemented, and what was actually running

### 2.1 The layers

| Layer | What exists | What the live path does |
|---|---|---|
| Intent | 8 retrieval intents + `boundary`; optional LLM planner | Deterministic keyword classifier by default (`NGA_TEXT_LLM_PLANNER=false`); `PAGE_TOPIC` is the catch-all |
| Planner | `site.search`, `site.navigate`, `site.open`, `site.latest`, `site.documents`, `site.freshness`, `site.follow` | Emits `site.search` for fee/programme/page-topic/contact; `site.latest` + `site.search` for date/notice; follow-ups add `site.open` only if a session node already exists; **never `site.navigate`, `site.documents`, `site.follow` or `site.freshness`** |
| Retrieval | Graph FTS + body FTS + optional chunk FTS + optional dense vectors | Graph FTS and body FTS. Chunk path only when `chunk_count() > 0`; dense only when `EMBEDDING_PROVIDER` is not off |
| Evidence | Typed evidence, verified facts, citations, tables | Search results (up to 3), opened page body, verified fee/contact/leadership facts. No relevance gate |
| Generation | Evidence-grounded prompt with strict "say you could not verify" instruction | Same; model is explicitly told to refuse when evidence is insufficient |
| Verification | Amount/date/URL/email/phone/person/programme claims; citation-span checks | Blocks a critical claim, rewrites a URL-only failure. A refusal has no claims and is marked `verified` |
| Cache | L2 SQLite `nga_answer_cache`, TTL + time-scope + source revalidation | Caches `verified` and `rewritten` answers; refusals with evidence are `verified`; 24 h TTL for untimed queries; 1 h for time-scoped queries; 10 min for `no_evidence` |
| Deep search | Site `/search` → fetch → extract → evidence → answer → verifier | Only when the bundle is empty (or cache revalidation failed), and only for non-voice channels |
| Conversation | `nga_turn_event`, `ConversationMemory.load/plan/record` | Records events on the normal answer path only. Never loads state for planning. Cache hits and deep-search returns skip the event write |
| Documents | 4,461 document nodes, PDF extraction, OCR review gate | 841 `done`, 7 done + needs_review, 2,548 pending, 865 empty, 198 blocked, 2 error at snapshot |
| Chunks | Chunker, indexer, BM25 chunk FTS, RRF, OpenAI embeddings | `nga_chunk = 0`, `nga_chunk_embedding = 0`, `EMBEDDING_PROVIDER=off` at snapshot |

### 2.2 Effective runtime settings at analysis time

```
NGA_ENABLED=True
NGA_MODE=agentic
DEEP_SEARCH_ENABLED=True
CACHE_REVALIDATE_ENABLED=True
NGA_TEXT_LLM_PLANNER=False
EMBEDDING_PROVIDER=off
LLM_MODEL=gpt-6-luna
LLM_MAX_TOKENS=192
NGA_TEXT_MAX_ITEMS=5
NGA_TEXT_MAX_CHARS=4000
```

The following flags named in the 2026-10-31 design do **not exist** in `app/config.py`:

```
RETRIEVAL_MODE      <missing>
VERIFY_MODE         <missing>
TIME_SCOPE_STRICT   <missing>
CACHE_BACKEND       <missing>
```

This matters for the post-mortem: the design document describes a rollout surface that the code does not have. "Implemented" in the status table means "the code path exists", not "the capability is wired, enabled, populated and verified end to end".

### 2.3 Graph and index state at snapshot

| Measure | Value |
|---|---|
| Graph nodes | 4,802 |
| Graph edges | 9,443 |
| Node status | 4,444 `ok`; 198 `blocked`; 158 `empty_shell`; 2 `external` |
| Page versions with text | 157 |
| Body FTS rows | 998 |
| Documents | 4,461 |
| Documents done | 841 (+7 needs review) |
| Documents pending | 2,548 |
| Documents empty | 865 |
| Documents blocked | 198 |
| Chunks | **0** |
| Chunk embeddings | **0** |
| Answer cache entries | 6 |
| Turn events | 8 |

The graph claims 329 nodes in the Phase 1 report and now has 4,802 because document nodes and legacy nodes were added. The important number is not the node count; it is the 157 pages with text and the 158 shells that are excluded from navigation.

---

## 3. The observed failure, reconstructed from the system's own records

The recent conversation is in `nga_turn_event` and `nga_answer_cache` (conversation `2b2fc045-9236-47a6-9f5d-ef489fb3f1b6`). It is the clearest evidence of the failure.

| Turn | User message | Planner result | Evidence used | Answer | Status |
|---|---|---|---|---|---|
| 1 | "can you search deep" | `page_topic` → `site.search` | M.Sc. IT page + two career PDFs | "Yes—what would you like me to search for at DSPMU, such as a programme, admission dates, fees, or a contact?" | `verified` |
| 2 | "which programs currently university" | `page_topic` → `site.search` | Best Practices, NEP 2020, NEP 2020 | "I couldn't verify which programmes are currently offered from the available evidence. Please contact the Registrar for the current list." | `verified`, cached 24 h as normalized query `programs currently` |
| 3 | "do you undertstand when I ask about program what is my intent of ask?" | `date` → `site.latest` + `site.search` | About the University, About DSPMU, About IQAC, two notices | "Yes. I understand 'program' to mean an academic programme offered by DSPMU, unless you specify otherwise. [ev-1]" | `verified`, cached 24 h |
| 4 | "can you check deep search to find university program" | `programme` → `site.search` | Grievance Portal, Grievance Redressal Cell, M.Sc. IT / career PDF | *(empty)* | `blocked` |

What the user saw:

- Turn 1: a meta request ("can you search deep") answered as if it were a site query, with an irrelevant evidence bundle and a canned offer to search.
- Turn 2: a direct programme-list question answered with a refusal, even though the graph contains department pages with "U.G History / P.G History / Syllabus & Course Documents" and the live site's `/search` endpoint returns the Academic Programmes and department pages. The refusal is then cached as `verified` for 24 hours, so repeating the question returns the same refusal.
- Turn 3: a meta question about the assistant's understanding was routed to the date/notice path because the classifier sees "when". It then produced a plausible-sounding but unsupported answer with a citation to the About page.
- Turn 4: the user explicitly asked for deep search. The planner produced a `site.search`; the evidence bundle was non-empty (irrelevant), so deep search was never called. The answer was blocked by the verifier and rendered as the generic "I could not find a verified answer in the public university pages. Please contact the registrar" refusal.

Two other cache entries show the same pattern:

- `happending` → "I checked the official university website for the latest details. I could not verify…" → `verified`, cached for 1 hour. This is the deep-search refusal path: it found something, could not answer, prefixed a message that claims it checked the site, and cached the non-answer.
- `search deep` → "Yes—what would you like me to search for at DSPMU…" → `verified`, cached 24 h.

The only demonstrated successes were `vice chancellor` ("The Vice Chancellor is Prof. Rajiv Manohar.") and `exam schedule oct 2026` ("The schedule lists exams on 13-10-2026."). This is exactly the profile of a lexical QA system: it works for distinctive noun phrases whose answer is in an indexed page or fact, and fails for conversational acts, broad enumeration, synonyms, PG entities and shell-shaped sections.

---

## 4. Reproduction: where the information is, and why retrieval misses it

All reproductions below were run against a copy of the live graph (`nga_graph.sqlite3`) with the same planner/search/evidence/verifier code. No LLM call was needed.

### 4.1 The correct page is often in the graph but excluded

`app/sitegraph/policy.py:42-56`:

```python
def is_answerable(node: SiteNode, pii_guard: bool = True) -> bool:
    if node.status in (
        NodeStatus.BLOCKED,
        NodeStatus.EMPTY_SHELL,
        NodeStatus.SOFT_404,
        NodeStatus.UNREACHABLE,
        NodeStatus.REMOVED,
    ):
        return False
```

The following nodes are all `empty_shell` and therefore unanswerable:

```
cms.academic-programmes          Academic Programmes
academic-programmes.ug           Under Graduate (UG)
academic-programmes.pg           Post Graduate (PG)
academic-programmes.ph-d-*       Ph.D. sections
cms.hostel                      Hostel
student-life.hostel             Hostel
```

Consequences:

- `site.search("academic programmes")` returns `cms.academic-council`, `NEP 2020`, `University at a Glance`, `CBCS NEP 2020`, `NSS` — never the Academic Programmes page.
- `site.navigate("cms.academic-programmes")` returns `BLOCKED: node 'cms.academic-programmes' is not answerable`.
- `site.search("hostel")` returns a 2022 hostel-allotment fee PDF and three "About" pages — never `cms.hostel` or `student-life.hostel`.
- `site.search("library timings")` returns examination-timing PDFs and a digital-library notice — never the Central Library page (which the live site search does return).

This is the single most damaging design contradiction. The intent says the agent must navigate empty CMS shells and follow documents (`00-intent.md` §1, IR-02; `05-...md` A1). The code makes empty shells invisible to every tool. A shell is useless as evidence, but it is valuable as a **navigation node**: it has a URL, a title, a parent, children and often links to documents. The implementation conflates "cannot ground a claim" with "does not exist".

### 4.2 The ranking prefers generic body matches to the specific page

For `"what programmes are offered"`, normalized to `"programmes offered"`:

```
1. 6.95  Cbcs Nep 2020
2. 6.95  NEP 2020
3. 6.95  Training & Placement Cell (Legacy)
4. 6.38  NSS
5. 6.37  NSS
6. 6.30  University at a Glance
...
20. 5.90  Results
```

The Academic Programmes node is not in the top 20; it is excluded as an empty shell. The closest useful page, `faculties.vocational-studies` ("Self-Finance and Vocational Courses" with a list of departments/courses), is not retrieved either. A department page such as `departments.history` contains "Under Graduate / U.G History / Post Graduate / P.G History / Syllabus & Course Documents" but is not in the top 20 for this query.

The scoring in `app/sitegraph/search.py:168-213` is still the old shape:

- term overlap is unweighted across title, nav, alias, path and section;
- BM25 contributes at most `min(2.0, -rank * 0.25)`;
- content bonus adds 2.5 for HTML and authority adds 1.0;
- there is no IDF, no field weighting, no stemming and no relevance threshold;
- `_GENERIC_TOKENS` only drops "university", "universities" and "upcoming"; "currently", "list", "programs", "offered" and "details" remain as ranking terms.

The 2026-10-31 amendment diagnosed this ("a generic token such as `university` outranks the specific schedule document"; "the FTS tokenizer does not stem") but the implementation only added a few word replacements and dropped three generic tokens. The core ranking is unchanged.

### 4.3 The tokenizer and classifier do not understand word forms

`nga_fts` is `fts5(..., tokenize='unicode61')`; there is no porter stemmer. So:

- `programs` does not match `programme` or `programmes`;
- `courses` does not match `course`;
- `lists` does not match `list`.

The planner is also word-boundary exact. `_PROGRAMME_WORDS` contains `programme`, `program`, `course`, `courses`, etc., but not `programs`. With the regex `(?<![a-z0-9])program(?![a-z0-9])`, the word "programs" does not match "program" because the final `s` is an alphanumeric. Reproduced:

```
"program"      -> programme
"programs"     -> page_topic
"programmes"   -> page_topic
"list of programmes" -> page_topic
```

`PAGE_TOPIC` still searches, so this is not fatal by itself. But it means the programme intent, filters and evidence behavior never trigger for the most common American/plural spelling. The same class of bug produced the date misroute: `"do you understand when I ask about program…"` contains "when", and `_DATE_WORDS` is checked before `_PROGRAMME_WORDS`, so the plan becomes `site.latest` + `site.search` rather than a conversational clarification.

### 4.4 The programme detector is a UG fee table, and it collides with PG names

`app/programs.py` says it plainly:

> "It covers the 43 undergraduate programmes in the 2026 fee sheet. Postgraduate programme names are deliberately absent until their list and fees are verified." (`app/programs.py:8-10`, `:132-209`)

`detect_program()` is used by the **verifier**, not just by lead capture. Reproduced:

```
M.Sc. Information Technology             -> Bachelor of Science in Information Technology (B.Sc.)
Master of Science in Information Technology -> Bachelor of Science in Information Technology (B.Sc.)
M.A. History                             -> Bachelor of Arts in History (BA)
M.Sc. Chemistry                          -> Bachelor of Science in Chemistry (B.Sc.)
M.Sc. Mathematics                        -> Bachelor of Science in Mathematics (B.Sc.)
M.Sc. Botany                             -> Bachelor of Science in Botany (B.Sc.)
M.Sc. Zoology                            -> Bachelor of Science in Zoology (B.Sc.)
```

The reason is alias ordering: the subject alias `"information technology"` maps to the UG B.Sc. IT row, and the detector returns the first matching alias. A generated answer that correctly names "M.Sc. Information Technology" from the evidence is therefore treated as claiming "B.Sc. Information Technology", which is not in the evidence, and the answer is blocked as a critical programme failure. I reproduced exactly that with the evidence from turn 4:

```
Answer: "Yes, I can search the official website. The university offers
programmes such as M.Sc. Information Technology [ev-3]."
Verifier: status=blocked
Failure: programme "Bachelor of Science in Information Technology (B.Sc.)"
         reason=programme_not_in_evidence
```

This is a concrete, reproducible cause of a legitimate answer being thrown away. It is also a warning about reusing a lead-capture entity detector as a grounding verifier: the two have different universes.

### 4.5 The planner never navigates

The deterministic planner's entire output surface is:

```
fee/programme/page_topic -> site.search
notice/date              -> site.latest, maybe + site.search
contact                  -> site.search "contact registrar"
follow-up                -> site.open(current_node) [+ site.search]
boundary                 -> no steps
```

Reproduced for ten representative questions; `site.navigate`, `site.documents`, `site.follow` and `site.freshness` never appear. The navigation graph is used only as a ranking index. JTBD-02 ("navigate the hierarchy when the exact page is unknown") is not implemented in the live default path.

This is not a small gap. A broad question like "list all programmes offered" or "what departments are there" cannot be answered by a top-3 search; it requires opening a section and enumerating children, or querying a structured catalogue. The planner has no enumerate/list plan.

### 4.6 The evidence gate is "any non-empty bundle"

`app/agent/text.py:501-505`:

```python
if (
    (not bundle.items or revalidation_invalid)
    and settings.DEEP_SEARCH_ENABLED
    and channel != "voice"
):
```

`assemble_evidence()` has no relevance gate. It takes the tool results that succeeded and adds them, up to the item/character caps (`app/agent/evidence.py:243-513`). So:

- "library timings" returns exam-timing PDFs → bundle non-empty → deep search skipped;
- "what programmes are offered" returns NEP/NSS pages → bundle non-empty → deep search skipped;
- "hostel rules" returns a 2022 fee PDF + About pages → bundle non-empty → deep search skipped;
- "can you check deep search…" returns Grievance Portal + M.Sc. IT → bundle non-empty → deep search skipped.

The live site's own `/search` endpoint returns the right pages for these queries:

```
/search?q=library timings  -> /facilities/central-library, /cms/central-library,
                              /academic-programmes/ug, /academic-programmes/pg,
                              /student-life/hostel, department pages
/search?q=programmes       -> /cms/academic-programmes, /academic-programmes/ug,
                              /academic-programmes/pg, departments/commerce,
                              departments/history, departments/mba, ...
/search?q=hostel rules     -> /cms/hostel, /student-life/hostel
```

The deep-search service uses exactly this endpoint (`app/deepsearch/service.py:104-151`). It is the correct escape hatch. It is simply never reached because the bundle has junk in it.

When it is reached, `_fetch_one()` rejects any page with fewer than 80 characters of text (`deepsearch/service.py:197-198`). The Academic Programmes, UG, PG, Hostel and Central Library pages are all below that threshold in server HTML; they are image/JS shells. So the escape hatch would reject the very pages the site search just found. Deep search has no child-link or PDF following either; it fetches only the top result URLs.

### 4.7 The live site is shell-shaped and image-shaped

I probed the live pages:

- `/cms/academic-programmes`, `/academic-programmes/ug`, `/academic-programmes/pg`: ~5,500 characters of HTML, but almost all navigation/footer; the content area is empty. The page's visible content is a gallery/link shell.
- `/facilities/central-library`, `/cms/central-library`: ~1,000 characters after removing scripts/nav/header/footer; 11 images, alt "Library"; no timing text in HTML.
- `/student-life/hostel`, `/cms/hostel`: title + "Gallery" shell; no rules/timings text.

The graph's `empty_shell` labels are **factually correct for the extracted HTML**. The problem is the assumption that the CMS HTML is the primary public surface. For these pages it is not. The information the user sees in a browser may be in images, JS-rendered blocks, or another page (the old site, department pages, PDFs). The current knowledge plane cannot see it, and the deep-search fallback rejects it. Adding chunks, embeddings or a cache over the same extracted text cannot retrieve content that was never extracted.

This is the answer to the user's "the information is already available on the website": **it is available to a human browser, but not to the HTML-only retrieval pipeline.** The pipeline needs a different acquisition strategy for these pages, or an explicit statement that these pages are not machine-readable and a hand-off to the published URL/contact. Right now it produces a generic refusal that is indistinguishable from "the university does not publish this".

### 4.8 The verifier verifies claims, not answers

`app/agent/verifier.py:568-583`:

```python
if not claims:
    notes.append("no_claims")
...
critical = [failure for failure in failures if failure["critical"]]
if critical:
    return VerificationResult(..., status="blocked", ...)
```

A refusal such as "I couldn't verify which programmes are currently offered…" contains no amounts, dates, URLs, emails, phones, named people or detectable programmes. It therefore has no claims, no failures, and returns `status="verified"`. `_write_answer_cache()` stores that as a verified cache entry for 24 hours. The next identical question returns the refusal without calling the LLM.

This is not a verifier bug in the narrow sense; the verifier does exactly what it says. The bug is the **answer contract** around it: the system has no representation of "I answered", "I clarified", "I refused because the evidence was irrelevant", "I refused because the request is out of policy", or "I failed". It has only `verified`, `rewritten`, `blocked`, `no_evidence`, `generation_failed`, and it maps several very different outcomes onto `verified` or a generic refusal.

The same contract flaw is why the blocked turn renders as "I could not find a verified answer in the public university pages" (`app/agent/text.py:577-591`, `app/agent/answer.py:70-84`). The user cannot tell whether the system found nothing, found something but rejected its own answer, or hit a policy boundary. In the observed turn 4, it had evidence and the LLM produced something; the verifier blocked it; the user was told the university pages had no verified answer. That statement is false.

### 4.9 The cache makes one bad turn sticky

`_write_answer_cache()` (`app/agent/text.py:162-235`) caches any `verified` or `rewritten` result. The cache key includes the normalized query, language, channel, programme, time scope, index version and policy version (`app/cache/answer_cache.py:236-261`). It does **not** include:

- the answer type (answer vs refusal vs clarification);
- the conversation topic/current node;
- the chunk index version;
- the embedding model/version.

`_cache_versions()` uses only the graph compile run:

```python
run = store.latest_run() or {}
index_version = f"{graph_hash}:{generated_at}"
```

So rebuilding the chunk index does not invalidate the cached refusal. The 24-hour TTL applies. Revalidation only runs near expiry (`CACHE_REVALIDATE_NEAR_EXPIRY_S=3600`) and, for an entry with no source URL, returns `valid=True, checked=False, "no_source"` (`app/cache/revalidate.py:74-76`). A cached refusal with citations to unchanged junk pages survives revalidation; a refusal with no citations is treated as valid without a check.

This is why "multiple attempts" produce the same answer: the first failure is not just displayed, it is promoted to a verified cache fact.

### 4.10 The conversation path is not wired

`app/agent/text.py:72-92`:

```python
def plan_text(...):
    state = SessionState(language=language)
    ...
    return Planner().plan(query, state)
```

A fresh state every turn. `ConversationMemory.load()` and `.plan()` exist (`app/agent/session.py:40-83`) but `build_text_answer()` never calls them. It only calls `.record()` at the end of the normal path (`app/agent/text.py:553-570`), and even that is skipped for cache hits and deep-search returns.

The Streamlit path (`app.py:129-137`) calls `query_rag_stream(prompt, ...)` with `agentic_text`, `channel` and `conversation_id`, but no history. `query_rag_stream()` has no `history` parameter (`app/rag.py:494-503`). The synchronous path has a `history` parameter and passes it to `build_text_answer`, but `build_text_answer` uses it only in the generation prompt; the planner still gets fresh state. So even on WhatsApp, "what about its fee?" cannot resolve "it" at retrieval time. The Phase 6 demo worked because the demo passed a `SessionState` explicitly; the live seam never does.

The result is a chat that behaves like a search box with a 24-hour memory of its own mistakes.

### 4.11 The serving and indexing processes share one SQLite writer

At analysis time, a `scripts/nga_documents.py --backfill --chunks` process was running at ~90% CPU and held many file descriptors on `content/dspmu/nga_graph.sqlite3`. Reads from the live database failed with `database is locked`, including read-only queries. The store uses `sqlite3.connect(..., timeout=30.0)`, `PRAGMA busy_timeout = 30000`, and the database is in `journal_mode=delete` (not WAL; `app/sitegraph/store.py:280-284`).

The answer path and the batch indexer use the same file. A long document backfill can therefore:

- make a user turn wait up to 30 seconds on a SQLite lock;
- exceed the 8-second text deadline;
- raise inside `build_text_answer`, where the broad `except Exception` returns `None`, silently falling back to the static path;
- make the user see a completely different, older answer with no indication of the fallback.

This is a reliability/architecture defect, not a retrieval bug. It also explains inconsistent behavior during testing: sometimes the agent path works, sometimes the static path answers, and the logs do not say which.

### 4.12 The profile gate can hijack the first turns

This is a channel-level issue in `app.py`, separate from the NGA core, but it contributes to "not responding as expected of a conversation":

- On first load, `awaiting_field` is set to `"name"` (`app.py:492`).
- Any message that is not recognised as a question, admission intent, skip word or short name is treated as a name. The question heuristic checks for `?` or a keyword list including programmes/fees/admission (`app.py:580-626`).
- "Tell me about the university" (5 words, no `?`, no keyword) becomes "That's a bit long for a name. Could you just tell me your first and last name?"
- "hello" is short enough to be stored as the user's name.
- A message containing "admission" starts the lead-capture flow rather than answering the admission question (`app.py:922-945`).

The observed conversation got past this gate, so it is not the cause of turns 1–4. But the first-run experience is a lead form, not a chat, and that is part of what the user is describing.

---

## 5. Root-cause analysis by level

### 5.1 Fundamental design issues

These are not bugs in a module. They are choices that make the current failure likely even if every module is correct.

#### D1. The product contract is conversational; the implementation contract is one-shot QA

The design promises a conversation record that improves follow-up retrieval (JTBD-06), but the live answer contract is "query → retrieve → generate → verify". There is no dialogue act model, no clarification state, no small-talk path, no follow-up frame. The only conversation state is a typed `SessionState` that the live path never loads. The assistant cannot behave like a chat because it was not designed as one at the seam; it was designed as a grounded QA pipeline with a conversation table bolted on.

This is the deepest mismatch. Adding more retrieval cannot make a search box conversational.

#### D2. The navigation contract is contradicted by the answerability policy

The architecture's central claim is that the typed graph preserves hierarchy and lets the agent navigate. But `is_answerable()` removes `empty_shell`, `soft_404`, `unreachable`, `removed` and `blocked` nodes from **search and navigation**, not just from evidence. The site's programme, hostel and library sections are shells. The agent therefore cannot navigate the exact sections the design was built for.

The correct distinction is:

- **Evidence answerability**: may this node's text ground a factual claim? (A shell cannot.)
- **Navigation answerability**: may this node be returned as a route, with metadata/children/links? (A shell can and should.)

The code collapses both into one predicate. This is a fundamental modelling error, and it is the direct cause of "Academic Programmes is not found even though it exists in the graph".

#### D3. "Verified" conflates claim safety with answer adequacy

The verifier answers "is every critical claim supported?" It does not answer "is this a responsive answer to the question?" A refusal has no claims, so it is safe and verified. A correct answer that mentions a PG programme is unsafe according to the UG-only detector, so it is blocked. Safety and correctness are not the same axis, and the system has only one axis.

A production answer contract needs at least two dimensions:

- **claim support** (no unsupported critical claim);
- **answer adequacy** (direct, relevant, complete for the asked shape; or an explicit clarification/refusal).

The current code has the first and calls it the second.

#### D4. Evidence has no relevance or sufficiency predicate

The decision to stop retrieving and start answering is "the bundle is non-empty". That is not a sufficiency test. The system needs a predicate such as:

```
adequate = top_score >= threshold
           AND query_term_coverage >= threshold
           AND evidence_type_matches_intent
           AND (for time questions) coverage includes the requested scope
           AND (for list/enumeration) expected entity type is present
```

Without it, junk evidence is indistinguishable from good evidence, deep search is suppressed, and the LLM is asked to answer from noise. The prompt then instructs it to refuse, and the verifier marks the refusal verified.

#### D5. The knowledge model is page-centric in a site that is entity/catalogue-shaped

The site's useful knowledge is spread across:

- department pages (UG/PG programme lists, syllabus PDFs);
- PDFs (fees, notices, schedules);
- shell pages that are navigation hubs;
- image galleries (library/hostel);
- legacy pages;
- the site's own search index.

The system models "a page with text" as the unit of knowledge. It has no first-class **programme**, **department**, **notice**, **document**, **time scope** or **authority** entity that can be queried independently of a page. The facts layer covers only fees, contacts and leadership. A question like "which programmes are offered" is an entity/list query; the system treats it as a page-search query and fails.

This is why chunking and embeddings do not fix it: they improve passage retrieval within pages, not the absence of a programme catalogue.

#### D6. The product scope and the policy boundary contradict each other

The intent says "any published detail" and the evaluation catalogue includes "Postgraduate programs" and "Doctoral / PhD programs" (`eval/intent-catalog.md`). But:

- the programme detector is UG-fee-only (`app/programs.py:8-10`);
- PG programme/fee/seat/eligibility requests can be answered with a deterministic boundary refusal (`app/site_router.py:327-356`), and the boundary heuristic is inconsistent: `"list of postgraduate courses"` is blocked, while `"what PG programmes are offered"` is not;
- the verifier will block an answer that mentions a PG programme that the detector maps to a UG programme.

The product needs an explicit decision: are PG programme lists (not fees) answerable from public pages? If yes, build a PG catalogue and adjust the detector/verifier. If no, say so in the UX and do not promise "any published detail". The current middle ground produces refusals that look like retrieval failures.

#### D7. Serving and indexing share one SQLite writer

The design says Postgres for multi-host, SQLite for local/demo. But even in the local demo, the answer path and a 4,461-document backfill share one `delete`-journal SQLite file. A batch job can block user turns, and the fallback is silent. This is a deployment design flaw, not just an implementation bug. The serving store and the indexing store should be separate (or the indexer should write a snapshot and swap it atomically), with WAL and a bounded busy timeout.

### 5.2 Implementation and wiring issues

These are places where the design intent exists but the live path does not use it, or uses it with the wrong gate.

#### I1. Conversation memory is write-only

`ConversationMemory.load()` and `.plan()` exist but `build_text_answer()` calls neither. `plan_text()` constructs a fresh `SessionState`. Streamlit passes no history. The planner's follow-up branch is therefore reachable only in demos/tests that inject state.

**Effect:** follow-ups fail; topic changes are not tracked; the user has to repeat context.

#### I2. Deep search is gated on zero evidence, not inadequate evidence

`text.py:501-505`. There is no relevance gate in `assemble_evidence()`. **Effect:** deep search is suppressed in exactly the cases it was designed for (irrelevant lexical hits). This is the most direct explanation of "the new deep-search solution didn't help".

#### I3. Hybrid chunks are node-first decoration, not fusion

`tools.py:330-408`:

- node hits are emitted first;
- chunk hits only decorate snippets for nodes that already ranked;
- chunk-only nodes are appended after the node hits;
- the list is then truncated to `limit`;
- with the planner's `limit=3`, a chunk-only node can never enter if three node hits exist.

Additionally, `EMBEDDING_PROVIDER=off`, so there is no dense signal. The 2026-10-31 amendment's D-01 ("BM25 + dense + RRF + rerank over a chunk corpus") is not what `site.search` does. It is "node ranking with optional chunk snippets".

**Effect:** even after the chunk backfill, the ranking for these queries will not change.

#### I4. Cache semantics promote non-answers and ignore index changes

- `_write_answer_cache` stores `verified` refusals.
- The cache key has no answer type, topic or chunk index version.
- `_cache_versions` uses only the graph compile run.
- `revalidate_entry` treats "no source URL" as valid unchecked.
- 24 h TTL for untimed queries.

**Effect:** one bad turn becomes a day of consistent bad answers; rebuilding the index does not clear it.

#### I5. The verifier's programme check uses the wrong universe

- `detect_program` is the lead-capture/UG-fee detector.
- It is applied to every generated answer (`verifier.py:332-336`).
- It maps PG names to UG canonical names via subject aliases.
- It is a critical claim; a mismatch blocks the whole answer.
- There is no repair loop: the system does not re-prompt with the failure, does not retry with the correct evidence, and does not fall back to an extractive answer.

**Effect:** legitimate answers are blocked or, worse, accepted with a wrong entity. The blocked result renders as "no verified answer", hiding the real cause.

#### I6. Evidence assembly skips too much and caps too early

`assemble_evidence()`:

- search results are capped at 3 (`data.get("results")[:3]`);
- items are capped by `max_items` and `max_chars`;
- an opened node's search stub is skipped even if the open produced no body (`opened_ids`), so an empty shell can remove the only useful search snippet of that node;
- there is no per-item relevance score in the bundle, so the generator and verifier cannot tell a strong match from a weak one.

**Effect:** the correct page can be pushed out by earlier results; the prompt can be dominated by one long page; the verifier checks claims against weak evidence.

#### I7. The static fallback is a different product

When `build_text_answer` returns `None` (no evidence, boundary, exception), `query_rag` falls through to the static path. The static prompt explicitly says: "If the context truly has NO relevant data for a question, say: 'I don't have that specific information in the university profile.'" (`app/rag.py:58`). The static bundle is a small committed snapshot. The user cannot tell which path answered. A silent fallback to a smaller, older corpus is indistinguishable from the agent's own refusal.

**Effect:** during DB locks, flag mistakes, or budget errors, the user gets the old static behavior, which is exactly the behavior the NGA was built to replace.

#### I8. There is no effective-config/readiness check

The app starts without logging or checking:

- whether `NGA_MODE=agentic` is actually in effect;
- whether `DEEP_SEARCH_ENABLED` is on;
- whether the graph has body text/documents/chunks/embeddings;
- whether the scheduler is running;
- whether the current process is using the same DB/version as the indexer.

The 2026-10-31 design names `RETRIEVAL_MODE`, `VERIFY_MODE`, `TIME_SCOPE_STRICT` and `CACHE_BACKEND`; none exist in config. A boot readiness report would have surfaced "chunks=0, embeddings=0, provider=off, 2,548 docs pending" before the user tested.

#### I9. Turn events are missing on the paths that matter most

`build_text_answer` records an event only after a normal generated answer. It returns early for:

- a valid cache hit;
- a deep-search result;
- no evidence (returns `None` before recording);
- blocked/generation_failed? (It records after `answer_from_execution`, so blocked is recorded; the observed turn 4 has an event.)

But cache hits and deep-search returns have no event. M-09 ("100% of turns have an event") is not met. The audit trail is missing for the exact paths the team needs to debug.

### 5.3 Module-level defects

| ID | Module | Defect | Evidence |
|---|---|---|---|
| M1 | `app/sitegraph/policy.py:42-56` | `EMPTY_SHELL` nodes are unanswerable for search and navigation, not just evidence | `site.navigate("cms.academic-programmes")` → `BLOCKED`; `site.search("hostel")` omits both hostel nodes |
| M2 | `app/agent/planner.py:46-64, 117-139` | Keyword intent with word-boundary exactness; `programs` ≠ `program`; `when` in a meta question → `date`; `PAGE_TOPIC` catch-all | `classify_intent("programs")` → `page_topic`; `"do you understand when…"` → `date` |
| M3 | `app/agent/planner.py:197-287` | Deterministic plan never uses `site.navigate`, `site.documents`, `site.follow`, `site.freshness` | Ten representative plans; only `site.search`/`site.latest` |
| M4 | `app/sitegraph/search.py:127-213` | OR-token FTS with unweighted overlap, capped BM25, generic-token removal incomplete, no stemming, no relevance threshold | `"what programmes are offered"` top-20 has no programme page; `"list of programmes"` returns galleries |
| M5 | `app/sitegraph/search.py:114-118` + `nga_fts` | No stemming: `programs` ≠ `programme`; `courses` ≠ `course` | FTS schema `tokenize='unicode61'` |
| M6 | `app/sitegraph/tools.py:330-408` | Chunk hits are appended after node hits and truncated; dense optional | With `limit=3`, chunk-only nodes cannot enter |
| M7 | `app/programs.py:132-209` | Alias table is UG-fee-only; subject aliases collide with PG names | `M.Sc. Information Technology` → `B.Sc. Information Technology` |
| M8 | `app/agent/verifier.py:332-336, 541-555, 568-583` | Programme claim detection uses the UG detector; no claims → `verified`; critical failure blocks whole answer; no repair | Reproduced blocked legitimate M.Sc. IT answer; cached refusals are `verified` |
| M9 | `app/agent/evidence.py:243-513` | No relevance gate; top-3 search cap; `opened_ids` skip even when open has no body | Turn 2 evidence: NEP/NSS/Best Practices; turn 4 evidence: Grievance Portal |
| M10 | `app/agent/text.py:95-101, 162-235, 501-505` | Cache versions exclude chunks; refusals cached as verified; deep search only on empty bundle | `programs currently` refusal cached 24 h |
| M11 | `app/cache/answer_cache.py:22, 224-233, 264-313` | `verified` is the only positive status; no answer type; 24 h default; time-scope only | Cache table has no `answer_type`/`topic` column |
| M12 | `app/cache/revalidate.py:74-104` | No source URL → `valid=True, checked=False`; 200 → invalid; no-source does not invalidate | Cached refusal with no citations survives |
| M13 | `app/deepsearch/service.py:104-151, 197-198, 221-249` | Only site search results; rejects <80 chars; no child/PDF following; no write-through | Academic Programmes/Hostel/Library shells rejected |
| M14 | `app/rag.py:494-503`; `app.py:129-137` | Stream path has no history; Streamlit passes none; planner gets fresh state | Follow-ups cannot resolve |
| M15 | `app/agent/text.py:72-92, 553-570` | `ConversationMemory.load/plan` unused; event writes skipped on cache/deep paths | Phase 6 is demo-only |
| M16 | `app.py:492, 580-626, 922-945` | First-run name gate and admission-intent lead capture can hijack knowledge questions | "Tell me about the university" → asks for a name |
| M17 | `app/sitegraph/store.py:280-284`; `scripts/nga_documents.py` | Shared SQLite `delete` journal; 30 s busy timeout; live DB locked during backfill | Reads failed with `database is locked` |
| M18 | `app/config.py` | Proposed flags `RETRIEVAL_MODE`, `VERIFY_MODE`, `TIME_SCOPE_STRICT`, `CACHE_BACKEND` missing | `grep` count 0 |

### 5.4 Process and engineering issues

#### P1. The roadmap put evaluation last

The README lists Phases 1–12 as implemented and Phase 13 ("Evaluation, dashboards, canary and default-on") as remaining. The user became the evaluator. The design set's own Definition of Done requires the evaluation set shape and acceptance gates to be agreed before implementation (`00-intent.md` §12), and M-01 is the North Star. Building 12 phases before an outcome-level evaluation is the process root cause of "multiple attempts didn't address the issue": each attempt was validated by unit tests and demos, not by the failing conversations.

#### P2. Tests validate components, not outcomes

- `tests/test_nga_text.py` builds a tiny fixture graph with one hostel page and injects a fake LLM. It asserts that a correct fake answer is verified and a wrong fake answer is blocked. It does not ask the real graph the real failing questions.
- `tests/test_dspmu_eval.py` validates that the 103-case golden set is well-formed and traceable. It does not run the agent against the set; it asserts `"every case is well-formed"` and `"PENDING PO SIGN-OFF"` only (`tests/test_dspmu_eval.py:13-39`).
- No test asserts that an `empty_shell` node is returned by search/navigate as a navigation result.
- No test asserts that "programs" (plural) is a programme intent.
- No test asserts that a PG programme mention does not block an answer.
- No test asserts that a refusal is not cached as `verified`.
- No test asserts that deep search triggers on low-relevance evidence.
- No test asserts a follow-up through the live Streamlit/`query_rag_stream` seam.

The unit tests are good at proving the new modules do what the author intended. They are not a regression suite for the user's experience.

#### P3. Status documents overstate completion

The amendment's status table says "Deep-search fallback — implemented", "Chunk + embedding hybrid — implemented", "Span-level verifier — implemented". The code exists, but the live path is gated/empty/off, and the follow-ups are listed in the same table ("Graph write-through is still a follow-up"; "live chunk backfill waits for the document job"). A reader can easily conclude the capability is live. The status vocabulary needs four states: **designed**, **coded**, **wired and enabled**, **verified end to end on the evaluation set**. Most of the recent work is at "coded".

#### P4. Fixes are compensation layers on broken upstream contracts

This is the pattern across the recent attempts:

```
broken intent/conversation
    -> add LLM planner (optional, off)
broken retrieval relevance
    -> add chunks/embeddings (empty/off; node-first)
broken answer adequacy
    -> add deep search (gated on empty bundle)
broken cache correctness
    -> add time-scope/revalidation (refusals cached as verified)
broken verification
    -> add span citations (UG detector blocks PG answers)
broken coverage
    -> add document backfill (same SQLite writer, no chunks yet)
```

Each layer assumes the previous gate produced a meaningful signal. None of them fixes the gate. The more layers added, the more silent fallbacks and the harder the system is to reason about.

---

## 6. Why each recent solution did not address the issue

| Recent solution | What it improved | Why the user still saw failure |
|---|---|---|
| Typed graph + lexical FTS (Phases 1–4) | Distinctive noun topics, citations, fee/VIP facts | Excludes empty shells; one-shot search; no conversation; broad questions need enumeration |
| Deep-search fallback | A live-site escape hatch | Trigger is `not bundle.items`; junk evidence suppresses it; shell pages rejected; no write-through; raw conversational query |
| Hybrid chunk + BM25 + embeddings | Passage-level recall in principle | Chunks were 0 at runtime; provider off; node hits first and truncated; no stemming; cache index version unchanged |
| Time-scoped cache | Prevents a December schedule answering January | Caches refusals as verified; no answer type; no chunk version; 24 h TTL; no-source revalidation valid |
| Span-level verifier | Checks a claim against its cited evidence | No claims → verified; UG-only programme detector maps PG→UG; a wrong citation blocks the whole answer; no repair |
| Archive/document backfill | More PDF text (841 done) | 2,548 pending/865 empty/198 blocked; no chunks yet; shared SQLite writer blocks the live path |
| LLM planner (optional) | Better intent/plan in principle | `NGA_TEXT_LLM_PLANNER=false`; deterministic planner is the live one; no navigate/enumerate plans |
| Conversation memory (Phase 6) | Follow-ups in the demo | Never loaded in the live path; Streamlit has no history; planner gets fresh state; events skipped on cache/deep paths |
| Profile/lead capture | Lead generation | First-run name gate can hijack open-ended questions; admission keyword starts a form instead of an answer |
| Time-scope module | Parses month/year/session | Only used for cache validity, not for retrieval or answer shaping; does not fix stale/irrelevant evidence |

The common thread: **the new component is downstream of the decision that failed.** Deep search is downstream of the evidence gate; chunks are downstream of node ranking; cache invalidation is downstream of cache writes; the verifier is downstream of generation and detector choice. The decision that failed is upstream.

---

## 7. The causal chain

```
User: "which programs currently university"
  │
  ├─ Planner: keyword classifier → PAGE_TOPIC (because "programs" ≠ "program")
  │     └─ one step: site.search, limit 3
  │
  ├─ Search: normalize → "programs currently"; FTS OR matches generic body text
  │     └─ top hits: Best Practices, NEP 2020, NEP 2020
  │
  ├─ Answerable filter: cms.academic-programmes / academic-programmes.ug|pg
  │     are empty_shell → excluded from search and navigate
  │
  ├─ Executor: auto-open the top HTML hit (CBCS/NEP)
  │
  ├─ Evidence: bundle non-empty (3 irrelevant items)
  │     └─ no relevance gate → adequate? yes (by accident)
  │
  ├─ Deep search: condition `not bundle.items` → false → never runs
  │     └─ the live site's `/search?q=programmes` would return the right pages
  │
  ├─ LLM prompt: "If the evidence is insufficient, say you could not verify it"
  │     └─ model refuses: "I couldn't verify which programmes are currently offered…"
  │
  ├─ Verifier: refusal has no critical claims → no failures → status=verified
  │
  ├─ Cache: verified + 24 h TTL + no chunk index in the version
  │     └─ next attempt returns the refusal without an LLM call
  │
  └─ User: "it doesn't answer even though the website has the information"
```

And the meta/conversational branch:

```
"can you search deep" / "do you understand when I ask…" / "hello"
  │
  ├─ No CHITCHAT/META/CLARIFY intent → PAGE_TOPIC (or DATE because of "when")
  ├─ site.search / site.latest on a conversational utterance
  ├─ irrelevant evidence or junk notice list
  ├─ LLM produces a plausible but non-responsive sentence
  ├─ verifier: no claims (or a supported claim) → verified
  └─ user sees a knowledge answer to a conversation move
```

And the PG/programme branch:

```
"can you check deep search to find university program"
  │
  ├─ Planner: PROGRAMME → site.search; evidence includes M.Sc. IT
  ├─ LLM answer mentions M.Sc. Information Technology [ev-N]
  ├─ Verifier: detect_program("M.Sc. Information Technology")
  │     → "Bachelor of Science in Information Technology (B.Sc.)"
  ├─ B.Sc. IT not in cited evidence → critical programme failure
  ├─ status=blocked, answer="" → render as "I could not find a verified answer…"
  └─ user sees a refusal; the actual cause is an alias collision in a UG fee table
```

---

## 8. What to do instead

The fixes should follow the causal order, not the layer order. Do not add another retrieval mechanism until the upstream contracts are explicit.

### 8.1 Make the four contracts explicit

#### Contract 1 — Conversation frame

Every turn must carry:

```
conversation_id, turn_id, channel, language,
dialogue_act (greeting | meta | thanks | backchannel | knowledge | followup | boundary | clarify),
resolved_query (with pronouns resolved),
topic (node/catalogue entity), programme, time_scope,
requested_shape (direct | list | comparison | how-to | confirmation)
```

The planner must receive this frame. The frame must be loaded from `ConversationMemory` for the live path, and updated only after a turn that produced a real answer/clarification. `hello`, `who are you`, `thanks`, `can you search deep` and `do you understand…` must not enter retrieval at all; they need a conversational response path.

#### Contract 2 — Retrieval adequacy

Replace "bundle is non-empty" with a predicate:

```
adequate = top_relevance >= T
           AND query_term_coverage >= C
           AND evidence_matches_requested_shape
           AND time_scope_covered_if_asked
           AND entity_catalogue_match_if_list
```

If not adequate, the system must do one of:

1. a different retrieval strategy (navigate section, enumerate catalogue, site `/search`, child links, PDFs);
2. deep search;
3. a clarification (ask which programme/department);
4. an explicit "not published in a machine-readable form" with the page URL, not a generic "I could not verify".

Deep search should trigger on `not adequate`, not `not items`.

#### Contract 3 — Answer state machine

Replace the overloaded statuses with an explicit state:

```
answered_verified        -> cache (positive, type=answer)
answered_rewritten       -> cache (positive, type=answer, note rewrite)
clarification            -> do not cache as an answer; cache as a short-lived clarification
refused_policy           -> do not cache as verified; deterministic message
refused_no_machine_text  -> include the published URL/contact; short negative cache
refused_low_confidence   -> ask a clarifying question or hand off
failed_generation        -> error path, not a refusal
```

Only `answered_verified` and `answered_rewritten` may be cached with a positive TTL. A refusal must never be stored under a positive answer status. The cache schema needs `answer_type`, `topic`, `evidence_signature`, `chunk_index_version`, `embedding_model`.

#### Contract 4 — Navigation answerability

Split `is_answerable`:

```
can_ground(node)  -> node.status == OK and not PII
can_navigate(node) -> node not blocked/removed/soft_404; an empty_shell is navigable
```

`site.search` may return shells with `content_status="empty_shell"` and `groundable=false`; `site.navigate` may return their children/links; `site.open` returns `body_available=false` and the links. The answer path must not use a shell as factual evidence, but the planner may use it as a route. This is the only way to reach the department pages/PDFs behind the Academic Programmes section.

### 8.2 Stop the bleeding (same day)

1. **Separate serving from indexing.** Stop the backfill against the serving DB or move it to a copy; enable WAL (`PRAGMA journal_mode=WAL`) and a short busy timeout; add a lock/health alarm. The serving process must never share a `delete`-journal SQLite writer with a batch job.
2. **Purge the bad cache.** Delete `nga_answer_cache` entries whose answer is a refusal or whose `answer_status` is `verified` but whose text matches a refusal pattern. Change `_write_answer_cache` to never store a no-claim refusal as `verified`.
3. **Add a relevance gate.** At minimum, compute a query-term coverage and top-score threshold in `assemble_evidence`; if below threshold, treat the bundle as empty for the deep-search decision and for the answer decision. Do not let three generic pages count as evidence.
4. **Trigger deep search on inadequate evidence.** Change `not bundle.items` to `not adequate` (or `not adequate or revalidation_invalid`). Use the site's `/search` endpoint as a first-class source; follow child links and PDFs; dedupe against the graph.
5. **Fix the programme detector collision.** Do not run the UG lead detector against generated answers as a universal programme claim. Either add a PG/PhD catalogue, or restrict the verifier's programme claim to programmes named in the cited evidence and the UG fee set, and never map a PG name to a UG canonical name. Add a regression test for `M.Sc. Information Technology`.
6. **Do not render blocked as no-evidence.** A blocked answer with evidence must say "I found a source but could not verify a specific claim" (or ask a clarifying question), not "the public university pages have no verified answer". Include the reason code in the turn event.
7. **Fix the meta/small-talk path.** Add `CHITCHAT`, `META` and `CLARIFY` intents that do not call retrieval. "hello", "who are you", "thanks", "can you search deep" and "do you understand…" must get a conversational reply.
8. **Fix plural/programme morphology.** Add `programs`/`programmes`/`courses` normalization at minimum; ideally use a porter/unicode61 FTS tokenizer plus a query-expansion alias map. Add tests for both spellings.

### 8.3 Fix the conversation contract (this week)

1. In `build_text_answer`, when `conversation_id` is present:
   - load `SessionState` via `ConversationMemory.load()` (or `.plan()`);
   - pass it to `Planner.plan()`;
   - pass the recent turns to the answer prompt;
   - record a turn event on every return path, including cache hits and deep search.
2. Add `history`/`conversation_id` to `query_rag_stream` and pass the Streamlit message history (or let the server load it from `conversation_id`). The server-side load is more reliable across channels.
3. Add the dialogue-act classifier before the retrieval planner. It can be deterministic for greetings/meta/thanks and use the LLM planner only for ambiguous knowledge questions.
4. Make the follow-up frame work end to end: "what about its fee?" after a hostel page must resolve the topic, programme and time scope, and the plan must open the current node before searching.
5. Move the Streamlit profile capture out of the answer path: answer the question first; ask for the name/email/phone only when the user wants a callback/offer, or make it skippable without hijacking short messages.

### 8.4 Fix navigation and the knowledge plane (next 1–2 weeks)

1. **Shells as navigation nodes** (Contract 4). Return them in search/navigate with `groundable=false`; expose children/links; let `site.open` say `body_available=false`.
2. **Use the site's own search** as a retrieval tool, not just a deep-search fallback. It already returns the right pages for the failing queries. Add relevance scoring, allowlist, dedupe, and a fetch policy that follows child links/PDFs.
3. **Build a structured programme/department catalogue** from department pages, PDFs and facts:
   ```
   programme(id, name, level UG/PG/PhD, department, duration, eligibility,
             fee_fact_id, page_url, document_urls, verified_at, source)
   ```
   This is what "list all programmes" needs. It also gives the verifier a correct universe and the planner a list/enumerate plan.
4. **Model the site's content shapes explicitly**: `page_text`, `navigation_shell`, `image_page`, `pdf`, `legacy_page`, `site_search_result`. For image/JS shells, decide one of:
   - OCR the published images (bounded, reviewed);
   - use the old site/PDF/department page;
   - render with a browser (out of scope for voice/chat latency, possibly an offline job);
   - hand off with the published URL and contact, clearly labelled.
   Do not pretend the HTML text is the site.
5. **Planner**: add `site.navigate`/`site.documents`/`site.follow` plans for broad/list questions; add a "list/enumerate" shape; make the LLM planner the default for text chat (with validated fallback), or add a deterministic route for departments/programmes.
6. **Index the site search results** into the graph/chunk index (write-through) so the next question does not repeat the same live search.

### 8.5 Fix verification and answer quality

1. Separate **claim verification** from **answer adequacy**. A response can be claim-safe but non-responsive; that is a refusal/clarification, not a verified answer.
2. Use a **programme catalogue** for programme claims, not the lead-capture UG fee detector. Compare against the cited evidence and the catalogue level (UG/PG/PhD).
3. Add a **repair loop** before refusal: if a critical claim fails, re-prompt with the failure and the cited evidence once; if it fails again, return a targeted clarification (e.g., "I found the M.Sc. IT page; do you mean the PG programme?"), not a generic no-evidence refusal.
4. Make citations **non-fatal to the whole answer** when the claim is supported elsewhere in the bundle; repair the citation, or accept the evidence-level support. The current span-only check turns a wrong `[ev-N]` into a total block.
5. Add a **refusal taxonomy** and surface it: `policy`, `no_machine_readable_source`, `low_confidence`, `ambiguous`, `stale`, `error`. The user and the operator need different messages and different cache rules.
6. Enforce `LLM_MAX_TOKENS` appropriate to the answer shape. 192 tokens is fine for voice, but it truncates lists and fee tables in chat. Use a per-channel/per-shape budget.

### 8.6 Fix operations, observability and evaluation

1. **Boot readiness report** (logged and exposed on `/health`):
   ```
   mode=agentic / deep_search=on / cache_revalidate=on
   graph=nodes=4802 pages=157 shells=158 docs=4461 done=841 pending=2548
   chunks=0 embeddings=0 embedding_provider=off
   scheduler=off last_compile=... last_refresh=...
   ```
   Warn if `NGA_ENABLED=true` but `NGA_MODE` is not agentic, if deep search is on but the graph has no chunks/docs, or if the serving DB is being written by a batch process.
2. **Turn trace with a reason code** for every path: `answered`, `cache_hit`, `deep_search`, `static_fallback`, `no_evidence`, `blocked`, `policy`, `error`. Include the retrieval scores, relevance-gate result, evidence IDs and cache key. M-09 should be enforceable.
3. **Outcome evaluation**, not component tests:
   - a frozen conversation set containing the exact failing utterances and their follow-ups;
   - expected shape: answer/clarification/refusal + required facts + forbidden claims + citations;
   - run against the live graph in CI/nightly;
   - metrics: grounded-answer rate, false-refusal rate, irrelevant-evidence rate, blocked rate, cache-hit correctness, follow-up success, p95 latency;
   - shadow mode for any retrieval change; canary before default-on.
4. **Deployment gates**: a phase is "done" only when (a) the code is wired in the live seam, (b) the flags are on in the target environment, (c) the index/store is populated, (d) the E2E evaluation passes, (e) the rollback is tested. Update the status vocabulary accordingly.
5. **Keep the indexer out of the serving DB** or use an atomic snapshot swap/Postgres. A live answer must never wait on a batch writer.

### 8.7 Acceptance tests for the next solution

These are the conversations that must pass before the next "fix" is called fixed. They are deliberately the observed failing cases plus the design's own examples.

| # | Utterance | Required behaviour |
|---|---|---|
| 1 | "which programs currently university" | A programme/department list from the catalogue or a clarifying question; never a verified refusal |
| 2 | "what programmes are offered" | Same, with UG/PG labelled; no NEP/NSS citations |
| 3 | "can you check deep search to find university program" | A conversational acknowledgement + deep search + answer, or an explicit "I checked the site, here is what I found"; never `blocked` rendered as no-evidence |
| 4 | "do you understand when I ask about program what is my intent of ask?" | A conversational clarification; no `site.latest`, no date intent |
| 5 | "hello" / "who are you" / "thanks" | Conversational reply, no retrieval, no citations |
| 6 | "what about its fee?" after a hostel/grievance topic | Resolves the current topic; opens the node; adds the fee evidence; no generic search |
| 7 | "list of postgraduate courses" | Either a PG programme list or an explicit policy answer that says PG fees/seats are out of scope, not a generic no-verification refusal |
| 8 | "library timings" | If the page is image-only: say the published page has no machine-readable timings, give the page URL/contact; never cite exam-timing PDFs as the answer |
| 9 | Repeat #1 twice | Second answer must not be a cached refusal; if the index changed, the answer must change |
| 10 | Same question in Hindi/Gujarati | Same evidence/citations and an equivalent answer |
| 11 | "vice chancellor" | Verified fact, correct name, citation |
| 12 | "exam schedule oct 2026" | Correct document/date; if the date is past, say so; no time-scope violation |
| 13 | Backfill running concurrently | p95 within budget; no silent static fallback; if fallback, the reason is surfaced |
| 14 | A hallucinated fee/VC name | Blocked, with a targeted message; the rest of the answer is not discarded |
| 15 | A legitimate answer naming M.Sc. Information Technology | Not blocked by a B.Sc. IT alias collision |

---

## 9. Mistake ledger: rules for the next solution

1. **Fix the contract before the machinery.** If the decision "is this evidence adequate?" is wrong, a better retriever, cache or verifier downstream will not save the turn.
2. **Measure the live path, not the component.** A unit test with a fake LLM and a one-page fixture is not evidence that the user's question works.
3. **Treat "irrelevant evidence" as equivalent to "no evidence"** for the purpose of exploration. The deep-search trigger must use a relevance/adequacy predicate.
4. **Never cache a non-answer as a positive answer.** A refusal is a result, not a verified fact.
5. **A flag is not a capability.** "Implemented" means wired, enabled, populated and verified end to end. Check effective config at boot and in CI.
6. **Do not share the serving store with a batch writer.** SQLite is not a queue; a backfill must not make a chat turn wait 30 seconds.
7. **Do not derive a universal entity detector from a fee table.** Programme detection (UG/PG/PhD, departments), lead capture and fee grounding are three different universes.
8. **A shell is a navigation node, not a non-existent node.** Distinguish "cannot ground" from "cannot navigate".
9. **Do not let the LLM's refusal be the only abstention signal.** Classify the answer state before generation; validate adequacy after generation; use a repair loop.
10. **Do not ship a retrieval upgrade without an E2E outcome test** on the exact failing conversations and the frozen evaluation set.
11. **Do not let the static path silently answer when the agent path fails.** Surface the path and reason; the static corpus is smaller and older.
12. **Do not confuse "the verifier found no false claim" with "the answer is correct".** Safety and adequacy are different axes.
13. **Do not let a status document create false confidence.** The README and amendment should say "coded" vs "live" and link the E2E evidence.
14. **Do not fix the site's content problem with retrieval alone.** If the page is an image/JS shell, HTML chunking and embeddings cannot see it. Decide the acquisition strategy per content shape.

---

## 10. Appendix — evidence index

### A. Observed turn events (conversation `2b2fc045-9236-47a6-9f5d-ef489fb3f1b6`)

| Turn | Question | Intent/plan | Evidence | Status |
|---|---|---|---|---|
| 1 | can you search deep | `page_topic` / `site.search` | M.Sc. IT + career PDFs | verified |
| 2 | which programs currently university | `page_topic` / `site.search` | Best Practices, NEP, NEP | verified, cached 24 h |
| 3 | do you undertstand when I ask about program what is my intent of ask? | `date` / `site.latest` + `site.search` | About pages + notices | verified, cached 24 h |
| 4 | can you check deep search to find university program | `programme` / `site.search` | Grievance Portal, Grievance Cell, M.Sc. IT | **blocked** |

### B. Answer cache entries observed

| Normalized query | Status | TTL | Answer summary |
|---|---|---|---|
| `programs currently` | verified | 24 h | "I couldn't verify which programmes are currently offered…" |
| `happending` | verified | 1 h | "I checked the official university website… I could not verify…" |
| `search deep` | verified | 24 h | "Yes—what would you like me to search for at DSPMU…" |
| `understand ask about program intent ask` | verified | 24 h | "Yes. I understand 'program' to mean an academic programme…" |
| `exam schedule oct 2026` | verified | 1 h | "The schedule lists exams on 13-10-2026." |
| `vice chancellor` | verified | 24 h | "The Vice Chancellor is Prof. Rajiv Manohar." |

### C. Search reproductions

```
Q: "what programmes are offered"  normalized: "programmes offered"
 1. 6.95 Cbcs Nep 2020
 2. 6.95 NEP 2020
 3. 6.95 Training & Placement Cell (Legacy)
 4. 6.38 NSS
 5. 6.37 NSS
 (Academic Programmes is excluded as empty_shell)

Q: "which programs currently university"  normalized: "programs currently"
 1. 6.45 Best Practices
 2. 6.43 Cbcs Nep 2020
 3. 6.43 NEP 2020
 4. 6.40 Vishvas
 5. 4.40 Placement Cell Notice

Q: "list of programmes"  normalized: "list programmes"
 1-6. 6.95 gallery pages (2026, Events, News Media Gallery, Photo Gallery, Video Gallery, Photo Gallery)
 ...
```

### D. Answerability reproductions

```
cms.academic-programmes          status=empty_shell  answerable=False
academic-programmes.ug           status=empty_shell  answerable=False
academic-programmes.pg           status=empty_shell  answerable=False
cms.hostel                       status=empty_shell  answerable=False
student-life.hostel              status=empty_shell  answerable=False

site.navigate("cms.academic-programmes") -> BLOCKED
site.search("academic programmes") -> Academic Council, NEP 2020, University at a Glance, CBCS NEP 2020, NSS
site.search("hostel") -> 2022 Hostel Allotment Fees PDF, University at a Glance, About pages
site.search("library timings") -> examination timing PDFs, digital library notice
```

### E. Programme detector collisions

```
M.Sc. Information Technology    -> Bachelor of Science in Information Technology (B.Sc.)
M.A. History                    -> Bachelor of Arts in History (BA)
M.Sc. Chemistry                 -> Bachelor of Science in Chemistry (B.Sc.)
M.Sc. Mathematics               -> Bachelor of Science in Mathematics (B.Sc.)
M.Sc. Botany                    -> Bachelor of Science in Botany (B.Sc.)
M.Sc. Zoology                   -> Bachelor of Science in Zoology (B.Sc.)
```

### F. Verifier reproduction

```
Answer: "Yes, I can search the official website. The university offers
         programmes such as M.Sc. Information Technology [ev-3]."
Result: status=blocked
Failure: kind=programme
         raw="Bachelor of Science in Information Technology (B.Sc.)"
         reason=programme_not_in_evidence
```

### G. Live site probes

| URL | Server HTML content | Graph status |
|---|---|---|
| `/cms/academic-programmes` | nav/footer + "Academic Programmes" shell (~1,000 chars after stripping) | `empty_shell` |
| `/academic-programmes/ug` | same shell | `empty_shell` |
| `/academic-programmes/pg` | same shell | `empty_shell` |
| `/facilities/central-library` | 11 images, alt "Library"; no timing text | `empty_shell` / missing |
| `/cms/central-library` | same | missing |
| `/student-life/hostel` | "Hostel Gallery" shell | `empty_shell` |
| `/cms/hostel` | same | `empty_shell` |
| `/search?q=programmes` | returns `/cms/academic-programmes`, `/academic-programmes/ug|pg`, department pages | not used except deep search |

### H. Effective config and runtime

```
NGA_ENABLED=True
NGA_MODE=agentic
DEEP_SEARCH_ENABLED=True
CACHE_REVALIDATE_ENABLED=True
NGA_TEXT_LLM_PLANNER=False
EMBEDDING_PROVIDER=off
LLM_MODEL=gpt-6-luna
LLM_MAX_TOKENS=192
NGA_TEXT_MAX_ITEMS=5
NGA_TEXT_MAX_CHARS=4000

RETRIEVAL_MODE / VERIFY_MODE / TIME_SCOPE_STRICT / CACHE_BACKEND: missing in config

Graph: nodes=4802 edges=9443 pages=157 body_fts=998
Documents: done=841 (+7 review), pending=2548, empty=865, blocked=198, error=2
Chunks: 0; chunk embeddings: 0
Answer cache: 6; turn events: 8
```

### I. Key file/line references

| Concern | File:line |
|---|---|
| Empty-shell answerability | `app/sitegraph/policy.py:42-56` |
| Search/navigate filter | `app/sitegraph/search.py:259,274`; `app/sitegraph/tools.py:211-214` |
| Planner intents | `app/agent/planner.py:46-64,117-139` |
| Planner steps | `app/agent/planner.py:197-287` |
| Search scoring | `app/sitegraph/search.py:127-213` |
| Chunk integration | `app/sitegraph/tools.py:327-408` |
| Deep-search trigger | `app/agent/text.py:501-505` |
| Evidence assembly | `app/agent/evidence.py:243-513` |
| Verifier programme claim | `app/agent/verifier.py:332-336,541-555,568-583` |
| Programme aliases | `app/programs.py:8-10,132-209` |
| Cache write | `app/agent/text.py:162-235` |
| Cache validity | `app/cache/answer_cache.py:22,224-313` |
| Revalidation | `app/cache/revalidate.py:74-104` |
| Conversation memory | `app/agent/session.py:40-83`; `app/agent/text.py:72-92,553-570` |
| Stream path history | `app/rag.py:494-503`; `app.py:129-137` |
| PG boundary | `app/site_router.py:327-356`; `app/rag.py:372-380` |
| Static fallback prompt | `app/rag.py:58` |
| SQLite connection | `app/sitegraph/store.py:280-284` |
| Golden-set validation only | `tests/test_dspmu_eval.py:13-39` |
| Agent tests with fake LLM | `tests/test_nga_text.py` |

---

## 11. One-paragraph conclusion

The recent solutions did not fail because they were badly built. They failed because they were built one layer too low. The system is deciding, before any of them run, that a conversation move is a knowledge query, that a shell page does not exist, that any retrieved text is adequate evidence, and that a refusal with no false claim is a verified answer. Those four decisions produce the exact user experience observed: meta questions get knowledge answers, programme questions get refusals, deep search never fires, legitimate answers are blocked by a UG alias table, and the refusal is cached for a day. The next solution should start by making those four decisions explicit — conversation frame, navigation answerability, evidence adequacy, and answer state/cache semantics — and only then re-enable the retrieval, chunking, embedding and deep-search machinery behind an end-to-end evaluation that uses the failing conversations as the acceptance test.
