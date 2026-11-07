"""The evidence envelope shared by prompt assembly and the grounding guard.

A bundle is immutable and deliberately small. The guard judges ``text``; the
fact, node and notice IDs plus the source URLs are provenance for logs,
citations and the selective router.
Keeping them together is the point: the guard must never re-read a different
document than the one the model received.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextBundle:
    text: str
    fact_ids: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    notice_ids: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()
    #: Structured citations (title, url, evidence snippet) for reference
    #: rendering and voice→WhatsApp delivery. Empty on the static path.
    citations: tuple[dict, ...] = ()
    valid_until: str | None = None
    #: The route that produced this bundle (``fee``, ``notices``,
    #: ``fallback``, ...), or the live suffix when request-time website text
    #: was appended. Provenance only: no consumer makes an answer decision on
    #: it, but it is what lets the live-retrieval gate stay narrow.
    kind: str = ""
