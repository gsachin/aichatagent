"""WhatsApp document-type menu.

A document arrives before its type is known. The file is saved, the numbered
menu is sent, and the lead row holds the upload as *pending* — no
``lead_documents`` row exists until the student answers, so the document can
never be validated or counted for readiness under a guessed type.

These tests drive the real webhook with the session/lead lookups stubbed. The
ordering case matters: the conversation-start language menu also answers to
"1", so the pending-document branch must run first or a document sent as the
first message can never be filed.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi import BackgroundTasks

from app import main as m
from app.crm.session import ConversationSession

PHONE = "whatsapp:+15550124"


def _message_text(response) -> str:
    body = response.body.decode()
    ET.fromstring(body)
    match = re.search(r"<Message>(.*?)</Message>", body, re.S)
    assert match, f"no <Message> in {body!r}"
    return match.group(1)


@pytest.fixture
def harness(monkeypatch, override_settings):
    """A live fake session, a lead with a pending document, and upload spies."""
    override_settings(WHATSAPP_LANGUAGE_MENU=True)
    session = ConversationSession(
        conversation_id="conv-doc", channel="whatsapp", key=PHONE
    )
    lead = {
        "id": "lead-doc",
        "phone_number": PHONE,
        "name": "",
        "email": "",
        "program_interest": "",
        "language": "",
    }
    state = {
        "pending": {
            "path": r"C:\nonexistent\whatsapp_doc.pdf",
            "name": "whatsapp_doc.pdf",
            "created_at": "2026-11-04T00:00:00+00:00",
        },
        "added": [],
        "scheduled": [],
        "cleared": [],
    }
    calls = {"rag": []}

    async def fake_resolve(*args, **kwargs):
        return session

    async def fake_get_lead(phone):
        return dict(lead)

    async def fake_upsert(phone_number="", **kwargs):
        return dict(lead)

    async def fake_get_pending(lead_id):
        return dict(state["pending"]) if state["pending"] else None

    async def fake_clear_pending(lead_id):
        state["cleared"].append(lead_id)
        state["pending"] = None
        return True

    async def fake_add_document(**kwargs):
        state["added"].append(kwargs)
        return {"id": "doc-1", **kwargs}

    def fake_schedule(**kwargs):
        state["scheduled"].append(kwargs)

    async def fake_turns(lead_id, limit=6):
        return []

    async def fake_rag(question, **kwargs):
        calls["rag"].append(question)
        return "rag answer"

    async def fake_link(**kwargs):
        return ""

    async def fake_recent(phone, within_seconds=None, channel="whatsapp"):
        return None

    import app.crm.documents as crm_documents
    import app.crm.session as crm_session
    import app.crm.sync as crm_sync
    import app.leads.models as leads_models
    import app.offers.models as offers_models

    monkeypatch.setattr(crm_session, "resolve_whatsapp_session", fake_resolve)
    monkeypatch.setattr(leads_models, "get_lead_by_phone", fake_get_lead)
    monkeypatch.setattr(leads_models, "upsert_lead_by_phone", fake_upsert)
    monkeypatch.setattr(leads_models, "get_recent_conversation_for_phone", fake_recent)
    monkeypatch.setattr(leads_models, "get_lead_pending_document", fake_get_pending)
    monkeypatch.setattr(leads_models, "clear_lead_pending_document", fake_clear_pending)
    monkeypatch.setattr(offers_models, "add_document", fake_add_document)
    monkeypatch.setattr(crm_documents, "schedule_document_upload", fake_schedule)
    monkeypatch.setattr(m, "_whatsapp_recent_turns", fake_turns)
    monkeypatch.setattr(m, "_whatsapp_chat_rag", fake_rag)
    monkeypatch.setattr(crm_sync, "link_conversation", fake_link)

    return session, lead, state, calls


def send(text: str) -> str:
    bg = BackgroundTasks()
    response = asyncio.run(
        m.twilio_whatsapp_webhook(
            background_tasks=bg,
            Body=text,
            MediaUrl0="",
            MediaContentType0="",
            NumMedia="0",
            From=PHONE,
            To="whatsapp:+14155238886",
            WaId="15550124",
            Language="",
        )
    )
    return _message_text(response)


def test_number_choice_files_the_document_as_class_12(harness):
    """'1' answers the document menu, not the language menu, and files it."""
    _session, _lead, state, calls = harness

    reply = send("1")

    assert "Filed as 12th Marksheet" in reply
    assert "Please choose the language" not in reply
    assert [d["doc_type"] for d in state["added"]] == ["marksheet"]
    assert [s["doc_type"] for s in state["scheduled"]] == ["marksheet"]
    assert state["cleared"] == ["lead-doc"]
    assert state["pending"] is None
    assert calls["rag"] == []


def test_exam_alias_files_as_the_exact_exam(harness):
    """A typed exam name keeps the exam, which is what the validator needs."""
    _session, _lead, state, _calls = harness

    reply = send("GMAT")

    assert "Filed as GMAT Score Report" in reply
    assert state["added"][0]["doc_type"] == "gmat"
    assert state["scheduled"][0]["doc_type"] == "gmat"


def test_invalid_reply_re_sends_the_menu_and_keeps_the_document_pending(harness):
    _session, _lead, state, calls = harness

    reply = send("banana")

    assert "What type of document is this?" in reply
    assert "1. 12th Marksheet" in reply
    assert state["added"] == []
    assert state["scheduled"] == []
    assert state["cleared"] == []
    assert state["pending"] is not None
    assert calls["rag"] == []
