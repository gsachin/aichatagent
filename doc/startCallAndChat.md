# Start, Call & Chat — Complete Setup Guide

> **Audience**: Anyone who wants to run the University Admissions Voice Assistant, make outbound AI phone calls, and share the tools with others over the internet.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Startup Sequence](#2-startup-sequence)
3. [How to Use Each Component](#3-how-to-use-each-component)
4. [Sharing Links with Others](#4-sharing-links-with-others)
5. [Troubleshooting](#5-troubleshooting)
6. [Stopping Everything](#6-stopping-everything)

---

## 1. Prerequisites

These must be installed **once** before the first run:

| Tool | Purpose | How to Check |
|------|---------|--------------|
| **Docker Desktop** | Runs PostgreSQL database | `docker --version` |
| **Ollama** | Local LLM (Qwen 2.5) | `ollama --version` |
| **Python 3.11+** | Runs FastAPI + Streamlit | `python --version` |
| **cloudflared** | Creates public tunnel URLs | `cloudflared --version` |
| **Python packages** | All dependencies | `pip install -r requirements.txt` |

### 1.1 Docker Desktop
- Download from [docker.com](https://www.docker.com/products/docker-desktop/)
- Start Docker Desktop (it must be running in the system tray)
- The first launch creates the PostgreSQL container automatically

### 1.2 Ollama
- Download from [ollama.com](https://ollama.com/)
- After install, pull the required models:
  ```bash
  ollama pull qwen2.5:7b-instruct-q3_K_M
  ollama pull nomic-embed-text
  ```

### 1.3 Python Packages
```bash
cd D:\university_project_demo
pip install -r requirements.txt
```

### 1.4 Environment Configuration
Make sure `.env` exists in the project root with your Twilio credentials:
```
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
```

---

## 2. Startup Sequence

> **Order matters.** Follow these steps in sequence.

### Step 1: Start Docker Desktop
- Open Docker Desktop from the Start Menu
- Wait for the whale icon to stop animating (engine is running)
- The PostgreSQL container should auto-start. Verify:
  ```bash
  docker ps --filter "name=elearning-postgres"
  ```
  You should see `elearning-postgres` with status `Up` / `(healthy)`.

  If it's not running, start it manually:
  ```bash
  docker start elearning-postgres
  ```

### Step 2: Start Ollama
- Ollama should auto-start with Windows. Verify it's running:
  ```bash
  curl http://127.0.0.1:11434/api/tags
  ```
  You should see a JSON response listing available models.

  If not running, launch Ollama from the Start Menu.

### Step 3: Start FastAPI Server
Open a **new terminal** (PowerShell or Command Prompt) and run:
```bash
cd D:\university_project_demo
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Wait until you see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

This may take 30-60 seconds — it loads ChromaDB, Whisper, and the AI models.

**Verify it's working:**
```bash
curl http://127.0.0.1:8000/
```
You should see a JSON response with `"status": "ok"`.

### Step 4: Start Cloudflare Tunnel
Open **another new terminal** and run:
```bash
cloudflared tunnel --url http://localhost:8000
```

After ~5 seconds you'll see:
```
+------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at:           |
|  https://xxxxx.trycloudflare.com                            |
+------------------------------------------------------------+
```

**Copy this URL** — it's your public access point. Save it to the project:
```bash
echo xxxxx.trycloudflare.com > D:\university_project_demo\.whatsapp_tunnel
```

Replace `xxxxx.trycloudflare.com` with your actual tunnel hostname.

### Step 5: Configure Twilio Console Webhooks
Open the [Twilio Console](https://console.twilio.com/) and configure these webhooks:

#### Inbound Voice Calls
1. Go to **Phone Numbers → Manage → Active Numbers** → click your number (`+19788198953`)
2. Under **Voice & Fax**:
   - **"A call comes in"**: Set to **Webhook** → `https://<tunnel>/twilio/voice` → **HTTP GET**
3. Click **Save**

#### WhatsApp (if using)
1. Go to **Messaging → Try it out → Send a WhatsApp message**
2. **"When a message comes in"**: Set to `https://<tunnel>/twilio/whatsapp` → **HTTP POST**

> ⚠️ **Important**: Both inbound voice calls and WhatsApp messages will fail if these webhooks aren't set.

### Step 6 (Optional): Start Streamlit Chat UI
Open a **third terminal** and run:
```bash
cd D:\university_project_demo
python -m streamlit run app.py --server.headless true
```

This starts the AI chat interface on `http://localhost:8501`.

### Step 6 (Optional): Start Streamlit Tunnels for Chat & Dashboard
If you want to share the Chat UI and Dashboard publicly:

```bash
# Terminal 4 — Chat UI tunnel
cloudflared tunnel --url http://localhost:8501

# Terminal 5 — Dashboard tunnel
cloudflared tunnel --url http://localhost:8502
```

Capture each URL and share as needed.

### 🚀 Quick Start (One-Click)
Instead of steps 1-6, you can run the bundled launcher:
```
Double-click: D:\university_project_demo\start_demo.bat
```
This starts everything automatically: FastAPI, Streamlit Chat, Dashboard, and 3 tunnels. Wait ~30 seconds for all services to be ready.

---

## 3. How to Use Each Component

### 3.1 Quick Call — Make AI Phone Calls

**From the webpage** (easiest):
```
Local:    http://localhost:8000/call
Public:   https://<tunnel-host>/call
```

1. Open the URL in any browser
2. Enter a phone number (e.g., `+917016872149`)
3. Optionally enter a name
4. Click **📞 Call Now**
5. Watch the status: `Queued → Ringing → Connected → Completed`
6. The AI admissions assistant will speak when the call is answered

**From the terminal** (CLI):
```bash
python quick_call.py +917016872149
# or interactively:
python quick_call.py
```

### 3.2 Streamlit Chat UI — Chat with the AI

```
Local:    http://localhost:8501
Public:   https://<chat-tunnel-host>    (if you started a tunnel for port 8501)
```

Type your questions about UMD/FDU admissions, tuition fees, programs, application process, etc.

### 3.3 Admin Dashboard — Manage Leads

```
Local:    http://localhost:8502
Public:   https://<dashboard-tunnel-host>    (if you started a tunnel for port 8502)
```

- **Overview** — KPIs: total leads, calls today, upcoming follow-ups
- **Leads** — View, add, edit leads. Click **"Call Now"** on any lead to queue a call
- **Conversations** — Browse call transcripts and WhatsApp chats
- **Scheduler** — Schedule follow-up calls

### 3.4 WhatsApp — Chat via WhatsApp

If you've configured the Twilio WhatsApp sandbox:

1. Go to [Twilio Console → WhatsApp](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Set the webhook URL to: `https://<tunnel-host>/twilio/whatsapp`
3. Method: **HTTP POST**
4. Users send a WhatsApp message to your Twilio number — the AI replies instantly

---

## 4. Sharing Links with Others

Once all tunnels are running, here are the links you can share:

| Tool | Public URL | What they can do |
|------|-----------|-----------------|
| **Quick Call** | `https://<tunnel>/call` | Enter a phone number, receive an AI phone call |
| **Chat UI** | `https://<chat-tunnel>` | Chat with the AI via text (if tunnel started for 8501) |
| **Dashboard** | `https://<dashboard-tunnel>` | Manage leads (if tunnel started for 8502) |

> **Note**: `<tunnel>` = `value-stronger-warned-mobility.trycloudflare.com` (or whatever URL cloudflared gives you for port 8000).

> **Important**: Cloudflare quick tunnels expire when you close the terminal. Each restart gives a new URL. For persistent URLs, use a named Cloudflare tunnel (see Cloudflare docs).

---

## 5. Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| `Docker not running` | Docker Desktop closed | Start Docker Desktop from Start Menu |
| `PostgreSQL not available` | Container stopped | `docker start elearning-postgres` |
| `Ollama not running` | Not launched | Open Ollama from Start Menu |
| `twilio not configured` | Missing `.env` | Create `.env` from `.env.example`, fill in Twilio credentials |
| `Tunnel URL not working` | Tunnel expired or crashed | Restart `cloudflared tunnel --url http://localhost:8000` |
| `Call fails: unverified number` | Trial Twilio account | In Twilio Console → Verified Caller IDs, add the number |
| `Call fails: geographic permissions` | India (+91) blocked | Twilio Console → Voice → Geographic Permissions → Enable India |
| `Page not found (404)` | Wrong port or tunnel URL | Make sure you're using the right tunnel URL for the right port |
| `Quick call shows Database unavailable` | Docker/PostgreSQL not running | Check `docker ps`, restart container if needed |

---

## 6. Stopping Everything

### Manual Stop
- **FastAPI**: Press `Ctrl+C` in its terminal
- **Cloudflare tunnels**: Press `Ctrl+C` in each tunnel terminal
- **Streamlit**: Press `Ctrl+C` in each Streamlit terminal
- **Docker**: Right-click Docker Desktop tray icon → Quit (or leave running for next time)
- **Ollama**: Right-click Ollama tray icon → Quit (or leave running)

### Quick Stop
Close all terminal windows. Docker and Ollama will keep running (you can leave them for next time).

---

## Quick Reference Card

```bash
# ── Start ─────────────────────────────────────────────────────
docker start elearning-postgres                              # Step 1: Database
ollama serve                                                 # Step 2: LLM (auto-starts)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # Step 3: FastAPI (terminal 1)
cloudflared tunnel --url http://localhost:8000               # Step 4: Tunnel   (terminal 2)
python -m streamlit run app.py --server.headless true        # Step 5: Chat UI  (terminal 3)

# ── Use ──────────────────────────────────────────────────────
http://localhost:8000/call                     # Quick Call (local)
https://<tunnel>/call                          # Quick Call (public)
http://localhost:8501                          # Chat UI
python quick_call.py +917016872149             # Quick Call (CLI)

# ── Stop ──────────────────────────────────────────────────────
Ctrl+C in each terminal
```
