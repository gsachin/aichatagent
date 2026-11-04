"""Offer follow-up after a WhatsApp document is validated.

The CRM upload runs in the background, so the webhook cannot put the offer in
its reply. ``maybe_send_offer_after_validation`` is what continues the
conversation: it issues the offer when the gate document passed, and sends the
decision message when the marks are below threshold or need review.
"""

from __future__ import annotations

import asyncio

import pytest

import app.messaging as messaging
import app.leads.models as leads_models
import app.offers.service as offer_service
from app.offers.eligibility import OfferDecision


def _decision(action: str, reason: str, doc_id: str = "doc-1") -> OfferDecision:
    return OfferDecision(
        action=action,
        reason=reason,
        message=f"decision message for {reason}",
        evidence={"document_id": doc_id},
    )


@pytest.fixture
def harness(monkeypatch, override_settings):
    override_settings(OFFER_ELIGIBILITY_SHADOW=False)
    state = {
        "lead": {
            "id": "lead-1",
            "phone_number": "whatsapp:+15550125",
            "source": "whatsapp",
            "program_interest": "MBA",
            "name": "Test",
            "email": "test@example.com",
        },
        "readiness": {
            "ready": True,
            "missing": [],
            "lead": None,
            "documents": 1,
            "decision": _decision("issue", "requirement-satisfied"),
        },
        "generated": [],
        "messages": [],
    }

    async def fake_get_lead(lead_id):
        return dict(state["lead"]) if state["lead"] else None

    async def fake_readiness(lead_id):
        return dict(state["readiness"])

    async def fake_generate(lead_id, **kwargs):
        state["generated"].append({"lead_id": lead_id, **kwargs})
        return {"id": "offer-1"}

    def fake_send(phone, sender, body, **kwargs):
        state["messages"].append({"phone": phone, "body": body})
        return True, "sid"

    monkeypatch.setattr(leads_models, "get_lead", fake_get_lead)
    monkeypatch.setattr(offer_service, "evaluate_offer_readiness", fake_readiness)
    monkeypatch.setattr(offer_service, "generate_and_send_offer", fake_generate)
    monkeypatch.setattr(messaging, "send_whatsapp_message", fake_send)
    return state


def run(lead_id="lead-1", doc_id="doc-1"):
    asyncio.run(
        offer_service.maybe_send_offer_after_validation(lead_id, doc_id=doc_id)
    )


def test_a_passing_gate_document_issues_the_offer_without_done(harness):
    run()

    assert harness["generated"] == [
        {"lead_id": "lead-1", "channel": "whatsapp"}
    ]
    assert harness["messages"] == []


def test_below_threshold_sends_the_reason_and_issues_nothing(harness):
    harness["readiness"]["decision"] = _decision("block_not_eligible", "below-threshold")

    run()

    assert harness["generated"] == []
    assert harness["messages"][0]["body"] == "decision message for below-threshold"


def test_review_required_sends_the_review_message(harness):
    harness["readiness"]["decision"] = _decision("block_review", "manual-review")

    run()

    assert harness["generated"] == []
    assert harness["messages"][0]["body"] == "decision message for manual-review"


def test_missing_documents_do_not_send_anything(harness):
    harness["readiness"]["decision"] = _decision("block_missing", "gate-document-missing")

    run()

    assert harness["generated"] == []
    assert harness["messages"] == []


def test_another_channels_upload_does_not_send_whatsapp(harness):
    harness["lead"]["source"] = "manual"

    run()

    assert harness["generated"] == []
    assert harness["messages"] == []


def test_a_document_that_did_not_produce_the_decision_is_ignored(harness):
    """Uploading ID proof after the transcript must not re-send the offer."""
    run(doc_id="some-other-document")

    assert harness["generated"] == []
    assert harness["messages"] == []


def test_shadow_mode_never_sends(harness, override_settings):
    override_settings(OFFER_ELIGIBILITY_SHADOW=True)

    run()

    assert harness["generated"] == []
    assert harness["messages"] == []


def test_missing_profile_fields_defer_the_offer(harness):
    harness["readiness"]["missing"] = ["email"]

    run()

    assert harness["generated"] == []
    assert harness["messages"] == []
