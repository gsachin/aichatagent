"""Chunk-level hybrid retrieval.

BM25 is the always-on signal. Dense retrieval is optional and only used when
an embedder is supplied and its vectors exist. Reciprocal rank fusion (RRF)
combines both; node-level metadata supplies provenance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.sitegraph.models import SiteNode
from app.sitegraph.search import build_fts_match, normalize_query
from app.sitegraph.store import GraphStore

logger = logging.getLogger("sitegraph.hybrid")

_RRF_K = 60
_SNIPPET_CHARS = 240


@dataclass(frozen=True)
class ChunkHit:
    chunk: dict
    node: SiteNode
    score: float
    snippet: str
    bm25_rank: int | None = None
    dense_rank: int | None = None


def _snippet(text: str, tokens: tuple[str, ...]) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    positions = [
        value.lower().find(token) for token in tokens
        if token and value.lower().find(token) >= 0
    ]
    if not positions:
        return value[: _SNIPPET_CHARS]
    start = max(0, min(positions) - 80)
    end = min(len(value), start + _SNIPPET_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(value) else ""
    return f"{prefix}{value[start:end]}{suffix}"


def search_chunks(
    store: GraphStore,
    query: str,
    *,
    limit: int = 10,
    pii_guard: bool = False,
    embedder=None,
    dense_limit: int = 50,
    index_version: str | None = None,
) -> list[ChunkHit]:
    """Return fused chunk hits, best first. Empty when the chunk index is empty."""
    normalized = normalize_query(query)
    if not normalized.tokens:
        return []
    match = build_fts_match(normalized.tokens)
    candidate_limit = max(50, int(limit) * 5)
    try:
        bm25_rows = store.search_chunk_fts(
            match,
            limit=candidate_limit,
            pii_guard=pii_guard,
            index_version=index_version,
        )
    except Exception:  # noqa: BLE001 - fall back to node search on index issues
        logger.exception("chunk BM25 search failed")
        bm25_rows = []
    if not bm25_rows and embedder is None:
        return []

    fused: dict[str, dict] = {}
    scores: dict[str, float] = {}
    bm25_rank: dict[str, int] = {}
    dense_rank: dict[str, int] = {}

    for rank, (chunk, _rank_value) in enumerate(bm25_rows, start=1):
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id:
            continue
        fused[chunk_id] = chunk
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (
            _RRF_K + rank
        )
        bm25_rank[chunk_id] = rank

    if embedder is not None:
        try:
            vector = embedder.embed([query])[0]
            dense_rows = store.search_chunk_embeddings(
                vector,
                model=getattr(embedder, "model", ""),
                limit=dense_limit,
                pii_guard=pii_guard,
                index_version=index_version,
            )
        except Exception:  # noqa: BLE001 - dense is optional
            logger.exception("dense chunk search failed")
            dense_rows = []
        for rank, (chunk, _score) in enumerate(dense_rows, start=1):
            chunk_id = str(chunk.get("chunk_id") or "")
            if not chunk_id:
                continue
            fused.setdefault(chunk_id, chunk)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (
                _RRF_K + rank
            )
            dense_rank[chunk_id] = rank

    hits: list[ChunkHit] = []
    for chunk_id, score in sorted(
        scores.items(), key=lambda item: (-item[1], item[0])
    ):
        chunk = fused[chunk_id]
        node = store.get_node(str(chunk.get("node_id") or ""))
        if node is None:
            continue
        hits.append(
            ChunkHit(
                chunk=chunk,
                node=node,
                score=round(score, 8),
                snippet=_snippet(
                    str(chunk.get("text") or ""), normalized.tokens
                ),
                bm25_rank=bm25_rank.get(chunk_id),
                dense_rank=dense_rank.get(chunk_id),
            )
        )
        if len(hits) >= max(1, int(limit)):
            break
    return hits


__all__ = ["ChunkHit", "search_chunks"]
