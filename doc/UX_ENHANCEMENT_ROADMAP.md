# UX Enhancement Roadmap — Admissions Counselor Dashboard

**Document version:** 1.0
**Date:** 2026-08-01
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`
**Focus:** User experience for the admissions counselor (daily operator)

---

## User Persona: Admissions Counselor

| Attribute | Detail |
|-----------|--------|
| **Name** | Sarah (representative) |
| **Role** | University Admissions Counselor |
| **Daily tasks** | Review new leads, call prospects, answer queries, schedule follow-ups, track pipeline |
| **Volume** | 50–200 active leads at any time |
| **Tech comfort** | Comfortable with web apps, not technical |
| **Goal** | Convert inquiries into enrolled students efficiently |
| **Pain points today** | Leads scattered across channels (phone, WhatsApp, web), no unified view, manual follow-up tracking |

---

## Current State — UX Audit

### What We Have Today

| Surface | URL | Audience | Current State |
|---------|-----|----------|---------------|
| Admin Dashboard | `:8502` | Counselor | KPIs, lead list, conversations, scheduler |
| Chat UI | `:8501` | Student | RAG-powered text + voice Q&A |
| Voice Client | `/voice` | Student (browser) | Echo-only, not wired to AI |
| Quick Call | `/call` | Counselor (web) | Form to enter phone → trigger call |
| Root page | `/` | Anyone | Raw JSON (broken UX) |
| `quick_call.py` | CLI | Counselor (terminal) | Terminal-based call tool |

### Critical UX Gaps

1. **No unified entry point** — Counselor must know which port to visit, which page to use for each task. The app is 5 separate surfaces with no connection between them.

2. **Leads page is a flat list of expanders** — The counselor cannot scan, sort, prioritize, or batch-operate. Every action requires opening an expander individually.

3. **No call log / call history view** — The counselor cannot see "who did we call today?" or "what happened on the last call with this lead?" in a timeline format.

4. **No command interface** — The counselor cannot tell the AI system to "call all pending MBA leads" or "send WhatsApp to everyone who hasn't responded." Every action is one-at-a-time manual.

5. **Chat UI is siloed** — The student-facing chat (`app.py`) runs on a different port, with a different codebase, different models, and no connection to the lead database. Conversations there don't appear in the counselor's dashboard.

6. **No notifications or alerts** — The counselor must constantly refresh to see new WhatsApp messages, completed calls, or due follow-ups.

7. **Scheduler has no calendar view** — Follow-ups are a text list. A counselor managing 100+ leads needs a day/week calendar view.

8. **Search is broken or absent** — Phone search on Leads page is inert. No name search. No program-interest filter. No free-text search across leads.

9. **No pipeline visibility** — The KPI dashboard shows numbers but not "which leads are stuck?" or "who hasn't been contacted in 7 days?"

10. **No reporting or export** — The counselor cannot export leads to CSV, cannot generate a weekly summary, cannot share data with colleagues.

---

## UX Enhancement Catalog

Each item is structured as:

> **As a** counselor, **I want to** ___ **so that** ___.

---

### PHASE 1: Foundation — Fix What's Broken (4–6 hours)

---

#### UX-1.1: Unified Dashboard Home — Replace the KPI Page

**As a** counselor, **I want to** open one URL and see everything I need to act on today, **so that** I don't waste time switching between different apps and tabs.

**Current:** Overview page shows 5 KPI metric cards and a "Call Next Pending Lead" button. No prioritization, no "what should I do right now?" guidance.

**Design:**

```
┌─────────────────────────────────────────────────────────┐
│  🎓 Admissions Command Center            [Today: Aug 1]  │
│─────────────────────────────────────────────────────────│
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐  │
│  │  23  │ │   5  │ │   3  │ │   8  │ │  🔔 2 alerts  │  │
│  │ NEW  │ │CALL  │ │DUE   │ │ACTIVE│ │  New WhatsApp │  │
│  │leads │ │today │ │today │ │calls │ │  messages     │  │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘  │
│─────────────────────────────────────────────────────────│
│                                                         │
│  📋 PRIORITY QUEUE — What to do right now               │
│  ┌─────────────────────────────────────────────────┐    │
│  │ #  Lead          Reason              Action      │    │
│  │ 1  John Smith    Due follow-up today  📞 Call    │    │
│  │ 2  Jane Doe      New lead (1hr ago)   📞 Call    │    │
│  │ 3  Bob Chen      3 days no contact    📱 WhatsApp │    │
│  │ 4  Alice Kim     Asked about MBA      📞 Call    │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  📊 PIPELINE SNAPSHOT                                   │
│  [New (23)] → [Contacted (15)] → [Interested (8)] → ...  │
│                                                         │
│  📅 TODAY'S SCHEDULE                                    │
│  10:00 — Call: John Smith                               │
│  14:00 — Follow-up: Jane Doe                            │
│  16:00 — WhatsApp: Bob Chen                             │
└─────────────────────────────────────────────────────────┘
```

**Key elements to add:**
- **Priority Queue** — ranked list of "next best action" (new lead → call; dormant lead → nudge; due follow-up → act)
- **Alerts panel** — new WhatsApp messages, completed calls with outcomes, leads that went cold
- **Pipeline funnel** — visual flow from New → Contacted → Interested → Enrolled, with drop-off at each stage
- **Today's schedule** — timeline of scheduled calls and follow-ups

**Implementation notes:**
- Priority queue logic: sort leads by `(overdue_follow_up DESC, created_at ASC, last_called_at ASC NULLS FIRST)`
- Pipeline uses lead statuses already in the schema
- Schedule pulls from `follow_ups` where `scheduled_at::date = today`

**Files:** `app/dashboard/overview.py` (rewrite), new `app/dashboard/components/priority_queue.py`

**Effort:** 3 hours

---

#### UX-1.2: Fix and Enhance Lead Search

**As a** counselor, **I want to** search leads by name, phone, program, or status instantly, **so that** I can find any lead in seconds without scrolling through 200 entries.

**Current:** Phone search input exists but is NOT wired to the API. No name search. No program filter. Status filter works but requires a full page reload.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  🔍 Search leads...  [________________]  ⚡ (as you type)│
│                                                          │
│  Filter: [All Statuses ▼]  [All Programs ▼]  [All Sources ▼] │
│  Sort:   [Newest first ▼]                                │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │ 🟡 John Smith     +1234567890   MBA Program       │    │
│  │    Last called: Aug 1  |  Follow-up: Aug 3        │    │
│  │    [📞 Call] [💬 Chat] [📋 History] [📅 Schedule]  │    │
│  │──────────────────────────────────────────────────│    │
│  │ 🟢 Jane Doe       +1987654321   Computer Science  │    │
│  │    Called today · Outcome: Interested             │    │
│  │    [📞 Call] [💬 Chat] [📋 History] [📅 Schedule]  │    │
│  └──────────────────────────────────────────────────┘    │
│                                    1 2 3 ... 10  >      │
└──────────────────────────────────────────────────────────┘
```

