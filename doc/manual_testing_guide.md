# Manual Testing Guide — Voice AI Transport Layer

**Project:** University Admissions Voice Assistant
**Test coverage:** Tasks 1–9 (two-phase transport strategy, Phase A)
**Prerequisite:** Python 3.11+ installed. Ollama running (for later pipeline tasks, not required for Tasks 1-8).

---

## How to Use This Guide

Each task has **two validation passes**. Run them in order:

| Step | Who | Command | Purpose |
|---|---|---|---|
| **1. Unit tests** | AI / CLI | `pytest tests/test_taskN_*.py -v` | Code-level checks — imports, config values, HTTP status codes, WAV metadata |
| **2. Manual tests** | Human | See each task below | Real-world checks — audio playback, browser mic, WS connect/disconnect |

**If unit tests fail:** The task is not complete. Fix the code and re-run pytest. Do not proceed to manual testing until all unit tests pass.

**Test results** (generated WAV files, etc.) are written to `tests/test_results/` — separate from the main code.

---

## Task 1: Create `app/` Directory Structure

### Goal
Scaffold the project package so Python recognizes `app/` as an importable module.

### What Gets Created
- `app/__init__.py` (empty file)

### Unit Test (Run First)
```powershell
pytest tests/test_task1_package.py -v
```

### How to Test Manually

**Step 1:** Verify the file exists:
```powershell
Get-ChildItem app\__init__.py
```

**Step 2:** Verify Python can import the package:
```powershell
python -c "import app; print('OK: app imported successfully')"
```

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `Get-ChildItem` shows `__init__.py` exists in `app/` |
| 2 | Python prints `OK: app imported successfully` with no ImportError or traceback |
| 3 | No `__pycache__` errors or permission warnings |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `No such file or directory` | `app/` directory doesn't exist or `__init__.py` wasn't created | Create the directory first, then create the file |
| `ModuleNotFoundError: No module named 'app'` | Running python from wrong directory | `cd D:\university_project_demo` first |
| `ImportError` | `__init__.py` has invalid content | File should be empty (0 bytes or just a comment) |

---

## Task 2: Create `app/config.py` — Transport Configuration

### Goal
Single configuration file that controls transport settings. Twilio placeholders are present but commented out.

### What Gets Created
- `app/config.py`

### Unit Test (Run First)
```powershell
pytest tests/test_task2_config.py -v
```

### How to Test Manually

**Step 1:** Verify Python can import and read settings:
```powershell
python -c "from app.config import settings; print('Provider:', settings.TRANSPORT_PROVIDER); print('Host:', settings.HOST); print('Port:', settings.PORT); print('Sample rate:', settings.AUDIO_SAMPLE_RATE)"
```

**Step 2:** Verify all expected fields are print-able:
```powershell
python -c "from app.config import settings; print(settings.TRANSPORT_PROVIDER, settings.HOST, settings.PORT, settings.AUDIO_SAMPLE_RATE, settings.AUDIO_CHANNELS, settings.AUDIO_SAMPLE_WIDTH)"
```

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `TRANSPORT_PROVIDER` prints as `"websocket"` |
| 2 | `PORT` prints as `8000` |
| 3 | `AUDIO_SAMPLE_RATE` prints as `16000` |
| 4 | No import errors or missing attribute errors |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | `app/__init__.py` missing (Task 1 incomplete) | Go back to Task 1 |
| `AttributeError: ... has no attribute 'TRANSPORT_PROVIDER'` | Field name mismatch in config | Check the field names in `config.py` |
| `ImportError: cannot import name 'settings'` | `settings` object not defined or named differently | Verify `config.py` exports a `settings` object |

---

## Task 3: Create `app/main.py` — FastAPI Skeleton

### Goal
FastAPI application starts, responds to HTTP GET at `/`, and returns valid JSON. No WebSocket yet.

### What Gets Created
- `app/main.py` (initial version, HTTP only)

### Unit Test (Run First)
```powershell
pytest tests/test_task3_fastapi.py -v
```

### How to Test Manually

**Step 1:** Start the server (runs in foreground — keep this terminal open):
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Step 2:** In a **second terminal**, test the health endpoint:
```powershell
curl -s http://127.0.0.1:8000/
```

**Step 3:** Verify the response is valid JSON:
```powershell
curl -s http://127.0.0.1:8000/ | python -c "import sys,json; d=json.load(sys.stdin); print('Status:', d['status'])"
```

