# University Admissions Voice AI Assistant — Flow Diagrams

Open this file in VS Code with the "Markdown Preview Mermaid Support" extension,
or push to GitHub (renders natively).

---

## 1. System Context Diagram (Top-Level)

```mermaid
graph TB
    subgraph EXTERNAL["🌐 External World"]
        CALLER["📞 Phone Caller (Twilio PSTN)"]
        WHATSAPP["💬 WhatsApp User"]
        BROWSER["🖥️ Browser User (Streamlit / Voice Page)"]
        DEV["🧪 Developer (WAV Test Harness)"]
    end

    subgraph TUNNEL["🔒 Tunnel Layer"]
        CF["Cloudflare Tunnel (trycloudflare.com)"]
        NGROK["ngrok (for Twilio webhooks)"]
    end

    subgraph SERVER["⚡ FastAPI Server (app/main.py)"]
        HEALTH["GET / - Health Check"]
        VOICE_PAGE["GET /voice - Browser Mic Page"]
        WS_VOICE["WS /ws/voice - Audio Stream"]
        TWILIO_WEBHOOK["GET /twilio/voice - TwiML Response"]
        WS_TWILIO["WS /ws/twilio - Twilio Media Stream"]
    end

    subgraph PIPELINE["🎯 Pipecat Pipeline (app/pipeline.py)"]
        VAD["Silero VAD - Voice Activity Detection"]
        STT["Faster-Whisper - STT (CUDA INT8)"]
        RAG_ROUTER["Pipeline Router - Query to Vector Search"]
        LLM["Qwen 2.5 6B - LLM Generation (Ollama)"]
        TTS["Kokoro-82M - TTS (ONNX)"]
    end

    subgraph DATA_LAYER["💾 Data Layer"]
        CHROMA[("ChromaDB - Admissions Embeddings")]
        POSTGRES[("PostgreSQL - lead_calls (deferred)")]
        OLLAMA[("Ollama Server - localhost:11434")]
    end

    subgraph EXISTING["📦 Existing System (Tier 1 - Working)"]
        STREAMLIT["Streamlit UI (app.py) - Text Chat"]
        LANGCHAIN["LangChain RAG Chain"]
    end

    CALLER --> TWILIO_WEBHOOK
    WHATSAPP --> TWILIO_WEBHOOK
    BROWSER --> CF
    BROWSER --> VOICE_PAGE
    BROWSER --> STREAMLIT
    DEV --> WS_VOICE
    CF --> STREAMLIT
    NGROK --> SERVER
    TWILIO_WEBHOOK --> WS_TWILIO
    WS_TWILIO --> PIPELINE
    WS_VOICE --> PIPELINE
    STREAMLIT --> LANGCHAIN
    LANGCHAIN --> CHROMA
    LANGCHAIN --> OLLAMA
    PIPELINE --> CHROMA
    PIPELINE --> OLLAMA
    PIPELINE -.-> POSTGRES

    style EXISTING fill:#1a3a1a,stroke:#4caf50,color:#e0e0e0
    style PIPELINE fill:#1a1a3a,stroke:#5c6bc0,color:#e0e0e0
    style DATA_LAYER fill:#3a2a1a,stroke:#ff9800,color:#e0e0e0
    style SERVER fill:#2a1a3a,stroke:#9c27b0,color:#e0e0e0
```

---

## 2. Voice Pipeline — Detailed Sequence

```mermaid
sequenceDiagram
    actor User as Caller / Browser
    participant WS as WebSocket /ws/voice
    participant VAD as Silero VAD
    participant STT as Faster-Whisper (CUDA INT8)
    participant RAG as ChromaDB Vector Search
    participant LLM as Qwen 2.5 6B (Ollama)
    participant TTS as Kokoro-82M (ONNX CUDA)
    participant OUT as WebSocket Output

    User->>WS: Binary PCM Audio Frames (16kHz, mono, 16-bit)
    WS->>VAD: Stream audio frames

    loop Until speech pause (over 0.5s silence)
        VAD->>VAD: Buffer frames, detect voice activity
    end

    VAD->>STT: Speech segment audio bytes
    Note over STT: ~200ms inference, 0.8 GB VRAM
    STT->>RAG: Transcribed text query

    RAG->>RAG: Convert query to embedding vector
    RAG->>RAG: Cosine similarity search (k=3)
    RAG->>LLM: Query + Top-3 context + system prompt

    Note over LLM: num_ctx: 2048, ~4.0 GB VRAM
    LLM->>TTS: Streaming text response chunks

    Note over TTS: Sub-200ms generation, ~0.35 GB VRAM
    TTS->>OUT: PCM audio frames (16kHz)
    OUT->>User: Binary audio response
```

---

## 3. RAG Retrieval Flow (ChromaDB)

