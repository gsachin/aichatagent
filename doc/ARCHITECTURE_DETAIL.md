# Cloud Infrastructure Architecture — Detailed Breakdown

**Document version:** 1.0
**Date:** 2026-08-02
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`

---

## Architecture Diagram — Request Flow

```
                          ┌──────────────────────────┐
                          │    THE INTERNET           │
                          │                          │
        📞 Phone Call ────┤  Twilio PSTN             │
        📱 WhatsApp ──────┤  Twilio API              │
        💻 Browser ───────┤  Streamlit Dashboard     │
                          └──────────┬───────────────┘
                                     │
                                     │ HTTPS (443)
                                     │
                          ┌──────────▼───────────────┐
                          │  CLOUDFLARE NAMED TUNNEL  │
                          │  admissions.yourdomain.com│
                          │  (Permanent, authenticated)│
                          └──────────┬───────────────┘
                                     │
                                     │ Internal tunnel (no public IP needed)
                                     │
┌────────────────────────────────────▼─────────────────────────────────────┐
│                         RUNPOD GPU INSTANCE                               │
│                      (A100 80GB or RTX 4090 24GB)                        │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                    DOCKER COMPOSE ORCHESTRATOR                       │  │
│  │                                                                      │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │  │
│  │  │  cloudflared │  │   NGINX      │  │   FastAPI (×2 workers)     │ │  │
│  │  │  (tunnel     │  │  (optional   │  │   Port 8000                │ │  │
│  │  │   endpoint)  │  │   reverse    │  │   app.main:app             │ │  │
│  │  └──────┬───────┘  │   proxy)     │  └──────────┬─────────────────┘ │  │
│  │         │          └──────┬───────┘             │                    │  │
│  │         │                 │                     │                    │  │
│  │         └─────────────────┴─────────────────────┘                    │  │
│  │                           │                                          │  │
│  │         ┌─────────────────┼─────────────────────┐                    │  │
│  │         │                 │                     │                    │  │
│  │         ▼                 ▼                     ▼                    │  │
│  │  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐           │  │
│  │  │  vLLM      │  │  Whisper     │  │  Kokoro TTS        │           │  │
│  │  │  Server    │  │  STT Engine  │  │  Engine            │           │  │
│  │  │  Port 8080 │  │  (in-process)│  │  (in-process)      │           │  │
│  │  │            │  │              │  │                    │           │  │
│  │  │  Qwen 7B   │  │  faster-     │  │  Kokoro ONNX       │           │  │
│  │  │  Q4_K_M    │  │  whisper     │  │  CUDA Provider     │           │  │
│  │  │            │  │  small.en    │  │  LRU Cache(100)    │           │  │
│  │  │  OpenAI    │  │  INT8 CUDA   │  │  Voice: af_heart   │           │  │
│  │  │  API       │  │              │  │                    │           │  │
│  │  └─────┬──────┘  └──────┬───────┘  └────────┬───────────┘           │  │
│  │        │                │                    │                       │  │
│  │        └────────────────┼────────────────────┘                       │  │
│  │                         │                                            │  │
│  │                         ▼                                            │  │
│  │  ┌──────────────────────────────────────────────┐                    │  │
│  │  │              SHARED GPU MEMORY                │                    │  │
│  │  │          (A100 80GB or RTX 4090 24GB)         │                    │  │
│  │  │                                              │                    │  │
│  │  │  ┌─────────┐ ┌─────────┐ ┌───────┐ ┌──────┐  │                    │  │
│  │  │  │ LLM     │ │ KV      │ │ STT   │ │ TTS  │  │                    │  │
│  │  │  │ Weights │ │ Cache   │ │ Model │ │Model │  │                    │  │
│  │  │  │ 4.8 GB  │ │ ×7users │ │ 0.8GB │ │0.35GB│  │                    │  │
│  │  │  └─────────┘ └─────────┘ └───────┘ └──────┘  │                    │  │
│  │  └──────────────────────────────────────────────┘                    │  │
│  │                                                                      │  │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐    │  │
│  │  │  ChromaDB       │  │  PostgreSQL      │  │  Streamlit       │    │  │
│  │  │  Vector Store   │  │  pgvector        │  │  Dashboard       │    │  │
│  │  │  (persistent)   │  │  Port 5432       │  │  Port 8501       │    │  │
│  │  └─────────────────┘  └──────────────────┘  └──────────────────┘    │  │
│  │                                                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Component 1: GPU Instance (RunPod)

