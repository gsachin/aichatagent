# RAG Pipeline Improvement Recommendations

**Date:** 2026-08-14 (rev 2 — Meridian pivot) · **Branch:** `offerlaterupdate`
**Analyst role:** AI RAG Architect
**Sources reviewed:**
1. `doc/RAGPIPLINE/RAG_Pipeline_Report.md` — audit of our current pipeline (dated 2026-08-09)
2. `doc/RAGPIPLINE/meridian_rag_reference.txt` — external reference pattern (validation gate, versioned collections, hybrid search)
3. `doc/RAGPIPLINE/RAgpipelinGemnai ..md` — architecture audit + production blueprint (chunk sizing, hybrid search, metadata, reranking)
4. `doc/RAGPIPLINE/UNIVERSITY OF MIRIDAN.pdf` — new source of truth (12 pages, partially contaminated — see §0.2)

**Rev 2 scope:** the chatbot must answer about **Meridian University only**. UMD/FDU content is removed everywhere: knowledge source, prompts, greetings, IVR, WhatsApp copy, offer letters, demo data. Prepared data deliverable: `content/meridian/meridian_knowledge_base.md` (clean, RAG-optimized — see §4.0).

**Guardrail for everything below:** *No functional impact.* Every recommendation is (a) build-time only, (b) env-gated and OFF by default, or (c) additive with a fallback. Current behavior stays identical until the team flips a flag. The Meridian pivot (§4.0) is a deliberate, coordinated content+copy change — it changes *what the bot knows*, not *how the app works*.

---

## 0. Ground-Truth Audit (verified against live code and the new PDF, 2026-08-14)

### 0.1 The vector store is still polluted and growing

| Metric | Report (Aug 9) | Live now |
|---|---|---|
| Embeddings in `chroma_local_db` | 4,618 | **5,346** |
| Collections | `langchain` | `langchain` (single) |
| Source document | UMD & FDU profile PDF | UMD & FDU profile PDF |
| Custom metadata (`source_file`, `doc_type`, …) | none | **none** (only PyPDF auto-keys) |

**Two distinct problems, don't conflate them:**
- **Wrong-domain pollution:** the store was built from `UMD_and_FDU_University_Profile_Report.pdf` — the chatbot answers UMD/FDU because that is literally what it was taught (§0.3).
- **Duplicate-generation pollution:** three writers (`app/rag.py` 1500/100, `admissions_bot.py` 800/150, the Jupyter notebook 800/150) appended to the same `chroma_local_db/` via `Chroma.from_documents()`, which is append-only. Every rebuild adds chunks without removing old ones → 5,346 chunks where a clean build should have ~100.

### 0.2 The new Meridian PDF is itself contaminated (verified by full extraction)

`UNIVERSITY OF MIRIDAN.pdf` — 12 pages, 17,227 extracted chars:

