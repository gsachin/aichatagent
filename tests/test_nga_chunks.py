"""Chunk index, chunk BM25 retrieval and embedding plumbing."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

from app.sitegraph.chunker import chunks_for_document, chunks_for_page
from app.sitegraph.documents import DocumentStatus
from app.sitegraph.hybrid import search_chunks
from app.sitegraph.indexer import (
    index_all_chunks,
    index_document_chunks,
    index_page_chunks,
    index_version_for,
)
from app.sitegraph.search import node_matches_level
from app.sitegraph.tools import SiteToolServer
from tests.nga_helpers import HOSTEL, build_store, seed_page


def _current_chunk_version(store) -> str:
    run = store.latest_run() or {}
    return index_version_for(str(run.get("graph_hash") or ""))


def _page_row(text: str, *, heading: str = "Hostel") -> dict:
    return {
        "node_id": HOSTEL,
        "title": "Hostel",
        "url": "https://dspmuranchi.ac.in/cms/hostel",
        "section": "Student Life",
        "authority": "official_page",
        "pii_class": "public",
        "language": "en",
        "sections": json.dumps([{"heading": heading, "text": text}]),
        "text": text,
    }


def test_page_chunks_split_long_text_and_keep_the_heading():
    text = "The warden office is open. " * 200

    chunks = chunks_for_page(_page_row(text), index_version="v1")

    assert len(chunks) > 1
    assert all(chunk.heading == "Hostel" for chunk in chunks)
    assert all(chunk.source_kind == "html" for chunk in chunks)
    assert all(chunk.text for chunk in chunks)


def test_repeated_document_text_produces_unique_chunk_ids():
    row = {
        "node_id": "doc.repeated",
        "title": "Repeated",
        "url": "https://dspmuranchi.ac.in/repeated.pdf",
        "section": "Examination",
        "authority": "official_document",
        "pii_class": "public",
        "language": "en",
        "extraction_status": "done",
        "text": "0123456789" * 500,
        "tables": "[]",
    }

    chunks = chunks_for_document(
        row, index_version="v1", max_chars=1200, overlap=180
    )

    assert len(chunks) > 1
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_non_canonical_duplicate_page_gets_no_chunks(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store, text="Sports facilities and a gymnasium.")
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE nga_node SET canonical_id = ? WHERE node_id = ?",
            ("cms.sports", HOSTEL),
        )
        con.commit()

    result = index_all_chunks(
        store,
        index_version=_current_chunk_version(store),
        created_at="2026-11-01T00:00:00Z",
    )

    assert result["pages"] == 0
    with sqlite3.connect(db) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM nga_chunk WHERE node_id = ?", (HOSTEL,)
        ).fetchone()[0]
    assert count == 0


def test_a_document_that_stops_being_done_loses_its_chunks(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    node_id = "uploads.news.fee-pdf"
    store.record_document(
        node_id,
        url="https://dspmuranchi.ac.in/uploads/news/fee.pdf",
        mime="application/pdf",
        pages=1,
        status=DocumentStatus.DONE,
        text="Scholarship deadline is 15 June 2026.",
        tables=(),
        ocr=False,
        ocr_lang="",
        ocr_quality="",
        needs_review=False,
        pii_flagged=False,
        content_hash="hash-1",
        size_bytes=30,
        fetched_at="2026-11-01T00:00:00Z",
        verified_at="2026-11-01T00:00:00Z",
    )
    index_document_chunks(
        store,
        store.document_rows()[0],
        index_version="v1",
        created_at="2026-11-01T00:00:00Z",
    )
    assert store.chunk_count() > 0

    store.record_document(
        node_id,
        url="https://dspmuranchi.ac.in/uploads/news/fee.pdf",
        mime="application/pdf",
        pages=0,
        status=DocumentStatus.EMPTY,
        text="",
        tables=(),
        ocr=False,
        ocr_lang="",
        ocr_quality="",
        needs_review=False,
        pii_flagged=False,
        content_hash="hash-2",
        size_bytes=0,
        fetched_at="2026-11-02T00:00:00Z",
        verified_at=None,
    )

    assert store.chunk_count() == 0


def test_full_chunk_rebuild_prunes_orphan_nodes(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    index_page_chunks(
        store,
        _page_row("The hostel warden office is open."),
        index_version="v1",
        created_at="2026-10-31T12:00:00Z",
    )
    assert store.chunk_count() > 0

    assert store.prune_chunks(set()) > 0
    assert store.chunk_count() == 0


def test_unreviewed_and_pii_documents_are_not_chunked():
    base = {
        "node_id": "doc.sensitive",
        "title": "OCR Document",
        "url": "https://dspmuranchi.ac.in/sensitive.pdf",
        "section": "Examination",
        "authority": "official_document",
        "pii_class": "public",
        "language": "en",
        "extraction_status": "done",
        "text": "Unreviewed OCR text.",
        "tables": "[]",
    }

    assert chunks_for_document(
        {**base, "needs_review": 1}, index_version="v1"
    ) == []
    assert chunks_for_document(
        {**base, "pii_flagged": 1}, index_version="v1"
    ) == []


def test_document_table_chunks_keep_the_page_number():
    row = {
        "node_id": "doc.schedule",
        "title": "Examination Schedule",
        "url": "https://dspmuranchi.ac.in/schedule.pdf",
        "section": "Examination",
        "authority": "official_document",
        "pii_class": "public",
        "language": "en",
        "extraction_status": "done",
        "text": "Examination schedule details.",
        "tables": json.dumps(
            [[[["Exam", "Date"], ["LL.M. Semester III", "14 Oct 2026"]]]]
        ),
    }

    chunks = chunks_for_document(row, index_version="v1")

    table_chunks = [chunk for chunk in chunks if chunk.source_kind == "table"]
    assert table_chunks
    assert table_chunks[0].page == 1
    assert "LL.M. Semester III | 14 Oct 2026" in table_chunks[0].text


def test_chunk_index_is_used_by_site_search(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    row = _page_row("Boys and girls hostels with a warden office.")
    index_page_chunks(
        store,
        row,
        index_version=_current_chunk_version(store),
        created_at="2026-10-31T12:00:00Z",
    )

    assert store.chunk_count() == 1
    hits = search_chunks(store, "warden", pii_guard=False)
    assert hits and hits[0].node.node_id == HOSTEL
    assert "warden" in hits[0].snippet

    server = SiteToolServer(db, pii_guard=False)
    result = server.call("site.search", {"query": "warden"})
    item = result["data"]["results"][0]
    assert item["node_id"] == HOSTEL
    assert item["chunk_id"]
    assert "warden" in item["snippet"]
    # Fusion changes ordering, not the node relevance score.
    assert item["score"] > 1.0


def test_chunk_retrieval_can_be_rolled_back_without_deleting_the_index(
    tmp_path, override_settings
):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store, text="The hostel warden office is open.")
    index_page_chunks(
        store,
        _page_row("The hostel warden office is open."),
        index_version=_current_chunk_version(store),
        created_at="2026-10-31T12:00:00Z",
    )
    assert store.chunk_count() == 1
    override_settings(NGA_CHUNK_RETRIEVAL_ENABLED=False)

    result = SiteToolServer(db, pii_guard=False).call(
        "site.search", {"query": "warden"}
    )

    assert result["data"]["results"]
    assert all(item.get("chunk_id") is None for item in result["data"]["results"])
    assert store.chunk_count() == 1


def test_chunk_only_hits_still_obey_the_pii_title_policy(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store, text="The hostel warden office is open.")
    run = store.latest_run() or {}
    nodes = [
        replace(node, title="Merit List of Selected Candidates")
        if node.node_id == HOSTEL else node
        for node in store.load_nodes()
    ]
    store.replace_graph(
        nodes,
        store.load_edges(),
        coverage=run.get("coverage") or {},
        graph_hash=str(run.get("graph_hash") or "fixture"),
        generated_at=str(run.get("generated_at") or "2026-10-31T00:00:00Z"),
        source_mode=str(run.get("source_mode") or "fixture"),
    )
    index_page_chunks(
        store,
        _page_row("The hostel warden office is open."),
        index_version=_current_chunk_version(store),
        created_at="2026-10-31T12:00:00Z",
    )

    result = SiteToolServer(db, pii_guard=True).call(
        "site.search", {"query": "warden"}
    )

    assert result["status"] == "ok"
    assert all(
        item["node_id"] != HOSTEL
        for item in result["data"]["results"]
    )


def test_level_filter_rejects_opposite_degree_titles(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    node = store.load_nodes()[0]
    pg = replace(node, title="M Sc Information Technology")
    ug = replace(node, title="B Sc Information Technology")

    assert node_matches_level(pg, "PG")
    assert not node_matches_level(pg, "UG")
    assert node_matches_level(ug, "UG")
    assert not node_matches_level(ug, "PG")
    assert node_matches_level(node, "PG")  # unknown titles are not excluded


def test_a_chunk_only_node_can_enter_a_limit_three_result(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store, text="The hostel warden office is open.")
    run = store.latest_run() or {}
    title_nodes = {
        "home": "Scholarship Funding A",
        "cms.about-the-university": "Scholarship Funding B",
        "cms.history-legacy": "Scholarship Funding C",
    }
    nodes = [
        replace(node, title=title_nodes[node.node_id])
        if node.node_id in title_nodes else node
        for node in store.load_nodes()
    ]
    store.replace_graph(
        nodes,
        store.load_edges(),
        coverage=run.get("coverage") or {},
        graph_hash=str(run.get("graph_hash") or "fixture"),
        generated_at=str(run.get("generated_at") or "2026-10-31T00:00:00Z"),
        source_mode=str(run.get("source_mode") or "fixture"),
    )
    index_page_chunks(
        store,
        _page_row("Scholarship applications are open at the warden office."),
        index_version=_current_chunk_version(store),
        created_at="2026-10-31T12:00:00Z",
    )

    result = SiteToolServer(db, pii_guard=False).call(
        "site.search", {"query": "scholarship", "limit": 3}
    )
    node_ids = [item["node_id"] for item in result["data"]["results"]]

    assert "cms.hostel" in node_ids
    hostel = next(
        item for item in result["data"]["results"]
        if item["node_id"] == "cms.hostel"
    )
    assert hostel["chunk_id"]
    assert "Scholarship" in hostel["snippet"]


def test_chunk_embedding_search_round_trip(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    index_page_chunks(
        store,
        _page_row("Boys and girls hostels with a warden office."),
        index_version="v1",
        created_at="2026-10-31T12:00:00Z",
    )
    chunk = store.search_chunk_fts('"warden"', pii_guard=False)[0][0]

    store.put_chunk_embeddings(
        [
            {
                "chunk_id": chunk["chunk_id"],
                "model": "test-model",
                "vector": [1.0, 0.0, 0.0],
                "content_hash": chunk["content_hash"],
                "created_at": "2026-10-31T12:00:00Z",
            }
        ]
    )
    hits = store.search_chunk_embeddings(
        [1.0, 0.0, 0.0], model="test-model", pii_guard=False
    )

    assert hits
    assert hits[0][0]["chunk_id"] == chunk["chunk_id"]
    assert hits[0][1] == 1.0


class FakeEmbedder:
    model = "test-model"

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_hybrid_search_merges_dense_and_bm25(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    index_page_chunks(
        store,
        _page_row("Boys and girls hostels with a warden office."),
        index_version="v1",
        created_at="2026-10-31T12:00:00Z",
    )
    chunk = store.search_chunk_fts('"warden"', pii_guard=False)[0][0]
    store.put_chunk_embeddings(
        [
            {
                "chunk_id": chunk["chunk_id"],
                "model": "test-model",
                "vector": [1.0, 0.0, 0.0],
                "content_hash": chunk["content_hash"],
                "created_at": "2026-10-31T12:00:00Z",
            }
        ]
    )

    hits = search_chunks(
        store, "warden", pii_guard=False, embedder=FakeEmbedder()
    )

    assert hits
    assert hits[0].bm25_rank is not None
    assert hits[0].dense_rank is not None
    assert hits[0].node.node_id == HOSTEL
