# Plan: Two-Phase Transport Strategy — Implementation


## Context

The `doc/architect_analysis.md` identified that Twilio requires credentials/subscription we don't have. The recommended two-phase transport strategy lets us build and test the voice pipeline now with zero-cost local transports, then swap Twilio in later via a config file when credentials arrive.

**Core principle:** The pipeline is transport-agnostic. We build the transport layer first, validate it independently, then the Pipecat pipeline plugs into it later. Twilio is a config-file swap — not a rewrite.

## Testing Workflow

For each task, run **two validation passes**:

| Pass | Command | What it validates |
|---|---|---|
| **1. Unit tests (AI)** | `pytest tests/test_taskN_*.py -v` | Code correctness — importability, values, HTTP responses, WAV integrity |
| **2. Manual tests (Human)** | See `doc/manual_testing_guide.md` Task N | Real-world behavior — audio quality, browser interaction, mic echo |

**Test results** (generated WAVs, logs) are written to `tests/test_results/` — separate from the main codebase.

Run all tests at any time:
```powershell
pytest tests/ -v --tb=short
```

---

## Task Breakdown (Linear Execution — Test Each Before Moving On)

---

### Task 1: Create `app/` Directory Structure

**Goal:** Scaffold the project directory and verify Python can import the package.

**Files created:**
- `app/__init__.py` — empty, marks the package

**Unit test:** `pytest tests/test_task1_package.py -v`
**Manual test:** `python -c "import app; print('app imported')"`

**Depends on:** Nothing

---

### Task 2: Create `app/config.py` — Transport Configuration

**Goal:** Single configuration file that controls which transport is active. Prepares the Twilio placeholder so later it's a one-line change.

**Files created:**
- `app/config.py` — settings dataclass with:
  - `TRANSPORT_PROVIDER` (default: `"websocket"`)
  - `HOST`, `PORT` for FastAPI
  - `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `AUDIO_SAMPLE_WIDTH`
  - Twilio placeholder fields (commented, empty strings)

**Unit test:** `pytest tests/test_task2_config.py -v`
**Manual test:** `python -c "from app.config import settings; print(settings.TRANSPORT_PROVIDER)"`

**Depends on:** Task 1

---

### Task 3: Create `app/main.py` — FastAPI Skeleton

**Goal:** FastAPI app that starts, serves a health-check page, and confirms the server is reachable. No WebSocket yet.

**Files created:**
- `app/main.py` — FastAPI application with:
  - `GET /` — health check JSON + links to `/voice` page
  - `GET /voice` — placeholder (returns plain text until Task 8)

**Unit test:** `pytest tests/test_task3_fastapi.py -v`
**Manual test:** Start server → `curl http://localhost:8000/` → JSON response with status "ok"

**Depends on:** Task 2

---

### Task 4: Add WebSocket Endpoint `/ws/voice`

**Goal:** WebSocket endpoint that accepts binary audio frames and echoes them back. Proves the real-time streaming transport works before any AI pipeline is connected.

**Files modified:**
- `app/main.py` — add `WS /ws/voice` route:
  - Accept binary messages (raw PCM audio)
  - Echo each received binary frame back to the client
  - Log connect/disconnect events
  - Handle client disconnect gracefully

**Unit test:** `pytest tests/test_task4_websocket.py -v`
**Manual test:** WebSocket echo check via Python one-liner

**Depends on:** Task 3

---

### Task 5: Create `test_transport.py` — WAV File Test Client

**Goal:** Python script that reads a WAV file, streams it in chunks over WebSocket to `/ws/voice`, receives echo frames back, and writes them to an output WAV file. Proves the full client→server→client audio loop works.

**Files created:**
- `test_transport.py`:
  - Parse WAV header (sample rate, channels, bit depth) using Python `wave` module
  - Connect to `ws://localhost:8000/ws/voice`
  - Stream PCM data in 20ms chunks
  - Receive echo frames back
  - Write assembled PCM into output WAV with correct header

**Unit test:** `pytest tests/test_task5_transport.py -v`
**Manual test:** `python test_transport.py` — echoes test_in.wav → writes test_out.wav, prints byte counts

**Depends on:** Task 4

---

### Task 6: End-to-End WAV Harness Validation

**Goal:** Full end-to-end test with a real spoken-audio sample. Confirms the transport layer is solid before we add browser UI or real AI pipeline.

**Files needed:**
- `test_in.wav` — 16kHz mono 16-bit WAV, 3-10 seconds, spoken English question (generate or download sample)

**Unit test:** `pytest tests/test_task6_e2e_wav.py -v`
**Manual test:** Run server + test_transport.py with real audio → inspect test_out.wav

**Depends on:** Task 5

---

### Task 7: Create `app/static/voice_client.html` — Browser Mic Page

**Goal:** Single self-contained HTML page that captures microphone audio, streams it over WebSocket to the server, plays back received audio. Zero dependencies — open in any modern browser and it works.

**Files created:**
- `app/static/voice_client.html`:
  - `getUserMedia()` for mic capture (requests 16kHz mono if supported)
  - `AudioContext` + `ScriptProcessorNode` (or `AudioWorklet`) for PCM conversion
  - WebSocket client to `/ws/voice`
  - Audio playback queue for received TTS chunks
  - Start/Stop button with status indicator
  - Minimal clean CSS, works on Chrome/Edge

**Unit test:** `pytest tests/test_task7_html.py -v`
**Manual test:** Open `voice_client.html` directly in browser (file://) → verify mic permission prompt

**Depends on:** Task 4 (needs WS endpoint running)

---

### Task 8: Serve Voice Client from FastAPI + Browser Test

**Goal:** Serve the HTML page from FastAPI so it can connect to the WebSocket endpoint (same origin, no CORS issues). Test the full browser mic → WS → echo → speaker loop.

**Files modified:**
- `app/main.py` — `GET /voice` serves `static/voice_client.html`
- `app/main.py` — `GET /` health page links to `/voice`

**Unit test:** `pytest tests/test_task8_browser.py -v`
**Manual test:** Open `http://localhost:8000/voice` in Chrome → click Start → speak → hear echo

**Depends on:** Task 7

---

### Task 9: Create `requirements.txt` + Final Integration Check

**Goal:** Formalize all dependencies and run a full integration check from scratch. This is the demo-ready checkpoint.

**Files created:**
- `requirements.txt` — pinned dependencies: `fastapi`, `uvicorn`, `websockets`, `wave` (stdlib)

**Unit test:** `pytest tests/test_task9_requirements.py -v`
**Manual test:** Full integration run — start server, test WAV harness, test browser page, verify all pass

**Depends on:** Tasks 1-8

---

## Deferred (Phase B — When Twilio Credentials Arrive)

- Update `app/config.py` with real Twilio credentials
- Add `GET /twilio/voice` (TwiML) route to `app/main.py`
- Add `WS /ws/twilio` route with μ-law resampling
- Pipeline code is untouched — transport swap only

## Verification

1. `python test_transport.py` — streams `test_in.wav`, saves `test_out.wav`
2. Open `http://localhost:8000/voice` in Chrome — mic capture works, audio plays back
3. `curl http://localhost:8000/` — health check returns 200
4. No errors on WebSocket connect, stream, or disconnect
