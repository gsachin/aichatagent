#!/usr/bin/env bash
# =============================================================================
# University Admissions Voice Assistant - One-shot launcher (macOS / Linux)
# -----------------------------------------------------------------------------
# Bash port of start_services.ps1 with full parity:
#   kills stale services, frees ports, checks the compute platform, pre-flights
#   Docker/PostgreSQL, starts FastAPI, pre-warms the LLM (Apple MLX on macOS
#   Apple Silicon, Ollama elsewhere), starts a Cloudflare tunnel, writes the
#   tunnel hostname everywhere it is needed, updates Twilio webhooks, and
#   optionally starts the Streamlit apps.
#
# Usage:
#   bash start_services.sh              # everything, including Streamlit UIs
#   bash start_services.sh --skip-streamlit   # FastAPI + tunnel only
#   bash start_services.sh --skip-twilio
#   bash start_services.sh --named-tunnel [--tunnel-name my-tunnel]
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
TUNNEL_NAME="admissions-tunnel"

while [ $# -gt 0 ]; do
    case "$1" in
        --with-streamlit) WITH_STREAMLIT=true ;;   # PS1-compat (already default)
        --skip-streamlit) WITH_STREAMLIT=false ;;
        --skip-twilio)    SKIP_TWILIO=true ;;
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
            echo "Usage: bash start_services.sh [--skip-streamlit] [--skip-twilio] [--named-tunnel [--tunnel-name NAME]]"
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
MLX_PORT="${MLX_PORT:-1234}"

mkdir -p "$LOG_DIR"

