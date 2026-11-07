"""Document-only promotion from the index DB to the serving DB."""

from __future__ import annotations

import sqlite3

import pytest

from app.sitegraph.documents import DocumentStatus
from app.sitegraph.store import GraphStore
from scripts.nga_promote import PromotionError, promote
from tests.nga_helpers import build_store, seed_page

DOC_NODE = "uploads.news.fee-pdf"
DOC_TEXT = "Scholarship deadline is 15 June 2026."


def _build_pair(tmp_path):
    serve_store, serve_db = build_store(tmp_path / "serve")
    seed_page(serve_store, text="serving hostel text")
    index_store, index_db = build_store(tmp_path / "index")
    return serve_store, serve_db, index_store, index_db


def _record_index_document(index_store: GraphStore) -> None:
    index_store.record_document(
        DOC_NODE,
        url="https://dspmuranchi.ac.in/uploads/news/fee.pdf",
        mime="application/pdf",
        pages=1,
        status=DocumentStatus.DONE,
        text=DOC_TEXT,
        tables=(),
        ocr=False,
        ocr_lang="",
        ocr_quality="",
        needs_review=False,
        pii_flagged=False,
        content_hash="index-doc-hash",
        size_bytes=len(DOC_TEXT),
        fetched_at="2026-11-01T00:00:00Z",
        verified_at="2026-11-01T00:00:00Z",
        etag='"v2"',
        last_modified="Sat, 01 Nov 2026 00:00:00 GMT",
        http_status=200,
        next_due_at="2026-11-08T00:00:00Z",
    )


def _seed_conversation_state(db) -> None:
    with sqlite3.connect(str(db)) as con:
        con.execute(
            "INSERT INTO nga_turn_event "
            "(event_id, conversation_id, turn_id, channel, question, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("evt-1", "conv-1", 1, "chat", "hostel", "2026-11-01T00:00:00Z"),
        )
        con.execute(
            "INSERT INTO nga_answer_cache "
            "(cache_key, index_version, policy_version, status, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "cache-key",
                "index",
                "policy",
                "verified",
                "2026-11-01T00:00:00Z",
                "2026-11-02T00:00:00Z",
            ),
        )


def _count(db, table: str) -> int:
    with sqlite3.connect(str(db)) as con:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_dry_run_reports_document_changes_without_writing(tmp_path):
    serve_store, serve_db, index_store, index_db = _build_pair(tmp_path)
    _record_index_document(index_store)
    _seed_conversation_state(serve_db)

    before = serve_store.get_node("uploads.news.fee-pdf")
    result = promote(serve_db, index_db, apply=False)

    assert result["plan"]["document_rows"] == 1
    assert result["plan"]["body_rows_added"] == 1
    assert _count(serve_db, "nga_document") == 0
    assert _count(serve_db, "nga_answer_cache") == 1
    assert _count(serve_db, "nga_turn_event") == 1
    assert serve_store.get_node("uploads.news.fee-pdf") == before


def test_apply_promotes_documents_and_preserves_conversations(tmp_path):
    serve_store, serve_db, index_store, index_db = _build_pair(tmp_path)
    _record_index_document(index_store)
    _seed_conversation_state(serve_db)
    page_before = serve_store.page_version("cms.hostel")

    result = promote(serve_db, index_db, apply=True)

    assert result["plan"]["document_rows"] == 1
    assert result["body_rows_added"] == 1
    assert _count(serve_db, "nga_document") == 1
    assert _count(serve_db, "nga_answer_cache") == 0
    assert _count(serve_db, "nga_turn_event") == 1
    with sqlite3.connect(str(serve_db)) as con:
        text = con.execute(
            "SELECT text FROM nga_document WHERE node_id = ?", (DOC_NODE,)
        ).fetchone()[0]
        body = con.execute(
            "SELECT body FROM nga_body_fts WHERE node_id = ?", (DOC_NODE,)
        ).fetchone()[0]
        fetch = con.execute(
            "SELECT etag FROM nga_fetch_state WHERE node_id = ?", (DOC_NODE,)
        ).fetchone()[0]
    assert text == DOC_TEXT
    assert body == DOC_TEXT
    assert fetch == '"v2"'
    node = serve_store.get_node(DOC_NODE)
    assert node is not None
    assert node.content_hash == "index-doc-hash"
    page_after = serve_store.page_version("cms.hostel")
    assert page_after is not None and page_before is not None
    assert page_after.text == page_before.text


def test_promotion_refuses_a_different_graph(tmp_path):
    _serve_store, serve_db, _index_store, index_db = _build_pair(tmp_path)
    with sqlite3.connect(str(index_db)) as con:
        con.execute(
            "UPDATE nga_compile_run SET graph_hash = 'different-graph'"
        )
        con.commit()

    with pytest.raises(PromotionError, match="different graphs"):
        promote(serve_db, index_db, apply=True)

    assert _count(serve_db, "nga_document") == 0


def test_promotion_refuses_the_same_database(tmp_path):
    _store, db = build_store(tmp_path)

    with pytest.raises(PromotionError, match="same"):
        promote(db, db, apply=True)
