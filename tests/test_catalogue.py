"""Programme catalogue schema, detection and list-answer tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.knowledge.catalogue import Catalogue

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nga" / "programmes.json"


def test_catalogue_loads_and_counts_levels():
    catalogue = Catalogue.load(FIXTURE)

    assert catalogue.errors == ()
    assert len(catalogue.rows) == 5
    assert catalogue.counts() == {"UG": 2, "PG": 2, "Diploma": 1}


def test_detection_is_level_aware():
    catalogue = Catalogue.load(FIXTURE)

    ug_it = catalogue.detect("B.Sc Information Technology")
    pg_it = catalogue.detect("M.Sc. Information Technology")
    ug_history = catalogue.detect("BA History")
    pg_history = catalogue.detect("M.A. History")
    diploma = catalogue.detect("Diploma in Amanat Survey")

    assert ug_it is not None and ug_it.programme.level == "UG"
    assert ug_it.programme.name == "B.Sc Information Technology"
    assert pg_it is not None and pg_it.programme.level == "PG"
    assert pg_it.programme.name == "M.Sc Information Technology"
    assert ug_history is not None and ug_history.programme.level == "UG"
    assert pg_history is not None and pg_history.programme.level == "PG"
    assert diploma is not None and diploma.programme.level == "Diploma"


def test_a_bare_ambiguous_subject_or_degree_is_not_matched():
    catalogue = Catalogue.load(FIXTURE)

    # "History" is both UG and PG; the detector must not guess a level.
    assert catalogue.detect("History") is None
    # A bare degree is a level, not a programme.
    assert catalogue.detect("M.Sc") is None


def test_degree_abbreviation_uses_the_right_row():
    catalogue = Catalogue.load(FIXTURE)

    match = catalogue.detect("M.Sc IT")
    assert match is not None
    assert match.programme.name == "M.Sc Information Technology"


def test_list_answer_filters_by_level():
    catalogue = Catalogue.load(FIXTURE)

    all_text, _citations, rows = catalogue.list_answer()
    pg_text, _pg_citations, pg_rows = catalogue.list_answer("PG")
    ug_text, _ug_citations, ug_rows = catalogue.list_answer("UG")

    assert len(rows) == 5
    assert "BA History" in all_text
    assert "M.Sc Information Technology" in all_text
    assert "Undergraduate (UG)" in all_text
    assert "Postgraduate (PG)" in all_text

    assert all(row.level == "PG" for row in pg_rows)
    assert "M.Sc Information Technology" in pg_text
    assert "BA History" not in pg_text

    assert all(row.level == "UG" for row in ug_rows)
    assert "BA History" in ug_text
    assert "M.Sc Information Technology" not in ug_text


def test_list_answer_marks_draft_rows_and_cites_sources():
    catalogue = Catalogue.load(FIXTURE)

    text, citations, _rows = catalogue.list_answer()

    assert "marked draft" in text
    assert citations
    assert any("dspmuranchi.ac.in" in item["url"] for item in citations)
    assert any(item["title"] == "Academic Programmes" for item in citations)


def test_missing_catalogue_is_empty_not_fatal(tmp_path):
    catalogue = Catalogue.load(tmp_path / "missing.json")

    assert catalogue.rows == ()
    assert catalogue.errors
    assert catalogue.list_answer() == ("", (), ())


def test_duplicate_ids_are_reported(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["programmes"].append(dict(payload["programmes"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload))

    catalogue = Catalogue.load(path)

    assert any(error.startswith("duplicate_id:") for error in catalogue.errors)
    assert len(catalogue.rows) == 5
