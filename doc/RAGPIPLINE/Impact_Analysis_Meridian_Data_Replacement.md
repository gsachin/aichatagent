# Impact Analysis — Dropping UMD & FDU, Replacing with Meridian University Data

**Date:** 2026-08-14 · **Branch:** `offerlaterupdate`
**Author role:** AI RAG Architect — analysis based on a full-codebase sweep (runtime code, static assets, scripts, launchers, tests, docs, live databases)
**Companion documents:** `RAG_Improvement_Recommendations.md` (rev 2) · `TestCases_Meridian_Update.md`

---

## 1. Executive Summary

Replacing the RAG knowledge source (UMD & FDU profile → Meridian University) is **not** a data-only change. The two universities are hardwired into **12 runtime files (22 references)**, **2 launcher scripts that will fail if the old PDF is removed**, **1 demo-seed script whose course catalog drives offer-letter fees**, and **~20 documentation files**. The offer-letter, lead, sentiment, outbound-call, MCP, and dashboard engines themselves are institution-agnostic (verified) and keep working — but they consume branded inputs (course catalog, program-capture lists, config default) that must move to Meridian in the same release.

**Overall risk rating: MEDIUM — manageable with an atomic, sequenced release.**
Highest risks: (R-01) half-migration mismatch (copy updated but store not rebuilt, or vice versa), (R-02) wrong tuition on offer letters from the stale UMD-flavored course seed, (R-03) launcher failure when the old PDF is removed.

---

## 2. Definition of the Change

| Item | From | To |
|---|---|---|
| RAG source | `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` | `content/meridian/meridian_knowledge_base.md` (prepared, clean, pages 1–7 of `UNIVERSITY OF MIRIDAN.pdf`) |
| Vector store | `chroma_local_db/` — 5,346 UMD/FDU chunks (polluted) | Clean rebuild ~80–150 Meridian chunks (atomic swap, `.bak` kept) |
| Institution identity | "University of Maryland / Fairleigh Dickinson University" | "Meridian University" |
| Program universe | MBA/CS/Data Science/Engineering/Business Analytics/Information Systems | Meridian catalog: B.Tech CS / AI&ML / IT, BBA, BCA, B.Com, BA, B.Sc, MBA, MCA, M.Tech, M.Sc, MA, M.Com + PhD areas |
| Caller-facing menu | "Press 1 for UMD programs / Press 2 for FDU programs" | "Press 1 for Undergraduate / Press 2 for Postgraduate" |

**Out of scope:** DB schema, API routes, Twilio configuration, WebSocket protocols, sentiment scoring, outbound calling mechanics, dashboard logic, deployment architecture.

---

## 3. Complete Component Inventory (verified file-by-file)

Legend — **Severity:** 🔴 Critical (breaks a flow) · 🟠 High (wrong output visible to users) · 🟡 Medium (confusing/cosmetic) · 🟢 Low (no user impact)

### 3.1 RAG & Knowledge Layer

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 1 | `app/rag.py:34` — `PDF_PATH` | Hardcoded UMD/FDU PDF | Bot keeps answering UMD/FDU regardless of any other change | Replace with source list pointing at `content/meridian/meridian_knowledge_base.md` | 🔴 |
| 2 | `chroma_local_db/` | 5,346 UMD/FDU chunks, multi-generation duplicates | Every answer continues from polluted UMD/FDU store | Atomic rebuild-swap (§4.1 recommendations); keep `.bak` | 🔴 |
| 3 | `app/rag.py:49` — header-strip regex | Matches "UMD & FDU — University Profile Report" | Dead pattern (harmless) after pivot | Remove/neutralize | 🟢 |
| 4 | `app/rag.py:63` — prompt rule "Keep UMD and FDU information clearly separated" | Active rule | Harmless with Meridian-only context, but instructs the LLM about institutions that no longer exist in the corpus | Delete rule; add Meridian citation rule | 🟡 |
| 5 | `content/sample_data/` (PDF + DOCX) | Old source | None if left archived; **launchers break if deleted** (see 3.7) | Archive in place; keep out of ingestion path | 🟢 |
| 6 | `content/meridian/meridian_knowledge_base.md` | Prepared 2026-08-14 (clean, tables, FAQ) | — | Use as single source of truth | ✅ (new) |
| 7 | Whisper tokenizer vocab (`models/…/vocabulary.txt`) | Contains both "Maryland" and "Meridian" | None — model vocabulary, not app logic | No action | 🟢 |

