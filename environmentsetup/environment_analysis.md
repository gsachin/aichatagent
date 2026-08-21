# Full Environment Analysis — New Machine Onboarding

**Project:** University Admissions AI Chat Agent (`gsachin/aichatagent`)
**Branch:** `whatsapp-chat-fix` @ `8625477` (latest on GitHub)
**Target machine (NEW):** Windows 11 Pro, Build 26200
**Analysis date:** 2026-08-21

> The project was built and verified on a **different machine** (per repo docs: Python 3.11, Windows + an older NVIDIA CUDA GPU, or an Apple M3 Max). This document is the full expert analysis of what this new machine has, what is missing, and what must be updated to run the project here.

---

## 1. Executive summary

| Verdict | Detail |
|---|---|
| 🟢 Machine capability | Excellent — 32 GB RAM, RTX 5060 Ti 16 GB (driver 610.88), WSL2 ready, ~387 GB free disk |
| 🔴 Project readiness | Nearly zero — wrong Python version, no venv, no `.env`, no Ollama models, no RAG store, no cloudflared |
| 🔴 Critical blocker #1 | Only **Python 3.14.7** installed; the repo requires **3.11** (pins don't support 3.14 — `torch==2.6.0` has no cp314 wheels) |
| 🔴 Critical blocker #2 | **`torch==2.6.0` (cu124) cannot use this GPU.** RTX 5060 Ti is Blackwell **sm_120**; PyTorch added sm_120 support in **2.7.0 with CUDA 12.8 wheels** |
| 🟡 Partial progress | A bootstrap run already installed **Ollama** (running, 0 models), **Docker Desktop** (running, CLI not on PATH), **ffmpeg**; **cloudflared install failed** (hung MSI); the run crashed before creating the venv/`.env` |

**Recommended strategy: install Python 3.11, create the venv with the repo's pins, and upgrade only `torch` to ≥2.7 with cu128 wheels (required for this GPU). Everything else stays as pinned.** This keeps the environment as close as possible to the verified one while fixing the one pin that physically cannot work on an RTX 50-series card.

---

## 2. Current machine configuration (verified today)

| Property | Value | Status |
|---|---|---|
| OS | Windows 11 Pro, Build 26200 | ✅ |
| RAM | 32,702 MB (32 GB) | ✅ (16 GB required) |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16,311 MiB | ✅ Hardware fine — ⚠️ see §3.2 |
| GPU driver | 610.88 | ✅ New enough for CUDA 12.8+ |
| Disk | C: 387 / 476 GB free · D: 250 / 250 GB free | ✅ |
| WSL2 | Present, default distro Ubuntu | ✅ |
| winget | v1.29 | ✅ |
| Python | **3.14.7 only** (`py -0p` shows no 3.11) | ❌ Needs 3.11 |
| Docker Desktop | Installed (user-scope), **backend running**; CLI at `C:\Users\ADMIN\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe` but **not on PATH** | 🟡 Fix PATH |
| PostgreSQL container | Not started (`elearning-postgres` absent) | ❌ |
| Ollama | v0.32.15 installed, daemon running on 11434, **models: none** | 🟡 Pull models |
| ffmpeg | Installed via winget (Gyan 9.0, PATH updated at install time) | ✅ (new shell needed) |
| cloudflared | **Not installed** — winget MSI hung and was killed | ❌ Reinstall |
| `.venv` / `.env` / `chroma_local_db` | None exist | ❌ |
| Twilio / SMTP credentials | Not present anywhere on this machine | ❌ User must supply |

---

## 3. What the project requires — full review of `requirements.txt`

### 3.1 Python packages (pinned 2026-08-15, verified env = Python 3.11)

**Web / API**

| Package | Pin | Purpose | On 3.11 | On 3.14 | sm_120 (GPU) |
|---|---|---|---|---|---|
| fastapi | 0.140.0 | Backend framework | ✅ | ✅ | n/a |
| uvicorn[standard] | 0.51.0 | ASGI server | ✅ | ✅ | n/a |
| streamlit | 1.59.2 | Chat UI + dashboard | ✅ | ✅ | n/a |
| websockets | 15.0.1 | Realtime transport | ✅ | ✅ | n/a |
| httpx / requests | 0.28.1 / 2.34.2 | HTTP clients | ✅ | ✅ | n/a |
| python-multipart | 0.0.32 | Form parsing (WhatsApp media) | ✅ | ✅ | n/a |
| python-dotenv | 1.2.2 | `.env` loading | ✅ | ✅ | n/a |

