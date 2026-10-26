> **Lens:** Architect + BA + Ops + Security · **Decided by:** Architect, reviewed by BA, security and the operator · **Inputs:** `00-intent.md`, `01-brd.md`, `02-hld.md`, `03-lld.md`, codebase audit 2026-10-24 · **Defines:** `NGA-R-01` … `NGA-R-16`

# Impact Analysis — DSPMU Live-Site Navigation-Grounded Agent (NGA)

## 1. Executive Summary

The change is **additive at the answer seam and invasive in the knowledge plane**. It does not rewrite the channels, the LLM backend, the CRM or the database schema for existing entities. It adds a navigation graph, a tool server, a planner/executor, an evidence verifier, an event store and a reference-delivery path, then connects them through the existing `app/rag.py` seam behind a master flag.

**Overall risk: MEDIUM-HIGH — manageable with phased rollout and a hard kill switch.**

Highest risks:

- **R-01 — live-site structure change** silently removes pages from the graph.
- **R-04 — PII exposure** from pages the site publishes but the assistant must not surface.
- **R-05 — voice latency regression** if live retrieval is allowed to block.
- **R-11 — WhatsApp reference sent to an unregistered or opted-out caller.**
- **R-03 — prompt injection** through fetched page text.

The design deliberately keeps `NGA_ENABLED=false` as the default until all gates pass. With the flag off, the existing static behaviour is unchanged.

## 2. Definition of the Change

| Item | From | To |
|---|---|---|
| Knowledge source | Committed snapshot (`content/dspmu/*`) only | Committed snapshot + live site graph + background document index |
| Retrieval | Fixed router vocabulary (`app/site_router.py`) + full static bundle | Graph navigation + FTS + constrained tools; static bundle remains fallback |
| Navigation hierarchy | Build-report counts only | Persistent `site_node`/`site_edge` graph with metadata |
| Answer grounding | Context presence + existing voice grounding | Claim-level verifier with citations |
| Conversation history | Transcript rows + short prompt window | Event-sourced turn record + typed session state |
| Freshness | Build-time snapshot | TTL, sitemap/nav diff, conditional GET, content hash |
| Chat/WhatsApp citations | Not a first-class feature | Title + snippet + href rendered from evidence |
| Voice references | None | WhatsApp delivery to registered callers, with call context |
| Rollout | Static behaviour | Flagged `off/shadow/fast/agentic` |
| RAG | Removed already | Remains removed; no vector/embedding dependency |

## 3. Current-State Baseline

| Area | Evidence | State |
|---|---|---|
| Answer seam | `app/rag.py:191` `context_bundle_for_query`; `query_rag`/`query_rag_stream` | Single seam, static bundle |
| Router | `app/site_router.py:47-52` (`_SAFE_NODES`), `:708-717` fallback | Network-free, fixed vocabulary |
| Prompt corpus | `content/dspmu/core_cards.md`, `site_map.md`, `recent_notices.md` | 3 files, 2,461 tokens |
| Facts | `content/dspmu/facts.json` | 43 programmes |
| Notices | `content/dspmu/notices.jsonl`, `notices.sqlite3` | 1,520 non-PII feed; 1,521 FTS rows |
| Nodes | `content/dspmu/nodes/` | 11 files |
| Build report | `content/dspmu/build_report.json:99-102` | 257 sitemap URLs, 68 nav nodes, 137 nav links, 51 departments |
| Chat | `app.py:123` → `query_rag_stream` | Static context |
| WhatsApp | `app/main.py:2865` `_whatsapp_chat_rag` → `run_rag_query_sync` | Static context + 6-turn history |
| Voice | `app/voice_handler.py:3174` → `query_rag_stream` | Static context + 12-line in-call history |
| History | `app/leads/schema.py:34-47`; `app/main.py:2821-2845`; `app/main.py:5425` | Persisted transcripts, short retrieval window |
| MCP | `app/mcp/tools.py` | Lead tools only |
| WhatsApp send | `app/messaging.py:19` `send_whatsapp_message` | Reusable sender; no reference delivery |
| Registration | `leads.phone_number`; `conversations.channel='whatsapp'` | No explicit opt-in column today |

