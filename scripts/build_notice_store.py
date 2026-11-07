#!/usr/bin/env python3
"""Build the DSPMU notice FTS5 store from ``notices.jsonl``.

This is also run by ``scripts/build_site_index.py`` after the JSONL is written.
It is separate so the store can be rebuilt from the committed catalogue without
re-crawling the site.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notices",
        default=str(ROOT / DEFAULT_NOTICES_PATH),
        help="JSONL notice catalogue",
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / DEFAULT_DB_PATH),
        help="destination SQLite FTS5 database",
    )
    parser.add_argument(
        "--body-dir",
        default="",
        help="optional directory of <notice-id>.txt bodies to index",
    )
    args = parser.parse_args(argv)

    try:
        stats = build_store(
            args.notices,
            args.db,
            body_dir=args.body_dir or None,
        )
    except NoticeStoreError as exc:
        print(f"notice store build failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"notice store: {stats.rows} rows, {stats.pii_excluded} PII excluded, "
        f"{stats.body_rows} bodies, {stats.db_bytes:,} bytes -> {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
