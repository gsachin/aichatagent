# Command Cockpit Dashboard — Single-Page Counselor Dashboard

**Document version:** 1.0
**Date:** 2026-08-01
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`
**Focus:** A single HTML page that replaces fragmented dashboards with one command center

---

## 1. Vision

**One URL. One screen. Complete control.**

The admissions counselor opens `https://xxx.trycloudflare.com/dashboard` and sees **everything** — live calls, lead pipeline, chat transcripts, follow-ups, lead scores, and a quick-call panel. No switching between ports. No opening Streamlit separately. No refreshing.

---

## 2. Full Page Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  🎓 Admissions Command Cockpit                    🔔 3 alerts    🟢 System OK │
│  ─────────────────────────────────────────────────────────────────────────── │
│                                                                              │
│  ┌── ROW 1 ───────────────────────────────────────────────────────────────┐ │
│  │                                                                            │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────┐│ │
│  │  │  📞 ACTIVE   │ │  🆕 NEW     │ │  ⏰ DUE      │ │  🔥 HOT      │ │ 📊   ││ │
│  │  │   CALLS      │ │  LEADS      │ │  FOLLOW-UPS │ │  LEADS       │ │TOTAL ││ │
│  │  │     2        │ │    12       │ │     5       │ │     8       │ │  47  ││ │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └──────┘│ │
│  │                                                                            │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌── ROW 2 — Left (60%) ─────────────┐ ┌── ROW 2 — Right (40%) ───────────┐ │
│  │                                    │ │                                    │ │
│  │  📞 LIVE CALL MONITOR              │ │  ⚡ QUICK CALL — BATCH DIALER      │ │
│  │  ┌────────────────────────────┐    │ │  ┌────────────────────────────┐    │ │
│  │  │ Call #1: John Smith        │    │ │  │ Phone numbers (one per     │    │ │
│  │  │ 📤 Outbound · 02:34 elapsed│    │ │  │ line or comma-separated):  │    │ │
│  │  │ 🟢 In Progress             │    │ │  │                            │    │ │
│  │  │ ─────────────────────────  │    │ │  │ +1234567890                │    │ │
│  │  │ AI: "Hi John, I'm calling  │    │ │  │ +1987654321                │    │ │
│  │  │ from admissions..."        │    │ │  │ +1122334455                │    │ │
│  │  │ John: "Yes, I wanted to    │    │ │  │                            │    │ │
│  │  │ ask about MBA fees..."     │    │ │  │ [+ Add More Lines]         │    │ │
│  │  │ AI: "UMD MBA tuition is    │    │ │  │────────────────────────────│    │ │
│  │  │ $45,000 per year..."       │    │ │  │ Name (optional):           │    │ │
│  │  │          [⏹ End Call]      │    │ │  │ [____________________]     │    │ │
│  │  └────────────────────────────┘    │ │  │                            │    │ │
│  │                                    │ │  │ Program Interest:          │    │ │
│  │  ┌────────────────────────────┐    │ │  │ [MBA            ▼]         │    │ │
│  │  │ Call #2: Jane Doe          │    │ │  │                            │    │ │
│  │  │ 📞 Inbound · 00:18 elapsed │    │ │  │ Source: [Manual     ▼]     │    │ │
│  │  │ 🟢 In Progress             │    │ │  │────────────────────────────│    │ │
│  │  │          [⏹ End Call]      │    │ │  │                            │    │ │
│  │  └────────────────────────────┘    │ │  │ [📞 Call All Now]          │    │ │
│  │                                    │ │  │ [📞 Call One by One]       │    │ │
│  │  (No active calls = hidden)        │ │  │ [📱 Send WhatsApp Instead] │    │ │
│  │                                    │ │  └────────────────────────────┘    │ │
│  └────────────────────────────────────┘ │                                    │ │
│                                         │  📊 CALL QUEUE STATUS                │ │
│                                         │  ┌────────────────────────────┐    │ │
│                                         │  │ #1 John Smith  🟡 Queued   │    │ │
│                                         │  │ #2 Jane Doe    🔵 Ringing  │    │ │
│                                         │  │ #3 Bob Chen    ⏳ Waiting  │    │ │
│                                         │  └────────────────────────────┘    │ │
│                                         └────────────────────────────────────┘ │
│                                                                              │
│  ┌── ROW 3 — Pipeline Board (Kanban) ──────────────────────────────────────┐ │
│  │                                                                            │
│  │  📋 LEAD PIPELINE                                                         │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │ │
│  │  │ 🆕 NEW   │  │ 📞 FIRST │  │ 💬 ACTIVE│  │ 🟢 HOT   │  │ ✅ CLOSED│   │ │
│  │  │   (12)   │  │ CONTACT  │  │ CONVO    │  │ FOLLOW-UP│  │  WON (5) │   │ │
│  │  │          │  │   (8)    │  │   (10)   │  │   (8)    │  │          │   │ │
│  │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │   │ │
│  │  ││ John │ │  ││ Jane │ │  ││ Bob  │ │  ││Alice │ │  ││Sarah │ │   │ │
│  │  ││ MBA  │ │  ││ CS   │ │  ││ MBA  │ │  ││ MBA  │ │  ││ CS   │ │   │ │
│  │  ││🔥Hot │ │  ││🟡Warm│ │  ││🟡Warm│ │  ││🔥Hot │ │  ││✅ Won│ │   │ │
│  │  ││⭐8.2 │ │  ││⭐6.5 │ │  ││⭐7.1 │ │  ││⭐9.0 │ │  ││⭐8.5 │ │   │ │
│  │  │└──────┘ │  │└──────┘ │  │└──────┘ │  │└──────┘ │  │└──────┘ │   │ │
│  │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │  │┌──────┐ │   │ │
│  │  ││ Mike │ │  ││ Lisa │ │  ││ Tom  │ │  ││Emma │  │  ││David │ │   │ │
│  │  ││ Data │ │  ││ MBA  │ │  ││ Eng  │ │  ││ CS   │ │  ││ MBA  │ │   │ │
│  │  ││🟡Warm│ │  ││🟠Cool│ │  ││🔥Hot │ │  ││🟡Warm│ │  ││❌Lost│ │   │ │
│  │  ││⭐5.0│ │  ││⭐4.2 │ │  ││⭐7.8 │ │  ││⭐6.0 │ │  ││⭐3.0 │ │   │ │
│  │  │└──────┘ │  │└──────┘ │  │└──────┘ │  │└──────┘ │  │└──────┘ │   │ │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │ │
│  │                                                                            │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌── ROW 4 — Recent Activity ──────────────────────────────────────────────┐ │
│  │                                                                            │
│  │  📜 RECENT CALLS & CHATS (Last 24 Hours)                     [View All]   │ │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │ │
│  │  │ Time     Type     Lead           Outcome     Duration  Score  Actions  │ │
│  │  │ ───────  ──────   ────────────   ─────────   ────────  ─────  ─────── │ │
│  │  │ 11:45 AM 📞 In    Jane Doe       Interested  2m 15s    ⭐7.5  [▶][📋] │ │
│  │  │ 10:23 AM 📤 Out   John Smith     Interested  4m 32s    ⭐8.2  [▶][📋] │ │
│  │  │ 09:15 AM 💬 WA    Bob Chen       Info Req    —         ⭐6.0  [📋]    │ │
│  │  │ 08:00 AM 📤 Out   Alice Kim      Voicemail   1m 08s    ⭐5.5  [🔄]    │ │
│  │  │ Yesterday📤 Out   Tom Harris     Not Intrst  0m 45s    ⭐2.0  [📋]    │ │
│  │  └──────────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                            │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌── ROW 5 — Footer Tabs ──────────────────────────────────────────────────┐ │
│  │                                                                            │
│  │  [📋 All Conversations] [📅 Follow-up Calendar] [📊 Reports] [⚙️ Settings]│ │
│  │                                                                            │
│  │  ── Expanded Tab Content (toggles visibility) ──────────────────────────  │ │
│  │  ┌──────────────────────────────────────────────────────────────────────┐ │ │
│  │  │ (Conversation search, transcript viewer, calendar view, or reports)   │ │ │
│  │  └──────────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                            │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Section Specifications

