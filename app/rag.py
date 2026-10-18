"""Prompt assembly and generation for document-context chat/voice.

RAG has been removed: there is no Chroma, no embedding call, no MCP retrieval
and no similarity gate. The full configured knowledge base
(``app.doc_context``) is passed to OpenAI on every turn. ``CONTEXT_MODE=off``
remains as a test/rollback switch that sends the explicit no-context marker.

The public functions (``query_rag``, ``query_rag_stream``,
``last_retrieved_context``) keep their names because the voice handler and the
text/WhatsApp surfaces call them; ``last_retrieved_context`` now returns the
injected document, which is what the grounding boundary checks against.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import AsyncIterator

from app.config import settings
from app.llm_backend import EngineCounters

logger = logging.getLogger("rag_module")

#: The document injected in the most recent turn, per async context. Two
#: concurrent callers must not see each other's evidence.
_LAST_CONTEXT: ContextVar[str] = ContextVar("last_retrieved_context", default="")

#: Chat-surface prompt (Markdown-oriented). Voice uses
#: ``app.voice_system_prompt.build_voice_system_prompt`` instead.
SYSTEM_PROMPT = (
    "You are a helpful University Admissions Advisor for Meridian University. "
    "Answer questions using the provided university profile context.\n\n"
    "RULES:\n"
    "1. Use facts from the context. When the context has relevant data, "
    "present it clearly — use Markdown for tables and figures.\n"
    "2. NEVER invent numbers, fees, URLs, or program names. "
    "Only cite dollar amounts and figures that appear in the context.\n"
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
    """The document injected for THIS turn ("" when ``CONTEXT_MODE=off``)."""
    return _LAST_CONTEXT.get()


def context_for_query(query: str = "") -> str:
    """The context for one turn.

    ``doc`` (default) — the full configured document.
    ``off``          — no context (the system prompt gets the explicit marker).
    """
    if settings.CONTEXT_MODE == "off":
        return ""
    if settings.CONTEXT_MODE not in ("doc", ""):
        logger.warning(
            "CONTEXT_MODE=%r is not supported after RAG removal; using doc",
            settings.CONTEXT_MODE,
        )
    from app.doc_context import document_context

    return document_context()


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

    ``retrieval_query`` is retained for call-site compatibility; there is no
    retriever any more.
    """
    if not question.strip():
        return None

    context = context_for_query(question)
    _LAST_CONTEXT.set(context)
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

    context = context_for_query(question)
    # Set on the loop so the grounding boundary sees it (the old to_thread
    # retrieval seam discarded context-var writes made in the worker).
    _LAST_CONTEXT.set(context)
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
        from app.doc_context import document_stats, source_label

        stats = document_stats()
        logger.info(
            "Context warmup: %s — %d bytes, %d tokens%s",
            source_label(), stats.bytes_read, stats.tokens,
            " [TRUNCATED]" if (stats.truncated_bytes or stats.truncated_tokens) else "",
        )
    except Exception:
        logger.exception("Context warmup failed")
