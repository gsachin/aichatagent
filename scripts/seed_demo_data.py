"""Seed the database with realistic demo data for dashboard presentation."""
import os, sys, uuid, random
from datetime import datetime, timedelta, timezone

import psycopg2

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://elearning:elearning_secret@localhost:5432/admissions",
)

conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()

# -- Clear existing test data --
cur.execute("DELETE FROM conversations")
cur.execute("DELETE FROM call_queue")
cur.execute("DELETE FROM follow_ups")
cur.execute("DELETE FROM lead_calls")
cur.execute("DELETE FROM leads")
print("Cleared existing data")

# -- Dummy Leads (Meridian programs) --
leads_data = [
    ("+12025551001", "John Smith", "john.smith@email.com", "MBA", "in_progress", "inbound_call", "Interested in part-time MBA, asked about eligibility and fees"),
    ("+12025551002", "Jane Doe", "jane.doe@email.com", "B.Tech Computer Science", "pending", "whatsapp", "International student from India, asked about Meridian B.Tech CS"),
    ("+12025551003", "Bob Chen", "bob.chen@email.com", "M.Sc", "in_progress", "outbound_call", "Comparing M.Sc and MCA programs"),
    ("+12025551004", "Alice Kim", "alice.kim@email.com", "MBA", "pending", "streamlit", "Exploring MBA options and scholarships"),
    ("+12025551005", "Mike Johnson", "mike.j@email.com", "B.Tech AI & Machine Learning", "completed", "inbound_call", "Enrolled in Meridian B.Tech AI & ML - Fall 2026"),
    ("+12025551006", "Sarah Lee", "sarah.lee@email.com", "MBA", "in_progress", "whatsapp", "Voice note asking about Meridian MBA tuition and scholarships"),
    ("+12025551007", "Tom Harris", "tom.h@email.com", "B.Tech Information Technology", "pending", "outbound_call", "Career fair lead, interested in IT track"),
    ("+12025551008", "Emma Wilson", "emma.w@email.com", "M.Sc", "failed", "inbound_call", "Not interested after learning tuition fees"),
    ("+12025551009", "David Brown", "david.b@email.com", "MCA", "pending", "streamlit", "Chatted on website, wants Meridian MCA info"),
    ("+12025551010", "Lisa Garcia", "lisa.g@email.com", "BBA", "in_progress", "outbound_call", "Follow-up scheduled, interested in Meridian BBA"),
    ("+12025551011", "Ryan Park", "ryan.park@email.com", "M.Tech", "unreachable", "outbound_call", "No answer on 3 attempts"),
    ("+12025551012", "Priya Patel", "priya.p@email.com", "BCA", "pending", "whatsapp", "Asked about visa process and Meridian BCA"),
]

lead_ids = []
for phone, name, email, program, status, source, notes in leads_data:
    lead_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created = now - timedelta(hours=random.randint(1, 72), minutes=random.randint(0, 59))
    cur.execute(
        "INSERT INTO leads (id, phone_number, name, email, program_interest, status, source, notes, call_attempts, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (lead_id, phone, name, email, program, status, source, notes, random.randint(0, 3), created, created),
    )
    lead_ids.append((lead_id, name, phone, program, status))

print(f"Inserted {len(lead_ids)} leads")

