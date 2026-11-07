"""Conversation memory and follow-up planning tests.

WP7: the live text path must load the previous turn's topic/programme/language
before planning, pass the recent turns to generation, and keep the topic
stable across refusals and cache hits.
"""

from __future__ import annotations

from app.agent.answer import AnswerResult
from app.agent.evidence import EvidenceBundle
from app.agent.executor import ExecutionResult
from app.agent.models import Intent, Plan
from app.agent.session import ConversationMemory
from app.agent.text import _cache_key_for, build_text_answer
from tests.nga_helpers import build_store, seed_page

HOSTEL_TEXT = (
    "The hostel has a warden office. Its fee is 500 rupees."
)


def _store(tmp_path):
    store, db = build_store(tmp_path)
    seed_page(store, text=HOSTEL_TEXT)
    return store, db


def _first_turn(db, conversation_id):
    return build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        conversation_id=conversation_id,
        llm=lambda messages: "The hostel has a warden office.",
    )


def test_followup_uses_the_previous_topic(tmp_path, override_settings):
    store, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=False)
    conversation_id = "conv-followup"

    first = _first_turn(db, conversation_id)
    assert first is not None
    assert first.status == "verified"
    events = ConversationMemory(store).replay(conversation_id)
    assert events[-1]["session_state"]["current_node_id"] == "cms.hostel"

    second = build_text_answer(
        "what about its fee?",
        graph_path=str(db),
        pii_guard=False,
        conversation_id=conversation_id,
        llm=lambda messages: "I couldn't verify the fee.",
    )

    assert second is not None
    assert second.plan is not None
    opens = [step for step in second.plan.steps if step.tool == "site.open"]
    assert opens
    assert opens[0].args["node_id"] == "cms.hostel"
    assert any(step.tool == "site.search" for step in second.plan.steps)


def test_followup_does_not_leak_across_conversations(
    tmp_path, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=False)

    assert _first_turn(db, "conv-a") is not None
    other = build_text_answer(
        "what about its fee?",
        graph_path=str(db),
        pii_guard=False,
        conversation_id="conv-b",
        llm=lambda messages: "I couldn't verify the fee.",
    )

    assert other is not None
    assert other.plan is not None
    assert not any(
        step.tool == "site.open" and step.args.get("node_id") == "cms.hostel"
        for step in other.plan.steps
    )


def test_followup_passes_previous_turns_to_generation(
    tmp_path, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=False)
    conversation_id = "conv-history"

    assert _first_turn(db, conversation_id) is not None

    seen: list[list[dict]] = []

    def llm(messages):
        seen.append(messages)
        return "I couldn't verify the fee."

    result = build_text_answer(
        "what about its fee?",
        graph_path=str(db),
        pii_guard=False,
        conversation_id=conversation_id,
        llm=llm,
    )

    assert result is not None
    assert seen, "the follow-up should reach generation"
    prompt = "\n".join(
        str(message.get("content") or "") for message in seen[-1]
    )
    assert "Recent conversation" in prompt
    assert "hostel" in prompt.lower()


def test_refusal_preserves_the_previous_topic(tmp_path, override_settings):
    store, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=False)
    conversation_id = "conv-refusal"

    assert _first_turn(db, conversation_id) is not None
    build_text_answer(
        "what about its fee?",
        graph_path=str(db),
        pii_guard=False,
        conversation_id=conversation_id,
        llm=lambda messages: "I couldn't verify the fee.",
    )

    events = ConversationMemory(store).replay(conversation_id)
    assert events[-1]["session_state"]["current_node_id"] == "cms.hostel"


def test_cache_hit_records_a_turn_event(tmp_path, override_settings):
    store, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=False)
    conversation_id = "conv-cache"

    first = _first_turn(db, conversation_id)
    assert first is not None
    assert first.status == "verified"

    def must_not_call(_messages):
        raise AssertionError("the cached answer should not call the LLM")

    second = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        conversation_id=conversation_id,
        llm=must_not_call,
    )

    assert second is not None
    assert second.verification is not None
    assert "cache_hit" in second.verification.notes
    events = ConversationMemory(store).replay(conversation_id)
    assert len(events) == 2
    assert events[-1]["answer"]
    assert events[-1]["session_state"]["current_node_id"] == "cms.hostel"


def test_a_handoff_citation_recovers_the_canonical_topic(tmp_path):
    store, _db = build_store(tmp_path)
    plan = Plan(intent=Intent.PAGE_TOPIC, steps=())
    execution = ExecutionResult(plan=plan)
    result = AnswerResult(
        query="hostel rules",
        answer="The matching official page is Hostel.",
        status="no_evidence",
        answer_type="refusal",
        citations=[
            {
                "node_id": "ondemand.transient",
                "title": "Hostel",
                "url": "https://dspmuranchi.ac.in/cms/hostel",
            }
        ],
        evidence=EvidenceBundle(query="hostel rules", intent=plan.intent),
        plan=plan,
        execution=execution,
        error="handoff:empty_shell",
    )

    ConversationMemory(store).record(
        "conv-handoff",
        "hostel rules",
        plan=plan,
        execution=execution,
        bundle=EvidenceBundle(query="hostel rules", intent=plan.intent),
        answer_result=result,
    )

    state = ConversationMemory(store).load("conv-handoff")
    assert state.current_node_id == "cms.hostel"
    assert state.current_node_title == "Hostel"


def test_cache_key_includes_the_followup_topic():
    plan_a = Plan(
        intent=Intent.FEE,
        steps=(),
        filters={"topic_node_id": "cms.hostel"},
    )
    plan_b = Plan(
        intent=Intent.FEE,
        steps=(),
        filters={"topic_node_id": "student-life.hostel"},
    )
    key_a, _scope_a, _prog_a, normalized_a = _cache_key_for(
        "what about its fee?",
        plan_a,
        language=None,
        channel="chat",
        index_version="index",
        policy_version="policy",
    )
    key_b, _scope_b, _prog_b, normalized_b = _cache_key_for(
        "what about its fee?",
        plan_b,
        language=None,
        channel="chat",
        index_version="index",
        policy_version="policy",
    )

    assert key_a != key_b
    assert "topic:cms.hostel" in normalized_a
    assert "topic:student-life.hostel" in normalized_b
