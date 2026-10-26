# DSPMU Live-Site Navigation-Grounded Agent (NGA) — Design Set

**Status:** DESIGN ONLY — not approved, not implemented, no production code in this set.
**Date:** 2026-10-24
**Branch:** `feat/dspmu-rebrand`
**Engagement:** Brownfield — `/Users/aiwork/codebase/aichatagent`
**Supersedes:** the uncommitted `app/live_site.py` prototype as a design authority. The prototype is a spike, not the architecture.

---

## 1. Why this design set exists

The current assistant answers only from a committed snapshot of the DSPMU website. The build sees a 257-URL sitemap, 68 navigation nodes and 137 navigation links, but the runtime keeps only three prompt files, 11 extracted nodes and four routable page nodes. A question outside the fixed router vocabulary cannot reach the live site; the model is instructed to say it does not have the information.

The CEO-level requirement is:

> A user can ask any published detail of `https://dspmuranchi.ac.in` through web chat, inbound voice, outbound voice or WhatsApp, and receive a real-time, grounded, cited answer in the caller's language — while keeping a durable conversation record.

The design constraint is:

> **No RAG.** No vector database, no embeddings, no top-k chunk stuffing.

The selected architecture is a **Navigation-Grounded Agent**: a typed site graph, an MCP-style tool server, a two-speed planner, claim-level grounding, event-sourced memory and stale-while-revalidate freshness.

---

## 2. Document map

| Doc | Audience | Answers | Primary IDs |
|---|---|---|---|
| [`00-intent.md`](00-intent.md) | PO, all lenses | Why are we doing this, for whom, what does success mean, what alternatives were rejected? | `AS-NGA-*`, intent metrics |
| [`01-brd.md`](01-brd.md) | BA, PO, QA | What must the system do, and how will acceptance be judged? | `NGA-BRD-01` … `NGA-BRD-32`, `NGA-G*` |
| [`02-hld.md`](02-hld.md) | Architect, TPO, security | How is the solution structured, deployed and made safe? | `NGA-C*`, `NGA-ADR-*` |
| [`03-lld.md`](03-lld.md) | Engineers, QA | Exactly what modules, schemas, tool contracts, algorithms and tests are required? | `NGA-M*`, `NGA-T-*` |
| [`04-impact-analysis.md`](04-impact-analysis.md) | PO, architect, ops | What existing files, channels, data, tests and operations change; what are the risks and the rollback? | `NGA-R-*` |

---

## 3. Traceability chain

```
Intent metric / persona job
        │
        ▼
NGA-BRD-nn  (business requirement + acceptance criteria + gate)
        │
        ▼
NGA-Cnn / NGA-ADR-nnn  (HLD component / decision)
        │
        ▼
NGA-Mnn / NGA-T-nnn  (LLD module / test)
        │
        ▼
File + config + data impact, risk, rollback
```

Every BRD requirement names the HLD component that satisfies it and the LLD module/test that proves it. No requirement is satisfied by prose alone.

---

## 4. The one decision that governs the rest

**No RAG is a hard constraint, not a preference.** The design deliberately uses:

- a **typed navigation graph** instead of an embedding index;
- **lexical/FTS search plus graph traversal** instead of vector similarity;
- **tool calls** instead of chunk stuffing;
- **claim-level verification** instead of “the chunk was in the prompt”;
- **event-sourced memory** instead of re-sending the transcript.

“Retrieval” in the generic sense still exists: the agent retrieves nodes and page sections through tools. It is not Retrieval-Augmented Generation: no embedding model, no vector store, no similarity top-k, and no chunk corpus injected into the prompt.

---

## 5. Sign-off required before implementation

| # | Decision | Owner |
|---|---|---|
| 1 | Intent, success metrics, scope and non-goals | PO |
| 2 | Authority hierarchy: verified facts remain authoritative for fees/dates; live pages fill the long tail | PO + Registrar liaison |
| 3 | PII boundary: result/merit/selection and staff directories remain excluded | PO + privacy owner |
| 4 | PDF policy: background extraction only; no PDF fetch in the voice path | PO + architect |
| 5 | Channel latency budgets (`NGA-BRD-22`) | PO + architect |
| 6 | Conversation/evidence retention policy | PO + privacy owner |
| 7 | University permission, robots/Content-Signal use and correction workflow | University liaison |
| 8 | WhatsApp registration/opt-in policy and approved template outside the 24 h window | PO + Privacy + Ops |

