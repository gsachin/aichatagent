#!/bin/bash

################################################################################
# University Admissions Voice AI Assistant - Complete Setup Script
################################################################################
# This script handles:
# 1. Docker Desktop verification
# 2. Environment setup (.env creation)
# 3. Python environment configuration
# 4. Dependency installation
# 5. Port management (kill existing, restart services)
# 6. Service initialization (Ollama, FastAPI, Streamlit)
#
# Usage:
#   bash setup.sh                 # Use defaults
#   bash setup.sh --port 8001     # Custom port
#   bash setup.sh --help          # Show help
################################################################################

set -e  # Exit on error

# ============================================================================
# Configuration & Defaults
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"
PYTHON_VERSION="3.11"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
FASTAPI_PORT="${FASTAPI_PORT:-8000}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"
VENV_PATH="$PROJECT_ROOT/venv"
ENV_FILE="$PROJECT_ROOT/.env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# Helper Functions
# ============================================================================

log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
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

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Kill process on port
kill_port() {
    local port=$1
    local pids=$(lsof -ti:$port 2>/dev/null || echo "")
    if [ -n "$pids" ]; then
        log_warn "Port $port is in use. Killing process(es)..."
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
        log_success "Port $port freed"
    fi
}

# ============================================================================
# Step 1: Parse Arguments
# ============================================================================

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --port)
                FASTAPI_PORT="$2"
                STREAMLIT_PORT=$((FASTAPI_PORT + 501))
                shift 2
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat << EOF
Usage: bash setup.sh [OPTIONS]

Options:
    --port PORT             FastAPI port (default: 8000, Streamlit: 8501)
    --help                  Show this help message

Examples:
    bash setup.sh                 # Use default ports (FastAPI: 8000, Streamlit: 8501)
    bash setup.sh --port 8001     # Use FastAPI: 8001, Streamlit: 8502

EOF
}

# ============================================================================
# Step 2: Docker Desktop Check
# ============================================================================

check_docker() {
    log_section "Checking Docker Desktop"
    
    if ! command_exists docker; then
        log_error "Docker Desktop is not installed"
        echo ""
        echo "Please install Docker Desktop from: https://www.docker.com/products/docker-desktop"
        echo ""
        echo "After installation, please restart this script:"
        echo "    bash setup.sh"
        exit 1
    fi
    
    # Check if Docker daemon is running
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker Desktop is not running"
        echo ""
        echo "Please start Docker Desktop and try again"
        echo ""
        exit 1
    fi
    
    local docker_version=$(docker --version)
    log_success "Docker installed: $docker_version"
}

# ============================================================================
# Step 3: Check System Requirements
# ============================================================================

check_system() {
    log_section "Checking System Requirements"
    
    # Check Python
    if ! command_exists python3; then
        log_error "Python 3 is not installed"
        exit 1
    fi
    
    local python_version=$(python3 --version 2>&1 | awk '{print $2}')
    log_success "Python $python_version installed"
    
    # Check Homebrew (for macOS)
    if ! command_exists brew; then
        log_warn "Homebrew not installed - some features may not be available"
    else
        log_success "Homebrew installed"
    fi
    
    # Check if on macOS
    if [[ "$OSTYPE" != "darwin"* ]]; then
        log_warn "This script is optimized for macOS. Some features may not work on $OSTYPE"
    fi
}

# ============================================================================
# Step 4: Create/Verify .env File
# ============================================================================

