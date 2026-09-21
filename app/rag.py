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
import time
from typing import AsyncIterator

from app import rag_legacy
from app import rag_mcp
from app.config import settings
from app.llm_backend import EngineCounters

logger = logging.getLogger("rag_module")

# ── Configuration surface (re-exported verbatim) ───────────────────────────

CHROMA_DB_PATH = rag_legacy.CHROMA_DB_PATH
SOURCES = rag_legacy.SOURCES
OLLAMA_BASE_URL = rag_legacy.OLLAMA_BASE_URL
OLLAMA_MODEL = rag_legacy.OLLAMA_MODEL
OLLAMA_NUM_CTX = rag_legacy.OLLAMA_NUM_CTX
OLLAMA_TEMPERATURE = rag_legacy.OLLAMA_TEMPERATURE
#: Output-token ceiling for the SPOKEN answer. 192 was chosen from the
#: measured answer-length distribution, not from taste: over 83 golden-set
#: cases the 95th percentile was 106 tokens (qwen2.5:14b) and 82
#: (llama3.2:3b), and a ceiling of 192 truncates ZERO of them. It buys a
#: bounded worst case -- the one thing an unbounded generation cannot give a
#: live call -- while changing nothing on the answers actually produced.
#: Set OLLAMA_NUM_PREDICT=0 to remove the ceiling.
OLLAMA_NUM_PREDICT = settings.OLLAMA_NUM_PREDICT
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
    """MCP-first decision: off -> never; on -> always; auto -> breaker-gated.

    US-013 AC-2/AC-3: in `auto`, a half-open circuit admits exactly ONE probe.
    Every other caller in that window takes the local rung instead of piling a
    full request onto a service that has not yet proved it is back -- so two
    callers meeting the same outage cost one probe between them, not two.

    The decision itself now lives in `rag_mcp.admit_primary()`, so all three
    serving call sites -- `_retrieve_context`, `_threshold_distance` and
    `rag_mcp.MCPRetriever` -- share one implementation rather than three
    chances to disagree. This name is kept because the US-013 and BRD-15
    suites drive it directly.
    """
    return rag_mcp.admit_primary()


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
    US-001 wrapper: emits retrieval_done on EVERY return path — MCP-first,
    legacy fallback, and empty result. A wrapper rather than a mark per return
    because four marks is four chances to miss one, and the metric it feeds
    (`retrieval_ms`) is the one BRD-01 names first (REC-12).

    The mark is emitted from inside the worker thread running the blocking
    RAG+LLM call. It reaches the right turn because asyncio.to_thread copies
    the calling context, so the per-WebSocket ContextVar is visible here; two
    concurrent callers get distinct copies (BRD-06).
    """
    result = _retrieve_context(query)
    try:
        from app.perf_trace import mark_current

        mark_current("retrieval_done")
    except Exception:
        pass
    return result


def _note_retrieval(rung: str, breaker: str, dead_ms: float) -> None:
    """Attribute this turn's retrieval cost to a rung and a breaker state.

    US-013 T-11 / MOD-06. The breaker's own counters (`mcp_rag_status`) are
    process-lifetime totals: they say an outage happened and how many times the
    circuit opened, but they cannot attribute any of it to the turn a caller
    waited on. TAC-4 is a per-turn claim -- the wall time charged to a dead
    dependency is bounded by (trips x probe cost) and does NOT grow with the
    number of turns served -- and before these fields that cost was INFERRED
    from those totals rather than measured.

    The three fields are deliberately per turn:
      retrieval_rung                 which rung served: mcp | local | none
      retrieval_breaker              closed | open | half_open, or off when
                                     USE_MCP_RAG=off never consults it
      retrieval_dead_dependency_ms   what the outage charged THIS turn: 0.0 on
                                     a refused or healthy turn, and the
                                     attempt's wall time when the primary was
                                     admitted and failed -- a probe budget at
                                     most, once per window
    """
    try:
        from app.perf_trace import note_current

        note_current(
            retrieval_rung=rung,
            retrieval_breaker=breaker,
            retrieval_dead_dependency_ms=round(dead_ms, 1),
        )
    except Exception:
        pass


def _retrieve_context(query: str) -> str:
    """
    Returns the formatted context string: '[§ {section}]\\n{body}' chunks
    joined by '\\n\\n---\\n\\n'. MCP-first with legacy fallback per USE_MCP_RAG.
    Returns '' when nothing is retrieved.
    """
    mode = _mode()
    admitted = _use_mcp()
    breaker = "off" if mode == "off" else rag_mcp.breaker_state()

    # ── The wiring (US-013 AC-2/AC-3) ──────────────────────────────────────
    # Admission is decided BEFORE the call, not after it fails. `_use_mcp` is
    # off -> never, on -> always (the documented breaker override), auto ->
    # closed admits, half_open admits exactly one probe, open refuses.
    #
    # This call is load-bearing, and it was missing. `_use_mcp` was written for
    # this path and until 2026-09-21 had NO caller outside the test suite: the
    # breaker kept its own state honestly on every failure and nothing ever
    # consulted it, so each turn during an outage called the dead service at the
    # full 6.0 s serving timeout. `claim_probe()` was therefore never reached,
    # `_probe_in_flight()` was never true, `_post` never selected the 1.5 s
    # probe budget, and `breaker_probes` could only ever read 0. TAC-4's
    # "bounded per window, not per request" was untrue as wired -- the cost grew
    # with every turn served. The suite passed 51/51 throughout, because it
    # calls `_use_mcp` and `claim_probe` directly: it tested the mechanism and
    # not its reachability.
    if not admitted:
        _note_retrieval(rung="local", breaker=breaker, dead_ms=0.0)
        return rag_legacy.retrieve_context(query)

    t0 = time.monotonic()
    chunks = rag_mcp.mcp_retrieve(query, top_k=MMR_K)
    attempt_ms = (time.monotonic() - t0) * 1000.0

    if chunks is not None:              # [] is a legitimate empty result
        _note_retrieval(rung="mcp", breaker=breaker, dead_ms=0.0)
        return _guard_context(rag_mcp.format_legacy_chunks(chunks))

    # Service unreachable/error, and this turn was admitted -- so the attempt
    # above IS what the outage charged it. With the circuit working that is one
    # probe budget per window, and 0.0 on every turn the circuit refused.
    if mode == "auto":
        logger.warning(
            "MCP retrieval failed (%s) — falling back to local Chroma (auto mode)",
            rag_mcp.mcp_rag_status().get("last_error"),
        )
        _note_retrieval(rung="local", breaker=breaker, dead_ms=attempt_ms)
        return rag_legacy.retrieve_context(query)
    _note_retrieval(rung="none", breaker=breaker, dead_ms=attempt_ms)
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
    # The circuit gates this call too. It is a SECOND primary call inside the
    # same turn (`query_rag` runs this before `retrieve_context`), so leaving it
    # ungated would put one full-budget request against a dead service on every
    # turn that has the threshold gate enabled -- and, because `_record_failure`
    # restamps `failed_at` on each failure, would keep pushing the cooldown out
    # so the half-open probe never became eligible. Dormant today only because
    # RAG_SIMILARITY_THRESHOLD is 0.0; a config flip would have re-opened it.
    if mode != "off" and _use_mcp():
        chunks = rag_mcp.mcp_retrieve(query, top_k=1)
        if chunks is not None:
            if chunks:
                score = max(0.0, min(1.0, float(chunks[0].get("score") or 0.0)))
                return 1.0 - score
            return 1.0
        if mode == "on":
            return None         # gate cannot run — proceed without it
    return rag_legacy._best_distance(query)


def query_rag(question: str, *, mode: str = "voice",
              retrieval_query: str | None = None,
              history: list[str] | None = None,
              profile: dict | None = None) -> str | None:
    """
    Full RAG pipeline: retrieve context -> build prompt -> query LLM.
    Identical behavior to the legacy pipeline; only the context source is
    dispatched (MCP-first). Returns the answer string, or None on failure.

    C3 (N1 fix): callers that pre-build a full instruction prompt (the voice
    handler passes conversation history + style rules) previously had that
    whole prompt embedded as the retrieval query — the dense vector was
    dominated by boilerplate. ``retrieval_query`` decouples the two: it is
    what gets embedded and retrieved on; ``question`` stays in the prompt
    tail, byte-identical to before.

    ``history`` / ``profile`` are the text-channel equivalent of what the voice
    handler has always passed: recent turns and the fields already on the lead.
    They are appended to the prompt rather than the system message so the
    shared SYSTEM_PROMPT stays byte-identical for callers that pass neither,
    and so the injected block can carry its own instructions.
    """
    if not question.strip():
        return None
    rq = (retrieval_query or question).strip() or question

    # Optional gate: refuse to answer when the best match is too distant
    if RAG_SIMILARITY_THRESHOLD > 0:
        dist = _threshold_distance(rq)
        if dist is not None and dist > RAG_SIMILARITY_THRESHOLD:
            logger.info(f"RAG threshold gate: distance {dist:.3f} > {RAG_SIMILARITY_THRESHOLD}")
            return "I don't have that specific information in the university profile."

    # Step 1: Retrieve context
    context = retrieve_context(rq)

    # Step 2: Build prompt (byte-identical to legacy)
    if mode == "chat":
        prompt = SYSTEM_PROMPT.format(
            context=context
            or "(No university profile information was retrieved for this question.)"
        )
    else:
        from app.voice_system_prompt import build_voice_system_prompt

        prompt = build_voice_system_prompt(context)

    # Step 2b: the student's own record and the conversation so far. Rule 7 of
    # SYSTEM_PROMPT tells the model the chat system collects profile details
    # itself, so when the record is present it should answer from it rather
    # than claim it has no student information.
    if profile:
        known = [(k, v) for k, v in profile.items() if v]
        if known:
            prompt += (
                "\n\nThis student's record (already on file — answer from it if "
                "they ask about themselves, and never ask for these again):\n"
                + "\n".join(f"- {k}: {v}" for k, v in known)
            )
    if history:
        prompt += (
            "\n\nRecent conversation for this student, oldest first:\n"
            + "\n".join(history)
        )

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
            num_predict=OLLAMA_NUM_PREDICT if OLLAMA_NUM_PREDICT > 0 else None,
        )

    except Exception:
        logger.exception("LLM query failed")
        return None


async def query_rag_stream(
    question: str, *, mode: str = "voice",
    cancel: "asyncio.Event | None" = None,
    retrieval_query: str | None = None,
) -> AsyncIterator[str | EngineCounters]:
    """US-004: `query_rag` streamed. Identical retrieval and prompt assembly;
    the generation is consumed as token deltas (llm_backend.generate_stream)
    so the caller can synthesise the first clause while the model decodes.
    The retrieval itself is synchronous, so it runs in a worker thread.

    retrieval_query (C3/N1): embedded for retrieval when the caller passes a
    pre-built instruction prompt (see query_rag).

    Yields str deltas, then exactly one EngineCounters; raises
    GenerationFailed on an empty/errored stream.
    """
    import asyncio

    from app.llm_backend import GenerationFailed, generate_stream

    if not question.strip():
        raise GenerationFailed("empty question")
    rq = (retrieval_query or question).strip() or question

    if RAG_SIMILARITY_THRESHOLD > 0:
        dist = _threshold_distance(rq)
        if dist is not None and dist > RAG_SIMILARITY_THRESHOLD:
            yield "I don't have that specific information in the university profile."
            return

    # Step 1: Retrieve context (sync client; off the loop). `retrieve_context`
    # marks retrieval_done on every return path itself, exactly as the batch
    # path gets it.
    context = await asyncio.to_thread(retrieve_context, rq)

    # Step 2: Build prompt (byte-identical to query_rag)
    if mode == "chat":
        prompt = SYSTEM_PROMPT.format(
            context=context
            or "(No university profile information was retrieved for this question.)"
        )
    else:
        from app.voice_system_prompt import build_voice_system_prompt

        prompt = build_voice_system_prompt(context)
    prompt += f"\n\nStudent's question: {question}"

    # Step 3: streamed generation
    model = rag_legacy._get_available_model()
    logger.info(f"RAG query (stream): model={model}, context_chars={len(context)}")
    async for item in generate_stream(
        prompt, model=model, num_ctx=OLLAMA_NUM_CTX,
        temperature=float(OLLAMA_TEMPERATURE) if OLLAMA_TEMPERATURE else None,
        cancel=cancel,
    ):
        yield item


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
