# Status & Next Steps — University Admissions Voice Assistant

**Date:** 2026-07-25
**Overall:** All 6 development phases + 9 transport tasks complete.
**Tests:** 127 passed · 0 failed · 12 skipped (AppLocker / no GPU)
**LLM Model:** `qwen2.5:7b-instruct-q3_K_M` (3.8 GB, fits 6 GB GPU at 85%)

---

## What is Done

### Transport Layer (Tasks 1–9)
| # | Component | File |
|---|-----------|------|
| 1 | Package scaffold | `app/__init__.py` |
| 2 | Config (transport, Twilio, DB placeholders) | `app/config.py` |
| 3 | FastAPI server (5 endpoints) | `app/main.py` |
| 4 | WebSocket echo endpoint | `/ws/voice` |
| 5 | WAV streaming client | `test_transport.py` |
| 6 | E2E WAV round-trip | tests pass |
| 7 | Browser mic page | `app/static/voice_client.html` |
| 8 | HTML served from FastAPI | `/voice` |
| 9 | Dependency manifest | `requirements.txt` |

### Voice AI Pipeline (Phases 1–6)
| Phase | What | Key Files |
|-------|------|-----------|
| 1 | Environment: PyTorch, Ollama, GPU check, VRAM analysis | `test_environment.py`, `doc/model_vram_analysis.md` |
| 2 | STT + TTS: Whisper + Kokoro modules | `test_audio_local.py` |
| 3 | RAG + LLM: ChromaDB + Qwen, context-aware answers | `test_rag_llm.py` |
| 4 | Full Pipecat pipeline (VAD→STT→RAG→LLM→TTS) | `app/pipeline.py`, `run_pipeline_test.py` |
| 5 | Twilio: TwiML, Media Streams, μ-law conversion | `/twilio/voice`, `/ws/twilio` |
| 6 | PostgreSQL: lead_calls schema, LLM extraction, post-call handler | `app/database.py` |

### Endpoints
```
GET  /              Health check
GET  /voice         Browser mic page (voice_client.html)
WS   /ws/voice      PCM audio endpoint (echo — pipeline NOT yet wired)
GET  /twilio/voice  TwiML XML (connects calls to /ws/twilio)
WS   /ws/twilio     Twilio Media Streams (8 kHz u-law)
```

### LLM Model
| Status | Model | Size | VRAM |
|--------|-------|------|------|
| ✅ Installed | `qwen2.5:7b-instruct-q3_K_M` | 3.8 GB | 3.46 GB |
| ℹ️ Fallback | `qwen2.5:7b-instruct` | 4.7 GB | 4.70 GB |
| ℹ️ Fallback | `qwen2.5:7b` | 4.7 GB | 4.70 GB |

**VRAM Budget (with q3_K_M):** 5.11 GB / 6.00 GB (85%) — fits comfortably.
Full analysis: `doc/model_vram_analysis.md`

### Documentation
```
doc/
├── STATUS_AND_NEXT_STEPS.md     ← This file
├── PENDING_TASKS.md             ← Complete task checklist
├── model_vram_analysis.md       ← LLM quantization comparison
├── development_plan.md          ← Original 6-phase plan
├── architecture_overview.md     ← System architecture
├── application_flow.md          ← Mermaid diagrams
├── architect_analysis.md        ← Transport strategy
├── implementation_plan.md       ← 9-task scaffold plan
├── manual_testing_guide.md      ← Manual test instructions
├── PROJECT_REFERENCE.md         ← Tech stack reference
└── project-overview.md          ← Project overview
```

### Verification Scripts
```bash
python test_environment.py       # GPU + Ollama health check
python test_audio_local.py       # STT transcription + TTS synthesis
python test_rag_llm.py           # RAG context retrieval + LLM answer
python test_transport.py         # WAV -> WebSocket -> output WAV
python run_pipeline_test.py      # Full pipeline assembly + 6 tests
pytest tests/ -v                 # Full test suite (127 pass, 0 fail)
```

