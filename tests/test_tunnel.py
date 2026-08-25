from pathlib import Path

from app.tunnel import resolve_tunnel_host, write_tunnel_host


def _tunnel_file_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".whatsapp_tunnel"


def test_resolve_tunnel_host_prefers_runtime_env(monkeypatch):
    tunnel_file = _tunnel_file_path()
    previous = tunnel_file.read_text(encoding="utf-8") if tunnel_file.exists() else None
    try:
        tunnel_file.write_text("cached.trycloudflare.com", encoding="utf-8")
        monkeypatch.setenv("TUNNEL_HOST", "runtime.trycloudflare.com")
        monkeypatch.delenv("NGROK_HOST", raising=False)
        assert resolve_tunnel_host() == "runtime.trycloudflare.com"
    finally:
        if previous is None:
            tunnel_file.unlink(missing_ok=True)
        else:
            tunnel_file.write_text(previous, encoding="utf-8")


def test_resolve_tunnel_host_reads_cache_when_env_missing(monkeypatch):
    tunnel_file = _tunnel_file_path()
    previous = tunnel_file.read_text(encoding="utf-8") if tunnel_file.exists() else None
    try:
        tunnel_file.write_text("cached.trycloudflare.com", encoding="utf-8")
        monkeypatch.delenv("TUNNEL_HOST", raising=False)
        monkeypatch.delenv("NGROK_HOST", raising=False)
        assert resolve_tunnel_host() == "cached.trycloudflare.com"
    finally:
        if previous is None:
            tunnel_file.unlink(missing_ok=True)
        else:
            tunnel_file.write_text(previous, encoding="utf-8")


def test_write_tunnel_host_persists_value():
    tunnel_file = _tunnel_file_path()
    previous = tunnel_file.read_text(encoding="utf-8") if tunnel_file.exists() else None
    try:
        value = write_tunnel_host("fresh.trycloudflare.com")
        assert value == "fresh.trycloudflare.com"
        assert tunnel_file.read_text(encoding="utf-8") == "fresh.trycloudflare.com"
    finally:
        if previous is None:
            tunnel_file.unlink(missing_ok=True)
        else:
            tunnel_file.write_text(previous, encoding="utf-8")
