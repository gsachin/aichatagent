"""Two-speed planner for the NGA tool contract.

The deterministic path is used for voice and as the fallback for text. It
classifies the question, resolves a follow-up against the typed session state,
and emits a small, schema-validated plan. The agentic path accepts a structured
JSON plan from an LLM callable; invalid output always falls back to the
deterministic planner.

No network call and no answer generation happens here.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

from app.agent.models import Intent, Plan, PlanStep, SessionState
from app.programs import DSPMU_PROGRAM_ALIASES, detect_program

logger = logging.getLogger("agent.planner")

ALLOWED_TOOLS = frozenset({
    "site.search",
    "site.navigate",
    "site.open",
    "site.latest",
    "site.documents",
    "site.freshness",
    "site.follow",
})
MAX_STEPS = 8

_FEE_WORDS = (
    "fee", "fees", "tuition", "cost", "price", "charge", "charges",
    "शुल्क", "फीस", "ફી", "tasas", "frais", "tasse", "tarifa", "mensalidade",
    "学费", "費用",
)
_NOTICE_WORDS = (
    "notice", "notices", "notification", "circular", "announcement",
    "convocation", "holiday", "timetable", "exam schedule",
    "examination programme", "admit card", "अधिसूचना", "सूचना", "પરિપત્ર",
    "સૂચના", "aviso", "annonce", "avviso", "お知らせ", "通知",
)
_DATE_WORDS = (
    "date", "dates", "deadline", "last date", "counselling", "counseling",
    "schedule", "calendar", "when", "तारीख", "कब", "समय", "તારીખ", "ક્યારે",
    "fecha", "fechas", "data", "scadenza", "日期", "締切",
)
_CONTACT_WORDS = (
    "contact", "phone", "email", "e-mail", "call", "address", "registrar",
    "helpline", "संपर्क", "फोन", "ईमेल", "पता", "સંપર્ક", "ફોન", "ઈમેલ",
    "સરનામું", "teléfono", "correo", "téléphone", "telefono", "電話", "电话",
)
_PROGRAMME_WORDS = (
    "programme", "program", "course", "courses", "eligibility", "duration",
    "degree", "कार्यक्रम", "कोर्स", "पात्रता", "अवधि", "કાર્યક્રમ", "કોર્સ",
    "પાત્રતા", "અવધિ",
)
_FOLLOWUP_RE = re.compile(
    r"\b(?:it|its|it's|that|this|there|same|above|former|latter)\b",
    re.IGNORECASE,
)
_FOLLOWUP_EXACT = frozenset({
    "more", "fee", "fees", "date", "dates", "contact", "notice", "notices",
    "details", "why", "how",
})


class PlanError(ValueError):
    """A plan that cannot be executed safely."""


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    for word in words:
        if re.search(
            rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])",
            text,
            re.IGNORECASE,
        ):
            return True
    return False


def classify_intent(query: str) -> Intent:
    """Deterministic intent for the fast path and the LLM fallback."""
    text = (query or "").strip().lower()
    if not text:
        return Intent.UNKNOWN
    try:
        from app.site_router import is_pg_data_question

        if is_pg_data_question(text):
            return Intent.BOUNDARY
    except Exception:  # noqa: BLE001 - boundary check must not drop a plan
        logger.exception("PG boundary check failed in planner")
    if _has_any(text, _FEE_WORDS):
        return Intent.FEE
    if _has_any(text, _NOTICE_WORDS):
        return Intent.NOTICE
    if _has_any(text, _DATE_WORDS):
        return Intent.DATE
    if _has_any(text, _CONTACT_WORDS):
        return Intent.CONTACT
    if _has_any(text, _PROGRAMME_WORDS):
        return Intent.PROGRAMME
    return Intent.PAGE_TOPIC


def _is_followup(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return bool(_FOLLOWUP_RE.search(text)) or text in _FOLLOWUP_EXACT


def _clean_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def validate_plan(plan: Plan, *, max_steps: int = MAX_STEPS) -> Plan:
    """Reject a plan that the executor must not run."""
    if not isinstance(plan, Plan):
        raise PlanError("plan must be a Plan")
    if not isinstance(plan.intent, Intent):
        try:
            plan = Plan(
                intent=Intent(plan.intent),
                steps=plan.steps,
                answer_language=plan.answer_language,
                filters=plan.filters,
            )
        except (TypeError, ValueError):
            raise PlanError(f"unknown intent {plan.intent!r}") from None
    if len(plan.steps) > max_steps:
        raise PlanError(f"plan has {len(plan.steps)} steps; max is {max_steps}")
    for index, step in enumerate(plan.steps):
        if not isinstance(step, PlanStep):
            raise PlanError(f"step {index} is not a PlanStep")
        if step.tool not in ALLOWED_TOOLS:
            raise PlanError(f"step {index} uses unknown tool {step.tool!r}")
        if not isinstance(step.args, dict):
            raise PlanError(f"step {index} args must be an object")
        if "url" in step.args:
            raise PlanError(f"step {index} may not pass a URL")
        try:
            json.dumps(step.args)
        except (TypeError, ValueError):
            raise PlanError(f"step {index} args are not JSON-serializable") from None
        if step.tool == "site.search":
            query = step.args.get("query")
            if not isinstance(query, str) or not query.strip():
                raise PlanError(f"step {index} site.search needs a query")
        if step.tool in ("site.open", "site.documents", "site.freshness"):
            if not step.args.get("node_id"):
                raise PlanError(f"step {index} {step.tool} needs node_id")
        if step.tool == "site.follow" and not step.args.get("edge_id"):
            raise PlanError(f"step {index} site.follow needs edge_id")
    return plan


class Planner:
    """Deterministic planner; the agentic variant is :func:`plan_with_llm`."""

    def plan(self, query: str, state: SessionState | None = None) -> Plan:
        text = _clean_query(query)
        state = state or SessionState()
        intent = classify_intent(text)
        language = state.language
        programme = (
            state.programme
            or detect_program(text.lower(), DSPMU_PROGRAM_ALIASES)
            or None
        )
        filters = {"programme": programme} if programme else {}

        if intent is Intent.BOUNDARY:
            return Plan(
                intent=intent,
                steps=(),
                answer_language=language,
                filters=filters,
            )

        if state.current_node_id and _is_followup(text):
            steps = [
                PlanStep(
                    "site.open",
                    {"node_id": state.current_node_id, "max_chars": 2000},
                )
            ]
            if intent in (Intent.FEE, Intent.DATE, Intent.NOTICE) or _has_any(
                text, _PROGRAMME_WORDS
            ):
                topic = state.current_node_title or ""
                steps.append(
                    PlanStep(
                        "site.search",
                        {"query": _clean_query(f"{topic} {text}"), "limit": 3},
                    )
                )
            return Plan(
                intent=intent,
                steps=tuple(steps),
                answer_language=language,
                filters=filters,
            )

        if intent is Intent.FEE:
            query_text = text
            if programme and programme.lower() not in text:
                query_text = f"{text} {programme}"
            return Plan(
                intent=intent,
                steps=(PlanStep("site.search", {"query": query_text, "limit": 3}),),
                answer_language=language,
                filters=filters,
            )
        if intent in (Intent.NOTICE, Intent.DATE):
            return Plan(
                intent=intent,
                steps=(PlanStep("site.latest", {"limit": 5}),),
                answer_language=language,
                filters=filters,
            )
        if intent is Intent.CONTACT:
            return Plan(
                intent=intent,
                steps=(
                    PlanStep(
                        "site.search", {"query": "contact registrar", "limit": 3}
                    ),
                ),
                answer_language=language,
                filters=filters,
            )
        if intent in (Intent.PROGRAMME, Intent.PAGE_TOPIC):
            return Plan(
                intent=intent,
                steps=(PlanStep("site.search", {"query": text, "limit": 3}),),
                answer_language=language,
                filters=filters,
            )
        return Plan(
            intent=Intent.UNKNOWN,
            steps=(),
            answer_language=language,
            filters=filters,
        )

    def plan_agentic(
        self,
        query: str,
        state: SessionState | None,
        llm: Callable[[str], str],
    ) -> Plan:
        return plan_with_llm(query, state, llm, fallback=self)


_PLAN_PROMPT = """You are the DSPMU site navigation planner.
Return ONLY a JSON object with this shape:
{{
  "intent": "fee|date|notice|contact|programme|page_topic|boundary|unknown",
  "steps": [{{"tool": "site.search|site.navigate|site.open|site.latest|site.documents|site.freshness|site.follow", "args": {{...}}}}],
  "answer_language": "en|hi|gu" or null
}}
Rules:
- maximum 8 steps
- never pass a URL; use node IDs returned by tools
- prefer verified facts for fees and dates
- stop when the evidence is enough; do not explore for its own sake
Question: {query}
Session topic: {topic}
Session programme: {programme}
"""


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def plan_with_llm(
    query: str,
    state: SessionState | None,
    llm: Callable[[str], str],
    *,
    fallback: Planner | None = None,
) -> Plan:
    """Ask an LLM for a structured plan; fall back deterministically on error."""
    state = state or SessionState()
    fallback = fallback or Planner()
    prompt = _PLAN_PROMPT.format(
        query=_clean_query(query),
        topic=state.current_node_title or "(none)",
        programme=state.programme or "(unknown)",
    )
    try:
        raw = llm(prompt)
        data = json.loads(_strip_code_fence(raw))
        if not isinstance(data, dict):
            raise PlanError("plan response is not an object")
        intent = Intent(str(data.get("intent") or "unknown"))
        steps = tuple(
            PlanStep(
                tool=str(item.get("tool") or ""),
                args=item.get("args") if isinstance(item.get("args"), dict) else {},
            )
            for item in (data.get("steps") or [])
            if isinstance(item, dict)
        )
        plan = validate_plan(
            Plan(
                intent=intent,
                steps=steps,
                answer_language=(
                    str(data.get("answer_language"))
                    if data.get("answer_language") else None
                ),
                filters=(
                    data.get("filters")
                    if isinstance(data.get("filters"), dict) else {}
                ),
            )
        )
        return plan
    except Exception:  # noqa: BLE001 - a bad plan must not drop the turn
        logger.info("agentic plan invalid; using deterministic planner")
        return fallback.plan(query, state)
