"""app.rag dispatcher — USE_MCP_RAG mode matrix (off/auto/on × up/down),
prompt byte-parity with the legacy pipeline, threshold-gate mapping, and the
retriever seam. Hermetic: MCP calls and legacy functions are monkeypatched."""
import httpx
import pytest

import app.rag as rag
import app.rag_mcp as rag_mcp
from app import rag_legacy

FAKE_CHUNKS = [
    {"chunk_id": "meridian-kb:s1:c1", "section_title": "Fees Structure",
     "content": "Tuition is $18,500.", "score": 0.9},
]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("USE_MCP_RAG", raising=False)
    rag_mcp._reset_session()
    rag_mcp._clear_failure()
    yield


# ── off mode: legacy only, MCP never touched ───────────────────────────────

def test_off_mode_uses_legacy(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "off")
    called = []
    monkeypatch.setattr(rag_legacy, "retrieve_context", lambda q: called.append(q) or "LEGACY")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MCP touched")))

    assert rag.retrieve_context("q") == "LEGACY"
    assert called == ["q"]
    assert rag.get_retriever() is not None  # legacy retriever path


def test_off_mode_query_rag_never_touches_mcp(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "off")
    monkeypatch.setattr(rag_legacy, "retrieve_context", lambda q: "LOCAL CONTEXT")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MCP touched")))

    captured = {}
    monkeypatch.setattr("app.llm_backend.chat", lambda **kw: captured.update(kw) or "answer")
    monkeypatch.setattr(rag_legacy, "_get_available_model", lambda: "qwen-test")

    answer = rag.query_rag("what are the fees?", mode="chat")
    assert answer == "answer"
    assert "LOCAL CONTEXT" in captured["messages"][0]["content"]
    assert captured["messages"][0]["content"].endswith("Student's question: what are the fees?")


# ── auto mode: MCP first, fallback on failure ──────────────────────────────

def test_auto_mode_mcp_first(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve",
                        lambda q, top_k=5: FAKE_CHUNKS)
    ctx = rag.retrieve_context("fees")
    assert ctx == "[§ Fees Structure]\nTuition is $18,500."


def test_auto_mode_falls_back_to_legacy_and_breaks(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")
    calls = []

    def fake_post(payload, session_id=None):
        raise httpx.ConnectError("down")        # the real failure path

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    monkeypatch.setattr(rag_legacy, "retrieve_context", lambda q: calls.append(q) or "LEGACY")

    assert rag.retrieve_context("q1") == "LEGACY"
    # breaker recorded the failure -> second call goes straight to legacy
    assert rag.retrieve_context("q2") == "LEGACY"
    assert calls == ["q1", "q2"]
    assert rag_mcp._breaker["failed_at"] is not None


def test_auto_mode_retriever_falls_back(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")

    class LegacyDoc:
        page_content = "legacy"
        metadata = {"section": "Old"}

    class LegacyRetriever:
        def invoke(self, query):
            return [LegacyDoc()]

    monkeypatch.setattr(rag_mcp, "mcp_retrieve", lambda *a, **k: None)
    monkeypatch.setattr(rag_legacy, "get_retriever", lambda: LegacyRetriever())
    docs = rag.get_retriever().invoke("q")
    assert [d.page_content for d in docs] == ["legacy"]


# ── on mode: MCP only, no fallback ─────────────────────────────────────────

def test_on_mode_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "on")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve", lambda *a, **k: None)
    monkeypatch.setattr(rag_legacy, "retrieve_context",
                        lambda q: (_ for _ in ()).throw(AssertionError("legacy touched")))
    assert rag.retrieve_context("q") == ""


# ── threshold gate mapping (MCP score -> cosine distance) ──────────────────

def test_threshold_distance_mcp_mapping(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve",
                        lambda q, top_k=1: [{"score": 0.9}])
    assert rag._threshold_distance("q") == pytest.approx(0.1)

    monkeypatch.setattr(rag_mcp, "mcp_retrieve", lambda q, top_k=1: None)
    monkeypatch.setattr(rag_legacy, "_best_distance", lambda q: 0.3)
    assert rag._threshold_distance("q") == 0.3     # auto: fall back to legacy


# ── warmup: never raises ───────────────────────────────────────────────────

def test_warmup_never_raises(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")
    monkeypatch.setattr(rag_mcp, "mcp_initialize", lambda: False)
    rag.warmup()          # must not raise

    monkeypatch.setenv("USE_MCP_RAG", "off")
    monkeypatch.setattr(rag_legacy, "get_vector_store", lambda: None)
    rag.warmup()          # must not raise


# ── prompt parity: chat prompt is the legacy SYSTEM_PROMPT with {context} ──

def test_query_rag_chat_prompt_matches_legacy_shape(monkeypatch):
    monkeypatch.setenv("USE_MCP_RAG", "auto")
    monkeypatch.setattr(rag_mcp, "mcp_retrieve",
                        lambda q, top_k=5: FAKE_CHUNKS)
    captured = {}
    monkeypatch.setattr("app.llm_backend.chat", lambda **kw: captured.update(kw) or "ok")
    monkeypatch.setattr(rag_legacy, "_get_available_model", lambda: "qwen")

    rag.query_rag("fees?", mode="chat")
    prompt = captured["messages"][0]["content"]
    assert prompt.startswith("You are a helpful University Admissions Advisor for Meridian University.")
    assert "[§ Fees Structure]\nTuition is $18,500." in prompt
    assert prompt.endswith("Student's question: fees?")
