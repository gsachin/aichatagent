"""Event-sourced conversation memory and follow-up planning.

Each turn is stored as a ``TurnEvent`` in ``nga_turn_event``. The next turn
loads the last event's typed state (topic node, programme, language) and the
recent transcript, then lets the planner resolve references such as
"what about its fee?" without stuffing the full history into the prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.agent.answer import AnswerResult
from app.agent.evidence import EvidenceBundle
from app.agent.executor import ExecutionResult
from app.agent.models import Plan, SessionState, TurnEvent
from app.agent.planner import Planner
from app.sitegraph.store import GraphStore


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConversationMemory:
    """Load, record and replay the typed state of one conversation."""

    def __init__(
        self,
        store: GraphStore,
        *,
        channel: str = "chat",
        history_limit: int = 6,
    ) -> None:
        self.store = store
        self.channel = channel
        self.history_limit = max(1, int(history_limit))

    def load(
        self,
        conversation_id: str,
        *,
        channel: str | None = None,
        language: str | None = None,
    ) -> SessionState:
        events = self.store.turn_events(
            conversation_id, limit=self.history_limit
        )
        last = events[-1] if events else None
        state = (last or {}).get("session_state") or {}
        recent: list[str] = []
        for event in events:
            recent.append(f"User: {event.get('question') or ''}")
            answer = (event.get("answer") or "").strip()
            if answer:
                recent.append(f"Assistant: {answer}")
            else:
                recent.append(
                    f"Assistant: ({event.get('answer_status') or 'no answer'})"
                )
        return SessionState(
            conversation_id=conversation_id,
            channel=(last or {}).get("channel") or channel or self.channel,
            language=(last or {}).get("language") or language,
            current_node_id=state.get("current_node_id"),
            current_node_title=state.get("current_node_title"),
            programme=state.get("programme"),
            recent_turns=recent,
        )

    def plan(
        self,
        conversation_id: str,
        query: str,
        *,
        channel: str | None = None,
        language: str | None = None,
    ) -> tuple[SessionState, Plan]:
        state = self.load(
            conversation_id, channel=channel, language=language
        )
        return state, Planner().plan(query, state)

    def record(
        self,
        conversation_id: str,
        query: str,
        *,
        plan: Plan,
        execution: ExecutionResult,
        bundle: EvidenceBundle,
        answer_result: AnswerResult | None = None,
        channel: str | None = None,
        language: str | None = None,
        answer_status: str | None = None,
    ) -> TurnEvent:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        previous = self.load(
            conversation_id, channel=channel, language=language
        )
        topic = next(
            (
                item for item in bundle.items
                if item.authority != "verified_fact"
                and item.node_id
                and self.store.get_node(item.node_id) is not None
            ),
            None,
        )
        session_state = {
            "current_node_id": (
                topic.node_id if topic is not None else previous.current_node_id
            ),
            "current_node_title": (
                topic.title if topic is not None else previous.current_node_title
            ),
            "programme": (
                plan.filters.get("programme") or previous.programme
            ),
            "language": language or previous.language,
        }

        plan_args = [dict(step.args) for step in plan.steps]
        synthetic_args = [
            dict(item.get("args") or {})
            for item in execution.synthetic_steps
        ]
        tool_calls = []
        for index, result in enumerate(execution.results):
            if index < len(plan_args):
                args = plan_args[index]
            else:
                synthetic_index = index - len(plan_args)
                args = (
                    synthetic_args[synthetic_index]
                    if synthetic_index < len(synthetic_args) else {}
                )
            tool_calls.append(
                {
                    "tool": result.get("tool"),
                    "args": args,
                    "status": result.get("status"),
                    "latency_ms": result.get("latency_ms"),
                }
            )

        citations = (
            list(answer_result.citations)
            if answer_result is not None and answer_result.citations
            else [citation.to_dict() for citation in bundle.citations]
        )
        verified = bool(
            answer_result is not None
            and answer_result.verification is not None
            and answer_result.verification.verified
        )
        event = TurnEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            conversation_id=conversation_id,
            turn_id=self.store.next_turn_id(conversation_id),
            channel=channel or previous.channel or self.channel,
            language=language or previous.language,
            question=query,
            plan={
                "intent": plan.intent.value,
                "steps": [
                    {"tool": step.tool, "args": dict(step.args)}
                    for step in plan.steps
                ],
                "filters": plan.filters,
            },
            tool_calls=tuple(tool_calls),
            evidence_ids=tuple(item.evidence_id for item in bundle.items),
            citations=tuple(citations),
            answer=(answer_result.answer if answer_result else ""),
            answer_status=(
                answer_result.status if answer_result else (answer_status or "no_answer")
            ),
            verified=verified,
            latency={
                "execution_ms": round(execution.elapsed_ms, 2),
                "steps": execution.steps_used,
                "deadline_exceeded": execution.deadline_exceeded,
            },
            session_state=session_state,
            created_at=_now(),
        )
        self.store.append_turn_event(event)
        return event

    def replay(self, conversation_id: str, *, limit: int = 50) -> list[dict]:
        return self.store.turn_events(conversation_id, limit=limit)

    def purge_before(self, before_iso: str) -> int:
        return self.store.purge_turn_events(before_iso)

    def clear(self, conversation_id: str) -> int:
        return self.store.delete_turn_events(conversation_id)


__all__ = ["ConversationMemory"]
