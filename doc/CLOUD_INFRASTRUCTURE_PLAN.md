# Cloud Infrastructure Plan — Multi-Tier (5–50 Concurrent Users)

**Document version:** 2.0
**Date:** 2026-08-02
**Session:** `34bf257f-37d3-41e5-98a2-e7dc51fb91ee`
**Branch:** `whatsapp-chatbot-integration`

---

## Executive Summary

Deploy the University Admissions Voice Assistant on cloud GPU infrastructure with **real-time streaming AI** — STT, RAG, LLM, TTS — at human-like latency (~2 seconds to first word). This document covers three tiers:

| Tier | Users | GPU | Monthly Cost | Use Case |
|------|-------|-----|-------------|----------|
| **🟢 Demo** | 5–7 | RTX 4090 | ~$315–570 | Current need — investor demos, pilot |
| **🟡 Standard** | 10–15 | A100 80GB | ~$775–1,523 | Production pilot, small call center |
| **🔴 Enterprise** | 50+ | 2× A100 | ~$3,000+ | Full-scale deployment |

---

## 🟢 Tier 1: Demo (5–7 Concurrent Users) — RECOMMENDED FOR CURRENT NEED

**This is the tier you need right now.** 5–7 simultaneous callers is enough for a convincing demo with admissions counselors, prospects on WhatsApp, and live inbound calls all happening at once.

### VRAM Budget (5–7 Users)

| Component | VRAM | Shared/Per-User |
|-----------|------|-----------------|
| Whisper small.en (INT8) | 0.8 GB | Shared |
| Qwen 7B Q4_K_M (weights) | 4.8 GB | Shared |
| LLM KV cache × 7 users | ~5.3 GB | ~0.75 GB × 7 |
| Kokoro ONNX (CUDA) | 0.35 GB | Shared |
| Embedding (all-MiniLM) | 0.1 GB | Shared |
| CUDA overhead + buffers | 0.5 GB | Shared |
| ChromaDB vectors | 0.1 GB | Shared |
| **Total** | **~12.0 GB** | **Fits on RTX 4090 (24 GB) with 12 GB to spare** |

### Instance Specs — RTX 4090 (24 GB)

A single RTX 4090 comfortably handles 5–7 concurrent calls with room to grow.

| Spec | Value | Notes |
|------|-------|-------|
| **GPU** | NVIDIA RTX 4090 | Ada Lovelace architecture |
| **VRAM** | 24 GB GDDR6X | 1,008 GB/s memory bandwidth |
| **CUDA Cores** | 16,384 | 4th gen Tensor Cores |
| **CPU** | 16 vCPUs | AMD EPYC (Zen 4) |
| **CPU Clock** | 2.8–3.7 GHz | Shared cloud instance |
| **RAM** | 62 GB DDR5 | ECC, sufficient for all services |
| **Storage** | 100 GB NVMe SSD | ~3,500 MB/s read, persistent volume |
| **Network** | 10 Gbps | Internal + external |
| **OS** | Ubuntu 22.04 LTS | RunPod template |
| **Docker** | Native GPU support | `--runtime=nvidia` |
| **Max concurrent calls** | 5–7 (comfortable), up to 15 (tight) | Limited by VRAM KV cache |

### Monthly Cost — RTX 4090

| Option | Hours/Month | Monthly Cost |
|--------|-------------|-------------|
| **Scheduled (12 hrs/day)** | 360 hrs | **~$285** |
| On-Demand (24/7) | 730 hrs | ~$570 |
| Spot (interruptible) | 730 hrs | ~$200–300 |

**💡 Recommendation:** Run 12 hours/day during business hours. A demo is usually shown during the workday. Auto-start at 8 AM, auto-stop at 8 PM using RunPod's schedule feature.

```
8 AM → Instance auto-starts → docker compose up → ready by 8:05 AM
8 PM → Instance auto-stops → zero cost overnight
Weekends → Off (optional, adjust schedule)
```

### Total Monthly: ~$337

| Item | Monthly |
|------|---------|
| RTX 4090 (12 hrs/day, 30 days) | $285 |
| Storage (100 GB NVMe) | $15 |
| Data Transfer (~500 GB) | $15 |
| Cloudflare Named Tunnel | Free |
| Twilio (phone + WhatsApp) | ~$22 |
| **Total** | **~$337/month** |

### Docker Compose — Demo Tier

All services run on the single RTX 4090 instance:

