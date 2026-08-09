# RAG Pipeline Report — University Admissions Voice Assistant

**Date:** 2026-08-09  
**Branch:** `offerlaterupdate`  
**Primary source file:** `app/rag.py`

---

## 1. Overview

This is a **fully local** RAG system built with:

```
PDF → PyPDFLoader → Regex header strip → RecursiveCharacterTextSplitter
→ OllamaEmbeddings (nomic-embed-text) → ChromaDB (local persistent)
→ MMR Retriever → Qwen2.5 7B LLM → Answer
```

There is **no LlamaIndex, no Pinecone, no cloud vector DB, and no OpenAI** — everything runs locally via Ollama.

---

## 2. Chunking & Ingestion Strategy

### 2.1 Chunk Configuration

| Setting | `app/rag.py` (current) | `admissions_bot.py` (legacy) | Jupyter Notebook (legacy) |
|---|---|---|---|
| **Chunk Size** | **1,500 characters** | 800 characters | 800 characters |
| **Chunk Overlap** | **100 characters** | 150 characters | 150 characters |
| **Splitter** | `RecursiveCharacterTextSplitter` | `RecursiveCharacterTextSplitter` | `RecursiveCharacterTextSplitter` |
| **Loader** | `PyPDFLoader` | `PyPDFLoader` | `PyPDFLoader` |

```python
# app/rag.py, lines 44-45
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 100
```

**Environment overrides:** None. These are hardcoded — not configurable via `.env`.

### 2.2 Source Document

| Item | Value |
|---|---|
| **File** | `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` |
| **Content** | Multi-page profile report covering UMD and FDU |
| **Header stripping** | Regex: `UMD & FDU - University Profile Report` removed from every page before chunking |

### 2.3 Offer Letter Note

The current pipeline ingests **only the university profile PDF**. Offer letters are **not** ingested into the vector store. A short 1-page offer letter (~2,000–4,000 chars) would be split into **2–3 chunks** at the current 1,500-char chunk size. If you add offer letters, you should:

- Add metadata (`source_file`, `doc_type`, `university_name`) to distinguish content
- Ingest through the same `_build_with_langchain()` path for consistency

### 2.4 Vector Store Population

```python
# app/rag.py, lines 104-129 (_build_with_langchain)
loader = PyPDFLoader(str(PDF_PATH))
raw_docs = loader.load()

for doc in raw_docs:
    doc.page_content = _HEADER_PATTERN.sub('', doc.page_content).strip()

splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
chunks = splitter.split_documents(raw_docs)

embeddings = OllamaEmbeddings(model=EMBED_MODEL)
return Chroma.from_documents(
    documents=chunks, embedding=embeddings, persist_directory=str(CHROMA_DB_PATH)
)
```

`Chroma.from_documents()` **appends** to the existing collection — it does not clear first. Combined with legacy code writing to the same path, the store is polluted with multiple generations of chunks at different sizes.

### 2.5 ⚠️ Known Issue — Polluted Vector Store

- **Expected chunks:** ~50
- **Actual chunks in persisted DB:** **4,618**
- **Root cause:** `admissions_bot.py` (800/150), the notebook (800/150), and `app/rag.py` (1500/100) all write to the same `chroma_local_db/` directory with `Chroma.from_documents()` (append-only). Each rebuild adds chunks without removing old ones.
- **Impact:** Retrieval returns mixed-generation, near-duplicate chunks at different granularities.

**Fix:** Delete `chroma_local_db/` and rebuild from a single code path.

---

## 3. Metadata

### 3.1 What is Stored

PyPDFLoader automatically stores per-page metadata from the PDF. Stored keys (found in `chroma.sqlite3` `embedding_metadata` table):

| Key | Example Value |
|---|---|
| `source` | `d:\university_project_demo\content\sample_data\UMD_and_FDU_University_Profile_Report.pdf` |
| `page` | `0`, `1`, `2`, ... |
| `page_label` | `1`, `2`, `3`, ... |
| `title` | (document title) |
| `creationdate` | (ISO date) |
| `creator` | `PyPDF` |
| `producer` | `Skia/PDF m152 Google Docs Renderer` |
| `total_pages` | (integer) |

### 3.2 What is NOT Stored (Gaps)

