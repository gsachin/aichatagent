# Open Questions — Production Re-Architecture

**Created:** 2026-08-23
**Purpose:** Decisions that must be made (or deliberately deferred) before the multi-LLM
architecture RFP prompt can be written and its answers compared fairly.
**Status:** All questions UNANSWERED. Fill in the `**Answer:**` lines and hand this back.

**How to use this document**
- **Sections 1–6** are questions *you* answer. They change what the RFP asks for.
- **Section 7** lists blockers that are yours to clear regardless of architecture — no LLM can solve them.
- **Section 8** lists questions I recommend deliberately leaving *open* so each LLM must
  argue its own answer — that is what gives you something to compare.

Everything below is grounded in the codebase analysis performed 2026-08-23
(5 parallel agents over ~21.8k Python LOC, 218 tracked files). Evidence is cited as `file:line`.

---

## Section 1 — Blocking strategic decisions

These four determine roughly half the target architecture. Without them, every LLM will
assume something different and the plans will not be comparable.

### Q1 — Where does production run?

**Why it matters:** Today the GPU box *is* the product. Public reachability is an
ephemeral `cloudflared` quick tunnel whose hostname is **scraped by regex out of a log file
in `%TEMP%`** and cached to flat files (`.whatsapp_tunnel`, `.tunnel_8000/8501/8502`).
The URL changes on every restart, and every start kills all `cloudflared` processes
(`start_services.ps1:240-259`). Twilio webhooks must be re-pointed each time. Four separate
code paths re-implement host resolution (`main.py:273-289`, `offers/service.py:18-28`,
`outbound/caller.py:61-76`, `app.py:35-61`). This cannot go to production as-is under any
architecture — but the *fix* differs completely by target.

| Option | Implication |
|---|---|
| **Cloud (AWS/GCP/Azure)** | GPU instances + autoscaling + managed Postgres. Docs already price this: $337/mo (5–7 users) → $3,236/mo (50+). Assumes student data may leave premises. |
| **On-prem / self-hosted GPU** | Data never leaves. Focus shifts to containerization, HA, multi-GPU scheduling, real ingress (TLS reverse proxy) instead of tunnels. |
| **Hybrid** | Latency-critical inference stays on local GPU; control plane, DB, dashboards move to cloud. |
| **Let the LLMs propose** | Prompt requires each plan to evaluate all three with cost/latency numbers. |

**Recommendation:** *Hybrid* if the GPU box is already paid for and data residency is a
real concern; otherwise *cloud*. The current 6 GB VRAM ceiling (RTX 2060) is the single
hardest constraint in the system — it is why the model is quantized to `q3_K_M` with
accepted accuracy loss (`doc/model_vram_analysis.md`).

**Answer:**

---

### Q2 — What happens to the inference layer?

**Why it matters:** This is the **biggest single lever on your latency problem**.
Documented voice turn is **12–35s** against a stated design goal of ~1s
(`doc/INFRASTRUCTURE_PLAN.md`, `doc/architect_analysis.md`). One audit attributes
**~80% of the turn to TTS alone**. Today: Ollama (`qwen2.5:7b-instruct-q3_K_M`,
`stream=False`), faster-whisper `small.en`, Kokoro ONNX — all in-process, all on one GPU,
one uvicorn worker with no `--workers` flag ever passed.

| Option | Trade |
|---|---|
| **Keep fully local** | No data leaves, no per-token cost. Requires vLLM/TensorRT migration, streaming, quantization work to hit latency. Docs already propose vLLM (~10 tok/s → 50–100 tok/s). |
| **Managed streaming APIs** | Fastest path to sub-second turns; trades GPU ops for per-token cost and data egress. |
| **Hybrid by path** | Cloud API for the live voice turn; local models for batch, offline, and PII-sensitive work. |
| **Let the LLMs propose** | Each plan must benchmark local-vs-API against your latency budget and recommend with numbers. |

**Recommendation:** Answer **Q9 (latency target) first** — it likely forces this answer.
A hard sub-2s turn requirement is very difficult to hit with a local 7B on 6 GB VRAM.

**Answer:**

