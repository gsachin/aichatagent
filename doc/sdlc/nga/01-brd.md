> **Lens:** BA · **Decided by:** BA, confirmed by PO; TPO consulted on feasibility · **Inputs:** `00-intent.md`, live-site audit 2026-10-24, current codebase · **Defines:** `NGA-BRD-01` … `NGA-BRD-34`, gates `NGA-G1` … `NGA-G7`

# BRD — DSPMU Live-Site Navigation-Grounded Agent (NGA)

## 0. Discovery Matrix — first completeness gate

| Dimension | Questions | Evidence | Gaps |
|---|---|---|---|
| **Personas** | Who uses it? | Document-observed — `00-intent.md` §3: student, parent, counselor, web team, operator, developer | — |
| **Actors** | Who/what interacts? | Code-observed — Twilio Voice/WhatsApp, Streamlit chat, OpenAI model, Postgres, DSPMU website, CRM | University web team has no UI in this system |
| **Business goals** | Why does it exist? | Derived — live coverage, grounded answers, channel parity, durable record | — |
| **Capabilities** | What must it do? | `NGA-BRD-01` … `NGA-BRD-32` | Intent coverage of the evaluation set is not yet proven |
| **Workflows** | How does work move? | Code-observed — chat `app.py:123`; WhatsApp `app/main.py:2865`; voice `app/voice_handler.py:3174`; all converge on `app/rag.py` | No existing live-site workflow |
| **States** | What states exist? | Code-observed — call session, lead, conversation, CRM session; new site-node states proposed in `02-hld.md` | Node lifecycle (ok/shell/soft-404/blocked) is new |
| **Data** | What data is required? | Code-observed — 3 prompt files, 43 fee facts, 1,520 non-PII notice feed, 11 nodes; build report counts 257/68/137/51 | Full nav hierarchy and per-node metadata are absent at runtime |
| **Integrations** | What external systems? | Code-observed — Twilio, OpenAI, Postgres, Redis, Salesforce, DSPMU site | No site tool server exists today |
| **Security** | What can go wrong? | Code-observed — student PII, prompt injection, SSRF, external content | Live-site data-handling review not yet recorded |
| **Failure** | What happens when dependencies fail? | Code-observed — static bundle fallback; PG boundary; PII title guard | No live-site failure policy exists |
| **Scale** | What at 10×/100×? | Derived — current target 2 voice + background; graph reads are cheap; refresh is single-writer | Arrival rate unmeasured |
| **Compliance** | What constraints? | User-provided — public site, robots, PII, OpenAI data handling | Written university permission pending |
| **Operations** | How monitored/recovered? | Code-observed — `/health`, `/ready`, JSONL logs, dashboards | No retrieval/graph/freshness metrics |

## 1. Objectives

| # | Objective | Traces to |
|---|---|---|
| O1 | Represent the public site’s full navigation hierarchy and per-node metadata in a runtime graph | M-10, JTBD-01/02 |
| O2 | Answer long-tail questions from the live site, not only the committed snapshot | M-01, JTBD-03/04/05 |
| O3 | Keep every answer grounded and cited, with verified facts winning conflicts | M-02, JTBD-03/07 |
| O4 | Keep answers fresh with bounded, observable refresh | M-06 |
| O5 | Give chat, WhatsApp, inbound and outbound voice the same evidence and answer semantics | M-07, JTBD-01 |
| O6 | Meet the voice latency budget while adding live coverage | M-04 |
| O7 | Keep a durable, replayable conversation record that also improves follow-ups | M-09, JTBD-06/09 |
| O8 | Make the change reversible and safe: no PII leakage, no arbitrary fetching, no dead air on failure | M-03/M-08, JTBD-08 |
| O9 | Return the website reference (title, snippet, href) in chat/WhatsApp and, for registered callers, deliver it to WhatsApp with the call context | M-11, JTBD-07 |

## 2. Scope

| In scope | Out of scope |
|---|---|
| Typed site graph + metadata | CMS/navigation changes on `dspmuranchi.ac.in` |
| Tool server (MCP-style) | Vector DB, embeddings, similarity search, chunk corpus |
| Two-speed planner and executor | Model training/fine-tuning |
| Claim-level verifier and citations | LLM provider change |
| Event-sourced turn record | CRM schema redesign |
| Freshness worker and TTL policy | Live PDF fetch in the voice path |
| Chat, WhatsApp, inbound/outbound voice integration | Student result/merit/selection ingestion |
| Inline citations in chat/WhatsApp; voice→WhatsApp reference delivery for registered callers | Marketing broadcasts or bulk WhatsApp campaigns |
| Policy, observability, evaluation, rollback | Offer letters, sentiment, lead scoring |

