"""WP1: batch indexing writes the index DB, never the serving DB."""

from __future__ import annotations

import sqlite3
import threading
import time

from app.sitegraph.documents import DocumentOptions, DocumentStats
from app.sitegraph.refresh import RefreshOptions, RefreshStats
from app.sitegraph.scheduler import RefreshScheduler
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer
from tests.nga_helpers import build_store, seed_page


def _copy_db(source, target) -> None:
    with sqlite3.connect(str(source), timeout=10) as src:
        with sqlite3.connect(str(target), timeout=10) as dst:
            src.backup(dst)


def test_index_writes_leave_the_serving_page_untouched(tmp_path):
    serve_store, serve_db = build_store(tmp_path / "serve")
    seed_page(serve_store, text="serving hostel text")
    index_db = tmp_path / "index.sqlite3"
    _copy_db(serve_db, index_db)
    index_store = GraphStore(index_db, busy_timeout_ms=30_000)

    before = serve_store.page_version("cms.hostel")
    assert before is not None

    seed_page(index_store, text="indexed hostel text from the backfill")
    after = serve_store.page_version("cms.hostel")
    indexed = index_store.page_version("cms.hostel")

    assert after is not None and after.text == before.text
    assert indexed is not None
    assert indexed.text == "indexed hostel text from the backfill"
    assert serve_store.document_summary() == index_store.document_summary()


def test_scheduler_document_refresh_uses_the_index_store(
    tmp_path, monkeypatch
):
    serve_store, _serve_db = build_store(tmp_path / "serve")
    index_store, _index_db = build_store(tmp_path / "index")
    captured: list[GraphStore] = []
    monkeypatch.setattr(
        "app.sitegraph.scheduler.refresh_graph",
        lambda *args, **kwargs: RefreshStats(),
    )
    monkeypatch.setattr(
        "app.sitegraph.scheduler.refresh_documents",
        lambda store, *args, **kwargs: (
            captured.append(store) or DocumentStats()
        ),
    )
    scheduler = RefreshScheduler(
        store=serve_store,
        document_store=index_store,
        fetcher=object(),
        options=RefreshOptions(ttl_s=3600, limit=1),
        report_path=tmp_path / "report.json",
        run_log_path=tmp_path / "runs.jsonl",
        alert_log_path=tmp_path / "alerts.jsonl",
        lock_path=tmp_path / "refresh.lock",
        document_options=DocumentOptions(limit=1),
        interval_s=0,
        jitter_s=0,
    )

    scheduler.run_once()

    assert captured == [index_store]


def test_a_serving_read_is_not_blocked_by_an_index_writer(tmp_path):
    serve_store, serve_db = build_store(tmp_path / "serve")
    seed_page(serve_store, text="serving hostel text")
    index_db = tmp_path / "index.sqlite3"
    _copy_db(serve_db, index_db)
    index_store = GraphStore(index_db, busy_timeout_ms=30_000)

    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for index in range(40):
                seed_page(
                    index_store,
                    text=f"indexed backfill row {index} " * 20,
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    server = SiteToolServer(serve_db, pii_guard=False, busy_timeout_ms=5_000)
    latencies: list[float] = []
    try:
        for _ in range(25):
            started = time.perf_counter()
            result = server.call("site.search", {"query": "hostel", "limit": 3})
            latencies.append(time.perf_counter() - started)
            assert result["status"] == "ok"
    finally:
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert errors == []
    assert max(latencies) < 1.0
