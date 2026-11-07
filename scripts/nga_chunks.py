#!/usr/bin/env python3
"""Build the chunk index from stored page versions and documents.

Usage:
    .venv/bin/python scripts/nga_chunks.py --backfill
    .venv/bin/python scripts/nga_chunks.py --backfill --limit 50 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.sitegraph.chunker import (  # noqa: E402
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
)
from app.sitegraph.indexer import (  # noqa: E402
    index_all_chunks,
    index_version_for,
)
from app.sitegraph.store import GraphStore  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the NGA chunk index."
    )
    parser.add_argument("--db", default=settings.index_db_path)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--index-version", default="")
    parser.add_argument(
        "--embed",
        action="store_true",
        help="embed chunks that do not yet have vectors",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=settings.EMBEDDING_BATCH,
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.backfill:
        print("error: pass --backfill to index chunks", file=sys.stderr)
        return 2
    store = GraphStore(args.db, busy_timeout_ms=30_000)
    run = store.latest_run() or {}
    index_version = args.index_version or index_version_for(
        str(run.get("graph_hash") or "")
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = index_all_chunks(
        store,
        index_version=index_version,
        max_chars=max(200, int(args.max_chars)),
        overlap=max(0, int(args.overlap)),
        created_at=now,
        limit=(int(args.limit) if int(args.limit) > 0 else None),
    )
    result["index_version"] = index_version
    if args.embed:
        from app.sitegraph.embeddings import get_embedding_provider

        provider = get_embedding_provider()
        embedded = 0
        if provider is None:
            result["embedding_model"] = "off"
            result["embedded"] = 0
        else:
            try:
                while True:
                    rows = store.chunks_without_embedding(
                        provider.model,
                        limit=max(1, int(args.batch)),
                    )
                    if not rows:
                        break
                    vectors = provider.embed(
                        [str(row.get("text") or "") for row in rows]
                    )
                    store.put_chunk_embeddings(
                        [
                            {
                                "chunk_id": row["chunk_id"],
                                "model": provider.model,
                                "vector": vector,
                                "content_hash": row.get("content_hash") or "",
                                "created_at": now,
                            }
                            for row, vector in zip(rows, vectors)
                        ]
                    )
                    embedded += len(rows)
            except Exception as exc:  # noqa: BLE001 - CLI boundary
                result["embedding_error"] = f"{type(exc).__name__}: {exc}"
            result["embedding_model"] = provider.model
            result["embedded"] = embedded
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("NGA chunk index")
        for key, value in result.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