setup_env() {
    log_section "Setting Up Environment Variables"
    
    if [ -f "$ENV_FILE" ]; then
        log_success ".env file already exists"
    else
        log_info "Creating .env file..."
        cat > "$ENV_FILE" << 'EOF'
# University Admissions Voice AI Assistant Configuration
# Generated by setup.sh

# ── Python Environment ──────────────────────────────────────────
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1

# ── GPU / Compute ────────────────────────────────────────────────
# Platform detection is automatic via app/platform.py
# DEVICE=mps (Metal GPU on Apple Silicon)
# COMPUTE_TYPE=float16 (optimal for Metal)

# ── Ollama Service ───────────────────────────────────────────────
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:6b-instruct-q4_K_M
OLLAMA_NUM_CTX=2048

# ── Whisper STT ──────────────────────────────────────────────────
WHISPER_MODEL=small.en
WHISPER_DEVICE=auto  # auto-detect via app/platform.py

# ── Kokoro TTS ───────────────────────────────────────────────────
KOKORO_VOICE=af_heart
KOKORO_MODEL_PATH=./models/kokoro-v0_19.onnx

# ── FastAPI Server ───────────────────────────────────────────────
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
FASTAPI_RELOAD=true
FASTAPI_LOG_LEVEL=info

# ── Streamlit Web UI ──────────────────────────────────────────────
STREAMLIT_PORT=8501
STREAMLIT_SERVER_HEADLESS=false

# ── RAG / ChromaDB ───────────────────────────────────────────────
CHROMA_DB_PATH=./chroma_local_db
RAG_TOP_K=2

# ── Logging ──────────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_FILE=logs/voice_assistant.log

# ── Feature Flags ────────────────────────────────────────────────
ENABLE_VOICE_INPUT=true
ENABLE_VOICE_OUTPUT=true
ENABLE_RAG=true
ENABLE_OPENAPI_DOCS=true
EOF
        log_success ".env file created at $ENV_FILE"
    fi
}

# ============================================================================
# Step 5: Setup Python Virtual Environment
# ============================================================================

setup_python_venv() {
    log_section "Setting Up Python Virtual Environment"
    
    if [ -d "$VENV_PATH" ]; then
        log_success "Virtual environment already exists"
    else
        log_info "Creating virtual environment..."
        python3 -m venv "$VENV_PATH"
        log_success "Virtual environment created"
    fi
    
    # Activate virtual environment
    source "$VENV_PATH/bin/activate"
    log_success "Virtual environment activated"
    
    # Upgrade pip
    log_info "Upgrading pip..."
    pip install --upgrade pip setuptools wheel -q
    log_success "pip upgraded"
}

# ============================================================================
# Step 6: Install Python Dependencies
# ============================================================================

install_dependencies() {
    log_section "Installing Python Dependencies"
    
    if [ ! -f "$PROJECT_ROOT/requirements.txt" ]; then
        log_error "requirements.txt not found"
        exit 1
    fi
    
    log_info "Installing dependencies from requirements.txt..."
    
    # Install with progress
    pip install -r "$PROJECT_ROOT/requirements.txt" -q
    
    log_success "All Python dependencies installed"
    
    # Show installed packages
    log_info "Verifying key packages..."
    pip show torch PyTorch streamlit fastapi ollama faster-whisper 2>/dev/null | grep "^Name:" || log_warn "Some packages may not be installed"
}

# ============================================================================
# Step 7: Verify Ollama
# ============================================================================

verify_ollama() {
    log_section "Verifying Ollama Service"
    
    if command_exists ollama; then
        local ollama_version=$(ollama --version 2>/dev/null || echo "unknown")
        log_success "Ollama installed: $ollama_version"
    else
        log_warn "Ollama not installed (optional)"
        log_info "Install from: https://ollama.ai"
    fi
}

# ============================================================================
# Step 8: Port Management
# ============================================================================

manage_ports() {
    log_section "Managing Ports"
    
    log_info "Checking ports..."
    log_info "  Ollama:     $OLLAMA_PORT"
    log_info "  FastAPI:    $FASTAPI_PORT"
    log_info "  Streamlit:  $STREAMLIT_PORT"
    
    # Kill any existing processes on these ports
    for port in $OLLAMA_PORT $FASTAPI_PORT $STREAMLIT_PORT; do
        kill_port "$port"
    done
    
    log_success "Port management complete"
}

# ============================================================================
# Step 9: Validate Installation
# ============================================================================

