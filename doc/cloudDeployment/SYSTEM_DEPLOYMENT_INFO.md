# Cloud Deployment — System Information

**Project:** University Admissions Voice AI Assistant (Meridian University)
**Branch:** `meridianDataUpdate` · **Generated:** 2026-08-13
**Purpose:** This document contains everything a cloud provider / DevOps engineer needs to deploy this system. Hand it over as the single source of truth for provisioning.

---

## 1. System Summary

A voice-first AI admissions assistant that answers phone calls and WhatsApp messages with a **fully local (self-hosted) AI pipeline**: STT → RAG → LLM → TTS. It also manages leads, outbound calling, offer-letter generation (PDF), sentiment analysis, and an admin dashboard.

| Component | Technology | Role |
|---|---|---|
| Backend API | FastAPI (Python 3.11) | Webhooks, WebSockets, REST, MCP server |
| Web UI (chatbot) | Streamlit | Student-facing chat interface |
| Web UI (dashboard) | Streamlit | Command Cockpit admin dashboard |
| LLM | Ollama + Qwen 2.5 7B (Q3_K_M / Q4_K_M) | RAG answer generation, lead extraction, intent classification |
| Embeddings | Ollama `nomic-embed-text` | RAG vector embeddings |
| Vector DB | ChromaDB (persistent local) | Admissions knowledge base (Meridian — content/meridian markdown) |
| STT | faster-whisper (`small.en`, CUDA INT8) | Live call + WhatsApp voice-note transcription |
| TTS | Kokoro-82M (ONNX Runtime, CUDA) | Voice replies over phone / WhatsApp |
| Relational DB | PostgreSQL 16 (+ pgvector image) | Leads, conversations, call_queue, follow-ups, offers, sentiment |
| Telephony/Messaging | Twilio (Voice + WhatsApp) | Inbound/outbound calls, WhatsApp webhook |
| Email | SMTP (Gmail) | Offer-letter PDF delivery |

**Entry points:**

- `app/main.py` → FastAPI app (`python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`)
- `app.py` → Streamlit chatbot (`streamlit run app.py --server.port 8501`)
- `dashboard.py` → Streamlit dashboard (`streamlit run dashboard.py --server.port 8502`)

---

## 2. Compute Requirements

### GPU (strongly recommended — real-time voice requires it)

The pipeline runs STT + LLM + TTS **sequentially** to stay within a 6 GB VRAM budget:

| Service | Model | VRAM |
|---|---|---|
| Ollama LLM | qwen2.5:7b-instruct-q3_K_M | ~4.0 GB |
| Whisper STT | faster-whisper small.en (INT8) | ~0.8 GB |
| Kokoro TTS | kokoro-v1.0.onnx (82M) | ~0.35 GB |
| CUDA overhead | — | ~0.5 GB |
| **Peak total** | | **~5.65 GB / 6 GB** |

- **Minimum viable cloud GPU: NVIDIA T4 (16 GB)** — gives headroom for larger models / concurrent calls.
- Recommended: T4 / L4 / A10G on AWS `g4dn.xlarge`+, GCP `n1-standard-4 + T4`, or Azure `NC4as_T4_v3`.
- Local dev machine reference: NVIDIA RTX 2060 6 GB — everything fits.
- CPU-only works but Whisper + Kokoro become slow (multi-second latency per turn) — not suitable for live calls.

### RAM / CPU / Disk

- **RAM:** ≥ 8 GB system (Whisper + torch + app + buffers). 16 GB comfortable.
- **CPU:** 4 vCPU minimum; 8 recommended.
- **Disk:** ≥ 30 GB total:
  - Ollama models: ~3.8 GB (qwen2.5:7b-instruct-q3_K_M) + 0.27 GB (nomic-embed-text) + optional Q4 (~4.7 GB)
  - Kokoro model cache: ~330 MB (`kokoro-v1.0.onnx` + `voices-v1.0.bin`)
  - Whisper small.en: ~244 MB (auto-downloaded on first run)
  - Code + deps + PyTorch (CUDA): ~5–8 GB
  - ChromaDB: 61 MB (can be shipped as-is or rebuilt from the PDF)