### What It Is

A cloud virtual machine with a dedicated NVIDIA GPU, rented by the hour. Think of it as renting a powerful computer in a data center instead of using your local PC.

### Why RunPod Over AWS/Azure/GCP

| Factor | RunPod | AWS (g5.xlarge) | Lambda Labs |
|--------|--------|-----------------|-------------|
| GPU options | RTX 4090, A100, H100 | A10G, A100 | A100, H100 |
| Price (A100/hr) | $1.99 | $3.71 | $1.99 |
| Setup complexity | Low (pre-built templates) | High (VPC, IAM, etc.) | Low |
| Persistent storage | Yes ($0.15/GB) | Yes (EBS) | Yes |
| GPU availability | Usually good | Often limited | Good |
| Auto-shutdown | Built-in | Must configure | Built-in |
| Docker support | Native | Native | Native |

### Two Instance Options

#### Option A: RTX 4090 (24 GB VRAM) — Budget Tier

```
┌────────────────────────────────────┐
│  RUNPOD RTX 4090 POD               │
│────────────────────────────────────│
│  GPU:  NVIDIA RTX 4090             │
│  VRAM: 24 GB GDDR6X                │
│  vCPU: 16 cores                    │
│  RAM:  62 GB                       │
│  Disk: 100 GB SSD                  │
│  BW:   10 Gbps                     │
│────────────────────────────────────│
│  Cost: $0.79/hr (~$570/month 24/7) │
│        $0.39/hr (spot/interrupt)    │
│────────────────────────────────────│
│  Concurrent Users: 5–15            │
│  Fit: Tight but works              │
└────────────────────────────────────┘
```

#### Option B: A100 SXM (80 GB VRAM) — Recommended Tier

```
┌────────────────────────────────────┐
│  RUNPOD A100 SXM POD               │
│────────────────────────────────────│
│  GPU:  NVIDIA A100 SXM             │
│  VRAM: 80 GB HBM2e                 │
│  vCPU: 24 cores                    │
│  RAM:  125 GB                      │
│  Disk: 100 GB SSD                  │
│  BW:   12 Gbps                     │
│────────────────────────────────────│
│  Cost: $1.99/hr (~$1,523/month)    │
│        $0.89/hr (spot)              │
│────────────────────────────────────│
│  Concurrent Users: 10–50+          │
│  Fit: Comfortable, room to grow    │
└────────────────────────────────────┘
```

### How It Works in Practice

1. **Create Pod:** Choose GPU type → pick Docker template → set disk size → launch
2. **SSH Access:** `ssh -p 22 user@pod-ip` or use RunPod's web terminal
3. **Deploy:** `git clone` the project → set env vars → `docker compose up -d`
4. **Monitor:** RunPod dashboard shows GPU utilization, VRAM usage, uptime
5. **Stop:** Pod stops when you shut it down. Storage persists (you pay only for storage when stopped)
6. **Auto-start:** Schedule pod to start/stop at specific times (e.g., 8 AM–8 PM)

### Cost Optimization — Scheduled Runtime

