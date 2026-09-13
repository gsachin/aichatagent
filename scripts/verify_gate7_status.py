#!/usr/bin/env python
"""
Gate 7 — the CRM record reflects what actually happened to the student.

    python scripts/verify_gate7_status.py
    python scripts/verify_gate7_status.py --keep --json

WHAT WAS WRONG
--------------
The CRM held a released offer letter while reporting `Offer_Letter_Released__c =
False`, and a student who switched to MBA stayed on their first-ever answer
because program interest only ever reached Salesforce inside lookup-or-create —
at create time and never again. Nothing pushed sentiment, and no worker drained
the outbox, so a transient CRM failure simply lost the fact.

WHAT THIS PROVES, by driving `app/crm/status.py` against the live API and then
**re-reading the record** — every clause asserts what Salesforce actually holds,
never what we sent:

    C1  a changed program reaches Course__c
    C2  an offer release moves Offer_Status__c / Offer_Sent_At__c /
        Offer_Letter_Released__c / Admission_Status__c together
    C3  accepting moves the offer, the acceptance and the admission together
    C4  declining moves them the other way
    C5  a lapsed offer is marked Expired
    C6  sentiment lands on both category fields, with each field's own spelling
    C7  a CRM outage loses nothing — the write queues and is replayed
    C8  a record that no longer exists is dropped, not retried forever
    cleanup  the test record is deleted

C7 is the one that has never been exercised before: `flush_outbox` existed with
a docstring claiming a scheduler called it, and no scheduler was ever written.

EXIT CODES
    0  every clause passed
    1  the CRM API or Postgres was unreachable
    2  a clause failed
"""

from __future__ import annotations

import argparse
import asyncio
import json as jsonlib
import os
import sys
import time
import urllib.error
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
TEST_PREFIX = "ZZ-VERIFY-G7"
DEAD_PORT = "http://127.0.0.1:9"  # nothing listens; a guaranteed connect failure


@dataclass
class Result:
    probe: str
    status: str
    detail: str


@dataclass
class Run:
    results: list[Result] = field(default_factory=list)

    def add(self, probe: str, status: str, detail: str) -> None:
        self.results.append(Result(probe, status, detail))
        if not ARGS.json:
            colour = {PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"}[status] \
                if sys.stdout.isatty() else ""
            reset = "\033[0m" if sys.stdout.isatty() else ""
            print(f"  {colour}{status:<4}{reset}  {probe:<26}  {detail}")

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if r.status == FAIL]


# ── HTTP ─────────────────────────────────────────────────────────────────────

def call(method: str, path: str, body: dict | None = None, timeout: float = 40):
    data = jsonlib.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        ARGS.crm_url.rstrip("/") + path, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, jsonlib.loads(raw)
            except ValueError:
                return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, jsonlib.loads(raw)
        except ValueError:
            return e.code, raw[:200]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def read_record(user_id: str) -> dict:
    """The whole record, so a clause can assert on what Salesforce holds."""
    status, body = call("GET", f"/admissions/{user_id}")
    return body if status == 200 and isinstance(body, dict) else {}


# ── the app's own code, wired to the live API ────────────────────────────────

def install(run: Run):
    """
    Point app.crm.status / sync at the live API.

    Builds a client **per event loop**, exactly as ``app/crm/client.py::get_client``
    does and for the same reason: an ``httpx.AsyncClient``'s pool is bound to the
    loop that created it, and this gate runs each step in its own ``asyncio.run``.
    One shared client fails on the second step with "Event loop is closed".
    """
    from app.config import settings

    object.__setattr__(settings, "CRM_ENABLED", True)
    object.__setattr__(settings, "CRM_BASE_URL", ARGS.crm_url)
    object.__setattr__(settings, "CRM_MAX_RETRIES", 0)

    from app.crm import status as status_mod
    from app.crm import sync as sync_mod
    from app.crm.client import CrmClient

    clients: dict = {}
    # Flipped to a dead port for the outage clause, so the write fails the way a
    # CRM outage fails rather than by mocking the failure.
    target = {"base": ARGS.crm_url}

    def client_for_loop() -> CrmClient:
        loop = asyncio.get_running_loop()
        key = (loop, target["base"])
        if key not in clients:
            clients[key] = CrmClient(
                target["base"], connect_timeout=3.0, read_timeout=30.0, max_retries=0
            )
        return clients[key]

    status_mod.get_client = client_for_loop
    sync_mod.get_client = client_for_loop
    run.add("setup", INFO, f"app.crm.status wired to {ARGS.crm_url}")
    return status_mod, sync_mod, target