---

### Q3 — Tenancy model?

**Why it matters:** "Works for any university data" means very different work depending on
the answer, and the current code is actively hostile to *both* readings. `app/rag.py:78`
has `EXPECTED_MARKERS = ["meridian university"]` as a **build-time validation gate that
rejects any non-Meridian corpus outright** (exit 1, nothing written). Beyond that: one
Chroma collection hardcoded as `"langchain"`, **no tenant field in chunk metadata**
(only `section`/`source_file`/`doc_type`/`ingested_at`, `rag.py:220-225`), **no `where=`
filter at retrieval**, a `courses` table with no university column, and ~40 hardcoded
"Meridian" references across prompts, canned replies, a program-alias map
(`main.py:1118-1136`), STT spelling corrections (`voice_handler.py:516-517`), config
defaults, DB seed data, and ~10 test files asserting `$15,000`/`$18,500`.

| Option | What "plug and play" then means |
|---|---|
| **Multi-tenant SaaS** | Runtime isolation: tenant-scoped retrieval, per-tenant configs/phone numbers/data, row-level security. Highest bar. |
| **White-label, one deploy per university** | Fast re-configuration + re-index. No runtime isolation needed. Much cheaper. |
| **Single university, production-grade** | Just make it robust and easy to re-point at different data. |
| **Let the LLMs propose** | Each argues a model and shows the single→multi migration path. |

**Recommendation:** *White-label per-deployment* unless you have a concrete pipeline of
customers. It gets you ~80% of the "any university" benefit for a fraction of the work,
and the analysis found exactly one retrieval choke point (`query_rag`/`get_retriever`)
that makes a later multi-tenant refactor tractable.

**Answer:**

---

### Q4 — Which constraints are hard requirements?

Check all that apply.

| Constraint | Effect on the RFP |
|---|---|
| ☐ **Keep the existing stack where sane** | Favors incremental refactor of FastAPI/Streamlit/Postgres over greenfield rewrite. |
| ☐ **Zero-downtime / demo must keep working** | Forces every plan to be phased, with the system functional at each step. Rules out big-bang. |
| ☐ **Compliance (FERPA / GDPR / other)** | Student PII handling, audit logs, retention, consent become explicit requirements. See Q16–Q18. |
| ☐ **Cost ceiling matters** | Each plan must include TCO. Cheapest-adequate beats most-scalable. State the ceiling if there is one. |

**Answer:**

---

## Section 2 — Scope decisions

### Q5 — Which user-facing surfaces survive into production?

**Why it matters:** There are currently **four UI surfaces over one backend**, with heavily
duplicated features: Streamlit chat (`app.py`, 897 lines), Streamlit dashboard
(`dashboard.py` + `app/dashboard/`), static "Command Cockpit" SPA
(`app/static/dashboard.html` + `dashboard.js`), and static voice/quick-call pages.
KPI overview, lead pipeline, conversation search, quick-call and follow-up calendar are
each implemented **twice**. Voice input exists in **three** forms (`st.audio_input`,
`voice_client.html` WebSocket, Twilio phone). Carrying all four into production means
maintaining every feature twice, forever.

Channels: ☐ Inbound voice ☐ Outbound voice ☐ WhatsApp ☐ Web chat ☐ Staff dashboard

Which implementation wins for the dashboard — Streamlit, the static SPA, or a rewrite?

**Recommendation:** Pick **one** dashboard and delete the other. The static SPA already has
SSE live-calls and a batch dialer; Streamlit has zero caching and an N+1 (one sentiment
HTTP call *per lead per render*, `leads_page.py:113`).

**Answer:**

---

### Q6 — Is the offer-letter + payment flow in production scope?

**Why it matters:** It works end-to-end *except* the parts that need real money and real
credentials. PDF generation works (fpdf2, 3,031-byte branded A4). Payment gateway
(Stripe/Razorpay) is **planned but never built** — every offer currently embeds
`DEFAULT_PAYMENT_LINK = "https://pay.university.edu/admissions"`, a placeholder domain
(`config.py:94`). Email delivery code works but **has never sent** — SMTP creds are empty
and the send is silently skipped (`doc/offerletterfile/gap_analysis.md` Gap 9).
The PDF is signed by a hardcoded "Dr. A. Chancellor" (`offers/pdf.py:190`).

