@echo off
title University Admissions Advisor - Launcher (Local)
color 0B

echo ============================================================
echo    University Admissions Advisor - LOCAL MODE
echo ============================================================
echo.

:: ── Step 1: Check Ollama ──────────────────────────────────────
echo [1/3] Checking Ollama server...
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
echo [2/3] Checking PDF file...
if exist "content\sample_data\UMD_and_FDU_University_Profile_Report.pdf" (
    echo    [OK] University profile PDF found.
) else (
    echo    [FAIL] PDF not found at content\sample_data\UMD_and_FDU_University_Profile_Report.pdf
    pause
    exit /b 1
)

:: ── Step 3: Check Python packages ─────────────────────────────
echo [3/3] Checking Python packages...
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

:: ── Launch ────────────────────────────────────────────────────
echo.
echo ============================================================
echo    All checks passed! Starting the app...
echo    Open: http://localhost:8501
echo    Press Ctrl+C to stop.
echo ============================================================
echo.
start http://localhost:8501
python -m streamlit run app.py --server.headless true
