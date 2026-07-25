# Architecture Overview — Local Voice AI Admissions Assistant

This architecture transitions a university admissions system into a fully local, zero-subscription AI pipeline. A 6 GB NVIDIA GPU handles the complete voice-to-voice turn sequentially (**STT → LLM Context → TTS**), while external communication remains bridged via WebSockets through Twilio / WhatsApp.

---

## System Architecture Diagram

```
                           [ USER INTERFACES ]
                    ┌──────────────────────────────┐
                    │ Twilio Voice / WhatsApp API  │
                    └──────────────┬───────────────┘
                                   │ HTTPS / WebSockets
                                   ▼
                       [ FASTAPI APPLICATION ]
                    ┌──────────────────────────────┐
                    │  Webhook & Gateway Handler   │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                       [ PIPECAT AI FRAMEWORK ]
    ┌─────────────────────────────────────────────────────────────┐
    │                       Pipeline Loop                         │
    │                                                             │
    │  1. Audio In ──► Silero VAD (Voice Activity Detection)      │
    │                          │                                  │
    │  2. Speech ────► Faster-Whisper (Local STT, CUDA INT8)     │
    │                          │                                  │
    │  3. Text ──────► Pipeline Router ──► ChromaDB Vector Search │
    │                                              │              │
    │  4. Prompt ────► Ollama / Qwen 2.5 6B (Local LLM)           │
    │                          │                                  │
    │  5. Response ──► Kokoro-82M (Local TTS, ONNX CUDA)          │
    │                          │                                  │
    │  6. Audio Out ─► Transport Output (Twilio WS / WhatsApp)    │
    └──────────────────────────────┬──────────────────────────────┘
                                   │ Async Updates
                                   ▼
                    [ POSTGRESQL & CHROMADB SYSTEM ]
           ┌───────────────────────┴───────────────────────┐
           │ PostgreSQL: Transcripts, Lead States & DB      │
           │ ChromaDB: Admissions Knowledge Embeddings     │
           └───────────────────────────────────────────────┘
```

---

## Technical Component Matrix

| Layer | Component | Hardware Target | Memory & Tech Specifications |
|---|---|---|---|
| **STT** | Faster-Whisper (`small.en`) | GPU (CUDA) | ~0.8 GB VRAM (INT8 quantized). Processes incoming user speech segments upon silence detection. |
| **LLM** | Qwen 2.5 6B / 7B | GPU (CUDA) | ~4.0 GB VRAM (Q4_K_M via Ollama). Formats prompts, handles RAG context, extracts lead state JSON. |
| **TTS** | Kokoro-82M | GPU / CPU | ~0.35 GB VRAM (ONNX engine). High-quality speech synthesis with sub-200ms streaming generation. |
| **VAD** | Silero VAD | CPU | < 50 MB RAM. Runs in lightweight threads to determine exact start/stop silence markers. |
| **Vector DB** | ChromaDB | CPU RAM | Stores embeddings for admissions guidelines, deadlines, and FAQ collections. |
| **Relational DB** | PostgreSQL | CPU RAM | Stores session logs, caller phone IDs, appointment states, and transcript history. |

---

## Unified Execution Sequence

To keep execution under the 6 GB VRAM budget, the pipeline processes tasks sequentially inside a streaming frame collector:

### Step 1: Inbound Processing & VAD
- Twilio/WhatsApp opens an inbound WebSocket stream to the FastAPI application.
- Pipecat streams incoming audio PCM frames into Silero VAD.
- Upon speech pause detection (>0.5s silence), audio is dispatched to STT.

### Step 2: Speech Recognition (STT)
- Faster-Whisper receives the audio segment.
- Transcribes speech to text in ~200ms using INT8 quantized CUDA tensors.

### Step 3: Retrieval-Augmented Generation (RAG)
- The extracted text query is converted to vector embeddings.
- ChromaDB performs cosine similarity search and returns top 2–3 relevant document chunks.
- Relevant chunks and recent conversation history are injected into the system prompt.

