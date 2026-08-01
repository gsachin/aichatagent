"""
Update the Twilio phone number voice webhook to point to the given tunnel host.

Usage: python scripts/update_twilio_webhook.py <tunnel_host>

Example:
    python scripts/update_twilio_webhook.py myapp.trycloudflare.com
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()
from twilio.rest import Client

PHONE_NUMBER = "+19788198953"


def main(tunnel_host: str) -> int:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        print("MISSING TWILIO CREDENTIALS - check .env file")
        return 1

    client = Client(sid, token)
    try:
        numbers = list(client.incoming_phone_numbers.list(phone_number=PHONE_NUMBER))
        if not numbers:
            print(f"NUMBER {PHONE_NUMBER} NOT FOUND in account")
            return 1

        for n in numbers:
            old_url = n.voice_url
            new_voice_url = f"https://{tunnel_host}/twilio/voice"
            updated = n.update(voice_url=new_voice_url, voice_method="GET")
            print(f"Voice URL: {old_url}")
            print(f"         -> {updated.voice_url}")
            print(f"Method:     {updated.voice_method}")
            print("Voice webhook UPDATED")
    except Exception as e:
        print(f"API ERROR: {e}")
        return 1

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/update_twilio_webhook.py <tunnel_host>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
