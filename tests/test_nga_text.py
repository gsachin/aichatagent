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


def test_text_agentic_answers_the_signed_off_vc_name(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "who is VC of university?",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The Vice Chancellor is Prof. Rajiv Manohar.",
    )

    assert result is not None
    assert result.status == "verified"
    assert "Rajiv Manohar" in result.answer
    assert result.citations
    assert result.citations[0]["fact_id"] == "university.vice_chancellor"


def test_text_agentic_blocks_a_wrong_vc_name(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "who is VC of university?",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The Vice Chancellor is Prof. Ram Kumar.",
    )

    assert result is not None
    assert result.status == "blocked"
    rendered = render_text_answer(result)
    assert "Ram Kumar" not in rendered
    assert "registrar" in rendered.lower()
    # Evidence existed; the message must not claim the university has no
    # verified answer.
    assert "could not verify a specific claim" in rendered.lower()
    assert "could not find a verified answer" not in rendered.lower()


def test_text_agentic_blocks_a_hallucinated_fee(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "What is the BA History fee?",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The BA History annual fee is ₹9,999 (GEN).",
        use_fact_answer=False,
    )

    assert result is not None
    assert result.status == "blocked"
    assert render_text_answer(result)
    assert "₹9,999" not in render_text_answer(result)
    assert "registrar" in render_text_answer(result).lower()
    assert "could not find a verified answer" not in render_text_answer(result).lower()


def test_text_agentic_refuses_when_there_is_no_evidence(
    tmp_path, override_settings
):
    _store_obj, db = _store(tmp_path)
    # Keep this test offline even when the developer .env enables deep search.
    override_settings(DEEP_SEARCH_ENABLED=False)
    result = build_text_answer(
        "quantum flux capacitor",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "anything",
    )

    # A labelled refusal is better than a silent fall back to the static
    # corpus: the caller can see that the agent path was reached and why it
    # stopped.
    assert result is not None
    assert result.status == "no_evidence"
    assert result.answer_type == "refusal"
    assert "registrar" in result.answer.lower()
    assert result.error


def test_text_agentic_answers_the_admissions_portal_without_an_llm(tmp_path):
    _store_obj, db = _store(tmp_path)

    def must_not_call(_messages):
        raise AssertionError("the signed-off portal must not need generation")

    result = build_text_answer(
        "admission process",
        graph_path=str(db),
        pii_guard=False,
        llm=must_not_call,
    )

    assert result is not None
    assert result.status == "verified"
    assert result.answer_type == "answer"
    assert "jharkhanduniversities.nic.in" in result.answer


def test_text_agentic_answers_a_verified_fee_without_an_llm(tmp_path):
    _store_obj, db = _store(tmp_path)

    def must_not_call(_messages):
        raise AssertionError("a verified fee must not need generation")

    result = build_text_answer(
        "BA History fee",
        graph_path=str(db),
        pii_guard=False,
        llm=must_not_call,
    )

    assert result is not None
    assert result.status == "verified"
    assert result.answer_type == "answer"
    assert "3,410" in result.answer
    assert "3,185" in result.answer
    assert "draft" in result.answer.lower()
    assert result.verification is not None
    assert result.verification.verified


def test_text_agentic_refuses_a_pg_boundary_deterministically(tmp_path):
    _store_obj, db = _store(tmp_path)
    result = build_text_answer(
        "MBA fee",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "anything",
    )

    assert result is not None
    assert result.status == "blocked"
    assert result.answer_type == "refusal"
    assert "postgraduate" in result.answer.lower()
    assert "registrar" in result.answer.lower() or "contact" in result.answer.lower()


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


def test_text_agentic_uses_the_cache_on_the_second_call(tmp_path):
    _store_obj, db = _store(tmp_path)
    first = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The hostel has a warden office.",
    )
    assert first is not None
    assert first.status == "verified"

    def must_not_call(_messages):
        raise AssertionError("the cached answer should not call the LLM")

    second = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=must_not_call,
    )

    assert second is not None
    assert second.answer == first.answer
    assert second.status == first.status
    assert second.verification is not None
    assert "cache_hit" in second.verification.notes
    assert second.evidence is not None
    assert second.citations


