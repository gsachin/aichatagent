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

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.agent.adequacy import assess_bundle, deep_search_query
from app.agent.conversation import conversational_reply
from app.agent.answer import (
    AnswerResult,
    answer_from_execution,
    blocked_answer,
    generation_failed_answer,
    no_evidence_answer,
    render_answer,
)
from app.agent.evidence import (
    Citation,
    EvidenceBundle,
    EvidenceItem,
    assemble_evidence,
    render_evidence_block,
)
from app.agent.executor import ExecutionResult, execute_plan
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan, PlanStep, SessionState
from app.agent.planner import Planner, plan_with_llm
from app.agent.time_scope import parse_time_scope, scope_covers
from app.agent.verifier import VerificationResult, verify_answer
from app.cache.answer_cache import (
    CacheEntry,
    RequestContext,
    cache_is_valid,
    cache_key,
    cache_ttl_seconds,
    coverage_from_text,
)
from app.cache.revalidate import revalidate_entry, should_revalidate
from app.config import settings
from app.context_bundle import ContextBundle
from app.deepsearch.service import (
    DeepSearchOptions,
    deep_search,
    deep_search_message,
    results_to_execution,
)
from app.sitegraph.fetch import SiteFetcher
from app.sitegraph.search import normalize_query
from app.sitegraph.tools import SiteToolServer

logger = logging.getLogger("agent.text")

#: Bump when answer composition or the evidence contract changes. Cached
#: answers from an older format must not survive the code change: the same
#: query can legitimately produce a different answer shape.
ANSWER_FORMAT_VERSION = "2026-10-31.7"

#: Keyboard-row fragments and consonant-only runs are the signature of a
#: mashed query. They must not trigger a web-wide deep search.
_KEYBOARD_RUN_RE = re.compile(
    r"(?:qwerty|asdf|zxcv|qazwsx|wsxedc|edcrfv|rfvtgb|tgbyhn|yhnujm|ujmik)",
    re.IGNORECASE,
)
_CONSONANT_RUN_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}", re.IGNORECASE)


def _looks_like_noise(query: str) -> bool:
    tokens = normalize_query(query).tokens
    if not tokens:
        return True
    for token in tokens:
        if _KEYBOARD_RUN_RE.search(token):
            return True
        if len(token) >= 4 and not any(
            vowel in token for vowel in "aeiou"
        ):
            return True
        if _CONSONANT_RUN_RE.search(token):
            return True
    return False


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
    state: SessionState | None = None,
    planner_llm: Callable[[str], str] | None = None,
    use_llm_planner: bool | None = None,
) -> Plan:
    """Deterministic plan by default; structured LLM plan when enabled.

    When a conversation state is supplied, the planner resolves follow-ups and
    programme filters against it. A caller that does not have state still gets
    the standalone behaviour.
    """
    use_llm = (
        settings.NGA_TEXT_LLM_PLANNER
        if use_llm_planner is None else use_llm_planner
    )
    if state is None:
        state = SessionState(language=language)
    elif language:
        state.language = language
    if planner_llm is not None or use_llm:
        return plan_with_llm(
            query,
            state,
            planner_llm or _default_planner_llm,
            fallback=Planner(),
        )
    return Planner().plan(query, state)


def _cache_versions(store) -> tuple[str, str]:
    try:
        run = store.latest_run() or {}
    except Exception:  # noqa: BLE001 - a cache version must not drop a turn
        logger.exception("could not read the graph compile run")
        run = {}
    graph_hash = str(run.get("graph_hash") or "unknown")
    generated_at = str(run.get("generated_at") or "")
    # A cached answer is only valid for the index that produced it. The graph
    # run alone missed chunk/embedding changes, so a rebuilt chunk index could
    # keep serving a pre-index refusal for a day.
    try:
        chunk_count = store.chunk_count()
    except Exception:  # noqa: BLE001 - treat an unreadable count as changed
        logger.exception("could not read the chunk count")
        chunk_count = "unknown"
    embedding = (
        settings.EMBEDDING_MODEL
        if settings.EMBEDDING_PROVIDER not in ("", "off", "none")
        else "off"
    )
    index_version = (
        f"{graph_hash}:{generated_at}:chunks-{chunk_count}:emb-{embedding}"
    )
    policy_version = (
        f"pii-{int(settings.NGA_PII_GUARD_ENABLED)}:policy-v1:"
        f"answer-{ANSWER_FORMAT_VERSION}"
    )
    return index_version, policy_version


def _cache_key_for(
    query: str,
    plan: Plan,
    *,
    language: str | None,
    channel: str,
    index_version: str,
    policy_version: str,
):
    time_scope = parse_time_scope(query)
    programme = (plan.filters or {}).get("programme")
    topic = str((plan.filters or {}).get("topic_node_id") or "")
    normalized = normalize_query(query).text or query.strip().lower()
    if topic:
        # "its fee" after a hostel page is not the same cache entry as
        # "its fee" after a grievance page.
        normalized = f"topic:{topic}|{normalized}"
    key = cache_key(
        normalized_query=normalized,
        language=language or "",
        channel=channel,
        time_scope=time_scope,
        programme=programme,
        index_version=index_version,
        policy_version=policy_version,
    )
    return key, time_scope, programme, normalized


def _cached_answer_result(
    entry: CacheEntry,
    query: str,
    plan: Plan,
) -> AnswerResult:
    evidence = EvidenceBundle.from_dict(entry.evidence or {})
    if entry.verification:
        verification = VerificationResult.from_dict(entry.verification)
    else:
        verification = VerificationResult(
            answer=entry.answer,
            verified=entry.status in ("verified", "rewritten"),
            status=entry.status,
            citations=list(entry.citations),
            notes=[],
        )
    if "cache_hit" not in verification.notes:
        verification.notes.append("cache_hit")
    status = "no_evidence" if entry.status == "not_found" else entry.status
    return AnswerResult(
        query=query,
        answer=entry.answer,
        status=status,
        answer_type="refusal" if status == "no_evidence" else "answer",
        verification=verification,
        citations=(
            list(entry.citations)
            or [citation.to_dict() for citation in evidence.citations]
        ),
        evidence=evidence,
        plan=plan,
        execution=None,
        builder=entry.builder or None,
    )


def _record_turn_with_store(
    store,
    conversation_id: str | None,
    query: str,
    result: AnswerResult,
    *,
    execution: ExecutionResult | None = None,
    channel: str,
    language: str | None,
) -> None:
    if not conversation_id or result is None:
        return
    run = execution or result.execution
    if run is None or result.plan is None:
        return
    try:
        from app.agent.session import ConversationMemory

        ConversationMemory(store, channel=channel).record(
            conversation_id,
            query,
            plan=result.plan,
            execution=run,
            bundle=result.evidence,
            answer_result=result,
            channel=channel,
            language=language,
        )
    except Exception:  # noqa: BLE001 - the turn must still answer
        logger.exception("text agentic turn event write failed")


