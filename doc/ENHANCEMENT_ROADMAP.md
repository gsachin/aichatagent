# Enhancement Roadmap — University Admissions Voice Assistant

**Document version:** 1.0
**Last updated:** 2026-08-01
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`

---

## How to Use This Document

Each enhancement is tagged with:

| Tag | Meaning |
|-----|---------|
| 🔴 **Critical** | System is broken or silently failing — fix before next demo |
| 🟡 **Quick Win** | High demo impact, low effort (< 2 hours) |
| 🟢 **Medium** | Worthwhile but needs 2–6 hours |
| 🔵 **Nice-to-Have** | Improves architecture but not demo-blocking |

Priority is a rough ordering within each tier (lower number = do first).

---

## 🔴 Critical — Fix Before Next Demo

### CRIT-1: Fix `transcript_parts` — Call Transcripts & Lead Extraction Are Dead

**File:** `app/main.py` (lines ~386–425 for inbound, ~502–543 for outbound)
**Effort:** 30 minutes

**Problem:**
`transcript_parts: list[str] = []` is declared in both `/ws/twilio` and `/ws/twilio-outbound` handlers but is **never appended to**. The STT result is obtained inside `VoiceCallSession.process_utterance()` but never returned to the WebSocket handler. This means:

- `_handle_disconnect()` always receives an empty string → skips lead extraction entirely
- `post_call_handler()` (LLM lead data extraction) never runs
- Conversations table never stores call transcripts
- Follow-up auto-detection never triggers
- All supporting code in `app/pipeline.py`, `app/leads/service.py`, and `app/leads/models.py` exists but is dead

**Fix:**
1. Modify `VoiceCallSession.process_utterance()` to return both the TTS chunks AND the transcript text
2. In the WebSocket handler, append the transcript to `transcript_parts` after each utterance

**Files affected:**
- `app/voice_handler.py` — change return type of `process_utterance()`
- `app/main.py` — append transcript in both WebSocket handlers

**Verification:** After a test call, check that the `conversations` table contains a row with a non-empty transcript.

---

### CRIT-2: `TUNNEL_HOST` Env Var Leakage Across Restarts

**File:** `start_services.ps1`, `app/main.py`
**Effort:** Already fixed in `start_services.ps1` (step 1 clears env var). Needs the same fix in `app/main.py`.

**Problem:**
`_resolve_tunnel_host()` checks `TUNNEL_HOST` env var before reading `.whatsapp_tunnel` file. On the second run in the same PowerShell session, the **stale env var** from the first run leaks into the new server process. The server ignores the updated file and serves the old dead tunnel URL in TwiML.

**Fix for `app/main.py`:**
Remove the env-var-first priority. Always read `.whatsapp_tunnel` file first, fall back to env var only if file is missing:
```python
def _resolve_tunnel_host() -> str:
    tunnel_file = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
    if tunnel_file.is_file():
        host = tunnel_file.read_text().strip()
        if host:
            return host
    return os.environ.get("TUNNEL_HOST", os.environ.get("NGROK_HOST", "localhost:8000"))
```

**Files affected:**
- `app/main.py` — reorder `_resolve_tunnel_host()` priority
- `app/outbound/caller.py` — same fix in `_resolve_host()`

---

### CRIT-3: Add `<Say>` Fallback After `<Connect>` in TwiML

**File:** `app/main.py` (TWIML_TEMPLATE), `app/outbound/twiml.py`
**Effort:** 5 minutes

**Problem:**
Both TwiML templates end immediately after `<Connect>`. If the WebSocket stream fails for any reason (tunnel dies mid-call, DTMF issue, network hiccup), the `<Connect>` ends and the call drops **with dead silence**. The caller hears nothing and assumes the system is broken.

**Fix:**
Add a `<Say>` after `<Connect>` so the caller hears a graceful message:
```xml
<Response>
    <Connect>
        <Stream url="wss://{host}/ws/twilio" />
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back or try our WhatsApp channel.</Say>
</Response>
```

**Files affected:**
- `app/main.py` — `TWIML_TEMPLATE` (line 187)
- `app/outbound/twiml.py` — `outbound_connect_twiml()` (line 20)

---

## 🟡 Quick Wins — High Demo Impact, Low Effort

### QUICK-1: Landing Page at `/` (Replace JSON)

**File:** `app/main.py` (health_check endpoint), new `app/static/index.html`
**Effort:** 1 hour

**Problem:**
Visiting the tunnel URL (`https://xxx.trycloudflare.com/`) shows raw JSON. This is the first thing anyone sees — it looks unfinished and unprofessional.

