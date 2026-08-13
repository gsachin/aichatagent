# Test Cases — Meridian Update (Phase-Aligned, Rev 3)

**Date:** 2026-08-14 · **Branch:** `offerlaterupdate`
**Aligned with:** `Implementation_Plan_Meridian_Update.md` (phases 1–4) · `RAG_Improvement_Recommendations.md` (rev 2) · `Impact_Analysis_Meridian_Data_Replacement.md`

**Purpose:** every change in the implementation plan has at least one test; each phase has its own gate; the full pipeline has end-to-end journey tests. All test IDs from the previous revision are retained and re-mapped (§9), plus new IDs for plan-specific items.

**Modes:** 🧪 pytest (automated) · ⚙️ script/CLI · 🖐️ manual E2E (Twilio/browser)

**Phase gating rule:** a phase is "done" when its 🧪 tests are green AND its 🖐️ smoke items pass. Phase 2 must never ship to production without Phase 3 (see plan §5).

---

# PART A — PHASE 1 TESTS (zero-impact preparations)

*Goal: everything additive/env-gated OFF; app behavior identical to pre-update baseline. Baseline capture first: record 20 reference Q/A from the current (UMD/FDU) store — used to prove "no behavior change" in Phase 1.*

## A1. Config plumbing (`app/rag.py`, `app/pipeline.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| CFG-01 | `CHROMA_DB_PATH` honored (rag) | Set `CHROMA_DB_PATH` to temp dir; `python -c "from app.rag import CHROMA_DB_PATH; print(CHROMA_DB_PATH)"` | Prints temp dir. Unset → default `chroma_local_db`. | 🧪 |
| CFG-02 | `CHROMA_DB_PATH` honored (pipeline) | Same check against `app.pipeline.CHROMA_DB_PATH` | Same behavior. | 🧪 |
| CFG-03 | Chunk env defaults | Unset env; print `CHUNK_SIZE`/`CHUNK_OVERLAP` | 1500 / 100 (unchanged defaults until Phase 3). | 🧪 |
| CFG-04 | Chunk env overrides | `RAG_CHUNK_SIZE=600 RAG_CHUNK_OVERLAP=90` | 600 / 90 read from env. | 🧪 |
| CFG-05 | Search mode default | Unset `RAG_SEARCH_MODE` | `"mmr"` — MMR path used (debug log). | 🧪 |
| CFG-06 | Threshold default OFF | Unset `RAG_SIMILARITY_THRESHOLD` | `0.0`; no threshold pre-check executes. | 🧪 |
| CFG-07 | Temperature default OFF | Unset `OLLAMA_TEMPERATURE` | `""`; `ollama.chat` called with `options={"num_ctx": …}` only (no temperature key). | 🧪 |
| CFG-08 | Max-context-chars default OFF | Unset `RAG_MAX_CONTEXT_CHARS` | `0`; no trimming applied. | 🧪 |
| CFG-09 | Phase-1 behavior parity | Run the 20-question baseline against current store, before/after Phase 1 changes | Identical answers (byte-for-byte) — proves zero functional impact. | 🧪 |

## A2. Overflow guard (`retrieve_context`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| OFL-01 | Warning near limit | Build context >80% of `OLLAMA_NUM_CTX`×4 chars (monkeypatch retriever) | Warning logged: "RAG context near overflow". | 🧪 |
| OFL-02 | Trim when enabled | `RAG_MAX_CONTEXT_CHARS=500`; retrieve on a large store | Context length ≤ 500; answer still coherent. | 🧪 |
| OFL-03 | Silent default | Defaults; normal query | No warnings; no trimming; answer unchanged vs baseline. | 🧪 |

## A3. Temperature wiring (`query_rag`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| TMP-01 | Env honored | `OLLAMA_TEMPERATURE=0.2`; mock `ollama.chat`; query | `options` includes `temperature: 0.2`. | 🧪 |
| TMP-02 | Unset → server default | Unset; mock `ollama.chat` | `options` has no `temperature` key. | 🧪 |

