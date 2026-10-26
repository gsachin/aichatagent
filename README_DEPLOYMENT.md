# Deployment guide — DSPMU admissions assistant

This guide describes the **current** deployment. Historical Ollama, Chroma,
enterprise-rag-core, Redis and standalone TTS service launchers have been
removed.

## Runtime components

| Component | Port | Started by |
|---|---|---|
| FastAPI API | 8000 | `start_services.sh` |
| Streamlit chat | 8501 | `start_services.sh` |
| Streamlit dashboard | 8502 | `start_services.sh` |
| PostgreSQL | 5432 | Docker (`elearning-postgres`) |
| Cloudflare tunnel(s) | dynamic | `start_services.sh` |
| NGA refresh/document/reference workers | none | separate CLIs, disabled by default |

Not part of this stack: Ollama, MLX, Chroma/pgvector retrieval, Redis,
enterprise-rag-core MCP, `tts_service/`.

## Install and configure

```bash
python3.11 bootstrap_services.py
```

Configure `.env` with the DSPMU brand and the OpenAI-compatible model:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=...
LLM_MODEL=...
COMPANY_NAME=Dr. Shyama Prasad Mukherjee University
COMPANY_SHORT_NAME=DSPMU
ADMISSIONS_EMAIL=registrar@dspmuranchi.ac.in
ADMISSIONS_PHONE=+91 930 455 261
PROGRAM_PROFILE=dspmu
DATABASE_URL=postgresql://elearning:elearning_secret@localhost:5432/admissions
```

Twilio values are optional for local work. Keep them empty to avoid webhook
updates.

## Launch

```bash
# Local validation: no tunnel, no Twilio changes
bash start_services.sh --local-only

# Local services plus public tunnels and Twilio update
bash start_services.sh --with-streamlit

# One-time named tunnel
bash start_services.sh --named-tunnel --tunnel-name dspmu-admissions
```

The launcher:

1. kills stale launchers on ports 8000/8501/8502 and stale `cloudflared`;
2. checks the machine profile and the compute platform;
3. starts/reuses `elearning-postgres`;
4. builds missing pre-rendered call assets;
5. starts FastAPI and waits for HTTP 200;
6. starts the Cloudflare tunnel and writes `.whatsapp_tunnel`;
7. updates Twilio voice/WhatsApp webhooks when configured;
8. starts Streamlit chat and dashboard tunnels.

## Docker Compose

`docker-compose.yml` provides PostgreSQL, FastAPI, Streamlit chat and the
dashboard. It no longer uses a vector database image.

```bash
docker compose up -d
docker compose logs -f fastapi
```

## Stop and rollback

```bash
bash stop_services.sh
docker stop elearning-postgres
```

Unused legacy scripts (`setup.sh`, `start.sh`, `stop.sh`, `launch.sh`,
`start_demo.bat`, `launch.bat`, `launch_tunnel.bat`, `tts_service/`) are gone.
Use `bootstrap_services.*` for setup and `start_services.*` /
`stop_services.sh` for lifecycle.

## NGA and reference delivery

These are separately operated and off by default:

```bash
.venv/bin/python scripts/nga_scheduler.py --status
.venv/bin/python scripts/nga_documents.py --status
.venv/bin/python scripts/nga_references.py --status
```

Operational procedures: `doc/ops/nga-runbook.md`.
Storage mapping and performance: `doc/ops/conversation-storage.md`.

## Verification

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8501/
curl http://127.0.0.1:8502/
```

Run the test suite:

```bash
.venv/bin/python -m pytest -q
```
