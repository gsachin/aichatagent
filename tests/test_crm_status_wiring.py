"""
The places that decide what the CRM gets told.

Three of them had no coverage before this: the offer-response handler (which
records the student's answer), the course sync (which is the only way a *changed*
program ever reaches Salesforce), and the replay branch for the wider profile
write.
"""

from __future__ import annotations

import httpx
import pytest

from app.crm import sync as sync_mod
from app.crm.client import CrmClient, CrmPermanentError

USER = "0o6T100000000cjIAA"
LEAD = "lead-1"


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True, CRM_MAX_RETRIES=0)


@pytest.fixture
def client(monkeypatch):
    """A recording client, patched at every module that calls get_client()."""
    sent: list[dict] = []
    state = {"response": httpx.Response(204)}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        sent.append({"path": request.url.path, "body": json.loads(request.content or b"{}")})
        return state["response"]

    c = CrmClient("http://crm.test", transport=httpx.MockTransport(handler), max_retries=0)
    for target in ("app.crm.sync.get_client", "app.crm.status.get_client",
                   "app.crm.client.get_client"):
        monkeypatch.setattr(target, lambda c=c: c)
    return sent, state


@pytest.fixture
def lead_store(monkeypatch):
    """The lead columns the course and conversation syncs read and write."""
    store = {"crm_course": "", "crm_conversation_id": ""}
    calls = {"sets": 0}

    async def get_lead_crm_course(lead_id):
        return store["crm_course"]

    async def set_lead_crm_course(lead_id, course):
        calls["sets"] += 1
        store["crm_course"] = course or ""
        return True

    async def get_lead_crm_conversation_id(lead_id):
        return store["crm_conversation_id"]

    async def set_lead_crm_conversation_id(lead_id, conversation_id):
        store["crm_conversation_id"] = conversation_id or ""
        return True

    async def get_lead_crm_user_id(lead_id):
        return USER

    monkeypatch.setattr("app.leads.models.get_lead_crm_course", get_lead_crm_course)
    monkeypatch.setattr("app.leads.models.set_lead_crm_course", set_lead_crm_course)
    monkeypatch.setattr(
        "app.leads.models.get_lead_crm_conversation_id", get_lead_crm_conversation_id
    )
    monkeypatch.setattr(
        "app.leads.models.set_lead_crm_conversation_id", set_lead_crm_conversation_id
    )
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_lead_crm_user_id)
    return store, calls


# ── course: the fact that could never change ─────────────────────────────────

@pytest.mark.anyio
async def test_a_changed_program_is_published(crm_on, client, lead_store):
    """
    Program interest used to reach the CRM only inside lookup-or-create — at
    create time and never again — so a student who switched to MBA stayed on
    their first answer forever.
    """
    sent, _ = client
    store, _ = lead_store
    store["crm_course"] = "Computer Science"

    assert await sync_mod.sync_course(LEAD, USER, "MBA") is True
    assert sent[0]["path"] == f"/admissions/{USER}"
    assert sent[0]["body"] == {"Course__c": "MBA"}


@pytest.mark.anyio
async def test_an_unchanged_program_costs_no_request(crm_on, client, lead_store):
    """This runs on every turn of every channel; the common case must be free."""
    sent, _ = client
    store, _ = lead_store
    store["crm_course"] = "MBA"

    assert await sync_mod.sync_course(LEAD, USER, "MBA") is False
    assert sent == []


@pytest.mark.anyio
async def test_a_failed_push_is_not_cached(crm_on, client, lead_store):
    """Caching a failure would mean never retrying it."""
    _, state = client
    store, calls = lead_store
    state["response"] = httpx.Response(500, json={"detail": "crm down"})

    assert await sync_mod.sync_course(LEAD, USER, "MBA") is False
    assert calls["sets"] == 0, "the cache must only record what the CRM accepted"


@pytest.mark.anyio
async def test_the_pushed_course_is_remembered(crm_on, client, lead_store):
    store, calls = lead_store

    await sync_mod.sync_course(LEAD, USER, "MBA")

    assert store["crm_course"] == "MBA"
    assert calls["sets"] == 1


@pytest.mark.anyio
async def test_no_course_and_no_link_are_both_no_ops(crm_on, client, lead_store):
    sent, _ = client

    assert await sync_mod.sync_course(LEAD, USER, "") is False
    assert await sync_mod.sync_course(LEAD, "", "MBA") is False
    assert sent == []


# ── conversation id: the fact the CRM could only ever learn once ─────────────