**ML / audio**

| Package | Pin | Purpose | On 3.11 | On 3.14 | sm_120 (GPU) |
|---|---|---|---|---|---|
| **torch** | **2.6.0** (cu124 wheel on Windows) | Core inference | ✅ | ❌ **no cp314 wheels** | ❌ **no sm_120 kernels → GPU unusable** |
| onnxruntime-gpu | 1.28.0 (Win/Linux) | Kokoro TTS | ✅ | ✅ (verify) | ✅ likely (2026 build) — verify |
| numpy / scipy / soundfile | 2.4.6 / 1.17.1 / 0.14.0 | Numerics/audio IO | ✅ | ✅ (verify) | n/a |
| faster-whisper | 1.2.1 | STT (CTranslate2 backend) | ✅ | ❓ ctranslate2 wheels | ✅ via cuBLAS |
| openai-whisper | 20250625 | Alt. STT | ✅ | ✅ | n/a |
| kokoro-onnx | 0.5.0 | TTS | ✅ | ✅ (verify) | via onnxruntime-gpu |
| pipecat-ai[whisper,kokoro] | 1.6.0 | Voice pipeline framework | ✅ | ❓ | n/a |

**RAG / LLM**

| Package | Pin | Purpose | On 3.11 | Notes |
|---|---|---|---|---|
| ollama | 0.6.2 | Ollama Python client | ✅ | ✅ |
| chromadb | 1.5.9 | Vector store | ✅ | ✅ (verify) |
| pandas / pyarrow | 3.0.3 / 24.0.0 | Data for leads/dashboard | ✅ | ✅ (verify) |
| langchain + community/ollama/text-splitters/classic | 1.3.14 / 0.4.2 / 1.1.0 / 1.1.2 / 1.0.8 | RAG orchestration | ✅ | ❓ |
| pypdf | 6.14.2 | PDF parsing (offer letters) | ✅ | ✅ |

**DB / integrations**

| Package | Pin | Purpose | On 3.11 | Notes |
|---|---|---|---|---|
| psycopg2-binary | 2.9.12 | PostgreSQL driver | ✅ | ✅ (verify) |
| twilio | 9.10.9 | Voice + WhatsApp | ✅ | ✅ |
| fpdf2 | 2.8.7 | Offer-letter PDFs | ✅ | ✅ |
| mcp | 2.0.0 | Optional MCP server | ✅ | ✅ |

**macOS-only markers (ignored on this machine):** mlx-lm, langchain-openai, openai, einops, mlx-whisper, torchvision — all gated `platform_system=="Darwin"`. The MLX backend replaces Ollama on Mac; **not relevant on Windows**.

### 3.2 System software

| Tool | Purpose | Status here |
|---|---|---|
| Docker Desktop + `elearning-postgres` (pgvector/pg16) | DB persistence; app runs DB-less without it | 🟡 Installed & running; container not started; CLI not on PATH |
| Ollama + models `qwen2.5:7b-instruct-q3_K_M` (LLM) and `nomic-embed-text` (embeddings) | Local LLM + RAG embeddings | 🟡 Installed & running; **0 models pulled** |
| cloudflared | Public HTTPS tunnel for Twilio webhooks | ❌ Install failed — retry |
| ffmpeg | Audio conversion | ✅ Installed |

> ⚠️ `docker-compose.yml` also defines an **Ollama container** (port 11434). The Windows launchers use **native Ollama**. Do not start both — they would fight over port 11434. Use `docker compose up -d postgres` only.

### 3.3 Configuration & data