| Missing metadata | Why it matters |
|---|---|
| `source_file` | Can't distinguish profile PDF from offer letter PDF |
| `doc_type` | Can't filter by document type (`profile` vs `offer_letter` vs `policy`) |
| `university_name` | UMD and FDU content is mixed; can't filter to one university |
| `doc_id` | No way to reference or update specific documents |
| `ingested_at` | No tracking of when chunks were added |

**No custom metadata is ever attached to chunks.** If offer letters are added, custom metadata is essential for filtering at retrieval time.

---

## 4. Embedding Model

| Setting | Value |
|---|---|
| **Model** | **`nomic-embed-text`** |
| **Dimension** | **768** |
| **Runtime** | Ollama (local) |
| **LangChain wrapper** | `OllamaEmbeddings` (from `langchain_ollama`) |
| **Fallback wrapper** | `OllamaEmbeddingFunction` (from `chromadb.utils.embedding_functions`) |
| **Environment override** | `EMBED_MODEL` env var |

```python
# app/rag.py, line 39
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
```

**Why this model?** It runs locally on CPU (no GPU required for embedding), produces 768-dim vectors, and integrates with Ollama's API — no external API keys needed.

**Future consideration:** `doc/INFRASTRUCTURE_PLAN.md` proposes moving to `all-MiniLM-L6-v2` (384-dim) for lower memory usage — **not implemented**.

---

## 5. Vector Store

| Setting | Value |
|---|---|
| **Database** | **ChromaDB** (local persistent) |
| **Path** | `./chroma_local_db/` (project root) |
| **Collection name** | `langchain` (default Chroma name) |
| **Index type** | **HNSW** (Hierarchical Navigable Small World) |
| **Distance metric** | **L2 (Euclidean)** |
| **HNSW params** | `ef_construction=100`, `max_neighbors=16`, `ef_search=100` |
| **LangChain wrapper** | `langchain_community.vectorstores.Chroma` |

### 5.1 Fallback Path

If LangChain imports are blocked (e.g., by AppLocker DLL policies on Windows), the code falls back to raw ChromaDB via `_build_raw_chromadb()`. This path:

- Loads the existing persisted collection with `chromadb.PersistentClient`
- Uses `OllamaEmbeddingFunction` directly (no LangChain)
- Wraps results in a minimal `_RawChromaWrapper` / `_RawRetriever` pair
- Performs **plain similarity search** — no MMR in fallback mode

---

## 6. Retrieval Strategy

### 6.1 Primary Path (Chat / Streamlit / API) — MMR

```python
# app/rag.py, lines 42-43, 179-195
MMR_FETCH_K = int(os.environ.get("RAG_FETCH_K", "20"))
MMR_K = int(os.environ.get("RAG_TOP_K", "5"))

def get_retriever():
    vs = get_vector_store()
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": MMR_K,              # 5 — final result count
            "fetch_k": MMR_FETCH_K,  # 20 — candidate pool
            "lambda_mult": 0.5,      # balanced relevance/diversity
        },
    )
```

| Parameter | Value | Meaning |
|---|---|---|
| `k` | **5** | Return 5 diverse chunks |
| `fetch_k` | **20** | First fetch 20 candidates by similarity |
| `lambda_mult` | **0.5** | 0.5 = balanced; 0.0 = max diversity; 1.0 = max relevance |
| **Strategy** | **MMR** (Maximal Marginal Relevance) | Prevents near-duplicate chunks from dominating the context window |

MMR first fetches `fetch_k` candidates ranked by similarity to the query, then greedily selects `k` results that maximize a weighted combination of relevance to the query AND dissimilarity to already-selected results. This is critical when the vector store has duplicate/near-duplicate chunks (which it does — see §2.5).

### 6.2 Voice Pipeline Path — Plain Similarity

```python
# app/pipeline.py, line 39
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "2"))
```

| Parameter | Value |
|---|---|
| `k` | **2** |
| **Strategy** | **Plain similarity** (no MMR) |
| **Why different?** | Voice responses need to be concise; fewer chunks = faster TTS output |

The voice pipeline (`app/pipeline.py`) queries ChromaDB directly via `collection.query(query_texts=[query], n_results=top_k)` — bypassing the LangChain retriever entirely.

