"""Offline-first site index for the DSPMU knowledge base.

The package is deliberately outside the request path. It builds plain markdown
and JSON files that the existing document-context and grounding code already
understand:

* ``content/dspmu/site_map.md`` is the full, hierarchical knowledge file. It
  uses ``##`` / ``###`` headings and includes a ``## Undergraduate Programs``
  section that ``app.kb_entities`` can parse.
* ``content/dspmu/core_cards.md`` is the compact, high-signal prefix for the
  voice prompt.
* ``content/dspmu/facts.json`` is the structured record for the next milestone.
* ``content/dspmu/notices.jsonl`` is the non-PII notice manifest.

Nothing here imports ``app.config`` or ``app.rag``. The builder must be usable
without a live LLM and without starting the application.
"""

from __future__ import annotations

__all__ = ["build", "discover", "extract", "facts", "fetch"]
