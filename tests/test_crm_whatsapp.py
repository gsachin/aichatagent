"""
Tests for the WhatsApp channel link (Phase 3).

WhatsApp is the first channel wired to the CRM, and the first place a real
lookup-or-create is intended to happen. Gate 3 is:

    a WhatsApp thread creates exactly ONE Customer row with the right
    Conversation_ID__c; a second thread from the same number reuses it and does
    not duplicate.

These tests exercise ``sync.link_conversation`` — the shared entry point every
channel calls once per turn — against a scripted transport and a stubbed lead
store, plus a simulated multi-message thread.

The lead store is stubbed because ``get_lead_crm_user_id`` /
``set_lead_crm_user_id`` are the only database calls in the path; everything
else here is real.
"""

from __future__ import annotations

import httpx
import pytest

from app.crm import sync
from app.crm.client import CrmClient


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True)


@pytest.fixture
def lead_store(monkeypatch):
    """A dict-backed stand-in for the leads table's CRM columns."""
    store: dict[str, str] = {}
    calls = {"get": 0, "set": 0}

    async def get_lead_crm_user_id(lead_id):
        calls["get"] += 1
        return store.get(lead_id, "")

    async def set_lead_crm_user_id(lead_id, crm_user_id):
        calls["set"] += 1
        store[lead_id] = crm_user_id
        return True

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_lead_crm_user_id)
    monkeypatch.setattr("app.leads.models.set_lead_crm_user_id", set_lead_crm_user_id)
    return store, calls


@pytest.fixture
def crm_transport(monkeypatch):
    """Install a scripted client and record every request body sent."""
    sent: list[dict] = []

    def install(responses, **kwargs):
        state = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append({"url": str(request.url), "body": _json(request)})
            idx = min(state["n"], len(responses) - 1)
            state["n"] += 1
            return responses[idx]

        client = CrmClient(
            "http://crm.test", transport=httpx.MockTransport(handler),
            max_retries=kwargs.get("max_retries", 0),
        )
        monkeypatch.setattr("app.crm.sync.get_client", lambda: client)
        return state

    return install, sent


def _json(request: httpx.Request) -> dict:
    import json

    try:
        return json.loads(request.content)
    except Exception:
        return {}


def found(user_id: str) -> httpx.Response:
    return httpx.Response(200, json={"userId": user_id})


# ── the link, step by step ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_first_message_cannot_link_and_says_so(crm_on, lead_store, crm_transport):
    """
    A brand-new WhatsApp student has a phone number and nothing else. The API
    needs a name (R17), so the first message must decline — without a request.
    """
    store, _ = lead_store
    install, sent = crm_transport
    install([found("a0X1")])

    user_id = await sync.link_conversation(
        channel="whatsapp",
        conversation_id="conv-1",
        lead={"id": "lead-1"},
        phone_number="whatsapp:+14155550100",
    )

    assert user_id is None
    assert sent == [], "no name means no request"
    assert store == {}, "and nothing is linked"


@pytest.mark.anyio
async def test_second_message_links_once_the_name_arrives(crm_on, lead_store, crm_transport):
    store, _ = lead_store
    install, sent = crm_transport
    install([found("a0X1")])

    user_id = await sync.link_conversation(
        channel="whatsapp",
        conversation_id="conv-1",
        lead={"id": "lead-1"},
        phone_number="whatsapp:+14155550100",
        name="Ana",
    )

    assert user_id == "a0X1"
    assert len(sent) == 1
    body = sent[0]["body"]
    # The whatsapp: prefix must be gone and the name present (R17).
    assert body["phoneNumber"] == "+14155550100"
    assert body["name"] == "Ana"
    assert body["conversationId"] == "conv-1"
    assert store["lead-1"] == "a0X1"


@pytest.mark.anyio
async def test_later_messages_reuse_the_link_without_calling_out(crm_on, lead_store, crm_transport):
    """
    The cache is what keeps a chatty thread from re-asking the API on every
    message — and, because the API's find-then-create has no lock (R5), from
    re-racing it too.
    """
    store, _ = lead_store
    install, sent = crm_transport
    install([found("a0X1")])

    common = dict(channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"})

    first = await sync.link_conversation(phone_number="+14155550100", name="Ana", **common)
    # Store now holds the link, as a real set_lead_crm_user_id would have left it.
    for _ in range(4):
        again = await sync.link_conversation(phone_number="+14155550100", name="Ana", **common)
        assert again == first

    assert len(sent) == 1, "only the first turn should have hit the CRM"


@pytest.mark.anyio
async def test_phone_only_identity_waits_rather_than_inventing_a_name(crm_on, lead_store, crm_transport):
    """
    A placeholder name is worse than waiting: the status PATCH can only write
    three unrelated fields, so a fabricated name could never be corrected.
    """
    install, sent = crm_transport
    install([found("a0X1")])

    await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1",
        lead={"id": "lead-1"}, phone_number="+14155550100",
    )
    assert sent == []


