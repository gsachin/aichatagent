#!/usr/bin/env bash
# =============================================================================
# University Admissions Voice Assistant - One-shot launcher (macOS / Linux)
# -----------------------------------------------------------------------------
# Bash port of start_services.ps1 with full parity:
#   kills stale services, frees ports, checks the compute platform, pre-flights
#   Docker/PostgreSQL, builds the pre-rendered call assets, starts FastAPI,
#   checks the configured OpenAI endpoint, starts a Cloudflare tunnel, writes
#   the tunnel hostname everywhere it is needed, updates Twilio webhooks, and
#   starts the Streamlit apps.
#
# The launcher starts only services this application still uses. RAG/Chroma,
# Ollama, enterprise-rag-core and Redis are deliberately not started; the
# application uses OpenAI plus the DSPMU local site graph.
#
# Usage:
#   bash start_services.sh              # everything, including Streamlit UIs
#   bash start_services.sh --skip-streamlit   # FastAPI + tunnel only
#   bash start_services.sh --skip-twilio
#   bash start_services.sh --named-tunnel [--tunnel-name my-tunnel]
#   bash start_services.sh --local-only   # FastAPI + Streamlit, no tunnel/Twilio
#
# NOTE: Streamlit starts BY DEFAULT here (unlike the PS1's -WithStreamlit
# opt-in) -- plain "bash start_services.sh" must leave localhost:8501/8502
# working. --with-streamlit is accepted for PS1 compatibility.
#
# Bash 3.2 compatible (macOS default shell): no associative arrays, no
# ${var,,}, no [[ =~ ]] regex — grep/awk are used instead.
# =============================================================================

WITH_STREAMLIT=true
SKIP_TWILIO=false
NAMED_TUNNEL=false
LOCAL_ONLY=false
TUNNEL_NAME="admissions-tunnel"

while [ $# -gt 0 ]; do
    case "$1" in
        --with-streamlit) WITH_STREAMLIT=true ;;   # PS1-compat (already default)
        --skip-streamlit) WITH_STREAMLIT=false ;;
        --skip-twilio)    SKIP_TWILIO=true ;;
        --local-only)     LOCAL_ONLY=true; WITH_STREAMLIT=true; SKIP_TWILIO=true ;;
        --named-tunnel)   NAMED_TUNNEL=true ;;
        --tunnel-name)
            if [ $# -lt 2 ]; then
                echo "ERROR: --tunnel-name requires a value" >&2
                exit 1
            fi
            TUNNEL_NAME="$2"
            shift
            ;;
        -h|--help)
            echo "Usage: bash start_services.sh [--skip-streamlit] [--skip-twilio] [--local-only] [--named-tunnel [--tunnel-name NAME]]"
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            exit 1
            ;;
    esac
    shift
done

# ---- Config ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
TUNNEL_FILE="$PROJECT_ROOT/.whatsapp_tunnel"
LOG_DIR="$PROJECT_ROOT/logs"
FASTAPI_PORT=8000
STREAMLIT_MAIN_PORT=8501
STREAMLIT_DASH_PORT=8502

mkdir -p "$LOG_DIR"

# ---- Load .env (safe for KEY=VALUE lines; comments/blank lines skipped) ----
if [ -f "$PROJECT_ROOT/.env" ]; then
    while IFS='=' read -r _key _value; do
        case "$_key" in ''|\#*) continue ;; esac
        # Match python-dotenv's default (override=False): an explicit
        # command-line environment value wins over the file. This is what
        # lets a local UAT run point the OpenAI-compatible client at a local
        # model without editing the deployed .env.
        if ! printenv "$_key" >/dev/null 2>&1; then
            [ -n "$_value" ] && export "$_key"="$_value"
        fi
    done < "$PROJECT_ROOT/.env"
fi

# ---- Python selection (bootstrap venv -> legacy venv -> system) -----------
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

# ---- LLM provider resolution (matches app/llm_backend.py) -----------------
LLM_PROVIDER="${LLM_PROVIDER:-auto}"
if [ "$LLM_PROVIDER" = "auto" ] || [ -z "$LLM_PROVIDER" ]; then
    LLM_PROVIDER="openai"
fi
if [ "$LLM_PROVIDER" != "openai" ]; then
    printf "  ERROR: LLM_PROVIDER='%s' is no longer supported by app/llm_backend.py.\n" "$LLM_PROVIDER" >&2
    printf "  Use LLM_PROVIDER=openai (or auto), or override the base URL for a local OpenAI-compatible server.\n" >&2
    exit 1
