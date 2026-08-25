from __future__ import annotations

import os
from pathlib import Path


def resolve_tunnel_host() -> str:
    """Return the current public tunnel hostname without a scheme.

    Resolution order is intentional:
    1. active TUNNEL_HOST env var (latest runtime value)
    2. file-based tunnel cache (.whatsapp_tunnel)
    3. legacy NGROK_HOST env var
    4. localhost fallback
    """
    tunnel_host = os.environ.get("TUNNEL_HOST", "").strip()
    if tunnel_host:
        return tunnel_host

    tunnel_file = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
    if tunnel_file.is_file():
        cached = tunnel_file.read_text(encoding="utf-8").strip()
        if cached:
            return cached

    return os.environ.get("NGROK_HOST", "localhost:8000")


def write_tunnel_host(host: str) -> str:
    """Persist a tunnel hostname for the next process start."""
    value = (host or "").strip()
    if not value:
        raise ValueError("Tunnel host cannot be empty")

    tunnel_file = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
    tunnel_file.write_text(value, encoding="utf-8")
    os.environ["TUNNEL_HOST"] = value
    return value
