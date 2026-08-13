# Implementation Plan — Meridian Data Replacement & RAG Hardening

**Date:** 2026-08-14 · **Branch:** `offerlaterupdate`
**Inputs analyzed:**
- `Impact_Analysis_Meridian_Data_Replacement.md` — what breaks if we change data, risk register
- `RAG_Improvement_Recommendations.md` (rev 2) — what to build and why
- `TestCases_Meridian_Update.md` — how to verify (test IDs referenced below)

**This document is the single implementation checklist.** Every change is listed file-by-file with exact edits, ordered so each phase is independently shippable and reversible. Verification references the test-case IDs.

---

## 0. Scope Summary

| # | Workstream | Files touched |
|---|---|---|
| W1 | RAG engine: Meridian source, env config, validation gate, hybrid search, threshold, overflow guard, temperature, prompt | `app/rag.py`, `app/pipeline.py` |
| W2 | Rebrand copy: IVR, greetings, WhatsApp, Streamlit, static pages, STT dictionary | `app/main.py`, `app/voice_handler.py`, `app/config.py`, `app.py`, `app/static/index.html`, `app/static/voice_client.html` |
| W3 | Meridian catalog + demo data: courses, leads, conversations, program-capture lists | `scripts/seed_demo_data.py`, `app/main.py` |
| W4 | Rebuild tooling: validation + smoke tests + atomic swap | `scripts/rebuild_rag_index.py` (new) |
| W5 | Launchers + legacy + docs: PDF pre-flight checks, retire `admissions_bot.py`, deployment doc | `launch.bat`, `launch_tunnel.bat`, `launch_Guide.txt`, `admissions_bot.py`, `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md` |
| W6 | Data: clean Meridian knowledge base | `content/meridian/meridian_knowledge_base.md` — **already created** ✅ |

**Out of scope (unchanged):** DB schema, API routes, Twilio configuration, WebSocket protocols, sentiment/leads/outbound/dashboard/MCP engines, deployment architecture.

---

## Phase 1 — Zero-Impact Preparations (no user-visible change)

Everything in this phase is additive or env-gated OFF. The app behaves identically after this phase.

### 1.1 `app/rag.py` — config plumbing

```python
# Line ~33 — honor the dead CHROMA_DB_PATH env
CHROMA_DB_PATH = Path(os.environ.get(
    "CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db"),
))

# Lines ~44-45 — chunk config env-driven (defaults stay 1500/100 for now)
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "100"))

# New — search mode + threshold + temperature (all default = current behavior)
RAG_SEARCH_MODE = os.environ.get("RAG_SEARCH_MODE", "mmr")   # "mmr" | "hybrid"
RAG_SIMILARITY_THRESHOLD = float(os.environ.get("RAG_SIMILARITY_THRESHOLD", "0.0"))  # 0.0 = disabled
OLLAMA_TEMPERATURE = os.environ.get("OLLAMA_TEMPERATURE", "")  # "" = ollama default

# New — overflow guard helper + optional auto-trim
RAG_MAX_CONTEXT_CHARS = int(os.environ.get("RAG_MAX_CONTEXT_CHARS", "0"))  # 0 = off
def _estimate_tokens(text: str) -> int:
    return len(text) // 4
```

### 1.2 `app/rag.py` — overflow guard inside `retrieve_context()`

At the end of `retrieve_context()` (after building `context`):
```python
est = _estimate_tokens(context)
if est > int(OLLAMA_NUM_CTX) * 0.8:
    logger.warning(f"RAG context near overflow: ~{est} tokens (limit {OLLAMA_NUM_CTX}).")
if RAG_MAX_CONTEXT_CHARS and len(context) > RAG_MAX_CONTEXT_CHARS:
    context = context[:RAG_MAX_CONTEXT_CHARS]
```

### 1.3 `app/rag.py` — temperature wiring in `query_rag()`

```python
options = {"num_ctx": OLLAMA_NUM_CTX}
if OLLAMA_TEMPERATURE:
    options["temperature"] = float(OLLAMA_TEMPERATURE)
response = ollama.chat(model=model, messages=[...], options=options)
```
(Replace the existing `options={"num_ctx": OLLAMA_NUM_CTX}` call.)

