"""
Pushing student state into the CRM record.

The behaviours that matter here are the ones the live API punishes:

* **Only allowlisted fields, and never a null.** ``PATCH /admissions/{id}`` dumps
  the body with ``exclude_unset=True`` and treats an explicit ``null`` as *clear
  this field* — verified live, where ``{"Conversation_ID__c": null}`` erased a
  value that was there. So an absent field must be omitted, not sent as None.
* **A record that is gone must not be retried.** The API answers 500/NOT_FOUND
  rather than 404, so without the marker a write to a deleted record would queue
  and fail on every replay.
* **The vocabularies are ours to enforce** — the API validates nothing, so an
  out-of-vocabulary value becomes a 500 at write time.
"""

from __future__ import annotations

import httpx
import pytest

from app.crm import identity as ident
from app.crm import status as status_mod
from app.crm.client import CrmClient, classify_response

USER = "0o6T100000000cjIAA"


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True, CRM_MAX_RETRIES=0)


@pytest.fixture
def transport(monkeypatch):
    """Records every request; scripted response, defaulting to success."""
    sent: list[dict] = []
    state = {"response": httpx.Response(204)}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        sent.append({
            "method": request.method,
            "path": request.url.path,
            "body": json.loads(request.content or b"{}"),
        })
        return state["response"]

    client = CrmClient("http://crm.test", transport=httpx.MockTransport(handler), max_retries=0)
    monkeypatch.setattr("app.crm.status.get_client", lambda: client)
    return sent, state


@pytest.fixture
def queued(monkeypatch):
    """Captures outbox enqueues instead of touching Postgres."""
    rows: list[dict] = []

    async def fake_enqueue(op, payload, **kwargs):
        rows.append({"op": op, "payload": payload, **kwargs})
        return True

    monkeypatch.setattr("app.crm.outbox.enqueue", fake_enqueue)
    return rows


# ── the two guards the route demands ─────────────────────────────────────────

@pytest.mark.anyio
async def test_only_allowlisted_fields_can_be_written(crm_on, transport):
    """The route accepts 35 fields; this module must accept ten."""
    sent, _ = transport

    assert await status_mod.push_profile(USER, Email__c="not-ours@example.com") is False
    assert sent == [], "nothing may reach the CRM when a field is not allowlisted"


@pytest.mark.anyio
async def test_absent_values_are_omitted_never_sent_as_null(crm_on, transport):
    """
    The difference between "leave it alone" and "erase it" on this route.
    """
    sent, _ = transport

    await status_mod.push_profile(
        USER, Course__c="MBA", Offer_Sent_At__c=None, Sentiment__c=""
    )

    assert len(sent) == 1
    assert sent[0]["body"] == {"Course__c": "MBA"}, "nulls and blanks must be dropped"
    assert "Offer_Sent_At__c" not in sent[0]["body"]
    assert "Sentiment__c" not in sent[0]["body"]


@pytest.mark.anyio
async def test_the_write_goes_to_the_admissions_route(crm_on, transport):
    """The only route that can carry course and the offer fields."""
    sent, _ = transport

    await status_mod.push_profile(USER, Course__c="MBA")

    assert sent[0]["method"] == "PATCH"
    assert sent[0]["path"] == f"/admissions/{USER}"


@pytest.mark.anyio
async def test_an_empty_payload_is_a_no_op(crm_on, transport):
    sent, _ = transport

    assert await status_mod.push_profile(USER, Course__c=None) is False
    assert sent == [], "no request when there is nothing to say"


@pytest.mark.anyio
async def test_nothing_happens_when_the_crm_is_off(transport, queued, override_settings):
    override_settings(CRM_ENABLED=False)
    sent, _ = transport

    assert await status_mod.push_profile(USER, Course__c="MBA") is False
    assert sent == [] and queued == []


# ── the conversation link (plan R6 / A4) ─────────────────────────────────────
#
# ``lookup-or-create`` writes ``Conversation_ID__c`` when it *creates* a record
# and never again, so without this route a repeat caller's CRM row names their
# first conversation forever. It is also the field the null trap was found on.

@pytest.mark.anyio
async def test_the_conversation_id_is_written_through_the_profile_route(crm_on, transport):
    sent, _ = transport

    assert await status_mod.push_conversation_id(USER, "conv-2") is True

    assert sent[0]["method"] == "PATCH"
    assert sent[0]["path"] == f"/admissions/{USER}"
    assert sent[0]["body"] == {"Conversation_ID__c": "conv-2"}


@pytest.mark.anyio
async def test_a_blank_conversation_id_is_never_sent(crm_on, transport):
    """
    Nothing at all, rather than a null: on this route a null *clears* the field,
    and the dry run proved it by erasing a live ``Conversation_ID__c``. A blank
    id must leave the CRM's existing link alone.
    """
    sent, _ = transport

    assert await status_mod.push_conversation_id(USER, "") is False
    assert await status_mod.push_conversation_id(USER, "   ") is False
    assert sent == []


@pytest.mark.anyio
async def test_pushing_a_conversation_id_keeps_the_other_fields_intact(crm_on, transport):
    """
    The regression the whole allowlist exists for: a conversation write must not
    round-trip the record, because every field it mentions it also sets.
    """
    sent, _ = transport

    await status_mod.push_conversation_id(USER, "conv-2")

    assert sent[0]["body"] == {"Conversation_ID__c": "conv-2"}
    assert "Course__c" not in sent[0]["body"]


# ── failure policy ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_transient_failure_is_queued_for_replay(crm_on, transport, queued):
    sent, state = transport
    state["response"] = httpx.Response(500, json={"detail": "salesforce is sad"})

    assert await status_mod.push_profile(USER, Course__c="MBA") is False
    assert len(queued) == 1
    assert queued[0]["op"] == "profile"
    assert queued[0]["payload"] == {"Course__c": "MBA"}
    assert queued[0]["crm_user_id"] == USER