### 6.3 Retrieval Strategy — Summary by Interface

| Interface | Strategy | Top-k | Code Path |
|---|---|---|---|
| Streamlit (chat UI) | MMR | 5 | `app/rag.py` → `get_retriever()` |
| FastAPI / WhatsApp | MMR | 5 | `app/rag.py` → `query_rag()` |
| Voice (Pipecat) | Similarity | 2 | `app/pipeline.py` → `retrieve_context()` |
| `admissions_bot.py` (legacy) | Similarity | 3 | Own chain, own config |

### 6.4 What is NOT Used

| Technique | Status |
|---|---|
| **Hybrid search (BM25 + Dense)** | ❌ Listed as future work in `doc/PENDING_IMPROVEMENTS.md` |
| **Parent Document Retriever** | ❌ Not implemented |
| **Reranker / Cross-encoder** | ❌ Not implemented |
| **Similarity threshold** | ❌ `RAG_SIMILARITY_THRESHOLD` exists in `.env.example` but is **dead config** — never referenced in any code |
| **Metadata filtering** | ❌ Not possible without custom metadata |

---

## 7. LLM Generation

| Setting | Value |
|---|---|
| **Model** | **`qwen2.5:7b-instruct-q3_K_M`** (Ollama) |
| **Quantization** | Q3_K_M (3-bit, medium) |
| **Context window** | **2,048 tokens** (`OLLAMA_NUM_CTX`) |
| **Base URL** | `http://localhost:11434` |
| **Streaming** | Supported (used by Pipecat voice pipeline) |
| **Temperature** | Default (1.0) for `ollama.chat()`; 0.0 for LangChain `ChatOllama` |

```python
# app/rag.py, lines 37-38
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "2048"))
```

**Model auto-detection:** `_get_available_model()` probes `GET /api/tags` and prefers models in order:
1. `qwen2.5:7b-instruct-q3_K_M`
2. `qwen2.5:7b-instruct`
3. `qwen2.5:7b`
4. Any model with "qwen" in the name
5. Falls back to `OLLAMA_MODEL` env value

### 7.1 System Prompt

```python
# app/rag.py, lines 55-69
SYSTEM_PROMPT = (
    "You are a helpful University Admissions Advisor. "
    "Answer questions using the provided university profile context.\n\n"
    "RULES:\n"
    "1. Use facts from the context. When the context has relevant data, "
    "present it clearly — use Markdown for tables and figures.\n"
    "2. NEVER invent numbers, fees, URLs, or program names. "
    "Only cite dollar amounts and figures that appear in the context.\n"
    "3. Keep UMD and FDU information clearly separated. "
    "Label which university each fact belongs to.\n"
    "4. If the context truly has NO relevant data for a question, "
    "say: \"I don't have that specific information in the university profile.\"\n"
    "5. Be concise and conversational.\n\n"
    "Context:\n{context}"
)
```

When no context is retrieved (empty string), a simpler fallback prompt is used that does not reference the university profile.

---

## 8. Full Pipeline Flow

```
┌─────────────────────────────────────────────────────────┐
│                    INGESTION (one-time)                   │
│                                                          │
│  PDF ──► PyPDFLoader ──► Header Strip ──► Splitter       │
│                                              │           │
│                                  1500-char chunks        │
│                                  100-char overlap        │
│                                              │           │
│  ChromaDB ◄── OllamaEmbeddings(nomic-embed-text)         │
│  (persisted to ./chroma_local_db/)                       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    RETRIEVAL (per query)                  │
│                                                          │
│  User Query ──► Embed (nomic-embed-text)                 │
│                        │                                 │
│          ┌─────────────┴─────────────┐                   │
│          │  Chat/API path   │ Voice path │                │
│          │  MMR (k=5,       │ Similarity │               │
│          │  fetch_k=20,     │ (k=2)      │               │
│          │  λ=0.5)          │             │               │
│          └─────────────┬─────────────┘                   │
│                        │                                 │
│  Context Chunks ──► System Prompt.format(context)        │
│                        │                                 │
│  Qwen2.5 7B ◄── Full Prompt (num_ctx=2048)              │
│                        │                                 │
│  Answer ──► User (text) / Kokoro TTS (voice)             │
└─────────────────────────────────────────────────────────┘
```

---