def _write_answer_cache(
    store,
    key: str,
    result: AnswerResult,
    *,
    query: str,
    time_scope,
    programme: str | None,
    channel: str,
    language: str | None,
    index_version: str,
    policy_version: str,
    now: datetime,
    normalized_query: str | None = None,
) -> None:
    try:
        normalized = (
            normalized_query
            or normalize_query(query).text
            or query.strip().lower()
        )
        common = {
            "cache_key": key,
            "normalized_query": normalized,
            "language": language or "",
            "channel": channel,
            "programme": programme,
            "index_version": index_version,
            "policy_version": policy_version,
        }
        if (
            result.status in ("verified", "rewritten")
            and result.answer_type == "answer"
        ):
            text = (
                render_evidence_block(result.evidence)
                if result.evidence is not None else result.answer
            )
            start, end, dates, unknown = coverage_from_text(text, now=now)
            entry = CacheEntry(
                status=result.status,
                expires_at=now
                + timedelta(seconds=cache_ttl_seconds(time_scope)),
                coverage_start=start,
                coverage_end=end,
                coverage_unknown=unknown,
                event_dates=dates,
                source_content_hash=hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                answer=result.answer,
                evidence=(
                    result.evidence.to_dict()
                    if result.evidence is not None else {}
                ),
                citations=tuple(result.citations or []),
                verification=(
                    result.verification.to_dict()
                    if result.verification is not None else {}
                ),
                builder=result.builder or "",
                **common,
            )
            store.put_answer_cache(
                entry.to_row(), created_at=now.isoformat()
            )
        elif result.status == "no_evidence":
            entry = CacheEntry(
                status="not_found",
                expires_at=now
                + timedelta(
                    seconds=cache_ttl_seconds(time_scope, negative=True)
                ),
                coverage_unknown=True,
                answer=result.answer,
                negative=True,
                **common,
            )
            store.put_answer_cache(
                entry.to_row(), created_at=now.isoformat()
            )
        else:
            # A verified refusal is still a non-answer. It must not become a
            # 24-hour cached fact; the next turn has to re-evaluate.
            logger.info(
                "not caching %s result (answer_type=%s)",
                result.status,
                result.answer_type,
            )
    except Exception:  # noqa: BLE001 - cache must never drop a turn
        logger.exception("answer cache write failed")


def _boundary_answer(
    query: str,
    plan: Plan,
) -> AnswerResult | None:
    """A deterministic refusal for a policy boundary owned by the text path.

    PG programme/fee/seat questions are a data boundary. Returning ``None``
    here would hand the turn to the static corpus, which is exactly the silent
    fallback the chat contract forbids. Other boundaries (for example a PII
    results query) keep the existing no-evidence refusal path.
    """
    try:
        from app.site_router import pg_boundary_answer

        answer = pg_boundary_answer(query)
    except Exception:  # noqa: BLE001 - a boundary must never crash a turn
        logger.exception("boundary answer failed")
        return None
    if not answer:
        return None
    execution = ExecutionResult(plan=plan)
    bundle = EvidenceBundle(query=query, intent=plan.intent)
    verification = VerificationResult(
        answer=answer,
        verified=True,
        status="blocked",
        notes=["pg_boundary"],
    )
    return AnswerResult(
        query=query,
        answer=answer,
        status="blocked",
        answer_type="refusal",
        verification=verification,
        citations=[],
        evidence=bundle,
        plan=plan,
        execution=execution,
        error="pg_boundary",
    )


def _handoff_result(
    query: str,
    plan: Plan,
    execution,
    item: dict,
    *,
    facts: FactStore,
    language: str | None,
) -> AnswerResult:
    """Return the official page URL when the matched page has no HTML text."""
    data = item.get("data") or {}
    node = data.get("node") or {}
    title = str(node.get("title") or "Official university page")
    url = str(node.get("url") or "")
    contacts = facts.contacts()
    email = ", ".join(contacts.get("emails") or [])
    phone = contacts.get("phone") or ""
    contact = " or ".join(part for part in (email, phone) if part)
    answer = (
        deep_search_message(language)
        + f"The matching official page is “{title}”: {url}. "
        "The page’s content is not published as text I can quote here. "
        "Please open the page"
    )
    if contact:
        answer += f" or contact the registrar: {contact}."
    else:
        answer += " or contact the registrar for the latest details."
    citation = Citation(
        node_id=str(node.get("node_id") or ""),
        title=title,
        url=url,
        snippet="Official page (content not published as text)",
        verified_at=None,
        authority=str(node.get("authority") or "official_page"),
    )
    bundle = EvidenceBundle(
        query=query,
        intent=plan.intent,
        citations=[citation],
        notes=["handoff:empty_shell"],
    )
    return AnswerResult(
        query=query,
        answer=answer,
        status="no_evidence",
        answer_type="refusal",
        citations=[citation.to_dict()],
        evidence=bundle,
        error="handoff:empty_shell",
        plan=plan,
        execution=execution,
    )


def _mark_deep_search_result(result, deep, search_query: str):
    if result is not None:
        result.deep_search = {
            "triggered": True,
            "status": str(getattr(deep, "status", "") or ""),
            "fetched": int(getattr(deep, "fetched", 0) or 0),
            "results": len(getattr(deep, "results", ()) or ()),
            "query": search_query,
        }
    return result


