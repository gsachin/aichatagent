"""
Upload a generated offer letter to the CRM, as a document on the student.

``sync.py`` is the module for the two small JSON calls this app makes; this is
the third thing, and it is different enough to live apart: it is a stateful
three-call protocol (init → chunks → complete) that carries bytes, and its side
effect — a Salesforce ``ContentVersion`` — cannot be undone through the API
(there is no delete route for a document). So the failure policy below is more
cautious than the one in ``sync.py``, and the ordering matters.

Two facts drive the design, both verified live against the dev org on
2026-09-12 (see ``scripts/verify_offer_document_upload.py``):

**The path segment is NOT our userId.** The upload routes resolve
``{application_no}`` with ``SELECT Id, Application_No__c FROM Customer WHERE
Application_No__c = X LIMIT 1``. The userId we store is the Salesforce record Id
(``format_user_response`` emits ``userId: user["Id"]``), and passing it to a
document route answers ``404 Application <id> was not found`` — reproduced. The
two are different columns on the same row. What we can do is turn one into the
other: ``GET /admissions/{userId}`` *is* record-Id keyed and returns the full
record, so the application number is one cheap call away and is cached on the
lead afterwards.

**``/complete`` is not idempotent.** It assembles the chunks, creates the
ContentVersion, reads it back, then deletes the session directory. A 500 can
therefore mean "the document exists but the response failed", and a retry would
create a second one. It is the one call here that is sent with ``is_create=True``
so that the client refuses to retry it.

Everything here is best-effort by construction: ``upload_offer_document`` returns
the document id or "" and never raises, so the offer path can schedule it and
forget it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.crm.client import (
    CrmCircuitOpen,
    CrmError,
    CrmPermanentError,
    CrmTransientError,
    CrmUnknownStateError,
    get_client,
)
from app.crm import identity as identity_mod
from app.crm.sync import enabled as crm_enabled
from app.crm.tasks import spawn

logger = logging.getLogger("crm.documents")

# ── Path safety ──────────────────────────────────────────────────────────────
#
# Every remote value that lands in a URL path is allowlisted and rejected on
# mismatch — never sanitised, never "cleaned up". Quoting alone is NOT enough:
# Starlette decodes %2F back to "/" for path matching, so a quoted "../../users/x"
# still routes somewhere it should not. (And do not reach for
# identity.sanitize() here — it is built for SOQL literals and would happily
# return "../../users/x".)
_APPLICATION_NO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SALESFORCE_ID_RE = re.compile(r"^[A-Za-z0-9]{15,18}$")
_UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Terminal upload states written to offer_letters.crm_upload_status.
STATUS_UPLOADED = "uploaded"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


def enabled() -> bool:
    """CRM on, and the offer-letter upload not separately switched off."""
    return crm_enabled() and bool(settings.CRM_OFFER_UPLOAD_ENABLED)


def documents_enabled() -> bool:
    """
    CRM on, and the *student* document upload not switched off.

    A separate gate from the offer letter on purpose: an operator may want the
    generated offer letter in the CRM while transcripts — which carry grades and
    identity documents — stay on this host until someone signs off on that.
    """
    return crm_enabled() and bool(settings.CRM_DOCUMENT_UPLOAD_ENABLED)


def _safe_segment(value: object, *, what: str, pattern: re.Pattern) -> str:
    """
    Validate a value that is about to be interpolated into a URL path.

    Returns the quoted value, or "" if it is not recognisably of that shape —
    in which case the caller must abandon the operation rather than guess.
    """
    text = str(value or "").strip()
    if not text or not pattern.match(text):
        logger.error(
            f"crm.documents: refusing unsafe {what} {text[:64]!r} — "
            f"does not match {pattern.pattern}"
        )
        return ""
    return quote(text, safe="")


# Our marker in the CRM: every document this module uploads is named with this
# prefix, which is what makes "have we already sent an offer letter to this
# student?" answerable without trusting Document_Type__c — a field we do not own,
# whose vocabulary is a restricted picklist (and whose only value we may use
# today, "Other", is a catch-all that would match other people's documents).
_FILE_PREFIX = "Offer_Letter_"


def _offer_file_name(offer_id: str) -> str:
    """
    The name this offer's PDF is uploaded under.

    Deterministic per offer row, so an operator looking at the CRM can tell two
    uploads of the same offer apart from two different offers. It becomes
    ``PathOnClient`` and the ContentVersion ``Title`` upstream, and its prefix is
    the marker the duplicate check looks for.
    """
    return f"{_FILE_PREFIX}{str(offer_id)[:8].upper()}.pdf"


# ── Resolving the application number ─────────────────────────────────────────

async def resolve_application_no(crm_user_id: str) -> str:
    """
    Turn the userId we store into the application number the routes address.

    ``GET /admissions/{id}`` is record-Id keyed and returns the whole record,
    including ``Application_No__c``. Returns "" when the id is unusable, the
    record is gone, or the record carries no application number at all (which
    makes the student unaddressable by the document routes — worth an operator
    knowing about, so it is logged as such rather than as a generic failure).

    Sent with ``retries=0`` deliberately. This endpoint answers *bad input* with
    a 500 (it wraps ``sf.Customer.get`` in a blanket ``except Exception``), and
    at the default three retries that single call would record four consecutive
    failures against a breaker whose threshold is five — pausing every other CRM
    call in the app because one lead has a stale id.
    """
    path_id = _safe_segment(crm_user_id, what="crm_user_id", pattern=_SALESFORCE_ID_RE)
    if not path_id:
        return ""

    client = get_client()
    try:
        data = await client.request("GET", f"/admissions/{path_id}", retries=0)
    except CrmPermanentError as exc:
        logger.error(
            f"crm.documents: cannot resolve an application number for user "
            f"{crm_user_id} — {exc}"
        )
        return ""
    except CrmError as exc:
        # Includes the 500-for-a-missing-record case. Either way there is
        # nothing to upload to, and no retry will change that.
        logger.error(
            f"crm.documents: application lookup failed for user {crm_user_id} — {exc}"
        )
        return ""

    application_no = str((data or {}).get("Application_No__c") or "").strip()
    if not application_no:
        logger.error(
            f"crm.documents: user {crm_user_id} has no Application_No__c — the "
            f"admission API cannot accept documents for this record"
        )
        return ""
    return application_no


# ── Duplicate defence ────────────────────────────────────────────────────────

async def _find_existing_offer_document(
    application_no: str, file_name: str
) -> tuple[str, dict | None]:
    """
    Look for an offer letter already attached to this application.

    Returns ``(state, document)`` where state is one of:

    ``"ok"``
        The CRM answered. ``document`` is the existing one, or None.
    ``"unavailable"``
        The CRM could not be asked. The caller must **skip** the upload rather
        than assume the document is absent: a duplicate ContentVersion cannot be
        deleted through this API, while a skipped upload costs nothing permanent.
    ``"stale"``
        The application number is not in the CRM at all (404). The cache is
        wrong, so the caller clears it and the next attempt resolves afresh.

    Two matching rules, strongest first, both keyed on our own file-name
    convention rather than on Document_Type__c:

    * the exact file name — this offer, uploaded by an earlier attempt that
      crashed before recording it;
    * any name carrying our prefix — an offer letter for this student already,
      which is what survives re-generation, where a new offer row means a new
      file name for what the admissions team sees as the same document.

    Deliberately *not* matching on document_type: that field's vocabulary is a
    restricted picklist we do not control, and the only value available today
    ("Other") is a catch-all — matching it would suppress our upload for any
    student who happens to have an unrelated document typed that way.
    """
    application = _safe_segment(
        application_no, what="application_no", pattern=_APPLICATION_NO_RE
    )
    if not application:
        return "stale", None

    try:
        docs = await get_client().get(f"/admissions/{application}/documents")
    except CrmPermanentError as exc:
        logger.warning(
            f"crm.documents: {application_no} is not in the CRM ({exc}) — "
            f"discarding the cached application number"
        )
        return "stale", None
    except CrmError as exc:
        logger.warning(
            f"crm.documents: could not list documents for {application_no} ({exc}) — "
            f"skipping the upload rather than risk a duplicate"
        )
        return "unavailable", None

    if not isinstance(docs, list):
        logger.warning(
            f"crm.documents: unexpected document listing for {application_no} — "
            f"skipping the upload rather than risk a duplicate"
        )
        return "unavailable", None

    for doc in docs:
        if isinstance(doc, dict) and str(doc.get("file_name") or "") == file_name:
            return "ok", doc

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("file_name") or "").startswith(_FILE_PREFIX):
            logger.info(
                f"crm.documents: {application_no} already has an offer letter "
                f"({doc.get('document_id')}, {doc.get('file_name')!r}) — not "
                f"uploading a second one for this student"
            )
            return "ok", doc

    return "ok", None


# ── The upload ───────────────────────────────────────────────────────────────

async def upload_offer_document(*, lead_id: str, offer_id: str, pdf_path: str) -> str:
    """
    Upload one offer PDF. Returns the CRM document id, or "" on any failure.

    Never raises: the caller is the offer path, where a CRM problem must not be
    visible to the student. Every exit logs one line an operator can act on.
    """
    if not enabled():
        logger.debug("crm.documents: upload disabled — nothing to do")
        return ""

    from app.leads.models import get_lead_crm_application_no, get_lead_crm_user_id
    from app.offers.models import get_offer_crm_upload, set_offer_crm_upload

    # Already done. This is the whole idempotency story for a repeat run of the
    # same offer row.
    state = await get_offer_crm_upload(offer_id)
    if state.get("document_id"):
        logger.debug(
            f"crm.documents: offer {offer_id} is already in the CRM as "
            f"{state['document_id']}"
        )
        return state["document_id"]

    user_id = await get_lead_crm_user_id(lead_id)
    if not user_id:
        logger.info(
            f"crm.documents: lead {lead_id} has no CRM link — offer PDF not uploaded"
        )
        return ""

    application_no = await get_lead_crm_application_no(lead_id)
    if not application_no:
        application_no = await resolve_application_no(user_id)
        if not application_no:
            await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
            return ""
        from app.leads.models import set_lead_crm_application_no

        await set_lead_crm_application_no(lead_id, application_no)

    file_name = _offer_file_name(offer_id)

    # Two concurrent generations for one person collapse into one upload. This
    # is the same single-flight the lookup uses, for the same reason: the API
    # has no way to make the operation atomic on its side.
    async def _do_upload() -> str:
        return await _upload(
            lead_id=lead_id,
            offer_id=offer_id,
            application_no=application_no,
            file_name=file_name,
            pdf_path=pdf_path,
        )

    return await get_client().single_flight(f"crm-doc:{application_no}", _do_upload)


class TransferResult:
    """
    What a chunked upload attempt actually did.

    Three outcomes, and the caller must treat them differently: the document is
    there, it may or may not be there, or it is definitely not. ``step`` says
    which call failed, for the log line.
    """

    def __init__(self, status: str, *, document_id: str = "", step: str = "",
                 error: CrmError | None = None) -> None:
        self.status = status          # "uploaded" | "unknown" | "failed"
        self.document_id = document_id
        self.step = step
        self.error = error


async def _transfer(
    *,
    application: str,
    file_name: str,
    blob: bytes,
    document_type: str,
    source: str,
    label: str,
    application_no: str,
) -> TransferResult:
    """
    init → chunks → complete. Returns what happened; never raises.

    Shared by the offer letter and by the student's own documents, because the
    three calls and their retry rules are the same for any file: only the
    ``document_type`` differs, and that is the caller's business.
    """
    client = get_client()
    timeout = settings.CRM_UPLOAD_TIMEOUT_S
    chunk_bytes = max(1, int(settings.CRM_UPLOAD_CHUNK_BYTES))
    total_chunks = max(1, -(-len(blob) // chunk_bytes))

    # ── 1. init ──────────────────────────────────────────────────────────
    # is_create=False on purpose. Re-sending init mints a new upload session and
    # orphans the old temp directory on the API host (nothing there ever GCs
    # them), but a single transient 500 must not cost us the document. Losing the
    # upload is the worse of the two, so this call keeps its retries.
    #
    # "" files: init carries form fields only, and httpx encodes a fields-only
    # request as application/x-www-form-urlencoded (`files` empty is falsy at
    # httpx/_content.py:211). The route declares those five as Form(...), and
    # FastAPI parses both encodings — verified live against :8098, which answers
    # 400 "Application ... was not found" for either, i.e. all five parsed. The
    # chunk call below *is* multipart, because there the file part is real.
    try:
        init = await client.post_multipart(
            f"/admissions/{application}/documents/uploads",
            data={
                "file_name": file_name,
                "file_size": len(blob),
                "total_chunks": total_chunks,
                "document_type": document_type,
                "source": source,
            },
            files={},
            is_create=False,
            timeout=timeout,
        )
    except CrmError as exc:
        return TransferResult("failed", step="initiate the upload", error=exc)

    upload_id = _safe_segment(
        (init or {}).get("upload_id"), what="upload_id", pattern=_UPLOAD_ID_RE
    )
    if not upload_id:
        logger.error(
            f"crm.documents: upload init for {label} returned no usable "
            f"upload_id: {init!r}"
        )
        return TransferResult("failed", step="initiate the upload")

    # ── 2. chunks (1-based, raw bytes) ───────────────────────────────────
    # Safe to retry: re-posting a chunk_number overwrites chunk_<n> upstream.
    base = f"/admissions/{application}/documents/uploads/{upload_id}"
    for number in range(1, total_chunks + 1):
        piece = blob[(number - 1) * chunk_bytes: number * chunk_bytes]
        try:
            await client.post_multipart(
                f"{base}/chunks",
                data={"chunk_number": number},
                files={"file": (file_name, piece, "application/pdf")},
                is_create=False,
                timeout=timeout,
            )
        except CrmError as exc:
            logger.warning(
                f"crm.documents: upload session {upload_id} is abandoned on the "
                f"API host and will not be cleaned up by anything"
            )
            return TransferResult("failed", step=f"send chunk {number}", error=exc)

    # ── 3. complete ──────────────────────────────────────────────────────
    # is_create=True: this is the call that creates the ContentVersion, so a 500
    # must never be retried automatically (see the module docstring).
    try:
        done = await client.post_multipart(
            f"{base}/complete",
            data={},
            files={},
            is_create=True,
            timeout=timeout,
        )
    except CrmUnknownStateError as exc:
        logger.error(
            f"crm.documents: {label} — the CRM answered 500 on complete, so the "
            f"document MAY exist on {application_no}. It is not being retried "
            f"(that could duplicate it). Check the CRM and record the id by hand "
            f"if it landed. {exc}"
        )
        return TransferResult("unknown", step="complete the upload", error=exc)
    except CrmError as exc:
        return TransferResult("failed", step="complete the upload", error=exc)

    document = (done or {}).get("document") or {}
    document_id = str(document.get("document_id") or "")
    if not document_id:
        logger.error(
            f"crm.documents: complete for {label} returned no document_id: {done!r}"
        )
        return TransferResult("unknown", step="complete the upload")

    logger.info(
        f"crm.documents: {label} uploaded to {application_no} as {document_id} "
        f"({file_name}, {len(blob)} bytes, {total_chunks} chunk(s))"
    )
    return TransferResult("uploaded", document_id=document_id)


async def _upload(
    *,
    lead_id: str,
    offer_id: str,
    application_no: str,
    file_name: str,
    pdf_path: str,
) -> str:
    from app.offers.models import set_offer_crm_upload

    application = _safe_segment(
        application_no, what="application_no", pattern=_APPLICATION_NO_RE
    )
    if not application:
        await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
        return ""

    path = Path(pdf_path)
    if not path.is_file():
        logger.error(
            f"crm.documents: offer {offer_id} PDF is not on disk ({pdf_path}) — "
            f"nothing to upload"
        )
        await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
        return ""

    try:
        blob = path.read_bytes()
    except OSError as exc:
        logger.error(f"crm.documents: cannot read {pdf_path} — {exc}")
        await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
        return ""

    if not blob:
        logger.error(f"crm.documents: {pdf_path} is empty — nothing to upload")
        await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
        return ""

    state, existing = await _find_existing_offer_document(application, file_name)
    if state == "unavailable":
        # Conservative: we could not prove the document is absent, and a
        # duplicate cannot be removed afterwards. Leave it for the next offer.
        return ""
    if state == "stale":
        from app.leads.models import set_lead_crm_application_no

        # The cached number no longer resolves. Clearing it costs one extra call
        # next time and is the only thing that stops this lead being permanently
        # wedged on a stale value.
        await set_lead_crm_application_no(lead_id, "")
        await set_offer_crm_upload(offer_id, status=STATUS_FAILED)
        return ""
    if existing:
        document_id = str(existing.get("document_id") or "")
        if document_id:
            await set_offer_crm_upload(
                offer_id, document_id=document_id, status=STATUS_UPLOADED
            )
            logger.info(
                f"crm.documents: offer {offer_id} was already uploaded to "
                f"{application_no} as {document_id} — recorded, not re-sent"
            )
        return document_id

    result = await _transfer(
        application=application,
        file_name=file_name,
        blob=blob,
        document_type=settings.CRM_OFFER_DOCUMENT_TYPE,
        source=settings.CRM_OFFER_DOCUMENT_SOURCE,
        label=f"offer {offer_id}",
        application_no=application_no,
    )

    if result.status == STATUS_UPLOADED:
        await set_offer_crm_upload(
            offer_id, document_id=result.document_id, status=STATUS_UPLOADED
        )
        return result.document_id

    await set_offer_crm_upload(offer_id, status=result.status)
    if result.error is not None:
        _log_upload_failure(offer_id, application_no, result.step, result.error)
    return ""


def _log_upload_failure(offer_id: str, application_no: str, step: str, exc: CrmError) -> None:
    """One actionable line per failure mode, matching sync.py's per-call policy."""
    if isinstance(exc, CrmPermanentError):
        # A 400 here usually means the application number was stale, or that
        # Document_Type__c / Source__c is a restricted picklist that does not
        # accept our value (R16 — it arrives as a 500, and never succeeds on
        # retry). Both are configuration problems, not transient ones.
        logger.error(
            f"crm.documents: could not {step} for offer {offer_id} on "
            f"{application_no} — the CRM rejected it permanently: {exc}. Check "
            f"CRM_OFFER_DOCUMENT_TYPE / CRM_OFFER_DOCUMENT_SOURCE against the org's "
            f"picklists."
        )
    elif isinstance(exc, CrmCircuitOpen):
        logger.warning(f"crm.documents: could not {step} for offer {offer_id} — {exc}")
    elif isinstance(exc, CrmTransientError):
        logger.warning(
            f"crm.documents: could not {step} for offer {offer_id} on "
            f"{application_no} after retries — {exc}"
        )
    else:
        logger.error(f"crm.documents: could not {step} for offer {offer_id} — {exc}")