validate_installation() {
    log_section "Validating Installation"
    
    # Test Python imports
    log_info "Testing Python imports..."
    python3 << 'PYEOF'
try:
    import torch
    print(f"  ✓ PyTorch {torch.__version__}")
except ImportError as e:
    print(f"  ✗ PyTorch: {e}")
    exit(1)

try:
    import streamlit
    print(f"  ✓ Streamlit {streamlit.__version__}")
except ImportError as e:
    print(f"  ✗ Streamlit: {e}")

try:
    import fastapi
    print(f"  ✓ FastAPI {fastapi.__version__}")
except ImportError as e:
    print(f"  ✗ FastAPI: {e}")

try:
    from faster_whisper import WhisperModel
    print(f"  ✓ Faster-Whisper available")
except ImportError as e:
    print(f"  ✗ Faster-Whisper: {e}")

try:
    from app.platform import detect_compute_device
    config = detect_compute_device()
    print(f"  ✓ Platform detection: {config['platform']} ({config['device']})")
except ImportError as e:
    print(f"  ✗ Platform detection: {e}")
    exit(1)
PYEOF
    
    log_success "Validation complete"
}

# ============================================================================
# Step 10: Create Logs Directory
# ============================================================================

setup_directories() {
    log_section "Setting Up Directories"
    
    mkdir -p "$PROJECT_ROOT/logs"
    mkdir -p "$PROJECT_ROOT/chroma_local_db"
    mkdir -p "$PROJECT_ROOT/models"
    
    log_success "Directories created/verified"
}

# ============================================================================
# Step 11: Display Summary
# ============================================================================

show_summary() {
    log_section "Setup Summary"
    
    cat << EOF
✓ Setup completed successfully!

Project Location:
    $PROJECT_ROOT

Environment:
    Python:     $(python3 --version 2>&1)
    PyTorch:    $(python3 -c "import torch; print(torch.__version__)" 2>/dev/null || echo "installed")
    Docker:     $(docker --version)

Configuration:
    .env file:          $ENV_FILE
    Virtual env:        $VENV_PATH
    
Services:
    Ollama:             http://localhost:$OLLAMA_PORT
    FastAPI:            http://localhost:$FASTAPI_PORT
    Streamlit Web UI:   http://localhost:$STREAMLIT_PORT

Next Steps:
    1. Activate virtual environment:
       source $VENV_PATH/bin/activate
    
    2. Start Ollama (if not already running):
       ollama serve
    
    3. In another terminal, start FastAPI:
       cd $PROJECT_ROOT
       python -m uvicorn app.main:app --host 0.0.0.0 --port $FASTAPI_PORT --reload
    
    4. In another terminal, start Streamlit Web UI:
       cd $PROJECT_ROOT
       streamlit run app.py --server.port $STREAMLIT_PORT

    5. Visit in your browser:
       - Web UI: http://localhost:$STREAMLIT_PORT
       - API Docs: http://localhost:$FASTAPI_PORT/docs

Environment:
    OLLAMA_URL=$OLLAMA_URL
    OLLAMA_MODEL=$OLLAMA_MODEL
    FASTAPI_PORT=$FASTAPI_PORT
    STREAMLIT_PORT=$STREAMLIT_PORT

For more information, see:
    - QUICKSTART.md
    - README.md
    - doc/application_flow.md

Happy coding! 🚀

EOF
}

# ============================================================================
# Step 12: Main Execution
# ============================================================================

main() {
    parse_arguments "$@"
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║  University Admissions Voice AI Assistant - Setup Script       ║"
    echo "║  Making your development environment ready in one click!       ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    
    # Execute setup steps
    check_docker
    check_system
    setup_env
    setup_python_venv
    install_dependencies
    verify_ollama
    manage_ports
    setup_directories
    validate_installation
    show_summary
    
    echo ""
    log_success "All setup steps completed successfully!"
    echo ""
    log_info "Keeping virtual environment activated in this terminal."
    log_info "You can now proceed to start the services."
    echo ""
}

# Run main function
main "$@"