**Answer:**

---

### Q7 — Is sentiment / lead scoring in production scope, and at what fidelity?

**Why it matters:** Two different things share the name. In-app: an LLM extraction +
weighted formula in `app/sentiment/` that runs **synchronously in the request path** —
`POST /api/interactions/log` blocks on 1–2 local 7B inferences, and voice-call hangup
chains **3–4 sequential** ones inline in the WebSocket `finally` (`main.py:2710-2793`).
Separately, `doc/Sentiment_analysis/INTEGRATION_GUIDE.md` documents a whole standalone
Kafka/Temporal/Deepgram/Hume/LightGBM platform that **appears nowhere in this repo** — the
analysis could not determine whether it is a real deployed system, a plan, or a pitch.

**Please clarify what that second document refers to.** It materially changes the target architecture.

**Answer:**

---

### Q8 — MCP server: promote, keep, or drop?

**Why it matters:** `app/mcp/` is **not actually an MCP server**. The `mcp==2.0.0` SDK is
installed and detected, but `handle_sse_request` deliberately falls through to a hand-rolled
REST imitation (`server.py:62-65`); the SSE stream emits one metadata line then 30s
keepalives forever and never speaks the MCP protocol. `MCP_ENABLED` (`config.py:75-77`) is
read **nowhere** except being echoed in the health endpoint — a dead switch. There is no
auth on `/mcp/*`. It exposes 7 leads-only tools.

**Answer:**

---

## Section 3 — Performance targets (needed to design against)

### Q9 — What is the target latency, per channel?

**Why it matters:** Without a number, "improve latency" is unfalsifiable and the plans
cannot be scored. This is the most important number in the entire RFP.

| Channel | Documented today | Your target |
|---|---|---|
| Inbound/outbound voice, per turn | **12–35s** (design goal was ~1s) | ? |
| WhatsApp text | ~5–15s (Twilio webhook hard-times-out at **15s**) | ? |
| WhatsApp voice note | **11–18s** measured — *already exceeds the 15s timeout* | ? |
| Streamlit chat, first token | No streaming at all today; full answer only | ? |

**Recommendation:** Set a p95 target, not an average, and set it per channel. Note the
WhatsApp voice-note path is already over Twilio's timeout — that is a correctness bug, not
just slowness.

**Answer:**

---

### Q10 — Is barge-in (interrupting the agent mid-sentence) required?

**Why it matters:** Today it is not just missing — caller audio is **actively discarded**
while the agent speaks. `MUTE_STT_DURING_TTS` (default on) drops every inbound chunk during
playback and calls `reset_utterance()`, throwing away partially accumulated speech
(`main.py:547-550`). It is an acoustic-echo-cancellation workaround. Real barge-in requires
proper AEC and streaming TTS, and is a significant chunk of work.

**Answer:**

---

### Q11 — Concurrency target: how many simultaneous calls / chats?

**Why it matters:** The current answer is effectively **one**. The outbound worker processes
strictly one call at a time — a 5-minute call blocks every other queued call
(`outbound/caller.py:84-86`). Per-call state lives in process-local dicts
(`_active_call_sids`, `_transcript_events`, `_batch_jobs`), so a second uvicorn worker
breaks the live-calls SSE feed and 404s batch-status lookups. Docs model VRAM as
`6.65 + (users × 0.75) GB` — on a 6 GB card that is **zero concurrent users** with headroom.

**Answer:** Peak concurrent calls: ____  Peak concurrent chats: ____

---

## Section 4 — RAG and data

### Q12 — What does "any university data" mean concretely?

Today: **markdown only**, exactly one hardcoded file
(`content/meridian/meridian_knowledge_base.md`, 11 KB → 28 chunks), pinned in a Python
constant (`rag.py:47-52`). The PDF/docx loaders were removed. Ingestion is a **script only**
(`scripts/rebuild_rag_index.py`) — no endpoint, no UI. Index swap is a **manual operator
rename** of a directory.