## 3. Stakeholders

| Stakeholder | Role | Responsibility | Interest |
|---|---|---|---|
| Product Owner | Decision maker | Approves intent, authority hierarchy, PII policy, latency budgets | Correct, current, safe answers |
| BA | Requirements owner | Owns this BRD and acceptance criteria | Testable requirements |
| Architect / TPO | Solution owner | Owns HLD/LLD and feasibility | Bounded, operable design |
| Caller / chat user | End user | — | Fast, correct, cited answer in their language |
| Admissions counselor | Handoff recipient | Receives warm context | Evidence and freshness |
| University registrar / web team | Content owner | Confirms facts, permissions, corrections | Official pages remain authoritative |
| Privacy/security owner | Control owner | Approves PII boundary and data handling | No student PII exposure |
| Operator | Runs the stack | Deploys, monitors, rolls back | Availability and clear alarms |
| QA | Verification | Owns fixtures, channel replay, gates | Reproducible evidence |

## 4. Definitions

| Term | Meaning in this document |
|---|---|
| **NGA** | Navigation-Grounded Agent: graph + tools + planner + verifier + event memory |
| **RAG** | Retrieval-Augmented Generation: embedding index, vector similarity, top-k chunks stuffed into the prompt. **Excluded.** |
| **Site graph** | Typed nodes (sections, pages, documents, notices) with parent/child edges and metadata |
| **Node** | One navigation point or content object in the graph |
| **Evidence** | A typed excerpt/claim from a node, with URL, span and verification time |
| **Verified fact** | A value in the signed-off facts layer for fees, dates or programme names |
| **Authority** | Rank order: verified fact > official page > derived/extracted content |
| **Freshness** | How recently a node was checked against the live site |
| **In-scope public set** | Public pages allowed by policy, excluding PII pages and staff directories |
| **Turn event** | The durable record of one user question and the system’s response path |

## 5. Business Requirements

### 5.1 Coverage and navigation

#### NGA-BRD-01 — Complete navigation graph
The system shall represent every public navigation point reachable from the site’s sitemap and rendered navigation in a persistent typed graph, preserving parent/child relationships, sibling order and section membership.

**Acceptance criteria**
- The compiler ingests `sitemap.xml` and the rendered nav; the union of URLs is de-duplicated.
- Coverage report states: sitemap URLs seen, nav nodes seen, unique nodes stored, orphans, exclusions with reason.
- 100% of top-level sections and 100% of the 51 listed departments are discoverable.
- ≥ 95% of public sitemap URLs are represented; every missing URL has a typed reason (`external`, `blocked`, `non_html`, `duplicate`, `soft_404`, `unreachable`).
- Running the compiler twice on unchanged input produces an identical graph hash.

**Traces:** M-10, JTBD-01/02. **HLD:** C5, C7. **LLD:** M01/M02, T-001/T-002.

#### NGA-BRD-02 — Per-node metadata
Every node shall carry, at minimum: stable `node_id`, canonical URL, path, parent, depth, section, title, navigation label, aliases, language(s), content type, authority class, PII class, status, last-modified, content hash, fetched-at, verified-at and byte size.

**Acceptance criteria**
- A schema validator rejects a node missing any required field.
- Title falls back to navigation label, then slug; the fallback is recorded.
- Aliases include English, Hindi and Gujarati forms for known topics.
- `status` is one of `ok`, `empty_shell`, `soft_404`, `blocked`, `external`, `unreachable`.
- `pii_class` is one of `public`, `staff_directory`, `student_list`, `blocked`.
- Querying metadata by URL, node ID or breadcrumb returns the same record.

**Traces:** M-10, JTBD-01/02/07. **HLD:** C5. **LLD:** M01/M03, T-003.

#### NGA-BRD-03 — Constrained navigation tools
The system shall expose a tool API for search, navigation, page opening, latest notices, document metadata and freshness. Tools shall accept node IDs or allowlisted paths — never an arbitrary URL from the model.