## 9. Configuration Reference

### 9.1 Hardcoded Constants (`app/rag.py`)

| Constant | Value | Line |
|---|---|---|
| `CHROMA_DB_PATH` | `./chroma_local_db/` | 33 |
| `PDF_PATH` | `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` | 34 |
| `CHUNK_SIZE` | 1500 | 44 |
| `CHUNK_OVERLAP` | 100 | 45 |
| `MMR_FETCH_K` | 20 (default) | 42 |
| `MMR_K` | 5 (default) | 43 |

### 9.2 Environment Variables

| Variable | Default | Used In |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | `app/rag.py`, `app/pipeline.py` |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct-q3_K_M` | `app/rag.py`, `app/pipeline.py` |
| `OLLAMA_NUM_CTX` | `2048` | `app/rag.py`, `app/pipeline.py` |
| `EMBED_MODEL` | `nomic-embed-text` | `app/rag.py` |
| `RAG_FETCH_K` | `20` | `app/rag.py` |
| `RAG_TOP_K` | `5` (rag.py) / `2` (pipeline.py) | Both |
| `RAG_SIMILARITY_THRESHOLD` | `0.5` | **DEAD CONFIG** — never read |
| `WHISPER_MODEL` | `small.en` | `app/pipeline.py` |
| `KOKORO_VOICE` | `af_heart` | `app/pipeline.py` |

### 9.3 LangSmith Disabled

```python
# app/rag.py, lines 24-27
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")
```

LangSmith tracing is explicitly disabled to avoid a `langsmith/xxhash` DLL block on Windows.

---

## 10. Related Files

| File | Role |
|---|---|
| `app/rag.py` | **Primary RAG module** — embedding, ChromaDB, MMR retrieval, LLM query |
| `app/pipeline.py` | Voice pipeline — separate ChromaDB query (k=2, no MMR) |
| `app/main.py` | FastAPI startup — pre-warms ChromaDB store (lines 106–123) |
| `app/voice_handler.py` | WhatsApp/voice handler — delegates to `app.rag.query_rag()` |
| `app.py` | Streamlit UI — uses `app.rag.get_retriever()` + LangChain `create_retrieval_chain` |
| `admissions_bot.py` | Legacy CLI bot — **different chunk config (800/150)**, writes to same DB |
| `test_rag_llm.py` | Standalone RAG demo — in-memory ChromaDB, 3 sample docs |
| `tests/test_phase3_rag_llm.py` | Phase 3 tests — in-memory ChromaDB + persisted DB existence check |
| `doc/PENDING_IMPROVEMENTS.md` | Lists hybrid BM25 and `mxbai-embed-large` as future work |
| `doc/INFRASTRUCTURE_PLAN.md` | Proposes `all-MiniLM-L6-v2` embeddings (not implemented) |
| `.env` | Active config — `OLLAMA_MODEL`, `OLLAMA_URL`, `OLLAMA_NUM_CTX` |
| `.env.example` | Template — includes `RAG_TOP_K=2`, dead `RAG_SIMILARITY_THRESHOLD` |

---

## 11. Step-by-Step Rebuild Guide

When the vector store needs to be rebuilt from scratch (e.g., after adding new documents, changing chunk config, or clearing the polluted store):

### 11.1 Full Rebuild (Recommended)

```powershell
# 1. Stop any running processes that hold ChromaDB open
#    (Streamlit, FastAPI, or any Python process using the RAG module)

# 2. Delete the polluted store
Remove-Item -Recurse -Force .\chroma_local_db\

# 3. Verify the PDF is in place
Test-Path .\content\sample_data\UMD_and_FDU_University_Profile_Report.pdf
# Should return True

# 4. Ensure Ollama is running with the embedding model
ollama pull nomic-embed-text
ollama pull qwen2.5:7b-instruct-q3_K_M

# 5. Rebuild by triggering ingestion
python -c "from app.rag import get_vector_store; vs = get_vector_store(); print(f'Done — {vs._collection.count() if hasattr(vs, \"_collection\") else \"built\"} chunks')"

