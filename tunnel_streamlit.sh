#!/usr/bin/env bash
# =============================================================================
# Start Cloudflare tunnels for Streamlit Main (8501) and Dashboard (8502).
# -----------------------------------------------------------------------------
# Bash port of tunnel_streamlit.ps1. FastAPI (8000) tunnel is handled by
# start_services.sh — this script only covers the UI ports.
#

# Conventions shared with bootstrap_services.py start_unix:
#   logs/cloudflared_<port>.log   tunnel logs (scanned for the URL)
#   .tunnel_<port>                cached public hostname
#
# Bash 3.2 compatible (macOS default shell).
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
LOG_DIR="$PROJECT_ROOT/logs"

ESC="$(printf '\033')"
G="$ESC[92m"; Y="$ESC[93m"; R="$ESC[91m"; C="$ESC[96m"
B="$ESC[1m"; N="$ESC[0m"

# port -> cache file
TUNNEL_8501="$PROJECT_ROOT/.tunnel_8501"
TUNNEL_8502="$PROJECT_ROOT/.tunnel_8502"

url_ok() { [ "$(curl -s -o /dev/null -w "%{http_code}" "$1" 2>/dev/null)" = "200" ]; }

# A tunnel for a port may already be running (started by check_and_tunnel.sh
# or bootstrap) with no usable cache — never start a second one.
tunnel_process_running() {
    pgrep -f "cloudflared.*--url.*localhost:$1" >/dev/null 2>&1
}

# cloudflared must exist before we burn 40s timeouts per port
if ! command -v cloudflared >/dev/null 2>&1; then
    printf '%scloudflared not found! Install: brew install cloudflared%s\n' "$R" "$N"
    exit 1
fi

mkdir -p "$LOG_DIR"
printf '%s%sStarting Streamlit Tunnels...%s\n\n' "$B" "$C" "$N"

start_one() {
    port="$1"
    label="$2"
    cache="$3"

    hostname=""

    # Check if a tunnel is already alive for this port
    if [ -f "$cache" ]; then
        cached="$(tr -d '[:space:]' < "$cache")"
        if [ -n "$cached" ] && url_ok "https://$cached/"; then
            hostname="$cached"
            printf '%s  [OK]   %s tunnel already alive: %s%s\n' "$G" "$label" "$hostname" "$N"
        fi
    fi

    if [ -z "$hostname" ]; then
        # Never double-start: a live cloudflared for this port may exist
        # without a usable cache URL (e.g. logs were cleaned).
        if tunnel_process_running "$port"; then
            printf '%s  [..]   %s tunnel process is running but its URL is unknown%s\n' "$Y" "$label" "$N"
            printf '%s         Kill it and re-run to recreate: pkill -f cloudflared%s\n' "$Y" "$N"
            return
        fi

        printf '%s  [..]   Starting %s tunnel on port %s ...%s\n' "$Y" "$label" "$port" "$N"
        log="$LOG_DIR/cloudflared_${port}.log"
        : > "$log"  # clear stale URLs so we never misread an old hostname

        nohup cloudflared tunnel --url "http://localhost:$port" \
            --metrics "localhost:0" >> "$log" 2>&1 &

        attempt=0
        while [ -z "$hostname" ] && [ "$attempt" -lt 20 ]; do
            sleep 2
            attempt=$((attempt + 1))
            hostname="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | head -1 | sed 's|https://||')"
        done

        if [ -n "$hostname" ]; then
            # Verify reachable before declaring OK — fresh quick-tunnel
            # hostnames can take a minute to resolve (DNS propagation).
            verify=false
            v=0
            while [ "$verify" = "false" ] && [ "$v" -lt 6 ]; do
                if [ "$v" -gt 0 ]; then sleep 3; fi
                v=$((v + 1))
                url_ok "https://$hostname/" && verify=true
            done
            printf '%s' "$hostname" > "$cache"
            if [ "$verify" = "true" ]; then
                printf '%s  [OK]   %s tunnel started: %s%s\n' "$G" "$label" "$hostname" "$N"
            else
                printf '%s  [..]   %s tunnel started but not yet reachable (DNS warm-up): %s%s\n' "$Y" "$label" "$hostname" "$N"
            fi
        else
            printf '%s  [FAIL] %s tunnel did not start%s\n' "$R" "$label" "$N"
        fi
    fi
}

start_one 8501 "Streamlit Main" "$TUNNEL_8501"
start_one 8502 "Dashboard" "$TUNNEL_8502"

printf '\n%s%s=== Shareable Links ===%s\n\n' "$B" "$C" "$N"
for entry in "8501:Streamlit Main:$TUNNEL_8501" "8502:Dashboard:$TUNNEL_8502"; do
    label="${entry#*:}"
    label="${label%%:*}"
    cache="${entry##*:}"
    if [ -f "$cache" ]; then
        hostname="$(tr -d '[:space:]' < "$cache")"
        # Re-verify before sharing — a cached URL can outlive its tunnel.
        if [ -n "$hostname" ] && url_ok "https://$hostname/"; then
            printf '  %s: %shttps://%s%s\n' "$label" "$C" "$hostname" "$N"
        else
            printf '  %s: %stunnel down (re-run to recreate)%s\n' "$label" "$R" "$N"
        fi
    else
        printf '  %s: %snot running%s\n' "$label" "$R" "$N"
    fi
done
printf '\n'
