"""WP1: WAL and configurable busy timeout keep serving reads moving."""

from __future__ import annotations

import sqlite3
import threading
import time

from app.sitegraph.store import GraphStore
from tests.nga_helpers import build_store, seed_page


def test_busy_timeout_is_configurable_for_serving_and_batch(tmp_path):
    _store, db = build_store(tmp_path)

    serving = GraphStore(db, busy_timeout_ms=5_000)
    batch = GraphStore(db, busy_timeout_ms=30_000)

    assert serving.busy_timeout_ms == 5_000
    assert batch.busy_timeout_ms == 30_000
    with serving._connect() as con:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
    with batch._connect() as con:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000


def test_a_serving_reader_does_not_lock_behind_a_short_writer(tmp_path):
    store, db = build_store(tmp_path)
    seed_page(store)
    writer_started = threading.Event()
    writer_error: list[BaseException] = []

    def writer() -> None:
        try:
            with sqlite3.connect(str(db), timeout=10) as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "UPDATE nga_node SET title = title WHERE node_id = ?",
                    ("cms.hostel",),
                )
                writer_started.set()
                time.sleep(0.25)
                con.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            writer_error.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    assert writer_started.wait(timeout=5)
    started = time.perf_counter()
    serving = GraphStore(db, busy_timeout_ms=5_000)
    count = serving.node_count()
    elapsed = time.perf_counter() - started
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert writer_error == []
    assert count > 0
    assert elapsed < 5.0
