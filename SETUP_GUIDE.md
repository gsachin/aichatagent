# Setup & Deployment Guide

**Date:** 2026-07-25  
**Status:** Complete - Single-Click Setup Ready

---

## Overview

This project includes complete setup and deployment automation for the University Admissions Voice AI Assistant. Three setup methods are available:

1. **setup.sh** - Recommended: Full automated setup with Python virtual environment
2. **docker-compose.yml** - Optional: Complete containerized deployment
3. **Manual** - For advanced users

---

## Prerequisites

### Required
- **macOS 11+** or **Linux** (WSL2 on Windows)
- **Docker Desktop** (for Docker deployment; setup.sh will verify)
- **Python 3.10+** (for local setup)
- **4GB RAM minimum** (8GB+ recommended)
- **6GB+ disk space** (for models)

### Optional
- **Ollama** (automatic detection; can be installed manually)
- **Homebrew** (for macOS)

---

## Method 1: Automated Setup (Recommended)

### One-Click Setup

```bash
# Navigate to project directory
cd /path/to/aichatagent

# Run setup script (handles everything)
bash setup.sh

# You'll see this output:
# ✓ Docker verified
# ✓ Python 3.11 found
# ✓ .env created
# ✓ Virtual environment created
# ✓ Dependencies installed
# ✓ All validation passed
```

### What the setup.sh script does

```
✓ Verifies Docker Desktop is installed and running
✓ Checks Python 3.10+ availability
✓ Creates .env file with sensible defaults
✓ Creates and activates Python virtual environment
✓ Installs all dependencies from requirements.txt
✓ Validates PyTorch, Streamlit, FastAPI installation
✓ Tests platform detection (GPU/Metal/CPU)
✓ Creates necessary directories (logs, models, chroma_db)
✓ Frees up ports 8000, 8501, 11434
✓ Displays summary and next steps
```

### Custom Ports

```bash
# Use custom FastAPI port (Streamlit auto-calculated as FastAPI + 501)
bash setup.sh --port 8001
```

---

## Method 2: Start Services

After setup, start the services:

### Option A: Both Services in Background

```bash
# Start both FastAPI and Streamlit
bash start.sh

# Output shows:
# ✓ FastAPI started on http://localhost:8000
# ✓ Streamlit started on http://localhost:8501
```

### Option B: Development Mode (with auto-reload)

```bash
bash start.sh --dev

# FastAPI will auto-reload on code changes
```

### Option C: Custom Port

```bash
bash start.sh --port 8001

# FastAPI: http://localhost:8001
# Streamlit: http://localhost:8502
```

### Start Individual Services Manually

```bash
# Terminal 1: Activate environment
source venv/bin/activate

# Terminal 2: Start Ollama (if not running)
ollama serve

# Terminal 3: Start FastAPI (in project directory)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 4: Start Streamlit (in project directory)
streamlit run app.py --server.port 8501
```

---

## Method 3: Docker Compose (Optional)

For containerized deployment:

### Prerequisites
- Docker Desktop installed and running

### Start All Services

```bash
# Start all services (Ollama + FastAPI + Streamlit)
docker-compose up -d

# Monitor startup
docker-compose logs -f

# Services ready when all show "healthy"
```

### Access Services

```bash
Web UI:       http://localhost:8501
API Docs:     http://localhost:8000/docs
Ollama:       http://localhost:11434
```

### Stop Services

```bash
docker-compose down

# Also remove volumes (data)
docker-compose down -v
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f ollama
docker-compose logs -f fastapi
docker-compose logs -f streamlit
```

---

## Post-Setup: Service Access

### Web Application

```
URL: http://localhost:8501

Features:
- ✓ Chat with voice input
- ✓ Text-based queries
- ✓ Real-time responses
- ✓ Local processing (no cloud)
```

### API Documentation

```
URL: http://localhost:8000/docs

Interactive API explorer:
- Try API endpoints
- See request/response schemas
- Get authentication token
```

### Health Check

```bash
# Test API
curl http://localhost:8000/health

# Test Streamlit
curl http://localhost:8501

# Test Ollama
curl http://localhost:11434/api/tags
```

---

## Port Management

### Default Ports

| Service | Port | URL |
|---------|------|-----|
| **Ollama** | 11434 | http://localhost:11434 |
| **FastAPI** | 8000 | http://localhost:8000 |
| **Streamlit** | 8501 | http://localhost:8501 |

### Port Conflicts

The setup scripts automatically handle port conflicts:

```bash
# If port is in use, the script will:
# 1. Detect it's in use
# 2. Kill the process safely
# 3. Free the port
# 4. Start the service
```

### Manual Port Management

```bash
# Find process using port
lsof -ti:8000

# Kill process
kill -9 <PID>

# Or use the stop script
bash stop.sh
```

---

## Troubleshooting

### Docker Not Running

```
Error: "Docker Desktop is not running"

Solution:
1. Open Docker Desktop application
2. Wait for it to fully start
3. Re-run setup.sh
```

### Port Already in Use

