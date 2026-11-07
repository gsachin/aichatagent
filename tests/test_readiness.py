"""Readiness report tests."""

from __future__ import annotations

import json

from pathlib import Path

from app.sitegraph.readiness import readiness_report
from tests.nga_helpers import build_store, seed_page

CATALOGUE_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "nga" / "programmes.json"
)


def test_missing_graph_is_degraded(tmp_path):
    report = readiness_report(tmp_path / "missing.sqlite3")
    assert report["status"] == "degraded"
    assert report["graph_exists"] is False
    assert "graph_missing" in report["warnings"]


def test_split_serve_and_index_paths_are_reported(
    tmp_path, override_settings
):
    _serve_store, serve_db = build_store(tmp_path / "serve")
    _index_store, index_db = build_store(tmp_path / "index")
    override_settings(
        NGA_SERVE_DB_PATH=str(serve_db),
        NGA_INDEX_DB_PATH=str(index_db),
    )

    report = readiness_report()

    assert report["db_paths_split"] is True
    assert report["serve_db"] == str(serve_db)
    assert report["index_db"] == str(index_db)
    assert report["index"]["exists"] is True
    assert report["index"]["lock"] == "ok"
    assert report["index"]["documents"] == report["documents"]


def test_an_explicit_graph_path_uses_one_database_for_both_stores(tmp_path):
    _store, db = build_store(tmp_path)

    report = readiness_report(db)

    assert report["db_paths_split"] is False
    assert report["serve_db"] == str(db)
    assert report["index_db"] == str(db)
    assert "serve_and_index_db_shared" in report["info"]


def test_report_includes_effective_mode_and_counts(tmp_path):
    store, db = build_store(tmp_path)
    seed_page(store)
    report = readiness_report(db)

    assert report["graph_exists"] is True
    assert report["graph"]["nodes"] >= 1
    assert report["graph"]["pages_with_text"] >= 1
    assert "mode" in report
    assert "nga_mode" in report["mode"]
    assert "adequacy_enabled" in report["mode"]
    # The fixture is small, so the chunk index is legitimately empty; that is
    # an informational state, not a degraded one.
    assert "chunk_index_empty" in report["info"]
    assert report["status"] in ("ok", "degraded")


def test_enabled_but_non_agentic_mode_is_a_warning(
    tmp_path, override_settings
):
    _store, db = build_store(tmp_path)
    override_settings(NGA_ENABLED=True, NGA_MODE="shadow")
    report = readiness_report(db)
    assert "nga_enabled_but_mode_shadow" in report["warnings"]
    assert report["status"] == "degraded"


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        for item in value.values():
            keys |= _keys(item)
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys |= _keys(item)
        return keys
    return set()


def test_report_includes_catalogue_state(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    override_settings(
        NGA_CATALOGUE_ENABLED=True,
        NGA_CATALOGUE_PATH=str(CATALOGUE_FIXTURE),
    )
    report = readiness_report(db)

    assert report["catalogue"]["rows"] == 5
    assert report["catalogue"]["levels"] == {"UG": 2, "PG": 2, "Diploma": 1}
    assert report["catalogue"]["errors"] == []


def test_missing_catalogue_is_reported(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    override_settings(
        NGA_CATALOGUE_ENABLED=True,
        NGA_CATALOGUE_PATH=str(tmp_path / "missing.json"),
    )
    report = readiness_report(db)

    assert "catalogue_missing" in report["warnings"]
    assert report["status"] == "degraded"


def test_report_has_no_credential_keys(tmp_path):
    _store, db = build_store(tmp_path)
    keys = _keys(readiness_report(db))
    assert not (
        {"api_key", "authorization", "password", "token", "secret"} & keys
    )
    assert json.dumps(readiness_report(db))  # JSON-serializable
