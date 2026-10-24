"""Check whether Twilio's WhatsApp Sandbox is reaching this demo.

Why this exists
---------------
The WhatsApp Sandbox webhook is console-only: Twilio has no API for it, so
every tunnel restart needs a manual repoint (see update_twilio_webhook.py).
Until that is done, the registered host is the *previous* tunnel's hostname,
Cloudflare answers the webhook with HTTP 404, and Twilio records the inbound
message with error 11210. From the phone this looks like "my first 'hi' did
nothing, I had to send it twice".

This script makes that state visible before a demo:

* prints the tunnel host this checkout is currently using and the exact
  Sandbox URL to register;
* checks that the host actually reaches the app (GET /twilio/whatsapp);
* lists recent inbound WhatsApp messages and the ones Twilio could not deliver
  to the webhook (11200 timeout, 11210 HTTP 404).

Usage:
    python scripts/demo/check_whatsapp_webhook.py [--minutes 60]

Exit codes: 0 = no recent webhook failures, 2 = failures found,
1 = the check itself could not run (missing credentials/tunnel).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

def _find_demo_root() -> Path:
    """The directory holding .env — works in the dev tree and in the demo copy.

    In the dev tree this file lives at ``scripts/demo/``; in the frozen demo it
    is copied to the demo root next to ``demo_start.ps1`` and ``.env``.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent, here.parent.parent):
        if (candidate / ".env").is_file() or (candidate / ".whatsapp_tunnel").is_file():
            return candidate
    return here


DEMO_ROOT = _find_demo_root()
ENV_FILE = DEMO_ROOT / ".env"
TUNNEL_FILE = DEMO_ROOT / ".whatsapp_tunnel"

#: Twilio errors that mean "the message was received but our webhook did not
#: take it": 11200 = webhook timeout / connection failure, 11210 = HTTP 404
#: (the classic stale-tunnel signature).
WEBHOOK_ERRORS = {
    11200: "webhook timed out or could not be reached",
    11210: "webhook returned HTTP 404 (stale tunnel host)",
}


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def tunnel_host() -> str:
    host = os.environ.get("TUNNEL_HOST", "").strip()
    if not host and TUNNEL_FILE.is_file():
        host = TUNNEL_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    return host.removeprefix("https://").removeprefix("http://").strip("/")


def _auth_header(sid: str, token: str) -> dict[str, str]:
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def fetch_messages(sid: str, token: str, limit: int = 50) -> list[dict]:
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        f"?PageSize={limit}"
    )
    request = urllib.request.Request(url, headers=_auth_header(sid, token))
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response).get("messages", [])


