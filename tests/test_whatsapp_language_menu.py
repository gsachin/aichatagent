"""WhatsApp conversation-start language menu.

Every new WhatsApp conversation opens with the numbered language menu, even
when the number has a saved language — the demo starts a new conversation on
every restart, and the operator needs the menu each time. Remembering is kept:
the saved language answers the held message if the menu is ignored, and an
explicit choice inside a conversation suppresses the menu for the rest of it.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi import BackgroundTasks

from app import main as m
from app.crm.session import ConversationSession

PHONE = "whatsapp:+15550123"


def _message_text(response) -> str:
    body = response.body.decode()
    ET.fromstring(body)
    match = re.search(r"<Message>(.*?)</Message>", body, re.S)
    assert match, f"no <Message> in {body!r}"
    return match.group(1)


@pytest.fixture
def harness(monkeypatch, override_settings):
    """A live fake WhatsApp session plus lead/RAG/CRM stubs."""
    override_settings(
        WHATSAPP_LANGUAGE_MENU=True,
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
    )
    session = ConversationSession(
        conversation_id="conv-1", channel="whatsapp", key=PHONE
    )
    lead = {
        "id": "lead-1",
        "phone_number": PHONE,
        "name": "",
        "email": "",
        "program_interest": "",
        "language": "",
    }
    calls = {"rag": [], "logs": [], "stored": {}}

    async def fake_resolve(*args, **kwargs):
        return session

    async def fake_get_lead(phone):
        return dict(lead)

    async def fake_upsert(phone_number="", **kwargs):
        return dict(lead)

    async def fake_update(lead_id, **kwargs):
        lead.update(
            {k: v for k, v in kwargs.items() if v is not None}
        )
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
    monkeypatch.setattr(leads_models, "get_recent_conversation_for_phone", fake_recent)
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
            WaId="15550123",
            Language="",
        )
    )
    return _message_text(response), bg


def test_first_contact_shows_the_menu_and_holds_the_question(harness):
    session, _lead, calls = harness

    reply, bg = send("What are the fees?")

    assert "Please choose the language" in reply
    assert "1. English" in reply
    assert "2. हिंदी (Hindi)" in reply
    assert "3. ગુજરાતી (Gujarati)" in reply
    assert "9. 中文 (Chinese)" in reply
    assert session.pending_language_text == "What are the fees?"
    assert session.language == ""
    assert calls["rag"] == []
    # The menu turn is not logged: a row with the default language would make
    # the number look like a returning user and suppress the menu forever.
    assert bg.tasks == []


def test_digit_replays_the_question_in_the_chosen_language(harness):
    session, _lead, calls = harness

    send("What are the fees?")
    reply, bg = send("3")

    assert session.language == "gu"
    assert session.language_source == "selected"
    assert session.pending_language_text == ""
    assert calls["rag"][0]["question"] == "What are the fees?"
    assert calls["rag"][0]["language"] == "gu"
    assert "[RAG:gu]" in reply
    assert "ઠીક છે" in reply
    assert bg.tasks and bg.tasks[0].kwargs["language"] == "gu"
    assert bg.tasks[0].kwargs["language_source"] == "selected"
    assert "User: What are the fees?" in bg.tasks[0].kwargs["transcript"]
    assert "User: 3" in bg.tasks[0].kwargs["transcript"]


def test_language_name_replays_the_question(harness):
    session, _lead, calls = harness

    send("What are the fees?")
    reply, _bg = send("Gujarati")

    assert session.language == "gu"
    assert calls["rag"][0]["language"] == "gu"
    assert "ઠીક છે" in reply


def test_bare_hi_is_a_greeting_not_hindi(harness):
    session, _lead, _calls = harness

    reply, bg = send("hi")

    assert "Please choose the language" in reply
    assert "1. English" in reply
    assert session.pending_language_text == "hi"
    assert session.language == ""


def test_restart_reply_without_pending_confirms_and_asks_to_resend(harness):
    session, _lead, _calls = harness
    # A fresh process: the menu prompt is gone, but no language was saved.
    session.pending_language_text = ""

    reply, bg = send("2")

    assert session.language == "hi"
    assert session.language_source == "selected"
    assert "ठीक है" in reply
    assert "कृपया अपना सवाल फिर से भेजिए।" in reply
    assert "Please choose the language" not in reply
    assert bg.tasks and bg.tasks[0].kwargs["language"] == "hi"
    assert bg.tasks[0].kwargs["language_source"] == "selected"


def test_invalid_reply_repeats_then_falls_back_after_two_tries(harness):
    session, _lead, calls = harness

    send("What are the fees?")
    reply1, _ = send("xyz")
    assert "Please choose the language" in reply1
    assert session.pending_language_text == "What are the fees?"

    # The second invalid reply is the fallback: answer the held question in
    # the default language instead of asking a third time.
    reply2, _ = send("abc")
    assert "Please choose the language" not in reply2
    assert "[RAG:en]" in reply2
    assert calls["rag"][0]["question"] == "What are the fees?"
    assert session.language == "en"
    assert session.pending_language_text == ""


def test_command_while_pending_replays_the_question(harness):
    session, _lead, calls = harness

    send("What are the fees?")
    reply, bg = send("Spanish")

    assert session.language == "es"
    assert session.language_source == "selected"
    assert calls["rag"][0]["language"] == "es"
    assert "Perfecto" in reply
    assert bg.tasks and bg.tasks[0].kwargs["language"] == "es"
    assert bg.tasks[0].kwargs["language_source"] == "selected"


def test_explicitly_selected_conversation_skips_the_menu(harness):
    """Within a conversation, an answered menu is not reopened by a later text."""
    session, _lead, calls = harness
    session.language = "fr"
    session.language_source = "selected"
    session.language_menu_done = True

    reply, _bg = send("What are the fees?")

    assert "Please choose the language" not in reply
    assert calls["rag"][0]["language"] == "fr"


def test_saved_language_is_offered_the_menu_and_answers_if_it_is_ignored(harness):
    """A new conversation asks again; remembering is the fallback, not a veto.

    The demo restarts between conversations, so a returning number must still
    see the menu. If the student ignores it twice, the held question is
    answered in the language they chose last time.
    """
    _session, lead, calls = harness
    lead["language"] = "gu"
    calls["stored"] = {"language": "gu", "language_source": "selected"}

    menu, _bg = send("What are the fees?")
    assert "Please choose the language" in menu

    send("xyz")
    reply, _bg = send("abc")

    assert "Please choose the language" not in reply
    assert calls["rag"][0]["language"] == "gu", "saved language is the fallback"
    assert calls["rag"][0]["question"] == "What are the fees?"


def test_menu_is_not_reopened_after_a_choice_in_the_same_conversation(harness):
    session, _lead, calls = harness

    send("What are the fees?")
    send("3")

    reply, _bg = send("hi")

    assert "Please choose the language" not in reply
    assert session.language == "gu"
    assert "નમસ્તે" in reply


def test_legacy_saved_language_is_asked_once(harness):
    """Older rows carry the implicit English default with no source marker."""
    session, lead, calls = harness
    lead["language"] = "en"
    calls["stored"] = {"language": "en", "language_source": ""}

    reply, bg = send("What are the fees?")

    assert "Please choose the language" in reply
    assert session.language == "en"          # kept as the fallback
    assert session.language_source == ""     # not an explicit choice
    assert bg.tasks == []

    reply2, bg2 = send("3")
    assert session.language == "gu"
    assert session.language_source == "selected"
    assert "[RAG:gu]" in reply2
    assert bg2.tasks[0].kwargs["language_source"] == "selected"


def test_foreign_disabled_offers_only_the_core_three(
    harness, override_settings
):
    override_settings(FOREIGN_LANGUAGES="")
    _session, _lead, _calls = harness

    reply, _bg = send("What are the fees?")

    assert "1. English" in reply
    assert "3. ગુજરાતી (Gujarati)" in reply
    assert "4." not in reply
    assert "Spanish" not in reply


def test_menu_text_is_short_enough_for_one_whatsapp_message(harness):
    from app.languages import whatsapp_language_menu_options
    from app.localized_text import whatsapp_language_menu

    text = whatsapp_language_menu(whatsapp_language_menu_options())
    assert len(text) < 1600


def test_menu_parser_accepts_numbers_names_and_slash_forms(harness):
    from app.languages import (
        language_from_menu_reply,
        whatsapp_language_menu_options,
    )

    options = whatsapp_language_menu_options()
    assert language_from_menu_reply("2", options) == "hi"
    assert language_from_menu_reply("2.", options) == "hi"
    assert language_from_menu_reply("2 - Hindi", options) == "hi"
    assert language_from_menu_reply("Hindi", options) == "hi"
    assert language_from_menu_reply("हिंदी", options) == "hi"
    assert language_from_menu_reply("ગુજરાતી", options) == "gu"
    assert language_from_menu_reply("Español", options) == "es"
    assert language_from_menu_reply("中文", options) == "zh"
    assert language_from_menu_reply("/hi", options) == "hi"
    assert language_from_menu_reply("/gu", options) == "gu"
    # Ambiguous bare tokens are deliberately not choices.
    assert language_from_menu_reply("hi", options) is None
    assert language_from_menu_reply("gu", options) is None
    assert language_from_menu_reply("it", options) is None
    assert language_from_menu_reply("10", options) is None


def test_menu_numbering_follows_the_configured_subset(
    harness, override_settings
):
    override_settings(FOREIGN_LANGUAGES="es,ja")
    from app.languages import (
        language_from_menu_reply,
        whatsapp_language_menu_options,
    )

    options = whatsapp_language_menu_options()
    assert [digit for digit, _code, _label in options] == [
        "1", "2", "3", "4", "5",
    ]
    assert language_from_menu_reply("4", options) == "es"
    assert language_from_menu_reply("5", options) == "ja"
    assert language_from_menu_reply("6", options) is None


def test_flag_off_keeps_the_old_first_message_flow(
    harness, override_settings
):
    override_settings(WHATSAPP_LANGUAGE_MENU=False)
    _session, _lead, _calls = harness

    reply, _bg = send("hello")

    assert "Please choose the language" not in reply
    assert "What's your name?" in reply


def test_language_command_shows_the_menu_and_the_help(harness):
    _session, _lead, _calls = harness

    reply, _bg = send("/language")

    assert "Please choose the language" in reply
    assert "9. 中文 (Chinese)" in reply
    assert "Hindi or /hi" in reply
    assert "Gujarati or /gu" in reply
