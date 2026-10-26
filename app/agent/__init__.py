"""Phase 4 planning, bounded execution and evidence assembly.

The agent package is deliberately separate from the channel adapters. It
plans against the read-only ``site.*`` tool contract, executes within budgets,
and assembles typed evidence with citations. It is not wired into the answer
path yet; ``NGA_ENABLED`` stays false.
"""

from app.agent.answer import (
    AnswerResult,
    SYSTEM_PROMPT,
    answer_from_execution,
    build_messages,
    generate_answer,
    render_answer,
)
from app.agent.evidence import (
    Citation,
    EvidenceBundle,
    EvidenceItem,
    assemble_evidence,
    render_citations,
    render_evidence_block,
)
from app.agent.executor import ExecutionResult, async_execute_plan, execute_plan
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan, PlanStep, SessionState, TurnEvent
from app.agent.planner import Planner, PlanError, plan_with_llm, validate_plan
from app.agent.session import ConversationMemory
from app.agent.shadow import (
    ShadowRunner,
    reset_shadow_runner,
    shadow_enabled,
    submit_shadow,
)
from app.agent.text import (
    build_text_answer,
    plan_text,
    render_text_answer,
    text_context_bundle,
)
from app.agent.verifier import Claim, VerificationResult, verify_answer
from app.agent.voice import VoiceContext, build_voice_bundle

__all__ = [
    "AnswerResult",
    "Citation",
    "Claim",
    "ConversationMemory",
    "EvidenceBundle",
    "EvidenceItem",
    "ExecutionResult",
    "FactStore",
    "Intent",
    "Plan",
    "PlanError",
    "PlanStep",
    "Planner",
    "SYSTEM_PROMPT",
    "SessionState",
    "ShadowRunner",
    "TurnEvent",
    "VerificationResult",
    "VoiceContext",
    "answer_from_execution",
    "assemble_evidence",
    "async_execute_plan",
    "build_messages",
    "build_text_answer",
    "build_voice_bundle",
    "execute_plan",
    "generate_answer",
    "plan_text",
    "plan_with_llm",
    "render_answer",
    "render_citations",
    "render_evidence_block",
    "render_text_answer",
    "reset_shadow_runner",
    "shadow_enabled",
    "submit_shadow",
    "text_context_bundle",
    "validate_plan",
    "verify_answer",
]
