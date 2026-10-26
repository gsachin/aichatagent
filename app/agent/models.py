"""Plan and session records shared by the planner and executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    FEE = "fee"
    DATE = "date"
    NOTICE = "notice"
    CONTACT = "contact"
    PROGRAMME = "programme"
    PAGE_TOPIC = "page_topic"
    BOUNDARY = "boundary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlanStep:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    intent: Intent
    steps: tuple[PlanStep, ...] = ()
    answer_language: str | None = None
    filters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TurnEvent:
    """The durable record of one planned/answered turn."""

    event_id: str
    conversation_id: str
    turn_id: int
    channel: str
    language: str | None
    question: str
    plan: dict
    tool_calls: tuple[dict, ...]
    evidence_ids: tuple[str, ...]
    citations: tuple[dict, ...]
    answer: str
    answer_status: str
    verified: bool
    latency: dict
    session_state: dict
    created_at: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "channel": self.channel,
            "language": self.language,
            "question": self.question,
            "plan": self.plan,
            "tool_calls": list(self.tool_calls),
            "evidence_ids": list(self.evidence_ids),
            "citations": list(self.citations),
            "answer": self.answer,
            "answer_status": self.answer_status,
            "verified": self.verified,
            "latency": self.latency,
            "session_state": self.session_state,
            "created_at": self.created_at,
        }


@dataclass
class SessionState:
    """The typed part of a conversation the planner may use.

    Full transcript history remains in the existing conversation store; this
    object carries only the current topic and language for the next plan.
    """

    conversation_id: str = ""
    channel: str = ""
    language: str | None = None
    current_node_id: str | None = None
    current_node_title: str | None = None
    programme: str | None = None
    recent_turns: list[str] = field(default_factory=list)
