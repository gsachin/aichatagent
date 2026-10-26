#!/usr/bin/env python3
"""Index linked PDFs and review OCR text (Phase 11).

Background only: the live answer path never fetches or OCRs a document. OCR
text is stored with ``needs_review`` and is not indexed until a named reviewer
approves it with ``--review``.

Usage:
    .venv/bin/python scripts/nga_documents.py --status
    .venv/bin/python scripts/nga_documents.py --list-review
    .venv/bin/python scripts/nga_documents.py --discover-only
    .venv/bin/python scripts/nga_documents.py --allow-network --limit 3
    .venv/bin/python scripts/nga_documents.py --review doc.abc123 \
        --reviewer "registrar office"
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
from app.sitegraph.documents import (  # noqa: E402
    DocumentOptions,
    discover_documents,
    refresh_documents,
)
from app.sitegraph.fetch import SiteFetcher  # noqa: E402
from app.sitegraph.metrics import utc_now  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index linked PDFs and review OCR text (Phase 11)."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--list-review", action="store_true")
    parser.add_argument("--review", default=None, metavar="NODE_ID")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--limit", type=int, default=settings.NGA_PDF_REFRESH_LIMIT)
    parser.add_argument("--ttl", type=int, default=settings.NGA_TTL_DOCUMENT_S)
    parser.add_argument(
        "--max-bytes", type=int, default=settings.NGA_PDF_MAX_BYTES
    )
    parser.add_argument("--max-pages", type=int, default=settings.NGA_PDF_MAX_PAGES)
    parser.add_argument(
        "--min-chars", type=int, default=settings.NGA_PDF_MIN_CHARS
    )
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument(
        "--ocr-max-pages", type=int, default=settings.NGA_PDF_OCR_MAX_PAGES
    )
    parser.add_argument("--ocr-langs", default=settings.NGA_PDF_OCR_LANGS)
    parser.add_argument("--node-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _hosts() -> tuple[str, ...]:
    return tuple(
        host.strip()
        for host in settings.NGA_ALLOWED_HOSTS.split(",")
        if host.strip()
    )


def _blocked_tokens() -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in settings.NGA_BLOCKED_PATH_TOKENS.split(",")
        if token.strip()
    )


def _status(store: GraphStore, *, as_json: bool) -> int:
    summary = store.document_summary()
    due = store.due_documents(limit=10_000)
    review = store.documents_needing_review(limit=1000)
    payload = {
        "summary": summary,
        "due": len(due),
        "needs_review": len(review),
        "pdf_index_enabled": settings.NGA_PDF_INDEX_ENABLED,
        "due_node_ids": [node.node_id for node in due[:50]],
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print("NGA document index")
    print(f"  pdf index:     {'on' if settings.NGA_PDF_INDEX_ENABLED else 'off'}")
    print(
        "  documents:     "
        f"{summary['indexed']} indexed, {summary['pending']} pending, "
        f"{summary['errors']} error, {summary['empty']} empty, "
        f"{summary['blocked']} blocked"
    )
    print(f"  review queue:  {summary['review_pending']}")
    print(f"  due now:       {len(due)}")
    return 0


def _list_review(store: GraphStore, *, as_json: bool) -> int:
    documents = store.documents_needing_review(limit=500)
    rows = []
    for document in documents:
        node = store.get_node(document.node_id)
        rows.append(
            {
                "node_id": document.node_id,
                "title": node.title if node else "",
                "url": document.url,
                "pages": document.pages,
                "ocr": document.ocr,
                "ocr_lang": document.ocr_lang,
                "ocr_quality": document.ocr_quality,
                "fetched_at": document.fetched_at,
            }
        )
    if as_json:
        print(json.dumps({"documents": rows, "total": len(rows)}, indent=2))
        return 0
    print(f"documents awaiting review: {len(rows)}")
    for row in rows:
        print(
            f"  {row['node_id']}  pages={row['pages']}  "
            f"ocr={row['ocr']} {row['ocr_lang']}  {row['url']}"
        )
    return 0


def _review(store: GraphStore, node_id: str, reviewer: str) -> int:
    if not reviewer.strip():
        print("error: --reviewer is required with --review", file=sys.stderr)
        return 2
    ok = store.mark_document_reviewed(
        node_id, reviewer=reviewer.strip(), now=utc_now()
    )
    if not ok:
        print(
            "error: document is missing, blocked, or not fully extracted",
            file=sys.stderr,
        )
        return 1
    print(f"approved and indexed: {node_id}")
    return 0


def _options(args: argparse.Namespace) -> DocumentOptions:
    ocr_langs = tuple(
        item.strip() for item in (args.ocr_langs or "").split(",") if item.strip()
    )
    return DocumentOptions(
        ttl_s=max(60, int(args.ttl)),
        limit=max(1, min(50, int(args.limit))),
        force=args.force,
        node_ids=tuple(args.node_id),
        max_bytes=max(1024, int(args.max_bytes)),
        max_pages=max(1, int(args.max_pages)),
        min_chars=max(1, int(args.min_chars)),
        ocr=settings.NGA_PDF_OCR_ENABLED and not args.no_ocr,
        ocr_max_pages=max(1, int(args.ocr_max_pages)),
        ocr_langs=ocr_langs,
        failure_backoff_s=max(60, settings.NGA_PDF_FAILURE_BACKOFF_S),
        allowed_hosts=_hosts(),
        blocked_path_tokens=_blocked_tokens(),
    )


def _run(args: argparse.Namespace, store: GraphStore) -> int:
    if not settings.NGA_PDF_INDEX_ENABLED and not args.force:
        print(
            "error: NGA_PDF_INDEX_ENABLED is false; set it to true or pass "
            "--force",
            file=sys.stderr,
        )
        return 2

    options = _options(args)
    if args.discover_only:
        linked = discover_documents(
            store,
            allowed_hosts=options.allowed_hosts,
            blocked_path_tokens=options.blocked_path_tokens,
            pii_guard=settings.NGA_PII_GUARD_ENABLED,
        )
        print(f"document links discovered: {linked}")
        return 0

    if not args.allow_network:
        print(
            "error: pass --allow-network to fetch PDFs, or --status / "
            "--list-review / --discover-only",
            file=sys.stderr,
        )
        return 2

    fetcher = SiteFetcher(
        _hosts(),
        user_agent=settings.NGA_FETCH_USER_AGENT,
        timeout_s=settings.NGA_FETCH_TIMEOUT_S,
        delay_s=settings.NGA_FETCH_DELAY_S,
        max_bytes=options.max_bytes,
        respect_robots=settings.NGA_ROBOTS_RESPECT,
    )
    stats = refresh_documents(
        store,
        fetcher,
        options,
        pii_guard=settings.NGA_PII_GUARD_ENABLED,
    )
    if args.json:
        print(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("NGA document index")
        print(f"  discovered:          {stats.discovered}")
        print(f"  checked:             {stats.checked}")
        print(f"  fetched:             {stats.fetched}")
        print(f"  indexed:             {stats.indexed}")
        print(f"  unchanged:           {stats.unchanged}")
        print(f"  not modified:        {stats.not_modified}")
        print(f"  needs review (OCR):  {stats.needs_review}")
        print(f"  empty:               {stats.empty}")
        print(f"  blocked (PII):       {stats.blocked}")
        print(f"  errors:              {stats.errors}")
        print(f"  pages processed:     {stats.pages}")
    return 0 if stats.errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = GraphStore(args.db)
    if args.review:
        return _review(store, args.review, args.reviewer)
    if args.list_review:
        return _list_review(store, as_json=args.json)
    if args.status:
        return _status(store, as_json=args.json)
    return _run(args, store)


if __name__ == "__main__":
    raise SystemExit(main())