Implementation does not begin until items 1–8 are recorded as decided in `04-impact-analysis.md` §20.

---

## 6. Phase 1 implementation status (2026-10-24)

Phase 1 is implemented as a standalone graph compiler. The answer path is not
connected; `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `app/sitegraph/models.py` | Typed node/edge/enums |
| `app/sitegraph/compiler.py` | Sitemap + nav-tree compiler, full ancestor chain |
| `app/sitegraph/store.py` | Atomic SQLite replace/read, compile-run metadata |
| `scripts/nga_compile.py` | Offline-cache CLI, coverage gate, `--allow-network` |
| `content/dspmu/nga_coverage.json` | Generated coverage report |
| `content/dspmu/nga_graph.sqlite3` | Generated graph (gitignored) |
| `tests/test_nga_compiler.py` | 16 tests, no network |
| `tests/fixtures/nga/` | Sitemap/nav fixtures |

Reference compile from the committed cache:

```
sitemap URLs:  257
nav nodes:     68  links: 137
nodes:         329 (sections 19, pages 308, documents 0, external 2, blocked 0)
coverage:      257/257 (100.0%) gate=PASS
departments:   51
```

Known Phase 1 limits, by design:

- No page bodies are fetched at compile time; `content_hash`, `fetched_at` and
  `verified_at` are filled by the Phase 3 refresh worker.
- No PDF documents are discovered from pages yet (`documents: 0`).
- 9 duplicate nav URLs are counted; the graph keeps one node per URL.
- The PII guard is off by default per the 2026-10-24 product decision. Set
  `NGA_PII_GUARD_ENABLED=true` and recompile to restore the exclusions.

## 7. Phase 2 implementation status (2026-10-24)

Phase 2 adds FTS search, deterministic ranking and the read-only tool server.
The answer path is still not connected; `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `app/sitegraph/search.py` | Query normalization, Hindi/Gujarati topic aliases, FTS candidates, deterministic ranking |
| `app/sitegraph/tools.py` | `site.search`, `site.navigate`, `site.open`, `site.latest`, `site.documents`, `site.freshness`, `site.follow` |
| `app/sitegraph/notices.py` | Read-only notice catalogue for `site.latest`, with the PII title guard |
| `app/sitegraph/policy.py` | URL + title PII guard shared by compile, search and tools |
| `app/sitegraph/store.py` | FTS5 index rebuilt atomically with the graph; graph reads |
| `scripts/nga_search.py` | Offline search CLI |
| `tests/test_nga_search.py` | 15 tests, no network |

Smoke results from the real cache:

```
"hostel"        -> cms.hostel, student-life.hostel
"vice chancellor" -> page.vice-chancellor first
"छात्रावास"      -> cms.hostel (Hindi alias)
"results"       -> published results page (guard off)
```

Known Phase 2 limits:

- `site.open` returns metadata only until a page is refreshed in Phase 3.
- `site.latest` reads the committed notice JSONL; no live notice refresh yet.
- Duplicate canonical pages are deduplicated by content hash in Phase 3.

## 8. Phase 3 implementation status (2026-10-24)

Phase 3 adds page fetch/extract, a body index, freshness state, `site.open`
body content and canonical deduplication. It remains background-only; the
answer path is not connected and `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `app/sitegraph/fetch.py` | Allowlisted HTTPS fetch, conditional GET, redirect revalidation, robots, size/type caps, soft-404 rejection |
| `app/sitegraph/extract.py` | Main-content extraction, links, tables, PDF link metadata, stable content hash |
| `app/sitegraph/refresh.py` | Due-node refresh, page versions, unchanged-content revalidation, fidelity stats, canonical dedupe |
| `app/sitegraph/store.py` | `nga_page_version`, `nga_fetch_state`, `nga_body_fts`, canonical IDs |
| `scripts/nga_refresh.py` | Background CLI, `--dry-run`, `--allow-network`, explicit node IDs |
| `tests/test_nga_fetch.py` | 11 tests |
| `tests/test_nga_extract.py` | 4 tests |
| `tests/test_nga_refresh.py` | 8 tests |

Real smoke (small, explicit sample; no mass crawl):

```
--node-id cms.hostel --node-id student-life.hostel
  -> 2 empty shells (the live hostel pages contain only the title and Gallery)

