"""
Tests for the offer-letter document upload (Phase 8).

Same discipline as ``test_crm_client.py``: everything runs against an
``httpx.MockTransport``, no live API and no database. The database accessors are
stubbed as plain dicts, so what is under test is the protocol and the failure
policy — which is where the risk is, because ``/complete`` creates a Salesforce
record that this API cannot delete.

Two behaviours are load-bearing and are asserted explicitly:

* ``/complete`` is never retried (a 500 there may mean the document already
  exists), while ``/chunks`` is;
* a duplicate is never created — the local marker short-circuits, and the remote
  pre-check catches what the marker cannot (a re-generated offer is a new row
  with a new file name).
"""

from __future__ import annotations

import asyncio
import email

import httpx
import pytest

from app.crm import documents as docs_mod
from app.crm.client import CrmClient

PDF = b"%PDF-1.4\n" + (b"x" * 5000) + b"\n%%EOF\n"


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def crm_on(override_settings):
    override_settings(
        CRM_ENABLED=True,
        CRM_OFFER_UPLOAD_ENABLED=True,
        CRM_OFFER_DOCUMENT_TYPE="Offer Letter",
        CRM_OFFER_DOCUMENT_SOURCE="Internal Upload",
        CRM_UPLOAD_CHUNK_BYTES=1024 * 1024,
        CRM_UPLOAD_TIMEOUT_S=30.0,
    )


@pytest.fixture
def store(monkeypatch):
    """
    Dict-backed stand-ins for the four database accessors, plus a call log.

    The upload touches lead and offer rows only through these; patching the
    functions (not a connection) keeps the test free of Postgres, matching
    ``tests/test_crm_whatsapp.py``.
    """
    state = {
        "user_id": "a0X1234567890ABCD",
        "application_no": "",
        "offer_state": {"document_id": "", "status": "", "uploaded_at": None},
        "recorded": [],
        "cleared": [],
    }

    async def get_lead_crm_user_id(lead_id):
        return state["user_id"]

    async def get_lead_crm_application_no(lead_id):
        return state["application_no"]

    async def set_lead_crm_application_no(lead_id, application_no):
        state["application_no"] = application_no or ""
        state["cleared"].append(application_no)
        return True

    async def get_offer_crm_upload(offer_id):
        return state["offer_state"]

    async def set_offer_crm_upload(offer_id, *, document_id="", status=""):
        state["recorded"].append({"document_id": document_id, "status": status})
        if document_id:
            state["offer_state"] = {
                "document_id": document_id, "status": status, "uploaded_at": None
            }
        return True

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", get_lead_crm_user_id)
    monkeypatch.setattr(
        "app.leads.models.get_lead_crm_application_no", get_lead_crm_application_no
    )
    monkeypatch.setattr(
        "app.leads.models.set_lead_crm_application_no", set_lead_crm_application_no
    )
    monkeypatch.setattr("app.offers.models.get_offer_crm_upload", get_offer_crm_upload)
    monkeypatch.setattr("app.offers.models.set_offer_crm_upload", set_offer_crm_upload)
    return state


def make_client(handler, **kwargs) -> CrmClient:
    kwargs.setdefault("max_retries", 2)
    kwargs.setdefault("base_url", "http://crm.test")
    return CrmClient(transport=httpx.MockTransport(handler), **kwargs)


def scripted(responses: list[httpx.Response]):
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


def init_ok(upload_id: str = "3f1a9e0c-1b2d-4c3e-8f90-abcdef012345") -> httpx.Response:
    return ok({
        "upload_id": upload_id,
        "application_no": "APP-D0ACBB945B",
        "file_name": "Offer_Letter_ABCD1234.pdf",
        "total_chunks": 1,
        "status": "initiated",
    })


def chunk_ok() -> httpx.Response:
    return ok({
        "upload_id": "3f1a9e0c-1b2d-4c3e-8f90-abcdef012345",
        "chunk_number": 1, "received_chunks": 1, "total_chunks": 1,
        "status": "uploaded",
    })