- What formats must be supported? ☐ Markdown ☐ PDF ☐ DOCX ☐ HTML/web crawl ☐ CSV/structured ☐ SIS/CRM feed
- Who supplies the data — university staff, or your team?
- How often does it change? ☐ Once at onboarding ☐ Termly ☐ Continuously
- Roughly how large? (Current corpus is 28 chunks. A 100-page handbook was estimated at 5–10 min to ingest on CPU.)

**Answer:**

---

### Q13 — Does the corpus need self-service runtime updating?

i.e. can a university admin upload a new prospectus through a UI and have it live, or is an
operator running a rebuild script acceptable? This is the difference between a content
pipeline with versioning/rollback and a documented runbook.

**Answer:**

---

### Q14 — Will you provide a golden question/answer set per university?

**Why it matters:** There is currently **no retrieval-quality evaluation of any kind**. The
existing "RAG tests" assert that canned strings like `"15000"` appear in the LLM output
against 3 in-memory documents. No recall@k, no golden set, no LLM-as-judge, no regression
harness. Without a golden set you cannot prove a corpus swap worked, and you cannot compare
the retrieval quality of competing architectures — which is precisely what you are trying
to evaluate.

**Recommendation:** Say yes. ~50 real questions with expected answers per university.
This is the highest-leverage thing you can supply.

**Answer:**

---

### Q15 — When do real institutional figures replace the demo data?

All current Meridian fees, deadlines, contacts and the campus address are **demo data**,
flagged as such in the source docs (risk R-11). The DB seed additionally hardcodes those
same figures as literal strings in 8 conversation transcripts and a 14-row course catalog
(`scripts/seed_demo_data.py:54-200`) — so a corpus swap silently leaves contradictory
figures in the database.

**Answer:**

---

## Section 5 — Security and compliance

### Q16 — What is the auth model?

**Why it matters:** There is **no authentication on anything**. Every `/api/*` endpoint is
open. `/api/demo/reset` deletes all tables with no auth. Offer-letter PDFs and uploaded
student documents are served unauthenticated at guessable URLs
(`/api/offers/{id}/pdf`, `/api/documents/{id}/file`). `/mcp/*` is open. The health endpoint
leaks the Twilio phone number and DB state. Anyone who learns the tunnel hostname has full
control of the system and full read access to student documents.

- Staff dashboard auth: ☐ SSO/SAML ☐ OAuth ☐ Username+password ☐ IP allowlist
- Student-facing document access: how is it authorized?

**Answer:**

---

### Q17 — Twilio webhook signature validation — confirm this becomes a requirement?

**Why it matters:** **No `X-Twilio-Signature` validation exists anywhere in the repo.**
Anyone who knows your webhook URL can forge inbound calls, WhatsApp messages, and call
status callbacks. This is a straightforward fix and should be non-negotiable for production.

**Recommendation:** Yes, mandatory.

**Answer:**

---

### Q18 — PII retention and consent policy?

Currently stored indefinitely with no retention policy, no encryption at rest beyond the DB
default, and no audit log: full call transcripts, WhatsApp conversation history, uploaded
student identity documents (on local disk under `data/documents/{lead_id}/`), phone numbers,
email addresses, and LLM-extracted profile data.

Also note: `scripts/seed_demo_data.py:7-10` and `docker-compose.yml` contain **hardcoded
database credentials in source**, and a live Cloudflare tunnel hostname is committed to the
repo in `.whatsapp_tunnel`.

- Retention period for transcripts/recordings: ____
- Retention for uploaded documents: ____
- Is call recording consent captured? ☐ Yes ☐ No ☐ N/A
- Regulatory regime: ☐ FERPA ☐ GDPR ☐ India DPDP ☐ Other: ____

**Answer:**

---

## Section 6 — Operational and commercial

### Q19 — Who operates this in production, and what is the team?

Team size, skills available (Kubernetes? GPU ops? on-call?), and whether ops is in-house or
managed. A plan that assumes a platform team is worthless if there is no platform team.

**Answer:**

---