# 6. Verify with a test query
python -c "from app.rag import query_rag; print(query_rag('What is the tuition fee?'))"
```

### 11.2 Rebuild via Streamlit (if Python shell path is blocked)

```powershell
# Just run the Streamlit app — it calls get_vector_store() on first query
streamlit run app.py
# Then ask any question in the UI to trigger ingestion
```

### 11.3 What Happens During Rebuild

1. `get_vector_store()` checks if `chroma_local_db/` exists and is non-empty
2. If missing/empty → calls `_build_with_langchain()`
3. `PyPDFLoader` loads the PDF (one `Document` per page)
4. Regex strips the repeating page header from each page
5. `RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=100)` splits into chunks
6. `OllamaEmbeddings(model="nomic-embed-text")` embeds each chunk via Ollama API
7. `Chroma.from_documents()` persists to `chroma_local_db/` with HNSW index

### 11.4 Expected Output

| Metric | Expected (clean rebuild) |
|---|---|
| PDF pages | ~15 (from the profile report) |
| Chunks | ~40–60 (depends on page content density) |
| Embedding dimension | 768 |
| Collection name | `langchain` |
| DB size on disk | ~10–20 MB |

---

## 12. How to Add a New Document (e.g., Offer Letter)

The current `_build_with_langchain()` only ingests one hardcoded PDF. To add a new document type like an offer letter:

### 12.1 Code Changes Needed (Conceptual)

```python
# In app/rag.py — a multi-document ingestion function

PDF_PATHS = {
    "profile": Path(__file__).resolve().parent.parent / "content" / "sample_data" / "UMD_and_FDU_University_Profile_Report.pdf",
    "offer_letter": Path(__file__).resolve().parent.parent / "content" / "sample_data" / "Offer_Letter.pdf",
}

def _build_with_langchain():
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from langchain_ollama import OllamaEmbeddings

    all_chunks = []

    for doc_type, path in PDF_PATHS.items():
        if not path.is_file():
            logger.warning(f"Skipping {doc_type} — file not found: {path}")
            continue

        loader = PyPDFLoader(str(path))
        raw_docs = loader.load()

        for doc in raw_docs:
            doc.page_content = _HEADER_PATTERN.sub('', doc.page_content).strip()
            # Attach custom metadata
            doc.metadata["doc_type"] = doc_type
            doc.metadata["source_file"] = path.name
            doc.metadata["ingested_at"] = __import__("datetime").datetime.now().isoformat()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
        )
        chunks = splitter.split_documents(raw_docs)
        all_chunks.extend(chunks)

    logger.info(f"Total chunks across {len(PDF_PATHS)} documents: {len(all_chunks)}")

    embeddings = OllamaEmbeddings(model=EMBED_MODEL)
    return Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DB_PATH),
    )
```

### 12.2 Retrieval with Metadata Filtering

Once metadata is in place, filter at retrieval time:

```python
def get_retriever(doc_type: str | None = None):
    vs = get_vector_store()
    search_kwargs = {
        "k": MMR_K,
        "fetch_k": MMR_FETCH_K,
        "lambda_mult": 0.5,
    }
    if doc_type:
        search_kwargs["filter"] = {"doc_type": doc_type}

    return vs.as_retriever(search_type="mmr", search_kwargs=search_kwargs)
