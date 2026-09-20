"""
WhatsApp state-machine regression tests.

These replay the production transcript that exposed the bug: a lead whose
stored program was MBA asked for M.Tech and was told, three separate times,
that their admission was for MBA — including on the turn that produced the
offer letter.

Two independent defects had to line up for that, and both are covered here:

  1. The program was write-once. `detected_prog and not lead_program` meant a
     stored value always won, so a later explicit choice was discarded.
  2. The alias matcher missed "M. Tech" — the spelling the knowledge base
     itself uses — because "m.tech" was tested as a raw substring, and "BA"
     was not in the table at all.

Neither fix alone is sufficient: with only (2) the guard still discarded the
detection, and with only (1) the message still detected nothing.
"""

import asyncio
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi import BackgroundTasks

from app import main as m
from app.crm.session import ConversationSession

PHONE = "whatsapp:+917757057985"


@pytest.fixture
def lead_store(monkeypatch):
    """A lead in exactly the state the live database was in."""
    lead = {
        "id": "lead-1",
        "phone_number": PHONE,
        "name": "Guruji",
        "email": "test@test.com",
        "program_interest": "MBA",
        "status": "in_progress",
        "source": "whatsapp",
    }
    writes: list[dict] = []

    async def fake_get_lead_by_phone(phone):
        return dict(lead)

    async def fake_upsert_lead_by_phone(phone_number="", **kwargs):
        return dict(lead)

    async def fake_update_lead(lead_id, **kwargs):
        writes.append(kwargs)
        lead.update({k: v for k, v in kwargs.items() if v is not None})
        return dict(lead)

    import app.leads.models as models

    monkeypatch.setattr(models, "get_lead_by_phone", fake_get_lead_by_phone)
    monkeypatch.setattr(models, "upsert_lead_by_phone", fake_upsert_lead_by_phone)
    monkeypatch.setattr(models, "update_lead", fake_update_lead)
    return lead, writes


@pytest.fixture
def session(monkeypatch):
    """One live WhatsApp session plus stubs for everything off-box."""
    live = ConversationSession(conversation_id="conv-1", channel="whatsapp", key=PHONE)
    holder = {"turns": []}

    async def fake_resolve(phone, *, idle_window_hours):
        return live

    async def fake_link_conversation(**kwargs):
        return ""

    async def fake_turns(lead_id, limit=6):
        return list(holder["turns"])

    async def fake_rag(question, **kwargs):
        return f"[RAG] {question}"

    import app.crm.session as crm_session
    import app.crm.sync as crm_sync

    monkeypatch.setattr(crm_session, "resolve_whatsapp_session", fake_resolve)
    monkeypatch.setattr(crm_sync, "link_conversation", fake_link_conversation)
    monkeypatch.setattr(m, "_whatsapp_recent_turns", fake_turns)
    monkeypatch.setattr(m, "_whatsapp_chat_rag", fake_rag)
    live.holder = holder
    return live


def send(text: str) -> str:
    """
    Drive the real webhook and return the <Message> body it replies with.

    Every Form(...) parameter is passed explicitly: calling the handler
    directly bypasses FastAPI's form resolution, so an omitted one arrives as
    the `Form()` default object rather than a string.
    """
    response = asyncio.run(
        m.twilio_whatsapp_webhook(
            background_tasks=BackgroundTasks(),
            Body=text,
            MediaUrl0="",
            MediaContentType0="",
            NumMedia="0",
            From=PHONE,
            To="whatsapp:+14155238886",
            WaId=PHONE.replace("whatsapp:", ""),
        )
    )
    body = response.body.decode()
    # Every reply must be parseable TwiML — a name or program containing
    # "&" or "<" used to make the response malformed and Twilio dropped it.
    parsed = ET.fromstring(body)
    match = re.search(r"<Message>(.*?)</Message>", body, re.S)
    assert match, f"no <Message> in {body!r}"
    assert parsed.findtext("Message") is not None
    return match.group(1)


# ── The reported bug ────────────────────────────────────────────────────

def test_m_tech_beats_a_stale_mba(lead_store, session):
    """The exact final turn of the transcript."""
    lead, _ = lead_store
    reply = send("I want to take admission in M. Tech")

    assert "M.Tech" in reply
    assert "MBA" not in reply, "the stored program must not win over the request"
    assert lead["program_interest"] == "M.Tech"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I want to take admission in M. Tech", "M.Tech"),
        ("I want to take admission in M.Tech", "M.Tech"),
        ("i want to take admission in mtech", "M.Tech"),
        ("I would like to take admission in BA", "BA"),
        ("I want to take admission in MBA", "MBA"),
    ],
)
def test_every_spelling_registers_the_program(lead_store, session, text, expected):
    """
    "M. Tech" with a space used to detect nothing, and "BA" was missing from
    the alias table entirely, so both fell through to the stored MBA.
    """
    lead, _ = lead_store
    reply = send(text)
    assert expected in reply
    assert lead["program_interest"] == expected


def test_program_change_is_logged_and_persisted(lead_store, session):
    """A switch is written, not just echoed back for one turn."""
    lead, writes = lead_store
    send("I want to take admission in M. Tech")
    assert {"program_interest": "M.Tech"} in writes

    # ...and a second switch moves it again rather than sticking on M.Tech.
    reply = send("I want to take admission in BCA")
    assert "BCA" in reply
    assert lead["program_interest"] == "BCA"