**Key changes:**
- **Real-time search** — filter as you type (client-side for < 200 leads, API for more)
- **Program filter dropdown** — populated from distinct values in DB
- **Source filter** — whatsapp, inbound_call, outbound_call, streamlit, manual
- **Sort options** — newest, oldest, last called, next follow-up, name A-Z
- **Compact card view** — show name, phone, program, last contact, next follow-up WITHOUT needing to click an expander
- **Action buttons visible on card** — Call, Chat, History, Schedule always visible
- **Pagination** — 20 leads per page

**Files:** `app/dashboard/leads_page.py` (rewrite), `app/leads/models.py` (add search query), `app/main.py` (add search params to `/api/leads`)

**Effort:** 2 hours

---

#### UX-1.3: Call Log / Timeline View

**As a** counselor, **I want to** see a chronological timeline of all calls made, with outcomes and quick replay, **so that** I can review what happened today and prepare for tomorrow.

**Current:** Conversations page shows transcripts but as a flat filterable list. No date grouping, no call-specific metadata (duration, outcome, who initiated), no "today's calls" summary.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  📞 Call Log                          [Today ▼] [All ▼]  │
│──────────────────────────────────────────────────────────│
│                                                          │
│  TODAY — August 1, 2026                                  │
│  ┌──────────────────────────────────────────────────┐    │
│  │ 10:23 AM  📤 Outbound → John Smith                │    │
│  │           Duration: 4m 32s  |  Outcome: Interested │    │
│  │           🟢 Lead status updated to "in_progress"  │    │
│  │           [▶ Play] [📋 Transcript] [📅 Follow-up]   │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ 11:45 AM  📞 Inbound ← Jane Doe                   │    │
│  │           Duration: 2m 15s  |  Outcome: Info given │    │
│  │           Asked about MBA tuition and deadlines    │    │
│  │           [▶ Play] [📋 Transcript] [📅 Follow-up]   │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ 02:30 PM  📱 WhatsApp — Bob Chen                  │    │
│  │           Voice note: 0m 45s  |  AI replied        │    │
│  │           [📋 Transcript] [🔊 Play Audio Reply]     │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  YESTERDAY — July 31, 2026                               │
│  ┌──────────────────────────────────────────────────┐    │
│  │ 03:15 PM  📤 Outbound → Alice Kim                 │    │
│  │           Duration: 1m 08s  |  Outcome: Voicemail  │    │
│  │           🔴 Status: "unreachable" · Retry scheduled│    │
│  │           [📋 Transcript] [🔄 Retry Now]            │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**Key elements:**
- **Date-grouped timeline** — conversations organized by day, newest first
- **Direction indicator** — inbound (📞), outbound (📤), WhatsApp (💬)
- **Quick actions per call** — Play recording, View transcript, Schedule follow-up, Retry
- **Outcome badges** — Interested, Not Interested, Voicemail, Info Given, Callback Requested
- **Duration display** — call duration pulled from `call_duration_seconds`
- **Date filter** — Today, Yesterday, Last 7 days, Last 30 days, Custom range
- **Summary stats at top** — "5 calls today · 3 interested · 2 voicemail · 18 min total talk time"

