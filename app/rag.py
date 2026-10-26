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
import re
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


#: Words that carry no retrieval signal when deciding whether the static
#: bundle already covers a question. Kept separate from the router's lists:
#: this is a novelty check, not a route.
_LIVE_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did can could would should "
    "i me my we our you your it this that these those of to in on at for from "
    "with by and or not no yes what which when where who whom how why about "
    "please tell show give any some latest new information details detail "
    "many much more most current available need want know".split()
)
_LIVE_TERM_RE = re.compile(r"[a-z0-9ऀ-ॿ઀-૿]{3,}")


def _live_terms(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        term for term in _LIVE_TERM_RE.findall((text or "").lower())
        if term not in _LIVE_STOPWORDS
    ))


def _text_terms(text: str) -> frozenset[str]:
    return frozenset(_LIVE_TERM_RE.findall((text or "").lower()))


def _covered(term: str, words: frozenset[str]) -> bool:
    """Cheap singular/plural match so "fees" does not count as a novel term."""
    if term in words:
        return True
    if term.endswith("ies") and term[:-3] + "y" in words:
        return True
    if term.endswith("s") and term[:-1] in words:
        return True
    return term + "s" in words


def _needs_live_site(query: str, bundle: ContextBundle) -> bool:
    """Whether a live website read can add anything this bundle lacks.

    The gate is deliberately narrow. Known routes (fee, notices, documents,
    dates, contact, syllabus, admissions, programme) are answered from the
    verified corpus; a live fetch there adds latency and prompt bulk without
    adding authority. Live retrieval is for the long tail: a router miss, a
    department question the static profile deliberately excludes, or a
    question carrying a term the corpus does not contain at all.
    """
    if not settings.LIVE_SITE_ENABLED:
        return False
    if (settings.PROGRAM_PROFILE or "").strip().lower() != "dspmu":
        # The live source is DSPMU-specific; a legacy or other rollback
        # must never receive DSPMU web text.
        return False
    if bundle.kind in ("boundary", "pg_boundary"):
        return False
    mode = (settings.LIVE_SITE_MODE or "novel").strip().lower()
    if mode in ("off", "never", "0", "false", "no"):
        return False
    if mode == "always":
        return True
    if bundle.kind in ("fallback", "department"):
        return True
    terms = _live_terms(query)
    if not terms:
        return False
    words = _text_terms(bundle.text)
    return any(not _covered(term, words) for term in terms)


def _augment_with_live(query: str, bundle: ContextBundle) -> ContextBundle:
    """Append request-time website text when the static bundle is short.

    Live retrieval is optional and never authoritative: on any failure the
    bundle is returned unchanged, so the static answer path is untouched.
    """
    if not _needs_live_site(query, bundle):
        return bundle
    from app.live_site import retrieve_live_context

    live = retrieve_live_context(query)
    if live is None:
        return bundle
    logger.info(
        "live site: appended %d page(s) for %r", len(live.urls), query
    )
    return ContextBundle(
        text=f"{bundle.text}\n\n{live.text}",
        fact_ids=bundle.fact_ids,
        node_ids=tuple(dict.fromkeys((*bundle.node_ids, "live_site"))),
        notice_ids=bundle.notice_ids,
        source_urls=tuple(dict.fromkeys((*bundle.source_urls, *live.urls))),
        valid_until=bundle.valid_until,
        kind=f"{bundle.kind}+live" if bundle.kind else "live",
    )


def _static_bundle_for_query(query: str = "") -> ContextBundle:
    """The existing static/live-augmented bundle, unchanged by shadow."""
    if settings.CONTEXT_MODE == "off":
        return ContextBundle(text="")
    if settings.CONTEXT_MODE == "site":
        try:
            from app.site_router import route_query

            return _augment_with_live(query, route_query(query))
        except Exception:  # noqa: BLE001 - a router fault must not drop the turn
            logger.exception("site router failed; using the full bundle")
    if settings.CONTEXT_MODE not in ("doc", "site", ""):
        logger.warning(
            "CONTEXT_MODE=%r is not supported after RAG removal; using doc",
            settings.CONTEXT_MODE,
        )
    from app.doc_context import document_bundle

    return _augment_with_live(query, document_bundle())


def _reply_channel(mode: str, channel: str | None) -> str:
    if channel:
        return channel
    if mode == "whatsapp":
        return "whatsapp"
    if mode in ("chat", "streamlit"):
        return "chat"
    return "voice"


def _nga_text_enabled(agentic_text: bool) -> bool:
    return bool(
        agentic_text
        and settings.NGA_ENABLED
        and (settings.NGA_MODE or "").strip().lower() == "agentic"
    )


def _nga_voice_enabled(mode: str, voice_fast: bool = False) -> bool:
    return bool(
        voice_fast
        and mode == "voice"
        and settings.NGA_ENABLED
        and (settings.NGA_MODE or "").strip().lower() in ("fast", "agentic")
    )