```
Error: "Address already in use: ('0.0.0.0', 8000)"

Solution 1: Use different port
  bash start.sh --port 8001

Solution 2: Kill existing process
  bash stop.sh
```

### PyTorch Installation Issues

```
Error: "ImportError: No module named 'torch'"

Solution:
1. Ensure virtual environment is activated
2. Re-run setup.sh
3. Check requirements.txt is present
```

### Ollama Connection Error

```
Error: "Failed to connect to Ollama at http://localhost:11434"

Solution:
1. Start Ollama manually:
   ollama serve

2. Or verify it's running:
   curl http://localhost:11434/api/tags
```

### Memory Issues

```
Error: "CUDA out of memory" or "Metal GPU out of memory"

Solutions:
1. Reduce num_ctx in .env (from 2048 to 1024)
2. Use smaller model
3. Close other applications
4. Use CPU mode (slower)
```

### Streamlit Connection Refused

```
Error: "ConnectionRefusedError: [Errno 111] Connection refused"

Solution:
1. Ensure FastAPI is running on port 8000
2. Check FastAPI logs: tail -f logs/fastapi.log
3. Restart FastAPI if needed: bash stop.sh && bash start.sh
```

---

## Environment Variables

### .env File

Created automatically by setup.sh. Key variables:

```bash
# GPU/Compute (auto-detected)
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:6b-instruct-q4_K_M
OLLAMA_NUM_CTX=2048

# API
FASTAPI_PORT=8000
FASTAPI_HOST=0.0.0.0

# Web UI
STREAMLIT_PORT=8501

# Whisper STT
WHISPER_MODEL=small.en

# Kokoro TTS
KOKORO_VOICE=af_heart

# Logging
LOG_LEVEL=INFO
```

### Custom Configuration

Edit `.env` file to customize:

```bash
# Use different Ollama model
OLLAMA_MODEL=neural-chat:latest

# Use different STT model
WHISPER_MODEL=tiny.en  # Faster but less accurate

# Use different port
FASTAPI_PORT=8001
```

---

## Performance Optimization

### For Apple Silicon (M1/M2/M3)

```bash
# Already optimized via app/platform.py
# - Auto-detects Metal GPU
# - Uses FP16 precision
# - 36GB unified memory
# - Expected latency: ~250ms per turn
```

### For NVIDIA GPU

```bash
# Already optimized via app/platform.py
# - Detects CUDA automatically
# - Uses INT8 quantization
# - Expected latency: ~200ms per turn
```

### For CPU-Only

```bash
# Fallback mode (slower)
# - Uses FP32 precision
# - Expected latency: ~2800ms per turn
# - Recommended: 12GB+ RAM
```

---

## Logs & Debugging

### Log Locations

```
logs/
├── fastapi.log      # API server logs
├── streamlit.log    # Web UI logs
└── voice_assistant.log  # Main application log
```

### View Logs

```bash
# FastAPI
tail -f logs/fastapi.log

# Streamlit
tail -f logs/streamlit.log

# All
tail -f logs/*.log
```

### Enable Debug Mode

```bash
# Edit .env
LOG_LEVEL=DEBUG

# Restart services
bash stop.sh
bash start.sh --dev
```

---

## Next Steps

1. **Setup:**
   ```bash
   bash setup.sh
   ```

2. **Start Services:**
   ```bash
   bash start.sh
   ```

3. **Access Web UI:**
   ```
   http://localhost:8501
   ```

4. **Try API:**
   ```
   http://localhost:8000/docs
   ```

5. **For Docker:**
   ```bash
   docker-compose up -d
   ```

---

## Uninstall/Cleanup

```bash
# Stop services
bash stop.sh

# Remove virtual environment
rm -rf venv

# Remove logs
rm -rf logs

# Remove local database
rm -rf chroma_local_db

# Remove Docker containers (if used)
docker-compose down -v
```

---

## Quick Commands Reference

```bash
# Setup
bash setup.sh                  # Full setup
bash setup.sh --port 8001      # Custom port

# Services
bash start.sh                  # Start services
bash start.sh --dev            # Development mode
bash stop.sh                   # Stop services

# Testing
python test_environment.py     # Verify setup
python -m pytest tests/ -v     # Run tests

# Docker
docker-compose up -d           # Start containers
docker-compose down            # Stop containers
docker-compose logs -f         # View logs

# Logs
tail -f logs/fastapi.log       # FastAPI logs
tail -f logs/streamlit.log     # Streamlit logs

# Development
source venv/bin/activate       # Activate environment
python -m uvicorn app.main:app --reload  # FastAPI
streamlit run app.py           # Streamlit
```

---

## Support

For issues:

1. Check `logs/` directory for error messages
2. Run `python test_environment.py` to verify setup
3. Review `.env` file for misconfigurations
4. Check internet connectivity (first startup may download models)

---

**Status: ✅ READY TO USE**

All setup scripts are ready. Start with:
```bash
bash setup.sh && bash start.sh
```

Then visit: http://localhost:8501

Happy coding! 🚀🎓
