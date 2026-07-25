# QA Validation Guide — University Admissions Voice Assistant

**Version:** 1.0
**Date:** 2026-07-25
**Target Machine:** Windows 11, NVIDIA RTX 2060 (6 GB), Python 3.11, Ollama, Docker Desktop
**Total Test Cases:** 34
**Estimated Time:** 45–60 minutes

---

## Prerequisites (Before Starting)

- [ ] Project cloned/copied to `D:\university_project_demo`
- [ ] Ollama installed and running (check tray icon or `http://localhost:11434`)
- [ ] Docker Desktop running (for PostgreSQL)
- [ ] `.env` file present with credentials (check: `type .env`)
- [ ] Terminal opened as **Administrator** in `D:\university_project_demo`

---

## Section 1: Environment Verification (6 tests)

### Test 1.1 — Python Version
```powershell
python --version
```
**Expected:** `Python 3.11.x` or higher

### Test 1.2 — Dependencies Installed
```powershell
pip check
```
**Expected:** No broken dependencies reported. If errors, run: `pip install -r requirements.txt`

### Test 1.3 — GPU CUDA Detection
```powershell
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
```
**Expected:**
```
CUDA: True
GPU: NVIDIA GeForce RTX 2060
```

### Test 1.4 — VRAM Budget
```powershell
python -c "import torch; t=torch.cuda.get_device_properties(0).total_memory/1e9; print(f'VRAM: {t:.1f} GB')"
```
**Expected:** `VRAM: 6.0 GB` or higher

### Test 1.5 — Ollama Service Running
```powershell
curl -s http://127.0.0.1:11434/api/tags
```
**Expected:** JSON response listing installed models. Must include:
- `qwen2.5:7b-instruct-q3_K_M` (or another Qwen 2.5 variant)
- `nomic-embed-text`

### Test 1.6 — PostgreSQL Running
```powershell
docker ps --filter "name=elearning-postgres" --format "{{.Status}}"
```
**Expected:** Output contains `Up` (e.g., `Up 2 hours (healthy)`)

---

## Section 2: Automated Test Suite (1 test)

### Test 2.1 — Run All 139 Unit Tests
```powershell
set HF_HUB_ENABLE_HF_XET=0
pytest tests/ -v --tb=short
```
**Expected:**
```
139 passed, 0 failed, 0 skipped
```
**Time:** ~60–90 seconds

**If any test fails**, note the test name and error message. Common causes:
- Ollama not running → Phase 3 tests fail
- Docker not running → Phase 6 `test_psycopg2` fails
- `.env` missing → `test_twilio_config_placeholders_present` fails

---

## Section 3: Component Verification Scripts (5 tests)

### Test 3.1 — Environment Verification Script
```powershell
python test_environment.py
```
**Expected:** All checks show `[PASS]` or `[OK]`. VRAM budget shows `Met`. Exit code 0.
```
[PASS] python
[PASS] torch
[PASS] cuda
[PASS] vram
[PASS] ollama
[PASS] llm_model
[PASS] embed_model

ALL CHECKS PASSED (7/7)
```

### Test 3.2 — RAG + LLM Integration
```powershell
python test_rag_llm.py
```
**Expected:** 
```
[PASS] Tuition amount ($15,000)
[PASS] Deadline (August 1st)
Phase 3 PASSED!
```
The LLM must respond with specific facts from the ChromaDB context, not hallucinated data.

### Test 3.3 — WAV Transport Client
```powershell
# Start the server first (in a separate terminal):
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# In another terminal:
python test_transport.py test_speech.wav test_transport_out.wav
```
**Expected:**
```
Sent:    76032 bytes (100%)
Received: 76032 bytes (100%)
Output:  test_transport_out.wav
```
Bytes sent == bytes received. Output WAV file created.

### Test 3.4 — STT + TTS Audio Engine
```powershell
set HF_HUB_ENABLE_HF_XET=0
python test_audio_local.py test_speech.wav test_audio_out.wav
```
**Expected:**
```
Device: cuda
-- Step 1: Speech-to-Text --
[OK] WhisperSTTService initialized (model=small.en, device=cuda)
[OK] Transcription complete
Transcript: "What is the tuition fee at UMD?"

-- Step 2: Text-to-Speech --
[OK] KokoroTTSService initialized (voice=af_heart)
Phase 2 complete!
```
Transcript must contain recognizable English words matching the input speech.