## 4. File-by-File Impact Inventory

Legend — **Severity:** 🔴 Critical (breaks a flow or violates policy) · 🟠 High (visible wrong behaviour) · 🟡 Medium (operational/complexity) · 🟢 Low (cosmetic/docs).

### 4.1 Answer and retrieval core

| # | File | Current | Impact if unchanged | Action | Severity |
|---|---|---|---|---|---|
| 1 | `app/rag.py` | Static bundle assembly; PG boundary | No live coverage; no citations; no event record | Add NGA branch at `context_bundle_for_query`; keep static fallback; pass session | 🔴 |
| 2 | `app/rag.py:query_rag_stream` | Synchronous bundle assembly on the loop | Live fetch would freeze the event loop | Bundle assembly in `asyncio.to_thread`; bind bundle on the loop | 🔴 |
| 3 | `app/site_router.py` | Fixed vocabulary, 4 safe nodes | Unchanged behaviour leaves 253 URLs unreachable | Retain as deterministic fast path; add `kind`; stop using it as the only retriever | 🟠 |
| 4 | `app/doc_context.py` | Full static bundle; manifest | Must remain the fallback | No breaking change; ensure `document_bundle()` still works with flag off | 🟢 |
| 5 | `app/context_bundle.py` | `text`, IDs, URLs, `valid_until` | No evidence/citation/session fields | Add `kind`; optionally add evidence/citation handles | 🟠 |
| 6 | `app/pipeline.py:85` `build_rag_prompt_parts` | Builds static prompt | Some text surfaces bypass the new seam | Pass session/evidence; keep signature backward-compatible | 🟠 |
| 7 | `app/pipeline.py:339` `run_rag_query_sync` | Calls `query_rag` | Background/voice priority unaffected | No change beyond session threading | 🟢 |
| 8 | `app/voice_grounding.py` | Existing grounding guard | Single-document assumptions | Extend to evidence spans/citations; keep existing checks | 🟠 |

### 4.2 New packages (planned)

| # | Path | Purpose | Severity if missing |
|---|---|---|---|
| 9 | `app/sitegraph/models.py` | Typed records | 🔴 design cannot be implemented |
| 10 | `app/sitegraph/store.py` | Postgres/SQLite graph + FTS | 🔴 |
| 11 | `app/sitegraph/compiler.py` | Sitemap/nav compile | 🔴 |
| 12 | `app/sitegraph/search.py` | Ranking | 🔴 |
| 13 | `app/sitegraph/tools.py` | Tool API | 🔴 |
| 14 | `app/sitegraph/policy.py` | Allowlist/PII/SSRF | 🔴 |
| 15 | `app/sitegraph/fetch.py` | HTTP/conditional GET | 🔴 |
| 16 | `app/sitegraph/extract.py` | HTML/PDF extraction | 🟠 |
| 17 | `app/sitegraph/refresh.py` | Freshness worker | 🟠 |
| 18 | `app/sitegraph/evidence.py` | Evidence/verifier | 🔴 |
| 19 | `app/agent/planner.py` | Two-speed planner | 🔴 |
| 20 | `app/agent/executor.py` | Bounded execution | 🔴 |
| 21 | `app/agent/session.py` | Typed state/follow-ups | 🟠 |
| 22 | `app/agent/events.py` | Turn/fetch/tool events | 🟠 |
| 23 | `app/references/delivery.py` | Inline + voice→WhatsApp references | 🟠 |
| 24 | `scripts/nga_refresh.py` | Compile/refresh CLI | 🟠 |

### 4.3 Channel adapters