# ── The student's own documents ──────────────────────────────────────────────

def _student_file_name(doc_id: str, original: str) -> str:
    """
    The name a student document is filed under in the CRM.

    Keeps the original name — the admissions team reads these, and
    "RAG.md.pdf" tells them more than a uuid does — but prefixes the local row id
    so the name is unique per upload and can be used to recognise a document
    that an earlier attempt already sent.
    """
    stem = Path(str(original or "document")).stem[:60] or "document"
    return f"{str(doc_id)[:8].upper()}_{stem}.pdf"


async def upload_student_document(
    *, lead_id: str, doc_id: str, file_path: str, doc_type: str, original_name: str = ""
) -> str:
    """
    Upload a document the student provided — transcript, ID proof — to the CRM.

    The offer letter is generated by us; these are the documents that *justify*
    it, and until now they never left this machine: ``lead_documents`` had no CRM
    columns and nothing uploaded them, so the admissions team could see the
    offer without ever seeing the transcript behind it.

    Returns the CRM document id, or "" on any failure. Never raises.
    """
    if not documents_enabled():
        logger.debug("crm.documents: student document upload disabled")
        return ""

    from app.leads.models import get_lead_crm_application_no, get_lead_crm_user_id
    from app.offers.models import get_document_crm_upload, set_document_crm_upload

    state = await get_document_crm_upload(doc_id)
    if state.get("document_id"):
        logger.debug(f"crm.documents: document {doc_id} is already in the CRM")
        return state["document_id"]

    user_id = await get_lead_crm_user_id(lead_id)
    if not user_id:
        logger.info(
            f"crm.documents: lead {lead_id} has no CRM link — document not uploaded"
        )
        return ""

    application_no = await get_lead_crm_application_no(lead_id)
    if not application_no:
        application_no = await resolve_application_no(user_id)
        if not application_no:
            await set_document_crm_upload(doc_id, status=STATUS_FAILED)
            return ""
        from app.leads.models import set_lead_crm_application_no

        await set_lead_crm_application_no(lead_id, application_no)

    application = _safe_segment(
        application_no, what="application_no", pattern=_APPLICATION_NO_RE
    )
    if not application:
        await set_document_crm_upload(doc_id, status=STATUS_FAILED)
        return ""

    path = Path(file_path)
    if not path.is_file():
        logger.error(f"crm.documents: document {doc_id} is not on disk ({file_path})")
        await set_document_crm_upload(doc_id, status=STATUS_FAILED)
        return ""

    try:
        blob = path.read_bytes()
    except OSError as exc:
        logger.error(f"crm.documents: cannot read {file_path} — {exc}")
        await set_document_crm_upload(doc_id, status=STATUS_FAILED)
        return ""
    if not blob:
        logger.error(f"crm.documents: {file_path} is empty — nothing to upload")
        await set_document_crm_upload(doc_id, status=STATUS_FAILED)
        return ""

    file_name = _student_file_name(doc_id, original_name or path.name)

    # Same conservative rule as the offer letter: an unprovable absence means
    # skip, because a duplicate ContentVersion cannot be deleted through this API.
    state, existing = await _find_existing_offer_document(application, file_name)
    if state == "unavailable":
        return ""
    if state == "stale":
        from app.leads.models import set_lead_crm_application_no

        await set_lead_crm_application_no(lead_id, "")
        await set_document_crm_upload(doc_id, status=STATUS_FAILED)
        return ""
    if existing:
        existing_id = str(existing.get("document_id") or "")
        if existing_id:
            await set_document_crm_upload(
                doc_id, document_id=existing_id, status=STATUS_UPLOADED
            )
            logger.info(
                f"crm.documents: document {doc_id} was already in the CRM as "
                f"{existing_id} — recorded, not re-sent"
            )
        return existing_id

    result = await _transfer(
        application=application,
        file_name=file_name,
        blob=blob,
        document_type=identity_mod.map_document_type(doc_type),
        source=settings.CRM_OFFER_DOCUMENT_SOURCE,
        label=f"document {doc_id}",
        application_no=application_no,
    )

    if result.status == STATUS_UPLOADED:
        await set_document_crm_upload(
            doc_id, document_id=result.document_id, status=STATUS_UPLOADED
        )
        return result.document_id

    await set_document_crm_upload(doc_id, status=result.status)
    if result.error is not None:
        _log_upload_failure(doc_id, application_no, result.step, result.error)
    return ""