**Fix:**
Serve a styled HTML landing page at `/` with:
- 🎓 Title: "University Admissions Voice Assistant"
- 📞 Big "Call Us: +19788198953" (prominent, copyable)
- 🔗 Links to: Voice Client (`/voice`), Quick Call (`/call`), Dashboard (localhost:8502)
- 📱 WhatsApp info with join instructions
- 🟢 Live status indicator (tunnel health, server status, DB connection)
- Dark theme matching `voice_client.html` and `quick_call.html`

**Files affected:**
- New: `app/static/index.html`
- `app/main.py` — modify `health_check()` to return HTML when `Accept: text/html`

---

### QUICK-2: IVR Voice Menu — `<Gather>` Before AI Connection

**File:** `app/main.py` (TWIML_TEMPLATE, new endpoint or modify `/twilio/voice`)
**Effort:** 1.5 hours

**Problem:**
Inbound calls connect directly to the AI stream. No menu, no context — the caller just hears "Hi, I'm the admissions assistant" with no guidance on what the system can do.

**Fix:**
Add a `<Gather>` menu before connecting to the AI:
- "Press 1 to hear about UMD programs"
- "Press 2 to hear about FDU programs"  
- "Press 3 for tuition and fees information"
- "Press 4 to speak with the AI assistant"
- "Or just start speaking to ask anything"

Each option plays a pre-recorded or TTS summary, then connects to the AI for follow-up questions.

**Flow:**
```
Inbound call → /twilio/voice
  → TwiML with <Gather numDigits="1" timeout="3">
    → If digit pressed: <Say> pre-recorded info → then <Connect> to AI
    → If no digit / speech: <Connect> directly to AI
```

**Files affected:**
- `app/main.py` — modify `twilio_voice_webhook()` to return a two-phase TwiML

---

### QUICK-3: Auto-Update WhatsApp Sandbox Webhook

**File:** `scripts/update_twilio_webhook.py`, `start_services.ps1`
**Effort:** 30 minutes

**Problem:**
`start_services.ps1` updates the **voice** webhook automatically via the Twilio API, but the **WhatsApp sandbox** webhook (`when a message comes in`) must still be manually configured in the Twilio Console every time the tunnel URL changes.

**Fix:**
Extend `scripts/update_twilio_webhook.py` to also update the WhatsApp sandbox webhook URL via the Twilio API (`client.messaging.v1.services` or sandbox update endpoint).

**Files affected:**
- `scripts/update_twilio_webhook.py` — add WhatsApp sandbox update
- `start_services.ps1` — already calls this script in step 6

---

### QUICK-4: Wire the Phone Search on Dashboard Leads Page

**File:** `app/dashboard/leads_page.py`
**Effort:** 20 minutes

**Problem:**
The Leads page has a phone search input field, but it's not wired to the API. Only the status filter works. Adding `?phone=xxx` to the `/api/leads` endpoint would make it functional.

**Fix:**
- Add `phone` query parameter to `GET /api/leads`
- Wire the search input in `leads_page.py` to pass it to `api_call()`

**Files affected:**
- `app/main.py` — add phone filter to leads endpoint
- `app/leads/models.py` — add phone search to SQL query
- `app/dashboard/leads_page.py` — wire the input field

---

### QUICK-5: Add `/api/follow-ups` List Endpoint

**File:** `app/main.py`, `app/leads/models.py`
**Effort:** 30 minutes

**Problem:**
The Dashboard Scheduler page has a comment: "there is no /api/follow-ups list endpoint." It works around this by scanning `leads.next_follow_up` instead of the `follow_ups` table, which is inaccurate.

**Fix:**
Add `GET /api/follow-ups` that returns rows from the `follow_ups` table, with optional filters (status, lead_id, date range).

**Files affected:**
- `app/main.py` — add endpoint
- `app/leads/models.py` — add `get_follow_ups()` query
- `app/dashboard/scheduler_page.py` — use the new endpoint

---

### QUICK-6: Fix "Calls Today" Dashboard Metric

**File:** `app/leads/models.py` (`get_lead_stats`), `app/dashboard/overview.py`
**Effort:** 15 minutes

**Problem:**
The "Calls Today" KPI card counts all conversations created today (any channel — WhatsApp, Streamlit, etc.), not actual phone calls. The label is misleading.

**Fix:**
Filter the count to only `channel IN ('inbound_call', 'outbound_call')`.

**Files affected:**
- `app/leads/models.py` — update `get_lead_stats()` SQL

---

### QUICK-7: Personalized AI Greeting for Outbound Calls

**File:** `app/voice_handler.py` (`generate_ulaw_greeting`), `app/main.py`
**Effort:** 1 hour

**Problem:**
Both inbound and outbound calls use the same generic greeting: *"Hi, I'm the admissions assistant. Ask me anything about UMD or FDU programs, tuition fees, or how to apply."* For outbound calls, the AI knows the lead's name and program interest — it should use them.

**Fix:**
For outbound calls, pass the lead's name and program interest to the greeting generator:
- "Hi {{name}}, I'm calling from the University Admissions office. I see you're interested in {{program}}. How can I help you today?"

