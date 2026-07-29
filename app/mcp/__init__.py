"""
MCP (Model Context Protocol) server package.

Exposes lead-management tools to AI clients (Claude Desktop, etc.)
via SSE transport.  If the ``mcp`` Python SDK is not available, a
lightweight REST-based fallback is used so tools remain accessible.

Registered tools:
    - add_lead
    - update_lead
    - trigger_call
    - view_conversations
    - schedule_follow_up
    - check_lead_status
    - list_leads
"""
