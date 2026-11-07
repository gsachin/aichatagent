# Command Cockpit Dashboard — Dependency Analysis & Implementation Plan

**Document version:** 1.0
**Date:** 2026-08-02
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`

---

## Executive Summary

The Command Cockpit Dashboard **can be built standalone** — most of its 5 rows depend only on existing APIs. However, **2 backend blockers must be fixed first** for full functionality.

**Recommended approach:** Fix the 2 blockers (45 min), then build the dashboard in 4 phases. You'll have a working dashboard in ~4 hours, and a fully-functional one in ~14 hours.

---

## 1. Dependency Map — What Blocks What

```
                    ┌─────────────────────────────────────┐
                    │  🔴 CRIT-1: Fix transcript_parts    │
                    │  (transcripts never saved)          │
                    │  app/voice_handler.py               │
                    └──────────────┬──────────────────────┘
                                   │
                                   │ BLOCKS
                    ┌──────────────▼──────────────────────┐
                    │  Conversations table is EMPTY        │
                    │  - No call transcripts               │
                    │  - No lead extraction                │
                    │  - No follow-up auto-detection       │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────┼──────────────────────┐
                    │              │                       │
                    ▼              ▼                       ▼
          Row 2: Live Call    Row 4: Recent       Phase 3: Lead Scoring
          Monitor             Activity Table      (needs engagement data
          (no transcripts     (empty without      from conversations)
           to stream)          transcripts)
```

```
                    ┌─────────────────────────────────────┐
                    │  🟡 CRIT-2: TUNNEL_HOST leak        │
                    │  (stale env var on second run)      │
                    └──────────────┬──────────────────────┘
                                   │
                                   │ BLOCKS
                                   ▼
                    ┌─────────────────────────────────────┐
                    │  Dashboard unreachable via tunnel    │
                    │  (wrong URL → HTTP 530)             │
                    │  ✅ Already fixed in start_services.ps1
                    └─────────────────────────────────────┘
```

```
                    ┌─────────────────────────────────────┐
                    │  New API endpoints needed            │
                    └──────────────┬──────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
   GET /api/calls/live      POST /api/quick-call/    GET /api/leads/{id}/score
   (SSE for live monitor)   batch                   (lead scoring)
   Blocks: Row 2            Blocks: Row 2 right     Blocks: Row 3 cards