--node-id student-life.grievance-redressal-cell
--node-id cms.about-the-university
  -> 2 fetched, 2 changed, body indexed

site.search "complaint status" -> Grievance Redressal Cell, body snippet
site.open                      -> body_available true, content_status indexed
site.freshness                 -> content_hash, checked_at, next_due_at
```

**Product decision — PII guard.** The chat is part of the public website and
the university already publishes these pages, so `NGA_PII_GUARD_ENABLED=false`
is the default. The 69 previously blocked URLs are part of the graph and the
tool results. Set the variable to `true` and run `scripts/nga_compile.py` to
restore the conservative URL/title exclusions. The current recompile reports
`blocked: 0`.

Known Phase 3 limits:

- PDF bodies are not extracted; PDF links are stored as document metadata.
- OCR and review-only text remain a later phase.
- Two live hostel pages are genuine empty shells; the graph records
  `empty_shell` rather than inventing content.
- The canonical duplicate rule requires identical title **and** content hash.

## 9. Phase 4 implementation status (2026-10-24)

Phase 4 adds the deterministic/structured planner, the bounded executor and
typed evidence/citation assembly. It is not wired into the answer path and no
answer is generated; `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `app/agent/models.py` | Intent, Plan, PlanStep, SessionState |
| `app/agent/planner.py` | Deterministic intent/plan, follow-up resolution, schema validation, structured LLM-plan fallback |
| `app/agent/executor.py` | Step/byte/deadline budgets, auto-open, sync and async execution |
| `app/agent/facts.py` | Verified fee/contact lookup from `facts.json` |
| `app/agent/evidence.py` | Evidence items, citations, verified-fact precedence, prompt/citation rendering |
| `scripts/nga_ask.py` | Plan + execute + evidence demo, no LLM |
| `tests/test_nga_planner.py` | 16 tests |
| `tests/test_nga_executor.py` | 9 tests |
| `tests/test_nga_evidence.py` | 9 tests |

Real demo (no LLM):

```
"What is the BA History fee?"
  plan: fee -> site.search
  execution: ok (2 steps)
  evidence: verified fee fact first (₹3,410 GEN), then fee/history pages
  citation: fee PDF + page URLs

"complaint status"
  plan: page_topic -> site.search
  execution: ok (2 steps)
  evidence: Grievance Redressal Cell body, verified 2026-10-25

"latest notice"
  plan: notice -> site.latest
  evidence: three current non-PII notices with PDF URLs

"MBA fee"
  plan: boundary -> no tool call
```

Phase 4 limits:

- The planner does not call an LLM by default; the structured LLM path is a
  validated adapter that falls back deterministically.
- Evidence assembly does not yet verify generated claims; that is Phase 5.
- No channel/prompt integration, no session event persistence, no reference
  delivery yet.

## 10. Phase 5 implementation status (2026-10-24)

Phase 5 adds claim-level verification, answer generation and prompt assembly.
It is exercised by a CLI demo but is still not wired into the channel answer
path; `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `app/agent/verifier.py` | Amount/date/URL/email/phone/programme claim extraction; verified-fact requirement for fees; critical-failure block; URL sentence rewrite |
| `app/agent/answer.py` | Evidence-grounded prompt, injected/default LLM generation, no-evidence refusal, answer+citation rendering |
| `scripts/nga_answer.py` | Dry-run prompt inspection and full generate→verify demo |
| `tests/test_nga_verifier.py` | 11 tests |
| `tests/test_nga_answer.py` | 10 tests |

Dry-run on the real graph (no LLM call):

```
"What is the BA History fee?"
  plan: fee -> site.search
  execution: ok (2 steps)
  system prompt: verified fee fact (₹3,410 GEN, ₹3,185 reserved) first,
                 then <site_evidence> with fee/history pages
  verifier: fee amounts must come from the verified fact; page dates,
            URLs, emails and phones must match evidence