### 3.2 Configuration

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 8 | `app/config.py:80` — `UNIVERSITY_NAME` default | "University of Maryland / Fairleigh Dickinson University" | **Offer-letter PDF header, email body, and footer keep the old university name** | Default → "Meridian University" | 🔴 |

### 3.3 Voice / Telephony Layer

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 9 | `app/main.py:208-209` — IVR menu | "Press 1 for UMD programs / Press 2 for FDU programs" | Callers hear the wrong institutions, then a Meridian bot | → Undergraduate / Postgraduate. **Note:** DTMF digits are functionally ignored by the WS handler (`app/main.py:466-468` logs and discards) — both options connect to the same AI stream today, so this change is copy-only, zero functional risk | 🟠 |
| 10 | `app/main.py:408, 548` — inbound/outbound AI greetings | "Ask me anything about UMD or FDU programs…" | Every call opens with the wrong brand | → Meridian greeting | 🟠 |
| 11 | `app/voice_handler.py:287-289` — STT mishearing dictionary | Maps mis-hearings to "UMD"/"FDU" (e.g. `"empty"→"UMD"`) | **Active corruption risk:** a caller saying "Meridian" mis-transcribed as "empty" gets corrected to "UMD" — the bot would then look up UMD in a Meridian store and answer not-found | Replace with Meridian-sounding variants (`"maridian"`, `"miridian"` → "Meridian") | 🟠 |
| 12 | `app/voice_handler.py:290` — generic corrections ("emma"→"MBA", "gp a"→"GPA", "i elts"→"IELTS") | Valid for Meridian too | None | Keep unchanged | 🟢 |

### 3.4 WhatsApp Layer

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 13 | `app/main.py:1299, 1329, 1429` — WhatsApp copy | "UMD or FDU admissions" in 3 replies | Every new WhatsApp conversation shows the wrong brand | → Meridian | 🟠 |
| 14 | `app/main.py:1147-1148, 1395-1396` — program-capture lists | `["mba", "computer science", "data science", "engineering", "business analytics", "information systems"]` | **Meridian program names are not recognized** — "I want B.Tech AI & Machine Learning" falls through to "I didn't catch the program name"; `program_interest` stays empty and the offer flow cannot start | Replace with Meridian program names (incl. "b.tech", "mca", "m.tech", "bca", "b.sc" …) | 🔴 |
| 15 | `app/main.py:1355, 1388, 1411` — "Which program?" examples | "(e.g., Computer Science, MBA, Data Science)" | Misleading examples; MBA still valid | → Meridian examples | 🟡 |

### 3.5 Offer-Letter Pipeline (verified institution-agnostic)

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 16 | `app/offers/pdf.py:79` — PDF header | Reads `settings.UNIVERSITY_NAME` | **Automatically becomes Meridian once #8 is done** — no code change needed | None beyond #8 | ✅ |
| 17 | `scripts/seed_demo_data.py:169-182` — course catalog | UMD-flavored: MBA $45,000, CS $42,000, Engineering $32k/$48k… | **Wrong tuition printed on offer PDFs, WhatsApp "Amount:", and payment sections.** Also new offers for Meridian program names won't match any course row (falls back to raw name — works but loses fees/duration/intake) | Re-seed with Meridian catalog + knowledge-base fees (MBA $18,500, B.Tech AI & ML $15,200, …) | 🔴 |
| 18 | Existing leads' `program_interest` ("Computer Science", "Data Science", …) | Historical rows in Postgres | After re-seed these names stop matching course rows → offers fall back to raw-name mode (no crash, but PDF loses duration/fees) | Accept raw-name fallback, or add a name-normalization map (e.g. "Computer Science"→"B.Tech Computer Science") | 🟡 |
| 19 | `app/offers/pdf.py:190` — signature "Dr. A. Chancellor, Dean of Admissions" | Generic | Cosmetic mismatch with Meridian leadership (Dr. Helena Cross) | Optional: update signature | 🟢 |
| 20 | Offer rows / PDFs already generated for UMD/FDU | Historical data in `data/offers/` + DB | None — historical documents remain correct for what they are | Keep as-is (do not regenerate) | 🟢 |

