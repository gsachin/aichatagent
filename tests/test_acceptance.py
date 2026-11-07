"""WP10: the frozen acceptance set is well-formed and gates the text seam."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.agent.facts import FactStore
from scripts.run_acceptance import (
    AcceptanceError,
    _contains,
    _select_cases,
    classify_outcome,
    load_cases,
    run_suite,
    validate_cases,
)
from tests.nga_helpers import build_store

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval/acceptance/cases.jsonl"
HELDOUT = ROOT / "eval/acceptance/heldout.jsonl"
ADVERSARIAL = ROOT / "eval/acceptance/adversarial.jsonl"


def test_acceptance_files_are_frozen_and_well_formed():
    tuned = load_cases(CASES)
    heldout = load_cases(HELDOUT)
    adversarial = load_cases(ADVERSARIAL)
    validate_cases(tuned)
    validate_cases(heldout)
    validate_cases(adversarial)
    assert len(adversarial) == 10

    assert len(tuned) == 30
    assert len(heldout) == 20
    assert len({case["id"] for case in tuned}) == 30
    assert len({case["id"] for case in heldout}) == 20
    assert not ({case["id"] for case in tuned} & {case["id"] for case in heldout})
    assert all(case.get("heldout") is True for case in heldout)
    assert not any(case.get("heldout") for case in tuned)
    assert sum(1 for case in tuned if case.get("ci")) >= 6


def test_heldout_set_requires_explicit_release():
    tuned = load_cases(CASES)
    heldout = load_cases(HELDOUT)

    adversarial = load_cases(ADVERSARIAL)
    with pytest.raises(AcceptanceError):
        _select_cases(
            tuned,
            heldout,
            case_ids=(),
            ci_only=False,
            include_heldout=True,
        )
    with pytest.raises(AcceptanceError):
        _select_cases(
            tuned,
            heldout,
            adversarial=adversarial,
            case_ids=(),
            ci_only=False,
            include_heldout=False,
            include_adversarial=True,
        )
    selected = _select_cases(
        tuned,
        heldout,
        adversarial=adversarial,
        case_ids=(),
        ci_only=False,
        include_heldout=True,
        include_adversarial=True,
        release=True,
    )
    assert len(selected) == 60


def test_acceptance_matchers_are_strict_enough_for_critical_facts():
    assert _contains("The annual fee is INR 3,410.", "3410")
    assert _contains("BA History", "ba history")
    assert not _contains("The fee is INR 9,999.", "3410")
    assert not _contains("B.Sc Information Technology", "M.Sc Information Technology")

    assert classify_outcome(None) == "fallback"
    assert (
        classify_outcome(
            SimpleNamespace(answer_type="refusal", status="blocked")
        )
        == "refusal"
    )
    assert (
        classify_outcome(
            SimpleNamespace(answer_type="answer", status="verified")
        )
        == "answer"
    )


def test_deterministic_acceptance_subset_passes_on_fixture(tmp_path):
    """The CI gate: no network, no LLM, a pinned graph, all CI cases pass."""
    _store, db = build_store(tmp_path, pii_guard=False)
    previous = {
        name: getattr(settings, name)
        for name in (
            "NGA_CATALOGUE_ENABLED",
            "NGA_CATALOGUE_PATH",
            "NGA_CATALOGUE_INCLUDE_DRAFT",
            "NGA_CONVERSATION_STATE_ENABLED",
        )
    }
    for name, value in {
        "NGA_CATALOGUE_ENABLED": True,
        "NGA_CATALOGUE_PATH": str(
            (ROOT / "tests/fixtures/nga/programmes.json").resolve()
        ),
        "NGA_CATALOGUE_INCLUDE_DRAFT": True,
        "NGA_CONVERSATION_STATE_ENABLED": False,
    }.items():
        object.__setattr__(settings, name, value)
    try:
        cases = [
            case for case in load_cases(CASES) if case.get("ci")
        ]
        suite = run_suite(
            cases,
            db=db,
            llm_mode="none",
            runs=1,
            snapshot=False,
            clean_cache=True,
            offline=True,
            pii_guard=False,
            facts=FactStore(),
        )
    finally:
        for name, value in previous.items():
            object.__setattr__(settings, name, value)

    assert suite.summary["cases_failed"] == 0, json.dumps(
        suite.to_dict(), indent=2
    )
    assert suite.summary["turns_failed"] == 0
    assert suite.summary["cases"] >= 6
    state = suite.to_dict()["data_state"]
    assert state["graph_hash"]
    assert state["chunk_count"] == 0
    assert len(state["case_digest"]) == 64