### Test 3.5 — Full Voice Pipeline (STT → RAG → LLM → TTS)
```powershell
set HF_HUB_ENABLE_HF_XET=0
python test_full_pipeline.py test_speech.wav
```
**Expected:**
```
-- Step 1: Speech-to-Text (Whisper on CUDA) --
Transcript: "What is the tuition fee at UMD?"
Time:       ~2-5s

-- Step 2: RAG + LLM (ChromaDB + Qwen) --
Context:  1503 chars retrieved
Model:    qwen2.5:7b-instruct-q3_K_M
Answer:   "<a factually correct answer about UMD tuition>"
Time:     ~10-20s

-- Step 3: Text-to-Speech (Kokoro) --
TTS service: KokoroTTSService (voice=af_heart)

Full Pipeline Complete!
All 4 stages working on CUDA + Ollama!
```
The LLM answer must be factually grounded in the ChromaDB context (not generic/hallucinated).

---

## Section 4: FastAPI Server & HTTP Endpoints (6 tests)

**Prerequisite:** Start the server:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Wait for: `Application startup complete.` and `Database: PostgreSQL connected`

### Test 4.1 — Health Check
```powershell
curl -s http://127.0.0.1:8000/
```
**Expected JSON response:**
```json
{
  "status": "ok",
  "app": "University Admissions Voice Assistant",
  "database": "connected",
  "transport": "websocket",
  "twilio_configured": true,
  "endpoints": {
    "health": "/",
    "voice_page": "/voice",
    "websocket_pcm": "/ws/voice",
    "websocket_text_rag": "/ws/voice/text",
    "twilio_webhook": "/twilio/voice",
    "twilio_websocket": "/ws/twilio"
  }
}
```
**Critical checks:**
- `database` must be `"connected"` (not `"unavailable"`)
- `twilio_configured` must be `true`
- All 6 endpoints listed

### Test 4.2 — Voice Client Page
```powershell
curl -s -I http://127.0.0.1:8000/voice
```
**Expected:** `HTTP/1.1 200 OK`, `content-type: text/html; charset=utf-8`

### Test 4.3 — TwiML Voice Webhook
```powershell
curl -s http://127.0.0.1:8000/twilio/voice
```
**Expected XML response:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://your-ngrok-hostname.ngrok.io/ws/twilio" />
    </Connect>
</Response>
```

### Test 4.4 — WebSocket PCM Echo
Run this Python script:
```powershell
python -c "
import asyncio, websockets
async def test():
    async with websockets.connect('ws://127.0.0.1:8000/ws/voice') as ws:
        await ws.send(b'hello-test-payload-12345')
        reply = await ws.recv()
        print('Echo match:', reply == b'hello-test-payload-12345')
asyncio.run(test())
"
```
**Expected:** `Echo match: True`

### Test 4.5 — WebSocket Text RAG Query
```powershell
python -c "
import asyncio, websockets, json
async def test():
    async with websockets.connect('ws://127.0.0.1:8000/ws/voice/text') as ws:
        await ws.send(json.dumps({'query': 'What is the tuition fee at UMD?'}))
        reply = await asyncio.wait_for(ws.recv(), timeout=30)
        data = json.loads(reply)
        print('Status:', data.get('status'))
        print('Answer:', data.get('answer', '')[:150])
asyncio.run(test())
"
```
**Expected:**
```
Status: ok
Answer: <context-aware answer about UMD tuition from ChromaDB>
```
The answer must contain specific facts, not "I don't have that information."

### Test 4.6 — Multiple Disconnect Resilience
```powershell
python -c "
import asyncio, websockets
async def test():
    for i in range(5):
        async with websockets.connect('ws://127.0.0.1:8000/ws/voice') as ws:
            await ws.send(f'ping-{i}'.encode())
            reply = await ws.recv()
            assert reply == f'ping-{i}'.encode()
    print('5 rapid connects/disconnects: OK')