def _deep_search_answer(
    query: str,
    server,
    plan: Plan,
    *,
    language: str | None,
    channel: str,
    facts: FactStore,
    llm: Callable[[list[dict]], str] | None,
    history: list[str] | None,
    guard: bool,
    items: int,
    chars: int,
    key: str,
    normalized_query: str,
    time_scope,
    programme: str | None,
    index_version: str,
    policy_version: str,
) -> AnswerResult | None:
    try:
        hosts = tuple(
            host.strip()
            for host in settings.NGA_ALLOWED_HOSTS.split(",")
            if host.strip()
        )
        fetcher = SiteFetcher(
            hosts,
            user_agent=settings.NGA_FETCH_USER_AGENT,
            timeout_s=settings.NGA_FETCH_TIMEOUT_S,
            delay_s=settings.NGA_FETCH_DELAY_S,
            max_bytes=settings.NGA_MAX_BYTES,
            respect_robots=settings.NGA_ROBOTS_RESPECT,
        )
        # The site's own search is lexical. Strip conversational filler and
        # canonicalise programme/course spelling before asking it, or the
        # query "which programs currently university" matches About pages.
        search_query = deep_search_query(query)
        logger.info("deep search query: %r (from %r)", search_query, query)
        deep = deep_search(
            search_query,
            fetcher,
            options=DeepSearchOptions(
                base_url=settings.NGA_SITE_BASE_URL,
                allowed_hosts=hosts,
                max_results=settings.DEEP_SEARCH_MAX_RESULTS,
                max_fetches=settings.DEEP_SEARCH_MAX_FETCHES,
                max_bytes=settings.NGA_PDF_MAX_BYTES,
                max_pages=settings.NGA_PDF_MAX_PAGES,
                timeout_s=settings.DEEP_SEARCH_TIMEOUT_S,
                pii_guard=guard,
                ocr=False,
            ),
        )
        logger.info(
            "deep search: status=%s results=%d fetched=%d errors=%d",
            deep.status,
            len(deep.results),
            deep.fetched,
            len(deep.errors),
        )
        if not deep.results:
            return None
        execution = results_to_execution(deep, plan)
        if (deep.results[0].get("data") or {}).get("handoff"):
            return _mark_deep_search_result(
                _handoff_result(
                    query,
                    plan,
                    execution,
                    deep.results[0],
                    facts=facts,
                    language=language,
                ),
                deep,
                search_query,
            )

        # Deep search is not an excuse to answer from junk. A later result
        # (a tender PDF, an About page) must pass the same adequacy gate as
        # the indexed path.
        deep_bundle = assemble_evidence(
            execution,
            query=query,
            facts=facts,
            max_items=items,
            max_chars=chars,
            pii_guard=guard,
        )
        deep_assessment = assess_bundle(query, deep_bundle)
        if not deep_assessment.adequate:
            for item in deep.results:
                if (item.get("data") or {}).get("handoff"):
                    return _mark_deep_search_result(
                        _handoff_result(
                            query,
                            plan,
                            execution,
                            item,
                            facts=facts,
                            language=language,
                        ),
                        deep,
                        search_query,
                    )
            logger.info(
                "deep search evidence inadequate: %s",
                deep_assessment.reasons,
            )
            return None

        result = answer_from_execution(
            execution,
            query=query,
            facts=facts,
            llm=llm,
            history=history,
            language=language,
            pii_guard=guard,
            max_items=items,
            max_chars=chars,
        )
        if result.status in ("verified", "rewritten"):
            result.answer = deep_search_message(language) + result.answer
            if result.verification is not None:
                result.verification.notes.append("deep_search_used")
        _write_answer_cache(
            server.store,
            key,
            result,
            query=query,
            time_scope=time_scope,
            programme=programme,
            channel=channel,
            language=language,
            index_version=index_version,
            policy_version=policy_version,
            now=datetime.now(timezone.utc),
            normalized_query=normalized_query,
        )
        return _mark_deep_search_result(result, deep, search_query)
    except Exception:  # noqa: BLE001 - fall back to the normal refusal
        logger.exception("deep search failed")
        return None


def _scope_label(scope) -> str:
    if scope is None:
        return "the requested period"
    if scope.granularity == "month" and scope.start:
        return scope.start.strftime("%B %Y")
    if scope.granularity == "day" and scope.start:
        return scope.start.strftime("%d %B %Y")
    if scope.granularity == "year" and scope.start:
        return str(scope.start.year)
    return scope.label or "the requested period"


_ADMISSION_QUERY_RE = re.compile(
    r"\b(?:admission|admissions|apply|application|eligibility)\b",
    re.IGNORECASE,
)


def _failure_detail(failures) -> str:
    """Format verifier failures (strings or dicts) for a diagnostics detail."""
    parts: list[str] = []
    for failure in failures or ():
        if isinstance(failure, dict):
            parts.append(
                str(
                    failure.get("reason")
                    or failure.get("claim")
                    or failure
                )
            )
        else:
            parts.append(str(failure))
    return "; ".join(parts)


def _decline_builder(
    declines: list[dict] | None,
    builder: str,
    reason: str,
    *,
    detail: str | None = None,
) -> None:
    """Record why an *applicable* deterministic builder produced no answer.

    Callers pass a per-turn list so the reason reaches ``AnswerResult`` and the
    durable turn event. A builder that does not apply to the query returns
    ``None`` without calling this, keeping the diagnostics signal clean.
    """
    entry = {"builder": builder, "reason": reason}
    if detail:
        entry["detail"] = detail
    if declines is not None:
        declines.append(entry)
    logger.info(
        "deterministic %s answer declined: %s%s",
        builder,
        reason,
        f" ({detail})" if detail else "",
    )


def _admission_answer_result(
    query: str,
    plan: Plan,
    execution,
    bundle: EvidenceBundle,
    *,
    declines: list[dict] | None = None,
):
    """Answer the admissions-portal question from the signed-off fact."""
    if not _ADMISSION_QUERY_RE.search(query):
        return None
    item = next(
        (
            candidate for candidate in bundle.items
            if candidate.fact_id == "admissions.portal"
        ),
        None,
    )
    if item is None or not item.url:
        _decline_builder(
            declines, "admission", "missing_fact", detail="admissions.portal"
        )
        return None
    answer = (
        "Admissions at DSPMU are handled through the official portal: "
        f"{item.url}. Eligibility and the exact steps are programme-specific."
    )
    verification = verify_answer(f"{answer} [{item.evidence_id}]", bundle)
    if verification.status == "blocked":
        _decline_builder(
            declines,
            "admission",
            "verification_blocked",
            detail=_failure_detail(verification.failures),
        )
        return None
    return AnswerResult(
        query=query,
        answer=verification.answer,
        status=verification.status,
        answer_type="answer",
        verification=verification,
        citations=verification.citations,
        evidence=bundle,
        plan=plan,
        execution=execution,
        builder="admission",
    )


