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

# -- Dummy Leads --
leads_data = [
    ("+12025551001", "John Smith", "john.smith@email.com", "MBA", "in_progress", "inbound_call", "Interested in part-time MBA, asked about GMAT and fees"),
    ("+12025551002", "Jane Doe", "jane.doe@email.com", "Computer Science", "pending", "whatsapp", "International student from India, asked about FDU MS in CS"),
    ("+12025551003", "Bob Chen", "bob.chen@email.com", "Data Science", "in_progress", "outbound_call", "Comparing UMD and FDU Data Science programs"),
    ("+12025551004", "Alice Kim", "alice.kim@email.com", "MBA", "pending", "streamlit", "Exploring MBA options at both universities"),
    ("+12025551005", "Mike Johnson", "mike.j@email.com", "Engineering", "completed", "inbound_call", "Enrolled in UMD Engineering - Fall 2026"),
    ("+12025551006", "Sarah Lee", "sarah.lee@email.com", "MBA", "in_progress", "whatsapp", "Voice note asking about FDU MBA tuition and scholarships"),
    ("+12025551007", "Tom Harris", "tom.h@email.com", "Computer Science", "pending", "outbound_call", "Career fair lead, interested in AI/ML track"),
    ("+12025551008", "Emma Wilson", "emma.w@email.com", "Data Science", "failed", "inbound_call", "Not interested after learning tuition fees"),
    ("+12025551009", "David Brown", "david.b@email.com", "MBA", "pending", "streamlit", "Chatted on website, wants UMD MBA info"),
    ("+12025551010", "Lisa Garcia", "lisa.g@email.com", "Engineering", "in_progress", "outbound_call", "Follow-up scheduled, interested in FDU"),
    ("+12025551011", "Ryan Park", "ryan.park@email.com", "MBA", "unreachable", "outbound_call", "No answer on 3 attempts"),
    ("+12025551012", "Priya Patel", "priya.p@email.com", "Computer Science", "pending", "whatsapp", "Asked about visa process and FDU programs"),
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

# -- Dummy Conversations --
transcripts = [
    (
        "inbound_call",
        "Caller: Hi, I wanted to ask about the MBA program at UMD.\n"
        "Assistant: Hello! The UMD MBA is a 2-year full-time program with specializations in Finance, Marketing, and Data Analytics. Tuition is approximately $45,000 per year. What would you like to know?\n"
        "Caller: What about GMAT requirements?\n"
        "Assistant: UMD requires a minimum GMAT of 600 for the MBA program. With 4 years of experience, you would be a strong candidate. Shall I schedule a follow-up?\n"
        "Caller: Yes, please.",
        272,
        "interested",
    ),
    (
        "outbound_call",
        "Assistant: Hi Jane, I am calling from University Admissions. I noticed your interest in Computer Science. Do you have a moment?\n"
        "Caller: Yes, I was looking at the FDU MS in CS program. I am an international student from India.\n"
        "Assistant: Great! FDU MS in CS has tracks in AI/ML, Cybersecurity, and Software Engineering. International students need TOEFL 80 or IELTS 6.5. Tuition is $35,000 per year.\n"
        "Caller: Any scholarships?\n"
        "Assistant: Yes, FDU offers merit scholarships up to 50% for outstanding international students. Would you like details via WhatsApp?",
        198,
        "interested",
    ),
    (
        "whatsapp",
        "Student: What are the FDU MBA tuition fees?\n"
        "Assistant: FDU MBA tuition is $38,500 per year for the full-time program, including campus facilities, career services, and alumni network access. Financial aid and scholarships are available.\n"
        "Student: What scholarships?\n"
        "Assistant: FDU offers: Dean Scholarship (up to 40%), Merit Scholarship (up to 25%), International Student Scholarship (up to 30%). Most students qualify for at least one. Would you like a counselor to call you?",
        0,
        "info_given",
    ),
    (
        "inbound_call",
        "Caller: I want to know about UMD Data Science.\n"
        "Assistant: The UMD Data Science MS covers Machine Learning, Big Data, Statistical Modeling, and Data Visualization. Requires GRE 310+ and programming background.\n"
        "Caller: I have a CS degree. Tuition?\n"
        "Assistant: With CS background, you are well-qualified! Tuition is $42,000 per year. The program has partnerships with Google, Amazon, and Microsoft for internships. Graduates have 95% job placement within 3 months.\n"
        "Caller: Sounds great. Let me think about it.\n"
        "Assistant: Would you like a brochure and a follow-up call scheduled?",
        145,
        "interested",
    ),
    (
        "outbound_call",
        "Assistant: Hi, I am calling from Admissions about your program interest.\n"
        "Caller: Actually, I already enrolled elsewhere. Thank you.\n"
        "Assistant: Congratulations on your enrollment! If you ever want to explore transfer or graduate options at UMD or FDU, feel free to reach out. Have a great day!",
        45,
        "not_interested",
    ),
    (
        "whatsapp",
        "Student: Application deadline for FDU?\n"
        "Assistant: FDU deadlines: Fall - June 15, Spring - November 15. Early decision is March 1 with priority scholarships. Which program?\n"
        "Student: MS in CS.\n"
        "Assistant: Same deadlines apply for MS in CS. I recommend early decision by March 1 for best scholarship chances. Need help with the application?",
        0,
        "info_given",
    ),
    (
        "inbound_call",
        "Caller: Tell me about UMD Engineering.\n"
        "Assistant: UMD offers Mechanical, Electrical, Civil, and Computer Engineering. All 4-year ABET-accredited programs. State-of-the-art labs and mandatory 3rd-year internship. Which one?\n"
        "Caller: Mechanical.\n"
        "Assistant: UMD Mechanical Engineering is highly ranked. Admission: SAT 1300+ or ACT 28+, strong math and physics. Tuition: $32,000 domestic, $48,000 international, plus $12,000 room and board. Shall I send the detailed fee structure?\n"
        "Caller: Yes, please do.",
        210,
        "interested",
    ),
    (
        "inbound_call",
        "Caller: Hi, what programs does UMD offer for someone interested in business analytics?\n"
        "Assistant: UMD offers both an MBA with Data Analytics specialization and a standalone MS in Business Analytics. The MBA is broader, while the MS is more technical. What is your background?\n"
        "Caller: I work in finance, 5 years experience.\n"
        "Assistant: With 5 years in finance, the MBA with Data Analytics would be ideal. You would learn business strategy plus technical analytics skills. GMAT 620+ required, tuition $45,000/year. Many finance professionals choose this path.",
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

courses_data = [
    ("Computer Science", "4 Years", "$42,000/year", "Fall 2026, Spring 2027",
     "ABET-accredited program covering AI, systems, software engineering, and cybersecurity."),
    ("Data Science", "2 Years (MS)", "$38,000/year", "Fall 2026",
     "Interdisciplinary program with concentrations in ML, big data, and statistical modeling."),
    ("MBA", "2 Years", "$45,000/year", "Fall 2026, Spring 2027",
     "AACSB-accredited with specializations in Finance, Marketing, Analytics, and Healthcare."),
    ("Engineering", "4 Years", "$32,000/year (domestic) / $48,000/year (international)", "Fall 2026",
     "Mechanical, Electrical, Civil, and Computer Engineering tracks. ABET-accredited."),
    ("Business Analytics", "1 Year (MS)", "$35,000/year", "Fall 2026",
     "STEM-designated program. Combines business strategy with advanced data analytics."),
    ("Information Systems", "2 Years (MS)", "$30,000/year", "Spring 2027",
     "Focus on IT management, enterprise systems, and digital transformation."),
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
