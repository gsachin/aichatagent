"""
Shared RAG Module — MCP-first dispatcher (enterprise-rag-core integration)
===========================================================================
The public API of the legacy module (app/rag_legacy.py) is preserved exactly,
but retrieval is now MCP-first: context comes from the standalone
enterprise-rag-core service (streamable HTTP, app/rag_mcp.py) with automatic
fallback to the local ChromaDB store when the service is unreachable.

Mode (env USE_MCP_RAG, read lazily per call):
    auto (default) — MCP first; on failure log a warning, record the circuit
                     breaker, and fall back to the legacy store.
    on             — MCP only; failures return empty context ("" / []).
    off            — legacy store only; the MCP client is never touched.

LLM generation stays in universityDemo (app/llm_backend.chat) — only
RETRIEVAL is decoupled. prompt construction and the LLM call are byte-
identical to the legacy pipeline.
"""

import logging
import os

from app import rag_legacy
from app import rag_mcp

logger = logging.getLogger("rag_module")

# ── Configuration surface (re-exported verbatim) ───────────────────────────

CHROMA_DB_PATH = rag_legacy.CHROMA_DB_PATH
SOURCES = rag_legacy.SOURCES
OLLAMA_BASE_URL = rag_legacy.OLLAMA_BASE_URL
OLLAMA_MODEL = rag_legacy.OLLAMA_MODEL
OLLAMA_NUM_CTX = rag_legacy.OLLAMA_NUM_CTX
OLLAMA_TEMPERATURE = rag_legacy.OLLAMA_TEMPERATURE
EMBED_MODEL = rag_legacy.EMBED_MODEL
CHUNK_SIZE = rag_legacy.CHUNK_SIZE
CHUNK_OVERLAP = rag_legacy.CHUNK_OVERLAP
MMR_FETCH_K = rag_legacy.MMR_FETCH_K
MMR_K = rag_legacy.MMR_K
RAG_SEARCH_MODE = rag_legacy.RAG_SEARCH_MODE
RAG_SIMILARITY_THRESHOLD = rag_legacy.RAG_SIMILARITY_THRESHOLD
RAG_MAX_CONTEXT_CHARS = rag_legacy.RAG_MAX_CONTEXT_CHARS
EXPECTED_MARKERS = rag_legacy.EXPECTED_MARKERS
BLOCKED_MARKERS = rag_legacy.BLOCKED_MARKERS
SYSTEM_PROMPT = rag_legacy.SYSTEM_PROMPT

# Legacy internals reused by the dispatcher and the rebuild script.
_guard_context = rag_legacy._guard_context
_estimate_tokens = rag_legacy._estimate_tokens
_load_markdown_sections = rag_legacy._load_markdown_sections


def _mode() -> str:
    return os.environ.get("USE_MCP_RAG", "auto").lower()


def _use_mcp() -> bool:
    """MCP-first decision: off -> never; on -> always; auto -> breaker-gated."""
    mode = _mode()
    if mode == "off":
        return False
    if mode == "on":
        return True
    return rag_mcp.mcp_available()


# ── Ingestion validation (delegated — build path is legacy-owned) ──────────

def validate_source(text: str) -> list[str]:
    """Legacy validation gate — used by scripts/rebuild_rag_index.py."""
    return rag_legacy.validate_source(text)


def get_vector_store():
    """Loads the persisted LOCAL ChromaDB store (legacy backend, unchanged)."""
    return rag_legacy.get_vector_store()


def build_vector_store(dest_dir=None):
    """Validated build into a fresh directory (rebuild script only)."""
    return rag_legacy.build_vector_store(dest_dir)


def retrieve_context_hybrid(query: str, top_k: int = MMR_K,
                            fetch_k: int = MMR_FETCH_K) -> str:
    """Legacy local hybrid path (kept for parity and the rebuild script)."""
    return rag_legacy.retrieve_context_hybrid(query, top_k, fetch_k)


# ── Retrieval (the decoupled seam) ─────────────────────────────────────────

