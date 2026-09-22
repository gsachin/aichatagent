"""
Pushing what happened to a student into the CRM's profile.

``documents.py`` uploads the offer letter; this module updates the *record* —
the program they are interested in, the sentiment of the conversation, and where
the offer has got to. Between them, Salesforce stops contradicting itself (it
used to hold a released offer letter while reporting that no offer was released).

Three facts about the API shape everything here, all verified live on
2026-09-12 (``scripts/verify_gate7_status.py`` re-checks them):

**One route does this: ``PATCH /admissions/{id}``.** It is record-Id keyed — the
userId we already store — and it is the only route that can write ``Course__c``
or any of the offer fields. ``PATCH /users/{id}/status`` writes three fields and
no more.

**Never send a null.** The route dumps the body with ``exclude_unset=True``, so a
key present as explicit ``null`` *is* sent, and null **clears the field in
Salesforce**. A PATCH containing only the keys we mean to change is safe;
sending a round-tripped object would wipe the record. Reproduced in the dry run:
``{"Conversation_ID__c": null}`` erased a value that was there.

**Nothing is validated upstream.** None of these are checked against the org's
picklists by the API, so an out-of-vocabulary value becomes a 500 at write time.
The mappings in ``identity.py`` are the only guard, and they refuse rather than
guess.

Everything here is best-effort: ``push_profile`` returns a bool and never raises,
so it can be called from a live conversation path.
"""

from __future__ import annotations

import logging

from app.crm import outbox
from app.crm.client import (
    CrmCircuitOpen,
    CrmError,
    CrmPermanentError,
    CrmTransientError,
    CrmUnknownStateError,
    get_client,
)
from app.crm.sync import enabled

logger = logging.getLogger("crm.status")

# The only fields this module will ever send. An allowlist rather than a
# passthrough because the route accepts 35 fields and treats an explicit null as
# a clear: a caller bug must not be able to reach the record.
#
# ``Conversation_ID__c`` is here because ``lookup-or-create`` writes it only when
# it *creates* a record, so without this a returning student's CRM row would
# point at their first conversation forever (plan R6 / A4).
WRITABLE_FIELDS = frozenset({
    "Course__c",
    "Conversation_ID__c",
    "Sentiment__c",
    "Lead_Category__c",
    "Offer_Status__c",
    "Offer_Sent_At__c",
    "Offer_Letter_Released__c",
    "Offer_Letter_Accepted__c",
    "Admission_Status__c",
    "Testing_Record__c",
})


def _clean(fields: dict) -> dict:
    """
    Keep only allowlisted keys that carry a value.

    Dropping ``None`` is not tidiness — on this route a null clears the field in
    Salesforce, so omitting an absent key is the difference between "leave it
    alone" and "erase it".
    """
    unknown = set(fields) - WRITABLE_FIELDS
    if unknown:
        # A programming error, not a data problem: fail loudly rather than write
        # something unintended to a CRM humans read.
        raise ValueError(f"refusing to write non-allowlisted CRM fields: {sorted(unknown)}")
    return {k: v for k, v in fields.items() if v is not None and v != ""}


async def push_profile(crm_user_id: str, **fields) -> bool:
    """
    Update the CRM record for ``crm_user_id``. Returns True if the CRM took it.

    ``fields`` are CRM field names (``Course__c=...``), already mapped into the
    org's vocabularies by the caller. Unknown field names raise before anything
    is sent; an empty payload is a no-op, so a caller can compute fields freely.

    A transient failure is queued for replay. A permanent one — an
    out-of-vocabulary picklist value, or a record that no longer exists — is
    logged and dropped, because replaying it would fail identically forever.
    """
    if not enabled():
        logger.debug("crm.status: CRM disabled — nothing to push")
        return False

    if not crm_user_id:
        logger.warning("crm.status: push with no userId — nothing to update")
        return False

    try:
        payload = _clean(fields)
    except ValueError as exc:
        logger.error(f"crm.status: {exc}")
        return False

    if not payload:
        logger.debug("crm.status: no fields to push")
        return False

    client = get_client()
    try:
        await client.patch(f"/admissions/{crm_user_id}", payload)
    except CrmPermanentError as exc:
        logger.error(
            f"crm.status: {crm_user_id} permanently rejected {sorted(payload)} — "
            f"not queued: {exc}"
        )
        return False
    except CrmCircuitOpen as exc:
        logger.warning(f"crm.status: {exc} — queueing {sorted(payload)} for {crm_user_id}")
        await _queue(crm_user_id, payload, str(exc))
        return False
    except (CrmTransientError, CrmUnknownStateError, CrmError) as exc:
        logger.warning(
            f"crm.status: could not update {crm_user_id} ({sorted(payload)}) — "
            f"queueing: {exc}"
        )
        await _queue(crm_user_id, payload, str(exc))
        return False

    logger.info(f"crm.status: {crm_user_id} updated — {sorted(payload)}")
    return True


