#!/usr/bin/env python3
"""Promote document state from the index DB into the serving DB.

The batch backfill writes a separate index database. This promoter copies
only the document state that changed:

* ``nga_document`` rows for changed document nodes;
* the document node's content/verification columns in ``nga_node``;
* document-only ``nga_fetch_state`` rows;
* the searchable document body in ``nga_body_fts``.

It never copies ``nga_turn_event``, conversation state, the answer cache
(which is purged because document evidence changed), page versions, or the
compiled graph. The answer path can keep reading the serving DB while this
runs, but the runbook still recommends stopping the answer process for a
clean publish.

Dry run (default) shows exactly what would change:

    .venv/bin/python scripts/nga_promote.py
    .venv/bin/python scripts/nga_promote.py --json

Apply (maintenance window; requires the index and serving graph hashes to
match):

    .venv/bin/python scripts/nga_promote.py --apply
    .venv/bin/python scripts/nga_promote.py --apply --no-purge-cache
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402

_DOC_COLUMNS = (
    "node_id",
    "url",
    "mime",
    "pages",
    "extraction_status",
    "text",
    "tables",
    "ocr",
    "ocr_lang",
    "ocr_quality",
    "needs_review",
    "pii_flagged",
    "content_hash",
    "size_bytes",
    "fetched_at",
    "verified_at",
    "error",
    "reviewed_at",
    "reviewed_by",
    "source_page_id",
)
_NODE_COLUMNS = (
    "status",
    "content_hash",
    "fetched_at",
    "verified_at",
    "size_bytes",
)
_FETCH_COLUMNS = (
    "node_id",
    "etag",
    "last_modified",
    "http_status",
    "checked_at",
    "next_due_at",
    "failure_count",
    "error",
)


class PromotionError(RuntimeError):
    """The two databases are not safe to promote."""


def _columns(con: sqlite3.Connection, schema: str, table: str) -> tuple[str, ...]:
    try:
        rows = con.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    except sqlite3.DatabaseError as exc:
        raise PromotionError(f"cannot read {schema}.{table}: {exc}") from exc
    return tuple(str(row[1]) for row in rows)


def _latest_hash(con: sqlite3.Connection, schema: str) -> str:
    try:
        row = con.execute(
            f"SELECT graph_hash FROM {schema}.nga_compile_run "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise PromotionError(f"cannot read compile run: {exc}") from exc
    return str((row or [""])[0] or "")


def _quick_check(path: Path) -> str:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as con:
            row = con.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise PromotionError(f"{path}: quick_check failed: {exc}") from exc
    return str((row or ["not ok"])[0])


def _prepare_changes(con: sqlite3.Connection) -> int:
    con.execute("DROP TABLE IF EXISTS temp.promote_doc_nodes")
    con.execute(
        "CREATE TEMP TABLE promote_doc_nodes (node_id TEXT PRIMARY KEY)"
    )
    con.execute(
        """
        INSERT INTO promote_doc_nodes (node_id)
        SELECT i.node_id
        FROM idx.nga_document i
        LEFT JOIN main.nga_document d ON d.node_id = i.node_id
        WHERE d.node_id IS NULL
           OR COALESCE(d.extraction_status, '') != COALESCE(i.extraction_status, '')
           OR COALESCE(d.needs_review, 0) != COALESCE(i.needs_review, 0)
           OR COALESCE(d.pii_flagged, 0) != COALESCE(i.pii_flagged, 0)
           OR COALESCE(d.content_hash, '') != COALESCE(i.content_hash, '')
           OR COALESCE(d.fetched_at, '') != COALESCE(i.fetched_at, '')
           OR COALESCE(d.verified_at, '') != COALESCE(i.verified_at, '')
           OR COALESCE(d.reviewed_at, '') != COALESCE(i.reviewed_at, '')
           OR COALESCE(d.reviewed_by, '') != COALESCE(i.reviewed_by, '')
           OR COALESCE(d.error, '') != COALESCE(i.error, '')
        """
    )
    return int(
        con.execute("SELECT COUNT(*) FROM promote_doc_nodes").fetchone()[0]
    )


def _plan(con: sqlite3.Connection) -> dict:
    selected = int(
        con.execute("SELECT COUNT(*) FROM promote_doc_nodes").fetchone()[0]
    )
    missing_nodes = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM promote_doc_nodes p
            LEFT JOIN main.nga_node n ON n.node_id = p.node_id
            WHERE n.node_id IS NULL
            """
        ).fetchone()[0]
    )
    node_changes = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM promote_doc_nodes p
            JOIN main.nga_node n ON n.node_id = p.node_id
            JOIN idx.nga_node i ON i.node_id = p.node_id
            WHERE COALESCE(n.status, '') != COALESCE(i.status, '')
               OR COALESCE(n.content_hash, '') != COALESCE(i.content_hash, '')
               OR COALESCE(n.fetched_at, '') != COALESCE(i.fetched_at, '')
               OR COALESCE(n.verified_at, '') != COALESCE(i.verified_at, '')
               OR COALESCE(n.size_bytes, 0) != COALESCE(i.size_bytes, 0)
            """
        ).fetchone()[0]
    )
    fetch_rows = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM promote_doc_nodes p
            JOIN idx.nga_fetch_state f ON f.node_id = p.node_id
            """
        ).fetchone()[0]
    )
    body_rows = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM promote_doc_nodes p
            JOIN idx.nga_document i ON i.node_id = p.node_id
            WHERE i.extraction_status = 'done'
              AND i.needs_review = 0
              AND i.pii_flagged = 0
              AND TRIM(COALESCE(i.text, '')) != ''
            """
        ).fetchone()[0]
    )
    body_deleted = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM main.nga_body_fts b
            JOIN promote_doc_nodes p ON p.node_id = b.node_id
            """
        ).fetchone()[0]
    )
    return {
        "document_rows": selected,
        "node_updates": node_changes,
        "fetch_state_rows": fetch_rows,
        "body_rows_added": body_rows,
        "body_rows_removed": body_deleted,
        "missing_serving_nodes": missing_nodes,
    }


