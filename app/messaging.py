"""
Shared Twilio WhatsApp message sender.

Extracted from app/main.py so that app/offers/service.py can import it
without creating a circular dependency (main → offers/service → main).

Usage:
    from app.messaging import send_whatsapp_message
    ok, sid = send_whatsapp_message("+91...", "+1...", "Hello", media_url="https://...")
"""

from __future__ import annotations

import logging

logger = logging.getLogger("messaging")


def send_whatsapp_message(
    to_number: str,
    from_number: str,
    body: str,
    media_url: str | None = None,
) -> tuple[bool, str]:
    """
    Send a WhatsApp message via the Twilio REST API, optionally with media.

    Returns:
        (True, message_sid) on success
        (False, error_message) on failure
    """
    try:
        from twilio.rest import Client
        from app.config import settings

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        kwargs = {"from_": from_number, "body": body, "to": to_number}
        if media_url:
            kwargs["media_url"] = [media_url]

        msg = client.messages.create(**kwargs)
        extra = " + media" if media_url else ""
        logger.info(f"Twilio message sent: {msg.sid} → {to_number}{extra}")
        return True, msg.sid
    except Exception as e:
        logger.exception(f"Twilio send failed: {e}")
        return False, str(e)
