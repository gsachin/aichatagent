@echo off
title University Admissions Advisor — Full Demo Launcher
color 0B

echo.
echo ============================================================
echo    University Admissions Advisor — DEMO MODE
echo    Starts Streamlit + WhatsApp backend + Public URLs
echo ============================================================
echo.

:: ── Step 1: Check Ollama ──────────────────────────────────────
echo [1/4] Checking Ollama server...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    [FAIL] Ollama is NOT running.
    echo    Open Start Menu, launch Ollama, then re-run this script.
    pause
    exit /b 1
)
echo    [OK] Ollama is running.

:: ── Step 2: Check Python packages ─────────────────────────────
echo [2/4] Checking Python packages...
python -c "import streamlit, fastapi, uvicorn, langchain_ollama, chromadb, kokoro_onnx, whisper, soundfile" >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    Some packages missing. Install them first:
    echo    pip install -r requirements.txt
    pause
    exit /b 1
)
echo    [OK] All Python packages present.

:: ── Step 3: Start Streamlit ───────────────────────────────────
echo [3/4] Starting Streamlit (port 8501)...
start "Streamlit — Chat UI" cmd /k "cd /d %~dp0 && python -m streamlit run app.py --server.headless true && pause"

echo    Waiting for Streamlit to boot (20 seconds)...
timeout /t 20 /nobreak >nul

:: ── Step 4: Start FastAPI ─────────────────────────────────────
echo [4/4] Starting FastAPI WhatsApp backend (port 8000)...
start "FastAPI — WhatsApp Backend" cmd /k "cd /d %~dp0 && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 && pause"

echo    Waiting for FastAPI to boot (10 seconds)...
timeout /t 10 /nobreak >nul

:: ── Step 5: Start Cloudflare Tunnels ──────────────────────────
echo.
echo ============================================================
echo    Starting public tunnels...
echo    Two new windows will open with public URLs.
echo ============================================================
echo.

start "TUNNEL — Streamlit" cmd /k "echo STREAMLIT PUBLIC URL: && cloudflared tunnel --url http://localhost:8501"
timeout /t 3 /nobreak >nul

start "TUNNEL — WhatsApp" cmd /k "echo WHATSAPP PUBLIC URL: && cloudflared tunnel --url http://localhost:8000"

:: ── Step 6: Open browser ──────────────────────────────────────
echo.
echo ============================================================
echo    Opening Streamlit in your browser...
echo ============================================================
start http://localhost:8501

:: ── Step 7: Instructions ──────────────────────────────────────
echo.
echo ============================================================
echo    DEMO IS STARTING — READ BELOW
echo ============================================================
echo.
echo    WINDOWS OPENED:
echo    ┌─────────────────────────────────────────────────────┐
echo    │ 1. Streamlit — Chat UI (green title)               │
echo    │ 2. FastAPI — WhatsApp backend (green title)        │
echo    │ 3. TUNNEL Streamlit (get public URL from here)     │
echo    │ 4. TUNNEL WhatsApp (get public URL from here)      │
echo    │ 5. Browser — http://localhost:8501                 │
echo    └─────────────────────────────────────────────────────┘
echo.
echo    NEXT STEPS (you do these once):
echo.
echo    Step A: Look at the "TUNNEL — WhatsApp" window.
echo            Copy the https://xxxx.trycloudflare.com URL
echo.
echo    Step B: Go to Twilio Console:
echo            https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn
echo.
echo    Step C: Set "When a message comes in" to:
echo            https://xxxx.trycloudflare.com/twilio/whatsapp (HTTP POST)
echo.
echo    Step D: On your phone, WhatsApp the join code to +14155238886
echo.
echo    Step E: Start chatting!
echo            - Browser: http://localhost:8501 (or the Streamlit tunnel URL)
echo            - WhatsApp: Send message to sandbox number
echo.
echo    TO STOP: Close all 4 windows.
echo ============================================================
echo.
pause
