"""Prompt assembly and generation for document-context chat/voice.

RAG has been removed: there is no Chroma, no embedding call, no MCP retrieval
and no similarity gate. The configured prompt bundle (``app.doc_context``) is
passed to OpenAI on every turn. ``CONTEXT_MODE=off`` remains as a
test/rollback switch that sends the explicit no-context marker.

``CONTEXT_MODE=site`` uses the deterministic router in
``app.site_router``: the stable core cards plus the fee fact, page node or
non-PII notices the query needs. An unrecognised query falls back to the full
compact bundle, so routing can only narrow what it is confident about.

The public functions (``query_rag``, ``query_rag_stream``,
``last_retrieved_context``) keep their names because the voice handler and the
text/WhatsApp surfaces call them. ``last_context_bundle`` is the provenance-
preserving form; the guard should use it.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import AsyncIterator

from app.config import settings
from app.context_bundle import ContextBundle
from app.llm_backend import EngineCounters
from app.voice_system_prompt import SPEAKER_IDENTITY

logger = logging.getLogger("rag_module")

#: The document injected in the most recent turn, per async context. Two
#: concurrent callers must not see each other's evidence.
_LAST_CONTEXT: ContextVar[str] = ContextVar("last_retrieved_context", default="")

#: The full bundle (text + IDs + sources) for THIS turn. The guard reads this
#: rather than re-reading the document.
_LAST_BUNDLE: ContextVar[ContextBundle | None] = ContextVar(
    "last_context_bundle", default=None
)

#: Chat-surface prompt (Markdown-oriented). Voice uses
#: ``app.voice_system_prompt.build_voice_system_prompt`` instead.
SYSTEM_PROMPT = (
    f"You are a helpful University Admissions Advisor for {settings.COMPANY_NAME}. "
    + SPEAKER_IDENTITY
    + "\n\nAnswer questions using the provided university profile context.\n\n"
    "RULES:\n"
    "1. Use facts from the context. When the context has relevant data, "
    "present it clearly — use Markdown for tables and figures.\n"
    "2. NEVER invent numbers, fees, URLs, or program names. "
    "Only cite amounts and figures that appear in the context.\n"
    "3. When you state fees, dates, or figures, cite the section they come "
    "from, e.g. (§ Fees Structure).\n"
    "4. If the context truly has NO relevant data for a question, "
    "say: \"I don't have that specific information in the university profile.\"\n"
    "5. Be concise and conversational.\n"
    "6. Answer only the question the student asked — give one answer at a "
    "time, and ask at most one short follow-up question if it is truly "
    "needed to answer.\n"
    "7. Never ask the student for personal details (name, phone number, "
    "email, city, country) and never re-ask details they already shared — "
    "the chat system collects profile information itself, one field at a time.\n\n"
    "Context:\n{context}"
)


def last_retrieved_context() -> str:
    """The prompt text injected for THIS turn ("" when ``CONTEXT_MODE=off``)."""
    bundle = _LAST_BUNDLE.get()
    if bundle is not None:
        return bundle.text
    return _LAST_CONTEXT.get()


def last_context_bundle() -> ContextBundle | None:
    """The bundle for THIS turn, or the configured bundle when none was set.

    The fallback keeps the guard working when a caller reaches it before
    ``query_rag``/``query_rag_stream`` has set the per-turn value. That
    fallback is deliberately the full configured bundle: until a turn has
    selected a smaller one, the safe evidence set is everything the prompt
    corpus carries.
    """
    bundle = _LAST_BUNDLE.get()
    if bundle is not None:
        return bundle
    if settings.CONTEXT_MODE == "off":
        return None
    from app.doc_context import document_bundle

    return document_bundle()


def context_bundle_for_query(query: str = "") -> ContextBundle:
    """The bundle for one turn.

    ``doc`` (default) — the full configured prompt bundle.
    ``site`` — the deterministic selective router; it falls back to the full
    compact bundle when it has no confident route.
    ``off`` — an empty bundle (the system prompt gets the explicit marker).
    """
    if settings.CONTEXT_MODE == "off":
        return ContextBundle(text="")
    if settings.CONTEXT_MODE == "site":
        try:
            from app.site_router import route_query

            return route_query(query)
        except Exception:  # noqa: BLE001 - a router fault must not drop the turn
            logger.exception("site router failed; using the full bundle")
    if settings.CONTEXT_MODE not in ("doc", "site", ""):
        logger.warning(
            "CONTEXT_MODE=%r is not supported after RAG removal; using doc",
            settings.CONTEXT_MODE,
        )
    from app.doc_context import document_bundle

    return document_bundle()


def context_for_query(query: str = "") -> str:
    """The context text for one turn (backward-compatible wrapper)."""
    return context_bundle_for_query(query).text


def clear_turn_context() -> None:
    """Drop this context's prompt/evidence (tests and worker teardown)."""
    _LAST_BUNDLE.set(None)
    _LAST_CONTEXT.set("")


