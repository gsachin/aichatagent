"""WP11: the configured rollback switches change behaviour safely."""

from __future__ import annotations

from pathlib import Path

from app.agent.text import build_text_answer
from app.knowledge.catalogue import Catalogue
from tests.nga_helpers import build_store, seed_page

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nga" / "programmes.json"


def test_conversation_state_off_drops_the_followup_topic(
    tmp_path, override_settings
):
    store, db = build_store(tmp_path)
    seed_page(store)
    override_settings(
        NGA_CONVERSATION_STATE_ENABLED=False,
        DEEP_SEARCH_ENABLED=False,
    )

    first = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        conversation_id="rollback-conv",
        llm=lambda messages: "The hostel has a warden office.",
    )
    assert first is not None
    second = build_text_answer(
        "what about its fee?",
        graph_path=str(db),
        pii_guard=False,
        conversation_id="rollback-conv",
        llm=lambda messages: "I couldn't verify the fee.",
    )

    assert second is not None
    assert second.plan is not None
    assert second.plan.filters.get("topic_node_id") is None
    assert not any(
        step.tool == "site.open"
        and step.args.get("node_id") == "cms.hostel"
        for step in second.plan.steps
    )


def test_catalogue_off_does_not_use_the_deterministic_programme_answer(
    tmp_path, override_settings
):
    _store, db = build_store(tmp_path)
    override_settings(
        NGA_CATALOGUE_ENABLED=False,
        DEEP_SEARCH_ENABLED=False,
    )

    result = build_text_answer(
        "M.Sc. Information Technology",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "M.Sc Information Technology is offered.",
    )

    assert result is None or "Official programme page" not in result.answer


def test_pg_list_switch_off_skips_the_pg_catalogue_answer(
    tmp_path, override_settings
):
    _store, db = build_store(tmp_path)
    override_settings(
        NGA_CATALOGUE_ENABLED=True,
        NGA_CATALOGUE_PATH=str(FIXTURE),
        NGA_CATALOGUE_INCLUDE_DRAFT=True,
        NGA_PG_PROGRAMME_LISTS_ANSWERABLE=False,
        DEEP_SEARCH_ENABLED=False,
    )

    result = build_text_answer(
        "list of postgraduate courses",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "Please contact the university.",
    )

    assert result is None or "Postgraduate (PG) —" not in result.answer


def test_draft_catalogue_rows_can_be_hidden():
    catalogue = Catalogue.load(FIXTURE)

    assert catalogue.list_rows(include_draft=False) == ()
    assert catalogue.list_rows(include_draft=True)


def test_navigation_mode_legacy_skips_the_browse_answer(
    tmp_path, override_settings
):
    store, db = build_store(tmp_path)
    seed_page(store)
    override_settings(
        NGA_NAVIGATION_MODE="legacy",
        DEEP_SEARCH_ENABLED=False,
    )

    result = build_text_answer(
        "what is under student life",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "I couldn't verify that.",
    )

    # The browse step is skipped. The ordinary path finds no groundable body
    # for the shell, so the turn becomes a labelled refusal (or hands back to
    # the static path) instead of emitting a navigation answer.
    assert result is None or result.answer_type == "refusal"
    if result is not None:
        assert "Student Life" not in result.answer
