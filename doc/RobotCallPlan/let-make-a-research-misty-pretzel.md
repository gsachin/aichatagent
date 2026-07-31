# Business Requirement Document: Twilio Outbound Calling & Lead Management System

## For: University Admissions Voice Assistant
**Twilio Number:** +19788198953
**Date:** July 29, 2026

---

## Part 1: The Story — In Simple English

### Meet Sarah, the Admissions Counselor Who Never Sleeps

Imagine Sarah works in the admissions office of a university. Every day, she gets a list of 100 students who have shown interest — they filled out a form online, sent a WhatsApp message, or called the office.

Sarah's job is to call each student, one by one, tell them about the university programs, answer their questions, and note down whether they're interested. If a student says "call me next week," Sarah writes it on a sticky note and puts it on her calendar. At the end of the day, she types up all her notes.

**Now imagine we build Sarah a robot assistant.**

This robot assistant (our system) does everything Sarah does, but automatically:

1. **It has a phone number** — (+19788198953) — that students can WhatsApp message or call anytime. The robot answers using AI, with knowledge about UMD and FDU universities.

2. **It keeps a smart contact book** — instead of sticky notes, it has a database table called `leads`. Each lead has a phone number, a name, and a status. When the status is "pending," it means "this person needs to be called."

3. **It makes calls automatically** — the robot looks at the leads table, finds the next "pending" lead, and dials their number. When they answer, the robot has a natural voice conversation about university admissions. It knows all the facts from the university profile (tuition fees, programs, deadlines).

4. **It takes notes** — every word of the conversation is saved as a transcript in a `conversations` table. The robot also uses AI to extract the important bits: the student's name, email, what program they're interested in.

5. **It remembers follow-ups** — if a student says "call me next Tuesday at 3pm," the robot creates a follow-up entry in the database. When Tuesday at 3pm arrives, the robot automatically adds that lead back to the call queue.

6. **It has a dashboard** — a web page where you can see all leads, read conversation transcripts, schedule calls, and check who needs following up.

7. **It exposes MCP tools** — so other AI systems (like Claude) can ask "hey, what's the status of lead #42?" or "please call this new lead's phone number" — all through a standard protocol.

**In short:** We're transforming the existing chatbot (which already answers questions on WhatsApp and a web chat) into a full outbound calling system that can proactively reach out to prospective students, have intelligent conversations, and manage the entire follow-up workflow.

---

## Part 2: Current System — What We Already Have

### Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │         FastAPI Server              │
                    │         app/main.py                 │
                    │         Port: 8000                  │
                    └──────┬──────┬──────┬──────┬────────┘
                           │      │      │      │
     ┌─────────────────────┘      │      │      └─────────────────────────┐
     │                            │      │                                │
┌────▼────┐                ┌──────▼──┐ ┌─▼────────┐              ┌───────▼──────┐
│ Browser │                │ Twilio  │ │ Twilio   │              │ Streamlit UI │
│ Mic     │                │ Voice   │ │ WhatsApp │              │ app.py       │
│ /voice  │                │ Calls   │ │ Webhook  │              │ Port: 8501   │
└────┬────┘                └──────┬──┘ └────┬─────┘              └───────┬──────┘
     │                            │         │                            │
┌────▼────────────────────────────▼─────────▼────────────────────────────▼─────┐
│                                                                               │
│                          app/pipeline.py / app/rag.py                         │
│                 ┌───────────────────┐  ┌──────────────────┐                   │
│                 │  ChromaDB RAG      │  │  Ollama LLM      │                   │
│                 │  (nomic-embed)     │  │  (Qwen 2.5 7B)   │                   │
│                 └───────────────────┘  └──────────────────┘                   │
└───────────────────────────────────────────────────────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                              │
      ┌─────────▼─────────┐          ┌─────────▼─────────┐
      │ PostgreSQL         │          │ Kokoro TTS        │
      │ lead_calls table   │          │ (ONNX, local)     │
      └──────────────────┘          └──────────────────┘
