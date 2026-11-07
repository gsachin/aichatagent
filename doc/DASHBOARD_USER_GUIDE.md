# Command Cockpit Dashboard — User Guide

**Dashboard URL:** `https://<tunnel>/dashboard`
**Current Link:** https://const-leaves-contest-legend.trycloudflare.com/dashboard

---

## Quick Start

1. Open the dashboard URL in any modern browser (Chrome, Edge, Firefox)
2. The page auto-loads all data — no login needed
3. Data refreshes automatically every 10–30 seconds

---

## Dashboard Layout

```
┌─────────────────────────────────────────────┐
│  🎓 Admissions Command Cockpit   🟢 System  │  ← Top bar
├─────────────────────────────────────────────┤
│  [📞 Active] [🆕 New] [⏰ Due] [🔥 Hot] [📊]  │  ← Row 1: Stat cards
├──────────────────────┬──────────────────────┤
│  📞 Live Call Monitor │  ⚡ Quick Call       │  ← Row 2: Live + Actions
├──────────────────────┴──────────────────────┤
│  📋 Lead Pipeline (Kanban Board)            │  ← Row 3: Pipeline
├─────────────────────────────────────────────┤
│  📜 Recent Activity (Last 24 Hours)          │  ← Row 4: Activity table
├─────────────────────────────────────────────┤
│  [Conversations] [Calendar] [Reports] [⚙️]   │  ← Row 5: Tabs
└─────────────────────────────────────────────┘
```

---

## Row 1: Stat Cards

Shows at-a-glance numbers. Click any card to jump to that section.

| Card | What It Shows |
|------|--------------|
| 📞 **Active Calls** | Calls currently ringing or in-progress. Green pulse dot when active |
| 🆕 **New Leads** | Leads with "pending" status created today |
| ⏰ **Due Follow-ups** | Leads with a follow-up scheduled for today |
| 🔥 **Hot Leads** | Leads with high engagement (contacted recently, interested) |
| 📊 **Total Pipeline** | All leads not marked as closed/failed |

---

## Row 2 Left: Live Call Monitor

**When calls are active:**
- Shows each call as a card with caller name, phone, direction (📞 inbound / 📤 outbound)
- Live transcript appears in real-time as the conversation happens
- Duration timer counts up
- "End Call" button to hang up

**When no calls:** Shows "No active calls right now" — normal state.

---

## Row 2 Right: Quick Call — Batch Dialer

**Enter phone numbers and the AI calls them automatically.**

### How to use:

**Single call:**
1. Type one phone number in the text box (e.g., `+12025551234`)
2. Fill in Name and Program (optional)
3. Click **📞 Call One**

**Batch calls:**
1. Paste multiple numbers (one per line, or comma-separated)
2. Examples:
   ```
   +12025551001 John Smith
   +12025551002 Jane Doe
   +12025551003
   ```
3. Click **📞 Call All Now**
4. Watch the progress bar — calls complete one by one
5. Results appear showing each call's outcome

**CSV/Excel paste works too:**
```
+12025551001, John Smith, MBA
+12025551002, Jane Doe, Computer Science
```

---

## Row 3: Lead Pipeline (Kanban Board)

Leads organized by status in 5 columns:

| Column | Status | Meaning |
|--------|--------|---------|
| 🆕 **New** | pending, created today | Not yet contacted |
| 💬 **Active** | in_progress | In conversation |
| ✅ **Completed** | completed | Enrolled or information given |
| ❌ **Not Interested** | failed | Said no or not a fit |
| ⚫ **Unreachable** | unreachable | No answer after multiple attempts |

**Click any lead card** to open the detail modal showing:
- Full contact info (phone, email, program, source)
- Status and call history
- All conversations with transcripts
- Action buttons: 📞 Call Now, 📝 Notes

---

## Row 4: Recent Activity

Shows all calls and chats from the last 24 hours.

| Column | Meaning |
|--------|---------|
| Time | When it happened ("2h ago", "Yesterday") |
| Type | 📞 Inbound / 📤 Outbound / 💬 WhatsApp / 🌐 Web |
| Lead | Phone number |
| Outcome | interested, not_interested, info_given, voicemail |
| Duration | Call length in minutes:seconds |
| Actions | 📋 View full transcript |

---

## Row 5: Footer Tabs

### 📋 All Conversations
- Search conversations by keyword or phone number
- Filter by channel (Inbound Calls, Outbound Calls, WhatsApp, Web Chat)
- Each conversation shows the full transcript
- Scroll through history

### 📅 Follow-up Calendar
- Shows leads with upcoming follow-ups
- Each entry shows name, phone, scheduled time, and status
- Quick actions: 📞 Call Now, ✅ Mark Complete

### 📊 Reports
- Summary stats (calls today, leads added, conversions)
- Reserved for future charts

### ⚙️ Settings
- Displays current configuration:
  - **Tunnel URL** — the public URL Twilio uses
  - **Twilio Phone** — +19788198953
  - **Database** — Connected / Unavailable
  - **Outbound Worker** — Active / Inactive

---

## Lead Detail Modal

Click any lead card in the pipeline to open:

1. **Contact info** — name, phone, email, program, status, source, call attempts
2. **Notes** — counselor notes about this lead
3. **Conversations** — every call/chat with this lead, with full transcripts
4. **Actions** — 📞 Call Now (queues an outbound call)

Click outside the modal or the ✕ button to close.

---

## Real-Time Behavior

| Data | Refresh Rate | How |
|------|-------------|-----|
| Stat cards | Every 10 seconds | Automatic polling |
| Pipeline board | Every 30 seconds | Automatic polling |
| Recent activity | Every 30 seconds | Automatic polling |
| Active calls | Every 5 seconds | Automatic polling |
| Conversations tab | Every 30 seconds | Automatic polling |

The page **pauses all polling when the browser tab is inactive** (saves bandwidth). Resumes when you switch back.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| **Dashboard shows "No data"** | Run `python scripts/seed_demo_data.py` to populate demo data |
| **Pipeline is empty** | Leads need to exist in the database — seed data or make calls |
| **Conversations show 0** | Conversations are created when calls complete. Make a test call |
| **"Database unavailable"** | PostgreSQL Docker container is not running. Run `docker start elearning-postgres` |
| **404 on /dashboard** | Server needs restart with latest code. Run `.\start_services.ps1` |
| **CSS/JS not loading** | Hard refresh browser (Ctrl+Shift+R) |

---

## Seeding Demo Data

If the dashboard looks empty, seed it with realistic test data:

```powershell
python scripts/seed_demo_data.py
```

This creates:
- 12 leads across all statuses (new, active, completed, failed, unreachable)
- 8 realistic conversations (inbound calls, outbound calls, WhatsApp chats)
- 5 upcoming follow-ups

Re-run anytime to reset to fresh demo data.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Esc` | Close any open modal |
| `Ctrl+F` | Focus search in Conversations tab |