### 3.6 UI / Dashboard / MCP

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 21 | `app.py:70, 156, 162` — Streamlit copy | "UMD & FDU" headings/captions | Main UI shows the wrong brand | → Meridian | 🟠 |
| 22 | `app/static/index.html:35, 73` — landing page | UMD & FDU copy | Wrong brand on landing page | → Meridian | 🟡 |
| 23 | `app/static/voice_client.html:123` — subtitle | "ask about UMD or FDU admissions" | Wrong brand on voice page | → Meridian | 🟡 |
| 24 | Dashboard placeholders — `courses_page.py:20`, `leads_page.py:39`, `mcp_tools.py:67`, `dashboard.html:96` | "Computer Science"/"MBA" example placeholders | Cosmetic; MBA is a Meridian program too | Optional refresh | 🟢 |
| 25 | MCP tools (`app/mcp/tools.py`, `app/leads/mcp_tools.py`) | Delegate to `app.rag.query_rag` — no brand logic | **None — automatically serve Meridian answers after the store swap** | None | ✅ |
| 26 | Dashboard logic, sentiment scorer/categorizer, leads service, follow-up scheduler, outbound worker | No university references found (full grep) | **None — domain-neutral** | None | ✅ |

### 3.7 Launchers & Ops Scripts (hidden landmine)

| # | Component (file:line) | Current state | Impact if not changed | Action | Severity |
|---|---|---|---|---|---|
| 27 | `launch.bat:31-34`, `launch_tunnel.bat:31-34` | Pre-flight check `if exist "content\sample_data\UMD_and_FDU_University_Profile_Report.pdf"` and **aborts with "[FAIL] PDF not found"** | **Local dev launchers hard-fail if the old PDF is moved/deleted** — "drop UMD and FDU" literally breaks startup until these are updated | Change check to the Meridian source (or remove the check); keep old PDF archived until scripts updated | 🔴 |
| 28 | `launch_Guide.txt:5-8` | Sample questions reference UMD/FDU | Misleading quick-start guide | Update sample questions | 🟢 |
| 29 | `start_services.ps1`, `check_and_tunnel.ps1`, `start.sh`, `setup.sh` | No UMD/FDU/PDF references found | None | None | ✅ |

### 3.8 Tests & Verification

| # | Component | Finding | Impact | Action | Severity |
|---|---|---|---|---|---|
| 30 | `tests/` (task1–task9, phase3 RAG, phase5 Twilio, phase6 DB) | **Zero UMD/FDU/Meridian assertions** (verified grep) — tests are transport/config/schema-focused | Regression risk is **low**; phase3 only asserts store existence | Add the new test suite from `TestCases_Meridian_Update.md`; keep existing suite green | 🟢 |

### 3.9 Documentation & Legacy Code

| # | Component | Impact | Action | Severity |
|---|---|---|---|---|
| 31 | ~20 `doc/*.md` historical docs (architect_analysis, ARCHITECTURE_DETAIL, application_flow, PROJECT_REFERENCE, ENHANCEMENT_ROADMAP, COMMAND_COCKPIT_DASHBOARD, UX_ENHANCEMENT_ROADMAP, VOICE_QUALITY_IMPROVEMENT_PLAN, INFRASTRUCTURE_PLAN, PENDING_IMPROVEMENTS, manual_testing_guide, WHATSAPP_INTEGRATION_GUIDE, startCallAndChat, project-overview) | Historical record; some contain stale procedures (e.g. PENDING_IMPROVEMENTS "Compare UMD and FDU" issues become obsolete) | Leave as history; add a short "Superseded by Meridian pivot 2026-08-14" note at the top of the operational ones (launch_Guide, WHATSAPP guide, deployment doc) | 🟢 |
| 32 | `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md:180-181` | Cloud rebuild instructions point at the UMD/FDU PDF | Update §6 to the Meridian source (`content/meridian/meridian_knowledge_base.md`) | 🟡 |
| 33 | `admissions_bot.py` (legacy CLI) | A second, conflicting ingestion writer (800/150 chunks) pointed at the old PDF | **Retire**: stop it from writing to the store (delete or neutralize); it re-pollutes otherwise | 🟠 |

### 3.10 Persistent Data Stores (behavioral impact)

