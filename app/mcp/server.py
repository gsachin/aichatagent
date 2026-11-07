"""
MCP server — exposes lead-management tools to AI clients.

Tries to use the ``mcp`` Python SDK (FastMCP) with SSE transport.
If the SDK is not available, falls back to simple REST-style endpoints
that accept JSON-RPC-like messages.

Endpoints (mounted in app/main.py):
    GET  /mcp/sse       — SSE stream for MCP client connection
    POST /mcp/messages  — JSON-RPC message handler
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger("mcp.server")

# ── Detect MCP SDK availability ───────────────────────────────────────

_MCP_SDK_AVAILABLE = False
try:
    import mcp.server.fastmcp  # noqa: F401

    _MCP_SDK_AVAILABLE = True
    logger.info("MCP SDK detected — using FastMCP")
except ImportError:
    logger.info("MCP SDK not installed — using REST fallback")


# ── Tool dispatching ──────────────────────────────────────────────────


def _handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call and return JSON-RPC-compatible result."""
    from app.mcp.tools import invoke_tool

    result = invoke_tool(tool_name, arguments)
    return {"result": result}


def _handle_list_tools() -> dict[str, Any]:
    """Return available tools in JSON-RPC format."""
    from app.mcp.tools import list_tools

    return {"tools": list_tools()}


# ── REST-fallback handlers (used when MCP SDK is unavailable) ─────────


async def handle_sse_request(request) -> Any:
    """
    SSE endpoint handler.

    When the MCP SDK is available, delegates to FastMCP.
    Otherwise, returns a simple SSE stream.
    """
    if _MCP_SDK_AVAILABLE:
        # Delegate to FastMCP — this would require the full SDK setup
        # For now, fall through to REST fallback
        pass

    # REST fallback: simple SSE stream that announces available tools
    from starlette.responses import StreamingResponse

    async def event_stream():
        tools = _handle_list_tools()
        yield f"data: {json.dumps(tools)}\n\n"
        # Keep connection alive
        import asyncio

        while True:
            await asyncio.sleep(30)
            yield ": keepalive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def handle_messages_request(request) -> Any:
    """
    JSON-RPC message handler endpoint.

    Accepts: {"method": "tools/list" | "tools/call", "params": {...}}
    Returns: JSON-RPC response.
    """
    from fastapi.responses import JSONResponse

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status_code=400,
        )

    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id", str(uuid.uuid4()))

    logger.info(f"MCP message: method={method}")

    if method == "tools/list":
        result = _handle_list_tools()
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = _handle_tool_call(tool_name, arguments)
    elif method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "university-admissions-voice-assistant",
                "version": "1.0.0",
            },
        }
    else:
        result = {"error": f"Unknown method: {method}"}

    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, **result},
    )
