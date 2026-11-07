#!/usr/bin/env bash
# =============================================================================
# Health check + tunnel manager for the University Admissions Assistant.
# -----------------------------------------------------------------------------
# Bash port of check_and_tunnel.ps1:
#   - Compute platform check (nvidia-smi on Linux, Metal/MLX on Apple Silicon)
#   - Docker / PostgreSQL readiness check
#   - Checks FastAPI (8000), Streamlit main (8501), Streamlit dashboard (8502)
#   - Checks / starts a Cloudflare tunnel for EACH port
#   - Prints a shareable status card with all local + public URLs
#   - Does NOT kill anything -- safe to run while the app is in use
#
# Usage:
#   bash check_and_tunnel.sh [--skip-tunnel]
#
# Conventions shared with bootstrap_services.py start_unix:
#   logs/cloudflared_<port>.log   tunnel logs (scanned for the URL)
#   .tunnel_<port>                cached public hostname
#
# Bash 3.2 compatible (macOS default shell).
# =============================================================================

SKIP_TUNNEL=false
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tunnel) SKIP_TUNNEL=true ;;
        *) echo "ERROR: unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
LOG_DIR="$PROJECT_ROOT/logs"
FASTAPI_PORT=8000
STREAMLIT_MAIN_PORT=8501
STREAMLIT_DASH_PORT=8502
TWILIO_PHONE="${TWILIO_PHONE_NUMBER:-+19788198953}"

ESC="$(printf '\033')"
G="$ESC[92m"; Y="$ESC[93m"; R="$ESC[91m"; C="$ESC[96m"
B="$ESC[1m"; N="$ESC[0m"

write_step() { printf '%s%s--- %s ---%s\n' "$C" "$B" "$*" "$N"; }
write_ok()   { printf '%s  [OK]   %s%s\n' "$G" "$*" "$N"; }
write_warn() { printf '%s  [WARN] %s%s\n' "$Y" "$*" "$N"; }
write_err()  { printf '%s  [DOWN] %s%s\n' "$R" "$*" "$N"; }

url_ok() { [ "$(curl -s -o /dev/null -w "%{http_code}" "$1" 2>/dev/null)" = "200" ]; }

tunnel_process_running() {
    pgrep -f "cloudflared.*--url.*localhost:$1" >/dev/null 2>&1
}

# Tunnel definitions: port label cache-file
tunnels() {
    printf '%s\n' "$FASTAPI_PORT FastAPI $PROJECT_ROOT/.tunnel_8000"
    printf '%s\n' "$STREAMLIT_MAIN_PORT Streamlit $PROJECT_ROOT/.tunnel_8501"
    printf '%s\n' "$STREAMLIT_DASH_PORT Dashboard $PROJECT_ROOT/.tunnel_8502"
}

# Resolve the live public hostname for a port.
# 1) Scan this port's OWN log first — a reachable URL there is always for
#    THIS port (logs are cleared right before a new tunnel starts).
# 2) Fall back to the cache file ONLY when this port has no log at all.
#    If a per-port log exists but held no reachable URL, a cached hostname
#    could belong to a different port (poisoned cache) — don't trust it.
get_tunnel_host() {
    port="$1"; cache="$2"
    log="$LOG_DIR/cloudflared_${port}.log"

    if [ -f "$log" ]; then
        while IFS= read -r hostname; do
            [ -n "$hostname" ] || continue
            if url_ok "https://$hostname/"; then
                printf '%s' "$hostname" > "$cache"
                echo "$hostname"
                return
            fi
        done < <(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | sed 's|https://||')
        return
    fi

    if [ -f "$cache" ]; then
        cached="$(tr -d '[:space:]' < "$cache")"
        if [ -n "$cached" ] && url_ok "https://$cached/"; then
            echo "$cached"
        fi
    fi
}