```

### 12.3 Steps to Add an Offer Letter (Operational)

1. Place the offer letter PDF in `content/sample_data/`
2. Update `PDF_PATHS` (or the equivalent config) to include the new file
3. Delete `chroma_local_db/` to force a clean rebuild
4. Run the ingestion (Streamlit or the Python one-liner from §11)
5. Verify with: `python -c "from app.rag import query_rag; print(query_rag('What does the offer letter say about tuition deposit?'))"`

---

## 13. Context Window Budget

This is a real operational constraint. The LLM has a strict 2,048-token context window — once exceeded, the prompt is truncated and the model may hallucinate or miss context.

### 13.1 Budget Breakdown

| Component | Characters | Estimated Tokens | % of 2,048 |
|---|---|---|---|
| **System prompt** (rules + instructions) | ~700 chars | ~175 tokens | 8.5% |
| **Retrieved context** (5 chunks × 1,500 chars) | ~7,500 chars (worst case) | ~1,875 tokens | 91.5% |
| **User question** | ~100 chars | ~25 tokens | 1.2% |
| **Overhead** (chat template, special tokens) | — | ~50 tokens | 2.4% |
| **Total (worst case)** | ~8,300 chars | **~2,125 tokens** | **103.7% ❌** |

### 13.2 Real-World Mitigation

In practice, chunks are rarely full 1,500 characters, so the budget usually fits:

| Scenario | Typical chunk fill | Context tokens | Status |
|---|---|---|---|
| All chunks 100% full | 5 × 1,500 = 7,500 chars | ~2,125 | ❌ Overflow |
| Chunks 50% full (typical) | 5 × 750 = 3,750 chars | ~1,187 | ✅ Fits |
| Voice pipeline (k=2) | 2 × 750 = 1,500 chars | ~550 | ✅ Ample room |

### 13.3 If Context Overflows

The `num_ctx=2048` setting does NOT auto-truncate intelligently — older Ollama versions silently truncate from the beginning (losing the system prompt), while newer versions may error. Watch for:

- **Hallucination** — LLM invents facts because the system prompt was truncated
- **Wrong university** — UMD/FDU separation rules lost
- **"I don't have that information"** — when context was actually relevant but got cut

### 13.4 Recommended Safeguards

```python
# Approximate token count (4 chars ≈ 1 token for English text)
def _estimate_tokens(text: str) -> int:
    return len(text) // 4

def retrieve_context(query: str) -> str:
    # ... existing retrieval code ...
    context = "\n\n---\n\n".join(chunks)

    # Warn if context alone approaches the limit
    est_tokens = _estimate_tokens(context)
    if est_tokens > 1600:  # 80% of 2048
        logger.warning(
            f"Context may overflow: ~{est_tokens} tokens (limit {OLLAMA_NUM_CTX}). "
            f"Consider reducing RAG_TOP_K from {MMR_K}."
        )
    return context
```

---

## 14. Fallback Path — AppLocker & Raw ChromaDB

### 14.1 Why It Exists

This project runs on Windows machines with **AppLocker** policies that block unsigned DLLs. Several LangChain dependencies (`langchain_core`, `langsmith`, `xxhash`) load native DLLs that AppLocker may reject, causing `ImportError` or `OSError` at runtime.

The code at `app/rag.py:76-101` handles this gracefully:

```python
def get_vector_store():
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    if not CHROMA_DB_PATH.is_dir() or not any(CHROMA_DB_PATH.iterdir()):
        return None

    try:
        _vector_store = _build_with_langchain()   # ← primary: MMR + header stripping
        if _vector_store is not None:
            return _vector_store
    except Exception as e:
        logger.warning(f"LangChain vector store unavailable ({e}), using raw ChromaDB")

    _vector_store = _build_raw_chromadb()          # ← fallback: plain similarity
    return _vector_store
```

### 14.2 What Degrades in Fallback Mode

| Feature | LangChain Path | Raw ChromaDB Path |
|---|---|---|
| **MMR retrieval** | ✅ k=5, fetch_k=20, λ=0.5 | ❌ Plain similarity only |
| **Header stripping** | ✅ Regex on every page | ❌ Not applied (existing chunks used as-is) |
| **Rebuild from PDF** | ✅ `_build_with_langchain()` | ❌ Cannot rebuild — only reads existing DB |
| **Metadata filtering** | ✅ (if implemented) | ⚠️ Possible but not wired up |
| **Environment overrides** | ✅ `RAG_TOP_K`, `RAG_FETCH_K` | ⚠️ Only `RAG_TOP_K` (via `_RawRetriever`) |

### 14.3 How to Check Which Path Is Active

```python
from app.rag import _vector_store
if _vector_store is None:
    print("Vector store not initialized")
elif isinstance(_vector_store, __import__('app.rag')._RawChromaWrapper):
    print("FALLBACK: Raw ChromaDB (no LangChain)")
else:
    print("PRIMARY: LangChain ChromaDB with MMR")
