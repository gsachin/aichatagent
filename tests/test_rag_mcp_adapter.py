"""app.rag_mcp adapter — hermetic wire-protocol tests.

Monkeypatches the single _post choke point; no network, no MCP SDK. Pins the
exact contract the enterprise-rag-core streamable-HTTP endpoint enforces:
initialize -> Mcp-Session-Id -> tools/call, dual Accept types, SSE-or-JSON
responses, one re-init on session expiry, and the legacy format contract.
"""
import json

import httpx
import pytest

import app.rag_mcp as rag_mcp


@pytest.fixture(autouse=True)
def _reset_state():
    rag_mcp._reset_session()
    rag_mcp._clear_failure()
    yield


def test_headers_accept_both_types():
    h = rag_mcp._headers()
    assert "application/json" in h["Accept"]
    assert "text/event-stream" in h["Accept"]


def test_initialize_session_sequence(monkeypatch):
    calls = []

    def fake_post(payload, session_id=None):
        calls.append((payload, session_id))
        if payload.get("method") == "initialize":
            return httpx.Response(
                200, headers={"mcp-session-id": "s1"},
                json={"jsonrpc": "2.0", "id": 1,
                      "result": {"protocolVersion": "2025-06-18"}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        raise AssertionError(payload)

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    assert rag_mcp.mcp_initialize() is True
    assert rag_mcp._state["session_id"] == "s1"
    init = calls[0][0]
    assert init["params"]["protocolVersion"] == "2025-06-18"
    assert init["params"]["clientInfo"]["name"] == "universityDemo-rag"
    # the initialized notification carried the session header
    assert calls[1][0]["method"] == "notifications/initialized"
    assert calls[1][1] == "s1"


def test_call_tool_parses_sse_result_and_sends_session(monkeypatch):
    calls = []

    def fake_post(payload, session_id=None):
        calls.append((payload, session_id))
        if payload.get("method") == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if payload.get("method") == "tools/call":
            body = {"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text",
                             "text": json.dumps({"chunks": [
                                 {"section_title": "Fees Structure",
                                  "content": "$18,500 per year"}]})}]}}
            return httpx.Response(200, text="data: " + json.dumps(body) + "\n",
                                  headers={"content-type": "text/event-stream"})
        raise AssertionError(payload)

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    result = rag_mcp.mcp_call_tool("retrieve_context", {"query": "fees", "top_k": 5})
    assert result["chunks"][0]["content"] == "$18,500 per year"
    tools_call = calls[2][0]
    assert tools_call["params"]["name"] == "retrieve_context"
    assert calls[2][1] == "s1"          # session header on tools/call


def test_format_legacy_chunks_exact():
    chunks = [
        {"section_title": "Fees Structure", "content": "Tuition is $18,500."},
        {"section_title": "", "content": "Bare chunk without a section."},
    ]
    assert rag_mcp.format_legacy_chunks(chunks) == (
        "[§ Fees Structure]\nTuition is $18,500."
        "\n\n---\n\n"
        "Bare chunk without a section."
    )


def test_mcp_retrieve_none_on_failure(monkeypatch):
    def fake_post(payload, session_id=None):
        if payload.get("method") == "initialize":
            raise httpx.ConnectError("refused")
        raise AssertionError(payload)

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    assert rag_mcp.mcp_retrieve("q") is None
    assert rag_mcp.mcp_retrieve_context("q") == ""
    assert rag_mcp._breaker["last_error"] == "refused"


def test_session_expiry_reinitializes_once(monkeypatch):
    calls = []
    tools_calls = []

    def fake_post(payload, session_id=None):
        calls.append(payload.get("method"))
        if payload.get("method") == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if payload.get("method") == "tools/call":
            tools_calls.append(session_id)
            if len(tools_calls) == 1:   # first call: session expired server-side
                return httpx.Response(404, json={"jsonrpc": "2.0", "id": 2,
                                                 "error": {"code": -32000}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": "{}"}]}})
        raise AssertionError(payload)

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    result = rag_mcp.mcp_call_tool("retrieve_context", {"query": "q"})
    assert result == {}
    assert calls.count("initialize") == 2      # one re-init, no more
    assert tools_calls == ["s1", "s1"]         # retry carried the fresh session


def test_circuit_breaker_cooldown(monkeypatch):
    def fake_post(payload, session_id=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    assert rag_mcp.mcp_retrieve("q") is None
    assert rag_mcp.mcp_available() is False          # cooldown active

    fake_now = [0.0]
    monkeypatch.setattr(rag_mcp.time, "monotonic", lambda: fake_now[0])
    fake_now[0] = 0.0
    rag_mcp._record_failure("x")
    assert rag_mcp.mcp_available() is False
    fake_now[0] = 31.0                               # past RAG_MCP_COOLDOWN=30
    assert rag_mcp.mcp_available() is True


def test_retriever_invoke_and_fallback(monkeypatch):
    def fake_post(payload, session_id=None):
        if payload.get("method") == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if payload.get("method") == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": json.dumps({"chunks": [
                    {"section_title": "Overview", "content": "Meridian text."}]})}]}})
        raise AssertionError(payload)

    monkeypatch.setattr(rag_mcp, "_post", fake_post)
    retriever = rag_mcp.MCPRetriever()
    docs = retriever.invoke("tell me about it")
    assert len(docs) == 1
    assert docs[0].page_content == "[§ Overview]\nMeridian text."
    assert docs[0].metadata["section"] == "Overview"


def test_retriever_fallback_paths(monkeypatch):
    def fake_post(payload, session_id=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(rag_mcp, "_post", fake_post)

    class LegacyDoc:
        page_content = "legacy body"
        metadata = {"section": "Old"}

    class LegacyRetriever:
        def invoke(self, query):
            return [LegacyDoc()]

    factory_calls = []
    def factory():
        factory_calls.append(1)
        return LegacyRetriever()

    # auto mode: falls back to the legacy factory
    retriever = rag_mcp.MCPRetriever(fallback_retriever_factory=factory)
    docs = retriever.invoke("q")
    assert len(docs) == 1 and docs[0].page_content == "legacy body"
    assert factory_calls == [1]

    # on mode: no fallback — degrades to empty
    strict = rag_mcp.MCPRetriever(fallback_retriever_factory=factory, allow_fallback=False)
    assert strict.invoke("q") == []