```mermaid
flowchart LR
    subgraph INDEXING["Indexing (One-Time)"]
        PDF["UMD+FDU Profile PDF (15 pages)"]
        LOAD["PyPDFLoader - Extract text"]
        SPLIT["RecursiveCharacterTextSplitter (chunk=800, overlap=150)"]
        EMBED["nomic-embed-text - Generate 768-dim vectors"]
        STORE[("ChromaDB - 50 chunks persisted")]
        PDF --> LOAD --> SPLIT --> EMBED --> STORE
    end

    subgraph QUERY["Query-Time Retrieval"]
        Q["Student asks: What is the tuition fee at UMD?"]
        Q_EMBED["OllamaEmbeddings - Vectorize query"]
        SEARCH["Cosine Similarity - Top-k=3 chunks"]
        CONTEXT["Retrieved Context: 1. Tuition $15,000/yr 2. Deadline Aug 1st 3. GPA 3.2 min"]
        Q --> Q_EMBED --> SEARCH --> CONTEXT
    end

    STORE --> SEARCH
    CONTEXT --> PROMPT["System Prompt: Role + Tone Rules + Context + Query"]
    PROMPT --> LLM_GEN["Qwen 2.5 6B - Generate Answer"]
```

---

## 4. Two-Phase Transport Strategy

```mermaid
flowchart TB
    subgraph PHASE_A1["Phase A1: WAV Test Harness (Zero Cost)"]
        WAV_IN["test_in.wav (16kHz mono PCM)"]
        CLIENT["test_transport.py: Parse WAV, stream 20ms chunks, receive TTS output"]
        WAV_OUT["test_out.wav (verify with media player)"]
        WAV_IN --> CLIENT --> WAV_OUT
    end

    subgraph PHASE_A2["Phase A2: Browser Mic Page (Live Demo)"]
        MIC["getUserMedia() - Browser Microphone"]
        PCM["AudioContext + ScriptProcessorNode - PCM 16kHz"]
        WS_CLIENT["WebSocket Client - connect /ws/voice"]
        SPEAKER["AudioContext.play() - TTS output to speakers"]
        MIC --> PCM --> WS_CLIENT --> SPEAKER
    end

    subgraph PHASE_B["Phase B: Twilio (Credentials Required)"]
        CALL["Inbound Phone Call - PSTN to Twilio"]
        TWIML["GET /twilio/voice - Return TwiML XML"]
        MEDIA_WS["WS /ws/twilio - 8kHz u-law stream"]
        CALL --> TWIML --> MEDIA_WS
    end

    subgraph PIPELINE["Shared Pipeline (Unchanged)"]
        CORE["VAD, STT, RAG, LLM, TTS (app/pipeline.py)"]
    end

    CLIENT -.-> WS_ENDPOINT["/ws/voice"]
    WS_CLIENT -.-> WS_ENDPOINT
    MEDIA_WS -.-> WS_TWILIO_EP["/ws/twilio"]
    WS_ENDPOINT --> PIPELINE
    WS_TWILIO_EP --> PIPELINE

    style PHASE_A1 fill:#1a3a1a,stroke:#4caf50,color:#e0e0e0
    style PHASE_A2 fill:#1a4a2a,stroke:#66bb6a,color:#e0e0e0
    style PHASE_B fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style PIPELINE fill:#1a1a3a,stroke:#5c6bc0,color:#e0e0e0
```

---

## 5. Task Execution Flow (Build Order)

```mermaid
flowchart TD
    T1["Task 1: app/__init__.py (Package scaffold)"] --> T2
    T2["Task 2: app/config.py (Transport settings)"] --> T3
    T3["Task 3: app/main.py (FastAPI + health check)"] --> T4
    T4["Task 4: WS /ws/voice (WebSocket echo endpoint)"] --> T5
    T4 --> T7

    T5["Task 5: test_transport.py (WAV streamer client)"] --> T6
    T6["Task 6: WAV E2E validation (Real audio round-trip)"]

    T7["Task 7: voice_client.html (Browser mic page)"] --> T8
    T8["Task 8: Serve HTML at /voice (Browser mic to echo test)"]

    T6 --> T9
    T8 --> T9
    T9["Task 9: requirements.txt (Final integration check)"]

    T9 --> DEPLOY{"Demo Ready"}

    style T1 fill:#1a2a3a,stroke:#5c6bc0,color:#e0e0e0
    style T2 fill:#1a2a3a,stroke:#5c6bc0,color:#e0e0e0
    style T3 fill:#1a2a3a,stroke:#5c6bc0,color:#e0e0e0
    style T4 fill:#2a1a3a,stroke:#9c27b0,color:#e0e0e0
    style T5 fill:#2a3a1a,stroke:#66bb6a,color:#e0e0e0
    style T6 fill:#2a3a1a,stroke:#66bb6a,color:#e0e0e0
    style T7 fill:#3a2a1a,stroke:#ff9800,color:#e0e0e0
    style T8 fill:#3a2a1a,stroke:#ff9800,color:#e0e0e0
    style T9 fill:#3a1a1a,stroke:#ef5350,color:#e0e0e0
    style DEPLOY fill:#1a3a1a,stroke:#4caf50,color:#e0e0e0
```