**Acceptance criteria**
- Tool set: `site.search`, `site.navigate`, `site.open`, `site.latest`, `site.documents`, `site.freshness`, `site.follow`.
- Every tool has a JSON schema, a response-size cap and typed error codes (`NOT_FOUND`, `BLOCKED`, `STALE`, `TOO_LARGE`, `UPSTREAM_ERROR`, `RATE_LIMITED`, `TIMEOUT`).
- A request for a host outside the allowlist returns `BLOCKED` without a network call.
- Tool calls are logged with args hash, latency and result status.

**Traces:** JTBD-01/02/05, M-01. **HLD:** C4/C5. **LLD:** M04, T-010 … T-016.

#### NGA-BRD-04 — One evidence service for every channel
Chat, WhatsApp, inbound voice and outbound voice shall use the same evidence/planning service and the same graph snapshot for a given question.

**Acceptance criteria**
- A parity test replays the same question on all four channels and asserts the same selected node IDs and equivalent answer semantics.
- No channel reads the site directly; only the tool server does.
- With NGA disabled, every channel reproduces the current static behaviour.

**Traces:** M-07, JTBD-01. **HLD:** C1/C2/C3. **LLD:** M08, T-040.

#### NGA-BRD-05 — Multilingual query mapping
The system shall map English, Hindi and Gujarati questions to the same graph nodes, and shall answer in the requested language.

**Acceptance criteria**
- A topic alias table covers fees, admissions, syllabus, examination, hostel, library, scholarship, placement, contact, grievance, vision, history, campus, sports, NSS, NCC, convocation, transcript, migration, bonafide, prospectus and calendar in en/hi/gu.
- A query in any of the three languages selects the same node for the same topic.
- Unsupported languages fall back to English node selection and answer in the requested language when the model supports it.

**Traces:** M-07, JTBD-01. **HLD:** C3. **LLD:** M05, T-020.

#### NGA-BRD-06 — Shell and document fallback
When a page is an empty CMS shell, the system shall record `status=empty_shell`, expose its linked documents, and answer from document metadata/extracted text where policy allows.

**Acceptance criteria**
- A page whose extracted main text is below the configured floor is never treated as an answer source.
- Its linked documents are returned by `site.documents`.
- In voice, document text is used only if it is already in the background index; no PDF fetch occurs in the voice turn.
- The answer states that the detail came from a document and cites it.

**Traces:** JTBD-05, M-01. **HLD:** C5/C7. **LLD:** M06, T-030.

### 5.2 Correctness, grounding and boundaries

#### NGA-BRD-07 — Evidence-only generation
Every factual claim in an answer shall be supported by at least one evidence item from a tool result or verified fact.

**Acceptance criteria**
- The verifier maps each number, date, name, fee and URL to an evidence span.
- An unsupported claim is removed or replaced with an explicit “I could not verify that”.
- If no evidence supports any part of the answer, the turn returns the unknown-handling response (`NGA-BRD-11`).
- The verifier result is stored in the turn event.

**Traces:** M-02, JTBD-03/07/08. **HLD:** C8. **LLD:** M07, T-050 … T-054.

#### NGA-BRD-08 — Citations and verification time
Every answer that uses a page or document shall carry a machine-readable citation: node ID, title, snippet, URL and `verified_at`.

**Acceptance criteria**
- The answer object contains `citations[]` with a `snippet` taken from the grounding evidence span.
- Chat/WhatsApp render a human-readable source line; voice may speak a short source phrase.
- A citation URL is always one of the evidence URLs, never constructed by the model.
- The turn event stores the same citations.

**Traces:** M-02, JTBD-07. **HLD:** C8. **LLD:** M07/M09, T-055.

#### NGA-BRD-09 — Authority precedence and conflict handling
The system shall apply: verified fact > official live page > background-extracted document > derived summary. A conflict shall be surfaced, not silently resolved.

**Acceptance criteria**
- Fee/date claims can only be grounded by the verified-fact authority class.
- If a live page disagrees with a verified fact, the verified fact is used and the conflict is logged with both values and URLs.
- The answer may say “the page shows X; the verified record says Y” when the conflict is material.
- Authority rank is stored on every node and evidence item.

**Traces:** M-02, IR-01, JTBD-03/08. **HLD:** ADR-003. **LLD:** M07, T-056.

#### NGA-BRD-10 — No invented values or links
The system shall never invent a fee, date, programme name, contact detail or URL.

**Acceptance criteria**
- Numeric/date/name/URL extraction is deterministic and normalized (commas, ₹, lakh/crore, date formats).
- A value not present in evidence is a verifier failure.
- A URL not present in evidence is removed or replaced by the cited node URL.
- Critical-fact hallucination rate is 0 on the frozen evaluation set.