- **`.env`** — copied from `.env.example` by bootstrap. **Twilio fields (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `TWILIO_WHATSAPP_NUMBER`) are the only fully manual step** — without them WhatsApp/voice can't work end-to-end. SMTP (offer-letter email) optional.
- **Machine profile block** — `scripts/predeploy.py` sizes `OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, `WHISPER_MODEL`, `WHISPER_NUM_THREADS`, `FASTAPI_WORKERS`, `RAG_TOP_K/FETCH_K` from this machine's RAM/VRAM.
- **RAG vector store** — built from `content/meridian/meridian_knowledge_base.md` via `scripts/rebuild_rag_index.py` (requires `nomic-embed-text` in Ollama).
- **HuggingFace downloads at first run** — Whisper `small.en` (~244 MB) and Kokoro voices.

---

## 4. Gap analysis

### ✅ DONE

| Item | State |
|---|---|
| GPU + new driver (610.88) | Working (`nvidia-smi` OK) |
| WSL2 + Ubuntu | Working |
| winget | Working |
| Docker Desktop | Installed, backend running |
| Ollama | Installed, daemon running (empty) |
| ffmpeg | Installed via winget |
| Disk space | Plenty (~387 GB free on C) |

### ❌ MISSING

| # | Item | Impact | Fix |
|---|---|---|---|
| 1 | Python 3.11 | Whole pinned stack assumes it; 3.14 breaks it | `winget install Python.Python.3.11` then new shell |
| 2 | `.venv` + dependencies | App cannot start | Create with 3.11 + pinned reqs (torch swapped to cu128) |
| 3 | `.env` | Server won't boot cleanly | Copy template; **user fills Twilio creds** |
| 4 | Ollama models (`qwen2.5:7b-instruct-q3_K_M`, `nomic-embed-text`) | No LLM / embeddings | `ollama pull` ×2 |
| 5 | cloudflared | No public URL → Twilio unreachable | Retry `winget install Cloudflare.cloudflared` |
| 6 | PostgreSQL container | No persistence (DB-less fallback exists) | `docker compose up -d postgres` after CLI on PATH |
| 7 | RAG vector store | RAG returns nothing | Rebuild from `content/meridian` |
| 8 | Docker CLI on PATH | All launcher scripts call `docker` | Add `…\DockerDesktop\resources\bin` to PATH |
| 9 | Machine profile block in `.env` | Sub-optimal sizing | `predeploy.py` writes it |

### ⚠️ NEEDS UPDATE (version/configuration changes required)

| # | Item | Why | Change |
|---|---|---|---|
| 1 | **torch 2.6.0 → ≥2.7.x + cu128** | RTX 50-series (sm_120) unsupported in 2.6.0/cu124 — GPU would silently fall back to CPU | **Mandatory.** `pip install torch --index-url https://download.pytorch.org/whl/cu128` in the venv; keep everything else pinned |
| 2 | Python 3.11 (vs current 3.14) | Pins have no 3.14 wheels (torch 2.6.0, likely faster-whisper/ctranslate2) | Install 3.11 alongside 3.14; build venv with `py -3.11` |
| 3 | onnxruntime-gpu 1.28.0 | Verify it exposes CUDA on sm_120 | `onnxruntime.get_available_providers()` — expect `CUDAExecutionProvider` |
| 4 | Ollama model quant | Default `q3_K_M` is sized for **6 GB** cards; this GPU has 16 GB | Optional: `qwen2.5:7b-instruct-q4_K_M` or `14b-instruct-q4_K_M`, bigger `OLLAMA_NUM_CTX` |
| 5 | PATH refresh | winget-installed tools (ffmpeg, ollama, docker) invisible to old shells | Open new terminals after setup |
| 6 | `bootstrap_services.py` bug on this machine | Crashed at `ensure_docker_daemon()` (`FileNotFoundError`) because `docker` isn't on PATH — user-scope Docker installs are not handled | Add Docker CLI to PATH before re-running, or run the phases manually (§5) |

---

## 5. Recommended action plan (ordered)

### Phase 0 — Cleanup & shell hygiene
```powershell
# Open a NEW terminal after every install step (PATH refresh)
# The orphaned msiexec (PID 18796) is the idle Windows Installer service — benign.
# Reboot only if the cloudflared reinstall below fails.
```