```yaml
version: '3.9'

services:
  # LLM Server — vLLM with small batch size (7 max concurrent sequences)
  vllm:
    image: vllm/vllm-openai:latest
    runtime: nvidia
    ports:
      - "8080:8080"
    command: >
      --model Qwen/Qwen2.5-7B-Instruct
      --quantization awq
      --max-model-len 4096
      --gpu-memory-utilization 0.60
      --max-num-seqs 10
      --port 8080

  # FastAPI Backend (2 workers sufficient for 5-7 users)
  fastapi:
    build:
      context: .
      dockerfile: Dockerfile.cloud
    runtime: nvidia
    ports:
      - "8000:8000"
    environment:
      - VLLM_URL=http://vllm:8080/v1
      - DATABASE_URL=postgresql://admissions:secret@postgres:5432/admissions
      - TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
      - TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
      - TWILIO_PHONE_NUMBER=${TWILIO_PHONE_NUMBER}
      - TUNNEL_HOST=${TUNNEL_HOST}
      - CLOUD_MODE=true
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

  # Streamlit Dashboard
  streamlit:
    build:
      context: .
      dockerfile: Dockerfile.cloud
    ports:
      - "8501:8501"
    environment:
      - DASHBOARD_API_URL=http://fastapi:8000
    command: streamlit run dashboard.py --server.port 8501 --server.headless true

  # PostgreSQL
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      - POSTGRES_USER=admissions
      - POSTGRES_PASSWORD=${DB_PASSWORD:-admissions_secret}
      - POSTGRES_DB=admissions
    volumes:
      - postgres_data:/var/lib/postgresql/data

  # Cloudflare Tunnel
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel run --token ${CLOUDFLARED_TUNNEL_TOKEN}

volumes:
  postgres_data:
```

### Demo Day Checklist

**30 minutes before demo:**
- [ ] Start RunPod instance (or verify auto-start worked)
- [ ] SSH: `docker compose ps` — all 5 services healthy
- [ ] `curl http://localhost:8000/` → 200 OK
- [ ] `curl http://localhost:8080/v1/models` → vLLM ready
- [ ] Make 1 test call to +19788198953 → verify AI answers
- [ ] Send 1 WhatsApp message → verify reply
- [ ] Open dashboard → verify leads appear
- [ ] Open Command Cockpit → verify live monitor works
- [ ] `nvidia-smi` → VRAM ~12 GB used, GPU ready

**During demo:**
- [ ] Keep `nvidia-smi -l 1` running in a side terminal
- [ ] Monitor dashboard for active calls
- [ ] Have a backup phone ready (Twilio fallback number)

**After demo:**
- [ ] `docker compose down` (or keep running)
- [ ] Stop instance if using scheduled mode (or let auto-stop handle it)

---

## 🟡 Tier 2: Standard — 10–15 Concurrent Users

### Summary

| Item | Detail |
|------|--------|
| **GPU** | Single A100 SXM (80 GB) |
| **VRAM Used** | ~18 GB (62 GB free — plenty of headroom) |
| **Instance** | RunPod A100 SXM, 24 vCPU, 125 GB RAM |
| **Schedule** | 12 hrs/day (auto start/stop) or 24/7 |
| **Monthly Cost** | ~$816 (12 hrs/day) / ~$1,618 (24/7) |
| **LLM** | vLLM with Qwen 7B Q4 (batched, 20 concurrent streams) |
| **STT** | faster-whisper small.en on CUDA (0.8 GB, 0.5s per transcript) |
| **TTS** | Kokoro ONNX CUDA + 200-phrase LRU cache + per-sentence streaming |
| **DB** | PostgreSQL pgvector (container on instance) |
| **Tunnel** | Cloudflare named tunnel (permanent URL) |
| **Concurrency** | 10–15 simultaneous calls — comfortable |
| **Per User/Month** | ~$54 (12 hrs/day) |

### VRAM Budget (10–15 Users)

| Component | VRAM |
|-----------|------|
| faster-whisper small.en (INT8) | 0.8 GB |
| Qwen 7B Q4_K_M weights | 4.8 GB |
| LLM KV cache × 15 users (~0.75 GB each) | 11.25 GB |
| Kokoro ONNX CUDA | 0.35 GB |
| Embedding (all-MiniLM) | 0.1 GB |
| CUDA overhead + buffers | 0.5 GB |
| ChromaDB vectors | 0.1 GB |
| **Total Used** | **~18 GB** |
| **Free on A100 80GB** | **~62 GB** — room for 50+ users |

### Instance Specs — A100 SXM (80 GB)

| Spec | Value | Notes |
|------|-------|-------|
| **GPU** | NVIDIA A100 SXM | Ampere architecture, data-center grade |
| **VRAM** | 80 GB HBM2e | 2,039 GB/s memory bandwidth (2× RTX 4090) |
| **CUDA Cores** | 6,912 | 3rd gen Tensor Cores with MIG support |
| **CPU** | 24 vCPUs | AMD EPYC (Zen 3) |
| **CPU Clock** | 3.0–3.7 GHz | Dedicated cores, less noisy neighbor |
| **RAM** | 125 GB DDR4 ECC | Plenty for all services + model caching |
| **Storage** | 200 GB NVMe SSD | ~7,000 MB/s read, persistent volume |
| **Network** | 12 Gbps | Internal + external, low jitter |
| **OS** | Ubuntu 22.04 LTS | RunPod template |
| **Docker** | Native GPU support | `--runtime=nvidia` |
| **Max concurrent calls** | 10–15 (comfortable), up to 50 (with tuning) | Limited by vLLM batch size |

