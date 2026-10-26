"""Conversation persistence guardrail for the synchronous NGA turn write."""

from __future__ import annotations

import statistics
import time

from app.agent.answer import AnswerResult
from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.executor import ExecutionResult
from app.agent.models import Intent, Plan, PlanStep
from app.agent.session import ConversationMemory
from app.sitegraph.metrics import utc_now
from app.sitegraph.store import GraphStore


def _turn():
    plan = Plan(
        intent=Intent.PAGE_TOPIC,
        steps=(PlanStep("site.search", {"query": "hostel", "limit": 3}),),
    )
    execution = ExecutionResult(
        plan=plan,
        results=[{"tool": "site.search", "status": "ok", "data": {}}],
        steps_used=1,
    )
    item = EvidenceItem(
        evidence_id="ev-1",
        node_id="bench.node",
        title="Hostel",
        url="https://dspmuranchi.ac.in/cms/hostel",
        authority="official_page",
        verified_at=utc_now(),
        snippet="Boys and girls hostels with a warden office.",
        text="Boys and girls hostels with a warden office.",
    )
    bundle = EvidenceBundle(
        query="hostel", intent=Intent.PAGE_TOPIC, items=[item]
    )
    result = AnswerResult(
        query="hostel",
        answer="The hostel has a warden office.",
        status="verified",
        citations=[
            {
                "node_id": item.node_id,
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "authority": item.authority,
            }
        ],
        evidence=bundle,
        plan=plan,
        execution=execution,
    )
    return plan, execution, bundle, result


def test_turn_event_write_p95_stays_inside_the_chat_budget(tmp_path):
    store = GraphStore(tmp_path / "events.sqlite3")
    memory = ConversationMemory(store, channel="bench")
    plan, execution, bundle, result = _turn()
    times: list[float] = []

    for index in range(60):
        started = time.perf_counter()
        memory.record(
            "cap-conversation",
            f"question {index}",
            plan=plan,
            execution=execution,
            bundle=bundle,
            answer_result=result,
            channel="bench",
        )
        times.append((time.perf_counter() - started) * 1000)

    p95 = sorted(times)[round((len(times) - 1) * 0.95)]
    assert store.turn_event_count("cap-conversation") == 60
    assert statistics.median(times) < 100
    # A gross regression, not normal machine variance: the measured p95 is
    # ~3.6 ms and the chat budget is 8,000 ms.
    assert p95 < 100
