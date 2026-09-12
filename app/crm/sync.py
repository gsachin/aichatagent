"""
The two things this app ever asks the CRM to do.

    lookup_or_create(identity) -> crm_user_id | None
    push_status(crm_user_id, ...) -> bool

Everything the channels need goes through these. Both are safe to call from a
live call path:

* they never raise — a CRM problem must not affect a student's call;
* they return immediately when ``CRM_ENABLED`` is false, without touching the
  network (Phase 2 gate);
* they log what they did in terms an operator can act on.

The failure handling differs per call, and deliberately so:

``lookup_or_create``
    A failure means "this conversation has no CRM link". Recoverable by doing
    nothing — the conversation still works, it just is not in the CRM. Not
    queued for replay (see ``outbox``).

``push_status``
    A failure means a business fact is lost — a student accepted an offer and
    nobody in the CRM knows. A *transient* failure is queued for replay; a
    *permanent* one is dropped and logged, because retrying a
    restricted-picklist violation forever is what a naive outbox does.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.crm import identity as identity_mod
from app.crm import outbox
from app.crm import session as session_mod
from app.crm.client import (
    CrmCircuitOpen,
    CrmError,
    CrmPermanentError,
    CrmTransientError,
    CrmUnknownStateError,
    get_client,
)
from app.crm.identity import ChannelIdentity

logger = logging.getLogger("crm.sync")


def enabled() -> bool:
    """The Phase 2 gate. False means every entry point is a no-op."""
    return bool(settings.CRM_ENABLED)


# ── lookup-or-create ─────────────────────────────────────────────────────────

async def lookup_or_create(identity: ChannelIdentity) -> str | None:
    """
    Find this person in the CRM, or create them. Returns the ``userId``.

    Returns None — never raises — when the CRM is disabled, the identity is not
    safe to send, or the call fails. Callers treat None as "no CRM link".
    """
    if not enabled():
        logger.debug("crm.sync: CRM disabled — skipping lookup")
        return None

    if not identity.is_sendable:
        # Not an error — this is the normal state early in a conversation. The
        # defence is what stops R2 (blank identifier), R17 (blank name) and
        # orphaned records (blank conversation id) from ever reaching the API.
        logger.info(
            f"crm.sync: {identity.channel} not ready for the CRM yet — "
            f"missing {', '.join(identity.missing_for_send)}"
        )
        return None

    payload = identity.lookup_payload()
    client = get_client()

    async def _do_lookup() -> str | None:
        try:
            data = await client.post("/users/lookup-or-create", payload, is_create=True)
        except CrmUnknownStateError as exc:
            # R4: the record may exist and the response failed to serialise.
            # Re-issuing the SAME lookup is safe — the API finds before it
            # creates — and either returns the existing record or creates the
            # one that is genuinely missing.
            logger.warning(
                f"crm.sync: lookup returned an unknown state ({exc}); re-issuing once"
            )
            try:
                data = await client.post("/users/lookup-or-create", payload, is_create=True)
            except CrmError as retry_exc:
                logger.error(
                    f"crm.sync: lookup still failed after re-issue for "
                    f"{identity.describe()} — {retry_exc}"
                )
                return None
        except CrmCircuitOpen as exc:
            logger.warning(f"crm.sync: {exc}")
            return None
        except CrmPermanentError as exc:
            logger.error(
                f"crm.sync: lookup permanently rejected for {identity.describe()} — {exc}"
            )
            return None
        except CrmError as exc:
            logger.error(f"crm.sync: lookup failed for {identity.describe()} — {exc}")
            return None

        user_id = (data or {}).get("userId")
        if not user_id:
            logger.error(
                f"crm.sync: lookup for {identity.describe()} returned no userId: {data!r}"
            )
            return None
        return user_id

    user_id = await client.single_flight(identity.dedupe_key(), _do_lookup)

    if user_id:
        logger.info(f"crm.sync: {identity.describe()} -> userId {user_id}")
    return user_id


# ── channel entry point ──────────────────────────────────────────────────────

async def link_conversation(
    *,
    channel: str,
    conversation_id: str = "",
    lead: dict | None = None,
    phone_number: object = "",
    email: object = "",
    name: object = "",
    course: object = "",
    session_key: str = "",
) -> str | None:
    """
    Make sure the person in this conversation exists in the CRM. Returns the
    ``userId``, or None if they are not (yet) linkable.

    Every channel calls this once per turn. It is cheap when it has nothing to
    do — a lead that is already linked returns its cached id without touching
    the network, which is what keeps a chatty WhatsApp thread from re-asking the
    API on every message.

    It will decline to link until the identity is complete enough to be safe
    (see :attr:`ChannelIdentity.is_sendable`), and try again on the next turn.
    That is the intended behaviour, not a failure: a WhatsApp student is asked
    for their name before anything else, so the first message usually cannot
    link and the second usually can.

    Never raises.
    """
    if not enabled():
        return None

    lead = lead or {}
    lead_id = str(lead.get("id") or "")

    # Already linked — the common case from turn two onward. The lead dict does
    # not carry crm_user_id (see get_lead_crm_user_id for why), but a caller that
    # already knows it can pass it through the dict to save the lookup.
    existing = str(lead.get("crm_user_id") or "")
    if not existing and lead_id:
        try:
            from app.leads.models import get_lead_crm_user_id

            existing = await get_lead_crm_user_id(lead_id)
        except Exception:
            logger.exception("crm.sync: could not read the existing CRM link")
    if existing:
        return existing

    try:
        ident = identity_mod.from_channel(
            channel,
            conversation_id=conversation_id or str(lead.get("conversation_id") or ""),
            phone_number=phone_number or lead.get("phone_number") or "",
            email=email or lead.get("email") or "",
            name=name or lead.get("name") or "",
            course=course or lead.get("program_interest") or "",
            lead_id=lead_id,
        )
    except Exception:
        logger.exception("crm.sync: could not build an identity from the lead")
        return None

    user_id = await lookup_or_create(ident)
    if not user_id:
        return None

    # Persist so the next turn short-circuits, and so the status pushes in
    # Phase 7 have a userId to address.
    if lead_id:
        try:
            from app.leads.models import set_lead_crm_user_id

            await set_lead_crm_user_id(lead_id, user_id)
        except Exception:
            logger.exception("crm.sync: could not persist crm_user_id on the lead")

    # Keep the live session in step so the rest of this conversation can read
    # the id without another database round trip.
    #
    # The key must be the one the session was registered under, which is NOT
    # always the normalised phone: Twilio WhatsApp sends `whatsapp:+1415...` and
    # the registry stores that verbatim, so normalising here would silently miss.
    # Callers pass their own key; the normalised number is only a fallback.
    key = session_key or ident.phone_number
    if key:
        live = session_mod.get(channel, key)
        if live is not None:
            live.crm_user_id = user_id

    return user_id


# ── status ───────────────────────────────────────────────────────────────────

async def push_status(
    crm_user_id: str,
    *,
    sentiment: object = None,
    offer_letter_released: bool | None = None,
    offer_letter_accepted: object = None,
    conversation_id: str = "",
) -> bool:
    """
    Update the three writable fields on a user. Returns True if the CRM took it.

    Values are mapped into the CRM's restricted picklists before sending; an
    unmappable value is refused here rather than sent to fail with a 500 that
    looks transient (R16). See ``identity.map_sentiment``.

    A transient failure is queued for replay. A permanent one is logged and
    dropped.
    """
    if not enabled():
        logger.debug("crm.sync: CRM disabled — skipping status push")
        return False

    if not crm_user_id:
        logger.warning("crm.sync: status push with no userId — nothing to update")
        return False

    payload: dict[str, object] = {}

    if sentiment is not None:
        mapped = identity_mod.map_sentiment(sentiment)
        if mapped is None:
            logger.warning(
                f"crm.sync: refusing to send unmappable sentiment {sentiment!r} "
                f"for user {crm_user_id}"
            )
        else:
            payload["sentiment"] = mapped

    if offer_letter_released is not None:
        payload["offerLetterReleased"] = bool(offer_letter_released)

    if offer_letter_accepted is not None:
        mapped_accepted = identity_mod.map_offer_accepted(offer_letter_accepted)
        if mapped_accepted is None:
            logger.warning(
                f"crm.sync: refusing to send unmappable offerLetterAccepted "
                f"{offer_letter_accepted!r} for user {crm_user_id}"
            )
        else:
            payload["offerLetterAccepted"] = mapped_accepted

    if not payload:
        logger.info(f"crm.sync: no valid status fields for user {crm_user_id} — nothing sent")
        return False

    return await _send_status(crm_user_id, payload, conversation_id=conversation_id)


async def _send_status(
    crm_user_id: str, payload: dict, *, conversation_id: str = ""
) -> bool:
    client = get_client()
    try:
        await client.patch(f"/users/{crm_user_id}/status", payload)
    except CrmPermanentError as exc:
        # Never queue these. A restricted-picklist value will fail identically
        # on every replay (A11).
        logger.error(
            f"crm.sync: status update permanently rejected for {crm_user_id} "
            f"({payload}) — not queued: {exc}"
        )
        return False
    except CrmCircuitOpen as exc:
        logger.warning(f"crm.sync: {exc} — queueing status for {crm_user_id}")
        await outbox.enqueue(
            "status", payload, crm_user_id=crm_user_id, conversation_id=conversation_id,
            error=str(exc),
        )
        return False
    except (CrmTransientError, CrmUnknownStateError, CrmError) as exc:
        logger.warning(
            f"crm.sync: status update failed for {crm_user_id} ({payload}) — queueing: {exc}"
        )
        await outbox.enqueue(
            "status", payload, crm_user_id=crm_user_id, conversation_id=conversation_id,
            error=str(exc),
        )
        return False

    logger.info(f"crm.sync: status updated for {crm_user_id} — {payload}")
    return True


# ── replay ───────────────────────────────────────────────────────────────────

async def replay(entry: dict) -> bool:
    """
    Re-send one queued write. Used by ``outbox.drain``, which passes the whole
    outbox row — the target user lives in its own column, not inside the body.

    Raises ``CrmPermanentError`` so the outbox drops the entry rather than
    retrying a write that can never succeed.
    """
    op = entry.get("op")
    if op != "status":
        raise CrmPermanentError(f"unsupported outbox op {op!r}")

    user_id = entry.get("crm_user_id") or ""
    if not user_id:
        raise CrmPermanentError("queued status entry has no crm_user_id")

    body = entry.get("payload")
    if not isinstance(body, dict) or not body:
        raise CrmPermanentError(f"queued status entry has no usable payload: {body!r}")

    await get_client().patch(f"/users/{user_id}/status", body)
    logger.info(f"crm.sync: replayed queued status for {user_id} — {body}")
    return True


async def flush_outbox(limit: int = 50) -> dict[str, int]:
    """Drain the outbox. Called by the scheduler and available for operators."""
    if not enabled():
        return {"attempted": 0, "succeeded": 0, "failed": 0, "dropped": 0}
    return await outbox.drain(limit, max_attempts=settings.CRM_OUTBOX_MAX_ATTEMPTS)