def bind_context_bundle(bundle: ContextBundle) -> None:
    """Publish *bundle* as this turn's prompt and guard evidence.

    The pipeline's text path builds its own prompt; without this binding the
    guard would fall back to the full configured bundle while the model saw a
    selective one. The two must stay the same text.
    """
    _LAST_BUNDLE.set(bundle)
    _LAST_CONTEXT.set(bundle.text)


def _temperature() -> float | None:
    raw = str(settings.LLM_TEMPERATURE or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _build_system(mode: str, context: str) -> str:
    if mode == "chat":
        return SYSTEM_PROMPT.format(
            context=context
            or "(No university profile information was retrieved for this question.)"
        )
    from app.voice_system_prompt import build_voice_system_prompt

    return build_voice_system_prompt(context)


def _pg_boundary_answer(query: str) -> str | None:
    """The deterministic PG programme/fee boundary, or None."""
    try:
        from app.site_router import pg_boundary_answer

        return pg_boundary_answer(query)
    except Exception:  # noqa: BLE001 - never drop a turn over the boundary check
        logger.exception("PG boundary check failed")
        return None


def _language_suffix(language: str | None) -> str:
    if not language:
        return ""
    from app.voice_system_prompt import language_instruction

    return f"\n\n{language_instruction(language)}"


def query_rag(question: str, *, mode: str = "voice",
              retrieval_query: str | None = None,
              history: list[str] | None = None,
              profile: dict | None = None,
              language: str | None = None) -> str | None:
    """Build the OpenAI prompt and return the answer (no retrieval).

    ``retrieval_query`` is the raw caller question when the caller has already
    built the full prompt; routing and the PG boundary use it instead of the
    prompt wrapper.
    """
    if not question.strip():
        return None

    routing_query = retrieval_query or question
    boundary = _pg_boundary_answer(routing_query)
    if boundary is not None:
        bind_context_bundle(context_bundle_for_query(routing_query))
        logger.info("PG programme/fee boundary answer")
        return boundary

    bundle = context_bundle_for_query(routing_query)
    context = bundle.text
    bind_context_bundle(bundle)
    system = _build_system(mode, context)

    # The student's own record and the conversation so far belong to the turn,
    # not to the instructions.
    turn = ""
    if profile:
        known = [(k, v) for k, v in profile.items() if v]
        if known:
            turn += (
                "\n\nThis student's record (already on file — answer from it if "
                "they ask about themselves, and never ask for these again):\n"
                + "\n".join(f"- {k}: {v}" for k, v in known)
            )
    if history:
        turn += (
            "\n\nRecent conversation for this student, oldest first:\n"
            + "\n".join(history)
        )

    prompt = (
        turn + f"\n\nStudent's question: {question}" + _language_suffix(language)
    )

    try:
        from app.llm_backend import _messages, chat as backend_chat

        logger.info(
            "LLM query: model=%s, context_chars=%d", settings.LLM_MODEL, len(context)
        )
        return backend_chat(
            messages=_messages(system, prompt),
            model=settings.LLM_MODEL,
            num_ctx=8192,
            temperature=_temperature(),
            num_predict=settings.LLM_MAX_TOKENS or None,
        )
    except Exception:
        logger.exception("LLM query failed")
        return None


async def query_rag_stream(
    question: str, *, mode: str = "voice",
    cancel: "asyncio.Event | None" = None,
    retrieval_query: str | None = None,
    language: str | None = None,
) -> AsyncIterator[str | EngineCounters]:
    """Streamed OpenAI generation with the document in the SYSTEM turn."""
    from app.llm_backend import GenerationFailed, generate_stream

    if not question.strip():
        raise GenerationFailed("empty question")

    routing_query = retrieval_query or question
    boundary = _pg_boundary_answer(routing_query)
    if boundary is not None:
        bind_context_bundle(context_bundle_for_query(routing_query))
        logger.info("PG programme/fee boundary answer (stream)")
        yield boundary
        return

    bundle = context_bundle_for_query(routing_query)
    context = bundle.text
    # Set on the loop so the grounding boundary sees it (the old to_thread
    # retrieval seam discarded context-var writes made in the worker).
    bind_context_bundle(bundle)
    system = _build_system(mode, context)
    prompt = (
        f"Student's question: {question}" + _language_suffix(language)
    )

    logger.info(
        "LLM query (stream): model=%s, context_chars=%d",
        settings.LLM_MODEL, len(context),
    )
    async for item in generate_stream(
        prompt, model=settings.LLM_MODEL, num_ctx=8192,
        temperature=_temperature(), cancel=cancel, system=system,
    ):
        yield item


def warmup() -> None:
    """Startup warm-up: load and report the document; no model call."""
    try:
        from app.doc_context import document_bundle, document_stats, source_label

        stats = document_stats()
        bundle = document_bundle()
        logger.info(
            "Context warmup: %s — %d bytes, %d tokens%s; "
            "facts=%d nodes=%d notices=%d sources=%d",
            source_label(), stats.bytes_read, stats.tokens,
            " [TRUNCATED]" if (stats.truncated_bytes or stats.truncated_tokens) else "",
            len(bundle.fact_ids), len(bundle.node_ids), len(bundle.notice_ids),
            len(bundle.source_urls),
        )
    except Exception:
        logger.exception("Context warmup failed")
