"""
Lead management package — University Admissions Voice Assistant.

Provides:
- schema.py   : SQL CREATE TABLE statements for leads, conversations,
                follow_ups, and call_queue tables.
- models.py   : Async CRUD functions for all lead-management tables.
- service.py  : Business logic — lead dedup, status transitions,
                conversation logging, post-call handling.
- mcp_tools.py: MCP tool implementations (delegates to models + service).
"""