def complete_ok(document_id: str = "068f6000002vvTWAAY") -> httpx.Response:
    return ok({
        "upload_id": "3f1a9e0c-1b2d-4c3e-8f90-abcdef012345",
        "application_no": "APP-D0ACBB945B",
        "status": "completed",
        "document": {
            "document_id": document_id,
            "file_name": "Offer_Letter_ABCD1234.pdf",
            "document_type": "Offer Letter",
            "source": "Internal Upload",
        },
    })


def multipart_parts(request: httpx.Request):
    """
    Split an encoded multipart body into ``(fields, files)``.

    ``MockTransport`` hands the handler a request whose body has already been
    read, so ``request.content`` holds the whole thing — parsed here with the
    stdlib rather than a third-party parser, so the test does not drift with a
    dependency's API.
    """
    raw = (
        b"Content-Type: " + request.headers["content-type"].encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content
    )
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in email.message_from_bytes(raw).get_payload():
        name = part.get_param("name", header="content-disposition")
        body = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, body)
        else:
            fields[name] = body.decode()
    return fields, files


def install(monkeypatch, handler, **kwargs) -> CrmClient:
    client = make_client(handler, **kwargs)
    # documents.py imports get_client by name, so this is the module attribute
    # that is actually called (the trap documented in test_crm_client.py).
    monkeypatch.setattr("app.crm.documents.get_client", lambda: client)
    return client


def upload(offer_id: str = "abcd1234-0000-0000-0000-000000000000", pdf_path: str = ""):
    return docs_mod.upload_offer_document(
        lead_id="lead-1", offer_id=offer_id, pdf_path=pdf_path
    )


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "offer.pdf"
    path.write_bytes(PDF)
    return str(path)


# ── resolving the application number ─────────────────────────────────────────

@pytest.mark.anyio
async def test_resolver_reads_application_no_from_the_record(crm_on, monkeypatch):
    """
    The userId is a record Id; `GET /admissions/{id}` is the only way to the
    Application_No__c the document routes actually resolve.
    """
    handler, state = scripted([ok({"Id": "a0X1234567890ABCD", "Application_No__c": "APP-D0ACBB945B"})])
    install(monkeypatch, handler)

    assert await docs_mod.resolve_application_no("a0X1234567890ABCD") == "APP-D0ACBB945B"
    assert state["requests"][0].url.path == "/admissions/a0X1234567890ABCD"


@pytest.mark.anyio
async def test_resolver_gives_up_on_a_missing_record_without_retrying(crm_on, monkeypatch):
    """
    This endpoint answers a bad id with a 500, and at the default three retries
    that would be four failures against a breaker whose threshold is five.
    """
    handler, state = scripted([err(500, "NOT_FOUND: record does not exist")])
    install(monkeypatch, handler)

    assert await docs_mod.resolve_application_no("a0X1234567890ABCD") == ""
    assert state["calls"] == 1, "the resolver must not retry a bad id"


@pytest.mark.anyio
async def test_resolver_rejects_a_hostile_user_id_without_any_request(crm_on, monkeypatch):
    """Path traversal in a path segment is refused, not quoted and sent."""
    handler, state = scripted([ok({})])
    install(monkeypatch, handler)

    assert await docs_mod.resolve_application_no("../../users/a0X1") == ""
    assert state["calls"] == 0


@pytest.mark.anyio
async def test_resolver_reports_a_record_with_no_application_number(crm_on, monkeypatch):
    handler, state = scripted([ok({"Id": "a0X1234567890ABCD", "Application_No__c": None})])
    install(monkeypatch, handler)

    assert await docs_mod.resolve_application_no("a0X1234567890ABCD") == ""


# ── the happy path ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_upload_runs_init_chunk_complete_in_order(crm_on, store, monkeypatch, pdf):
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([
        ok([]),          # pre-check: no documents yet
        init_ok(),
        chunk_ok(),
        complete_ok(),
    ])
    install(monkeypatch, handler)

    document_id = await upload(pdf_path=pdf)

    assert document_id == "068f6000002vvTWAAY"
    paths = [r.url.path for r in state["requests"]]
    assert paths == [
        "/admissions/APP-D0ACBB945B/documents",
        "/admissions/APP-D0ACBB945B/documents/uploads",
        "/admissions/APP-D0ACBB945B/documents/uploads/"
        "3f1a9e0c-1b2d-4c3e-8f90-abcdef012345/chunks",
        "/admissions/APP-D0ACBB945B/documents/uploads/"
        "3f1a9e0c-1b2d-4c3e-8f90-abcdef012345/complete",
    ]
    assert store["recorded"][-1] == {
        "document_id": "068f6000002vvTWAAY", "status": "uploaded"
    }