### Monthly Cost — A100 80GB

| Option | Hours/Month | Monthly |
|--------|-------------|---------|
| **Scheduled (12 hrs/day)** | 360 hrs | **~$816** |
| On-Demand (24/7) | 730 hrs | ~$1,618 |
| Spot (interruptible) | 730 hrs | ~$600 |

---

## 🟠 Tier 3: Growth — 20–30 Concurrent Users

### Summary

| Item | Detail |
|------|--------|
| **GPU** | Single A100 SXM (80 GB) or 2× RTX 4090 (48 GB) |
| **VRAM Used** | ~28 GB (A100: 52 GB free / 2× RTX 4090: 20 GB free) |
| **Instance** | RunPod A100 SXM (80 GB) or 2× RTX 4090 Pod |
| **Schedule** | 24/7 recommended (consistent availability needed) |
| **Monthly Cost** | ~$1,618 (A100 24/7) / ~$1,334 (2× RTX 4090 24/7) |
| **LLM** | vLLM with Qwen 7B Q4 (batched, 40 concurrent streams) |
| **STT** | faster-whisper small.en on CUDA (2 parallel workers) |
| **TTS** | Kokoro ONNX CUDA + 500-phrase LRU cache + per-sentence streaming |
| **DB** | PostgreSQL pgvector (separate container, 4 GB RAM allocated) |
| **Tunnel** | Cloudflare named tunnel + optional Load Balancer |
| **Concurrency** | 20–30 simultaneous calls — comfortable |
| **Per User/Month** | ~$54 (24/7, 30 users) |

### VRAM Budget (20–30 Users)

| Component | VRAM |
|-----------|------|
| faster-whisper small.en (INT8) × 2 workers | 1.6 GB |
| Qwen 7B Q4_K_M weights | 4.8 GB |
| LLM KV cache × 30 users (~0.75 GB each) | 22.5 GB |
| Kokoro ONNX CUDA × 2 parallel | 0.7 GB |
| Embedding (all-MiniLM) | 0.1 GB |
| CUDA overhead + buffers | 0.8 GB |
| ChromaDB vectors | 0.1 GB |
| **Total Used** | **~28 GB** |
| **On A100 80GB** | **52 GB free** — room for 80+ users |
| **On 2× RTX 4090 (48GB)** | **20 GB free** — room for 50+ users |

### vLLM Configuration for 20–30 Users

```yaml
vllm:
  command: >
    --model Qwen/Qwen2.5-7B-Instruct
    --quantization awq
    --max-model-len 4096
    --gpu-memory-utilization 0.85     # Increased from 0.75
    --max-num-seqs 40                  # Increased from 20
    --port 8080
```

### Instance Specs — 20–30 Users

Two options depending on budget vs headroom:

| Spec | Option A: A100 80GB | Option B: 2× RTX 4090 |
|------|--------------------|------------------------|
| **GPU** | 1× NVIDIA A100 SXM | 2× NVIDIA RTX 4090 |
| **VRAM** | 80 GB HBM2e (2,039 GB/s) | 48 GB GDDR6X (2,016 GB/s total) |
| **CUDA Cores** | 6,912 | 32,768 total (16,384 × 2) |
| **CPU** | 24 vCPUs (AMD EPYC Zen 3) | 32 vCPUs (AMD EPYC Zen 4) |
| **CPU Clock** | 3.0–3.7 GHz | 2.8–3.7 GHz |
| **RAM** | 125 GB DDR4 ECC | 125 GB DDR5 ECC |
| **Storage** | 200 GB NVMe SSD | 200 GB NVMe SSD |
| **Network** | 12 Gbps | 20 Gbps (2× 10 Gbps) |
| **OS** | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| **Max concurrent calls** | 30 (comfortable), 50 (tight) | 25 (comfortable), 40 (tight) |
| **Best for** | Future growth to 50+ users | Budget-conscious 20–30 user setup |

### Docker Changes for 20–30 Users

```yaml
fastapi:
  command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4  # 4 workers (was 2)

# Optional: Add NGINX for load balancing across workers
nginx:
  image: nginx:alpine
  ports:
    - "80:80"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf
  depends_on:
    - fastapi
```

### Monthly Cost — 20–30 Users

| Option | GPU | Monthly (24/7) |
|--------|-----|----------------|
| **Recommended** | A100 80GB | **~$1,618** |
| Budget | 2× RTX 4090 (48 GB) | ~$1,334 |
| Spot | A100 80GB (interruptible) | ~$800 |

---

## 🔴 Tier 4: Scale — 50+ Concurrent Users

### Summary