| # | File / function | Current | Impact | Action | Severity |
|---|---|---|---|---|---|
| 25 | `app.py:123` `query_rag_stream` | Chat answer | No inline references | Pass session; render title+snippet+href | 🟠 |
| 26 | `app.py:218-245` `_log_and_score_async` | Logs transcript | No evidence/citations | Include event/evidence IDs (PII-safe) | 🟡 |
| 27 | `app/main.py:2865` `_whatsapp_chat_rag` | WhatsApp answer | No inline references | Render references; pass typed state | 🟠 |
| 28 | `app/main.py:3206` WhatsApp webhook | Text reply | No STOP/opt-out handling for proactive refs | Add STOP/opt-out handling; set `whatsapp_opt_out_at` | 🟠 |
| 29 | `app/main.py:1173` `process_utterance` | Voice turn | No citation/enqueue | Return citations; enqueue reference delivery | 🟠 |
| 30 | `app/main.py:1173` caller phone/`call_sid` | Available from Twilio context | Not passed to delivery | Thread `From`/`call_sid` into the reference request | 🟠 |
| 31 | `app/voice_handler.py:3174` | Streams answer | No evidence in the turn | Attach citations to the turn result/event | 🟠 |
| 32 | `app/voice_handler.py:1154`, `3283` | 12-line history | Retains only text | Add typed state alongside history | 🟡 |
| 33 | `app/messaging.py:19` | Sends WhatsApp | No reference formatter/registration | Reuse sender; add `app/references/delivery.py` | 🟡 |

### 4.4 Data

| # | Object | Change | Impact | Severity |
|---|---|---|---|---|
| 34 | `leads` table | Add `whatsapp_opt_in`, `whatsapp_opt_out_at` | Needed for compliant delivery | 🟠 |
| 35 | `nga_node`, `nga_edge` | New tables | Graph storage | 🔴 |
| 36 | `nga_page_version`, `nga_document`, `nga_notice` | New tables | Content and documents | 🔴 |
| 37 | `nga_turn_event`, `nga_fetch_log`, `nga_tool_log` | New tables | Audit/eval | 🟠 |
| 38 | `nga_reference_delivery` | New table | Dedupe/retry/audit | 🟠 |
| 39 | `conversations` | No schema break; evidence stays in `nga_turn_event` | Avoids a risky migration | 🟢 |
| 40 | `content/dspmu/nga_graph.sqlite3` (single-box) | New generated artifact | Must be gitignored or versioned deliberately | 🟡 |
| 41 | Retention jobs | New purge targets | Storage growth; privacy | 🟠 |

### 4.5 Configuration

| # | File | Impact | Action | Severity |
|---|---|---|---|---|
| 42 | `app/config.py` | New `NGA_*` settings | Add typed fields; default off | 🟠 |
| 43 | `app/config_truth.py` | New int/float keys | Register and validate | 🟡 |
| 44 | `.env` / `.env.example` | New keys | Document; keep `NGA_ENABLED=false` by default | 🟠 |
| 45 | Prototype `LIVE_SITE_*` | Ad-hoc, unapproved | Retire or map to `NGA_*`; do not ship both | 🟠 |

### 4.6 MCP and scripts

| # | File | Impact | Action | Severity |
|---|---|---|---|---|
| 46 | `app/mcp/tools.py` | Lead tools only | Register `site.*` tools | 🟡 |
| 47 | `app/mcp/server.py` | JSON-RPC/SSE | No transport change required | 🟢 |
| 48 | `scripts/build_site_index.py` | Build-time static corpus | Keep for facts/notices; add graph compile separately | 🟡 |
| 49 | `scripts/build_notice_store.py` | FTS for notices | Reuse pattern for graph FTS | 🟢 |
| 50 | `launch*.sh`, `start_services.sh` | Static stack | Add refresh worker? Optional; keep flag off | 🟡 |

### 4.7 Tests and evaluation

| # | Area | Impact | Action | Severity |
|---|---|---|---|---|
| 51 | `tests/conftest.py` | Current suite hermetic | Pin `NGA_ENABLED=off` | 🟢 |
| 52 | `tests/test_site_router.py` | Existing router expectations | Keep; add `kind` assertions | 🟡 |
| 53 | New `tests/test_nga_*` | 60+ new tests | Fixtures, fake HTTP, parity, latency, security | 🟠 |
| 54 | `eval/` | Existing DSPMU eval | Add NGA golden set + trajectory metrics | 🟠 |
| 55 | Load/latency harness | Existing perf tooling | Add per-stage NGA marks | 🟡 |

### 4.8 Documentation