```

Verifier policy:

- Fee amounts require a `verified_fact` item; a page-only amount is blocked.
- Dates, URL, email, phone and programme names must appear in evidence.
- Any critical failure returns `status=blocked` and an empty answer.
- A URL-only failure removes the sentence and returns `status=rewritten`.
- Every returned answer carries the bundle citations.

Phase 5 limits:

- The default LLM path is implemented but not exercised in CI; tests inject a
  fake LLM. A live key/cost smoke is a deployment step.
- No streaming generation, no channel integration, no event persistence.
- The verifier is deterministic and conservative; it removes a critical answer
  rather than asking the model to repair it.

## 11. Phase 6 implementation status (2026-10-24)

Phase 6 adds event-sourced conversation memory and automatic follow-up
planning. A durable `nga_turn_event` row carries the plan, tool calls, evidence
IDs, citations, answer status, latency and typed session state. The next turn
loads that state and resolves “it/that/what about the fee?” against the prior
topic. Still no channel integration; `NGA_ENABLED=false`.

| Deliverable | Status |
|---|---|
| `nga_turn_event` table | Conversation-scoped turn IDs, plan/tool/evidence/citation JSON, session state |
| `app/agent/session.py` `ConversationMemory` | Load state, record events, replay, purge/clear, follow-up plan |
| `scripts/nga_session.py` | Offline two-turn dry-run demo |
| `tests/test_nga_session.py` | 9 tests |

Real two-turn demo:

```
turn 1: "complaint status"
  plan: page_topic -> site.search
  state after: topic = Grievance Redressal Cell

turn 2: "what about its fee?"
  state before: topic = Grievance Redressal Cell
  plan: fee -> site.open (current topic) + site.search
  execution: ok (2 steps)
  state after: topic preserved
```

Phase 6 limits:

- Events are recorded by the demo/session service, not yet by live channels.
- `next_turn_id` is a read-then-insert; concurrent turns in one conversation
  need a transaction/unique-retry when the channels are connected.
- No event retention scheduler yet; the purge method and `NGA_EVENT_RETENTION_DAYS`
  are in place.

## 12. Phase 7 implementation status (2026-10-24)

Phase 7 connects the NGA observer to the existing answer seam in shadow mode.
The returned bundle is byte-identical to the static path; a bounded background
runner records what NGA would have planned, retrieved and optionally answered.
Shadow is active only with `NGA_ENABLED=true` and `NGA_MODE=shadow`; the
default remains off.

| Deliverable | Status |
|---|---|
| `app/agent/shadow.py` | Bounded worker pool, sampling, JSONL record, query redaction, optional generate/verify |
| `app/rag.py` | Zero-cost shadow hook at `context_bundle_for_query`; no bundle change |
| `NGA_SHADOW_*` config | Log path, workers, sample rate, query logging, deadline/steps/chars, generate |
| `tests/test_nga_shadow.py` | 9 tests |

Real shadow turn:

```
"complaint status"
  plan: page_topic -> site.search
  execution: ok (2 steps)
  evidence: student-life.grievance-redressal-cell
  delta: new_ids = [student-life.grievance-redressal-cell]
  query logged: None (query_chars=16, query_hash=...)