asyncio.run(test())
"
```
**Expected:** `5 rapid connects/disconnects: OK` (no server crash)

---

## Section 5: Browser Client Test (3 tests)

### Test 5.1 — Voice Page Loads
1. Open Chrome/Edge
2. Navigate to `http://127.0.0.1:8000/voice`
3. **Expected:** Page loads with title "Voice Assistant — University Admissions"
4. Shows: "Ready" status, Start button (enabled), WebSocket URL at bottom

### Test 5.2 — Microphone Permission
1. Click the **Start** button
2. Browser shows microphone permission prompt
3. Click **Allow**
4. **Expected:** Status changes to "Listening…" with green pulsing dot

### Test 5.3 — Audio Echo Loop
1. With mic active, speak a short phrase ("hello test one two three")
2. Click **Stop**
3. **Expected:** You should hear your voice echoed back through speakers
4. Status returns to "Ready"

---

## Section 6: Database Verification (3 tests)

### Test 6.1 — lead_calls Table Exists
```powershell
docker exec elearning-postgres psql -U elearning -d admissions -c "\dt"
```
**Expected:** Output includes `lead_calls` table in the list.

### Test 6.2 — Record Insertion
```powershell
python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from app.database import save_lead_call
async def test():
    ok = await save_lead_call(
        phone_number='+15551234567',
        transcript='QA test call: Student asked about tuition fees.',
        extracted_lead={'name': 'QA Tester', 'email': 'qa@test.com', 'program': 'Computer Science'}
    )
    print('Insert:', 'OK' if ok else 'FAILED')
asyncio.run(test())
"
```
**Expected:** `Insert: OK`

### Test 6.3 — Verify Inserted Record
```powershell
docker exec elearning-postgres psql -U elearning -d admissions -c "SELECT id, phone_number, extracted_lead FROM lead_calls ORDER BY created_at DESC LIMIT 1;"
```
**Expected:** Shows the record just inserted with:
- `phone_number`: `+15551234567`
- `extracted_lead`: `{"name": "QA Tester", "email": "qa@test.com", "program": "Computer Science"}`

---

## Section 7: Lead Extraction Verification (2 tests)

### Test 7.1 — LLM Extracts Lead from Transcript
```powershell
python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from app.database import extract_lead_from_transcript
async def test():
    transcript = '''
    Agent: Hello, welcome to University Admissions.
    Caller: Hi, my name is Sarah Johnson. I want to apply for the MBA program.
    Agent: Great! Can I get your email?
    Caller: Yes, it's sarah.j@email.com.
    '''
    lead = await extract_lead_from_transcript(transcript)
    print('Name:', lead.get('name'))
    print('Email:', lead.get('email'))
    print('Program:', lead.get('program'))
    assert 'sarah' in str(lead.get('name', '')).lower()
    assert '@' in str(lead.get('email', ''))
    print('Lead extraction: PASS')
asyncio.run(test())
"
```
**Expected:**
```
Name: Sarah Johnson
Email: sarah.j@email.com
Program: MBA
Lead extraction: PASS
```

### Test 7.2 — Missing Fields Return Null
```powershell
python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from app.database import extract_lead_from_transcript
async def test():
    transcript = 'Caller: What time does the library close?'
    lead = await extract_lead_from_transcript(transcript)
    print('Name:', lead.get('name'))
    print('Email:', lead.get('email'))
    print('Program:', lead.get('program'))
    # At least one field should be null for a non-lead transcript
    nulls = [k for k,v in lead.items() if v is None or v == '']
    print(f'Null fields: {len(nulls)} (expected >=1)')
    assert len(nulls) >= 1
    print('Missing fields test: PASS')
asyncio.run(test())
"
```
**Expected:**
```
Name: null (or None)
Null fields: >=1
Missing fields test: PASS
```

---

## Section 8: μ-Law Audio Conversion (1 test)