```

### What Works Today

| Feature | Status | Details |
|---------|--------|---------|
| **WhatsApp text chat** | ✅ Working | Via `POST /twilio/whatsapp` webhook, Qwen LLM answers with RAG context |
| **WhatsApp voice notes** | ✅ Working | Async processing: Whisper STT → RAG → Kokoro TTS → audio reply |
| **Streamlit web chat** | ✅ Working | Text + mic input, LangChain RAG chain, optional TTS readback |
| **Inbound voice calls** | ⚠️ Partial | TwiML endpoint exists, Media Streams WebSocket echos audio (pipeline not wired) |
| **LLM RAG pipeline** | ✅ Working | ChromaDB with UMD & FDU university profile PDF |
| **Lead extraction** | ✅ Working | LLM extracts {name, email, program} from transcripts |
| **Database** | ✅ Working | PostgreSQL with `lead_calls` table (raw SQL, no ORM) |
| **Tunnel** | ✅ Working | Cloudflare tunnel auto-detection for public HTTPS |
| **TTS audio serving** | ✅ Working | MP3 files generated and served for WhatsApp replies |

### Existing Database Table

```sql
-- Current single table (app/database.py)
CREATE TABLE IF NOT EXISTS lead_calls (
    id              UUID PRIMARY KEY,
    phone_number    VARCHAR(32),
    transcript      TEXT,
    extracted_lead  JSONB,          -- {name, email, program}
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Key Gaps (What We're Missing)

- **No lead status tracking** — we don't know which leads are pending, called, or completed
- **No outbound calling** — the system can only receive calls, not make them
- **No conversation history** — WhatsApp and Streamlit chats are not logged to the database
- **No follow-up scheduling** — no way to schedule a callback
- **No MCP tools** — no programmatic API for other AI systems to interact
- **No admin dashboard** — no UI to view or manage leads
- **TWILIO_PHONE_NUMBER is empty** in `.env` — needs to be set to `+19788198953`

### Tech Stack

| Component | Technology | Location |
|-----------|-----------|----------|
| LLM | Qwen 2.5 7B via Ollama | `localhost:11434` |
| RAG Vector Store | ChromaDB (nomic-embed-text) | `chroma_local_db/` |
| STT | openai-whisper (base) / faster-whisper (small.en) | Local models |
| TTS | Kokoro ONNX (af_heart voice) | Local ONNX model |
| Database | PostgreSQL (psycopg2, raw SQL) | `localhost:5432/admissions` |
| Web Framework | FastAPI + Streamlit | Ports 8000, 8501 |
| Tunneling | Cloudflare Tunnel (cloudflared) | Auto-detected |
| Telephony | Twilio (WhatsApp + Voice) | Account configured |

---

## Part 3: New System — What We're Building

### High-Level Flow Chart

```
                         ┌──────────────────────────────┐
                         │     LEADS TABLE              │
                         │  phone, name, status,        │
                         │  program, follow_up_date     │
                         └──────────┬───────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌──────▼──────┐  ┌────▼─────┐
              │  PENDING   │  │ IN_PROGRESS │  │COMPLETED │
              │  (waiting  │  │  (on call   │  │  (done)  │
              │   to call) │  │   now)      │  │          │
              └─────┬─────┘  └─────────────┘  └──────────┘
                    │
         ┌──────────▼──────────┐
         │  OUTBOUND CALL      │
         │  WORKER             │
         │  (polls every 10s)  │
         └──────────┬──────────┘
                    │
                    │ Picks next "pending" lead
                    │ Initiates call via Twilio API
                    │
         ┌──────────▼──────────┐
         │  TWILIO             │
         │  +19788198953       │
         │                     │
         │  Dials lead's phone │
         │  Streams audio via  │
         │  WebSocket          │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │  VOICE PIPELINE     │
         │  (on our server)    │
         │                     │
         │  Mic → STT → RAG →  │
         │  LLM → TTS → Speaker│
         └──────────┬──────────┘
                    │
                    │ Call ends → status callback
                    │
         ┌──────────▼──────────┐
         │  POST-CALL LOGGING  │
         │                     │
         │  Save transcript    │
         │  Extract lead info  │
         │  Update lead status │
         │  Check follow-up    │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │  CONVERSATIONS      │
         │  TABLE              │
         │  (full history)     │
         └─────────────────────┘
```

### New Database Schema

We add 4 new tables alongside the existing `lead_calls`:

```
┌──────────────────────────────────────────────────────────────┐
│                      LEADS TABLE                             │
├──────────────────────────────────────────────────────────────┤
│ id (UUID)          │ PRIMARY KEY                             │
│ phone_number       │ VARCHAR(32) — the student's phone #     │
│ name               │ VARCHAR(255) — extracted or manual      │
│ email              │ VARCHAR(255) — extracted or manual      │
│ program_interest   │ VARCHAR(255) — e.g. "Computer Science"  │
│ status             │ VARCHAR(32) — see states below          │
│ source             │ VARCHAR(64) — whatsapp/streamlit/call   │
│ notes              │ TEXT — manual notes                     │
│ call_attempts      │ INTEGER — how many times we tried       │
│ last_called_at     │ TIMESTAMP                               │
│ next_follow_up     │ TIMESTAMP — when to call next           │
│ created_at         │ TIMESTAMP                               │
│ updated_at         │ TIMESTAMP                               │
└──────────────────────────────────────────────────────────────┘

LEAD STATUS FLOW:
  pending → in_progress → completed
     │           │
     └──→ failed/unreachable (can be retried)

┌──────────────────────────────────────────────────────────────┐
│                   CONVERSATIONS TABLE                        │
├──────────────────────────────────────────────────────────────┤
│ id (UUID)          │ PRIMARY KEY                             │
│ lead_id (FK)       │ REFERENCES leads(id)                    │
│ phone_number       │ VARCHAR(32)                             │
│ channel            │ VARCHAR(32) — whatsapp/streamlit/call   │
│ transcript         │ TEXT — full conversation                │
│ summary            │ TEXT — AI-generated summary             │
│ call_duration_sec  │ INTEGER                                 │
│ outcome            │ VARCHAR(64)                             │
│ follow_up_needed   │ BOOLEAN                                 │
│ follow_up_reason   │ TEXT                                    │
│ extracted_lead     │ JSONB — {name, email, program}          │
│ created_at         │ TIMESTAMP                               │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                   FOLLOW_UPS TABLE                           │
├──────────────────────────────────────────────────────────────┤
│ id (UUID)          │ PRIMARY KEY                             │
│ lead_id (FK)       │ REFERENCES leads(id)                    │
│ scheduled_at       │ TIMESTAMP — when to execute             │
│ status             │ VARCHAR(32) — scheduled/completed/...   │
│ type               │ VARCHAR(32) — call / message            │
│ notes              │ TEXT                                    │
│ created_at         │ TIMESTAMP                               │
│ completed_at       │ TIMESTAMP                               │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                   CALL_QUEUE TABLE                           │
├──────────────────────────────────────────────────────────────┤
│ id (UUID)          │ PRIMARY KEY                             │
│ lead_id (FK)       │ REFERENCES leads(id)                    │
│ status             │ VARCHAR(32) — queued/ringing/...        │
│ call_sid           │ VARCHAR(64) — Twilio call identifier    │
│ scheduled_at       │ TIMESTAMP                               │
│ started_at         │ TIMESTAMP                               │
│ completed_at       │ TIMESTAMP                               │
│ error_message      │ TEXT                                    │
│ created_at         │ TIMESTAMP                               │
└──────────────────────────────────────────────────────────────┘
```

### Complete System Flow

```
                          ┌──────────────┐
                          │  ANY CHANNEL │
                          │  (WhatsApp,  │
                          │  Streamlit,  │
                          │  Inbound Call│
                          │  MCP Tool)   │
                          └──────┬───────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
              ▼                  ▼                  ▼
    ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
    │  NEW LEAD?  │   │  REPLY TO   │   │  SCHEDULE   │
    │  CREATE IN   │   │  MESSAGE    │   │  FOLLOW-UP  │
    │  LEADS TABLE │   │  VIA LLM    │   │  IN DB      │
    └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
           │                 │                  │
           └─────────────────┼──────────────────┘
                             │
                   ┌─────────▼─────────┐
                   │  CONVERSATIONS    │
                   │  TABLE            │
                   │  (full transcript │
                   │   + extracted     │
                   │   lead data)      │
                   └─────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ OUTBOUND     │  │ FOLLOW-UP    │  │ MCP TOOLS    │
   │ CALL WORKER  │  │ SCHEDULER    │  │ (Claude/other│
   │ (polls       │  │ (polls       │  │  AI systems) │
   │  call_queue) │  │  follow_ups) │  │              │
   └──────┬───────┘  └──────┬───────┘  └──────────────┘
          │                 │
          │    ┌────────────┘
          │    │
          ▼    ▼
   ┌──────────────────┐
   │  TWILIO API      │
   │  +19788198953    │
   │                  │
   │  • Make calls    │
   │  • Send WhatsApp │
   │  • Stream audio  │
   └──────────────────┘

          ┌──────────────────┐
          │  ADMIN DASHBOARD │
          │  (Streamlit)     │
          │  Port 8502       │
          │                  │
          │  • View leads    │
          │  • Read convos   │
          │  • Schedule      │
          │  • Trigger calls │
          └──────────────────┘
```

---

## Part 4: Functional Requirements

### FR-1: Lead Management

**FR-1.1 — Lead Creation**
- System SHALL automatically create a lead when a new phone number interacts via WhatsApp, Streamlit, or voice call
- System SHALL allow manual lead creation via the admin dashboard and MCP tools
- Each lead SHALL have: phone_number (required), name, email, program_interest, status, source, notes

**FR-1.2 — Lead Status Lifecycle**
- Statuses: `pending` (ready to call), `in_progress` (on a call now), `completed` (done), `failed` (couldn't reach), `unreachable` (wrong number)
- Status transitions SHALL be logged with timestamps
- Pending leads SHALL be automatically picked up by the outbound call worker

**FR-1.3 — Lead Deduplication**
- System SHALL detect duplicate phone numbers and update existing leads rather than creating duplicates

### FR-2: Outbound Calling

**FR-2.1 — Automatic Call Queue**
- System SHALL maintain a `call_queue` table for outbound calls
- A background worker (`OutboundCallWorker`) SHALL poll the queue every 10 seconds
- Worker SHALL pick the next `queued` entry and initiate a Twilio voice call

**FR-2.2 — Call Flow**
- When a call is answered, Twilio SHALL stream audio to our WebSocket endpoint via Media Streams
- System SHALL run the voice pipeline: Speech-to-Text → RAG context retrieval → LLM response → Text-to-Speech
- LLM SHALL be prompted as a university admissions advisor with full context from the university profile (UMD & FDU)
- When the call ends, system SHALL save the full transcript and update lead status

**FR-2.3 — Call Status Tracking**
- Twilio status callbacks (completed, failed, busy, no-answer) SHALL update the call_queue and lead status
- Failed calls SHALL be retriable (call_attempts counter, max 3 attempts)

### FR-3: Conversation Logging

**FR-3.1 — Multi-Channel Logging**
- Every interaction SHALL be logged to the `conversations` table with: lead_id, channel (whatsapp/streamlit/inbound_call/outbound_call), full transcript, duration, outcome
- WhatsApp text messages, WhatsApp voice notes, Streamlit chat, inbound voice calls, and outbound calls SHALL all be logged

**FR-3.2 — AI Lead Extraction**
- After each conversation, the LLM SHALL extract structured data: {name, email, program_interest}
- Extracted data SHALL be stored in conversations.extracted_lead (JSONB)
- If the lead record is missing name/email/program, extracted data SHALL update the lead record

**FR-3.3 — Follow-Up Detection**
- LLM SHALL analyze conversation transcripts for follow-up requests (e.g., "call me next week")
- When detected, system SHALL automatically create a follow_up entry

### FR-4: Follow-Up Scheduling

**FR-4.1 — Schedule Management**
- Users SHALL be able to schedule follow-up calls via the admin dashboard and MCP tools
- Follow-ups have: lead_id, scheduled_at, type (call/message), status, notes

**FR-4.2 — Automated Execution**
- A `FollowUpScheduler` background task SHALL poll every 30 seconds for due follow-ups
- Due follow-ups SHALL be automatically added to the call_queue
- Completed follow-ups SHALL have their status updated

### FR-5: MCP Tools

**FR-5.1 — Tool Definitions**
The system SHALL expose these MCP tools via SSE transport:

| Tool | Purpose |
|------|---------|
| `add_lead` | Create a new lead (phone, name, email, program, source, notes) |
| `update_lead` | Update lead fields (status, notes, program_interest, etc.) |
| `trigger_call` | Queue an immediate outbound call to a lead |
| `view_conversations` | Retrieve conversation history for a lead |
| `schedule_follow_up` | Schedule a follow-up call or message |
| `check_lead_status` | Get current status of a lead by ID or phone number |
| `list_leads` | List leads, optionally filtered by status |

**FR-5.2 — MCP Transport**
- MCP server SHALL be integrated into the existing FastAPI process
- SSE endpoint at `GET /mcp/sse`
- Message handler at `POST /mcp/messages`

### FR-6: Admin Dashboard

**FR-6.1 — Pages**
| Page | Content |
|------|---------|
| **Overview** | KPI cards (total leads, pending, completed, calls today), upcoming follow-ups, quick-call button |
| **Leads** | Filterable data table, add/edit forms, status management, action buttons |
| **Conversations** | Transcript viewer with search, channel filter, export |
| **Scheduler** | Follow-up calendar/table, schedule form, complete/cancel actions |

**FR-6.2 — Implementation**
- Separate Streamlit app (`dashboard.py`) on port 8502
- Communicates with FastAPI backend via REST API endpoints

---

## Part 5: Technical Design

### New File Structure

```
D:\university_project_demo\
├── app/
│   ├── leads/                    # NEW PACKAGE
│   │   ├── __init__.py
│   │   ├── schema.py             # SQL CREATE TABLE statements + indexes
│   │   ├── models.py             # CRUD functions (async psycopg2)
│   │   ├── service.py            # Business logic
│   │   └── mcp_tools.py          # MCP tool implementations
│   ├── outbound/                 # NEW PACKAGE
│   │   ├── __init__.py
│   │   ├── caller.py             # OutboundCallWorker (async polling loop)
│   │   ├── twiml.py              # Outbound TwiML templates
│   │   └── scheduler.py          # FollowUpScheduler (AsyncIOScheduler)
│   ├── mcp/                      # NEW PACKAGE
│   │   ├── __init__.py
│   │   ├── server.py             # FastMCP server, SSE transport
│   │   └── tools.py              # Tool registration
│   ├── dashboard/                # NEW PACKAGE
│   │   ├── __init__.py
│   │   ├── pages.py              # Navigation
│   │   ├── overview.py           # KPI dashboard
│   │   ├── leads_page.py         # Lead management UI
│   │   ├── conversations_page.py # Transcript viewer
│   │   └── scheduler_page.py     # Follow-up management
│   ├── config.py                 # MODIFY: add new settings
│   ├── database.py               # MODIFY: add init_new_tables()
│   ├── main.py                   # MODIFY: new endpoints, lifespan wiring
│   └── pipeline.py               # MODIFY: update post_call_handler
├── dashboard.py                  # NEW: Streamlit entry point (port 8502)
├── .env                          # MODIFY: set TWILIO_PHONE_NUMBER
└── requirements.txt              # MODIFY: add apscheduler, mcp
```

### New FastAPI Endpoints

**REST API (for dashboard + MCP fallback):**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/leads` | Create lead |
| GET | `/api/leads` | List leads (filterable) |
| GET | `/api/leads/{id}` | Get lead detail |
| PUT | `/api/leads/{id}` | Update lead |
| POST | `/api/leads/{id}/call` | Trigger outbound call |
| GET | `/api/conversations` | List conversations |
| POST | `/api/follow-ups` | Schedule follow-up |
| GET | `/api/stats` | Dashboard KPIs |

**Twilio Webhooks:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/twilio/outbound/status` | Outbound call status callback |

**WebSocket:**
| Method | Path | Purpose |
|--------|------|---------|
| WS | `/ws/twilio-outbound` | Media Streams for outbound calls |

**MCP:**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/mcp/sse` | MCP SSE transport |
| POST | `/mcp/messages` | MCP message handler |

### New Config Settings

```python
# app/config.py additions
OUTBOUND_POLL_INTERVAL: int = 10      # seconds between queue polls
MAX_CALL_ATTEMPTS: int = 3            # max retries per lead
FOLLOW_UP_POLL_INTERVAL: int = 30     # seconds between follow-up checks
MCP_ENABLED: bool = True              # toggle MCP server
```

### Background Workers

Two background tasks run inside the FastAPI process:

1. **OutboundCallWorker** — polls `call_queue` every 10s, initiates Twilio calls one at a time
2. **FollowUpScheduler** — polls `follow_ups` every 30s, adds due items to `call_queue`

Both start/stop with the FastAPI lifespan.

---

## Part 6: Implementation Phases

### Phase 1: Database Foundation
- Create `app/leads/schema.py` with all table DDL
- Modify `app/database.py` to initialize new tables
- Create `app/leads/models.py` with CRUD functions
- Create `app/leads/service.py` with business logic
- Modify `app/config.py` with new settings
- **Verify:** Script that creates tables, inserts lead, queries back

### Phase 2: Conversation Logging
- Wire existing WhatsApp, Streamlit, and voice call paths to log to `conversations` table
- Update `post_call_handler` in `app/pipeline.py`
- **Verify:** Send WhatsApp message, check conversation row in DB

### Phase 3: Outbound Call Engine
- Set `TWILIO_PHONE_NUMBER=+19788198953` in `.env`
- Create `app/outbound/twiml.py` (TwiML templates)
- Create `app/outbound/caller.py` (OutboundCallWorker)
- Add `WS /ws/twilio-outbound` endpoint in `app/main.py`
- Add `POST /twilio/outbound/status` callback in `app/main.py`
- Wire worker into FastAPI lifespan
- **Verify:** Insert call_queue entry, confirm Twilio call is placed

### Phase 4: Follow-Up Scheduling
- Add `apscheduler` to requirements
- Create `app/outbound/scheduler.py` (FollowUpScheduler)
- Wire into FastAPI lifespan
- Add due-follow-up query to `app/leads/models.py`
- **Verify:** Schedule follow-up 1 min ahead, confirm call_queue entry appears

### Phase 5: MCP Tools
- Add `mcp` SDK to requirements
- Create `app/leads/mcp_tools.py` (7 tool implementations)
- Create `app/mcp/server.py` (FastMCP + SSE transport)
- Create `app/mcp/tools.py` (registration layer)
- Mount `/mcp/sse` and `/mcp/messages` in `app/main.py`
- **Verify:** Connect MCP client, call `list_leads()`, get results

### Phase 6: Admin Dashboard
- Create `app/dashboard/` package (5 files)
- Create `dashboard.py` entry point (port 8502)
- Add REST API endpoints in `app/main.py` for dashboard data
- Add stats aggregation queries in `app/leads/models.py`
- Update `start_demo.bat` to launch dashboard
- **Verify:** Open dashboard, view leads, schedule call, read transcript

### Phase 7: Documentation
- Write `doc/LEAD_MANAGEMENT_BRD.md` (this document, expanded)

---

## Part 7: Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| AppLocker blocks `apscheduler` or `mcp` DLLs | Cannot start background tasks or MCP | Fallback: use `asyncio.create_task` + sleep loops for scheduler. Fallback: REST API only (no MCP SDK). |
| Twilio trial account limits outbound calls | Cannot dial unverified numbers | Verify target numbers in Twilio console, or upgrade account. Document setup steps. |
| Voice pipeline not fully wired for Media Streams | Outbound calls connect but AI doesn't speak | `/ws/twilio-outbound` follows same pattern as existing `/ws/twilio`. The existing WhatsApp async voice note pipeline (STT→RAG→TTS) works and can serve as the initial implementation while Media Streams pipeline is completed. |
| Cloudflare tunnel URL changes on restart | Outbound TwiML points to wrong host | Already solved: `.whatsapp_tunnel` file auto-updated by `start_demo.bat`. Resolve host at call-time. |
| Concurrent call conflicts | Two workers process same queued entry | Atomic DB status transition: `UPDATE ... WHERE status='queued' RETURNING *`. Only one worker. |

---

## Part 8: Success Criteria

1. **Twilio number +19788198953 is active** — receives WhatsApp messages and voice calls
2. **Leads are auto-created** — when someone messages or calls, a lead record appears
3. **Outbound calls work** — adding a lead with status "pending" results in an automated call
4. **Conversations are logged** — every interaction (any channel) is saved with full transcript
5. **Follow-ups execute automatically** — scheduled follow-ups trigger calls at the right time
6. **MCP tools are accessible** — Claude or another MCP client can query and control the system
7. **Dashboard is usable** — admin can view leads, read transcripts, schedule calls from a web UI