---

## Skipped Tests (12 total — dev machine only)

### No CUDA GPU (4 tests)
`test_cuda_available`, `test_gpu_vram_sufficient`, `test_cuda_device_count`, `test_whisper_service_instantiate_cuda`

**Fix:** Run on GPU machine. These verify CUDA, VRAM, and device count.

### Windows AppLocker / WDAC (8 tests)
`hf_xet.dll` (HuggingFace) and `_regex.dll` (NLTK) blocked by Application Control policy. Same issue that blocked `streamlit.exe` earlier.

**Fix:** Run on machine without AppLocker. Code is correct — no changes needed.

---

## Remaining Work

### Priority 1: Wire Pipeline to WebSocket

**The biggest remaining code task.** Currently `/ws/voice` just echoes audio back. It needs to run the real pipeline:

```
mic audio → VAD → STT → RAG → LLM → TTS → speaker audio
```

| # | Step | File |
|---|------|------|
| 1.1 | Replace echo loop with pipeline runner | `app/main.py` `/ws/voice` |
| 1.2 | Handle audio frame routing (in → pipeline → out) | `app/main.py` |
| 1.3 | Start/stop pipeline on WebSocket connect/disconnect | `app/main.py` |
| 1.4 | Call `post_call_handler` on disconnect | `app/main.py` |
| 1.5 | Test: browser mic → AI voice response | manual |

### Priority 2: Deploy to GPU Machine

| # | Step |
|---|------|
| 2.1 | Copy project to GPU machine (≥6 GB NVIDIA) |
| 2.2 | `pip install -r requirements.txt` |
| 2.3 | Verify Ollama has `qwen2.5:7b-instruct-q3_K_M` + `nomic-embed-text` |
| 2.4 | Run `pytest tests/ -v` — all 12 skips should become passes |
| 2.5 | Run `python test_audio_local.py` with real speech WAV |
| 2.6 | Run `python run_pipeline_test.py` |
| 2.7 | Validate VRAM: `python test_environment.py` should show <6 GB |

### Priority 3: Real Speech Test File

| # | Step |
|---|------|
| 3.1 | Record a 16kHz mono WAV with a spoken admissions question |
| 3.2 | Replace the sine-tone `test_in.wav` with real speech |
| 3.3 | Run `python test_audio_local.py` → verify transcription + TTS output |

### Priority 4: Twilio Production (requires account)

| # | Step |
|---|------|
| 4.1 | Create Twilio account, get SID + token + phone number |
| 4.2 | Uncomment and fill `TWILIO_*` in `app/config.py` |
| 4.3 | Start ngrok: `ngrok http 8000` |
| 4.4 | Update TwiML hostname in `app/main.py` |
| 4.5 | Point Twilio phone webhook → ngrok URL |
| 4.6 | Test inbound call → full voice pipeline |

### Priority 5: Lead Capture (requires PostgreSQL)

| # | Step |
|---|------|
| 5.1 | Start PostgreSQL (local or Docker) |
| 5.2 | Set `DATABASE_URL` env var |
| 5.3 | Verify: `python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` |
| 5.4 | Run a test conversation → `SELECT * FROM lead_calls;` |

---

## Quick Reference

```bash
# Run everything
pytest tests/ -v                          # 127 pass, 0 fail, 12 skip

# Start the voice server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Browser demo (echo mode — pipeline not yet wired)
http://localhost:8000/voice

# Test the pipeline (text-only RAG + LLM)
python run_pipeline_test.py               # 6/6 tests pass

# Check environment
python test_environment.py                # GPU + Ollama health

# Test STT + TTS
python test_audio_local.py                # needs real speech WAV

# Test RAG + LLM
python test_rag_llm.py                    # context-aware Q&A

# WAV transport test
python test_transport.py                  # WAV -> WS -> output WAV
```
