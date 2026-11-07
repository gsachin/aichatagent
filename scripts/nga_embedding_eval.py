#!/usr/bin/env python3
"""Evaluate lexical vs dense chunk retrieval on a labeled query set.

This is an offline evaluation tool. It never changes the live retrieval flags
and should be pointed at a disposable snapshot with chunk embeddings built by
``scripts/nga_chunks.py --embed``. It reports recall/rank against hand-labeled
source URLs plus the latency of the lexical search, the dense vector scan and
the fused hybrid search.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.sitegraph.embeddings import get_embedding_provider  # noqa: E402
from app.sitegraph.hybrid import search_chunks  # noqa: E402
from app.sitegraph.indexer import index_version_for  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402


class CachedEmbedder:
    """Re-use query vectors so hybrid latency excludes provider round trips."""

    def __init__(self, model: str, vectors: dict[str, list[float]]) -> None:
        self.model = model
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


def load_queries(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    rows: list[dict] = []
    for line_number, raw in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError as exc:
            raise SystemExit(
                f"{target}:{line_number}: invalid JSON: {exc}"
            ) from exc
        query = str(row.get("query") or "").strip()
        expected = row.get("expected_urls") or row.get("expected_url") or []
        if isinstance(expected, str):
            expected = [expected]
        expected = [str(item) for item in expected if str(item).strip()]
        if not query or not expected:
            raise SystemExit(
                f"{target}:{line_number}: query and expected_urls are required"
            )
        rows.append(
            {
                "id": str(row.get("id") or f"R{line_number:02d}"),
                "query": query,
                "expected_urls": expected,
                "note": str(row.get("note") or ""),
            }
        )
    if not rows:
        raise SystemExit(f"{target}: no queries")
    return rows


def url_matches(url: str, fragments: list[str]) -> bool:
    decoded = unquote(str(url or "")).casefold()
    return any(
        unquote(fragment).casefold() in decoded for fragment in fragments
    )


def first_rank(urls: list[str], fragments: list[str]) -> int | None:
    for rank, url in enumerate(urls, start=1):
        if url_matches(url, fragments):
            return rank
    return None


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))),
    )
    return ordered[index]


def _hit(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate(
    *,
    db: str | Path,
    queries_path: str | Path,
    limit: int = 10,
    dense_limit: int = 50,
) -> dict:
    provider = get_embedding_provider()
    if provider is None:
        raise SystemExit(
            "no embedding provider: set EMBEDDING_PROVIDER=openai for the "
            "evaluation process (never in the live service)"
        )
    queries = load_queries(queries_path)
    store = GraphStore(db, busy_timeout_ms=30_000)
    run = store.latest_run() or {}
    index_version = index_version_for(str(run.get("graph_hash") or ""))

    started = perf_counter()
    vectors = provider.embed([row["query"] for row in queries])
    embedding_ms = (perf_counter() - started) * 1000
    vector_by_text = {
        row["query"]: vector for row, vector in zip(queries, vectors)
    }
    cached = CachedEmbedder(provider.model, vector_by_text)

    details: list[dict] = []
    lexical_ms: list[float] = []
    dense_ms: list[float] = []
    hybrid_ms: list[float] = []
    lexical_ranks: list[int | None] = []
    dense_ranks: list[int | None] = []
    hybrid_ranks: list[int | None] = []

    for row in queries:
        query = row["query"]
        expected = row["expected_urls"]

        started = perf_counter()
        lexical_hits = search_chunks(
            store,
            query,
            limit=limit,
            index_version=index_version,
            embedder=None,
        )
        lexical_ms.append((perf_counter() - started) * 1000)
        lexical_urls = [hit.node.url for hit in lexical_hits]

        started = perf_counter()
        dense_rows = store.search_chunk_embeddings(
            vector_by_text[query],
            model=provider.model,
            limit=dense_limit,
            index_version=index_version,
        )
        dense_ms.append((perf_counter() - started) * 1000)
        dense_urls = [str(chunk.get("url") or "") for chunk, _ in dense_rows]

        started = perf_counter()
        hybrid_hits = search_chunks(
            store,
            query,
            limit=limit,
            index_version=index_version,
            embedder=cached,
        )
        hybrid_ms.append((perf_counter() - started) * 1000)
        hybrid_urls = [hit.node.url for hit in hybrid_hits]

        lexical_rank = first_rank(lexical_urls, expected)
        dense_rank = first_rank(dense_urls, expected)
        hybrid_rank = first_rank(hybrid_urls, expected)
        lexical_ranks.append(lexical_rank)
        dense_ranks.append(dense_rank)
        hybrid_ranks.append(hybrid_rank)

        details.append(
            {
                "id": row["id"],
                "query": query,
                "note": row["note"],
                "expected_urls": expected,
                "lexical_rank": lexical_rank,
                "dense_rank": dense_rank,
                "hybrid_rank": hybrid_rank,
                "lexical_urls": lexical_urls,
                "dense_urls": dense_urls,
                "hybrid_urls": hybrid_urls,
            }
        )

    def summary(ranks: list[int | None]) -> dict:
        return {
            "hit_at_5": sum(1 for rank in ranks if _hit(rank, 5)),
            "hit_at_10": sum(1 for rank in ranks if _hit(rank, 10)),
            "mrr": round(
                _mean(
                    [
                        1.0 / rank if rank is not None else 0.0
                        for rank in ranks
                    ]
                ),
                4,
            ),
        }

    regressions = [
        row["id"]
        for row, lexical, hybrid in zip(details, lexical_ranks, hybrid_ranks)
        if _hit(lexical, limit) and not _hit(hybrid, limit)
    ]
    gains = [
        row["id"]
        for row, lexical, hybrid in zip(details, lexical_ranks, hybrid_ranks)
        if not _hit(lexical, limit) and _hit(hybrid, limit)
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(Path(db)),
        "model": provider.model,
        "index_version": index_version,
        "limit": limit,
        "dense_limit": dense_limit,
        "query_count": len(details),
        "query_embedding_ms": round(embedding_ms, 1),
        "latency_ms": {
            "lexical": {
                "p50": round(percentile(lexical_ms, 0.50), 1),
                "p95": round(percentile(lexical_ms, 0.95), 1),
            },
            "dense_scan": {
                "p50": round(percentile(dense_ms, 0.50), 1),
                "p95": round(percentile(dense_ms, 0.95), 1),
            },
            "hybrid": {
                "p50": round(percentile(hybrid_ms, 0.50), 1),
                "p95": round(percentile(hybrid_ms, 0.95), 1),
            },
        },
        "summary": {
            "lexical": summary(lexical_ranks),
            "dense_only": summary(dense_ranks),
            "hybrid": summary(hybrid_ranks),
        },
        "hybrid_gains": gains,
        "hybrid_regressions": regressions,
        "queries": details,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate lexical vs dense chunk retrieval."
    )
    parser.add_argument("--db", default=settings.serve_db_path)
    parser.add_argument(
        "--queries",
        default="eval/retrieval/chunk_queries.jsonl",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dense-limit", type=int, default=50)
    parser.add_argument("--report", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = evaluate(
        db=args.db,
        queries_path=args.queries,
        limit=max(1, int(args.limit)),
        dense_limit=max(1, int(args.dense_limit)),
    )
    if args.report:
        Path(args.report).write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Chunk retrieval evaluation")
        print(f"  db:      {result['db']}")
        print(f"  model:   {result['model']}")
        print(f"  queries: {result['query_count']}")
        print(f"  embedding latency: {result['query_embedding_ms']} ms")
        for name in ("lexical", "dense_only", "hybrid"):
            stats = result["summary"][name]
            print(
                f"  {name:10} hit@5={stats['hit_at_5']}/"
                f"{result['query_count']} hit@10={stats['hit_at_10']}/"
                f"{result['query_count']} mrr={stats['mrr']}"
            )
        for name, stats in result["latency_ms"].items():
            print(
                f"  {name:10} p50={stats['p50']} ms p95={stats['p95']} ms"
            )
        if result["hybrid_gains"]:
            print("  gains: " + ", ".join(result["hybrid_gains"]))
        if result["hybrid_regressions"]:
            print("  regressions: " + ", ".join(result["hybrid_regressions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