### Q20 — Timeline and budget?

- Target date for production: ____
- Build budget: ____
- Monthly running budget: ____ (docs model $337–$3,236/mo depending on tier)

**Answer:**

---

### Q21 — Any existing systems to integrate with?

☐ Student Information System (Banner/PeopleSoft/…) ☐ CRM (Salesforce/HubSpot/…)
☐ Payment gateway ☐ Email/calendar ☐ None — standalone

**Why it matters:** Nothing in the current code integrates with anything except Twilio and
SMTP. If a real SIS/CRM is in the picture it changes the data model substantially.

**Answer:**

---

## Section 7 — External blockers (yours to clear, no architecture fixes these)

These are flagged so they do not get lost. Each is an unverified path in the current system.

| # | Blocker | Evidence |
|---|---|---|
| B1 | **WhatsApp Business API never provisioned** — still on the Twilio sandbox, error 63007. Production WhatsApp cannot launch without this. | `doc/offerletterfile/IMPLEMENTATION_STATUS.md` |
| B2 | **Email has never sent end-to-end** — SMTP credentials empty; sends are silently skipped. | `gap_analysis.md` Gap 9 |
| B3 | **Real institutional data not supplied** — fees, deadlines, signatory, payment URL are all placeholders. | risk R-11 |
| B4 | **Payment gateway not selected** (Stripe/Razorpay) — every offer links to a fake domain. | `config.py:94` |
| B5 | **Twilio geographic permissions** for target calling regions (India numbers used in testing). | `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md` |
| B6 | **Inbound caller phone number never threaded through the WebSocket** — post-call follow-up is impossible for inbound calls; sentiment saved with `lead_id=""`. | `updated_plan_student_self_service.md` Gap 2 |
| B7 | **No CI exists** — `.github/` contains only Copilot instructions. Also `requirements.txt` declares **no pytest**, so the test suite cannot run in the shipped environment. | verified |

---

## Section 8 — Recommended to leave OPEN for the competing LLMs

Do **not** answer these. Requiring each LLM to argue its own position on them is what makes
the plans comparable and worth commissioning. The RFP prompt will mandate a structured
answer for each.

| # | Question to put to the LLMs |
|---|---|
| O1 | Module decomposition of the 2,793-line `app/main.py` — what are the correct service boundaries, and in what order should they be extracted while keeping the system running? |
| O2 | Sync-to-async strategy — psycopg2 has no async support; is the answer asyncpg, SQLAlchemy async, a thread pool, or a separate worker tier? |
| O3 | Where to draw the process boundary between inference and application logic (in-process, sidecar, separate service, model server)? |
| O4 | Streaming architecture end-to-end — partial STT → streaming LLM → chunked TTS → barge-in. What is the minimum viable version? |
| O5 | Which of the ~6 known latency bottlenecks to attack first for best return per unit of effort. |
| O6 | Vector store choice — stay on Chroma, or move to pgvector (Postgres is already there), Qdrant, or another. Justify against corpus size and tenancy model. |
| O7 | The correct abstraction for "swap the university" — config + convention, a plugin interface, or full multi-tenancy. |
| O8 | State externalization approach for the in-memory dicts (Redis? DB? sticky sessions?) and the resulting scaling model. |
| O9 | Migration sequencing and risk — what order, what is reversible, where are the one-way doors. |
| O10 | Observability stack and the specific SLIs/SLOs worth instrumenting for a voice pipeline. |
| O11 | Testing strategy — what to mock (Twilio, LLM, SMTP are currently unmocked), and how to regression-test a non-deterministic LLM pipeline. |
| O12 | Build vs buy for each component, with TCO. |

---

## Next step

Fill in Sections 1–6 (Section 7 is FYI, Section 8 is deliberately unanswered) and hand this
back. I will then write the enriched multi-LLM RFP prompt to
`doc/prod_enhancemnt/`, including a scoring rubric so the returned plans can be compared
on equal terms.

**Minimum viable answer set** if you want to move fast: **Q1, Q2, Q3, Q9, Q12**.
Those five alone are enough to write a good prompt; the rest sharpen it.
