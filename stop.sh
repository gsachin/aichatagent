#!/bin/bash

################################################################################
# Stop Services - University Admissions Voice AI Assistant
################################################################################
# This script stops all running services
#
# Usage:
#   bash stop.sh              # Stop all services
#   bash stop.sh --help       # Show help
################################################################################

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            cat << EOF
Usage: bash stop.sh [OPTIONS]

Options:
    --help              Show this help message

Examples:
    bash stop.sh        # Stop all services

EOF
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

log_section "University Admissions Voice AI Assistant - Service Shutdown"

# Function to kill process on port
kill_port_process() {
    local port=$1
    local service=$2
    
    local pids=$(lsof -ti:$port 2>/dev/null || echo "")
    if [ -n "$pids" ]; then
        log_info "Stopping $service on port $port..."
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
        log_success "$service stopped"
        return 0
    else
        log_warn "$service not running on port $port"
        return 1
    fi
}

# Function to find and kill by process name
kill_by_name() {
    local process_name=$1
    local pids=$(pgrep -f "$process_name" 2>/dev/null || echo "")
    
    if [ -n "$pids" ]; then
        log_info "Stopping $process_name..."
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
        log_success "$process_name stopped"
        return 0
    else
        log_warn "$process_name not running"
        return 1
    fi
}

log_section "Stopping Services"

# Stop FastAPI (port 8000-8009)
found_fastapi=false
for port in {8000..8009}; do
    pids=$(lsof -ti:$port 2>/dev/null || echo "")
    if [ -n "$pids" ] && echo "$pids" | xargs ps -p 2>/dev/null | grep -q uvicorn; then
        log_info "Stopping FastAPI on port $port..."
        echo "$pids" | xargs kill -9 2>/dev/null || true
        found_fastapi=true
        break
    fi
done

if [ "$found_fastapi" = true ]; then
    log_success "FastAPI stopped"
else
    log_warn "FastAPI not running"
fi

# Stop Streamlit (port 8501)
kill_port_process 8501 "Streamlit" || true

# Stop any remaining nohup processes
log_info "Cleaning up background processes..."
kill_by_name "uvicorn" || true
kill_by_name "streamlit" || true

# Kill Ollama if requested (usually manual startup)
log_info ""
log_warn "Note: Ollama service was not started by this script"
log_info "If you want to stop Ollama manually, run:"
log_info "  pkill ollama"

log_section "Cleanup Complete"

cat << EOF

Services Status:

  FastAPI:   $(lsof -ti:8000 >/dev/null 2>&1 && echo "❌ Still running" || echo "✓ Stopped")
  Streamlit: $(lsof -ti:8501 >/dev/null 2>&1 && echo "❌ Still running" || echo "✓ Stopped")

Logs available at:
  $PROJECT_ROOT/logs/

To restart services:
  bash start.sh

To check logs:
  tail -f $PROJECT_ROOT/logs/fastapi.log
  tail -f $PROJECT_ROOT/logs/streamlit.log

EOF

log_success "All services stopped successfully!"
