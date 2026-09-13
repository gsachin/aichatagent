"""
Web chat → CRM (Phase 6).

The chat runs in a Streamlit process that has no database and no CRM client, so
the link happens in the FastAPI handlers that its lead writes already go
through — backgrounded, because Streamlit's HTTP timeout is shorter than a cold
CRM call with retries, and all the chat needs back is the lead id.

What matters here: a first-time chatter with name + email/phone and a
conversation id gets linked exactly once, a returning one does not re-link, and
a lead with no conversation (the dashboard's add-lead form, the manual API) is
declined rather than given an invented one.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.crm.client import CrmClient

BODY = {
    "phone_number": "+917757057985",
    "name": "Pradeep",
    "email": "pradeepdubey@test.com",
    "program_interest": "MBA",
    "conversation_id": "chat-conv-1",
}
LEAD = {"id": "lead-1", **{k: v for k, v in BODY.items() if k != "conversation_id"}}


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True, CRM_MAX_RETRIES=0)


@pytest.fixture
def crm_transport(monkeypatch):
    """Records every CRM request and replays a scripted response."""
    sent: list[dict] = []
    state = {"response": httpx.Response(200, json={"userId": "0o6LINKED"})}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"method": request.method, "url": str(request.url),
                     "body": _json(request)})
        return state["response"]

    client = CrmClient(
        "http://crm.test", transport=httpx.MockTransport(handler), max_retries=0
    )
    monkeypatch.setattr("app.crm.sync.get_client", lambda: client)
    return sent, state


@pytest.fixture
def lead_store(monkeypatch):
    """The only two database calls the link path makes."""
    store = {"crm_user_id": ""}
    calls = {"get": 0, "set": 0}

    async def get_lead_crm_user_id(lead_id):
        calls["get"] += 1
        return store["crm_user_id"]

    async def set_lead_crm_user_id(lead_id, crm_user_id):
        calls["set"] += 1
        store["crm_user_id"] = crm_user_id
        return True

    async def get_lead(lead_id):
        return dict(LEAD)

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_lead_crm_user_id)
    monkeypatch.setattr("app.leads.models.set_lead_crm_user_id", set_lead_crm_user_id)
    monkeypatch.setattr("app.leads.models.get_lead", get_lead)
    return store, calls


def _json(request: httpx.Request) -> dict:
    try:
        import json
        return json.loads(request.content)
    except Exception:
        return {}


def run_link(coro_coro):
    """Run the backgrounded link to completion the way BackgroundTasks would."""
    return asyncio.run(coro_coro)


# ── the link itself ──────────────────────────────────────────────────────────

def test_a_web_chatter_is_linked_once(crm_on, crm_transport, lead_store):
    from app.main import _link_chat_lead

    sent, _ = crm_transport
    store, calls = lead_store

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="Pradeep",
        email="pradeepdubey@test.com", course="MBA",
    ))

    assert len(sent) == 1, "exactly one CRM call for the first link"
    assert sent[0]["url"].endswith("/users/lookup-or-create")
    body = sent[0]["body"]
    assert body["name"] == "Pradeep"
    assert body["email"] == "pradeepdubey@test.com"
    assert body["phoneNumber"] == "+917757057985", "normalised to bare E.164"
    assert body["conversationId"] == "chat-conv-1", "the chat's id names the conversation"
    assert body["course"] == "MBA"
    assert store["crm_user_id"] == "0o6LINKED", "persisted on the lead for later turns"


def test_a_second_turn_does_not_re_link(crm_on, crm_transport, lead_store):
    """The chat PUTs on every program mention; only the first should reach out."""
    from app.main import _link_chat_lead

    sent, _ = crm_transport
    store, _ = lead_store
    store["crm_user_id"] = "0o6LINKED"  # already linked

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="Pradeep",
        email="pradeepdubey@test.com", course="MBA",
    ))

    assert sent == [], "a linked lead must not touch the network again"


def test_a_chat_without_a_conversation_id_is_declined(crm_on, crm_transport, lead_store):
    """
    The dashboard's add-lead form posts to the same endpoint with no conversation.
    Salesforce needs an id to name the conversation, and inventing one would be
    worse than declining — there is no way to correct it afterwards.
    """
    from app.main import _link_chat_lead

    sent, _ = crm_transport

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="",
        phone_number="+917757057985", name="Pradeep",
        email="pradeepdubey@test.com", course="MBA",
    ))

    assert sent == []


def test_a_chat_without_a_name_anywhere_is_declined(crm_on, crm_transport, lead_store, monkeypatch):
    """
    R17: an empty name makes the API raise, so it must not be sent. Note the
    fallback is deliberate — a request that omits the name still links if the
    lead row already has one, since it is the same person's data.
    """
    from app.main import _link_chat_lead

    sent, _ = crm_transport
    monkeypatch.setattr("app.leads.models.get_lead", lambda lead_id: _nameless())

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="",
        email="pradeepdubey@test.com", course="MBA",
    ))

    assert sent == []


def test_a_missing_name_falls_back_to_the_lead_row(crm_on, crm_transport, lead_store):
    """The lead row is the better source when the request omits a field."""
    from app.main import _link_chat_lead

    sent, _ = crm_transport

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="",
        email="", course="",
    ))

    assert len(sent) == 1
    assert sent[0]["body"]["name"] == "Pradeep"
    assert sent[0]["body"]["email"] == "pradeepdubey@test.com"


async def _nameless():
    return {"id": "lead-1", "phone_number": "+917757057985", "name": "", "email": ""}


@pytest.mark.anyio
async def test_a_crm_failure_never_reaches_the_chat(crm_on, crm_transport, lead_store):
    from app.main import _link_chat_lead

    sent, state = crm_transport
    state["response"] = httpx.Response(500, json={"detail": "crm on fire"})

    await _link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="Pradeep",
        email="pradeepdubey@test.com", course="MBA",
    )  # must not raise: the chat already got its answer


def test_nothing_is_scheduled_when_the_crm_is_off(crm_transport, lead_store, override_settings):
    from app.main import _link_chat_lead

    override_settings(CRM_ENABLED=False)
    sent, _ = crm_transport

    run_link(_link_chat_lead(
        lead_id="lead-1", conversation_id="chat-conv-1",
        phone_number="+917757057985", name="Pradeep",
        email="pradeepdubey@test.com", course="MBA",
    ))

    assert sent == []


# ── the scheduling wrapper ───────────────────────────────────────────────────

def test_the_link_is_scheduled_in_the_background_not_awaited(crm_on, monkeypatch):
    """
    The endpoint must return the lead immediately: Streamlit allows this request
    10 seconds, and a cold CRM call with retries can outlast that.
    """
    from app import main as m

    seen = []

    async def fake_link(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(m, "_link_chat_lead", fake_link)

    class FakeBackground:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, **kwargs):
            self.tasks.append((fn, kwargs))

    bg = FakeBackground()
    m._schedule_chat_crm_link(bg, dict(BODY), dict(LEAD))

    assert len(bg.tasks) == 1, "queued, not run inline"
    fn, kwargs = bg.tasks[0]
    assert fn is fake_link
    assert kwargs["conversation_id"] == "chat-conv-1"
    assert kwargs["lead_id"] == "lead-1"
    assert seen == [], "scheduling must not execute the link"


def test_a_lead_without_an_id_is_not_scheduled(crm_on, monkeypatch):
    from app import main as m

    class FakeBackground:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, **kwargs):
            self.tasks.append((fn, kwargs))

    bg = FakeBackground()
    m._schedule_chat_crm_link(bg, dict(BODY), {})  # no id came back
    assert bg.tasks == []


def test_scheduling_never_raises(crm_on, monkeypatch):
    """A broken scheduler must not fail the lead write that already succeeded."""
    from app import main as m

    class Exploding:
        def add_task(self, *a, **k):
            raise RuntimeError("no background tasks here")

    m._schedule_chat_crm_link(Exploding(), dict(BODY), dict(LEAD))
