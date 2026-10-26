"""SQLite persistence for the Phase 1 navigation graph.

The production target is Postgres (see ``doc/sdlc/nga/03-lld.md`` §3.1), but
Phase 1 is a single-box compiler. This store mirrors the node/edge contract so
the SQLite artifact can be swapped for Postgres without changing the compiler.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.sitegraph.models import (
    Authority,
    ContentType,
    DocumentStatus,
    NodeStatus,
    PageVersion,
    PiiClass,
    SiteDocument,
    SiteEdge,
    SiteNode,
)

SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nga_schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nga_node (
    node_id       TEXT PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    canonical_path TEXT NOT NULL,
    parent_id     TEXT REFERENCES nga_node(node_id),
    depth         INTEGER NOT NULL,
    section       TEXT NOT NULL,
    title         TEXT NOT NULL,
    nav_label     TEXT NOT NULL DEFAULT '',
    aliases       TEXT NOT NULL DEFAULT '[]',
    languages     TEXT NOT NULL DEFAULT '[]',
    content_type  TEXT NOT NULL,
    authority     TEXT NOT NULL,
    pii_class     TEXT NOT NULL DEFAULT 'public',
    status        TEXT NOT NULL DEFAULT 'ok',
    lastmod       TEXT,
    content_hash  TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL DEFAULT '',
    verified_at   TEXT,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    canonical_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS nga_node_parent_idx ON nga_node(parent_id);
CREATE INDEX IF NOT EXISTS nga_node_section_idx ON nga_node(section);
CREATE INDEX IF NOT EXISTS nga_node_status_idx ON nga_node(status);

CREATE TABLE IF NOT EXISTS nga_edge (
    edge_id  TEXT PRIMARY KEY,
    src_id   TEXT NOT NULL REFERENCES nga_node(node_id) ON DELETE CASCADE,
    dst_id   TEXT NOT NULL REFERENCES nga_node(node_id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    anchor   TEXT NOT NULL DEFAULT '',
    ordinal  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS nga_edge_src_idx ON nga_edge(src_id, kind, ordinal);
CREATE INDEX IF NOT EXISTS nga_edge_dst_idx ON nga_edge(dst_id, kind);

CREATE TABLE IF NOT EXISTS nga_compile_run (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    graph_hash   TEXT NOT NULL,
    source_mode  TEXT NOT NULL,
    nodes        INTEGER NOT NULL,
    edges        INTEGER NOT NULL,
    coverage     TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS nga_fts USING fts5(
    node_id UNINDEXED,
    title,
    nav_label,
    aliases,
    section,
    canonical_path,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS nga_page_version (
    node_id       TEXT NOT NULL REFERENCES nga_node(node_id) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    text          TEXT NOT NULL,
    markdown      TEXT NOT NULL DEFAULT '',
    tables        TEXT NOT NULL DEFAULT '[]',
    sections      TEXT NOT NULL DEFAULT '[]',
    links         TEXT NOT NULL DEFAULT '[]',
    documents     TEXT NOT NULL DEFAULT '[]',
    language      TEXT NOT NULL DEFAULT 'en',
    content_hash  TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    valid_from    TEXT NOT NULL,
    valid_until   TEXT,
    PRIMARY KEY (node_id, version)
);

CREATE TABLE IF NOT EXISTS nga_fetch_state (
    node_id        TEXT PRIMARY KEY REFERENCES nga_node(node_id) ON DELETE CASCADE,
    etag           TEXT NOT NULL DEFAULT '',
    last_modified  TEXT NOT NULL DEFAULT '',
    http_status    INTEGER,
    checked_at     TEXT,
    next_due_at    TEXT,
    failure_count  INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS nga_body_fts USING fts5(
    node_id UNINDEXED,
    title,
    body,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS nga_document (
    node_id           TEXT PRIMARY KEY REFERENCES nga_node(node_id) ON DELETE CASCADE,
    url               TEXT NOT NULL,
    mime              TEXT NOT NULL DEFAULT 'application/pdf',
    pages             INTEGER NOT NULL DEFAULT 0,
    extraction_status TEXT NOT NULL DEFAULT 'pending',
    source_page_id    TEXT NOT NULL DEFAULT '',
    text              TEXT NOT NULL DEFAULT '',
    tables            TEXT NOT NULL DEFAULT '[]',
    ocr               INTEGER NOT NULL DEFAULT 0,
    ocr_lang          TEXT NOT NULL DEFAULT '',
    ocr_quality       TEXT NOT NULL DEFAULT '',
    needs_review      INTEGER NOT NULL DEFAULT 0,
    pii_flagged       INTEGER NOT NULL DEFAULT 0,
    content_hash      TEXT NOT NULL DEFAULT '',
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    fetched_at        TEXT,
    verified_at       TEXT,
    error             TEXT NOT NULL DEFAULT '',
    reviewed_at       TEXT,
    reviewed_by       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS nga_document_review_idx
    ON nga_document(needs_review, extraction_status);

CREATE TABLE IF NOT EXISTS nga_reference_delivery (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid        TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    phone_hash      TEXT NOT NULL DEFAULT '',
    phone_last4     TEXT NOT NULL DEFAULT '',
    node_id         TEXT NOT NULL,
    url             TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'turn',
    status          TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    twilio_sid      TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    UNIQUE (call_sid, node_id)
);
CREATE INDEX IF NOT EXISTS nga_reference_call_idx
    ON nga_reference_delivery(call_sid, created_at);
CREATE INDEX IF NOT EXISTS nga_reference_phone_idx
    ON nga_reference_delivery(phone_hash, created_at);

CREATE TABLE IF NOT EXISTS nga_turn_event (
    event_id        TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id         INTEGER NOT NULL,
    channel         TEXT NOT NULL,
    language        TEXT,
    question        TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT '{}',
    tool_calls      TEXT NOT NULL DEFAULT '[]',
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    citations       TEXT NOT NULL DEFAULT '[]',
    answer          TEXT NOT NULL DEFAULT '',
    answer_status   TEXT NOT NULL DEFAULT '',
    verified        INTEGER NOT NULL DEFAULT 0,
    latency         TEXT NOT NULL DEFAULT '{}',
    session_state   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, turn_id)
);
CREATE INDEX IF NOT EXISTS nga_turn_conv_idx
    ON nga_turn_event(conversation_id, turn_id);
CREATE INDEX IF NOT EXISTS nga_turn_created_idx
    ON nga_turn_event(created_at);
"""


