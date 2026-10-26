"""Phase 8: voice fast path with a real tool deadline."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.voice import build_voice_bundle
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


def test_voice_bundle_contains_evidence_and_citations(tmp_path):
    _store_obj, db = _store(tmp_path)
    context = build_voice_bundle(
        "hostel", graph_path=str(db), pii_guard=False
    )

    assert context.bundle is not None
    assert context.bundle.kind == "voice_evidence"
    assert "site_evidence" in context.bundle.text or "Verified facts" in context.bundle.text
    assert context.bundle.source_urls
    assert context.plan is not None
    assert context.plan.intent.value == "page_topic"
    assert context.execution is not None
    assert context.execution.status == "ok"
    assert context.evidence is not None
    assert context.evidence.items


def test_voice_fast_path_falls_back_when_no_evidence(tmp_path):
    _store_obj, db = _store(tmp_path)
    context = build_voice_bundle(
        "quantum flux capacitor", graph_path=str(db), pii_guard=False
    )

    assert context.bundle is None
    assert context.fallback_reason == "no_evidence"
    assert context.execution is not None


def test_voice_fast_path_deadline_falls_back(tmp_path):
    _store_obj, db = _store(tmp_path)
    context = build_voice_bundle(
        "hostel", graph_path=str(db), pii_guard=False, deadline_ms=0
    )

    assert context.bundle is None
    assert context.execution is not None
    assert context.execution.deadline_exceeded is True


def test_boundary_query_never_reaches_the_tools(tmp_path):
    _store_obj, db = _store(tmp_path)
    context = build_voice_bundle(
        "MBA fee", graph_path=str(db), pii_guard=False
    )

    assert context.bundle is None
    assert context.fallback_reason == "boundary_or_empty_plan"


@pytest.mark.anyio
async def test_streaming_voice_uses_the_fast_path(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="fast",
        NGA_GRAPH_SQLITE_PATH=str(db),
        NGA_VOICE_DEADLINE_MS=2000,
        NGA_VOICE_MAX_STEPS=2,
        NGA_VOICE_MAX_CHARS=1200,
    )
    clear_turn_context()
    seen = {}

    async def fake_stream(prompt, **kwargs):
        seen["system"] = kwargs.get("system") or ""
        yield "ok"

    monkeypatch.setattr("app.llm_backend.generate_stream", fake_stream)
    chunks = [item async for item in query_rag_stream("hostel", mode="voice", voice_fast=True)]

    assert chunks == ["ok"]
    assert "site_evidence" in seen["system"] or "Verified facts" in seen["system"]
    assert "dspmuranchi.ac.in/cms/hostel" in seen["system"]
    bundle = last_context_bundle()
    assert bundle is not None
    assert bundle.kind == "voice_fast"


def test_batch_voice_uses_the_fast_path(tmp_path, monkeypatch, override_settings):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="fast",
        NGA_GRAPH_SQLITE_PATH=str(db),
        NGA_VOICE_DEADLINE_MS=2000,
    )
    clear_turn_context()
    seen = {}

    def fake_chat(messages, **kwargs):
        seen["messages"] = messages
        return "ok"

    monkeypatch.setattr("app.llm_backend.chat", fake_chat)
    answer = query_rag("hostel", mode="voice", voice_fast=True)

    assert answer == "ok"
    system = seen["messages"][0]["content"]
    assert "site_evidence" in system or "Verified facts" in system
    assert last_context_bundle().kind == "voice_fast"


@pytest.mark.anyio
async def test_text_chat_is_not_changed_by_the_voice_path(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="fast",
        NGA_GRAPH_SQLITE_PATH=str(db),
    )
    clear_turn_context()

    async def fake_stream(prompt, **kwargs):
        yield "ok"

    monkeypatch.setattr("app.llm_backend.generate_stream", fake_stream)
    chunks = [item async for item in query_rag_stream("hostel", mode="chat")]

    assert chunks == ["ok"]
    assert last_context_bundle().kind != "voice_fast"


@pytest.mark.anyio
async def test_voice_falls_back_to_static_when_the_path_returns_nothing(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="fast",
        NGA_GRAPH_SQLITE_PATH=str(db),
    )
    clear_turn_context()

    async def fake_stream(prompt, **kwargs):
        yield "ok"

    monkeypatch.setattr("app.llm_backend.generate_stream", fake_stream)
    chunks = [
        item async for item in query_rag_stream(
            "quantum flux capacitor", mode="voice", voice_fast=True
        )
    ]

    assert chunks == ["ok"]
    assert last_context_bundle().kind != "voice_fast"