**Files:** New `app/dashboard/call_log.py`, `app/main.py` (enhance `/api/conversations`), `app/leads/models.py`

**Effort:** 2.5 hours

---

### PHASE 2: Command & Control — Let the Counselor Instruct the AI (4–6 hours)

---

#### UX-2.1: AI Command Bar — Natural Language Lead Actions

**As a** counselor, **I want to** type natural language commands like "call all pending MBA leads" or "send WhatsApp to everyone who asked about tuition" and have the system execute them, **so that** I can manage 100 leads as easily as managing 5.

**Current:** Every action is one-at-a-time: click expander → click "Call Now" → wait → repeat. Batch operations don't exist. The MCP tools exist but require API/CLI access.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  💬 Command Bar                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  🤖 Tell the AI what to do...                     │    │
│  │                                                   │    │
│  │  "Call all pending leads who asked about MBA"     │    │
│  │                                          [⏎ Run] │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  💡 Suggestions:                                         │
│  [Call all new leads from today]  [Follow up with        │
│  [Send WhatsApp to pending leads]  everyone not called   │
│  [Call leads due for follow-up]   in 7 days]             │
│                                                          │
│  ┌─── Execution Log ────────────────────────────────┐    │
│  │  ✅ Queued call for John Smith (+1234567890)       │    │
│  │  ✅ Queued call for Alice Kim (+1987654321)        │    │
│  │  ⚠️  Bob Chen — no phone number, skipped          │    │
│  │  ─────────────────────────────────────────        │    │
│  │  📊 3 calls queued · 1 skipped · 0 failed          │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**How it works:**
1. Counselor types a natural language command
2. Backend sends the command + current lead context to the LLM
3. LLM parses intent and parameters: `{action: "call", filter: {status: "pending", program: "MBA"}}`
4. System validates, shows a **preview** of affected leads
5. Counselor confirms → system executes in batch
6. Results shown in execution log

**Supported commands (v1):**
- `call all [filter] leads` — queue calls for matching leads
- `send whatsapp to [filter] leads saying "[message]"` — queue WhatsApp messages
- `schedule follow-up for [filter] leads on [date]` — batch schedule
- `mark [filter] leads as [status]` — batch status update
- `show me [filter] leads` — filtered view (same as search but natural language)