### Python version

- **Python 3.11 required (≤ 3.12).** The code uses the stdlib `audioop` module (µ-law ↔ PCM conversion), **removed in Python 3.13**. Do NOT use 3.13+.
- Verified working version: **Python 3.11.9**, PyTorch **2.6.0+cu124**, CUDA 12.x.

---

## 3. Network & Ports

| Port | Service | Expose publicly? |
|---|---|---|
| 8000 | FastAPI backend | **Yes** — HTTPS required for Twilio webhooks |
| 8501 | Streamlit chatbot UI | Yes (or behind VPN) |
| 8502 | Streamlit dashboard UI | Yes (or behind VPN — contains lead data) |
| 11434 | Ollama | **No** — internal only |
| 5432 | PostgreSQL | **No** — internal only |

### Critical networking requirements

1. **Public HTTPS endpoint for FastAPI.** Twilio must be able to reach the server at a stable, public, TLS-valid URL. Currently done locally via Cloudflare quick tunnels (`*.trycloudflare.com`); in cloud this must be a real domain + reverse proxy (Nginx/Caddy/Traefik) or the platform's own TLS termination.
2. **Long-lived connections must be allowed:**
   - WebSocket streams (`/ws/twilio`, `/ws/twilio-outbound`) run for the whole call duration — no 30-second idle timeouts, no request-size limits on WS frames.
   - SSE stream (`/api/calls/live?stream=true`) stays open indefinitely.
   - WhatsApp voice-note processing happens in background tasks after the webhook returns — server must not be shut down/scale-to-zero mid-task.
3. **Single-instance state.** `_active_call_sids`, `_transcript_events`, `_batch_jobs` are in-memory. If you scale horizontally, put SSE/WS-aware routing in place or accept that live-call monitoring only works per instance. (DB state is shared, so leads/offers are safe.)
4. **Twilio webhook URLs** (configure in Twilio Console):
   - Voice inbound (phone number → "A call comes in", GET): `https://<domain>/twilio/voice`
   - WhatsApp sandbox ("When a message comes in", POST): `https://<domain>/twilio/whatsapp`
   - Outbound status callback (POST): `https://<domain>/twilio/outbound/status`
   - Outbound call voice URL (GET/POST): `https://<domain>/twilio/outbound-voice`

---

## 4. Dependencies

### Python packages (`requirements.txt` — pinned by verified versions)

```
fastapi (0.140.0), uvicorn[standard] (0.51.0), streamlit (1.59.2)
websockets (15.0.1), httpx (0.28.1)
torch (2.6.0+cu124), ollama (0.6.2), chromadb (1.5.9)
onnxruntime-gpu (1.28.0)        # swap to 'onnxruntime' for CPU-only hosts
openai-whisper (20250625)       # WhatsApp fallback STT
pipecat-ai[whisper,kokoro] (1.6.0)   # brings faster-whisper 1.2.1, kokoro-onnx 0.5.0
psycopg2-binary (2.9.12), twilio (9.10.9), pandas (3.0.3), fpdf2 (2.8.7)
```

**⚠️ Missing from `requirements.txt` but required at runtime — install explicitly:**

```
python-multipart (0.0.32)      # FastAPI Form/File endpoints (Twilio webhooks, uploads)
requests (2.34.2)              # Streamlit backend-sync helper
numpy (2.4.6), scipy (1.17.1), soundfile (0.14.0)   # audio decode/resample
langchain (1.3.14), langchain-ollama (1.1.0), langchain-community (0.4.2),
langchain-text-splitters (1.1.2), pypdf (6.14.2)     # ChromaDB build path (RAG)
```

- `mcp` package is NOT installed locally — the app degrades gracefully (MCP endpoints exist; AI-client integrations need `pip install mcp`).

### System packages (Linux image)

```
ffmpeg, build-essential, libssl-dev, libffi-dev, python3-dev, git, curl
```

### Docker (already provided in repo)