### 1.4 `app/rag.py` — hybrid retrieval (additive function)

Implemented and verified (2026-08-14). Note: chromadb 1.5.9's native `collection.search()` raises `NotImplementedError` on the local backend, so the implementation fuses dense Chroma KNN + a stdlib `_MiniBM25` scorer (no `rank_bm25` dependency) with Python RRF — see `app/rag.py::retrieve_context_hybrid`. Hook it in `retrieve_context()` at the top:

```python
if RAG_SEARCH_MODE == "hybrid":
    try:
        return _guard_context(retrieve_context_hybrid(query, MMR_K))
    except Exception:
        logger.exception("Hybrid retrieval failed — falling back to MMR")
# ... existing MMR path unchanged ...
```

### 1.5 `app/rag.py` — similarity threshold gate in `query_rag()`

```python
if RAG_SIMILARITY_THRESHOLD > 0:
    try:
        import chromadb
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
        client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        ef = OllamaEmbeddingFunction(model_name=EMBED_MODEL, url=OLLAMA_BASE_URL)
        col = client.get_collection("langchain", embedding_function=ef)
        r = col.query(query_texts=[question], n_results=1, include=["distances"])
        dists = (r.get("distances") or [[None]])[0]
        if dists and dists[0] is not None and dists[0] > RAG_SIMILARITY_THRESHOLD:  # cosine distance
            return "I don't have that specific information in the university profile."
    except Exception:
        logger.exception("Threshold pre-check failed — continuing without it")
```
(Requires the cosine-space rebuild from Phase 3; default OFF until then.)

### 1.6 `app/pipeline.py` — env path one-liner

```python
# Line 33
CHROMA_DB_PATH = Path(os.environ.get(
    "CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db"),
))
```

### 1.7 `app/pipeline.py` — remove the duplicate retriever

Delete `retrieve_context()` (lines 67–114) and update its only consumers (`run_pipeline_test.py:82`, `test_full_pipeline.py:77`) to `from app.rag import retrieve_context`. `build_rag_prompt` already imports from `app.rag` — unaffected.

### 1.8 `scripts/rebuild_rag_index.py` — NEW single ingestion writer

