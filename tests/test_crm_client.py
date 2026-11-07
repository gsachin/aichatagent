"""
Tests for the CRM client, identity mapping and status sync (Phase 2).

Everything here runs against an ``httpx.MockTransport`` — no live API, no
database. The behaviour under test is almost entirely *failure* behaviour,
because that is where the API's contract is genuinely hard: it reports
transient faults, permanent validation errors and partial successes all as a
bare 500.

Gate 2 is checked at the end: with CRM_ENABLED=false nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.crm import identity as identity_mod
from app.crm.client import (
    CircuitBreaker,
    CrmCircuitOpen,
    CrmClient,
    CrmPermanentError,
    CrmTransientError,
    CrmUnknownStateError,
    classify_response,
)
from app.crm.identity import from_channel


# ── helpers ──────────────────────────────────────────────────────────────────

def make_client(handler, **kwargs) -> CrmClient:
    """A CrmClient whose transport is a scripted handler."""
    kwargs.setdefault("max_retries", 2)
    kwargs.setdefault("base_url", "http://crm.test")
    return CrmClient(transport=httpx.MockTransport(handler), **kwargs)


def scripted(responses: list[httpx.Response]):
    """
    Return a handler that plays back a scripted list of responses, repeating the
    last one once exhausted. Records how many calls were made.
    """
    state = {"calls": 0, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        idx = min(state["calls"], len(responses) - 1)
        state["calls"] += 1
        return responses[idx]

    return handler, state


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def err(status: int, detail: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail})


# ── R3 / R2: value sanitisation ──────────────────────────────────────────────

def test_sanitize_strips_soql_hostile_characters():
    """The API interpolates these into SOQL with no escaping (R3)."""
    assert identity_mod.sanitize("O'Brien") == "OBrien"
    assert identity_mod.sanitize('a"b\\c;d') == "abcd"


def test_sanitize_removes_control_characters_and_collapses_space():
    assert identity_mod.sanitize("Ana\x00  Maria\n\nLopez") == "Ana Maria Lopez"


def test_sanitize_truncates_to_the_column_width():
    assert len(identity_mod.sanitize("x" * 500)) == 255


def test_normalize_phone_strips_the_whatsapp_prefix():
    """Twilio WhatsApp sends 'whatsapp:+1415...' and the app stores it verbatim."""
    assert identity_mod.normalize_phone("whatsapp:+14155550100") == "+14155550100"
    assert identity_mod.normalize_phone("WhatsApp:+14155550100") == "+14155550100"


def test_normalize_phone_cleans_spacing_and_dashes():
    assert identity_mod.normalize_phone("+1 415-555-0100") == "+14155550100"


@pytest.mark.parametrize(
    "raw",
    ["", None, "not-a-number", "4155550100", "+1", "+1234567890123456789", "+1a2b3c"],
)
def test_normalize_phone_declines_anything_unusable(raw):
    """
    Returning "" is the R2 defence: a value we cannot vouch for must become
    "no phone" rather than a lookup that could match a stranger.
    """
    assert identity_mod.normalize_phone(raw) == ""


def test_normalize_email_lowercases_and_shape_checks():
    assert identity_mod.normalize_email("  Ana@Example.COM ") == "ana@example.com"
    assert identity_mod.normalize_email("nope") == ""
    assert identity_mod.normalize_email("a@b") == ""
    assert identity_mod.normalize_email(None) == ""


# ── R16: the restricted picklists ────────────────────────────────────────────

def test_every_categorizer_output_maps_into_the_picklist():
    """
    Acceptance criterion A10. The categorizer's five outputs are the only
    sanctioned source for Sentiment__c — assert each one lands in-vocabulary.
    """
    from app.sentiment.categorizer import LeadCategory

    for category in LeadCategory:
        mapped = identity_mod.map_sentiment(category.value)
        assert mapped in identity_mod.CRM_SENTIMENT_VOCAB, category.value


def test_primary_emotion_values_are_refused():
    """
    The trap this whole mapping exists for: the obvious field to send shares no
    values with the picklist, and would 500 on every call.

    The vocabulary below is app/sentiment/scorer.py:95.
    """
    for emotion in ("excited", "interested", "neutral", "skeptical",
                    "frustrated", "angry", "confused"):
        assert identity_mod.map_sentiment(emotion) is None

    assert not (set(identity_mod.SENTIMENT_MAP.values())
                & {"EXCITED", "INTERESTED", "NEUTRAL"})


def test_sentiment_mapping_is_case_insensitive_and_tolerates_lead_category_spelling():
    assert identity_mod.map_sentiment("Hot") == "HOT"
    assert identity_mod.map_sentiment("HOT") == "HOT"
    assert identity_mod.map_sentiment("hot") == "HOT"
    # Lead_Category__c spells it with a space; Sentiment__c with a hyphen.
    assert identity_mod.map_sentiment("AT RISK") == "AT-RISK"
    assert identity_mod.map_sentiment("At-Risk") == "AT-RISK"


def test_offer_accepted_mapping():
    assert identity_mod.map_offer_accepted("accepted") == "ACCEPTED"
    assert identity_mod.map_offer_accepted("rejected") == "NOT_ACCEPTED"
    assert identity_mod.map_offer_accepted("ACCEPTED") == "ACCEPTED"
    assert identity_mod.map_offer_accepted("declined") is None
    assert identity_mod.map_offer_accepted("rejected!") is None


# ── identity assembly ────────────────────────────────────────────────────────

def test_from_channel_normalises_every_field():
    ident = from_channel(
        "whatsapp",
        conversation_id="conv-1",
        phone_number="whatsapp:+1 415 555 0100",
        email="  Ana@Example.com ",
        name="O'Brien",
        course="  MBA  ",
    )
    assert ident.phone_number == "+14155550100"
    assert ident.email == "ana@example.com"
    assert ident.name == "OBrien"
    assert ident.course == "MBA"
    assert ident.is_sendable


def test_identity_without_an_identifier_is_not_sendable():
    """R2: this is the case that must never reach the network."""
    ident = from_channel("inbound_call", conversation_id="conv-1", name="Anonymous")
    assert not ident.has_identifier
    assert not ident.is_sendable


def test_identity_without_a_conversation_is_not_sendable():
    """A record created with no conversation link defeats the purpose."""
    ident = from_channel("inbound_call", phone_number="+14155550100")
    assert ident.has_identifier
    assert not ident.is_sendable


def test_lookup_payload_always_carries_the_required_keys():
    """name/email/phoneNumber are required str upstream — present, never omitted."""
    payload = from_channel("whatsapp", conversation_id="c", phone_number="+14155550100").lookup_payload()
    assert set(payload) >= {"name", "email", "phoneNumber", "conversationId"}
    assert "course" not in payload  # optional, omitted when empty


# ── the placeholder email ────────────────────────────────────────────────────

def test_a_phone_only_student_gets_a_usable_email_not_an_empty_one():
    """
    The 2026-09-20 incident: the API refuses an empty ``email`` with a 422
    (``min_length=1``) and, being a permanent failure, that is never replayed —
    so the student is never linked at all. Sending "" is not a safe default.
    """
    payload = from_channel(
        "outbound_call", conversation_id="c", phone_number="+917016872149"
    ).lookup_payload()

    assert payload["email"] == "emailnotfound+917016872149@gmail.com"
    assert payload["phoneNumber"] == "+917016872149"


def test_the_placeholder_is_unique_per_phone():
    """
    This is the reason it is not one shared address. The API resolves by email
    *first*, falling back to phone only when the email finds nothing. A shared
    ``emailnotfound@gmail.com`` would match the first placeholder record ever
    created — so the second phone-only student's lookup would return the *first*
    student's userId, and their own phone would never be consulted.
    """
    a = from_channel("outbound_call", conversation_id="c", phone_number="+917016872149")
    b = from_channel("outbound_call", conversation_id="c", phone_number="+14155550100")

    assert a.lookup_payload()["email"] != b.lookup_payload()["email"]
    assert a.lookup_payload()["email"] != "emailnotfound@gmail.com"


def test_a_real_email_is_never_replaced_by_the_placeholder():
    payload = from_channel(
        "whatsapp", conversation_id="c", phone_number="+14155550100", email="ana@example.com"
    ).lookup_payload()

    assert payload["email"] == "ana@example.com"


def test_the_literal_string_null_is_treated_as_no_email():
    """
    How the incident actually arrived: ``batch-dashboard`` wrote the string
    "null" into ``leads.email``. It fails the shape check, so it normalises to ""
    and the placeholder takes over — the fix does not need a data migration.
    """
    ident = from_channel(
        "outbound_call", conversation_id="c", phone_number="+917016872149", email="null"
    )

    assert ident.email == ""
    assert ident.lookup_payload()["email"] == "emailnotfound+917016872149@gmail.com"


def test_no_placeholder_without_a_phone_to_derive_it_from():
    """Nothing to make it unique with — leave it empty rather than share one."""
    assert identity_mod.placeholder_email("") == ""
    assert identity_mod.placeholder_email(None) == ""
    assert identity_mod.placeholder_email("not-a-number") == ""


def test_the_placeholder_does_not_change_who_this_person_is():
    """
    The placeholder is wire-only. ``has_identifier``, ``is_sendable`` and the
    single-flight key must all keep seeing the real (empty) email — otherwise an
    anonymous caller would become "sendable" on the strength of an address this
    app invented.
    """
    ident = from_channel("outbound_call", conversation_id="c", phone_number="+917016872149")

    assert ident.email == ""
    assert ident.has_identifier, "the phone is the identifier"
    assert ident.dedupe_key() == "phone:+917016872149"


def test_dedupe_key_prefers_email():
    a = from_channel("whatsapp", conversation_id="c", phone_number="+14155550100", email="a@b.com")
    b = from_channel("whatsapp", conversation_id="c", phone_number="+14155550100")
    assert a.dedupe_key() == "email:a@b.com"
    assert b.dedupe_key() == "phone:+14155550100"


# ── 500 classification: the R4 / R16 problem ─────────────────────────────────

def test_restricted_picklist_500_is_permanent_not_transient():
    """
    R16 reproduced: the API reports a bad picklist value as 500. A retry loop
    that only looks at the status code will hammer it forever.
    """
    response = httpx.Response(500, json={"detail": (
        "Malformed request. Response content: [{'message': 'Sentiment: bad value "
        "for restricted picklist field: verify-123', 'errorCode': "
        "'INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST', 'fields': ['Sentiment__c']}]"
    )})
    error = classify_response(response, is_create=False)
    assert isinstance(error, CrmPermanentError)


def test_500_on_create_is_unknown_state_not_transient():
    """R4: the row may already exist, so a blind retry duplicates it."""
    error = classify_response(httpx.Response(500, text="boom"), is_create=True)
    assert isinstance(error, CrmUnknownStateError)


def test_500_on_patch_is_transient():
    error = classify_response(httpx.Response(500, text="boom"), is_create=False)
    assert isinstance(error, CrmTransientError)


def test_404_and_422_are_permanent():
    assert isinstance(classify_response(httpx.Response(404, text="nope"), is_create=False),
                      CrmPermanentError)
    assert isinstance(classify_response(httpx.Response(422, json={"detail": []}), is_create=True),
                      CrmPermanentError)


def test_deleted_record_500_is_permanent_not_transient():
    """
    The live shape of "the record is gone" (observed 2026-09-22): the code is
    ENTITY_IS_DELETED inside a "Resource Customer Not Found" wrapper, which the
    NOT_FOUND marker does not match. Read as transient it did not just retry —
    the queue is refilled by the refusal that replaying it causes — so one
    deleted record produced 248 outbox rows and kept the breaker, shared by
    every caller, open.
    """
    response = httpx.Response(500, json={"detail": (
        "Resource Customer Not Found. Response content: [{'message': 'entity is "
        "deleted', 'errorCode': 'ENTITY_IS_DELETED', 'fields': []}]"
    )})
    assert isinstance(classify_response(response, is_create=False), CrmPermanentError)


def test_429_is_transient():
    assert isinstance(classify_response(httpx.Response(429, text="slow down"), is_create=False),
                      CrmTransientError)


def test_success_classifies_as_no_error():
    assert classify_response(httpx.Response(200, json={}), is_create=True) is None


# ── retry behaviour ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_transient_failure_is_retried_then_succeeds():
    """
    Rate limiting is the transient case that can hit a create — note this is a
    429, not a 500. A 500 on a POST is ambiguous and must not be retried (see
    test_unknown_state_raises_immediately_without_retrying).
    """
    handler, state = scripted([
        httpx.Response(429, text="slow down"),
        httpx.Response(429, text="slow down"),
        ok({"userId": "a0X1"}),
    ])
    client = make_client(handler, max_retries=3)
    result = await client.post("/users/lookup-or-create", {"a": 1})
    assert result == {"userId": "a0X1"}
    assert state["calls"] == 3


@pytest.mark.anyio
async def test_connect_failure_is_retried_then_succeeds():
    """A refused connection is unambiguously transient, on any verb."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("connection refused", request=request)
        return ok({"userId": "a0X1"})

    client = make_client(handler, max_retries=3)
    assert await client.post("/users/lookup-or-create", {"a": 1}) == {"userId": "a0X1"}
    assert attempts["n"] == 3