### Test 8.1 — PCM ↔ μ-Law Roundtrip
```powershell
python -c "
import audioop, struct, math
# Generate a 440Hz test tone at 8kHz (Twilio rate)
sr, dur = 8000, 0.5
samples = [int(16000*math.sin(2*math.pi*440*t/sr)) for t in range(int(sr*dur))]
pcm = struct.pack(f'<{len(samples)}h', *samples)
# Encode
ulaw = audioop.lin2ulaw(pcm, 2)
assert len(ulaw) == len(samples), f'u-law size mismatch: {len(ulaw)} vs {len(samples)}'
# Decode
decoded = audioop.ulaw2lin(ulaw, 2)
dec_samples = struct.unpack(f'<{len(samples)}h', decoded)
# Verify roundtrip (allow u-law quantization error)
errors = [abs(samples[i]-dec_samples[i]) for i in range(len(samples))]
max_err = max(errors)
print(f'u-law roundtrip: {len(samples)} samples, max error: {max_err}')
assert max_err < 2000, f'u-law error too large: {max_err}'
print('u-law conversion: PASS')
"
```
**Expected:**
```
u-law roundtrip: 4000 samples, max error: < 2000
u-law conversion: PASS
```

---

## Section 9: Config & Credentials (3 tests)

### Test 9.1 — .env File Present and Loaded
```powershell
python -c "
from dotenv import load_dotenv; load_dotenv()
import os
print('TWILIO_ACCOUNT_SID:', 'SET' if os.getenv('TWILIO_ACCOUNT_SID') else 'MISSING')
print('TWILIO_AUTH_TOKEN:', 'SET' if os.getenv('TWILIO_AUTH_TOKEN') else 'MISSING')
print('DATABASE_URL:', 'SET' if os.getenv('DATABASE_URL') else 'MISSING')
print('HF_HUB_ENABLE_HF_XET:', os.getenv('HF_HUB_ENABLE_HF_XET', 'NOT SET'))
"
```
**Expected:** All three credentials show `SET`. `HF_HUB_ENABLE_HF_XET` shows `0`.

### Test 9.2 — Settings Dataclass Loads Correctly
```powershell
python -c "
from app.config import settings
print('Transport:', settings.TRANSPORT_PROVIDER)
print('Host:', settings.HOST)
print('Port:', settings.PORT)
print('Twilio SID:', 'SET' if settings.TWILIO_ACCOUNT_SID else 'EMPTY')
print('DB URL:', 'SET' if settings.DATABASE_URL else 'EMPTY')
"
```
**Expected:**
```
Transport: websocket
Host: 127.0.0.1
Port: 8000
Twilio SID: SET
DB URL: SET
```

### Test 9.3 — .gitignore Exists
```powershell
type .gitignore
```
**Expected:** File contains `.env` (should NOT be committed to git).

---

## Section 10: Pipeline Assembly (2 tests)

### Test 10.1 — Pipeline Components Import
```powershell
python run_pipeline_test.py
```
**Expected:** Exit code 0. Tests 1-6 show `[PASS]`. All pipeline components import and assemble correctly.

### Test 10.2 — Pipeline Stage Order
```powershell
python -c "
from app.pipeline import create_local_voice_pipeline
import asyncio
async def test():
    result = await create_local_voice_pipeline()
    print('Pipeline creation: OK')
    print('Result type:', type(result).__name__)
asyncio.run(test())
"
```
**Expected:**
```
[OK] SileroVADAnalyzer initialized
[OK] WhisperSTTService initialized
[OK] OLLamaLLMService initialized
[OK] KokoroTTSService initialized
[OK] Pipeline assembled: N processors
Pipeline creation: OK
```

---

## Section 11: VRAM Profiling (2 tests)

### Test 11.1 — Idle VRAM (Ollama Running)
```powershell
python -c "
import torch, subprocess, json
# Check Ollama model VRAM usage
total = torch.cuda.get_device_properties(0).total_memory / 1e9
used = torch.cuda.memory_allocated(0) / 1e9
reserved = torch.cuda.memory_reserved(0) / 1e9
print(f'VRAM: {used:.1f} GB used / {total:.1f} GB total')
print(f'Headroom: {total - reserved:.1f} GB')
assert total >= 5.5, 'GPU must have >= 5.5 GB for full pipeline'
print('VRAM budget: OK')
"
```
**Expected:**
```
VRAM: 0.0 GB used / 6.4 GB total
Headroom: ~6.0 GB
VRAM budget: OK
```