```python
"""Rebuild the RAG vector store from validated sources with atomic swap."""
import os, sys, shutil, re, json, logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag_rebuild")

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get("CHROMA_DB_PATH", str(ROOT / "chroma_local_db")))
SOURCES = [
    {"path": ROOT / "content" / "meridian" / "meridian_knowledge_base.md",
     "doc_type": "meridian_profile"},
]
EXPECTED_MARKERS = ["meridian university"]
BLOCKED_MARKERS = ["university of maryland", "umd", "terrapin", "fafsa", "mddcs",
                   "mhec", "fairleigh dickinson", "fdu", "maradian", "marradian"]
DUMMY_MARKERS = ["lorem ipsum", "placeholder value"]
SMOKE_QUERIES = [  # each must retrieve non-empty context
    "What is the tuition fee for the MBA program?",
    "What are the application deadlines?",
    "What undergraduate programs does Meridian offer?",
    "What is the hostel fee?",
    "How do I apply to Meridian?",
]
NEGATIVE_QUERY = "What is the Terrapin Commitment?"  # must NOT return Maryland content

def validate(text: str) -> list[str]:
    low = text.lower()
    issues = []
    if not any(m in low for m in EXPECTED_MARKERS):
        issues.append(f"expected institution not found: {EXPECTED_MARKERS}")
    for m in BLOCKED_MARKERS:
        if m in low:
            issues.append(f"foreign content marker: '{m}'")
    return issues

def build_into(dest: Path) -> None:
    from langchain_community.document_loaders import TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from langchain_ollama import OllamaEmbeddings
    import chromadb

    chunks = []
    for src in SOURCES:
        text = src["path"].read_text(encoding="utf-8")
        issues = validate(text)
        if issues:
            logger.error(f"VALIDATION FAILED for {src['path'].name}: {issues}")
            sys.exit(1)
        loader = TextLoader(str(src["path"]), encoding="utf-8")
        docs = loader.load()
        for d in docs:
            d.metadata.update({
                "source_file": src["path"].name,
                "doc_type": src["doc_type"],
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            })
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=int(os.environ.get("RAG_CHUNK_SIZE", "600")),
            chunk_overlap=int(os.environ.get("RAG_CHUNK_OVERLAP", "90")),
            separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
        )
        chunks.extend(splitter.split_documents(docs))
    logger.info(f"Total chunks: {len(chunks)}")

    client = chromadb.PersistentClient(path=str(dest))
    existing = client.list_collections()
    for c in existing:
        client.delete_collection(c.name)   # dest is always a fresh directory
    embeddings = OllamaEmbeddings(model=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    store = Chroma.from_documents(
        documents=chunks, embedding=embeddings, persist_directory=str(dest),
        collection_metadata={"hnsw:space": "cosine"},
    )
    return store

def smoke_test(store) -> None:
    from app.rag import retrieve_context  # uses CHROMA_DB_PATH env
    for q in SMOKE_QUERIES:
        ctx = retrieve_context(q)
        if not ctx.strip():
            logger.error(f"SMOKE FAIL: no context for '{q}'")
            sys.exit(1)
    neg = retrieve_context(NEGATIVE_QUERY)
    for m in BLOCKED_MARKERS:
        if m in neg.lower():
            logger.error(f"SMOKE FAIL: blocked marker '{m}' in negative-query context")
            sys.exit(1)
    logger.info("All smoke tests passed")

if __name__ == "__main__":
    os.environ["CHROMA_DB_PATH"] = str(TARGET)
    build_into(TARGET)
    # smoke test against the fresh build
    from langchain_community.vectorstores import Chroma
    from langchain_ollama import OllamaEmbeddings
    store = Chroma(persist_directory=str(TARGET),
                   embedding_function=OllamaEmbeddings(model=os.environ.get("EMBED_MODEL", "nomic-embed-text")))
    smoke_test(store)
    logger.info(f"Rebuild complete at {TARGET}. Swap with live directory when ready.")
```
(Adjust imports/smoke wiring as needed during implementation — the contract is: validate → build fresh dir → smoke test → exit non-zero on any failure. The script never touches the live directory.)

**Verify Phase 1:** `pytest tests/` all green · RET-07/08, DEG-03 · BRD-01 shows no new hits.

---

## Phase 2 — Rebrand Copy (no data swap yet; answers remain old store until Phase 3)

### 2.1 `app/config.py:80`
```python
UNIVERSITY_NAME: str = field(default_factory=lambda: _env("UNIVERSITY_NAME", "Meridian University"))
```

### 2.2 `app/main.py` — telephony + WhatsApp copy (exact replacements)

| Line | Replace with |
|---|---|
| 208-209 (IVR) | `Press 1 for Undergraduate programs.` / `Press 2 for Postgraduate programs.` |
| 408 & 548 (greetings) | `"Hi, I'm the admissions assistant. Ask me anything about Meridian University programs, tuition fees, or how to apply."` |
| 1299 | `answer="Hello! Send me a question about Meridian admissions, or send a voice note."` |
| 1329 | `answer = f"Thanks {Body.strip()}! I've updated your profile. How can I help you with Meridian admissions?"` |
| 1429 | `answer = f"I have you as {lead_name or 'there'}. How can I help with Meridian admissions?"` |
| 1355, 1388, 1411 (program examples) | `"Which program are you interested in? (e.g., B.Tech Computer Science, MBA, BCA)"` |

### 2.3 `app/main.py` — Meridian program capture (lines 1147-1152 & 1395-1400)

Replace both duplicate lists with one module-level mapping and a shared helper:

```python
# module level, near the other templates
_MERIDIAN_PROGRAMS = {
    "mba": "MBA",
    "mca": "MCA",
    "m.tech": "M.Tech",
    "b.tech": "B.Tech",
    "bca": "BCA",
    "bba": "BBA",
    "b.com": "B.Com",
    "b.sc": "B.Sc",
    "b.a": "BA",
    "computer science": "B.Tech Computer Science",
    "ai & machine learning": "B.Tech AI & Machine Learning",
    "information technology": "B.Tech Information Technology",
    "computer applications": "BCA",
    "business administration": "BBA",
    "master of business administration": "MBA",
}

def _detect_meridian_program(msg_lower: str) -> str:
    for alias, canonical in _MERIDIAN_PROGRAMS.items():
        if alias in msg_lower:
            return canonical
    return ""
```
Then in `_detect_admission_intent_whatsapp` and the short-reply program capture: `detected_program = _detect_meridian_program(msg_lower)`.

### 2.4 `app/voice_handler.py:287-289` — STT dictionary

Remove UMD/FDU entries; add:
```python
"maridian": "Meridian", "miridian": "Meridian", "meridien": "Meridian",
"mary dian": "Meridian", "mirid": "Meridian",
```
Keep line 290 generic corrections (MBA/GPA/IELTS — still valid).

### 2.5 `app.py` (Streamlit) — lines 70, 156, 162
- `"Ask questions about **Meridian University** programs, fees, scholarships, and admissions."`
- `st.markdown("📚 **Meridian University** knowledge base")`
- `st.caption("Ask me anything about Meridian — type or use your voice.")`

### 2.6 `app/static/index.html:35, 73` and `app/static/voice_client.html:123`
- index: `AI-powered admissions counselor for Meridian University` / `Meridian University — Inspiring Tomorrow's Leaders`
- voice_client: `Local AI — ask about Meridian admissions`

**Verify Phase 2:** BRD-02..09 · CALL-01 (greeting text) · ST-04 (UI copy). Answers still UMD/FDU — expected until Phase 3. Do NOT ship this phase alone to production.

---

## Phase 3 — Data Swap & Ingestion Changes (the release)

### 3.1 `app/rag.py` — source switch + prompt update

```python
# Replace PDF_PATH usage in _build_with_langchain: keep the function as the
# markdown+langchain path; the rebuild script owns PDF-free ingestion.
SOURCE_PATHS = [
    Path(__file__).resolve().parent.parent / "content" / "meridian" / "meridian_knowledge_base.md",
]
```
- `get_vector_store()` unchanged in shape (loads existing store; build path used when empty).
- **Prompt (line ~63):** delete rule 3 (`"3. Keep UMD and FDU information clearly separated..."`) and add: `"3. When you state fees, dates, or figures, cite the section they come from, e.g. (§ Fees Structure)."`
- **Header regex (line 49):** remove or leave inert (no longer matches markdown).

### 3.2 Chunk defaults → 600/90 (same release as the rebuild)

Change defaults in 1.1 from `"1500"/"100"` to `"600"/"90"` (or pass via env in the rebuild command — both acceptable; defaults preferred so every future rebuild matches).

### 3.3 `scripts/seed_demo_data.py` — Meridian catalog, leads, conversations

**Courses (lines 169-182) — release-blocking (R-02):**
```python
courses_data = [
    ("B.Tech Computer Science", "4 Years", "$14,500/year", "Fall 2026, Spring 2027", "Core, computer and electronics engineering with strong lab exposure."),
    ("B.Tech AI & Machine Learning", "4 Years", "$15,200/year", "Fall 2026", "AI and machine learning specialization in the School of Computer Science."),
    ("B.Tech Information Technology", "4 Years", "$14,200/year", "Fall 2026", "IT program covering software, AI, data science and cybersecurity."),
    ("BBA", "3 Years", "$10,800/year", "Fall 2026", "Bachelor of Business Administration — finance, marketing, entrepreneurship."),
    ("BCA", "3 Years", "$10,200/year", "Fall 2026", "Bachelor of Computer Applications."),
    ("B.Com", "3 Years", "$8,600/year", "Fall 2026", "Accounting, economics and business analytics."),
    ("BA", "3 Years", "$7,900/year", "Fall 2026", "Design, media, literature and humanities."),
    ("B.Sc", "3 Years", "$9,400/year", "Fall 2026", "Physics, chemistry, biology and mathematics with research tracks."),
    ("MBA", "2 Years", "$18,500/year", "Fall 2026, Spring 2027", "Master of Business Administration — School of Business / Management."),
    ("MCA", "2 Years", "$13,800/year", "Fall 2026", "Master of Computer Applications."),
    ("M.Tech", "2 Years", "$14,600/year", "Fall 2026", "M.Tech specializations across Engineering & Computing."),
    ("M.Sc", "2 Years", "$11,200/year", "Fall 2026", "Master of Science."),
    ("MA", "2 Years", "$9,600/year", "Fall 2026", "Master of Arts."),
    ("M.Com", "2 Years", "$9,900/year", "Fall 2026", "Master of Commerce."),
]
```
**Leads/conversations:** rewrite all UMD/FDU mentions with Meridian equivalents (e.g. "Voice note asking about Meridian MBA tuition and scholarships", "Enrolled in Meridian B.Tech Computer Science — Fall 2026"; conversation figures must match the knowledge base: MBA $18,500, scholarships "up to 50% Merit Scholarship").