**Filter language the LLM should understand:**
- Status: "pending", "new", "in progress", "completed"
- Program: "MBA", "Computer Science", "any program"
- Time: "from today", "from last 3 days", "not called in 7 days"
- Source: "from WhatsApp", "from website"

**Files:** New `app/commands/` module, `app/main.py` (add `/api/command` endpoint), `app/dashboard/components/command_bar.py`

**Effort:** 4 hours

---

#### UX-2.2: Quick Actions Panel — One-Click Batch Operations

**As a** counselor, **I want to** perform common batch actions with one click, without typing commands, **so that** routine tasks take seconds.

**Design:**

```
┌─────────────────────────────────────┐
│  ⚡ QUICK ACTIONS                    │
│─────────────────────────────────────│
│  [📞 Call All New Leads]             │
│      5 leads with status "pending"   │
│                                     │
│  [📅 Schedule Follow-ups]            │
│      3 leads due for contact today   │
│                                     │
│  [📱 WhatsApp All Pending]           │
│      Send reminder to 8 leads        │
│                                     │
│  [🔄 Retry Unreachable]              │
│      2 leads marked unreachable      │
│                                     │
│  [📊 Export Leads CSV]               │
│      Download all 47 leads           │
│                                     │
│  [🗑️ Reset Demo Data]               │
│      Clear everything, add samples   │
└─────────────────────────────────────┘
```

**Files:** `app/dashboard/components/quick_actions.py`, `app/main.py` (batch endpoints)

**Effort:** 1.5 hours

---

#### UX-2.3: Lead Detail Page — Everything About One Lead in One View

**As a** counselor, **I want to** click on a lead and see their full profile — contact info, all conversations, call recordings, AI-extracted interests, and action buttons — all on one page, **so that** I don't have to jump between 3 different tabs to understand this lead.

**Current:** Lead details are buried inside expanders on the leads list. To see conversations, you must navigate to a different page. To schedule follow-up, you navigate again. No single view.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  ← Back to Leads                                         │
│                                                          │
│  👤 JOHN SMITH                           [📞 Call Now]   │
│  ───────────────────────────────────────────────────────  │
│  📱 +1234567890  |  📧 john@email.com                     │
│  🎓 Program: MBA  |  📅 Created: Jul 28                   │
│  🟡 Status: In Progress  |  🔄 Call Attempts: 2           │
│  📝 Notes: Interested in part-time MBA, asked about fees  │
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  [📋 Info] [💬 Conversations] [📞 Call Log] [📅 Follow-ups] │
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  CONVERSATIONS (3)                                       │
│  ┌──────────────────────────────────────────────────┐    │
│  │ Aug 1, 10:23 AM — 📤 Outbound Call (4m 32s)      │    │
│  │ Outcome: Interested                               │    │
│  │ AI: "Hi John, I'm calling from admissions..."     │    │
│  │ John: "Yes, I'm interested in the MBA program..." │    │
│  │ AI: "The MBA program starts in September..."      │    │
│  │ [▶ Play Recording] [📋 Full Transcript]            │    │
│  ├──────────────────────────────────────────────────┤    │
│  │ Jul 30, 2:15 PM — 📱 WhatsApp                    │    │
│  │ John: "What are the MBA tuition fees?"            │    │
│  │ AI: "UMD MBA tuition is $45,000..."               │    │
│  │ [📋 Full Transcript]                              │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  FOLLOW-UPS                                              │
│  📅 Aug 5, 10:00 AM — Call to discuss application        │
│                                                          │
│  ACTIONS                                                 │
│  [📞 Call Now] [📱 Send WhatsApp] [📅 Schedule]           │
│  [✏️ Edit Lead] [📝 Add Note] [🔄 Change Status]         │
└──────────────────────────────────────────────────────────┘
```

**Key elements:**
- **Tabbed sub-navigation** — Info, Conversations, Call Log, Follow-ups
- **Inline conversation transcripts** — no need to navigate away
- **All actions visible** — Call, WhatsApp, Schedule, Edit always accessible
- **Lead metadata at top** — name, phone, email, program, status, notes
- **AI-extracted interests** — what the AI learned about this lead from calls

**Files:** New `app/dashboard/lead_detail.py`, `app/main.py` (enhance `/api/leads/{id}` with conversations)

**Effort:** 2.5 hours

---

### PHASE 3: Proactive Intelligence — The App Works for Sarah (3–5 hours)

---

#### UX-3.1: Smart Notification Center

**As a** counselor, **I want to** see alerts when things happen — new WhatsApp message, call completed, lead went cold — without constantly refreshing, **so that** I can react quickly instead of polling.

**Design:**

```
┌─────────────────────────────────────┐
│  🔔 NOTIFICATIONS              [3]  │
│─────────────────────────────────────│
│  🆕 2m ago                          │
│     New WhatsApp message from       │
│     Bob Chen — "MBA tuition?"       │
│     [Reply] [View Lead]             │
│─────────────────────────────────────│
│  📞 15m ago                         │
│     Call with John Smith completed  │
│     Outcome: Interested             │
│     [View Transcript] [Follow-up]   │
│─────────────────────────────────────│
│  ⚠️ 1h ago                          │
│     5 leads have not been contacted │
│     in 7+ days                      │
│     [View List] [Call All]          │
│─────────────────────────────────────│
│  📅 Tomorrow                        │
│     3 follow-ups scheduled          │
│     [View Schedule]                 │
└─────────────────────────────────────┘
```

**Implementation:**
- SSE (Server-Sent Events) endpoint streams notifications to the dashboard
- Polling fallback every 30 seconds
- Badge count on the bell icon in the top nav
- Clicking a notification navigates to the relevant page

**Files:** `app/main.py` (add `/api/notifications/stream` SSE endpoint), `app/dashboard/components/notifications.py`

**Effort:** 2 hours

---

#### UX-3.2: Lead Health Score & Cold Lead Detection

**As a** counselor, **I want to** see which leads are "going cold" so I can prioritize re-engagement before they're lost.

**Design:**

```
Lead health is computed as a simple score:

  Score = (recency_of_last_contact × 0.4)
        + (has_follow_up_scheduled × 0.3)
        + (conversation_count × 0.2)
        + (positive_sentiment × 0.1)

