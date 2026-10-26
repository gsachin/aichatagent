#!/usr/bin/env python3
"""Refresh DSPMU page content and freshness state (Phase 3).

Background only: it fetches allowlisted public HTML pages, extracts their main
text, stores a version and updates the body FTS index. The live answer path
reads the indexed content; it never calls this module.

Usage:
    .venv/bin/python scripts/nga_refresh.py --dry-run
    .venv/bin/python scripts/nga_refresh.py --allow-network --limit 2
    .venv/bin/python scripts/nga_refresh.py --allow-network \
        --node-id cms.hostel --node-id student-life.hostel --force
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.sitegraph.fetch import SiteFetcher  # noqa: E402
from app.sitegraph.refresh import RefreshOptions, refresh_graph  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh DSPMU page content and freshness state (Phase 3)."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--limit", type=int, default=settings.NGA_REFRESH_LIMIT)
    parser.add_argument("--ttl", type=int, default=settings.NGA_TTL_PAGE_S)
    parser.add_argument(
        "--min-chars", type=int, default=settings.NGA_MIN_PAGE_CHARS
    )
    parser.add_argument("--max-bytes", type=int, default=settings.NGA_MAX_BYTES)
    parser.add_argument("--timeout", type=float, default=settings.NGA_FETCH_TIMEOUT_S)
    parser.add_argument("--delay", type=float, default=settings.NGA_FETCH_DELAY_S)
    parser.add_argument(
        "--allowed-hosts",
        default=settings.NGA_ALLOWED_HOSTS,
    )
    parser.add_argument("--user-agent", default=settings.NGA_FETCH_USER_AGENT)
    parser.add_argument(
        "--node-id",
        action="append",
        default=[],
        help="refresh these node IDs (repeatable); ignores due ordering",
    )
    parser.add_argument("--section", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-robots", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = GraphStore(args.db)
    options = RefreshOptions(
        ttl_s=max(60, int(args.ttl)),
        limit=max(1, min(50, int(args.limit))),
        force=args.force,
        min_chars=max(1, int(args.min_chars)),
        node_ids=tuple(args.node_id),
        section=args.section,
    )

    if args.dry_run:
        due = store.due_nodes(
            limit=options.limit,
            force=options.force,
            node_ids=options.node_ids or None,
            section=options.section,
        )
        data = [
            {
                "node_id": node.node_id,
                "url": node.url,
                "title": node.title,
                "section": node.section,
            }
            for node in due
        ]
        if args.json:
            print(json.dumps({"due": data, "total": len(data)}, indent=2))
        else:
            print(f"due nodes: {len(data)}")
            for item in data:
                print(f"  {item['node_id']}  {item['url']}")
        return 0

    if not args.allow_network:
        print(
            "error: pass --allow-network to fetch, or --dry-run to list the "
            "due nodes",
            file=sys.stderr,
        )
        return 2

    hosts = tuple(
        host.strip()
        for host in args.allowed_hosts.split(",")
        if host.strip()
    )
    fetcher = SiteFetcher(
        hosts,
        user_agent=args.user_agent,
        timeout_s=args.timeout,
        delay_s=args.delay,
        max_bytes=args.max_bytes,
        respect_robots=not args.no_robots,
    )
    stats = refresh_graph(store, fetcher, options)
    if args.json:
        print(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("NGA refresh")
        print(f"  checked:             {stats.checked}")
        print(f"  fetched:             {stats.fetched}")
        print(f"  changed:             {stats.changed}")
        print(f"  unchanged:           {stats.unchanged}")
        print(f"  not modified:        {stats.not_modified}")
        print(f"  empty shells:        {stats.empty}")
        print(f"  soft 404:            {stats.soft_404}")
        print(f"  errors:              {stats.errors}")
        print(f"  documents seen:      {stats.documents}")
        print(f"  canonical duplicates:{stats.canonical_duplicates}")
    return 0 if stats.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
