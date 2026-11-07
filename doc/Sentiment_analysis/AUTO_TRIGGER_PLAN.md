# Sentiment Analysis Auto-Trigger — Implementation Plan

**Created:** 2026-08-10  
**Status:** Implemented

---

## Goal

After every conversation ends, sentiment analysis is automatically triggered
and persisted to the database, then reflected on the Command Cockpit dashboard.

---

## Conversation Channels & Lifecycle Analysis

| Channel | Entry Point | End-of-Conversation Signal | Current Sentiment Hook |
|---------|------------|---------------------------|----------------------|
| **Streamlit text** | `app.py` : `st.chat_input` | Each exchange (user+assistant pair) — no explicit "end" | ❌ **Missing** — conversations not logged at all |
| **Streamlit voice** | `app.py` : `st.audio_input` | After each transcribed utterance+RAG response | ❌ **Missing** — conversations not logged at all |
| **Twilio inbound call** | `main.py` : `/ws/twilio` → `_handle_disconnect` | WebSocket disconnect | ⚠️ **Partial** — scores but `lead_id=""` (not linked to lead) |
| **Twilio outbound call** | `main.py` : `/ws/twilio-outbound` → `_handle_disconnect` | WebSocket disconnect | ⚠️ **Partial** — same as inbound |
| **WhatsApp text** | `main.py` : `/twilio/whatsapp` → `_log_whatsapp_conversation` → `log_interaction` | Each message exchange | ✅ **Working** — `log_interaction` already calls `score_transcript(lead_id=...)` |
| **WhatsApp voice note** | `main.py` : `_process_voice_note_async` → `log_interaction` | After transcription+RAG+reply | ✅ **Working** — same path as WhatsApp text |

---

## Changes Required

### 1. Streamlit `app.py` — Log conversations + trigger sentiment

**Problem:** Streamlit chat has no backend logging at all. Messages only exist in
`st.session_state.messages` (in-memory, lost on browser close).

**Fix:**
- After each assistant response (both text and voice paths), call a new
  `POST /api/conversations/log` endpoint that:
  1. Upserts the lead by phone (from session state)
  2. Logs the user+assistant exchange as a conversation
  3. Triggers sentiment scoring on the exchange transcript
- This makes Streamlit conversations visible in the dashboard
  and feeds the sentiment pipeline.

### 2. `_handle_disconnect` — Link sentiment to lead

**Problem:** Voice call transcripts are scored but with `lead_id=""`.
The score is dry-run only — not persisted per-lead.

**Fix:**
- After a voice call, try to extract the phone number from the lead database
  using any phone/name/email info extracted from the transcript.
- If a lead match is found, pass `lead_id` to `score_transcript`.
- Already partially done in `log_interaction` — just needs the phone number
  to be available at disconnect time.

### 3. Dashboard — Already reflects sentiment

**What already works:**
- Dashboard Overview shows sentiment KPI row (Hot/Warm/etc counts)
- Dashboard Leads page shows sentiment metrics per lead
- Both pull from the API which queries the database

**Auto-refresh:**
- The dashboard pages call API on every Streamlit rerun
- User clicks "Refresh" or navigates between pages

---

## Implementation Checklist

- [x] `app/sentiment/scorer.py` — Scoring engine (already done)
- [x] `app/sentiment/categorizer.py` — Category logic (already done)
- [x] `app/sentiment/models.py` — DB CRUD (already done)
- [x] `app/sentiment/schema.py` — Table schema (already done)
- [x] `app/main.py` — API endpoints (already done)
- [x] `app/leads/service.py` — `log_interaction` auto-scores (already done)
- [x] `app/main.py` — `_handle_disconnect` scores (already done)
- [ ] `app.py` (Streamlit) — Log conversations + trigger sentiment ← **THIS CHANGE**
- [ ] `app/main.py` — `_handle_disconnect` resolve lead_id from phone ← **THIS CHANGE**
- [ ] `app/dashboard/overview.py` — Sentiment KPI row (already done)
- [ ] `app/dashboard/leads_page.py` — Sentiment per lead (already done)

---

## Data Flow (after fix)

```
Streamlit chat
  ↓ (after each assistant response)
POST /api/conversations/log {phone_number, transcript, channel: "streamlit"}
  ↓
log_interaction() → upsert lead → log conversation → score_transcript(lead_id)
  ↓
sentiment_scores table + leads.current_category updated
  ↓
Dashboard GET /api/dashboard/summary + GET /api/leads/{id}/sentiment
  ↓
KPI row & lead cards updated on next refresh
```

```
Voice call (Twilio inbound/outbound)
  ↓ (WebSocket disconnect)
_handle_disconnect(transcript_parts)
  ↓
extract phone → resolve lead_id → score_transcript(lead_id)
  ↓
sentiment_scores table updated
  ↓
Dashboard reflects on refresh
```
