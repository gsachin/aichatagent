"""The rebuildable FTS5 notice store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.notice_store import (
    SCHEMA_VERSION,
    NoticeStoreError,
    build_store,
    search_notices,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def catalogue(tmp_path: Path) -> Path:
    path = tmp_path / "notices.jsonl"
    _write_jsonl(
        path,
        [
            {
                "id": "n1",
                "date": "2026-10-14",
                "date_text": "14-Oct-2026",
                "title": "Fill Application Form for Ph.D. Programme",
                "category": "Examination",
                "url": "https://example.test/phd.pdf",
                "pii": False,
            },
            {
                "id": "n2",
                "date": "2026-09-21",
                "title": "Holiday Notice",
                "category": "Holiday",
                "url": "https://example.test/holiday.pdf",
                "pii": False,
            },
            {
                "id": "n3",
                "date": "2026-09-20",
                "title": "Merit List of Selected Candidates",
                "category": "Miscellaneous",
                "url": "https://example.test/merit.pdf",
                "pii": False,
            },
            {
                "id": "n4",
                "date": "2026-09-19",
                "title": "Private notice",
                "category": "Miscellaneous",
                "url": "https://example.test/private.pdf",
                "pii": True,
            },
        ],
    )
    return path


def test_builds_and_searches_the_fts5_store(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"

    stats = build_store(catalogue, db)

    assert stats.built is True
    assert stats.rows == 2
    assert stats.pii_excluded == 2
    assert db.exists()
    assert stats.schema_version == SCHEMA_VERSION

    phd = search_notices("PhD", db_path=db)
    assert phd is not None
    assert phd[0]["id"] == "n1"
    assert phd[0]["title"].startswith("Fill Application Form")

    exam = search_notices("exam", db_path=db)
    assert exam is not None
    assert [row["id"] for row in exam] == ["n1"]

    holiday = search_notices("holiday", db_path=db)
    assert holiday is not None
    assert [row["id"] for row in holiday] == ["n2"]


def test_pii_and_selection_lists_are_not_searchable(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"
    build_store(catalogue, db)

    for query in ("merit list", "selected candidates", "private"):
        assert search_notices(query, db_path=db) == []


def test_a_missing_or_unreadable_store_returns_none(tmp_path):
    assert search_notices("PhD", db_path=tmp_path / "missing.sqlite3") is None

    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a sqlite database")
    assert search_notices("PhD", db_path=broken) is None


def test_a_stale_schema_returns_none(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"
    build_store(catalogue, db)
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA user_version = 999")

    assert search_notices("PhD", db_path=db) is None


def test_empty_query_returns_an_empty_list(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"
    build_store(catalogue, db)

    assert search_notices("the notice about", db_path=db) == []


def test_body_text_is_indexed_when_present(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"
    body_dir = tmp_path / "bodies"
    body_dir.mkdir()
    (body_dir / "n2.txt").write_text(
        "The campus will remain closed on Monday.", encoding="utf-8"
    )

    stats = build_store(catalogue, db, body_dir=body_dir)

    assert stats.body_rows == 1
    rows = search_notices("campus", db_path=db)
    assert rows is not None
    assert [row["id"] for row in rows] == ["n2"]


def test_rebuild_is_atomic_and_idempotent(catalogue, tmp_path):
    db = tmp_path / "notices.sqlite3"

    first = build_store(catalogue, db)
    second = build_store(catalogue, db)

    assert first.rows == second.rows
    assert not (tmp_path / "notices.sqlite3.tmp").exists()
    assert search_notices("holiday", db_path=db)[0]["id"] == "n2"


def test_invalid_jsonl_fails_loudly(tmp_path):
    path = tmp_path / "notices.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(NoticeStoreError):
        build_store(path, tmp_path / "notices.sqlite3")


def test_duplicate_ids_fail_loudly(catalogue, tmp_path):
    duplicate = tmp_path / "duplicate.jsonl"
    rows = [json.loads(line) for line in catalogue.read_text().splitlines()]
    _write_jsonl(duplicate, [rows[0], rows[0]])

    with pytest.raises(NoticeStoreError):
        build_store(duplicate, tmp_path / "notices.sqlite3")
