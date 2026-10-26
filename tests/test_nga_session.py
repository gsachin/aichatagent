"""Phase 6: event-sourced session memory and follow-up planning."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.agent.answer import AnswerResult
from app.agent.evidence import assemble_evidence
from app.agent.executor import execute_plan
from app.agent.facts import FactStore
from app.agent.session import ConversationMemory
from app.agent.verifier import VerificationResult
from tests.nga_helpers import build_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _turn(
    memory: ConversationMemory,
    server,
    conversation_id: str,
    query: str,
    *,
    language: str | None = "en",
    answer_result: AnswerResult | None = None,
):
    state, plan = memory.plan(
        conversation_id, query, language=language
    )
    execution = execute_plan(server, plan)
    bundle = assemble_evidence(
        execution,
        query=query,
        facts=FactStore(),
        pii_guard=False,
    )
    event = memory.record(
        conversation_id,
        query,
        plan=plan,
        execution=execution,
        bundle=bundle,
        answer_result=answer_result,
        language=language,
    )
    return state, plan, execution, bundle, event


def _build(tmp_path):
    server, store, db = build_server(tmp_path, seed=True)
    memory = ConversationMemory(store, history_limit=6)
    return server, store, db, memory


def test_turn_ids_increment_and_replay_is_ordered(tmp_path):
    server, store, _db, memory = _build(tmp_path)

    first = _turn(memory, server, "c1", "hostel")[4]
    second = _turn(memory, server, "c1", "library")[4]
    replay = memory.replay("c1")

    assert first.turn_id == 1
    assert second.turn_id == 2
    assert first.event_id != second.event_id
    assert [event["turn_id"] for event in replay] == [1, 2]
    assert store.turn_event_count("c1") == 2


def test_load_returns_recent_turns_and_current_topic(tmp_path):
    server, _store, _db, memory = _build(tmp_path)
    _turn(memory, server, "c1", "hostel")

    state = memory.load("c1")

    assert state.current_node_id == "cms.hostel"
    assert state.current_node_title == "Hostel"
    assert state.channel == "chat"
    assert state.recent_turns == [
        "User: hostel",
        "Assistant: (no_answer)",
    ]


def test_follow_up_plan_opens_the_previous_topic(tmp_path):
    server, _store, _db, memory = _build(tmp_path)
    _turn(memory, server, "c1", "hostel")

    state, plan = memory.plan("c1", "what about its fee?")

    assert state.current_node_id == "cms.hostel"
    assert [step.tool for step in plan.steps] == ["site.open", "site.search"]
    assert plan.steps[0].args["node_id"] == "cms.hostel"
    assert "Hostel" in plan.steps[1].args["query"]


def test_programme_and_language_carry_to_the_next_turn(tmp_path):
    server, _store, _db, memory = _build(tmp_path)
    _turn(memory, server, "c1", "BA History fee", language="hi")

    state, plan = memory.plan("c1", "what about its fee?")

    assert state.language == "hi"
    assert state.programme
    assert plan.filters["programme"] == state.programme


def test_event_records_plan_tools_evidence_and_session_state(tmp_path):
    server, _store, _db, memory = _build(tmp_path)
    _turn(memory, server, "c1", "hostel")

    event = memory.replay("c1")[-1]

    assert event["question"] == "hostel"
    assert event["plan"]["intent"] == "page_topic"
    assert event["tool_calls"]
    assert event["tool_calls"][0]["tool"] == "site.search"
    assert event["evidence_ids"]
    assert event["citations"]
    assert event["latency"]["steps"] >= 1
    assert event["session_state"]["current_node_id"] == "cms.hostel"
    assert event["answer_status"] == "no_answer"


def test_verified_answer_result_is_recorded(tmp_path):
    server, _store, _db, memory = _build(tmp_path)
    _, plan, execution, bundle, _event = _turn(
        memory, server, "c1", "hostel", answer_result=None
    )
    answer = AnswerResult(
        query="hostel",
        answer="The hostel has a warden office.",
        status="verified",
        verification=VerificationResult(
            answer="The hostel has a warden office.",
            verified=True,
            status="verified",
        ),
        citations=[],
        evidence=bundle,
    )
    event = memory.record(
        "c1",
        "hostel",
        plan=plan,
        execution=execution,
        bundle=bundle,
        answer_result=answer,
        language="en",
    )

    assert event.verified is True
    assert event.answer_status == "verified"
    assert event.answer == "The hostel has a warden office."


def test_no_events_returns_an_empty_state(tmp_path):
    _server, _store, _db, memory = _build(tmp_path)

    state = memory.load("missing")

    assert state.conversation_id == "missing"
    assert state.current_node_id is None
    assert state.recent_turns == []


def test_clear_and_purge_turn_events(tmp_path):
    server, store, _db, memory = _build(tmp_path)
    _turn(memory, server, "c1", "hostel")
    _turn(memory, server, "c2", "library")

    assert store.purge_turn_events("9999-01-01T00:00:00Z") == 2
    assert store.turn_event_count() == 0

    _turn(memory, server, "c3", "hostel")
    assert memory.clear("c3") == 1
    assert store.turn_event_count("c3") == 0


def test_cli_dry_run_records_and_resolves_a_follow_up(tmp_path):
    server, _store, db, _memory = _build(tmp_path)
    _turn(server=server, memory=_memory, conversation_id="cli", query="hostel")

    first = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_session.py"),
            "--db", str(db),
            "--conversation-id", "cli",
            "--query", "hostel",
            "--dry-run",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    second = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_session.py"),
            "--db", str(db),
            "--conversation-id", "cli",
            "--query", "what about its fee?",
            "--dry-run",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)
    assert payload["state_before"]["current_node_id"] == "cms.hostel"
    assert payload["plan"]["steps"][0]["tool"] == "site.open"
    assert payload["plan"]["steps"][0]["args"]["node_id"] == "cms.hostel"
    assert payload["turn_id"] >= 2