def _notice_answer_result(
    query: str,
    plan: Plan,
    execution,
    bundle: EvidenceBundle,
    *,
    facts: FactStore,
    declines: list[dict] | None = None,
) -> AnswerResult | None:
    """Deterministic notice/date answer, with an explicit time-scope check.

    A schedule is a list of official notices, not prose. Composing it from the
    evidence removes the LLM citation race that could block a correct notice
    answer, and a requested month with no matching notice becomes a labelled
    time-scope refusal instead of a confident wrong-month answer.
    """
    items = [
        item for item in bundle.items
        if item.authority != "verified_fact"
    ]
    if not items:
        _decline_builder(declines, "notice", "empty_bundle")
        return None
    request = parse_time_scope(query)
    if request is not None:
        covered = False
        for item in items:
            parsed = parse_time_scope(
                f"{item.title} {item.text[:2000]}"
            )
            if parsed is None:
                continue
            if request.granularity == "month":
                # A year or session label is not a month schedule. A notice
                # that says only "2025" cannot answer "March 2025".
                if parsed.granularity not in ("day", "month"):
                    continue
                if (
                    parsed.start is not None
                    and request.start <= parsed.start <= request.end
                ):
                    covered = True
                    break
            elif request.granularity == "day":
                if (
                    parsed.granularity == "day"
                    and parsed.start == request.start
                ):
                    covered = True
                    break
            elif scope_covers(parsed.start, parsed.end, request):
                covered = True
                break
        if not covered:
            latest = items[0]
            contacts = facts.contacts()
            email = ", ".join(contacts.get("emails") or [])
            phone = contacts.get("phone") or ""
            contact = " or ".join(part for part in (email, phone) if part)
            answer = (
                f"I could not find an official notice for "
                f"{_scope_label(request)}. The latest official notice I "
                f"found is “{latest.title}”"
            )
            if latest.url:
                answer += f": {latest.url}"
            answer += "."
            if contact:
                answer += (
                    f" For the exact schedule, contact the registrar: "
                    f"{contact}."
                )
            citation = Citation(
                node_id=latest.node_id,
                title=latest.title,
                url=latest.url,
                snippet=latest.snippet,
                verified_at=latest.verified_at,
                authority=latest.authority,
                fact_id=latest.fact_id,
            )
            return AnswerResult(
                query=query,
                answer=answer,
                status="no_evidence",
                answer_type="refusal",
                citations=[citation.to_dict()],
                evidence=bundle,
                error="time_scope_mismatch",
                plan=plan,
                execution=execution,
                builder="notice",
            )

    lines = ["Official notices I found:"]
    for item in items[:5]:
        lines.append(f"- {item.title} [{item.evidence_id}]")
        if item.url:
            lines.append(f"  {item.url}")
    answer = "\n".join(lines)
    verification = verify_answer(answer, bundle)
    if verification.status == "blocked":
        _decline_builder(
            declines,
            "notice",
            "verification_blocked",
            detail=_failure_detail(verification.failures),
        )
        return None
    return AnswerResult(
        query=query,
        answer=verification.answer,
        status=verification.status,
        answer_type="answer",
        verification=verification,
        citations=verification.citations,
        evidence=bundle,
        plan=plan,
        execution=execution,
        builder="notice",
    )


def _fee_fact_answer_result(
    query: str,
    plan: Plan,
    execution,
    bundle: EvidenceBundle,
    *,
    facts: FactStore,
    language: str | None,
    declines: list[dict] | None = None,
) -> AnswerResult | None:
    """A deterministic answer for a verified fee fact.

    Exact money is the one class where an LLM paraphrase adds risk without
    adding value. The fact item is already in the bundle, so the answer can be
    composed, cited and verified without generation.
    """
    matches = facts.fees_for_query(query)
    if not matches:
        _decline_builder(declines, "fee", "no_fact_for_query")
        return None
    fact = matches[0]
    fact_id = str(fact.get("fact_id") or "")
    item = next(
        (
            candidate for candidate in bundle.items
            if candidate.fact_id == fact_id
        ),
        None,
    )
    if item is None:
        _decline_builder(declines, "fee", "missing_fact", detail=fact_id)
        return None
    programme = str(fact.get("programme") or "Programme")
    gen = int(fact.get("annual_fee_gen") or 0)
    reserved = int(fact.get("annual_fee_reserved") or 0)
    semester = int(fact.get("semester_course_fee_gen") or 0)
    code = (language or "en").lower()
    if code.startswith("hi"):
        answer = (
            f"{programme} का वार्षिक शुल्क सामान्य वर्ग के लिए "
            f"₹{gen:,} और आरक्षित वर्ग (ST/SC/BC I & II) के लिए "
            f"₹{reserved:,} है।"
        )
        if semester:
            answer += f" सेमेस्टर कोर्स शुल्क ₹{semester:,} है।"
    elif code.startswith("gu"):
        answer = (
            f"{programme}ની વાર્ષિક ફી સામાન્ય વર્ગ માટે "
            f"₹{gen:,} અને અનામત વર્ગ (ST/SC/BC I & II) માટે "
            f"₹{reserved:,} છે."
        )
        if semester:
            answer += f" સેમેસ્ટર કોર્સ ફી ₹{semester:,} છે."
    else:
        answer = (
            f"{programme}: the annual fee is ₹{gen:,} (GEN) and "
            f"₹{reserved:,} (ST/SC/BC I & II)."
        )
        if semester:
            answer += f" The semester course fee is ₹{semester:,}."
    answer += f" [{item.evidence_id}]"
    valid_from = str(fact.get("valid_from") or "")
    valid_until = str(fact.get("valid_until") or "")
    if valid_from and valid_until:
        answer += f" Valid {valid_from} to {valid_until}."
    status = str(fact.get("status") or "")
    if status:
        answer += f" The fee record is marked {status}."
    if item.url:
        answer += f"\nSource: {item.url}"
    verification = verify_answer(answer, bundle)
    if verification.status == "blocked":
        _decline_builder(
            declines,
            "fee",
            "verification_blocked",
            detail=_failure_detail(verification.failures),
        )
        return None
    return AnswerResult(
        query=query,
        answer=verification.answer,
        status=verification.status,
        answer_type="answer",
        verification=verification,
        citations=verification.citations,
        evidence=bundle,
        plan=plan,
        execution=execution,
        builder="fee",
    )