```
┌─────────────────────────────────────────────────────────────┐
│                    WEEKLY SCHEDULE                           │
│                                                             │
│  Mon    ████████████░░░░░░░░░░░░  8 AM – 8 PM  (12 hrs)    │
│  Tue    ████████████░░░░░░░░░░░░  8 AM – 8 PM  (12 hrs)    │
│  Wed    ████████████░░░░░░░░░░░░  8 AM – 8 PM  (12 hrs)    │
│  Thu    ████████████░░░░░░░░░░░░  8 AM – 8 PM  (12 hrs)    │
│  Fri    ████████████░░░░░░░░░░░░  8 AM – 8 PM  (12 hrs)    │
│  Sat    ░░░░░░░░░░░░░░░░░░░░░░░░  OFF                      │
│  Sun    ░░░░░░░░░░░░░░░░░░░░░░░░  OFF                      │
│                                                             │
│  Total: 60 hrs/week × 4.3 weeks = 258 hrs/month             │
│  Cost (A100): 258 × $1.99 = ~$513/month                      │
│  Cost (RTX 4090): 258 × $0.79 = ~$204/month                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Component 2: vLLM — LLM Inference Server

### What It Replaces

```
BEFORE (Ollama — Local Dev)          AFTER (vLLM — Production)
─────────────────────────────        ──────────────────────────
Single request at a time             Continuous batching (20+ concurrent)
~10 tokens/sec                        ~50–100 tokens/sec total
No memory sharing                    PagedAttention KV cache sharing
Proprietary API                      OpenAI-compatible /v1/chat/completions
~40% GPU utilization                  ~90% GPU utilization
Cold start on first query            Model pre-loaded, always warm
```

### Why vLLM Instead of Ollama

Ollama is designed for **local development** — one user, one query at a time. When 5-7 callers ask questions simultaneously, Ollama processes them **one after another** (sequential queue). The 5th caller waits for callers 1–4 to finish before their LLM even starts.

vLLM is designed for **production serving** — it uses:

**Continuous Batching:**
```
Time ──────────────────────────────────────────────────────►
      Ollama (Sequential)
      [── Query 1 ──][── Query 2 ──][── Query 3 ──]...
      
      vLLM (Continuous Batching)  
      [── Query 1 ──────────────────────]
         [── Query 2 ──────────────]
            [── Query 3 ────────────────────]
               [── Query 4 ──────]
      All queries process IN PARALLEL on the same GPU
```

**PagedAttention:** Instead of each caller having their own separate KV cache (wasting VRAM), PagedAttention shares memory pages across calls. This is the difference between fitting 5 callers vs 20 callers in the same VRAM.

### How It Integrates

vLLM exposes an **OpenAI-compatible API** at `http://localhost:8080/v1`. The FastAPI backend calls it just like calling OpenAI:

```python
# FastAPI → vLLM (OpenAI-compatible, no API key needed)
from openai import OpenAI

client = OpenAI(base_url="http://vllm:8080/v1", api_key="not-needed")

# Streaming response — token by token
stream = client.chat.completions.create(
    model="Qwen/Qwen2.5-7B-Instruct",
    messages=[
        {"role": "system", "content": "You are an admissions counselor..."},
        {"role": "user", "content": "What's the MBA tuition?"}
    ],
    stream=True,           # ← Tokens arrive as they're generated
    max_tokens=512,
    temperature=0.7,
)

for chunk in stream:
    token = chunk.choices[0].delta.content
    # Send token to TTS as sentences complete
```

### Docker Configuration

```yaml
vllm:
  image: vllm/vllm-openai:latest
  runtime: nvidia
  ports:
    - "8080:8080"
  command: >
    --model Qwen/Qwen2.5-7B-Instruct
    --quantization awq              # 4-bit quantized, fits in 4.8 GB
    --max-model-len 4096            # 4096 token context window
    --gpu-memory-utilization 0.75   # Use 75% of GPU VRAM (60GB on A100)
    --max-num-seqs 20               # Up to 20 concurrent sequences
    --port 8080
  volumes:
    - vllm_cache:/root/.cache/huggingface  # Cache downloaded model
```

### VRAM Breakdown Inside vLLM

```
A100 (80 GB VRAM)
┌────────────────────────────────────┐
│  Qwen 7B Weights (AWQ 4-bit) 4.8GB│  ← Loaded once, shared
│  KV Cache Pool              45 GB │  ← PagedAttention (shared pool)
│  CUDA Context + Overhead     5 GB │
│  Free / Buffer               25 GB │  ← Room for more users
│────────────────────────────────────│
│  Total Used                  55 GB │
└────────────────────────────────────┘

Each concurrent user needs ~0.75 GB KV cache for 4096-token context.
45 GB pool ÷ 0.75 GB = 60 concurrent sequences possible (we limit to 20).
```