**Traces:** M-02, JTBD-03/08. **HLD:** C8. **LLD:** M07, T-057.

#### NGA-BRD-11 — Unknown handling
When the in-scope public set does not contain an answer, the system shall say so, offer the official contact and, where useful, the most relevant navigation links.

**Acceptance criteria**
- The response contains no invented content.
- It includes the registrar/admissions contact from the verified facts.
- It may list up to three suggested nodes with titles and URLs.
- The turn is marked `answer_status=not_found` for evaluation.

**Traces:** JTBD-08, M-01. **HLD:** C3/C8. **LLD:** M07, T-058.

#### NGA-BRD-12 — Product and PII boundaries
The existing boundaries shall be preserved: PG programme/fee/seat/eligibility details remain out of scope; result/merit/selection/student lists remain excluded; staff directories remain excluded until a separate review.

**Acceptance criteria**
- PG fee/seat questions get the deterministic boundary answer without a live fetch.
- A result/merit/selection query never returns a student-list page or row.
- A staff-directory query returns a boundary response and, if appropriate, the official contact.
- Boundary decisions are logged and evaluated per intent.

**Traces:** M-03, AS-NGA-05. **HLD:** C10. **LLD:** M10, T-060 … T-063.

#### NGA-BRD-13 — PII protection at fetch and response time
The system shall enforce PII policy at graph-build, tool-response and answer-verification time.

**Acceptance criteria**
- URL tokens, page titles and extracted content are checked against the PII guard.
- A blocked node is never stored as answerable content; only its metadata may be retained for coverage reporting.
- Logs and turn events contain no student names, roll numbers or other list data.
- A red-team suite finds zero PII leaks across the four channels.

**Traces:** M-03, IR-07. **HLD:** C10. **LLD:** M10, T-064.

> **Decision override (2026-10-24):** the product owner has classified the
> university's published pages as usable because the chat is part of the
> public website. `NGA_PII_GUARD_ENABLED=false` is the default; the URL/title
> exclusions in `NGA-BRD-12`/`13` remain implemented and are restored by
> setting the flag to `true` and recompiling the graph.

### 5.3 Freshness

#### NGA-BRD-14 — TTL by content class
The system shall apply explicit freshness tiers and expose them in the freshness tool.

**Acceptance criteria**
- Notices/examination updates: ≤ 5 minutes.
- Page content: ≤ 24 hours.
- Navigation graph: ≤ 1 hour.
- Verified fee/date facts: admissions-cycle review, not autonomous refresh.
- Every node reports its TTL and next-due time.

**Traces:** M-06, JTBD-04. **HLD:** C7. **LLD:** M06/M11, T-070.

#### NGA-BRD-15 — Change detection
The refresh worker shall detect changes using sitemap/nav diff, `ETag`/`Last-Modified`, and content hash.

**Acceptance criteria**
- A 304 or an unchanged hash updates `checked_at`, not content.
- A changed hash creates a new content version and re-indexes FTS.
- A removed sitemap URL is marked `unreachable`/`removed` without deleting historical evidence.
- A new URL is added to the graph before it is answerable.

**Traces:** M-06, IR-04. **HLD:** C7. **LLD:** M11, T-071 … T-073.

#### NGA-BRD-16 — Stale-while-revalidate
A refresh failure shall not fail a turn. The system shall serve cached content, mark it stale in provenance, and schedule a retry.

**Acceptance criteria**
- Live-site timeout, DNS failure, 5xx or soft-404 does not produce a user-facing error when cache exists.
- The answer’s source line includes the last successful verification time.
- A retry is scheduled with backoff and bounded attempts.
- With no cache and no verified fact, the turn falls back to the static bundle or unknown handling.

**Traces:** M-08, IR-01/IR-04. **HLD:** C7/C10. **LLD:** M11, T-074.

### 5.4 Conversation record and history

#### NGA-BRD-17 — Durable turn events
Every turn shall persist an event containing: conversation ID, turn ID, channel, language, question, plan, tool calls, evidence IDs, citations, answer, verifier result, answer status, latency and timestamp.

**Acceptance criteria**
- The event schema is versioned.
- 100% of turns in the channel test suite produce an event.
- An event can be replayed to reproduce the evidence set and answer decision.
- Event writes never block the answer path; a failed write is retried via outbox.