def _browse_answer(
    query: str,
    plan: Plan,
    execution,
    result: dict,
    *,
    facts: FactStore,
) -> AnswerResult | None:
    """Compose a deterministic answer from a `site.browse` navigation result."""
    data = result.get("data") or {}
    node = data.get("node") or {}
    collection = data.get("collection") or {}
    children = data.get("children") or []
    documents = data.get("documents") or []
    siblings = data.get("siblings") or []
    links = data.get("links") or []
    title = str(
        collection.get("title")
        or node.get("title")
        or "university page"
    )
    url = str(node.get("url") or "")
    page_label = (
        title if title.lower().endswith("page") else f"{title} page"
    )

    if not (collection or children or documents or siblings or links):
        return None

    lines: list[str] = []
    if collection:
        count = int(collection.get("count") or len(children))
        collection_title = str(
            collection.get("title") or "Departments and Faculties"
        )
        lines.append(f"{collection_title} — {count}:")
        names = [str(item.get("title") or "") for item in children]
        for start in range(0, len(names), 6):
            lines.append(", ".join(names[start:start + 6]))
    else:
        # List what the page actually exposes. A shell is still a useful
        # navigation answer; never explain index internals to the visitor.
        if children:
            lines.append(
                f"Sections on the {page_label}: "
                + ", ".join(
                    str(item.get("title") or "") for item in children
                )
            )
        if documents:
            lines.append(
                f"Documents on the {page_label}: "
                + ", ".join(
                    str(item.get("title") or item.get("url") or "")
                    for item in documents
                )
            )
        if siblings and not documents:
            lines.append(
                f"Related pages from the {page_label}: "
                + ", ".join(
                    str(item.get("title") or "") for item in siblings[:20]
                )
            )
        if links and not (children or documents):
            lines.append(
                f"Links on the {page_label}: "
                + ", ".join(
                    str(item.get("title") or item.get("url") or "")
                    for item in links[:20]
                )
            )
        if url:
            lines.append(f"Official page: {url}")

    contacts = facts.contacts()
    email = ", ".join(contacts.get("emails") or [])
    phone = contacts.get("phone") or ""
    contact = " or ".join(part for part in (email, phone) if part)
    if contact:
        lines.append(f"For details not listed here, contact the registrar: {contact}.")

    answer = "\n".join(line for line in lines if line.strip())
    citations: list[Citation] = []
    seen_urls: set[str] = set()

    def add_citation(
        item: dict,
        authority: str,
        snippet: str,
    ) -> None:
        citation_url = str(item.get("url") or "")
        if not citation_url or citation_url in seen_urls:
            return
        seen_urls.add(citation_url)
        citations.append(
            Citation(
                node_id=str(item.get("node_id") or ""),
                title=str(item.get("title") or citation_url),
                url=citation_url,
                snippet=snippet,
                verified_at=item.get("verified_at"),
                authority=authority,
            )
        )

    if url:
        add_citation(
            {"node_id": node.get("node_id"), "title": title, "url": url,
             "verified_at": node.get("verified_at")},
            "official_page",
            "Navigation page",
        )
    for item in children:
        add_citation(item, "official_page", "Section")
    for item in documents:
        add_citation(item, "official_document", "Document")
    for item in siblings:
        add_citation(item, "official_page", "Related page")
    citations = citations[:10]

    citation_dicts = [citation.to_dict() for citation in citations]
    bundle = EvidenceBundle(
        query=query,
        intent=plan.intent,
        citations=citations,
        notes=["browse"],
    )
    verification = VerificationResult(
        answer=answer,
        verified=True,
        status="verified",
        citations=citation_dicts,
        notes=["browse"],
    )
    return AnswerResult(
        query=query,
        answer=answer,
        status="verified",
        answer_type="answer",
        verification=verification,
        citations=citation_dicts,
        evidence=bundle,
        plan=plan,
        execution=execution,
        builder="browse",
    )


_LEVEL_LABELS = {
    "UG": "Undergraduate (UG)",
    "PG": "Postgraduate (PG)",
    "PhD": "Doctoral (PhD)",
    "Diploma": "Diploma",
    "Certificate": "Certificate",
}

_LEVEL_LABELS_HI = {
    "UG": "स्नातक (UG)",
    "PG": "स्नातकोत्तर (PG)",
    "PhD": "डॉक्टरेट (PhD)",
    "Diploma": "डिप्लोमा",
    "Certificate": "प्रमाणपत्र",
}

_LEVEL_LABELS_GU = {
    "UG": "સ્નાતક (UG)",
    "PG": "અનુસ્નાતક (PG)",
    "PhD": "ડોક્ટરેટ (PhD)",
    "Diploma": "ડિપ્લોમા",
    "Certificate": "પ્રમાણપત્ર",
}


def _programme_answer_result(
    query: str,
    plan: Plan,
    execution,
    *,
    language: str | None,
    declines: list[dict] | None = None,
):
    """A deterministic answer for one named programme, or None.

    A programme-name question should not be answered by the LLM with an
    admission caveat that happens to contain the programme name. The catalogue
    already carries the level, department and official page.
    """
    if not settings.NGA_CATALOGUE_ENABLED:
        _decline_builder(declines, "programme", "disabled")
        return None
    try:
        from app.knowledge.catalogue import Catalogue
        from app.programs import detect_program_mentions

        catalogue = Catalogue.load(settings.NGA_CATALOGUE_PATH)
        match = catalogue.detect(query)
        mentions = detect_program_mentions(query)
    except Exception:  # noqa: BLE001 - fall back to retrieval on a bad catalogue
        logger.exception("programme answer lookup failed")
        _decline_builder(declines, "programme", "lookup_error")
        return None
    if match is None:
        _decline_builder(declines, "programme", "no_catalogue_match")
        return None
    if mentions and match.programme.level != mentions[0].level:
        _decline_builder(
            declines,
            "programme",
            "level_mismatch",
            detail=(
                f"{match.programme.name} is {match.programme.level}; "
                f"query mentions {mentions[0].level}"
            ),
        )
        return None
    programme = match.programme
    url = (
        programme.page_url
        or (programme.source_urls[0] if programme.source_urls else "")
    )
    if not url:
        _decline_builder(
            declines, "programme", "missing_url", detail=programme.name
        )
        return None
    label = _LEVEL_LABELS.get(programme.level, programme.level)
    code = (language or "en").lower()
    if code.startswith("hi"):
        answer_label = _LEVEL_LABELS_HI.get(programme.level, programme.level)
        answer = (
            f"DSPMU {programme.name} को {answer_label} स्तर पर "
            "प्रदान करता है"
        )
        if programme.department:
            answer += f" ({programme.department} विभाग में)"
        answer += f"। आधिकारिक कार्यक्रम पृष्ठ: {url}."
        if programme.status != "verified":
            answer += (
                f" कैटलॉग प्रविष्टि {programme.status} के रूप में चिह्नित "
                "है; विवरण के लिए विभाग से संपर्क करें।"
            )
    elif code.startswith("gu"):
        answer_label = _LEVEL_LABELS_GU.get(programme.level, programme.level)
        answer = (
            f"DSPMU {programme.name} ને {answer_label} સ્તર પર "
            "પ્રદાન કરે છે"
        )
        if programme.department:
            answer += f" ({programme.department} વિભાગમાં)"
        answer += f". અધિકૃત કાર્યક્રમ પૃષ્ઠ: {url}."
        if programme.status != "verified":
            answer += (
                f" કેટલોગ પ્રવેશ {programme.status} તરીકે ચિહ્નિત છે; "
                "વિગતો માટે વિભાગનો સંપર્ક કરો."
            )
    else:
        answer = (
            f"DSPMU offers {programme.name} at the {label} level"
        )
        if programme.department:
            answer += f" in the {programme.department}"
        answer += f". Official programme page: {url}."
        if programme.status != "verified":
            answer += (
                f" The catalogue entry is marked {programme.status}; "
                "confirm details with the department."
            )
    citation = Citation(
        node_id=programme.id,
        title=programme.name,
        url=url,
        snippet=f"{programme.name} ({label})",
        verified_at=programme.verified_at,
        authority="official_page",
    )
    item = EvidenceItem(
        evidence_id="ev-1",
        node_id=programme.id,
        title=programme.name,
        url=url,
        authority="official_page",
        verified_at=programme.verified_at,
        snippet=f"{programme.name} ({label})",
        text=(
            f"{programme.name} is offered at the {label} level"
            + (f" by the {programme.department}" if programme.department else "")
            + f". Official programme page: {url}."
        ),
        status=programme.status,
    )
    bundle = EvidenceBundle(
        query=query,
        intent=plan.intent,
        items=[item],
        citations=[citation],
        notes=["catalogue_programme"],
    )
    verification = verify_answer(f"{answer} [{item.evidence_id}]", bundle)
    if verification.status == "blocked":
        _decline_builder(
            declines,
            "programme",
            "verification_blocked",
            detail=_failure_detail(verification.failures),
        )
        return None
    return AnswerResult(
        query=query,
        answer=verification.answer,
        status=verification.status,
        answer_type="answer",
        verification=verification,
        citations=verification.citations,
        evidence=bundle,
        plan=plan,
        execution=execution,
        builder="programme",
    )


