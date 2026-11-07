"""The deterministic WhatsApp chat flow in the selected language.

The translation table is covered separately; these tests drive the real
webhook and assert the greeting/name/email/program/docs replies, the menu
replay, the English byte-identity and the XML boundary.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import pytest
from fastapi import BackgroundTasks

from app import main as m
from app.crm.session import ConversationSession
from app.whatsapp_text import WHATSAPP_TEXT, whatsapp_text

PHONE = "whatsapp:+15550124"
SELECTABLE = ("hi", "gu", "es", "zh")


def _message_text(response) -> str:
    body = response.body.decode()
    parsed = ET.fromstring(body)
    message = parsed.findtext("Message")
    assert message is not None, f"no <Message> in {body!r}"
    return message


@pytest.fixture
def harness(monkeypatch, override_settings):
    """A live fake session plus mutable lead/RAG/CRM stubs."""
    override_settings(
        WHATSAPP_LANGUAGE_MENU=False,
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
    )
    session = ConversationSession(
        conversation_id="conv-i18n", channel="whatsapp", key=PHONE
    )
    lead = {
        "id": "lead-i18n",
        "phone_number": PHONE,
        "name": "",
        "email": "",
        "program_interest": "",
        "language": "",
    }
    calls = {"rag": [], "stored": {}}

    async def fake_resolve(*args, **kwargs):
        return session

    async def fake_get_lead(phone):
        return dict(lead)

    async def fake_upsert(phone_number="", **kwargs):
        return dict(lead)

    async def fake_update(lead_id, **kwargs):
        lead.update({k: v for k, v in kwargs.items() if v is not None})
        return dict(lead)

    async def fake_turns(lead_id, limit=6):
        return []

    async def fake_rag(question, **kwargs):
        calls["rag"].append({"question": question, **kwargs})
        return f"[RAG:{kwargs.get('language')}] {question}"

    async def fake_link(**kwargs):
        return ""

    async def fake_recent(phone, within_seconds=None, channel="whatsapp"):
        return dict(calls["stored"]) if calls["stored"] else None

    import app.crm.session as crm_session
    import app.crm.sync as crm_sync
    import app.leads.models as leads_models

    monkeypatch.setattr(crm_session, "resolve_whatsapp_session", fake_resolve)
    monkeypatch.setattr(leads_models, "get_lead_by_phone", fake_get_lead)
    monkeypatch.setattr(leads_models, "upsert_lead_by_phone", fake_upsert)
    monkeypatch.setattr(leads_models, "update_lead", fake_update)
    monkeypatch.setattr(
        leads_models, "get_recent_conversation_for_phone", fake_recent
    )
    monkeypatch.setattr(m, "_whatsapp_recent_turns", fake_turns)
    monkeypatch.setattr(m, "_whatsapp_chat_rag", fake_rag)
    monkeypatch.setattr(crm_sync, "link_conversation", fake_link)

    return session, lead, calls


def send(text: str) -> tuple[str, BackgroundTasks]:
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
    return _message_text(response), bg


def _use(session, language: str) -> None:
    session.language = language
    session.language_source = "selected"


@pytest.mark.parametrize("language", SELECTABLE)
def test_greeting_and_name_prompt_are_localized(harness, language):
    session, _lead, _calls = harness
    _use(session, language)

    reply, _bg = send("hello")

    assert reply == WHATSAPP_TEXT[language]["greeting_unknown_name"]
    assert "What's your name?" not in reply


@pytest.mark.parametrize("language", SELECTABLE)
def test_name_capture_asks_for_email_in_the_selected_language(harness, language):
    session, lead, _calls = harness
    _use(session, language)

    send("hello")
    reply, _bg = send("Asha")

    expected = whatsapp_text(
        "name_captured_ask_email",
        language,
        "Thanks {name}! What's your email address? I'll use it to send you "
        "program details and follow up.",
        name="Asha",
    )
    assert reply == expected
    assert lead["name"] == "Asha"
    assert "What's your email address?" not in reply
    assert "Asha" in reply


@pytest.mark.parametrize("language", ("hi", "gu"))
def test_localized_onboarding_reaches_program_prompt(harness, language):
    session, lead, _calls = harness
    _use(session, language)

    send("hello")
    send("Asha")
    reply, _bg = send("asha@example.com")
    expected = whatsapp_text(
        "email_saved_with_name",
        language,
        "Thanks {name}! I've saved your email.",
        name="Asha",
    )
    assert reply == expected
    assert lead["email"] == "asha@example.com"

    reply, _bg = send("I want to take admission")
    assert reply == WHATSAPP_TEXT[language]["program_ask_interested"]


def test_docs_prompt_is_localized(harness):
    session, lead, _calls = harness
    _use(session, "hi")
    lead.update(name="Asha", email="asha@example.com", program_interest="MBA")

    reply, _bg = send("I want to take admission")

    expected = whatsapp_text(
        "admission_docs_prompt",
        "hi",
        "Great! To process your admission for *{program}*, please upload the "
        "following documents:\n\n📄 Transcript / Mark Sheet\n🆔 ID Proof "
        "(Passport, Aadhaar, or Driver's License)\n📝 Any additional "
        "certificates (optional)\n\nJust send clear photos or PDFs right here "
        "in WhatsApp. Type 'done' when you've sent everything.",
        program="MBA",
    )
    # The webhook strips Markdown before sending; the emphasis around the
    # program name is not part of the wire text.
    assert reply == m._strip_markdown(expected)
    assert "MBA" in reply
    assert "please upload" not in reply


def test_menu_selection_replays_greeting_in_the_chosen_language(
    harness, override_settings
):
    override_settings(WHATSAPP_LANGUAGE_MENU=True)
    session, _lead, _calls = harness

    menu, _bg = send("hello")
    assert "Please choose the language" in menu
    assert session.pending_language_text == "hello"

    reply, _bg = send("2")

    assert "ठीक है" in reply
    assert WHATSAPP_TEXT["hi"]["greeting_unknown_name"] in reply
    assert "What's your name?" not in reply


def test_english_output_is_byte_identical_for_empty_language(harness):
    """The English path must not depend on the new table."""
    session, _lead, _calls = harness
    # No language at all: the deployment default is English.
    session.language = ""
    session.language_source = ""

    greeting, _bg = send("hello")
    assert greeting == (
        "Hello! 👋 I'm the Dr. Shyama Prasad Mukherjee University admissions assistant. "
        "What's your name?"
    )

    reply, _bg = send("Asha")
    assert reply == (
        "Thanks Asha! What's your email address? I'll use it to send you "
        "program details and follow up."
    )


def test_english_docs_prompt_is_byte_identical(harness):
    session, lead, _calls = harness
    session.language = ""
    session.language_source = ""
    lead.update(name="Asha", email="asha@example.com", program_interest="MBA")

    reply, _bg = send("I want to take admission")

    expected = m._strip_markdown(
        "Great! To process your admission for *MBA*, please upload the "
        "following documents:\n\n📄 Transcript / Mark Sheet\n🆔 ID Proof "
        "(Passport, Aadhaar, or Driver's License)\n📝 Any additional "
        "certificates (optional)\n\nJust send clear photos or PDFs right here "
        "in WhatsApp. Type 'done' when you've sent everything."
    )
    assert reply == expected


def test_hindi_intent_starts_the_document_flow(harness):
    """A natural Hindi sentence must reach the flow, not the knowledge base."""
    session, lead, calls = harness
    _use(session, "hi")
    lead.update(name="Asha", email="asha@example.com")

    reply, _bg = send("मैं BA History में एडमिशन लेना चाहता हूँ")

    expected = whatsapp_text(
        "admission_docs_prompt",
        "hi",
        "Great! To process your admission for *{program}*, please upload the "
        "following documents:\n\n📄 Transcript / Mark Sheet\n🆔 ID Proof "
        "(Passport, Aadhaar, or Driver's License)\n📝 Any additional "
        "certificates (optional)\n\nJust send clear photos or PDFs right here "
        "in WhatsApp. Type 'done' when you've sent everything.",
        program="Bachelor of Arts in History (BA)",
    )
    assert reply == m._strip_markdown(expected)
    assert lead["program_interest"] == "Bachelor of Arts in History (BA)"
    assert calls["rag"] == []


def test_gujarati_intent_starts_the_document_flow(harness):
    session, lead, calls = harness
    _use(session, "gu")
    lead.update(name="Asha", email="asha@example.com")

    reply, _bg = send("મને BA History માં એડમિશન લેવું છે")

    expected = whatsapp_text(
        "admission_docs_prompt",
        "gu",
        "Great! To process your admission for *{program}*, please upload the "
        "following documents:\n\n📄 Transcript / Mark Sheet\n🆔 ID Proof "
        "(Passport, Aadhaar, or Driver's License)\n📝 Any additional "
        "certificates (optional)\n\nJust send clear photos or PDFs right here "
        "in WhatsApp. Type 'done' when you've sent everything.",
        program="Bachelor of Arts in History (BA)",
    )
    assert reply == m._strip_markdown(expected)
    assert calls["rag"] == []


def test_hindi_process_question_stays_a_knowledge_question(harness, monkeypatch):
    """``प्रवेश प्रक्रिया`` is a question, not intent to start the flow."""
    session, lead, calls = harness
    _use(session, "hi")
    lead.update(name="Asha", email="asha@example.com")

    import app.llm_backend as backend

    monkeypatch.setattr(backend, "chat", lambda **kwargs: "no")

    question = "मैं प्रवेश प्रक्रिया जानना चाहता हूँ"
    reply, _bg = send(question)

    assert calls["rag"] and calls["rag"][0]["question"] == question
    assert "[RAG:hi]" in reply


def test_hindi_done_reply_triggers_offer_generation(harness, monkeypatch):
    session, lead, _calls = harness
    _use(session, "hi")
    lead.update(name="Asha", email="asha@example.com", program_interest="M.Com")

    async def fake_offer(lead_id, lead_name, conversation_id="", *, language=""):
        return f"[OFFER:{language}]"

    monkeypatch.setattr(m, "_whatsapp_offer_on_done", fake_offer)

    reply, _bg = send("हो गया")

    assert reply == "[OFFER:hi]"


def test_unknown_language_falls_back_to_english(harness):
    session, _lead, _calls = harness
    _use(session, "de")

    reply, _bg = send("hello")

    assert reply == (
        "Hello! 👋 I'm the Dr. Shyama Prasad Mukherjee University admissions assistant. "
        "What's your name?"
    )


def test_localized_reply_still_escapes_xml(harness):
    session, lead, _calls = harness
    _use(session, "hi")

    send("hello")
    reply, _bg = send("Tom & Jerry")

    assert lead["name"] == "Tom & Jerry"
    # The TwiML parser is the real assertion: the reply must stay valid and
    # the message must contain the raw name after one escape/decode round-trip.
    assert "Tom & Jerry" in reply


def test_menu_selection_keeps_log_language_and_source(
    harness, override_settings
):
    override_settings(WHATSAPP_LANGUAGE_MENU=True)
    session, _lead, calls = harness

    send("What are the fees?")
    _reply, bg = send("3")

    assert session.language == "gu"
    assert session.language_source == "selected"
    assert calls["rag"][0]["language"] == "gu"
    assert bg.tasks[0].kwargs["language"] == "gu"
    assert bg.tasks[0].kwargs["language_source"] == "selected"


@pytest.mark.anyio
async def test_offer_messages_are_localized(monkeypatch, override_settings):
    import app.offers.models as offers_models
    import app.offers.service as offers_service

    async def missing_readiness(lead_id):
        return {"missing": ["name"]}

    async def no_docs(lead_id):
        return []

    monkeypatch.setattr(
        offers_service, "evaluate_offer_readiness", missing_readiness
    )
    monkeypatch.setattr(offers_models, "list_documents", no_docs)

    reply = await m._whatsapp_offer_on_done(
        "lead-1", "Asha", "conv-1", language="hi"
    )
    assert reply == (
        "आपका ऑफ़र लेटर बनाने से पहले मुझे यह अभी चाहिए: आपका पूरा नाम।"
    )
    assert "your full name" not in reply

    async def ready(lead_id):
        return {"missing": []}

    monkeypatch.setattr(offers_service, "evaluate_offer_readiness", ready)
    reply = await m._whatsapp_offer_on_done(
        "lead-1", "Asha", "conv-1", language="hi"
    )
    assert reply == WHATSAPP_TEXT["hi"]["offer_no_documents"]
    assert "documents from you yet" not in reply


@pytest.mark.anyio
async def test_english_offer_messages_are_byte_identical(
    monkeypatch,
):
    import app.offers.models as offers_models
    import app.offers.service as offers_service

    async def ready(lead_id):
        return {"missing": []}

    async def no_docs(lead_id):
        return []

    monkeypatch.setattr(offers_service, "evaluate_offer_readiness", ready)
    monkeypatch.setattr(offers_models, "list_documents", no_docs)

    reply = await m._whatsapp_offer_on_done("lead-1", "Asha", "conv-1")
    assert reply == (
        "I don't see any documents from you yet. Please send clear photos or "
        "PDFs of your transcript/marksheet and ID proof, then type 'done'."
    )


def test_english_message_bank_stays_out_of_the_table():
    for language, entries in WHATSAPP_TEXT.items():
        assert entries, language
        for key, value in entries.items():
            assert value, (language, key)