def retrieve_context(query: str) -> str:
    """
    Returns the formatted context string: '[§ {section}]\\n{body}' chunks
    joined by '\\n\\n---\\n\\n'. MCP-first with legacy fallback per USE_MCP_RAG.
    Returns '' when nothing is retrieved.
    """
    mode = _mode()
    if mode == "off":
        return rag_legacy.retrieve_context(query)

    chunks = rag_mcp.mcp_retrieve(query, top_k=MMR_K)
    if chunks is not None:              # [] is a legitimate empty result
        return _guard_context(rag_mcp.format_legacy_chunks(chunks))

    # Service unreachable/error:
    if mode == "auto":
        logger.warning(
            "MCP retrieval failed (%s) — falling back to local Chroma (auto mode)",
            rag_mcp.mcp_rag_status().get("last_error"),
        )
        return rag_legacy.retrieve_context(query)
    return ""


def get_retriever():
    """
    Retriever for the LCEL chain sites (app.py, admissions_bot.py).
    MCP mode returns an MCP-backed retriever (lazy legacy fallback in auto
    mode); off mode returns the legacy MMR retriever unchanged.
    """
    mode = _mode()
    if mode == "off":
        return rag_legacy.get_retriever()
    return rag_mcp.MCPRetriever(
        top_k=MMR_K,
        fallback_retriever_factory=rag_legacy.get_retriever if mode == "auto" else None,
        allow_fallback=(mode == "auto"),
    )


# ── LLM Query (retrieval swapped, prompt + LLM byte-identical) ─────────────

def _threshold_distance(query: str) -> float | None:
    """Best cosine distance for the similarity-threshold gate.

    Legacy path uses Chroma distances directly; the MCP path maps the top
    chunk score (cosine similarity) to distance = 1 - score."""
    mode = _mode()
    if mode != "off":
        chunks = rag_mcp.mcp_retrieve(query, top_k=1)
        if chunks is not None:
            if chunks:
                score = max(0.0, min(1.0, float(chunks[0].get("score") or 0.0)))
                return 1.0 - score
            return 1.0
        if mode == "on":
            return None         # gate cannot run — proceed without it
    return rag_legacy._best_distance(query)


def query_rag(question: str, *, mode: str = "voice") -> str | None:
    """
    Full RAG pipeline: retrieve context -> build prompt -> query LLM.
    Identical behavior to the legacy pipeline; only the context source is
    dispatched (MCP-first). Returns the answer string, or None on failure.
    """
    if not question.strip():
        return None

    # Optional gate: refuse to answer when the best match is too distant
    if RAG_SIMILARITY_THRESHOLD > 0:
        dist = _threshold_distance(question)
        if dist is not None and dist > RAG_SIMILARITY_THRESHOLD:
            logger.info(f"RAG threshold gate: distance {dist:.3f} > {RAG_SIMILARITY_THRESHOLD}")
            return "I don't have that specific information in the university profile."

    # Step 1: Retrieve context
    context = retrieve_context(question)

    # Step 2: Build prompt (byte-identical to legacy)
    if mode == "chat":
        prompt = SYSTEM_PROMPT.format(
            context=context
            or "(No university profile information was retrieved for this question.)"
        )
    else:
        from app.voice_system_prompt import build_voice_system_prompt

        prompt = build_voice_system_prompt(context)
    prompt += f"\n\nStudent's question: {question}"

    # Step 3: Query LLM (Ollama on Windows/Linux, MLX on Apple Silicon)
    try:
        from app.llm_backend import chat as backend_chat

        model = rag_legacy._get_available_model()
        logger.info(f"RAG query: model={model}, context_chars={len(context)}")

        return backend_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            num_ctx=OLLAMA_NUM_CTX,
            temperature=float(OLLAMA_TEMPERATURE) if OLLAMA_TEMPERATURE else None,
        )

    except Exception:
        logger.exception("LLM query failed")
        return None


# ── Startup warm-up (FastAPI lifespan) ─────────────────────────────────────

def warmup() -> None:
    """Startup warm-up, called from the FastAPI lifespan. Never raises.

    MCP mode: initialize the MCP session (so the first chat turn is warm).
    Legacy mode: load the local ChromaDB store."""
    try:
        if _mode() == "off":
            rag_legacy.get_vector_store()
            logger.info("RAG warmup: local Chroma store loaded")
        else:
            ok = rag_mcp.mcp_initialize()
            logger.info(
                "RAG warmup: MCP %s (%s)",
                "session ready" if ok else "unavailable — auto fallback active",
                rag_mcp.mcp_rag_status().get("url"),
            )
    except Exception:
        logger.exception("RAG warmup failed")