start_single_tunnel() {
    # NOTE: stdout carries ONLY the captured hostname (this function is
    # called in $(...) substitution) -- all human messages go to stderr.
    port="$1"; label="$2"; cache="$3"

    # Never double-start: a live cloudflared for this port may exist without
    # a usable cache URL (e.g. logs were cleaned).
    if tunnel_process_running "$port"; then
        write_warn "$label tunnel process already running but URL unknown -- pkill -f cloudflared and re-run to recreate" >&2
        return
    fi

    log="$LOG_DIR/cloudflared_${port}.log"
    : > "$log"

    nohup cloudflared tunnel --url "http://localhost:$port" \
        --metrics "localhost:0" >> "$log" 2>&1 &

    hostname=""
    attempt=0
    while [ -z "$hostname" ] && [ "$attempt" -lt 20 ]; do
        sleep 2
        attempt=$((attempt + 1))
        hostname="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | head -1 | sed 's|https://||')"
    done

    if [ -n "$hostname" ]; then
        # Verify reachable before trusting it (DNS can lag for fresh hostnames).
        # Cache is written either way -- next run's cache check re-verifies, so a
        # URL that is only slow to resolve heals itself without tunnel churn.
        verify=false
        v=0
        while [ "$verify" = "false" ] && [ "$v" -lt 6 ]; do
            if [ "$v" -gt 0 ]; then sleep 3; fi
            v=$((v + 1))
            url_ok "https://$hostname/" && verify=true
        done
        printf '%s' "$hostname" > "$cache"
        if [ "$verify" = "true" ]; then
            write_ok "$label tunnel started: $hostname" >&2
        else
            write_warn "$label tunnel started but not yet reachable (DNS warm-up): $hostname" >&2
        fi
        echo "$hostname"
    else
        write_err "$label tunnel did not start within timeout" >&2
    fi
}

# ============================================================================
printf '%s%sUniversity Admissions -- Service Status%s\n\n' "$B" "$C" "$N"

# ==== Step 1: Compute platform check =======================================
write_step "Compute Platform Check"

GPU_OK=false
if command -v nvidia-smi >/dev/null 2>&1; then
    gpu_info="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ' | ')"
    if [ -n "$gpu_info" ]; then
        write_ok "GPU: $gpu_info"
        GPU_OK=true
    else
        write_warn "nvidia-smi found but query failed -- GPU may be unavailable"
    fi
