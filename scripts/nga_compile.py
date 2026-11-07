#!/usr/bin/env python3
"""Compile the DSPMU navigation graph (Phase 1).

Offline by default: reads ``content/dspmu/cache/current/{home.html,sitemap.xml}``.
With ``--allow-network`` it fetches missing sources through the polite
``SourceBook``. The answer path is not touched; ``NGA_ENABLED`` stays false.

Usage:
    .venv/bin/python scripts/nga_compile.py
    .venv/bin/python scripts/nga_compile.py --allow-network --verbose
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
from app.sitegraph.compiler import compile_graph  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402
from app.siteindex.fetch import SourceBook, SourceSpec  # noqa: E402

DEFAULT_BASE_URL = "https://dspmuranchi.ac.in"
DEFAULT_SITEMAP_URL = "https://dspmuranchi.ac.in/sitemap.xml"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "content/dspmu/cache"
DEFAULT_DB = PROJECT_ROOT / "content/dspmu/nga_graph.sqlite3"
DEFAULT_COVERAGE = PROJECT_ROOT / "content/dspmu/nga_coverage.json"


def _load_source(book: SourceBook, key: str, url: str, relpath: str,
                 kind: str):
    return book.load(SourceSpec(key=key, url=url, relpath=relpath, kind=kind))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile the DSPMU navigation graph (Phase 1)."
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP_URL)
    parser.add_argument(
        "--allowed-hosts",
        default="dspmuranchi.ac.in,www.dspmuranchi.ac.in",
        help="comma-separated hostnames allowed in the graph",
    )
    parser.add_argument(
        "--blocked-tokens",
        default="",
        help="comma-separated PII/staff path tokens (empty uses the default)",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--coverage", default=str(DEFAULT_COVERAGE))
    parser.add_argument("--min-coverage", type=float, default=0.95)
    guard = parser.add_mutually_exclusive_group()
    guard.add_argument(
        "--pii-guard",
        dest="pii_guard",
        action="store_true",
        help="restore the conservative result/staff PII exclusions",
    )
    guard.add_argument(
        "--no-pii-guard",
        dest="pii_guard",
        action="store_false",
        help="use the university's published pages without PII exclusions",
    )
    parser.set_defaults(pii_guard=settings.NGA_PII_GUARD_ENABLED)
    parser.add_argument(
        "--allow-below-gate",
        action="store_true",
        help="write the artifacts even when coverage is below --min-coverage",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_url = args.base_url.rstrip("/")
    book = SourceBook(args.cache_dir, allow_network=args.allow_network)

    try:
        home = _load_source(
            book, "home", base_url + "/", "current/home.html", "html"
        )
        sitemap = _load_source(
            book, "sitemap", args.sitemap_url, "current/sitemap.xml", "xml"
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: could not load sources: {exc}", file=sys.stderr)
        return 2

    source_mode = "cache" if home.from_cache and sitemap.from_cache else "network"
    allowed_hosts = tuple(
        host.strip() for host in args.allowed_hosts.split(",") if host.strip()
    )
    kwargs: dict = {
        "base_url": base_url,
        "sitemap_url": args.sitemap_url,
        "allowed_hosts": allowed_hosts,
        "min_coverage": args.min_coverage,
        "pii_guard": args.pii_guard,
        "source_mode": source_mode,
    }
    if args.blocked_tokens.strip():
        kwargs["blocked_tokens"] = tuple(
            token.strip()
            for token in args.blocked_tokens.split(",")
            if token.strip()
        )

    result = compile_graph(sitemap.text, home.text, **kwargs)

    store = GraphStore(args.db)
    store.replace_graph(
        result.nodes,
        result.edges,
        coverage=result.coverage,
        graph_hash=result.graph_hash,
        generated_at=result.generated_at,
        source_mode=source_mode,
    )

    coverage_path = Path(args.coverage)
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.write_text(
        json.dumps(result.coverage, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    c = result.coverage
    print("NGA graph compile (Phase 1)")
    print(f"  source:        {source_mode}")
    print(f"  base URL:      {base_url}")
    print(f"  sitemap URLs:  {c['sitemap_urls']}")
    print(f"  nav nodes:     {c['nav_nodes']}  links: {c['nav_links']}")
    print(
        "  nodes:         "
        f"{c['unique_nodes']} "
        f"(sections {c['sections']}, pages {c['pages']}, "
        f"documents {c['documents']}, external {c['external']}, "
        f"blocked {c['blocked']})"
    )
    print(
        "  coverage:      "
        f"{c['represented_sitemap_urls']}/{c['sitemap_urls']} "
        f"({c['coverage_pct'] * 100:.1f}%) "
        f"gate={'PASS' if c['meets_gate'] else 'FAIL'} "
        f"(min {c['min_coverage']:.0%})"
    )
    print(f"  top sections:  {len(c['top_level_sections'])}")
    print(f"  departments:   {c['departments']}")
    print(f"  graph hash:    {result.graph_hash}")
    print(f"  database:      {args.db}")
    print(f"  coverage:      {args.coverage}")
    if c["warnings"]:
        print("  warnings:      " + ", ".join(c["warnings"]))
    if args.verbose:
        for node in result.nodes:
            marker = {
                "blocked": "!",
                "external": "->",
                "pdf": "pdf",
                "section": "sec",
            }.get(node.content_type.value, "   ")
            print(f"  {marker:>3} {node.depth:>2} {node.node_id}  {node.url}")
    if not c["meets_gate"] and not args.allow_below_gate:
        print(
            "error: coverage is below the gate; use --allow-below-gate to "
            "write anyway",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