fi

# Local-only is a validation mode: keep every process local and never touch
# the public tunnel or Twilio. It also skips the machine-profile rewrite so a
# test run cannot change the deployed .env.
if [ "$LOCAL_ONLY" = "true" ]; then
    MACHINE_PROFILE_CHECK=0
fi

# ---- Machine profile (.env sizing) ------------------------------------------
# Re-size .env if the machine changed since last deploy. Idempotent no-op
# (~0.3s) when the profile matches. Disable with MACHINE_PROFILE_CHECK=0.
if [ "${MACHINE_PROFILE_CHECK:-1}" != "0" ] \
   && [ -f "$PROJECT_ROOT/scripts/predeploy.py" ] \
   && [ -f "$PROJECT_ROOT/.env" ]; then
    if ! "$PYTHON" "$PROJECT_ROOT/scripts/predeploy.py" --auto --quiet 2>/dev/null; then
        printf "  WARN: machine profile out of date — run: python scripts/predeploy.py\n"
    fi
fi

# ---- Colours --------------------------------------------------------------
ESC="$(printf '\033')"
GREEN="$ESC[32m"; YELLOW="$ESC[33m"; RED="$ESC[31m"
CYAN="$ESC[36m"; RESET="$ESC[0m"; BOLD="$ESC[1m"

write_step() { printf "\n%s%s%s--- %s ---%s\n" "$CYAN" "$BOLD" "$*" "$RESET"; }
write_ok()   { printf "%s  OK: %s%s\n" "$GREEN" "$*" "$RESET"; }
write_warn() { printf "%s  WARN: %s%s\n" "$YELLOW" "$*" "$RESET"; }
write_err()  { printf "%s  ERROR: %s%s\n" "$RED" "$*" "$RESET"; }

port_pids() { lsof -ti "tcp:$1" -s tcp:LISTEN 2>/dev/null; }
http_code() { curl -s -o /dev/null -w "%{http_code}" "$1" 2>/dev/null; }

# ==== Step 1: Kill stale processes ========================================
write_step "Step 1: Killing stale processes"

CLOUDFLARED_KILLED=0
if pgrep -f cloudflared >/dev/null 2>&1; then
    pkill -f cloudflared 2>/dev/null
    sleep 1
    pkill -9 -f cloudflared 2>/dev/null
    CLOUDFLARED_KILLED=1
fi
if pgrep -f cloudflared >/dev/null 2>&1; then
    write_warn "cloudflared still alive -- reboot may be needed"
else
    if [ "$CLOUDFLARED_KILLED" = "1" ]; then
        write_ok "Killed stale cloudflared processes"
    else
        write_ok "No stale cloudflared processes"
    fi
fi

for _port in "$FASTAPI_PORT" "$STREAMLIT_MAIN_PORT" "$STREAMLIT_DASH_PORT"; do
    for _pid in $(port_pids "$_port"); do
        kill -9 "$_pid" 2>/dev/null
        write_ok "Killed PID $_pid on port $_port"
    done
done
write_ok "Process cleanup complete"

# Clear stale TUNNEL_HOST from previous runs
unset TUNNEL_HOST 2>/dev/null || true
write_ok "Cleared stale TUNNEL_HOST env var (server will read .whatsapp_tunnel file)"

# ==== Step 2: Verify ports are free ========================================
write_step "Step 2: Verifying ports are free"

for _port in "$FASTAPI_PORT" "$STREAMLIT_MAIN_PORT" "$STREAMLIT_DASH_PORT"; do
    _attempt=0
    while [ -n "$(port_pids "$_port")" ] && [ "$_attempt" -lt 10 ]; do
        _attempt=$((_attempt + 1))
        write_warn "Port $_port still in use - waiting ($_attempt/10)..."
        sleep 1
    done
    if [ -n "$(port_pids "$_port")" ]; then
        write_warn "Port $_port is STILL busy after 10s - may be TIME_WAIT"
        for _pid in $(port_pids "$_port"); do
            kill -9 "$_pid" 2>/dev/null
        done
        sleep 2
    else
        write_ok "Port $_port is free"
    fi
done

# ==== Step 3: Compute platform check =======================================
write_step "Step 3: Compute platform check"

if command -v nvidia-smt >/dev/null 2>&1; then :; fi  # no-op guard
if command -v nvidia-smi >/dev/null 2>&1; then
    _gpu_info="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ' | ')"
    if [ $? -eq 0 ] && [ -n "$_gpu_info" ]; then
        write_ok "GPU detected: $_gpu_info"
    else
        write_warn "nvidia-smi found but query failed -- GPU may be unavailable"
    fi
