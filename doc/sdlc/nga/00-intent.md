> **Stage 0 — Engagement [Lens: all]** · **Decided by:** PO + BA + Architect (jointly, with TPO feasibility review) · **Engagement:** Brownfield — `/Users/aiwork/codebase/aichatagent`, branch `feat/dspmu-rebrand` · **Scope:** design-only until sign-off · **Date:** 2026-10-24

# Product Intent — DSPMU Live-Site Navigation-Grounded Agent (NGA)

## 1. Problem

The assistant answers admissions questions from a **committed snapshot** of the DSPMU website. The website itself is live, public and substantially larger than what the runtime can reach.

| Measured fact | Evidence |
|---|---|
| The site’s `sitemap.xml` contains **257 URLs** | Live probe, 2026-10-24; `content/dspmu/build_report.json:99` |
| The build parses **68 navigation nodes / 137 navigation links / 51 departments** | `content/dspmu/build_report.json:100-102` |
| The runtime keeps **3 prompt files**, **11 extracted nodes**, **4 routable page nodes** | `content/dspmu/site_map.md` (65 lines), `content/dspmu/nodes/`, `app/site_router.py:47-52` |
| The router is explicitly **network-free** | `app/site_router.py:14-16` |
| An unrecognised query falls to the full static bundle | `app/site_router.py:708-717` |
| The model is instructed to refuse when the context lacks the answer | `app/rag.py:56-57` |
| The build report preserves **counts, not the hierarchy or node metadata** | `content/dspmu/build_report.json` |
| The raw sitemap/home cache is gitignored and never read at request time | `content/dspmu/README.md:26`; `content/dspmu/cache/current/sitemap.xml` |

Consequences:

1. A question such as “What are the library timings?”, “Who is the Vice Chancellor?”, “What is the hostel process?” or “What does the Grievance Redressal Cell do?” cannot reach the live page unless the static router happens to know the vocabulary.
2. A notice published after the last build is invisible until the corpus is rebuilt.
3. The site itself is partly shell-shaped: some CMS pages contain little body text, while fees/examination facts live in PDFs; current-host `/pdf/...` links return HTTP 200 soft-404s (`content/dspmu/source_fixes/README.md:21-42`).
4. Conversation history is persisted, but it is not used to navigate: a follow-up such as “what about its fee?” does not resolve “it” to the previously opened page.
5. Chat, WhatsApp, inbound voice and outbound voice all converge on the same answer seam, so the coverage gap is uniform across channels.

This is a **solution-design failure**, not a website-availability failure. The site is up; the runtime has no path to it.

## 2. Product Intent

> **Make any published DSPMU website detail answerable, in real time, in the user’s channel and language, with a grounded and cited response — without using RAG.**

The agent must:

- know the site’s **navigation hierarchy and per-node metadata**;
- **navigate** that hierarchy through constrained tools rather than similarity search;
- combine **verified facts** (fees, dates, contacts) with **live page content** for everything else;
- keep a **durable, queryable conversation record** that also improves follow-up retrieval;
- degrade to the current static answer when the live site or a tool fails.

## 3. Target Users & Personas

| Persona | Role | Job-to-be-done | Pain today |
|---|---|---|---|
| **Prospective student** | Web chat / WhatsApp / caller | Ask any admissions, campus, examination or office question and get a current answer | Questions outside the static vocabulary are refused or answered from a stale snapshot |
| **Parent / guardian** | Caller | Confirm fees, dates, hostel, scholarships and contacts without hunting the website | The site is hard to navigate; PDFs are buried; some pages are empty |
| **Admissions counselor** | Human escalation target | Receive a caller with a complete, cited context | The assistant cannot tell the counselor which page it used or when it was verified |
| **University web team / registrar** | Content owner | Publish once and have the assistant use the current page | The assistant may quote an old fee or miss a new notice |
| **Operator** | Runs the stack | Keep the service healthy with a live external dependency | A live-site outage must not take the assistant down |
| **Developer / maintainer** | Evolves the system | Add capabilities without a vector pipeline or a second knowledge store | The current corpus and router are hand-curated and do not scale to 257 pages |