**Traces:** M-09, JTBD-09. **HLD:** C9. **LLD:** M09/M12, T-080.

#### NGA-BRD-18 — History-aware follow-ups
The planner shall use typed session state and recent turns to resolve references such as “it”, “that programme”, “what about the fee?”.

**Acceptance criteria**
- A follow-up after opening a node carries that node as context.
- A programme mentioned earlier is retained as a filter until the topic changes.
- The system does not stuff the whole transcript into the prompt.
- A follow-up test set achieves ≥ 90% correct topic retention.

**Traces:** JTBD-06, M-01. **HLD:** C2/C3. **LLD:** M08/M13, T-081.

#### NGA-BRD-19 — Durable history across channels and restarts
Conversation history shall survive process restart and shall be retrievable by lead, phone, conversation ID and channel.

**Acceptance criteria**
- Web chat, WhatsApp, inbound and outbound calls write to the same conversation model.
- A returning user’s recent turns are available to the planner.
- History retrieval failure degrades to stateless answering without failing the turn.

**Traces:** JTBD-06/09, M-09. **HLD:** C2/C9. **LLD:** M12, T-082.

#### NGA-BRD-20 — Retention and deletion
Transcripts and evidence shall follow a configurable retention policy and a deletion request path.

**Acceptance criteria**
- Retention is configured per data class (transcript, evidence, fetch log).
- Expired rows are purged by a scheduled job.
- A deletion request removes or anonymizes the subject’s history within the policy window.
- PII is never written into fetch logs.

**Traces:** M-03/M-09, AS-NGA-12. **HLD:** C10/C11. **LLD:** M12, T-083.

### 5.5 Channels, performance and degradation

#### NGA-BRD-21 — Channel parity
The same question shall select the same evidence and produce equivalent answers on chat, WhatsApp, inbound and outbound voice, subject only to channel formatting.

**Acceptance criteria**
- A channel replay suite compares node IDs, citations and answer facts.
- Differences are classified as formatting, language or defect; only defects fail the gate.
- Outbound calls use the same evidence service with campaign context added, not a separate retriever.

**Traces:** M-07, JTBD-01. **HLD:** C1/C2. **LLD:** M08, T-040.

#### NGA-BRD-22 — Channel latency budgets
The system shall meet per-channel latency budgets including the live-retrieval step.

**Acceptance criteria**
- Voice cache hit: live step p95 ≤ 400 ms.
- Voice cache miss: live step p95 ≤ 1,500 ms; total turn stays within the existing voice budget.
- Chat/WhatsApp: end-to-end p95 ≤ 8 s on the reference box.
- A slow tool is cancelled at its deadline and the static/cache fallback is used.
- Per-turn traces record planner, tool, generation and verification time separately.

**Traces:** M-04/M-05, O6. **HLD:** C3/C4. **LLD:** M04/M08, T-090.

#### NGA-BRD-23 — Two-speed planning
The system shall use a deterministic fast path for voice and a bounded agentic path for text channels.

**Acceptance criteria**
- Voice: at most two tool calls and one graph search before generation.
- Text: at most eight steps, a byte budget and a deadline.
- Invalid/empty plan → deterministic planner.
- Planner emits only allowed tools and schema-valid arguments.
- No planner path can fetch an arbitrary URL.

**Traces:** IR-05, M-04. **HLD:** ADR-004. **LLD:** M14, T-091 … T-094.

#### NGA-BRD-24 — Graceful degradation matrix
Failure of any component shall degrade to the best available answer and shall never produce dead air.

| Failed component | Fallback |
|---|---|
| Live site / fetch | Cached node; then static bundle; then unknown handling with contact |
| Graph database | Static bundle; log degradation |
| Tool server | Static bundle |
| Planner | Deterministic planner |
| Verifier | Do not release the unverified claim; return verified subset or unknown handling |
| LLM | Existing deterministic fallback / fixed response |
| Event store | Answer continues; event queued in outbox |
| Search index | Graph navigation/metadata; static bundle |

**Acceptance criteria**
- Each row is exercised by a chaos test.
- No failure produces silence on voice.
- The answer status and degradation reason are recorded.

**Traces:** M-08, IR-01/IR-04/IR-05. **HLD:** C11. **LLD:** M11/M15, T-095.

### 5.6 Safety, privacy and compliance

