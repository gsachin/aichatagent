"""
Offer-letter orchestration — auto-trigger, PDF generation, and sending.

Usage:
    from app.offers.service import generate_and_send_offer
    result = await generate_and_send_offer(lead_id)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("offers.service")


def _resolve_host() -> str:
    """Resolve the public tunnel host for building media URLs."""
    import os as _os

    tunnel = _os.environ.get("TUNNEL_HOST", "")
    if tunnel:
        return tunnel
    tunnel_file = Path(__file__).resolve().parent.parent.parent / ".whatsapp_tunnel"
    if tunnel_file.is_file():
        return tunnel_file.read_text().strip()
    return _os.environ.get("NGROK_HOST", "localhost:8000")


def _wa(num: str) -> str:
    """Ensure WhatsApp prefix is present exactly once."""
    n = str(num or "")
    return n if n.startswith("whatsapp:") else f"whatsapp:{n}"


def _whatsapp_from() -> str:
    """Return the WhatsApp sender number (sandbox or dedicated), falling back to voice number."""
    from app.config import settings
    return settings.TWILIO_WHATSAPP_NUMBER or settings.TWILIO_PHONE_NUMBER


async def generate_and_send_offer(lead_id: str, force: bool = False) -> dict | None:
    """
    Generate a PDF offer letter, send via WhatsApp + email, and return the offer dict.

    Idempotency: skips if an offer was already sent to this lead within 24h,
    unless ``force=True``.

    Returns the offer-letter dict on success, None if skipped or failed.
    """
    from app.config import settings
    from app.leads.models import get_lead
    from app.offers.models import (
        create_offer_letter,
        get_course_by_name,
        get_recent_offer_for_lead,
        update_offer_letter_status,
    )
    from app.offers.pdf import build_offer_pdf

    lead = await get_lead(lead_id)
    if not lead:
        logger.warning(f"generate_and_send_offer: lead {lead_id} not found")
        return None

    lead_name = lead.get("name") or "Prospective Student"
    program_interest = lead.get("program_interest", "")
    lead_email = lead.get("email", "")
    lead_phone = lead.get("phone_number", "")

    if not program_interest:
        logger.info(f"Lead {lead_id}: no program_interest — skipping offer")
        return None

    # ── Idempotency guard ────────────────────────────────────────────
    if not force:
        recent = await get_recent_offer_for_lead(lead_id, within_hours=24)
        if recent:
            logger.info(
                f"Lead {lead_id}: offer already sent within 24h (id={recent['id']}) — skipping"
            )
            return recent

    # ── Match course ─────────────────────────────────────────────────
    course = await get_course_by_name(program_interest)
    if course:
        logger.info(f"Lead {lead_id}: matched course '{course['name']}'")
    else:
        logger.info(f"Lead {lead_id}: no course match for '{program_interest}' — using raw name")

    # ── Compute dates ────────────────────────────────────────────────
    today = date.today()
    offer_date = today.isoformat()
    valid_until = (today + timedelta(days=settings.OFFER_VALID_DAYS)).isoformat()

    # ── Generate PDF ─────────────────────────────────────────────────
    data_dir = Path(settings.DATA_DIR)
    offer_id = None  # placeholder until we insert the row

    # We need the offer id before building the PDF (it's part of the filename).
    # Create the row with an empty pdf_path first, then update.
    offer = await create_offer_letter(
        lead_id=lead_id,
        program=course["name"] if course else program_interest,
        course_id=course["id"] if course else "",
        offer_date=offer_date,
        valid_until=valid_until,
        sent_via=("both" if lead_email else "whatsapp"),
    )
    if not offer:
        logger.error(f"Lead {lead_id}: failed to create offer_letter row")
        return None

    # Build PDF
    try:
        out_dir = data_dir / "offers"
        pdf_path = build_offer_pdf(lead, course, offer, out_dir)

        # Update the row with the actual pdf_path
        from app.offers.models import update_offer_letter_status
        offer = await update_offer_letter_status(offer["id"], "sent", pdf_path=str(pdf_path))
        if offer is None:
            logger.warning("Failed to update offer after PDF gen — continuing")
        else:
            offer["pdf_path"] = str(pdf_path)
    except Exception:
        logger.exception(f"Lead {lead_id}: PDF generation failed")
        return offer  # still return the row for debugging

    # ── Send via WhatsApp ────────────────────────────────────────────
    if lead_phone:
        host = _resolve_host()
        if host and host != "localhost:8000":
            try:
                media_url = f"https://{host}/api/offers/{offer['id']}/pdf"
                # Resolve payment link
                payment_link = (course or {}).get("payment_link", "") or settings.DEFAULT_PAYMENT_LINK
                payment_section = ""
                if payment_link:
                    payment_section = (
                        f"\n\n💳 *Secure Your Seat:*\n"
                        f"Complete payment here: {payment_link}\n"
                        f"Amount: {(course or {}).get('fees', 'See fee structure')}"
                    )
                body = (
                    f"Congratulations {lead_name}! 🎓\n\n"
                    f"Your offer letter for the *{offer['program']}* program is attached. "
                    f"Please review it and reply *ACCEPT* or *DECLINE*.\n\n"
                    f"This offer is valid until {valid_until}."
                    f"{payment_section}"
                )
                from app.messaging import send_whatsapp_message
                ok, sid = send_whatsapp_message(
                    _wa(lead_phone),
                    _wa(_whatsapp_from()),
                    body,
                    media_url=media_url,
                )
                if ok:
                    await update_offer_letter_status(
                        offer["id"], "sent", message_sid=sid, sent_at=offer.get("sent_at", "")
                    )
                    logger.info(f"Offer {offer['id']}: WhatsApp sent → {lead_phone} (sid={sid})")
                else:
                    logger.error(f"Offer {offer['id']}: WhatsApp send failed — {sid}")
            except Exception:
                logger.exception(f"Offer {offer['id']}: WhatsApp send exception")
        else:
            logger.warning("No tunnel host configured — cannot send WhatsApp media")

    # ── Send via Email ───────────────────────────────────────────────
    if lead_email:
        try:
            from app.emailer import send_email

            payment_link = (course or {}).get("payment_link", "") or settings.DEFAULT_PAYMENT_LINK
            payment_section = ""
            if payment_link:
                payment_section = (
                    f"\n💳 Payment Link: {payment_link}\n"
                    f"   Amount: {(course or {}).get('fees', 'See fee structure')}\n\n"
                )

            subject = f"Offer of Admission — {offer['program']}"
            body = (
                f"Dear {lead_name},\n\n"
                f"Congratulations! Your offer of admission for the {offer['program']} "
                f"program is attached to this email.\n\n"
                f"Offer Date: {offer_date}\n"
                f"Valid Until: {valid_until}\n\n"
                f"{payment_section}"
                f"Please reply to this email to accept or decline the offer.\n\n"
                f"Sincerely,\nAdmissions Office\n{settings.OFFER_EMAIL}"
            )
            ok = send_email(lead_email, subject, body, pdf_path=str(pdf_path))
            if ok:
                await update_offer_letter_status(
                    offer["id"], "sent", email_id="sent"
                )
                logger.info(f"Offer {offer['id']}: email sent → {lead_email}")
            else:
                logger.warning(f"Offer {offer['id']}: email send failed")
        except Exception:
            logger.exception(f"Offer {offer['id']}: email send exception")

    # ── Log conversation ─────────────────────────────────────────────
    try:
        from app.leads.models import create_conversation
        await create_conversation(
            lead_id=lead_id,
            phone_number=lead_phone,
            channel="whatsapp",
            transcript=f"Offer letter ({offer['program']}) generated and sent to {lead_name}",
            summary=f"Offer letter sent for {offer['program']}",
            outcome="offer_sent",
        )
    except Exception:
        logger.exception("Failed to log offer conversation")

    return offer
