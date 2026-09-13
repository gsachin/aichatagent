"""
The offer path hands its PDF to the CRM — and nothing else changes when it cannot.

``generate_and_send_offer`` runs inline inside the Twilio WhatsApp webhook, so
the upload is *scheduled*, never awaited. These tests pin the three things that
matter about that arrangement:

* the hook fires with the offer's own id and a PDF that is really on disk;
* a scheduling failure cannot change what the student receives;
* nothing is scheduled when there is no PDF to send.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.offers import service as svc

LEAD = {
    "id": "lead-1",
    "name": "Sachin Tendulkar",
    "email": "sachin@example.com",
    "phone_number": "whatsapp:+14155550123",
    "program_interest": "B.Tech Computer Science",
}

COURSE = {
    "id": "course-1",
    "name": "B.Tech Computer Science",
    "fees": "1000",
    "payment_link": "https://pay.example.com/x",
    "intake": "Fall",
}


def _async_ret(value):
    async def f(*args, **kwargs):
        return value
    return f


def _stub_offer_surface(monkeypatch, *, offer_id="abcd1234-0000-0000-0000-000000000000",
                        pdf_raises=False):
    """Stub everything around the PDF build, so the hook is what is under test."""
    created = {"offer": {
        "id": offer_id,
        "lead_id": "lead-1",
        "program": "B.Tech Computer Science",
        "status": "sent",
        "pdf_path": "",
        "sent_at": "",
    }}

    async def create_offer_letter(**kwargs):
        return dict(created["offer"])

    async def update_status(offer_id, status, **kwargs):
        row = dict(created["offer"])
        row.update({"status": status})
        return row

    async def create_conversation(**kwargs):
        return None

    monkeypatch.setattr("app.leads.models.get_lead", _async_ret(dict(LEAD)))
    monkeypatch.setattr("app.offers.models.list_documents", _async_ret([{"id": "doc-1"}]))
    monkeypatch.setattr("app.offers.models.get_recent_offer_for_lead", _async_ret(None))
    monkeypatch.setattr("app.offers.models.get_course_by_name", _async_ret(dict(COURSE)))
    monkeypatch.setattr("app.offers.models.create_offer_letter", create_offer_letter)
    monkeypatch.setattr("app.offers.models.update_offer_letter_status", update_status)
    monkeypatch.setattr("app.leads.models.create_conversation", create_conversation)
    # The sends reach outside the process; their success is not what is under test.
    monkeypatch.setattr("app.messaging.send_whatsapp_message", lambda *a, **k: (True, "SM123"))
    monkeypatch.setattr("app.emailer.send_email", lambda *a, **k: True)

    if pdf_raises:
        def boom(*args, **kwargs):
            raise RuntimeError("pdf backend exploded")
        monkeypatch.setattr("app.offers.pdf.build_offer_pdf", boom)


def test_offer_generation_schedules_the_crm_upload(monkeypatch, tmp_path, override_settings):
    override_settings(DATA_DIR=str(tmp_path))
    _stub_offer_surface(monkeypatch)
    seen = []

    def fake_schedule(*, lead_id, offer_id, pdf_path):
        seen.append({"lead_id": lead_id, "offer_id": offer_id, "pdf_path": pdf_path})

    monkeypatch.setattr("app.crm.documents.schedule_offer_upload", fake_schedule)

    offer = asyncio.run(svc.generate_and_send_offer("lead-1"))

    assert offer is not None
    assert len(seen) == 1, "exactly one upload per generated offer"
    assert seen[0]["lead_id"] == "lead-1"
    assert seen[0]["offer_id"] == offer["id"]
    assert Path(seen[0]["pdf_path"]).is_file(), (
        "the hook must pass a path to a real PDF, since the uploader reads bytes "
        "from it later"
    )
    assert Path(seen[0]["pdf_path"]).read_bytes().startswith(b"%PDF")


def test_a_scheduling_failure_does_not_change_the_offer(monkeypatch, tmp_path, override_settings):
    override_settings(DATA_DIR=str(tmp_path))
    _stub_offer_surface(monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("crm module unavailable")

    monkeypatch.setattr("app.crm.documents.schedule_offer_upload", boom)

    offer = asyncio.run(svc.generate_and_send_offer("lead-1"))
    assert offer is not None and offer["status"] == "sent"


def test_nothing_is_scheduled_when_the_pdf_could_not_be_built(
    monkeypatch, tmp_path, override_settings
):
    override_settings(DATA_DIR=str(tmp_path))
    _stub_offer_surface(monkeypatch, pdf_raises=True)
    seen = []
    monkeypatch.setattr(
        "app.crm.documents.schedule_offer_upload",
        lambda **kwargs: seen.append(kwargs),
    )

    offer = asyncio.run(svc.generate_and_send_offer("lead-1"))

    assert offer is not None, "the row is still returned for debugging"
    assert seen == [], "there is nothing to upload without a PDF"