## A4. Hybrid retrieval (additive; OFF by default)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| HYB-01 | Function returns chunks | Call `retrieve_context_hybrid("MBA tuition?")` on existing store | Non-empty string of chunks; no exception. | 🧪 |
| HYB-02 | Hybrid implementation path | Inspect `retrieve_context_hybrid` | Uses dense KNN + stdlib `_MiniBM25` + Python RRF — **not** `collection.search()` (chromadb 1.5.9 raises `NotImplementedError` on local backend — verified). | 🧪 |
| HYB-03 | Flag OFF → MMR | `RAG_SEARCH_MODE` unset; query | `retrieve_context_hybrid` never called (debug log shows MMR). | 🧪 |
| HYB-04 | Flag ON + failure → fallback | `RAG_SEARCH_MODE=hybrid`; point `CHROMA_DB_PATH` at a corrupt dir | Exception logged "falling back to MMR"; MMR path still answers (or graceful not-found). | 🧪 |
| HYB-05 | Hybrid answer quality | `RAG_SEARCH_MODE=hybrid`; ask "BCA fees" on clean Meridian store (Phase 3+) | Answer includes BCA $10,200/year. | 🧪 |

## A5. Similarity threshold gate (OFF by default)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| THR-01 | Default no-op | Threshold unset; query anything | No `distances` pre-query executed (mock `chromadb` or check logs). | 🧪 |
| THR-02 | Gate triggers | Threshold 0.9 + cosine store (Phase 3+); out-of-scope question | Returns "I don't have that specific information in the university profile." without LLM call. | 🧪 |
| THR-03 | Gate passes through | Threshold 0.9 + cosine store; on-topic question | Normal RAG answer. | 🧪 |
| THR-04 | Pre-check failure safe | Threshold on; corrupt store path | Exception logged; query proceeds without the gate. | 🧪 |

## A6. Pipeline cleanup (`app/pipeline.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| PIPE-01 | Duplicate removed | `hasattr(app.pipeline, "retrieve_context")` | `False` — function deleted. | 🧪 |
| PIPE-02 | Consumers updated | Run `run_pipeline_test.py` and `test_full_pipeline.py` | Both pass importing from `app.rag`. | ⚙️ |
| PIPE-03 | `build_rag_prompt` intact | Call with a question | Same output shape as before (imports `app.rag` functions). | 🧪 |

## A7. Rebuild script (`scripts/rebuild_rag_index.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| RBLD-01 | Validation rejects UMD/FDU source | Point source list at old `UMD_and_FDU_University_Profile_Report.pdf`-derived text | Exit code ≠ 0; "VALIDATION FAILED" with blocked markers; no store written. | ⚙️ |
| RBLD-02 | Validation rejects foreign markers | Feed md containing "University of Maryland" / "Terrapin" / "Maradian" | Blocked (all three). | 🧪 |
| RBLD-03 | Validation passes clean source | Feed `meridian_knowledge_base.md` | No issues; proceeds to build. | 🧪 |
| RBLD-04 | Builds fresh directory only | Run with `CHROMA_DB_PATH` = fresh dir | New dir created; **live `chroma_local_db` untouched** (mtime/content unchanged). | ⚙️ |
| RBLD-05 | Chunk count sane | After build | 25–60 chunks (verified: 31 — not 5,346). | 🧪 |
| RBLD-06 | Cosine space set | Read collection metadata | `hnsw:space == "cosine"`. | 🧪 |
| RBLD-07 | Metadata attached | `collection.peek()` sample | Chunks carry `source_file`, `doc_type`, `ingested_at`. | 🧪 |
| RBLD-08 | Smoke tests gate | Temporarily break source (empty md) | Exit ≠ 0; no smoke pass; build refused or smoke fails. | ⚙️ |
| RBLD-09 | Negative canary | Smoke includes "What is the Terrapin Commitment?" | Context contains **none** of the blocked markers. | 🧪 |
| RBLD-10 | Swap + rollback drill | Rename swap per plan §3.8, then revert | App serves new store after swap; returns to old after revert; `.bak` never auto-deleted. | 🖐️ |