#### NGA-BRD-25 — Allowlist, robots and content signals
The system shall fetch only allowlisted DSPMU hosts, respect `robots.txt` and `Content-Signal`, identify itself, rate-limit requests and stop on request.

**Acceptance criteria**
- Non-allowlisted host → no network call.
- `Disallow` → no fetch; status `blocked`.
- `ai-train=no` is respected: no content is used for training.
- One request at a time with the configured delay; no parallel crawl of the site.
- A kill switch stops all live fetching within one refresh cycle.

**Traces:** AS-NGA-02/03, IR-08. **HLD:** C10. **LLD:** M10/M11, T-100.

#### NGA-BRD-26 — Prompt-injection resistance
Fetched content shall be treated as untrusted data and shall never be able to change the system’s tools, policy or answer rules.

**Acceptance criteria**
- Evidence is delimited and labelled as data.
- The system prompt instructs the model to ignore instructions inside evidence.
- Tool arguments are validated server-side; a model-proposed URL is rejected.
- A red-team fixture containing “ignore previous instructions” cannot alter behaviour.
- The verifier still requires claims to match evidence.

**Traces:** IR-03. **HLD:** C10. **LLD:** M10/M15, T-101.

#### NGA-BRD-27 — Audit trail
Every fetch, tool call, planner decision, verifier result and answer shall be traceable to a node, URL, timestamp and conversation.

**Acceptance criteria**
- Fetch log: URL, status, bytes, content hash, timestamp, policy decision.
- Tool log: tool, args hash, node IDs returned, latency, error.
- Turn event links to fetch/tool records.
- An auditor can reconstruct why an answer was given without re-running the model.

**Traces:** M-09, O8. **HLD:** C9. **LLD:** M12, T-102.

#### NGA-BRD-28 — External links are not fetched
Links to non-allowlisted or external systems (for example the Chancellor Portal) shall be returned as links with metadata, never fetched.

**Acceptance criteria**
- `site.follow` on an external edge returns `{external_url, title}` and no content.
- The answer may present the link; the verifier treats it as a citation only if it came from an official node.
- No redirect to a non-allowlisted host is followed.

**Traces:** IR-08. **HLD:** C10. **LLD:** M10, T-103.

### 5.7 Operations, rollout and evaluation

#### NGA-BRD-29 — Feature flag and kill switch
The live path shall be controlled by a master flag with modes `off`, `shadow`, `fast`, `agentic`.

**Acceptance criteria**
- `off` reproduces current static behaviour exactly.
- `shadow` computes live evidence but does not change the user answer; it logs the delta.
- `fast` enables the deterministic path only.
- `agentic` enables the text agentic path.
- A kill switch forces `off` without a restart.

**Traces:** AS-NGA-01/03, O8. **HLD:** C11. **LLD:** M16, T-110.

#### NGA-BRD-30 — Observability
The system shall expose metrics and structured events for coverage, freshness, retrieval, grounding, latency and failures.

**Acceptance criteria**
- Metrics: graph coverage, cache hit rate, live share, tool latency, planner steps, verifier pass/fail, fallback rate, not-found rate, PII blocks.
- Alerts: graph coverage drop, refresh failure streak, verifier failure spike, latency budget breach, PII guard trip.
- Dashboards do not require reading application logs.

**Traces:** O8, M-01/M-06/M-09. **HLD:** C9. **LLD:** M12, T-111.

#### NGA-BRD-31 — Evaluation and regression gate
A frozen, task-representative evaluation set shall score the live path before and after adoption, per intent, with trajectory-level metrics.

**Acceptance criteria**
- The set covers all intents in `00-intent.md` §4 and the boundary cases.
- Metrics: grounded answer rate, critical-fact accuracy, citation correctness, freshness, steps, latency, refusal correctness.
- Critical-intent regression is an absolute blocker; aggregate improvement cannot offset it.
- Channel replay and live smoke are separate; live smoke is opt-in and never required for CI.
- Each unanswered eval case becomes a new graph/index gap or a new case.

**Traces:** M-01 … M-10. **HLD:** C9. **LLD:** M17, T-120 … T-124.

#### NGA-BRD-32 — University collaboration and corrections
The system shall support a correction workflow with the university and shall use published machine-readable indexes where available.

**Acceptance criteria**
- A named university contact and correction channel are recorded.
- A correction updates the verified fact layer or marks the node stale within one working day of notification.
- If the university publishes `llms.txt` or `notices.json`, the system can consume it as a higher-authority index without changing the tool contract.
- The assistant’s user agent and purpose are documented to the university.

