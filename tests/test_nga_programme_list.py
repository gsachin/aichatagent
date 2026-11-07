"""Programme-list answers from the catalogue, not deep search or hand-off."""

from __future__ import annotations

from pathlib import Path

from app.agent.models import Intent
from app.agent.text import build_text_answer
from tests.nga_helpers import build_store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nga" / "programmes.json"


def _settings(override_settings, **extra):
    values = {
        "NGA_CATALOGUE_ENABLED": True,
        "NGA_CATALOGUE_PATH": str(FIXTURE),
        "NGA_CATALOGUE_INCLUDE_DRAFT": True,
        "NGA_PG_PROGRAMME_LISTS_ANSWERABLE": True,
        "DEEP_SEARCH_ENABLED": False,
    }
    values.update(extra)
    override_settings(**values)


def _must_not_call(_messages):
    raise AssertionError("a catalogue list answer must not call the LLM")


def test_programme_list_answers_from_the_catalogue(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "what programmes are offered",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME_LIST
    assert result.status == "verified"
    assert result.answer_type == "answer"
    assert "BA History" in result.answer
    assert "M.Sc Information Technology" in result.answer
    assert "Undergraduate (UG)" in result.answer
    assert "Postgraduate (PG)" in result.answer


def test_a_single_programme_is_answered_from_the_catalogue(
    tmp_path, override_settings
):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "M.Sc. Information Technology",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME
    assert result.status == "verified"
    assert result.answer_type == "answer"
    assert "M.Sc Information Technology" in result.answer
    assert "Postgraduate (PG)" in result.answer
    assert "Official programme page" in result.answer
    assert "couldn’t verify" not in result.answer.lower()
    assert "could not verify" not in result.answer.lower()


def test_plural_american_spelling_is_a_list(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "which programs currently university",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME_LIST
    assert "BA History" in result.answer
    assert "M.Sc Information Technology" in result.answer


def test_pg_list_is_filtered_and_does_not_include_ug(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "list of postgraduate courses",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME_LIST
    assert result.plan.filters.get("level") == "PG"
    assert "M.Sc Information Technology" in result.answer
    assert "MA History" in result.answer
    assert "BA History" not in result.answer


def test_ug_list_is_filtered(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "UG programmes",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME_LIST
    assert result.plan.filters.get("level") == "UG"
    assert "BA History" in result.answer
    assert "M.Sc Information Technology" not in result.answer


def test_a_specific_programme_fee_is_not_a_list(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings)

    result = build_text_answer(
        "what is the fee for BA History",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The BA History annual fee is ₹3,410 (GEN).",
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.FEE
    assert "3,410" in result.answer


def test_missing_catalogue_falls_back_to_retrieval(tmp_path, override_settings):
    _store, db = build_store(tmp_path)
    _settings(override_settings, NGA_CATALOGUE_PATH=str(tmp_path / "missing.json"))

    result = build_text_answer(
        "what programmes are offered",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    # With no catalogue and no matching page, the path refuses rather than
    # inventing a list; the plan has fallen back to a normal search.
    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.PROGRAMME
    assert result.status == "no_evidence"