| # | File | Impact | Action |
|---|---|---|---|
| 56 | `content/dspmu/README.md` | Describes static build | Add NGA section, flags, graph rebuild |
| 57 | `doc/sdlc/nga/*` | This design set | Keep as the authority |
| 58 | `doc/ARCHITECTURE_DETAIL.md` | Historical architecture | Add a superseded/extension note |
| 59 | `README_DEPLOYMENT.md`, `launch_Guide.txt` | Deployment/launch | Add refresh worker and flags |
| 60 | Demo guides | Demo behavior | Add reference-delivery demo script |

### 4.9 External integrations

| # | Integration | Impact | Action | Severity |
|---|---|---|---|---|
| 61 | `dspmuranchi.ac.in` | New request-time traffic | Allowlist, robots, rate limit, permission | 🔴 |
| 62 | Twilio Voice | Caller number/call_sid used for delivery | Thread identity; no protocol change | 🟠 |
| 63 | Twilio WhatsApp | New proactive message path | Template approval, 24 h window, opt-out | 🟠 |
| 64 | OpenAI | Live page text added to prompts | Data-handling approval; no training | 🟠 |
| 65 | Postgres | New tables/columns | Migration; backup; retention | 🟠 |
| 66 | Redis (optional) | Cache | No new mandatory dependency | 🟢 |

## 5. Channel Impact

| Channel | User-visible change | Breaking? | Rollback |
|---|---|---|---|
| Streamlit chat | Answers can use live pages; source block with title+snippet+href | No | Flag off |
| WhatsApp inbound | Answers can use live pages; inline references; STOP/opt-out recognized | No | Flag off |
| Inbound voice | Fast-path live evidence; no spoken URLs; references sent to registered WhatsApp | No | Flag off |
| Outbound voice | Same as inbound; campaign context in session | No | Flag off |
| MCP clients | New `site.*` tools | No (additive) | Unregister tools |
| CRM/dashboard | Turn events/evidence available for audit | No (new data) | Ignore new tables |

## 6. Data Impact

### 6.1 New tables

`nga_node`, `nga_edge`, `nga_page_version`, `nga_document`, `nga_notice`, `nga_turn_event`, `nga_fetch_log`, `nga_tool_log`, `nga_reference_delivery`, `nga_schema_version`.

All are additive; no existing table is altered except `leads` gaining two nullable columns.

### 6.2 Volume estimate (order of magnitude)

| Table | Volume |
|---|---|
| `nga_node` | 257–1,000 rows |
| `nga_edge` | 500–3,000 rows |
| `nga_page_version` | 1–5 versions per changed node |
| `nga_turn_event` | turns/day × retention |
| `nga_fetch_log` | refreshed pages/day × retention |
| `nga_reference_delivery` | cited voice turns × references × retention |

At 1,000 turns/day and 90-day retention, `nga_turn_event` is ~90k rows; indices are sufficient. No partitioning needed at this scale.

### 6.3 Retention

- Transcript/evidence: `NGA_TRANSCRIPT_RETENTION_DAYS` / `NGA_EVIDENCE_RETENTION_DAYS` (default 90, policy to confirm).
- Fetch/tool/reference logs: 30 days.
- Graph/content: retained while the node exists; historical versions pruned by policy.

### 6.4 Migration

- Additive DDL; no destructive change.
- Backfill graph before enabling shadow.
- `leads` columns nullable; existing rows unaffected.
- Rollback drops `nga_*` tables only after the approval window; existing tables untouched.

## 7. API Impact

| Surface | Change | Breaking? |
|---|---|---|
| HTTP `/twilio/*`, `/api/*` | No route change; response content may include references | No |
| MCP tools | Add `site.*`; lead tools unchanged | No |
| Internal `query_rag`/`query_rag_stream` | Add optional `session` arg; default behavior preserved | No |
| `build_rag_prompt_parts` | Add optional session; signature compatible | No |
| Event schema | New `nga_turn_event`; no change to `conversations` | No |

## 8. Configuration Impact

New keys are listed in `03-lld.md` §12. Critical defaults:

- `NGA_ENABLED=false` — no behavior change.
- `NGA_MODE=off` — explicit.
- `NGA_REFERENCE_DELIVERY_ENABLED=false` — no WhatsApp sends.
- `NGA_WHATSAPP_REFERENCE_REQUIRE_OPT_IN=true` — fail closed.