### 3.1 KPI Stat Cards (Row 1)

**Data source:** `GET /api/stats` + `GET /api/call-queue?status=active`

**Display:**
- **Active Calls** — count of calls currently in "ringing" or "in-progress" status. Pulsing green dot if > 0
- **New Leads** — leads with status "pending" created today. Pulsing blue dot if any are < 1 hour old
- **Due Follow-ups** — count of `follow_ups` scheduled for today. Yellow badge if > 0
- **Hot Leads** — leads with health score ≥ 7.0 (see scoring below). Red/orange flame icon
- **Total Pipeline** — grand total of non-closed leads

**Refresh:** Every 10 seconds via `setInterval()` polling

**Visual:** Each card is a rounded rectangle with large number, small label, and subtle background color. On click, scrolls to the relevant section below.

---

### 3.2 Live Call Monitor (Row 2 — Left)

**Data source:** `GET /api/calls/live` (new SSE endpoint needed) + `GET /api/call-queue?status=active`

**What it shows:**
- Every active call (inbound or outbound) as a card
- Real-time transcript streaming (new utterances appear as they happen)
- Call duration timer (counting up)
- Direction indicator (📞 inbound, 📤 outbound)
- Caller phone number + lead name if known
- "End Call" button (calls Twilio API to hang up)

