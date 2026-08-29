"""MCP client adapter for the standalone enterprise-rag-core retrieval service.

Pure-httpx JSON-RPC over streamable HTTP — deliberately NO mcp-SDK import so
this repo's mcp==2.0.0 pin (used by app/mcp/server.py) is never loaded or
affected by the enterprise-rag-core service's mcp==2.1.1.

Contract (verified against the service's streamable-HTTP endpoint):
  - Accept header MUST list BOTH "application/json" and "text/event-stream"
    (the endpoint rejects application/json-only clients with 406).
  - Responses may arrive as plain JSON or SSE; _parse handles both.
  - Session sequence: POST initialize -> capture Mcp-Session-Id response
    header -> POST tools/call with that header. Session-expiry (400/404)
    triggers exactly one re-initialize + one re-call.

Env (read lazily per call so tests can monkeypatch):
  RAG_MCP_URL       default http://127.0.0.1:8010/mcp
  RAG_MCP_TIMEOUT   read timeout, default 2.5s (connect fixed at 1.0s)
  RAG_MCP_COOLDOWN  circuit-breaker cooldown seconds, default 30
  USE_MCP_RAG       auto | on | off (dispatch lives in app/rag.py)
"""
import json
import os
import time

import httpx

PROTOCOL_VERSION = "2025-06-18"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=1.0,
        read=float(_env("RAG_MCP_TIMEOUT", "2.5")),
        write=2.5,
        pool=2.5,
    )


def _headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        # Both types MUST be listed — the streamable-HTTP endpoint rejects
        # application/json-only clients (406).
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _parse(r: httpx.Response) -> dict | None:
    """Parses a streamable-HTTP JSON-RPC response body (plain JSON or SSE)."""
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        payload = None
        for line in r.text.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: "):])
        return payload
    try:
        return r.json()
    except Exception:
        return None


# ── HTTP choke point (single monkeypatch target for tests) ─────────────────

def _post(payload: dict, session_id: str | None = None) -> httpx.Response:
    with httpx.Client(timeout=_timeout()) as client:
        return client.post(
            _env("RAG_MCP_URL", "http://127.0.0.1:8010/mcp"),
            json=payload,
            headers=_headers(session_id),
        )


# ── Session state ──────────────────────────────────────────────────────────

_state: dict = {"session_id": None, "server_info": None}


def _reset_session() -> None:
    _state["session_id"] = None
    _state["server_info"] = None


def mcp_initialize() -> bool:
    """Initializes (or re-initializes) the MCP session. Returns success."""
    try:
        r = _post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "universityDemo-rag", "version": "1.0"},
            },
        })
        if r.status_code != 200:
            _record_failure(f"initialize HTTP {r.status_code}")
            return False
        session_id = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if not session_id:
            _record_failure("initialize: no Mcp-Session-Id header in response")
            return False
        _state["session_id"] = session_id
        _state["server_info"] = _parse(r)
        # Notify the server the client completed initialization.
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"},
              session_id=session_id)
        _clear_failure()
        return True
    except Exception as exc:                    # noqa: BLE001 — adapter boundary
        _record_failure(str(exc))
        return False


def mcp_call_tool(name: str, arguments: dict) -> dict | None:
    """Calls a tool on the initialized session. One re-init + re-call on
    session-expiry errors; None on any failure."""
    def _call() -> httpx.Response:
        return _post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, session_id=_state["session_id"])

    if not _state["session_id"] and not mcp_initialize():
        return None

    try:
        r = _call()
        if r.status_code in (400, 404):
            # session expired server-side — re-initialize once and retry
            _reset_session()
            if not mcp_initialize():
                return None
            r = _call()
        if r.status_code != 200:
            _record_failure(f"tools/call HTTP {r.status_code}")
            return None
        body = _parse(r)
        if not body or body.get("error"):
            _record_failure(f"tools/call JSON-RPC error: {body and body.get('error')}")
            return None
        result = body.get("result", {})
        content = result.get("content") or []
        text = content[0].get("text", "") if content else ""
        _clear_failure()
        return json.loads(text) if text else {}
    except Exception as exc:                    # noqa: BLE001
        _record_failure(str(exc))
        return None


# ── Retrieval helper ───────────────────────────────────────────────────────

def mcp_retrieve(query: str, top_k: int = 5) -> list[dict] | None:
    """Retrieve context chunks from the service. None on failure; [] is a
    legitimate empty result."""
    result = mcp_call_tool("retrieve_context", {"query": query, "top_k": top_k})
    if result is None:
        return None
    return result.get("chunks", [])


def format_legacy_chunks(chunks: list[dict]) -> str:
    """Renders service chunks in the exact legacy retrieve_context shape:
    '[§ {section}]\\n{content}' joined by '\\n\\n---\\n\\n' (SYSTEM_PROMPT rule 3
    relies on the section label)."""
    parts = []
    for c in chunks:
        section = (c.get("section_title") or "").strip()
        body = (c.get("content") or "").strip()
        parts.append(f"[§ {section}]\n{body}" if section else body)
    return "\n\n---\n\n".join(parts)


def mcp_retrieve_context(query: str, top_k: int = 5) -> str:
    """Retrieve + render; '' on failure (legacy parity)."""
    chunks = mcp_retrieve(query, top_k)
    if chunks is None:
        return ""
    return format_legacy_chunks(chunks)


# ── LangChain-compatible retriever shim (for the LCEL chain sites) ─────────

class MCPDocument:
    """Duck-typed like langchain_core Document (page_content + metadata) —
    same pattern as the legacy _FakeDoc shim."""

    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class MCPRetriever:
    """Retriever whose .invoke() hits the MCP service, falling back to a lazy
    legacy retriever factory on failure (auto mode only)."""

    def __init__(self, top_k: int = 5, fallback_retriever_factory=None,
                 allow_fallback: bool = True):
        self._top_k = top_k
        self._fallback_factory = fallback_retriever_factory
        self._allow_fallback = allow_fallback

    def invoke(self, query: str) -> list[MCPDocument]:
        chunks = mcp_retrieve(query, self._top_k)
        if chunks is None:
            if self._allow_fallback and self._fallback_factory is not None:
                retriever = self._fallback_factory()
                if retriever is not None:
                    return list(retriever.invoke(query))
            return []
        return [
            MCPDocument(
                page_content=f"[§ {c.get('section_title', '')}]\n{c.get('content', '')}"
                if c.get("section_title") else c.get("content", ""),
                metadata={"section": c.get("section_title", "")},
            )
            for c in chunks
        ]


# ── Circuit breaker ────────────────────────────────────────────────────────

_breaker: dict = {"failed_at": None, "last_error": None}


def _record_failure(error: str) -> None:
    _breaker["failed_at"] = time.monotonic()
    _breaker["last_error"] = error


def _clear_failure() -> None:
    _breaker["failed_at"] = None
    _breaker["last_error"] = None


def mcp_available() -> bool:
    """True unless a recent failure put the breaker into cooldown."""
    if _breaker["failed_at"] is None:
        return True
    cooldown = float(_env("RAG_MCP_COOLDOWN", "30"))
    return (time.monotonic() - _breaker["failed_at"]) >= cooldown


def mcp_rag_status() -> dict:
    """Operational snapshot for logs / the migration script."""
    return {
        "mode": _env("USE_MCP_RAG", "auto"),
        "url": _env("RAG_MCP_URL", "http://127.0.0.1:8010/mcp"),
        "session_ok": _state["session_id"] is not None,
        "last_error": _breaker["last_error"],
        "cooldown_active": not mcp_available(),
    }