**Step 4:** Stop the server (Ctrl+C in the first terminal).

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | Server starts without error (shows `Uvicorn running on http://127.0.0.1:8000`) |
| 2 | `curl` returns HTTP 200 with JSON body |
| 3 | JSON contains `"status": "ok"` field |
| 4 | Server shuts down cleanly on Ctrl+C with no crash traceback |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `uvicorn: command not found` | uvicorn not installed | `pip install uvicorn fastapi` |
| `ModuleNotFoundError: No module named 'app.main'` | Wrong working directory | `cd D:\university_project_demo` |
| `Address already in use` | Port 8000 already occupied | Kill the other process or change PORT in config |
| `curl: (7) Failed to connect` | Server not running or wrong host | Verify server terminal shows "Uvicorn running" |
| Server starts but curl returns empty/error | Route not defined | Check `@app.get("/")` decorator in `main.py` |

---

## Task 4: Add WebSocket Endpoint `/ws/voice`

### Goal
WebSocket endpoint that accepts binary audio frames and echoes them back to the client. Proves real-time streaming works.

### What Gets Modified
- `app/main.py` (add `WS /ws/voice` route)

### Unit Test (Run First)
```powershell
pytest tests/test_task4_websocket.py -v
```

### How to Test Manually

**Step 1:** Start the server:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Step 2:** In a **second terminal**, run this Python echo test:
```powershell
python -c "
import asyncio, websockets
async def test():
    async with websockets.connect('ws://127.0.0.1:8000/ws/voice') as ws:
        sent = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09' * 100  # 1000 bytes
        await ws.send(sent)
        received = await ws.recv()
        print(f'Sent: {len(sent)} bytes')
        print(f'Received: {len(received)} bytes')
        print(f'Match: {sent == received}')
asyncio.run(test())
"
```

**Step 3:** Stop the server (Ctrl+C).

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | Server starts without error |
| 2 | Client connects — no `ConnectionRefusedError` or timeout |
| 3 | Client prints `Sent: 1000 bytes` |
| 4 | Client prints `Received: 1000 bytes` |
| 5 | Client prints `Match: True` |
| 6 | Server logs connection open/close messages |
| 7 | Server shuts down cleanly on Ctrl+C |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `ConnectionRefusedError` | Server not running or wrong port | Check Task 3 first |
| `websockets not found` | websockets library not installed | `pip install websockets` |
| Timeout on `ws.recv()` | Server not echoing back | Check WS handler sends received data back |
| `Match: False` | Data corrupted or truncated | Check that the handler echoes the exact same bytes received |
| Server crashes on WebSocket connect | Unhandled exception in WS handler | Read the server traceback — likely a missing await or attribute error |

---

## Task 5: Create `test_transport.py` — WAV File Test Client

### Goal
Standalone Python script that reads a WAV file, streams it over WebSocket to `/ws/voice`, receives echo frames, and writes an output WAV file. Full client→server→client loop.

### What Gets Created
- `test_transport.py`

### Unit Test (Run First)
```powershell
pytest tests/test_task5_transport.py -v
```

### How to Test Manually

**Step 1:** First, generate a simple test WAV file:
```powershell
python -c "
import wave, struct, math
with wave.open('test_in.wav', 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    # 3 seconds of 440Hz sine tone
    frames = b''.join(struct.pack('<h', int(16000 * math.sin(2*math.pi*440*t/16000))) for t in range(16000*3))
    w.writeframes(frames)
print('test_in.wav created:', 16000*3*2, 'bytes')
"
```

**Step 2:** Start the server (terminal 1):
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Step 3:** Run the test client (terminal 2):
```powershell
python test_transport.py
```

**Step 4:** Stop the server (Ctrl+C in terminal 1).

**Step 5:** Verify the output file exists and has correct size:
```powershell
python -c "
import wave
with wave.open('test_in.wav', 'rb') as w: print(f'Input: {w.getnframes()} frames, {w.getframerate()}Hz')
with wave.open('test_out.wav', 'rb') as w: print(f'Output: {w.getnframes()} frames, {w.getframerate()}Hz')
"
```

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `test_transport.py` runs without exception or traceback |
| 2 | Script prints bytes sent count and bytes received count |
| 3 | Sent byte count equals received byte count (or very close, within 1 chunk) |
| 4 | `test_out.wav` exists on disk |
| 5 | `test_out.wav` has non-zero file size (> 1000 bytes) |
| 6 | Input and output WAV files have same sample rate (16000 Hz) |
| 7 | Input and output WAV files have same number of channels (1) |
| 8 | No connection errors or WebSocket close errors in output |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `test_in.wav not found` | Step 1 was skipped | Run the sine tone generation command first |
| `ConnectionRefusedError` | Server not running (Task 4) | Start server in another terminal |
| Output WAV has 0 bytes | Client disconnected before receiving all data | Check that client waits for all echo frames |
| Sent/received byte mismatch > 1% | Network buffer issue or premature disconnect | Add small delay after final send before closing |
| `wave.Error: unknown format` | WAV header written incorrectly | Verify sample width=2, channels=1 in output WAV header |