@pytest.mark.anyio
async def test_permanent_failure_is_not_retried():
    handler, state = scripted([err(422, "bad payload")])
    client = make_client(handler, max_retries=3)
    with pytest.raises(CrmPermanentError):
        await client.post("/users/lookup-or-create", {"a": 1})
    assert state["calls"] == 1, "a permanent error must stop immediately"


@pytest.mark.anyio
async def test_picklist_failure_is_not_retried():
    """The concrete R16 case — must cost exactly one request."""
    handler, state = scripted([err(500, "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST")])
    client = make_client(handler, max_retries=3)
    with pytest.raises(CrmPermanentError):
        await client.patch("/users/a0X1/status", {"sentiment": "NOPE"})
    assert state["calls"] == 1


@pytest.mark.anyio
async def test_unknown_state_raises_immediately_without_retrying():
    handler, state = scripted([httpx.Response(500, text="boom")])
    client = make_client(handler, max_retries=3)
    with pytest.raises(CrmUnknownStateError):
        await client.post("/users/lookup-or-create", {"a": 1})
    assert state["calls"] == 1, "the caller must decide, not the retry loop"


@pytest.mark.anyio
async def test_retries_are_bounded():
    handler, state = scripted([httpx.Response(500, text="always down")])
    client = make_client(handler, max_retries=2)
    with pytest.raises(CrmTransientError):
        await client.patch("/users/a0X1/status", {"sentiment": "HOT"})
    assert state["calls"] == 3, "1 initial attempt + 2 retries"


