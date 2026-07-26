# 🔍 RCA: RAG Quality Divergence Between Streamlit and WhatsApp

**Date:** 2026-07-26
**Severity:** High — WhatsApp gives lower-quality answers than Streamlit

---

## Symptom

The same question asked in Streamlit vs WhatsApp produces different answers. Streamlit gives accurate, well-sourced responses. WhatsApp sometimes says "I don't have that information" for questions Streamlit handles correctly, or gives less detailed answers.

---

## Root Cause: Two Completely Separate RAG Implementations

The codebase has **two independent RAG paths** with zero code sharing. Every improvement made to Streamlit's RAG was never ported to the pipeline path used by WhatsApp.

### Side-by-Side Comparison

| Capability | Streamlit (`app.py`) | WhatsApp/FastAPI (`pipeline.py`) |
|---|---|---|
| **PDF header stripping** | ✅ Removes "UMD & FDU — University Profile Report" | ❌ Raw text with headers |
| **Chunk size** | 1500 chars, 100 overlap | Whatever Streamlit last built |
| **Retrieval method** | MMR (Maximal Marginal Relevance) | Plain similarity search |
| **Chunks retrieved** | 5 (from 20 fetched, diversity-filtered) | 2 (raw, often duplicates) |
| **Deduplication** | Implicit via MMR diversity penalty | None |
| **System prompt** | 5-rule anti-hallucination prompt | Basic one-liner |
| **LLM model** | Hardcoded `qwen2.5:7b-instruct-q3_K_M` | Auto-detected first qwen model |
| **Temperature** | 0.0 | Default |
| **Context window** | 2048 | 2048 |
| **Fallback behavior** | "I don't have that information" | Generic "answer to the best of your ability" |

### Impact on Each Query Type

| Query | Streamlit | WhatsApp (before fix) |
|-------|-----------|----------------------|
| "UMD tuition fees" | Real fee data from PDF | May return wrong university's data or refuse |
| "Compare UMD and FDU" | Attempts comparison | Almost certainly "I don't know" |
| "UMD admission requirements" | Accurate, detailed | Less detailed, fewer specifics |
| "UMD programs" | Real program counts + areas | May refuse due to insufficient chunks |

---

## Architectural Gap

```
┌──────────────────────────────────────────────────────┐
│                   CURRENT STATE                      │
│                                                      │
│  Streamlit (app.py)          WhatsApp (pipeline.py)  │
│  ┌──────────────────┐       ┌──────────────────┐     │
│  │ load_rag_chain() │       │ retrieve_context()│     │
│  │   - header strip │       │   - NO strip      │     │
│  │   - MMR retriever│       │   - plain query   │     │
│  │   - k=5 diverse  │       │   - k=2 duplicates│     │
│  │   - refined prompt│      │   - basic prompt  │     │
│  └──────────────────┘       └──────────────────┘     │
│         ⬇                          ⬇                 │
│     ChromaDB A                ChromaDB B?            │
│  (./chroma_local_db)    (app/../chroma_local_db)     │
│                                                      │
│  ⚠️ DIFFERENT PATHS, POSSIBLY DIFFERENT DBs          │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│                   TARGET STATE                       │
│                                                      │
│  Streamlit ─┐                    ┌── WhatsApp        │
│             │                    │                   │
│  Future ────┼── app/rag.py ─────┼── Future          │
│  (Slack)    │  (single source   │   (Telegram)      │
│             │   of truth)       │                   │
│  Future ────┘                    └── Future          │
│  (Web)                            (SMS)             │
│                                                      │
│  Shared: chunking, retrieval, prompt, model config   │
└──────────────────────────────────────────────────────┘
```

---

## Fix Plan

### Phase 1: Create `app/rag.py` — Shared RAG Module

Move all RAG logic into a single module:
- `get_vector_store()` — build or load ChromaDB with header stripping
- `retrieve(query)` — MMR retrieval returning deduplicated chunks
- `query_rag(question)` — full RAG pipeline returning answer string
- Shared prompt template, model config, chunk settings

### Phase 2: Refactor `app.py` to use `app/rag.py`

- Replace `load_rag_chain()` with call to shared module
- Streamlit still handles its own UI, TTS, session state

### Phase 3: Refactor `app/pipeline.py` to use `app/rag.py`

- Replace `retrieve_context()` + `build_rag_prompt()` + `run_rag_query_sync()` with shared module
- WhatsApp endpoint gets same RAG quality as Streamlit

### Phase 4: Future-Proof

Any new integration (Telegram, Slack, SMS, web widget) can import `app/rag.py` and get the same RAG quality with zero duplication.