| Item | Detail |
|------|--------|
| **GPU** | 2× A100 (160 GB) or H100 (80 GB) |
| **VRAM Used** | ~55 GB (105 GB free on 2× A100) |
| **Instance** | 2× RunPod A100 SXM Pods or single H100 |
| **Schedule** | 24/7 |
| **Monthly Cost** | ~$3,236 (2× A100 24/7) / ~$2,150 (H100 24/7) |
| **LLM** | vLLM with Qwen 7B Q4 (batched, 80 concurrent streams) + optional 14B/72B model |
| **STT** | faster-whisper medium.en (769M) on dedicated GPU for accuracy |
| **TTS** | Kokoro ONNX CUDA + 1000-phrase LRU + dedicated TTS worker pool |
| **DB** | Managed PostgreSQL (AWS RDS / Supabase) |
| **Load Balancer** | NGINX + Cloudflare Load Balancer |
| **Concurrency** | 50–80 simultaneous calls |

### VRAM Budget (50 Users)

| Component | VRAM |
|-----------|------|
| faster-whisper medium.en | 1.5 GB |
| Qwen 7B Q4 weights | 4.8 GB |
| LLM KV cache × 50 users | 37.5 GB |
| Kokoro ONNX CUDA × 4 parallel | 1.4 GB |
| Embedding (all-MiniLM) | 0.1 GB |
| CUDA overhead + buffers | 1.5 GB |
| ChromaDB vectors | 0.2 GB |
| **Total Used** | **~47 GB** |
| **On 2× A100 (160 GB)** | **113 GB free** |

---

## All Tiers — Quick Comparison

| | 🟢 Demo | 🟡 Standard | 🟠 Growth | 🔴 Scale |
|---|---|---|---|---|
| **Users** | 5–7 | 10–15 | 20–30 | 50+ |
| **GPU** | RTX 4090 (24GB) | A100 (80GB) | A100 (80GB) or 2× RTX 4090 | 2× A100 or H100 |
| **VRAM Used** | ~12 GB | ~18 GB | ~28 GB | ~47 GB |
| **vLLM max seqs** | 10 | 20 | 40 | 80 |
| **FastAPI workers** | 2 | 2 | 4 | 8 |
| **STT** | small.en ×1 | small.en ×1 | small.en ×2 | medium.en dedicated |
| **TTS cache** | 100 phrases | 200 phrases | 500 phrases | 1000 phrases |
| **DB** | Container | Container | Container (4GB) | Managed RDS |
| **Schedule** | 12 hrs/day | 12 hrs/day | 24/7 | 24/7 |
| **Monthly** | **~$337** | **~$816** | **~$1,618** | **~$3,236** |
| **Per user/month** | ~$48 | ~$54 | ~$54 | ~$65 |

---

## 1. Architecture Overview (All Tiers)

```
                          INTERNET
                              │
    ┌─────────────────────────┼─────────────────────────┐
    │                         │                         │
    ▼                         ▼                         ▼
📞 Phone Calls          📱 WhatsApp              💻 Streamlit Web
(Twilio PSTN)          (Twilio Sandbox)          (Browser)
    │                         │                         │
    └─────────────────────────┼─────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  CLOUDFLARE TUNNEL │
                    │  (Named, permanent)│
                    │  admissions.example.com
                    └─────────┬─────────┘
                              │
    ┌─────────────────────────▼─────────────────────────┐
    │                  CLOUD GPU INSTANCE                 │
    │                                                    │
    │  ┌──────────────────────────────────────────────┐  │
    │  │         DOCKER COMPOSE ORCHESTRATION          │  │
    │  │                                              │  │
    │  │  ┌──────────┐ ┌──────────┐ ┌──────────────┐  │  │
    │  │  │ FastAPI ×2│ │ Streamlit│ │ PostgreSQL    │  │  │
    │  │  │ (uvicorn) │ │  (8501)  │ │ (pgvector)    │  │  │
    │  │  │ Port 8000 │ │          │ │ Port 5432     │  │  │
    │  │  └─────┬─────┘ └──────────┘ └──────────────┘  │  │
    │  │        │                                        │  │
    │  │        ├──────────────────────────────┐         │  │
    │  │        │                              │         │  │
    │  │  ┌─────▼─────┐  ┌──────────┐  ┌──────▼──────┐  │  │
    │  │  │ vLLM      │  │ Whisper  │  │ Kokoro TTS  │  │  │
    │  │  │ Qwen 7B   │  │ small.en │  │ ONNX (CUDA) │  │  │
    │  │  │ (Batched) │  │ (CUDA)   │  │ (CUDA)      │  │  │
    │  │  │ Port 8080 │  │          │  │             │  │  │
    │  │  └───────────┘  └──────────┘  └─────────────┘  │  │
    │  │                                              │  │
    │  │         ALL SHARING SINGLE GPU                │  │
    │  └──────────────────────────────────────────────┘  │
    │                                                    │
    │  GPU: NVIDIA A100 80GB  or  2× RTX 4090 24GB      │
    └────────────────────────────────────────────────────┘
```

### Flow Per Call

