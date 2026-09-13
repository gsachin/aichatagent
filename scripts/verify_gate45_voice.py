#!/usr/bin/env python
"""
Gates 4 & 5 — the voice channels reach the CRM with the caller's real number.

    python scripts/verify_gate45_voice.py
    python scripts/verify_gate45_voice.py --json

THE PROBLEM THIS PROVES FIXED
-----------------------------
Neither voice path carried a phone number. Twilio Media Streams does not put the
caller's number in the `start` event, the webhooks discarded `From`, and the
post-call extractor never even asks for a phone — so every inbound call created a
lead keyed on the empty string, and nothing could be linked to the CRM.

The fix is a chain, and this gate walks it end to end:

    Twilio GET ?From=…  →  <Parameter name="phone"> on <Stream>  →  the
    WebSocket `start` event  →  the session registry (and, mid-call, the CRM).

WHAT IS AND IS NOT PROVEN HERE
    Proven: the number survives every hop, for both directions, and a real
    WebSocket `start` registers a session carrying it. The registry is read
    in-process — the server runs in this process, as in verify_gate3_whatsapp.py.
    NOT proven: the mid-call CRM link itself, which needs real audio through STT.
    That is covered by tests/test_crm_voice.py and needs one live call to confirm
    for real. This script says so rather than implying otherwise.

EXIT CODES
    0  every clause passed
    1  the server did not start
    2  a clause failed
"""

from __future__ import annotations

import argparse
import asyncio
import json as jsonlib
import os
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover
        pass

PASS, FAIL, INFO, WARN = "PASS", "FAIL", "INFO", "WARN"
CALLER = "+14155550177"          # reserved fictional range
# UUID-shaped, because the outbound linker looks the lead up by id: a non-UUID
# makes Postgres reject the query and logs a spurious error. It matches no row,
# which is the honest state for a synthetic call.
OUTBOUND_LEAD = str(uuid.uuid4())


@dataclass
class Result:
    probe: str
    status: str
    detail: str


@dataclass
class Run:
    results: list[Result] = field(default_factory=list)
    deferred: list[Result] = field(default_factory=list)

    def add(self, probe: str, status: str, detail: str) -> None:
        self.results.append(Result(probe, status, detail))
        if not ARGS.json:
            colour = {PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"}[status] \
                if sys.stdout.isatty() else ""
            reset = "\033[0m" if sys.stdout.isatty() else ""
            print(f"  {colour}{status:<4}{reset}  {probe:<26}  {detail}")

    def defer(self, probe: str, detail: str) -> None:
        self.deferred.append(Result(probe, "DEFER", detail))
        if not ARGS.json:
            print(f"  DEFER  {probe:<26}  {detail}")

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if r.status == FAIL]


def get(url: str) -> tuple[int, str]:
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/xml"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, resp.read().decode()


def start_server(port: int):
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return server
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not come up on 127.0.0.1:{port} within 30s")


def drive_start_event(ws_url: str, *, stream_sid: str, custom: dict, inspect):
    """
    Send the `start` event Twilio sends, with our <Parameter>s on it, and run
    ``inspect()`` while the stream is still open.

    The inspection has to happen *during* the call: the handler ends the session
    in its teardown, so reading the registry after the socket closes finds
    nothing — which looks exactly like the plumbing being broken when it is not.
    """
    import websockets

    async def _run():
        async with websockets.connect(ws_url) as ws:
            await ws.send(jsonlib.dumps({
                "event": "start",
                "streamSid": stream_sid,
                "start": {
                    "streamSid": stream_sid,
                    "callSid": f"CA{uuid.uuid4().hex[:8]}",
                    "customParameters": custom,
                },
            }))
            deadline = time.time() + 15
            while time.time() < deadline:
                await asyncio.sleep(0.25)
                found = inspect()
                if found is not None:
                    return found
            return None

    return asyncio.run(_run())


