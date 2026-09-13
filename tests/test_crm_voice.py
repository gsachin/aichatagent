"""
Voice → CRM (Phases 4 and 5).

The caller's number is the thing this channel was missing. Twilio Media Streams
never carries it in the `start` event, so it has to be captured at the webhook
and threaded through the TwiML as a `<Parameter>` — which is why these tests
check the handoff at each hop rather than only the endpoints.

Two behaviours matter beyond the plumbing:

* the *carrier's* number wins over one the AI thinks it heard, because the
  post-call extractor does not even ask for a phone (so it always returns
  nothing, and every inbound call used to create a lead keyed on "");
* a call links mid-conversation, once, and stops trying after a few attempts.
"""

from __future__ import annotations

import httpx
import pytest

from app.crm.client import CrmClient
from app.crm.voice import MAX_ATTEMPTS, CallLinker

CALLER = "+14155550133"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ── the number crosses the webhook → TwiML → stream boundary ─────────────────

def test_the_ivr_webhook_carries_the_caller_number_into_the_twiml(client):
    # params= so the "+" is percent-encoded, exactly as Twilio sends it — a raw
    # "+" in a query string decodes to a space, which would silently turn E.164
    # into something else.
    resp = client.get("/twilio/voice", params={"From": CALLER})

    assert resp.status_code == 200
    assert '<Parameter name="phone"' in resp.text, (
        "the media stream has no other way to learn the caller's number"
    )
    assert f'value="{CALLER}"' in resp.text


def test_the_connect_webhook_carries_it_too(client):
    """Twilio re-sends the original parameters on the <Gather> action."""
    resp = client.get("/twilio/voice/connect", params={"Digits": "4", "From": CALLER})

    assert resp.status_code == 200
    assert '<Parameter name="phone"' in resp.text
    assert f'value="{CALLER}"' in resp.text
    assert "<Stream" in resp.text and "</Stream>" in resp.text


def test_a_space_mangled_number_is_refused_rather_than_guessed(client):
    """
    A raw "+" in a query string becomes a space, so an unencoded number loses its
    country code. Twilio encodes it properly — this pins what happens if anything
    upstream ever does not: the number is *declined*, not silently sent with a
    guessed country code (R2). The call simply stays unlinked, which is the
    recoverable failure.
    """
    from app.crm.identity import normalize_phone

    resp = client.get(f"/twilio/voice?From={CALLER}")  # deliberately unencoded

    assert resp.status_code == 200
    assert 'value=" 14155550133"' in resp.text, "this is what arrives unencoded"
    assert normalize_phone(" 14155550133") == "", "no country code — refuse, never guess"


def test_a_caller_number_with_xml_characters_cannot_break_the_twiml(client):
    """
    These routes are GETs, so the number is attacker-influenced input that lands
    inside an XML attribute. Unescaped, it produces TwiML Twilio rejects at call
    time — a fault that would only appear on a live call.
    """
    resp = client.get('/twilio/voice?From=+1"><Say>gotcha</Say>')

    assert resp.status_code == 200
    assert "<Say>gotcha</Say>" not in resp.text
    assert "&quot;" in resp.text or "&lt;" in resp.text


def test_no_caller_number_still_produces_valid_twiml(client):
    """A call arriving without From must not emit an empty parameter."""
    resp = client.get("/twilio/voice")

    assert resp.status_code == 200
    assert "<Parameter" not in resp.text
    assert "<Stream" in resp.text


def test_the_outbound_route_passes_the_lead_through(client):
    resp = client.get("/twilio/outbound-voice", params={"leadId": "lead-9", "phone": CALLER})

    assert resp.status_code == 200
    assert '<Parameter name="leadId" value="lead-9"' in resp.text
    assert f'value="{CALLER}"' in resp.text


def test_an_untagged_outbound_call_is_still_valid(client):
    resp = client.get("/twilio/outbound-voice")

    assert resp.status_code == 200
    assert "<Parameter" not in resp.text
    assert "ws/twilio-outbound" in resp.text


# ── the mid-call linker ──────────────────────────────────────────────────────

def make_linker(**kwargs) -> CallLinker:
    base = dict(
        channel="inbound_call",
        conversation_id="conv-1",
        phone_number=CALLER,
        session_key="MZ123",
    )
    base.update(kwargs)
    return CallLinker(**base)


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True, CRM_MAX_RETRIES=0)


@pytest.fixture
def crm_transport(monkeypatch):
    sent: list[dict] = []
    state = {"response": httpx.Response(200, json={"userId": "0o6CALLER"})}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "body": _json(request)})
        return state["response"]

    client = CrmClient("http://crm.test", transport=httpx.MockTransport(handler), max_retries=0)
    monkeypatch.setattr("app.crm.sync.get_client", lambda: client)
    return sent, state


def _json(request: httpx.Request) -> dict:
    import json
    try:
        return json.loads(request.content)
    except Exception:
        return {}


@pytest.fixture
def extractor(monkeypatch):
    """Stands in for the LLM extraction, so no model is loaded."""
    state = {"result": {"name": "Pradeep", "email": "", "program": "MBA"}, "calls": 0}

    async def fake_extract(_transcript):
        state["calls"] += 1
        return state["result"]

    monkeypatch.setattr("app.database.extract_lead_from_transcript", fake_extract)
    return state