def _catalogue_answer(
    query: str,
    plan: Plan,
    server,
    *,
    conversation_id: str | None,
    channel: str,
    language: str | None,
    key: str,
    normalized_query: str,
    time_scope,
    programme: str | None,
    index_version: str,
    policy_version: str,
) -> AnswerResult | None:
    """Answer a programme-list question deterministically from the catalogue."""
    if not settings.NGA_CATALOGUE_ENABLED:
        return None
    level = (plan.filters or {}).get("level")
    if level == "PG" and not settings.NGA_PG_PROGRAMME_LISTS_ANSWERABLE:
        return None
    try:
        from app.knowledge.catalogue import Catalogue

        catalogue = Catalogue.load(settings.NGA_CATALOGUE_PATH)
    except Exception:  # noqa: BLE001 - fall back to retrieval/handoff
        logger.exception("catalogue load failed")
        return None
    if not catalogue.rows:
        if catalogue.errors:
            logger.info("catalogue unavailable: %s", catalogue.errors)
        return None

    text, citations, rows = catalogue.list_answer(
        level,
        include_draft=settings.NGA_CATALOGUE_INCLUDE_DRAFT,
    )
    if not text or not rows:
        return None

    citation_objects = [Citation.from_dict(item) for item in citations]
    bundle = EvidenceBundle(
        query=query,
        intent=plan.intent,
        citations=citation_objects,
        notes=["catalogue"],
    )
    verification = VerificationResult(
        answer=text,
        verified=True,
        status="verified",
        citations=list(citations),
        notes=["catalogue"],
    )
    result = AnswerResult(
        query=query,
        answer=text,
        status="verified",
        answer_type="answer",
        verification=verification,
        citations=list(citations),
        evidence=bundle,
        plan=plan,
        execution=ExecutionResult(plan=plan),
        builder="programme_list",
    )
    _write_answer_cache(
        server.store,
        key,
        result,
        query=query,
        time_scope=time_scope,
        programme=programme,
        channel=channel,
        language=language,
        index_version=index_version,
        policy_version=policy_version,
        now=datetime.now(timezone.utc),
        normalized_query=normalized_query,
    )
    _record_turn_with_store(
        server.store,
        conversation_id,
        query,
        result,
        execution=ExecutionResult(plan=plan),
        channel=channel,
        language=language,
    )
    return result