```
1. Caller dials → Twilio → webhook hits FastAPI via Cloudflare tunnel
2. FastAPI returns TwiML with <Stream url="wss://...">
3. Twilio opens WebSocket through tunnel → FastAPI /ws/twilio
4. Audio streams in (8kHz μ-law) → FastAPI
5. FastAPI pipelines audio:
   a. Whisper STT (local, CUDA, ~0.5s)
   b. RAG retrieval (ChromaDB + local embeddings, ~0.2s)
   c. vLLM serving Qwen 7B (batched, streaming, ~0.5s first token)
   d. Kokoro TTS CUDA (cached per sentence, ~1s per chunk)
6. μ-law audio streams back to caller via WebSocket
7. Transcript + lead data → PostgreSQL
```

---

## 2. Instance Sizing

### 2.1 VRAM Budget Per Concurrent User

| Component | Base VRAM (shared) | Per-User VRAM |
|-----------|-------------------|---------------|
| Whisper small.en | 0.8 GB | 0 GB (shared, sequential) |
| Qwen 7B Q4_K_M (weights) | 4.8 GB | 0 GB (shared across all users) |
| LLM KV cache (per user) | 0 GB | 0.5–0.8 GB (context = 4096 tokens) |
| Kokoro ONNX | 0.35 GB | 0 GB (shared, sequential) |
| Embedding (all-MiniLM) | 0.1 GB | 0 GB (shared) |
| CUDA context + buffers | 0.5 GB | 0 GB |
| ChromaDB vectors | 0.1 GB | 0 GB |
| **Total** | **6.65 GB** | **0.5–0.8 GB per user** |

**Formula:** `6.65 + (users × 0.75) GB`

| Users | VRAM Needed |
|-------|-------------|
| 1 | 7.4 GB |
| 5 | 10.4 GB |
| 10 | 14.15 GB |
| 15 | 17.9 GB |
| 20 | 21.65 GB |
| 50 | 44.15 GB |

### 2.2 GPU Options

| GPU | VRAM | Max Concurrent Users | Cloud Cost/hr | Monthly (24/7) |
|-----|------|---------------------|---------------|----------------|
| RTX 4090 | 24 GB | 15–20 | ~$0.79 (RunPod) | ~$570 |
| A100 SXM | 80 GB | 50+ | ~$1.99 (RunPod) | ~$1,430 |
| 2× RTX 4090 | 48 GB | 40+ | ~$1.58 (RunPod) | ~$1,140 |
| H100 | 80 GB | 50+ | ~$2.99 (Lambda) | ~$2,150 |

### 2.3 Recommended: Single A100 80GB

**Why:** 80GB VRAM comfortably handles 15 concurrent users with room to grow to 50+. Batching improves throughput significantly. No multi-GPU complexity.

---

## 3. Model Serving Architecture

### 3.1 LLM: vLLM (Replaces Ollama for Production)

| | Ollama (Current) | vLLM (Production) |
|---|---|---|
| Batching | No (one-at-a-time) | Yes (continuous batching) |
| Throughput | ~10 tokens/s single stream | ~50–100 tokens/s total across streams |
| KV cache | Per-request, not shared | PagedAttention, memory-efficient sharing |
| API | Ollama proprietary | OpenAI-compatible `/v1/chat/completions` |
| GPU utilization | ~40% single stream | ~90% with batching |

**vLLM command:**
```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --quantization awq \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 20 \
  --port 8080
```

### 3.2 STT: faster-whisper as a Service

Run a dedicated whisper server for batched STT:
```bash
# Use faster-whisper-server or whisper-x
# Handles concurrent transcription requests
pip install faster-whisper
```

Or use **Whisper LiveKit** integration for real-time streaming STT.

### 3.3 TTS: Kokoro ONNX (CUDA) with Caching

Same Kokoro ONNX model, but with:
- CUDA execution provider (not CPU like current)
- LRU cache for top 200 phrases
- Sentence-level streaming (synthesize per sentence, not full response)

### 3.4 Embedding: Local Sentence-Transformers

```bash
pip install sentence-transformers
```
```
Model: all-MiniLM-L6-v2 (22M params, 0.1 GB VRAM)
Speed: ~0.1s per embedding
```

---

## 4. Cloud Provider Comparison

### 4.1 RunPod.io (Recommended)

| GPU Pod | VRAM | vCPUs | RAM | $/hr | Concurrent Users |
|---------|------|-------|-----|------|-------------------|
| RTX 4090 | 24 GB | 16 | 62 GB | $0.79 | 15–20 |
| A100 SXM | 80 GB | 24 | 125 GB | $1.99 | 50+ |
| 2× RTX 4090 | 48 GB | 32 | 125 GB | $1.58 | 40+ |

**Pros:** GPU-focused, simple UI, pre-built templates, persistent storage, auto-shutdown
**Cons:** No managed PostgreSQL (run as container)

### 4.2 AWS (g5/l4 instances)

