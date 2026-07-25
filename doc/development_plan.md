# Local Voice AI Assistant — Development & Testing Plan

A step-by-step build plan for an open-source, local voice AI assistant for university admissions. Each phase is self-contained — test independently before moving on.

**Tech Stack:** Pipecat · Ollama (Qwen 2.5 6B) · Faster-Whisper · Kokoro TTS · ChromaDB · PostgreSQL · FastAPI · Twilio

**Architecture:** `Audio In → STT (Whisper) → RAG (ChromaDB) → LLM (Qwen) → TTS (Kokoro) → Audio Out`

---

## Phase 1: Environment & Dependency Setup

### Goal
Set up Python environment, verify 6 GB GPU CUDA visibility, and pull required models locally.

### Prompt for AI Code Assistant

Create a robust `requirements.txt` file including:

- `pipecat-ai[whisper,kokoro]`
- `ollama`
- `chromadb`
- `psycopg2-binary`
- `torch` (with CUDA 12 support)
- `fastapi`, `uvicorn`, `websockets`

Also, write a Python verification script `test_environment.py` that:

1. Checks if PyTorch detects an NVIDIA CUDA GPU and prints total/available VRAM.
2. Checks if Ollama service is reachable on `http://localhost:11434` and verifies `qwen2.5:6b-instruct-q4_K_M` is pulled.

### How to Test Manually

Pull the LLM via CLI:

```bash
ollama pull qwen2.5:6b-instruct-q4_K_M
```

Run the environment verification script:

```bash
python test_environment.py
```

### Pass Criteria
- Output confirms CUDA GPU visible (>5.5 GB detected)
- Ollama connection successful

---

## Phase 2: Speech-to-Text (STT) & Text-to-Speech (TTS) Modules

### Goal
Instantiate local Whisper STT and Kokoro TTS models inside Pipecat service instances and verify audio input/output processing.

### Prompt for AI Code Assistant

Write a script `test_audio_local.py` using Pipecat components:

1. Initialize `WhisperSTTService` using model `small.en` with device `cuda` and compute_type `int8` to optimize VRAM.
2. Initialize `KokoroTTSService` using ONNX with voice `af_heart`.
3. Create a test function that:
   - Takes a 5-second sample WAV file (`test_in.wav`) containing speech and transcribes it to text using Whisper.
   - Takes the transcribed string, synthesizes audio using Kokoro TTS, and saves the output as `test_out.wav`.

Include clean error handling and print memory usage (VRAM) before and after execution.

### How to Test Manually

Record a short audio file (`test_in.wav`) or download an example 16 kHz mono WAV file. Run the audio engine test:

```bash
python test_audio_local.py
```

Play `test_out.wav` using any media player.

### Pass Criteria
- Speech is accurately recognized from `test_in.wav`
- `test_out.wav` plays back clear synthesized voice

---

## Phase 3: RAG Retrieval & Ollama LLM Execution

### Goal
Connect local ChromaDB vector search with Ollama Qwen 2.5 6B for contextual university admissions responses.

### Prompt for AI Code Assistant

Write a script `test_rag_llm.py` that demonstrates RAG with Ollama:

1. Initialize ChromaDB (in-memory or persistent) and populate a collection named `"admissions"` with 3 sample context documents:
   - *"Undergraduate tuition fee for 2026 is $15,000 per year. Application deadline is August 1st."*
   - *"Computer Science requires a minimum GPA of 3.2 and SAT score of 1200."*
   - *"International students must submit TOEFL scores above 80 or IELTS above 6.5."*
2. Define a function `query_admissions_bot(user_query: str)` that:
   - Searches ChromaDB for top 2 relevant chunks.
   - Injects the context into a concise prompt for Qwen 2.5 6B (`qwen2.5:6b-instruct-q4_K_M`) via Ollama.
   - Sets Ollama `num_ctx: 2048` to preserve GPU memory.
   - Returns the generated answer string.

### How to Test Manually

Run the test script directly from terminal:

```bash
python test_rag_llm.py
```

**Query tested:** *"What is the tuition fee and deadline?"*

### Pass Criteria
- LLM responds specifically with **"$15,000 per year"** and **"August 1st"** based on the ChromaDB context
- Uses ~4.0 GB VRAM

---

## Phase 4: Full Pipecat Voice Pipeline

### Goal
Chain **STT → RAG → LLM → TTS** into a unified, streaming Pipecat pipeline loop.

### Prompt for AI Code Assistant

Create `app/pipeline.py` containing a complete local Pipecat pipeline runner:

1. Set up `SileroVADAnalyzer` for silence detection.
2. Initialize `WhisperSTTService` (CUDA, INT8, `small.en`).
3. Initialize `OllamaLLMService` pointing to Qwen 2.5 6B.
4. Initialize `KokoroTTSService` for ONNX execution on CUDA.
5. Combine them into a Pipecat `Pipeline([vad, stt, llm, tts])`.
6. Add a custom context processor step where the STT output query is enriched with retrieved ChromaDB documents **before** hitting the LLM.
7. Write a runner script `run_pipeline_test.py` that feeds mock PCM audio frames into the pipeline and listens for output audio frames.

### How to Test Manually

Run the end-to-end local test harness:

```bash
python run_pipeline_test.py
```

### Pass Criteria
System logs confirm the full chain:

```
audio frame received → Whisper transcript emitted → RAG context appended → Qwen 2.5 text streamed → Kokoro TTS audio generated
```

---

## Phase 5: FastAPI Webhooks & Telephony Integration

### Goal
Expose a FastAPI WebSocket server that handles incoming raw audio calls from Twilio or WhatsApp WebSockets.

### Prompt for AI Code Assistant

Write a FastAPI server `app/main.py` that integrates Pipecat with Twilio WebSockets:

1. Expose a **GET** `/twilio/voice` HTTP endpoint returning TwiML that connects calls to a WebSocket endpoint (`/ws/twilio`).
2. Expose a **WebSocket** endpoint `/ws/twilio` that initializes a Pipecat `FastAPIWebsocketTransport`.
3. Route incoming audio frames into the `app/pipeline.py` pipeline.
4. Convert Kokoro TTS output audio streams into 8 kHz μ-law format before sending frames back over the Twilio WebSocket.
5. Include graceful WebSocket connection closing and GPU VRAM memory cleanup on call hangup.

### How to Test Manually

Start the FastAPI server:

```bash
uvicorn app.main:app --port 8000
```

Start an ngrok tunnel:

```bash
ngrok http 8000
```

Test using a WebSocket client tool (or WebSockets in Postman) — connect to `ws://localhost:8000/ws/twilio` and send test base64 audio frames.

### Pass Criteria
- Server responds with active WebSocket audio frame stream
- No memory leaks or crash errors

---

## Phase 6: System State & Lead Capture Verification

### Goal
Log conversation transcripts and extract candidate details (Name, Email, Course Interest) into PostgreSQL.

### Prompt for AI Code Assistant

Create `app/database.py` and update `app/pipeline.py`:

1. Define PostgreSQL schema for `lead_calls`:

| Column | Type |
|---|---|
| `id` | UUID |
| `phone_number` | VARCHAR |
| `transcript` | JSONB / TEXT |
| `extracted_lead` | JSONB (name, email, target_program) |

2. Add a post-call handler in Pipecat that runs when the WebSocket disconnects:
   - Takes full call transcript log.
   - Asynchronously calls Qwen 2.5 6B with a JSON-extraction prompt: *"Extract candidate Name, Email, and Program from transcript."*
   - Saves call record and lead payload into PostgreSQL database.

### How to Test Manually

1. Trigger a simulated multi-turn conversation via the WebSocket test script.
2. Disconnect the session.
3. Query PostgreSQL:

```sql
SELECT * FROM lead_calls ORDER BY created_at DESC LIMIT 1;
```

### Pass Criteria
- Database contains the full call transcript
- Correctly parsed JSON metadata (name, email, program) extracted by the local model

---

## Module Dependency Map

```
Phase 1 (Environment)
   │
   ▼
Phase 2 (STT + TTS) ──┐
   │                   │
   ▼                   ▼
Phase 3 (RAG + LLM)   │
   │                   │
   └───────┬───────────┘
           ▼
   Phase 4 (Full Pipeline)
           │
           ▼
   Phase 5 (FastAPI + Twilio)
           │
           ▼
   Phase 6 (Database + Lead Capture)
```

## Key Configuration Summary

| Component | Model / Setting | Purpose |
|---|---|---|
| GPU | ≥6 GB NVIDIA CUDA | All local inference |
| Whisper | `small.en`, CUDA, INT8 | Speech-to-text |
| Kokoro TTS | ONNX, voice `af_heart` | Text-to-speech |
| Ollama | `qwen2.5:6b-instruct-q4_K_M` | LLM reasoning |
| ChromaDB | Persistent, collection `admissions` | RAG vector storage |
| Ollama Context | `num_ctx: 2048` | VRAM budget control |
| Twilio Audio | 8 kHz μ-law | Telephony output format |