# ── False positives the old substring test produced ─────────────────────

def test_a_city_containing_mba_does_not_set_the_program(lead_store, session):
    lead, _ = lead_store
    lead["program_interest"] = ""
    send("I am from Mumbai")
    assert lead["program_interest"] == "", "'mumbai' must not match 'mba'"


# ── Name correction (previously impossible) ─────────────────────────────

def test_name_correction_is_honoured(lead_store, session):
    """The name could only be set while blank, so this request was ignored."""
    lead, _ = lead_store
    reply = send("can you correct my name in you systems my name is Karthik")

    assert lead["name"] == "Karthik"
    assert "Karthik" in reply


def test_negated_name_does_not_become_the_name(lead_store, session):
    """"my name is not Guruji" must not capture "not Guruji" as the name."""
    lead, _ = lead_store
    reply = send("my name is not Guruji check the start the chat for my name")

    assert lead["name"] == "Guruji", "the negation must not be captured"
    assert "change your name to" in reply


# ── Acknowledgements are read in context ────────────────────────────────

def test_yes_after_a_knowledge_question_is_not_a_confirmation(lead_store, session):
    """The turn that produced "You're confirmed for MBA" out of a "Yes"."""
    lead, _ = lead_store
    # A knowledge question that happens to name no program.
    session.holder["turns"] = [
        "User: tell me about the campus\n"
        "Assistant: Meridian has a 250-acre campus. Would you like to know more "
        "about campus life?"
    ]
    reply = send("Yes")

    assert "confirmed for" not in reply
    assert reply.startswith("[RAG]")


def test_yes_never_changes_the_course(lead_store, session):
    """
    An acknowledgement is not a course choice.

    The bot's own previous message named M.Tech, but only the student states a
    course — a "yes" to something the bot said must leave the stored program
    exactly where it was.
    """
    lead, writes = lead_store
    session.holder["turns"] = [
        "User: tell me about m.tech\n"
        "Assistant: You're interested in pursuing an M.Tech in Mechanical "
        "Engineering at Meridian University. Would you like to know more?"
    ]
    reply = send("Yes")

    assert lead["program_interest"] == "MBA", "the bot must not move the course"
    assert {"program_interest": "M.Tech"} not in writes
    assert "confirmed for" not in reply
    assert reply.startswith("[RAG]")


def test_yes_reaffirms_the_program_already_on_file(lead_store, session):
    """When the bot discussed the course the student already chose, re-affirm."""
    lead, writes = lead_store
    lead["program_interest"] = "M.Tech"
    session.holder["turns"] = [
        "User: i want to take admission in m.tech\n"
        "Assistant: Great! To process your admission for M.Tech, please upload "
        "the following documents..."
    ]
    reply = send("Yes")

    assert "confirmed for M.Tech" in reply
    assert not writes, "re-affirming must not rewrite the row"


# ── A mention of a course is not a choice of one ────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "is mba good",
        "mba or mca",
        "mba worth it",
        "mtech hard",
    ],
)
def test_a_mention_does_not_change_the_course(lead_store, session, text):
    """
    These name a program without choosing one. Only the student choosing a
    course may move the stored program.
    """
    lead, _ = lead_store
    send(text)
    assert lead["program_interest"] == "MBA", f"{text!r} must not move the course"


def test_the_student_can_still_change_the_course_during_the_chat(lead_store, session):
    """The flip side: an explicit choice must still switch it, mid-chat."""
    lead, _ = lead_store

    send("I want to take admission in B.Tech Computer Science")
    assert lead["program_interest"] == "B.Tech Computer Science"

    # ...and again, later in the same conversation.
    send("I want to take admission in M. Tech")
    assert lead["program_interest"] == "M.Tech"

    # ...and by answering the "which program?" prompt.
    send("change")
    send("program")
    send("BCA")
    assert lead["program_interest"] == "BCA"


# ── The "what would you like to update?" dead end ───────────────────────

def test_update_field_reply_is_consumed(lead_store, session):
    """
    The prompt promised a choice and no branch read the answer, so it fell
    through to the knowledge base.
    """
    lead, _ = lead_store
    send("no")
    assert session.awaiting == "update_field"

    reply = send("program")
    assert session.awaiting == "program"
    assert "which program" in reply.lower()

    reply = send("M.Tech")
    assert lead["program_interest"] == "M.Tech"
    assert session.awaiting == ""


def test_greeting_uses_the_known_name(lead_store, session):
    """The bot used to ask for a name it already had — and could not accept."""
    reply = send("hi")
    assert "Guruji" in reply
    assert "your name" not in reply.lower()


# ── A short program name is not a person's name ─────────────────────────

def test_a_program_name_is_not_captured_as_the_name(lead_store, session):
    """A new lead whose first message was "M.Tech" got that as their name."""
    lead, _ = lead_store
    lead["name"] = ""
    lead["email"] = ""

    send("M.Tech")

    assert lead["name"] != "M.Tech", "the program must not become the name"


@pytest.mark.parametrize(
    "text",
    ["this is urgent", "call me tomorrow", "call me back later", "i am interested"],
)
def test_ordinary_sentences_do_not_rename_the_student(lead_store, session, text):
    lead, _ = lead_store
    send(text)
    assert lead["name"] == "Guruji", f"{text!r} must not be read as a name"
