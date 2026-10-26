"""Seed the database with DSPMU demo data for dashboard presentation.

Demo-only safety:

- loads ``DATABASE_URL`` from the project ``.env``;
- prints the target host/database (never the password);
- refuses any database whose name does not end in ``_demo``;
- requires ``--yes`` before deleting and replacing data;
- runs the deletes and inserts in one transaction, so a failed seed rolls back
  instead of leaving the database empty.

The application never imports this script. It is a manual demo tool.
"""

from __future__ import annotations

import argparse
import atexit
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _dsn_parts(dsn: str) -> tuple[str, str]:
    parsed = urlparse(dsn)
    if parsed.scheme.startswith("postgres"):
        return (
            parsed.hostname or "<unknown>",
            (parsed.path or "").lstrip("/") or "<unknown>",
        )
    host = "<unknown>"
    db_name = "<unknown>"
    for part in dsn.split():
        if part.startswith("host="):
            host = part.split("=", 1)[1]
        elif part.startswith("dbname="):
            db_name = part.split("=", 1)[1]
    return host, db_name


_parser = argparse.ArgumentParser(
    description="Seed synthetic DSPMU demo data into a *_demo database only."
)
_parser.add_argument("--yes", action="store_true", help="confirm the delete + insert")
_parser.add_argument(
    "--allow-dev-db",
    action="store_true",
    help="allow a database name without the _demo suffix (intentional development use)",
)
_args = _parser.parse_args()

_dsn = (os.environ.get("DATABASE_URL") or "").strip()
if not _dsn:
    print("REFUSED: DATABASE_URL is not set. Load the demo .env or export the demo DSN.")
    raise SystemExit(2)

_host, _db_name = _dsn_parts(_dsn)
print(f"Target database: host={_host} db={_db_name}")

if not _db_name.endswith("_demo") and not _args.allow_dev_db:
    print(
        "REFUSED: the database name must end in _demo. "
        "Use --allow-dev-db only for an intentional development seed."
    )
    raise SystemExit(2)
if not _args.yes:
    print("REFUSED: this script deletes and replaces demo data. Pass --yes to proceed.")
    raise SystemExit(2)

conn = psycopg2.connect(_dsn)
conn.autocommit = False
_committed = False


@atexit.register
def _cleanup() -> None:
    try:
        if not _committed:
            conn.rollback()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


cur = conn.cursor()

cur.execute("DELETE FROM conversations")
cur.execute("DELETE FROM call_queue")
cur.execute("DELETE FROM follow_ups")
cur.execute("DELETE FROM lead_calls")
cur.execute("DELETE FROM leads")
print("Cleared existing data")

leads_data = [
    ("+919300000001", "Aarav Kumar", "aarav@example.com", "BA History", "in_progress", "inbound_call", "Asked about BA History eligibility and annual fee"),
    ("+919300000002", "Diya Singh", "diya@example.com", "B.Sc Physics", "pending", "whatsapp", "Asked about the physics department and admission steps"),
    ("+919300000003", "Rohan Das", "rohan@example.com", "B.Com", "in_progress", "outbound_call", "Comparing B.Com and BBA options"),
    ("+919300000004", "Ananya Gupta", "ananya@example.com", "BBA", "pending", "streamlit", "Explored BBA and asked about the application form"),
    ("+919300000005", "Kabir Khan", "kabir@example.com", "BCA", "completed", "inbound_call", "Completed the demo admission enquiry"),
    ("+919300000006", "Meera Nair", "meera@example.com", "MA History", "in_progress", "whatsapp", "Asked about postgraduate history options"),
    ("+919300000007", "Vivaan Patel", "vivaan@example.com", "M.Sc Physics", "pending", "outbound_call", "Follow-up requested for M.Sc Physics"),
    ("+919300000008", "Isha Verma", "isha@example.com", "BA Political Science", "failed", "inbound_call", "Call dropped before the enquiry completed"),
]

lead_ids = []
for phone, name, email, program, status, source, notes in leads_data:
    lead_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created = now - timedelta(
        hours=random.randint(1, 72), minutes=random.randint(0, 59)
    )
    cur.execute(
        "INSERT INTO leads (id, phone_number, name, email, program_interest, "
        "status, source, notes, call_attempts, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            lead_id,
            phone,
            name,
            email,
            program,
            status,
            source,
            notes,
            random.randint(0, 3),
            created,
            created,
        ),
    )
    lead_ids.append((lead_id, name, phone, program, status))

print(f"Inserted {len(lead_ids)} leads")