### Step 4: Local Generation (LLM)
- Query + Context is sent to local Ollama running Qwen 2.5 6B.
- Answers are generated using a restricted context window (`num_ctx: 2048`) to keep KV cache VRAM low.
- Streaming text frames emit directly to the TTS queue.

### Step 5: Speech Synthesis (TTS) & Outbound Audio
- Kokoro-82M receives streaming text chunks.
- Synthesizes output audio using ONNX.
- Pipecat resamples the generated audio stream into 8 kHz μ-law format and sends it over the WebSocket back to Twilio/WhatsApp.

---

## Hardware Memory Allocation Map (6 GB NVIDIA GPU)

| Service | Active VRAM Allocation | Mode |
|---|---|---|
| CUDA Base Overhead | ~0.50 GB | Static |
| Qwen 2.5 6B (Q4_K_M) | ~4.00 GB | Loaded permanently |
| Faster-Whisper (`small.en`) | ~0.80 GB | INT8 CUDA loaded |
| Kokoro-82M ONNX | ~0.35 GB | Dynamic CUDA/CPU allocation |
| **Peak Total VRAM** | **~5.65 GB / 6.00 GB** | **Within Safe Threshold (< 95%)** |

---

## Local Code Configuration (`app/pipeline.py`)

```python
import os
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.services.whisper import WhisperSTTService   # Local STT
from pipecat.services.ollama import OllamaLLMService     # Local LLM
from pipecat.services.kokoro import KokoroTTSService     # Local TTS
from pipecat.audio.vad.silero import SileroVADAnalyzer

async def create_local_voice_pipeline(transport):
    # 1. Local Voice Activity Detection
    vad = SileroVADAnalyzer()

    # 2. Local STT - Faster-Whisper on CUDA (INT8 to save VRAM)
    stt = WhisperSTTService(
        device="cuda",
        compute_type="int8",
        settings=WhisperSTTService.Settings(
            model="small.en"
        )
    )

    # 3. Local LLM - Qwen 2.5 6B via local Ollama
    llm = OllamaLLMService(
        model="qwen2.5:6b-instruct-q4_K_M",
        url="http://localhost:11434"
    )

    # 4. Local TTS - Kokoro-82M (ONNX Engine)
    tts = KokoroTTSService(
        settings=KokoroTTSService.Settings(
            voice="af_heart"
        )
    )

    # 5. Build the Pipecat Pipeline Flow
    pipeline = Pipeline([
        transport.input(),  # Receive raw audio from WebSocket
        stt,                # Audio -> Text
        llm,                # Text + RAG -> Agent Answer
        tts,                # Agent Answer -> Speech Audio
        transport.output()  # Speech Audio -> Outbound WebSocket
    ])

    task = PipelineTask(pipeline, params=PipelineTask.Params(allow_interruptions=True))
    runner = PipelineRunner()

    return runner, task
```

---

## Operational Safeguards for 6 GB GPUs

1. **Context Cap (`num_ctx`)**: Set Ollama's `num_ctx` parameter to **2048** or **3072**. Higher context windows (e.g., 8192) expand memory consumption during execution and can trigger out-of-memory errors.

2. **Top-K Chunk Limit**: Limit vector search retrieval to **K=2** or **K=3** chunks. This provides enough admissions detail without overloading the LLM context.

3. **Audio Resampling**: Ensure Kokoro's output is automatically resampled to **8000 Hz μ-law**, matching the standard telecommunication sample rate used by phone carriers.

---

## Quick Reference

| Setting | Value |
|---|---|
| GPU VRAM Budget | 6 GB NVIDIA CUDA |
| Ollama Model | `qwen2.5:6b-instruct-q4_K_M` |
| Ollama Context Window | `num_ctx: 2048` |
| Whisper Model | `small.en`, INT8 |
| TTS Voice | `af_heart` |
| VAD Silence Threshold | >0.5s |
| ChromaDB Top-K | 2–3 chunks |
| Audio Output Format | 8000 Hz μ-law |
