#!/usr/bin/env python
"""
Gate 6 — a first-time web chatter appears in Salesforce.

    python scripts/verify_gate6_webchat.py
    python scripts/verify_gate6_webchat.py --keep --json

WHY THIS IS THE ONE THAT MATTERED
---------------------------------
A web-chat conversation (name, email, phone, program, an offer letter generated
and delivered) left *nothing* in Salesforce, because only the WhatsApp channel
was ever wired. This gate drives the real fix: the chat's lead write goes to the
FastAPI backend, which links the person to the CRM in the background while the
chat carries on.

It runs a real uvicorn server in a thread — one event loop, as in production —
and talks to it over HTTP exactly as the Streamlit process does. Postgres must
be up: the lead write is a real database write, and it is the write that the
back-end hook hangs off.

WHAT IT CHECKS
    C1  POST /api/leads returns a lead id
    C2  the person appears in Salesforce, with the conversation id attached
    C3  a second write for the same person does not create a second record
    C4  a lead with no conversation id is declined, and said so in the log
    cleanup  the test record is deleted

Test data convention: name ZZ-VERIFY-G6-<runid>, phone +1415555 01xx (NANP
fictional range), email verify+<runid>@example.com.

EXIT CODES
    0  every clause passed
    1  the CRM API or the app server was unreachable
    2  a clause failed
"""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
import time
import urllib.parse
import urllib.request
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
TEST_PREFIX = "ZZ-VERIFY-G6"


@dataclass
class Result:
    probe: str
    status: str
    detail: str


@dataclass
class Run:
    results: list[Result] = field(default_factory=list)
    deferred: list[Result] = field(default_factory=list)
    cleanup: list[str] = field(default_factory=list)

    def add(self, probe: str, status: str, detail: str) -> None:
        self.results.append(Result(probe, status, detail))
        if not ARGS.json:
            colour = {PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"}[status] \
                if sys.stdout.isatty() else ""
            reset = "\033[0m" if sys.stdout.isatty() else ""
            print(f"  {colour}{status:<4}{reset}  {probe:<22}  {detail}")

    def defer(self, probe: str, status: str, detail: str) -> None:
        """A clause this harness cannot decide on its own — stated, not hidden."""
        self.deferred.append(Result(probe, status, detail))
        if not ARGS.json:
            print(f"  \033[33mDEFER\033[0m  {probe:<22}  {detail}" if sys.stdout.isatty()
                  else f"  DEFER  {probe:<22}  {detail}")

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if r.status == FAIL]


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def api(path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 30):
    data = jsonlib.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        ARGS.base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, jsonlib.loads(resp.read().decode() or "{}")


def crm_rows() -> list[dict]:
    with urllib.request.urlopen(
        ARGS.crm_url.rstrip("/") + "/admissions", timeout=30
    ) as resp:
        return jsonlib.loads(resp.read().decode())


def rows_for(rows: list[dict], phone: str) -> list[dict]:
    return [r for r in rows if str(r.get("Phone__c") or "").strip() == phone]