**Files affected:**
- `app/main.py` — pass lead context to greeting in outbound WS handler
- `app/voice_handler.py` — accept optional name/program params in `generate_ulaw_greeting()`

---

## 🟢 Medium Effort — Build for Next Demo Cycle

### MED-1: Live Call Transcript Streaming (SSE)

**Effort:** 3 hours

**Description:**
Stream the STT transcript to the dashboard in real-time during active calls via Server-Sent Events. The dashboard shows text appearing word-by-word as the caller speaks.

**Approach:**
1. Add an SSE endpoint (`GET /api/calls/live`) that streams transcript updates
2. When `process_utterance()` returns a transcript, push it to an in-memory queue
3. Dashboard page shows a "Live Calls" panel that subscribes to the SSE stream

**Files affected:**
- `app/main.py` — add SSE endpoint + transcript queue
- `app/dashboard/` — new live-calls component
- `app/voice_handler.py` — publish transcripts to queue

---

### MED-2: Call Recording Playback

**Effort:** 1.5 hours

**Description:**
Save the combined µ-law audio from each call to disk, and add an audio player to the Conversations page in the dashboard so you can play back actual calls.

**Approach:**
1. In the WebSocket handlers, accumulate all received and sent audio chunks
2. On disconnect, save as a WAV/MP3 file to `app/static/recordings/`
3. Store the filename in the `conversations` table
4. Add `<audio>` player in the dashboard conversations page

**Files affected:**
- `app/main.py` — accumulate audio, save on disconnect
- `app/leads/schema.py` — add `recording_url` column to conversations
- `app/dashboard/conversations_page.py` — add audio player

---

### MED-3: Call Sentiment Analysis

**Effort:** 2 hours

**Description:**
Run a lightweight sentiment analysis on each call transcript and color-code conversations (green = positive, yellow = neutral, red = negative) in the dashboard.

**Approach:**
1. After call ends, send transcript to Ollama with a sentiment prompt
2. Store sentiment label + score in the conversations table
3. Display color badges in the dashboard

**Files affected:**
- `app/leads/schema.py` — add sentiment columns
- `app/leads/service.py` — add sentiment analysis to `handle_post_interaction()`
- `app/dashboard/conversations_page.py` — display sentiment badges

---

### MED-4: Demo Data Reset Button

**Effort:** 1.5 hours

**Description:**
A single button in the dashboard that wipes all test data, re-adds sample leads, clears the call queue, and resets counters — so you can start fresh for each demo without manual DB cleanup.

**Approach:**
1. Add `POST /api/demo/reset` endpoint
2. Truncate all tables (preserving schema)
3. Insert 3–5 sample leads with realistic names, phone numbers, and program interests
4. Show a "Demo Ready" toast in the dashboard

**Files affected:**
- `app/main.py` — add reset endpoint
- `app/dashboard/overview.py` — add reset button

---

### MED-5: Unify the Two Streamlit Apps

**Effort:** 3 hours

**Description:**
Merge `app.py` (chat UI, port 8501) and `dashboard.py` (admin, port 8502) into a single multi-page Streamlit app with consistent theming and a shared sidebar for navigation.

**Approach:**
1. Create `app/streamlit_app.py` as the unified entry point
2. Move `app.py` into `app/pages/chat.py`
3. Move `dashboard.py` into `app/pages/dashboard.py`
4. Add a sidebar nav that switches between Chat, Dashboard, Leads, Conversations, Scheduler
5. Update `start_services.ps1` to launch one Streamlit app instead of two

**Files affected:**
- New: `app/streamlit_app.py` (entry point)
- `app/pages/` — reorganized page modules
- `start_services.ps1` — single Streamlit launch

---

### MED-6: RAG Quality — Structured Comparison Data

**Effort:** 3 hours

**Description:**
Fix the known RAG issue where "Compare UMD and FDU" returns useless results, and UMD tuition queries can return FDU data. Add structured comparison data and improve chunking.

**Approach:**
1. Create a structured JSON comparison document (UMD vs FDU side-by-side)
2. Add it to the ChromaDB ingestion alongside the existing PDF
3. Add query re-writing for comparison questions ("compare" → structured lookup)
4. Test with: "Compare UMD and FDU tuition", "Which is cheaper?", "Which has better MBA?"

**Files affected:**
- `content/sample_data/` — add comparison JSON
- `app/rag.py` — add structured comparison retrieval
- `app/pipeline.py` — add query re-writing

---

## 🔵 Nice-to-Have — Architecture & Polish

### NICE-1: Consolidate Tunnel Host Resolution

**Problem:** Three duplicate functions resolve the tunnel host:
- `_resolve_tunnel_host()` in `app/main.py` (defined twice — lines 65 and 166)
- `_resolve_host()` in `app/outbound/caller.py`
- `get_tunnel_host()` in `quick_call.py`