@pytest.mark.anyio
async def test_connect_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler, max_retries=0)
    with pytest.raises(CrmTransientError):
        await client.get("/health")


# ── circuit breaker ──────────────────────────────────────────────────────────

def test_breaker_opens_after_threshold_consecutive_failures():
    breaker = CircuitBreaker(threshold=3, reset_seconds=60)
    assert breaker.state == "closed"
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False


def test_breaker_success_resets_the_count():
    breaker = CircuitBreaker(threshold=3, reset_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == "closed", "two failures then a success is not three in a row"


def test_breaker_half_opens_after_the_reset_window():
    breaker = CircuitBreaker(threshold=2, reset_seconds=0.01)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    import time
    time.sleep(0.02)
    assert breaker.state == "half_open"
    assert breaker.allow() is True


@pytest.mark.anyio
async def test_open_breaker_refuses_requests_without_calling_out():
    handler, state = scripted([httpx.Response(500, text="down")])
    breaker = CircuitBreaker(threshold=1, reset_seconds=600)
    client = make_client(handler, max_retries=0, breaker=breaker)

    with pytest.raises(CrmTransientError):
        await client.patch("/users/a0X1/status", {"sentiment": "HOT"})

    with pytest.raises(CrmCircuitOpen):
        await client.patch("/users/a0X1/status", {"sentiment": "HOT"})

    assert state["calls"] == 1, "the second call must not reach the network"


# ── single-flight (the R5 defence) ───────────────────────────────────────────

@pytest.mark.anyio
async def test_concurrent_lookups_for_one_person_collapse_into_one_request():
    """
    Two inbound calls from the same number arriving together must not both hit
    find-then-create — the API has no lock, so that is how duplicates happen.
    """
    handler, state = scripted([ok({"userId": "a0X1"})])
    client = make_client(handler)

    async def one_lookup():
        return await client.single_flight("phone:+14155550100", lambda: client.post(
            "/users/lookup-or-create", {"a": 1}
        ))

    results = await asyncio.gather(*[one_lookup() for _ in range(5)])

    assert all(r == {"userId": "a0X1"} for r in results)
    assert state["calls"] == 1, "five concurrent callers, one request"


@pytest.mark.anyio
async def test_single_flight_releases_the_key_after_completion():
    handler, state = scripted([ok({"userId": "a0X1"})])
    client = make_client(handler)

    async def one_lookup():
        return await client.single_flight("k", lambda: client.post("/x", {"a": 1}))

    await one_lookup()
    await one_lookup()
    assert state["calls"] == 2, "sequential calls are not deduplicated"


# ── sync: the two entry points ───────────────────────────────────────────────

@pytest.fixture
def crm_enabled(override_settings):
    override_settings(CRM_ENABLED=True)
    return override_settings


@pytest.fixture
def patched_client(monkeypatch):
    """Install a scripted client as the shared one, and capture outbox writes."""
    holder = {}

    def install(handler, **kwargs):
        client = make_client(handler, **kwargs)
        # sync.py imports get_client at module level, so patch it there — patching
        # app.crm.client.get_client would have no effect on the call site.
        monkeypatch.setattr("app.crm.sync.get_client", lambda: client)
        return client

    async def fake_enqueue(op, payload, **kwargs):
        holder.setdefault("queued", []).append((op, payload, kwargs))
        return True

    monkeypatch.setattr("app.crm.outbox.enqueue", fake_enqueue)
    return install, holder


@pytest.mark.anyio
async def test_lookup_or_create_returns_the_user_id(crm_enabled, patched_client):
    from app.crm import sync

    install, _ = patched_client
    install(lambda r: ok({"userId": "a0X1"}))

    ident = from_channel(
        "whatsapp", conversation_id="c1", phone_number="+14155550100", name="Ana"
    )
    assert await sync.lookup_or_create(ident) == "a0X1"


@pytest.mark.anyio
async def test_lookup_refuses_an_identity_with_no_name(crm_enabled, patched_client):
    """
    R17: the API's split_name("") raises IndexError and returns a 500. A name is
    a required field, so it cannot be omitted — the only safe move is to wait
    until the student gives one.
    """
    from app.crm import sync

    install, _ = patched_client
    handler, state = scripted([ok({"userId": "a0X1"})])
    install(handler)

    ident = from_channel("whatsapp", conversation_id="c1", phone_number="+14155550100")
    assert await sync.lookup_or_create(ident) is None
    assert state["calls"] == 0, "an empty name must never reach the API"


@pytest.mark.anyio
async def test_lookup_refuses_an_identity_with_no_identifier(crm_enabled, patched_client):
    """R2: refused before any request is made."""
    from app.crm import sync

    install, _ = patched_client
    handler, state = scripted([ok({"userId": "a0X1"})])
    install(handler)

    ident = from_channel("inbound_call", conversation_id="c1", name="Anonymous")
    assert await sync.lookup_or_create(ident) is None
    assert state["calls"] == 0, "an unusable identity must never reach the network"


@pytest.mark.anyio
async def test_lookup_reissues_once_on_unknown_state(crm_enabled, patched_client):
    """
    R4 recovery: the record may exist, so re-issue the same lookup — the API
    finds before it creates — rather than retrying blindly.
    """
    from app.crm import sync

    install, _ = patched_client
    handler, state = scripted([
        httpx.Response(500, text="boom"),
        ok({"userId": "a0X1"}),
    ])
    install(handler)

    ident = from_channel(
        "whatsapp", conversation_id="c1", phone_number="+14155550100", name="Ana"
    )
    assert await sync.lookup_or_create(ident) == "a0X1"
    assert state["calls"] == 2


@pytest.mark.anyio
async def test_lookup_returns_none_on_failure_and_never_raises(crm_enabled, patched_client):
    from app.crm import sync

    install, _ = patched_client
    install(lambda r: httpx.Response(422, json={"detail": "bad"}))

    ident = from_channel(
        "whatsapp", conversation_id="c1", phone_number="+14155550100", name="Ana"
    )
    assert await sync.lookup_or_create(ident) is None


@pytest.mark.anyio
async def test_push_status_sends_mapped_values(crm_enabled, patched_client):
    from app.crm import sync

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return ok({"userId": "a0X1"})

    install, _ = patched_client
    install(handler)

    ok_result = await sync.push_status(
        "a0X1", sentiment="At-Risk", offer_letter_released=True,
        offer_letter_accepted="rejected",
    )

    assert ok_result is True
    assert seen["body"] == {
        "sentiment": "AT-RISK",
        "offerLetterReleased": True,
        "offerLetterAccepted": "NOT_ACCEPTED",
    }
    assert seen["url"].endswith("/users/a0X1/status")


@pytest.mark.anyio
async def test_push_status_refuses_an_unmappable_sentiment(crm_enabled, patched_client):
    """
    The R16 defence: 'excited' is refused locally rather than sent to fail as a
    500 that the outbox would then retry forever.
    """
    from app.crm import sync

    seen = {}
    install, holder = patched_client
    install(lambda r: (seen.setdefault("called", True), ok({}))[1])

    result = await sync.push_status("a0X1", sentiment="excited")

    assert result is False
    assert "called" not in seen, "nothing should be sent"
    assert not holder.get("queued"), "and nothing should be queued"


@pytest.mark.anyio
async def test_transient_status_failure_is_queued(crm_enabled, patched_client):
    """A lost status write is a lost business fact, so it goes to the outbox."""
    from app.crm import sync

    install, holder = patched_client
    install(lambda r: httpx.Response(500, text="down"), max_retries=0)

    result = await sync.push_status("a0X1", sentiment="HOT", conversation_id="c1")

    assert result is False
    assert len(holder["queued"]) == 1
    op, payload, kwargs = holder["queued"][0]
    assert op == "status"
    assert payload == {"sentiment": "HOT"}
    assert kwargs["crm_user_id"] == "a0X1"


@pytest.mark.anyio
async def test_permanent_status_failure_is_not_queued(crm_enabled, patched_client):
    """
    Acceptance criterion A11: a restricted-picklist failure must never enter the
    retry queue — it would fail identically on every replay, forever.
    """
    from app.crm import sync

    install, holder = patched_client
    install(lambda r: err(500, "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST"))

    result = await sync.push_status("a0X1", sentiment="HOT")

    assert result is False
    assert not holder.get("queued"), "permanent failures must be dropped, not queued"


@pytest.mark.anyio
async def test_push_status_with_no_user_id_is_a_no_op(crm_enabled, patched_client):
    from app.crm import sync

    install, holder = patched_client
    install(lambda r: ok({}))

    assert await sync.push_status("", sentiment="HOT") is False
    assert not holder.get("queued")


# ── the shared client belongs to one event loop ──────────────────────────────

def test_shared_client_is_stable_within_one_loop():
    import asyncio

    from app.crm.client import get_client

    async def twice():
        return get_client(), get_client()

    first, second = asyncio.run(twice())
    assert first is second, "the pool must not be rebuilt on every call"


def test_shared_client_is_rebuilt_when_the_loop_changes():
    """
    Regression for a failure that is opaque in the wild: an httpx.AsyncClient
    reused across event loops raises "Event loop is closed" on its first pooled
    connection, and the run of failures then opens the circuit breaker — so
    everything starts being refused with no visible cause.

    app/leads/mcp_tools.py creates extra loops via asyncio.run(), so this is
    reachable in this app, not just in tests.
    """
    import asyncio

    from app.crm.client import get_client

    from_loop_a = asyncio.run(_grab())
    from_loop_b = asyncio.run(_grab())

    assert from_loop_a is not from_loop_b, "a new loop needs its own client"


async def _grab():
    from app.crm.client import get_client

    return get_client()


def test_rebuilt_client_is_not_called_outside_the_crm_gate(override_settings, monkeypatch):
    """
    Rebuilding must not smuggle in any behaviour when the CRM is disabled —
    get_client is never reached at all, which the Phase 3 gate already asserts.
    """
    from app.crm.client import get_client
    from app.crm import sync

    override_settings(CRM_ENABLED=False)
    monkeypatch.setattr(sync, "get_client", lambda: pytest.fail("must not be reached"))

    import asyncio

    assert asyncio.run(_noop()) is None
    # And building one directly still respects the configured base URL.
    assert get_client().base_url


async def _noop():
    from app.crm import sync

    return await sync.lookup_or_create(from_channel("whatsapp"))


# ── Gate 2: inert with the feature off ───────────────────────────────────────

@pytest.mark.anyio
async def test_disabled_crm_makes_no_network_call(monkeypatch, override_settings):
    """
    Phase 2's gate. With CRM_ENABLED=false (the default) both entry points must
    return without touching the network — no client, no request, no outbox.
    """
    from app.crm import sync

    override_settings(CRM_ENABLED=False)

    def explode():
        pytest.fail("get_client() must not be reached when CRM is disabled")

    monkeypatch.setattr("app.crm.sync.get_client", explode)

    async def explode_enqueue(*_a, **_kw):
        pytest.fail("outbox must not be touched when CRM is disabled")

    monkeypatch.setattr("app.crm.outbox.enqueue", explode_enqueue)

    ident = from_channel(
        "whatsapp", conversation_id="c1", phone_number="+14155550100", name="Ana"
    )
    assert await sync.lookup_or_create(ident) is None
    assert await sync.push_status("a0X1", sentiment="HOT") is False


@pytest.mark.anyio
async def test_default_configuration_has_crm_disabled(monkeypatch):
    """
    The *code* default must be off: reaching Salesforce requires an explicit
    opt-in.

    Asserted against a freshly built Settings with the environment variable
    removed, not against the running singleton — a deployment may legitimately
    turn the CRM on in .env (this one has), and that must not read as the
    default having changed.
    """
    monkeypatch.delenv("CRM_ENABLED", raising=False)
    from app.config import Settings

    assert Settings().CRM_ENABLED is False


@pytest.mark.anyio
async def test_flush_outbox_is_a_no_op_when_disabled(override_settings):
    from app.crm import sync

    override_settings(CRM_ENABLED=False)
    assert await sync.flush_outbox() == {
        "attempted": 0, "succeeded": 0, "failed": 0, "dropped": 0
    }