@pytest.mark.anyio
async def test_init_declares_the_expected_fields(crm_on, store, monkeypatch, pdf):
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), init_ok(), chunk_ok(), complete_ok()])
    install(monkeypatch, handler)

    await upload(offer_id="abcd1234-0000-0000-0000-000000000000", pdf_path=pdf)

    request = state["requests"][1]
    fields = dict(httpx.QueryParams(request.content.decode()))
    assert fields == {
        "file_name": "Offer_Letter_ABCD1234.pdf",
        "file_size": str(len(PDF)),
        "total_chunks": "1",
        "document_type": "Offer Letter",
        "source": "Internal Upload",
    }


@pytest.mark.anyio
async def test_chunk_carries_the_raw_bytes_and_a_one_based_number(crm_on, store, monkeypatch, pdf):
    """Raw bytes, not base64 — the API encodes for Salesforce itself."""
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), init_ok(), chunk_ok(), complete_ok()])
    install(monkeypatch, handler)

    await upload(pdf_path=pdf)

    request = state["requests"][2]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    fields, files = multipart_parts(request)
    assert fields["chunk_number"] == "1"
    assert files["file"][0] == "Offer_Letter_ABCD1234.pdf"
    assert files["file"][1] == PDF, "the chunk must be the raw bytes"


@pytest.mark.anyio
async def test_a_large_pdf_is_split_into_ordered_chunks(crm_on, store, monkeypatch, pdf, override_settings):
    override_settings(CRM_UPLOAD_CHUNK_BYTES=1024)
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), init_ok(), chunk_ok(), chunk_ok(), chunk_ok(), chunk_ok(), complete_ok()])
    install(monkeypatch, handler)

    await upload(pdf_path=pdf)

    chunks = [r for r in state["requests"] if r.url.path.endswith("/chunks")]
    assert len(chunks) == len(PDF) // 1024 + 1
    rebuilt = b""
    for request in chunks:
        fields, files = multipart_parts(request)
        rebuilt += files["file"][1]
    assert rebuilt == PDF, "chunks must reassemble byte-for-byte"


# ── duplicate defence ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_recorded_document_id_short_circuits_everything(crm_on, store, monkeypatch, pdf):
    store["offer_state"] = {
        "document_id": "068f6000002vvTWAAY", "status": "uploaded", "uploaded_at": None
    }
    handler, state = scripted([ok([])])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == "068f6000002vvTWAAY"
    assert state["calls"] == 0, "a known upload must not touch the network"


@pytest.mark.anyio
async def test_an_existing_offer_letter_is_adopted_not_duplicated(crm_on, store, monkeypatch, pdf):
    """
    A re-generated offer is a new row with a new file name, so the pre-check also
    matches our naming convention — otherwise the CRM would collect a second copy
    of the same letter for the same student.
    """
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([
        ok([{
            "document_id": "068f6000001aaAAAAY",
            "file_name": "Offer_Letter_OLD00001.pdf",
            "document_type": "Other",
        }]),
    ])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == "068f6000001aaAAAAY"
    assert [r.url.path for r in state["requests"]] == [
        "/admissions/APP-D0ACBB945B/documents"
    ], "no upload may start once an offer letter is known to exist"
    assert store["recorded"][-1]["status"] == "uploaded"


@pytest.mark.anyio
async def test_an_unrelated_document_does_not_suppress_the_upload(crm_on, store, monkeypatch, pdf):
    """
    The org's Document_Type__c has no offer-letter value, so an offer letter is
    filed as "Other" — a catch-all shared with unrelated documents. Matching on
    it would silently stop uploading offers for any student who has one, which is
    why the duplicate rule keys on our own file-name prefix instead.
    """
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([
        ok([{
            "document_id": "068f6000001aaAAAAY",
            "file_name": "TESTAPP2027200_IELTS_Score.pdf",
            "document_type": "Other",
        }]),
        init_ok(), chunk_ok(), complete_ok("068f6000002vvTWAAY"),
    ])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == "068f6000002vvTWAAY"
    assert any(r.url.path.endswith("/uploads") for r in state["requests"]), (
        "an unrelated 'Other' document must not be mistaken for an offer letter"
    )


