"""The evidence envelope shared by prompt assembly and the grounding guard.

A bundle is immutable and deliberately small. The guard judges ``text``; the
IDs and source URLs are provenance for logs, citations and the future router.
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
    source_urls: tuple[str, ...] = ()
    valid_until: str | None = None