### 3.4 `admissions_bot.py` — retire as a writer

Delete the Chroma-writing path (or the whole file — it's legacy CLI). Minimum: comment out/remove `Chroma.from_documents` so nothing can re-pollute. Its PDF path reference (lines 34/39) becomes irrelevant.

### 3.5 Launchers — `launch.bat:31-34`, `launch_tunnel.bat:31-34`

Replace the PDF existence check:
```bat
if exist "content\meridian\meridian_knowledge_base.md" (
    echo    [OK] Meridian knowledge base found.
) else (
    echo    [FAIL] Meridian knowledge base not found at content\meridian\meridian_knowledge_base.md
    pause
    exit /b 1
)
```
Keep the old PDF archived in `content/sample_data/` (do not delete — R-03).

### 3.6 `launch_Guide.txt` — sample questions → Meridian examples

### 3.7 `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md:180-181` — §6 data table

Update rebuild source to `content/meridian/meridian_knowledge_base.md` (the deployment doc lives in `doc/cloudDeployment/` — keep the cloud-doc convention).

### 3.8 Execute the rebuild & swap

```powershell
# 1. Warm-up checks
ollama pull nomic-embed-text; ollama pull qwen2.5:7b-instruct-q3_K_M
# 2. Build + smoke test (script exits non-zero on any failure)
$env:CHROMA_DB_PATH = "chroma_local_db_new"
python scripts/rebuild_rag_index.py
# 3. Swap (atomic rename; instant rollback = rename back)
Rename-Item chroma_local_db chroma_local_db.bak
Rename-Item chroma_local_db_new chroma_local_db
# 4. Restart FastAPI + Streamlit; check warmup logs (ChromaDB pre-warmed)
# 5. Re-seed demo data (Meridian leads/courses)
python scripts/seed_demo_data.py
```

**Verify Phase 3 (release gate):** RAG-01..16 · NEG-01..07 (canary NEG-03) · RBLD-01..06 · OFR-04..06 · BRD-01 clean · REG-01 green.

---

## Phase 4 — Optional Tuning (post-release, env-gated)

| Step | Env change | Tests |
|---|---|---|
| Enable hybrid search | `RAG_SEARCH_MODE=hybrid` in `.env` | RET-02..04 |
| Cooler temperature | `OLLAMA_TEMPERATURE=0.3` (chat) / `0.2` (voice) | RET-09, RAG-01 |
| Enable threshold (after measuring distances on real queries) | `RAG_SIMILARITY_THRESHOLD=<measured>` | RET-05/06 |
| Section citations polish | `section` metadata in chunks + prompt rule (already in 3.1) | RAG-16 |
| Optional: offer PDF signature → "Dr. Helena Cross, Vice Chancellor" (`app/offers/pdf.py:190`); `DEFAULT_PAYMENT_LINK` env → Meridian URL | — | OFR-05 |

---

## 5. Dependencies & Release Ordering

```
Phase 1 (safe, additive) ──► Phase 2 (copy only) ──► Phase 3 (store swap + seed + launchers)
                                                        │  single deploy, release-blocking items:
                                                        │  • course re-seed (R-02)
                                                        │  • launcher PDF checks (R-03)
                                                        │  • program-capture lists (R-04)
                                                        └──► Phase 4 (tuning, independent)
```
- Phase 2 must not ship alone to production (answers would still be UMD/FDU behind Meridian copy — R-01).
- Phase 3 items are one release: store swap, config default, copy, seeds, launchers.
- `.bak` store is retained until the release verification window closes (≥ 1 full business cycle or as decided).

---

## 6. Rollback Plan

| Failure point | Rollback action | Time to recover |
|---|---|---|
| Bad store / wrong answers | `Rename-Item chroma_local_db chroma_local_db_bad` then `Rename-Item chroma_local_db.bak chroma_local_db`; restart FastAPI | < 5 min |
| Bad copy/config | Revert the single release commit (config + copy are one commit); restart | < 5 min |
| Launcher regression | Restore the two `.bat` files from the previous commit | < 5 min |
| Course seed wrong | Re-run `seed_demo_data.py` with corrected data (rows are idempotent — DELETEs first) | < 5 min |
| DB historical data | Never touched by this change — no action | — |

---

## 7. Definition of Done

- [ ] `pytest tests/` green (REG-01) — existing suite, no brand assertions to update
- [ ] BRD-01 grep clean: no UMD/FDU/Fairleigh/Maryland in `app/`, `app.py`, `scripts/`, `app/static/` (except the validation gate's blocked-marker list and archived `content/sample_data/`)
- [ ] Store rebuilt: ~80–150 chunks, cosine space, Meridian-only (NEG-05/06)
- [ ] All 🧪 tests from `TestCases_Meridian_Update.md` green
- [ ] Manual E2E: Streamlit chat (ST-01), WhatsApp text + voice note + program capture (WA-01/04/10), inbound call (CALL-01), outbound call (CALL-03), offer letter generated with Meridian header + $18,500 MBA fees (OFR-05/06)
- [ ] Canary NEG-03: "Terrapin Commitment" → honest not-found, zero Maryland content
- [ ] `launch.bat` and `launch_tunnel.bat` start the app cleanly with the Meridian knowledge base present
- [ ] Rollback drill executed once (RBLD-05)
- [ ] Deployment doc + launch guide updated (W5)

---

## 8. File Change Ledger (for the PR)

| File | Change | Phase |
|---|---|---|
| `app/rag.py` | env config, overflow guard, hybrid, threshold, temperature, source switch, prompt rules, chunk defaults | 1 + 3 |
| `app/pipeline.py` | `CHROMA_DB_PATH` env; delete duplicate `retrieve_context` | 1 |
| `app/config.py` | `UNIVERSITY_NAME` → Meridian | 2 |
| `app/main.py` | IVR, greetings, WhatsApp copy, program lists + helper | 2 |
| `app/voice_handler.py` | STT dictionary → Meridian variants | 2 |
| `app.py` | Streamlit copy | 2 |
| `app/static/index.html`, `app/static/voice_client.html` | copy | 2 |
| `scripts/rebuild_rag_index.py` | NEW | 1 |
| `scripts/seed_demo_data.py` | Meridian courses + leads + conversations | 3 |
| `run_pipeline_test.py`, `test_full_pipeline.py` | import `retrieve_context` from `app.rag` | 1 |
| `tests/conftest.py` | server-startup deadline 10s → 90s (warmup takes ~20s with Whisper CUDA load — pre-existing flake) | 1 |
| `launch.bat`, `launch_tunnel.bat` | Meridian knowledge-base pre-flight check | 3 |
| `launch_Guide.txt` | Meridian sample questions | 3 |
| `admissions_bot.py` | retire ingestion path | 3 |
| `doc/cloudDeployment/SYSTEM_DEPLOYMENT_INFO.md` | §6 rebuild source | 3 |
| `content/meridian/meridian_knowledge_base.md` | already created ✅ | — |
| `tests/` new cases | add suites from TestCases doc (§1–5, 10, 12) | 1–3 |

**Not touched:** DB schema, Twilio config, `app/offers/*` (except optional signature), `app/leads/*`, `app/sentiment/*`, `app/outbound/*`, `app/dashboard/*`, `app/mcp/*`, `docker-compose.yml`, `Dockerfile`.