def main() -> int:
    run = Run()
    ws_base = f"ws://127.0.0.1:{ARGS.port}"
    http_base = f"http://127.0.0.1:{ARGS.port}"

    print(f"\nGates 4 & 5 — voice → CRM live verification")
    print(f"  app     {http_base}   (started by this script)")
    print(f"  caller  {CALLER}\n")

    try:
        start_server(ARGS.port)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    run.add("preflight", INFO, "uvicorn is up (lifespan off — no warm-up needed)")

    from app.crm import session as crm_session
    crm_session.reset()

    # ── C1: the inbound IVR webhook carries the number ───────────────────────
    status, twiml = get(f"{http_base}/twilio/voice?From=%2B{CALLER.lstrip('+')}")
    if status == 200 and f'<Parameter name="phone" value="{CALLER}"' in twiml:
        run.add("C1 ivr webhook", PASS, "caller number on the <Stream>")
    else:
        run.add("C1 ivr webhook", FAIL, f"{status}: no phone parameter in the TwiML")

    # ── C2: and so does the post-IVR connect webhook ─────────────────────────
    status, twiml = get(
        f"{http_base}/twilio/voice/connect?Digits=4&From=%2B{CALLER.lstrip('+')}"
    )
    if status == 200 and f'<Parameter name="phone" value="{CALLER}"' in twiml:
        run.add("C2 connect webhook", PASS, "survives the <Gather> round trip")
    else:
        run.add("C2 connect webhook", FAIL, f"{status}: phone parameter missing")

    # ── C3: a real WS `start` registers the session with that number ─────────
    inbound_sid = f"MZ{uuid.uuid4().hex[:12]}"
    live = drive_start_event(
        f"{ws_base}/ws/twilio",
        stream_sid=inbound_sid,
        custom={"phone": CALLER},
        inspect=lambda: crm_session.get("inbound_call", inbound_sid),
    )
    if live and live.phone_number == CALLER and live.conversation_id:
        run.add("C3 inbound session", PASS,
                f"registered with {CALLER}, conversation {live.conversation_id[:8]}…")
    else:
        run.add("C3 inbound session", FAIL,
                f"session={live and live.phone_number!r}, expected {CALLER!r}")

    # ── C4: outbound carries the lead it dialled ─────────────────────────────
    status, twiml = get(
        f"{http_base}/twilio/outbound-voice"
        f"?leadId={OUTBOUND_LEAD}&phone=%2B{CALLER.lstrip('+')}"
    )
    if (status == 200
            and f'<Parameter name="leadId" value="{OUTBOUND_LEAD}"' in twiml
            and f'<Parameter name="phone" value="{CALLER}"' in twiml):
        run.add("C4 outbound route", PASS, "lead id and number on the <Stream>")
    else:
        run.add("C4 outbound route", FAIL, f"{status}: parameters missing from the TwiML")

    outbound_sid = f"MZ{uuid.uuid4().hex[:12]}"
    live = drive_start_event(
        f"{ws_base}/ws/twilio-outbound",
        stream_sid=outbound_sid,
        custom={"leadId": OUTBOUND_LEAD, "phone": CALLER},
        inspect=lambda: crm_session.get("outbound_call", outbound_sid),
    )
    if live and live.phone_number == CALLER and live.lead_id == OUTBOUND_LEAD:
        run.add("C5 outbound session", PASS, "registered with the lead and number")
    else:
        run.add("C5 outbound session", FAIL,
                f"phone={live and live.phone_number!r} lead={live and live.lead_id!r}")

    # ── what this gate cannot reach ──────────────────────────────────────────
    run.defer("mid-call CRM link",
              "needs real audio through STT — covered by tests/test_crm_voice.py, "
              "confirm once with a live call")
    run.defer("CRM row created",
              "not attempted here: a synthetic call has no name to link with")

    return _finish(run)


def _finish(run: Run) -> int:
    if ARGS.json:
        print(jsonlib.dumps({
            "results": [r.__dict__ for r in run.results],
            "deferred": [r.__dict__ for r in run.deferred],
        }, indent=2))
        return 2 if run.failed else 0

    counts: dict[str, int] = {}
    for r in run.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"\n  {counts.get(PASS, 0)} passed · {counts.get(WARN, 0)} warned · "
          f"{counts.get(FAIL, 0)} failed")
    if run.deferred:
        print("\n  NOT PROVEN HERE:")
        for r in run.deferred:
            print(f"    - {r.probe}: {r.detail}")
    if run.failed:
        print("\n  FAILED:")
        for r in run.failed:
            print(f"    - {r.probe}: {r.detail}")
    print()
    return 2 if run.failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gates 4/5 — voice reaches the CRM.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


ARGS = parse_args()
if not ARGS.port:
    with socket.socket() as _s:
        _s.bind(("127.0.0.1", 0))
        ARGS.port = _s.getsockname()[1]

# The app reads these at import time.
os.environ.setdefault("CRM_ENABLED", "true")

if __name__ == "__main__":
    raise SystemExit(main())