@pytest.mark.anyio
async def test_explicit_arguments_win_over_a_stale_lead_row(crm_on, lead_store, crm_transport):
    """
    The webhook fetches the lead *before* the state machine updates it, so the
    dict it holds is stale. The freshly-parsed values must take precedence.
    """
    store, _ = lead_store
    install, sent = crm_transport
    install([found("a0X1")])

    await sync.link_conversation(
        channel="whatsapp",
        conversation_id="conv-1",
        lead={"id": "lead-1", "name": "", "email": "", "program_interest": ""},
        phone_number="+14155550100",
        name="Ana",
        email="ana@example.com",
        course="MBA",
    )

    body = sent[0]["body"]
    assert body["name"] == "Ana"
    assert body["email"] == "ana@example.com"
    assert body["course"] == "MBA"


@pytest.mark.anyio
async def test_live_whatsapp_session_is_updated_in_step(crm_on, lead_store, crm_transport):
    """So the rest of the conversation can read the id without another query."""
    from app.crm import session as crm_session

    crm_session.reset()
    crm_session.start("whatsapp", "+14155550100", phone_number="+14155550100")

    install, _ = crm_transport
    install([found("a0X1")])

    await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    )

    assert crm_session.get("whatsapp", "+14155550100").crm_user_id == "a0X1"
    crm_session.reset()


# ── failure handling ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_crm_failure_does_not_raise_and_stays_unlinked(crm_on, lead_store, crm_transport):
    store, _ = lead_store
    install, _ = crm_transport
    install([httpx.Response(422, json={"detail": "bad"})])

    user_id = await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    )

    assert user_id is None
    assert store == {}, "a failed link must not be cached as success"


@pytest.mark.anyio
async def test_a_dead_database_does_not_break_the_link(crm_on, monkeypatch, crm_transport):
    """Running without Postgres is a supported mode in this app."""
    async def boom(*_a, **_kw):
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", boom)
    install, sent = crm_transport
    install([found("a0X1")])

    user_id = await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    )
    assert user_id == "a0X1", "the lookup still succeeds; only caching is lost"


@pytest.mark.anyio
async def test_link_is_a_no_op_when_the_crm_is_disabled(override_settings, monkeypatch):
    override_settings(CRM_ENABLED=False)
    monkeypatch.setattr(
        "app.crm.sync.get_client",
        lambda: pytest.fail("no client may be built while the CRM is disabled"),
    )

    assert await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    ) is None


# ── Gate 3: a whole thread ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_multi_message_thread_creates_exactly_one_crm_user(crm_on, lead_store, crm_transport):
    """
    Gate 3, simulated. Walks a realistic WhatsApp thread — greeting, name,
    email, program — and asserts the API was asked to create a user exactly
    once, with the conversation id attached.
    """
    store, _ = lead_store
    install, sent = crm_transport
    install([found("a0X-CREATED")])

    lead = {"id": "lead-1", "name": "", "email": "", "program_interest": ""}
    convo = "conv-thread-1"

    # Turn 1 — the student says hello; the app has only the number.
    assert await sync.link_conversation(
        channel="whatsapp", conversation_id=convo, lead=lead,
        phone_number="whatsapp:+14155550100",
    ) is None

    # Turn 2 — they give their name. First linkable moment.
    uid = await sync.link_conversation(
        channel="whatsapp", conversation_id=convo, lead=lead,
        phone_number="whatsapp:+14155550100", name="Ana",
    )
    assert uid == "a0X-CREATED"

    # Turns 3-5 — email, then a program. Already linked; no further calls.
    for course, email in (("", "ana@example.com"), ("MBA", "ana@example.com"), ("MBA", "ana@example.com")):
        assert await sync.link_conversation(
            channel="whatsapp", conversation_id=convo, lead=lead,
            phone_number="whatsapp:+14155550100", name="Ana",
            email=email, course=course,
        ) == "a0X-CREATED"

    assert len(sent) == 1, f"expected exactly one CRM call, got {len(sent)}"
    assert sent[0]["body"]["conversationId"] == convo
    assert store["lead-1"] == "a0X-CREATED"


@pytest.mark.anyio
async def test_a_second_thread_from_the_same_number_reuses_the_user(crm_on, lead_store, crm_transport):
    """
    Gate 3's second half: a new conversation (past the idle window) must find the
    existing CRM user, not create a second one. The API's find-first behaviour is
    what delivers this — the client just has to ask again with the new
    conversation id.
    """
    store, _ = lead_store
    install, sent = crm_transport

    # First thread links the lead.
    install([found("a0X-EXISTING")])
    await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-1", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    )

    # Second thread, next week: same person, new conversation, lead row cleared
    # to simulate the link not having been cached locally.
    store.clear()
    await sync.link_conversation(
        channel="whatsapp", conversation_id="conv-2", lead={"id": "lead-1"},
        phone_number="+14155550100", name="Ana",
    )

    assert len(sent) == 2
    assert sent[0]["body"]["conversationId"] == "conv-1"
    assert sent[1]["body"]["conversationId"] == "conv-2"
    # Both calls carried the same identity, which is what makes the API's
    # existing-record lookup succeed rather than create a duplicate.
    assert sent[0]["body"]["phoneNumber"] == sent[1]["body"]["phoneNumber"] == "+14155550100"