| Instance | GPU | VRAM | $/hr | Concurrent Users |
|----------|-----|------|------|-------------------|
| g5.2xlarge | A10G | 24 GB | $1.52 | 10–15 |
| g5.4xlarge | A10G | 24 GB | $2.18 | 10–15 |
| p4d.xlarge | A100 | 40 GB | $3.71 | 25+ |

**Pros:** Full AWS ecosystem (RDS, S3, ECS), enterprise security
**Cons:** Higher cost, more complex setup, GPU availability can be limited

### 4.3 Lambda Labs

| GPU | VRAM | $/hr | Concurrent Users |
|-----|------|------|-------------------|
| A100 80GB | 80 GB | $1.99 | 50+ |
| H100 80GB | 80 GB | $2.99 | 50+ |

**Pros:** Simple, researcher-friendly, good pricing
**Cons:** Limited regions, no managed services

---

## 5. Monthly Cost Estimate (RunPod A100, 24/7)

| Component | Monthly Cost |
|-----------|-------------|
| GPU Instance (A100 80GB) | $1,433 |
| Storage (100 GB SSD) | $15 |
| Data Transfer (500 GB) | $25 |
| Cloudflare Tunnel | Free |
| Twilio (phone + WhatsApp) | ~$50 |
| PostgreSQL (container on instance) | Free |
| **Total (24/7 operation)** | **~$1,523/month** |
| **Total (12 hrs/day)** | **~$775/month** |

### Cost-Saving Options

| Option | Monthly |
|--------|---------|
| Spot/Interruptible instance (50% discount, risk of interruption) | ~$765 |
| 12 hrs/day scheduled (auto start/stop) | ~$775 |
| RTX 4090 instead of A100 (15 users limit) | ~$600 |
| Reserved instance (1-year commit, 40% discount) | ~$920 |

---

## 6. Network Architecture

### 6.1 External Access

```
Twilio PSTN ──→ HTTPS ──→ Cloudflare Named Tunnel ──→ FastAPI:8000
WhatsApp API ──→ HTTPS ──→ Cloudflare Named Tunnel ──→ FastAPI:8000
Browser ──→ HTTPS ──→ Cloudflare Named Tunnel ──→ FastAPI:8000
                                                  ──→ Streamlit:8501
```

### 6.2 Internal Service Mesh

```
FastAPI:8000 ──→ vLLM:8080 (LLM, OpenAI-compatible API)
             ──→ ChromaDB (local, persistent volume)
             ──→ PostgreSQL:5432 (container, persistent volume)
             ──→ Whisper (local, in-process or separate service)
             ──→ Kokoro TTS (local, in-process)
             ──→ Ollama (optional, for embedding backup)
```

### 6.3 Port Map

| Port | Service | External? |
|------|---------|-----------|
| 8000 | FastAPI | Via Cloudflare tunnel only |
| 8501 | Streamlit Dashboard | Via Cloudflare tunnel only |
| 8080 | vLLM | Internal only |
| 5432 | PostgreSQL | Internal only |
| 11434 | Ollama (if used) | Internal only |

---

## 7. Docker Compose (Cloud Edition)

```yaml
version: '3.9'

services:
  # ── LLM: vLLM Server (batched, OpenAI-compatible) ──
  vllm:
    image: vllm/vllm-openai:latest
    container_name: voice-vllm
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    ports:
      - "8080:8080"
    volumes:
      - vllm_cache:/root/.cache/huggingface
    command: >
      --model Qwen/Qwen2.5-7B-Instruct
      --quantization awq
      --max-model-len 4096
      --gpu-memory-utilization 0.75
      --max-num-seqs 20
      --port 8080
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ── FastAPI: Backend Server (2 workers) ──
  fastapi:
    build:
      context: .
      dockerfile: Dockerfile.cloud
    container_name: voice-fastapi
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - OLLAMA_HOST=none                # Disable Ollama, use vLLM
      - VLLM_URL=http://vllm:8080/v1
      - DATABASE_URL=postgresql://admissions:secret@postgres:5432/admissions
      - TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
      - TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
      - TWILIO_PHONE_NUMBER=${TWILIO_PHONE_NUMBER}
      - TUNNEL_HOST=${TUNNEL_HOST}
    ports:
      - "8000:8000"
    volumes:
      - whisper_models:/root/.cache/whisper
      - kokoro_models:/root/.cache/pipecat/kokoro-onnx
      - chroma_data:/app/chroma_local_db
      - audio_data:/app/app/static/audio
    command: >
      uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
    depends_on:
      vllm:
        condition: service_healthy
      postgres:
        condition: service_healthy
    restart: unless-stopped

  # ── Streamlit: Admin Dashboard ──
  streamlit:
    build:
      context: .
      dockerfile: Dockerfile.cloud
    container_name: voice-streamlit
    runtime: nvidia
    environment:
      - DASHBOARD_API_URL=http://fastapi:8000
    ports:
      - "8501:8501"
    command: streamlit run dashboard.py --server.port 8501 --server.headless true
    depends_on:
      - fastapi
    restart: unless-stopped

  # ── PostgreSQL: Database with pgvector ──
  postgres:
    image: pgvector/pgvector:pg16
    container_name: voice-postgres
    environment:
      - POSTGRES_USER=admissions
      - POSTGRES_PASSWORD=${DB_PASSWORD:-admissions_secret}
      - POSTGRES_DB=admissions
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U admissions"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  # ── Cloudflare Tunnel (named, authenticated) ──
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: voice-tunnel
    command: tunnel run --token ${CLOUDFLARED_TUNNEL_TOKEN}
    restart: unless-stopped
    depends_on:
      - fastapi

volumes:
  vllm_cache:
  whisper_models:
  kokoro_models:
  chroma_data:
  audio_data:
  postgres_data:
```