@pytest.mark.anyio
async def test_a_failed_pre_check_skips_the_upload_rather_than_risking_a_duplicate(
    crm_on, store, monkeypatch, pdf
):
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([err(500, "salesforce unavailable")])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    # The list call itself is retried (a transient blip there is worth a second
    # try); what must never happen is an upload starting on an unproven absence.
    assert [r.url.path for r in state["requests"]] == ["/admissions/APP-D0ACBB945B/documents"] * 3
    assert not any(r.url.path.endswith("/uploads") for r in state["requests"])


@pytest.mark.anyio
async def test_a_stale_application_number_is_cleared_so_the_next_run_recovers(
    crm_on, store, monkeypatch, pdf
):
    store["application_no"] = "APP-GONE"
    handler, state = scripted([err(404, "Application APP-GONE was not found.")])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert store["application_no"] == "", "the bad cache must not persist"
    assert store["recorded"][-1]["status"] == "failed"


@pytest.mark.anyio
async def test_two_concurrent_uploads_for_one_application_collapse(crm_on, store, monkeypatch, pdf):
    """Single-flight: two generations at once must not both upload."""
    store["application_no"] = "APP-D0ACBB945B"
    gate = asyncio.Event()
    calls = {"init": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents"):
            return ok([])
        if request.url.path.endswith("/uploads"):
            calls["init"] += 1
            await gate.wait()
            return init_ok()
        if request.url.path.endswith("/chunks"):
            return chunk_ok()
        return complete_ok()

    install(monkeypatch, handler)

    first = asyncio.ensure_future(upload(pdf_path=pdf))
    second = asyncio.ensure_future(upload(pdf_path=pdf))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second)

    assert calls["init"] == 1, "the second caller must join the first, not re-upload"
    assert results[0] == results[1] == "068f6000002vvTWAAY"


# ── the calls that must not be retried ───────────────────────────────────────

@pytest.mark.anyio
async def test_complete_is_never_retried_on_a_bare_500(crm_on, store, monkeypatch, pdf):
    """
    A 500 from /complete can mean the ContentVersion was created and only the
    response failed. Retrying would create a second, undeletable document.
    """
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), init_ok(), chunk_ok(), err(500, "salesforce blew up")])
    install(monkeypatch, handler, max_retries=3)

    assert await upload(pdf_path=pdf) == ""
    completes = [r for r in state["requests"] if r.url.path.endswith("/complete")]
    assert len(completes) == 1
    assert store["recorded"][-1]["status"] == "unknown", (
        "an ambiguous outcome must be recorded as unknown, not failed — the "
        "difference is whether an operator needs to go and look"
    )


@pytest.mark.anyio
async def test_a_restricted_picklist_rejection_is_permanent(crm_on, store, monkeypatch, pdf):
    """R16: an out-of-vocabulary Document_Type__c arrives as a 500, and retrying
    it would fail identically forever."""
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([
        ok([]), init_ok(), chunk_ok(),
        err(500, "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST: bad value for Document_Type__c"),
    ])
    install(monkeypatch, handler, max_retries=3)

    assert await upload(pdf_path=pdf) == ""
    completes = [r for r in state["requests"] if r.url.path.endswith("/complete")]
    assert len(completes) == 1
    assert store["recorded"][-1]["status"] == "failed"


@pytest.mark.anyio
async def test_a_chunk_500_is_retried(crm_on, store, monkeypatch, pdf):
    """Re-posting a chunk overwrites it upstream, so retrying is safe here — the
    opposite judgement from /complete."""
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), init_ok(), err(500, "blip"), chunk_ok(), complete_ok()])
    install(monkeypatch, handler, max_retries=3)

    assert await upload(pdf_path=pdf) == "068f6000002vvTWAAY"
    chunks = [r for r in state["requests"] if r.url.path.endswith("/chunks")]
    assert len(chunks) == 2


