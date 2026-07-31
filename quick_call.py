#!/usr/bin/env python3
"""
Quick Outbound Call — CLI tool.

Makes an outbound call through the University Admissions Voice Assistant
with a single command.  Auto-detects whether the FastAPI server and
Cloudflare tunnel are already running.

Usage:
    python quick_call.py                          # interactive prompt
    python quick_call.py +917016872149            # call this number
    python quick_call.py +917016872149 "Rahul"    # call with name
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TUNNEL_FILE = PROJECT_ROOT / ".whatsapp_tunnel"
API_BASE = "http://127.0.0.1:8000"

# ── ANSI colours ────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def print_header():
    print()
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║   📞 Quick Outbound Call — CLI Tool     ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════╝{RESET}")
    print()


# ── Service detection ────────────────────────────────────────────────

def is_port_open(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Check if a TCP port is listening."""
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_fastapi() -> bool:
    """Check if FastAPI is running and healthy."""
    if not is_port_open():
        return False
    try:
        req = urllib.request.Request(f"{API_BASE}/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("status") == "ok"
    except Exception:
        return False


def get_tunnel_host() -> str | None:
    """Read the Cloudflare tunnel hostname from file or env var."""
    host = os.environ.get("TUNNEL_HOST", "")
    if host:
        return host
    if TUNNEL_FILE.is_file():
        return TUNNEL_FILE.read_text().strip()
    return None


# ── API calls ────────────────────────────────────────────────────────

def api_call(method: str, path: str, body: dict | None = None) -> dict:
    """Make a JSON REST call to the FastAPI backend."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def quick_call(phone: str, name: str = "") -> dict:
    """POST /api/quick-call — create lead + queue call in one step."""
    print(f"{BLUE}→ Creating lead & queuing call for {phone}...{RESET}")
    return api_call("POST", "/api/quick-call", {
        "phone_number": phone,
        "name": name,
    })


def poll_status(lead_id: str, timeout: int = 120) -> str:
    """Poll call status until terminal state or timeout."""
    print(f"{YELLOW}→ Polling call status (timeout {timeout}s)...{RESET}")
    start = time.time()
    last_status = ""

    while (time.time() - start) < timeout:
        try:
            result = api_call("GET", f"/api/call-queue?lead_id={lead_id}")
        except Exception:
            time.sleep(2)
            continue

        status = result.get("status", "queued")

        if status != last_status:
            ts = time.strftime("%H:%M:%S")
            if status == "queued":
                print(f"  {ts}  {YELLOW}⏳ Queued — waiting for worker...{RESET}")
            elif status == "ringing":
                print(f"  {ts}  {BLUE}📞 Ringing...{RESET}")
            elif status == "in-progress":
                print(f"  {ts}  {GREEN}✅ Call answered — AI speaking…{RESET}")
            elif status == "completed":
                print(f"  {ts}  {GREEN}✅ Call completed!{RESET}")
                return status
            elif status == "failed":
                err = result.get("error_message", "unknown")
                print(f"  {ts}  {RED}❌ Call failed: {err}{RESET}")
                return status
            elif status == "busy":
                print(f"  {ts}  {RED}📵 Line busy{RESET}")
                return status
            elif status == "no-answer":
                print(f"  {ts}  {RED}📵 No answer{RESET}")
                return status
            last_status = status

        if status in ("completed", "failed", "busy", "no-answer"):
            return status

        time.sleep(2)

    print(f"{RED}❌ Timeout — call did not complete within {timeout}s{RESET}")
    return "timeout"


# ── Startup helpers ──────────────────────────────────────────────────

def start_fastapi():
    """Launch FastAPI as a background subprocess."""
    print(f"{YELLOW}→ Starting FastAPI server...{RESET}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(PROJECT_ROOT),
    )
    for _ in range(30):
        if check_fastapi():
            print(f"{GREEN}  ✅ FastAPI ready on port 8000{RESET}")
            return proc
        time.sleep(1)
    print(f"{RED}  ❌ FastAPI failed to start within 30s{RESET}")
    return None


def start_tunnel():
    """Launch cloudflared tunnel and capture hostname."""
    print(f"{YELLOW}→ Starting Cloudflare tunnel...{RESET}")
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", "http://localhost:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    deadline = time.time() + 30
    for line in proc.stderr:
        if "trycloudflare.com" in line:
            parts = line.strip().split()
            for p in parts:
                if "trycloudflare.com" in p:
                    host = p.replace("https://", "").rstrip("/")
                    TUNNEL_FILE.write_text(host + "\n")
                    print(f"{GREEN}  ✅ Tunnel: {host}{RESET}")
                    return host
        if time.time() > deadline:
            break
    print(f"{RED}  ❌ Failed to capture tunnel URL within 30s{RESET}")
    return None


# ── Main ─────────────────────────────────────────────────────────────

def main():
    print_header()

    # 1. Check / start FastAPI
    if check_fastapi():
        print(f"{GREEN}✅ FastAPI running on port 8000{RESET}")
    else:
        print(f"{YELLOW}⚠️  FastAPI not running — starting...{RESET}")
        proc = start_fastapi()
        if proc is None:
            print(f"{RED}Cannot start FastAPI. Start it manually:{RESET}")
            print(f"  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
            sys.exit(1)

    # 2. Check / start tunnel
    host = get_tunnel_host()
    if host:
        print(f"{GREEN}✅ Tunnel: {host}{RESET}")
    else:
        print(f"{YELLOW}⚠️  No tunnel found — starting...{RESET}")
        host = start_tunnel()
        if host is None:
            print(f"{YELLOW}⚠️  Tunnel not available — outbound calls may not work{RESET}")
            print(f"   Start manually: cloudflared tunnel --url http://localhost:8000")

    # 3. Get phone number
    phone = ""
    name = ""
    if len(sys.argv) > 1:
        phone = sys.argv[1].strip()
    if len(sys.argv) > 2:
        name = sys.argv[2].strip()

    if not phone:
        phone = input(f"{BOLD}Phone number: {RESET}").strip()

    if not phone:
        print(f"{RED}No phone number provided. Exiting.{RESET}")
        sys.exit(1)

    if not name:
        try:
            name = input(f"{BOLD}Name (optional): {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            name = ""

    # 4. Make the call
    print()
    try:
        result = quick_call(phone, name)
    except Exception as e:
        print(f"{RED}❌ API call failed: {e}{RESET}")
        sys.exit(1)

    if result.get("error"):
        print(f"{RED}❌ Error: {result['error']}{RESET}")
        sys.exit(1)

    lead = result.get("lead", {})
    print(f"{GREEN}✅ Lead: {lead.get('id', '?')[:8]}…{RESET}")
    print(f"{GREEN}✅ Call queued!{RESET}")

    if result.get("tunnel_host"):
        wh = f"https://{result['tunnel_host']}/twilio/whatsapp"
        print(f"{CYAN}📋 Twilio Webhook: {wh}{RESET}")

    # 5. Poll until done
    lead_id = lead.get("id", "")
    if lead_id:
        print()
        final = poll_status(lead_id)
        print()
        if final == "completed":
            print(f"{BOLD}{GREEN}🎉 Call completed successfully!{RESET}")
        elif final in ("failed", "busy", "no-answer"):
            print(f"{BOLD}{RED}Call did not connect.{RESET}")
        else:
            print(f"{BOLD}{YELLOW}Call status: {final}{RESET}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{RESET}Exited.")