Displayed as:
  🟢 Hot   — contacted within 24h, follow-up scheduled
  🟡 Warm  — contacted within 72h
  🟠 Cool  — contacted within 7 days
  🔴 Cold  — no contact in 7+ days
  ⚫ Dead  — no contact in 30+ days
```

**Files:** `app/leads/service.py` (health score calculation), `app/dashboard/overview.py` (display)

**Effort:** 1.5 hours

---

#### UX-3.3: Calendar View for Follow-ups

**As a** counselor, **I want to** see all scheduled follow-ups on a calendar, **so that** I can plan my week at a glance instead of reading a text list.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  📅 Follow-up Calendar            [◀ July] [August ▶]    │
│──────────────────────────────────────────────────────────│
│  Mon 28    Tue 29    Wed 30    Thu 31    Fri 1 (Today)  │
│  ┌────┐    ┌────┐    ┌────┐    ┌────┐    ┌────────────┐  │
│  │    │    │    │    │ 2  │    │    │    │ 10:00 📞    │  │
│  │    │    │    │    │calls│    │    │    │ John Smith  │  │
│  │    │    │    │    │    │    │    │    │ 14:00 📞    │  │
│  │    │    │    │    │    │    │    │    │ Jane Doe    │  │
│  │    │    │    │    │    │    │    │    │ 16:00 📱    │  │
│  │    │    │    │    │    │    │    │    │ Bob Chen    │  │
│  └────┘    └────┘    └────┘    └────┘    └────────────┘  │
│                                                          │
│  UPCOMING — August 1, 2026                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │ 10:00  📞 John Smith — MBA follow-up              │    │
│  │         [Mark Done] [Reschedule] [View Lead]       │    │
│  │ 14:00  📞 Jane Doe — Initial consultation          │    │
│  │         [Mark Done] [Reschedule] [View Lead]       │    │
│  │ 16:00  📱 Bob Chen — WhatsApp check-in             │    │
│  │         [Mark Done] [Reschedule] [View Lead]       │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**Implementation:**
- Use Streamlit's native components for a simple week-view (no external calendar library needed for v1)
- Each day cell shows a count badge
- Click a day to see that day's follow-ups below
- Color code: 📞 call = blue, 📱 message = green
- "Schedule New" button opens a modal/drawer on the selected day

**Files:** `app/dashboard/scheduler_page.py` (rewrite with calendar view)

**Effort:** 2 hours

---

### PHASE 4: Connect Everything — Unified App (4–6 hours)

---

#### UX-4.1: Single Unified Streamlit App

**As a** counselor, **I want to** access everything from one URL with one sidebar, **so that** I don't need to remember which port has which feature.

**Current state:** 3 separate surfaces:
- `:8502` — Dashboard (Leads, Conversations, Scheduler, Overview)
- `:8501` — Student-facing Chat (separate app, separate models)
- `:8000/voice` & `:8000/call` — HTML pages on FastAPI

**Target state:**
```
One app on :8501 with sidebar navigation:

┌──────────────────────────────────────┐
│  🎓 Admissions Command Center        │
│──────────────────────────────────────│
│  📊 Command Center     (home)        │
│  👥 Leads                           │
│  💬 Conversations                    │
│  📞 Call Log                        │
│  📅 Calendar                        │
│  ─────────────────────              │
│  🤖 AI Chat (student-facing)        │
│  📱 Quick Call                      │
│  ─────────────────────              │
│  📊 Reports                         │
│  ⚙️ Settings                        │
│──────────────────────────────────────│
│  Backend: localhost:8000  🟢        │
│  Tunnel: xxx.trycloudflare.com      │
│  Twilio: +19788198953              │
└──────────────────────────────────────┘
```

**Implementation:**
- Merge `app.py` and `dashboard.py` into a single Streamlit entry point
- Student-facing chat becomes a page ("AI Chat") for the counselor to monitor/demo
- Quick Call form becomes a page
- Voice Client link becomes a sidebar item
- Single `start_services.ps1 -WithStreamlit` starts just ONE app

**Files:** New `app/unified_app.py`, restructured `app/pages/` directory, updated `start_services.ps1`

**Effort:** 3 hours

---

#### UX-4.2: Embedded Quick Call in Lead Pages

**As a** counselor, **I want to** click "Call" on any lead card and have the call happen immediately with live status, **so that** I don't have to go to a separate page to make a call.

**Current:** Quick Call (`/call`) is a separate HTML page that requires manually typing the phone number. Dashboard "Call Now" queues the call but doesn't show live status.

**Design:**

```
┌──────────────────────────────────────────┐
│  📞 Calling John Smith...                │
│──────────────────────────────────────────│
│  ┌──────────────────────────────────┐    │
│  │         🟢 Ringing...             │    │
│  │    +1234567890                    │    │
│  │                                  │    │
│  │    Status: Call in progress       │    │
│  │    Duration: 00:45                │    │
│  │                                  │    │
│  │    [📋 Live Transcript]           │    │
│  │    [⏹️ Hang Up]                   │    │
│  └──────────────────────────────────┘    │
│                                          │
│  Or use the phone: Dial +19788198953     │
│  Then enter lead code: 1234#            │
└──────────────────────────────────────────┘
```

**Implementation:**
- Embed a call status widget directly in the dashboard
- Poll `/api/call-queue?lead_id=xxx` every 2 seconds
- Show animated status rings (queued → ringing → in-progress → completed)
- Display live call duration
- After call completes, show outcome + transcript inline

**Files:** `app/dashboard/components/call_widget.py`, `app/main.py` (add `/api/calls/live` endpoint)

**Effort:** 2 hours

---

### PHASE 5: Reports & Insights (3–4 hours)

---

#### UX-5.1: Weekly Summary Report

**As a** counselor, **I want to** see a weekly summary — calls made, leads added, conversions, channel breakdown — **so that** I can report to my manager without manually counting.

**Design:**

```
┌──────────────────────────────────────────────────────────┐
│  📊 Weekly Report — July 28 to August 1, 2026             │
│──────────────────────────────────────────────────────────│
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │    47    │ │    23    │ │    12    │ │      8       │ │
│  │ NEW LEADS│ │CALLS MADE│ │ WHATSAPP │ │  CONVERTED   │ │
│  │  ↑12%   │ │  ↑5%     │ │  ↑20%    │ │   (enrolled)  │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘ │
│                                                          │
│  Calls by Outcome          Leads by Source               │
│  ┌──────────────────┐     ┌──────────────────┐          │
│  │ ████████ 12 Intr │     │ ████████ 20 Web  │          │
│  │ ████      6 Info │     │ ██████   15 Ref  │          │
│  │ ██        3 VM   │     │ ████      8 WA   │          │
│  │ █         2 NoAns│     │ ██        4 Man  │          │
│  └──────────────────┘     └──────────────────┘          │
│                                                          │
│  [📥 Export CSV]  [📤 Share Report]  [🖨️ Print]          │
└──────────────────────────────────────────────────────────┘
```

**Files:** New `app/dashboard/reports.py`, `app/leads/models.py` (aggregation queries)

**Effort:** 2 hours

---

#### UX-5.2: Export to CSV

**As a** counselor, **I want to** export leads or conversations to CSV with one click, **so that** I can open them in Excel for further analysis.

**Implementation:**
- `GET /api/leads/export?status=&program=&source=` → CSV download
- `GET /api/conversations/export?lead_id=&date_from=&date_to=` → CSV download
- Button on Leads page, Conversations page, and Reports page

**Files:** `app/main.py` (add export endpoints)

**Effort:** 1 hour

---

## Priority Implementation Order

This is ordered by **counselor impact per hour of work** — what makes the biggest difference to daily usability fastest:

| # | ID | Enhancement | Effort | Impact |
|---|-----|-------------|--------|--------|
| **1** | UX-1.2 | Fix & enhance lead search + card view | 2 hr | ⭐⭐⭐⭐⭐ |
| **2** | UX-1.3 | Call log / timeline view | 2.5 hr | ⭐⭐⭐⭐⭐ |
| **3** | UX-2.2 | Quick actions panel (batch ops) | 1.5 hr | ⭐⭐⭐⭐⭐ |
| **4** | UX-1.1 | Unified dashboard home with priority queue | 3 hr | ⭐⭐⭐⭐ |
| **5** | UX-2.3 | Lead detail page (all info in one view) | 2.5 hr | ⭐⭐⭐⭐ |
| **6** | UX-2.1 | AI command bar (natural language actions) | 4 hr | ⭐⭐⭐⭐⭐ |
| **7** | UX-3.3 | Calendar view for follow-ups | 2 hr | ⭐⭐⭐⭐ |
| **8** | UX-3.1 | Smart notification center | 2 hr | ⭐⭐⭐ |
| **9** | UX-3.2 | Lead health score & cold detection | 1.5 hr | ⭐⭐⭐ |
| **10** | UX-4.1 | Single unified Streamlit app | 3 hr | ⭐⭐⭐ |
| **11** | UX-4.2 | Embedded call widget in dashboard | 2 hr | ⭐⭐⭐ |
| **12** | UX-5.1 | Weekly summary report | 2 hr | ⭐⭐ |
| **13** | UX-5.2 | Export to CSV | 1 hr | ⭐⭐ |

**Total effort for all items:** ~29 hours

**MVP (minimum viable for a great counselor demo):** Items 1–5 (~11.5 hours)

---

## Design System Notes

All new UI should follow these patterns already established in the codebase:

| Element | Spec |
|---------|------|
| **Colors** | Dark theme: bg `#0f172a`, card `#1e293b`, accent `#3b82f6`, text `#e2e8f0` |
| **Status colors** | 🟡 pending, 🔵 in_progress, 🟢 completed, 🔴 failed, ⚫ unreachable |
| **Channel icons** | 📞 inbound, 📤 outbound, 💬 WhatsApp, 🌐 Streamlit |
| **Typography** | System font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto...` |
| **Cards** | `border-radius: 8-16px`, `box-shadow: 0 4px 24px rgba(0,0,0,0.35)` |
| **Buttons** | Full-width, rounded (8-10px), color-coded by action type |
| **Spacing** | 1rem base, 2rem section gaps |

---

## Related Documents

- `doc/ENHANCEMENT_ROADMAP.md` — Technical/system enhancements (the companion document)
- `DEMO_GUIDE.md` — How to run the demo
- `doc/PENDING_IMPROVEMENTS.md` — Older improvement notes
- `start_services.ps1` — One-shot launcher