@pytest.mark.anyio
async def test_a_new_conversation_repoints_the_crm(crm_on, client, lead_store):
    """
    ``lookup-or-create`` writes ``Conversation_ID__c`` at create time and never
    again, so a repeat caller's row names their first conversation forever.
    """
    sent, _ = client
    store, _ = lead_store
    store["crm_conversation_id"] = "conv-1"

    assert await sync_mod.sync_conversation_id(LEAD, USER, "conv-2") is True
    assert sent[0]["path"] == f"/admissions/{USER}"
    assert sent[0]["body"] == {"Conversation_ID__c": "conv-2"}
    assert store["crm_conversation_id"] == "conv-2"


@pytest.mark.anyio
async def test_the_same_conversation_costs_no_request(crm_on, client, lead_store):
    """
    This is offered on every turn, so a conversation the CRM already points at
    must not become a per-turn write.
    """
    sent, _ = client
    store, _ = lead_store
    store["crm_conversation_id"] = "conv-2"

    assert await sync_mod.sync_conversation_id(LEAD, USER, "conv-2") is False
    assert sent == []


@pytest.mark.anyio
async def test_a_failed_conversation_push_is_not_cached(crm_on, client, lead_store):
    """Caching a failure would mean the CRM never learns the new conversation."""
    _, state = client
    store, _ = lead_store
    store["crm_conversation_id"] = "conv-1"
    state["response"] = httpx.Response(500, json={"detail": "crm down"})

    assert await sync_mod.sync_conversation_id(LEAD, USER, "conv-2") is False
    assert store["crm_conversation_id"] == "conv-1", "still points at the old one"


@pytest.mark.anyio
async def test_no_conversation_and_no_link_are_both_no_ops(crm_on, client, lead_store):
    """A chat with no session id must not blank a link the CRM already holds."""
    sent, _ = client

    assert await sync_mod.sync_conversation_id(LEAD, USER, "") is False
    assert await sync_mod.sync_conversation_id(LEAD, "", "conv-2") is False
    assert sent == []


@pytest.mark.anyio
async def test_an_already_linked_lead_still_publishes_a_changed_program(
    crm_on, client, lead_store
):
    """
    The regression that the end-to-end run found.

    From the second turn onward `link_conversation` returns early on the cached
    link — and that early return is the *only* path a returning student takes,
    so a course sync placed after the lookup would never run for the one case it
    exists to fix: a student changing their mind.

    The conversation id rides the same branch for the same reason: the CRM holds
    whichever conversation was current when the record was created, so a repeat
    caller needs this early return to repoint it (plan R6/A4). Two writes, in
    this order — course first, so the request sequence stays stable.
    """
    sent, _ = client
    store, _ = lead_store
    store["crm_course"] = "Computer Science"

    result = await sync_mod.link_conversation(
        channel="chat",
        conversation_id="conv-2",
        lead={"id": LEAD, "crm_user_id": USER, "program_interest": "MBA"},
        course="MBA",
    )

    assert result == USER
    assert len(sent) == 2, "the changed program and the conversation must be published"
    assert sent[0]["body"] == {"Course__c": "MBA"}
    assert sent[1]["body"] == {"Conversation_ID__c": "conv-2"}
    assert store["crm_conversation_id"] == "conv-2"


# ── replaying the wider profile write ────────────────────────────────────────

@pytest.mark.anyio
async def test_a_queued_profile_write_replays_to_the_admissions_route(crm_on, client):
    """
    The two routes have opposite null semantics, so a replay has to know which
    one it is replaying to.
    """
    sent, _ = client

    ok = await sync_mod.replay({
        "op": "profile",
        "crm_user_id": USER,
        "payload": {"Course__c": "MBA", "Offer_Status__c": "Offered"},
    })

    assert ok is True
    assert sent[0]["path"] == f"/admissions/{USER}"
    assert sent[0]["body"] == {"Course__c": "MBA", "Offer_Status__c": "Offered"}


@pytest.mark.anyio
async def test_replay_still_handles_the_original_status_op(crm_on, client):
    sent, _ = client

    await sync_mod.replay({
        "op": "status", "crm_user_id": USER, "payload": {"sentiment": "HOT"},
    })

    assert sent[0]["path"] == f"/users/{USER}/status"


@pytest.mark.anyio
async def test_an_unknown_op_is_dropped_not_retried(crm_on, client):
    with pytest.raises(CrmPermanentError):
        await sync_mod.replay({"op": "something-else", "crm_user_id": USER,
                               "payload": {"x": 1}})


@pytest.mark.anyio
async def test_a_profile_entry_with_no_target_is_dropped(crm_on, client):
    with pytest.raises(CrmPermanentError):
        await sync_mod.replay({"op": "profile", "crm_user_id": "", "payload": {"x": 1}})


