"""Pure helpers for the offline chunk-retrieval evaluation."""

from __future__ import annotations

import json

import pytest

from scripts.nga_embedding_eval import first_rank, load_queries, url_matches


def test_url_matches_decodes_percent_encoding_both_ways():
    assert url_matches(
        "https://dspmuranchi.ac.in/archive-notice/pdf/UG%20launch%20A4.pdf",
        ["UG launch A4.pdf"],
    )
    assert url_matches(
        "https://dspmuranchi.ac.in/archive-notice/pdf/UG%20launch%20A4.pdf",
        ["UG%20launch%20A4.pdf"],
    )
    assert not url_matches(
        "https://dspmuranchi.ac.in/archive-notice/pdf/other.pdf",
        ["UG%20launch%20A4.pdf"],
    )


def test_first_rank_returns_the_first_relevant_position():
    urls = [
        "https://dspmuranchi.ac.in/archive-notice/pdf/a.pdf",
        "https://dspmuranchi.ac.in/archive-notice/pdf/b.pdf",
        "https://dspmuranchi.ac.in/archive-notice/pdf/c.pdf",
    ]

    assert first_rank(urls, ["b.pdf"]) == 2
    assert first_rank(urls, ["missing.pdf"]) is None
    assert first_rank(urls, ["c.pdf", "b.pdf"]) == 2


def test_load_queries_validates_and_normalises(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"id": "R1", "query": "hostel", "expected_urls": "x"}),
                json.dumps({"query": "library", "expected_url": "y"}),
            ]
        ),
        encoding="utf-8",
    )

    rows = load_queries(path)

    assert rows[0]["expected_urls"] == ["x"]
    assert rows[1]["id"] == "R02"
    assert rows[1]["expected_urls"] == ["y"]


def test_load_queries_rejects_a_missing_label(tmp_path):
    path = tmp_path / "queries.jsonl"
    path.write_text(json.dumps({"query": "hostel"}), encoding="utf-8")

    with pytest.raises(SystemExit):
        load_queries(path)