```

Phase 7 limits:

- The shadow delta is evidence-level. A generated answer is recorded only when
  `NGA_SHADOW_GENERATE=true`; the live answer is never changed.
- Shadow records are JSONL, not yet a dashboard or evaluation gate.
- Channel/session IDs are not attached to shadow records yet; that arrives with
  the channel integration phases.
- The runner is process-local; a restart drops in-flight observations.

## 13. Phase 8 implementation status (2026-10-24)

Phase 8 adds the voice fast path. When `NGA_ENABLED=true` and
`NGA_MODE=fast` (or `agentic`), a voice turn uses the deterministic planner,
at most two local tool calls and a hard deadline, then appends cited evidence
to the stable static core cards. Text channels are unchanged.

| Deliverable | Status |
|---|---|
| `app/agent/voice.py` | Deterministic plan + bounded executor + evidence block; static fallback on no evidence/deadline/error |
| `app/rag.py` | Voice-only branch in `query_rag` and `query_rag_stream`; text chat unchanged |
| `NGA_VOICE_*` config | 1,500 ms deadline, 2 steps, 1,200-char evidence budget |
| `tests/test_nga_voice.py` | 8 tests |

Real fast-path timings (no LLM):

```
"complaint status"        -> voice_evidence, 1 item, 42 ms
"BA History fee"          -> voice_evidence, 3 items (verified fact first), 7.5 ms
"latest notice"           -> voice_evidence, 3 notices, 8.9 ms
"quantum flux capacitor"  -> no_evidence fallback, static bundle
```

Phase 8 limits:

- The fast path uses the existing voice grounding guard; there is no
  post-stream claim verifier on voice (the verifier is used for the text demo).
- The deadline is enforced on the tool loop; LLM/TTS latency remains the
  existing voice path's concern.
- Session/turn events are not recorded from the live voice handler yet.
- `NGA_MODE=agentic` still uses the voice fast path; text agentic is Phase 9.

## 14. Phase 9 implementation status (2026-10-24)

Phase 9 adds the text agentic path for chat/WhatsApp. When
`NGA_ENABLED=true` and `NGA_MODE=agentic`, text turns use the bounded tool
loop, evidence bundle, answer generation and claim verifier. The deterministic
planner is the default; a structured LLM planner is optional.

| Deliverable | Status |
|---|---|
| `app/agent/text.py` | Plan → bounded tools → evidence → answer → verifier; safe refusal on blocked/failed generation |
| `app/rag.py` | `agentic_text` branch in sync and streamed seam; static fallback on no evidence |
| `app/pipeline.py`, `app/main.py`, `app.py` | WhatsApp and Streamlit opt in only when `NGA_ENABLED=true` |
| `NGA_TEXT_*` config | 8 s deadline, 8 steps, 4,000-char evidence, optional LLM planner |
| `tests/test_nga_text.py` | 10 tests |

Real text demo with a fake LLM (no API call):

```
"complaint status"
  -> verified; Grievance Redressal Cell page + citation

"What is the BA History fee?"
  -> verified; ₹3,410 GEN / ₹3,185 reserved from the verified fact

same question, hallucinated ₹9,999
  -> blocked; safe registrar-contact refusal
```

Phase 9 limits:

- The agentic text path generates a complete answer and verifies it before
  returning. Streamlit therefore receives one rendered block, not a token
  stream, when agentic mode is on.
- `NGA_TEXT_LLM_PLANNER=false` is the default; enabling it adds a planning
  model call before the bounded tools.
- Profile fields are not yet inserted into the agentic text prompt; WhatsApp
  history is passed, but the full Phase 6 session service is not wired into
  the live channels yet.
- No event recording from live text channels yet.

## 15. Phase 10 implementation status (2026-10-24)

Phase 10 adds the single-writer refresh worker, its run report/history and
deterministic ops alerts. It is a separate process behind
`NGA_SCHEDULER_ENABLED=false`; enabling `NGA_ENABLED` never starts background
fetching. The on-call procedures are in `doc/ops/nga-runbook.md`.

| Deliverable | Status |
|---|---|
| `app/sitegraph/scheduler.py` | Advisory file lock, one-cycle refresh, latest report, JSONL history, graceful stop |
| `app/sitegraph/metrics.py` | Run record with stats, graph coverage, freshness counters; atomic report and history readers |
| `app/sitegraph/alerts.py` | Coverage, staleness, failure-streak, rate and PII-guard rules; JSONL alert sink |
| `app/sitegraph/store.py` | `node_status_counts` and `fetch_summary` (never checked, failing, due, stale) |
| `scripts/nga_scheduler.py` | Worker CLI plus offline `--status`; refuses to run without the flag and network opt-in |
| `doc/ops/nga-runbook.md` | Start/stop, alert catalog, incident runbooks, backup/restore, rollback |
| `tests/test_nga_ops.py` | 22 tests |

Offline status on the compiled graph:

```
$ .venv/bin/python scripts/nga_scheduler.py --status
NGA refresh status
  last run:      none (offline snapshot)
  graph:         329 nodes, coverage 100.0% (gate PASS), compiled 2026-10-25T02:14:42Z
  freshness:     306 tracked, 2 checked, 0 failing, 0 stale, 0 due
  alerts:        none