---

## 6. Memory Allocation Map (6 GB GPU)

```mermaid
pie title Peak VRAM Usage: 5.65 GB / 6.00 GB (94.2%)
    "CUDA Base Overhead (0.50 GB)" : 0.50
    "Qwen 2.5 6B Q4_K_M (4.00 GB)" : 4.00
    "Faster-Whisper small.en INT8 (0.80 GB)" : 0.80
    "Kokoro-82M ONNX (0.35 GB)" : 0.35
```

---

## 7. Component State Machine (Per Call)

```mermaid
stateDiagram-v2
    [*] --> Idle: Server started

    Idle --> Connecting: WebSocket handshake
    Connecting --> Listening: VAD active

    Listening --> Processing: Speech detected (over 0.5s pause)
    Processing --> Transcribing: STT running
    Transcribing --> Retrieving: Text query ready

    Retrieving --> Generating: Top-3 chunks + context
    Generating --> Synthesizing: LLM text streaming

    Synthesizing --> Playing: TTS frames ready
    Playing --> Listening: Response complete, wait for next input

    Listening --> Disconnecting: Client closes / hangup
    Processing --> Disconnecting: Error / timeout
    Generating --> Disconnecting: Error / OOM

    Disconnecting --> SavingState: Persist transcript + lead
    SavingState --> Idle: Cleanup complete
    Disconnecting --> Idle: Force cleanup (no DB)

    note right of Processing: GPU peak: ~5.65 GB
    note right of SavingState: Async: PostgreSQL INSERT
```

---

## 8. File & Directory Structure

```mermaid
graph TB
    ROOT["university_project_demo/"]

    subgraph DOCS["doc/"]
        ARCH["architecture_overview.md"]
        DEV["development_plan.md"]
        ANALYSIS["architect_analysis.md"]
        IMPL["implementation_plan.md"]
        TEST_GUIDE["manual_testing_guide.md"]
        FLOW["application_flow.md"]
    end

    subgraph APP_DIR["app/"]
        INIT["__init__.py"]
        CONFIG["config.py"]
        MAIN["main.py"]
        PIPELINE_FILE["pipeline.py (from vision docs)"]
        DB_FILE["database.py (deferred)"]
        subgraph STATIC["static/"]
            VOICE_HTML["voice_client.html"]
        end
    end

    subgraph TESTS["tests/"]
        CONF["conftest.py"]
        TP1["test_task1_package.py"]
        TP2["test_task2_config.py"]
        TP3["test_task3_fastapi.py"]
        TP4["test_task4_websocket.py"]
        TP5["test_task5_transport.py"]
        TP6["test_task6_e2e_wav.py"]
        TP7["test_task7_html.py"]
        TP8["test_task8_browser.py"]
        TP9["test_task9_requirements.py"]
        subgraph RESULTS["test_results/"]
            WAV_OUT["generated .wav files"]
        end
    end

    ROOT --> DOCS
    ROOT --> APP_DIR
    ROOT --> TESTS
    ROOT --> TRANSPORT["test_transport.py"]
    ROOT --> REQS["requirements.txt"]
    ROOT --> STREAMLIT_APP["app.py (existing Streamlit)"]
    ROOT --> BOT["admissions_bot.py (existing CLI)"]
    ROOT --> LAUNCH["launch.bat / launch_tunnel.bat"]
    ROOT --> CHROMA_DB["chroma_local_db/ (existing)"]
    ROOT --> SAMPLE_DATA["content/sample_data/ (existing)"]

    style APP_DIR fill:#2a1a3a,stroke:#9c27b0,color:#e0e0e0
    style TESTS fill:#1a3a2a,stroke:#4caf50,color:#e0e0e0
    style RESULTS fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style DOCS fill:#1a2a3a,stroke:#5c6bc0,color:#e0e0e0
```

---

## 9. Demo Day Flow

```mermaid
journey
    title Demo Day - University Admissions Voice Assistant
    section Setup (5 min)
        Launch Ollama: 1: Developer
        Run Streamlit app: 1: Developer
        Start FastAPI server: 1: Developer
        Open browser voice page: 1: Developer
    section Tier 1 Demo - Text Chat (10 min)
        Show Streamlit UI: 3: Developer and Audience
        Ask pre-prepared questions: 3: Audience
        Verify RAG accuracy: 3: Audience
        Share via Cloudflare tunnel: 3: Audience
    section Tier 2 Vision - Architecture (5 min)
        Show flow diagram: 4: Developer
        Explain voice pipeline: 4: Developer
        Show Twilio placeholder config: 4: Developer
    section Live Voice Demo (5 min)
        Open voice_client.html: 5: Developer
        Mic capture to WS to echo: 5: Audience
        Explain echo now AI pipeline next: 5: Developer
    section Q and A (5 min)
        Answer technical questions: 5: Developer and Audience
```