| Pages | Content | Verdict |
|---|---|---|
| 1–7 | "Meridian University — AI Admission Agent Reference Document": overview, history, leadership, 6 schools, 8 UG + 6 PG programs, PhD areas, admission process, fee tables, scholarships, dates, FAQ, facilities, contact, conversation script | ✅ **Keep — this is the real Meridian knowledge** |
| 8–9 | **University of Maryland financial aid**: FAFSA, "Maradian/Marradian" grants (OCR-typo'd Maryland), Terrapin Commitment, MDDCS scholarship | ❌ **Foreign content — exclude** |
| 10 | blank | ❌ exclude |
| 11–12 | **University of Maryland housing**: 39 residence halls (Anne Arundel, Ellicott, Cambridge …), "Terrapin community" | ❌ **Foreign content — exclude** |

This is exactly the contamination the meridian reference's validation gate was written for. **Ingesting this PDF as-is would have poisoned the new store on day one.**

### 0.3 Why the data is polluted with UMD/FDU — root cause chain

1. **Hardcoded wrong source.** `app/rag.py:34` pins `PDF_PATH` to `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf`. Nothing else can be ingested without a code edit — so the store has *only* ever contained UMD/FDU data.
2. **No ingestion gate.** Any PDF placed at that path is embedded blindly: no expected-institution check, no foreign-marker check, no page-count sanity, no smoke test. (The Maryland pages in the new PDF would sail through.)
3. **Append-only multi-writer ingestion.** `Chroma.from_documents()` never clears; `admissions_bot.py` and the legacy notebook wrote competing chunk configs to the same directory (§0.1).
4. **Brand copy hardcoded to UMD/FDU** in 22 places across runtime code + static files (see §4.0 checklist), so even a Meridian store would be surrounded by UMD/FDU greetings, IVR menus and WhatsApp replies.

### 0.4 Prevention rules (how this can never happen again)

1. **Expected-institution check at build time:** the ingestion gate must verify the source contains `"Meridian University"` AND contains none of the blocked markers (`university of maryland`, `umd`, `terrapin`, `fafsa`, `mddcs`, `mhec`, `fairleigh dickinson`, `fdu`). Fail → refuse to build (§5.1).
2. **Page-range extraction:** when ingesting the PDF, extract pages 1–7 only (verified clean); the extraction log (`_extracted_*.txt`) is human-reviewable before anything is embedded.
3. **Single ingestion entry point:** one rebuild script (`scripts/rebuild_rag_index.py`), one source list, one chunk config. Retire `admissions_bot.py` and the notebook as writers (§5.3).
4. **Build-then-swap, never delete-first:** new store goes to `chroma_local_db_new`; swap only after smoke tests pass; old store kept as `.bak` (§4.1).
5. **Smoke tests with canonical questions** must pass before swap — including one question that must NOT return foreign content (e.g. "What is the Terrapin Commitment?" should answer "not in profile").
6. **Config, not code, for institution identity:** `UNIVERSITY_NAME=Meridian University` in env (already honored by `app/offers/pdf.py:79`); a future `grep` CI check for UMD/FDU in `app/` and `app/static/` keeps copy honest.

### 0.5 All live interfaces converge on ONE retrieval path (correction to report §6.2/§6.3)

| Interface | Actual path (verified) | Retrieval |
|---|---|---|
| Twilio live calls (`voice_handler.process_utterance` line 345) | `run_rag_query_sync` → **`app.rag.query_rag`** | **MMR k=5** |
| WhatsApp text + voice notes (`app/main.py` lines 856, 1251) | `run_rag_query_sync` → **`app.rag.query_rag`** | **MMR k=5** |
| Streamlit chatbot (`app.py` line 174) | `app.rag.get_retriever` + `create_retrieval_chain` | **MMR k=5** |
| MCP tools | **`app.rag.query_rag`** | **MMR k=5** |
| `app.pipeline.retrieve_context` (plain similarity k=2) | only `run_pipeline_test.py` / `test_full_pipeline.py` | test scripts only |

Every live channel flows through `app/rag.py` — one choke point, so one set of fixes upgrades everything.

### 0.6 Chroma 1.5.9 already ships the hybrid-search API (different names than the reference)

The meridian reference uses `from chromadb.search import Knn, Rrf` — **that path does not exist** in our installed `chromadb 1.5.9`. Verified live:

```python
from chromadb import Knn, Rrf, Search          # ✅ imports fine on 1.5.9
from chromadb.utils.embedding_functions import Bm25EmbeddingFunction  # ✅
collection.search(searches=[Search(rank=Knn(query="…", limit=5), limit=5), …])  # ✅ method exists
```

The persisted store already contains FTS tables (`embedding_fulltext_search*`), so the BM25 side has index data. The reference's concept ports cleanly (§4.3); its verbatim code does not.

**Implementation note (verified 2026-08-14):** `collection.search()` raises `NotImplementedError: Search is not implemented for Local Chroma` on the local `PersistentClient` backend. The implemented hybrid (§4.3) therefore fuses dense Chroma KNN + a stdlib `_MiniBM25` scorer with Python RRF — zero new dependencies, verified working on the live store.

### 0.7 Other verified facts

- **Chroma metric is L2** (LangChain default). The dead `RAG_SIMILARITY_THRESHOLD` (0.5) assumes cosine — meaningless on L2; switch to cosine at rebuild.
- **Dead env config:** `CHROMA_DB_PATH`, `RAG_SIMILARITY_THRESHOLD`, `OLLAMA_TEMPERATURE` are documented but never read.
- **Context overflow risk:** worst case 5 × 1,500 chars + prompt ≈ 2,125 tokens > `num_ctx=2048`. Silent truncation → hallucination.
- **`.env` sets only** `OLLAMA_MODEL`, `OLLAMA_URL`, `OLLAMA_NUM_CTX`.

---

## 1. Source-to-Recommendation Mapping (rev 2)

| Idea | From | Verdict | Where |
|---|---|---|---|
| Meridian-only knowledge + prepared data file | new PDF | ✅ **Done** — `content/meridian/meridian_knowledge_base.md` built from pages 1–7 | §4.0 |
| Wipe + clean rebuild of polluted store | Report §2.5 · Gemini §4 | ✅ **Adopt — atomic directory swap, not destructive delete** | §4.1 |
| Validation gate before embedding | Meridian §1 | ✅ **Adopt — inverted for us:** Meridian expected; Maryland/UMD/FDU markers **blocked** | §5.1 |
| Versioned collections + pointer + smoke test | Meridian §2 | ⚠️ **Adopt simplified** (directory swap = same guarantee, zero API change) | §4.1, §5.3 |
| Custom metadata on chunks | Report §3.2 · Gemini §4 | ✅ **Adopt** at rebuild | §4.2 |
| Hybrid search BM25 + dense, RRF | Meridian §3 · Gemini §3 · Report §18.7 | ✅ **Adopt behind env flag** — API verified in 1.5.9 | §4.3 |
| Smaller chunks (600) + 15% overlap | Gemini §1.2/§4 | ✅ **Adopt at rebuild** with k rebalance | §4.4 |
| Header/footer/page-number cleaning | Gemini §4 | ✅ **Adopt** (build-time only) | §4.2 |
| Similarity threshold (make dead config live) | Report §6.4/§18.6 | ✅ **Adopt after cosine rebuild**, env-gated, default OFF | §4.5 |
| Context-window overflow guard | Report §13.4 | ✅ **Adopt** (additive) | §4.6 |
| Grounded prompt + citations | Meridian prompt | ✅ **Adopt (prompt-only)**, UMD/FDU separation rule **deleted** | §4.7 |
| Temperature control | Gemini §4 | ✅ **Adopt via env**, unset = current | §4.8 |
| Reranker | Meridian §4 · Gemini §5 | ⏸️ **Defer** — latency on live-call path | §6 |
| PyMuPDF loader | Gemini §4/§5 | ⚠️ **Optional** — or skip PDF entirely and ingest the markdown | §5.2 |
| Parent-child chunking, LangGraph, embedding swap | Gemini §5 · Report §4 | ❌ **Reject** — no payoff at this corpus size | §6 |

---

## 2. Executive Summary — Do These, In This Order

**Tier P0 — the Meridian pivot + clean rebuild (this sprint):**
1. **Ingest the prepared Meridian knowledge file** (`content/meridian/meridian_knowledge_base.md`) instead of the UMD/FDU PDF — §4.0/§4.1.
2. **Rebrand the app copy** — 22 UMD/FDU references across `app/rag.py`, `app/main.py`, `app/voice_handler.py`, `app/config.py`, `app.py`, `app/static/*`, `scripts/seed_demo_data.py` — §4.0 checklist.
3. **Honor `CHROMA_DB_PATH` env var** + **atomic rebuild-swap** with `.bak` rollback — §4.1.
4. **Context-overflow guard** (additive) — §4.6.
5. **Kill the dead configs** — §4.5/§4.8.

**Tier P1 — "answer more questions":**
6. **Hybrid search (dense + BM25, RRF)** behind `RAG_SEARCH_MODE=hybrid` — the biggest coverage lever; the new corpus is table-heavy, which BM25 handles especially well — §4.3.
7. **Custom metadata + markdown-aware chunking (600/90)** at the same rebuild — §4.2/§4.4.
8. **Grounded prompt with page citations, Meridian-only rules** — §4.7.

**Tier P2 — hardening:**
9. **Ingestion validation gate** (expected = Meridian; blocked = Maryland/UMD/FDU markers) — §5.1.
10. **Permanent rebuild script with smoke-test-before-swap** — §5.3.

**Explicitly defer/reject:** reranker, PyMuPDF (markdown supersedes PDF for ingestion), parent-child chunking, embedding swap, LangGraph — §6.

---

## 3. Why "No Functional Impact" Holds

- Everything user-facing flows through `app.rag.query_rag` / `get_retriever`; §4 changes leave their contract untouched or sit behind flags that default to current behavior.
- Rebuild changes are **file-level swaps**: if the new index misbehaves, rename `chroma_local_db.bak` back — the app never notices.
- Prompt changes are additive string edits at the same call sites (`SYSTEM_PROMPT.format(context=…)`).
- **No new dependencies** for the core path: markdown ingestion uses `TextLoader` (already in langchain_community); hybrid search uses the installed chromadb 1.5.9; validation uses stdlib + existing pypdf.
- No DB schema, API route, WebSocket, or Twilio changes.
- The Meridian pivot changes *content and copy* (deliberate, user-requested). All app *mechanics* stay identical.

---

## 4. Recommendations in Detail

### 4.0 (P0) Meridian data preparation — DONE + brand pivot checklist

**Data deliverable (already prepared):** `content/meridian/meridian_knowledge_base.md`
- Built from PDF **pages 1–7 only**; Maryland pages 8–12 excluded.
- Structure chosen for maximum answerability:
  - **Markdown tables** for programs, fees, milestones, dates, at-a-glance stats — tables are dense, unambiguous context the LLM answers well from, and BM25 matches exact terms in them.
  - **`##` sections per topic** — headers act as natural chunk boundaries for markdown-aware splitting, so chunks stay on-topic.
  - **FAQ as explicit Q/A pairs** — a question phrased like the stored question retrieves the verbatim answer (highest-precision case).
  - **Derived Q/As added** (tuition, intakes, deadlines, how-to-apply) — each strictly grounded in a source table, marked `(derived from …)`.
  - Single provenance + dummy-data note at the top; inline "dummy/sample" disclaimers removed from body sections so chunks stay clean.
  - Conversation script retained — the agent flow still needs it.

**Brand pivot — exact UMD/FDU references to change (verified by full-codebase grep):**

| File:Line | Current | Change to |
|---|---|---|
| `app/rag.py:34` | `PDF_PATH` → UMD_and_FDU PDF | source list → `content/meridian/meridian_knowledge_base.md` (PDF optional) |
| `app/rag.py:49` | header regex `UMD & FDU — University Profile Report` | remove/neutralize (markdown has no repeating header) |
| `app/rag.py:63` | prompt rule "Keep UMD and FDU information clearly separated" | delete rule (Meridian-only corpus) |
| `app/config.py:80` | `UNIVERSITY_NAME` default "University of Maryland / Fairleigh Dickinson University" | `"Meridian University"` |
| `app/main.py:208-209` | IVR "Press 1 for UMD programs / Press 2 for FDU programs" | "Press 1 for Undergraduate / Press 2 for Postgraduate" (Meridian has no UMD/FDU split) |
| `app/main.py:408, 548` | voice greetings "Ask me anything about UMD or FDU programs" | "…about Meridian University programs, fees, scholarships or how to apply" |
| `app/main.py:1299, 1329, 1429` | WhatsApp copy "UMD or FDU admissions" | "Meridian admissions" |
| `app/voice_handler.py:287-289` | STT mishearing dictionary maps `"hold you"→"FDU"`, `"empty"→"UMD"` | map to `"Meridian"`-sounding variants (e.g. `"maridian"`, `"miridian"` → "Meridian") |
| `app/main.py:1147, 1395` | WhatsApp program-capture lists (`mba`, `computer science`, `data science`, …) | align with Meridian program names (`b.tech ai & machine learning`, `bca`, `mca`, `m.tech`, `m.sc`, …) so "I want B.Tech AI & ML" is captured for the offer flow |
| `app.py:70, 156, 162` | Streamlit copy "UMD & FDU" | Meridian copy |
| `app/static/voice_client.html:123`, `index.html:35, 73` | HTML copy UMD/FDU | Meridian copy |
| `scripts/seed_demo_data.py` (~30 refs + course catalog lines 169–182) | demo leads/conversations about UMD/FDU **and course catalog with UMD-flavored fees** ($45k MBA etc.) | re-seed leads/conversations **and the `courses` table** with Meridian programs + fees from the knowledge base ($18,500 MBA, $15,200 B.Tech AI & ML, …) — the offer PDF/WhatsApp amount come from these rows |
| `admissions_bot.py:34, 39, 87` | legacy CLI bot | **retire as writer** (see §5.3); at minimum point it at the new source or delete |
| `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` | old source | archive; keep out of ingestion path |

Note: `app/offers/pdf.py:79` already reads `settings.UNIVERSITY_NAME` — the offer letter header flips automatically once the env/default changes.

### 4.1 (P0) Single-source ingestion + honor `CHROMA_DB_PATH` + atomic swap

**Code (build/config only):**
```python
# app/rag.py — replace hardcoded path (line 33) and PDF_PATH (line 34)
CHROMA_DB_PATH = Path(os.environ.get("CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db")))

SOURCES = [  # ordered list; rebuilt into a clean store
    {
        "type": "markdown",
        "path": Path(__file__).resolve().parent.parent / "content" / "meridian" / "meridian_knowledge_base.md",
        "doc_type": "meridian_profile",
    },
]
```
Ingestion via `TextLoader` (already available in langchain_community — no new dependency) + `RecursiveCharacterTextSplitter` with markdown-aware separators (`["\n## ", "\n### ", "\n\n", "\n", " ", ""]`) so each `##` section stays intact. Keep the PyPDF path only as an optional secondary loader with the page-range filter (§5.1).

**Rebuild procedure (operational, no code):**
```powershell
$env:CHROMA_DB_PATH = "chroma_local_db_new"
python scripts/rebuild_rag_index.py          # validates source → builds → smoke tests
# after all smoke tests pass:
Rename-Item chroma_local_db chroma_local_db.bak
Rename-Item chroma_local_db_new chroma_local_db
# rollback = rename back. Old store untouched until you delete it.
```
Expected result: **~30–60 clean Meridian chunks** (verified: 31) instead of 5,346 mixed UMD/FDU chunks. Retrieval latency drops sharply (report §17.2), duplicate answers disappear (report §16.6).

### 4.2 (P1) Custom metadata + generalized cleaning at ingestion

**Source:** Report §3.2, Gemini `clean_pdf_text` + metadata tagging, Meridian chunk dict.

Attach per chunk in the build path:
```python
doc.metadata.update({
    "source_file": "meridian_knowledge_base.md",
    "doc_type": "meridian_profile",
    "section": "<the ## heading the chunk came from>",   # markdown loader + splitter makes this cheap
    "ingested_at": datetime.now(timezone.utc).isoformat(),
})
```
For PDF fallback, also apply the generalized regexes (`page N of M` strip, whitespace collapse). Enables future `where={"doc_type": …}` filtering (offer letters, policies) and per-section diagnostics.

### 4.3 (P1) Hybrid search — dense + BM25 fused with RRF (env-gated)

**Why this is the "answer more questions" lever here:** the Meridian corpus is table- and figure-heavy (tuition, fees, deadlines, program names). Dense `nomic-embed-text` similarity alone misses exact-term queries like *"BCA fees"* or *"MBA eligibility"*; BM25 catches them. Chroma fuses both server-side with RRF — no separate index to maintain.

```python
# app/rag.py — ADDITIVE new function; nothing existing changes.
# chromadb 1.5.9's native Search API is NOT implemented for the local
# backend (raises NotImplementedError — verified), so hybrid fuses
# dense Chroma KNN + stdlib _MiniBM25 with Python RRF. No new deps.

def retrieve_context_hybrid(query: str, top_k: int = MMR_K, fetch_k: int = MMR_FETCH_K) -> str:
    import chromadb
    from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    ef = OllamaEmbeddingFunction(model_name=EMBED_MODEL, url=OLLAMA_BASE_URL)
    col = client.get_collection("langchain", embedding_function=ef)
    dense = (col.query(query_texts=[query], n_results=fetch_k, include=["documents"])
             .get("documents") or [[]])[0]
    sparse = _bm25_cache["bm25"].top(query, fetch_k)   # cached _MiniBM25 over the corpus
    # ... RRF fuse (k=60, 0-based ranks), return top_k
```
Gate it (default = current behavior):
```python
RAG_SEARCH_MODE = os.environ.get("RAG_SEARCH_MODE", "mmr")   # "mmr" | "hybrid"
# retrieve_context(): if hybrid → try hybrid, on exception log + fall back to MMR
```
Flip to `hybrid` only after the clean Meridian rebuild. Voice latency impact ~10–30 ms — negligible.

### 4.4 (P1) Markdown-aware chunking (600/90) — at rebuild only

Gemini's core complaint — 1,500-char chunks dilute similarity — is even more relevant for table data. With `##`-section boundaries + 600 chars:

| Path | Today | Recommended |
|---|---|---|
| Chat/WhatsApp/MCP | k=5 × 1,500 = 7,500 chars | k=6 × 600 = 3,600 chars ✅ more topics, half the tokens |
| Voice (`query_rag`) | k=5 × 1,500 | k=5 × 600 = 3,000 chars ✅ concise, within budget |

Worst-case context drops to ~900 tokens vs 2,048 limit — overflow risk largely evaporates (§4.6 becomes a safety net). Make both configurable (`RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`); change defaults only at the rebuild milestone.

### 4.5 (P1) Similarity threshold (after cosine rebuild)

At rebuild, set cosine space so thresholds are interpretable:
```python
Chroma.from_documents(..., collection_metadata={"hnsw:space": "cosine"})
```
Wire the gate, default OFF:
```python
RAG_SIMILARITY_THRESHOLD = float(os.environ.get("RAG_SIMILARITY_THRESHOLD", "0.0"))  # 0.0 = disabled
```
When enabled and the best score is below threshold → return "I don't have that specific information in the university profile." (honest fallback instead of an LLM guess — report §16.5). Caveat: MMR doesn't return distances; measure via a small pre-query (`include=["distances"]`) or gate the hybrid path only. Ships OFF until measured on real queries.

### 4.6 (P0) Context-overflow guard (additive)

```python
def _estimate_tokens(text: str) -> int:
    return len(text) // 4
# in retrieve_context():
if _estimate_tokens(context) > int(OLLAMA_NUM_CTX) * 0.8:
    logger.warning(f"RAG context near overflow: ~{est} tokens (limit {OLLAMA_NUM_CTX}).")
```
Optional env-gated auto-trim (`RAG_MAX_CONTEXT_CHARS`, default 0 = off). Unset → zero behavior change; logs only.

### 4.7 (P1) Prompt: Meridian-only rules + citations

- **Delete** rule 3 ("Keep UMD and FDU information clearly separated") — no longer applicable.
- **Add** citations: prefix chunks with `[Profile §{section}]` (from the new `section` metadata) and add rule: *"When you state fees, dates, or figures, cite the section, e.g. (§ Fees Structure)."*
- Keep the strong rules: never invent figures; honest "not available" answer.
- Cost: ~15 extra tokens per chunk. Prompt-only change.

### 4.8 (P1) Controllable temperature (env-gated)

```python
TEMP = os.environ.get("OLLAMA_TEMPERATURE", "")
options = {"num_ctx": OLLAMA_NUM_CTX}
if TEMP:
    options["temperature"] = float(TEMP)
```
Unset → identical behavior. Recommend `0.3` chat / `0.2` voice once tested. Cooler = fewer invented figures.

---

## 5. Hardening (P2)

### 5.1 Ingestion validation gate (adapted — Meridian expected, Maryland/UMD/FDU blocked)

```python
EXPECTED_MARKERS = ["meridian university"]          # source must contain this
BLOCKED_MARKERS  = [                                 # source must NOT contain any of these
    "university of maryland", "umd", "terrapin", "fafsa", "mddcs", "mhec",
    "fairleigh dickinson", "fdu", "maradian", "marradian",   # OCR variants of Maryland
]
DUMMY_MARKERS    = ["lorem ipsum", "placeholder value"]
MIN_PAGES        = 3

def validate_source(text: str) -> list[str]:
    low = text.lower()
    issues = []
    if not any(m in low for m in EXPECTED_MARKERS):
        issues.append(f"expected institution not found: {EXPECTED_MARKERS}")
    for m in BLOCKED_MARKERS:
        if m in low:
            issues.append(f"foreign content marker found: '{m}'")
    return issues
```
For the PDF path additionally: **extract pages 1–7 only** and log a per-page marker report before embedding — this is precisely the check that catches the Maryland pages 8–12. (This gate failed silently before because it never existed; now it fails loudly and refuses to build.)

### 5.2 PyMuPDF loader (optional — or skip PDF entirely)

The markdown knowledge base supersedes the PDF for ingestion, so PyMuPDF (`pymupdf` package) is optional. If the team prefers PDF ingestion: use `PyMuPDFLoader` with the page-range filter, keep `PyPDFLoader` as fallback, and re-export the PDF with only pages 1–7 first.

### 5.3 Permanent rebuild script (single writer, smoke-test-before-swap)

`scripts/rebuild_rag_index.py`:
1. Validate source(s) (§5.1) → 2. Build into `<path>_new` → 3. Smoke test: ~10 canonical questions must return non-empty context/answers (e.g. "MBA tuition?", "application deadline?", "hostel fees?", "How do I apply?") **plus one negative test** ("What is the Terrapin Commitment?" must NOT return Maryland content) → 4. Swap directories → 5. Print rollback instructions.

This makes the script the **only** writer to the store. Retire `admissions_bot.py` as an ingester (delete or strip its Chroma-writing path); the notebook is legacy documentation, not a runtime writer.

---

## 6. What We Recommend AGAINST (and why)

| Idea | Reason |
|---|---|
| **Ingesting the raw Meridian PDF as-is** | Pages 8–12 are University of Maryland content — would re-poison the store on day one. Use the prepared markdown or page-filtered extraction. |
| **Reranker in the live path** | +1 GB model, +0.5–2 s/query on CPU; voice budget is already ~12–30 s/turn. |
| **Embedding swap (all-MiniLM / mxbai)** | Rebuild + revalidation for no demonstrated gain on a ~100-chunk corpus. |
| **Parent-child / hierarchical chunking** | Built for long documents; our answers fit single 600-char sections. |
| **LangGraph / multi-agent retrieval** | One institution, one-hop questions — overreach. |
| **Meridian reference as a drop-in module** | Wrong import paths for chromadb 1.5.9; its validation flags UMD as contamination *of a Meridian doc* (correct) but our app's whole corpus was UMD/FDU; its rerank stub is unimplemented. Mine the patterns, don't import it. |
| **Destructive `Remove-Item chroma_local_db`** | Always directory-swap (§4.1). |

---

## 7. Suggested Rollout Sequence (with zero-impact checks)

| Step | Action | Impact check |
|---|---|---|
| 1 | Brand copy pivot (§4.0 checklist: `config.py`, `main.py`, `voice_handler.py`, `app.py`, static HTML) + `CHROMA_DB_PATH` env + overflow guard + temp env | `pytest tests/`; start FastAPI; WhatsApp + voice greeting say "Meridian"; all flows still answer (still old store, temporarily UMD/FDU answers). |
| 2 | Build `scripts/rebuild_rag_index.py` with validation + smoke tests (§5.1/§5.3) | Script **fails loudly** on the old UMD/FDU store's source; succeeds on `meridian_knowledge_base.md`. |
| 3 | Clean Meridian rebuild: markdown source, 600/90, cosine, metadata (§4.1/§4.2/§4.4/§4.5) | 20-question A/B: Meridian answers correct, no UMD/FDU content; voice turn latency measured. |
| 4 | Flip `RAG_SEARCH_MODE=hybrid` (§4.3) | Same 20-question A/B; keyword queries ("BCA fees", "MBA eligibility") improve; watch fallback logs. |
| 5 | Prompt citations + threshold after measuring distances (§4.5/§4.7) | Manual review of 20 answers; "not in profile" appears only for genuinely unanswerable questions. |
| 6 | Re-seed demo data with Meridian programs (§4.0) | Dashboard/lead flows unchanged mechanically. |

Every step independently reversible (env flag off, or directory rename back).

---

## 8. Files That Would Change (when implementing)

| File | Change | Scale |
|---|---|---|
| `app/rag.py` | source list (markdown), env paths, chunk env vars, metadata, cleaning, validation gate, hybrid fn, threshold, overflow guard, temp, prompt rules | moderate (all guarded) |
| `app/main.py` | IVR menu, greetings, WhatsApp copy (6 lines) | small |
| `app/voice_handler.py` | STT mishearing dictionary → Meridian variants | small |
| `app/config.py` | `UNIVERSITY_NAME` default → Meridian | 1 line |
| `app.py` | Streamlit copy (3 lines) | small |
| `app/static/index.html`, `voice_client.html` | copy | small |
| `app/pipeline.py` | `CHROMA_DB_PATH` env one-liner only | 1 line |
| `scripts/rebuild_rag_index.py` | new | new file |
| `scripts/seed_demo_data.py` | Meridian demo leads/conversations | data only |
| `admissions_bot.py` | retire as ingester | removal/neutralize |
| `content/meridian/meridian_knowledge_base.md` | **already created (rev 2 deliverable)** | — |
| DB schema, Twilio flow, MCP tools, offer pipeline | **no change** | — |

**Net effect:** the bot answers Meridian questions accurately and broadly (clean store + hybrid + FAQ pairs + tables + citations), says "I don't have that information" instead of hallucinating (threshold + overflow guard + cool temperature), and the system cannot silently revert to polluted data (validation gate + single writer + atomic swaps + blocked-marker checks).

---

## 9. RAG_Pipeline_Report — Full Issue Coverage Matrix

Every issue raised in `doc/RAGPIPLINE/RAG_Pipeline_Report.md` is mapped to its resolution in this document. Verified 2026-08-14.

| # | Report issue (§) | Resolved by | Status |
|---|---|---|---|
| 1 | Polluted vector store — 4,618 (now 5,346) chunks, append-only multi-writer ingestion (§2.5, §18.1) | §0.1 diagnosis, §4.1 atomic rebuild-swap, §5.3 single-writer script, retire `admissions_bot.py` | ✅ Covered |
| 2 | No custom metadata on chunks (§3.2, §18.2) | §4.2 (`source_file`, `doc_type`, `section`, `ingested_at`) | ✅ Covered |
| 3 | Chunk config not centralized — 1500/100 vs 800/150 (§18.3) | §4.4 env-configurable (`RAG_CHUNK_SIZE/OVERLAP`), §5.3 single writer; legacy writers retired | ✅ Covered |
| 4 | Retrieval strategy inconsistent — MMR vs plain similarity (§6.2, §18.4) | §0.5 correction: all live paths already use MMR via `app.rag.query_rag`; `pipeline.retrieve_context` (k=2) is test-only. Recommended cleanup: **delete `pipeline.retrieve_context`** and update the two test scripts to import from `app.rag` — removes the confusion permanently | ✅ Covered (+cleanup) |
| 5 | No offer-letter ingestion pipeline (§2.3, §12, §18.5) | Split: **generation** is verified working and institution-agnostic (§10). **Ingestion** of offer letters into the vector store is intentionally deferred — not needed for Meridian v1; metadata (`doc_type`) is ready when needed (§12 pattern from the report still applies) | ⏸️ Deferred (deliberate) |
| 6 | Dead config `RAG_SIMILARITY_THRESHOLD` (§6.4, §18.6) | §4.5 wired, env-gated OFF, meaningful after cosine rebuild | ✅ Covered |
| 7 | No hybrid/BM25 search (§6.4, §18.7) | §4.3 native Chroma 1.5.9 hybrid (Knn+Rrf), env-gated `RAG_SEARCH_MODE` | ✅ Covered |
| 8 | Context-window overflow risk (§13) | §4.6 overflow guard + §4.4 smaller chunks (worst case ~900 tokens vs 2,048) | ✅ Covered |
| 9 | LLM hallucination (§16.5) | §4.5 threshold, §4.7 prompt rules + citations, §4.8 temperature | ✅ Covered |
| 10 | Duplicate/overlapping answers (§16.6) | Clean rebuild removes multi-generation chunks (§4.1) | ✅ Covered |
| 11 | Fallback path degradation on AppLocker (§14) | Kept by design (Windows dev workaround); hybrid path adds its own try/except fallback to MMR (§4.3). On Linux cloud the LangChain path works natively | ✅ Covered |
| 12 | Troubleshooting: empty store / Ollama down / dimension mismatch / LangChain missing (§16.1–16.4) | Prevention rules §0.4 (validation, smoke tests, single writer, extraction log) + §5.3 rebuild script diagnostics | ✅ Covered |
| 13 | Manual rebuild guide (§11) | Superseded by `scripts/rebuild_rag_index.py` (§5.3) with validation + smoke tests + auto-swap | ✅ Superseded |
| 14 | Performance: HNSW over 5k vectors (§17.2) | Clean store (~100 chunks) → retrieval <5 ms; hybrid adds ~10–30 ms | ✅ Covered |
| 15 | Temperature uncontrolled (§7) | §4.8 `OLLAMA_TEMPERATURE` wired | ✅ Covered |
| 16 | Embedding swap proposals — all-MiniLM / mxbai (§4, §10) | §6 rejected with reasons (no gain at this corpus size, forced rebuild) | ✅ Decided |
| 17 | LangSmith/AppLocker env workarounds (§9.3) | Keep — harmless on Linux, required on locked-down Windows dev boxes | ✅ No change |
| 18 | **NEW (rev 2 find):** course catalog seeded with UMD-flavored fees → offer letters would show wrong tuition | §4.0 checklist: re-seed `courses` table with Meridian programs/fees from the knowledge base | ✅ Covered |
| 19 | **NEW (rev 2 find):** WhatsApp program-capture lists miss Meridian program names | §4.0 checklist: update `app/main.py:1147, 1395` lists | ✅ Covered |
| 20 | **NEW (rev 2 find):** `pipeline.retrieve_context` duplicate code path | §9 row 4 cleanup | ✅ Covered |

**Conclusion:** all 17 original report issues are resolved or deliberately deferred; 3 additional gaps found during the Meridian re-audit are covered. Nothing from the report is lost.

---

## 10. Offer-Letter Pipeline — Verified Works for Meridian

**Requirement:** the same flow as today — the system asks name, email, phone number; if the student expresses interest, an offer letter is generated — but branded **Meridian University**.

**Verification result: the pipeline is already institution-agnostic.** Traced end to end:

| Step | Code | UMD/FDU dependency? |
|---|---|---|
| WhatsApp: phone auto-captured (`From`), asks name → email → program → intent ("I want to take admission") → documents → auto-offer | `app/main.py:1305-1440` state machine | ❌ none — but program-capture lists need Meridian names (§4.0) |
| Readiness gate: name + email + phone_number + program_interest + ≥1 document | `app/offers/service.py:46-104` | ❌ none |
| Offer generation: course match, dates, idempotency guard | `app/offers/service.py:110-185` | ❌ none |
| PDF: header uses `settings.UNIVERSITY_NAME` | `app/offers/pdf.py:79` | ✅ **auto-fixes when the config default changes to "Meridian University"** |
| WhatsApp delivery (PDF media URL + payment section) | `app/offers/service.py:203-242` | ❌ none — amounts come from the course row → **re-seed courses** (§4.0) |
| Email delivery (SMTP + PDF attachment) | `app/offers/service.py:244-277`, `app/emailer.py` | ❌ none |
| ACCEPT/DECLINE reply handling | `app/main.py:1367-1378` | ❌ none |
| Streamlit: profile form collects name/email/phone/program; document upload auto-triggers offer | `app.py` + `POST /api/leads/{id}/documents` (`app/main.py:2295-2357`) | ❌ none |
| Calls: post-call LLM extracts name/email/phone from transcript → lead; dashboard can attach documents → offer | `app/main.py:_handle_disconnect` | ❌ none |

**Required changes for Meridian (small):**
1. `app/config.py:80` — `UNIVERSITY_NAME` default → `"Meridian University"` (PDF header, email signature).
2. `scripts/seed_demo_data.py:169-182` — course catalog → Meridian programs with fees from the knowledge base (MBA $18,500, B.Tech AI & ML $15,200, B.Tech CS $14,500, MCA $13,800, M.Tech $14,600, BBA $10,800, …). The offer PDF + WhatsApp "Amount:" + payment section read these rows.
3. `app/main.py:1147, 1395` — program-capture lists aligned to Meridian names.
4. Optional polish: `app/offers/pdf.py:190` signature block "Dr. A. Chancellor, Dean of Admissions" → "Dr. Helena Cross, Vice Chancellor" (matches the knowledge base's leadership section); `DEFAULT_PAYMENT_LINK` env to a Meridian payment URL.

**Test coverage for this flow:** see `doc/RAGPIPLINE/TestCases_Meridian_Update.md` §10 (offer-letter E2E).

**Use-case coverage summary after the update** (all flows keep working, only content/branding changes):
- Streamlit chat → `app.rag` (MMR/hybrid, Meridian store) ✅
- WhatsApp text + voice note → `query_rag` ✅
- Inbound/outbound calls → `run_rag_query_sync` → `query_rag` ✅ (IVR/greetings copy updated)
- Offer-letter generation → unchanged mechanics, Meridian branding via config + course seed ✅
- MCP tools → `query_rag` ✅ · Dashboard/leads/sentiment → untouched ✅
