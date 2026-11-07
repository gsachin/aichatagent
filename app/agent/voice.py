"""Voice fast path: deterministic plan, bounded tools, evidence only.

The live voice handler calls this from a worker thread. It never calls an LLM,
never fetches the web, and falls back to the configured static bundle when the
graph has no evidence or the deadline is hit. The caller combines the returned
evidence context with the stable static core cards.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.agent.evidence import (
    EvidenceBundle,
    assemble_evidence,
    render_evidence_block,
)
from app.agent.executor import ExecutionResult, execute_plan
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan
from app.agent.planner import Planner
from app.config import settings
from app.context_bundle import ContextBundle
from app.sitegraph.tools import SiteToolServer

logger = logging.getLogger("agent.voice")


@dataclass
class VoiceContext:
    bundle: ContextBundle | None
    plan: Plan | None
    execution: ExecutionResult | None
    evidence: EvidenceBundle | None
    fallback_reason: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.bundle.kind if self.bundle else None,
            "fallback_reason": self.fallback_reason,
            "latency_ms": round(self.latency_ms, 2),
            "plan": (
                {
                    "intent": self.plan.intent.value,
                    "steps": [
                        {"tool": step.tool, "args": step.args}
                        for step in self.plan.steps
                    ],
                }
                if self.plan else None
            ),
            "execution": (
                {
                    "status": self.execution.status,
                    "steps_used": self.execution.steps_used,
                    "errors": self.execution.errors,
                    "deadline_exceeded": self.execution.deadline_exceeded,
                }
                if self.execution else None
            ),
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }


def _record_voice_turn(
    server: SiteToolServer,
    conversation_id: str | None,
    query: str,
    *,
    plan: Plan,
    execution: ExecutionResult,
    evidence: EvidenceBundle,
    answer_status: str,
    channel: str,
    language: str | None,
) -> None:
    if not conversation_id or not settings.NGA_CONVERSATION_STATE_ENABLED:
        return
    try:
        from app.agent.session import ConversationMemory

        ConversationMemory(server.store, channel=channel).record(
            conversation_id,
            query,
            plan=plan,
            execution=execution,
            bundle=evidence,
            answer_status=answer_status,
            channel=channel,
            language=language,
        )
    except Exception:  # noqa: BLE001 - a voice turn must still answer
        logger.exception("voice turn event write failed")


def build_voice_bundle(
    query: str,
    *,
    graph_path: str | None = None,
    pii_guard: bool | None = None,
    deadline_ms: int | None = None,
    max_steps: int | None = None,
    max_chars: int | None = None,
    conversation_id: str | None = None,
    channel: str = "voice",
    language: str | None = None,
    clock=time.monotonic,
) -> VoiceContext:
    """Build live evidence for one voice turn, or return a fallback marker."""
    query = (query or "").strip()
    started = clock()
    if not query:
        return VoiceContext(
            bundle=None, plan=None, execution=None, evidence=None,
            fallback_reason="empty_query", latency_ms=0.0,
        )

    deadline = int(
        settings.NGA_VOICE_DEADLINE_MS if deadline_ms is None else deadline_ms
    )
    steps = int(
        settings.NGA_VOICE_MAX_STEPS if max_steps is None else max_steps
    )
    chars = int(
        settings.NGA_VOICE_MAX_CHARS if max_chars is None else max_chars
    )
    guard = (
        settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
    )
    path = graph_path or settings.serve_db_path
    state = None
    if conversation_id and settings.NGA_CONVERSATION_STATE_ENABLED:
        try:
            from app.agent.session import ConversationMemory
            from app.sitegraph.store import GraphStore

            state = ConversationMemory(
                GraphStore(path, busy_timeout_ms=5_000),
                channel=channel,
            ).load(
                conversation_id,
                channel=channel,
                language=language,
            )
            if language:
                state.language = language
        except Exception:  # noqa: BLE001 - state must not drop a voice turn
            logger.exception("voice conversation state load failed")

    try:
        plan = Planner().plan(query, state)
        if plan.intent is Intent.BOUNDARY or not plan.steps:
            return VoiceContext(
                bundle=None, plan=plan, execution=None, evidence=None,
                fallback_reason="boundary_or_empty_plan",
                latency_ms=(clock() - started) * 1000,
            )
        server = SiteToolServer(path, pii_guard=guard)
        execution = execute_plan(
            server, plan, max_steps=steps, deadline_ms=deadline
        )
        evidence = assemble_evidence(
            execution,
            query=query,
            facts=FactStore(),
            max_chars=chars,
            pii_guard=guard,
        )
        if not evidence.items:
            _record_voice_turn(
                server,
                conversation_id,
                query,
                plan=plan,
                execution=execution,
                evidence=evidence,
                answer_status="no_evidence",
                channel=channel,
                language=language,
            )
            return VoiceContext(
                bundle=None, plan=plan, execution=execution,
                evidence=evidence, fallback_reason="no_evidence",
                latency_ms=(clock() - started) * 1000,
            )
        text = render_evidence_block(evidence, max_chars=chars)
        if not text.strip():
            _record_voice_turn(
                server,
                conversation_id,
                query,
                plan=plan,
                execution=execution,
                evidence=evidence,
                answer_status="empty_evidence",
                channel=channel,
                language=language,
            )
            return VoiceContext(
                bundle=None, plan=plan, execution=execution,
                evidence=evidence, fallback_reason="empty_evidence",
                latency_ms=(clock() - started) * 1000,
            )
        node_ids = tuple(dict.fromkeys(
            ("nga_voice", *(
                item.node_id for item in evidence.items if item.node_id
            ))
        ))
        urls = tuple(dict.fromkeys(
            citation.url for citation in evidence.citations if citation.url
        ))
        bundle = ContextBundle(
            text=text,
            node_ids=node_ids,
            source_urls=urls,
            citations=tuple(
                citation.to_dict() for citation in evidence.citations
            ),
            kind="voice_evidence",
        )
        _record_voice_turn(
            server,
            conversation_id,
            query,
            plan=plan,
            execution=execution,
            evidence=evidence,
            answer_status="voice_fast",
            channel=channel,
            language=language,
        )
        return VoiceContext(
            bundle=bundle,
            plan=plan,
            execution=execution,
            evidence=evidence,
            latency_ms=(clock() - started) * 1000,
        )
    except Exception:  # noqa: BLE001 - voice must always have a static fallback
        logger.exception("voice fast path failed; using static bundle")
        return VoiceContext(
            bundle=None, plan=None, execution=None, evidence=None,
            fallback_reason="error",
            latency_ms=(clock() - started) * 1000,
        )


__all__ = ["VoiceContext", "build_voice_bundle"]