The existing `LIVE_SITE_*` prototype keys must not coexist with `NGA_*` in production; one contract only.

## 9. Dependency Impact

| Dependency | Status | Impact |
|---|---|---|
| `requests`, `beautifulsoup4`, `lxml` | Already in `requirements.txt` | No new install |
| `sqlite3` | Standard library | Single-box graph/FTS |
| Postgres | Existing `DATABASE_URL` | New tables |
| Twilio SDK | Existing | New WhatsApp send path |
| OpenAI | Existing | More/longer prompts on live turns |
| Neo4j / vector DB | **Not added** | Explicitly excluded |
| `mcp` SDK | Optional existing fallback | New tools registered; no hard dependency |

## 10. Security and Privacy Impact

| Concern | Impact | Mitigation | Severity |
|---|---|---|---|
| SSRF | New outbound fetches | Allowlist, HTTPS, private-IP block, redirect validation | 🔴 |
| Prompt injection | Live page text reaches the model | Delimit as data; tools reject URLs; verifier | 🔴 |
| PII | Site publishes lists/staff directories | Product decision 2026-10-24: guard off (`NGA_PII_GUARD_ENABLED=false`) because the chat is part of the public website; set true and recompile to restore the guards | 🟠 |
| WhatsApp consent | Proactive messages to callers | Registration + opt-in + STOP + template | 🟠 |
| Data handling | Live text sent to OpenAI | Approval; no training; retention | 🟠 |
| Audit | New fetch/tool/delivery logs | Structured logs; no raw PII | 🟡 |
| Secret handling | Twilio/OpenAI keys | Existing config; never logged | 🟢 |

## 11. Performance Impact

| Area | Impact | Mitigation |
|---|---|---|
| Voice turn | +0–1.5 s live step | Cache-first; deadline; static fallback; 1–2 tool calls |
| Chat/WhatsApp | +1–3 s on live turns | 2–3 pages; cache; deadline 8 s |
| DB | New reads/writes | Indexed queries; async event writes |
| Refresh | Background CPU/network | Single writer; 1 req/s; off the request path |
| Prompt size | +1.2–4 KB live evidence | Per-channel caps; verified facts first |
| Memory | Graph/cache | Bounded LRU; 257 nodes is small |

## 12. Operations Impact

- New refresh worker/CLI (`scripts/nga_scheduler.py`; `scripts/nga_refresh.py` remains the one-cycle tool). Start via the existing service manager only when `NGA_SCHEDULER_ENABLED=true`.
- New run report (`content/dspmu/nga_refresh_report.json`), run history (`logs/nga_refresh.jsonl`) and alert log (`logs/nga_alerts.jsonl`).
- New dashboards: coverage, freshness, tool latency, verifier failures, reference delivery.
- New alerts: coverage drop, refresh failure streak, PII guard mismatch, stale backlog, page-level error rates, document error rate, OCR review backlog, reference send failures and template-required spikes.
- New reference delivery log (`nga_reference_delivery`) stores only a phone hash, last four digits and the reference; the message body and full number are never stored.
- Conversation storage and the measured turn-event write cost are documented in `doc/ops/conversation-storage.md`.
- New document index: `nga_document` in the graph DB, with a human review gate for OCR. A compile clears it along with page bodies.
- Runbooks: `doc/ops/nga-runbook.md` covers start/stop, alerts, rebuild, force refresh, kill switch and restore static.
- Backups: graph is rebuildable from the site + facts; event log is the durable audit. A compile clears bodies and freshness, so back up and re-warm.

## 13. Test Impact

- Existing suite must stay green with `NGA_ENABLED=off`.
- New fixtures cover sitemap/nav, soft-404, empty shell, PII, injection, registration, template.
- Channel parity tests need a shared harness across Streamlit/WhatsApp/voice.
- Live smoke is opt-in; CI never touches the network.
- Evaluation expands from answer accuracy to trajectory/grounding/freshness.

## 14. Documentation Impact