**Fix:** Create a single `app/tunnel.py` module with `get_tunnel_host()` imported everywhere.

**Files affected:**
- New: `app/tunnel.py`
- `app/main.py` — delete duplicates, import from tunnel
- `app/outbound/caller.py` — import from tunnel
- `quick_call.py` — import from tunnel

---

### NICE-2: Wire `/ws/voice` to the AI Pipeline

**Problem:** The browser microphone page (`/voice`) is explicitly an "echo for now — pipeline wiring pending GPU." The most visually accessible demo page does nothing intelligent.

**Fix:** Connect `/ws/voice` to the same `VoiceCallSession` pipeline used by Twilio calls, or clearly label the page as a transport test with a link to working alternatives.

**Files affected:**
- `app/main.py` — modify `websocket_voice()` handler

---

### NICE-3: WebSocket Health Check in PS1 Script

**Problem:** The PS1 script checks HTTP health (port 8000 responds 200) but doesn't verify the WebSocket endpoint works. A tunnel can be alive but WebSocket broken.

**Fix:** Add a quick WebSocket connection test (using a Python one-liner) after the tunnel starts.

**Files affected:**
- `start_services.ps1` — add WS check in step 4

---

### NICE-4: Consider ngrok as Tunnel Alternative

**Problem:** Cloudflared keeps dying with DNS resolution errors on this Windows machine. Multiple exits with code 127 observed.

**Fix:** Add ngrok support as an alternative tunnel provider. ngrok is generally more stable on Windows and has a free tier.

**Files affected:**
- `start_services.ps1` — add `-UseNgrok` switch
- `app/main.py` — already supports `NGROK_HOST` env var (legacy)

---

### NICE-5: Remove Stale Artifacts

**Cleanup list:**
- Delete duplicate `_resolve_tunnel_host()` at line 65 of `app/main.py`
- Remove `.wav` files from `app/static/audio/` (WhatsApp now uses MP3)
- Archive `admissions_bot.py` (legacy CLI, superseded by Streamlit + quick_call)
- Archive stale docs in `doc/` that overlap or contradict current functionality

---

### NICE-6: Database Migration Instead of Idempotent DDL

**Problem:** `app/leads/schema.py` uses idempotent `CREATE TABLE IF NOT EXISTS` for schema management. Adding columns or changing types requires manual ALTER statements or table drops.

**Fix:** Use Alembic or a simple migration runner for schema changes.

---

### NICE-7: MCP Protocol Compliance

**Problem:** The MCP server (`app/mcp/server.py`) is a REST stub, not a standards-compliant MCP implementation. The SSE endpoint emits the tool list once then keepalives — no real protocol.

**Fix:** Either implement proper MCP protocol (sessions, negotiation, streaming) or remove the MCP endpoints and document the tools as a REST API.

---

## Enhancement Priority Summary

| Order | ID | What | Effort | Demo Impact |
|-------|-----|------|--------|-------------|
| **1** | CRIT-1 | Fix `transcript_parts` — save transcripts | 30 min | Unlocks entire post-call pipeline |
| **2** | CRIT-2 | Fix `_resolve_tunnel_host()` file-first priority | 10 min | Prevents URL mismatch on restart |
| **3** | CRIT-3 | Add `<Say>` fallback after `<Connect>` | 5 min | No more silent disconnections |
| **4** | QUICK-1 | Landing page at `/` | 1 hr | Professional first impression |
| **5** | QUICK-2 | IVR voice menu | 1.5 hr | Polished caller experience |
| **6** | QUICK-3 | Auto-update WhatsApp webhook | 30 min | No manual Twilio Console steps |
| **7** | QUICK-7 | Personalized outbound greeting | 1 hr | Shows lead-aware intelligence |
| **8** | QUICK-4 | Wire phone search on dashboard | 20 min | Fix dead UI |
| **9** | QUICK-5 | Add `/api/follow-ups` endpoint | 30 min | Fix broken scheduler page |
| **10** | MED-1 | Live call transcript streaming | 3 hr | Killer demo feature |
| **11** | MED-2 | Call recording playback | 1.5 hr | Let audience hear real calls |
| **12** | MED-4 | Demo data reset button | 1.5 hr | Clean slate for each demo |
| **13–18** | *Remaining* | See sections above | Varies | Progressive polish |

---

## Related Documents

- `DEMO_GUIDE.md` — How to run the demo
- `doc/PENDING_IMPROVEMENTS.md` — Older improvement notes
- `doc/WHATSAPP_INTEGRATION_GUIDE.md` — WhatsApp setup
- `doc/manual_testing_guide.md` — Test procedures
- `start_services.ps1` — One-shot launcher script
- `scripts/update_twilio_webhook.py` — Twilio API helper
