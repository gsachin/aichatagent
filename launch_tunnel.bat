@echo off
title University Admissions Advisor - Launcher + Tunnel
color 0B

echo ============================================================
echo    University Admissions Advisor - PUBLIC MODE (Cloudflare)
echo ============================================================
echo.

:: ── Step 1: Check Ollama ──────────────────────────────────────
echo [1/4] Checking Ollama server...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    Ollama is NOT running. Starting Ollama now...
    start "" "C:\Users\%USERNAME%\AppData\Local\Programs\Ollama\ollama app.exe"
    echo    Waiting 10 seconds for Ollama to start...
    timeout /t 10 /nobreak >nul
    curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
    if %errorlevel% NEQ 0 (
        echo    [FAIL] Could not start Ollama. Please start it manually from Start Menu.
        pause
        exit /b 1
    )
    echo    [OK] Ollama started successfully.
) else (
    echo    [OK] Ollama is already running.
)

:: ── Step 2: Check PDF ─────────────────────────────────────────
echo [2/4] Checking PDF file...
if exist "content\sample_data\UMD_and_FDU_University_Profile_Report.pdf" (
    echo    [OK] University profile PDF found.
) else (
    echo    [FAIL] PDF not found at content\sample_data\UMD_and_FDU_University_Profile_Report.pdf
    pause
    exit /b 1
)

:: ── Step 3: Check Python packages ─────────────────────────────
echo [3/4] Checking Python packages...
python -c "import streamlit, langchain, langchain_ollama, langchain_classic, langchain_community, langchain_text_splitters, chromadb, pypdf" >nul 2>&1
if %errorlevel% NEQ 0 (
    echo    Some packages missing. Installing now...
    pip install -q streamlit langchain langchain-ollama langchain-text-splitters chromadb pypdf langchain-community langchain-classic
    if %errorlevel% NEQ 0 (
        echo    [FAIL] Could not install packages.
        pause
        exit /b 1
    )
    echo    [OK] Packages installed.
) else (
    echo    [OK] All Python packages present.
)

:: ── Step 4: Launch Streamlit + Tunnel ─────────────────────────
echo [4/4] All checks passed! Starting app + tunnel...
echo.

echo --> Starting Streamlit app...
start "Streamlit - University Advisor" cmd /k "cd /d %~dp0 && python -m streamlit run app.py --server.headless true"

echo --> Waiting for Streamlit to boot (30 seconds)...
timeout /t 30 /nobreak >nul

echo --> Starting Cloudflare Tunnel...
start "Cloudflare Tunnel - Public URL" cmd /k "cd /d %~dp0 && cloudflared tunnel --url http://localhost:8501"

echo.
echo ============================================================
echo    LOCAL:       http://localhost:8501
echo    TUNNEL URL:  Check the "Cloudflare Tunnel" window ^^
echo    Share with:  Anyone, anywhere in the world
echo ============================================================
echo.
echo To stop: close both windows or press Ctrl+C in each.
echo.
start http://localhost:8501
pause