```

---

## 2. What Works Today (No Dependencies)

These sections can be built **immediately** using existing APIs:

| Dashboard Section | Existing API | Status |
|-------------------|-------------|--------|
| **Row 1 — Stat Cards** | `GET /api/stats` (active calls, new leads, total leads) | ✅ Ready |
| **Row 3 — Pipeline Board** | `GET /api/leads?status=&limit=200` | ✅ Ready (cards show name, phone, program, status) |
| **Row 4 — Recent Activity** | `GET /api/conversations?limit=20` | ⚠️ Works but empty — CRIT-1 needed for data |
| **Row 5 — Conversations Tab** | `GET /api/conversations?channel=&limit=50` | ⚠️ Works but empty |
| **Row 5 — Calendar Tab** | `GET /api/leads?limit=200` (uses `next_follow_up` field) | ✅ Ready |
| **Row 5 — Reports Tab** | `GET /api/stats` + `GET /api/conversations` | ✅ Ready |
| **Row 5 — Settings Tab** | Static content (tunnel URL, Twilio number) | ✅ Ready |
| **Lead Detail Modal** | `GET /api/leads/{id}` | ✅ Ready |
| **Lead Detail — Conversations** | `GET /api/conversations?lead_id=` | ⚠️ Works but empty |

**Conclusion:** ~70% of the dashboard works with existing APIs. You can build the entire HTML/CSS/JS structure and see real data in Rows 1, 3, and 5 immediately.

---

## 3. What Needs Backend Work First

### Blocker #1: CRIT-1 — Fix `transcript_parts` (30 min)

**What**: `transcript_parts` is never populated in WebSocket handlers. Conversations table stays empty.

**Impact**: Without this, Rows 2, 4 and the Conversations tab show "No data" forever. Lead scoring has no engagement data.

**Files**: `app/voice_handler.py` (change `process_utterance()` return type), `app/main.py` (append transcript in both WS handlers)

**Priority**: **DO THIS FIRST.** It's 30 minutes and unblocks 40% of the dashboard.

### Blocker #2: `GET /api/calls/live` SSE endpoint (1 hour)

**What**: New endpoint streaming active call transcripts via Server-Sent Events.

**Impact**: Row 2 (Live Call Monitor) needs this. Without it, the "Live Call Monitor" section just shows "No active calls" even when calls are happening.

**Files**: `app/main.py` (new SSE endpoint), `app/voice_handler.py` (publish transcripts to in-memory queue)

**Priority**: Can be deferred to Phase 2. Row 2 shows "No active calls" as empty state until implemented.

---

## 4. Prioritized Implementation Order

### Step 0: Fix Blockers (45 min) — BEFORE STARTING DASHBOARD

| # | Task | Time | Why First |
|---|------|------|-----------|
| 0.1 | **CRIT-1: Fix transcript_parts** — save transcripts from STT | 30 min | Conversations table stays empty otherwise |
| 0.2 | **CRIT-2: Fix TUNNEL_HOST** — file-first priority | Already fixed in PS1 | Dashboard unreachable otherwise |

### Phase 1: Core Dashboard Shell (3 hours) — IMMEDIATELY BUILDABLE

| # | Task | Depends On |
|---|------|-----------|
| 1.1 | Create `app/static/dashboard.html` — CSS Grid layout, all 5 rows, static placeholders | Nothing |
| 1.2 | Create `app/static/dashboard.css` — design tokens, card styles, animations, responsive | Nothing |
| 1.3 | Create `app/static/dashboard.js` — AppState, fetchJSON, render functions | Nothing |
| 1.4 | Add `GET /dashboard` endpoint in `main.py` | Nothing |
| 1.5 | **Row 1 — Stat Cards**: Wire to `GET /api/stats` + `GET /api/call-queue` | Nothing (existing APIs) |
| 1.6 | **Row 3 — Pipeline Board**: Wire to `GET /api/leads` with kanban columns | Nothing (existing API) |
| 1.7 | **Row 4 — Recent Activity**: Wire to `GET /api/conversations` | Step 0.1 (CRIT-1) for data |
| 1.8 | **Row 5 — Conversations Tab**: Search + transcript viewer | Step 0.1 (CRIT-1) for data |
| 1.9 | **Row 5 — Settings Tab**: Static content | Nothing |
| 1.10 | **Lead Detail Modal**: Open on card click, show lead info + conversations | Nothing (existing APIs) |

**After Phase 1:** You have a working dashboard at `/dashboard` showing real leads, real stats, and real activity (if CRIT-1 is fixed). The Live Call Monitor and Batch Dialer show empty states.

### Phase 2: Live Features (4 hours)

| # | Task | Depends On |
|---|------|-----------|
| 2.1 | Add `GET /api/dashboard/summary` — aggregated KPI endpoint | Nothing |
| 2.2 | Add `GET /api/calls/live` SSE endpoint | Step 0.1 (CRIT-1) |
| 2.3 | **Row 2 — Live Call Monitor**: Wire to SSE, transcript streaming, duration timer | Steps 2.2 |
| 2.4 | Add `POST /api/quick-call/batch` endpoint | Existing `/api/quick-call` (reuse) |
| 2.5 | Add `GET /api/quick-call/batch/{id}` progress endpoint | Step 2.4 |
| 2.6 | **Row 2 — Batch Dialer**: Number parsing UI, call-queue status widget | Steps 2.4, 2.5 |
| 2.7 | **Row 5 — Calendar Tab**: 7-day view from follow-ups data | Existing `/api/leads` |
| 2.8 | **Row 5 — Reports Tab**: Weekly summary stats | Existing `/api/stats` |

**After Phase 2:** Full dashboard — live calls streaming, batch dialer working, all tabs functional.

### Phase 3: Intelligence (3 hours)

| # | Task | Depends On |
|---|------|-----------|
| 3.1 | Add lead scoring in `app/leads/service.py` | Step 0.1 (CRIT-1 — needs conversation data) |
| 3.2 | Add `GET /api/leads/{id}/score` endpoint | Step 3.1 |
| 3.3 | Add hot/warm/cool/cold classification | Step 0.1 |
| 3.4 | **Row 3 — Lead Cards**: Add score badges + temperature indicators | Steps 3.1–3.3 |
| 3.5 | Modify `GET /api/leads` — add `?search=`, `?program=`, `?health=` filters | Nothing |
| 3.6 | Modify `GET /api/conversations` — add date/outcome filters | Nothing |

**After Phase 3:** Dashboard shows lead intelligence — scores, temperature, smart filtering.

### Phase 4: Polish (3 hours)

| # | Task | Depends On |
|---|------|-----------|
| 4.1 | Loading skeletons for all sections | Nothing |
| 4.2 | Empty states for all sections | Nothing |
| 4.3 | Error handling + retry buttons | Nothing |
| 4.4 | Responsive breakpoints (mobile/tablet) | Nothing |
| 4.5 | Animations (fade-in, pulse, slide, progress bar) | Nothing |
| 4.6 | Auto-refresh with `document.hidden` pause | Nothing |
| 4.7 | Cross-browser testing | Nothing |

---

## 5. New API Endpoints — Implementation Details

### 5.1 `GET /dashboard` (trivial)

```python
@app.get("/dashboard")
async def dashboard_page():
    html_path = Path(__file__).resolve().parent / "static" / "dashboard.html"
    if not html_path.is_file():
        return JSONResponse({"error": "Dashboard page not found"}, status_code=404)
    return FileResponse(html_path, media_type="text/html")