def fetch_alerts(sid: str, token: str, limit: int = 50) -> list[dict]:
    """Monitor alerts carry the exact URL Twilio tried; messages do not."""
    url = f"https://monitor.twilio.com/v1/Alerts?PageSize={limit}"
    request = urllib.request.Request(url, headers=_auth_header(sid, token))
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response).get("alerts", [])


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_tunnel(host: str) -> bool:
    if not host:
        print("  [FAIL] no tunnel host (.whatsapp_tunnel is empty)")
        return False
    url = f"https://{host}/twilio/whatsapp"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "whatsapp-webhook-check"})
        with urllib.request.urlopen(request, timeout=8) as response:
            ok = 200 <= response.status < 400
        print(f"  [{'OK' if ok else 'FAIL'}] {url} -> HTTP {response.status}")
        return ok
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            # The host reached a FastAPI app that only had the POST route
            # (before the GET health route shipped). Still a live host.
            print(f"  [OK] {url} -> HTTP 405 (host reaches the app; POST route present)")
            return True
        print(f"  [FAIL] {url} -> HTTP {exc.code}")
        return False
    except Exception as exc:  # noqa: BLE001 - the check must never crash a demo
        print(f"  [FAIL] {url} -> {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--minutes", type=int, default=60,
        help="how far back to look for failed inbound messages (default 60)",
    )
    args = parser.parse_args()

    env = read_dotenv(ENV_FILE)
    sid = os.environ.get("TWILIO_ACCOUNT_SID") or env.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN") or env.get("TWILIO_AUTH_TOKEN", "")

    host = tunnel_host()
    print("WhatsApp Sandbox webhook check")
    print(f"  current tunnel: https://{host or '(none)'}")
    if host:
        print("  register this in the Twilio Console (HTTP POST):")
        print(f"    https://{host}/twilio/whatsapp")
    print("  console: https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn")
    print()
    print("Tunnel reachability:")
    tunnel_ok = check_tunnel(host)

    if not sid or not token:
        print("\n  [FAIL] TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing from .env")
        return 1

    try:
        messages = fetch_messages(sid, token)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [FAIL] could not read the Twilio message log: {exc}")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
    inbound = [
        m for m in messages
        if str(m.get("direction", "")).startswith("inbound")
        and str(m.get("from", "")).startswith("whatsapp:")
    ]

    failed = []
    recent = []
    for message in inbound:
        created = message.get("date_created")
        if not created:
            continue
        try:
            when = parsedate_to_datetime(created)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            continue
        recent.append(message)
        code = message.get("error_code")
        if code in WEBHOOK_ERRORS:
            failed.append((when, message.get("body", ""), code))

    print(f"\nInbound WhatsApp messages in the last {args.minutes} min: {len(recent)}")
    if not recent:
        print("  [INFO] no inbound messages in that window — send a test 'hi' after repointing")
    if failed:
        print(f"  [WARN] {len(failed)} message(s) never reached the webhook:")
        for when, body, code in failed[-10:]:
            preview = (body or "").replace("\n", " ")[:40]
            print(
                f"    {when.astimezone().strftime('%Y-%m-%d %H:%M')} "
                f"error {code} ({WEBHOOK_ERRORS[code]}): {preview!r}"
            )
        print(
            "\n  This is the \"type hi twice\" symptom. The Sandbox webhook is\n"
            "  pointing at a host that no longer reaches this app. Set it to the\n"
            "  URL printed above, method HTTP POST, then send a test message."
        )
    elif recent:
        print("  [OK] no webhook delivery failures (11200/11210) in that window")

    # The Messages API says a message failed; Monitor carries the exact URL
    # Twilio POSTed to. That URL is the stale-tunnel smoking gun, and it is the
    # one thing the Console-only Sandbox setting will not show you from here.
    stale_target = ""
    try:
        alerts = fetch_alerts(sid, token)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [INFO] could not read Monitor alerts: {exc}")
        alerts = []

    recent_alerts = []
    for alert in alerts:
        when = _parse_iso(alert.get("date_created"))
        if when is None or when < cutoff:
            continue
        if str(alert.get("error_code")) in {str(c) for c in WEBHOOK_ERRORS}:
            recent_alerts.append((when, alert))

    if recent_alerts:
        recent_alerts.sort(key=lambda item: item[0])
        url = recent_alerts[-1][1].get("request_url") or ""
        target_host = (
            url.removeprefix("https://").removeprefix("http://").split("/")[0]
        )
        print(f"\nTwilio's latest failed webhook target: {url or '(not reported)'}")
        if host and target_host and target_host != host:
            stale_target = target_host
            print(f"  [WARN] STALE: Twilio is still posting to {target_host}")
            print(f"         the current tunnel is {host}")
            print(
                "\n  The \"You are all set\" sandbox message only confirms the\n"
                "  JOIN; it does not change the webhook. Set 'When a message\n"
                f"  comes in' to https://{host}/twilio/whatsapp (HTTP POST)."
            )
        elif host:
            print(
                "  [INFO] the target host matches the current tunnel — check the\n"
                "         path (/twilio/whatsapp) and method (POST)."
            )

    if not tunnel_ok:
        print("\n  The current tunnel itself is not answering the health check.")
        print("  Restart the demo tunnel before repointing the Sandbox webhook.")

    return 2 if failed or not tunnel_ok or stale_target else 0


if __name__ == "__main__":
    sys.exit(main())