```

### 14.4 DLL Block Workarounds Already in Place

```python
# app/rag.py, lines 20-27
os.environ.setdefault("HF_HUB_ENABLE_HF_XET", "0")       # Bypass huggingface_hub DLL
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")    # Bypass langsmith import
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")
```

---

## 15. MCP Tools That Expose RAG

The Model Context Protocol (MCP) server exposes RAG queries to external AI clients (Claude Desktop, VS Code, etc.). Two files register RAG-related tools:

### 15.1 `app/mcp/tools.py`

| Tool Name | Description | RAG Function Used |
|---|---|---|
| `lookup_admissions_info` | Query the university profile for admissions info | Delegates to `app.rag.query_rag()` |
| `check_application_status` | Check an application by ID | DB query, not RAG |
| `get_program_details` | Get program-specific info | May use RAG context |

### 15.2 `app/leads/mcp_tools.py`

| Tool Name | Description | RAG Function Used |
|---|---|---|
| `query_knowledge_base` | General knowledge base query | Delegates to `app.rag.query_rag()` |
| `search_admissions_policy` | Policy-specific search | Delegates to `app.rag.query_rag()` |

### 15.3 How External Clients Reach the RAG

```
Claude Desktop / VS Code
        │
        ▼
  MCP Protocol (JSON-RPC over stdio or HTTP)
        │
        ▼
  app/mcp/tools.py  ──►  app/rag/query_rag()
        │                       │
        │              ┌────────┴────────┐
        │              │  ChromaDB MMR   │
        │              │  Ollama Qwen    │
        │              └─────────────────┘
        │
  app/leads/mcp_tools.py  ──►  same path