def check(run: Run, clause: str, record: dict, expected: dict, *, fields_note: str = "") -> None:
    """Assert the *record* holds each expected value — not that we sent it."""
    wrong = {k: (record.get(k), v) for k, v in expected.items() if record.get(k) != v}
    if not wrong:
        run.add(clause, PASS, fields_note or ", ".join(f"{k}={v!r}" for k, v in expected.items()))
    else:
        detail = "; ".join(f"{k}: expected {v!r}, found {got!r}" for k, (got, v) in wrong.items())
        run.add(clause, FAIL, detail)


def main() -> int:
    runid = uuid.uuid4().hex[:8]
    run = Run()
    phone = f"+1415555{100 + int(runid[:2], 16) % 100:04d}"

    print(f"\nGate 7 — status sync, live  (run {runid})")
    print(f"  crm   {ARGS.crm_url}")
    print(f"  test  {phone}\n")

    # ── preflight ────────────────────────────────────────────────────────────
    status, health = call("GET", "/health")
    if status != 200:
        print(f"FATAL: CRM API unreachable at {ARGS.crm_url} — {status} {health}", file=sys.stderr)
        return 1
    run.add("preflight", INFO, "CRM API reachable")

    import app.config  # noqa: F401 — loads .env before app.database snapshots os.environ
    try:
        import psycopg2
        from app.database import _connection_string
        conn = psycopg2.connect(_connection_string())
        conn.close()
        run.add("preflight", INFO, "Postgres reachable — the outbox clause needs it")
    except Exception as exc:
        print(f"FATAL: Postgres unreachable ({exc}). C7 depends on the real outbox.",
              file=sys.stderr)
        return 1

    status, created = call("POST", "/users/lookup-or-create", {
        "name": f"{TEST_PREFIX}-{runid}",
        "email": f"verify+g7{runid}@example.com",
        "phoneNumber": phone,
        "conversationId": f"gate7-{runid}",
        "course": "Starting Course",
    })
    user_id = (created or {}).get("userId") if isinstance(created, dict) else ""
    if not user_id:
        run.add("create-user", FAIL, f"{status}: {created}")
        return _finish(run)
    run.add("create-user", PASS, user_id)

    status_mod, sync_mod, target = install(run)

    try:
        # ── C1: a changed program ────────────────────────────────────────────
        asyncio.run(status_mod.push_course(user_id, "MBA"))
        check(run, "C1 course", read_record(user_id), {"Course__c": "MBA"},
              fields_note="Course__c=MBA (was 'Starting Course')")

        # ── C2: the offer is released ────────────────────────────────────────
        asyncio.run(status_mod.push_offer_released(
            user_id, sent_at="2026-09-12T23:38:26.000+0000"
        ))
        check(run, "C2 offer released", read_record(user_id), {
            "Offer_Status__c": "Offered",
            "Offer_Letter_Released__c": True,
            "Admission_Status__c": "Under Review",
        }, fields_note="Offered + Released=True + Under Review")

        record = read_record(user_id)
        if record.get("Offer_Sent_At__c"):
            run.add("C2 sent-at", PASS, f"Offer_Sent_At__c={record['Offer_Sent_At__c']}")
        else:
            run.add("C2 sent-at", FAIL, "Offer_Sent_At__c was not stored")

        # ── C3: the student accepts ──────────────────────────────────────────
        asyncio.run(status_mod.push_offer_response(user_id, "accepted"))
        check(run, "C3 accepted", read_record(user_id), {
            "Offer_Letter_Accepted__c": "ACCEPTED",
            "Offer_Status__c": "Accepted",
            "Admission_Status__c": "Approved",
        }, fields_note="ACCEPTED + Accepted + Approved")

        # ── C4: and declining moves them the other way ───────────────────────
        asyncio.run(status_mod.push_offer_response(user_id, "rejected"))
        check(run, "C4 declined", read_record(user_id), {
            "Offer_Letter_Accepted__c": "NOT_ACCEPTED",
            "Offer_Status__c": "Declined",
            "Admission_Status__c": "Rejected",
        }, fields_note="NOT_ACCEPTED + Declined + Rejected")

        # ── C5: a lapsed offer ───────────────────────────────────────────────
        asyncio.run(status_mod.push_offer_expired(user_id))
        check(run, "C5 expired", read_record(user_id), {"Offer_Status__c": "Expired"},
              fields_note="Offer_Status__c=Expired")

        # ── C6: sentiment, on both category fields ───────────────────────────
        asyncio.run(status_mod.push_sentiment(user_id, "At-Risk"))
        check(run, "C6 sentiment", read_record(user_id), {
            "Sentiment__c": "AT-RISK",
            "Lead_Category__c": "AT RISK",
        }, fields_note="AT-RISK (hyphen) and AT RISK (space) — each field's own spelling")

        # ── C7: a CRM outage loses nothing ───────────────────────────────────
        # Aim the client at a dead port so the write fails the way a real outage
        # fails, then replay it through the real outbox.
        target["base"] = DEAD_PORT
        asyncio.run(status_mod.push_course(user_id, "BSc Data Science"))

        from app.crm import outbox

        depth = asyncio.run(outbox.depth())
        if depth >= 1:
            run.add("C7 outage/queued", PASS, f"the failed write is durable ({depth} queued)")
        else:
            run.add("C7 outage/queued", FAIL, "the write was lost instead of queued")

        target["base"] = ARGS.crm_url
        result = asyncio.run(sync_mod.flush_outbox())
        run.add("C7 outage/replay", INFO, f"drain: {result}")

        record = read_record(user_id)
        if record.get("Course__c") == "BSc Data Science":
            run.add("C7 outage/recovered", PASS, "the replayed write landed after the outage")
        else:
            run.add("C7 outage/recovered", FAIL,
                    f"Course__c is {record.get('Course__c')!r} after the replay")

        remaining = asyncio.run(outbox.depth())
        if remaining == 0:
            run.add("C7 outage/drained", PASS, "the queue is empty again")
        else:
            run.add("C7 outage/drained", WARN, f"{remaining} row(s) still queued")

        # ── C8: a deleted record is dropped, not retried ─────────────────────
        asyncio.run(status_mod.push_course("ZZ-NOT-A-RECORD", "MBA"))
        after = asyncio.run(outbox.depth())
        if after == remaining:
            run.add("C8 gone-record", PASS, "a missing record is permanent — nothing queued")
        else:
            run.add("C8 gone-record", FAIL,
                    f"the failure was queued ({after - remaining} row(s)) and would retry forever")

        # ── the record survived every write ──────────────────────────────────
        record = read_record(user_id)
        intact = {k: record.get(k) for k in ("First_Name__c", "Email__c", "Phone__c",
                                             "Conversation_ID__c")}
        expected_name = f"{TEST_PREFIX}-{runid}"
        if (intact["First_Name__c"] == expected_name
                and intact["Email__c"] == f"verify+g7{runid}@example.com"
                and intact["Phone__c"] == phone
                and intact["Conversation_ID__c"] == f"gate7-{runid}"):
            run.add("intact", PASS, "identity fields untouched by seven status writes")
        else:
            run.add("intact", FAIL, f"a status write damaged the record: {intact}")

    finally:
        if not ARGS.keep:
            status, resp = call("DELETE", f"/users/{user_id}")
            run.add("cleanup", PASS if status and status < 400 else WARN,
                    f"DELETE /users/{user_id} -> {status}")
            gone = not read_record(user_id).get("Id")
            run.add("cleanup/verified", PASS if gone else FAIL,
                    "record removed" if gone else "the record is still there")

    return _finish(run)


def _finish(run: Run) -> int:
    if ARGS.json:
        print(jsonlib.dumps({"results": [r.__dict__ for r in run.results]}, indent=2))
        return 2 if run.failed else 0

    counts: dict[str, int] = {}
    for r in run.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"\n  {counts.get(PASS, 0)} passed · {counts.get(WARN, 0)} warned · "
          f"{counts.get(FAIL, 0)} failed")
    if run.failed:
        print("\n  FAILED:")
        for r in run.failed:
            print(f"    - {r.probe}: {r.detail}")
    print()
    return 2 if run.failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate 7 — status reaches the CRM.")
    parser.add_argument("--crm-url", default=os.environ.get("CRM_BASE_URL", "http://127.0.0.1:8098"))
    parser.add_argument("--keep", action="store_true", help="do not delete the test record")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


ARGS = parse_args()

if __name__ == "__main__":
    raise SystemExit(main())