**Transcript display:**
```
AI: "Hi, I'm calling from admissions..."     ← gray text
John: "Yes, I wanted to ask about MBA..."    ← white text
AI: "UMD MBA tuition is $45,000 per year..." ← gray text
```

New lines append at the bottom. Auto-scroll. Fade-in animation (300ms).

**When no active calls:** Section collapses to "No active calls" with a phone icon. Click expands the quick-call panel.

**Implementation:** SSE (Server-Sent Events) endpoint streams transcript updates. Fallback: poll `/api/call-queue` every 3 seconds.

**New endpoint needed:** `GET /api/calls/live` — returns current active calls with their latest transcript snippet. Accepts `?stream=true` for SSE mode.

**SSE event format:**
```
event: transcript
data: {"call_sid": "CAxxx", "lead_name": "John Smith", "speaker": "caller", "text": "Yes, I wanted to ask...", "timestamp": "..."}

event: call_started
data: {"call_sid": "CAxxx", "direction": "outbound", "phone": "+1234567890", "lead_name": "John Smith"}

event: call_ended
data: {"call_sid": "CAxxx", "outcome": "interested", "duration_seconds": 272}
```

---

### 3.3 Quick Call — Batch Dialer (Row 2 — Right)

**Data source:** `POST /api/quick-call` (existing, but needs batch variant)

**This is the key feature the counselor requested.** She can feed multiple phone numbers, and the AI calls them one by one automatically.

**Three modes:**

#### Mode 1: "Call All Now"
- Counselor pastes a list of phone numbers (one per line or comma-separated)
- Clicks "Call All Now"
- System creates leads for each number (if not existing), queues all calls
- Calls execute sequentially (the outbound worker processes them one at a time)
- Queue status panel shows progress: "✅ 3 done · 🔵 1 ringing · ⏳ 2 waiting"

#### Mode 2: "Call One by One" (Manual Pace)
- Same input, but after each call completes, the system pauses and shows the outcome
- Counselor clicks "Next Call" to proceed, or "Skip" to move to the next lead
- Shows a summary after each call: "John Smith → Interested (4m 32s)"
- Useful for the counselor to review each call before moving on

#### Mode 3: "Single Quick Call"
- Enter one phone number + name + program interest
- Click "Call Now"
- System upserts lead, queues call, shows live status
- Same as the existing `/call` page but embedded in the dashboard

**Batch call flow:**

