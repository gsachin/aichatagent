# SETUP — University Admissions AI Chat Agent on THIS Machine

**Machine:** Windows 11 Pro (Build 26200) · 32 GB RAM · NVIDIA RTX 5060 Ti 16 GB (Blackwell, sm_120)  
**Project root:** `D:\project\universityDemo`  
**Document date:** 2026-08-21 · **Author:** AI Architect review of the full codebase + system

This ONE document contains the complete root-cause analysis of why the project did not
run on this machine, and the exact step-by-step commands to install everything and start
it. Commands are PowerShell — run them in a terminal opened at `D:\project\universityDemo`.

---

## 1. What the project is (architecture)

| Service | Runs as | Port | Purpose |
|---|---|---|---|
| FastAPI backend (`app.main`) | local venv, launched by `start_services.ps1` | 8000 | Voice/chat/WhatsApp API, RAG, DB |
| Streamlit main app (`app.py`) | local venv | 8501 | Admissions chat UI |
| Streamlit dashboard (`dashboard.py`) | local venv | 8502 | Admin cockpit |
| Ollama (native Windows app) | system service/app | 11434 | Local LLM `qwen2.5:14b` (sized by `predeploy.py` for this 16 GB GPU) + embeddings `nomic-embed-text` |
| PostgreSQL 16 + pgvector (`elearning-postgres`) | Docker container | 5432 | Leads/calls/offers persistence (app runs DB-less without it) |
| cloudflared | local exe | — | Public HTTPS tunnel so Twilio can reach the webhooks |
| Chroma vector store | files (`chroma_local_db/`) | — | RAG index built from `content/meridian/` |
| Twilio | external SaaS | — | Voice + WhatsApp (credentials are the only manual step) |
| NVIDIA GPU | hardware | — | sm_120 (compute 12.0) — needs **torch ≥ 2.7 + CUDA 12.8** |

> ⚠️ Do NOT run the `ollama` container from `docker-compose.yml` — it would fight the
> native Ollama app over port 11434. Only the `postgres` compose service is used on Windows.

---

## 2. Root cause analysis (why the original setup failed here)

