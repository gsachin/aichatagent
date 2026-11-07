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
    "site.browse",
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
# A notice/date question with a topic should search the content index, not
# only list the newest notices. "Latest notice" itself stays latest-only.
_NOTICE_SEARCH_WORDS = (
    "exam", "examination", "schedule", "timetable", "admit card", "result",
    "admission", "fee", "fees", "programme", "program", "course", "courses",
    "scholarship", "hostel", "convocation", "holiday", "syllabus", "placement",
    "seat", "seats", "eligibility", "registration",
)
_WORD_REPLACEMENTS = {
    "scheduled": "schedule",
    "schedules": "schedule",
    "exams": "exam",
    "examinations": "examination",
    "timetables": "timetable",
    "timetabled": "timetable",
    # The site uses British "programme"; users routinely type the American
    # plural. Normalise both before the word-boundary intent match.
    "programs": "programme",
    "programmes": "programme",
    "courses": "course",
}
_PROGRAMME_LIST_MARKERS = re.compile(
    r"\b(?:list|all|which|what|offer|offered|available|programs?|"
    r"programmes?|courses?)\b",
    re.IGNORECASE,
)
_NAVIGATE_WORDS = (
    "department", "departments", "faculty", "faculties", "section",
    "sections", "student life", "syllabus", "what facilities",
    "what is under", "list of departments", "list of sections",
    "list of faculties", "list of facilities", "list of documents",
    "documents on", "documents under",
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


def _normalize_text(text: str) -> str:
    """Normalize spelling variants that defeat word-boundary intent matching.

    ``"exam scheduled"`` used to fall through to ``page_topic`` because the
    notice phrase is ``"exam schedule"``. ``"up coming"`` had the same effect.
    The normalized form is used for intent, search and session matching; the
    original text is retained in the plan/turn event.
    """
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    value = re.sub(r"\bup[\s-]+coming\b", "upcoming", value)
    for source, target in _WORD_REPLACEMENTS.items():
        value = re.sub(rf"\b{source}\b", target, value)
    return value


def _programme_mention(text: str):
    """The first level-aware programme mention, or None."""
    try:
        from app.programs import detect_program_mentions

        mentions = detect_program_mentions(text)
    except Exception:  # noqa: BLE001 - planning must not crash on the detector
        logger.exception("programme mention detection failed")
        return None
    return mentions[0] if mentions else None


def _is_programme_list_query(text: str) -> bool:
    """True for a broad "which programmes/courses are offered" question.

    A specific programme mention ("BA History", "M.Sc. IT") is not a list
    query; it is answered from facts or the department page.
    """
    if not _has_any(text, _PROGRAMME_WORDS):
        return False
    if text.strip() in ("programme", "course"):
        return True
    if detect_program(text, DSPMU_PROGRAM_ALIASES):
        return False
    try:
        from app.programs import detect_program_mentions

        if detect_program_mentions(text):
            return False
    except Exception:  # noqa: BLE001 - list detection must not drop a plan
        logger.exception("programme mention check failed in planner")
    # "UG programmes" / "PG courses" are lists even without "list/what/which".
    if _programme_level(text) and _has_any(
        text, ("programme", "course")
    ):
        return True
    return bool(_PROGRAMME_LIST_MARKERS.search(text))


def _is_navigate_query(text: str) -> bool:
    """True for a section/department/document browsing request."""
    return _has_any(text, _NAVIGATE_WORDS)


def _programme_level(text: str) -> str | None:
    if re.search(r"\b(?:ph\.?\s?d|phd|doctorate|doctoral)\b", text, re.I):
        return "PhD"
    if re.search(
        r"\b(?:pg|post\s?graduate|postgraduate|m\.?\s?sc|m\.?\s?a|"
        r"m\.?\s?com|mba|mca|ll\.?\s?m|m\.?\s?ed|mha)\b",
        text,
        re.I,
    ):
        return "PG"
    if re.search(
        r"\b(?:ug|under\s?graduate|undergraduate|b\.?\s?sc|b\.?\s?a|"
        r"b\.?\s?com|bba|bca|b\.?\s?ed|b\.?\s?f\.?\s?sc|b\.?\s?j\.?\s?m\.?\s?c)\b",
        text,
        re.I,
    ):
        return "UG"
    if re.search(r"\bdiploma\b", text, re.I):
        return "Diploma"
    if re.search(r"\bcertificate\b", text, re.I):
        return "Certificate"
    return None


def classify_intent(query: str) -> Intent:
    """Deterministic intent for the fast path and the LLM fallback."""
    text = _normalize_text(query)
    if not text:
        return Intent.UNKNOWN
    # A PG *programme list* is answerable; the PG fee/seat boundary below is
    # only about hard data. The list intent therefore wins over the boundary.
    if _is_programme_list_query(text):
        return Intent.PROGRAMME_LIST
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
    if _is_navigate_query(text):
        return Intent.NAVIGATE
    if _has_any(text, _PROGRAMME_WORDS):
        return Intent.PROGRAMME
    try:
        from app.programs import detect_program_mentions

        if detect_program_mentions(text):
            return Intent.PROGRAMME
    except Exception:  # noqa: BLE001 - intent must not crash a turn
        logger.exception("programme mention check failed in intent")
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
        if step.tool in ("site.search", "site.browse"):
            query = step.args.get("query")
            if not isinstance(query, str) or not query.strip():
                raise PlanError(f"step {index} {step.tool} needs a query")
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
        normalized = _normalize_text(text)
        state = state or SessionState()
        intent = classify_intent(normalized)
        language = state.language
        mention = _programme_mention(normalized)
        programme = (
            mention.raw if mention is not None
            else (
                state.programme
                or detect_program(normalized, DSPMU_PROGRAM_ALIASES)
                or None
            )
        )
        level = mention.level if mention is not None else None
        filters = {"programme": programme} if programme else {}

        if intent is Intent.BOUNDARY:
            return Plan(
                intent=intent,
                steps=(),
                answer_language=language,
                filters=filters,
            )

        if intent is Intent.PROGRAMME_LIST:
            list_filters = dict(filters)
            level = _programme_level(normalized)
            if level:
                list_filters["level"] = level
            return Plan(
                intent=intent,
                steps=(),
                answer_language=language,
                filters=list_filters,
            )

        if state.current_node_id and _is_followup(normalized):
            steps = [
                PlanStep(
                    "site.open",
                    {"node_id": state.current_node_id, "max_chars": 2000},
                )
            ]
            if intent in (Intent.FEE, Intent.DATE, Intent.NOTICE) or _has_any(
                normalized, _PROGRAMME_WORDS
            ):
                topic = state.current_node_title or ""
                steps.append(
                    PlanStep(
                        "site.search",
                        {"query": _clean_query(f"{topic} {normalized}"), "limit": 3},
                    )
                )
            # The follow-up is anchored to the current topic. Put the topic in
            # the plan filters so the answer cache key can distinguish
            # "its fee" after a hostel page from "its fee" after a grievance
            # page.
            follow_filters = dict(filters)
            follow_filters["topic_node_id"] = state.current_node_id
            return Plan(
                intent=intent,
                steps=tuple(steps),
                answer_language=language,
                filters=follow_filters,
            )

        if intent is Intent.FEE:
            query_text = normalized
            if programme and programme.lower() not in normalized:
                query_text = f"{normalized} {programme}"
            return Plan(
                intent=intent,
                steps=(PlanStep("site.search", {"query": query_text, "limit": 3}),),
                answer_language=language,
                filters=filters,
            )
        if intent in (Intent.NOTICE, Intent.DATE):
            steps = [PlanStep("site.latest", {"limit": 5})]
            # A topic-bearing notice/date question ("exam schedule",
            # "admission last date") also searches the content index. The
            # newest-five list alone cannot reach an older schedule PDF.
            if _has_any(normalized, _NOTICE_SEARCH_WORDS):
                steps.append(
                    PlanStep("site.search", {"query": normalized, "limit": 3})
                )
            return Plan(
                intent=intent,
                steps=tuple(steps),
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
        if intent is Intent.NAVIGATE:
            return Plan(
                intent=intent,
                steps=(
                    PlanStep(
                        "site.browse", {"query": normalized, "limit": 5}
                    ),
                ),
                answer_language=language,
                filters=filters,
            )
        if intent in (Intent.PROGRAMME, Intent.PAGE_TOPIC):
            search_args: dict = {"query": normalized, "limit": 3}
            if intent is Intent.PROGRAMME and level in ("UG", "PG", "PhD"):
                # Keep a PG programme from being crowded out by a UG page that
                # happens to share the subject after token normalization.
                search_args["level"] = level
            return Plan(
                intent=intent,
                steps=(PlanStep("site.search", search_args),),
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