---

## Component 3: faster-whisper small.en — Speech-to-Text

### What It Replaces

```
BEFORE                                AFTER
───────────────────────────           ───────────────────────
openai-whisper base (74M params)      faster-whisper small.en (244M params)
CPU inference (2–4 seconds)           CUDA INT8 inference (0.5–1 second)
fp32 (slow, accurate)                 int8 (fast, ~same accuracy)
~75% accuracy on phone audio          ~92% accuracy on phone audio
Blocked by AppLocker (local dev)      Works on cloud (no AppLocker!)
```

### Why faster-whisper

faster-whisper uses **CTranslate2** — a dedicated inference engine that converts the Whisper model to an optimized format. This gives:

- **4× faster** than openai-whisper on the same hardware
- **INT8 quantization** — same quality, half the VRAM
- **Beam search** — tests multiple transcription hypotheses, picks the best
- **VAD filter built-in** — automatically skips silence, cleaner output
- **No PyTorch dependency** — works in Docker without PyTorch overhead

### How It Integrates

STT runs **in-process** inside the FastAPI worker (not as a separate service). Why? Whisper inference is fast enough (0.5–1s) that a dedicated service adds network overhead without benefit.

```python
# In app/voice_handler.py — runs inside FastAPI process
from faster_whisper import WhisperModel

model = WhisperModel(
    "small.en",           # 244M params, English-optimized
    device="cuda",        # GPU inference
    compute_type="int8",  # Quantized, 0.8 GB VRAM
)

# Phone audio arrives as numpy array (8kHz μ-law → 16kHz PCM)
segments, info = model.transcribe(
    audio_numpy,
    language="en",
    beam_size=5,          # Better accuracy through beam search
    vad_filter=True,      # Auto-detect speech vs silence
)

transcript = " ".join(seg.text for seg in segments)
# "Tell me about FDU tuition fees" ← accurate!
```

### VRAM: 0.8 GB (shared across all calls)

Whisper processes one utterance at a time (the model is small and fast enough). Multiple concurrent calls queue for STT — since each STT takes <1 second, 7 callers experience near-zero wait.

---

## Component 4: Kokoro ONNX on CUDA — Text-to-Speech

### What Changes

```
BEFORE                                AFTER
────────────────────────────           ────────────────────────
Kokoro ONNX on CPU                    Kokoro ONNX on CUDA GPU
15–30 seconds per response            2–5 seconds per response
Full response at once                 Sentence-by-sentence streaming
No caching                            LRU cache (100 common phrases)
Fixed speed (1.0)                     Variable speed (0.95–1.05)
Monotone voice                        Natural pauses between sentences
af_heart only                         Configurable voice selection
```

### Why Kokoro Stays (Not Replaced)

Kokoro ONNX is actually a **very good** TTS model — 82M params, produces warm natural speech. The problem was never the model, it was:

1. **Running on CPU** (CUDA providers installed but never wired)
2. **No caching** (same greeting synthesized fresh every call)
3. **No streaming** (waiting 30 seconds before sending ANY audio)

Fixing these three things brings Kokoro from "unusable" to "production-ready" without changing the model.

### How CUDA Wiring Works

```python
import onnxruntime as ort

# Check available execution providers
print(ort.get_available_providers())
# Before fix: ['CPUExecutionProvider']
# After fix:  ['CUDAExecutionProvider', 'CPUExecutionProvider']

# Force CUDA
session_options = ort.SessionOptions()
session = ort.InferenceSession(
    "kokoro-v1.0.onnx",
    providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
)
# Now Kokoro runs on GPU — 5–10× faster!
```

### How Caching Works

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def tts_cached(text: str, voice: str) -> bytes:
    """First call: synthesize (2–5s). Subsequent calls: instant."""
    return _synthesise_raw(text, voice)

