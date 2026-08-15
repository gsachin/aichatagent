# RAG Pipeline Update — Status: Done vs Pending (Handoff Document)

**Date:** 2026-08-14 (final status)
**Purpose:** Resume-point for the Meridian data replacement + RAG hardening work. Everything below is verified against the live system on branch **`meridianDataUpdate`**.
**Companion docs (same folder):** `Impact_Analysis_Meridian_Data_Replacement.md` · `RAG_Improvement_Recommendations.md` (rev 2) · `Implementation_Plan_Meridian_Update.md` · `TestCases_Meridian_Update.md` (rev 3)

---

## 1. One-Line Status

**Fully implemented, verified, and LIVE. The release is committed on branch `meridianDataUpdate` (`ac35d6f` + `447ddf0`); a follow-up `fix:` commit (2026-08-15) resolved the release-review blockers and updated this document's verification numbers.**

---

## 2. ✅ DONE — Implemented, Verified, Live

### 2.1 Code & data (all committed-to-disk, staged in git)
- RAG engine: Meridian markdown source, load-only store, validation gate, cosine space, section citations, env-configurable, overflow guard, hybrid retrieval (dense KNN + stdlib BM25 + RRF), threshold gate (code), temperature
- `scripts/rebuild_rag_index.py`; `pipeline.py` dedupe; conftest 90s deadline fix
- Rebrand: config, IVR, greetings, WhatsApp copy + program capture, STT dict, Streamlit, static pages
- Seeds: Meridian catalog (14 courses, KB-exact fees), leads, conversations; legacy bot retired; launchers fixed; docs updated
- Vector store: 31 clean Meridian chunks, cosine (was 5,346 polluted)

### 2.2 Verification evidence (all executed)
| Check | Result |
|---|---|
| pytest per-file (17 files) | **201 passed, 0 failed** (working tree, DB up) · fresh clone without `.env`: 199 passed + 2 env-skips (stats tests) |
| E2E RAG queries (direct + live WS `/ws/voice/text`) | MBA → $18,500 + citation · BCA → $10,200 · deadlines correct · canaries (Terrapin/UMD) → honest not-found |
| Store | 31 chunks · cosine · zero blocked markers · negative canary clean |
| Brand grep gate | clean |
| **Offer-letter E2E (live API)** | readiness gate (`missing: ['document']`) → doc upload → offer auto-generated → **PDF header "Meridian University", $18,500 fees, MBA, zero UMD/FDU** |
| **WhatsApp state machine (webhook simulation)** | Hi → name → email → "I want to take admission in B.Tech AI & Machine Learning" → **program captured as "B.Tech AI & Machine Learning"**, docs requested, lead row verified (status in_progress, source whatsapp) |
| Demo seed (Postgres) | 14 Meridian courses + 12 leads + 8 conversations; zero UMD/FDU in transcripts |
| Services | FastAPI (8000), Streamlit chatbot (8501), dashboard (8502) all running, HTTP 200 |
| Health | `/` reports `database: connected`, `twilio_configured: true`, `outbound_worker: active` |

### 2.3 Phase-4 flags (now LIVE in `.env`)
| Flag | Value | Rationale |
|---|---|---|
| `RAG_SEARCH_MODE` | **hybrid** | verified (dense+BM25+RRF) |
| `OLLAMA_TEMPERATURE` | **0.3** | cooler grounded QA |
| `RAG_SIMILARITY_THRESHOLD` | **OFF (0.0)** — deliberate | Measured cosine distances overlap between valid and off-topic questions (valid "hostel fee?" = 0.35 vs off-topic "weather?" = 0.32), so a global threshold would gate valid questions. Revisit with per-section/hybrid-aware gating only if hallucination appears. |

### 2.4 Cleanup
- `chroma_local_db.bak` (61 MB old UMD/FDU store) **deleted** after verification window.
- `content/sample_data/` old PDF archived in place (not ingested).

---

## 3. ⏸️ PENDING — only two items

### 3.1 Git commits — status
- Release: **`ac35d6f`** (Meridian pivot) + **`447ddf0`** (runtime-write sync) are committed on **`meridianDataUpdate`**.
- Review fixes: committed 2026-08-15 (`fix:` commit) — WhatsApp DB-less guard, test env-skips, deployment-doc rebrand completion, `.env.example` refresh, tunnel-script cache hardening, UMD sample PDF removed.
- Do NOT add: `.tunnel_*` cache files, `app/__pycache__/*.pyc`, `chroma_local_db.bak/` (now deleted anyway).
- **Store policy:** `chroma_local_db/` is tracked **deliberately** (release artifact — fresh clones work out of the box). Chroma writes bookkeeping rows at runtime; sync those writes with each release (see `447ddf0`) and rebuild via `scripts/rebuild_rag_index.py` whenever the knowledge base changes.
- Suggested message:

```
feat: Meridian data pivot — replace UMD/FDU knowledge source, rebrand app, harden RAG

- New clean Meridian knowledge base (content/meridian) from source PDF pages 1-7
- Vector store rebuilt: 31 clean chunks, cosine (was 5,346 polluted)
- app/rag.py: load-only store, validation gate, env-configurable, hybrid
  (dense+BM25+RRF), threshold, overflow guard, temperature, section citations
- scripts/rebuild_rag_index.py with smoke tests; pipeline.py dedupe
- Rebrand: config, IVR, greetings, WhatsApp, program capture, STT dict, UI
- Meridian course catalog + demo seeds; legacy bot retired; launchers updated
- tests/conftest.py startup deadline fix
- Docs: impact analysis, recommendations, implementation plan, test cases, status
```

### 3.2 Genuinely manual checks (can only be done with real Twilio/phone/browser)
Per `TestCases_Meridian_Update.md` Part E:
- E2E-02 real WhatsApp sandbox run (webhook simulation already passed — see §2.2)
- E2E-04/05 real inbound/outbound phone calls (greeting audio + spoken answers)
- OFR-10 email delivery with SMTP creds · OFR-11 WhatsApp media URL via tunnel host
- Browser spot-check of Streamlit (8501) and dashboard (8502) UI copy

### 3.3 Optional polish (non-blocking)
- Offer PDF signature "Dr. A. Chancellor" → "Dr. Helena Cross" (`app/offers/pdf.py:190`)
- `DEFAULT_PAYMENT_LINK` env → Meridian payment URL
- Combined `pytest tests/` still crashes on a Windows pandas/pyarrow heap-corruption during collection (pre-existing env issue) — per-file runs are green; investigate import order if a single-pass run is needed
- LangChain `Chroma` deprecation warning — cosmetic; future migration to `langchain-chroma`

---

## 4. Resume Checklist (final state)

1. **Commit** on `meridianDataUpdate` (§3.1) — everything already staged.
2. Optional manual E2E (§3.2) when Twilio/SMTP testing is wanted.
3. Optional polish (§3.3).

**Rollback (if ever needed after commit):** `git revert <commit>` restores code; rebuild the old store from archived sources if the old knowledge is ever required again.