```
┌──────────────────────────────────────────────────────┐
│  ⚡ QUICK CALL — BATCH DIALER                         │
│──────────────────────────────────────────────────────│
│                                                      │
│  📋 Phone Numbers:                                   │
│  ┌──────────────────────────────────────────────┐    │
│  │ +1234567890  (John Smith — MBA)               │    │
│  │ +1987654321  (Jane Doe — Computer Science)    │    │
│  │ +1122334455  (Bob Chen)                       │    │
│  │ +1555666777                                    │    │
│  │                                              │    │
│  │ [Type or paste numbers here...]               │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  📝 Default Name (optional):  [________________]     │
│  🎓 Default Program:          [Auto-detect ▼]        │
│  📱 Source:                   [Manual ▼]             │
│                                                      │
│  ───────────────────────────────────────────────     │
│                                                      │
│  [📞 Call All Now]  [📞 Call One by One]  [📱 Send WhatsApp] │
│                                                      │
│  ───────────────────────────────────────────────     │
│                                                      │
│  📊 CALL QUEUE STATUS                                │
│  ┌──────────────────────────────────────────────┐    │
│  │ ✅ John Smith    Completed · Interested · 4m32s│    │
│  │ 🔵 Jane Doe      Ringing...                  │    │
│  │ ⏳ Bob Chen       Waiting (next in queue)      │    │
│  │ ⏳ +1555666777   Waiting                     │    │
│  │                                              │    │
│  │ Progress: ████████░░░░  2/4 calls done        │    │
│  │                                              │    │
│  │ [⏹ Stop All]  [📊 View Report]               │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  💡 TIP: Paste numbers from Excel or CSV.            │
│  Format: +1234567890, Name, Program (optional)       │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**CSV parsing:**
The input box accepts multiple formats:
- Plain: `+1234567890`
- With name: `+1234567890 John Smith`
- With name + program: `+1234567890, John Smith, MBA`
- Comma-separated: `+1234567890, +1987654321, +1122334455`
- Excel paste (tab-separated): `+1234567890\tJohn Smith\tMBA`

**API design:**

New endpoint: `POST /api/quick-call/batch`
```json
{
  "leads": [
    {"phone_number": "+1234567890", "name": "John Smith", "program_interest": "MBA"},
    {"phone_number": "+1987654321", "name": "Jane Doe", "program_interest": "Computer Science"}
  ],
  "mode": "all_at_once"  // or "one_by_one"
}
```

Response:
```json
{
  "batch_id": "batch_abc123",
  "total": 4,
  "queued": 4,
  "skipped": 0,
  "errors": [],
  "call_queue_ids": ["uuid1", "uuid2", "uuid3", "uuid4"]
}
```

Polling endpoint: `GET /api/quick-call/batch/{batch_id}`
```json
{
  "batch_id": "batch_abc123",
  "total": 4,
  "completed": 2,
  "ringing": 1,
  "waiting": 1,
  "results": [
    {"phone": "+1234567890", "status": "completed", "outcome": "interested", "duration": 272},
    {"phone": "+1987654321", "status": "ringing"},
    {"phone": "+1122334455", "status": "queued"},
    {"phone": "+1555666777", "status": "queued"}
  ]
}
```

---

### 3.4 Pipeline Board — Kanban View (Row 3)

**Data source:** `GET /api/leads?limit=200`

**Columns (lead statuses mapped to pipeline stages):**

| Column | Status Filter | Color | Description |
|--------|--------------|-------|-------------|
| 🆕 New | `pending`, created today | Blue | Fresh leads, not yet contacted |
| 📞 First Contact | `pending`, called 1-2 times | Yellow | Attempted but no conversation yet |
| 💬 Active Conversation | `in_progress` | Teal | Had at least one real conversation |
| 🟢 Hot Follow-up | `in_progress`, follow-up scheduled today | Green | Scheduled for contact today |
| ✅ Closed Won | `completed`, outcome=interested | Emerald | Converted / enrolled |
| ❌ Closed Lost | `failed`, `unreachable` | Gray | Not interested or unreachable |

**Each lead card shows:**
- Name (or "Unknown" if no name)
- Program interest (e.g., "MBA", "Computer Science")
- Temperature badge: 🔥 Hot / 🟡 Warm / 🟠 Cool / 🔴 Cold
- Score badge: ⭐ 8.2
- Last contact timestamp (e.g., "2h ago", "Yesterday")
- Click → opens lead detail modal

**Card design:**
```
┌──────────────────┐
│ John Smith       │
│ MBA              │
│ 🔥 Hot  ⭐ 8.2   │
│ Last: 2h ago     │
└──────────────────┘
```

Small cards, 3-4 visible per column without scrolling. Overflow → "Show all 12 →" link that expands the column or opens a filtered list.

**Drag and drop (v2):** Drag a card from one column to another to update lead status. v1 uses a dropdown on the detail modal.

---

### 3.5 Lead Scoring Algorithm

**Data source:** Computed client-side or via `GET /api/leads/{id}/score`

**Score: 1.0 – 10.0**

```
SCORE = (RECENCY × 0.25) + (ENGAGEMENT × 0.30) + (RESPONSE × 0.25) + (INTEREST × 0.20)
```

| Factor | Weight | How It's Calculated |
|--------|--------|---------------------|
| **Recency** | 25% | Last contact within 1h = 10, 24h = 8, 3d = 5, 7d = 3, 14d+ = 1 |
| **Engagement** | 30% | 0 calls = 1, 1 call = 4, 2+ calls with actual conversation = 7, WhatsApp replies = +2 bonus |
| **Response** | 25% | Answered calls? Yes = 8, Voicemail = 4, No answer = 1, Multiple no-answers = 0 |
| **Interest** | 20% | Explicit "interested" in transcript = 10, Asked questions = 7, Neutral = 4, "Not interested" = 1 |

**Display:** Numeric badge on each lead card. Color-coded: 8–10 green, 5–7 yellow, 1–4 red.

**Auto-update:** Recalculated after every new conversation entry.

---

### 3.6 Hot / Warm / Cold Classification

**Data source:** Computed from `last_called_at` and `next_follow_up` fields

```
🟢 HOT   — last contact < 24 hours ago AND follow-up scheduled
🟡 WARM  — last contact < 72 hours ago
🟠 COOL  — last contact < 7 days ago
🔴 COLD  — last contact ≥ 7 days ago
⚫ DEAD  — last contact ≥ 30 days ago
```

**Display:** Colored dot or badge on every lead card, in the pipeline board, and in the recent activity table.

**Filter:** Quick filter buttons at the top of the pipeline: `[🔥 Hot] [🟡 Warm] [🟠 Cool] [🔴 Cold] [All]`

---

### 3.7 Recent Activity Table (Row 4)

**Data source:** `GET /api/conversations?limit=20`

**Columns:**

| Time | Type | Lead | Outcome | Duration | Score | Actions |
|------|------|------|---------|----------|-------|---------|
| 11:45 AM | 📞 In | Jane Doe | Interested | 2m 15s | ⭐7.5 | [▶ Play] [📋 Transcript] |
| 10:23 AM | 📤 Out | John Smith | Interested | 4m 32s | ⭐8.2 | [▶ Play] [📋 Transcript] |
| 09:15 AM | 💬 WA | Bob Chen | Info Req | — | ⭐6.0 | [📋 View Chat] |

**Row click:** Expands to show full transcript inline

**Filter tabs above table:** `[All] [Calls] [WhatsApp] [Web Chat]`

**Actions:**
- ▶ Play — audio recording (if available)
- 📋 Transcript — expand to read full conversation
- 🔄 Retry — re-queue call for "unreachable" or "voicemail" outcomes
- 📅 Schedule — quick follow-up scheduler

---

### 3.8 Footer Tabs (Row 5)

Clicking a tab expands its content below, replacing the tab content area. Only one tab open at a time.

#### Tab: 📋 All Conversations
- Full conversation search with filters (date range, channel, lead, keyword)
- Transcript viewer with copy-to-clipboard
- Audio playback for recorded calls
- Export button (CSV)

#### Tab: 📅 Follow-up Calendar
- 7-day calendar view (Mon–Sun)
- Each day shows count of scheduled follow-ups
- Click day → list of follow-ups with actions (Call Now, Mark Done, Reschedule)
- "Schedule New" button

#### Tab: 📊 Reports
- This week's stats (calls, leads, conversions, channel breakdown)
- Simple bar charts (drawn with CSS/SVG, no library needed)
- Export CSV for all data

#### Tab: ⚙️ Settings
- Tunnel URL display (read-only)
- Twilio phone number (read-only)
- Refresh interval toggle (5s / 10s / 30s / manual)
- Sound notifications on/off (new call, call completed)
- Theme toggle (dark/light) — v2

---

## 4. Technical Architecture

### 4.1 File Structure

```
app/
├── static/
│   ├── dashboard.html          ← THE COMMAND COCKPIT (single HTML file)
│   ├── dashboard.css           ← all styles (dark theme, cards, animations)
│   ├── dashboard.js            ← all JavaScript (fetch, render, SSE, polling)
│   ├── voice_client.html       ← existing (browser mic page)
│   └── quick_call.html         ← existing (simple call form, kept as standalone)
│
├── main.py                     ← add: GET /dashboard, SSE endpoints, batch call API
├── leads/
│   ├── models.py               ← add: batch call query, lead scoring query
│   └── service.py              ← add: score calculation, temperature classification
│
└── scripts/
    └── update_twilio_webhook.py ← existing (auto-update Twilio)