def build_text_answer(
    query: str,
    *,
    graph_path: str | None = None,
    pii_guard: bool | None = None,
    deadline_ms: int | None = None,
    max_steps: int | None = None,
    max_chars: int | None = None,
    max_items: int | None = None,
    llm: Callable[[list[dict]], str] | None = None,
    planner_llm: Callable[[str], str] | None = None,
    use_llm_planner: bool | None = None,
    history: list[str] | None = None,
    language: str | None = None,
    facts: FactStore | None = None,
    conversation_id: str | None = None,
    channel: str = "chat",
    use_fact_answer: bool = True,
) -> AnswerResult | None:
    """Return a verified answer, or ``None`` when the static path should run."""
    query = (query or "").strip()
    if not query:
        return None
    builder_declines: list[dict] = []
    deadline = int(
        settings.NGA_TEXT_DEADLINE_MS if deadline_ms is None else deadline_ms
    )
    steps = int(
        settings.NGA_TEXT_MAX_STEPS if max_steps is None else max_steps
    )
    chars = int(
        settings.NGA_TEXT_MAX_CHARS if max_chars is None else max_chars
    )
    items = int(
        settings.NGA_TEXT_MAX_ITEMS if max_items is None else max_items
    )
    guard = (
        settings.NGA_PII_GUARD_ENABLED if pii_guard is None else pii_guard
    )
    path = graph_path or settings.serve_db_path
    try:
        reply = conversational_reply(query)
        if reply is not None:
            conv_plan = Plan(intent=Intent.UNKNOWN, steps=())
            execution = ExecutionResult(plan=conv_plan)
            bundle = EvidenceBundle(query=query, intent=Intent.UNKNOWN)
            result = AnswerResult(
                query=query,
                answer=reply,
                status="conversational",
                answer_type="answer",
                citations=[],
                evidence=bundle,
                plan=conv_plan,
                execution=execution,
            )
            if conversation_id:
                try:
                    server = SiteToolServer(path, pii_guard=guard)
                    _record_turn_with_store(
                        server.store,
                        conversation_id,
                        query,
                        result,
                        execution=execution,
                        channel=channel,
                        language=language,
                    )
                except Exception:  # noqa: BLE001 - reply is already ready
                    logger.exception("conversational turn event write failed")
            return result
        server = SiteToolServer(path, pii_guard=guard)
        state: SessionState | None = None
        if conversation_id and settings.NGA_CONVERSATION_STATE_ENABLED:
            try:
                from app.agent.session import ConversationMemory

                state = ConversationMemory(
                    server.store, channel=channel
                ).load(
                    conversation_id,
                    channel=channel,
                    language=language,
                )
                # A language change in this turn wins over the stored one.
                if language:
                    state.language = language
            except Exception:  # noqa: BLE001 - state must not drop a turn
                logger.exception("conversation state load failed")
        effective_history = (
            state.recent_turns
            if state is not None and state.recent_turns
            else history
        )
        plan = plan_text(
            query,
            language=language,
            state=state,
            planner_llm=planner_llm,
            use_llm_planner=use_llm_planner,
        )
        index_version, policy_version = _cache_versions(server.store)
        if plan.intent is Intent.PROGRAMME_LIST:
            key, time_scope, programme, normalized_query = _cache_key_for(
                query,
                plan,
                language=language,
                channel=channel,
                index_version=index_version,
                policy_version=policy_version,
            )
            catalogue_result = _catalogue_answer(
                query,
                plan,
                server,
                conversation_id=conversation_id,
                channel=channel,
                language=language,
                key=key,
                normalized_query=normalized_query,
                time_scope=time_scope,
                programme=programme,
                index_version=index_version,
                policy_version=policy_version,
            )
            if catalogue_result is not None:
                return catalogue_result
            # No catalogue or no rows for the requested level: keep the
            # previous retrieval/hand-off path rather than returning nothing.
            plan = Plan(
                intent=Intent.PROGRAMME,
                steps=(
                    PlanStep(
                        "site.search",
                        {"query": query, "limit": 3},
                    ),
                ),
                answer_language=plan.answer_language,
                filters=plan.filters,
            )
        if plan.intent is Intent.PROGRAMME:
            key, time_scope, programme, normalized_query = _cache_key_for(
                query,
                plan,
                language=language,
                channel=channel,
                index_version=index_version,
                policy_version=policy_version,
            )
            programme_execution = ExecutionResult(plan=plan)
            programme_result = _programme_answer_result(
                query,
                plan,
                programme_execution,
                language=language,
                declines=builder_declines,
            )
            if programme_result is not None:
                programme_result.builder_declines = list(builder_declines)
                _write_answer_cache(
                    server.store,
                    key,
                    programme_result,
                    query=query,
                    time_scope=time_scope,
                    programme=programme,
                    channel=channel,
                    language=language,
                    index_version=index_version,
                    policy_version=policy_version,
                    now=datetime.now(timezone.utc),
                    normalized_query=normalized_query,
                )
                _record_turn_with_store(
                    server.store,
                    conversation_id,
                    query,
                    programme_result,
                    execution=programme_execution,
                    channel=channel,
                    language=language,
                )
                return programme_result
        if plan.intent is Intent.BOUNDARY:
            boundary_result = _boundary_answer(query, plan)
            if boundary_result is not None:
                _record_turn_with_store(
                    server.store,
                    conversation_id,
                    query,
                    boundary_result,
                    channel=channel,
                    language=language,
                )
                return boundary_result
            return None
        if not plan.steps:
            return None
        key, time_scope, programme, normalized_query = _cache_key_for(
            query,
            plan,
            language=language,
            channel=channel,
            index_version=index_version,
            policy_version=policy_version,
        )
        if (
            plan.intent in (Intent.UNKNOWN, Intent.PAGE_TOPIC)
            and _looks_like_noise(query)
        ):
            # A mashed query is not a page that search or deep search can
            # recover; searching the web only finds a random shell and turns a
            # nonsense turn into a confident hand-off.
            result = AnswerResult(
                query=query,
                answer=no_evidence_answer(facts or FactStore()),
                status="no_evidence",
                answer_type="refusal",
                citations=[],
                evidence=EvidenceBundle(
                    query=query, intent=plan.intent
                ),
                error="no_known_topic",
                plan=plan,
                execution=ExecutionResult(plan=plan),
            )
            _record_turn_with_store(
                server.store,
                conversation_id,
                query,
                result,
                channel=channel,
                language=language,
            )
            return result
        now = datetime.now(timezone.utc)
        revalidation_invalid = False
        cached = server.store.get_answer_cache(key)
        if cached:
            entry = CacheEntry.from_row(cached)
            request = RequestContext(
                time_scope=time_scope, programme=programme
            )
            if cache_is_valid(
                entry,
                request,
                now=now,
                index_version=index_version,
                policy_version=policy_version,
            ):
                revalidated = False
                if (
                    settings.CACHE_REVALIDATE_ENABLED
                    and channel != "voice"
                    and should_revalidate(
                        entry,
                        request,
                        now=now,
                        window_s=settings.CACHE_REVALIDATE_NEAR_EXPIRY_S,
                    )
                ):
                    try:
                        hosts = tuple(
                            host.strip()
                            for host in settings.NGA_ALLOWED_HOSTS.split(",")
                            if host.strip()
                        )
                        fetcher = SiteFetcher(
                            hosts,
                            user_agent=settings.NGA_FETCH_USER_AGENT,
                            timeout_s=settings.NGA_FETCH_TIMEOUT_S,
                            delay_s=settings.NGA_FETCH_DELAY_S,
                            max_bytes=settings.NGA_MAX_BYTES,
                            respect_robots=settings.NGA_ROBOTS_RESPECT,
                        )

                        def _state_lookup(url: str) -> dict:
                            node = server.store.get_node_by_url(url)
                            return (
                                server.store.fetch_state(node.node_id)
                                if node is not None else {}
                            )

                        revalidation = revalidate_entry(
                            entry,
                            fetcher,
                            now=now,
                            ttl_seconds=cache_ttl_seconds(time_scope),
                            state_lookup=_state_lookup,
                            max_urls=settings.CACHE_REVALIDATE_MAX_URLS,
                            max_bytes=settings.NGA_PDF_MAX_BYTES,
                        )
                    except Exception:  # noqa: BLE001 - cache must not drop a turn
                        logger.exception("cache revalidation failed")
                        revalidation = None

                    if revalidation is None or not revalidation.valid:
                        revalidation_invalid = True
                        server.store.delete_answer_cache(key)
                        logger.info(
                            "cache invalidated by revalidation: %s",
                            revalidation.reason if revalidation else "error",
                        )
                    else:
                        revalidated = True
                        if revalidation.checked and revalidation.expires_at:
                            server.store.refresh_answer_cache_expiry(
                                key,
                                expires_at=revalidation.expires_at.isoformat(),
                                now=now.isoformat(),
                            )
                        else:
                            server.store.touch_answer_cache(
                                key, now=now.isoformat()
                            )
                if revalidated or not (
                    settings.CACHE_REVALIDATE_ENABLED
                    and channel != "voice"
                    and should_revalidate(
                        entry,
                        request,
                        now=now,
                        window_s=settings.CACHE_REVALIDATE_NEAR_EXPIRY_S,
                    )
                ):
                    if not revalidated:
                        try:
                            server.store.touch_answer_cache(
                                key, now=now.isoformat()
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception("answer cache touch failed")
                    logger.info("text agentic cache hit")
                    cached_result = _cached_answer_result(entry, query, plan)
                    cached_result.builder_declines = list(builder_declines)
                    _record_turn_with_store(
                        server.store,
                        conversation_id,
                        query,
                        cached_result,
                        execution=ExecutionResult(plan=plan),
                        channel=channel,
                        language=language,
                    )
                    return cached_result
        execution = execute_plan(
            server,
            plan,
            max_steps=steps,
            deadline_ms=deadline,
            max_bytes=max(chars * 4, 4000),
        )
        if (
            plan.intent is Intent.NAVIGATE
            and (settings.NGA_NAVIGATION_MODE or "browse") != "legacy"
        ):
            browse = next(
                (
                    item for item in execution.results
                    if item.get("tool") == "site.browse"
                    and item.get("status") == "ok"
                ),
                None,
            )
            if browse is not None:
                browse_result = _browse_answer(
                    query,
                    plan,
                    execution,
                    browse,
                    facts=facts or FactStore(),
                )
                if browse_result is not None:
                    browse_result.builder_declines = list(builder_declines)
                    _write_answer_cache(
                        server.store,
                        key,
                        browse_result,
                        query=query,
                        time_scope=time_scope,
                        programme=programme,
                        channel=channel,
                        language=language,
                        index_version=index_version,
                        policy_version=policy_version,
                        now=datetime.now(timezone.utc),
                        normalized_query=normalized_query,
                    )
                    _record_turn_with_store(
                        server.store,
                        conversation_id,
                        query,
                        browse_result,
                        execution=execution,
                        channel=channel,
                        language=language,
                    )
                    return browse_result
        bundle = assemble_evidence(
            execution,
            query=query,
            facts=facts or FactStore(),
            max_items=items,
            max_chars=chars,
            pii_guard=guard,
        )
        admission_result = _admission_answer_result(
            query,
            plan,
            execution,
            bundle,
            declines=builder_declines,
        )
        if admission_result is not None:
            admission_result.builder_declines = list(builder_declines)
            _write_answer_cache(
                server.store,
                key,
                admission_result,
                query=query,
                time_scope=time_scope,
                programme=programme,
                channel=channel,
                language=language,
                index_version=index_version,
                policy_version=policy_version,
                now=datetime.now(timezone.utc),
                normalized_query=normalized_query,
            )
            _record_turn_with_store(
                server.store,
                conversation_id,
                query,
                admission_result,
                execution=execution,
                channel=channel,
                language=language,
            )
            return admission_result
        if plan.intent in (Intent.NOTICE, Intent.DATE):
            notice_result = _notice_answer_result(
                query,
                plan,
                execution,
                bundle,
                facts=facts or FactStore(),
                declines=builder_declines,
            )
            if notice_result is not None:
                notice_result.builder_declines = list(builder_declines)
                _write_answer_cache(
                    server.store,
                    key,
                    notice_result,
                    query=query,
                    time_scope=time_scope,
                    programme=programme,
                    channel=channel,
                    language=language,
                    index_version=index_version,
                    policy_version=policy_version,
                    now=datetime.now(timezone.utc),
                    normalized_query=normalized_query,
                )
                _record_turn_with_store(
                    server.store,
                    conversation_id,
                    query,
                    notice_result,
                    execution=execution,
                    channel=channel,
                    language=language,
                )
                return notice_result
        assessment = (
            assess_bundle(query, bundle)
            if settings.NGA_ADEQUACY_ENABLED else None
        )
        retrieval = assessment.to_dict() if assessment is not None else None
        inadequate = bool(assessment and not assessment.adequate)
        if (
            (inadequate or not bundle.items or revalidation_invalid)
            and settings.DEEP_SEARCH_ENABLED
            and channel != "voice"
        ):
            deep_result = _deep_search_answer(
                query,
                server,
                plan,
                language=language,
                channel=channel,
                facts=facts or FactStore(),
                llm=llm,
                history=effective_history,
                guard=guard,
                items=items,
                chars=chars,
                key=key,
                normalized_query=normalized_query,
                time_scope=time_scope,
                programme=programme,
                index_version=index_version,
                policy_version=policy_version,
            )
            if deep_result is not None:
                deep_result.retrieval = retrieval
                deep_result.builder_declines = list(builder_declines)
                _record_turn_with_store(
                    server.store,
                    conversation_id,
                    query,
                    deep_result,
                    channel=channel,
                    language=language,
                )
                return deep_result
        if inadequate and assessment is not None:
            # The retrieval ran but did not produce sufficient evidence. Do
            # not let the old static corpus answer in its place: return a
            # labelled refusal that carries the reason. The negative cache is
            # short-lived (10 minutes) and is re-evaluated on the next turn.
            result = AnswerResult(
                query=query,
                answer=no_evidence_answer(facts or FactStore()),
                status="no_evidence",
                answer_type="refusal",
                citations=[],
                evidence=bundle,
                error=";".join(assessment.reasons) or "inadequate_evidence",
                retrieval=retrieval,
                plan=plan,
                execution=execution,
                builder_declines=list(builder_declines),
            )
            _write_answer_cache(
                server.store,
                key,
                result,
                query=query,
                time_scope=time_scope,
                programme=programme,
                channel=channel,
                language=language,
                index_version=index_version,
                policy_version=policy_version,
                now=datetime.now(timezone.utc),
                normalized_query=normalized_query,
            )
            _record_turn_with_store(
                server.store,
                conversation_id,
                query,
                result,
                execution=execution,
                channel=channel,
                language=language,
            )
            logger.info(
                "text agentic: inadequate evidence (%s); refusing",
                ";".join(assessment.reasons),
            )
            return result
        if use_fact_answer and plan.intent is Intent.FEE:
            fact_result = _fee_fact_answer_result(
                query,
                plan,
                execution,
                bundle,
                facts=facts or FactStore(),
                language=language,
                declines=builder_declines,
            )
            if fact_result is not None:
                fact_result.retrieval = retrieval
                fact_result.builder_declines = list(builder_declines)
                _write_answer_cache(
                    server.store,
                    key,
                    fact_result,
                    query=query,
                    time_scope=time_scope,
                    programme=programme,
                    channel=channel,
                    language=language,
                    index_version=index_version,
                    policy_version=policy_version,
                    now=datetime.now(timezone.utc),
                    normalized_query=normalized_query,
                )
                _record_turn_with_store(
                    server.store,
                    conversation_id,
                    query,
                    fact_result,
                    execution=execution,
                    channel=channel,
                    language=language,
                )
                return fact_result
        if not bundle.items:
            logger.info("text agentic: no evidence; using static path")
            return None
        result = answer_from_execution(
            execution,
            query=query,
            facts=facts or FactStore(),
            llm=llm,
            history=effective_history,
            language=language,
            pii_guard=guard,
            max_items=items,
            max_chars=chars,
        )
        result.retrieval = retrieval
        result.builder_declines = list(builder_declines)
        _write_answer_cache(
            server.store,
            key,
            result,
            query=query,
            time_scope=time_scope,
            programme=programme,
            channel=channel,
            language=language,
            index_version=index_version,
            policy_version=policy_version,
            now=datetime.now(timezone.utc),
            normalized_query=normalized_query,
        )
        _record_turn_with_store(
            server.store,
            conversation_id,
            query,
            result,
            channel=channel,
            language=language,
        )
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
    if result.status == "conversational":
        return result.answer
    if result.status == "no_evidence":
        return result.answer
    if result.status == "blocked":
        # Evidence existed; the verifier refused a specific claim. Do not
        # claim the university has no answer.
        return blocked_answer(result, facts or FactStore())
    if result.status == "generation_failed":
        return generation_failed_answer(facts or FactStore())
    # Unknown state: never release an unverified answer.
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