## 4. Jobs to Be Done

| ID | Job | Example |
|---|---|---|
| JTBD-01 | Find the right page from a natural-language question | “hostel rules” → `/cms/hostel` |
| JTBD-02 | Navigate the hierarchy when the exact page is unknown | “student life” → section → hostel / sports / NSS / NCC |
| JTBD-03 | Answer from verified facts where money or deadlines are involved | “BA History annual fee” → `facts.json` |
| JTBD-04 | Answer from a live page for the long tail | “Who is the Vice Chancellor?” → live page |
| JTBD-05 | Follow a document when the page is a shell | fee/examination page → linked PDF metadata/text |
| JTBD-06 | Resolve follow-ups from conversation state | “what about its fee?” after opening the hostel page |
| JTBD-07 | Cite the source and verification time | “Source: … verified 24 Oct 2026” |
| JTBD-08 | Refuse safely at a boundary | PG fee/seat, result list, unverifiable claim |
| JTBD-09 | Keep a record for audit and CRM | every turn: plan, tools, evidence, answer, latency |

## 5. Success Metrics

Targets are **design targets**, not measured results. They become gates in `01-brd.md`.

| # | Metric | Type | Target | How measured |
|---|---|---|---|---|
| M-01 | **Grounded live-answer rate** — in-scope questions answered with at least one cited live/verified source | **North Star** | ≥ 90% on the frozen evaluation set | Trajectory evaluation |
| M-02 | **Critical-fact accuracy** — fees, dates, contacts, programme names | Guardrail | 100% on critical cases; zero ungrounded critical claims | Deterministic verifier + golden set |
| M-03 | **PII leakage** — student names, roll/registration numbers, merit/selection rows | Guardrail | 0 | PII regression suite |
| M-04 | **Voice latency added by live retrieval** | Guardrail | cache hit: p95 ≤ 400 ms; cache miss: p95 ≤ 1,500 ms; never exceed the existing voice turn budget | Per-turn trace |
| M-05 | **Chat/WhatsApp answer latency** | Guardrail | p95 ≤ 8 s end-to-end on the reference box | Per-turn trace |
| M-06 | **Freshness** | Guardrail | notices ≤ 5 min; page metadata ≤ 24 h; nav graph ≤ 1 h | Refresh report |
| M-07 | **Channel parity** | Guardrail | ≥ 95% of eval questions produce the same evidence set and equivalent answer on chat / WhatsApp / voice | Channel replay |
| M-08 | **Static availability under live-site failure** | Guardrail | live-site failure never reduces the static-answer success rate | Chaos test |
| M-09 | **Conversation record completeness** | Guardrail | 100% of turns have an event with plan, tools, evidence IDs, answer, verifier result, latency | Event-store audit |
| M-10 | **Graph coverage** | Guardrail | ≥ 95% of public sitemap URLs represented; 100% of top-level sections; all 51 departments discoverable | Coverage report |
| M-11 | **Reference delivery** | Guardrail | 100% of cited chat/WhatsApp answers show title+snippet+href; registered voice callers receive the WhatsApp reference; 0 sends to unregistered/opted-out numbers | Channel replay + delivery log |

> **North Star caveat.** “Any published detail” is bounded by law, policy and physics: PII pages are excluded, PDFs are extracted in the background, and the voice path has a hard latency budget. The metric measures the **in-scope** public set, not every byte on the site.

## 6. Scope