| # | Store | Current content | Impact of the change | Decision |
|---|---|---|---|---|
| 34 | Postgres — `leads`, `conversations`, `call_queue`, `follow_ups`, `offer_letters`, `lead_calls`, sentiment tables | Historical UMD/FDU conversations and leads | **None mechanically** — all schema/APIs unchanged. Old transcripts remain visible in the dashboard (historical record, correct). Sentiment aggregates unaffected. | Keep historical rows; re-seed demo data only via `POST /api/demo/reset` after updating the seed script |
| 35 | `chroma_local_db/` | UMD/FDU vectors | Replaced via atomic swap; `.bak` retained for rollback | Swap; delete `.bak` only after a full release verification window |
| 36 | `data/documents/`, `data/offers/` | Uploaded student docs + generated offer PDFs | Untouched; old offers remain valid historical documents | Keep |

---

## 4. Functional Impact by Use Case (what changes, what stays)

| Use case | After the change | Regression risk |
|---|---|---|
| **Streamlit chat** | Answers Meridian facts with cited sections; UI copy says Meridian. Profile form (name/email/phone/program) unchanged. | Low |
| **WhatsApp text** | Same state machine (name → email → program → intent → documents → offer); program capture now recognizes Meridian names; replies say Meridian. | Medium (program list — test WA-04) |
| **WhatsApp voice note** | Whisper → `query_rag` → Meridian answer + Kokoro TTS MP3. Unchanged mechanics. | Low |
| **Inbound calls** | IVR says Undergraduate/Postgraduate; greeting says Meridian; AI stream answers from Meridian store; post-call lead extraction + sentiment unchanged. STT dictionary now corrects toward "Meridian". | Medium (greeting + STT dict) |
| **Outbound calls** | Same dialing, queueing, retry logic; greeting Meridian; status callbacks unchanged. | Low |
| **Offer-letter generation** | Flow identical: readiness gate requires name + email + phone + program + ≥1 document. PDF header auto-switches to Meridian via `UNIVERSITY_NAME`. Fees/duration come from the **re-seeded Meridian course catalog** (critical dependency #17). ACCEPT/DECLINE flow unchanged. | **Medium-High until course seed lands** (OFR-06 is the canary test) |
| **MCP tools** | `query_rag`-based tools serve Meridian answers automatically. | None |
| **Dashboard / leads / sentiment / follow-ups** | Unchanged mechanics; demo data re-seeded with Meridian content. | None |
| **Cloud deployment** | Unchanged architecture; deployment doc's rebuild section updated to the Meridian source. | None |

---

## 5. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-01 | **Half-migration mismatch** — copy updated but store not rebuilt (bot advertises Meridian, answers UMD/FDU) or store rebuilt but copy stale (IVR says UMD, bot answers Meridian) | High (without sequencing) | 🔴 Critical — inconsistent user experience, brand confusion | Single atomic release: rebuild store first, verify with smoke tests, then flip config+copy in the same deploy (§7 checklist). Grep gate BRD-01 in CI. |
| R-02 | **Wrong tuition on offer letters** — course catalog still UMD-flavored ($45k MBA) | High if seed forgotten | 🔴 Critical — legally sensitive wrong figures in official documents | Course re-seed is a **release-blocking dependency**; canary test OFR-06 asserts $18,500 for MBA offers |
| R-03 | **Launcher failure** — `launch.bat` / `launch_tunnel.bat` abort when the old PDF is absent | Certain if the PDF is deleted before scripts update | 🟠 High for local dev | Update both checks in the same change as the data swap, or keep the old PDF archived in place |
| R-04 | **Program capture misses Meridian names** — WhatsApp cannot capture "B.Tech AI & ML" | Certain without #14 | 🟠 High — blocks the offer flow for the most specific programs | Update both program lists; test WA-04 |
| R-05 | **STT dictionary corrupts "Meridian"** — mis-transcribed "empty"-like audio corrected to "UMD" | Low-medium | 🟠 High per occurrence — wrong lookup + wrong answer | Replace UMD/FDU entries with Meridian variants in the same release (#11) |
| R-06 | **Existing leads lose course matching** — old program names ("Computer Science") stop matching new course rows | Certain for historical rows | 🟡 Medium — offers fall back to raw-name mode (works, loses fees/duration on PDF) | Accept fallback or add a name-normalization map; do not delete historical rows |
| R-07 | **Rollback complexity** — reverting after release | Low | 🟡 Medium | `.bak` store rename-back + config/copy revert is a single-commit revert; document in release notes |
| R-08 | **Transition-window hallucination** — old prompt rules + new store briefly coexist | Low | 🟢 Low — rule 3 ("separate UMD and FDU") is inert when the store has no UMD/FDU context | Remove rule in the same release |
| R-09 | **Doc drift** — deployment/quick-start docs still say UMD/FDU | Certain if skipped | 🟢 Low — operational confusion only | Update the operational docs (launch_Guide, deployment doc §6, WHATSAPP guide) |
| R-10 | **Legacy writer re-pollutes** — `admissions_bot.py` (or the notebook) writes old-PDF chunks into the new store | Low (only if someone runs it) | 🟠 High — re-introduces UMD/FDU chunks into the Meridian store | Retire the legacy writer; validation gate + blocked markers make re-pollution impossible even if run |
| R-11 | **Meridian data quality** — source labels figures as dummy/sample (v1.0) | Certain (known) | 🟡 Medium — answers are internally consistent but figures are demo data | Keep the provenance note in the knowledge base; replace figures when real institutional data is provided (drop-in: edit the md, re-run rebuild) |

---

## 6. Non-Functional Impact

| Dimension | Impact | Direction |
|---|---|---|
| Retrieval latency | 5,346 → ~100 vectors; HNSW search drops from ~10–50 ms to <5 ms | ✅ Improves |
| Answer quality | Clean single-generation chunks + Meridian FAQ pairs + tables + optional hybrid BM25 | ✅ Improves |
| Token budget | 600-char chunks × k=5 ≈ 900 tokens vs previous worst-case ~2,125/2,048 overflow | ✅ Removes overflow risk |
| Disk | `chroma_local_db` shrinks (~46 MB → few MB); `.bak` kept temporarily (~46 MB); new md ~15 KB | ✅ Improves |
| Memory/GPU | No new models, no new dependencies | ➡️ Unchanged |
| DB schema / migrations | None | ➡️ Unchanged |
| API contracts | None changed | ➡️ Unchanged |
| Twilio config | Webhook URLs unchanged; only the TwiML copy served changes | ➡️ Unchanged |
| Cloud deployment | Same architecture; source-file reference in deployment doc updated | ➡️ Unchanged |

---

## 7. Atomic Release Sequence (failure = rollback)

1. **Prepare (no user impact):** add `CHROMA_DB_PATH` env support, validation gate, rebuild script; run full pytest + new 🧪 suite.
2. **Build new store** into `chroma_local_db_new` from the Meridian md; smoke tests + canary NEG-03 (no Terrapin/Maryland content); **do not swap yet.**
3. **Release window (single deploy):**
   a. Swap store directories (`chroma_local_db` → `.bak`, `_new` → live).
   b. Flip `UNIVERSITY_NAME` default → Meridian (config.py).
   c. Update copy: main.py (IVR, greetings, WhatsApp), voice_handler STT dict, app.py, static HTML.
   d. Update program-capture lists + course seed + demo seeds.
   e. Update `launch.bat` / `launch_tunnel.bat` PDF checks.
   f. Restart FastAPI + Streamlit; verify warmup + health check.
4. **Verify:** run the 🧪 suite end-to-end; manual checks ST-01, WA-01/04, CALL-01, OFR-06; grep gate BRD-01 clean.
5. **Rollback (any failure):** rename `.bak` back; revert the release commit (config+copy are one commit); restart. Historical DB rows and old offers are never touched.

---

## 8. Conclusion

The Meridian data replacement touches **12 runtime files, 2 launcher scripts, 1 seed script, and 3 operational docs** — but every *engine* (offer letters, leads, sentiment, outbound calling, MCP, dashboard) is verified institution-agnostic and survives unchanged. The change is low-risk **if and only if** it ships as one atomic release with the course-catalog re-seed and launcher fixes included; the two silent killers are the UMD-flavored course fees on offer letters (R-02) and the launcher PDF check (R-03). With the validation gate, blocked-marker list, atomic store swap, and the test suite from `TestCases_Meridian_Update.md`, the system also becomes permanently immune to the contamination that produced this situation.

**Recommendation: proceed with the change**, following the sequencing in §7 and the rollout plan in `RAG_Improvement_Recommendations.md` §7. Full test matrix: `TestCases_Meridian_Update.md`.