def _voice_bundle_sync(query: str) -> ContextBundle:
    """Static core cards plus the bounded NGA voice evidence.

    The static bundle is kept as the stable prefix and fallback. The NGA
    evidence is appended only when the fast path produced citations; any
    failure or empty evidence returns the static bundle unchanged.
    """
    static = context_bundle_for_query(query)
    try:
        from app.agent.voice import build_voice_bundle

        voice = build_voice_bundle(query)
    except Exception:  # noqa: BLE001 - voice must keep the static answer path
        logger.exception("voice fast path failed; using static bundle")
        return static
    if voice.bundle is None:
        logger.info(
            "voice fast path fallback: %s (%.1f ms)",
            voice.fallback_reason,
            voice.latency_ms,
        )
        return static
    text = (
        f"{static.text}\n\n{voice.bundle.text}"
        if static.text.strip() else voice.bundle.text
    )
    bundle = ContextBundle(
        text=text,
        fact_ids=static.fact_ids,
        node_ids=tuple(dict.fromkeys(
            (*static.node_ids, *voice.bundle.node_ids)
        )),
        notice_ids=static.notice_ids,
        source_urls=tuple(dict.fromkeys(
            (*static.source_urls, *voice.bundle.source_urls)
        )),
        citations=voice.bundle.citations,
        valid_until=static.valid_until,
        kind="voice_fast",
    )
    logger.info(
        "voice fast path: %s, %d evidence item(s), %.1f ms",
        voice.plan.intent.value if voice.plan else "?",
        len(voice.evidence.items) if voice.evidence else 0,
        voice.latency_ms,
    )
    return bundle


def _submit_shadow(query: str, bundle: ContextBundle) -> None:
    """Observe the turn with NGA shadow mode; never change the bundle."""
    if not (query or "").strip():
        return
    # Avoid importing/building the shadow pipeline on the answer path when it
    # is off (the default). Shadow must remain a zero-cost observer.
    if not (
        settings.NGA_ENABLED
        and (settings.NGA_MODE or "").strip().lower() == "shadow"
    ):
        return
    try:
        from app.agent.shadow import submit_shadow

        submit_shadow(query, bundle)
    except Exception:  # noqa: BLE001 - shadow must never drop a turn
        logger.exception("shadow submission failed")


def context_bundle_for_query(query: str = "") -> ContextBundle:
    """The bundle for one turn.

    ``doc`` (default) — the full configured prompt bundle.
    ``site`` — the deterministic selective router; it falls back to the full
    compact bundle when it has no confident route.
    ``off`` — an empty bundle (the system prompt gets the explicit marker).

    When live retrieval is enabled and the static bundle does not cover the
    question, a bounded live website block is appended (see
    ``app.live_site``). The caller's prompt and the guard receive the same
    combined text.

    When NGA shadow mode is enabled, the same query is also observed by the
    NGA planner/executor and written to the shadow log. The returned bundle is
    unchanged; shadow has no effect on the answer.
    """
    bundle = _static_bundle_for_query(query)
    _submit_shadow(query, bundle)
    return bundle


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
              language: str | None = None,
              voice_fast: bool = False,
              agentic_text: bool = False,
              channel: str | None = None,
              conversation_id: str | None = None,
              reference_sink: list | None = None) -> str | None:
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

    if _nga_text_enabled(agentic_text):
        try:
            from app.agent.text import (
                build_text_answer,
                render_text_answer,
                text_context_bundle,
            )

            result = build_text_answer(
                routing_query,
                history=history,
                language=language,
                conversation_id=conversation_id,
                channel=_reply_channel(mode, channel),
            )
        except Exception:  # noqa: BLE001 - fall back to the static path
            logger.exception("text agentic path failed; using static")
            result = None
        if result is not None:
            bundle = text_context_bundle(result)
            bind_context_bundle(bundle)
            if reference_sink is not None:
                reference_sink.extend(bundle.citations)
            logger.info("text agentic answer: status=%s", result.status)
            return render_text_answer(
                result, channel=_reply_channel(mode, channel)
            )

    if _nga_voice_enabled(mode, voice_fast):
        bundle = _voice_bundle_sync(routing_query)
    else:
        bundle = context_bundle_for_query(routing_query)
    context = bundle.text
    bind_context_bundle(bundle)
    if reference_sink is not None:
        reference_sink.extend(bundle.citations)
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
    voice_fast: bool = False,
    agentic_text: bool = False,
    channel: str | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[str | EngineCounters]:
    """Streamed OpenAI generation with the document in the SYSTEM turn."""
    from app.llm_backend import GenerationFailed, generate_stream

    if not question.strip():
        raise GenerationFailed("empty question")

    routing_query = retrieval_query or question
    boundary = _pg_boundary_answer(routing_query)
    if boundary is not None:
        # ``to_thread`` for the same reason as the main path below: bundle
        # assembly may read the live site, and a synchronous HTTP call here
        # would freeze the voice/chat event loop.
        boundary_bundle = await asyncio.to_thread(
            context_bundle_for_query, routing_query
        )
        bind_context_bundle(boundary_bundle)
        logger.info("PG programme/fee boundary answer (stream)")
        yield boundary
        return

    if _nga_text_enabled(agentic_text):
        try:
            from app.agent.text import (
                build_text_answer,
                render_text_answer,
                text_context_bundle,
            )

            result = await asyncio.to_thread(
                build_text_answer,
                routing_query,
                language=language,
                conversation_id=conversation_id,
                channel=_reply_channel(mode, channel),
            )
        except Exception:  # noqa: BLE001 - fall back to the static path
            logger.exception("text agentic path failed; using static")
            result = None
        if result is not None:
            bundle = text_context_bundle(result)
            bind_context_bundle(bundle)
            logger.info("text agentic answer (stream): status=%s", result.status)
            yield render_text_answer(
                result, channel=_reply_channel(mode, channel)
            )
            return

    # Build on a worker thread: the static path may perform a bounded live
    # read, and the NGA voice fast path executes local tools. The bundle is
    # bound back on THIS loop because a ContextVar write made in the worker is
    # discarded on return.
    if _nga_voice_enabled(mode, voice_fast):
        bundle = await asyncio.to_thread(_voice_bundle_sync, routing_query)
    else:
        bundle = await asyncio.to_thread(
            context_bundle_for_query, routing_query
        )
    context = bundle.text
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