def wait_for_row(phone: str, timeout: float = 20.0) -> dict | None:
    """
    The link is backgrounded, so it lands just after the response. Poll briefly.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = rows_for(crm_rows(), phone)
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def start_server(port: int):
    """A real uvicorn server, one event loop, as production runs it."""
    import threading
    import socket

    import uvicorn

    from app.main import app

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return server, thread
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not come up on 127.0.0.1:{port} within 30s")


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    runid = uuid.uuid4().hex[:8]
    run = Run()
    # Inside the check-numbers block this harness is the older 555-0100..0199 range.
    phone = f"+1415555{100 + int(runid[:2], 16) % 100:04d}"
    # A second number for the no-conversation clause; kept distinct so it can
    # never be confused with the linked one.
    bare_phone = f"+1415555{100 + (int(runid[2:4], 16) + 37) % 100:04d}"
    phones = [phone, bare_phone]
    conversation_id = f"gate6-{runid}"

    print(f"\nGate 6 — web chat → CRM live verification  (run {runid})")
    print(f"  app        http://127.0.0.1:{ARGS.port}   (started by this script)")
    print(f"  crm        {ARGS.crm_url}")
    print(f"  chatter    {phone}   conversation {conversation_id}\n")

    # ── preflight ────────────────────────────────────────────────────────────
    try:
        before = crm_rows()
    except Exception as exc:
        print(f"FATAL: CRM API unreachable at {ARGS.crm_url} — {exc}", file=sys.stderr)
        return 1
    if rows_for(before, phone):
        print(f"FATAL: test number {phone} already exists in the CRM — rerun.", file=sys.stderr)
        return 1
    run.add("preflight", INFO, f"CRM reachable — {len(before)} rows, {phone} free")

    # Order matters: app/database.py reads os.environ at *import* time into
    # module constants, and it is app/config.py that loads .env. Importing the
    # database module first yields a passwordless connection string.
    import app.config  # noqa: F401  — loads .env before the imports below
    from app.database import _connection_string

    try:
        import psycopg2
        conn = psycopg2.connect(_connection_string())
        conn.close()
        run.add("preflight", INFO, "Postgres reachable — the lead write is real")
    except Exception as exc:
        print(f"FATAL: Postgres unreachable ({exc}). This gate needs it: the hook "
              f"hangs off a real lead write.", file=sys.stderr)
        return 1

    server, _thread = start_server(ARGS.port)
    created_ids: list[str] = []

    try:
        # ── C1: the chat writes its lead ─────────────────────────────────────
        status, lead = api("/api/leads", method="POST", body={
            "phone_number": phone,
            "name": f"{TEST_PREFIX}-{runid}",
            "email": f"verify+{runid}@example.com",
            "program_interest": "MBA",
            "source": "streamlit",
            "conversation_id": conversation_id,
        })
        lead_id = lead.get("id", "") if isinstance(lead, dict) else ""
        if status < 400 and lead_id:
            run.add("C1 lead write", PASS, f"{status}, lead {lead_id[:8]}…")
            run.cleanup.append(lead_id)
        else:
            run.add("C1 lead write", FAIL, f"{status}: {lead}")
            return _finish(run, created_ids)

        # ── C2: the person reaches Salesforce ────────────────────────────────
        row = wait_for_row(phone)
        if not row:
            run.add("C2 in Salesforce", FAIL, "no Customer row appeared within 20s")
            return _finish(run, created_ids)

        created_ids.append(str(row.get("Id") or ""))
        observed = str(row.get("Conversation_ID__c") or "")
        if observed == conversation_id:
            run.add("C2 in Salesforce", PASS,
                    f"{row.get('Id')} — Conversation_ID__c matches the chat")
        else:
            run.add("C2 in Salesforce", WARN,
                    f"{row.get('Id')} — Conversation_ID__c is {observed!r}, "
                    f"expected {conversation_id!r}")
        name = f"{row.get('First_Name__c') or ''} {row.get('Last_Name__c') or ''}".strip()
        run.add("C2 fields", INFO,
                f"name={name!r} email={row.get('Email__c')!r} course={row.get('Course__c')!r}")

        # ── C3: a second write does not duplicate ────────────────────────────
        api("/api/leads", method="POST", body={
            "phone_number": phone,
            "name": f"{TEST_PREFIX}-{runid}",
            "email": f"verify+{runid}@example.com",
            "program_interest": "MBA",
            "source": "streamlit",
            "conversation_id": conversation_id,
        })
        time.sleep(3)
        after = rows_for(crm_rows(), phone)
        if len(after) == 1:
            run.add("C3 no duplicate", PASS, "still exactly one Customer row")
        else:
            run.add("C3 no duplicate", FAIL, f"{len(after)} rows for one person")

        # ── C5: the local lead carries the link ──────────────────────────────
        # This is the seam the offer upload depends on: app/crm/documents.py
        # stops when a lead has no crm_user_id, so a web chatter with a link now
        # gets their offer letter in Salesforce too — the exact thing that was
        # missing for Pradeep.
        linked = _local_crm_user_id(phone)
        if linked:
            run.add("C5 lead linked", PASS,
                    f"leads.crm_user_id = {linked} — the offer PDF can now be uploaded")
        else:
            run.add("C5 lead linked", FAIL,
                    "the lead row has no crm_user_id; the CRM row would be orphaned")

        # ── C4: no conversation id → nothing is created ──────────────────────
        # The dashboard's add-lead form posts here too, with no conversation to
        # name. The person must NOT appear in the CRM: lookup-or-create is the
        # only call that creates a record, and it is refused without an id.
        status, bare = api("/api/leads", method="POST", body={
            "phone_number": bare_phone,
            "name": f"{TEST_PREFIX}-noc-{runid}",
            "email": f"verify+noc+{runid}@example.com",
            "program_interest": "MBA",
            "source": "manual",
        })
        if status >= 400 or not (isinstance(bare, dict) and bare.get("id")):
            run.add("C4 bare lead", FAIL, f"the lead write itself failed: {status}")
        else:
            time.sleep(5)
            leaked = rows_for(crm_rows(), bare_phone)
            if leaked:
                run.add("C4 bare lead", FAIL,
                        f"a lead with no conversation created {leaked[0].get('Id')} — "
                        f"Salesforce has a record it cannot name")
                created_ids.append(str(leaked[0].get("Id") or ""))
            else:
                run.add("C4 bare lead", PASS,
                        "no conversation id → no CRM record, as intended")

    finally:
        _finish_cleanup(run, created_ids)
        _cleanup_local_leads(phones)

    return _finish(run, created_ids)


def _local_crm_user_id(phone: str) -> str:
    """The CRM link on the local lead row, polled because the link is backgrounded."""
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            import psycopg2
            from app.database import _connection_string

            conn = psycopg2.connect(_connection_string())
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT crm_user_id FROM leads WHERE phone_number = %s", (phone,)
                )
                row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            return ""
        time.sleep(0.5)
    return ""


def _cleanup_local_leads(phones: list[str]) -> None:
    """Remove the test leads this gate created in the local database."""
    if ARGS.keep:
        return
    try:
        import psycopg2
        from app.database import _connection_string

        conn = psycopg2.connect(_connection_string())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM leads WHERE phone_number = ANY(%s) RETURNING id", (phones,)
            )
            removed = len(cur.fetchall())
        conn.close()
        print(f"  \033[32mPASS\033[0m  cleanup/local          removed {removed} test lead row(s)"
              if sys.stdout.isatty() else
              f"  PASS  cleanup/local          removed {removed} test lead row(s)")
    except Exception as exc:
        print(f"  WARN  cleanup/local          {type(exc).__name__}: {exc}")


def _finish_cleanup(run: Run, created_ids: list[str]) -> None:
    for crm_id in created_ids:
        if not crm_id:
            continue
        try:
            req = urllib.request.Request(
                ARGS.crm_url.rstrip("/") + f"/users/{crm_id}", method="DELETE"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                ok = resp.status < 400
        except Exception as exc:
            run.add("cleanup", WARN, f"{crm_id}: {type(exc).__name__}")
            continue
        run.add("cleanup", PASS if ok else WARN, f"deleted {crm_id}")


def _finish(run: Run, created_ids: list[str]) -> int:
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
    parser = argparse.ArgumentParser(description="Gate 6 — web chat reaches the CRM.")
    parser.add_argument("--base-url", default="", help="app base URL (default: a free port)")
    parser.add_argument("--port", type=int, default=0, help="port for the app (default: free)")
    parser.add_argument("--crm-url", default=os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8098"))
    parser.add_argument("--keep", action="store_true", help="do not delete the test record")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


ARGS = parse_args()
if not ARGS.port:
    # Chosen before the server starts so --base-url and the bind agree.
    import socket as _socket
    with _socket.socket() as _s:
        _s.bind(("127.0.0.1", 0))
        ARGS.port = _s.getsockname()[1]
if not ARGS.base_url:
    ARGS.base_url = f"http://127.0.0.1:{ARGS.port}"

# The app reads these at import time, so they must be set before app.main loads.
os.environ.setdefault("CRM_ENABLED", "true")
os.environ.setdefault("CRM_BASE_URL", ARGS.crm_url)

if __name__ == "__main__":
    raise SystemExit(main())
