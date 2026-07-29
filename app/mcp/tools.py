"""
MCP tool registry — maps tool name strings to their implementation
functions (from app.leads.mcp_tools).

This indirection lets us:
1. Register tools with the FastMCP server (if the mcp SDK is available).
2. Expose the same tools via REST endpoints (fallback mode).
3. Add new tools by registering them here and in mcp_tools.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("mcp.tools")

# ── Tool registry ─────────────────────────────────────────────────────

TOOLS: dict[str, dict] = {
    "add_lead": {
        "function": None,  # lazy-loaded
        "description": "Add a new lead to the system (or return existing by phone number).",
        "parameters": {
            "phone_number": {"type": "str", "required": True, "description": "Lead's phone number"},
            "name": {"type": "str", "required": False, "default": "", "description": "Full name"},
            "email": {"type": "str", "required": False, "default": "", "description": "Email address"},
            "program_interest": {"type": "str", "required": False, "default": "", "description": "Program they're interested in"},
            "source": {"type": "str", "required": False, "default": "manual", "description": "Lead source — whatsapp, streamlit, manual, etc."},
            "notes": {"type": "str", "required": False, "default": "", "description": "Free-text notes"},
        },
    },
    "update_lead": {
        "function": None,
        "description": "Update fields on an existing lead. Only provided fields are changed.",
        "parameters": {
            "lead_id": {"type": "str", "required": True, "description": "UUID of the lead to update"},
            "name": {"type": "str", "required": False, "default": None, "description": "New name"},
            "email": {"type": "str", "required": False, "default": None, "description": "New email"},
            "program_interest": {"type": "str", "required": False, "default": None, "description": "New program interest"},
            "status": {"type": "str", "required": False, "default": None, "description": "New status — pending, in_progress, completed, failed, unreachable"},
            "notes": {"type": "str", "required": False, "default": None, "description": "New notes"},
        },
    },
    "trigger_call": {
        "function": None,
        "description": "Queue an immediate outbound call to a lead.",
        "parameters": {
            "lead_id": {"type": "str", "required": True, "description": "UUID of the lead to call"},
        },
    },
    "view_conversations": {
        "function": None,
        "description": "Retrieve conversation history for a lead.",
        "parameters": {
            "lead_id": {"type": "str", "required": True, "description": "UUID of the lead"},
            "limit": {"type": "int", "required": False, "default": 10, "description": "Max conversations to return"},
            "channel": {"type": "str", "required": False, "default": None, "description": "Optional filter — whatsapp, streamlit, inbound_call, outbound_call"},
        },
    },
    "schedule_follow_up": {
        "function": None,
        "description": "Schedule a follow-up call or message for a lead.",
        "parameters": {
            "lead_id": {"type": "str", "required": True, "description": "UUID of the lead"},
            "scheduled_at": {"type": "str", "required": True, "description": "ISO 8601 datetime, e.g. 2026-08-01T14:00:00Z"},
            "type": {"type": "str", "required": False, "default": "call", "description": "'call' or 'message'"},
            "notes": {"type": "str", "required": False, "default": "", "description": "Reason or context"},
        },
    },
    "check_lead_status": {
        "function": None,
        "description": "Get current status and details for a lead by ID or phone number.",
        "parameters": {
            "lead_id": {"type": "str", "required": False, "default": None, "description": "UUID of the lead"},
            "phone_number": {"type": "str", "required": False, "default": None, "description": "Phone number of the lead"},
        },
    },
    "list_leads": {
        "function": None,
        "description": "List leads, optionally filtered by status.",
        "parameters": {
            "status": {"type": "str", "required": False, "default": None, "description": "Optional filter — pending, in_progress, completed, failed, unreachable"},
            "limit": {"type": "int", "required": False, "default": 50, "description": "Max leads to return"},
        },
    },
}


def _ensure_loaded():
    """Lazy-load tool functions from app.leads.mcp_tools."""
    if TOOLS["add_lead"]["function"] is not None:
        return  # already loaded

    from app.leads.mcp_tools import (
        add_lead,
        update_lead,
        trigger_call,
        view_conversations,
        schedule_follow_up,
        check_lead_status,
        list_leads,
    )

    TOOLS["add_lead"]["function"] = add_lead
    TOOLS["update_lead"]["function"] = update_lead
    TOOLS["trigger_call"]["function"] = trigger_call
    TOOLS["view_conversations"]["function"] = view_conversations
    TOOLS["schedule_follow_up"]["function"] = schedule_follow_up
    TOOLS["check_lead_status"]["function"] = check_lead_status
    TOOLS["list_leads"]["function"] = list_leads
    logger.info("MCP tool functions loaded")


def list_tools() -> list[dict]:
    """Return tool metadata for MCP tool listing."""
    _ensure_loaded()
    return [
        {
            "name": name,
            "description": info["description"],
            "parameters": info["parameters"],
        }
        for name, info in TOOLS.items()
    ]


def invoke_tool(name: str, arguments: dict) -> dict | list:
    """
    Invoke a named tool with the given keyword arguments.

    Returns the tool's result (dict or list), or an error dict.
    """
    _ensure_loaded()

    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    fn = TOOLS[name]["function"]
    if fn is None:
        return {"error": f"Tool '{name}' failed to load"}

    try:
        return fn(**arguments)
    except TypeError as e:
        return {"error": f"Invalid arguments for '{name}': {e}"}
    except Exception as e:
        logger.exception(f"Tool '{name}' failed")
        return {"error": f"Tool execution failed: {e}"}