elif [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    mem_gb="$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}')"
    chip="$(system_profiler SPDisplaysDataType 2>/dev/null | grep -i "Chipset Model" | head -1 | sed 's/.*: //')"
    write_ok "Apple Silicon: ${chip:-Metal GPU} (${mem_gb} GB unified memory) -- LLM via MLX"
    GPU_OK=true
else
    write_warn "No NVIDIA GPU detected -- CPU mode"
fi

# ==== Step 2: Docker / PostgreSQL check ====================================
write_step "Docker / PostgreSQL Check"

DB_READY=false
if docker info >/dev/null 2>&1; then
    write_ok "Docker daemon is running"
    pg_out="$(docker exec elearning-postgres pg_isready -U elearning -d admissions 2>/dev/null || true)"
    if echo "$pg_out" | grep -q "accepting"; then
        write_ok "PostgreSQL: accepting connections on localhost:5432"
        DB_READY=true
    else
        write_warn "PostgreSQL container not responding -- checking alternative..."
        if docker ps --filter "name=elearning-postgres" --format "{{.Status}}" 2>/dev/null | grep -q "healthy"; then
            write_ok "Found existing elearning-postgres container (healthy)"
            DB_READY=true
        else
            write_warn "PostgreSQL is NOT available -- database features will fail"
        fi
    fi
else
    write_warn "Docker daemon is NOT running -- database features will fail"
fi
if [ "$DB_READY" = "true" ]; then
    write_ok "Database pre-flight: PASSED"
fi

# ==== Step 3: Check local services =========================================
write_step "Local Services"

url_ok "http://localhost:$FASTAPI_PORT/"        && FASTAPI_UP=true    || FASTAPI_UP=false
url_ok "http://localhost:$STREAMLIT_MAIN_PORT/" && STREAMLIT_UP=true  || STREAMLIT_UP=false
url_ok "http://localhost:$STREAMLIT_DASH_PORT/" && DASH_UP=true       || DASH_UP=false

if [ "$FASTAPI_UP" = "true" ];  then write_ok   "FastAPI backend      http://localhost:$FASTAPI_PORT"; else write_err "FastAPI backend      http://localhost:$FASTAPI_PORT"; fi
if [ "$STREAMLIT_UP" = "true" ]; then write_ok "Streamlit Main       http://localhost:$STREAMLIT_MAIN_PORT"; else write_err "Streamlit Main       http://localhost:$STREAMLIT_MAIN_PORT"; fi
if [ "$DASH_UP" = "true" ];      then write_ok "Streamlit Dashboard  http://localhost:$STREAMLIT_DASH_PORT"; else write_err "Streamlit Dashboard  http://localhost:$STREAMLIT_DASH_PORT"; fi

# ==== Step 4: Check / start Cloudflare tunnels =============================
write_step "Cloudflare Tunnels"

if ! command -v cloudflared >/dev/null 2>&1 && [ "$SKIP_TUNNEL" = "false" ]; then
    write_err "cloudflared not found! Install: brew install cloudflared"
    write_err "Re-run with --skip-tunnel to see the status card without tunnels."
    exit 1
fi

mkdir -p "$LOG_DIR"

F_TUNNEL=""; S_TUNNEL=""; D_TUNNEL=""
while read -r port label cache; do
    hostname="$(get_tunnel_host "$port" "$cache")"
    if [ -n "$hostname" ]; then
        write_ok "$label tunnel alive: $hostname"
    elif tunnel_process_running "$port"; then
        # A process exists but its URL isn't reachable yet (fresh quick
        # tunnel, DNS propagation) — do NOT start a second tunnel.
        write_warn "$label tunnel running but URL not reachable yet (DNS warm-up) -- re-run in a minute"
    elif [ "$SKIP_TUNNEL" = "false" ]; then
        write_warn "$label tunnel missing -- starting..."
        hostname="$(start_single_tunnel "$port" "$label" "$cache")"
    else
        write_err "$label tunnel DOWN (--skip-tunnel set)"
    fi
    case "$port" in
        "$FASTAPI_PORT")        F_TUNNEL="$hostname" ;;
        "$STREAMLIT_MAIN_PORT") S_TUNNEL="$hostname" ;;
        "$STREAMLIT_DASH_PORT") D_TUNNEL="$hostname" ;;
    esac
done < <(tunnels)

# Keep the app's own tunnel file in sync when the FastAPI tunnel is known
# (the app resolves its public webhooks from .whatsapp_tunnel).
if [ -n "$F_TUNNEL" ]; then
    printf '%s' "$F_TUNNEL" > "$PROJECT_ROOT/.whatsapp_tunnel"
    write_ok ".whatsapp_tunnel updated -> $F_TUNNEL"
fi

# ==== Step 5: Status table =================================================
printf '\n'
printf '%s+---------------------+-----------------------------------------------+-----------+%s\n' "$B" "$N"
printf '%s| Service             | URL                                           | Status    |%s\n' "$B" "$N"
printf '%s+---------------------+-----------------------------------------------+-----------+%s\n' "$B" "$N"

up_down() { if [ "$1" = "true" ]; then printf '%sUP  %s' "$G" "$N"; else printf '%sDOWN%s' "$R" "$N"; fi; }

printf '  FastAPI Backend       http://localhost:%s\t\t\t   %s\n' "$FASTAPI_PORT" "$(up_down "$FASTAPI_UP")"
printf '  Streamlit Main        http://localhost:%s\t\t\t   %s\n' "$STREAMLIT_MAIN_PORT" "$(up_down "$STREAMLIT_UP")"
printf '  Streamlit Dashboard   http://localhost:%s\t\t\t   %s\n' "$STREAMLIT_DASH_PORT" "$(up_down "$DASH_UP")"

printf '%s+---------------------+-----------------------------------------------+-----------+%s\n' "$B" "$N"