| In scope | Out of scope |
|---|---|
| Persistent navigation graph with hierarchy and per-node metadata | Replacing the website’s CMS or navigation (university-owned) |
| MCP-style tool server for search/navigate/open/latest/documents/freshness | Vector database, embeddings, similarity search, chunk corpus |
| Two-speed planner: deterministic voice fast path + agentic text path | Model training or fine-tuning |
| Claim-level grounding and citation verifier | Changing the LLM provider or hosting model |
| Event-sourced conversation record and history-aware query rewrite | CRM/Salesforce schema redesign |
| Freshness worker: sitemap/nav diff, conditional GET, content hash, TTL | Live PDF fetching in the voice path |
| Chat, WhatsApp, inbound voice and outbound voice integration | Student result/merit/selection-list ingestion |
| Inline citations in chat/WhatsApp; voice→WhatsApp reference delivery for registered callers | Marketing broadcasts or bulk WhatsApp campaigns |
| PII/robots/allowlist policy enforcement | University-side permission negotiation (a dependency, not a build item) |
| Trajectory evaluation and shadow mode | Offer-letter, sentiment, lead-scoring features |
| Feature flag, kill switch, rollback | Multi-region or cloud migration |

## 7. Non-Goals

- **Not RAG with a different name.** There is no embedding model, no vector store, no top-k similarity, and no chunk stuffing. Lexical FTS and graph traversal are tools, not a RAG index.
- **Not a general web crawler.** The agent may read only the allowlisted DSPMU hosts and only the nodes the policy permits.
- **Not a browser automation agent.** No headless browser in the live path; it is slow, nondeterministic and unnecessary for a CMS with a sitemap.
- **Not a replacement for verified facts.** Fees, admission dates and programme names remain governed by the signed-off fact layer.
- **Not a quality-improvement project for the LLM.** The bar is current quality plus live coverage, not a new model.
- **Not a PII reclassification.** The existing boundary remains until a separate review changes it.

## 8. Solution Space and the No-RAG Decision

| Option | What it would do | Why rejected / selected |
|---|---|---|
| **Vector RAG** | Embed page chunks, retrieve top-k, stuff the prompt | **Rejected by constraint.** Loses hierarchy, no freshness, chunk boundaries, embedding drift, and a second index to rebuild. |
| **Long-context whole-site** | Put all 257 pages into a long context | **Rejected.** Cost and latency are unacceptable for voice; content changes make every request stale; prompt-injection surface is enormous. |
| **Fine-tuning** | Train the model on the site | **Rejected.** Cannot learn a notice published tomorrow; no provenance; retraining is not a freshness strategy. |
| **Browser agent** | Let an LLM drive a browser | **Rejected for live channels.** Too slow, nondeterministic, hard to bound and audit; useful only as an offline gap-finder. |
| **Static crawl only** | Rebuild the snapshot more often | **Rejected.** Still not real-time, still no hierarchy-aware navigation, still a fixed router. |
| **Navigation-Grounded Agent** | Typed graph + tools + planner + verifier + event memory | **Selected.** Meets the no-RAG constraint, preserves hierarchy and metadata, supports freshness, and fits the existing channel seam. |

### 8.1 What “no RAG” means operationally

| Concern | NGA implementation |
|---|---|
| Finding a page | FTS/BM25 over titles, nav labels, aliases and body text; graph expansion |
| Knowing where a page sits | `parent_id`, `depth`, `section`, breadcrumb, sibling order |
| Understanding a question | Language/alias normalisation, intent classification, programme/entity detection, history-aware rewrite |
| Giving the model context | Structured tool results (typed evidence), not chunks |
| Proving an answer | Claim-level matching to node/span evidence |
| Staying current | TTL, sitemap diff, conditional GET, content hash, stale-while-revalidate |
| Remembering a conversation | Event store + typed session state, not transcript stuffing |

## 9. Assumptions