# ── the student's answer ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_an_accepted_offer_is_published(monkeypatch):
    """The path that records what the student actually decided."""
    from app import main as m

    published: list[tuple[str, str]] = []

    async def fake_recent(lead_id, within_hours=720):
        return {"id": "offer-1", "status": "sent", "lead_id": lead_id, "program": "MBA"}

    async def fake_update(offer_id, status, **kwargs):
        return {"id": offer_id, "lead_id": LEAD, "status": status, "program": "MBA"}

    async def fake_push(crm_user_id, response):
        published.append((crm_user_id, response))
        return True

    async def link(lead_id):
        return USER

    monkeypatch.setattr("app.offers.models.get_recent_offer_for_lead", fake_recent)
    monkeypatch.setattr("app.offers.models.update_offer_letter_status", fake_update)
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", link)
    monkeypatch.setattr("app.crm.status.push_offer_response", fake_push)

    result = await m._handle_offer_response(LEAD, "accepted")

    assert result is not None
    assert published == [(USER, "accepted")], (
        "the CRM must learn about the decision on the same turn the student makes it"
    )


@pytest.mark.anyio
async def test_the_published_category_comes_from_a_query_that_selects_it(monkeypatch):
    """
    The regression the live run caught.

    ``get_lead`` does not select the sentiment columns, so an implementation that
    reads `current_category` from a lead dict pushes nothing and says nothing —
    it looks identical to "no sentiment yet". This asserts the publisher uses the
    dedicated accessor by making the lead dict deliberately unhelpful.
    """
    from app.leads import service as lead_service

    pushed: list[tuple[str, str]] = []

    async def get_lead_without_sentiment(lead_id):
        return {"id": lead_id, "name": "Ana"}  # no current_category — as get_lead returns

    async def get_category(lead_id):
        return "Warm"

    async def get_user(lead_id):
        return USER

    async def fake_push(crm_user_id, category):
        pushed.append((crm_user_id, category))
        return True

    monkeypatch.setattr("app.leads.models.get_lead", get_lead_without_sentiment)
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_user)
    monkeypatch.setattr("app.leads.models.get_lead_sentiment_category", get_category)
    monkeypatch.setattr("app.crm.status.push_sentiment", fake_push)

    await lead_service._publish_sentiment(LEAD, "")

    assert pushed == [(USER, "Warm")], (
        "the category must be read from the column that holds it, not from a lead dict"
    )


@pytest.mark.anyio
async def test_no_category_yet_publishes_nothing(monkeypatch):
    from app.leads import service as lead_service

    pushed = []

    async def no_category(lead_id):
        return ""

    async def get_user(lead_id):
        return USER

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_user)
    monkeypatch.setattr("app.leads.models.get_lead_sentiment_category", no_category)
    monkeypatch.setattr("app.crm.status.push_sentiment",
                        lambda *a, **k: pushed.append(a))

    await lead_service._publish_sentiment(LEAD, "")

    assert pushed == []


# ── the post-call publish: how a voice call reaches the CRM at all ───────────
#
# A voice call never goes through link_conversation: mid-call there is no lead
# row to write against, and the linker will not link until the caller has given
# a name. So this helper is the only thing that repoints the CRM for a repeat
# caller — which is the reported failure.

@pytest.mark.anyio
async def test_the_program_published_is_the_one_just_discussed(monkeypatch):
    """
    A lead whose stored program is MBA who spends the call asking about M.Tech.

    ``log_interaction`` only *back-fills* ``program_interest``, so a publish that
    read the stored column would re-send MBA forever — the exact symptom.
    """
    from app.leads import service as lead_service

    published = []

    async def fake_upsert(phone_number, **kwargs):
        return {"id": LEAD, "name": "Sachin", "email": "", "program_interest": "MBA"}

    async def fake_create_conversation(**kwargs):
        return {"id": "conv-row", **kwargs}

    async def fake_publish(lead_id, crm_user_id="", *, course="", conversation_id=""):
        published.append((lead_id, crm_user_id, course, conversation_id))

    monkeypatch.setattr("app.leads.models.upsert_lead_by_phone", fake_upsert)
    monkeypatch.setattr("app.leads.models.create_conversation", fake_create_conversation)
    monkeypatch.setattr("app.leads.models.update_lead", fake_create_conversation)
    monkeypatch.setattr("app.leads.service._publish_profile_facts", fake_publish)

    await lead_service.log_interaction(
        phone_number="+17208402670",
        channel="inbound_call",
        # Short enough to skip the sentiment block, so this test stays about the
        # publish and does not need the scorer mocked.
        transcript="Caller: engineering?",
        extracted_lead={"program": "M.Tech"},
        conversation_id="conv-2",
        crm_user_id=USER,
    )

    assert published == [(LEAD, USER, "M.Tech", "conv-2")]


