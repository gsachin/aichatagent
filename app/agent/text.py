"""Text agentic path: bounded tools, evidence, answer and verification.

Chat/WhatsApp can use this path when `NGA_ENABLED=true` and
`NGA_MODE=agentic`. It plans deterministically by default (an optional LLM
planner is available), executes at most eight local tool calls, assembles the
evidence bundle, generates an answer, and returns only what the verifier
accepts.

The rendering functions are used by the `app.rag` seam. This module does not
own a channel and does not record events.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.agent.answer import (
    AnswerResult,
    answer_from_execution,
    no_evidence_answer,
    render_answer,
)
from app.agent.evidence import assemble_evidence, render_evidence_block
from app.agent.executor import execute_plan
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan, SessionState
from app.agent.planner import Planner, plan_with_llm
from app.config import settings
from app.context_bundle import ContextBundle
from app.sitegraph.tools import SiteToolServer

logger = logging.getLogger("agent.text")


def _default_planner_llm(prompt: str) -> str:
    from app.llm_backend import _messages, chat

    return chat(
        messages=_messages(None, prompt),
        num_ctx=8192,
        temperature=0.0,
        json_mode=True,
    )


def plan_text(
    query: str,
    *,
    language: str | None = None,
    planner_llm: Callable[[str], str] | None = None,
    use_llm_planner: bool | None = None,
) -> Plan:
    """Deterministic plan by default; structured LLM plan when enabled."""
    use_llm = (
        settings.NGA_TEXT_LLM_PLANNER
        if use_llm_planner is None else use_llm_planner
    )
    state = SessionState(language=language)
    if planner_llm is not None or use_llm:
        return plan_with_llm(
            query,
            state,
            planner_llm or _default_planner_llm,
            fallback=Planner(),
        )
    return Planner().plan(query, state)


def build_text_answer(
    query: str,
    *,
    graph_path: str | None = None,
    pii_guard: bool | None = None,
    deadline_ms: int | None = None,
    max_steps: int | None = None,
    max_chars: int | None = None,
    llm: Callable[[list[dict]], str] | None = None,
    planner_llm: Callable[[str], str] | None = None,
    use_llm_planner: bool | None = None,
    history: list[str] | None = None,
    language: str | None = None,
    facts: FactStore | None = None,
    conversation_id: str | None = None,
    channel: str = "chat",
) -> AnswerResult | None:
    """Return a verified answer, or ``None`` when the static path should run."""
    query = (query or "").strip()
    if not query:
        return None
    deadline = int(
        settings.NGA_TEXT_DEADLINE_MS if deadline_ms is None else deadline_ms
    )
    steps = int(
        settings.NGA_TEXT_MAX_STEPS if max_steps is None else max_steps
    )
    chars = int(
        settings.NGA_TEXT_MAX_CHARS if max_chars is None else max_chars
    )
    guard = (
        settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
    )
    path = graph_path or settings.NGA_GRAPH_SQLITE_PATH
    try:
        plan = plan_text(
            query,
            language=language,
            planner_llm=planner_llm,
            use_llm_planner=use_llm_planner,
        )
        if plan.intent is Intent.BOUNDARY or not plan.steps:
            return None
        server = SiteToolServer(path, pii_guard=guard)
        execution = execute_plan(
            server,
            plan,
            max_steps=steps,
            deadline_ms=deadline,
            max_bytes=max(chars * 4, 4000),
        )
        bundle = assemble_evidence(
            execution,
            query=query,
            facts=facts or FactStore(),
            max_chars=chars,
            pii_guard=guard,
        )
        if not bundle.items:
            logger.info("text agentic: no evidence; using static path")
            return None
        result = answer_from_execution(
            execution,
            query=query,
            facts=facts or FactStore(),
            llm=llm,
            history=history,
            language=language,
            pii_guard=guard,
        )
        if conversation_id and result.plan and result.execution:
            try:
                from app.agent.session import ConversationMemory

                ConversationMemory(
                    server.store, channel=channel
                ).record(
                    conversation_id,
                    query,
                    plan=result.plan,
                    execution=result.execution,
                    bundle=result.evidence,
                    answer_result=result,
                    channel=channel,
                    language=language,
                )
            except Exception:  # noqa: BLE001 - the turn must still answer
                logger.exception("text agentic turn event write failed")
        return result
    except Exception:  # noqa: BLE001 - the static answer path is the fallback
        logger.exception("text agentic path failed; using static path")
        return None


def render_text_answer(
    result: AnswerResult | None,
    *,
    facts: FactStore | None = None,
    channel: str = "chat",
) -> str:
    """Render a verified answer, citations, or a safe refusal."""
    if result is None:
        return ""
    if result.status in ("verified", "rewritten"):
        return render_answer(result, channel=channel)
    if result.status == "no_evidence":
        return result.answer
    # A blocked or failed generation must not release an unverified answer.
    return no_evidence_answer(facts or FactStore())


def text_context_bundle(result: AnswerResult) -> ContextBundle:
    """The evidence bundle used for the guard and turn logs."""
    if result.evidence is None:
        return ContextBundle(text="", kind="text_agentic")
    text = render_evidence_block(result.evidence)
    node_ids = tuple(dict.fromkeys(
        ("text_agentic", *(
            item.node_id for item in result.evidence.items if item.node_id
        ))
    ))
    urls = tuple(dict.fromkeys(
        citation.url for citation in result.evidence.citations if citation.url
    ))
    return ContextBundle(
        text=text,
        node_ids=node_ids,
        source_urls=urls,
        citations=tuple(
            citation.to_dict() for citation in result.evidence.citations
        ),
        kind="text_agentic",
    )


__all__ = [
    "build_text_answer",
    "plan_text",
    "render_text_answer",
    "text_context_bundle",
]
