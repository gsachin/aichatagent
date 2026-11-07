"""
Update the Twilio phone number webhooks to point to the given tunnel host.

Updates (with read-back verification):
  1. Voice webhook      -> https://<tunnel_host>/twilio/voice            (GET)
  2. Status callback    -> https://<tunnel_host>/twilio/outbound/status  (POST)

WhatsApp note: the WhatsApp Sandbox webhook has NO Twilio API (console-only),
so this script prints the exact manual steps instead. If a Messaging Service
is ever adopted, add its update here (client.messaging.v1.services(SID).update).

Usage: python scripts/update_twilio_webhook.py <tunnel_host>

Example:
    python scripts/update_twilio_webhook.py myapp.trycloudflare.com
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from twilio.rest import Client

DEFAULT_PHONE_NUMBER = "+19788198953"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def _update_with_retry(number, **kwargs):
    """Call number.update() with retries on transient API errors."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return number.update(**kwargs)
        except Exception as e:  # network blips / 5xx from Twilio
            last_exc = e
            if attempt < MAX_RETRIES:
                print(f"    retry {attempt}/{MAX_RETRIES} after: {e}")
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_exc


def main(tunnel_host: str) -> int:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        print("MISSING TWILIO CREDENTIALS - check .env file")
        return 1

    phone_number = os.environ.get("TWILIO_PHONE_NUMBER", DEFAULT_PHONE_NUMBER)
    voice_url = f"https://{tunnel_host}/twilio/voice"
    status_url = f"https://{tunnel_host}/twilio/outbound/status"

    client = Client(sid, token)
    try:
        numbers = list(client.incoming_phone_numbers.list(phone_number=phone_number))
        if not numbers:
            print(f"NUMBER {phone_number} NOT FOUND in account")
            return 1

        # ── 1. Voice webhook ──────────────────────────────────────────
        for n in numbers:
            old_voice = n.voice_url
            updated = _update_with_retry(n, voice_url=voice_url, voice_method="GET")
            print(f"Voice URL: {old_voice}")
            print(f"         -> {updated.voice_url}")
            print(f"Method:     {updated.voice_method}")

        # ── 2. Outbound status callback ───────────────────────────────
        for n in numbers:
            old_status = n.status_callback
            updated = _update_with_retry(
                n,
                status_callback=status_url,
                status_callback_method="POST",
            )
            print(f"Status URL: {old_status or '(none)'}")
            print(f"         -> {updated.status_callback}")
            print(f"Method:     {updated.status_callback_method}")

        # ── 3. Read-back verification (fresh fetch, not the update echo)
        verified = True
        for n in client.incoming_phone_numbers.list(phone_number=phone_number):
            if n.voice_url != voice_url:
                print(f"VERIFY FAIL: voice_url={n.voice_url!r} != {voice_url!r}")
                verified = False
            if n.status_callback != status_url:
                print(f"VERIFY FAIL: status_callback={n.status_callback!r} != {status_url!r}")
                verified = False
        if verified:
            print("Verified: voice + status callbacks match the tunnel host")

        # ── 4. WhatsApp sandbox (manual — no Twilio API for the sandbox)
        whatsapp_url = f"https://{tunnel_host}/twilio/whatsapp"
        print("")
        print("WhatsApp Sandbox (manual step — Twilio has no API for the sandbox):")
        print(f"  1. Open https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn")
        print(f"  2. Set 'When a message comes in' to: {whatsapp_url}")
        print("  3. Method: HTTP POST")

        return 0 if verified else 1
    except Exception as e:
        print(f"API ERROR: {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/update_twilio_webhook.py <tunnel_host>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