```

### 4.2 New / Modified API Endpoints

| Method | Endpoint | Purpose | New/Modify |
|--------|----------|---------|------------|
| `GET` | `/dashboard` | Serve the cockpit HTML page | **New** |
| `GET` | `/api/dashboard/summary` | Aggregated KPIs + recent activity in one call | **New** |
| `GET` | `/api/calls/live` | Active calls with transcript snippets (SSE or JSON) | **New** |
| `POST` | `/api/quick-call/batch` | Queue multiple calls at once | **New** |
| `GET` | `/api/quick-call/batch/{id}` | Poll batch call progress | **New** |
| `GET` | `/api/leads/{id}/score` | Lead score breakdown | **New** |
| `GET` | `/api/leads` | Add `?search=`, `?program=`, `?health=` filters | **Modify** |
| `GET` | `/api/conversations` | Add `?date_from=`, `?date_to=`, `?outcome=` filters | **Modify** |
| `GET` | `/api/stats` | Add `today_stats`, `health_distribution` to response | **Modify** |

### 4.3 JavaScript Architecture

```
dashboard.js
├── App State (single source of truth)
│   ├── state.leads          ← cached lead list
│   ├── state.activeCalls    ← live call objects
│   ├── state.stats          ← KPI numbers
│   ├── state.batchJobs      ← batch call progress
│   └── state.ui             ← selected tab, filters, sort
│
├── Data Layer
│   ├── fetchJSON(url)       ← generic API caller with error handling
│   ├── pollStats()          ← GET /api/dashboard/summary (every 10s)
│   ├── pollActiveCalls()    ← GET /api/calls/live (every 3s)
│   ├── pollBatchStatus(id)  ← GET /api/quick-call/batch/{id} (every 3s)
│   └── sseConnect()         ← SSE for real-time transcript streaming
│
├── Render Layer
│   ├── renderStatCards()    ← Row 1
│   ├── renderLiveCalls()    ← Row 2 left
│   ├── renderQuickCall()    ← Row 2 right
│   ├── renderPipeline()     ← Row 3
│   ├── renderActivity()     ← Row 4
│   └── renderFooterTab()    ← Row 5
│
├── Event Handlers
│   ├── onBatchCallSubmit()  ← parse numbers, POST batch, start polling
│   ├── onCallEnd(callSid)   ← POST to hang up
│   ├── onLeadClick(leadId)  ← open lead detail modal
│   └── onTabClick(tabName)  ← switch footer tab
│
└── Utilities
    ├── timeAgo(timestamp)   ← "2h ago", "Yesterday"
    ├── formatDuration(sec)  ← "4m 32s"
    ├── parseNumberList(str) ← parse multi-format phone number input
    ├── scoreToColor(n)      ← green/yellow/red for score
    └── tempToClass(lead)    ← hot/warm/cool/cold CSS class
