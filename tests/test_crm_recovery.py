"""
R18 recovery: finding a person the /users routes cannot read back.

A record created without an email answers 500 on every read — ``UserResponse``
requires ``email: str`` while Salesforce stores an empty string as null — even
though the row itself was created. The admissions listing uses a model where
``Email__c`` is optional, so the same person is readable there. These tests pin
that workaround: it fires only when the normal lookup has already failed, only
when there is a phone to match on, and never at the cost of the happy path.
"""

from __future__ import annotations

import httpx
import pytest

from app.crm import sync as sync_mod
from app.crm.client import CrmClient
from app.crm.identity import from_channel

PHONE = "+917757057985"


def make_client(handler, **kwargs) -> CrmClient:
    kwargs.setdefault("max_retries", 0)  # failures are the point; keep it quick
    kwargs.setdefault("base_url", "http://crm.test")
    return CrmClient(transport=httpx.MockTransport(handler), **kwargs)


def install(monkeypatch, handler, **kwargs) -> CrmClient:
    client = make_client(handler, **kwargs)
    monkeypatch.setattr("app.crm.sync.get_client", lambda: client)
    return client


def recording_handler(responses: dict[str, httpx.Response], *, record: list):
    """Route by path; record every request. Unmapped paths raise loudly."""
    def handler(request: httpx.Request) -> httpx.Response:
        record.append((request.method, request.url.path))
        key = f"{request.method} {request.url.path}"
        if key not in responses:
            raise AssertionError(f"unexpected request: {key}")
        return responses[key]
    return handler


def identity(*, email="", phone=PHONE, name="Ana", conversation_id="conv-1"):
    return from_channel(
        "inbound_call",
        conversation_id=conversation_id,
        phone_number=phone,
        email=email,
        name=name,
    )


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True, CRM_MAX_RETRIES=0)


# ── the workaround ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_phone_only_person_is_recovered_from_the_admissions_listing(crm_on, monkeypatch):
    """The exact R18 case: the lookup 500s, but the row exists and is readable."""
    sent: list = []
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(
                500, json={"detail": "Internal Server Error"}
            ),
            "GET /admissions": httpx.Response(
                200,
                json=[
                    {"Id": "0o6OTHER", "Phone__c": "+14155550000"},  # someone else
                    {"Id": "0o6TARGET", "Phone__c": PHONE, "Application_No__c": "APP-X"},
                ],
            ),
        },
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) == "0o6TARGET"
    assert ("GET", "/admissions") in sent


@pytest.mark.anyio
async def test_the_phone_match_is_normalised(crm_on, monkeypatch):
    """
    The org stores numbers in whatever shape they arrived in — Twilio's
    ``whatsapp:`` prefix included — and we send bare E.164.
    """
    sent: list = []
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"}),
            "GET /admissions": httpx.Response(
                200, json=[{"Id": "0o6TARGET", "Phone__c": "whatsapp:+91 77570 57985"}]
            ),
        },
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) == "0o6TARGET"


@pytest.mark.anyio
async def test_no_match_in_the_listing_stays_unlinked(crm_on, monkeypatch):
    sent: list = []
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"}),
            "GET /admissions": httpx.Response(200, json=[{"Id": "0o6OTHER", "Phone__c": "+14155550000"}]),
        },
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) is None


@pytest.mark.anyio
async def test_a_row_with_no_id_is_not_mistaken_for_a_link(crm_on, monkeypatch):
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"}),
            "GET /admissions": httpx.Response(200, json=[{"Phone__c": PHONE}]),
        },
        record=[],
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) is None


# ── what must NOT change ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_happy_path_never_touches_the_listing(crm_on, monkeypatch):
    """Recovery is a failure-path cost; a successful lookup must not pay it."""
    sent: list = []
    handler = recording_handler(
        {"POST /users/lookup-or-create": httpx.Response(200, json={"userId": "0o6FOUND"})},
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity(email="ana@example.com")) == "0o6FOUND"
    assert sent == [("POST", "/users/lookup-or-create")], "no listing lookup on the happy path"


@pytest.mark.anyio
async def test_an_identity_with_an_email_and_no_phone_does_not_recover(crm_on, monkeypatch):
    """Nothing to match on — the recovery needs a phone, so it must not fire."""
    sent: list = []
    handler = recording_handler(
        {"POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"})},
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity(email="ana@example.com", phone="")) is None
    assert ("GET", "/admissions") not in sent


@pytest.mark.anyio
async def test_an_unusable_phone_does_not_recover(crm_on, monkeypatch):
    """A number without a country code normalises to "" — never a phone to match."""
    sent: list = []
    handler = recording_handler(
        {"POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"})},
        record=sent,
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity(phone="7757057985")) is None
    assert ("GET", "/admissions") not in sent


# ── degradation ──────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_failing_listing_does_not_raise(crm_on, monkeypatch):
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"}),
            "GET /admissions": httpx.Response(500, json={"detail": "listing down"}),
        },
        record=[],
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) is None


@pytest.mark.anyio
async def test_an_unexpected_listing_shape_does_not_raise(crm_on, monkeypatch):
    handler = recording_handler(
        {
            "POST /users/lookup-or-create": httpx.Response(500, json={"detail": "boom"}),
            "GET /admissions": httpx.Response(200, json={"detail": "not a list"}),
        },
        record=[],
    )
    install(monkeypatch, handler)

    assert await sync_mod.lookup_or_create(identity()) is None


@pytest.mark.anyio
async def test_recovery_is_skipped_entirely_when_the_crm_is_off(monkeypatch, override_settings):
    override_settings(CRM_ENABLED=False)
    sent: list = []
    install(monkeypatch, recording_handler({}, record=sent))

    assert await sync_mod.lookup_or_create(identity()) is None
    assert sent == []