# Before: Every call synthesizes "Hi, I'm from admissions..." — wasteful
# After: First call synthesizes it, 100 subsequent calls replay instantly
```

Common cached phrases:
- Greetings ("Hi, I'm calling from University Admissions...")
- Program descriptions ("The MBA program is a 2-year...")
- Fee answers ("UMD MBA tuition is $45,000 per year...")
- Closings ("Thank you for your time. We'll follow up...")

### How Streaming Works

```
Before (Current — Full Synthesis Then Play):
  LLM completes full response ──→ TTS 30 seconds ──→ Play all audio
  Caller hears: [30 seconds of silence] [response plays]

After (Target — Sentence Streaming):
  LLM token: "UMD"
  LLM token: "MBA"
  LLM token: "tuition"
  LLM token: "is"
  LLM token: "$45,000." ← SENTENCE END → TTS chunk (2s) → Play NOW
  LLM token: "Scholarships"
  LLM token: "are..."
  LLM token: "available." ← SENTENCE END → TTS chunk (1.5s) → Play NOW
  
  Caller hears: [2 seconds of silence] "UMD MBA tuition is $45,000." [pause] "Scholarships are available."
```

### VRAM: 0.35 GB (shared across all calls)

Kokoro is small — 325 MB ONNX model. Like Whisper, it processes sequentially (fast enough at 2–5s per sentence that concurrent users don't queue).

---

## Component 5: PostgreSQL with pgvector

### What It Stores

```
┌──────────────────────────────────────────┐
│           POSTGRESQL DATABASE             │
│           (pgvector/pgvector:pg16)        │
│──────────────────────────────────────────│
│                                           │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  │
│  │ LEADS   │  │CONVERSAT-│  │CALL     │  │
│  │         │  │IONS      │  │QUEUE    │  │
│  │ name    │  │ transcript│  │ status  │  │
│  │ phone   │  │ channel  │  │ call_sid│  │
│  │ status  │  │ outcome  │  │ attempts│  │
│  │ program │  │ duration │  │ lead_id │  │
│  │ score   │  │ sentiment│  └─────────┘  │
│  └─────────┘  └──────────┘               │
│                                           │
│  ┌──────────┐                             │
│  │FOLLOW-UPS│    Total: 5 tables          │
│  │ schedule │    ~50 MB for 10K leads     │
│  │ type     │    ~200 MB for 100K convos  │
│  │ status   │                             │
│  └──────────┘                             │
│                                           │
│  Storage: Persistent Docker volume        │
│  Backup:  Daily pg_dump to /backups/      │
└──────────────────────────────────────────┘
```

### Why Container on Same Instance (Not Separate DB Service)

For 5–50 concurrent users, PostgreSQL uses negligible CPU/RAM (~200 MB, <5% CPU). Running it as a container on the GPU instance avoids:
- Extra monthly cost for a managed DB (AWS RDS: ~$50/month minimum)
- Network latency between app server and DB
- Complexity of managing a separate service

For 100+ users, upgrade to a managed database (AWS RDS or Supabase).

### Docker Configuration

```yaml
postgres:
  image: pgvector/pgvector:pg16       # PostgreSQL 16 with vector extension
  environment:
    POSTGRES_USER: admissions
    POSTGRES_PASSWORD: ${DB_PASSWORD}
    POSTGRES_DB: admissions
  volumes:
    - postgres_data:/var/lib/postgresql/data  # Persistent storage
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U admissions"]
    interval: 10s
```

---

## Component 6: Cloudflare Named Tunnel

### What It Replaces

```
BEFORE (Quick Tunnel — Current)       AFTER (Named Tunnel — Production)
────────────────────────────────      ─────────────────────────────────
Random URL every restart               Permanent URL (never changes)
adf3-xk9m.trycloudflare.com           admissions.yourdomain.com
No authentication                      Authenticated (token-based)
Best-effort uptime                     Production-grade uptime
Manual Twilio reconfig each restart    Set Twilio webhook ONCE
DNS failures observed (exit 127)       Stable, monitored
```

### How to Set Up (One-Time)

```bash
# Step 1: Install cloudflared on your local machine
# (already installed on your Windows PC)