# -- Dummy Conversations (Meridian-grounded facts from the knowledge base) --
transcripts = [
    (
        "inbound_call",
        "Caller: Hi, I wanted to ask about the MBA program at Meridian.\n"
        "Assistant: Hello! The Meridian MBA is a 2-year program open to anyone with a Bachelor's Degree. Tuition is $18,500 per year. What would you like to know?\n"
        "Caller: What about application deadlines?\n"
        "Assistant: The early application deadline is January 15 and the regular deadline is April 30. Admission decisions are released June 10, and the Fall semester begins August 1. Shall I schedule a follow-up?\n"
        "Caller: Yes, please.",
        272,
        "interested",
    ),
    (
        "outbound_call",
        "Assistant: Hi Jane, I am calling from Meridian University Admissions. I noticed your interest in B.Tech Computer Science. Do you have a moment?\n"
        "Caller: Yes, I was looking at the B.Tech CS program. I am an international student from India.\n"
        "Assistant: Great! B.Tech Computer Science is a 4-year program requiring 10+2 (PCM). Tuition is $14,500 per year. International applicants need a valid passport copy and, where applicable, an English proficiency score.\n"
        "Caller: Any scholarships?\n"
        "Assistant: Yes, Meridian offers a Merit Scholarship with up to 50% tuition waiver, Need-Based Aid, and Sports & Arts Scholarships. Would you like details via WhatsApp?",
        198,
        "interested",
    ),
    (
        "whatsapp",
        "Student: What are the Meridian MBA tuition fees?\n"
        "Assistant: Meridian MBA tuition is $18,500 per year for the 2-year program, including campus facilities, career services, and alumni network access. Financial aid and scholarships are available.\n"
        "Student: What scholarships?\n"
        "Assistant: Meridian offers: Merit Scholarship (up to 50% tuition waiver), Need-Based Aid, and Sports & Arts Scholarships. Most students qualify for at least one. Would you like a counselor to call you?",
        0,
        "info_given",
    ),
    (
        "inbound_call",
        "Caller: I want to know about B.Tech AI & Machine Learning.\n"
        "Assistant: B.Tech AI & Machine Learning is a 4-year program requiring 10+2 (PCM). Tuition is $15,200 per year. It is offered by the School of Computer Science, covering software, AI, data science and cybersecurity.\n"
        "Caller: I have 10+2 with PCM. How is placement?\n"
        "Assistant: You are well-qualified! Meridian's placement rate is 95%, with 40,000+ alumni working across 70 countries. Hostel & accommodation is $3,200 per year if needed.\n"
        "Caller: Sounds great. Let me think about it.\n"
        "Assistant: Would you like a brochure and a follow-up call scheduled?",
        145,
        "interested",
    ),
    (
        "outbound_call",
        "Assistant: Hi, I am calling from Meridian Admissions about your program interest.\n"
        "Caller: Actually, I already enrolled elsewhere. Thank you.\n"
        "Assistant: Congratulations on your enrollment! If you ever want to explore transfer or graduate options at Meridian, feel free to reach out. Have a great day!",
        45,
        "not_interested",
    ),
    (
        "whatsapp",
        "Student: Application deadline for Meridian?\n"
        "Assistant: Meridian deadlines: Early application January 15, Regular application April 30. Admission decisions are released June 10 and the Fall semester begins August 1. Which program?\n"
        "Student: MCA.\n"
        "Assistant: Same deadlines apply for the MCA. I recommend applying by the early deadline for the best chance at scholarships. Need help with the application?",
        0,
        "info_given",
    ),
    (
        "inbound_call",
        "Caller: Tell me about Meridian Engineering.\n"
        "Assistant: Meridian's School of Engineering covers core, computer and electronics engineering with strong lab exposure. B.Tech programs are 4 years with 10+2 (PCM) eligibility. Which one interests you?\n"
        "Caller: Computer Science.\n"
        "Assistant: B.Tech Computer Science is $14,500 per year. The Innovation Labs offer prototyping benches, 3D printers and startup incubation space. Shall I send the detailed fee structure?\n"
        "Caller: Yes, please do.",
        210,
        "interested",
    ),
    (
        "inbound_call",
        "Caller: Hi, what business programs does Meridian offer?\n"
        "Assistant: Meridian's School of Business / Management offers finance, marketing and entrepreneurship. The MBA is a 2-year program at $18,500 per year, and the BBA is 3 years at $10,800 per year. What is your background?\n"
        "Caller: I work in finance, 5 years experience.\n"
        "Assistant: With 5 years in finance, the MBA would be ideal. Admission requires a Bachelor's Degree, and tuition is $18,500 per year. Many finance professionals choose this path.",
        160,
        "interested",
    ),
]