| # | Symptom | Root cause |
|---|---|---|
| R1 | `bootstrap_services.py` crashed with `FileNotFoundError: [WinError 2]` (`bootstrap_err.log`) | Docker Desktop was installed **user-scope** (no admin). Its CLI lives in `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin` and is **not on PATH** for the running session. `ensure_docker_daemon()` called bare `docker` without a try/except → unhandled exception **before the venv/.env/deps phases ever ran**. |
| R2 | `start_services.ps1` Step 4: `docker : The term 'docker' is not recognized` + `Start-Process` failed | Same user-scope install, plus the script hardcoded the **system** path `C:\Program Files\Docker\Docker\Docker Desktop.exe`, which does not exist for user-scope installs. |
| R3 | "PyTorch CUDA check failed" | `.venv` exists but is **empty** (only pip/setuptools). No torch installed at all. |
| R4 | Only Python **3.14.7** was installed initially | All pins were verified on Python **3.11**; `torch` had no cp314 wheels. (Since fixed: Python 3.11.9 is now installed and the venv uses it.) |
| R5 | **Latent GPU blocker** — even after installing everything, the GPU would have been dead | `requirements.txt` pinned `torch==2.6.0` (+cu124). This GPU is Blackwell **sm_120**; torch < 2.7 has no sm_120 kernels → `torch.cuda.is_available()` = False → Whisper/Kokoro fall back to CPU (15–35 s per utterance). |
| R6 | `ensure_venv()` would silently skip installing dependencies | It only checked "venv exists" — the half-created venv (R3) would never get its packages. |
| R7 | cloudflared MSI hung during winget install (killed) | No tunnel → `start_services.ps1` Step 7 hard-exits → Twilio can never reach the app. |
| R8 | Ollama daemon running but **0 models**; no `.env`; no `chroma_local_db/` | Bootstrap never reached those phases (R1). |
| R9 | winget-installed tools invisible to old shells | winget updates only the *user* PATH; terminals opened before the install (and the bootstrap's own process) never see `docker`/`ollama`/`ffmpeg`. |

**Fixes applied (see §3):** R1/R2 — Docker CLI discovery for user-scope installs + PATH repair in all three launcher scripts; R4 — check now requires 3.11 (or `py -3.11`); R5 — torch swapped to `2.7.1+cu128`; R6 — venv completeness probe; R7 — cloudflared standalone-exe fallback; R9 — helpers probe known install directories instead of trusting PATH.

---

## 3. What was updated in the repo (for this machine)

| File | Change |
|---|---|
| `requirements.txt` | `torch==2.6.0` → `torch>=2.7.0,<2.8` (+ comment: cu128 needed for sm_120) |
| `bootstrap_services.py` (ensure_venv) | After `pip install -r`, on NVIDIA machines force-reinstalls `onnxruntime-gpu==1.28.0` last — pipecat-ai's `onnxruntime~=1.24.3` pin drags in the CPU wheel, which shadows the GPU build's files and drops `CUDAExecutionProvider` (Kokoro would run on CPU) |
| `bootstrap_services.py` | `_find_docker()` / `_find_ollama()` / `ensure_docker_on_path()` (session + **persisted** user PATH); `ensure_docker_daemon()` no longer crashes on missing CLI and probes user-scope `Docker Desktop.exe`; `check_python()` returns False on non-3.11 and honors `py -3.11`; `ensure_venv()` recreates wrong-version venvs, **installs deps into an existing-but-empty venv**, and installs `torch==2.7.1+cu128` on NVIDIA machines; `ensure_postgres()`/`ensure_models()` use the discovered CLI paths; cloudflared standalone-download fallback when the winget MSI fails |
| `start_services.ps1` | `Find-DockerCli` / `Find-DockerDesktopExe` / `Find-CloudflaredExe` helpers; Step 4 rewritten to use them (no more hardcoded system path); Step 7 uses the resolved cloudflared exe; torch-fix hint in Step 3 |
| `check_and_tunnel.ps1` | Same discovery helpers; venv Python used for the torch check; bare `docker`/`cloudflared` calls replaced |

---

## 4. Current state of this machine (verified 2026-08-21)

| Item | State |
|---|---|
| Python 3.11.9 | ✅ installed (`py -3.11` works) |
| Python 3.14.7 | ⚠️ also installed (do NOT use it for this project) |
| `.venv` | 🟡 exists, Python 3.11.9, **empty** (no packages) |
| Docker Desktop | ✅ installed user-scope, daemon running; CLI at `C:\Users\ADMIN\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`; dir already in user PATH |
| PostgreSQL container | ❌ not started |
| Ollama | ✅ app running on :11434 — **0 models pulled** |
| ffmpeg | ✅ installed (in user PATH; needs a fresh shell) |
| cloudflared | ❌ not installed |
| `.env` | ❌ missing (will be created from `.env.example`) |
| `chroma_local_db/` | ❌ missing (RAG must be rebuilt) |
| GPU | ✅ RTX 5060 Ti, compute 12.0 (sm_120) |
| Twilio credentials | ❌ **YOU must provide these** (only manual step) |

---

## 5. PATH A — RECOMMENDED: one-command bootstrap (the scripts now self-heal)

The patched `bootstrap_services.py` is idempotent: run it once, it installs whatever is
missing, then starts everything.

```powershell
# 0. Open a NEW PowerShell terminal at the project root
cd D:\project\universityDemo

# 1. Run the bootstrap (installs missing software, venv+deps incl. torch cu128,
#    .env, machine profile, Ollama models, PostgreSQL, RAG store, then launches)
powershell -ExecutionPolicy Bypass -File .\bootstrap_services.ps1

#    (~15-30 min first run: torch cu128 ~3 GB, Ollama models ~5 GB, pip deps ~3-4 GB,
#     HF Whisper/Kokoro models ~1-2 GB on first launch)
#    Add -WithStreamlit to also start the chat UI + dashboard.
```

```powershell
# 2. FILL IN YOUR TWILIO CREDENTIALS (only manual step):
notepad .env
#    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#    TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#    TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
#    TWILIO_WHATSAPP_NUMBER=+1xxxxxxxxxx
#    (from https://console.twilio.com; SMTP optional)

# 3. Re-run the launcher so Twilio webhooks point at the fresh tunnel URL:
powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -WithStreamlit
```

Done. URLs printed at the end: FastAPI `http://localhost:8000`, chat `http://localhost:8501`,
dashboard `http://localhost:8502`, plus the public `https://*.trycloudflare.com` tunnel.

> **Self-healing:** `start_services.ps1` now checks at startup that the venv can
> `import fastapi, torch`; if not (empty venv, fresh clone, interrupted bootstrap)
> it runs `bootstrap_services.py --install-only` automatically — installing all
> missing software, dependencies, models, and config — before starting the services.
> So even running `.\start_services.ps1` first on a bare machine now works.

> The bootstrap keeps a terminal window open holding the services (like the original
> machine). Closing it stops FastAPI/cloudflared. Ollama + Docker/PostgreSQL survive.

### 5.1 Quick verification after bootstrap

```powershell
.venv\Scripts\python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
#    MUST print: 2.7.1+cu128 True   (True is the critical part)

.venv\Scripts\python -c "import onnxruntime as o; print(o.get_available_providers())"
#    should include 'CUDAExecutionProvider'

.venv\Scripts\python test_environment.py        # project's own validator

ollama list                                    # both models listed
docker exec elearning-postgres pg_isready -U elearning -d admissions   # 'accepting connections'
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/          # 200
```

---

## 6. PATH B — Fully manual, step by step (every command)

Use this if you prefer to run each step yourself (also useful as a checklist).

### Step 0 — fresh shell

Open a **new** PowerShell window (so the user PATH picks up winget installs from the
earlier bootstrap attempt). Verify:

```powershell
py -0p                     # must list -V:3.11[-64]
ollama --version           # v0.32+
ffmpeg -version            # any
```

### Step 1 — Python 3.11 (already done, verify only)

```powershell
py -3.11 --version         # Python 3.11.9
# If missing:
winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
# then open a NEW shell and re-verify
```

### Step 2 — create the venv with Python 3.11

```powershell
# The current .venv is empty — reuse it (it is already 3.11.9):
py -3.11 -m venv .venv
.venv\Scripts\python.exe --version        # Python 3.11.9
.venv\Scripts\pip install --upgrade pip setuptools wheel
```

### Step 3 — install dependencies (torch cu128 FIRST — required for this GPU)

```powershell
# RTX 5060 Ti = Blackwell sm_120. torch 2.6/cu124 cannot use it. cu128 build:
.venv\Scripts\pip install torch==2.7.1+cu128 --extra-index-url https://download.pytorch.org/whl/cu128

# Everything else, exactly as pinned (torch already satisfied):
.venv\Scripts\pip install -r requirements.txt
```

### Step 4 — verify the GPU is actually usable (MUST print True)

```powershell
.venv\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
#    True NVIDIA GeForce RTX 5060 Ti
```

### Step 5 — Docker CLI + PostgreSQL

```powershell
# Docker CLI dir is already in the user PATH — verify in THIS (new) shell:
docker --version
docker info                   # 'Server Version' must appear (Docker Desktop running)

# Start the DB container (only postgres — NOT the compose ollama container):
docker compose -f docker-compose.yml up -d postgres
docker exec elearning-postgres pg_isready -U elearning -d admissions
#    must print: accepting connections
```

### Step 6 — Ollama models

```powershell
# Pull the model your .env was sized for (check the OLLAMA_MODEL line first):
Select-String "^OLLAMA_MODEL|^EMBED_MODEL" .env
#    On THIS machine (16 GB VRAM, nvidia_high tier): OLLAMA_MODEL=qwen2.5:14b

ollama pull qwen2.5:14b                 # LLM (~9 GB) — name from OLLAMA_MODEL in .env
ollama pull nomic-embed-text            # embeddings (~274 MB)
ollama list                             # both must appear
```

> The exact model name always comes from `.env` (`OLLAMA_MODEL`), which
> `scripts/predeploy.py` sizes per machine. Smaller machines get
> `qwen2.5:7b-instruct-q3_K_M`; this 16 GB GPU gets the 14B model.

### Step 7 — cloudflared (public tunnel for Twilio)

```powershell
winget install --id Cloudflare.cloudflared -e --silent --accept-package-agreements --accept-source-agreements
# If the MSI hangs again (it did on the first attempt), use the direct download:
#   Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
#                     -OutFile "$env:LOCALAPPDATA\Programs\cloudflared\cloudflared.exe"
#   (create the folder first, then add that folder to your user PATH)
# Open a NEW shell, then:
cloudflared --version
```

### Step 8 — .env + machine profile + Twilio credentials

```powershell
Copy-Item .env.example .env
.venv\Scripts\python scripts\predeploy.py      # sizes the Machine Profile block for THIS machine
notepad .env
#    Fill in: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, TWILIO_WHATSAPP_NUMBER
#    (https://console.twilio.com). SMTP optional. Keep the rest as generated.
```

### Step 9 — build the RAG vector store

```powershell
.venv\Scripts\python scripts\rebuild_rag_index.py
#    requires Ollama + nomic-embed-text (Step 6) — creates chroma_local_db/
```

### Step 10 — validate everything

```powershell
.venv\Scripts\python test_environment.py
```

### Step 11 — LAUNCH

```powershell
# Minimal (FastAPI + tunnel only):
powershell -ExecutionPolicy Bypass -File .\start_services.ps1

# Full stack (chat UI + dashboard):
powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -WithStreamlit

# Status card (safe to run any time, fixes missing per-port tunnels):
powershell -ExecutionPolicy Bypass -File .\check_and_tunnel.ps1

# Stop everything: press Ctrl+C in the launcher terminal (kills FastAPI + tunnel).
# Ollama and Docker/PostgreSQL keep running in the background — stop them with:
#   ollama app:   exit from the tray icon
#   PostgreSQL:   docker compose -f docker-compose.yml down
```

- Twilio webhooks are updated automatically from the fresh tunnel URL (Step 9 inside the launcher).
- The tunnel is **ephemeral** — the public URL changes each restart. For a permanent URL:
  `.\start_services.ps1 -NamedTunnel` (first run needs a one-time `cloudflared tunnel login`).

### Step 12 — optional: demo data

```powershell
.venv\Scripts\python scripts\seed_demo_data.py    # only when the DB is empty (safe guard built in)
```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `torch.cuda.is_available()` is False | You have the CPU/PyPI build. Re-run Step 3's cu128 install, then re-check. |
| `docker : The term 'docker' is not recognized` | Old shell. Open a new terminal (user PATH now contains the DockerDesktop bin dir), or the scripts find it via `Find-DockerCli`. |
| `cloudflared not found` in the launcher | Install per Step 7; the launcher now also finds `%LOCALAPPDATA%\Programs\cloudflared\cloudflared.exe`. |
| Ollama unreachable / no models | Ollama app closed? Start `Ollama` from the Start menu, then `ollama pull ...`. |
| FastAPI starts but RAG answers are empty | Re-run `scripts\rebuild_rag_index.py` (needs `nomic-embed-text` pulled). |
| Twilio calls/WhatsApp not working | Check `.env` credentials; check `update_twilio_webhook.py` output in the launcher log; verify the tunnel URL is reachable (`curl.exe https://<host>/`). |
| Port 11434 conflict | You started the compose `ollama` container — stop it (`docker compose down`) and use native Ollama only. |
| `onnxruntime-gpu` lacks `CUDAExecutionProvider` | The CPU `onnxruntime` wheel (pulled by pipecat-ai's `~=1.24.3` pin) shadowed the GPU build. Fix: `.venv\Scripts\pip install --force-reinstall --no-deps onnxruntime-gpu==1.28.0` (bootstrap now does this automatically). |
| `TypeError: run() got an unexpected keyword argument 'shell'` from bootstrap | Fixed in `bootstrap_services.py` (run() now accepts `shell=`). Update the repo if you see it. |
| winget installs say "already installed" but command not found | PATH refresh — open a new shell. |

---

## 8. Notes

- **GPU is the make-or-break item** — never let pip silently install the PyPI torch
  (CPU) build. The verification command in Step 4 must print `True`.
- The pinned stack was verified on Python 3.11; 3.14 on this machine must not be used
  for this project (`py -3.11` everywhere).
- Keep `MACHINE_PROFILE_CHECK=0` only inside containers; on the host, `scripts/predeploy.py`
  rewrites the machine-profile block on each deploy — put manual overrides ABOVE the
  `=== MACHINE PROFILE ===` block in `.env`.
- First launch downloads HuggingFace Whisper (`small.en`, ~244 MB) and Kokoro voices into
  `~/.cache/huggingface` (one-time).
