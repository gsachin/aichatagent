#!/usr/bin/env python
"""
Gate 3 — live verification that WhatsApp creates ONE CRM user and reuses it.

Gate 3 as written in the plan:

    a real WhatsApp thread creates exactly one Customer row with the right
    Course__c and Conversation_ID__c; a second thread from the same number
    reuses it and does not duplicate.

This drives the **real** ``/twilio/whatsapp`` route with simulated Twilio form
posts (no HTTP server needed — FastAPI's TestClient dispatches straight to the
route, and deliberately without the lifespan, so no Whisper/Chroma warmup), then
reads the outcome back through ``GET /admissions``.

It writes to the CRM. Every record it creates is marked by a reserved fictional
phone number and deleted again in a finally block.

USAGE
    set CRM_ENABLED=true            # the whole integration is inert without it
    python scripts/verify_gate3_whatsapp.py [--base-url http://127.0.0.1:8098]

EXIT CODES
    0  every clause passed
    2  at least one clause failed
    1  preflight failed (API unreachable, or the test number is already in use)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Run from anywhere: the script imports the app package, so the repo root — which
# is this file's parent's parent — has to be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The app must believe the CRM is on, or every call site short-circuits.
os.environ.setdefault("CRM_ENABLED", "true")
os.environ.setdefault("USE_MCP_RAG", "off")  # never touch the MCP service

# Windows consoles default to cp1252 and choke on the arrows/box-drawing below.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover
        pass

# Reserved for fiction (NANP 555-0100..0199); chosen so nothing real is touched.
THREAD1_PHONE = "+14155550197"   # driven through the webhook
THREAD2_PHONE = "+14155550198"   # second thread, same person, new conversation
COURSE_PHONE = "+14155550199"    # direct link, to isolate the course question

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"
_results: list[tuple[str, str, str]] = []


def record(clause: str, status: str, detail: str) -> None:
    _results.append((clause, status, detail))
    colour = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m", "INFO": "\033[36m"}[status]
    reset = "\033[0m" if sys.stdout.isatty() else ""
    if not sys.stdout.isatty():
        colour = ""
    print(f"  {colour}{status:<4}{reset}  {clause:<34}  {detail}")


# ── CRM access ───────────────────────────────────────────────────────────────

def get_rows(base_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/admissions", timeout=30) as resp:
        return json.load(resp)


def delete_user(base_url: str, user_id: str) -> bool:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/users/{user_id}", method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as exc:
        print(f"    cleanup failed for {user_id}: HTTP {exc.code}")
        return False


def rows_for(rows: list[dict], phone: str) -> list[dict]:
    return [r for r in rows if (r.get("Phone__c") or "") == phone]


# ── the simulated thread ─────────────────────────────────────────────────────

def post_whatsapp(base_url: str, sender: str, body: str) -> int:
    """POST the webhook exactly as Twilio would."""
    payload = urllib.parse.urlencode({
        "From": f"whatsapp:{sender}",
        "Body": body,
        "WaId": sender.lstrip("+"),
        "NumMedia": "0",
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/twilio/whatsapp",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def start_server(port: int):
    """
    Run the real app under uvicorn in a background thread.

    A real server is the faithful way to drive the webhook: it owns exactly one
    event loop, which is how production runs. TestClient would work, but it
    spins up a fresh loop per request, so the shared httpx client gets rebuilt
    every time and connection pooling is never actually exercised.

    ``lifespan="off"`` skips the startup warm-up (Whisper, ChromaDB, the RAG
    index) — none of which the WhatsApp text path needs, and which would add
    a minute to every run.
    """
    import threading
    import time

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import socket

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return server, thread
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not come up on 127.0.0.1:{port} within 30s")


def main() -> int:
    global ARGS
    ARGS = parse_args()

    print(f"\nGate 3 — WhatsApp → CRM live verification")
    print(f"  base URL   {ARGS.base_url}")
    print(f"  thread 1   whatsapp:{THREAD1_PHONE}  (multi-message)\n")

    # ── preflight ────────────────────────────────────────────────────────────
    try:
        before = get_rows(ARGS.base_url)
    except Exception as exc:
        print(f"FATAL: CRM API unreachable at {ARGS.base_url} — {exc}", file=sys.stderr)
        return 1

    for phone in (THREAD1_PHONE, THREAD2_PHONE, COURSE_PHONE):
        if rows_for(before, phone):
            print(f"FATAL: test number {phone} already exists in the CRM.", file=sys.stderr)
            print("       Pick another number before running this.", file=sys.stderr)
            return 1
    print(f"  CRM reachable — {len(before)} rows, all three test numbers free\n")

    import socket

    from app.crm import session as crm_session

    created_ids: list[str] = []

    # A free port, so this never collides with a running instance.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app_url = f"http://127.0.0.1:{port}"

    server, thread = start_server(port)
    print(f"  app under test on {app_url} (lifespan off)\n")

    try:
        crm_session.reset()

        # ── thread 1: a realistic conversation ───────────────────────────────
        conversation = [
            "hi",                       # greeting — nothing to link with yet
            "Ana",                      # the name arrives; first linkable turn
            "ana@example.com",          # email
            "I want to take admission", # intent
            "MBA",                      # program
        ]
        print("  Driving the webhook:")
        for body in conversation:
            status = post_whatsapp(app_url, THREAD1_PHONE, body)
            state = "ok" if status == 200 else f"HTTP {status}"
            print(f"    -> {body!r:<28} {state}")
            if status != 200:
                record("webhook/responds", FAIL, f"{body!r} returned {status}")
                return 2

        after = get_rows(ARGS.base_url)
        mine = rows_for(after, THREAD1_PHONE)

        # C0 — did the link actually SUCCEED? This is the clause that matters,
        # and the one a CRM-side row count cannot detect: the API can create the
        # record and still fail the response, leaving us with no userId.
        live = crm_session.get("whatsapp", f"whatsapp:{THREAD1_PHONE}")
        linked = (live.crm_user_id if live else "") or ""
        if linked:
            record("C0 link returned a userId", PASS, f"userId={linked}")
        else:
            record(
                "C0 link returned a userId", FAIL,
                "no userId — see C1; a row may exist anyway, which is R4",
            )

        # C1 — exactly one record
        if len(mine) == 1:
            record("C1 one row created", PASS, f"1 row for {THREAD1_PHONE}")
        elif not mine:
            record("C1 one row created", FAIL, "no row was created")
            return 2
        else:
            record("C1 one row created", FAIL, f"{len(mine)} rows — duplicated")

        row = mine[0]
        created_ids.append(row["Id"])

        # C2 — the phone is stored bare, without Twilio's prefix
        raw = row.get("Phone__c") or ""
        if raw == THREAD1_PHONE:
            record("C2 phone normalised", PASS, f"stored as {raw!r} (no whatsapp: prefix)")
        else:
            record("C2 phone normalised", FAIL, f"stored as {raw!r}, expected {THREAD1_PHONE!r}")

        # C3 — the conversation is linked
        conv = row.get("Conversation_ID__c") or ""
        live = crm_session.get("whatsapp", f"whatsapp:{THREAD1_PHONE}")
        if conv and live and conv == live.conversation_id:
            record("C3 conversation linked", PASS, f"Conversation_ID__c={conv}")
        elif conv:
            record("C3 conversation linked", WARN, f"set to {conv}, session says "
                                                   f"{live.conversation_id if live else '—'}")
        else:
            record("C3 conversation linked", FAIL, "Conversation_ID__c is empty")

        # C4 — the course. Gate 3 asks for "the right Course__c".
        course = row.get("Course__c")
        if course:
            record("C4 course captured", PASS, f"Course__c={course!r}")
        else:
            record(
                "C4 course captured", FAIL,
                "Course__c is empty — the API accepts course only at CREATE time, "
                "and the link happens before the program is known",
            )

        # ── thread 2: same person, a NEW conversation ────────────────────────
        # Clearing the registry is what a new session (past the idle window)
        # looks like to the resolver.
        crm_session.reset()
        post_whatsapp(app_url, THREAD1_PHONE, "Ana")
        post_whatsapp(app_url, THREAD1_PHONE, "Still interested in the MBA")

        after2 = get_rows(ARGS.base_url)
        mine2 = rows_for(after2, THREAD1_PHONE)

        if len(mine2) == 1:
            record("C5 second thread reuses", PASS, "still exactly 1 row — no duplicate")
        else:
            record("C5 second thread reuses", FAIL, f"{len(mine2)} rows — duplicated")
            for extra in mine2:
                if extra["Id"] not in created_ids:
                    created_ids.append(extra["Id"])

        # R6 — expected: the CRM keeps the FIRST conversation, not the newest.
        conv2 = mine2[0].get("Conversation_ID__c") if mine2 else ""
        if conv2 and conv2 != conv:
            record("C6 conversation updated", PASS, f"now {conv2}")
        else:
            record(
                "C6 conversation updated", WARN,
                f"still {conv2!r} — the API never updates Conversation_ID__c on the "
                "hit path (R6). The new conversation is not linked.",
            )

        # ── isolate the course question ──────────────────────────────────────
        # Same code path, but with the course known at link time. If this works,
        # C4's failure is about *timing*, not about the plumbing.
        import asyncio

        from app.crm import sync

        user_id = asyncio.run(sync.link_conversation(
            channel="whatsapp",
            conversation_id="gate3-course-probe",
            lead={},
            phone_number=COURSE_PHONE,
            name="Course Probe",
            course="MBA",
        ))
        if user_id:
            created_ids.append(user_id)
            probe_rows = rows_for(get_rows(ARGS.base_url), COURSE_PHONE)
            got = probe_rows[0].get("Course__c") if probe_rows else None
            if got == "MBA":
                record(
                    "C7 course works when known", PASS,
                    "Course__c='MBA' — so C4 is a timing problem, not a plumbing one",
                )
            else:
                record("C7 course works when known", FAIL, f"Course__c={got!r}, expected 'MBA'")
        else:
            record("C7 course works when known", FAIL, "the direct link returned no userId")

    finally:
        # ── stop the app, then clean up ──────────────────────────────────────
        server.should_exit = True
        thread.join(timeout=10)
        print()
        if created_ids:
            rows = get_rows(ARGS.base_url)
            # Re-resolve ids by phone so a partial run still cleans up fully.
            for row in rows:
                if (row.get("Phone__c") or "") in (THREAD1_PHONE, THREAD2_PHONE, COURSE_PHONE):
                    if row["Id"] not in created_ids:
                        created_ids.append(row["Id"])

            removed = sum(1 for uid in dict.fromkeys(created_ids) if delete_user(ARGS.base_url, uid))
            record("cleanup", PASS if removed == len(set(created_ids)) else FAIL,
                   f"deleted {removed}/{len(set(created_ids))} test record(s)")
        else:
            record("cleanup", INFO, "nothing created")

    # ── summary ──────────────────────────────────────────────────────────────
    print("\n" + "─" * 74)
    failed = [r for r in _results if r[1] == FAIL]
    warned = [r for r in _results if r[1] == WARN]

    # The failure mode worth naming explicitly, because the CRM looks fine.
    if any(r[0].startswith("C0") and r[1] == FAIL for r in _results) and \
       any(r[0].startswith("C1") and r[1] == PASS for r in _results):
        print(
            "\nDIAGNOSIS — R4, on the normal path.\n"
            "  The record WAS created in Salesforce, but the API failed to serialise\n"
            "  its response and returned a bare 500. UserResponse declares\n"
            "  `email: str` as required, while Email__c reads back as null for any\n"
            "  record Salesforce stored without one — and Salesforce turns an empty\n"
            "  string into null. So every lookup and every status PATCH against such\n"
            "  a record returns 500, permanently, and no userId can ever be obtained.\n"
            "\n"
            "  Why it fires here: WhatsApp's first linkable turn has a name and a\n"
            "  phone but no email yet, so we send email=\"\".\n"
            "\n"
            "  Consequence: crm_user_id is never persisted, so nothing caches, every\n"
            "  turn re-attempts, and Phase 7's status pushes will have no user to\n"
            "  address. The CRM accumulates rows that no one can read or update."
        )

    print(f"\n{len(_results)} clauses · {len(failed)} FAIL · {len(warned)} WARN\n")
    return 2 if failed else 0


def parse_args():
    p = argparse.ArgumentParser(description="Live Gate 3 verification for the WhatsApp → CRM link.")
    p.add_argument("--base-url", default=os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8098"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
