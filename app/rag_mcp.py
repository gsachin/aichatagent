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
  RAG_MCP_TIMEOUT   read timeout, default 6.0s (connect fixed at 1.0s).
                    Raised from 2.5s: under load ERC's embedding call queues
                    behind Ollama generation, so a 2.5s read timeout fired on
                    a service that answers in milliseconds -- and the fallback
                    then paid for a SECOND embedding. Waiting is strictly
                    cheaper than timing out and re-retrieving locally.
  RAG_MCP_COOLDOWN  circuit-breaker cooldown seconds, default 30
  USE_MCP_RAG       auto | on | off (dispatch lives in app/rag.py)
"""
import json
import os
import time
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _timeout(probe: bool = False) -> httpx.Timeout:
    """Request budget. A half-open probe gets a fraction of the serving budget.

    US-013 AC-2: the point of a probe is to learn cheaply whether the service is
    back. Paying the full read timeout to rediscover "still down", once per
    cooldown window for the length of an outage, is the cost this removes. The
    floor keeps a probe from being so short that a merely-busy service reads as
    dead.
    """
    read = float(_env("RAG_MCP_TIMEOUT", "6.0"))
    if probe:
        fraction = float(_env("RAG_MCP_PROBE_FRACTION", "0.25"))
        read = max(read * fraction, 0.5)
    else:
        # US-013 AC-1: one failure must cost one budget, not two. The caller
        # pays the primary read timeout AND then a full local re-retrieval, so
        # the primary attempt has to leave room for the fallback inside the
        # retrieval budget. The default budget (8 s) minus the reserve (1.5 s)
        # is 6.5 s and does not bind on the tuned 6 s timeout -- it binds only
        # when someone sets a tighter budget deliberately.
        budget = float(_env("RAG_RETRIEVAL_BUDGET", "8.0"))
        reserve = float(_env("RAG_FALLBACK_RESERVE", "1.5"))
        read = max(min(read, budget - reserve), 0.5)
    return httpx.Timeout(connect=1.0, read=read, write=2.5, pool=2.5)


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

#: US-008 follow-on. The original built a NEW httpx.Client per request, so every
#: `tools/call` paid a fresh TCP connection (plus a session re-check) against a
#: 2.5 s read budget. Under two-caller load that is enough to cross the timeout
#: on a service whose own handler answers in milliseconds — and the cost is paid
#: TWICE, because the timeout is followed by a full local re-retrieval.
#:
#: One client, reused. httpx.Client is thread-safe for requests, and _post runs
#: on worker threads, so a module-level client is correct here.
_client: "httpx.Client | None" = None


def _get_client() -> httpx.Client:
    """The shared client. Created once, with the configured timeout."""
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=_timeout(),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
    return _client


def _post(payload: dict, session_id: str | None = None) -> httpx.Response:
    # REVERTED 2026-09-19 for a controlled test. The module-level client above
    # was introduced to stop paying a fresh TCP connection per request, but it
    # was never confirmed under load — and it shares one connection pool across
    # every worker thread. With the read timeout raised to 6 s, a stalled call
    # now holds a pool slot six times longer than before, and the pool has
    # max_connections=8. The long-run failures (both sessions dropped at 28
    # samples on a keepalive ping timeout) postdate both changes, so this
    # restores the per-request client while KEEPING the 6 s timeout — isolating
    # one variable at a time.
    with httpx.Client(timeout=_timeout(probe=_probe_in_flight())) as client:
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

# langchain_core is only required by the LCEL chain sites (app.py,
# admissions_bot.py). app/main.py imports this module at FastAPI startup, and
# this machine blocks the langchain->langsmith->xxhash DLL chain (see
# app/main.py), so the import is guarded: without langchain_core the class
# degrades to a plain duck-typed shim, which is all the string-based path
# (retrieve_context) needs.
try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    from pydantic import PrivateAttr
    _HAS_LANGCHAIN_CORE = True
except Exception:                                   # pragma: no cover
    Document = None
    BaseRetriever = object
    _HAS_LANGCHAIN_CORE = False


class MCPDocument:
    """Duck-typed like langchain_core Document (page_content + metadata) —
    same pattern as the legacy _FakeDoc shim; used only when langchain_core
    is unavailable (degraded, non-LCEL path)."""

    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class MCPRetriever(BaseRetriever):
    """Retriever for the LCEL chain sites: retrieval hits the MCP service,
    falling back to a lazy legacy retriever factory on failure (auto mode).

    MUST subclass langchain_core's BaseRetriever: create_retrieval_chain calls
    retriever.with_config() and, for non-BaseRetriever inputs, treats the
    retriever as a Runnable[dict, ...] — passing the whole input dict instead
    of the query string. The legacy path always satisfied this (Chroma's
    as_retriever returns a real BaseRetriever); the original duck-typed shim
    crashed with AttributeError: no attribute 'with_config'.
    """

    top_k: int = 5

    if _HAS_LANGCHAIN_CORE:
        # Pydantic v2 model: undeclared attrs must be PrivateAttr.
        _fallback_factory: Any = PrivateAttr(default=None)
        _allow_fallback: bool = PrivateAttr(default=True)

    def __init__(self, top_k: int = 5, fallback_retriever_factory=None,
                 allow_fallback: bool = True):
        if _HAS_LANGCHAIN_CORE:
            super().__init__(top_k=top_k)
        else:                                        # pragma: no cover
            self.top_k = top_k
        self._fallback_factory = fallback_retriever_factory
        self._allow_fallback = allow_fallback

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list:
        chunks = mcp_retrieve(query, self.top_k)
        if chunks is None:
            if self._allow_fallback and self._fallback_factory is not None:
                retriever = self._fallback_factory()
                if retriever is not None:
                    return list(retriever.invoke(query))
            return []
        doc_cls = Document if Document is not None else MCPDocument
        return [
            doc_cls(
                page_content=f"[§ {c.get('section_title', '')}]\n{c.get('content', '')}"
                if c.get("section_title") else c.get("content", ""),
                metadata={"section": c.get("section_title", "")},
            )
            for c in chunks
        ]

    if not _HAS_LANGCHAIN_CORE:                      # pragma: no cover
        def invoke(self, query: str) -> list:
            """Plain shim used when langchain_core is unavailable."""
            return self._get_relevant_documents(query)


# ── Circuit breaker ────────────────────────────────────────────────────────

#: US-013 / BRD-14. Three states, not two.
#:
#: The original breaker had only closed and open, so once the cooldown elapsed
#: the NEXT caller sent a full-timeout request to a service that was probably
#: still dead -- paying the maximum to learn nothing, once per window, for the
#: whole outage. `half_open` replaces that blind retry with one cheap probe.
#:
#:   closed     normal serving
#:   open       a call failed; callers take the local rung until the cooldown
#:   half_open  cooldown elapsed; exactly ONE caller may probe at a fraction of
#:              the read timeout, and its result decides the next state
_breaker: dict = {
    "failed_at": None,
    "last_error": None,
    "probing": False,   # a probe is in flight (single-flight, AC-3)
    "probe_claimed_at": None,
    "opens": 0,         # times the breaker opened
    "probes": 0,        # probes actually issued
}


def _record_failure(error: str) -> None:
    was_closed = _breaker["failed_at"] is None
    _breaker["failed_at"] = time.monotonic()
    _breaker["last_error"] = error
    _breaker["probing"] = False
    _breaker["probe_claimed_at"] = None
    if was_closed:
        _breaker["opens"] += 1


def _clear_failure() -> None:
    _breaker["failed_at"] = None
    _breaker["last_error"] = None
    _breaker["probing"] = False
    _breaker["probe_claimed_at"] = None


def breaker_mode() -> str:
    """`probe` (shipped) or `flat` (the pre-US-013 behaviour).

    US-013's revert is a SETTING, not a code change (`BRD-15`). The first
    version of the rollback demonstration reconstructed the old two-state logic
    by hand, which proved the arithmetic of the defect but was my reconstruction
    rather than the code — a weaker claim than the five stories whose reverts
    are genuine setting flips. `RAG_BREAKER_MODE=flat` puts the old behaviour
    back for real.

    Read per call, like the cooldown, so the flip takes effect without an import
    and a restart.

      probe  closed / open / half_open — one cheap probe per cooldown window
      flat   closed / open           — an elapsed cooldown serves the primary
                                       again at the FULL timeout, blind
    """
    return "flat" if os.environ.get("RAG_BREAKER_MODE", "probe").strip().lower() == "flat" \
        else "probe"


def breaker_state() -> str:
    """`closed`, `open` or `half_open` (US-013 AC-2).

    In `flat` mode `half_open` is never returned: the cooldown elapsing reads as
    `closed`, so the next caller is admitted to the primary at the full timeout.
    That is precisely the defect US-013 removed, and it is reachable again by
    configuration rather than by editing code.
    """
    if _breaker["failed_at"] is None:
        return "closed"
    cooldown = float(_env("RAG_MCP_COOLDOWN", "30"))
    elapsed = (time.monotonic() - _breaker["failed_at"]) >= cooldown
    if breaker_mode() == "flat":
        return "closed" if elapsed else "open"
    return "half_open" if elapsed else "open"


def _probe_in_flight() -> bool:
    """True only for the request that holds the probe slot.

    `_post` reads this to pick the short budget, so only the probe pays a
    reduced timeout -- a healthy serving call keeps the full one.

    A slot older than the full serving budget plus a second is treated as
    abandoned. Without that, one code path returning without recording a
    verdict would hold the slot forever and `auto` mode would stop probing
    permanently -- a worse failure than the one this story fixes.
    """
    if not _breaker["probing"]:
        return False
    claimed = _breaker.get("probe_claimed_at")
    if claimed is None:
        return True
    budget = float(_env("RAG_MCP_TIMEOUT", "6.0")) + 1.0
    if (time.monotonic() - claimed) > budget:
        _breaker["probing"] = False
        _breaker["probe_claimed_at"] = None
        return False
    return True


def claim_probe() -> bool:
    """Claim the single half-open probe slot, or refuse.

    AC-3: the probe is not duplicated per caller. Two callers meeting the same
    recovered-or-not service produce one probe and one local-rung answer, not
    two probes competing for the same dead service.
    """
    # There is no probe in `flat` mode -- that is what the mode removes.
    if breaker_mode() != "probe":
        return False
    if breaker_state() != "half_open" or _probe_in_flight():
        return False
    _breaker["probing"] = True
    _breaker["probe_claimed_at"] = time.monotonic()
    _breaker["probes"] += 1
    return True


def release_probe() -> None:
    """Give the slot back without changing the breaker's verdict."""
    _breaker["probing"] = False
    _breaker["probe_claimed_at"] = None


def mcp_available() -> bool:
    """True when a caller may attempt the primary (closed, or probe-eligible)."""
    return breaker_state() != "open"


def mcp_rag_status() -> dict:
    """Operational snapshot for logs / the migration script."""
    return {
        "mode": _env("USE_MCP_RAG", "auto"),
        "url": _env("RAG_MCP_URL", "http://127.0.0.1:8010/mcp"),
        "session_ok": _state["session_id"] is not None,
        "last_error": _breaker["last_error"],
        "cooldown_active": breaker_state() == "open",
        "breaker_state": breaker_state(),
        "breaker_mode": breaker_mode(),
        "breaker_opens": _breaker["opens"],
        "breaker_probes": _breaker["probes"],
        "probe_read_timeout_s": round(_timeout(probe=True).read, 3),
    }