```

### 4.4 CSS Architecture

```css
/* Design tokens (CSS custom properties) */
:root {
  --bg-primary: #0f172a;
  --bg-card: #1e293b;
  --bg-card-hover: #273449;
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --accent-blue: #3b82f6;
  --accent-green: #22c55e;
  --accent-yellow: #f59e0b;
  --accent-red: #ef4444;
  --border: #334155;
  --radius: 8px;
  --radius-lg: 16px;
  --shadow: 0 4px 24px rgba(0,0,0,0.35);
}

/* Layout */
.grid-2-col { ... }    /* Two-column layouts */
.grid-5-col { ... }    /* Five stat cards */
.grid-kanban { ... }   /* Horizontal scroll columns */

/* Components */
.stat-card { ... }       /* KPI cards */
.call-monitor { ... }    /* Live call panels */
.lead-card { ... }       /* Pipeline cards */
.activity-row { ... }    /* Table rows */
.batch-input { ... }     /* Quick call textarea */
.score-badge { ... }     /* ⭐8.2 badges */
.temp-badge { ... }      /* 🔥Hot 🟡Warm etc */
.modal-overlay { ... }   /* Lead detail modal */

/* Animations */
@keyframes pulse { ... }       /* Active call dot */
@keyframes fadeIn { ... }      /* New transcript lines */
@keyframes slideIn { ... }     /* Modal open */
@keyframes progress { ... }    /* Batch progress bar */

