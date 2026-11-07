# Voice AI Lead Scoring — Integration Guide

**Version:** 0.5.0  
**Last Updated:** 2026-08-10  

A step-by-step guide to integrate this Voice AI Lead Scoring & Sentiment Analysis platform into any project that needs sales call sentiment analysis, lead scoring, and automated lead categorization.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Tech Stack Validation](#3-tech-stack-validation)
4. [Dependencies & Infrastructure](#4-dependencies--infrastructure)
5. [Integration Steps](#5-integration-steps)
   - [5.1 Clone & Configure](#51-clone--configure)
   - [5.2 Start Infrastructure](#52-start-infrastructure)
   - [5.3 Run Database Migrations](#53-run-database-migrations)
   - [5.4 Start the Services](#54-start-the-services)
   - [5.5 Verify the Stack](#55-verify-the-stack)
6. [Build, Run & Validate](#6-build-run--validate)
7. [Integrating With Your Project](#7-integrating-with-your-project)
   - [7.1 Sending Call Audio for Analysis](#71-sending-call-audio-for-analysis)
   - [7.2 Querying Lead Scores](#72-querying-lead-scores)
   - [7.3 Receiving Categorization Results](#73-receiving-categorization-results)
   - [7.4 Ad-Hoc Transcript Scoring (Dry-Run)](#74-ad-hoc-transcript-scoring-dry-run)
8. [Manual Sentiment Analysis Testing Guide](#8-manual-sentiment-analysis-testing-guide)
   - [8.1 Test 1: Health Check & Readiness](#81-test-1-health-check--readiness)
   - [8.2 Test 2: Ingest a Call Event](#82-test-2-ingest-a-call-event)
   - [8.3 Test 3: Score a Transcript Directly (Dry-Run)](#83-test-3-score-a-transcript-directly-dry-run)
   - [8.4 Test 4: Record Consent](#84-test-4-record-consent)
   - [8.5 Test 5: Query Lead Score & History](#85-test-5-query-lead-score--history)
   - [8.6 Test 6: Get Categorization Explanation](#86-test-6-get-categorization-explanation)
   - [8.7 Test 7: End-to-End Pipeline Verification](#87-test-7-end-to-end-pipeline-verification)
   - [8.8 Test 8: Negative / Edge Case Testing](#88-test-8-negative--edge-case-testing)
9. [API Reference](#9-api-reference)
10. [Configuration Reference](#10-configuration-reference)
11. [Troubleshooting](#11-troubleshooting)
12. [Integration Patterns & Code Examples](#12-integration-patterns--code-examples)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        YOUR PROJECT                                   │
│                                                                       │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────┐                 │
│  │ Telephony │    │  Your CRM /  │    │  Your Agent │                 │
│  │  (Twilio) │    │  Dashboard   │    │  (MCP/REST) │                 │
│  └─────┬─────┘    └──────▲───────┘    └──────▲──────┘                 │
│        │                 │                    │                       │
│        │  Webhook        │  REST API          │  MCP Adapter          │
│        ▼                 │                    │                       │
│  ┌─────────────────────────────────────────────────────┐             │
│  │         VOICE AI LEAD SCORING PLATFORM               │             │
│  │                                                      │             │
│  │  ┌─────────┐   ┌────────┐   ┌────────────────────┐  │             │
│  │  │ Webhook │──▶│ Kafka  │──▶│  Kafka→Temporal     │  │             │
│  │  │ Receiver│   │ (Queue)│   │  Bridge Consumer    │  │             │
│  │  └─────────┘   └────────┘   └─────────┬──────────┘  │             │
│  │                                       │              │             │
│  │                              ┌────────▼──────────┐  │             │
│  │                              │  Temporal Workflow │  │             │
│  │                              │  (Durable, Retry)  │  │             │
│  │                              └────────┬──────────┘  │             │
│  │                                       │              │             │
│  │    ┌──────────────────────────────────┼──────────┐  │             │
│  │    │  Node 1          │               │          │  │             │
│  │    │  STT (Deepgram)  │  SLM Extractor│          │  │             │
│  │    │  Prosody (Hume)  │  (Llama/GPT)  │          │  │             │
│  │    └────────┬─────────┴──────┬────────┘          │  │             │
│  │             │                │                    │  │             │
│  │    ┌────────▼────────────────▼──────────┐        │  │             │
│  │    │  Node 2: Trajectory + Hybrid Score │        │  │             │
│  │    │  EWMA Momentum, Variance, LightGBM │        │  │             │
│  │    └────────────────┬───────────────────┘        │  │             │
│  │                     │                            │  │             │
│  │    ┌────────────────▼───────────────────┐        │  │             │
│  │    │  Node 3: Category + Action + Guard │        │  │             │
│  │    │  Hot/Warm/At-Risk/Disqualified     │        │  │             │
│  │    └────────────────┬───────────────────┘        │  │             │
│  │                     │                            │  │             │
│  │    ┌────────────────▼───────────────────┐        │  │             │
│  │    │  Node 4: Audit (LLM-as-Judge)      │        │  │             │
│  │    └────────────────────────────────────┘        │  │             │
│  │                                                  │  │             │
│  │    ┌──────────────────────────────────────┐      │  │             │
│  │    │  PostgreSQL 15  │  Redis  │ Temporal  │      │  │             │
│  │    └──────────────────────────────────────┘      │  │             │
│  └──────────────────────────────────────────────────┘  │             │
└──────────────────────────────────────────────────────────────────────┘
```

**Key components and what they do:**

| Component | Role | Default Port |
|-----------|------|-------------|
| **Webhook API** (FastAPI) | Receives call events from telephony systems (Twilio-compatible) | `8000` |
| **Kafka** | Decouples ingestion from processing; provides backpressure and replay | `9092` |
| **Kafka→Temporal Bridge** | Consumes Kafka events, starts idempotent Temporal workflows | — |
| **Temporal** | Durable workflow orchestration with retries, timeouts, and state management | `7233` |
| **Temporal Worker** | Executes activities: STT, prosody, extraction, scoring, categorization | — |
| **MCP Adapter** | Read-only REST facade exposing lead scores, history, dry-run scoring, and explanations | `8001` |
| **PostgreSQL 15** | Primary data store for conversations, sentiment, lead profiles, scoring history | `5432` |
| **Redis** | Circuit breaker state, EWMA cache, and vendor health tracking | `6379` |
| **Temporal UI** | Dashboard for monitoring workflow executions | `8080` |

---

## 2. Prerequisites

### 2.1 Required Software

| Software | Minimum Version | Purpose |
|----------|----------------|---------|
| **Docker** | 24.0+ | Container runtime for all services |
| **Docker Compose** | 2.20+ | Multi-service orchestration |
| **Python** | 3.11+ | Local development, running tests, CLI tools |
| **pip** | 23.0+ | Python package installation |
| **Git** | 2.40+ | Source control |
| **curl** / **httpie** | Any | Manual API testing (optional) |

### 2.2 Hardware Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **CPU** | 4 cores | 8+ cores |
| **RAM** | 8 GB | 16+ GB |
| **Disk** | 10 GB free | 20+ GB free (for Docker images + data) |

**Note:** The SLM extraction step can optionally use a self-hosted LLM (vLLM). If you run vLLM locally, GPU memory requirements depend on the model. Without a GPU, the system uses deterministic fallback data for development and testing.

### 2.3 External Service Accounts (Optional in Dev)

For production or when using real vendors instead of fallback data:

| Service | Purpose | Sign-Up URL |
|---------|---------|-------------|
| **Deepgram** | Speech-to-Text (STT) | https://deepgram.com |
| **Hume AI** | Prosody/emotion analysis | https://hume.ai |
| **Twilio** | Telephony webhook source | https://twilio.com |

**In development mode, all three services have built-in fallback data** — no API keys required.

---

## 3. Tech Stack Validation

Run this checklist to confirm your environment is ready:

### 3.1 Verify Docker

```bash
docker --version
# Expected: Docker version 24.x.x or higher

docker compose version
# Expected: Docker Compose version v2.x.x or higher

docker info
# Should show "Server Version: ..." without errors
```

### 3.2 Verify Python

```bash
python --version
# Expected: Python 3.11.x or higher

pip --version
# Expected: pip 23.x.x or higher
```

### 3.3 Verify Port Availability

Ensure these ports are free on your host:

```
5432  — PostgreSQL
6379  — Redis
7233  — Temporal
8000  — Webhook API
8001  — MCP Adapter API
8080  — Temporal UI
9092  — Kafka
```

Check with:

```bash
# Windows (PowerShell)
netstat -ano | Select-String "5432|6379|7233|8000|8001|8080|9092"

# Linux / macOS
lsof -i :5432 -i :6379 -i :7233 -i :8000 -i :8001 -i :8080 -i :9092
```

### 3.4 Validate Project Dependencies

```bash
cd voice-ai-lead-scoring
pip install -e ".[dev]"
# Should install without errors

python -c "import fastapi; import temporalio; import aiokafka; import sqlalchemy; print('All core deps OK')"
# Expected: All core deps OK
```

---

## 4. Dependencies & Infrastructure

### 4.1 Python Dependencies (`pyproject.toml`)

```
Core:
  fastapi >= 0.115.0          — Web framework
  uvicorn[standard] >= 0.30.0 — ASGI server
  pydantic >= 2.5.0           — Data validation
  pydantic-settings >= 2.0.0  — Env-based configuration
  sqlalchemy >= 2.0.0         — ORM
  asyncpg >= 0.29.0           — Async PostgreSQL driver
  alembic >= 1.14.0           — Database migrations
  pgvector >= 0.3.0           — Vector embeddings (planned)
  temporalio >= 1.7.0         — Workflow orchestration SDK
  aiokafka >= 0.11.0          — Async Kafka client
  redis >= 5.0.0              — Redis client
  httpx >= 0.27.0             — Async HTTP client
  lightgbm >= 4.5.0           — Gradient boosting (predictive layer)
  scikit-learn >= 1.5.0       — ML utilities
  numpy >= 1.26.0             — Numerical computing
  pandas >= 2.2.0             — Data manipulation
  python-dotenv >= 1.0.0      — .env file loading

Dev:
  pytest >= 8.0.0             — Test framework
  pytest-asyncio >= 0.23.0    — Async test support
  pytest-cov >= 5.0.0         — Coverage reporting
```

### 4.2 Infrastructure Services (`docker-compose.yml`)

| Service | Image | Purpose |
|---------|-------|---------|
| `postgres` | `postgres:15-alpine` | Primary database |
| `redis` | `redis:7-alpine` | Caching + circuit breaker |
| `kafka` | `bitnami/kafka:3.7` | Message queue (KRaft mode, no ZooKeeper) |
| `temporal` | `temporalio/auto-setup:latest` | Workflow engine |
| `temporal-ui` | `temporalio/ui:latest` | Workflow dashboard |
| `webhook` | (built from Dockerfile) | FastAPI webhook receiver |
| `worker` | (built from Dockerfile) | Temporal activity worker |
| `consumer` | (built from Dockerfile) | Kafka→Temporal bridge |
| `mcp-server` | (built from Dockerfile) | Read-only REST adapter |

---

## 5. Integration Steps

### 5.1 Clone & Configure

```bash
# 1. Navigate to your project or workspace
cd /path/to/your/project

# 2. Clone this repository
git clone https://github.com/your-org/voice-ai-lead-scoring.git
cd voice-ai-lead-scoring

# 3. Copy and review the environment file
cp .env.example .env
```

**Required `.env` changes for your project:**

```bash
# .env — edit these values for your environment

# Environment
ENV=development              # development | staging | production
DEBUG=true                   # Enable verbose logging

# Database — change credentials for production
DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/lead_scoring

# External APIs — add your keys for production
TWILIO_AUTH_TOKEN=           # Your Twilio auth token
DEEPGRAM_API_KEY=            # Your Deepgram API key
HUME_API_KEY=                # Your Hume AI API key

# LLM endpoint for SLM extraction — set to your vLLM/OpenAI-compatible endpoint
SLM_MODEL_ENDPOINT=http://vllm:8000/v1
SLM_MODEL_NAME=llama-3.3-8b

# Scoring weights — tune these for your business
W1=0.30                      # Weight: latest call sentiment
W2=0.30                      # Weight: momentum (delta)
W3=0.25                      # Weight: buying intent
W4=0.15                      # Weight: friction penalty

# Predictive layer gate
MIN_LABELED_OUTCOMES=100     # Minimum labeled CRM outcomes before ML activates

# Audit
AUDIT_SAMPLE_RATE=0.10       # 10% of calls audited
```

### 5.2 Start Infrastructure

```bash
# Start all services (first run builds Docker images — may take 2-5 minutes)
docker compose up --build

# Or run in background:
docker compose up --build -d
```

Wait for all services to be healthy. Check with:

```bash
docker compose ps
# All services should show "healthy" or "running"
```

### 5.3 Run Database Migrations

```bash
# Apply all migrations to create tables
alembic upgrade head

# Verify tables were created
docker compose exec postgres psql -U user -d lead_scoring -c "\dt"
```

Expected tables: `conversations`, `call_sentiments`, `lead_profiles`, `lead_score_history`, `audit_findings`, `hitl_review_queue`, `call_embeddings`, `jurisdiction_consent_rules`, `jurisdiction_retention_policies`, `lead_score_history_outcomes`, `alembic_version`.

### 5.4 Start the Services

All services start via `docker compose up`. Here's what each does:

| Service | Command | Description |
|---------|---------|-------------|
| `webhook` | `uvicorn backend.main:app --host 0.0.0.0 --port 8000` | Public API: webhooks + consent + data rights |
| `worker` | `python -m backend.temporal.worker` | Temporal worker polling `call-processing` task queue |
| `consumer` | `python -m backend.temporal.consumer` | Kafka consumer bridging to Temporal workflows |
| `mcp-server` | `uvicorn backend.mcp_server.server:app --host 0.0.0.0 --port 8001` | Read-only lead intelligence API |

### 5.5 Verify the Stack

```bash
# 1. Health check
curl http://localhost:8000/health
# Expected: {"status":"ok"}

# 2. Readiness check
curl http://localhost:8000/ready
# Expected: {"status":"ready"}

# 3. MCP adapter health
curl http://localhost:8001/health
# Expected: 200 OK

# 4. Check Temporal UI
# Open http://localhost:8080 in your browser
# You should see the Temporal dashboard

# 5. Verify Kafka topics
docker compose exec kafka kafka-topics.sh --list --bootstrap-server localhost:9092
# Expected: call-events, audit-tasks, call-events-dlq

# 6. Run the test suite
pip install -e ".[dev]"
pytest -q
# Expected: all tests pass
```

---

## 6. Build, Run & Validate

### Quick-Start (One Command)

```bash
# Clone → configure → start → migrate → test
git clone git@github.com:gsachin/voice-ai-lead-scoring.git && cd voice-ai-lead-scoring && \
cp .env.example .env && \
docker compose up --build -d && \
sleep 30 && \
alembic upgrade head && \
pytest -q && \
echo "✅ Platform is running at http://localhost:8000"
```

### Using Make (Shortcuts)

```bash
make install     # pip install -e ".[dev]"
make up          # docker compose up --build
make down        # docker compose down -v
make migrate     # alembic upgrade head
make test        # pytest -q
make worker      # python -m backend.temporal.worker
make consume     # python -m backend.temporal.consumer
```

### Validation Checklist

- [ ] `curl http://localhost:8000/health` returns `{"status":"ok"}`
- [ ] `curl http://localhost:8000/ready` returns `{"status":"ready"}`
- [ ] Temporal UI is accessible at `http://localhost:8080`
- [ ] `docker compose ps` shows all services running/healthy
- [ ] `pytest -q` passes all tests
- [ ] Kafka topics `call-events`, `audit-tasks`, `call-events-dlq` exist

---

## 7. Integrating With Your Project

### 7.1 Sending Call Audio for Analysis

**Integration point:** `POST /webhooks/telephony/call-events`

This is the primary entry point. Your telephony system (Twilio, or any system that can POST JSON) sends call events here after a call completes.

```bash
curl -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d '{
    "call_sid": "CA1234567890abcdef",
    "event_type": "call.completed",
    "direction": "inbound",
    "audio_url": "https://your-storage.example.com/calls/recording-123.mp3",
    "duration_seconds": 180,
    "lead_id": "550e8400-e29b-41d4-a716-446655440000",
    "conversation_id": "660e8400-e29b-41d4-a716-446655440001",
    "metadata": {
      "campaign": "spring_outreach_2026",
      "agent_id": "agent-42"
    }
  }'
```

**Expected response:**
```json
{
  "workflow_id": "CA1234567890abcdef:call.completed",
  "status": "accepted"
}
```

**What happens next:**
1. Webhook publishes the event to Kafka topic `call-events`
2. The Kafka→Temporal bridge consumer picks it up
3. A `CallProcessingWorkflow` starts in Temporal
4. The workflow runs: STT → Prosody → Sentiment Extraction → Scoring → Categorization → Guardrail → Audit
5. Results are persisted to PostgreSQL

**Tracking progress:**
- Open Temporal UI at `http://localhost:8080`
- Find your workflow by ID: `CA1234567890abcdef:call.completed`
- Watch each activity complete in real-time

### 7.2 Querying Lead Scores

**Integration point:** `GET /leads/{lead_id}/score` (MCP Adapter, port `8001`)

After the pipeline processes a call, query the computed lead score:

```bash
curl http://localhost:8001/leads/550e8400-e29b-41d4-a716-446655440000/score
```

**Response:**
```json
{
  "lead_id": "550e8400-e29b-41d4-a716-446655440000",
  "current_category": "Hot",
  "overall_sentiment_score": 0.78,
  "sentiment_trajectory": "Upward",
  "conversion_probability": 0.82,
  "updated_at": "2026-08-10T14:30:00+00:00"
}
```

**Score history:**
```bash
curl http://localhost:8001/leads/550e8400-e29b-41d4-a716-446655440000/score-history?limit=5
```

### 7.3 Receiving Categorization Results

The platform categorizes every lead into one of five buckets:

| Category | Meaning | Suggested Action |
|----------|---------|-----------------|
| **Hot** | S_lead ≥ 0.70, strong BANT, positive momentum, P_convert ≥ 0.55 | Assign to AE immediately |
| **Warm** | 0.35 ≤ S_lead < 0.70 or high score with some gaps | Follow up, nurture |
| **Nurture** | -0.20 ≤ S_lead < 0.35, no risk signals | Add to drip campaign |
| **At-Risk** | delta_S drop > 0.35 or rising friction | Escalate to CS, intervene |
| **Disqualified** | S_lead < -0.20 or explicit non-fit objection | Suppress for 90 days |

**To integrate with your CRM:**

Poll the MCP adapter periodically, or extend the `execute_action` activity in `backend/agents/action_executor.py` to call your CRM's API.

**Get grounded explanation:**
```bash
curl -X POST http://localhost:8001/tools/explain-categorization \
  -H "Content-Type: application/json" \
  -d '{"lead_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

**Response:**
```json
{
  "lead_id": "550e8400-e29b-41d4-a716-446655440000",
  "category": "Hot",
  "rationale": "S_lead 0.78 >= 0.70 ✓ AND P_convert 0.82 >= 0.55 ✓ AND delta_S 0.12 > 0 (positive momentum) ✓ AND Strong BANT qualification ✓ → Hot",
  "grounded": true,
  "scoring_components": {
    "s_latest": 0.65,
    "delta_s": 0.12,
    "i_intent": 0.85,
    "friction": 0.10,
    "ewma_baseline": 0.53,
    "trajectory_flag": null
  },
  "model_version": "rules-v1"
}
```

### 7.4 Ad-Hoc Transcript Scoring (Dry-Run)

**Integration point:** `POST /tools/score-transcript` (MCP Adapter, port `8001`)

Score a transcript without persisting anything — useful for pre-screening, testing, or building UIs that preview scores:

```bash
curl -X POST http://localhost:8001/tools/score-transcript \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Customer: I'\''ve been looking at your enterprise plan and I think it fits our needs perfectly. The pricing works within our Q3 budget and I have sign-off from our CTO. When can we start the onboarding?\n\nAgent: We can get you started as early as next week. Let me walk you through the setup process.\n\nCustomer: That sounds great. Let'\''s move forward.",
    "prosody": {"emotion": "excited", "arousal": 0.72, "confidence": 0.88}
  }'
```

**Response:**
```json
{
  "s_lead": 0.72,
  "p_convert": 0.86,
  "trajectory": "Stable",
  "i_intent": 0.75,
  "friction": 0.05,
  "extraction": {
    "primary_emotion": "excited",
    "buying_intent_score": 0.85,
    "objections": [],
    "friction_score": 0.05
  },
  "note": "Dry-run only — no data persisted, no actions executed."
}
```

---

## 8. Manual Sentiment Analysis Testing Guide

This section walks a tester through manually validating every capability of the sentiment analysis pipeline. Run these tests after initial setup to confirm the system works correctly.

### 8.1 Test 1: Health Check & Readiness

**Goal:** Verify all services are running.

```bash
# Test 1a: Webhook API health
curl -s http://localhost:8000/health | python -m json.tool
# ✅ Expected: {"status": "ok"}

# Test 1b: Webhook API readiness
curl -s http://localhost:8000/ready | python -m json.tool
# ✅ Expected: {"status": "ready"}

# Test 1c: MCP Adapter health
curl -s http://localhost:8001/health | python -m json.tool
# ✅ Expected: 200 OK

# Test 1d: All Docker services healthy
docker compose ps
# ✅ Expected: All services show "healthy" or "running" (not "restarting" or "unhealthy")
```

---

### 8.2 Test 2: Ingest a Call Event (Webhook)

**Goal:** Verify a call event is accepted and published to Kafka.

```bash
# Generate a unique call_sid for each test (prevents idempotency rejection)
CALL_SID="TEST-$(date +%s)"
LEAD_ID="11111111-1111-1111-1111-111111111111"
CONV_ID="22222222-2222-2222-2222-222222222222"

curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d "{
    \"call_sid\": \"${CALL_SID}\",
    \"event_type\": \"call.completed\",
    \"direction\": \"inbound\",
    \"audio_url\": \"https://example.com/test-call.mp3\",
    \"duration_seconds\": 120,
    \"lead_id\": \"${LEAD_ID}\",
    \"conversation_id\": \"${CONV_ID}\",
    \"metadata\": {\"test\": true}
  }" | python -m json.tool
```

**✅ Expected response:**
```json
{
  "workflow_id": "TEST-1723300000:call.completed",
  "status": "accepted"
}
```

**Verification in Temporal UI:**
1. Open http://localhost:8080
2. Click on "Recent Workflows"
3. Find workflow `TEST-1723300000:call.completed`
4. Click into it to see the execution history (STT → Prosody → Extraction → Scoring → Categorization)

**❌ Failure indicators:**
- `401` — signature verification issue (only in non-dev mode)
- `422` — invalid event payload (check field names/types)
- No workflow in Temporal UI — Kafka→Temporal bridge may be down

---

### 8.3 Test 3: Score a Transcript Directly (Dry-Run)

**Goal:** Verify the sentiment extraction + scoring pipeline works without any external dependencies (no Temporal, no Kafka — just LLM + scoring logic).

#### Test 3a: Positive Sentiment Transcript

```bash
curl -s -X POST http://localhost:8001/tools/score-transcript \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Customer: I love your product. We have budget approved and I want to move forward with the annual plan. Can you send over the contract?\n\nAgent: Absolutely! I will have the contract to you by end of day.\n\nCustomer: Perfect, I will sign it as soon as I receive it."
  }' | python -m json.tool
```

**✅ Expected:** `s_lead > 0.5`, `buying_intent_score > 0.7`, `friction_score < 0.3`, few or no objections, positive primary emotion.

#### Test 3b: Negative / Friction-Heavy Transcript

```bash
curl -s -X POST http://localhost:8001/tools/score-transcript \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Customer: This is way too expensive. Your competitor offers the same thing for half the price. I am not sure we can justify this cost.\n\nAgent: I understand your concern about pricing. Let me see if we can work something out.\n\nCustomer: I am also worried about the contract terms. Our legal team will take weeks to review this."
  }' | python -m json.tool
```

**✅ Expected:** `s_lead < 0.0`, `buying_intent_score < 0.3`, `friction_score > 0.5`, pricing and competitor objections present, negative primary emotion.

#### Test 3c: Neutral / Mixed Transcript

```bash
curl -s -X POST http://localhost:8001/tools/score-transcript \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Customer: The product seems interesting but I need to discuss it with my team. Can you send me some documentation?\n\nAgent: Of course! I will email you the spec sheet and case studies.\n\nCustomer: Thanks. I will review them and get back to you next week."
  }' | python -m json.tool
```

**✅ Expected:** `s_lead` around 0.0–0.4, moderate buying intent, timeline objection, neutral/mixed emotion.

#### Test 3d: Empty / Invalid Transcript

```bash
curl -s -X POST http://localhost:8001/tools/score-transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": ""}' | python -m json.tool
```

**✅ Expected:** `422` error — "transcript is required".

---

**📋 Manual Tester's Sentiment Validation Table:**

| Test Case | Expected s_lead Range | Expected Buying Intent | Expected Friction | Notes |
|-----------|----------------------|----------------------|-------------------|-------|
| Very positive (budget + authority + timeline) | 0.5 to 1.0 | 0.6 to 1.0 | 0.0 to 0.2 | Should show no objections |
| Very negative (pricing + competitor) | -1.0 to -0.2 | 0.0 to 0.3 | 0.5 to 1.0 | Should have pricing + competitor objections |
| Neutral/information-gathering | -0.2 to 0.4 | 0.2 to 0.5 | 0.2 to 0.5 | May have timeline objection |
| Empty transcript | N/A (error) | N/A | N/A | 422 validation error |

---

### 8.4 Test 4: Record Consent

**Goal:** Verify consent recording works (required for compliance).

```bash
curl -s -X POST http://localhost:8000/internal/v1/conversations/${CONV_ID}/consent \
  -H "Content-Type: application/json" \
  -d '{
    "confirmed_at": "2026-08-10T14:00:00Z",
    "method": "explicit_opt_in"
  }' | python -m json.tool
```

**✅ Expected:** `{"status": "ok", "method": "explicit_opt_in"}`

**Test idempotency (double-send):**
```bash
# Send again with same conversation_id
curl -s -X POST http://localhost:8000/internal/v1/conversations/${CONV_ID}/consent \
  -H "Content-Type: application/json" \
  -d '{
    "confirmed_at": "2026-08-10T15:00:00Z",
    "method": "explicit_opt_in"
  }' | python -m json.tool
```

**✅ Expected:** `404` — "Not found or consent already recorded" (idempotent guard works).

---

### 8.5 Test 5: Query Lead Score & History

**Goal:** Verify scores are persisted and queryable.

```bash
# After the pipeline has processed a call, query the lead
curl -s http://localhost:8001/leads/${LEAD_ID}/score | python -m json.tool
```

**✅ Expected:** Returns score object with all fields populated.

```bash
# Query score history
curl -s "http://localhost:8001/leads/${LEAD_ID}/score-history?limit=5" | python -m json.tool
```

**✅ Expected:** Array of historical scores, each with `scoring_components` and `model_version`.

**Test non-existent lead:**
```bash
curl -s http://localhost:8001/leads/00000000-0000-0000-0000-000000000000/score | python -m json.tool
```

**✅ Expected:** `404` — "Lead 00000000-0000-0000-0000-000000000000 not found".

---

### 8.6 Test 6: Get Categorization Explanation

**Goal:** Verify the grounded explanation returns actual values (not a template).

```bash
curl -s -X POST http://localhost:8001/tools/explain-categorization \
  -H "Content-Type: application/json" \
  -d "{\"lead_id\": \"${LEAD_ID}\"}" | python -m json.tool
```

**✅ Expected:** Response includes `"grounded": true` and a rationale string that cites actual numeric values, not generic text.

**Test with no history:**
```bash
curl -s -X POST http://localhost:8001/tools/explain-categorization \
  -H "Content-Type: application/json" \
  -d '{"lead_id": "00000000-0000-0000-0000-000000000000"}' | python -m json.tool
```

**✅ Expected:** `404` — lead not found.

---

### 8.7 Test 7: End-to-End Pipeline Verification

**Goal:** Send a call through the full pipeline and verify every stage completed.

**Step 1: Send a new call event**
```bash
E2E_SID="E2E-$(date +%s)"
E2E_LEAD="33333333-3333-3333-3333-333333333333"
E2E_CONV="44444444-4444-4444-4444-444444444444"

curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d "{
    \"call_sid\": \"${E2E_SID}\",
    \"event_type\": \"call.completed\",
    \"direction\": \"inbound\",
    \"audio_url\": \"https://example.com/e2e-test.mp3\",
    \"duration_seconds\": 90,
    \"lead_id\": \"${E2E_LEAD}\",
    \"conversation_id\": \"${E2E_CONV}\"
  }" | python -m json.tool
```

**✅ Expected:** `202 Accepted` with `workflow_id`.

**Step 2: Check Temporal UI**
1. Go to http://localhost:8080
2. Find the workflow by its ID (e.g., `E2E-1723300000:call.completed`)
3. Verify the execution history shows these activities in order:
   - `transcribe` (STT)
   - `analyze` (Prosody) — runs in parallel with STT
   - `extract_sentiment` (SLM Extraction)
   - `compute_score` (Trajectory Scoring)
   - `categorize_and_act` (Categorization)
   - `check` (Guardrail, only if Hot/Disqualified)
   - `sample_audit` (if sampled)

**Step 3: Verify database records**
```bash
docker compose exec postgres psql -U user -d lead_scoring -c \
  "SELECT id, lead_id, call_status, direction FROM conversations WHERE call_sid='${E2E_SID}';"

docker compose exec postgres psql -U user -d lead_scoring -c \
  "SELECT lead_id, primary_emotion, buying_intent_score, s_call FROM call_sentiments WHERE lead_id='${E2E_LEAD}';"

docker compose exec postgres psql -U user -d lead_scoring -c \
  "SELECT lead_id, current_category, overall_sentiment_score, sentiment_trajectory FROM lead_profiles WHERE lead_id='${E2E_LEAD}';"
```

**✅ Expected:** Records exist in all three tables.

**Step 4: Query lead score via API**
```bash
curl -s http://localhost:8001/leads/${E2E_LEAD}/score | python -m json.tool
```

**✅ Expected:** Score is returned, matching the database values.

---

### 8.8 Test 8: Negative / Edge Case Testing

#### Test 8a: Invalid Signature (non-dev mode)

Set `ENV=production` in `.env` and restart. Then send a request without a valid Twilio signature:

```bash
curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d '{"call_sid":"test","event_type":"test","direction":"inbound","audio_url":"url","duration_seconds":1,"lead_id":"11111111-1111-1111-1111-111111111111","conversation_id":"22222222-2222-2222-2222-222222222222"}'
```

**✅ Expected:** `401` — "Invalid signature".

Reset `ENV=development` after this test.

#### Test 8b: Invalid Event Payload

```bash
curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d '{"bad_field": "missing required fields"}'
```

**✅ Expected:** `422` — validation error details.

#### Test 8c: Duplicate Event (Idempotency)

Send the same `call_sid` + `event_type` twice:

```bash
DUP_SID="DUP-$(date +%s)"
for i in 1 2; do
  curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
    -H "Content-Type: application/json" \
    -d "{\"call_sid\":\"${DUP_SID}\",\"event_type\":\"call.completed\",\"direction\":\"inbound\",\"audio_url\":\"url\",\"duration_seconds\":60,\"lead_id\":\"11111111-1111-1111-1111-111111111111\",\"conversation_id\":\"22222222-2222-2222-2222-222222222222\"}"
  echo ""
done
```

**✅ Expected:** Both return `202`. Only ONE workflow runs (check Temporal UI — second is a no-op).

#### Test 8d: Subject Rights Deletion

```bash
DELETE_LEAD="55555555-5555-5555-5555-555555555555"

# First, create a lead by sending a call event
curl -s -X POST http://localhost:8000/webhooks/telephony/call-events \
  -H "Content-Type: application/json" \
  -d "{\"call_sid\":\"DEL-$(date +%s)\",\"event_type\":\"call.completed\",\"direction\":\"inbound\",\"audio_url\":\"url\",\"duration_seconds\":60,\"lead_id\":\"${DELETE_LEAD}\",\"conversation_id\":\"66666666-6666-6666-6666-666666666666\"}"

# Wait a few seconds for processing, then delete
sleep 10
curl -s -X DELETE "http://localhost:8000/internal/v1/leads/${DELETE_LEAD}/data" | python -m json.tool
```

**✅ Expected:** `{"status": "deleted", "lead_id": "...", "reason": "subject_rights_request"}`.

---

## 9. API Reference

### 9.1 Webhook API (Port 8000)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/health` | Health check | None |
| `GET` | `/ready` | Readiness check | None |
| `POST` | `/webhooks/telephony/call-events` | Ingest a call event | HMAC-SHA256 (non-dev) |
| `POST` | `/internal/v1/conversations/{conversation_id}/consent` | Record consent | Internal |
| `DELETE` | `/internal/v1/leads/{lead_id}/data` | Subject-rights deletion | Internal |

### 9.2 MCP Adapter API (Port 8001)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/leads/{lead_id}/score` | Get current lead score + category |
| `GET` | `/leads/{lead_id}/score-history?limit=10` | Get historical scores |
| `POST` | `/tools/explain-categorization` | Grounded categorization rationale |
| `POST` | `/tools/score-transcript` | Dry-run scoring (no persistence) |

### 9.3 Webhook Event Schema (`TelephonyEvent`)

```json
{
  "call_sid": "string (required) — unique call identifier from telephony provider",
  "event_type": "string (required) — e.g., 'call.completed'",
  "direction": "string (required) — 'inbound' or 'outbound'",
  "audio_url": "string (required) — URL to the call recording audio file",
  "duration_seconds": "integer — call duration in seconds (default: 0)",
  "lead_id": "string (required) — UUID of the lead in your system",
  "conversation_id": "string (required) — UUID of the conversation",
  "metadata": "object (optional) — arbitrary key-value pairs"
}
```

### 9.4 Scoring Response Schema

```json
{
  "s_call": "float [-1, 1] — per-call sentiment score",
  "ewma_baseline": "float | null — EWMA of prior calls (null for first call)",
  "delta_s": "float | null — momentum: s_call - baseline (null for first call)",
  "trajectory": "string — 'Upward' | 'Stable' | 'Degrading' | 'Volatile'",
  "trajectory_flag": "string — 'insufficient_history' or null",
  "i_intent": "float [0,1] — composite buying intent",
  "friction": "float [0,1] — resistance/friction score",
  "s_lead": "float [-1,1] — composite lead score",
  "p_convert": "float | null — conversion probability (null if predictive layer inactive)",
  "predictive_layer_active": "bool",
  "scoring_components": "object — inputs that produced this score",
  "weights_used": "object — {w1, w2, w3, w4} weights applied"
}
```

---

## 10. Configuration Reference

All configuration is in `.env` (loaded by `backend/config.py` via `pydantic-settings`).

### 10.1 Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `ENV` | `development` | `development`, `staging`, or `production` |
| `DEBUG` | `true` | Enable verbose logging |

### 10.2 Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@postgres:5432/lead_scoring` | Async PostgreSQL connection string |

### 10.3 Message Queue

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka broker address |
| `KAFKA_INGESTION_TOPIC` | `call-events` | Topic for incoming call events |
| `KAFKA_AUDIT_TOPIC` | `audit-tasks` | Topic for audit sampling |
| `KAFKA_DLQ_TOPIC` | `call-events-dlq` | Dead-letter queue for failed messages |

### 10.4 Workflow Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_HOST` | `temporal:7233` | Temporal server address |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TASK_QUEUE` | `call-processing` | Temporal task queue name |

### 10.5 AI/ML Models

| Variable | Default | Description |
|----------|---------|-------------|
| `SLM_MODEL_ENDPOINT` | `http://vllm:8000/v1` | OpenAI-compatible LLM endpoint |
| `SLM_MODEL_NAME` | `llama-3.3-8b` | Model name for SLM extraction |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (for future pgvector) |

### 10.6 Scoring Weights

| Variable | Default | Description |
|----------|---------|-------------|
| `W1` | `0.30` | Weight for latest call sentiment (s_call) |
| `W2` | `0.30` | Weight for momentum (delta_s) |
| `W3` | `0.25` | Weight for buying intent (i_intent) |
| `W4` | `0.15` | Weight for friction penalty (subtracted) |

### 10.7 Predictive Layer Gates

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_LABELED_OUTCOMES` | `100` | Min CRM outcomes before ML activates |
| `WEEK5_DATE` | `2026-10-01` | Date after which predictive model can activate |
| `AUDIT_SAMPLE_RATE` | `0.10` | Fraction of calls to audit (0.0–1.0) |

### 10.8 External API Keys

| Variable | Default | Description |
|----------|---------|-------------|
| `TWILIO_AUTH_TOKEN` | (empty) | Twilio auth token for webhook signature verification |
| `DEEPGRAM_API_KEY` | (empty) | Deepgram API key for STT |
| `HUME_API_KEY` | (empty) | Hume AI API key for prosody analysis |

**All three are optional in development.** Without them, the system uses deterministic fallback data.

---

## 11. Troubleshooting

### 11.1 Service Won't Start

```bash
# Check which service failed
docker compose ps

# View logs for a specific service
docker compose logs webhook
docker compose logs worker
docker compose logs consumer

# Common issues:
# - Port conflict: another process is using 5432, 6379, 8000, etc.
#   Fix: Stop conflicting process or change port mapping in docker-compose.yml
# - Docker out of memory: increase Docker Desktop memory limit
```

### 11.2 Kafka Won't Start

```bash
# Kafka may need extra time on first start
docker compose restart kafka
sleep 15
docker compose logs kafka | tail -20
```

### 11.3 Webhook Returns 401

In non-development mode, the webhook verifies Twilio signatures. Either:
- Set `ENV=development` in `.env`
- Provide a valid `TWILIO_AUTH_TOKEN` and send correct `X-Twilio-Signature` headers

### 11.4 Workflow Not Appearing in Temporal UI

```bash
# Check the consumer bridge is running
docker compose logs consumer

# Check Kafka has the message
docker compose exec kafka kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic call-events \
  --from-beginning \
  --max-messages 1
```

### 11.5 Sentiment Scores Look Wrong

1. **Check if fallback is active:** Without `DEEPGRAM_API_KEY` / `HUME_API_KEY` / `SLM_MODEL_ENDPOINT`, the system uses fallback data. Real API keys produce real results.

2. **Check Temporal workflow logs:** Open Temporal UI → find the workflow → click each activity → see input/output.

3. **Run a dry-run test:** Use `POST /tools/score-transcript` with a known transcript to isolate the scoring logic.

### 11.6 Database Connection Issues

```bash
# Verify PostgreSQL is running
docker compose exec postgres pg_isready -U user -d lead_scoring

# Check if migrations ran
docker compose exec postgres psql -U user -d lead_scoring -c "\dt"

# Re-run migrations if needed
alembic upgrade head
```

### 11.7 Reset Everything

```bash
# Full reset: remove all containers, volumes, and rebuild
docker compose down -v
docker compose up --build
alembic upgrade head
```

---

## 12. Integration Patterns & Code Examples

### 12.1 Python: Send a Call for Analysis

```python
import httpx
import uuid

async def analyze_sales_call(
    audio_url: str,
    lead_id: str,
    duration_seconds: int,
    direction: str = "inbound",
    metadata: dict | None = None,
):
    """Send a completed sales call for sentiment analysis."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/webhooks/telephony/call-events",
            json={
                "call_sid": f"call-{uuid.uuid4().hex[:12]}",
                "event_type": "call.completed",
                "direction": direction,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
                "lead_id": lead_id,
                "conversation_id": str(uuid.uuid4()),
                "metadata": metadata or {},
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

# Usage
result = await analyze_sales_call(
    audio_url="https://storage.example.com/calls/recording.mp3",
    lead_id="550e8400-e29b-41d4-a716-446655440000",
    duration_seconds=245,
)
print(f"Workflow started: {result['workflow_id']}")
```

### 12.2 Python: Poll for Lead Score

```python
import asyncio
import httpx

async def get_lead_score(lead_id: str) -> dict:
    """Fetch the current score for a lead."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"http://localhost:8001/leads/{lead_id}/score",
            timeout=10,
        )
        if response.status_code == 404:
            return {"status": "not_found", "lead_id": lead_id}
        response.raise_for_status()
        return response.json()

async def wait_for_score(lead_id: str, timeout: int = 60, interval: int = 3):
    """Poll until a lead has a score (pipeline completed)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await get_lead_score(lead_id)
        if result.get("overall_sentiment_score") is not None:
            return result
        await asyncio.sleep(interval)
    raise TimeoutError(f"Lead {lead_id} not scored within {timeout}s")
```

### 12.3 Python: Dry-Run Score a Transcript

```python
import httpx

async def score_transcript(
    transcript: str,
    prosody: dict | None = None,
) -> dict:
    """Score a transcript without persisting data."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8001/tools/score-transcript",
            json={
                "transcript": transcript,
                "prosody": prosody,
            },
            timeout=60,  # Longer timeout for LLM inference
        )
        response.raise_for_status()
        return response.json()

# Usage
result = await score_transcript(
    transcript="Customer: I'm ready to buy. Let's do this!",
)
print(f"S_lead: {result['s_lead']}, P_convert: {result['p_convert']}")
print(f"Emotion: {result['extraction']['primary_emotion']}")
print(f"Objections: {result['extraction']['objections']}")
```

### 12.4 JavaScript/TypeScript: Send a Call for Analysis

```typescript
async function analyzeSalesCall(params: {
  audioUrl: string;
  leadId: string;
  durationSeconds: number;
  direction?: 'inbound' | 'outbound';
  metadata?: Record<string, unknown>;
}) {
  const response = await fetch('http://localhost:8000/webhooks/telephony/call-events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      call_sid: `call-${crypto.randomUUID().slice(0, 12)}`,
      event_type: 'call.completed',
      direction: params.direction || 'inbound',
      audio_url: params.audioUrl,
      duration_seconds: params.durationSeconds,
      lead_id: params.leadId,
      conversation_id: crypto.randomUUID(),
      metadata: params.metadata || {},
    }),
  });

  if (!response.ok) {
    throw new Error(`Webhook failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}

// Usage
const result = await analyzeSalesCall({
  audioUrl: 'https://storage.example.com/calls/recording.mp3',
  leadId: '550e8400-e29b-41d4-a716-446655440000',
  durationSeconds: 180,
  metadata: { campaign: 'q3_outreach' },
});
console.log(`Workflow: ${result.workflow_id}`);
```

### 12.5 JavaScript/TypeScript: Query Lead Score

```typescript
async function getLeadScore(leadId: string) {
  const response = await fetch(`http://localhost:8001/leads/${leadId}/score`);

  if (response.status === 404) {
    return null; // Lead not found or not yet scored
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  return response.json();
}

// Usage
const score = await getLeadScore('550e8400-e29b-41d4-a716-446655440000');
if (score) {
  console.log(`Category: ${score.current_category}`);
  console.log(`Sentiment: ${score.overall_sentiment_score}`);
  console.log(`Trajectory: ${score.sentiment_trajectory}`);
  console.log(`P(convert): ${score.conversion_probability}`);
}
```

### 12.6 Webhook Integration (Your CRM/Telephony System)

If your telephony system supports webhooks (Twilio, etc.), configure it to POST to:

```
https://your-host.example.com/webhooks/telephony/call-events
```

**Twilio-specific setup:**
1. In Twilio Console, go to your phone number
2. Set "A call ends" webhook to your endpoint URL
3. Ensure `TWILIO_AUTH_TOKEN` is set in `.env` for signature verification

**Generic webhook setup:**
If using a different provider, adapt their webhook payload to the `TelephonyEvent` schema. You may need a thin adapter/transform layer:

```python
# Example: adapter for a custom telephony provider
def adapt_my_provider_to_telephony_event(provider_payload: dict) -> dict:
    return {
        "call_sid": provider_payload["callId"],
        "event_type": "call.completed",
        "direction": "inbound" if provider_payload["type"] == "incoming" else "outbound",
        "audio_url": provider_payload["recordingUrl"],
        "duration_seconds": provider_payload["duration"],
        "lead_id": provider_payload["contactId"],  # Map your contact/lead ID
        "conversation_id": provider_payload["interactionId"],
        "metadata": {
            "source": "my_telephony_provider",
            "campaign": provider_payload.get("campaign"),
        },
    }
```

---

## Appendix A: Architecture Decisions & Constraints

| Decision | Rationale |
|----------|-----------|
| **Kafka between webhook and Temporal** | Decouples ingestion from processing. Survives Temporal outages. Enables backpressure and replay. |
| **Temporal for workflow orchestration** | Durable execution with built-in retries, timeouts, and state management. Survives worker crashes. |
| **SLM (Small Language Model) for extraction** | Lower latency and cost than GPT-4-class models. Extracts structured sentiment from transcripts. |
| **EWMA for sentiment momentum** | Smooths per-call noise. Lambda 0.35 favors recent calls while retaining history. |
| **Deterministic categorization** | Category precedence is fixed and auditable. No LLM hallucination risk for lead routing decisions. |
| **Predictive layer gated on outcome volume** | LightGBM only activates after 100+ labeled CRM outcomes. Prevents overfitting on sparse data. |
| **Guardrail on Hot/Disqualified only** | Only blocks externally-visible, hard-to-reverse actions (AE assignment, suppression). Warm/Nurture skip guardrail. |
| **MCP adapter is read-only** | External agents can read scores and dry-run scoring but CANNOT execute actions. Guardrail boundary is enforced at the architecture level. |

## Appendix B: Scoring Formula Reference

```
S_lead = W1 × S_latest + W2 × delta_S + W3 × I_intent − W4 × F_friction
         clamped to [-1, 1]

Where:
  S_latest    = per-call sentiment from SLM extraction [-1, 1]
  delta_S     = S_latest − EWMA_baseline (null for first call)
  I_intent    = 0.5 × BANT_completeness + 0.5 × buying_intent_score
  F_friction  = friction score from extraction [0, 1]

Trajectory classification order:
  1. Volatile   — variance(last 5 calls) > 0.10
  2. Upward     — mean trend > +0.05
  3. Degrading  — mean trend < −0.05
  4. Stable     — otherwise

Category precedence (first match wins):
  1. Disqualified — S_lead < −0.20 OR explicit non-fit objection
  2. At-Risk      — delta_S < −0.35 OR rising friction
  3. Hot          — S_lead ≥ 0.70 AND P_convert ≥ 0.55 AND positive delta_S AND strong BANT
  4. Warm         — 0.35 ≤ S_lead < 0.70 OR S_lead ≥ 0.70 without full Hot criteria
  5. Nurture      — fallthrough (S_lead < 0.35, no risk signals)
```

---

*Documentation generated from codebase v0.5.0. For questions or updates, refer to the source code or open an issue.*