class GraphStore:
    """Create, replace and read the navigation graph in SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(_SCHEMA)
            columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(nga_node)").fetchall()
            }
            if "canonical_id" not in columns:
                con.execute(
                    "ALTER TABLE nga_node ADD COLUMN canonical_id "
                    "TEXT NOT NULL DEFAULT ''"
                )
            document_columns = {
                row["name"]
                for row in con.execute(
                    "PRAGMA table_info(nga_document)"
                ).fetchall()
            }
            if "source_page_id" not in document_columns:
                con.execute(
                    "ALTER TABLE nga_document ADD COLUMN source_page_id "
                    "TEXT NOT NULL DEFAULT ''"
                )
            con.execute(
                "UPDATE nga_node SET canonical_id = node_id "
                "WHERE canonical_id = '' OR canonical_id IS NULL"
            )
            con.execute(
                "INSERT OR IGNORE INTO nga_schema_version(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            con.commit()

    def replace_graph(
        self,
        nodes: list[SiteNode] | tuple[SiteNode, ...],
        edges: list[SiteEdge] | tuple[SiteEdge, ...],
        *,
        coverage: dict,
        graph_hash: str,
        generated_at: str,
        source_mode: str,
    ) -> None:
        """Atomically replace the stored graph with one compile result."""
        ordered = sorted(nodes, key=lambda node: (node.depth, node.node_id))
        con = self._connect()
        try:
            con.execute("BEGIN")
            con.execute("DELETE FROM nga_edge")
            con.execute("DELETE FROM nga_node")
            con.execute("DELETE FROM nga_body_fts")
            con.executemany(
                """
                INSERT INTO nga_node (
                    node_id, url, canonical_path, parent_id, depth, section,
                    title, nav_label, aliases, languages, content_type,
                    authority, pii_class, status, lastmod, content_hash,
                    fetched_at, verified_at, size_bytes, canonical_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.node_id,
                        node.url,
                        node.canonical_path,
                        node.parent_id,
                        node.depth,
                        node.section,
                        node.title,
                        node.nav_label,
                        json.dumps(list(node.aliases), ensure_ascii=False),
                        json.dumps(list(node.languages), ensure_ascii=False),
                        node.content_type.value,
                        node.authority.value,
                        node.pii_class.value,
                        node.status.value,
                        node.lastmod,
                        node.content_hash,
                        node.fetched_at,
                        node.verified_at,
                        node.size_bytes,
                        node.canonical_id or node.node_id,
                    )
                    for node in ordered
                ],
            )
            con.executemany(
                """
                INSERT INTO nga_edge (edge_id, src_id, dst_id, kind, anchor, ordinal)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        edge.edge_id,
                        edge.src_id,
                        edge.dst_id,
                        edge.kind,
                        edge.anchor,
                        edge.ordinal,
                    )
                    for edge in edges
                ],
            )
            con.execute("DELETE FROM nga_fts")
            con.executemany(
                """
                INSERT INTO nga_fts (
                    node_id, title, nav_label, aliases, section, canonical_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.node_id,
                        node.title,
                        node.nav_label,
                        " ".join(node.aliases),
                        node.section,
                        node.canonical_path,
                    )
                    for node in ordered
                    if node.status.value == "ok" and node.pii_class.value == "public"
                ],
            )
            con.execute(
                """
                INSERT INTO nga_compile_run (
                    generated_at, graph_hash, source_mode, nodes, edges, coverage
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    generated_at,
                    graph_hash,
                    source_mode,
                    len(ordered),
                    len(edges),
                    json.dumps(coverage, ensure_ascii=False),
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def load_nodes(self) -> list[SiteNode]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM nga_node ORDER BY depth, node_id"
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def load_edges(self) -> list[SiteEdge]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM nga_edge ORDER BY kind, src_id, ordinal, edge_id"
            ).fetchall()
        return [
            SiteEdge(
                edge_id=row["edge_id"],
                src_id=row["src_id"],
                dst_id=row["dst_id"],
                kind=row["kind"],
                anchor=row["anchor"],
                ordinal=int(row["ordinal"]),
            )
            for row in rows
        ]

    # ── graph reads ────────────────────────────────────────────────

    def get_node(self, node_id: str) -> SiteNode | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_node WHERE node_id = ?", (node_id,)
            ).fetchone()
        return self._row_to_node(row) if row is not None else None

    def get_node_by_path(self, path: str) -> SiteNode | None:
        canonical = (path or "/").strip()
        if not canonical.startswith("/"):
            canonical = "/" + canonical
        if len(canonical) > 1:
            canonical = canonical.rstrip("/")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_node WHERE canonical_path = ? "
                "ORDER BY depth LIMIT 1",
                (canonical,),
            ).fetchone()
        return self._row_to_node(row) if row is not None else None

    def get_edge(self, edge_id: str) -> SiteEdge | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_edge WHERE edge_id = ?", (edge_id,)
            ).fetchone()
        if row is None:
            return None
        return SiteEdge(
            edge_id=row["edge_id"],
            src_id=row["src_id"],
            dst_id=row["dst_id"],
            kind=row["kind"],
            anchor=row["anchor"],
            ordinal=int(row["ordinal"]),
        )

    def children(self, node_id: str) -> list[SiteNode]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT n.* FROM nga_edge e
                JOIN nga_node n ON n.node_id = e.dst_id
                WHERE e.src_id = ? AND e.kind = 'parent_of'
                ORDER BY e.ordinal, n.node_id
                """,
                (node_id,),
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def parent(self, node_id: str) -> SiteNode | None:
        node = self.get_node(node_id)
        if node is None or node.parent_id is None:
            return None
        return self.get_node(node.parent_id)

    def siblings(self, node_id: str) -> list[SiteNode]:
        node = self.get_node(node_id)
        if node is None or node.parent_id is None:
            return []
        return [
            other for other in self.children(node.parent_id)
            if other.node_id != node_id
        ]

    def breadcrumb(self, node_id: str) -> list[SiteNode]:
        chain: list[SiteNode] = []
        cursor = self.get_node(node_id)
        seen: set[str] = set()
        while cursor is not None and cursor.node_id not in seen:
            seen.add(cursor.node_id)
            chain.append(cursor)
            cursor = (
                self.get_node(cursor.parent_id)
                if cursor.parent_id else None
            )
        return list(reversed(chain))

    def outgoing_edges(self, node_id: str) -> list[SiteEdge]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM nga_edge WHERE src_id = ? "
                "ORDER BY kind, ordinal, dst_id",
                (node_id,),
            ).fetchall()
        return [
            SiteEdge(
                edge_id=row["edge_id"],
                src_id=row["src_id"],
                dst_id=row["dst_id"],
                kind=row["kind"],
                anchor=row["anchor"],
                ordinal=int(row["ordinal"]),
            )
            for row in rows
        ]

    def documents(self, node_id: str) -> list[SiteNode]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT n.* FROM nga_edge e
                JOIN nga_node n ON n.node_id = e.dst_id
                WHERE e.src_id = ? AND e.kind = 'documents'
                ORDER BY e.ordinal, n.node_id
                """,
                (node_id,),
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def get_node_by_url(self, url: str) -> SiteNode | None:
        value = (url or "").strip()
        if not value:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_node WHERE url = ? "
                "ORDER BY depth, node_id LIMIT 1",
                (value,),
            ).fetchone()
        return self._row_to_node(row) if row is not None else None

    def document(self, node_id: str) -> SiteDocument | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_document WHERE node_id = ?", (node_id,)
            ).fetchone()
        return self._row_to_document(row) if row is not None else None

    def documents_needing_review(self, limit: int = 50) -> list[SiteDocument]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM nga_document WHERE needs_review = 1 "
                "ORDER BY fetched_at, node_id LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def document_summary(self) -> dict:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN extraction_status = 'done' THEN 1 ELSE 0 END)
                        AS done,
                    SUM(CASE WHEN extraction_status = 'done'
                              AND needs_review = 0
                              AND pii_flagged = 0 THEN 1 ELSE 0 END)
                        AS indexed,
                    SUM(CASE WHEN extraction_status = 'pending' THEN 1 ELSE 0 END)
                        AS pending,
                    SUM(CASE WHEN extraction_status = 'error' THEN 1 ELSE 0 END)
                        AS errors,
                    SUM(CASE WHEN extraction_status = 'empty' THEN 1 ELSE 0 END)
                        AS empty,
                    SUM(CASE WHEN extraction_status = 'blocked' THEN 1 ELSE 0 END)
                        AS blocked,
                    SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END)
                        AS needs_review,
                    SUM(CASE WHEN needs_review = 1
                              AND extraction_status = 'done' THEN 1 ELSE 0 END)
                        AS review_pending
                FROM nga_document
                """
            ).fetchone()
        data = {key: row[key] for key in row.keys()}
        for key in (
            "total",
            "done",
            "indexed",
            "pending",
            "errors",
            "empty",
            "blocked",
            "needs_review",
            "review_pending",
        ):
            data[key] = int(data.get(key) or 0)
        return data

    def has_sent_reference(self, call_sid: str, node_id: str) -> bool:
        if not call_sid or not node_id:
            return False
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM nga_reference_delivery "
                "WHERE call_sid = ? AND node_id = ? AND status = 'sent'",
                (call_sid, node_id),
            ).fetchone()
        return row is not None

    def sent_reference_count(self, call_sid: str) -> int:
        if not call_sid:
            return 0
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM nga_reference_delivery "
                "WHERE call_sid = ? AND status = 'sent'",
                (call_sid,),
            ).fetchone()
        return int(row["n"] or 0)

    def last_reference_sent_at(self, call_sid: str) -> str | None:
        if not call_sid:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT MAX(created_at) AS last FROM nga_reference_delivery "
                "WHERE call_sid = ? AND status = 'sent'",
                (call_sid,),
            ).fetchone()
        return row["last"] if row is not None else None

    def record_reference_delivery(
        self,
        *,
        call_sid: str,
        conversation_id: str,
        phone_hash: str,
        phone_last4: str,
        node_id: str,
        url: str,
        mode: str,
        status: str,
        reason: str = "",
        twilio_sid: str = "",
        error: str = "",
        created_at: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO nga_reference_delivery (
                    call_sid, conversation_id, phone_hash, phone_last4,
                    node_id, url, mode, status, reason, twilio_sid, error,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_sid, node_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    phone_hash = excluded.phone_hash,
                    phone_last4 = excluded.phone_last4,
                    url = excluded.url,
                    mode = excluded.mode,
                    status = excluded.status,
                    reason = excluded.reason,
                    twilio_sid = excluded.twilio_sid,
                    error = excluded.error,
                    created_at = excluded.created_at
                """,
                (
                    call_sid or "",
                    conversation_id or "",
                    phone_hash or "",
                    phone_last4 or "",
                    node_id or "",
                    url or "",
                    mode or "turn",
                    status,
                    reason or "",
                    twilio_sid or "",
                    (error or "")[:2000],
                    created_at,
                ),
            )
            con.commit()

    def reference_deliveries(
        self, *, call_sid: str | None = None, limit: int = 50
    ) -> list[dict]:
        sql = "SELECT * FROM nga_reference_delivery"
        params: list[object] = []
        if call_sid is not None:
            sql += " WHERE call_sid = ?"
            params.append(call_sid)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def reference_delivery_summary(self, *, since_iso: str | None = None) -> dict:
        sql = (
            "SELECT status, COUNT(*) AS n FROM nga_reference_delivery"
        )
        params: list[object] = []
        if since_iso:
            sql += " WHERE created_at >= ?"
            params.append(since_iso)
        sql += " GROUP BY status"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        summary = {
            "sent": 0,
            "skipped": 0,
            "template_required": 0,
            "failed": 0,
        }
        for row in rows:
            summary[str(row["status"])] = int(row["n"])
        summary["total"] = sum(summary.values())
        return summary

    def purge_reference_deliveries(self, before_iso: str) -> int:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM nga_reference_delivery WHERE created_at < ?",
                (before_iso,),
            )
            con.commit()
            return int(cursor.rowcount or 0)

    def latest_document_links(self) -> list[tuple[str, tuple[dict, ...]]]:
        """Latest page version's linked-document metadata, per page."""
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT pv.node_id, pv.documents
                FROM nga_page_version pv
                JOIN (
                    SELECT node_id, MAX(version) AS version
                    FROM nga_page_version GROUP BY node_id
                ) latest
                  ON latest.node_id = pv.node_id
                 AND latest.version = pv.version
                """
            ).fetchall()
        return [
            (str(row["node_id"]), tuple(json.loads(row["documents"] or "[]")))
            for row in rows
        ]

    def due_documents(
        self,
        *,
        limit: int = 10,
        force: bool = False,
        node_ids: list[str] | tuple[str, ...] | None = None,
        now: str | None = None,
    ) -> list[SiteNode]:
        now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params: list[object] = []
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            sql = (
                "SELECT n.* FROM nga_node n WHERE n.node_id IN "
                f"({placeholders}) AND n.status = 'ok' "
                "AND n.pii_class = 'public' AND n.content_type = 'pdf'"
            )
            params.extend(node_ids)
        else:
            sql = """
                SELECT n.* FROM nga_node n
                LEFT JOIN nga_fetch_state f ON f.node_id = n.node_id
                WHERE n.status = 'ok'
                  AND n.pii_class = 'public'
                  AND n.content_type = 'pdf'
                  AND EXISTS (
                    SELECT 1 FROM nga_document d
                    WHERE d.node_id = n.node_id
                      AND d.source_page_id != ''
                  )
                  AND (
                    ? = 1
                    OR f.node_id IS NULL
                    OR f.next_due_at IS NULL
                    OR f.next_due_at <= ?
                  )
            """
            params.extend([1 if force else 0, now])
        if "LEFT JOIN" in sql:
            sql += " ORDER BY COALESCE(f.next_due_at, ''), n.depth, n.node_id"
        else:
            sql += " ORDER BY n.depth, n.node_id"
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def search_fts(
        self,
        match_query: str,
        *,
        limit: int = 10,
        section: str | None = None,
        content_type: str | None = None,
    ) -> list[tuple[SiteNode, float]]:
        """FTS candidates, best first. ``rank`` is SQLite bm25 (lower is better)."""
        sql = """
            SELECT n.*, bm25(nga_fts, 0.0, 5.0, 4.0, 3.0, 2.0, 1.0) AS rank
            FROM nga_fts
            JOIN nga_node n ON n.node_id = nga_fts.node_id
            WHERE nga_fts MATCH ?
              AND n.status = 'ok'
              AND n.pii_class = 'public'
              AND (n.canonical_id = n.node_id OR n.canonical_id = '')
        """
        params: list[object] = [match_query]
        if section:
            sql += " AND lower(n.section) = lower(?)"
            params.append(section)
        if content_type:
            sql += " AND n.content_type = ?"
            params.append(content_type)
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [(self._row_to_node(row), float(row["rank"])) for row in rows]

    def search_body_fts(
        self,
        match_query: str,
        *,
        limit: int = 10,
        section: str | None = None,
        content_type: str | None = None,
    ) -> list[tuple[SiteNode, float]]:
        """Body-index candidates, best first. Canonical nodes only."""
        sql = """
            SELECT n.*, bm25(nga_body_fts, 0.0, 4.0, 1.0) AS rank
            FROM nga_body_fts
            JOIN nga_node n ON n.node_id = nga_body_fts.node_id
            WHERE nga_body_fts MATCH ?
              AND n.status = 'ok'
              AND n.pii_class = 'public'
              AND (n.canonical_id = n.node_id OR n.canonical_id = '')
        """
        params: list[object] = [match_query]
        if section:
            sql += " AND lower(n.section) = lower(?)"
            params.append(section)
        if content_type:
            sql += " AND n.content_type = ?"
            params.append(content_type)
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [(self._row_to_node(row), float(row["rank"])) for row in rows]

    # ── content and freshness ──────────────────────────────────────

    def page_version(self, node_id: str) -> PageVersion | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_page_version WHERE node_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return PageVersion(
            node_id=row["node_id"],
            version=int(row["version"]),
            text=row["text"],
            markdown=row["markdown"],
            tables=tuple(json.loads(row["tables"] or "[]")),
            sections=tuple(json.loads(row["sections"] or "[]")),
            links=tuple(json.loads(row["links"] or "[]")),
            documents=tuple(json.loads(row["documents"] or "[]")),
            language=row["language"],
            content_hash=row["content_hash"],
            fetched_at=row["fetched_at"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
        )

    def fetch_state(self, node_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_fetch_state WHERE node_id = ?", (node_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def node_status_counts(self) -> dict[str, int]:
        """Node counts by lifecycle status, for the refresh report."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM nga_node GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def fetch_summary(
        self,
        *,
        now: str | None = None,
        failure_streak: int = 3,
        stale_before: str | None = None,
    ) -> dict:
        """Freshness counters for the refresh report and alerts.

        Only public HTML pages that are expected to have a body are counted.
        A node with no fetch state has never been checked; it is not stale
        until a first fetch sets ``next_due_at``.
        """
        now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_before = stale_before or now
        with self._connect() as con:
            row = con.execute(
                """
                SELECT
                    COUNT(*) AS tracked,
                    SUM(CASE WHEN f.checked_at IS NULL THEN 1 ELSE 0 END)
                        AS never_checked,
                    SUM(CASE WHEN f.checked_at IS NOT NULL THEN 1 ELSE 0 END)
                        AS checked,
                    SUM(CASE WHEN f.failure_count > 0 THEN 1 ELSE 0 END)
                        AS failing,
                    SUM(CASE WHEN f.failure_count >= ? THEN 1 ELSE 0 END)
                        AS failure_streak,
                    SUM(CASE WHEN f.next_due_at IS NOT NULL
                              AND f.next_due_at <= ? THEN 1 ELSE 0 END)
                        AS due_now,
                    SUM(CASE WHEN f.next_due_at IS NOT NULL
                              AND f.next_due_at <= ? THEN 1 ELSE 0 END)
                        AS stale,
                    MIN(f.checked_at) AS oldest_checked_at,
                    MAX(f.checked_at) AS newest_checked_at
                FROM nga_node n
                LEFT JOIN nga_fetch_state f ON f.node_id = n.node_id
                WHERE n.status = 'ok'
                  AND n.pii_class = 'public'
                  AND n.content_type = 'html'
                """,
                (max(1, int(failure_streak)), now, stale_before),
            ).fetchone()
        data = {key: row[key] for key in row.keys()}
        for key in (
            "tracked",
            "never_checked",
            "checked",
            "failing",
            "failure_streak",
            "due_now",
            "stale",
        ):
            data[key] = int(data.get(key) or 0)
        return data

    def due_nodes(
        self,
        *,
        limit: int = 10,
        force: bool = False,
        node_ids: list[str] | tuple[str, ...] | None = None,
        section: str | None = None,
        now: str | None = None,
    ) -> list[SiteNode]:
        """Nodes whose fetch is due (or explicitly requested)."""
        now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params: list[object] = []
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            sql = (
                "SELECT n.* FROM nga_node n WHERE n.node_id IN "
                f"({placeholders}) AND n.status = 'ok' "
                "AND n.pii_class = 'public' AND n.content_type = 'html'"
            )
            params.extend(node_ids)
        else:
            sql = """
                SELECT n.* FROM nga_node n
                LEFT JOIN nga_fetch_state f ON f.node_id = n.node_id
                WHERE n.status = 'ok'
                  AND n.pii_class = 'public'
                  AND n.content_type = 'html'
                  AND (
                    ? = 1
                    OR f.node_id IS NULL
                    OR f.next_due_at IS NULL
                    OR f.next_due_at <= ?
                  )
            """
            params.extend([1 if force else 0, now])
        if section:
            sql += " AND lower(n.section) = lower(?)"
            params.append(section)
        if "LEFT JOIN" in sql:
            sql += " ORDER BY COALESCE(f.next_due_at, ''), n.depth, n.node_id"
        else:
            sql += " ORDER BY n.depth, n.node_id"
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def record_page(
        self,
        node_id: str,
        *,
        text: str,
        markdown: str,
        tables: tuple[dict, ...] | list[dict],
        sections: tuple[dict, ...] | list[dict],
        links: tuple[dict, ...] | list[dict],
        documents: tuple[dict, ...] | list[dict],
        language: str,
        content_hash: str,
        fetched_at: str,
        verified_at: str,
        size_bytes: int,
        etag: str,
        last_modified: str,
        http_status: int,
        next_due_at: str,
    ) -> int:
        """Store a new page version, update the node and re-index its body."""
        con = self._connect()
        try:
            con.execute("BEGIN")
            row = con.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next "
                "FROM nga_page_version WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            version = int(row["next"])
            con.execute(
                """
                INSERT INTO nga_page_version (
                    node_id, version, text, markdown, tables, sections, links,
                    documents, language, content_hash, fetched_at, valid_from
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    version,
                    text,
                    markdown,
                    json.dumps(list(tables), ensure_ascii=False),
                    json.dumps(list(sections), ensure_ascii=False),
                    json.dumps(list(links), ensure_ascii=False),
                    json.dumps(list(documents), ensure_ascii=False),
                    language,
                    content_hash,
                    fetched_at,
                    fetched_at,
                ),
            )
            con.execute(
                """
                UPDATE nga_node
                SET content_hash = ?, fetched_at = ?, verified_at = ?,
                    size_bytes = ?, status = 'ok'
                WHERE node_id = ?
                """,
                (content_hash, fetched_at, verified_at, size_bytes, node_id),
            )
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                checked_at=verified_at,
                next_due_at=next_due_at,
                failure_count=0,
                error="",
            )
            con.execute("DELETE FROM nga_body_fts WHERE node_id = ?", (node_id,))
            title_row = con.execute(
                "SELECT title FROM nga_node WHERE node_id = ?", (node_id,)
            ).fetchone()
            title = title_row["title"] if title_row else ""
            if text.strip():
                con.execute(
                    "INSERT INTO nga_body_fts (node_id, title, body) "
                    "VALUES (?, ?, ?)",
                    (node_id, title, text),
                )
            con.commit()
            return version
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_document_node(
        self,
        *,
        url: str,
        title: str,
        parent: SiteNode,
    ) -> SiteNode | None:
        """Create or reuse a PDF node discovered on an indexed page."""
        value = (url or "").strip()
        if not value:
            return None
        label = (title or "").strip() or value.rsplit("/", 1)[-1] or value
        existing = self.get_node_by_url(value)
        if existing is not None:
            if existing.content_type is not ContentType.PDF:
                return None
            self._ensure_document_link(parent, existing, label)
            return existing

        node_id = "doc." + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        path = urlparse(value).path or value
        con = self._connect()
        try:
            con.execute("BEGIN")
            created = con.execute(
                """
                INSERT OR IGNORE INTO nga_node (
                    node_id, url, canonical_path, parent_id, depth, section,
                    title, nav_label, aliases, languages, content_type,
                    authority, pii_class, status, lastmod, content_hash,
                    fetched_at, verified_at, size_bytes, canonical_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', 'pdf',
                          'official_document', 'public', 'ok', NULL, '', '', NULL,
                          0, ?)
                """,
                (
                    node_id,
                    value,
                    path,
                    parent.node_id,
                    parent.depth + 1,
                    parent.section,
                    label,
                    label,
                    node_id,
                ),
            ).rowcount
            if created:
                con.execute(
                    """
                    INSERT INTO nga_fts (
                        node_id, title, nav_label, aliases, section,
                        canonical_path
                    ) VALUES (?, ?, ?, '', ?, ?)
                    """,
                    (node_id, label, label, parent.section, path),
                )
            ordinal = con.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next FROM nga_edge "
                "WHERE src_id = ? AND kind = 'parent_of'",
                (parent.node_id,),
            ).fetchone()["next"]
            for kind in ("parent_of", "documents"):
                con.execute(
                    "INSERT OR IGNORE INTO nga_edge "
                    "(edge_id, src_id, dst_id, kind, anchor, ordinal) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"{kind}:{parent.node_id}:{node_id}",
                        parent.node_id,
                        node_id,
                        kind,
                        label,
                        int(ordinal),
                    ),
                )
            con.execute(
                "INSERT OR IGNORE INTO nga_document "
                "(node_id, url, source_page_id) VALUES (?, ?, ?)",
                (node_id, value, parent.node_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return self.get_node(node_id)

    def _ensure_document_link(
        self, parent: SiteNode, doc: SiteNode, label: str
    ) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN")
            ordinal = con.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next FROM nga_edge "
                "WHERE src_id = ? AND kind = 'documents'",
                (parent.node_id,),
            ).fetchone()["next"]
            con.execute(
                "INSERT OR IGNORE INTO nga_edge "
                "(edge_id, src_id, dst_id, kind, anchor, ordinal) "
                "VALUES (?, ?, ?, 'documents', ?, ?)",
                (
                    f"documents:{parent.node_id}:{doc.node_id}",
                    parent.node_id,
                    doc.node_id,
                    label,
                    int(ordinal),
                ),
            )
            con.execute(
                "INSERT OR IGNORE INTO nga_document "
                "(node_id, url, source_page_id) VALUES (?, ?, ?)",
                (doc.node_id, doc.url, parent.node_id),
            )
            con.execute(
                "UPDATE nga_document SET source_page_id = ? "
                "WHERE node_id = ? AND source_page_id = ''",
                (parent.node_id, doc.node_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_document_row(self, node: SiteNode) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO nga_document (node_id, url) VALUES (?, ?)",
                (node.node_id, node.url),
            )
            con.commit()

    def record_document(
        self,
        node_id: str,
        *,
        url: str,
        mime: str,
        pages: int,
        status: DocumentStatus | str,
        text: str,
        tables,
        ocr: bool,
        ocr_lang: str,
        ocr_quality: str,
        needs_review: bool,
        pii_flagged: bool,
        content_hash: str,
        size_bytes: int,
        fetched_at: str,
        verified_at: str | None,
        error: str = "",
        etag: str = "",
        last_modified: str = "",
        http_status: int = 200,
        next_due_at: str | None = None,
    ) -> None:
        """Store one extracted PDF and re-index its text when answerable."""
        status_value = status.value if isinstance(status, DocumentStatus) else str(status)
        con = self._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                "INSERT OR IGNORE INTO nga_document (node_id, url) VALUES (?, ?)",
                (node_id, url),
            )
            con.execute(
                """
                UPDATE nga_document
                SET url = ?, mime = ?, pages = ?, extraction_status = ?,
                    text = ?, tables = ?, ocr = ?, ocr_lang = ?,
                    ocr_quality = ?, needs_review = ?, pii_flagged = ?,
                    content_hash = ?, size_bytes = ?, fetched_at = ?,
                    verified_at = ?, error = ?
                WHERE node_id = ?
                """,
                (
                    url,
                    mime,
                    max(0, int(pages)),
                    status_value,
                    text,
                    json.dumps(list(tables), ensure_ascii=False),
                    int(bool(ocr)),
                    ocr_lang,
                    ocr_quality,
                    int(bool(needs_review)),
                    int(bool(pii_flagged)),
                    content_hash,
                    max(0, int(size_bytes)),
                    fetched_at,
                    verified_at,
                    error,
                    node_id,
                ),
            )
            con.execute(
                """
                UPDATE nga_node
                SET content_hash = ?, fetched_at = ?, verified_at = ?,
                    size_bytes = ?, status = 'ok'
                WHERE node_id = ?
                """,
                (content_hash, fetched_at, verified_at, size_bytes, node_id),
            )
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                checked_at=fetched_at,
                next_due_at=next_due_at,
                failure_count=0,
                error="",
            )
            self._reindex_document_body(
                con,
                node_id=node_id,
                text=text,
                indexable=(
                    status_value == DocumentStatus.DONE.value
                    and not needs_review
                    and not pii_flagged
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def record_document_not_modified(
        self,
        node_id: str,
        *,
        checked_at: str,
        next_due_at: str,
        etag: str = "",
        last_modified: str = "",
        http_status: int = 304,
        verified_at: str | None = None,
    ) -> None:
        """Revalidate a document fetch without touching its stored text."""
        con = self._connect()
        try:
            con.execute("BEGIN")
            if verified_at:
                con.execute(
                    "UPDATE nga_node SET verified_at = ? WHERE node_id = ?",
                    (verified_at, node_id),
                )
                con.execute(
                    "UPDATE nga_document SET verified_at = ? WHERE node_id = ?",
                    (verified_at, node_id),
                )
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                checked_at=checked_at,
                next_due_at=next_due_at,
                failure_count=0,
                error="",
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def set_document_error(self, node_id: str, error: str) -> None:
        """Record the last extraction/fetch error without deleting good text."""
        with self._connect() as con:
            con.execute(
                """
                UPDATE nga_document
                SET error = ?,
                    extraction_status = CASE
                        WHEN extraction_status = 'pending' THEN 'error'
                        ELSE extraction_status END
                WHERE node_id = ?
                """,
                ((error or "")[:2000], node_id),
            )
            con.commit()

    def record_document_blocked(
        self,
        node_id: str,
        *,
        url: str,
        error: str,
        content_hash: str,
        size_bytes: int,
        fetched_at: str,
        pages: int,
    ) -> None:
        """Content-PII block: retain metadata only, never the text."""
        con = self._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                "INSERT OR IGNORE INTO nga_document (node_id, url) VALUES (?, ?)",
                (node_id, url),
            )
            con.execute(
                """
                UPDATE nga_document
                SET extraction_status = 'blocked', text = '', tables = '[]',
                    needs_review = 0, pii_flagged = 1, error = ?, pages = ?,
                    content_hash = ?, size_bytes = ?, fetched_at = ?,
                    verified_at = NULL
                WHERE node_id = ?
                """,
                (
                    (error or "")[:2000],
                    max(0, int(pages)),
                    content_hash,
                    max(0, int(size_bytes)),
                    fetched_at,
                    node_id,
                ),
            )
            con.execute(
                """
                UPDATE nga_node
                SET status = 'blocked', pii_class = 'blocked',
                    content_hash = ?, fetched_at = ?, size_bytes = ?
                WHERE node_id = ?
                """,
                (content_hash, fetched_at, size_bytes, node_id),
            )
            self._reindex_document_body(con, node_id=node_id, text="", indexable=False)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def mark_document_reviewed(
        self,
        node_id: str,
        *,
        reviewer: str,
        now: str,
    ) -> bool:
        """Approve OCR text for answer use and index it."""
        con = self._connect()
        try:
            con.execute("BEGIN")
            row = con.execute(
                "SELECT * FROM nga_document WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row is None:
                con.rollback()
                return False
            if (
                row["extraction_status"] != DocumentStatus.DONE.value
                or int(row["pii_flagged"] or 0)
            ):
                con.rollback()
                return False
            con.execute(
                """
                UPDATE nga_document
                SET needs_review = 0, reviewed_at = ?, reviewed_by = ?,
                    verified_at = ?, error = ''
                WHERE node_id = ?
                """,
                (now, (reviewer or "")[:200], now, node_id),
            )
            con.execute(
                "UPDATE nga_node SET verified_at = ? WHERE node_id = ?",
                (now, node_id),
            )
            self._reindex_document_body(
                con, node_id=node_id, text=row["text"] or "", indexable=True
            )
            con.commit()
            return True
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _reindex_document_body(
        con: sqlite3.Connection,
        *,
        node_id: str,
        text: str,
        indexable: bool,
    ) -> None:
        con.execute("DELETE FROM nga_body_fts WHERE node_id = ?", (node_id,))
        if not indexable or not (text or "").strip():
            return
        title_row = con.execute(
            "SELECT title FROM nga_node WHERE node_id = ?", (node_id,)
        ).fetchone()
        title = title_row["title"] if title_row else ""
        con.execute(
            "INSERT INTO nga_body_fts (node_id, title, body) VALUES (?, ?, ?)",
            (node_id, title, text),
        )

    def record_not_modified(
        self,
        node_id: str,
        *,
        checked_at: str,
        next_due_at: str,
        etag: str = "",
        last_modified: str = "",
        http_status: int = 304,
    ) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                "UPDATE nga_node SET verified_at = ? WHERE node_id = ?",
                (checked_at, node_id),
            )
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                checked_at=checked_at,
                next_due_at=next_due_at,
                failure_count=0,
                error="",
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def record_status(
        self,
        node_id: str,
        *,
        status: NodeStatus,
        checked_at: str,
        next_due_at: str,
        http_status: int | None = None,
        etag: str = "",
        last_modified: str = "",
        error: str = "",
    ) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                "UPDATE nga_node SET status = ? WHERE node_id = ?",
                (status.value, node_id),
            )
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag=etag,
                last_modified=last_modified,
                http_status=http_status,
                checked_at=checked_at,
                next_due_at=next_due_at,
                failure_count=0,
                error=error,
            )
            con.execute("DELETE FROM nga_body_fts WHERE node_id = ?", (node_id,))
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def record_fetch_failure(
        self,
        node_id: str,
        *,
        checked_at: str,
        next_due_at: str,
        http_status: int | None = None,
        error: str = "",
    ) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN")
            row = con.execute(
                "SELECT failure_count FROM nga_fetch_state WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            failures = int(row["failure_count"]) + 1 if row else 1
            self._upsert_fetch_state(
                con,
                node_id=node_id,
                etag="",
                last_modified="",
                http_status=http_status,
                checked_at=checked_at,
                next_due_at=next_due_at,
                failure_count=failures,
                error=error[:500],
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _upsert_fetch_state(
        con: sqlite3.Connection,
        *,
        node_id: str,
        etag: str,
        last_modified: str,
        http_status: int | None,
        checked_at: str,
        next_due_at: str,
        failure_count: int,
        error: str,
    ) -> None:
        con.execute(
            """
            INSERT INTO nga_fetch_state (
                node_id, etag, last_modified, http_status, checked_at,
                next_due_at, failure_count, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                http_status = excluded.http_status,
                checked_at = excluded.checked_at,
                next_due_at = excluded.next_due_at,
                failure_count = excluded.failure_count,
                error = excluded.error
            """,
            (
                node_id, etag, last_modified, http_status, checked_at,
                next_due_at, failure_count, error,
            ),
        )

    def reset_canonical_ids(self) -> None:
        with self._connect() as con:
            con.execute("UPDATE nga_node SET canonical_id = node_id")
            con.commit()

    def set_canonical_group(self, node_ids: list[str], canonical_id: str) -> None:
        if not node_ids:
            return
        with self._connect() as con:
            con.executemany(
                "UPDATE nga_node SET canonical_id = ? WHERE node_id = ?",
                [(canonical_id, node_id) for node_id in node_ids],
            )
            con.commit()

    # ── turn events ───────────────────────────────────────────────

    def next_turn_id(self, conversation_id: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(turn_id), 0) + 1 AS next "
                "FROM nga_turn_event WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row["next"])

    def append_turn_event(self, event) -> None:
        """Persist a ``TurnEvent`` (or an object exposing ``to_dict``)."""
        data = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO nga_turn_event (
                    event_id, conversation_id, turn_id, channel, language,
                    question, plan, tool_calls, evidence_ids, citations,
                    answer, answer_status, verified, latency, session_state,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["event_id"],
                    data["conversation_id"],
                    int(data["turn_id"]),
                    data["channel"],
                    data.get("language"),
                    data["question"],
                    json.dumps(data.get("plan") or {}, ensure_ascii=False),
                    json.dumps(list(data.get("tool_calls") or []), ensure_ascii=False),
                    json.dumps(
                        list(data.get("evidence_ids") or []), ensure_ascii=False
                    ),
                    json.dumps(list(data.get("citations") or []), ensure_ascii=False),
                    data.get("answer") or "",
                    data.get("answer_status") or "",
                    1 if data.get("verified") else 0,
                    json.dumps(data.get("latency") or {}, ensure_ascii=False),
                    json.dumps(
                        data.get("session_state") or {}, ensure_ascii=False
                    ),
                    data["created_at"],
                ),
            )
            con.commit()

    def turn_events(
        self, conversation_id: str, *, limit: int = 20
    ) -> list[dict]:
        """Oldest-first events for one conversation."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM nga_turn_event WHERE conversation_id = ? "
                "ORDER BY turn_id DESC LIMIT ?",
                (conversation_id, max(1, int(limit))),
            ).fetchall()
        return [self._row_to_event(row) for row in reversed(rows)]

    def last_turn_event(self, conversation_id: str) -> dict | None:
        events = self.turn_events(conversation_id, limit=1)
        return events[-1] if events else None

    def turn_event_count(self, conversation_id: str | None = None) -> int:
        with self._connect() as con:
            if conversation_id:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM nga_turn_event "
                    "WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM nga_turn_event"
                ).fetchone()
        return int(row["n"])

    def purge_turn_events(self, before_iso: str) -> int:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM nga_turn_event WHERE created_at < ?", (before_iso,)
            )
            con.commit()
            return int(cursor.rowcount or 0)

    def delete_turn_events(self, conversation_id: str) -> int:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM nga_turn_event WHERE conversation_id = ?",
                (conversation_id,),
            )
            con.commit()
            return int(cursor.rowcount or 0)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "conversation_id": row["conversation_id"],
            "turn_id": int(row["turn_id"]),
            "channel": row["channel"],
            "language": row["language"],
            "question": row["question"],
            "plan": json.loads(row["plan"] or "{}"),
            "tool_calls": json.loads(row["tool_calls"] or "[]"),
            "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
            "citations": json.loads(row["citations"] or "[]"),
            "answer": row["answer"],
            "answer_status": row["answer_status"],
            "verified": bool(row["verified"]),
            "latency": json.loads(row["latency"] or "{}"),
            "session_state": json.loads(row["session_state"] or "{}"),
            "created_at": row["created_at"],
        }

    def latest_run(self) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM nga_compile_run ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["coverage"] = json.loads(data.get("coverage") or "{}")
        return data

    def node_count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM nga_node").fetchone()
        return int(row["n"])

    def edge_count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS n FROM nga_edge").fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> SiteDocument:
        tables = json.loads(row["tables"] or "[]")
        return SiteDocument(
            node_id=row["node_id"],
            url=row["url"],
            mime=row["mime"],
            pages=int(row["pages"] or 0),
            extraction_status=row["extraction_status"],
            source_page_id=row["source_page_id"] or "",
            text=row["text"] or "",
            tables=tuple(tables),
            ocr=bool(row["ocr"]),
            ocr_lang=row["ocr_lang"] or "",
            ocr_quality=row["ocr_quality"] or "",
            needs_review=bool(row["needs_review"]),
            content_hash=row["content_hash"] or "",
            size_bytes=int(row["size_bytes"] or 0),
            fetched_at=row["fetched_at"],
            verified_at=row["verified_at"],
            error=row["error"] or "",
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"] or "",
            pii_flagged=bool(row["pii_flagged"]),
        )

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> SiteNode:
        return SiteNode(
            node_id=row["node_id"],
            url=row["url"],
            canonical_path=row["canonical_path"],
            parent_id=row["parent_id"],
            depth=int(row["depth"]),
            section=row["section"],
            title=row["title"],
            nav_label=row["nav_label"],
            aliases=tuple(json.loads(row["aliases"] or "[]")),
            languages=tuple(json.loads(row["languages"] or "[]")),
            content_type=ContentType(row["content_type"]),
            authority=Authority(row["authority"]),
            pii_class=PiiClass(row["pii_class"]),
            status=NodeStatus(row["status"]),
            lastmod=row["lastmod"],
            content_hash=row["content_hash"],
            fetched_at=row["fetched_at"],
            verified_at=row["verified_at"],
            size_bytes=int(row["size_bytes"]),
            canonical_id=row["canonical_id"] or row["node_id"],
        )
