#!/usr/bin/env python3
"""Search the compiled DSPMU navigation graph (Phase 2).

Reads the SQLite graph produced by ``scripts/nga_compile.py``. No network
call, no LLM call, no answer-path integration.

Usage:
    .venv/bin/python scripts/nga_search.py --query "hostel"
    .venv/bin/python scripts/nga_search.py --query "फीस" --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.sitegraph.tools import SiteToolServer  # noqa: E402

DEFAULT_DB = PROJECT_ROOT / "content/dspmu/nga_graph.sqlite3"
DEFAULT_NOTICES = PROJECT_ROOT / "content/dspmu/notices.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the compiled DSPMU navigation graph (Phase 2)."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--notices",
        default=str(DEFAULT_NOTICES),
        help="notice JSONL path; pass an empty string to disable site.latest",
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--section", default=None)
    parser.add_argument(
        "--type",
        dest="content_type",
        default=None,
        choices=("html", "pdf", "section", "external"),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    notices = args.notices.strip() or None
    server = SiteToolServer(args.db, notices_path=notices)
    result = server.call(
        "site.search",
        {
            "query": args.query,
            "limit": args.limit,
            "section": args.section,
            "content_type": args.content_type,
        },
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 1

    if result["status"] != "ok":
        print(
            f"error: {result['status']}: {result['error']}",
            file=sys.stderr,
        )
        return 1
    data = result["data"]
    print(
        f"query: {args.query!r}  normalized: {data['normalized_query']!r}  "
        f"language: {data['language']}  results: {data['total']}"
    )
    for item in data["results"]:
        crumb = " > ".join(part["title"] for part in item["breadcrumb"])
        print(
            f"  {item['score']:>6.2f}  {item['title']}  "
            f"[{item['content_type']}]  {item['url']}"
        )
        print(f"          {crumb}")
        print(f"          {item['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
