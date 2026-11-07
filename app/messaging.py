"""
Shared Twilio WhatsApp message sender.

Extracted from app/main.py so that app/offers/service.py can import it
without creating a circular dependency (main → offers/service → main).

Usage:
    from app.messaging import send_whatsapp_message
    ok, sid = send_whatsapp_message("+91...", "+1...", "Hello", media_url="https://...")
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("messaging")


def _masked(number: str) -> str:
    """Last four digits only; full WhatsApp numbers are not logged."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return f"***{digits[-4:]}" if digits else "***"


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
        logger.info(
            f"Twilio message sent: {msg.sid} → {_masked(to_number)}{extra}"
        )
        return True, msg.sid
    except Exception as e:
        logger.exception(f"Twilio send failed: {e}")
        return False, str(e)


def send_whatsapp_template(
    to_number: str,
    from_number: str,
    content_sid: str,
    variables: dict | None = None,
) -> tuple[bool, str]:
    """Send an approved WhatsApp content template.

    The template must be created in Twilio with ``{{1}}`` as its single
    variable; the delivery worker passes the rendered reference message there.
    """
    try:
        from twilio.rest import Client
        from app.config import settings

        if not content_sid:
            return False, "content_sid is required"
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        kwargs = {
            "from_": from_number,
            "to": to_number,
            "content_sid": content_sid,
        }
        if variables:
            kwargs["content_variables"] = json.dumps(variables)
        msg = client.messages.create(**kwargs)
        logger.info(
            f"Twilio template sent: {msg.sid} → {_masked(to_number)}"
        )
        return True, msg.sid
    except Exception as e:
        logger.exception(f"Twilio template send failed: {e}")
        return False, str(e)