async def _queue(crm_user_id: str, payload: dict, error: str) -> None:
    """Hand a failed write to the durable queue. Never raises."""
    try:
        await outbox.enqueue("profile", payload, crm_user_id=crm_user_id, error=error)
    except Exception:
        logger.exception("crm.status: could not queue the failed update")


# ── Convenience wrappers ─────────────────────────────────────────────────────
#
# Each computes its field set from the app's own vocabulary, so the call sites
# stay short and the mapping stays in one place.

async def push_course(crm_user_id: str, course: str) -> bool:
    """Publish the program a student is interested in. ``Course__c`` is free text."""
    return await push_profile(crm_user_id, Course__c=str(course).strip() or None)


async def push_conversation_id(crm_user_id: str, conversation_id: str) -> bool:
    """
    Point the CRM at the conversation that is happening now (plan A4).

    ``lookup-or-create`` sets this field at create time only, so for a returning
    student the CRM would otherwise name their first conversation forever. An
    empty id is dropped by ``_clean`` rather than sent as a null, which on this
    route would *erase* the value instead of leaving it alone.
    """
    return await push_profile(
        crm_user_id, Conversation_ID__c=str(conversation_id).strip() or None
    )


async def push_offer_released(
    crm_user_id: str,
    *,
    sent_at: str = "",
    admission_status: object = "under_review",
) -> bool:
    """
    Publish that an offer letter went out.

    Three fields move together: the lifecycle picklist, the timestamp, and the
    boolean the admissions team watches. The boolean is a **required** org field,
    so it is always sent as a real True rather than omitted.
    """
    from app.crm.identity import map_admission_status

    return await push_profile(
        crm_user_id,
        Offer_Status__c="Offered",
        Offer_Sent_At__c=sent_at or None,
        Offer_Letter_Released__c=True,
        Admission_Status__c=map_admission_status(admission_status),
    )


async def push_offer_response(crm_user_id: str, response: object) -> bool:
    """
    Publish the student's answer: accepted or declined.

    The app says "accepted"/"rejected"; the CRM wants ACCEPTED/NOT_ACCEPTED on
    one field, Accepted/Declined on another, and Approved/Rejected on the
    admission itself. All three move together — a declined offer that still
    reads "Approved" is worse than no update at all.
    """
    from app.crm.identity import map_admission_status, map_offer_accepted, map_offer_status

    accepted = map_offer_accepted(response)
    status = map_offer_status(response)
    admission = map_admission_status(response)
    if accepted is None or status is None or admission is None:
        logger.warning(
            f"crm.status: refusing to send an unmappable offer response {response!r}"
        )
        return False

    return await push_profile(
        crm_user_id,
        Offer_Letter_Accepted__c=accepted,
        Offer_Status__c=status,
        Admission_Status__c=admission,
    )


async def push_sentiment(crm_user_id: str, category: object) -> bool:
    """
    Publish the conversation's sentiment category.

    ``category`` is the categorizer's output ("Hot"/"Warm"/…), not
    ``primary_emotion`` — an emotion word is not in either picklist and would be
    a 500 (R16). Both category fields are written from the same source, because
    the two spellings differ and copying one into the other is the trap.
    """
    from app.crm.identity import map_lead_category, map_sentiment

    sentiment = map_sentiment(category)
    lead_category = map_lead_category(category)
    if sentiment is None:
        logger.warning(f"crm.status: refusing to send unmappable sentiment {category!r}")
        return False

    return await push_profile(
        crm_user_id,
        Sentiment__c=sentiment,
        Lead_Category__c=lead_category,
    )


async def push_admission_status(crm_user_id: str, state: object) -> bool:
    """Publish where the admission stands (documents owed, under review, …)."""
    from app.crm.identity import map_admission_status

    mapped = map_admission_status(state)
    if mapped is None:
        return False
    return await push_profile(crm_user_id, Admission_Status__c=mapped)


async def push_offer_expired(crm_user_id: str) -> bool:
    """An offer passed its validity date without an answer."""
    return await push_profile(crm_user_id, Offer_Status__c="Expired")