# Step 2: Authenticate with Cloudflare
cloudflared tunnel login
# This opens a browser → choose your domain → creates cert.pem

# Step 3: Create a named tunnel
cloudflared tunnel create admissions-tunnel
# Returns: Tunnel ID + credentials JSON at ~/.cloudflared/<id>.json

# Step 4: Configure DNS
cloudflared tunnel route dns admissions-tunnel admissions.yourdomain.com
# Now admissions.yourdomain.com points to your tunnel

# Step 5: Get the token for Docker
cloudflared tunnel token admissions-tunnel
# Copy this token → paste into .env as CLOUDFLARED_TUNNEL_TOKEN

# Step 6: Set Twilio webhooks ONCE (never change again)
# Voice:   https://admissions.yourdomain.com/twilio/voice
# WhatsApp: https://admissions.yourdomain.com/twilio/whatsapp
# Status:   https://admissions.yourdomain.com/twilio/outbound/status
```

### Docker Configuration

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel run --token ${CLOUDFLARED_TUNNEL_TOKEN}
  restart: unless-stopped        # Auto-restart if it crashes
```

**No port mapping needed** — cloudflared connects OUT to Cloudflare's edge, not the other way around. Your instance doesn't need a public IP at all.

---

## Component 7: Docker Compose Orchestration

### How All 6 Services Work Together

```
docker compose up -d
│
├── 1. postgres starts first (healthcheck: pg_isready)
│      └── Creates admissions database + tables on first run
│
├── 2. vllm starts (pulls Qwen 7B model on first run, ~15 min download)
│      └── healthcheck: curl localhost:8080/health
│      └── Exposes OpenAI-compatible API on port 8080
│
├── 3. fastapi starts (depends on postgres + vllm being healthy)
│      └── 2 uvicorn workers (handles ~7 concurrent WS connections each)
│      └── Connects to vllm:8080 for LLM
│      └── Loads whisper + kokoro in-process
│      └── Exposes port 8000
│
├── 4. streamlit starts (depends on fastapi)
│      └── Dashboard on port 8501
│      └── Connects to fastapi:8000 for API data
│
├── 5. cloudflared starts (connects to Cloudflare edge)
│      └── Routes admissions.yourdomain.com → fastapi:8000
│      └── Auto-restarts if connection drops
│
└── ALL SERVICES HEALTHY — ready for calls
```

### Service Dependency Chain

```
postgres ◄── vllm ◄── fastapi ◄── streamlit
                         ▲
                         │
                    cloudflared (external → internal routing)
```

### Startup Time

| Phase | Time | What Happens |
|-------|------|-------------|
| Docker pull images | 2–5 min | First run only — images cached after |
| vLLM download model | 10–15 min | First run only — model cached in volume |
| PostgreSQL init | 5 sec | Creates tables if new volume |
| FastAPI startup | 10–15 sec | Loads whisper + kokoro, pre-warms chromadb |
| Cloudflare connect | 5–10 sec | Tunnel registers with Cloudflare edge |
| **Total cold start** | **~15–20 min** | First-ever run |
| **Total warm start** | **~30 sec** | Subsequent runs (models cached) |

---

## Monthly Cost Breakdown

### Option A: RTX 4090 — Scheduled 12 hrs/day (Best for 5–7 User Demo)