@pytest.mark.anyio
async def test_a_call_links_once_the_caller_gives_a_name(crm_on, crm_transport, extractor):
    linker = make_linker()

    assert await linker.attempt("User: my name is Pradeep") == "0o6CALLER"
    sent, _ = crm_transport
    assert len(sent) == 1
    body = sent[0]["body"]
    assert body["name"] == "Pradeep"
    assert body["phoneNumber"] == CALLER, "the carrier number, not a guess"
    assert body["conversationId"] == "conv-1"
    assert linker.crm_user_id == "0o6CALLER"


@pytest.mark.anyio
async def test_a_name_less_turn_is_retried_and_then_gives_up(crm_on, crm_transport, extractor):
    extractor["result"] = {"name": "", "email": "", "program": ""}
    linker = make_linker()

    for _ in range(MAX_ATTEMPTS + 2):
        await linker.attempt("User: hello")
        if linker.settled:
            break

    sent, _ = crm_transport
    assert sent == [], "no CRM call without a name"
    assert extractor["calls"] == MAX_ATTEMPTS, "the extractor runs a bounded number of times"
    assert linker.settled, "a long call must not keep paying for extraction"


@pytest.mark.anyio
async def test_a_linked_call_stops_asking(crm_on, crm_transport, extractor):
    linker = make_linker()
    await linker.attempt("User: I am Pradeep")
    calls_after_link = extractor["calls"]

    await linker.attempt("User: something else entirely")

    assert extractor["calls"] == calls_after_link, "no further extraction once linked"
    assert len(crm_transport[0]) == 1


@pytest.mark.anyio
async def test_a_call_with_no_caller_number_never_tries(crm_on, crm_transport, extractor):
    """
    Nothing to match on: the CRM can search by name only as part of a create,
    and creating a record for an unknown number would be worse than waiting.
    """
    linker = make_linker(phone_number="")

    assert linker.can_try is False
    assert await linker.attempt("User: I am Pradeep") == ""
    assert crm_transport[0] == []
    assert extractor["calls"] == 0, "no point extracting when we cannot link"


@pytest.mark.anyio
async def test_an_empty_transcript_is_not_extracted(crm_on, crm_transport, extractor):
    linker = make_linker()

    await linker.attempt("   ")

    assert extractor["calls"] == 0
    assert crm_transport[0] == []


@pytest.mark.anyio
async def test_a_crm_failure_is_absorbed_and_the_call_can_retry(crm_on, crm_transport, extractor):
    linker = make_linker()
    crm_transport[1]["response"] = httpx.Response(500, json={"detail": "crm down"})

    assert await linker.attempt("User: I am Pradeep") == ""
    assert linker.crm_user_id == ""
    assert not linker.settled, "a CRM fault must not use up the call's attempts forever"


@pytest.mark.anyio
async def test_an_extraction_failure_does_not_break_the_call(crm_on, monkeypatch):
    """The extractor is an LLM call; it can fail on a live call path."""
    async def boom(_transcript):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("app.database.extract_lead_from_transcript", boom)
    linker = make_linker()

    assert await linker.attempt("User: hello") == ""


# ── the post-call handoff ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_carrier_number_reaches_the_lead_upsert(monkeypatch):
    """
    Before this, the phone came from the LLM extraction — which returns nothing
    for a phone, so inbound calls created leads keyed on "". Carrier wins.
    """
    from app import main as m

    seen: dict = {}

    async def fake_extract(_transcript):
        return {"name": "Pradeep", "email": "p@example.com", "program": "MBA"}

    async def fake_get_lead_by_phone(phone):
        seen.setdefault("looked_up", []).append(phone)
        return None

    async def fake_upsert(**kwargs):
        seen["upsert"] = kwargs
        return {"id": "lead-1", **kwargs}

    async def fake_log(**kwargs):
        seen["post_interaction"] = kwargs
        return True

    async def noop(**_kwargs):
        return None

    monkeypatch.setattr("app.database.extract_lead_from_transcript", fake_extract)
    monkeypatch.setattr("app.leads.models.get_lead_by_phone", fake_get_lead_by_phone)
    monkeypatch.setattr("app.leads.models.upsert_lead_by_phone", fake_upsert)
    monkeypatch.setattr("app.leads.models.set_lead_crm_user_id", noop)
    monkeypatch.setattr("app.leads.service.handle_post_interaction", fake_log)
    monkeypatch.setattr("app.pipeline.post_call_handler", lambda **_k: _false())
    monkeypatch.setattr("app.sentiment.scorer.score_transcript", noop)

    await m._handle_disconnect(
        ["User: hello", "Assistant: hi"],
        "conv-1",
        channel="inbound_call",
        phone_number=CALLER,
        crm_user_id="0o6CALLER",
    )

    assert seen.get("upsert", {}).get("phone_number") == CALLER
    assert seen.get("looked_up") == [CALLER], "looked up by the carrier number"
    assert seen.get("post_interaction", {}).get("crm_user_id") == "0o6CALLER", (
        "the conversation row must know which Salesforce user it belongs to"
    )


async def _false():
    return False