if [ -n "$F_TUNNEL" ]; then printf '  FastAPI Tunnel        https://%s\t\t   %sUP%s\n' "$F_TUNNEL" "$G" "$N"; else printf '  FastAPI Tunnel        (not running)\t\t\t\t   %sDOWN%s\n' "$R" "$N"; fi
if [ -n "$S_TUNNEL" ]; then printf '  Streamlit Tunnel      https://%s\t\t   %sUP%s\n' "$S_TUNNEL" "$G" "$N"; else printf '  Streamlit Tunnel      (not running)\t\t\t\t   %sDOWN%s\n' "$R" "$N"; fi
if [ -n "$D_TUNNEL" ]; then printf '  Dashboard Tunnel      https://%s\t\t   %sUP%s\n' "$D_TUNNEL" "$G" "$N"; else printf '  Dashboard Tunnel      (not running)\t\t\t\t   %sDOWN%s\n' "$R" "$N"; fi

printf '%s+---------------------+-----------------------------------------------+-----------+%s\n' "$B" "$N"

gpu_label="OK"; db_label="OK"
[ "$GPU_OK" = "false" ] && gpu_label="N/A"
[ "$DB_READY" = "false" ] && db_label="N/A"
printf '\n  GPU: %s     PostgreSQL: %s\n' "$gpu_label" "$db_label"

# ==== Step 6: Twilio setup instructions ====================================
if [ -n "$F_TUNNEL" ]; then
    printf '\n%s%s=== Twilio Configuration -- Copy & Paste ===%s\n\n' "$B" "$C" "$N"

    printf '%s1. Voice Webhook (Phone Number)%s\n' "$B" "$N"
    printf '   URL:  %shttps://%s/twilio/voice%s\n' "$G" "$F_TUNNEL" "$N"
    printf '   Where: Twilio Console -> Phone Numbers -> %s -> Voice & Fax\n' "$TWILIO_PHONE"
    printf '          Set "A call comes in" to this URL (HTTP GET)\n'
    printf '   Auto:  start_services.sh updates this via API -- no manual step needed\n\n'

    printf '%s2. WhatsApp Sandbox (Manual -- the one you need to do)%s\n' "$B" "$N"
    printf '   URL:  %shttps://%s/twilio/whatsapp%s\n' "$G" "$F_TUNNEL" "$N"
    printf '   Where: Twilio Console -> Messaging -> Try it out -> WhatsApp Sandbox\n'
    printf '          Paste in the "When a message comes in" field (HTTP POST)\n'
    printf '   %s>> THIS is the one you must update manually each time the tunnel changes%s\n\n' "$Y" "$N"

    printf '%s3. Outbound Call Status (optional)%s\n' "$B" "$N"
    printf '   URL:  %shttps://%s/twilio/outbound/status%s\n' "$C" "$F_TUNNEL" "$N"
    printf '   Where: Twilio Console -> Phone Numbers -> %s -> Voice & Fax\n' "$TWILIO_PHONE"
    printf '          Set "Call status changes" to this URL (HTTP POST)\n\n'
fi

# ==== Step 7: Shareable quick-links ========================================
printf '\n%s%s=== Shareable Links ===%s\n\n' "$B" "$C" "$N"
if [ -n "$S_TUNNEL" ]; then printf '  Chatbot:   %shttps://%s%s\n' "$C" "$S_TUNNEL" "$N"; else printf '  Chatbot:   %shttp://localhost:%s%s (local only)\n' "$R" "$STREAMLIT_MAIN_PORT" "$N"; fi
if [ -n "$D_TUNNEL" ]; then printf '  Dashboard: %shttps://%s%s\n' "$C" "$D_TUNNEL" "$N"; else printf '  Dashboard: %shttp://localhost:%s%s (local only)\n' "$R" "$STREAMLIT_DASH_PORT" "$N"; fi

# ==== Step 8: Help if things are down ======================================
if [ "$FASTAPI_UP" != "true" ] || [ "$STREAMLIT_UP" != "true" ] || [ "$DASH_UP" != "true" ] || [ -z "$F_TUNNEL" ] || [ -z "$S_TUNNEL" ] || [ -z "$D_TUNNEL" ]; then
    printf '\n%sSome services are DOWN. To restart everything:%s\n' "$Y" "$N"
    printf '  %sbash start_services.sh --with-streamlit%s\n' "$C" "$N"
fi

printf '\n'