## A8. Regression — existing suite untouched

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| REG-01 | Full existing suite | `pytest tests/ -x` | All green (verified: no UMD/FDU assertions exist in tests/). | 🧪 |
| REG-02 | Phase-1 runtime regression | Start FastAPI + Streamlit after Phase 1; run baseline Q/A | Identical behavior to pre-update (CFG-09). | 🖐️ |

---

# PART B — PHASE 2 TESTS (rebrand copy; store NOT yet swapped)

*Expected transitional state: copy says Meridian; answers still from old store (UMD/FDU) until Phase 3. Tests here assert COPY only — do not assert answer content in this phase.*

## B1. Config identity

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| BRD-09 | `UNIVERSITY_NAME` default | Fresh env, no override | `"Meridian University"`. | 🧪 |
| BRD-10 | Env override still wins | `UNIVERSITY_NAME=Test University` | Override respected (config unchanged in mechanics). | 🧪 |

## B2. Telephony copy (`app/main.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| BRD-05a | IVR menu | `GET /twilio/voice` with `TUNNEL_HOST` set; inspect TwiML | Contains "Press 1 for Undergraduate programs." and "Press 2 for Postgraduate programs."; **no** UMD/FDU string. | 🧪 |
| BRD-05b | IVR semantics unchanged | Confirm WS handler still ignores DTMF (`app/main.py` dtmf branch) | Both digits connect to the same AI stream — behavior identical to before. | 🧪 |
| BRD-11 | Inbound greeting | Inspect `generate_ulaw_greeting` text in `/ws/twilio` start handler | "Ask me anything about Meridian University programs, tuition fees, or how to apply." | 🧪 |
| BRD-12 | Outbound greeting | Same check for `/ws/twilio-outbound` | Meridian text; no UMD/FDU. | 🧪 |

## B3. WhatsApp copy + program capture (`app/main.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| BRD-06 | Empty-message reply | `POST /twilio/whatsapp` empty body (mock Twilio) | "Send me a question about Meridian admissions…". | 🧪 |
| BRD-13 | Name-thanks reply | Send a name to a lead without email | "How can I help you with Meridian admissions?" | 🧪 |
| BRD-14 | Fallback reply | Trigger the final fallback branch | "How can I help with Meridian admissions?" | 🧪 |
| PCAP-01 | Helper — exact match | `_detect_meridian_program("i want b.tech ai & machine learning")` | `"B.Tech AI & Machine Learning"`. | 🧪 |
| PCAP-02 | Helper — abbreviations | `"mca"`, `"mba"`, `"bca"` inputs | `"MCA"`, `"MBA"`, `"BCA"`. | 🧪 |
| PCAP-03 | Helper — long form | `"master of business administration"` | `"MBA"`. | 🧪 |
| PCAP-04 | Helper — no match | `"i like painting"` | `""` (empty). | 🧪 |
| PCAP-05 | Helper — case-insensitive | `"B.TECH Computer Science"` | `"B.Tech Computer Science"`. | 🧪 |
| PCAP-06 | Intent path uses helper | `_detect_admission_intent_whatsapp("I am ready to enroll in mca")` | `(True, "MCA")`. | 🧪 |
| PCAP-07 | Short-reply path uses helper | Simulate the `lead_name+email` short-reply branch with "bca" | `program_interest` updated to `"BCA"`. | 🧪 |
| PCAP-08 | Examples updated | Grep lines 1355/1388/1411 | Examples mention Meridian programs (B.Tech Computer Science / MBA / BCA). | 🧪 |

## B4. STT dictionary (`app/voice_handler.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| STT-01 | UMD/FDU entries gone | Grep the correction dictionary | Zero UMD/FDU/maryland entries. | 🧪 |
| STT-02 | Meridian variants present | `"maridian"`, `"miridian"`, `"meridien"` keys | All map to `"Meridian"`. | 🧪 |
| STT-03 | Generic entries kept | `"emma"`, `"gp a"`, `"i elts"` | Still → MBA / GPA / IELTS (valid for Meridian). | 🧪 |
| STT-04 | Correction applied | Run the correction function on a transcript with "maridian" | Output contains "Meridian". | 🧪 |