elif [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    _mem_gb="$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}')"
    _chip="$(system_profiler SPDisplaysDataType 2>/dev/null | grep -i "Chipset Model" | head -1 | sed 's/.*: //')"
    write_ok "Apple Silicon detected: ${_chip:-Metal GPU} (${_mem_gb} GB unified memory)"
    write_ok "Generation backend: OpenAI-compatible remote endpoint (no local model)"
else
    write_warn "No NVIDIA GPU detected -- STT and TTS will fall back to CPU (slow)"
fi

# ==== Step 4: Docker / PostgreSQL check ====================================
write_step "Step 4: Docker / PostgreSQL check"

DB_READY=false

if [ "$LOCAL_ONLY" = "true" ]; then
    write_warn "Local-only mode: skipping Docker/PostgreSQL pre-flight"
else
if docker info >/dev/null 2>&1; then
    write_ok "Docker daemon is running"
else
    write_warn "Docker daemon is NOT running -- attempting to start..."
    if [ "$(uname -s)" = "Darwin" ]; then
        open -a Docker 2>/dev/null
    fi
    write_ok "Docker launching (this may take 30-60s)..."
    _wait=0
    while [ "$_wait" -lt 45 ]; do
        sleep 2
        _wait=$((_wait + 1))
        if docker info >/dev/null 2>&1; then
            write_ok "Docker ready (after ~$((_wait * 2))s)"
            break
        fi
    done
fi

if docker info >/dev/null 2>&1; then
    # Prefer the existing named container. docker compose refuses to recreate
    # it when the name already exists, even if the container is merely stopped.
    if docker ps -a --format "{{.Names}}" 2>/dev/null | grep -qx "elearning-postgres"; then
        if ! docker ps --format "{{.Names}}" 2>/dev/null | grep -qx "elearning-postgres"; then
            write_warn "Starting existing elearning-postgres container..."
            docker start elearning-postgres >/dev/null 2>&1 || true
            sleep 3
        fi
    else
        write_warn "PostgreSQL container not found -- attempting docker compose up..."
        docker compose -f "$PROJECT_ROOT/docker-compose.yml" up -d postgres >/dev/null 2>&1 || true
        sleep 5
    fi

    # Check the actual DATABASE_URL, not pg_isready. pg_isready reports the
    # server accepts connections even when the role or database in the URL
    # does not exist -- the exact mismatch that made the old pre-flight pass
    # while the application logged "PostgreSQL not available".
    if [ -n "${DATABASE_URL:-}" ] && "$PYTHON" - <<'PYDB' >/dev/null 2>&1
import os
import psycopg2
psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5).close()
PYDB
    then
        write_ok "PostgreSQL: DATABASE_URL connects"
        DB_READY=true
    else
        write_warn "PostgreSQL: DATABASE_URL does not connect"
    fi
fi

if [ "$DB_READY" = "false" ]; then
    write_warn "PostgreSQL is NOT available -- server will start in database-less mode"
    write_warn "Leads, calls, and follow-ups will NOT be persisted"
    write_warn "Fix: ensure Docker is running with the elearning-postgres container"
else
    write_ok "Database pre-flight: PASSED"
fi
fi

# ==== Step 4b: Pre-rendered call assets ====================================
# The greeting WAVs are generated, not committed (`app/static/audio/` is
# gitignored). A fresh clone or deploy that skips this step silently falls back
# to live synthesis on every call -- the greeting latency win is lost with no
# error anywhere. `--ensure` is a no-op when every asset is present and
# current, so a restart pays nothing; it renders only what is missing or stale.
write_step "Step 4b: Pre-rendered call assets"
if [ "$LOCAL_ONLY" = "true" ]; then
    write_warn "Local-only mode: skipping call-asset build"
elif "$PYTHON" "$PROJECT_ROOT/scripts/build_call_assets.py" --ensure; then
    write_ok "call assets ready (greetings asset-first on the next call)"
else
    write_warn "call asset build failed - missing greetings will be synthesised live"
fi

# ==== Step 5: Start FastAPI backend ========================================
write_step "Step 5: Starting FastAPI backend (port $FASTAPI_PORT)"

SERVER_LOG="$LOG_DIR/fastapi.log"
echo "  Python: $PYTHON"
nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$FASTAPI_PORT" \
    >> "$SERVER_LOG" 2>&1 &
FASTAPI_PID=$!
write_ok "FastAPI starting (PID $FASTAPI_PID) - log: $SERVER_LOG"