bootstrap_project_environment() {
    if [ "$(uname -s)" = "Darwin" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            echo "ERROR: Homebrew is required but not installed. Install it from https://brew.sh" >&2
            exit 1
        fi

        for _tool in ffmpeg cloudflared ollama; do
            if ! command -v "$_tool" >/dev/null 2>&1; then
                echo "Installing missing tool: $_tool"
                brew install "$_tool"
            fi
        done
    fi

    PY311_BIN=""
    for _cand in "$(command -v python3.11 2>/dev/null)" "/opt/homebrew/bin/python3.11" "/usr/local/bin/python3.11"; do
        if [ -n "$_cand" ] && [ -x "$_cand" ]; then
            if "$_cand" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
            then
                PY311_BIN="$_cand"
                break
            fi
        fi
    done

    if [ -z "$PY311_BIN" ]; then
        echo "ERROR: Python 3.11 is required but was not found." >&2
        exit 1
    fi

    for _venv_dir in "$PROJECT_ROOT/.venv" "$PROJECT_ROOT/venv"; do
        if [ -x "$_venv_dir/bin/python" ]; then
            if "$_venv_dir/bin/python" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
            then
                PYTHON="$_venv_dir/bin/python"
                break
            fi
            echo "WARN: Found stale venv at $_venv_dir with Python < 3.11; recreating it..."
            rm -rf "$_venv_dir"
        fi
    done

    if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
        echo "Creating local virtual environment: $PROJECT_ROOT/.venv"
        "$PY311_BIN" -m venv "$PROJECT_ROOT/.venv"
    fi

    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        "$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || true
        "$PROJECT_ROOT/.venv/bin/python" -m pip install -r "$PROJECT_ROOT/requirements.txt" >/dev/null 2>&1 || true
    fi

    PYTHON="$PROJECT_ROOT/.venv/bin/python"
}

bootstrap_project_environment

# ---- Load .env (safe for KEY=VALUE lines; comments/blank lines skipped) ----
if [ -f "$PROJECT_ROOT/.env" ]; then
    while IFS='=' read -r _key _value; do
        case "$_key" in ''|\#*) continue ;; esac
        [ -n "$_value" ] && export "$_key"="$_value"
    done < "$PROJECT_ROOT/.env"
fi

# ---- Python selection (prefer the project .venv, then venv, never system 3.9) ---
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/venv/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
then
    echo "ERROR: Project runtime must use Python 3.11+; the active interpreter is: $PYTHON" >&2
    echo "Fix: rerun setup.sh or delete the stale venv and recreate it with python3.11." >&2
    exit 1
fi

# ---- LLM provider resolution (matches app/llm_backend.py) -----------------
LLM_PROVIDER="${LLM_PROVIDER:-auto}"
if [ "$LLM_PROVIDER" = "auto" ]; then
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        LLM_PROVIDER="mlx"
    else
        LLM_PROVIDER="ollama"
    fi
fi
MLX_MODEL="${MLX_MODEL:-mlx-community/Qwen2.5-14B-Instruct-4bit}"
MLX_BASE_URL="${MLX_BASE_URL:-http://127.0.0.1:$MLX_PORT}"
MLX_WARMUP_TIMEOUT="${MLX_WARMUP_TIMEOUT:-20}"

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
    write_ok "LLM backend: Apple MLX (mlx_lm.server on :$MLX_PORT) -- no CUDA/Ollama needed"
else
    write_warn "No NVIDIA GPU detected -- STT and TTS will fall back to CPU (slow)"
fi

# ==== Step 4: Docker / PostgreSQL check ====================================
write_step "Step 4: Docker / PostgreSQL check"

DB_READY=false

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
    _pg_out="$(docker exec elearning-postgres pg_isready -U elearning -d admissions 2>/dev/null || true)"
    if echo "$_pg_out" | grep -q "accepting"; then
        write_ok "PostgreSQL: accepting connections on localhost:5432"
        DB_READY=true
    else
        write_warn "PostgreSQL container not running -- attempting docker compose up..."
        if docker compose -f "$PROJECT_ROOT/docker-compose.yml" up -d postgres >/dev/null 2>&1; then
            sleep 5
            _pg_out2="$(docker exec elearning-postgres pg_isready -U elearning -d admissions 2>/dev/null || true)"
            if echo "$_pg_out2" | grep -q "accepting"; then
                write_ok "PostgreSQL started and accepting connections"
                DB_READY=true
            fi
        else
            write_warn "docker compose failed -- checking for existing e-learning container..."
            if docker ps --filter "name=elearning-postgres" --format "{{.Status}}" 2>/dev/null | grep -q "healthy"; then
                write_ok "Found existing elearning-postgres container (healthy)"
                DB_READY=true
            fi
        fi
    fi
fi

if [ "$DB_READY" = "false" ]; then
    write_warn "PostgreSQL is NOT available -- server will start in database-less mode"
    write_warn "Leads, calls, and follow-ups will NOT be persisted"
    write_warn "Fix: ensure Docker is running with the elearning-postgres container"
else
    write_ok "Database pre-flight: PASSED"
fi

# ==== Step 5: Start FastAPI backend ========================================
write_step "Step 5: Starting FastAPI backend (port $FASTAPI_PORT)"

SERVER_LOG="$LOG_DIR/fastapi.log"
: > "$SERVER_LOG"
echo "  Python: $PYTHON"
nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$FASTAPI_PORT" \
    >> "$SERVER_LOG" 2>&1 &
FASTAPI_PID=$!
write_ok "FastAPI starting (PID $FASTAPI_PID) - log: $SERVER_LOG"

_attempt=0
SERVER_READY=false
while [ "$SERVER_READY" = "false" ] && [ "$_attempt" -lt 30 ]; do
    sleep 2
    _attempt=$((_attempt + 1))
    if [ "$(http_code "http://127.0.0.1:$FASTAPI_PORT/")" = "200" ]; then
        write_ok "FastAPI server responding (took ~$((_attempt * 2))s)"
        SERVER_READY=true
    else
        write_warn "Waiting for FastAPI server... ($_attempt/30)"
    fi
done

if [ "$SERVER_READY" = "false" ]; then
    write_err "FastAPI server did NOT come up - check $SERVER_LOG"
    exit 1
fi

# ==== Step 6: LLM pre-warming ==============================================
write_step "Step 6: LLM pre-warming ($LLM_PROVIDER)"

if [ "$LLM_PROVIDER" = "mlx" ]; then
    # ---- Apple MLX: ensure model is cached and mlx_lm.server is ready ----
    # Install huggingface_hub if missing so we can pre-download with progress
    if ! "$PYTHON" -c "import huggingface_hub" >/dev/null 2>&1; then
        write_warn "Required Python package 'huggingface_hub' not found -- installing..."
        "$PYTHON" -m pip install --upgrade huggingface_hub || true
    fi

    # Check whether the MLX model is already present in the HF cache. If not,
    # run snapshot_download in the foreground so the user sees progress.
    write_step "Checking MLX model cache: $MLX_MODEL"
    if "$PYTHON" - <<PY
import sys
from huggingface_hub import snapshot_download
repo = "${MLX_MODEL}"
try:
    snapshot_download(repo, local_files_only=True)
    print('MODEL_CACHED')
    sys.exit(0)
except Exception as e:
    print('MODEL_NOT_CACHED', e)
    sys.exit(2)
PY
    then
        write_ok "Model already cached: $MLX_MODEL"
    else
        write_warn "Model not cached: will download now (this may take many minutes)"
        write_warn "If the download appears stuck, check your network / HF_TOKEN"
        # Run the download in the foreground so progress bars are visible to the user
        "$PYTHON" - <<PY
from huggingface_hub import snapshot_download
import os
repo = "${MLX_MODEL}"
token = os.environ.get('HF_TOKEN')
snapshot_download(repo, token=token, library_name='mlx_lm')
print('MODEL_DOWNLOAD_COMPLETE')
PY
        write_ok "Model download finished: $MLX_MODEL"
    fi

    # Now start the MLX server (it will load from cache rather than re-downloading)
    if [ -z "$(port_pids "$MLX_PORT")" ]; then
        write_warn "MLX server not running -- starting mlx_lm.server on :$MLX_PORT..."
        nohup "$PYTHON" -m mlx_lm.server --model "$MLX_MODEL" \
            --host 127.0.0.1 --port "$MLX_PORT" \
            >> "$LOG_DIR/mlx_server.log" 2>&1 &
        write_ok "MLX server starting (log: $LOG_DIR/mlx_server.log)"
    else
        write_ok "MLX server already running on :$MLX_PORT"
    fi

    # First load of a 14B model can take well over a minute.
    _attempt=0
    MLX_UP=false
    while [ "$MLX_UP" = "false" ] && [ "$_attempt" -lt 90 ]; do
        sleep 2
        _attempt=$((_attempt + 1))
        if [ "$(http_code "$MLX_BASE_URL/v1/models")" = "200" ]; then
            write_ok "MLX server ready (took ~$((_attempt * 2))s)"
            MLX_UP=true
        fi
    done
    if [ "$MLX_UP" = "false" ]; then
        write_err "MLX server did NOT come up - check $LOG_DIR/mlx_server.log"
        exit 1
    fi

    if curl -sS --connect-timeout 5 --max-time "$MLX_WARMUP_TIMEOUT" \
        -X POST "$MLX_BASE_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$MLX_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}" \
        -o /dev/null 2>/dev/null; then
        write_ok "  $MLX_MODEL loaded into memory"
    else
        write_warn "  Pre-warm request timed out or failed for $MLX_MODEL -- continuing startup"
    fi
else
    # ---- Ollama: /api/tags + per-model keep_alive pre-warm (PS1 parity) ---
    if [ "$(http_code "http://127.0.0.1:11434/api/tags")" = "200" ]; then
        write_ok "Ollama is running"
    else
        write_warn "Ollama not reachable on port 11434 -- skip pre-warming"
    fi

    if [ "$(http_code "http://127.0.0.1:11434/api/tags")" = "200" ]; then
        _model_list="$(curl -s "http://127.0.0.1:11434/api/tags" 2>/dev/null | \
            "$PYTHON" -c "import sys,json; print('\n'.join(m['name'] for m in json.load(sys.stdin).get('models',[])))" 2>/dev/null)"
        if [ -n "$_model_list" ]; then
            write_ok "Found models: $(echo "$_model_list" | tr '\n' ' ')"
            _old_ifs="$IFS"; IFS='
'
            for _model in $_model_list; do
                [ -z "$_model" ] && continue
                write_ok "Pre-warming: $_model ..."
                if curl -s -X POST "http://127.0.0.1:11434/api/generate" \
                    -H "Content-Type: application/json" \
                    -d "{\"model\":\"$_model\",\"prompt\":\"ping\",\"keep_alive\":\"24h\",\"max_tokens\":1}" \
                    -o /dev/null 2>/dev/null; then
                    write_ok "  $_model loaded into GPU (keep_alive=24h)"
                else
                    write_warn "  Pre-warm failed for $_model"
                fi
            done
            IFS="$_old_ifs"
        else
            write_warn "No models found in Ollama -- run: ollama pull qwen2.5:7b"
        fi
    fi
fi

# ==== Step 7: Start Cloudflare tunnel ======================================
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

# ==== Step 11: Optional Streamlit apps =====================================
if [ "$WITH_STREAMLIT" = "true" ]; then
    write_step "Step 11: Starting Streamlit apps"

    # Ensure the persisted RAG vector store exists so the Streamlit app
    # doesn't block trying to build or fail with the "Check that the PDF exists" message.
    CHROMA_DIR="$PROJECT_ROOT/chroma_local_db"
    if [ -n "${CHROMA_DB_PATH:-}" ]; then
        CHROMA_DIR="$CHROMA_DB_PATH"
    fi
    if [ ! -d "$CHROMA_DIR" ] || [ -z "$(ls -A "$CHROMA_DIR" 2>/dev/null)" ]; then
        write_warn "Chroma DB missing or empty at $CHROMA_DIR — rebuilding index (scripts/rebuild_rag_index.py)"
        # Run rebuild; allow it to fail without aborting the whole startup (user can inspect logs)
        if "$PYTHON" "$PROJECT_ROOT/scripts/rebuild_rag_index.py"; then
            write_ok "Rebuilt Chroma DB: $CHROMA_DIR"
        else
            write_warn "Rebuild script failed — Streamlit may show a vector-store error"
        fi
    else
        write_ok "Found existing Chroma DB: $CHROMA_DIR"
    fi

    # Export backend URL and timeout for the Streamlit processes so
    # `app.streamlit_backend` uses the correct address instead of
    # falling back to a possibly different host.
    export BACKEND_BASE="http://127.0.0.1:$FASTAPI_PORT"
    export BACKEND_TIMEOUT=10
    write_ok "Exported BACKEND_BASE=$BACKEND_BASE BACKEND_TIMEOUT=$BACKEND_TIMEOUT"

    nohup "$PYTHON" -m streamlit run dashboard.py \
        --server.port "$STREAMLIT_DASH_PORT" --server.headless true \
        >> "$LOG_DIR/dashboard.log" 2>&1 &
    write_ok "Dashboard starting -> http://localhost:$STREAMLIT_DASH_PORT"

    nohup "$PYTHON" -m streamlit run app.py \
        --server.port "$STREAMLIT_MAIN_PORT" --server.headless true \
        >> "$LOG_DIR/streamlit.log" 2>&1 &
    write_ok "Main app starting -> http://localhost:$STREAMLIT_MAIN_PORT"
fi

# ==== Step 12: Streamlit chat tunnel (8501) ================================
# The chat UI needs its own public URL (WebSocket UI can't share the
# FastAPI tunnel). Reuse the conventions of tunnel_streamlit.sh.
CHAT_TUNNEL_HOST=""
if [ "$WITH_STREAMLIT" = "true" ]; then
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

# ==== Step 13: Summary =====================================================
printf '\n%s%sALL SERVICES STARTED SUCCESSFULLY%s\n\n' "$GREEN" "$BOLD" "$RESET"
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
printf '\n%sLocal Services:%s\n' "$BOLD" "$RESET"
printf '   FastAPI backend:  %shttp://localhost:%s%s\n' "$CYAN" "$FASTAPI_PORT" "$RESET"
printf '   LLM backend:      %s (%s)%s\n' "$CYAN" "$LLM_PROVIDER" "$RESET"
if [ "$LLM_PROVIDER" = "mlx" ]; then
    printf '   MLX server:       %shttp://127.0.0.1:%s/v1%s\n' "$CYAN" "$MLX_PORT" "$RESET"
fi
if [ "$WITH_STREAMLIT" = "true" ]; then
    printf '   Dashboard:        %shttp://localhost:%s%s\n' "$CYAN" "$STREAMLIT_DASH_PORT" "$RESET"
    printf '   Main Streamlit:   %shttp://localhost:%s%s\n' "$CYAN" "$STREAMLIT_MAIN_PORT" "$RESET"
fi
printf '\n%sQuick API Test:%s\n' "$BOLD" "$RESET"
printf '   curl https://%s/\n' "$TUNNEL_HOST"
printf '\n%sLogs:%s\n' "$BOLD" "$RESET"
printf '   Server:  %s\n' "$SERVER_LOG"
printf '   Tunnel:  %s\n' "$TUNNEL_LOG"
if [ "$LLM_PROVIDER" = "mlx" ]; then
    printf '   MLX:     %s/mlx_server.log\n' "$LOG_DIR"
fi
if [ "$WITH_STREAMLIT" = "true" ]; then
    printf '   Dash:    %s/dashboard.log\n' "$LOG_DIR"
    printf '   App:     %s/streamlit.log\n' "$LOG_DIR"
fi
printf '\n%s%sIMPORTANT:%s\n' "$YELLOW" "$BOLD" "$RESET"
if [ "$NAMED_TUNNEL" = "true" ]; then
    printf 'Named tunnel "%s" -- URL persists across restarts.\n' "$TUNNEL_NAME"
else
    printf '%sThis tunnel is ephemeral. If you close this terminal, the\n' "$YELLOW"
    printf '%stunnel URL will CHANGE. Re-run this script to get a fresh tunnel\n' "$YELLOW"
    printf '%sand update all configs.%s\n' "$YELLOW" "$RESET"
    printf '%sTip: Use --named-tunnel for a permanent URL.%s\n' "$CYAN" "$RESET"
fi
printf '\n%sPress Ctrl+C to stop all services...%s\n' "$CYAN" "$RESET"