| ID | Assumption | Classification |
|---|---|---|
| AS-NGA-01 | `https://dspmuranchi.ac.in` remains public and reachable; the sitemap and CMS HTML remain the primary public surface | User-provided |
| AS-NGA-02 | `robots.txt` continues to allow the assistant’s user agent for reference use; `ai-train=no` is respected | User-provided + verified |
| AS-NGA-03 | Written university permission and a correction contact will be obtained before production traffic | Assumed / dependency |
| AS-NGA-04 | The verified `facts.json` layer remains authoritative for fees, admission dates and programme names | Assumed; PO sign-off required |
| AS-NGA-05 | The existing PII boundary (no result/merit/selection lists; staff directories excluded until review) stays in force | User-provided |
| AS-NGA-06 | The current OpenAI-only stack is retained; live page text will be sent to that model | User-provided; data-handling review required |
| AS-NGA-07 | Postgres is available; a single-box SQLite mode is acceptable for local/demo | Assumed |
| AS-NGA-08 | Peak concurrency remains the current target: two voice callers plus background work | User-provided |
| AS-NGA-09 | English, Hindi and Gujarati are the primary spoken languages; other languages are text-best-effort | Derived |
| AS-NGA-10 | Some CMS pages will remain empty shells and some facts will remain PDF-only | Verified |
| AS-NGA-11 | A human will sign off critical facts and the evaluation ground truth | Assumed |
| AS-NGA-12 | Conversation/evidence retention will be set by policy before production | Open |

## 10. Constraints

| Constraint | Implication |
|---|---|
| **No RAG** | No embeddings, no vector DB, no similarity top-k, no chunk stuffing |
| Voice latency | Voice path gets a deterministic fast path and a hard tool deadline |
| Prompt budgets | Live evidence is capped by channel; verified facts take precedence |
| Robots / Content-Signal / permission | Reference use only; no training; allowlist; rate limit; stop on request |
| PII | Student lists and staff directories excluded; no PII in prompts or logs |
| Existing channels | One answer seam must serve chat, WhatsApp, inbound and outbound voice |
| Existing stack | OpenAI-only; Postgres/Redis already present; no new mandatory paid API |
| Backward compatibility | With NGA disabled, current static behaviour must be byte-for-byte equivalent |
| Audit | Every answer must be reproducible from its event record |

## 11. Intent-Level Risks

| ID | Risk | Why it matters |
|---|---|---|
| IR-01 | Live page conflicts with a verified fact | A wrong fee is worse than a refusal; authority precedence must be explicit |
| IR-02 | Empty CMS shells / PDF-only content | A page that looks found may contain nothing; the agent must follow documents |
| IR-03 | Prompt injection from page text | Fetched content is untrusted; the planner and verifier must not obey it |
| IR-04 | Site structure change | A stale graph silently loses pages; refresh and coverage alarms are mandatory |
| IR-05 | Planner loop / tool misfire | A voice caller cannot wait for an agent to wander |
| IR-06 | Latency regression | Live retrieval can consume the entire voice budget if unbounded |
| IR-07 | PII exposure | The site publishes some lists; the assistant must never surface them |
| IR-08 | University objection | The assistant must be stoppable and auditable |
| IR-09 | Data handling | Live text leaving the network to the LLM provider needs review |
| IR-10 | Scope creep | “Any detail” must stay inside the in-scope public boundary |

## 12. Definition of Done for the Design Phase

The design phase is complete when:

1. This Intent and the BRD are approved by the PO.
2. The authority hierarchy and PII boundary are signed off.
3. The HLD component model and ADRs are accepted by the architect and security.
4. The LLD specifies every contract, schema, config key and test.
5. The impact analysis lists every affected file, risk and rollback step.
6. The evaluation set shape and acceptance gates are agreed.
7. No production code has been written against an unapproved design.

## 13. Traceability to BRD

| Intent concern | BRD requirements |
|---|---|
| Live coverage and hierarchy | `NGA-BRD-01` … `NGA-BRD-06` |
| Grounded, cited answers | `NGA-BRD-07` … `NGA-BRD-13` |
| Freshness | `NGA-BRD-14` … `NGA-BRD-16` |
| Conversation record | `NGA-BRD-17` … `NGA-BRD-20` |
| Channels, latency and reference delivery | `NGA-BRD-21` … `NGA-BRD-24`, `NGA-BRD-33` … `NGA-BRD-34` |
| Safety, privacy, compliance | `NGA-BRD-25` … `NGA-BRD-28` |
| Operations, rollout, evaluation | `NGA-BRD-29` … `NGA-BRD-32` |
| Success metrics M-01 … M-10 | Acceptance gates `NGA-G1` … `NGA-G6` in `01-brd.md` |