**Traces:** AS-NGA-02/03, IR-08. **HLD:** C7/C10. **LLD:** M11/M18, T-125.

#### NGA-BRD-33 — Website reference shown in chat and WhatsApp
Every cited answer on Streamlit chat or WhatsApp shall show the user a website reference containing the page title, a short snippet and a clickable href.

**Acceptance criteria**
- The reference block contains `title`, `snippet` (≤ 240 characters from the evidence text) and `url`.
- The URL is the exact `source_url` of the cited node, never a model-constructed link.
- Streamlit renders a Markdown link and a snippet; WhatsApp renders a plain-text title, snippet and URL that Twilio delivers as a clickable link.
- The block is omitted when the answer has no citation.
- A boundary or not-found answer never shows an invented reference.
- The snippet is taken from the evidence span that grounds the answer, not generated separately.
- The reference block is stored in the turn event with the same `node_id` and `url`.

**Traces:** M-11, JTBD-07. **HLD:** C09/C15. **LLD:** M10/M22, T-130 … T-132.

#### NGA-BRD-34 — Voice references delivered to a registered WhatsApp number
When `NGA_REFERENCE_DELIVERY_ENABLED=true`, after a voice turn that produced citations, if the caller’s phone number is registered for WhatsApp, the system shall send the website reference (title, snippet, href) and the call context to that WhatsApp number.

**Acceptance criteria**
- **Registration** means: a lead exists for the normalized E.164 number **and** the lead has an inbound WhatsApp conversation within `NGA_WHATSAPP_REGISTRATION_WINDOW_DAYS` **and** the lead has not opted out. An explicit `whatsapp_opt_in` flag is sufficient on its own; when `NGA_WHATSAPP_REFERENCE_REQUIRE_OPT_IN=true`, it is required.
- The message includes: a short call context (question and answer summary, ≤ 300 characters) and up to `NGA_REFERENCE_MAX_PER_CALL` references.
- Delivery mode is configurable: `turn` (after the cited turn, throttled) or `call_end` (consolidated after the call).
- Duplicate references for the same `(call_sid, node_id)` are sent at most once.
- A send failure never affects the call; it is queued for bounded retry and logged.
- Outside the WhatsApp 24-hour customer-service window, the system uses an approved template; without a template it skips the send and records `template_required`.
- No reference is sent to an unregistered or opted-out number.
- The message contains only public page content; no student PII or internal IDs are included.

**Traces:** M-11, JTBD-07, IR-08. **HLD:** C15, ADR-014. **LLD:** M22, T-133 … T-136.

## 6. Non-Functional Requirements

| ID | Requirement | Target / constraint |
|---|---|---|
| NGA-NFR-01 | Availability of the answer path | Live-site failure must not reduce static-answer availability |
| NGA-NFR-02 | Voice latency | `NGA-BRD-22` budgets; no dead air |
| NGA-NFR-03 | Chat/WhatsApp latency | p95 ≤ 8 s |
| NGA-NFR-04 | Concurrency | Two voice callers + background work without cross-caller evidence leakage |
| NGA-NFR-05 | Isolation | No conversation may see another conversation’s evidence or state |
| NGA-NFR-06 | Security | SSRF, prompt injection, size/decompression limits, content-type allowlist |
| NGA-NFR-07 | Privacy | PII guard at build/tool/verifier; retention policy |
| NGA-NFR-08 | Data handling | Live text sent to OpenAI only under the approved data policy |
| NGA-NFR-09 | Maintainability | Schema versioned; migrations reversible; one graph compiler |
| NGA-NFR-10 | Portability | Postgres primary; SQLite single-box mode |
| NGA-NFR-11 | Observability | Every turn traceable without application logs |
| NGA-NFR-12 | Testability | All tools fixture-testable; no mandatory network in CI |
| NGA-NFR-13 | Rollback | Master flag and data migration rollback in < 15 minutes |
| NGA-NFR-14 | Cost | No new mandatory paid API; live reads bounded per turn |

## 7. Acceptance Gates