```

### 15.4 MCP Configuration

```python
# app/config.py, lines 72-75
MCP_ENABLED: bool = field(
    default_factory=lambda: _env("MCP_ENABLED", "true").lower() == "true"
)
```

When `MCP_ENABLED=true` (the default), the FastAPI server registers these tools at startup via `app/main.py`.

---

## 16. Troubleshooting

### 16.1 "ChromaDB not found" / Empty Retrieval

**Symptom:** `retrieve_context()` returns empty string, logs show "ChromaDB not found at..."

**Causes & Fixes:**
1. **Store never built** → Run Streamlit (`app.py`) once or use the rebuild command from §11
2. **Wrong path** → Verify `chroma_local_db/` exists in the project root, not in a subfolder
3. **Collection deleted** → Rebuild from scratch (delete the folder and re-ingest)

### 16.2 "Ollama not reachable" / Connection Refused

**Symptom:** `urllib.error.URLError` or `ConnectionRefusedError` when querying Ollama

**Fixes:**
```powershell
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Start Ollama (if installed as a Windows app, just launch it)
# Or via CLI:
ollama serve
```

### 16.3 Embedding Dimension Mismatch

**Symptom:** `ValueError: embedding dimension mismatch` or similar

**Root cause:** The persisted ChromaDB was built with one embedding model (e.g., 384-dim) but the current config uses another (768-dim `nomic-embed-text`).

**Fix:** Delete `chroma_local_db/` and rebuild — the new build will use the current model consistently.

### 16.4 "LangChain vector store unavailable" in Logs

**Symptom:** Log shows the warning about LangChain being unavailable, falling back to raw ChromaDB

**Causes:**
1. **AppLocker blocking DLLs** → This is expected on locked-down Windows; the fallback handles it
2. **Missing pip packages** → Run: `pip install langchain langchain-ollama langchain-text-splitters chromadb pypdf langchain-community`
3. **PDF file missing** → Verify `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` exists

**Impact:** You lose MMR retrieval and header stripping, but basic similarity search still works.

### 16.5 LLM Hallucination

**Symptom:** The LLM invents tuition fees, program names, or deadlines not in the source PDF

**Likely causes:**
1. **Context overflow** → 5 × full chunks exceed 2,048 tokens; system prompt gets truncated (§13)
2. **No relevant chunks retrieved** → Question doesn't semantically match the profile text; LLM falls back to its training data
3. **System prompt not in effect** → If using the voice pipeline, verify `build_rag_prompt()` is being called between STT and LLM

**Diagnostic:** Enable debug logging and check how many chunks were retrieved and their character count:
```python
import logging
logging.getLogger("rag_module").setLevel(logging.DEBUG)
```

### 16.6 Duplicate / Overlapping Answers

**Symptom:** The LLM repeats the same fact multiple times in its answer

**Root cause:** The polluted vector store (§2.5) returns near-duplicate chunks from different generations (same content, different chunk boundaries).

**Fix:** Rebuild the store from scratch (§11).

---

## 17. Performance Benchmarks

*Note: These are estimates based on the local-only architecture. Actual numbers depend on CPU speed, RAM, and whether GPU acceleration is available for Ollama.*

### 17.1 Embedding (nomic-embed-text via Ollama on CPU)

| Document Size | Pages | Chunks | Embedding Time | Throughput |
|---|---|---|---|---|
| 1-page offer letter | 1 | ~3 | ~2–5 seconds | ~1 chunk/sec |
| Full profile PDF | ~15 | ~50 | ~30–90 seconds | ~0.5–1 chunk/sec |
| Large handbook (estimate) | ~100 | ~350 | ~5–10 minutes | ~0.5–1 chunk/sec |

nomic-embed-text is CPU-only and relatively lightweight — embedding is the slowest step in ingestion but happens only once.

### 17.2 Retrieval (ChromaDB HNSW, 4,618 chunks)

| Operation | Latency |
|---|---|
| Query embedding (nomic-embed-text) | ~200–500 ms |
| HNSW search (4,618 vectors, L2) | ~10–50 ms |
| **Total retrieval** | **~250–550 ms** |

With a clean store (~50 chunks), HNSW search drops to <5 ms.

### 17.3 LLM Generation (Qwen2.5 7B Q3_K_M, CPU)

| Context Size | Time-to-First-Token | Full Response (200 tokens) |
|---|---|---|
| Small (~500 tokens) | ~2–5 seconds | ~10–20 seconds |
| Medium (~1,000 tokens) | ~5–10 seconds | ~15–30 seconds |
| Full window (~2,000 tokens) | ~10–20 seconds | ~20–45 seconds |

These are CPU-only estimates. With GPU offloading (CUDA/Metal), generation is 3–10× faster.

### 17.4 End-to-End RAG Query (Typical)

| Phase | Latency |
|---|---|
| Query embedding | ~300 ms |
| HNSW retrieval | ~20 ms |
| Prompt construction | <1 ms |
| LLM generation (1,000-token context, 150-token response) | ~8–20 seconds |
| **Total** | **~8–21 seconds** |

### 17.5 Voice Pipeline Additional Latency

On top of the RAG query, the voice pipeline adds:

| Component | Latency |
|---|---|
| VAD (voice activity detection) | ~200–300 ms (real-time) |
| Whisper STT (small.en, CPU) | ~1–3 seconds per utterance |
| Kokoro TTS | ~1–2 seconds per sentence |
| **Voice pipeline overhead** | **~3–6 seconds** |

**End-to-end voice interaction (user speaks → hears answer): ~12–30 seconds** depending on utterance length and context size.

### 17.6 Storage

| Component | Size |
|---|---|
| ChromaDB (clean, ~50 chunks × 768-dim) | ~2–5 MB |
| ChromaDB (polluted, 4,618 chunks) | ~50–150 MB |
| nomic-embed-text model (Ollama) | ~274 MB |
| Qwen2.5 7B Q3_K_M (Ollama) | ~3.5 GB |
| Whisper small.en | ~466 MB |

---

## 18. Key Gaps & Recommendations

| # | Gap | Severity | Recommendation |
|---|---|---|---|
| 1 | **Polluted vector store** (4,618 chunks vs ~50 expected) | 🔴 High | Delete `chroma_local_db/` and rebuild from a single code path |
| 2 | **No custom metadata** on chunks | 🟡 Medium | Add `source_file`, `doc_type`, `university_name` before ingesting offer letters |
| 3 | **Chunk config not centralized** — `app/rag.py` (1500/100) vs `admissions_bot.py` (800/150) | 🟡 Medium | Standardize on one config, deprecate or update `admissions_bot.py` |
| 4 | **Retrieval strategy inconsistent** — MMR for chat, plain similarity for voice | 🟢 Low | Document the intentional difference; consider upgrading voice to MMR with k=3 |
| 5 | **No offer letter ingestion** pipeline | 🟡 Medium | Add offer letter PDF ingestion with metadata tagging |
| 6 | **Dead config** `RAG_SIMILARITY_THRESHOLD` | 🟢 Low | Either implement threshold filtering or remove the env var from `.env.example` |
| 7 | **No hybrid/BM25 search** | 🟢 Low | Listed as future work — would improve retrieval on keyword-heavy queries |