@pytest.mark.anyio
async def test_a_call_that_says_nothing_new_reuses_the_stored_program(monkeypatch):
    """No program in this call means the lead's own answer, not a blank push."""
    from app.leads import service as lead_service

    published = []

    async def fake_upsert(phone_number, **kwargs):
        return {"id": LEAD, "name": "Sachin", "email": "", "program_interest": "MBA"}

    async def fake_create_conversation(**kwargs):
        return {"id": "conv-row", **kwargs}

    async def fake_publish(lead_id, crm_user_id="", *, course="", conversation_id=""):
        published.append((course, conversation_id))

    monkeypatch.setattr("app.leads.models.upsert_lead_by_phone", fake_upsert)
    monkeypatch.setattr("app.leads.models.create_conversation", fake_create_conversation)
    monkeypatch.setattr("app.leads.models.update_lead", fake_create_conversation)
    monkeypatch.setattr("app.leads.service._publish_profile_facts", fake_publish)

    await lead_service.log_interaction(
        phone_number="+17208402670",
        channel="inbound_call",
        transcript="Caller: hello",
        extracted_lead={},
        conversation_id="conv-3",
        crm_user_id=USER,
    )

    assert published == [("MBA", "conv-3")]


@pytest.mark.anyio
async def test_a_lead_with_no_crm_link_publishes_nothing(monkeypatch):
    """Nothing to address the write to — and no reason to look one up twice."""
    from app.leads import service as lead_service

    called = []

    async def no_link(lead_id):
        return ""

    async def record(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", no_link)
    monkeypatch.setattr("app.crm.sync.sync_course", record)
    monkeypatch.setattr("app.crm.sync.sync_conversation_id", record)

    await lead_service._publish_profile_facts(
        LEAD, "", course="M.Tech", conversation_id="conv-2"
    )

    assert called == []


@pytest.mark.anyio
async def test_the_post_call_publish_writes_both_facts(monkeypatch):
    """One resolution of the CRM link, then both writes, course first."""
    from app.leads import service as lead_service

    written = []

    async def record(lead_id, crm_user_id, value):
        written.append((lead_id, crm_user_id, value))

    monkeypatch.setattr("app.crm.sync.sync_course", record)
    monkeypatch.setattr("app.crm.sync.sync_conversation_id", record)

    await lead_service._publish_profile_facts(
        LEAD, USER, course="M.Tech", conversation_id="conv-2"
    )

    assert written == [(LEAD, USER, "M.Tech"), (LEAD, USER, "conv-2")]


@pytest.mark.anyio
async def test_the_post_call_publish_never_raises(monkeypatch):
    """It runs after the conversation row is written; nothing may undo that."""
    from app.leads import service as lead_service

    async def boom(*args, **kwargs):
        raise RuntimeError("crm exploded")

    monkeypatch.setattr("app.crm.sync.sync_course", boom)

    await lead_service._publish_profile_facts(
        LEAD, USER, course="M.Tech", conversation_id="conv-2"
    )


@pytest.mark.anyio
async def test_a_lead_with_no_crm_link_is_skipped_quietly(monkeypatch):
    from app import main as m

    async def fake_recent(lead_id, within_hours=720):
        return {"id": "offer-1", "status": "sent", "lead_id": lead_id}

    async def fake_update(offer_id, status, **kwargs):
        return {"id": offer_id, "lead_id": LEAD, "status": status}

    async def no_link(lead_id):
        return ""

    published = []
    monkeypatch.setattr("app.offers.models.get_recent_offer_for_lead", fake_recent)
    monkeypatch.setattr("app.offers.models.update_offer_letter_status", fake_update)
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", no_link)
    monkeypatch.setattr("app.crm.status.push_offer_response",
                        lambda *a, **k: published.append(a))

    result = await m._handle_offer_response(LEAD, "accepted")

    assert result is not None, "the local update still happens"
    assert published == [], "but there is nobody in the CRM to tell"


@pytest.mark.anyio
async def test_a_crm_failure_does_not_break_the_student_reply(monkeypatch):
    """The student's reply is a local fact first; the CRM is a side effect."""
    from app import main as m

    async def fake_recent(lead_id, within_hours=720):
        return {"id": "offer-1", "status": "sent", "lead_id": lead_id}

    async def fake_update(offer_id, status, **kwargs):
        return {"id": offer_id, "lead_id": LEAD, "status": status}

    async def boom(crm_user_id, response):
        raise RuntimeError("crm exploded")

    async def link(lead_id):
        return USER

    monkeypatch.setattr("app.offers.models.get_recent_offer_for_lead", fake_recent)
    monkeypatch.setattr("app.offers.models.update_offer_letter_status", fake_update)
    # Patch the link too, so the test actually reaches the failing push rather
    # than passing because it never got there.
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", link)
    monkeypatch.setattr("app.crm.status.push_offer_response", boom)

    assert await m._handle_offer_response(LEAD, "accepted") is not None
