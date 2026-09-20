"""
Salesforce user-API integration.

Build order (see ``doc/salesforce/PLAN_SALESFORCE_USER_INTEGRATION.md`` §9):

    Phase 1  session.py, schema.py   conversation identity — no network, no CRM
    Phase 2  config, client, identity, sync, outbox
    Phase 3+ channel wiring (WhatsApp, outbound voice, inbound voice, web chat)

Phase 1 is deliberately inert: importing this package performs no I/O and makes
no outbound calls. Everything that talks to Salesforce arrives in Phase 2 behind
``CRM_ENABLED``, which defaults to off — a deployment turns it on in its .env.
"""

__all__: list[str] = []