_attempt=0
SERVER_READY=false
# First boot can spend ~1 minute downloading the Whisper model; 60s was too
# short and made a healthy server look like a failed start.
while [ "$SERVER_READY" = "false" ] && [ "$_attempt" -lt 90 ]; do
    sleep 2
    _attempt=$((_attempt + 1))
    if [ "$(http_code "http://127.0.0.1:$FASTAPI_PORT/")" = "200" ]; then
        write_ok "FastAPI server responding (took ~$((_attempt * 2))s)"
        SERVER_READY=true
    else
        write_warn "Waiting for FastAPI server... ($_attempt/90)"
    fi
done

if [ "$SERVER_READY" = "false" ]; then
    write_err "FastAPI server did NOT come up - check $SERVER_LOG"
    exit 1
fi

# ==== Step 6: LLM pre-warming ==============================================
write_step "Step 6: LLM readiness"
if [ -n "${LLM_BASE_URL:-}" ]; then
    write_ok "Generation endpoint: $LLM_BASE_URL (model: ${LLM_MODEL:-default})"
else
    write_ok "Remote LLM: OpenAI - no local model to load or warm"
fi
if [ -z "${LLM_API_KEY:-}" ]; then
    write_warn "LLM_API_KEY is empty - the app will use the fixed-response path"
fi

# ==== Step 7: Start Cloudflare tunnel ======================================
if [ "$LOCAL_ONLY" = "true" ]; then
    write_step "Step 7: Local-only mode"
    TUNNEL_HOST=""
    export DASHBOARD_API_URL="http://127.0.0.1:$FASTAPI_PORT"
    write_ok "Skipping Cloudflare tunnel and Twilio update"
    write_ok "DASHBOARD_API_URL=$DASHBOARD_API_URL"
else
write_step "Step 7: Starting Cloudflare tunnel"

if ! command -v cloudflared >/dev/null 2>&1; then
    write_err "cloudflared not found! Install with:"
    write_err "  macOS: brew install cloudflared"
    write_err "  Linux: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/"
    exit 1
fi

TUNNEL_LOG="$LOG_DIR/cloudflared_8000.log"
# Truncate before starting -- the URL grep must only ever see THIS run's
# hostname (PS1 parity: Start-Process redirect truncates on Windows).
: > "$TUNNEL_LOG"

if [ "$NAMED_TUNNEL" = "true" ]; then
    write_ok "Named tunnel mode: $TUNNEL_NAME"
    if ! cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
        write_ok "Creating named tunnel: $TUNNEL_NAME ..."
        if ! cloudflared tunnel create "$TUNNEL_NAME" >> "$TUNNEL_LOG" 2>&1; then
            write_err "Failed to create tunnel '$TUNNEL_NAME' -- see $TUNNEL_LOG"
            write_warn "Falling back to ephemeral quick tunnel..."
            NAMED_TUNNEL=false
        else
            write_ok "Tunnel '$TUNNEL_NAME' created. Credentials stored in ~/.cloudflared/"
            write_warn "IMPORTANT: Route DNS for '$TUNNEL_NAME' in the Cloudflare Zero Trust dashboard"
        fi
    fi
    if [ "$NAMED_TUNNEL" = "true" ]; then
        nohup cloudflared tunnel run --url "http://localhost:$FASTAPI_PORT" "$TUNNEL_NAME" \
            >> "$TUNNEL_LOG" 2>&1 &
        write_ok "Named tunnel '$TUNNEL_NAME' starting - log: $TUNNEL_LOG"
    fi
fi

if [ "$NAMED_TUNNEL" != "true" ]; then
    write_warn "Ephemeral tunnel mode (URL will change on next restart)"
    nohup cloudflared tunnel --url "http://localhost:$FASTAPI_PORT" \
        >> "$TUNNEL_LOG" 2>&1 &
    write_ok "Ephemeral tunnel starting - log: $TUNNEL_LOG"
fi

# ==== Step 8: Parse tunnel URL and write hostname everywhere ===============
write_step "Step 8: Writing tunnel hostname everywhere"

TUNNEL_HOST=""
_attempt=0
while [ -z "$TUNNEL_HOST" ] && [ "$_attempt" -lt 15 ]; do
    sleep 3
    _attempt=$((_attempt + 1))
    if [ -f "$TUNNEL_LOG" ]; then
        TUNNEL_HOST="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 | sed 's|https://||')"
    fi
    if [ -z "$TUNNEL_HOST" ]; then
        write_warn "Waiting for tunnel URL... ($_attempt/15)"
    fi
done

if [ -z "$TUNNEL_HOST" ]; then
    write_err "Failed to capture tunnel URL - check $TUNNEL_LOG"
    exit 1
fi

write_ok "Tunnel URL captured: $TUNNEL_HOST"

printf '%s' "$TUNNEL_HOST" > "$TUNNEL_FILE"
write_ok ".whatsapp_tunnel file updated -> $TUNNEL_HOST"

export TUNNEL_HOST="$TUNNEL_HOST"
write_ok "Env TUNNEL_HOST = $TUNNEL_HOST"

export DASHBOARD_API_URL="https://$TUNNEL_HOST"
write_ok "Env DASHBOARD_API_URL = https://$TUNNEL_HOST"

# ==== Step 9: Update Twilio webhooks =======================================
write_step "Step 9: Updating Twilio webhooks"

if [ "$SKIP_TWILIO" = "true" ]; then
    write_warn "Skipping Twilio (--skip-twilio flag set)"
else
    TWILIO_SCRIPT="$PROJECT_ROOT/scripts/update_twilio_webhook.py"
    if [ -f "$TWILIO_SCRIPT" ]; then
        _twilio_out="$("$PYTHON" "$TWILIO_SCRIPT" "$TUNNEL_HOST" 2>&1)"
        if [ $? -eq 0 ]; then
            echo "$_twilio_out" | while IFS= read -r line; do write_ok "$line"; done
        else
            write_warn "Twilio update had issues (check .env for credentials)"
            echo "$_twilio_out" | while IFS= read -r line; do write_warn "$line"; done
        fi
    else
        write_warn "Twilio update script not found: $TWILIO_SCRIPT"
        write_warn "Manual fix: set Voice URL to https://$TUNNEL_HOST/twilio/voice (GET)"
    fi
fi

# ==== Step 10: Verify URLs =================================================
write_step "Step 10: Verifying URLs"

# Quick-tunnel DNS records take a moment to propagate, and macOS may
# negative-cache an early lookup — give the record time before probing.
TUNNEL_OK=false
sleep 5
_tunnel_attempt=0
while [ "$TUNNEL_OK" = "false" ] && [ "$_tunnel_attempt" -lt 10 ]; do
    if [ "$_tunnel_attempt" -gt 0 ]; then sleep 6; fi
    _tunnel_attempt=$((_tunnel_attempt + 1))
    _code="$(http_code "https://$TUNNEL_HOST/")"
    if [ "$_code" = "200" ]; then
        write_ok "Tunnel reachable: https://$TUNNEL_HOST/ (HTTP 200)"
        TUNNEL_OK=true
    else
        write_warn "Tunnel not ready yet (HTTP $_code) -- retry $_tunnel_attempt/10..."
    fi
done
if [ "$TUNNEL_OK" = "false" ]; then
    write_warn "Tunnel not reachable after 10 attempts (HTTP DNS/edge propagation"
    write_warn "can take a minute). Verify manually: curl https://$TUNNEL_HOST/"
fi

# Verify Twilio webhook matches (when credentials are present)
if [ "$SKIP_TWILIO" = "false" ] && [ -n "${TWILIO_ACCOUNT_SID:-}" ] && [ -n "${TWILIO_AUTH_TOKEN:-}" ]; then
    _twilio_verify="$("$PYTHON" -c "
from dotenv import load_dotenv; load_dotenv()
import os
from twilio.rest import Client
c = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])
for n in c.incoming_phone_numbers.list():
    print(n.phone_number + ' -> ' + (n.voice_url or ''))