- `content/dspmu/README.md`: add NGA architecture, rebuild commands, flags.
- `README_DEPLOYMENT.md` / launch guides: refresh worker, Twilio template, opt-out.
- `doc/ARCHITECTURE_DETAIL.md`: add an extension/superseded note.
- This design set: update when decisions are approved.
- Runbook for university corrections and kill switch.

## 15. Rollout and Migration Sequence

| Step | Action | User impact | Gate |
|---|---|---|---|
| 0 | Approve this design set | None | Sign-off §20 |
| 1 | Create tables + config; flag off | None | Unit tests |
| 2 | Compile graph; coverage report | None | `NGA-G1` |
| 3 | Build tool server + verifier | None | Unit/integration |
| 4 | Shadow mode on a canary number/caller | None; logs delta | Delta review |
| 5 | Voice fast path for canary | Voice canary | `NGA-G2/G3/G4` |
| 6 | Text agentic for canary | Chat/WhatsApp canary | `NGA-G2/G5` |
| 7 | Reference delivery for canary | WhatsApp references | `NGA-G7` |
| 8 | Default on after soak | All channels | 2-week soak |
| 9 | Remove prototype `LIVE_SITE_*` | None | Code review |

## 16. Rollback Plan

1. **Immediate:** set `NGA_ENABLED=false` (kill switch) → `context_bundle_for_query` returns the static bundle exactly as today.
2. **Reference delivery:** set `NGA_REFERENCE_DELIVERY_ENABLED=false` → no WhatsApp sends; inline chat references can remain or be disabled independently.
3. **Refresh worker:** stop the process; cached graph remains readable.
4. **Data:** `nga_*` tables are additive; keep them for audit or drop after the approval window. Existing `conversations`/`leads` data is untouched except the two nullable columns.
5. **Verification:** run the static regression suite and a channel smoke; confirm no live fetch occurs.

Target rollback time: **< 15 minutes**.

## 17. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| NGA-R-01 | Site structure/nav changes silently drop pages | High | High | Sitemap/nav diff, coverage alarm, `unreachable` status | Architect |
| NGA-R-02 | Empty shells / PDF-only facts leave gaps | High | Medium | `empty_shell` status, document index, unknown handling | BA |
| NGA-R-03 | Prompt injection from live page text | Medium | High | Data delimiter, tool URL rejection, verifier, red-team | Security |
| NGA-R-04 | PII exposure from published lists/staff pages | Medium | Critical | Accepted by product decision 2026-10-24 (public website); `NGA_PII_GUARD_ENABLED=true` restores build/tool/verifier guards | PO + Privacy |
| NGA-R-05 | Voice latency regression | Medium | High | Cache-first, 1–2 tools, deadlines, static fallback | Architect |
| NGA-R-06 | Planner loop/tool misfire | Medium | Medium | Step limits, deterministic fallback, deadlines | Architect |
| NGA-R-07 | Graph staleness | Medium | High | TTL, refresh, stale-while-revalidate, freshness metric | Ops |
| NGA-R-08 | Live text → OpenAI data-handling objection | Medium | High | Approval before production; no training; retention | Privacy |
| NGA-R-09 | University permission/robots objection | Low | High | Written permission, UA, rate limit, stop-on-request | PO |
| NGA-R-10 | WhatsApp template/compliance failure | Medium | High | Approved template, 24 h rule, skip + log | Ops |
| NGA-R-11 | Reference sent to unregistered/opted-out caller | Low | Critical | Fail-closed registration, opt-out, red-team, delivery log | Privacy |
| NGA-R-12 | Channel divergence | Medium | Medium | One seam, parity suite, canary per channel | QA |
| NGA-R-13 | Event/log storage growth | Low | Medium | Retention, indices, purge job | Ops |
| NGA-R-14 | Refresh worker operational failure | Medium | Medium | Alerts, manual CLI, cached reads | Ops |
| NGA-R-15 | Scope creep into “crawl everything” | High | Medium | In-scope public set, PII policy, phase gates | PO |
| NGA-R-16 | A vector/RAG dependency slips in | Low | High | ADR-001, code review, dependency check | Architect |

## 18. Effort Estimate