```

Alert rules: `coverage_below_gate`, `coverage_drop`, `section_missing`,
`graph_missing`, `graph_stale`, `refresh_failure_streak`,
`node_failure_streak`, `refresh_error_rate`, `empty_shell_spike`,
`soft_404_spike`, `stale_backlog` and `pii_guard_mismatch`.

Phase 10 limits:

- It does not recompile the graph automatically. `graph_stale` asks an operator
  to compile, because `replace_graph` clears page bodies and freshness.
- Alerts are local JSONL; there is no built-in dedupe, retry or paging
  integration.
- The graph store is SQLite with one writer. A multi-host deployment needs
  Postgres first.
- Conversation-event retention still has no automatic purge job.

## 16. Phase 11 implementation status (2026-10-24)

Phase 11 adds the background PDF/OCR index. Document text is fetched and
extracted only by the worker; the live answer path reads stored text. OCR text
is stored with `needs_review` and is not indexed until a named reviewer
approves it with `scripts/nga_documents.py --review`.

| Deliverable | Status |
|---|---|
| `app/sitegraph/documents.py` | Discovery, bounded fetch, PDF extraction, OCR review gate, document stats |
| `app/sitegraph/store.py` | `nga_document` table, document nodes/edges, FTS, review workflow, summary counters |
| `app/sitegraph/fetch.py` | `fetch_document` with PDF content type, magic-byte check and separate byte cap |
| `app/sitegraph/tools.py` | `site.documents` metadata, `site.open` document text/tables, freshness fields |
| `app/agent/executor.py` | Auto-open of indexed PDF search hits |
| `scripts/nga_documents.py` | Offline status, discovery, indexing and review CLI |
| Scheduler / alerts | Document pass per cycle, `document_error_rate`, `document_review_backlog` |
| `tests/test_nga_documents.py` | 17 tests |

The indexing path:

```
indexed page HTML
  -> latest page version's PDF links
  -> node + documents edge + nga_document(pending)
  -> fetch_document (HTTPS allowlist, PDF magic, max bytes)
  -> extract_pdf (first N pages, OCR fallback)
  -> done + body FTS          (text answerable)
     done + needs_review      (stored, not indexed, not answerable)
     empty                    (too little text, not indexed)
     blocked                  (content PII; text discarded)
```

Commands:

```bash
.venv/bin/python scripts/nga_documents.py --status
.venv/bin/python scripts/nga_documents.py --discover-only
.venv/bin/python scripts/nga_documents.py --allow-network --limit 5
.venv/bin/python scripts/nga_documents.py --list-review
.venv/bin/python scripts/nga_documents.py \
  --review doc.<id> --reviewer "registrar office"
