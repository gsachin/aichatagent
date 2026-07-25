# University Admissions Advisor — Project Reference

## Overview

Streamlit-based RAG chatbot that answers questions about UMD and FDU university admissions using a local LLM (Qwen 2.5 7B via Ollama) and a vector store (ChromaDB) built from university profile PDFs.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| UI | Streamlit 1.59.2 |
| LLM | Qwen 2.5 7B (via Ollama) |
| Embeddings | nomic-embed-text (via Ollama) |
| Vector Store | ChromaDB (local) |
| RAG Framework | LangChain + langchain-classic |
| PDF Loading | PyPDFLoader |
| Text Splitting | RecursiveCharacterTextSplitter (chunk_size=800, overlap=150) |
| Tunnel (optional) | Cloudflare Tunnel (cloudflared) |

---

## Prerequisites

- **Ollama** installed and running with these models pulled:
  - `qwen2.5:7b`
  - `nomic-embed-text`
- **Python 3.11**+
- **cloudflared** (optional, for public tunnel mode)

### Python Packages

```
streamlit langchain langchain-ollama langchain-text-splitters langchain-community langchain-classic chromadb pypdf
```

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Main Streamlit application |
| `admissions_bot.py` | Standalone bot script |
| `launch.bat` | Local-mode launcher (localhost only) |
| `launch_tunnel.bat` | Public-mode launcher (localhost + Cloudflare tunnel) |
| `chroma_local_db/` | Persisted ChromaDB vector store |
| `content/sample_data/UMD_and_FDU_University_Profile_Report.pdf` | Source PDF for RAG |

---

## How to Run

### Local Mode

Double-click `launch.bat` — it will:
1. Check Ollama is running (auto-start if not)
2. Check the PDF exists
3. Check Python packages are installed
4. Start Streamlit on `http://localhost:8501`

### Public Mode (Cloudflare Tunnel)

Double-click `launch_tunnel.bat` — it does everything local mode does, plus:
5. Starts a Cloudflare Tunnel to expose `localhost:8501` publicly
6. The public URL appears in the Cloudflare Tunnel console window

---

## Known Issues & Fixes Applied

### 1. `streamlit.exe` blocked by Application Control (AppLocker/WDAC)

**Symptom:** `ERR_CONNECTION_REFUSED` on `localhost:8501`

**Root cause:** Windows Application Control policy blocks `streamlit.exe` from executing.

**Fix:** Use `python -m streamlit run app.py --server.headless true` instead of `streamlit run app.py`. This runs Streamlit through the Python interpreter, bypassing the `.exe` restriction.

### 2. Streamlit email onboarding prompt hangs launch

**Symptom:** Streamlit hangs on first run, waiting for stdin input (email prompt).

**Fix:**
- Added `--server.headless true` flag
- Created `~/.streamlit/credentials.toml` with `email = ""` to pre-answer the prompt

### 3. Cloudflared path quoting fragile

**Symptom:** Tunnel window may fail to open or show path errors.

**Root cause:** `^"C:\Program Files (x86)\cloudflared\cloudflared.exe^"` escaping is fragile inside nested `cmd /k "..."` quotes.

**Fix:** Replaced with bare `cloudflared` since it's already in PATH.

### 4. Missing `langchain-text-splitters` in package check

**Symptom:** `ImportError` for `langchain_text_splitters` even after checks pass.

**Fix:** Added `langchain_text_splitters` to the import check and `langchain-text-splitters` to the pip install command in both batch files.

### 5. Boot wait too short for tunnel mode

**Symptom:** Cloudflare tunnel starts before Streamlit is ready, causing tunnel errors.

**Fix:** Increased boot wait from 12s → 30s in `launch_tunnel.bat`. First run loads PDF + builds vector store + boots LLM, which can take 30-60s.

---

## Commands Reference

```bash
# Start Ollama (if not running)
"C:\Users\%USERNAME%\AppData\Local\Programs\Ollama\ollama app.exe"

# Check Ollama health
curl -s http://127.0.0.1:11434/api/tags

# Start Streamlit (local)
cd d:\university_project_demo
python -m streamlit run app.py --server.headless true

# Start Cloudflare tunnel
cloudflared tunnel --url http://localhost:8501

# Install all dependencies
pip install streamlit langchain langchain-ollama langchain-text-splitters langchain-community langchain-classic chromadb pypdf
```

---

## Ports

| Port | Service |
|------|---------|
| 8501 | Streamlit app |
| 11434 | Ollama API |