- `Dockerfile` — multi-stage, `python:3.11-slim`, installs system deps + requirements, default CMD runs FastAPI.
- `docker-compose.yml` — services: `postgres` (pgvector/pgvector:pg16), `ollama` (ollama/ollama:latest), `fastapi`, `streamlit`, on a bridge network `voice-network`.

---

## 5. Environment Variables

Create a `.env` (or set in the platform's secret manager). **Required in production:**

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TWILIO_ACCOUNT_SID` | ✅ | — | Twilio console |
| `TWILIO_AUTH_TOKEN` | ✅ | — | Twilio console |
| `TWILIO_PHONE_NUMBER` | ✅ | — | e.g. `+19788198953` |
| `TWILIO_WHATSAPP_NUMBER` | ✅ | — | `whatsapp:+14155238886` style |
| `DATABASE_URL` | ✅ | — | Full Postgres DSN, e.g. `postgresql://user:pass@host:5432/admissions` |
| `SMTP_USER` / `SMTP_PASS` | ⚠️ offer email | — | Gmail app password; offer PDFs are emailed |
| `SMTP_HOST` / `SMTP_PORT` | — | `smtp.gmail.com` / `587` | |
| `TUNNEL_HOST` | ⚠️ | `localhost:8000` | **Public host:port used inside TwiML/WhatsApp media URLs.** Set to your public domain (e.g. `bot.yourdomain.com`). On cloud this replaces the local tunnel. |

**Runtime tuning (defaults are sane):**

| Variable | Default | Notes |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Set `HOST=0.0.0.0` in containers |
| `OLLAMA_URL` | `http://localhost:11434` | Point at Ollama service host in cloud |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct-q3_K_M` | Must be pulled on the Ollama host |
| `OLLAMA_NUM_CTX` | `2048` | Keep low — VRAM safeguard |
| `EMBED_MODEL` | `nomic-embed-text` | Must be pulled on the Ollama host |
| `WHISPER_MODEL` | `small.en` | `tiny.en`/`base.en` for smaller instances |
| `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` | `localhost/5432/admissions/postgres/""` | Used when `DATABASE_URL` unset |
| `TRANSPORT_PROVIDER` | `websocket` | `twilio` for telephony mode |
| `DATA_DIR` | `data` | Student uploads + offer PDFs — use persistent volume |
| `MCP_ENABLED` | `true` | Model Context Protocol server |
| `OUTBOUND_POLL_INTERVAL` / `FOLLOW_UP_POLL_INTERVAL` / `MAX_CALL_ATTEMPTS` | `10` / `30` / `3` | Outbound call engine |
| `UNIVERSITY_NAME`, `OFFER_EMAIL`, `OFFER_VALID_DAYS`, `OFFER_GUARD_MINUTES`, `DEFAULT_PAYMENT_LINK` | Meridian University defaults | Offer-letter content |
| `SENTIMENT_W1..W4`, `SENTIMENT_EWMA_LAMBDA`, `MIN_LABELED_OUTCOMES` | 0.30/0.30/0.25/0.15, 0.35, 100 | Sentiment scoring weights |
| `BACKEND_BASE` | `http://localhost:8000` | Streamlit → FastAPI (set to `http://localhost:8000` if same host) |

**Windows-only workarounds (NOT needed on Linux cloud, harmless if set):**
`LANGCHAIN_TRACING_V2=false`, `LANGCHAIN_ENDPOINT=""`, `LANGCHAIN_API_KEY=""`, `LANGCHAIN_PROJECT=""`, `HF_HUB_ENABLE_HF_XET=0` — these bypass AppLocker DLL blocks on the dev machine; keep them unset in production.

---

## 6. Data, Models & Persistent Storage

| Path | Size | Persistent? | Notes |
|---|---|---|---|
| `chroma_local_db/` | 61 MB | ✅ | Vector store (Meridian). Ship as-is, or rebuild from `content/meridian/meridian_knowledge_base.md` via `scripts/rebuild_rag_index.py` (requires Ollama + embedding model). |
| `content/meridian/meridian_knowledge_base.md` | ~15 KB | ✅ | RAG source of truth — required to rebuild ChromaDB. (`content/sample_data/` holds the archived UMD/FDU sources — not ingested.) |
| `data/` (documents, offer PDFs) | ~10 MB | ✅ | Student uploads + generated offer letters. Mount as a volume. |
| `logs/` | small | optional | App logs. |
| `~/.cache/pipecat/kokoro-onnx/` | ~330 MB | ✅ (cache) | `kokoro-v1.0.onnx` + `voices-v1.0.bin` — auto-downloaded by `kokoro_onnx` on first TTS use. Warm the cache during startup (already done by app warmup) to avoid first-call delay. |
| Ollama model store (`/root/.ollama`) | ~4 GB | ✅ | Docker volume `ollama_data` in compose. |
| Whisper `small.en` | ~244 MB | cache | Auto-downloaded by faster-whisper from Hugging Face on first STT. |
| `app/static/audio/` | small | ✅ (writable) | Generated WhatsApp TTS replies (`.mp3`) served at `/audio/{filename}`. |
| `app/static/` | — | read-only | Dashboard + voice-client HTML/JS/CSS. |

**PostgreSQL schema** is auto-created on startup by `init_db()` (tables: `lead_calls`, `leads`, `conversations`, `call_queue`, `follow_ups`, offers subsystem, sentiment subsystem). A `scripts/seed_demo_data.py` seeds demo data (`POST /api/demo/reset` and `/api/demo/seed`).

---

## 7. Startup & Warmup Behavior (affects health checks)

On boot, the FastAPI lifespan does:
1. `init_db()` — connects to Postgres and creates all tables. Fails gracefully (app runs without DB) but log a warning.
2. **Model warmup (can take 1–3+ minutes on first boot):** loads ChromaDB vector store + Whisper model + Kokoro TTS engine. Set generous health-check grace periods (e.g. start_period ≥ 180 s) and don't route traffic until warm.
3. Starts `OutboundCallWorker` (polls call_queue every `OUTBOUND_POLL_INTERVAL` s) and `FollowUpScheduler` (every `FOLLOW_UP_POLL_INTERVAL` s).

**Startup order:** PostgreSQL → Ollama (pull `qwen2.5:7b-instruct-q3_K_M` and `nomic-embed-text` once: `ollama pull ...`) → FastAPI (8000) → Streamlit chatbot (8501) → Streamlit dashboard (8502).

---

## 8. Recommended Cloud Architecture

### Option A — Single GPU VM + Docker Compose (simplest, matches current repo)

- 1 × GPU VM (e.g. AWS `g4dn.xlarge` 16 GB T4, GCP `a2-highgpu-1g` or `n1` + T4, Azure `NC4as_T4_v3`), Ubuntu 22.04/24.04, 30 GB disk.
- Install NVIDIA driver + **nvidia-container-toolkit** so containers see the GPU.
- Use the repo's `docker-compose.yml` + add `deploy.resources.reservations.devices` (gpu) on the `fastapi` service (or run Ollama + FastAPI on the host).
- Reverse proxy with TLS (Caddy is the least config: `bot.domain.com` → 8000, `chat.domain.com` → 8501, `admin.domain.com` → 8502). Set `TUNNEL_HOST=bot.domain.com`.
- Persistent volumes: `chroma_local_db/`, `data/`, `app/static/audio/`, `logs/`, `ollama_data`, `postgres_data`, Kokoro cache.
- Managed Postgres (RDS/Cloud SQL) can replace the container — just set `DATABASE_URL`.

### Option B — Split services

- GPU inference host: Ollama (can also run Whisper+Kokoro if FastAPI placed here).
- App host: FastAPI + Streamlit (CPU ok only if Whisper/Kokoro run on GPU host — currently they run **in-process** with FastAPI, so the FastAPI host itself needs the GPU unless you refactor STT/TTS into remote services).

### Option C — Fully managed pieces

- Ollama → any Ollama-compatible endpoint (change `OLLAMA_URL`), or swap LLM for a hosted API (requires code change in `app/rag.py`, `app/database.py`, `app/main.py`).
- ChromaDB → Chroma Cloud (change `CHROMA_DB_PATH` usage in `app/rag.py`).
- Whisper/Kokoro → cloud STT/TTS APIs (requires refactor of `app/voice_handler.py`).

**⚠️ Constraints for any PaaS/serverless (Fly.io, Railway, Render, Lambda):**
- GPU + CUDA runtime required for acceptable latency (most PaaS don't offer it — pick GPU VMs).
- `~/.cache` and file storage must be writable/persistent (Kokoro, Whisper, `data/`, `static/audio/`).
- Long-running WebSockets/SSE must not be idle-terminated.
- Background tasks after webhook response must survive (no scale-to-zero).
- One instance minimum (in-memory live-call state).

---

## 9. Deployment Checklist

1. **Provision** GPU VM (T4+), Ubuntu, 30 GB disk, static IP + DNS record (`bot.yourdomain.com`).
2. **Install** NVIDIA driver, CUDA 12 toolkit, `nvidia-container-toolkit`; Python 3.11; `ffmpeg`.
3. **Clone repo**, `pip install -r requirements.txt` + the extra packages listed in §4 (or use the Dockerfile).
4. **Start Postgres** (compose or managed); confirm `DATABASE_URL` reachable.
5. **Start Ollama**; pull models: `ollama pull qwen2.5:7b-instruct-q3_K_M && ollama pull nomic-embed-text`.
6. **Ship `chroma_local_db/`** and `content/` (or let the app rebuild from the PDF).
7. **Set env vars** (§5) — Twilio credentials, `DATABASE_URL`, `TUNNEL_HOST=<public domain>`, SMTP.
8. **Launch FastAPI**: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`; wait for warmup logs (ChromaDB + Whisper + Kokoro ready).
9. **Verify** `curl https://bot.yourdomain.com/` → JSON health check shows `"database": "connected"`, `"twilio_configured": true`.
10. **Launch UIs**: Streamlit chatbot on 8501, dashboard on 8502 (behind TLS via proxy).
11. **Twilio Console** (once DNS is live — this is the step that currently needs the tunnel):
    - Phone number → Voice: `https://bot.yourdomain.com/twilio/voice` (GET) + status callback `https://bot.yourdomain.com/twilio/outbound/status` (POST).
    - WhatsApp sandbox → `https://bot.yourdomain.com/twilio/whatsapp` (POST).
12. **Test end-to-end**: call the Twilio number (IVR → AI stream); WhatsApp text + voice note; offer-letter flow; dashboard lead updates.
13. **Warm the Kokoro cache** once (first TTS or a test call) so subsequent calls are fast.
14. **Security**: dashboard (8502) should be authenticated or IP-restricted — it exposes lead PII; restrict Postgres/Ollama to private network; keep Twilio + SMTP secrets in the platform secret manager.

---

## 10. Key Code References (for the deployment engineer)

| Concern | File |
|---|---|
| FastAPI app, endpoints, Twilio webhooks, warmup | `app/main.py` |
| All env-var settings | `app/config.py` |
| DB init + schema | `app/database.py`, `app/leads/schema.py`, `app/offers/schema.py`, `app/sentiment/schema.py` |
| RAG (ChromaDB + Ollama LLM) | `app/rag.py` |
| Live-call STT/TTS session | `app/voice_handler.py` |
| GPU/device detection | `app/platform.py` |
| Outbound calls / follow-ups | `app/outbound/caller.py`, `app/outbound/scheduler.py` |
| Offer letters (PDF + email + WhatsApp) | `app/offers/service.py`, `app/offers/pdf.py`, `app/emailer.py` |
| Sentiment scoring | `app/sentiment/scorer.py`, `app/sentiment/categorizer.py` |
| Streamlit UIs | `app.py` (chatbot), `dashboard.py` (admin), `app/streamlit_backend.py` |
| Docker | `Dockerfile`, `docker-compose.yml` |
| Demo data seed | `scripts/seed_demo_data.py` |
| Local tunnel/health scripts (dev-only reference) | `check_and_tunnel.ps1`, `start_services.ps1`, `tunnel_streamlit.ps1` |