@pytest.mark.anyio
async def test_a_rejected_init_records_a_failure(crm_on, store, monkeypatch, pdf):
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([]), err(400, "Application APP-D0ACBB945B was not found.")])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert store["recorded"][-1]["status"] == "failed"


# ── preconditions ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_nothing_leaves_the_process_when_the_crm_is_off(store, monkeypatch, pdf, override_settings):
    override_settings(CRM_ENABLED=False, CRM_OFFER_UPLOAD_ENABLED=True)
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([])])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert state["calls"] == 0


@pytest.mark.anyio
async def test_the_upload_can_be_switched_off_while_the_crm_stays_on(store, monkeypatch, pdf, override_settings):
    """The rollback lever: stop the one CRM write that cannot be undone."""
    override_settings(CRM_ENABLED=True, CRM_OFFER_UPLOAD_ENABLED=False)
    handler, state = scripted([ok([])])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert state["calls"] == 0


@pytest.mark.anyio
async def test_a_lead_with_no_crm_link_is_skipped(crm_on, store, monkeypatch, pdf):
    store["user_id"] = ""
    handler, state = scripted([ok([])])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert state["calls"] == 0


@pytest.mark.anyio
async def test_a_missing_pdf_is_not_uploaded(crm_on, store, monkeypatch, tmp_path):
    store["application_no"] = "APP-D0ACBB945B"
    handler, state = scripted([ok([])])
    install(monkeypatch, handler)

    assert await upload(pdf_path=str(tmp_path / "gone.pdf")) == ""
    assert state["calls"] == 0
    assert store["recorded"][-1]["status"] == "failed"


@pytest.mark.anyio
async def test_an_unresolvable_application_number_stops_before_the_upload(
    crm_on, store, monkeypatch, pdf
):
    """No cached number and the lookup fails — nothing to address the upload to."""
    store["application_no"] = ""
    handler, state = scripted([err(500, "NOT_FOUND")])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == ""
    assert store["recorded"][-1]["status"] == "failed"


@pytest.mark.anyio
async def test_a_resolved_application_number_is_cached_on_the_lead(
    crm_on, store, monkeypatch, pdf
):
    store["application_no"] = ""
    handler, state = scripted([
        ok({"Id": "a0X1234567890ABCD", "Application_No__c": "APP-D0ACBB945B"}),
        ok([]), init_ok(), chunk_ok(), complete_ok(),
    ])
    install(monkeypatch, handler)

    assert await upload(pdf_path=pdf) == "068f6000002vvTWAAY"
    assert store["application_no"] == "APP-D0ACBB945B"
    assert store["cleared"][0] == "APP-D0ACBB945B"


# ── scheduling ───────────────────────────────────────────────────────────────

def test_scheduling_returns_immediately_and_runs_in_the_background(crm_on, monkeypatch, pdf):
    seen = []

    async def fake_upload(*, lead_id, offer_id, pdf_path):
        seen.append((lead_id, offer_id, pdf_path))
        return "068f6000002vvTWAAY"

    monkeypatch.setattr(docs_mod, "upload_offer_document", fake_upload)

    async def scenario():
        docs_mod.schedule_offer_upload(lead_id="lead-1", offer_id="off-1", pdf_path=pdf)
        assert seen == [], "scheduling must not await the upload"
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert seen == [("lead-1", "off-1", pdf)]


def test_scheduling_is_a_no_op_when_disabled(monkeypatch, pdf, override_settings):
    override_settings(CRM_ENABLED=False)
    seen = []

    async def fake_upload(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(docs_mod, "upload_offer_document", fake_upload)
    docs_mod.schedule_offer_upload(lead_id="lead-1", offer_id="off-1", pdf_path=pdf)
    assert seen == []


def test_a_raising_upload_does_not_escape_the_task(crm_on, monkeypatch, pdf):
    """upload_offer_document promises never to raise; if it does, the done-callback
    must swallow it rather than let it surface as an unhandled task exception."""
    async def boom(**kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(docs_mod, "upload_offer_document", boom)

    async def scenario():
        docs_mod.schedule_offer_upload(lead_id="lead-1", offer_id="off-1", pdf_path=pdf)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())