### Test 11.2 — Peak VRAM Under Load
Observe VRAM during the full pipeline test:
```powershell
# In terminal 1 — run nvidia-smi in a loop:
powershell -c "while(1) { nvidia-smi --query-gpu=memory.used --format=csv,noheader; sleep 2 }"

# In terminal 2 — run the full pipeline:
set HF_HUB_ENABLE_HF_XET=0
python test_full_pipeline.py test_speech.wav
```
**Expected:** VRAM peaks at ~4.5–5.5 GB during LLM inference. Never exceeds 6.0 GB (no CUDA OOM errors).

---

## Test Summary Checklist

| # | Test | Section | Pass/Fail |
|---|------|---------|-----------|
| 1.1 | Python version >= 3.11 | Env | [ ] |
| 1.2 | Dependencies OK (`pip check`) | Env | [ ] |
| 1.3 | CUDA GPU detected | Env | [ ] |
| 1.4 | VRAM >= 5.5 GB | Env | [ ] |
| 1.5 | Ollama running + models pulled | Env | [ ] |
| 1.6 | PostgreSQL running (Docker) | Env | [ ] |
| 2.1 | All 139 unit tests pass | Tests | [ ] |
| 3.1 | `test_environment.py` | Scripts | [ ] |
| 3.2 | `test_rag_llm.py` | Scripts | [ ] |
| 3.3 | `test_transport.py` | Scripts | [ ] |
| 3.4 | `test_audio_local.py` | Scripts | [ ] |
| 3.5 | `test_full_pipeline.py` | Scripts | [ ] |
| 4.1 | Health check `/` | HTTP | [ ] |
| 4.2 | Voice page `/voice` | HTTP | [ ] |
| 4.3 | TwiML `/twilio/voice` | HTTP | [ ] |
| 4.4 | WebSocket echo `/ws/voice` | WS | [ ] |
| 4.5 | WebSocket RAG `/ws/voice/text` | WS | [ ] |
| 4.6 | 5 rapid WS disconnects | WS | [ ] |
| 5.1 | Browser voice page loads | Browser | [ ] |
| 5.2 | Microphone permission | Browser | [ ] |
| 5.3 | Audio echo loop | Browser | [ ] |
| 6.1 | `lead_calls` table exists | DB | [ ] |
| 6.2 | Record insertion works | DB | [ ] |
| 6.3 | Inserted record verified | DB | [ ] |
| 7.1 | LLM extracts lead (name/email/program) | DB | [ ] |
| 7.2 | Missing fields return null | DB | [ ] |
| 8.1 | μ-law PCM roundtrip | Audio | [ ] |
| 9.1 | `.env` credentials loaded | Config | [ ] |
| 9.2 | Settings dataclass correct | Config | [ ] |
| 9.3 | `.gitignore` present | Config | [ ] |
| 10.1 | `run_pipeline_test.py` passes | Pipeline | [ ] |
| 10.2 | Pipeline assembles correctly | Pipeline | [ ] |
| 11.1 | Idle VRAM < 6 GB | VRAM | [ ] |
| 11.2 | Peak VRAM < 6 GB under load | VRAM | [ ] |

**Pass: ___ / 34**

---

## Troubleshooting Quick Reference

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `CUDA: False` | CPU-only PyTorch | `pip install torch --index-url https://download.pytorch.org/whl/cu124` |
| Ollama connection refused | Ollama not running | Start Ollama from Start Menu |
| `database: unavailable` | Docker stopped | `docker start elearning-postgres` |
| Phase 2 tests fail | `HF_HUB_ENABLE_HF_XET` not set | `set HF_HUB_ENABLE_HF_XET=0` before running |
| ChromaDB `768 vs 384` error | Embedding mismatch | Server auto-fixes via `OllamaEmbeddingFunction` |
| `ImportError: _regex` | regex package corrupted | `pip uninstall regex -y && pip install regex` |
| Transcript empty | Input is not speech | Use a real speech recording (not sine tone) |
| Browser mic not working | HTTP (not HTTPS) | Use `http://127.0.0.1:8000/voice` (localhost is allowed) |
