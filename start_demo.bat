@echo off
title University Admissions Advisor — Demo Launcher
color 0B
cd /d "%~dp0"

:: Clean up old tunnel files
del /q "%TEMP%\whatsapp_tunnel_url.txt" 2>NUL
del /q "%TEMP%\streamlit_tunnel_url.txt" 2>NUL

echo.
echo ============================================================
echo    University Admissions Advisor - DEMO MODE
echo ============================================================
echo.

:: ── Check Ollama ─────────────────────────────────────────────
echo [1/5] Checking Ollama...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    [FAIL] Ollama NOT running. Launch from Start Menu.
    pause
    exit /b 1
)
echo    [OK] Ollama running

:: ── Check packages ───────────────────────────────────────────
echo [2/5] Checking Python packages...
python -c "import streamlit,fastapi,uvicorn,langchain_ollama,chromadb,kokoro_onnx,whisper,soundfile,twilio" >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    [FAIL] Run: pip install -r requirements.txt
    pause
    exit /b 1
)
echo    [OK] All packages present

:: ── Start servers ────────────────────────────────────────────
echo [3/5] Starting servers...
start "Streamlit Chat UI" cmd /c "cd /d %~dp0 && title Streamlit - http://localhost:8501 && python -m streamlit run app.py --server.headless true"
start "FastAPI WhatsApp Backend" cmd /c "cd /d %~dp0 && title FastAPI - port 8000 && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

:: ── Start tunnels and capture URLs ───────────────────────────
echo [4/5] Starting tunnels (wait 20 seconds)...

:: Start WhatsApp tunnel - redirect stderr to temp file
start "WhatsApp Tunnel URL - WAIT" cmd /c "cd /d %~dp0 && cloudflared tunnel --url http://localhost:8000 2> %TEMP%\whatsapp_tunnel_raw.txt"
timeout /t 3 /nobreak >nul

:: Start Streamlit tunnel
start "Streamlit Tunnel URL - WAIT" cmd /c "cd /d %~dp0 && cloudflared tunnel --url http://localhost:8501 2> %TEMP%\streamlit_tunnel_raw.txt"

:: Wait for tunnels to get URLs
timeout /t 20 /nobreak >nul

:: ── Extract URLs ─────────────────────────────────────────────
echo [5/5] Extracting URLs...

:: Extract WhatsApp URL
for /f "tokens=4" %%a in ('type "%TEMP%\whatsapp_tunnel_raw.txt" 2^>NUL ^| findstr "trycloudflare.com"') do (
    echo %%a > "%TEMP%\whatsapp_tunnel_url.txt"
    set WHATSAPP_URL=%%a
)

:: Extract Streamlit URL
for /f "tokens=4" %%a in ('type "%TEMP%\streamlit_tunnel_raw.txt" 2^>NUL ^| findstr "trycloudflare.com"') do (
    echo %%a > "%TEMP%\streamlit_tunnel_url.txt"
    set STREAMLIT_URL=%%a
)

:: Set TUNNEL_HOST for this session
for /f "tokens=*" %%a in ('type "%TEMP%\whatsapp_tunnel_url.txt" 2^>NUL') do set TUNNEL_HOST=%%a

:: Write to project file so Python can read it
echo %TUNNEL_HOST% > "%~dp0.whatsapp_tunnel"

:: ── Open browser and Twilio Console ───────────────────────────
start http://localhost:8501
start https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn

:: ── Display results ──────────────────────────────────────────
echo.
echo ============================================================
echo    SETUP COMPLETE
echo ============================================================
echo.
echo    LOCAL:
echo      Streamlit:  http://localhost:8501
echo.
echo    PUBLIC (share with others):
echo      Streamlit:  %STREAMLIT_URL%
echo      WhatsApp:   %WHATSAPP_URL%
echo.
echo    TWILIO WEBHOOK URL (copy this):
echo      %WHATSAPP_URL%/twilio/whatsapp
echo.
echo    The Twilio Console is already open in your browser.
echo    Paste the webhook URL above into:
echo      "When a message comes in" -^> HTTP POST
echo.
echo ============================================================
echo    TO STOP: Close all 4 terminal windows
echo ============================================================
echo.
pause
