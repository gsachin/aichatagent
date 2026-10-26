"""Phase 5: claim-level answer verification."""

from __future__ import annotations

from app.agent.evidence import Citation, EvidenceBundle, EvidenceItem
from app.agent.models import Intent
from app.agent.verifier import _extract_claims, verify_answer

FACT_TEXT = (
    "Bachelor of Arts (BA) in History: annual fee ₹3,410 (GEN); "
    "₹3,185 (ST/SC/BC I & II); semester course fee ₹0 (GEN); "
    "valid 2026-07-01 to 2027-06-30; status draft."
)
PAGE_TEXT = (
    "The Grievance Redressal Cell helps students. "
    "Last date for the form is 25.10.2026. "
    "Contact: registrar@dspmuranchi.ac.in or +91 930 455 261."
)


def _bundle(with_fact: bool = True) -> EvidenceBundle:
    items: list[EvidenceItem] = []
    facts: list[EvidenceItem] = []
    if with_fact:
        fact = EvidenceItem(
            evidence_id="fact-1",
            node_id="fee.ug.history.2026.gen",
            title="Bachelor of Arts (BA) in History",
            url="https://dspmuranchi.ac.in/uploads/fee.pdf",
            authority="verified_fact",
            verified_at="2026-10-24T00:00:00Z",
            snippet="BA History fee",
            text=FACT_TEXT,
            fact_id="fee.ug.history.2026.gen",
        )
        items.append(fact)
        facts.append(fact)
    page = EvidenceItem(
        evidence_id="ev-1",
        node_id="student-life.grievance-redressal-cell",
        title="Grievance Redressal Cell",
        url="https://dspmuranchi.ac.in/student-life/grievance-redressal-cell",
        authority="official_page",
        verified_at="2026-10-25T00:00:00Z",
        snippet="Grievance page",
        text=PAGE_TEXT,
    )
    items.append(page)
    return EvidenceBundle(
        query="test",
        intent=Intent.FEE,
        items=items,
        verified_facts=facts,
        citations=[
            Citation(
                node_id=item.node_id,
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                verified_at=item.verified_at,
                authority=item.authority,
                fact_id=item.fact_id,
            )
            for item in items
        ],
    )


def test_correct_fee_and_programme_are_verified():
    result = verify_answer(
        "The BA History annual fee is ₹3,410 (GEN), valid 2026-07-01 to 2027-06-30.",
        _bundle(),
    )

    assert result.status == "verified"
    assert result.verified is True
    assert result.answer
    assert {claim.kind for claim in result.claims} >= {"amount", "date", "programme"}
    assert result.citations


def test_wrong_fee_is_blocked():
    result = verify_answer(
        "The BA History annual fee is ₹9,999 (GEN).", _bundle()
    )

    assert result.status == "blocked"
    assert result.answer == ""
    assert any(
        failure["reason"] == "fee_amount_not_in_verified_fact"
        for failure in result.failures
    )


def test_invented_date_is_blocked():
    result = verify_answer(
        "The last date is 15 August 2030.", _bundle()
    )

    assert result.status == "blocked"
    assert any(
        failure["reason"] == "date_not_in_evidence"
        for failure in result.failures
    )


def test_page_date_is_accepted_when_it_is_in_the_page():
    result = verify_answer(
        "The last date for the form is 25 October 2026.", _bundle()
    )

    assert result.status == "verified"
    assert any(claim.kind == "date" for claim in result.claims)


def test_wrong_url_removes_the_sentence_and_rewrites():
    result = verify_answer(
        "See https://evil.example/details for the form. "
        "The BA History annual fee is ₹3,410 (GEN).",
        _bundle(),
    )

    assert result.status == "rewritten"
    assert result.verified is True
    assert "evil.example" not in result.answer
    assert "₹3,410" in result.answer
    assert result.failures[0]["reason"] == "url_not_in_evidence"


def test_correct_url_is_accepted():
    result = verify_answer(
        "Source: https://dspmuranchi.ac.in/student-life/grievance-redressal-cell",
        _bundle(),
    )

    assert result.status == "verified"


def test_contact_details_are_checked():
    good = verify_answer(
        "Contact registrar@dspmuranchi.ac.in or +91 930 455 261.", _bundle()
    )
    bad = verify_answer(
        "Contact fake@example.org or +91 999 999 9999.", _bundle()
    )

    assert good.status == "verified"
    assert bad.status == "blocked"
    assert {failure["kind"] for failure in bad.failures} >= {"email", "phone"}


def test_programme_not_in_evidence_is_blocked():
    result = verify_answer(
        "The B.Sc Physics annual fee is ₹3,410 (GEN).", _bundle()
    )

    assert result.status == "blocked"
    assert any(
        failure["reason"] == "programme_not_in_evidence"
        for failure in result.failures
    )


def test_fee_amount_without_verified_fact_is_blocked():
    result = verify_answer(
        "The BA History annual fee is ₹3,410 (GEN).", _bundle(with_fact=False)
    )

    assert result.status == "blocked"
    assert any(
        failure["reason"] == "fee_amount_not_in_verified_fact"
        for failure in result.failures
    )


def test_no_evidence_and_empty_answer_are_blocked():
    empty = verify_answer("Any answer", EvidenceBundle("q", Intent.UNKNOWN))
    blank = verify_answer("", _bundle())

    assert empty.status == "blocked"
    assert "no_evidence" in empty.notes
    assert blank.status == "blocked"
    assert "empty_answer" in blank.notes


def test_claim_extraction_covers_the_supported_kinds():
    claims = _extract_claims(
        "₹3,410 on 25.10.2026 at https://dspmuranchi.ac.in/x "
        "or registrar@dspmuranchi.ac.in, +91 930 455 261. BA History."
    )
    kinds = {claim.kind for claim in claims}

    assert kinds >= {"amount", "date", "url", "email", "phone", "programme"}