" 2>&1)"
    if echo "$_twilio_verify" | grep -q "$TUNNEL_HOST"; then
        write_ok "Twilio webhook verified: matches $TUNNEL_HOST"
    else
        write_warn "Twilio webhook may be stale! Current: $_twilio_verify"
        write_warn "Expected: https://$TUNNEL_HOST/twilio/voice"
    fi
fi

fi

# ==== Step 11: Optional Streamlit apps =====================================
if [ "$WITH_STREAMLIT" = "true" ]; then
    write_step "Step 11: Starting Streamlit apps"

    nohup "$PYTHON" -m streamlit run dashboard.py \
        --server.port "$STREAMLIT_DASH_PORT" --server.headless true \
        >> "$LOG_DIR/dashboard.log" 2>&1 &
    write_ok "Dashboard starting -> http://localhost:$STREAMLIT_DASH_PORT"

    nohup "$PYTHON" -m streamlit run app.py \
        --server.port "$STREAMLIT_MAIN_PORT" --server.headless true \
        >> "$LOG_DIR/streamlit.log" 2>&1 &
    write_ok "Main app starting -> http://localhost:$STREAMLIT_MAIN_PORT"

    # In local-only validation the tunnel normally provides the first external
    # probe; wait on the local ports so the launcher cannot report success
    # before Streamlit is actually accepting connections.
    if [ "$LOCAL_ONLY" = "true" ]; then
        for _s_port in "$STREAMLIT_MAIN_PORT" "$STREAMLIT_DASH_PORT"; do
            _s_ready=false
            _s_attempt=0
            while [ "$_s_ready" = "false" ] && [ "$_s_attempt" -lt 20 ]; do
                sleep 2
                _s_attempt=$((_s_attempt + 1))
                if [ "$(http_code "http://127.0.0.1:$_s_port/")" = "200" ]; then
                    write_ok "Streamlit :$_s_port responding (took ~$((_s_attempt * 2))s)"
                    _s_ready=true
                fi
            done
            if [ "$_s_ready" = "false" ]; then
                write_warn "Streamlit :$_s_port did not respond within 40s -- check $LOG_DIR"
            fi
        done
    fi
fi

# ==== Step 12: Streamlit chat tunnel (8501) ================================
# The chat UI needs its own public URL (WebSocket UI can't share the
# FastAPI tunnel). Reuse the conventions of tunnel_streamlit.sh.
CHAT_TUNNEL_HOST=""
if [ "$LOCAL_ONLY" != "true" ] && [ "$WITH_STREAMLIT" = "true" ]; then
    write_step "Step 12: Starting Streamlit chat tunnel"
    CHAT_TUNNEL_CACHE="$PROJECT_ROOT/.tunnel_8501"

    # Reuse a live cached URL (e.g. started by tunnel_streamlit.sh).
    if [ -f "$CHAT_TUNNEL_CACHE" ]; then
        _cached_chat="$(tr -d '[:space:]' < "$CHAT_TUNNEL_CACHE")"
        if [ -n "$_cached_chat" ] && [ "$(http_code "https://$_cached_chat/")" = "200" ]; then
            CHAT_TUNNEL_HOST="$_cached_chat"
            write_ok "Streamlit chat tunnel already alive: $CHAT_TUNNEL_HOST"
        fi
    fi

    if [ -z "$CHAT_TUNNEL_HOST" ]; then
        # Never double-start: a live cloudflared for 8501 may exist without
        # a usable cache URL (e.g. logs were cleaned).
        if pgrep -f "cloudflared.*--url.*localhost:$STREAMLIT_MAIN_PORT" >/dev/null 2>&1; then
            write_warn "Streamlit chat tunnel process already running but its URL is unknown"
            write_warn "Kill it and re-run to recreate: pkill -f cloudflared"
        else
            CHAT_TUNNEL_LOG="$LOG_DIR/cloudflared_8501.log"
            : > "$CHAT_TUNNEL_LOG"  # clear stale URLs so we never misread an old hostname

            nohup cloudflared tunnel --url "http://localhost:$STREAMLIT_MAIN_PORT" \
                --metrics "localhost:0" >> "$CHAT_TUNNEL_LOG" 2>&1 &
            write_ok "Streamlit chat tunnel starting - log: $CHAT_TUNNEL_LOG"

            _chat_attempt=0
            while [ -z "$CHAT_TUNNEL_HOST" ] && [ "$_chat_attempt" -lt 15 ]; do
                sleep 3
                _chat_attempt=$((_chat_attempt + 1))
                CHAT_TUNNEL_HOST="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$CHAT_TUNNEL_LOG" 2>/dev/null | head -1 | sed 's|https://||')"
                if [ -z "$CHAT_TUNNEL_HOST" ]; then
                    write_warn "Waiting for Streamlit chat tunnel URL... ($_chat_attempt/15)"
                fi
            done

            if [ -n "$CHAT_TUNNEL_HOST" ]; then
                printf '%s' "$CHAT_TUNNEL_HOST" > "$CHAT_TUNNEL_CACHE"
                # Fresh quick-tunnel hostnames can take a minute to resolve.
                _chat_verify=false
                _chat_v=0
                while [ "$_chat_verify" = "false" ] && [ "$_chat_v" -lt 6 ]; do
                    if [ "$_chat_v" -gt 0 ]; then sleep 3; fi
                    _chat_v=$((_chat_v + 1))
                    [ "$(http_code "https://$CHAT_TUNNEL_HOST/")" = "200" ] && _chat_verify=true
                done
                if [ "$_chat_verify" = "true" ]; then
                    write_ok "Streamlit chat tunnel reachable: https://$CHAT_TUNNEL_HOST/"
                else
                    write_warn "Streamlit chat tunnel started but not yet reachable (DNS warm-up): $CHAT_TUNNEL_HOST"
                fi
            else
                write_warn "Streamlit chat tunnel did not start -- run ./tunnel_streamlit.sh to retry"
            fi
        fi
    fi
fi

# ==== Step 12b: Streamlit dashboard tunnel (8502) ==========================
# The dashboard is a separate Streamlit process and needs its own hostname.
DASH_TUNNEL_HOST=""
if [ "$LOCAL_ONLY" != "true" ] && [ "$WITH_STREAMLIT" = "true" ]; then
    write_step "Step 12b: Starting Streamlit dashboard tunnel"
    DASH_TUNNEL_CACHE="$PROJECT_ROOT/.tunnel_8502"

    if [ -f "$DASH_TUNNEL_CACHE" ]; then
        _cached_dash="$(tr -d '[:space:]' < "$DASH_TUNNEL_CACHE")"
        if [ -n "$_cached_dash" ] && [ "$(http_code "https://$_cached_dash/")" = "200" ]; then
            DASH_TUNNEL_HOST="$_cached_dash"
            write_ok "Dashboard tunnel already alive: $DASH_TUNNEL_HOST"
        fi
    fi

    if [ -z "$DASH_TUNNEL_HOST" ]; then
        if pgrep -f "cloudflared.*--url.*localhost:$STREAMLIT_DASH_PORT" >/dev/null 2>&1; then
            write_warn "Dashboard tunnel process already running but its URL is unknown"
            write_warn "Kill it and re-run to recreate: pkill -f cloudflared"
        else
            DASH_TUNNEL_LOG="$LOG_DIR/cloudflared_8502.log"
            : > "$DASH_TUNNEL_LOG"
            nohup cloudflared tunnel --url "http://localhost:$STREAMLIT_DASH_PORT" \
                --metrics "localhost:0" >> "$DASH_TUNNEL_LOG" 2>&1 &
            write_ok "Dashboard tunnel starting - log: $DASH_TUNNEL_LOG"

            _dash_attempt=0
            while [ -z "$DASH_TUNNEL_HOST" ] && [ "$_dash_attempt" -lt 15 ]; do
                sleep 3
                _dash_attempt=$((_dash_attempt + 1))
                DASH_TUNNEL_HOST="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$DASH_TUNNEL_LOG" 2>/dev/null | head -1 | sed 's|https://||')"
                if [ -z "$DASH_TUNNEL_HOST" ]; then
                    write_warn "Waiting for dashboard tunnel URL... ($_dash_attempt/15)"
                fi
            done

            if [ -n "$DASH_TUNNEL_HOST" ]; then
                printf '%s' "$DASH_TUNNEL_HOST" > "$DASH_TUNNEL_CACHE"
                _dash_verify=false
                _dash_v=0
                while [ "$_dash_verify" = "false" ] && [ "$_dash_v" -lt 6 ]; do
                    if [ "$_dash_v" -gt 0 ]; then sleep 3; fi
                    _dash_v=$((_dash_v + 1))
                    [ "$(http_code "https://$DASH_TUNNEL_HOST/")" = "200" ] && _dash_verify=true
                done
                if [ "$_dash_verify" = "true" ]; then
                    write_ok "Dashboard tunnel reachable: https://$DASH_TUNNEL_HOST/"
                else
                    write_warn "Dashboard tunnel started but not yet reachable (DNS warm-up): $DASH_TUNNEL_HOST"
                fi
            else
                write_warn "Dashboard tunnel did not start -- run ./tunnel_streamlit.sh to retry"
            fi
        fi
    fi
fi

# ==== Step 13: Summary =====================================================
printf '\n%s%sALL SERVICES STARTED SUCCESSFULLY%s\n\n' "$GREEN" "$BOLD" "$RESET"
if [ "$LOCAL_ONLY" = "true" ]; then
    printf '%sLocal-only validation mode%s\n' "$BOLD" "$RESET"
    printf '   No public tunnel and no Twilio changes.\n'
else
    printf '%sPublic Tunnel:%s\n' "$BOLD" "$RESET"
    printf '%s   https://%s%s\n' "$CYAN" "$TUNNEL_HOST" "$RESET"
    printf '\n%sInbound Calls:%s\n' "$BOLD" "$RESET"
    # .env stores TWILIO_PHONE_NUMBER in E.164 (already has a leading +),
    # so strip any existing + before re-adding one — avoids "Dial ++1..."
    _TWILIO_DISPLAY="${TWILIO_PHONE_NUMBER#+}"
    printf '   Dial %s+%s%s for AI admissions assistant\n' "$YELLOW" "${_TWILIO_DISPLAY:-19788198953}" "$RESET"
    printf '   Webhook: %shttps://%s/twilio/voice%s\n' "$CYAN" "$TUNNEL_HOST" "$RESET"
    printf '\n%sWhatsApp:%s\n' "$BOLD" "$RESET"
    printf '   Webhook: %shttps://%s/twilio/whatsapp%s\n' "$CYAN" "$TUNNEL_HOST" "$RESET"
    printf '   (Configure in the Twilio Console -> WhatsApp Sandbox)\n'
    if [ -n "$CHAT_TUNNEL_HOST" ]; then
        printf '\n%sStreamlit Chat:%s\n' "$BOLD" "$RESET"
        printf '   Public URL: %shttps://%s%s\n' "$CYAN" "$CHAT_TUNNEL_HOST" "$RESET"
    fi
    if [ -n "$DASH_TUNNEL_HOST" ]; then
        printf '%sDashboard:%s\n' "$BOLD" "$RESET"
        printf '   Public URL: %shttps://%s%s\n' "$CYAN" "$DASH_TUNNEL_HOST" "$RESET"
    fi
fi
printf '\n%sLocal Services:%s\n' "$BOLD" "$RESET"
printf '   FastAPI backend:  %shttp://localhost:%s%s\n' "$CYAN" "$FASTAPI_PORT" "$RESET"
printf '   LLM backend:      %s%s%s (%s)\n' "$CYAN" "$LLM_PROVIDER" "$RESET" "${LLM_MODEL:-default}"
printf '   NGA:              mode=%s enabled=%s scheduler=%s pdf=%s refs=%s\n' \
    "${NGA_MODE:-off}" "${NGA_ENABLED:-false}" "${NGA_SCHEDULER_ENABLED:-false}" \
    "${NGA_PDF_INDEX_ENABLED:-false}" "${NGA_REFERENCE_DELIVERY_ENABLED:-false}"
if [ "$WITH_STREAMLIT" = "true" ]; then
    printf '   Dashboard:        %shttp://localhost:%s%s\n' "$CYAN" "$STREAMLIT_DASH_PORT" "$RESET"
    printf '   Main Streamlit:   %shttp://localhost:%s%s\n' "$CYAN" "$STREAMLIT_MAIN_PORT" "$RESET"
fi
printf '\n%sQuick API Test:%s\n' "$BOLD" "$RESET"
if [ "$LOCAL_ONLY" = "true" ]; then
    printf '   curl http://127.0.0.1:%s/\n' "$FASTAPI_PORT"
else
    printf '   curl https://%s/\n' "$TUNNEL_HOST"
fi
printf '\n%sLogs:%s\n' "$BOLD" "$RESET"
printf '   Server:  %s\n' "$SERVER_LOG"
if [ "$LOCAL_ONLY" != "true" ]; then
    printf '   Tunnel:  %s\n' "$TUNNEL_LOG"
fi
if [ "$WITH_STREAMLIT" = "true" ]; then
    printf '   Dash:    %s/dashboard.log\n' "$LOG_DIR"
    printf '   App:     %s/streamlit.log\n' "$LOG_DIR"
fi
printf '\n%s%sIMPORTANT:%s\n' "$YELLOW" "$BOLD" "$RESET"
if [ "$LOCAL_ONLY" = "true" ]; then
    printf 'Local-only validation: no public URL was created and Twilio was not touched.\n'
elif [ "$NAMED_TUNNEL" = "true" ]; then
    printf 'Named tunnel "%s" -- URL persists across restarts.\n' "$TUNNEL_NAME"
else
    printf '%sThis tunnel is ephemeral. If you close this terminal, the\n' "$YELLOW"
    printf '%stunnel URL will CHANGE. Re-run this script to get a fresh tunnel\n' "$YELLOW"
    printf '%sand update all configs.%s\n' "$YELLOW" "$RESET"
    printf '%sTip: Use --named-tunnel for a permanent URL.%s\n' "$CYAN" "$RESET"
fi
printf '\n%sPress Ctrl+C to stop all services...%s\n' "$CYAN" "$RESET"