/* Responsive */
@media (max-width: 1200px) { ... }  /* Stack columns */
@media (max-width: 768px) { ... }   /* Single column, collapse cards */
```

---

## 5. Lead Detail Modal

When a counselor clicks any lead card or name, a modal overlay opens:

```
┌──────────────────────────────────────────────────────────┐
│  👤 JOHN SMITH                                   [✕ Close]│
│  ───────────────────────────────────────────────────────  │
│                                                          │
│  ┌── Left Column ──────────┐ ┌── Right Column ─────────┐ │
│  │                          │ │                          │ │
│  │ 📱 +1234567890           │ │ 🔥 LEAD HEALTH           │ │
│  │ 📧 john@email.com        │ │ ┌────────────────────┐   │ │
│  │ 🎓 MBA Program           │ │ │ ██████████████████  │   │ │
│  │ 📅 Created: Jul 28       │ │ │     Score: 8.2/10   │   │ │
│  │ 🟢 Status: In Progress   │ │ │ 🔥 Hot · Contacted  │   │ │
│  │ 📝 Source: WhatsApp      │ │ │      today           │   │ │
│  │ 🔄 2 call attempts       │ │ └────────────────────┘   │ │
│  │                          │ │                          │ │
│  │ 📝 NOTES                 │ │ 📊 SCORE BREAKDOWN       │ │
│  │ Interested in part-time  │ │ Recency:    9/10         │ │
│  │ MBA, asked about fees    │ │ Engagement: 8/10         │ │
│  │ and scholarship options  │ │ Response:   8/10         │ │
│  │                          │ │ Interest:   8/10         │ │
│  └──────────────────────────┘ └──────────────────────────┘ │
│                                                          │
│  ───────────────────────────────────────────────────────  │
│  💬 CONVERSATIONS (3)                                    │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Aug 1, 10:23 AM  📤 Outbound Call  4m 32s  Interested│ │
│  │ AI: "Hi John, I'm calling from admissions..."         │ │
│  │ John: "Yes, I'm interested in the MBA program..."     │ │
│  │ AI: "The MBA program starts in September..."          │ │
│  │ [▶ Play Recording]  [📋 Copy Transcript]              │ │
│  ├──────────────────────────────────────────────────────┤ │
│  │ Jul 30, 2:15 PM  💬 WhatsApp                         │ │
│  │ John: "What are the MBA tuition fees?"                │ │
│  │ AI: "UMD MBA tuition is $45,000..."                   │ │
│  │ [📋 Copy Transcript]                                  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                          │
│  ───────────────────────────────────────────────────────  │
│  ⚡ ACTIONS                                              │
│  [📞 Call Now]  [📱 Send WhatsApp]  [📅 Schedule Follow-up] │
│  [✏️ Edit Lead]  [📝 Add Note]      [🔄 Change Status]    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. Implementation Plan

### Phase 1: Core Page + APIs (4 hours)

| Step | Task | Files |
|------|------|-------|
| 1.1 | Create `app/static/dashboard.html` with full layout (CSS Grid) | New file |
| 1.2 | Create `app/static/dashboard.css` with design tokens and all component styles | New file |
| 1.3 | Create `app/static/dashboard.js` with state management and render functions | New file |
| 1.4 | Add `GET /dashboard` endpoint in `main.py` to serve the HTML | Modify |
| 1.5 | Add `GET /api/dashboard/summary` — aggregated KPIs in one call | Modify |
| 1.6 | Wire stat cards and activity table to live API data | JS |

### Phase 2: Batch Call + Live Monitor (4 hours)

| Step | Task | Files |
|------|------|-------|
| 2.1 | Add `POST /api/quick-call/batch` endpoint | `main.py` |
| 2.2 | Add `GET /api/quick-call/batch/{id}` progress endpoint | `main.py` |
| 2.3 | Build batch dialer UI with number parsing (CSV, tabs, commas) | `dashboard.js` + HTML |
| 2.4 | Add `GET /api/calls/live` SSE endpoint | `main.py` |
| 2.5 | Build live call monitor with transcript streaming | `dashboard.js` |
| 2.6 | Add call duration timer and "End Call" button | `dashboard.js` |

### Phase 3: Pipeline + Scoring (3 hours)

| Step | Task | Files |
|------|------|-------|
| 3.1 | Build Kanban pipeline board with CSS columns | `dashboard.html` + CSS |
| 3.2 | Add lead scoring calculation in `app/leads/service.py` | Modify |
| 3.3 | Add `GET /api/leads/{id}/score` endpoint | `main.py` |
| 3.4 | Add hot/warm/cool/cold classification logic | `service.py` |
| 3.5 | Build lead card component with score + temp badges | `dashboard.js` |
| 3.6 | Build lead detail modal | `dashboard.js` + HTML |

### Phase 4: Footer Tabs + Polish (3 hours)