def schedule_document_upload(
    *, lead_id: str, doc_id: str, file_path: str, doc_type: str, original_name: str = ""
) -> None:
    """
    Start a student document upload in the background.

    Called from the upload endpoints, which sit on a student's request: a
    half-megabyte transcript is three HTTP calls, and none of them should make
    the student wait or fail their upload.
    """
    if not documents_enabled():
        logger.debug("crm.documents: student document upload disabled — not scheduling")
        return
    if not file_path:
        return

    spawn(
        upload_student_document(
            lead_id=lead_id, doc_id=doc_id, file_path=file_path,
            doc_type=doc_type, original_name=original_name,
        ),
        label=f"document upload {doc_id}",
    )


# ── Fire and forget ──────────────────────────────────────────────────────────

def schedule_offer_upload(*, lead_id: str, offer_id: str, pdf_path: str) -> None:
    """
    Start the upload in the background and return immediately.

    Called from the offer path, which is reached inline inside a Twilio webhook:
    three HTTP calls there would sit in the webhook window, so this is
    deliberately not awaited. An in-flight upload is lost if the process stops,
    which is acceptable for a best-effort copy — the offer itself is already
    delivered, and the next generation of the same offer re-attempts it.

    The scheduling itself lives in ``app/crm/tasks.py``, shared with the voice
    and chat channels.
    """
    if not enabled():
        logger.debug("crm.documents: upload disabled — not scheduling")
        return
    if not pdf_path:
        logger.debug("crm.documents: no PDF path — not scheduling an upload")
        return

    spawn(
        upload_offer_document(lead_id=lead_id, offer_id=offer_id, pdf_path=pdf_path),
        label=f"offer upload {offer_id}",
    )