for i, (lead_id, name, phone, program, status) in enumerate(lead_ids):
    if i < len(transcripts):
        channel, transcript, duration, outcome = transcripts[i]
        conv_id = str(uuid.uuid4())
        created = datetime.now(timezone.utc) - timedelta(
            hours=random.randint(0, 24), minutes=random.randint(0, 59)
        )
        cur.execute(
            "INSERT INTO conversations (id, lead_id, phone_number, channel, transcript, call_duration_seconds, outcome, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (conv_id, lead_id, phone, channel, transcript, duration, outcome, created),
        )

print(f"Inserted {min(len(lead_ids), len(transcripts))} conversations")

# -- Dummy Follow-ups --
for i, (lead_id, name, phone, program, status) in enumerate(lead_ids[:5]):
    fu_id = str(uuid.uuid4())
    scheduled = datetime.now(timezone.utc) + timedelta(hours=random.randint(2, 48))
    fu_type = random.choice(["call", "message"])
    cur.execute(
        "INSERT INTO follow_ups (id, lead_id, scheduled_at, status, type, notes, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (fu_id, lead_id, scheduled, "pending", fu_type, f"Follow-up about {program} program", datetime.now(timezone.utc)),
    )
    cur.execute("UPDATE leads SET next_follow_up = %s WHERE id = %s", (scheduled, lead_id))

print("Inserted 5 follow-ups")

# -- Demo Courses --
cur.execute("DELETE FROM offer_letters")
cur.execute("DELETE FROM lead_documents")
cur.execute("DELETE FROM courses")
print("Cleared existing offer-letter data")

# Meridian catalog — names, durations and fees must match
# content/meridian/meridian_knowledge_base.md exactly (offer letters read these rows).
courses_data = [
    ("B.Tech Computer Science", "4 Years", "$14,500/year", "Fall 2026, Spring 2027",
     "Core, computer and electronics engineering with strong lab exposure."),
    ("B.Tech AI & Machine Learning", "4 Years", "$15,200/year", "Fall 2026",
     "AI and machine learning specialization in the School of Computer Science."),
    ("B.Tech Information Technology", "4 Years", "$14,200/year", "Fall 2026",
     "IT program covering software, AI, data science and cybersecurity."),
    ("BBA", "3 Years", "$10,800/year", "Fall 2026",
     "Bachelor of Business Administration — finance, marketing, entrepreneurship."),
    ("BCA", "3 Years", "$10,200/year", "Fall 2026",
     "Bachelor of Computer Applications."),
    ("B.Com", "3 Years", "$8,600/year", "Fall 2026",
     "Accounting, economics and business analytics."),
    ("BA", "3 Years", "$7,900/year", "Fall 2026",
     "Design, media, literature and humanities."),
    ("B.Sc", "3 Years", "$9,400/year", "Fall 2026",
     "Physics, chemistry, biology and mathematics with research tracks."),
    ("MBA", "2 Years", "$18,500/year", "Fall 2026, Spring 2027",
     "Master of Business Administration — School of Business / Management."),
    ("MCA", "2 Years", "$13,800/year", "Fall 2026",
     "Master of Computer Applications — Bachelor's in Computing eligibility."),
    ("M.Tech", "2 Years", "$14,600/year", "Fall 2026",
     "M.Tech specializations across Engineering & Computing."),
    ("M.Sc", "2 Years", "$11,200/year", "Fall 2026",
     "Master of Science — Bachelor's in Science eligibility."),
    ("MA", "2 Years", "$9,600/year", "Fall 2026",
     "Master of Arts — Bachelor's Degree eligibility."),
    ("M.Com", "2 Years", "$9,900/year", "Fall 2026",
     "Master of Commerce — Bachelor's in Commerce eligibility."),
]
for name, duration, fees, intake, desc in courses_data:
    cid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO courses (id, name, duration, fees, intake, description, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW())",
        (cid, name, duration, fees, intake, desc),
    )
print(f"Inserted {len(courses_data)} courses")

conn.close()
print("\n=== DASHBOARD DATA SEEDED ===")
print("Dashboard: https://const-leaves-contest-legend.trycloudflare.com/dashboard")
print(f"12 leads | 8 conversations | 5 follow-ups | {len(courses_data)} courses")