transcripts = [
    (
        "inbound_call",
        "Caller: What is the BA History annual fee?\n"
        "Assistant: The verified BA History annual fee is ₹3,410 for the "
        "general category and ₹3,185 for ST/SC/BC I & II. The registrar can "
        "confirm the current notification.\n"
        "Caller: How do I apply?\n"
        "Assistant: Complete the university application form and submit the "
        "documents listed on the admissions page. I can send the reference "
        "link to this number.",
        272,
        "interested",
    ),
    (
        "outbound_call",
        "Assistant: Hello, I am calling from DSPMU admissions about your "
        "B.Sc Physics enquiry. Do you have a moment?\n"
        "Caller: Yes. What are the admission steps?\n"
        "Assistant: Submit the online application, upload your 10+2 marksheet "
        "and identity proof, and follow the university's merit and counselling "
        "notices. The registrar can confirm the current schedule.",
        198,
        "interested",
    ),
    (
        "whatsapp",
        "Student: Where can I find the fee structure?\n"
        "Assistant: The verified fee facts and the official fee document are "
        "in the university's published pages. I can send the reference on "
        "request; please confirm the programme you are asking about.",
        0,
        "info_given",
    ),
    (
        "inbound_call",
        "Caller: Tell me about the grievance process.\n"
        "Assistant: The university publishes a Grievance Redressal Cell page. "
        "It lists the contact route and the escalation steps. I can send the "
        "page reference to this number.",
        "145",
        "interested",
    ),
    (
        "whatsapp",
        "Student: What documents are needed for admission?\n"
        "Assistant: The admissions page lists the required documents, "
        "including marksheets and identity proof. The exact list depends on "
        "the programme and category, so please check the current notice.",
        0,
        "info_given",
    ),
]

for index, (lead_id, name, phone, program, status) in enumerate(lead_ids):
    if index >= len(transcripts):
        break
    channel, transcript, duration, outcome = transcripts[index]
    conv_id = str(uuid.uuid4())
    created = datetime.now(timezone.utc) - timedelta(
        hours=random.randint(0, 24), minutes=random.randint(0, 59)
    )
    cur.execute(
        "INSERT INTO conversations (id, lead_id, phone_number, channel, "
        "transcript, call_duration_seconds, outcome, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            conv_id,
            lead_id,
            phone,
            channel,
            transcript,
            int(duration) if str(duration).isdigit() else 0,
            outcome,
            created,
        ),
    )

print(f"Inserted {min(len(lead_ids), len(transcripts))} conversations")

for lead_id, name, phone, program, status in lead_ids[:5]:
    fu_id = str(uuid.uuid4())
    scheduled = datetime.now(timezone.utc) + timedelta(
        hours=random.randint(2, 48)
    )
    fu_type = random.choice(["call", "message"])
    cur.execute(
        "INSERT INTO follow_ups (id, lead_id, scheduled_at, status, type, "
        "notes, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            fu_id,
            lead_id,
            scheduled,
            "pending",
            fu_type,
            f"Follow-up about {program}",
            datetime.now(timezone.utc),
        ),
    )
    cur.execute(
        "UPDATE leads SET next_follow_up = %s WHERE id = %s",
        (scheduled, lead_id),
    )

print("Inserted 5 follow-ups")

cur.execute("DELETE FROM offer_letters")
cur.execute("DELETE FROM lead_documents")
cur.execute("DELETE FROM courses")
print("Cleared existing offer-letter data")

courses_data = [
    (
        "BA History",
        "3 Years",
        "₹3,410/year (GEN); ₹3,185/year (ST/SC/BC I & II)",
        "As notified",
        "History programme at DSPMU.",
    ),
    (
        "BA Political Science",
        "3 Years",
        "As notified",
        "As notified",
        "Political Science programme at DSPMU.",
    ),
    (
        "B.Sc Physics",
        "3 Years",
        "As notified",
        "As notified",
        "Physics programme at DSPMU.",
    ),
    (
        "B.Com",
        "3 Years",
        "As notified",
        "As notified",
        "Commerce programme at DSPMU.",
    ),
    (
        "BBA",
        "3 Years",
        "As notified",
        "As notified",
        "Bachelor of Business Administration at DSPMU.",
    ),
    (
        "BCA",
        "3 Years",
        "As notified",
        "As notified",
        "Bachelor of Computer Applications at DSPMU.",
    ),
    (
        "MA History",
        "2 Years",
        "As notified",
        "As notified",
        "Postgraduate History programme at DSPMU.",
    ),
    (
        "M.Sc Physics",
        "2 Years",
        "As notified",
        "As notified",
        "Postgraduate Physics programme at DSPMU.",
    ),
]
for name, duration, fees, intake, description in courses_data:
    cur.execute(
        "INSERT INTO courses (id, name, duration, fees, intake, description, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s, NOW())",
        (str(uuid.uuid4()), name, duration, fees, intake, description),
    )
print(f"Inserted {len(courses_data)} courses")

conn.commit()
_committed = True
conn.close()
print("\n=== DSPMU DEMO DATA SEEDED ===")
print("Dashboard: http://127.0.0.1:8502")
print(
    f"{len(lead_ids)} leads | {min(len(lead_ids), len(transcripts))} "
    f"conversations | 5 follow-ups | {len(courses_data)} courses"
)