def test_text_agentic_does_not_cache_a_refusal_with_evidence(tmp_path):
    _store_obj, db = _store(tmp_path)
    calls: list[list[dict]] = []

    def refusing_llm(messages):
        calls.append(messages)
        return "I couldn't verify the hostel warden office from the available evidence."

    first = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=refusing_llm,
    )
    assert first is not None
    assert first.status == "verified"  # no false claims
    assert first.answer_type == "refusal"  # but not an answer

    second = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=refusing_llm,
    )
    assert second is not None
    # The refusal must not have been promoted to a 24-hour cache fact: the
    # second turn re-evaluates instead of returning the first answer.
    assert len(calls) == 2
    assert "cache_hit" not in (second.verification.notes or [])


def test_deep_search_triggers_on_inadequate_evidence(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    # The seeded hostel page contains "hostel" but not "rules". It is a
    # non-empty bundle, but not adequate evidence; the old path would answer
    # from it and never deep-search.
    override_settings(
        DEEP_SEARCH_ENABLED=True,
        DEEP_SEARCH_MAX_RESULTS=3,
        DEEP_SEARCH_MAX_FETCHES=1,
        DEEP_SEARCH_TIMEOUT_S=5,
    )
    from app.deepsearch.service import DeepSearchResult

    called = {"value": False}
    fake = DeepSearchResult(
        query="hostel rules",
        status="ok",
        urls=["https://dspmuranchi.ac.in/cms/hostel-rules"],
        results=[
            {
                "tool": "site.open",
                "status": "ok",
                "data": {
                    "node": {
                        "node_id": "ondemand.hostel-rules",
                        "title": "Hostel Rules",
                        "url": "https://dspmuranchi.ac.in/cms/hostel-rules",
                        "status": "ok",
                        "authority": "official_page",
                    },
                    "sections": [
                        {
                            "heading": "Hostel Rules",
                            "text": "Hostel rules: visitors must sign in at the gate.",
                        }
                    ],
                    "tables": [],
                    "body_available": True,
                    "content_status": "indexed",
                },
            }
        ],
        fetched=1,
        message="deep_search_ok",
    )

    def fake_deep_search(*args, **kwargs):
        called["value"] = True
        return fake

    monkeypatch.setattr("app.agent.text.deep_search", fake_deep_search)

    result = build_text_answer(
        "hostel rules",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "I checked the official website.",
    )

    assert called["value"] is True
    assert result is not None
    assert result.status == "verified"
    assert result.answer.startswith("I checked the official university website")


def test_deep_search_shell_returns_a_handoff_without_the_llm(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=True)
    from app.deepsearch.service import DeepSearchResult

    fake = DeepSearchResult(
        query="library timings",
        status="ok",
        urls=["https://dspmuranchi.ac.in/cms/central-library"],
        results=[
            {
                "tool": "site.open",
                "status": "ok",
                "data": {
                    "node": {
                        "node_id": "ondemand.library",
                        "title": "Central Library",
                        "url": "https://dspmuranchi.ac.in/cms/central-library",
                        "status": "ok",
                        "authority": "official_page",
                    },
                    "sections": [],
                    "tables": [],
                    "body_available": False,
                    "content_status": "empty_shell",
                    "handoff": True,
                },
            }
        ],
        fetched=1,
        message="deep_search_ok",
    )
    monkeypatch.setattr(
        "app.agent.text.deep_search", lambda *args, **kwargs: fake
    )

    def must_not_call(_messages):
        raise AssertionError("a shell handoff must not call the LLM")

    result = build_text_answer(
        "library timings",
        graph_path=str(db),
        pii_guard=False,
        llm=must_not_call,
    )

    assert result is not None
    assert result.status == "no_evidence"
    assert result.answer_type == "refusal"
    assert result.error == "handoff:empty_shell"
    assert result.deep_search is not None
    assert result.deep_search["triggered"] is True
    assert result.deep_search["fetched"] == 1
    assert "Central Library" in result.answer
    assert "https://dspmuranchi.ac.in/cms/central-library" in result.answer
    assert "registrar" in result.answer.lower()


def test_deep_search_inadequate_evidence_does_not_answer(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(DEEP_SEARCH_ENABLED=True)
    from app.deepsearch.service import DeepSearchResult

    fake = DeepSearchResult(
        query="library timings",
        status="ok",
        urls=["https://dspmuranchi.ac.in/uploads/tender.pdf"],
        results=[
            {
                "tool": "site.open",
                "status": "ok",
                "data": {
                    "node": {
                        "node_id": "ondemand.tender",
                        "title": "Tender for Repair and Renovation of Library",
                        "url": "https://dspmuranchi.ac.in/uploads/tender.pdf",
                        "status": "ok",
                        "authority": "official_document",
                    },
                    "sections": [
                        {
                            "heading": "Tender",
                            "text": (
                                "Tender for repair and renovation of the "
                                "library building, including civil work and "
                                "painting, with submission dates."
                            ),
                        }
                    ],
                    "tables": [],
                    "body_available": True,
                    "content_status": "indexed",
                },
            }
        ],
        fetched=1,
        message="deep_search_ok",
    )
    monkeypatch.setattr(
        "app.agent.text.deep_search", lambda *args, **kwargs: fake
    )

    def must_not_call(_messages):
        raise AssertionError("inadequate deep evidence must not call the LLM")

    result = build_text_answer(
        "library timings",
        graph_path=str(db),
        pii_guard=False,
        llm=must_not_call,
    )

    assert result is not None
    assert result.status == "no_evidence"
    assert result.answer_type == "refusal"
    # The old path would have answered from the tender PDF. The gate must
    # reject it and surface the reason instead.
    assert "Tender" not in result.answer


def test_text_agentic_uses_deep_search_after_no_evidence(
    tmp_path, monkeypatch, override_settings
):
    _store_obj, db = _store(tmp_path)
    override_settings(
        DEEP_SEARCH_ENABLED=True,
        DEEP_SEARCH_MAX_RESULTS=3,
        DEEP_SEARCH_MAX_FETCHES=1,
        DEEP_SEARCH_TIMEOUT_S=5,
    )
    from app.deepsearch.service import DeepSearchResult

    fake = DeepSearchResult(
        query="library timings",
        status="ok",
        urls=["https://dspmuranchi.ac.in/facilities/central-library"],
        results=[
            {
                "tool": "site.open",
                "status": "ok",
                "data": {
                    "node": {
                        "node_id": "ondemand.library",
                        "title": "Central Library",
                        "url": "https://dspmuranchi.ac.in/facilities/central-library",
                        "status": "ok",
                        "authority": "official_page",
                    },
                    "sections": [
                        {
                            "heading": "Central Library",
                            "text": "Central Library timings are 9 AM to 5 PM.",
                        }
                    ],
                    "tables": [],
                    "body_available": True,
                    "content_status": "indexed",
                },
            }
        ],
        fetched=1,
        message="deep_search_ok",
    )
    monkeypatch.setattr(
        "app.agent.text.deep_search", lambda *args, **kwargs: fake
    )

    result = build_text_answer(
        "library timings",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The Central Library timings are 9 AM to 5 PM.",
    )

    assert result is not None
    assert result.status == "verified"
    assert result.answer.startswith(
        "I checked the official university website"
    )
    assert result.verification is not None
    assert "deep_search_used" in result.verification.notes
    assert result.deep_search is not None
    assert result.deep_search["triggered"] is True
    assert result.deep_search["status"] == "ok"
    assert result.citations