| Step | Task | Files |
|------|------|-------|
| 4.1 | Build Conversations tab with search and transcript viewer | JS + HTML |
| 4.2 | Build Calendar tab with 7-day view | JS + HTML |
| 4.3 | Build Reports tab with summary stats | JS + HTML |
| 4.4 | Add Settings tab | JS + HTML |
| 4.5 | Add responsive breakpoints | CSS |
| 4.6 | Add loading states, error handling, empty states | JS |
| 4.7 | Cross-browser testing + polish animations | CSS + JS |

**Total: ~14 hours**

---

## 7. Empty States & Error Handling

Every section must handle three states:

| State | Behavior |
|-------|----------|
| **Loading** | Skeleton placeholder (gray pulsing rectangle) while data fetches |
| **Empty** | Friendly message with icon: "No active calls right now" / "No leads yet — add your first lead above" |
| **Error** | Red banner: "Could not load data. [Retry]" — never crash the page |

---

## 8. Design Tokens (Matching Existing Style)

```css
:root {
  /* Backgrounds */
  --bg-body: #0f172a;          /* Main page background */
  --bg-card: #1e293b;          /* Card / panel background */
  --bg-card-hover: #273449;    /* Card hover state */
  --bg-input: #0f172a;         /* Input field background */
  --bg-modal: rgba(0,0,0,0.6); /* Modal overlay */

  /* Text */
  --text-primary: #e2e8f0;     /* Headings, important text */
  --text-secondary: #94a3b8;   /* Labels, descriptions */
  --text-muted: #64748b;       /* Timestamps, minor info */

  /* Accent Colors */
  --blue: #3b82f6;             /* Primary actions, links, info */
  --blue-hover: #2563eb;
  --green: #22c55e;            /* Success, completed, hot leads */
  --yellow: #f59e0b;           /* Warnings, pending, warm leads */
  --red: #ef4444;              /* Errors, failed, cold leads */
  --teal: #14b8a6;             /* Active, in-progress */
  --gray: #64748b;             /* Neutral, unknown */

  /* Borders & Shadows */
  --border: #334155;
  --border-focus: #3b82f6;
  --radius-sm: 6px;
  --radius: 8px;
  --radius-lg: 16px;
  --shadow: 0 4px 24px rgba(0,0,0,0.35);

  /* Typography */
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen,
           Ubuntu, Cantarell, "Helvetica Neue", Arial, sans-serif;
  --font-mono: "Cascadia Code", "Fira Code", "Consolas", monospace;

  /* Spacing */
  --space-xs: 0.25rem;
  --space-sm: 0.5rem;
  --space: 1rem;
  --space-lg: 1.5rem;
  --space-xl: 2rem;
}
```

---

## 9. Refresh Strategy

| Data | Refresh Method | Interval |
|------|---------------|----------|
| KPI Stats | Poll `GET /api/dashboard/summary` | 10 seconds |
| Active Calls | Poll `GET /api/calls/live` | 3 seconds |
| Live Transcript | SSE `GET /api/calls/live?stream=true` | Real-time push |
| Pipeline Board | Poll `GET /api/leads` | 30 seconds |
| Recent Activity | Poll `GET /api/conversations?limit=20` | 30 seconds |
| Batch Call Status | Poll `GET /api/quick-call/batch/{id}` | 3 seconds (only when active) |
| Footer Tabs | Fetch on tab open | On demand |

All polling stops when the browser tab is inactive (`document.hidden`). Resumes on focus.

---

## 10. Browser Support

| Browser | Support |
|---------|---------|
| Chrome 90+ | ✅ Primary target |
| Edge 90+ | ✅ |
| Firefox 90+ | ✅ |
| Safari 15+ | ✅ (macOS only for counselor) |
| Mobile Safari/Chrome | ✅ Responsive, single-column layout |

**No external JS libraries required.** Pure vanilla JavaScript with `fetch()`, `EventSource` (SSE), and `DOM` APIs. Zero dependencies. The page loads fast even on slow connections.

---

## 11. Related Documents

- `doc/ENHANCEMENT_ROADMAP.md` — Technical system enhancements
- `doc/UX_ENHANCEMENT_ROADMAP.md` — Counselor UX enhancements for Streamlit dashboard
- `doc/COMMAND_COCKPIT_DASHBOARD.md` — This document (the single HTML dashboard spec)
- `DEMO_GUIDE.md` — Demo runbook
- `start_services.ps1` — One-shot launcher