| Gate | Question | Pass condition | Owner |
|---|---|---|---|
| **NGA-G1 — Graph coverage** | Does the graph represent the site? | ≥95% sitemap coverage; 100% top-level sections and 51 departments; every exclusion typed | Architect + BA |
| **NGA-G2 — Grounding** | Are answers correct and cited? | ≥90% grounded live-answer rate; 0 critical-fact hallucinations; citation correctness 100% on critical cases | QA + PO |
| **NGA-G3 — Latency** | Does it fit the channels? | Voice/chat budgets from `NGA-BRD-22` met on the reference box | Architect + TPO |
| **NGA-G4 — Safety** | Is it safe? | Zero PII leaks; prompt-injection suite passes; allowlist/robots tests pass | Security/privacy |
| **NGA-G5 — Parity** | Do channels agree? | ≥95% same evidence set; only formatting differences allowed | QA |
| **NGA-G6 — Rollback** | Can we get out? | `off` reproduces current behaviour; rollback drill < 15 min | Operator |
| **NGA-G7 — Reference delivery** | Can the user see the source? | 100% of cited chat/WhatsApp answers show title+snippet+href; registered voice callers receive the WhatsApp reference; unregistered/opted-out callers receive nothing | QA + privacy |

## 8. Out of Scope

- Vector RAG, embeddings, similarity search.
- Model training or fine-tuning.
- Website CMS changes.
- Student result/merit/selection-list ingestion.
- Staff-directory publication.
- New CRM features or offer-letter changes.
- Multi-region or cloud migration.
- Live PDF fetch in the voice path.

## 9. Dependencies

| ID | Dependency | Owner | Needed by |
|---|---|---|---|
| D-01 | University permission / correction contact | University liaison | Production |
| D-02 | Verified fact sign-off for fees/dates | Registrar / PO | Gate G2 |
| D-03 | PII policy confirmation | Privacy owner | Gate G4 |
| D-04 | OpenAI data-handling approval | Security/privacy | Production |
| D-05 | Postgres availability (or SQLite decision) | Operator | Phase 1 |
| D-06 | Evaluation ground truth | PO + BA | Gate G2 |
| D-07 | Reference hardware for latency tests | Operator | Gate G3 |
| D-08 | Twilio WhatsApp sender + approved reference template (outside 24 h) | Operator + Twilio | Gate G7 |
| D-09 | WhatsApp opt-in/opt-out policy and registration window | PO + privacy | Gate G7 |

## 10. Traceability Matrix

| Intent metric | BRD requirements | HLD components | LLD modules |
|---|---|---|---|
| M-01 grounded live-answer rate | 01–08, 11, 14–16, 21–24 | C3/C4/C5/C7/C8 | M01–M07, M14 |
| M-02 critical-fact accuracy | 07–10 | C8 | M07 |
| M-03 PII leakage | 12, 13, 20, 25–28 | C10 | M10 |
| M-04 voice latency | 22, 23, 24 | C3/C4/C11 | M04/M08/M15 |
| M-05 chat/WhatsApp latency | 22, 23, 24 | C3/C4 | M08/M14 |
| M-06 freshness | 14–16, 32 | C7 | M11 |
| M-07 channel parity | 04, 05, 21 | C1/C2/C3 | M08 |
| M-08 static availability | 24, 29 | C11 | M15/M16 |
| M-09 record completeness | 17–20, 27, 30 | C9 | M09/M12 |
| M-10 graph coverage | 01–03, 06 | C5/C7 | M01–M03 |
| M-11 reference delivery | 08, 33, 34 | C09/C15 | M10/M22 |

## 11. Open Questions

| ID | Question | Owner | Blocks |
|---|---|---|---|
| Q-01 | Is staff-directory content ever allowed, and under what review? | PO + privacy | `NGA-BRD-12/13` |
| Q-02 | Which PDFs are in the in-scope public set? | Registrar + PO | `NGA-BRD-06` |
| Q-03 | What is the transcript/evidence retention period? | PO + privacy | `NGA-BRD-20` |
| Q-04 | Is live page text approved for the OpenAI provider? | Security/privacy | `NGA-NFR-08` |
| Q-05 | Can the university publish `llms.txt` / `notices.json`? | University liaison | `NGA-BRD-32` |
| Q-06 | What is the reference box for latency gates? | Operator | `NGA-G3` |
| Q-07 | Does outbound campaign context change the evidence policy? | PO | `NGA-BRD-21` |
| Q-08 | What counts as “registered” for voice→WhatsApp: lead + recent WhatsApp conversation, or explicit opt-in only? | PO + privacy | `NGA-BRD-34` |
| Q-09 | Which approved WhatsApp template is used outside the 24-hour window? | Operator | `NGA-BRD-34` |