---

## Task 6: End-to-End WAV Harness Validation

### Goal
Full end-to-end test with a real spoken-audio WAV file. Confirms the transport layer handles real voice audio correctly.

### What Gets Created
- `test_in.wav` — **replace** the sine tone with a real spoken audio file

### Unit Test (Run First)
```powershell
pytest tests/test_task6_e2e_wav.py -v
```

### How to Test Manually

**Step 1:** Create or obtain a real spoken audio WAV file. You can:
- **Option A:** Record a short clip on your phone saying *"What is the tuition fee at UMD?"* — save as 16kHz mono WAV
- **Option B:** Use the sine tone from Task 5 (acceptable if no mic available, but real speech is better)
- **Option C:** Generate speech with a TTS tool:
  ```powershell
  # If you have edge-tts installed:
  python -c "import subprocess; subprocess.run(['edge-tts', '--text', 'What is the tuition fee at UMD', '--write-media', 'test_in.mp3'])"
  # Then convert to WAV with ffmpeg or an online converter
  ```

Place the file as `test_in.wav` in the project root.

**Step 2:** Verify the test file properties:
```powershell
python -c "
import wave
with wave.open('test_in.wav', 'rb') as w:
    print(f'Channels: {w.getnchannels()} (expect 1)')
    print(f'Sample rate: {w.getframerate()} (expect 16000)')
    print(f'Sample width: {w.getsampwidth()} (expect 2)')
    print(f'Duration: {w.getnframes()/w.getframerate():.1f}s (expect 3-10)')
"
```

**Step 3:** Start server + run test:
```powershell
# Terminal 1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2
python test_transport.py
```

**Step 4:** Inspect the output audio:
```powershell
python -c "
import wave
with wave.open('test_out.wav', 'rb') as w:
    print(f'Output: {w.getnframes()} frames, {w.getframerate()}Hz, {w.getnframes()/w.getframerate():.1f}s')
    print(f'File size: {w.getnframes() * w.getsampwidth()} bytes')
"
```

**Step 5:** Play `test_out.wav` in any media player (VLC, Windows Media Player, etc.) and confirm it sounds like the input.

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `test_in.wav` is a valid WAV file (passes Step 2 checks) |
| 2 | `test_transport.py` completes without exception |
| 3 | `test_out.wav` exists and is > 1000 bytes |
| 4 | Output duration matches input duration (±0.1 second) |
| 5 | Output sample rate matches input sample rate |
| 6 | Output audio plays back and is recognizable (if using real speech, you hear the same words) |
| 7 | Server logs show WebSocket connect, stream, and disconnect without errors |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| Input WAV is stereo or wrong sample rate | Recording settings not correct | Resample/rechannel with ffmpeg: `ffmpeg -i input.wav -ac 1 -ar 16000 test_in.wav` |
| Output audio is garbled/screeching | Sample width mismatch between WAV header and PCM data | Verify WAV reader correctly parses 16-bit samples |
| Output audio is silent | PCM data is all zeros | Check sine tone generation if using generated audio |
| WAV header parse error | File is not a valid WAV (e.g., .mp3 renamed to .wav) | Use a proper WAV file |
| Duration mismatch > 0.5s | Client dropped the connection early or buffered incorrectly | Add close-delay in client before disconnecting |

---

## Task 7: Create `app/static/voice_client.html` — Browser Mic Page

### Goal
Single self-contained HTML page that captures microphone audio and streams it over WebSocket. No build tools, no npm, no external CDN. Works offline after the file is loaded.

### What Gets Created
- `app/static/voice_client.html`

### Unit Test (Run First)
```powershell
pytest tests/test_task7_html.py -v
```

### How to Test Manually

**Step 1:** First, verify the file exists and is non-empty:
```powershell
Get-ChildItem app\static\voice_client.html | Select-Object Name, Length
```

**Step 2:** Test that the HTML file opens in a browser (basic check):
```powershell
# Opens in default browser — just confirm it loads without errors
start app\static\voice_client.html
```

**Step 3:** Check for JavaScript syntax errors — open the browser's Developer Tools (F12 → Console). There should be **no red errors** on page load. You should see the Start/Stop button and status area rendered.