---

## 8. Code Changes for Cloud Mode

### 8.1 New: `app/config.py` — Cloud vs Local Mode

```python
# Cloud mode detection
CLOUD_MODE: bool = _env("CLOUD_MODE", "false").lower() == "true"
VLLM_URL: str = _env("VLLM_URL", "http://localhost:8080/v1")

# When cloud mode: use vLLM instead of Ollama
# When local mode: use Ollama (current behavior)
```

### 8.2 Modify: `app/rag.py` — vLLM Backend

```python
def _query_llm(messages: list[dict], stream: bool = True) -> str | Iterator[str]:
    if settings.CLOUD_MODE:
        # Use vLLM (OpenAI-compatible)
        client = openai.OpenAI(base_url=settings.VLLM_URL, api_key="not-needed")
        response = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=messages,
            stream=stream,
            max_tokens=512,
            temperature=0.7,
        )
        if stream:
            for chunk in response:
                yield chunk.choices[0].delta.content
        else:
            return response.choices[0].message.content
    else:
        # Fallback to Ollama (current)
        ...
```

### 8.3 New: `Dockerfile.cloud`

```dockerfile
FROM nvidia/cuda:12.4-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y \
    python3.11 python3-pip ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cloud-specific: vLLM client, optimized whisper
RUN pip install --no-cache-dir openai faster-whisper sentence-transformers

COPY . .

HEALTHCHECK --interval=30s --timeout=10s \
    CMD curl -f http://localhost:8000/ || exit 1
```

### 8.4 Modify: `app/voice_handler.py` — Streaming TTS for Cloud

```python
async def process_utterance_streaming(self) -> AsyncIterator[bytes]:
    """Stream TTS chunks as LLM produces tokens."""
    
    # STT (same as current, but on GPU)
    transcript = await self._transcribe(audio_16k)
    
    # RAG (same, but with local embeddings)
    context = await self._retrieve_context(transcript)
    
    # LLM STREAMING (vLLM or Ollama stream)
    sentence_buffer = ""
    async for token in self._query_llm_streaming(transcript, context):
        sentence_buffer += token
        
        # On sentence boundary → TTS chunk → yield immediately
        if token in ('.', '?', '!', '\n') and len(sentence_buffer) > 20:
            tts_chunk = await self._synthesise_chunk(sentence_buffer)
            sentence_buffer = ""
            if tts_chunk:
                yield tts_chunk  # Stream to caller RIGHT NOW
    
    # Flush remaining text
    if sentence_buffer.strip():
        tts_chunk = await self._synthesise_chunk(sentence_buffer)
        if tts_chunk:
            yield tts_chunk
```

---

## 9. Scaling Strategy

### 9.1 Vertical Scaling (Single GPU)

| Users | GPU | Strategy |
|-------|-----|----------|
| 1–5 | RTX 4090 | Single instance, all models on one GPU |
| 5–15 | A100 80GB | Single instance, vLLM batching, 2 FastAPI workers |
| 15–30 | A100 80GB | vLLM batching, 4 FastAPI workers, TTS on separate thread pool |
| 30–50 | H100 80GB | vLLM batching, 8 FastAPI workers, dedicated TTS worker processes |

### 9.2 Horizontal Scaling (Multiple GPUs)

For 50+ concurrent users, split across multiple instances behind a load balancer:

```
                    Cloudflare Tunnel
                          │
                   ┌──────▼──────┐
                   │  NGINX LB   │
                   │ (round-robin)│
                   └──┬──┬──┬──┬─┘
                      │  │  │  │
               ┌──────▼──▼──▼──▼──────┐
               │   FastAPI  (×4)      │
               │   Port 8000          │
               └──────────┬───────────┘
                          │
               ┌──────────▼───────────┐
               │    vLLM Cluster      │
               │  (GPU 1)  (GPU 2)   │
               │  Qwen 7B   Qwen 7B  │
               └──────────────────────┘
```

### 9.3 Auto-Scaling (RunPod)

RunPod supports auto-scaling based on queue depth. Configure:
- Min instances: 1 (A100)
- Max instances: 3
- Scale up: When GPU utilization > 80% for 5 minutes
- Scale down: When GPU utilization < 30% for 15 minutes

