#!/usr/bin/env bash
# Stop the services started by start_services.sh.
#
# Only current services are touched: FastAPI (:8000), Streamlit chat (:8501),
# Streamlit dashboard (:8502), and cloudflared tunnels. Postgres is left running
# because Docker manages it and other applications may share the container.
#
# Usage: bash stop_services.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTS="8000 8501 8502"

write_ok() { printf "  OK: %s\n" "$*"; }

if pgrep -f cloudflared >/dev/null 2>&1; then
    pkill -f cloudflared 2>/dev/null
    sleep 1
    pkill -9 -f cloudflared 2>/dev/null
    write_ok "stopped cloudflared"
else
    write_ok "no cloudflared process"
fi

for port in $PORTS; do
    pids="$(lsof -ti "tcp:$port" -s tcp:LISTEN 2>/dev/null)"
    if [ -n "$pids" ]; then
        for pid in $pids; do
            kill "$pid" 2>/dev/null || true
        done
        sleep 1
        for pid in $pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
        write_ok "freed port $port"
    else
        write_ok "port $port already free"
    fi
done

printf '\nApplication services stopped. PostgreSQL was left alone.\n'
printf 'Stop the database explicitly with: docker stop elearning-postgres\n'
