#!/bin/bash

################################################################################
# Start Services - University Admissions Voice AI Assistant
################################################################################
# This script starts all required services:
# 1. Ollama (LLM server)
# 2. FastAPI (backend API)
# 3. Streamlit (web UI)
#
# Usage:
#   bash start.sh                 # Use defaults
#   bash start.sh --dev           # Dev mode with auto-reload
#   bash start.sh --port 8001     # Custom FastAPI port
#   bash start.sh --help          # Show help
################################################################################

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"
ENV_FILE="$PROJECT_ROOT/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
FASTAPI_PORT=8000
STREAMLIT_PORT=8501
DEV_MODE=false
OLLAMA_PORT=11434

# Load environment
if [ -f "$ENV_FILE" ]; then
    export $(cat "$ENV_FILE" | grep -v '^#' | grep '=' | cut -d= -f1,2 | tr '\n' ' ')
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dev)
            DEV_MODE=true
            shift
            ;;
        --port)
            FASTAPI_PORT="$2"
            STREAMLIT_PORT=$((FASTAPI_PORT + 501))
            shift 2
            ;;
        --help)
            cat << EOF
Usage: bash start.sh [OPTIONS]

Options:
    --dev               Start in development mode (auto-reload)
    --port PORT         FastAPI port (default: 8000)
    --help              Show this help message

Examples:
    bash start.sh                 # Production mode
    bash start.sh --dev           # Development mode with auto-reload
    bash start.sh --port 8001     # Custom port

EOF
            exit 0
            ;;
        *)
            echo -e "${RED}✗ Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
}

log_section() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Check virtual environment
if [ ! -d "$VENV_PATH" ]; then
    log_error "Virtual environment not found at $VENV_PATH"
    log_info "Please run: bash setup.sh"
    exit 1
fi

# Activate virtual environment
source "$VENV_PATH/bin/activate"
log_success "Virtual environment activated"

# Function to kill port
kill_port() {
    local port=$1
    local pids=$(lsof -ti:$port 2>/dev/null || echo "")
    if [ -n "$pids" ]; then
        log_info "Port $port is in use. Killing process(es)..."
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
        log_success "Port $port freed"
    fi
}

log_section "University Admissions Voice AI Assistant - Service Startup"

log_info "Configuration:"
log_info "  FastAPI Port:     $FASTAPI_PORT"
log_info "  Streamlit Port:   $STREAMLIT_PORT"
log_info "  Ollama Port:      $OLLAMA_PORT"
log_info "  Mode:             $([ "$DEV_MODE" = true ] && echo "Development" || echo "Production")"

# Check if Ollama is running
log_section "Checking Services"

if lsof -ti:$OLLAMA_PORT >/dev/null 2>&1; then
    log_success "Ollama is running on port $OLLAMA_PORT"
else
    log_info "Ollama is not running"
    log_info "To start Ollama manually in another terminal:"
    log_info "  ollama serve"
fi

# Kill existing processes on ports
log_section "Preparing Ports"
kill_port "$FASTAPI_PORT"
kill_port "$STREAMLIT_PORT"

# Create logs directory
mkdir -p "$PROJECT_ROOT/logs"

log_section "Starting Services"

# Start FastAPI in background
log_info "Starting FastAPI server on port $FASTAPI_PORT..."
if [ "$DEV_MODE" = true ]; then
    log_info "  (Development mode with auto-reload)"
    nohup python -m uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "$FASTAPI_PORT" \
        --reload \
        > "$PROJECT_ROOT/logs/fastapi.log" 2>&1 &
else
    nohup python -m uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "$FASTAPI_PORT" \
        --workers 4 \
        > "$PROJECT_ROOT/logs/fastapi.log" 2>&1 &
fi
log_success "FastAPI started (PID: $!)"

# Wait for FastAPI to start
sleep 2

# Start Streamlit in background
log_info "Starting Streamlit Web UI on port $STREAMLIT_PORT..."
nohup streamlit run app.py \
    --server.port "$STREAMLIT_PORT" \
    --server.headless true \
    --logger.level=info \
    > "$PROJECT_ROOT/logs/streamlit.log" 2>&1 &
log_success "Streamlit started (PID: $!)"

# Wait for Streamlit to start
sleep 3

log_section "Services Started Successfully! 🚀"

cat << EOF

Access your services:

  🌐 Web UI (Streamlit):
     http://localhost:$STREAMLIT_PORT

  📡 API Documentation (FastAPI):
     http://localhost:$FASTAPI_PORT/docs

  🔗 API Endpoint:
     http://localhost:$FASTAPI_PORT

Services:
  ✓ FastAPI:   http://0.0.0.0:$FASTAPI_PORT (background)
  ✓ Streamlit: http://0.0.0.0:$STREAMLIT_PORT (background)
  $([ -z "$(lsof -ti:$OLLAMA_PORT 2>/dev/null)" ] && echo "✗ Ollama:    Not running (start manually: ollama serve)" || echo "✓ Ollama:    http://0.0.0.0:$OLLAMA_PORT")

Logs:
  FastAPI:   $PROJECT_ROOT/logs/fastapi.log
  Streamlit: $PROJECT_ROOT/logs/streamlit.log

Stop services:
  bash stop.sh

View logs:
  tail -f $PROJECT_ROOT/logs/fastapi.log
  tail -f $PROJECT_ROOT/logs/streamlit.log

Environment:
  Virtual env:  $VENV_PATH (activated)
  Project:      $PROJECT_ROOT

Happy development! 🎓🚀

EOF

log_success "All services running in background"
log_info "Keep this terminal open or use 'bash stop.sh' to stop services"
