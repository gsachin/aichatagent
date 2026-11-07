"""Level-aware programme mentions for grounding.

The lead detector returns the first UG fee alias. The verifier must not use
that universe: an answer that names the PG programme in the evidence must be
grounded, not rewritten into a UG programme.
"""

from __future__ import annotations

from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.models import Intent
from app.agent.planner import Planner
from app.agent.verifier import verify_answer
from app.programs import detect_program_mentions


def _mention(text: str):
    return detect_program_mentions(text)[0]


def test_msc_it_is_a_pg_mention_with_the_right_subject():
    mention = _mention("M.Sc. Information Technology")
    assert mention.level == "PG"
    assert mention.subject.lower() == "information technology"


def test_ma_history_is_a_pg_mention():
    mention = _mention("M.A. History")
    assert mention.level == "PG"
    assert mention.subject.lower() == "history"


def test_bsc_it_is_a_ug_mention():
    mention = _mention("B.Sc. Information Technology")
    assert mention.level == "UG"
    assert mention.subject.lower() == "information technology"


def test_master_of_science_phrase_skips_the_degree_field():
    mention = _mention("Master of Science in Information Technology")
    assert mention.level == "PG"
    assert mention.subject.lower() == "information technology"


def test_it_abbreviation_expands_to_information_technology():
    mention = _mention("M.Sc. IT")
    assert mention.level == "PG"
    assert mention.subject.lower() == "information technology"


def test_planner_uses_the_level_aware_programme_and_search_level():
    pg = Planner().plan("M.Sc. Information Technology")
    assert pg.filters["programme"].lower().startswith("m.sc")
    assert pg.steps[0].args["level"] == "PG"

    ug = Planner().plan("B.Sc Information Technology")
    assert ug.filters["programme"].lower().startswith("b.sc")
    assert ug.steps[0].args["level"] == "UG"


def test_generic_programme_words_are_not_programme_claims():
    assert detect_program_mentions("The university offers programmes.") == ()
    assert detect_program_mentions("History is a subject.") == ()
    assert detect_program_mentions("Information Technology") == ()


def test_prose_after_a_degree_is_not_a_programme_claim():
    # These phrases used to become critical programme claims and blocked
    # correct answers from the verifier.
    assert detect_program_mentions("undergraduate postgraduate programmes along") == ()
    assert detect_program_mentions("B.Sc. Chemistry Semester II mid-semester")[0].subject == "Chemistry"
    assert detect_program_mentions("postgraduate 2022 2024") == ()
    assert detect_program_mentions("Ph.D. dated 14 October 2026") == ()
    assert detect_program_mentions("B.A. admission") == ()


def _bundle(*, title: str, text: str) -> EvidenceBundle:
    return EvidenceBundle(
        query="programme",
        intent=Intent.PAGE_TOPIC,
        items=[
            EvidenceItem(
                evidence_id="ev-1",
                node_id="dept-it",
                title=title,
                url="https://dspmuranchi.ac.in/departments/m.sc.-information-technology",
                authority="official_page",
                verified_at="2026-10-26T00:00:00Z",
                snippet=text,
                text=text,
            )
        ],
    )


def test_a_pg_mention_in_the_evidence_is_verified():
    bundle = _bundle(
        title="M Sc Information Technology",
        text="Department of MCA AND M. SC. (INFORMATION TECHNOLOGY).",
    )
    result = verify_answer(
        "The university offers M.Sc. Information Technology [ev-1].",
        bundle,
    )
    assert result.status == "verified"


def test_a_ug_claim_on_pg_evidence_is_blocked_by_level():
    bundle = _bundle(
        title="M Sc Information Technology",
        text="Department of MCA AND M. SC. (INFORMATION TECHNOLOGY).",
    )
    result = verify_answer(
        "The university offers B.Sc Information Technology [ev-1].",
        bundle,
    )
    assert result.status == "blocked"
    assert any(
        failure["reason"] == "programme_level_mismatch"
        for failure in result.failures
    )


def test_a_pg_claim_on_ug_evidence_is_blocked_by_level():
    bundle = _bundle(
        title="B Sc Information Technology",
        text="UG Information Technology syllabus and course documents.",
    )
    result = verify_answer(
        "The university offers M.Sc Information Technology [ev-1].",
        bundle,
    )
    assert result.status == "blocked"
    assert any(
        failure["reason"] == "programme_level_mismatch"
        for failure in result.failures
    )


def test_a_wrong_pg_subject_is_blocked():
    bundle = _bundle(
        title="M Sc Information Technology",
        text="Department of MCA AND M. SC. (INFORMATION TECHNOLOGY).",
    )
    result = verify_answer(
        "The university offers M.Sc. Physics [ev-1].",
        bundle,
    )
    assert result.status == "blocked"
    assert any(
        failure["reason"] == "programme_not_in_evidence"
        for failure in result.failures
    )
