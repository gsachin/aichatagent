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


def test_an_inactive_wrong_document_is_reported_as_wrong():
    """The API marks a wrong-level upload inactive; still name the problem."""
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("FAILED", active=False, codes="DOCUMENT_TYPE_MISMATCH,ACADEMIC_LEVEL_MISMATCH")],
    )
    assert decision.action == "block_wrong_document"


def test_an_inactive_review_is_reported_as_review():
    decision = assess_offer_eligibility(
        {"program_interest": "B.Tech Computer Science"},
        "B.Tech Computer Science",
        [row("REVIEW_REQUIRED", active=False)],
    )
    assert decision.action == "block_review"


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
    assert ident.validation_document_type("marksheet_12") == "CLASS_12_MARKSHEET"
    assert ident.validation_document_type("marksheet_10") is None
    assert ident.validation_document_type("id_proof") == "ID_PROOF"
    assert ident.validation_document_type("ielts") == "IELTS"
    assert ident.validation_document_type("sat") == "SAT"
    assert ident.validation_document_type("gre") == "GRE"
    assert ident.validation_document_type("gmat") == "GMAT"
    assert ident.validation_document_type("toefl") == "TOEFL"
    assert ident.validation_document_type("test_score") is None
    assert ident.validation_document_type("resume") is None


def test_program_level_mapping():
    assert ident.program_level("B.Tech Computer Science") == "Bachelor's"
    assert ident.program_level("BBA") == "Bachelor's"
    assert ident.program_level("MCA") == "Master's"
    assert ident.program_level("MBA") == "Master's"
    assert ident.program_level("Computer Science") is None


def test_offer_pdf_shows_the_basis_of_offer(tmp_path):
    from pypdf import PdfReader

    from app.offers.pdf import build_offer_pdf

    lead = {"name": "Test Student", "program_interest": "B.Tech Computer Science"}
    course = {
        "name": "B.Tech Computer Science",
        "duration": "4 years",
        "fees": "INR 100000",
        "intake": "Fall",
        "payment_link": "",
    }
    offer = {
        "id": "abcd1234-0000-0000-0000-000000000000",
        "program": "B.Tech Computer Science",
        "offer_date": "2026-11-04",
        "valid_until": "2026-12-04",
        "terms": "",
    }
    evidence = {
        "gate_document": "CLASS_12_MARKSHEET",
        "score": "70.83",
        "threshold": "70.0",
        "uploaded_at": "2026-11-04T10:00:00+00:00",
    }

    path = build_offer_pdf(lead, course, offer, tmp_path, evidence=evidence)
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(str(path)).pages
    )
    assert "Basis of Offer" in text
    assert "Class 12 percentage" in text
    assert "70.83" in text
    assert "70.0" in text


def test_blocked_decision_maps_to_crm_status(monkeypatch):
    import asyncio

    from app.crm import status as status_mod

    sent = {}

    async def fake_push(crm_user_id, **fields):
        sent.update(fields)
        return True

    monkeypatch.setattr(status_mod, "push_profile", fake_push)

    assert asyncio.run(status_mod.push_offer_decision("U", "block_missing")) is True
    assert sent["Admission_Status__c"] == "Documents Pending"

    sent.clear()
    assert (
        asyncio.run(status_mod.push_offer_decision("U", "block_not_eligible"))
        is True
    )
    assert sent["Admission_Status__c"] == "Under Review"


def test_offer_guard_keeps_fractional_hours(monkeypatch):
    import asyncio

    from app.offers import models as offer_models

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):
            return None

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(offer_models, "_get_db", lambda: Conn())

    result = asyncio.run(
        offer_models.get_recent_offer_for_lead("lead-1", within_hours=1 / 60)
    )
    assert result is None
    assert "INTERVAL '1 hour'" in captured["sql"]
    assert captured["params"][1] == 1 / 60


def test_generate_does_not_issue_when_the_gate_blocks(
    monkeypatch, override_settings
):
    import asyncio

    from app.offers import service as svc
    from app.offers.eligibility import OfferDecision

    override_settings(OFFER_ELIGIBILITY_SHADOW=False)
    decision = OfferDecision(
        "block_not_eligible", "below-threshold", "no offer", {}
    )

    async def fake_lead(lead_id):
        return {
            "id": lead_id,
            "name": "Test",
            "email": "test@example.com",
            "phone_number": "+1234567890",
            "program_interest": "B.Tech Computer Science",
        }

    async def fake_readiness(lead_id):
        return {
            "ready": False,
            "missing": [],
            "lead": {"id": lead_id},
            "documents": 1,
            "decision": decision,
        }

    published = {}

    async def fake_publish(lead_id, dec):
        published["action"] = dec.action

    monkeypatch.setattr("app.leads.models.get_lead", fake_lead)
    monkeypatch.setattr(svc, "evaluate_offer_readiness", fake_readiness)
    monkeypatch.setattr(svc, "_publish_blocked_decision", fake_publish)

    assert asyncio.run(svc.generate_and_send_offer("lead-1")) is None
    assert published["action"] == "block_not_eligible"
