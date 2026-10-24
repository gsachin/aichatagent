#!/usr/bin/env python3
"""Extract notice PDFs into PII-gated body text for the FTS5 store.

Offline by default: reads ``--pdf-dir`` or the site-index cache. Pass
``--allow-network`` only after the university's written permission; the
fetcher checks robots.txt, uses a contactable user agent and waits one second
between requests.

Only PII-clean, non-OCR bodies are written. Use ``--rebuild-db`` after a full
(or intentional partial) extraction to index the clean bodies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.notice_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_NOTICES_PATH,
    NoticeStoreError,
    build_store,
)
from app.siteindex.notice_bodies import (  # noqa: E402
    DEFAULT_BODIES_DIR,
    DEFAULT_CACHE_DIR,
    extract_notice_bodies,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notices",
        default=str(ROOT / DEFAULT_NOTICES_PATH),
        help="JSONL notice catalogue",
    )
    parser.add_argument(
        "--bodies-dir",
        default=str(DEFAULT_BODIES_DIR),
        help="directory for PII-clean body text",
    )
    parser.add_argument(
        "--pdf-dir",
        default="",
        help="optional local directory of <notice-id>.pdf files",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="site-index cache; PDFs are read from notices/<id>.pdf",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="fetch missing PDFs after robots.txt checks; requires permission",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="process at most N non-PII notices (0 = all)",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="enable OCR fallback; OCR bodies are review-only and not indexed",
    )
    parser.add_argument(
        "--ocr-langs",
        default="eng,hin",
        help="comma-separated OCR language order (default: eng,hin)",
    )
    parser.add_argument(
        "--tessdata-dir",
        default="",
        help="optional tessdata directory; defaults to .tessdata when present",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="re-extract notices whose body file already exists",
    )
    parser.add_argument(
        "--rebuild-db",
        action="store_true",
        help="rebuild the FTS5 store after extraction using the clean bodies",
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / DEFAULT_DB_PATH),
        help="FTS5 database to rebuild with --rebuild-db",
    )
    args = parser.parse_args(argv)

    stats = extract_notice_bodies(
        args.notices,
        args.bodies_dir,
        pdf_dir=args.pdf_dir or None,
        cache_dir=args.cache_dir,
        allow_network=args.allow_network,
        limit=args.limit or None,
        ocr=args.ocr,
        ocr_langs=tuple(
            part.strip() for part in args.ocr_langs.split(",") if part.strip()
        ),
        tessdata_dir=args.tessdata_dir or None,
        skip_existing=not args.no_skip_existing,
    )
    print(
        "notice bodies: "
        f"scanned={stats.scanned} indexed={stats.indexed} "
        f"pii_blocked={stats.pii_blocked} needs_review={stats.needs_review} "
        f"missing={stats.missing_pdf} empty={stats.empty} errors={stats.errors} "
        f"existing={stats.skipped_existing} title_pii={stats.skipped_title_pii}"
    )
    print(f"manifest: {Path(args.bodies_dir) / 'manifest.jsonl'}")

    if not args.rebuild_db:
        print("FTS5 store not rebuilt; pass --rebuild-db to index the clean bodies")
        return 0

    try:
        store = build_store(
            args.notices,
            args.db,
            body_dir=args.bodies_dir,
        )
    except NoticeStoreError as exc:
        print(f"notice store rebuild failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"notice store: {store.rows} rows, {store.body_rows} with bodies, "
        f"{store.db_bytes:,} bytes -> {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