```

### 5.2 `GET /api/dashboard/summary` (aggregate multiple API calls)

```python
@app.get("/api/dashboard/summary")
async def dashboard_summary():
    """One API call that returns everything Row 1-4 needs."""
    leads = await get_leads(limit=200)
    conversations = await get_conversations(limit=50)
    stats = await get_stats()
    active_calls = await get_active_calls()
    
    return {
        "stats": {
            "active_calls": len(active_calls),
            "new_leads_today": count_new_today(leads),
            "due_follow_ups": count_due_today(leads),
            "hot_leads": count_hot(leads),
            "total_pipeline": count_active(leads),
        },
        "leads": leads,
        "recent_activity": conversations[:20],
        "active_calls": active_calls,
    }
```

### 5.3 `GET /api/calls/live` (SSE for transcript streaming)

```python
@app.get("/api/calls/live")
async def calls_live(request: Request, stream: bool = False):
    if not stream:
        # Return JSON snapshot
        return {"active_calls": list(_active_transcript_queue.values())}
    
    # SSE streaming
    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            # Check for new transcript events
            while not _transcript_event_queue.empty():
                event = await _transcript_event_queue.get()
                yield f"event: transcript\ndata: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.5)
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 5.4 `POST /api/quick-call/batch` (bulk call queue)

```python
@app.post("/api/quick-call/batch")
async def batch_quick_call(payload: BatchCallRequest):
    batch_id = str(uuid.uuid4())
    queued = []
    
    for lead in payload.leads:
        # Upsert lead
        lead_id = await upsert_lead(lead.phone_number, lead.name, lead.program_interest)
        # Queue call
        entry = await queue_call(lead_id)
        queued.append({"lead_id": lead_id, "call_queue_id": entry["id"]})
    
    _batch_jobs[batch_id] = {"total": len(queued), "results": []}
    return {"batch_id": batch_id, "total": len(queued), "queued": len(queued)}
```

### 5.5 `GET /api/leads/{id}/score` (lead scoring)

```python
@app.get("/api/leads/{lead_id}/score")
async def lead_score(lead_id: str):
    lead = await get_lead(lead_id)
    conversations = await get_conversations(lead_id=lead_id)
    score = calculate_score(lead, conversations)
    return {"lead_id": lead_id, "score": score.score, "breakdown": score.breakdown}
```

---

## 6. What Already Exists (Don't Rebuild)

| Existing Endpoint | Used By Dashboard Section | Status |
|-------------------|--------------------------|--------|
| `GET /api/stats` | Row 1 (stat cards) | Works — needs `health_distribution` added |
| `GET /api/leads` | Row 3 (pipeline), Row 5 (calendar) | Works — needs `?search=` filter added |
| `GET /api/leads/{id}` | Lead detail modal | Works |
| `GET /api/conversations` | Row 4 (activity), Row 5 (conversations tab) | Works but empty until CRIT-1 fixed |
| `GET /api/call-queue` | Row 2 (live calls) | Works for call status, not transcripts |
| `POST /api/quick-call` | Row 2 (batch dialer — single call fallback) | Works |
| `POST /api/leads/{id}/call` | Row 3 (call-now button on lead cards) | Works |

---

## 7. Summary — What to Do & When

```
TODAY (45 min):
  ├── Fix CRIT-1: transcript_parts (30 min)
  └── Verify CRIT-2: TUNNEL_HOST fixed in PS1 (15 min — already done)

PHASE 1 (3 hours): BUILD THE SHELL
  ├── Create dashboard.html, dashboard.css, dashboard.js
  ├── Add GET /dashboard endpoint
  ├── Wire Row 1 (stats), Row 3 (pipeline), Row 4 (activity), Row 5 (tabs)
  └── Result: Working dashboard with real leads, stats, pipeline ✅

PHASE 2 (4 hours): LIVE FEATURES
  ├── Add SSE endpoint for live transcripts
  ├── Wire Row 2 (live monitor + batch dialer)
  ├── Add batch call API endpoints
  └── Result: Live call streaming + multi-call dialer ✅

PHASE 3 (3 hours): INTELLIGENCE
  ├── Implement lead scoring + hot/warm/cold
  ├── Add score badges to pipeline cards
  └── Result: Smart lead prioritization ✅

PHASE 4 (3 hours): POLISH
  ├── Loading skeletons, empty states, error handling
  ├── Responsive design, animations
  └── Result: Production-quality dashboard ✅
```

**Bottom line:** The dashboard has **no hard blockers**. You can start building Phase 1 immediately. Fixing CRIT-1 first (30 min) will make the activity sections show real data instead of "No conversations found." Everything else is additive.