**Step 4:** Verify the page asks for microphone permission when you click "Start". If the browser shows a mic permission prompt, the `getUserMedia()` call is working.

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | File exists at `app/static/voice_client.html` and is > 500 bytes |
| 2 | Opens in Chrome/Edge without JavaScript errors (F12 Console is clear) |
| 3 | A "Start" button and status area are visible on the page |
| 4 | Clicking "Start" triggers a browser microphone permission prompt |
| 5 | No 404 errors for any resources (the page is self-contained, so there should be no network requests) |
| 6 | The page does NOT need an active server to render (it can be opened via `file://` protocol) |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| Blank page | HTML syntax error or missing closing tag | Validate HTML structure |
| `getUserMedia is not defined` in Console | Using wrong API name | Use `navigator.mediaDevices.getUserMedia()` |
| Mic permission prompt doesn't appear | `getUserMedia()` not called on button click | Check the click event handler wiring |
| `NotAllowedError` in Console | User denied mic permission or page opened via HTTP (not HTTPS/localhost) | Reset permission in browser settings; use `file://` or `localhost` |
| Page loads but Start button does nothing | JavaScript event handler not attached | Check `addEventListener` or `onclick` wiring |
| `AudioContext not defined` | Browser too old | Use Chrome 55+ or Edge 80+ |

---

## Task 8: Serve Voice Client from FastAPI + Browser Test

### Goal
Serve the HTML page from FastAPI so the browser can connect to the WebSocket endpoint on the same origin. Test the full browser mic → WS echo → speaker loop.

### What Gets Modified
- `app/main.py` (add `GET /voice` serving the static HTML file + add `/` link)

### Unit Test (Run First)
```powershell
pytest tests/test_task8_browser.py -v
```

### How to Test Manually

**Step 1:** Start the server:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Step 2:** Open the voice client in Chrome or Edge:
```
http://localhost:8000/voice
```

**Step 3:** Open Developer Tools (F12 → Console tab) to watch for errors.

**Step 4:** Click the **Start** button. You should see:
- Browser mic permission prompt → click **Allow**
- Status changes to "Connected" or "Listening"
- Status indicator turns green

**Step 5:** Speak into the microphone for 2-3 seconds. You should hear your voice echoed back through the speakers (the server is echoing).

**Step 6:** Click **Stop**. Status should show "Disconnected".

**Step 7:** Verify the server console logs the connect/disconnect events.