@pytest.mark.anyio
async def test_a_picklist_rejection_is_not_queued(crm_on, transport, queued):
    """Replaying an out-of-vocabulary value fails identically, forever."""
    _, state = transport
    state["response"] = httpx.Response(500, json={
        "detail": "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST: bad value for Offer_Status__c"
    })

    assert await status_mod.push_profile(USER, Offer_Status__c="Offered") is False
    assert queued == [], "permanent failures must be dropped, not retried"


@pytest.mark.anyio
async def test_a_deleted_record_is_permanent_not_queued(crm_on, transport, queued):
    """
    The API answers a missing record with 500 'Resource Customer Not Found …
    NOT_FOUND' (verified live). Without the marker this would retry three times,
    queue, and fail on every replay.
    """
    _, state = transport
    state["response"] = httpx.Response(500, json={
        "detail": "Resource Customer Not Found. Response content: "
                  "[{'errorCode': 'NOT_FOUND', 'message': 'Provided external ID field...'}]"
    })

    assert await status_mod.push_profile(USER, Course__c="MBA") is False
    assert queued == [], "a record that no longer exists can never be updated"


def test_not_found_is_classified_permanent():
    """A unit-level check of the marker itself, independent of the caller."""
    response = httpx.Response(500, json={
        "detail": "Resource Customer Not Found. Response content: [{'errorCode': 'NOT_FOUND'}]"
    })
    from app.crm.client import CrmPermanentError

    assert isinstance(classify_response(response, is_create=False), CrmPermanentError)


# ── the vocabulary guards ────────────────────────────────────────────────────

def test_the_offer_lifecycle_maps_onto_the_org_vocabulary():
    assert ident.map_offer_status("sent") == "Offered"
    assert ident.map_offer_status("accepted") == "Accepted"
    assert ident.map_offer_status("rejected") == "Declined"
    assert ident.map_offer_status("expired") == "Expired"
    assert ident.map_offer_status("bogus") is None


def test_the_admission_status_only_covers_what_we_observe():
    assert ident.map_admission_status("under_review") == "Under Review"
    assert ident.map_admission_status("documents_pending") == "Documents Pending"
    assert ident.map_admission_status("accepted") == "Approved"
    assert ident.map_admission_status("rejected") == "Rejected"
    assert ident.map_admission_status("halfway through marking") is None


def test_the_two_category_fields_spell_at_risk_differently():
    """
    Sentiment__c uses a hyphen, Lead_Category__c a space. They are written from
    the same source, so this is the one place the difference is allowed to exist.
    """
    assert ident.map_sentiment("At-Risk") == "AT-RISK"
    assert ident.map_lead_category("At-Risk") == "AT RISK"
    assert ident.map_lead_category("Hot") == "HOT"


def test_an_emotion_is_not_a_category():
    """`primary_emotion` values must never reach either picklist (R16)."""
    assert ident.map_sentiment("excited") is None
    assert ident.map_lead_category("excited") is None


# ── the convenience wrappers ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_an_offer_release_moves_three_fields_together(crm_on, transport):
    sent, _ = transport

    await status_mod.push_offer_released(USER, sent_at="2026-09-12T23:38:26+00:00")

    body = sent[0]["body"]
    assert body["Offer_Status__c"] == "Offered"
    assert body["Offer_Sent_At__c"] == "2026-09-12T23:38:26+00:00"
    assert body["Offer_Letter_Released__c"] is True, "a required org field — always sent"
    assert body["Admission_Status__c"] == "Under Review"


@pytest.mark.anyio
async def test_accepting_moves_the_offer_and_the_admission_together(crm_on, transport):
    """
    A declined offer that still reads 'Approved' is worse than no update at all,
    which is why the three fields travel as one payload.
    """
    sent, _ = transport

    await status_mod.push_offer_response(USER, "accepted")
    assert sent[0]["body"] == {
        "Offer_Letter_Accepted__c": "ACCEPTED",
        "Offer_Status__c": "Accepted",
        "Admission_Status__c": "Approved",
    }

    await status_mod.push_offer_response(USER, "rejected")
    assert sent[1]["body"] == {
        "Offer_Letter_Accepted__c": "NOT_ACCEPTED",
        "Offer_Status__c": "Declined",
        "Admission_Status__c": "Rejected",
    }


@pytest.mark.anyio
async def test_an_unmappable_offer_response_is_refused(crm_on, transport):
    sent, _ = transport

    assert await status_mod.push_offer_response(USER, "maybe later") is False
    assert sent == []


@pytest.mark.anyio
async def test_sentiment_writes_both_category_fields(crm_on, transport):
    sent, _ = transport

    await status_mod.push_sentiment(USER, "Hot")

    assert sent[0]["body"] == {"Sentiment__c": "HOT", "Lead_Category__c": "HOT"}

    await status_mod.push_sentiment(USER, "At-Risk")
    assert sent[1]["body"] == {"Sentiment__c": "AT-RISK", "Lead_Category__c": "AT RISK"}


@pytest.mark.anyio
async def test_an_emotion_never_reaches_the_sentiment_field(crm_on, transport):
    sent, _ = transport

    assert await status_mod.push_sentiment(USER, "frustrated") is False
    assert sent == []


@pytest.mark.anyio
async def test_expiry_only_changes_the_offer_status(crm_on, transport):
    sent, _ = transport

    await status_mod.push_offer_expired(USER)

    assert sent[0]["body"] == {"Offer_Status__c": "Expired"}