---

## 10. Monitoring Stack

| Component | Tool | Metrics |
|-----------|------|---------|
| GPU utilization | `nvidia-smi` + Prometheus exporter | VRAM %, GPU %, temperature |
| LLM latency | vLLM metrics endpoint | Time-to-first-token, tokens/s, queue depth |
| API latency | FastAPI middleware | Request duration, concurrent WS connections |
| Call quality | Custom (app logs) | STT accuracy, TTS latency, pipeline total |
| Database | pg_stat | Connections, query timing |
| Uptime | Cloudflare tunnel health | Tunnel status, DNS resolution |

Add Prometheus + Grafana containers to docker-compose for a live dashboard.

---

## 11. Deployment Checklist

### Pre-Deploy (Local)
- [ ] Build `Dockerfile.cloud` and test locally
- [ ] Switch STT to faster-whisper small.en (numpy bypass, no PyAV)
- [ ] Add TTS caching (LRU 200 entries)
- [ ] Add vLLM code path in `app/rag.py`
- [ ] Add `CLOUD_MODE=true` config flag
- [ ] Create named Cloudflare tunnel, get token
- [ ] Test all APIs locally in cloud mode

### Deploy (RunPod)
- [ ] Create RunPod GPU Pod (A100 80GB, 125GB RAM, 100GB storage)
- [ ] Upload project via `runpodctl` or git clone
- [ ] Set all environment variables (Twilio, DB, tunnel token)
- [ ] Run: `docker compose up -d`
- [ ] Verify: `curl http://localhost:8000/`
- [ ] Verify: `curl http://localhost:8080/v1/models` (vLLM)
- [ ] Test: Make test call to +19788198953
- [ ] Test: Send WhatsApp message
- [ ] Monitor: `nvidia-smi` for VRAM usage
- [ ] Monitor: vLLM metrics for throughput

### Post-Deploy
- [ ] Set up auto-start on instance reboot
- [ ] Configure backups (PostgreSQL dump to S3/volume)
- [ ] Set up Cloudflare tunnel health monitoring
- [ ] Load test with 5, 10, 15 concurrent calls
- [ ] Tune vLLM parameters (max-num-seqs, GPU memory)
- [ ] Document recovery procedure

---

## 12. Cost Summary — Monthly

### Option A: Single A100 (Recommended for 10–15 users)

| Item | Monthly |
|------|---------|
| RunPod A100 80GB (24/7) | $1,433 |
| Storage (100 GB) | $15 |
| Data Transfer | $25 |
| Cloudflare Tunnel | Free |
| Twilio (phone + WhatsApp) | ~$50 |
| **Total** | **~$1,523/month** |

### Option B: Single RTX 4090 (Budget, 10–15 users)

| Item | Monthly |
|------|---------|
| RunPod RTX 4090 24GB (24/7) | $570 |
| Storage (100 GB) | $15 |
| Data Transfer | $25 |
| Cloudflare Tunnel | Free |
| Twilio (phone + WhatsApp) | ~$50 |
| **Total** | **~$660/month** |

### Option C: Spot Instance A100 (Best Value)

| Item | Monthly |
|------|---------|
| RunPod A100 Spot (60% discount, ~50% availability) | ~$573 |
| Storage (100 GB) | $15 |
| Data Transfer | $25 |
| Cloudflare Tunnel | Free |
| Twilio (phone + WhatsApp) | ~$50 |
| **Total** | **~$663/month** |

⚠ Spot instances can be interrupted. Use checkpointing and auto-resume.

---

## 13. Quick Reference

| Question | Answer |
|----------|--------|
| **GPU for 10–15 users?** | A100 80GB (comfortable) or RTX 4090 24GB (tight) |
| **Monthly cost?** | ~$660 (RTX 4090) to ~$1,523 (A100 24/7) |
| **Cheapest option?** | ~$660/month (RunPod RTX 4090) |
| **Best value?** | ~$663/month (RunPod A100 Spot) |
| **LLM serving?** | vLLM with continuous batching (replaces Ollama for production) |
| **STT serving?** | faster-whisper small.en on CUDA |
| **TTS serving?** | Kokoro ONNX on CUDA with LRU caching |
| **Database?** | PostgreSQL pgvector (container on same instance) |
| **Tunnel?** | Cloudflare named tunnel (permanent URL, free) |
| **Scaling beyond 50?** | Horizontal: multiple GPU instances + load balancer |

---

## 14. Related Documents

- `doc/INFRASTRUCTURE_PLAN.md` — Full infrastructure analysis (local + cloud)
- `doc/ENHANCEMENT_ROADMAP.md` — Technical enhancements
- `doc/COMMAND_COCKPIT_DASHBOARD.md` — Counselor dashboard design
- `docker-compose.yml` — Current compose (needs cloud updates)
- `start_services.ps1` — Local Windows launcher (not used in cloud)
