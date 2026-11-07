"""Effective NGA readiness for operators and the boot gate.

The answer path can look healthy while the capability is not actually running
(mode off, no page text, empty chunk index, embedding provider off, serving DB
locked by a backfill). This report makes that state visible without exposing
secrets.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config import settings


def _count(con: sqlite3.Connection, sql: str) -> int | None:
    try:
        row = con.execute(sql).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.DatabaseError:
        return None


def _group_counts(
    con: sqlite3.Connection, sql: str
) -> dict[str, int] | None:
    try:
        return {
            str(row[0]): int(row[1])
            for row in con.execute(sql).fetchall()
        }
    except sqlite3.DatabaseError:
        return None


def _document_counts(rows) -> dict[str, int]:
    documents: dict[str, int] = {}
    for row in rows:
        status = str(row["extraction_status"])
        key = "needs_review" if int(row["needs_review"] or 0) else status
        documents[key] = documents.get(key, 0) + int(row["n"])
    return documents


def readiness_report(graph_path: str | Path | None = None) -> dict:
    """Return the effective mode and index state for the NGA path."""
    path = Path(graph_path or settings.serve_db_path)
    # An explicit path is a test/CLI override: use the same file for both
    # stores. A production call without an argument reports the split.
    index_path = path if graph_path else Path(settings.index_db_path)
    report: dict = {
        "status": "ok",
        "mode": {
            "nga_enabled": settings.NGA_ENABLED,
            "nga_mode": settings.NGA_MODE,
            "deep_search_enabled": settings.DEEP_SEARCH_ENABLED,
            "cache_revalidate_enabled": settings.CACHE_REVALIDATE_ENABLED,
            "text_llm_planner": settings.NGA_TEXT_LLM_PLANNER,
            "adequacy_enabled": settings.NGA_ADEQUACY_ENABLED,
            "adequacy_min_score": settings.NGA_ADEQUACY_MIN_SCORE,
            "embedding_provider": settings.EMBEDDING_PROVIDER,
            "embedding_model": settings.EMBEDDING_MODEL,
        },
        "server": {
            "host": settings.HOST,
            "port": settings.PORT,
        },
        "scheduler": {
            "enabled": settings.NGA_SCHEDULER_ENABLED,
        },
        "graph_path": str(path),
        "serve_db": str(path),
        "index_db": str(index_path),
        "db_paths_split": path.resolve() != index_path.resolve(),
        "graph_exists": path.exists(),
        "index": {
            "path": str(index_path),
            "exists": index_path.exists(),
            "journal_mode": None,
            "lock": "unknown",
            "documents": {},
            "chunks": None,
        },
        "journal_mode": None,
        "lock": "unknown",
        "compile": None,
        "graph": {},
        "documents": {},
        "chunks": None,
        "embeddings": None,
        "catalogue": {},
        "warnings": [],
        "info": [],
    }
    warnings: list[str] = report["warnings"]
    info: list[str] = report["info"]

    mode = (settings.NGA_MODE or "").strip().lower()
    if settings.NGA_ENABLED and mode != "agentic":
        warnings.append(f"nga_enabled_but_mode_{mode or 'unset'}")
    if settings.DEEP_SEARCH_ENABLED and not path.exists():
        warnings.append("deep_search_enabled_but_graph_missing")

    catalogue_path = Path(settings.NGA_CATALOGUE_PATH)
    try:
        from app.knowledge.catalogue import Catalogue

        catalogue = Catalogue.load(catalogue_path)
        report["catalogue"] = {
            "path": str(catalogue_path),
            "exists": catalogue_path.exists(),
            "enabled": settings.NGA_CATALOGUE_ENABLED,
            "rows": len(catalogue.rows),
            "levels": catalogue.counts(),
            "errors": list(catalogue.errors),
        }
        if settings.NGA_CATALOGUE_ENABLED and not catalogue_path.exists():
            warnings.append("catalogue_missing")
        elif settings.NGA_CATALOGUE_ENABLED and not catalogue.rows:
            warnings.append("catalogue_empty")
        elif catalogue.errors:
            warnings.append("catalogue_errors")
    except Exception:  # noqa: BLE001 - readiness must not fail on a bad file
        report["catalogue"] = {
            "path": str(catalogue_path),
            "exists": catalogue_path.exists(),
            "enabled": settings.NGA_CATALOGUE_ENABLED,
            "errors": ["catalogue_load_failed"],
        }
        warnings.append("catalogue_load_failed")

    if not path.exists():
        report["status"] = "degraded"
        report["lock"] = "missing"
        warnings.append("graph_missing")
        return report

    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=2.0
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 2000")
        report["journal_mode"] = str(
            con.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        report["lock"] = "ok"

        nodes = _count(con, "SELECT COUNT(*) FROM nga_node")
        pages = _count(con, "SELECT COUNT(*) FROM nga_page_version")
        chunks = _count(con, "SELECT COUNT(*) FROM nga_chunk")
        embeddings = _count(con, "SELECT COUNT(*) FROM nga_chunk_embedding")
        report["graph"] = {
            "nodes": nodes,
            "pages_with_text": pages,
            "node_status": _group_counts(
                con, "SELECT status, COUNT(*) FROM nga_node GROUP BY status"
            ),
        }
        report["chunks"] = chunks
        report["embeddings"] = embeddings

        rows = con.execute(
            "SELECT extraction_status, needs_review, COUNT(*) AS n "
            "FROM nga_document GROUP BY extraction_status, needs_review"
        ).fetchall()
        documents = _document_counts(rows)
        report["documents"] = documents

        compile_row = con.execute(
            "SELECT graph_hash, generated_at, source_mode, coverage "
            "FROM nga_compile_run ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        if compile_row is not None:
            data = dict(compile_row)
            try:
                data["coverage"] = json.loads(data.get("coverage") or "{}")
            except (TypeError, ValueError):
                data["coverage"] = {}
            report["compile"] = data

        if nodes == 0:
            warnings.append("graph_empty")
        if pages == 0:
            warnings.append("no_page_text")
        if settings.DEEP_SEARCH_ENABLED and not pages:
            warnings.append("deep_search_enabled_but_no_page_text")
        if report["journal_mode"] != "wal":
            warnings.append("serving_db_not_wal")
        if chunks == 0:
            info.append("chunk_index_empty")
        if settings.EMBEDDING_PROVIDER in ("", "off", "none"):
            info.append("embedding_provider_off")
        elif not embeddings:
            warnings.append("embedding_provider_on_but_no_vectors")
        pending = documents.get("pending") or 0
        if pending:
            info.append(f"documents_pending_{pending}")
    except sqlite3.DatabaseError as exc:
        report["status"] = "degraded"
        report["lock"] = "locked"
        warnings.append(f"database_error:{type(exc).__name__}")
    finally:
        if con is not None:
            con.close()

    index = report["index"]
    if not report["db_paths_split"]:
        index.update(
            exists=path.exists(),
            journal_mode=report["journal_mode"],
            lock=report["lock"],
            documents=report["documents"],
            chunks=report["chunks"],
        )
        if settings.NGA_ENABLED and mode == "agentic":
            warnings.append("serve_and_index_db_shared")
        else:
            info.append("serve_and_index_db_shared")
    elif not index_path.exists():
        warnings.append("index_db_missing")
    else:
        index_con: sqlite3.Connection | None = None
        try:
            index_con = sqlite3.connect(
                f"file:{index_path}?mode=ro", uri=True, timeout=2.0
            )
            index_con.row_factory = sqlite3.Row
            index_con.execute("PRAGMA busy_timeout = 2000")
            index["lock"] = "ok"
            index["journal_mode"] = str(
                index_con.execute("PRAGMA journal_mode").fetchone()[0]
            ).lower()
            index["documents"] = _document_counts(
                index_con.execute(
                    "SELECT extraction_status, needs_review, COUNT(*) AS n "
                    "FROM nga_document "
                    "GROUP BY extraction_status, needs_review"
                ).fetchall()
            )
            index["chunks"] = _count(
                index_con, "SELECT COUNT(*) FROM nga_chunk"
            )
            if index["journal_mode"] != "wal":
                warnings.append("index_db_not_wal")
            pending = index["documents"].get("pending") or 0
            if pending:
                info.append(f"index_documents_pending_{pending}")
        except sqlite3.DatabaseError as exc:
            index["lock"] = "error"
            warnings.append(f"index_database_error:{type(exc).__name__}")
        finally:
            if index_con is not None:
                index_con.close()

    if warnings:
        report["status"] = "degraded"
    return report


__all__ = ["readiness_report"]
