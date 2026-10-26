"""Phase 9: text agentic path for chat/WhatsApp."""

from __future__ import annotations

import json

import pytest

from app.agent.text import (
    build_text_answer,
    plan_text,
    render_text_answer,
    text_context_bundle,
)
from app.rag import (
    clear_turn_context,
    last_context_bundle,
    query_rag,
    query_rag_stream,
)
from tests.nga_helpers import build_store, seed_page


def _store(tmp_path):
    store, db = build_store(tmp_path)
    seed_page(store)
    return store, db


def _correct_fee(messages):
    return (
        "The BA History annual fee is ₹3,410 (GEN) and "
        "₹3,185 for ST/SC/BC I & II."
    )


def test_text_agentic_page_answer_is_verified(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The hostel page mentions a warden office.",
    )

    assert result is not None
    assert result.status == "verified"
    assert result.answer
    assert result.evidence is not None
    assert result.citations


def test_text_agentic_fee_uses_the_verified_fact(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "What is the BA History fee?",
        graph_path=str(db),
        pii_guard=False,
        llm=_correct_fee,
    )

    assert result is not None
    assert result.status == "verified"
    assert "3,410" in result.answer
    assert result.verification is not None
    assert result.verification.verified is True
    assert result.citations[0]["authority"] == "verified_fact"


def test_text_agentic_blocks_a_hallucinated_fee(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "What is the BA History fee?",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The BA History annual fee is ₹9,999 (GEN).",
    )

    assert result is not None
    assert result.status == "blocked"
    assert render_text_answer(result)
    assert "₹9,999" not in render_text_answer(result)
    assert "registrar" in render_text_answer(result).lower()


def test_text_agentic_returns_none_when_there_is_no_evidence(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "quantum flux capacitor",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "anything",
    )

    assert result is None


def test_text_agentic_returns_none_for_a_boundary(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "MBA fee",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "anything",
    )

    assert result is None


def test_llm_planner_is_optional_and_validated():
    def fake_planner(prompt: str) -> str:
        return json.dumps(
            {
                "intent": "page_topic",
                "steps": [
                    {"tool": "site.search", "args": {"query": "hostel"}}
                ],
            }
        )

    plan = plan_text("hostel", planner_llm=fake_planner)
    fallback = plan_text("hostel", planner_llm=lambda prompt: "not json")

    assert plan.steps[0].tool == "site.search"
    assert fallback.steps[0].tool == "site.search"


def test_text_context_bundle_is_guard_evidence(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The hostel has a warden office.",
    )

    bundle = text_context_bundle(result)

    assert bundle.kind == "text_agentic"
    assert bundle.source_urls
    assert "<site_evidence>" in bundle.text or "Verified facts" in bundle.text


def test_sync_query_rag_uses_the_text_agentic_path(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="agentic",
        NGA_GRAPH_SQLITE_PATH=str(db),
        NGA_TEXT_DEADLINE_MS=2000,
        NGA_TEXT_MAX_STEPS=4,
        NGA_TEXT_MAX_CHARS=2000,
    )
    clear_turn_context()
    monkeypatch.setattr("app.llm_backend.chat", lambda messages, **kwargs: _correct_fee(messages))

    answer = query_rag(
        "What is the BA History fee?",
        mode="chat",
        agentic_text=True,
    )

    assert answer is not None
    assert "3,410" in answer
    assert "Sources" in answer
    assert last_context_bundle().kind == "text_agentic"


@pytest.mark.anyio
async def test_streamed_query_rag_uses_the_text_agentic_path(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="agentic",
        NGA_GRAPH_SQLITE_PATH=str(db),
        NGA_TEXT_DEADLINE_MS=2000,
        NGA_TEXT_MAX_STEPS=4,
        NGA_TEXT_MAX_CHARS=2000,
    )
    clear_turn_context()
    monkeypatch.setattr("app.llm_backend.chat", lambda messages, **kwargs: _correct_fee(messages))

    chunks = [
        item async for item in query_rag_stream(
            "What is the BA History fee?",
            mode="chat",
            agentic_text=True,
        )
    ]

    answer = "".join(chunk for chunk in chunks if isinstance(chunk, str))
    assert "3,410" in answer
    assert "Sources" in answer
    assert last_context_bundle().kind == "text_agentic"


def test_fast_mode_does_not_enable_the_text_agentic_path(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="fast",
        NGA_GRAPH_SQLITE_PATH=str(db),
    )
    clear_turn_context()
    monkeypatch.setattr(
        "app.llm_backend.chat",
        lambda messages, **kwargs: "static answer",
    )

    answer = query_rag("hostel", mode="chat", agentic_text=True)

    assert answer == "static answer"
    assert last_context_bundle().kind != "text_agentic"