def _apply(con: sqlite3.Connection, *, purge_cache: bool) -> dict:
    doc_columns = ", ".join(_DOC_COLUMNS)
    doc_select = ", ".join(f"i.{column}" for column in _DOC_COLUMNS)
    fetch_columns = ", ".join(_FETCH_COLUMNS)
    fetch_select = ", ".join(f"f.{column}" for column in _FETCH_COLUMNS)
    node_sets = ",\n                ".join(
        f"{column} = (SELECT i.{column} FROM idx.nga_node i "
        f"WHERE i.node_id = main.nga_node.node_id)"
        for column in _NODE_COLUMNS
    )
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            f"""
            INSERT OR REPLACE INTO main.nga_document ({doc_columns})
            SELECT {doc_select}
            FROM idx.nga_document i
            JOIN promote_doc_nodes p ON p.node_id = i.node_id
            """
        )
        con.execute(
            f"""
            UPDATE main.nga_node
            SET {node_sets}
            WHERE node_id IN (SELECT node_id FROM promote_doc_nodes)
              AND EXISTS (
                  SELECT 1 FROM idx.nga_node i
                  WHERE i.node_id = main.nga_node.node_id
              )
            """
        )
        con.execute(
            f"""
            INSERT OR REPLACE INTO main.nga_fetch_state ({fetch_columns})
            SELECT {fetch_select}
            FROM idx.nga_fetch_state f
            JOIN promote_doc_nodes p ON p.node_id = f.node_id
            """
        )
        removed = con.execute(
            """
            DELETE FROM main.nga_body_fts
            WHERE node_id IN (SELECT node_id FROM promote_doc_nodes)
            """
        ).rowcount
        added = con.execute(
            """
            INSERT INTO main.nga_body_fts (node_id, title, body)
            SELECT i.node_id, n.title, i.text
            FROM idx.nga_document i
            JOIN promote_doc_nodes p ON p.node_id = i.node_id
            JOIN main.nga_node n ON n.node_id = i.node_id
            WHERE i.extraction_status = 'done'
              AND i.needs_review = 0
              AND i.pii_flagged = 0
              AND TRIM(COALESCE(i.text, '')) != ''
            """
        ).rowcount
        cache_purged = 0
        if purge_cache:
            cache_purged = con.execute(
                "DELETE FROM main.nga_answer_cache"
            ).rowcount
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {
        "body_rows_removed": max(0, int(removed)),
        "body_rows_added": max(0, int(added)),
        "cache_rows_purged": max(0, int(cache_purged)),
    }