```

The scheduler runs the document pass when `NGA_PDF_INDEX_ENABLED=true`; the
default is `false`. `site.open` on an approved document returns its text and
table JSON; on an OCR document awaiting review it returns metadata and
`content_status: needs_review` with no text.

Phase 11 limits:

- Only PDFs linked from already-indexed pages are auto-indexed. A sitemap PDF
  with no page link is indexable only with an explicit `--node-id`; the site is
  not crawled for attachments.
- Extraction keeps one current text per PDF; there is no document version
  history. A failed refresh keeps the last good text.
- Only the leading `NGA_PDF_MAX_PAGES` pages are read; OCR is capped by
  `NGA_PDF_OCR_MAX_PAGES`.
- The scheduler never approves OCR. Review is a human CLI action; unreviewed
  text remains unanswerable.
- A graph compile deletes document nodes and extracted text; re-warm and
  re-review after a compile.
- PDF table JSON is returned by `site.open` but is not yet used as evidence by
  the answer path.

## 17. Phase 12 implementation status (2026-10-25)

Phase 12 adds channel-specific website references and voice→WhatsApp delivery.
Cited agentic answers on Streamlit/chat show Markdown links; WhatsApp shows
plain title, snippet and exact URL; voice never reads a URL aloud. After a
cited voice turn, the delivery worker checks WhatsApp registration and sends
the references plus call context when `NGA_REFERENCE_DELIVERY_ENABLED=true`.

| Deliverable | Status |
|---|---|
| `app/references/formatting.py` | Reference extraction, snippet cap, phone normalization, WhatsApp 1,600-char split, voice message builder |
| `app/references/delivery.py` | Registration, opt-out, dedupe, per-call cap, throttle, template fallback, retries, delivery worker |
| `app/messaging.py` | Approved-template sender; Twilio logs mask numbers to the last four digits |
| `app/sitegraph/store.py` | `nga_reference_delivery`, summary/window counts, purge |
| `app/agent/answer.py`, `app/agent/text.py` | Channel-aware rendering; cited text turns write `nga_turn_event` |
| `app/agent/voice.py`, `app/context_bundle.py` | Citations travel with the voice bundle; batch-path sink avoids ContextVar loss |
| `app/voice_handler.py`, `app/main.py` | Per-call identity, turn queue, call-end flush, STOP/START consent |
| `scripts/nga_references.py` | Status, list and purge for the delivery log |
| `scripts/bench_nga_events.py`, `tests/test_nga_persistence.py` | Turn-event write benchmark and p95 guardrail |
| `doc/ops/conversation-storage.md` | Store map and measured write cost |
| `tests/test_nga_references.py` | 17 tests |

Inline references:

- Chat/Streamlit: `**Sources**` with `[title](url) — "snippet"`.
- WhatsApp: `Sources:` with title, exact URL and quoted snippet; if the reply
  exceeds 1,600 characters, the reference block is sent as a second message
  without delaying the webhook response.
- Voice: no inline URL; delivery is the voice→WhatsApp path.

Voice delivery:

```
cited voice turn
  -> in-memory request (never blocks the call)
  -> worker: registration -> dedupe -> cap -> throttle
  -> inside 24 h: free-form message
     outside 24 h + template SID: approved template {{1}}
     outside 24 h without template: template_required
  -> record sent/skipped/template_required/failed
```

The delivery log holds no message body and no full phone number: only a phone
hash, last four digits, reference node/URL and status. `--status` on the
scheduler and `scripts/nga_references.py` show the counts.

Measured turn-event write on 2026-10-25 (200 iterations, local SQLite):
p50 **1.751 ms**, p95 **3.567 ms**, p99 **10.930 ms**. At p95 this is
**0.045%** of the 8,000 ms chat budget; details are in
`doc/ops/conversation-storage.md`.

Phase 12 limits:

- Voice turns still do not write `nga_turn_event`; Postgres `conversations` is
  the full transcript and `nga_reference_delivery` is the delivery audit.
- The delivery queue and `call_end` pending batch are in memory. A restart
  loses queued work; completed attempts remain in the log.
- The 24-hour-window template expects an approved Twilio template with a
  single `{{1}}` variable.
- The WhatsApp second-message reference follow-up has no retry; the voice
  delivery worker does retry.
- No live Twilio/WhatsApp send or approved-template test has been performed.

## 18. Remaining work

| Phase | Scope | Rough effort |
|---|---|---|
| 13 | Evaluation, dashboards, canary and default-on | 2 weeks |

**Total remaining: roughly 2–3 weeks for a small team**, excluding university
permission and sign-off waits. Phases 1–12 are implemented and tested; the live
channel paths remain opt-in behind `NGA_ENABLED=false` and
`NGA_REFERENCE_DELIVERY_ENABLED=false`.

## 19. Prototype disclosure

Before this design set was requested, an uncommitted spike was written:

- **New:** `app/live_site.py`, `tests/test_live_site.py`
- **Modified:** `app/config.py`, `app/config_truth.py`, `app/context_bundle.py`, `app/rag.py`, `app/site_router.py`, `tests/conftest.py`, `.env.example`, `.env` (gitignored)

Its findings are used in this design; its code is **not** the accepted architecture. The disposition is recorded in `04-impact-analysis.md` §14. It must not be merged as-is.
