"""
Shared RAG Module — University Admissions Assistant
=====================================================
Single source of truth for retrieval-augmented generation.
Used by ALL interfaces: Streamlit, WhatsApp/FastAPI, and future integrations.

Provides:
    get_vector_store()   — build/load ChromaDB with header stripping + MMR
    query_rag(question)  — full RAG pipeline, returns answer string
    retrieve_context(q)  — MMR retrieval, returns formatted context string
"""

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger("rag_module")

# ── Configuration ────────────────────────────────────────────────────

CHROMA_DB_PATH = Path(__file__).resolve().parent.parent / "chroma_local_db"
PDF_PATH = Path(__file__).resolve().parent.parent / "content" / "sample_data" / "UMD_and_FDU_University_Profile_Report.pdf"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "2048"))
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Retrieval settings
MMR_FETCH_K = int(os.environ.get("RAG_FETCH_K", "20"))
MMR_K = int(os.environ.get("RAG_TOP_K", "5"))
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 100

# PDF header pattern that repeats on every page — must be stripped
_HEADER_PATTERN = re.compile(
    r'UMD\s+&\s+FDU\s+[-—]\s+University\s+Profile\s+Report\s*\n?',
    re.IGNORECASE
)

# ── System Prompt ─────────────────────────────────────────────────────

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

# ── Vector Store ──────────────────────────────────────────────────────

_vector_store = None  # Module-level cache


def get_vector_store():
    """
    Build or load the ChromaDB vector store.
    Cached at module level — built once per process.

    Uses:
    - Header stripping to prevent embedding bias
    - Larger chunks (1500 chars) for more content per chunk
    - MMR retrieval for diversity
    """
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from langchain_ollama import OllamaEmbeddings

    if not PDF_PATH.is_file():
        logger.error(f"PDF not found: {PDF_PATH}")
        return None

    if CHROMA_DB_PATH.is_dir() and any(CHROMA_DB_PATH.iterdir()):
        # Load existing collection
        logger.info(f"Loading existing ChromaDB from {CHROMA_DB_PATH}")
        embeddings = OllamaEmbeddings(model=EMBED_MODEL)
        _vector_store = Chroma(
            persist_directory=str(CHROMA_DB_PATH),
            embedding_function=embeddings,
        )
        return _vector_store

    # Build from scratch
    logger.info(f"Building ChromaDB from {PDF_PATH}...")
    loader = PyPDFLoader(str(PDF_PATH))
    raw_docs = loader.load()

    # Strip repeating headers
    for doc in raw_docs:
        doc.page_content = _HEADER_PATTERN.sub('', doc.page_content).strip()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(raw_docs)
    logger.info(f"Split into {len(chunks)} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})")

    embeddings = OllamaEmbeddings(model=EMBED_MODEL)
    _vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DB_PATH),
    )
    return _vector_store


def get_retriever():
    """
    Returns an MMR retriever for diverse, deduplicated results.
    MMR (Maximal Marginal Relevance) prevents near-duplicate chunks
    from dominating the context window.
    """
    vs = get_vector_store()
    if vs is None:
        return None
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": MMR_K,
            "fetch_k": MMR_FETCH_K,
            "lambda_mult": 0.5,
        },
    )


# ── Retrieval ──────────────────────────────────────────────────────────

def retrieve_context(query: str) -> str:
    """
    MMR retrieval — returns formatted context string from ChromaDB.
    Returns empty string if retrieval fails.
    """
    retriever = get_retriever()
    if retriever is None:
        return ""

    try:
        docs = retriever.invoke(query)
        if not docs:
            logger.debug(f"No documents retrieved for: {query[:60]}...")
            return ""

        chunks = [d.page_content for d in docs]
        context = "\n\n---\n\n".join(chunks)
        logger.debug(f"Retrieved {len(chunks)} chunks for: {query[:60]}...")
        return context

    except Exception:
        logger.exception("Retrieval failed")
        return ""


# ── LLM Query ──────────────────────────────────────────────────────────

def _get_available_model() -> str:
    """Find the best available Ollama model, preferring instruct variants."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]

        # Prefer instruct models, then any qwen
        for preference in ("qwen2.5:7b-instruct-q3_K_M", "qwen2.5:7b-instruct", "qwen2.5:7b"):
            if preference in models:
                return preference

        qwen_models = [m for m in models if "qwen" in m.lower()]
        if qwen_models:
            return qwen_models[0]

        return OLLAMA_MODEL
    except Exception:
        return OLLAMA_MODEL


def query_rag(question: str) -> str | None:
    """
    Full RAG pipeline: retrieve context → build prompt → query LLM.

    This is the single entry point for ALL interfaces.
    Thread-safe (no shared mutable state). Callable from sync or async contexts.

    Returns:
        Answer string, or None if the pipeline failed.
    """
    if not question.strip():
        return None

    # Step 1: Retrieve context
    context = retrieve_context(question)

    # Step 2: Build prompt
    if context:
        prompt = SYSTEM_PROMPT.format(context=context)
    else:
        prompt = (
            "You are a helpful university admissions assistant. "
            "Answer the user's question to the best of your ability. "
            "If you're unsure, say so.\n\n"
        )
    prompt += f"Student's question: {question}"

    # Step 3: Query LLM
    try:
        import ollama

        model = _get_available_model()
        logger.info(f"RAG query: model={model}, context_chars={len(context)}")

        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": OLLAMA_NUM_CTX},
        )
        return response["message"]["content"]

    except Exception:
        logger.exception("LLM query failed")
        return None