## B5. UI copy

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| BRD-03 | Landing page | Open `/` | Meridian copy; no UMD/FDU. | 🖐️ |
| BRD-04 | Streamlit UI | Run `streamlit run app.py` | Headers/captions say Meridian (lines 70/156/162). | 🖐️ |
| BRD-15 | Voice client page | Open `/voice` | Subtitle: "Local AI — ask about Meridian admissions". | 🧪 |
| BRD-01 | Whole-codebase grep gate | `grep -riE "umd|fdu|fairleigh|maryland" app/ app.py scripts/ app/static/` | Only allowed: validation-gate blocked-marker list. | 🧪 |

---

# PART C — PHASE 3 TESTS (source swap, seeds, launchers, rebuild — the release)

## C1. RAG source + prompt (`app/rag.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| SRC-01 | Source points to Meridian md | Inspect `SOURCE_PATHS` / build path | `content/meridian/meridian_knowledge_base.md`; no reference to the UMD/FDU PDF in `app/rag.py`. | 🧪 |
| SRC-02 | Prompt rule 3 removed | Inspect `SYSTEM_PROMPT` | No "Keep UMD and FDU information clearly separated". | 🧪 |
| SRC-03 | Citation rule added | Inspect `SYSTEM_PROMPT` | Contains "cite the section" rule. | 🧪 |
| SRC-04 | Header regex inert | Build from the md source | No content loss (regex doesn't match markdown). | 🧪 |
| SRC-05 | Chunk defaults 600/90 | Unset env; print constants | 600 / 90. | 🧪 |
| SRC-06 | Empty-store auto-build | `CHROMA_DB_PATH` → fresh dir; call `get_vector_store()` | Builds from the md source automatically. | 🧪 |

## C2. Store swap

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| SWAP-01 | Swap executed | Plan §3.8 commands | Live dir = new store; `.bak` exists with old store. | ⚙️ |
| SWAP-02 | Warmup OK | Restart FastAPI | Logs "ChromaDB vector store pre-warmed"; `/` health `200`. | 🧪 |
| SWAP-03 | Store size | `collection.count()` | 25–60 (verified: 31). | 🧪 |
| SWAP-04 | Cosine metric | Collection metadata | `hnsw:space == cosine`. | 🧪 |

## C3. Meridian knowledge correctness (post-swap — the core suite)

| ID | Question | Must contain | Mode |
|---|---|---|---|
| RAG-01 | What is the tuition fee for the MBA program? | $18,500 per year, 2 Years, Bachelor's Degree | 🧪 |
| RAG-02 | What undergraduate programs does Meridian offer? | B.Tech CS, B.Tech AI & ML, BBA, BCA, B.Com, BA, B.Sc | 🧪 |
| RAG-03 | Tell me about B.Tech AI & Machine Learning | 4 Years, 10+2 (PCM), $15,200/year | 🧪 |
| RAG-04 | What fees do I pay besides tuition? | $60/$75 app, $500/$650 registration, $3,200 hostel, $350 library, $120/$150 exam, $300 deposit | 🧪 |
| RAG-05 | What scholarships are available? | Merit up to 50%, Need-Based, Sports & Arts | 🧪 |
| RAG-06 | What are the application deadlines? | Jan 15, Apr 30, Jun 10, Aug 1 | 🧪 |
| RAG-07 | Which intakes does Meridian have? | Fall, Spring, limited Summer | 🧪 |
| RAG-08 | How do I apply to Meridian? | 4 steps: apply online → documents → review → offer | 🧪 |
| RAG-09 | What documents are required? | transcripts, ID/passport, photo, SoP, English score (intl) | 🧪 |
| RAG-10 | How can I contact the admissions office? | 221 Harborview Road · +1 (555) 204-7890 · admissions@meridian.edu · 9–5 Mon–Fri | 🧪 |
| RAG-11 | Can I apply to more than one program? | "up to two programs within the same intake cycle" | 🧪 |
| RAG-12 | What are the campus facilities? | Smart classrooms, 200,000+ volume library, innovation labs, 40+ clubs | 🧪 |
| RAG-13 | When was Meridian University founded? | 1978, 240 students | 🧪 |
| RAG-14 | What doctoral research areas? | 4 areas listed; fees per research area | 🧪 |
| RAG-15 | What is Meridian's placement rate? | 95% · 5,000+ students · 150+ faculty · 50+ programs | 🧪 |
| RAG-16 | Citation present (any Q) | answer cites "(§ section)" | 🧪 |

## C4. Leakage canaries

| ID | Test | Expected result | Mode |
|---|---|---|---|
| NEG-01 | "What is the tuition at UMD?" | Not-found answer; no UMD figures | 🧪 |
| NEG-02 | "Tell me about FDU MBA fees" | Not-found; no FDU figures | 🧪 |
| NEG-03 | "What is the Terrapin Commitment?" | Not-found; zero FAFSA/Terrapin/MDDCS content | 🧪 |
| NEG-04 | "Do you have residence halls like Ellicott or Cambridge?" | Not-found; no 39 halls / 9,601 beds | 🧪 |
| NEG-05 | Store document dump scan for blocked markers | Zero hits | ⚙️ |
| NEG-06 | Chunk count sane | 25–60 (verified: 31) | 🧪 |
| NEG-07 | "What is the weather in Meridian City?" | Not-found, no hallucination | 🧪 |

## C5. Seeds (`scripts/seed_demo_data.py`)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| SEED-01 | Course catalog matches KB | Diff `courses_data` fees/names against knowledge-base tables | All 14 rows match exactly (MBA $18,500, B.Tech AI & ML $15,200 …). | 🧪 |
| SEED-02 | No UMD/FDU in seeds | Grep file | Zero hits. | 🧪 |
| SEED-03 | Seed idempotent | Run twice | No errors; row counts stable (DELETEs first). | ⚙️ |
| SEED-04 | Course matching works | `get_course_by_name("MBA")` | Returns row with $18,500/year. | 🧪 |
| SEED-05 | Demo reset endpoint | `POST /api/demo/reset` | Re-seeds Meridian data; dashboard loads. | 🧪 |
| SEED-06 | New lead program names match | Seed a lead with "B.Tech AI & Machine Learning" | `get_course_by_name` matches (offer flow gets fees). | 🧪 |

## C6. Historical data policy (must NOT break)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| HIST-01 | Old leads intact | Query existing leads created pre-release | Rows unchanged; dashboard lists them. | 🧪 |
| HIST-02 | Old offers served | `GET /api/offers/{old_id}/pdf` | PDF still served (200) — historical documents untouched. | 🧪 |
| HIST-03 | Old program names fall back | `generate_and_send_offer` for a lead with `program_interest="Computer Science"` | Offer generated with raw program name (no crash; no fees row). | 🧪 |
| HIST-04 | Sentiment history intact | `GET /api/leads/{id}/sentiment` for an old lead | History preserved. | 🧪 |

## C7. Legacy retirement + launchers + docs

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| LEG-01 | `admissions_bot.py` can't write | Grep for `Chroma.from_documents` / `PersistentClient` writes | None (or file deleted). | 🧪 |
| LCH-01 | `launch.bat` checks Meridian md | Inspect lines ~31-34 | Checks `content\meridian\meridian_knowledge_base.md`; no old-PDF reference. | 🧪 |
| LCH-02 | `launch_tunnel.bat` same | Inspect | Same as LCH-01. | 🧪 |
| LCH-03 | Launcher runs with PDF absent | Temporarily rename old PDF; run launcher pre-flight portion | Passes (does not FAIL on old PDF). | ⚙️ |
| DOC-01 | `launch_Guide.txt` examples | Inspect | Meridian sample questions. | 🧪 |
| DOC-02 | Deployment doc §6 | Inspect `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md` | Rebuild source = Meridian md. | 🧪 |

## C8. Offer pipeline on Meridian (release-blocking — R-02)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| OFR-04 | Full simulated flow | Lead → fields → doc upload → `generate_and_send_offer` | Offer row; PDF at `data/offers/{id}.pdf`; WhatsApp/email attempted or logged-skipped. | 🧪 |
| OFR-05 | Meridian PDF header | Open generated PDF | "Meridian University" header; no UMD/FDU text. | 🧪 |
| OFR-06 | Fees from Meridian course row | Offer for "MBA" | PDF + WhatsApp amount = **$18,500/year**. Must fail against old seed data, pass against new (canary for R-02). | 🧪 |
| OFR-07 | Program fallback | Lead with unmatchable program | Offer with raw name; no crash. | 🧪 |
| OFR-08 | Idempotency | Generate twice | Second call returns existing offer within guard window. | 🧪 |
| OFR-09 | ACCEPT/DECLINE | API + WhatsApp replies | DB status flips; replies correct. | 🧪 |
| OFR-01 | Readiness — missing fields | Lead with only phone | `ready=false`, missing `["name","email","program_interest","document"]`. | 🧪 |
| OFR-02 | Readiness — complete | Full lead + 1 doc | `ready=true`. | 🧪 |
| OFR-03 | Blocked when not ready | `generate_and_send_offer` on incomplete lead | Returns `None`; "offer skipped" logged; no PDF. | 🧪 |

---

# PART D — PHASE 4 TESTS (post-release tuning, env-gated)

| ID | Test | Steps | Expected result | Mode |
|---|---|---|---|---|
| TUNE-01 | Hybrid enable | `RAG_SEARCH_MODE=hybrid` in `.env`; restart | Hybrid path used; RAG-01..16 still pass (re-run C3). | 🧪 |
| TUNE-02 | Hybrid keyword wins | "MCA eligibility" | Finds "Bachelor's in Computing" row (MMR baseline comparison documented). | 🧪 |
| TUNE-03 | Hybrid latency | Measure retrieval on 20 queries | ≤ ~50 ms retrieval (excl. LLM). | 🧪 |
| TUNE-04 | Temperature chat | `OLLAMA_TEMPERATURE=0.3`; fee question twice | Consistent, no invented figures. | 🧪 |
| TUNE-05 | Threshold enable | Measure real distance distribution first, then set threshold | Out-of-scope questions → honest not-found; on-topic unaffected. | 🧪 |
| TUNE-06 | Citations render | Check RAG-16 across channels (chat, WhatsApp, voice text path) | §-citations present in text channels. | 🖐️ |

---

# PART E — ENTIRE PIPELINE (end-to-end journeys)

*Each journey is one continuous scenario proving the whole system works together post-update.*

| ID | Journey | Steps | Expected result | Mode |
|---|---|---|---|---|
| E2E-01 | **Streamlit full journey** | Open chatbot → enter name/email/phone/program (B.Tech AI & ML) → ask "what is the fee?" → upload transcript image → verify offer auto-generation | Chat answers $15,200 with citation; lead row has all fields; document saved; offer PDF generated with Meridian header + correct fees; dashboard shows the lead + offer | 🖐️ |
| E2E-02 | **WhatsApp full journey** | New sandbox number → "Hi" → provide name → email → "I want B.Tech AI & Machine Learning" → "I want to take admission" → send transcript photo → receive offer → reply "accept" | State machine collects all fields; program captured (PCAP); docs saved; offer WhatsApp message with PDF link + $15,200 amount; ACCEPT flips status; congratulation reply | 🖐️ |
| E2E-03 | **Voice note journey** | WhatsApp voice note: "How much is the MBA fee?" | Whisper STT → Meridian RAG → text + TTS MP3 reply, both $18,500 | 🖐️ |
| E2E-04 | **Inbound call journey** | Call Twilio number → IVR → ask "hostel fees?" → ask "application deadline?" → hang up | Meridian IVR + greeting; spoken answers $3,200 and Jan 15/Apr 30; post-call lead extracted; transcript + sentiment recorded; live-call SSE showed the call | 🖐️ |
| E2E-05 | **Outbound call journey** | Dashboard/API quick-call a test number → caller asks "scholarships?" → call completes | Meridian outbound greeting; spoken answer (3 scholarships); status callback updates lead + call_queue | 🖐️ |
| E2E-06 | **Cross-channel consistency** | Ask the same question ("What are the application deadlines?") via Streamlit, WhatsApp text, `/ws/voice/text`, and MCP tool | Same facts (Jan 15 / Apr 30 / Jun 10 / Aug 1) from every channel | 🧪/🖐️ |
| E2E-07 | **Full-session AI loop** | Complete E2E-04, then check: lead score, sentiment aggregate, dashboard summary, offer readiness for that lead | All downstream subsystems consume the call correctly (score, sentiment, KPIs) | 🖐️ |

---

# PART F — REGRESSION & DEGRADATION (unchanged from rev 2, still mandatory)

| ID | Test | Expected result | Mode |
|---|---|---|---|
| REG-01 | `pytest tests/` full suite | Green (no brand assertions to update — verified). | 🧪 |
| REG-03 | Dashboard render | KPIs, leads, Meridian courses, sentiment sections render. | 🖐️ |
| REG-04 | Sentiment pipeline | `POST /api/interactions/log` → scored + persisted. | 🧪 |
| REG-05 | Follow-up scheduler | Picks up follow-ups; no exceptions. | 🧪 |
| REG-06 | Lead scoring | `GET /api/leads/{id}/score` returns score + sentiment. | 🧪 |
| REG-07 | Demo reset | `POST /api/demo/reset` re-seeds Meridian data. | 🧪 |
| REG-08 | Health after warmup | Warmup logs + `/` reports `database: connected`. | 🧪 |
| REG-09 | MCP tools | `lookup_admissions_info` returns Meridian answers. | 🧪 |
| DEG-01 | Ollama down | Graceful error replies; no 500s; call stays up. | 🖐️ |
| DEG-02 | Postgres down | Boots "Database: not available"; chat still answers. | 🧪 |
| DEG-03 | Empty/missing Chroma | Logs "not found"; fallback prompt; no crash. | 🧪 |
| DEG-04 | No tunnel host | Offer PDF generated; WhatsApp media skipped w/ warning; email sent. | 🧪 |
| DEG-05 | Wrong-dimension store | Clear error; no crash loop. | 🧪 |

---

## G. Execution Order & Gates

1. **Phase 1 gate:** CFG-01..09, OFL-01..03, TMP-01/02, HYB-01..04, THR-01/04, PIPE-01..03, RBLD-01..10, REG-01/02 → all green → **then** proceed.
2. **Phase 2 gate:** BRD-* (all), PCAP-01..08, STT-01..04 → green; note: answers still old-store (expected; do not ship alone).
3. **Phase 3 gate (release):** SRC-*, SWAP-*, RAG-01..16, NEG-01..07, SEED-01..06, HIST-01..04, LEG-01, LCH-01..03, DOC-01/02, OFR-01..09 → all green → release.
4. **Phase 4 gate:** TUNE-01..06 → green per flag before flipping each in production.
5. **E2E gate:** E2E-01..07 pass manually before declaring the update complete.
6. **Release gate overall:** every gate above + BRD-01 grep clean + RBLD-10 rollback drill demonstrated once.

## H. Test ID Map (rev 2 → rev 3)

Retained IDs: RAG-01..16, NEG-01..07, RET-*→(HYB/TUNE), BRD-01..15, ST-01..05→(E2E-01), WA-01..11→(E2E-02/03 + PCAP), CALL-01..07→(E2E-04/05), OFR-01..09, REG-01..09, DEG-01..05, VAL-*→(RBLD-02/03), RBLD-01..10.
New IDs: CFG-01..09, OFL-01..03, TMP-01/02, HYB-01..05, THR-01..04, PIPE-01..03, PCAP-01..08, STT-01..04, SRC-01..06, SWAP-01..04, SEED-01..06, HIST-01..04, LEG-01, LCH-01..03, DOC-01/02, TUNE-01..06, E2E-01..07.

**Definition of done:** all phase gates green; E2E journeys complete; canary tests (NEG-03, OFR-06, BRD-01) clean; rollback drill demonstrated; nothing broken per REG-* and DEG-*.