**Step 8:** Stop the server (Ctrl+C).

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `http://localhost:8000/voice` serves the page with HTTP 200 |
| 2 | No JavaScript errors in browser Console on page load |
| 3 | Clicking "Start" → status changes to "Connected" (or similar) |
| 4 | Speaking into mic → audio echo plays back through speakers (may have slight delay — that's normal) |
| 5 | Clicking "Stop" → status changes to "Disconnected" |
| 6 | Server console shows WebSocket connect/disconnect log messages |
| 7 | Clicking "Start" again after "Stop" works (reconnect works) |
| 8 | Server shuts down cleanly on Ctrl+C |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| `GET /voice` returns 404 | Route not defined or static file path wrong | Check `app.mount()` or file-serving route |
| Page loads but can't connect (status stays "Connecting...") | WebSocket URL is hardcoded to wrong host | Make sure WS URL is `ws://localhost:8000/ws/voice` (relative to origin) |
| Mic permission granted but no audio | `AudioContext` suspended (browser autoplay policy) | Add `audioContext.resume()` on user click |
| Echo is garbled/robotic | Sample rate mismatch between mic and WS | Mic capture sample rate should match `AUDIO_SAMPLE_RATE` (16000) in config |
| Echo is deafening (feedback) | Speakers too close to mic or volume too high | Use headphones for testing |
| `WebSocket connection to ... failed` | Server not running (Task 3) | Start the server first |
| Static file not found (500 error) | `static/` directory not in correct location | Should be `app/static/voice_client.html` relative to project root |

---

## Task 9: Create `requirements.txt` + Final Integration Check

### Goal
Formalize all project dependencies and perform a clean-room integration test that verifies everything works together.

### What Gets Created
- `requirements.txt`

### Unit Test (Run First)
```powershell
pytest tests/test_task9_requirements.py -v
```

### How to Test Manually

**Step 1:** Verify `requirements.txt` has all required packages:
```powershell
Get-Content requirements.txt
```

Expected packages: `fastapi`, `uvicorn`, `websockets` (at minimum — `wave` is stdlib).

**Step 2:** Verify all packages are importable:
```powershell
python -c "
import fastapi; print('fastapi:', fastapi.__version__)
import uvicorn; print('uvicorn:', uvicorn.__version__)
import websockets; print('websockets:', websockets.__version__)
print('All packages OK')
"
```

**Step 3:** Full integration test — start server:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Step 4:** Test 1: Health endpoint:
```powershell
curl -s http://127.0.0.1:8000/ | python -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'; print('PASS: Health check')"
```

**Step 5:** Test 2: WebSocket echo:
```powershell
python -c "
import asyncio, websockets
async def test():
    async with websockets.connect('ws://127.0.0.1:8000/ws/voice') as ws:
        data = b'\x00\x01\x02\x03' * 250
        await ws.send(data)
        back = await ws.recv()
        assert data == back, f'Mismatch: sent {len(data)}, got {len(back)}'
        print('PASS: WebSocket echo')
asyncio.run(test())
"
```

**Step 6:** Test 3: Voice client page loads:
```powershell
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/voice
```
Expected output: `200`

**Step 7:** Test 4: WAV harness:
```powershell
python test_transport.py
```

**Step 8:** Stop the server (Ctrl+C).

**Step 9:** Open `http://localhost:8000/voice` in Chrome, do the mic → echo test one final time.

### Pass Criteria

| # | Criterion |
|---|---|
| 1 | `requirements.txt` exists and lists `fastapi`, `uvicorn`, `websockets` |
| 2 | All packages import without errors (Step 2) |
| 3 | Health check returns `PASS: Health check` |
| 4 | WebSocket echo returns `PASS: WebSocket echo` |
| 5 | Voice client page returns HTTP 200 |
| 6 | `test_transport.py` completes without errors |
| 7 | `test_out.wav` is playable and matches input |
| 8 | Browser mic → echo loop works (Step 9) |
| 9 | Server shuts down cleanly |

### Fail Conditions

| Symptom | Likely Cause | Fix |
|---|---|---|
| Any package fails to import | Not installed | `pip install -r requirements.txt` |
| Health check fails | Server not started or port conflict | Kill stale processes, restart |
| WebSocket echo fails with `Match: False` | Regression in WS handler | Check `app/main.py` WS route |
| Voice client returns non-200 | Static file path broken | Verify `static/voice_client.html` exists |
| WAV harness fails | Regression from earlier tasks | Go back through Task 5 and 6 |

---

## Quick-Reference Test Commands (All Tasks)

Copy-paste these in order when verifying the complete project:

```powershell
# === TASK 1 ===
python -c "import app; print('OK: app imported successfully')"

# === TASK 2 ===
python -c "from app.config import settings; print('Provider:', settings.TRANSPORT_PROVIDER)"

# === TASK 3 ===
# Terminal 1: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Terminal 2:
curl -s http://127.0.0.1:8000/

# === TASK 4 ===
# Terminal 1: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Terminal 2:
python -c "import asyncio,websockets; async def t(): async with websockets.connect('ws://127.0.0.1:8000/ws/voice') as ws: d=b'\x00'*100; await ws.send(d); r=await ws.recv(); print('Match:',d==r); asyncio.run(t())"

# === TASK 5 ===
# (generate test_in.wav first — see Task 5 instructions)
python test_transport.py

# === TASK 6 ===
python -c "import wave; w=wave.open('test_out.wav','rb'); print(f'Out: {w.getnframes()} frames, {w.getframerate()}Hz')"

# === TASK 7 ===
Get-ChildItem app\static\voice_client.html | Select-Object Name, Length

# === TASK 8 ===
# Open http://localhost:8000/voice in Chrome
# Click Start → speak → hear echo → click Stop

# === TASK 9 ===
Get-Content requirements.txt
pip install -r requirements.txt
# Run Tasks 3-6 checks again
```

---

## Dependency Chain

```
Task 1 (__init__.py)
   │
   ▼
Task 2 (config.py)
   │
   ▼
Task 3 (FastAPI skeleton)
   │
   ▼
Task 4 (WS /ws/voice) ─────────────────────┐
   │                                         │
   ▼                                         │
Task 5 (test_transport.py)                   │
   │                                         │
   ▼                                         │
Task 6 (WAV E2E test)                        │
                                             │
Task 7 (voice_client.html) ──────────────────┘
   │
   ▼
Task 8 (serve HTML + browser test)

Task 9 (requirements.txt + full integration)
```

Tasks 5 and 7 can be done in parallel after Task 4, but sequential is recommended for testing clarity.
