#!/usr/bin/env bash
# Unix entry point (macOS + Linux) for bootstrap_services — ensures Python
# 3.11 exists, then delegates to bootstrap_services.py.
# Usage: bash bootstrap_services.sh [--check-only | --dry-run | ...]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "Python not found -- installing..."
    if command -v brew >/dev/null 2>&1; then
        brew install python@3.11
        PY="python3.11"
    elif command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
        apt-get update -qq && apt-get install -y -qq python3.11 python3.11-venv
        PY="python3.11"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "Need root to install Python: re-run with sudo"
        exit 1
    else
        echo "Install Python 3.11 manually, then re-run this script."
        exit 1
    fi
fi

exec "$PY" "$SCRIPT_DIR/bootstrap_services.py" "$@"