def promote(
    serve_path: str | Path,
    index_path: str | Path,
    *,
    apply: bool = False,
    purge_cache: bool = True,
) -> dict:
    serve = Path(serve_path)
    index = Path(index_path)
    if not serve.is_file():
        raise PromotionError(f"serving database not found: {serve}")
    if not index.is_file():
        raise PromotionError(f"index database not found: {index}")
    if serve.resolve() == index.resolve():
        raise PromotionError("serving and index paths are the same")

    index_check = _quick_check(index)
    if index_check != "ok":
        raise PromotionError(f"index quick_check: {index_check}")

    serve_conn = sqlite3.connect(str(serve), timeout=30)
    try:
        # Manage the promotion transaction explicitly; ATTACH otherwise leaves
        # Python's implicit transaction open and BEGIN IMMEDIATE fails.
        serve_conn.isolation_level = None
        serve_conn.row_factory = sqlite3.Row
        serve_conn.execute("PRAGMA busy_timeout = 30000")
        serve_conn.execute("ATTACH DATABASE ? AS idx", (str(index),))

        for table in ("nga_document", "nga_node", "nga_fetch_state"):
            serve_cols = _columns(serve_conn, "main", table)
            index_cols = _columns(serve_conn, "idx", table)
            if serve_cols != index_cols:
                raise PromotionError(
                    f"{table} schema differs between serving and index"
                )
        if _latest_hash(serve_conn, "main") != _latest_hash(
            serve_conn, "idx"
        ):
            raise PromotionError(
                "serving and index were compiled from different graphs"
            )

        _prepare_changes(serve_conn)
        plan = _plan(serve_conn)
        if plan["missing_serving_nodes"]:
            raise PromotionError(
                f"{plan['missing_serving_nodes']} index document node(s) "
                "are missing from the serving graph"
            )
        result = {
            "serve_db": str(serve),
            "index_db": str(index),
            "apply": bool(apply),
            "purge_cache": bool(purge_cache),
            "plan": plan,
        }
        if apply and plan["document_rows"]:
            result.update(
                _apply(serve_conn, purge_cache=purge_cache)
            )
            result["applied"] = True
        else:
            result["applied"] = False
        return result
    finally:
        serve_conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote document state from the index DB to serving."
    )
    parser.add_argument("--serve", default=settings.serve_db_path)
    parser.add_argument("--index", default=settings.index_db_path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the promotion; without it this is a dry run",
    )
    parser.add_argument(
        "--no-purge-cache",
        action="store_true",
        help="keep the answer cache (not recommended after document changes)",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = promote(
            args.serve,
            args.index,
            apply=args.apply,
            purge_cache=not args.no_purge_cache,
        )
    except PromotionError as exc:
        print(f"promotion error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    plan = result["plan"]
    print("NGA document promotion")
    print(f"  serving:              {result['serve_db']}")
    print(f"  index:                {result['index_db']}")
    print(f"  mode:                 {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"  document rows:        {plan['document_rows']}")
    print(f"  node updates:         {plan['node_updates']}")
    print(f"  fetch-state rows:     {plan['fetch_state_rows']}")
    print(f"  body rows added:      {plan['body_rows_added']}")
    print(f"  body rows removed:    {plan['body_rows_removed']}")
    if args.apply:
        print(f"  body rows written:    {result.get('body_rows_added', 0)}")
        print(f"  cache rows purged:    {result.get('cache_rows_purged', 0)}")
    else:
        print("  no changes written; pass --apply to promote")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