```
┌─────────────────────────────────────────────────────────┐
│                MONTHLY COST — RTX 4090                   │
│─────────────────────────────────────────────────────────│
│                                                         │
│  COMPUTE                                                │
│  RunPod RTX 4090 Pod                                     │
│  $0.79/hr × 12 hrs/day × 30 days                        │
│  ─────────────────────────────────────                   │
│  Subtotal:                                 $285         │
│                                                         │
│  STORAGE                                                │
│  100 GB persistent SSD                                   │
│  $0.15/GB × 100 GB                                       │
│  ─────────────────────────────────────                   │
│  Subtotal:                                  $15         │
│                                                         │
│  DATA TRANSFER                                          │
│  ~500 GB/month (voice audio streaming)                  │
│  ─────────────────────────────────────                   │
│  Subtotal:                                  $15         │
│                                                         │
│  CLOUDFLARE                                            │
│  Named Tunnel                                            │
│  ─────────────────────────────────────                   │
│  Subtotal:                                   $0         │
│                                                         │
│  TWILIO                                                 │
│  Phone number ($1.15) + usage (~$20)                    │
│  WhatsApp sandbox ($0 during testing)                    │
│  ─────────────────────────────────────                   │
│  Subtotal:                                  $22         │
│                                                         │
│  ════════════════════════════════════════               │
│  TOTAL MONTHLY:                            $337         │
│  ════════════════════════════════════════               │
│                                                         │
│  Per user (7 users):  ~$48/user/month                   │
│  Per call (~5 min):   ~$0.03/call                       │
└─────────────────────────────────────────────────────────┘
```

### Option B: RTX 4090 — 24/7 (Always Available)

```
┌─────────────────────────────────────────────────────────┐
│                MONTHLY COST — RTX 4090 24/7              │
│─────────────────────────────────────────────────────────│
│  Compute:     $0.79 × 730 hrs = $577                    │
│  Storage:     $15                                        │
│  Transfer:    $25                                        │
│  Cloudflare:  $0                                         │
│  Twilio:      $50                                        │
│  ════════════════════════════════════════               │
│  TOTAL:       ~$667/month                                │
└─────────────────────────────────────────────────────────┘
```

### Option C: A100 — Scheduled 12 hrs/day (Best for 10–15 Users)

```
┌─────────────────────────────────────────────────────────┐
│                MONTHLY COST — A100 80GB                  │
│─────────────────────────────────────────────────────────│
│  Compute:     $1.99 × 12 hrs × 30 days = $716           │
│  Storage:     $25 (200 GB for multiple models)           │
│  Transfer:    $25                                        │
│  Cloudflare:  $0                                         │
│  Twilio:      $50                                        │
│  ════════════════════════════════════════════               │
│  TOTAL:       ~$816/month                                │
│                                                         │
│  Per user (15 users): ~$54/user/month                   │
└─────────────────────────────────────────────────────────┘
```

### Option D: A100 — 24/7 (Enterprise)

```
┌─────────────────────────────────────────────────────────┐
│                MONTHLY COST — A100 80GB 24/7              │
│─────────────────────────────────────────────────────────│
│  Compute:     $1.99 × 730 hrs = $1,453                  │
│  Storage:     $25                                        │
│  Transfer:    $40                                        │
│  Cloudflare:  $0                                         │
│  Twilio:      $100                                       │
│  ════════════════════════════════════════════               │
│  TOTAL:       ~$1,618/month                              │
└─────────────────────────────────────────────────────────┘
```

---

## Summary Table

| Component | What It Replaces | Why It's Better | VRAM |
|-----------|-----------------|-----------------|------|
| **vLLM** | Ollama | Batched 20 concurrent, PagedAttention, 90% GPU util | 4.8 GB + KV pool |
| **faster-whisper small.en** | openai-whisper base | 4× faster, 3× more accurate, INT8 CUDA | 0.8 GB |
| **Kokoro ONNX CUDA** | Kokoro ONNX CPU | 5–10× faster, cached, streaming per sentence | 0.35 GB |
| **PostgreSQL pgvector** | External Docker | Containerized on same instance, zero network latency | ~0.2 GB RAM |
| **Cloudflare Named Tunnel** | Ephemeral quick tunnel | Permanent URL, never reconfigure Twilio | N/A |
| **Docker Compose** | Manual PS1 script | One command start, auto-restart, healthchecks | N/A |

| Tier | GPU | Schedule | Users | Monthly |
|------|-----|----------|-------|---------|
| Demo | RTX 4090 | 12 hrs/day | 5–7 | **~$337** |
| Budget | RTX 4090 | 24/7 | 5–15 | ~$667 |
| Standard | A100 | 12 hrs/day | 10–50 | ~$816 |
| Enterprise | A100 | 24/7 | 10–50 | ~$1,618 |
