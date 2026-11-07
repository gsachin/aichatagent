# Quick start — DSPMU admissions assistant

Current stack: **OpenAI-compatible LLM**, FastAPI, Streamlit chat + dashboard,
PostgreSQL, Twilio voice/WhatsApp, Cloudflare tunnel, and the DSPMU NGA site
graph. There is **no Ollama, Chroma/vector store, Redis, enterprise-rag-core or
standalone TTS service** in the current architecture.

## 1. Prerequisites

- Python 3.11
- Docker Desktop (PostgreSQL)
- An OpenAI API key (or an OpenAI-compatible endpoint)
- Optional: Twilio account and `cloudflared` for public voice/WhatsApp

## 2. Install

```bash
python3.11 bootstrap_services.py
```

## 3. Configure `.env`

At minimum:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=your-key
LLM_MODEL=gpt-4.1-mini

COMPANY_NAME=Dr. Shyama Prasad Mukherjee University
COMPANY_SHORT_NAME=DSPMU
ADMISSIONS_EMAIL=registrar@dspmuranchi.ac.in
ADMISSIONS_PHONE=+91 930 455 261
PROGRAM_PROFILE=dspmu
```

PostgreSQL is started by `start_services.sh`; the default database URL points at
the local `elearning-postgres` container.

Keep the NGA and reference-delivery features off until their sign-off gates are
complete:

```dotenv
NGA_ENABLED=false
NGA_SCHEDULER_ENABLED=false
NGA_PDF_INDEX_ENABLED=false
NGA_REFERENCE_DELIVERY_ENABLED=false
```

## 4. Run

```bash
# Local only: FastAPI + Streamlit, no tunnel and no Twilio changes
bash start_services.sh --local-only

# Full launcher: FastAPI + Streamlit + Cloudflare tunnel + Twilio webhook
bash start_services.sh --with-streamlit

# Backend + tunnel only
bash start_services.sh --skip-streamlit
```

URLs:

- FastAPI: http://localhost:8000
- Streamlit chat: http://localhost:8501
- Dashboard: http://localhost:8502
- Health: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

## 5. Stop

```bash
bash stop_services.sh
```

PostgreSQL is left running; stop it explicitly with:

```bash
docker stop elearning-postgres
```

## 6. NGA operations

The site graph, refresh worker, PDF index and reference delivery are separate
from the web process and remain off by default:

```bash
.venv/bin/python scripts/nga_scheduler.py --status
.venv/bin/python scripts/nga_documents.py --status
.venv/bin/python scripts/nga_references.py --status
```

See `doc/sdlc/nga/`, `doc/ops/nga-runbook.md` and
`doc/ops/conversation-storage.md`.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| FastAPI does not start | `logs/fastapi.log`; `.env`; `LLM_API_KEY` |
| PostgreSQL unavailable | `docker ps`; `DATABASE_URL` |
| Dashboard shows no data | PostgreSQL is running and the lead tables exist |
| Tunnel URL missing | `logs/cloudflared_8000.log`; `cloudflared` installed |
| Twilio stale | re-run `start_services.sh` or `scripts/update_twilio_webhook.py` |
| Voice has no audio | `logs/fastapi.log`; STT/TTS provider keys |