### Phase 1 — Python 3.11
```powershell
winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
# NEW shell, then verify:
py -3.11 --version
```

### Phase 2 — venv + dependencies (with the one required upgrade)
```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu128   # >= 2.7 for sm_120
.venv\Scripts\pip install -r requirements.txt                                         # torch already satisfied
# GPU verification (MUST print True):
.venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### Phase 3 — Docker CLI on PATH + PostgreSQL
```powershell
# Add to user PATH: C:\Users\ADMIN\AppData\Local\Programs\DockerDesktop\resources\bin
# NEW shell, then:
docker info
docker compose up -d postgres
docker exec elearning-postgres pg_isready -U elearning -d admissions
```

### Phase 4 — Ollama models
```powershell
ollama pull qwen2.5:7b-instruct-q3_K_M    # or q4_K_M / 14b — see §6.4
ollama pull nomic-embed-text
ollama list
```

### Phase 5 — `.env` + machine profile (USER ACTION for Twilio)
```powershell
Copy-Item .env.example .env
# ✏️ EDIT .env — fill in from https://console.twilio.com:
#   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, TWILIO_WHATSAPP_NUMBER
.venv\Scripts\python scripts\predeploy.py     # writes the Machine Profile block
```

### Phase 6 — RAG store
```powershell
.venv\Scripts\python scripts\rebuild_rag_index.py
```

### Phase 7 — cloudflared (retry after the earlier failed install)
```powershell
winget install --id Cloudflare.cloudflared -e --silent --accept-package-agreements --accept-source-agreements
```

### Phase 8 — Launch
```powershell
powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -WithStreamlit
```

### Verification checklist
```powershell
py -3.11 --version                                                              # 3.11 present
.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"        # True
.venv\Scripts\python -c "import onnxruntime as o; print(o.get_available_providers())"  # CUDAExecutionProvider
.venv\Scripts\python test_environment.py                                        # project validator
curl http://127.0.0.1:8000/                                                     # FastAPI 200
ollama list                                                                     # both models
docker exec elearning-postgres pg_isready -U elearning -d admissions            # accepting connections
# Optional: .venv\Scripts\python -m pytest tests/test_whatsapp_intent.py -v
```

---

## 6. Risks & notes

1. **GPU support is the #1 risk.** If the cu128 torch swap is skipped, the app runs but every GPU check fails and Whisper/Kokoro fall back to CPU — unusably slow. Verify with the `torch.cuda.is_available()` command above. If a cu128 build is unavailable for the torch version pip resolves, use the latest torch that `download.pytorch.org/whl/cu128` offers (2.7+).
2. **Do not run the compose Ollama container alongside native Ollama** (port 11434 conflict). Native Ollama is what the launchers expect.
3. **The tunnel exposes the app publicly.** Default mode is ephemeral (URL changes each restart); `start_services.ps1 -NamedTunnel` needs a one-time `cloudflared tunnel login`.
4. **Twilio credentials are the only user-manual item.** Everything else in §5 is automatable. Until they're filled in, Twilio webhook update (Step 9 of the launcher) will warn and WhatsApp/voice won't work end-to-end.
5. **`bootstrap_services.py` crashed** on this machine at the Docker-daemon check because the user-scope Docker CLI isn't on PATH — a real gap in the script for non-default Docker installs. Prefer the manual phases above, or fix PATH first and re-run it.
6. **Model sizing opportunity:** 16 GB VRAM comfortably fits `qwen2.5:7b-instruct-q4_K_M` (better quality than the pinned q3) or `qwen2.5:14b-instruct-q4_K_M`; raise `OLLAMA_NUM_CTX` to 16384 if you pick the 14B. Move overrides **above** the Machine Profile block in `.env` so `predeploy.py` doesn't rewrite them.
7. **Disk is ample** for the ~10–15 GB this stack downloads (torch cu128 ~3 GB, models ~5 GB, HF whisper/kokoro ~1–2 GB, chroma <1 GB).

---

*Analysis generated by Claude Code on 2026-08-21. Re-run §5's verification checklist after setup and update this file with the final state.*