Estimates are rough, for sequencing only; they are not commitments.

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Models, schema, compiler, coverage | 2–3 weeks |
| 2 | Store, FTS, search/ranking | 1–2 weeks |
| 3 | Tool server, policy, fetch/extract | 2 weeks |
| 4 | Planner, executor, voice fast path | 2 weeks |
| 5 | Evidence, verifier, citations | 1–2 weeks |
| 6 | Session, events, memory | 1–2 weeks |
| 7 | Text agentic path | 1–2 weeks |
| 8 | Refresh worker, TTL, PDF background | 2 weeks |
| 9 | Reference delivery (chat + voice→WhatsApp) | 1 week |
| 10 | Eval, dashboards, canary, rollout | 1–2 weeks |
| **Total** | | **~14–18 weeks** (small team) |

## 19. Prototype Disposition

The uncommitted spike must not be merged as-is.

| File | Status | Decision |
|---|---|---|
| `app/live_site.py` | New, uncommitted | Retain as reference for fetch/cache ideas; rewrite as `app/sitegraph/fetch.py` + tools |
| `tests/test_live_site.py` | New, uncommitted | Retain the fixture patterns; move to `tests/test_nga_*` |
| `app/config.py`, `app/config_truth.py` | Modified | Replace `LIVE_SITE_*` with `NGA_*` or remove |
| `app/context_bundle.py` | Modified (`kind`) | Keep `kind`; add evidence/citation fields per LLD |
| `app/rag.py` | Modified | Keep the `to_thread`/gate structure; replace ad-hoc retrieval with NGA |
| `app/site_router.py` | Modified (`kind`) | Keep as deterministic fast path |
| `tests/conftest.py` | Modified (`LIVE_SITE_ENABLED=off`) | Change to `NGA_ENABLED=off` |
| `.env`, `.env.example` | Modified | Replace with `NGA_*`; default off |

**Prototype findings used in this design:**

- Live sitemap is reachable: 257 URLs.
- HTML extraction works for normal pages.
- Hostel/library pages returned no live result because their CMS bodies are empty shells.
- URL-slug-only ranking is insufficient; the graph needs titles, nav labels and aliases.
- Direct `retrieve_live_context` bypassed the planner gate in the probe; the production design must not expose that path.

## 20. Approval Checklist

| # | Decision | Owner | Status |
|---|---|---|---|
| 1 | Intent, success metrics, scope | PO | Pending |
| 2 | Authority hierarchy (verified facts win) | PO + Registrar | Pending |
| 3 | PII boundary (student lists, staff directories) | PO + Privacy | **Decided 2026-10-24: guard off; restore with `NGA_PII_GUARD_ENABLED=true`** |
| 4 | PDF policy (background only) | PO + Architect | Pending |
| 5 | Voice/chat latency budgets | PO + Architect | Pending |
| 6 | Retention and deletion policy | PO + Privacy | Pending |
| 7 | Registration definition and opt-in policy | PO + Privacy | Pending |
| 8 | WhatsApp template and 24 h handling | Operator + Twilio | Pending |
| 9 | OpenAI data-handling approval | Security/Privacy | Pending |
| 10 | University permission and correction contact | University liaison | Pending |
| 11 | Postgres vs SQLite for the demo box | Architect + Ops | Pending |
| 12 | Rollback drill and kill switch | Operator | Pending |

## 21. Open Decisions

| ID | Decision | Default if unresolved |
|---|---|---|
| Q-01 | Staff-directory content allowed? | Allowed by the 2026-10-24 product decision (guard off) |
| Q-02 | Which PDFs are in scope? | Only documents linked from allowed pages, background-indexed |
| Q-03 | Retention period | 90 days, pending policy |
| Q-04 | Live text to OpenAI approved? | Disabled in production |
| Q-05 | `llms.txt` / `notices.json` available? | Optional enhancement, not a dependency |
| Q-06 | Graph store: Postgres or SQLite | Postgres in production; SQLite for local demo |
| Q-07 | Registration: conversation presence or explicit opt-in | Recent WhatsApp conversation + no opt-out; privacy may tighten to explicit opt-in |
| Q-08 | Reference delivery mode | `turn` throttled during the call; `call_end` is the low-noise option |
| Q-09 | WhatsApp template SID | Required before production delivery |
