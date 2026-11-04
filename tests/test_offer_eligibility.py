"""Pure tests for the marks-based offer decision and its mappings."""

from app.crm import identity as ident
from app.offers.eligibility import assess_offer_eligibility


def row(
    status: str,
    *,
    dtype: str = "CLASS_12_MARKSHEET",
    active: bool = True,
    requirement: str = "",
    score: str = "75",
    threshold: str = "70",
    codes: str = "",
    doc_id: str = "doc-1",
) -> dict:
    return {
        "api_document_type": dtype,
        "validation_status": status,
        "requirement_status": requirement,
        "is_active": active,
        "extracted_score": score,
        "score_threshold": threshold,
        "reason_codes": codes,
        "document_id": doc_id,
    }


def test_bachelor_pass_issues_the_offer():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("PASSED", requirement="SATISFIED")],
    )
    assert decision.action == "issue"
    assert decision.evidence["score"] == "75"
    assert decision.evidence["threshold"] == "70"


def test_below_threshold_blocks_the_offer():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("FAILED", requirement="BELOW_THRESHOLD", score="62", threshold="70")],
    )
    assert decision.action == "block_not_eligible"
    assert "62" in decision.message and "70" in decision.message


def test_wrong_document_asks_for_the_correct_one():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("FAILED", codes="DOCUMENT_TYPE_MISMATCH;ACADEMIC_LEVEL_MISMATCH")],
    )
    assert decision.action == "block_wrong_document"


def test_review_required_blocks_the_offer():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("REVIEW_REQUIRED")],
    )
    assert decision.action == "block_review"


def test_missing_gate_document_blocks_the_offer():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [],
    )
    assert decision.action == "block_missing"


def test_only_the_gate_document_counts():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("PASSED", dtype="ID_PROOF")],
    )
    assert decision.action == "block_missing"


def test_master_uses_the_undergraduate_transcript():
    program = "MCA"
    only_class_12 = assess_offer_eligibility(
        {"program_interest": program}, program,
        [row("PASSED", dtype="CLASS_12_MARKSHEET")],
    )
    assert only_class_12.action == "block_missing"

    transcript = assess_offer_eligibility(
        {"program_interest": program}, program,
        [row("PASSED", dtype="UNDERGRADUATE_TRANSCRIPT", score="3.5", threshold="3.2")],
    )
    assert transcript.action == "issue"


def test_mba_uses_the_undergraduate_transcript():
    decision = assess_offer_eligibility(
        {"program_interest": "MBA"},
        "MBA",
        [row("PASSED", dtype="UNDERGRADUATE_TRANSCRIPT", score="3.5", threshold="3.2")],
    )
    assert decision.action == "issue"


def test_inactive_rows_are_ignored():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("PASSED", active=False)],
    )
    assert decision.action == "block_missing"

    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("FAILED", active=False), row("PASSED", active=True)],
    )
    assert decision.action == "issue"


def test_unclassified_program_blocks_unknown():
    decision = assess_offer_eligibility(
        {"program_interest": "Computer Science"},
        "Computer Science",
        [row("PASSED")],
    )
    assert decision.action == "block_unknown"


def test_validation_type_mapping():
    assert ident.validation_document_type("transcript") == "UNDERGRADUATE_TRANSCRIPT"
    assert ident.validation_document_type("marksheet") == "CLASS_12_MARKSHEET"
    assert ident.validation_document_type("id_proof") == "ID_PROOF"
    assert ident.validation_document_type("ielts") == "IELTS"
    assert ident.validation_document_type("test_score") is None
    assert ident.validation_document_type("resume") is None


def test_program_level_mapping():
    assert ident.program_level("B.Tech Computer Science") == "Bachelor's"
    assert ident.program_level("BBA") == "Bachelor's"
    assert ident.program_level("MCA") == "Master's"
    assert ident.program_level("MBA") == "Master's"
    assert ident.program_level("Computer Science") is None
