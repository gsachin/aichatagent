"""SQLite serving-mode invariants.

The live answer path and the refresh worker share the graph file. WAL is the
minimum requirement for a reader to proceed while a writer holds a
transaction; without it the answer path can block for the whole busy timeout
and then fall back to the static corpus.
"""

from __future__ import annotations

import sqlite3

from app.sitegraph.store import GraphStore


def test_store_opens_in_wal_mode(tmp_path):
    store = GraphStore(tmp_path / "graph.sqlite3")
    with store._connect() as con:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_reader_proceeds_while_writer_holds_a_transaction(tmp_path):
    store = GraphStore(tmp_path / "graph.sqlite3")
    writer = sqlite3.connect(str(store.path), timeout=5.0)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO nga_schema_version(version) VALUES (?)", (99,)
        )
        # A separate connection must still read the committed graph while the
        # writer's transaction is open. Under the old rollback journal this
        # waits for the writer; under WAL it returns immediately.
        reader = store._connect()
        try:
            value = reader.execute(
                "SELECT COUNT(*) FROM nga_schema_version"
            ).fetchone()[0]
        finally:
            reader.close()
        assert value >= 1
    finally:
        writer.rollback()
        writer.close()
