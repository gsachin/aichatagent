"""
bootstrap_services — one-click pre-req check, install, and ordered startup.

Single entry point for the University Admissions Voice Assistant across
Windows / macOS / cloud Linux:

  Phase B (CHECK)   — verify every prerequisite, change nothing.
  Phase C (INSTALL) — install whatever is missing (idempotent, never
                      downgrades, refuses destructive actions).
  Phase D (START)   — start services in dependency order (least dependent
                      first): Docker -> PostgreSQL -> Ollama (+models) ->
                      FastAPI -> Cloudflare tunnel -> Twilio updates ->
                      Streamlit + per-port tunnels.
  Phase E (TWILIO)  — voice + status-callback webhooks via API; WhatsApp
                      sandbox instructions (console-only, no API).

Stdlib-only by design — this script must run before any pip dependency
exists. Windows delegates the launch to the battle-tested
start_services.ps1 + check_and_tunnel.ps1; macOS/Linux launch natively.

Usage:
    python bootstrap_services.py                 # full bootstrap + start
    python bootstrap_services.py --check-only    # report pre-reqs, no changes
    python bootstrap_services.py --dry-run       # print actions, do nothing
    python bootstrap_services.py --skip-install  # launch only (assume pre-reqs)
    python bootstrap_services.py --with-streamlit
    python bootstrap_services.py --with-demo-data   # seeds ONLY if DB empty
"""

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = (
    ROOT / ".venv" / "Scripts" / "python.exe"
    if platform.system() == "Windows"
    else ROOT / ".venv" / "bin" / "python"
)
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

REQUIRED_MODELS = ["qwen2.5:7b-instruct-q3_K_M", "nomic-embed-text"]
OLLAMA_API = "http://127.0.0.1:11434"
DOCKER_DESKTOP_WIN = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"

# Apple MLX backend (macOS Apple Silicon only — replaces Ollama there)
MLX_MODEL = os.environ.get("MLX_MODEL", "mlx-community/Qwen2.5-14B-Instruct-4bit")
MLX_EMBED_MODEL = os.environ.get("MLX_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
MLX_PORT = int(os.environ.get("MLX_PORT", "1234"))
MLX_BASE_URL = os.environ.get("MLX_BASE_URL", f"http://127.0.0.1:{MLX_PORT}")
IS_APPLE_SILICON = IS_MACOS and platform.machine() in ("arm64", "aarch64")


def _llm_provider() -> str:
    """'mlx' on Apple Silicon unless LLM_PROVIDER says otherwise, else 'ollama'."""
    explicit = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
    if explicit in ("mlx", "ollama"):
        return explicit
    return "mlx" if IS_APPLE_SILICON else "ollama"


def _hf_model_cached(model_id: str) -> bool:
    """Filesystem-only check that a HuggingFace model is fully downloaded."""
    org, repo = model_id.split("/", 1)
    hub = Path(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))) / "hub"
    return (hub / f"models--{org}--{repo}").is_dir()

# winget package IDs (Windows)
WINGET_PACKAGES = {
    "python": "Python.Python.3.11",
    "docker": "Docker.DockerDesktop",
    "ollama": "Ollama.Ollama",
    "cloudflared": "Cloudflare.cloudflared",
    "ffmpeg": "Gyan.FFmpeg",
}

# ── Output helpers ──────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty() or IS_WINDOWS
C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[36m", "x": "\033[0m"}


def _c(color: str, text: str) -> str:
    return f"{C[color]}{text}{C['x']}" if _USE_COLOR else text


def ok(msg: str) -> None:
    print(_c("g", f"  [OK]   {msg}"))


def warn(msg: str) -> None:
    print(_c("y", f"  [WARN] {msg}"))


def err(msg: str) -> None:
    print(_c("r", f"  [DOWN] {msg}"))


def step(title: str) -> None:
    print(f"\n{_c('b', '--- ' + title + ' ---')}")


# ── Command runner ──────────────────────────────────────────────────────

DRY_RUN = False


def run(cmd, check: bool = True, capture: bool = False, cwd=None, input_text=None):
    """Run a command; in dry-run mode, print it instead of executing."""
    printable = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else cmd
    print(f"    $ {printable}"[:200])
    if DRY_RUN:
        return None
    return subprocess.run(
        cmd, check=check, capture_output=capture, cwd=cwd, input=input_text,
        shell=isinstance(cmd, str),
    )


def which(name: str) -> bool:
    return shutil.which(name) is not None


def _http_get(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.7)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(desc: str, fn, tries: int, delay: float, ok_msg: str) -> bool:
    if DRY_RUN:
        ok(f"{ok_msg} (dry-run)")
        return True
    for i in range(tries):
        if fn():
            ok(ok_msg)
            return True
        print(f"    waiting for {desc}... ({i + 1}/{tries})")
        time.sleep(delay)
    return False


# ── Phase B: pre-req checks (never change anything) ─────────────────────

def check_python() -> bool:
    py = shutil.which("python") or shutil.which("python3")
    if not py:
        err("Python not found")
        return False
    try:
        r = subprocess.run([py, "--version"], capture_output=True, text=True)
        ver = r.stdout.strip()
    except Exception:
        err("Python check failed")
        return False
    if "3.11" in ver:
        ok(f"Python {ver} ({py})")
        return True
    warn(f"Python {ver} — expected 3.11.x (app requires 3.11, <=3.12)")
    return True


def check_docker() -> bool:
    if not which("docker"):
        err("docker CLI not found")
        return False
    daemon_ok = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    if daemon_ok:
        ok("Docker daemon running")
    else:
        warn("Docker CLI present but daemon NOT running")
        return False
    compose_ok = subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0
    if compose_ok:
        ok("docker compose plugin present")
    else:
        warn("docker compose plugin missing")
    return daemon_ok and compose_ok


def _ollama_models() -> list:
    data = _http_get(f"{OLLAMA_API}/api/tags")
    if data is None:
        return []
    try:
        return [m.get("name", "") for m in json.loads(data).get("models", [])]
    except Exception:
        return []


def _missing_models(models: list) -> list:
    """Tags may carry suffixes (nomic-embed-text:latest) — match by prefix."""
    return [
        m for m in REQUIRED_MODELS
        if not any(name == m or name.startswith(m + ":") for name in models)
    ]


def _mlx_models_served() -> list:
    data = _http_get(f"{MLX_BASE_URL}/v1/models")
    if data is None:
        return []
    try:
        return [m.get("id", "") for m in json.loads(data).get("data", [])]
    except Exception:
        return []


def check_llm() -> bool:
    """Verify the active LLM backend (MLX on Apple Silicon, Ollama elsewhere)."""
    if _llm_provider() == "mlx":
        if not _hf_model_cached(MLX_MODEL):
            err(f"MLX model not downloaded: {MLX_MODEL} (install phase pulls it)")
            return False
        if not _port_listening(MLX_PORT):
            warn(f"MLX model cached but server not serving on :{MLX_PORT}")
            return False
        served = _mlx_models_served()
        if served and not any(m.startswith(MLX_MODEL) for m in served):
            warn(f"MLX server serving different model: {served[0]}")
            return False
        ok(f"MLX server up on :{MLX_PORT} serving {MLX_MODEL}")
        return True

    if not which("ollama"):
        err("ollama binary not found")
        return False
    if not _port_listening(11434):
        warn("Ollama installed but not serving on :11434")
        return False
    models = _ollama_models()
    missing = _missing_models(models)
    if missing:
        # /api/tags can return a transient partial list right after a
        # restart — re-query once before warning.
        time.sleep(2)
        models = _ollama_models()
        missing = _missing_models(models)
    if missing:
        warn(f"Ollama up, missing models: {missing}")
        return False
    ok(f"Ollama up with {len(models)} models (required present)")
    return True


def check_cloudflared() -> bool:
    if which("cloudflared"):
        ok("cloudflared present")
        return True
    err("cloudflared not found")
    return False


def check_ffmpeg() -> bool:
    if which("ffmpeg"):
        ok("ffmpeg present")
        return True
    warn("ffmpeg not found (audio conversion may fail)")
    return False


def check_gpu() -> None:
    if which("nvidia-smi"):
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok(f"GPU: {r.stdout.strip().replace(chr(10), ' | ')}")
        else:
            warn("nvidia-smi present but query failed")
        return
    if IS_APPLE_SILICON:
        try:
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            mem_gb = int(r.stdout.strip()) / (1024**3)
            ok(f"Apple Silicon (Metal/MLX): {mem_gb:.0f} GB unified memory")
        except Exception:
            ok("Apple Silicon (Metal/MLX) detected")
        return
    warn("No NVIDIA GPU detected — STT/TTS will fall back to CPU (slow)")


def check_env_file() -> bool:
    env = ROOT / ".env"
    if env.is_file():
        ok(".env present")
        return True
    warn(".env missing — will be created from .env.example")
    return False


def check_venv() -> bool:
    if VENV_PY.is_file():
        ok(f"venv present ({VENV_PY.name})")
        return True
    warn("no project venv — dependencies will be installed into .venv/")
    return False


def check_rag_store() -> bool:
    store = ROOT / "chroma_local_db"
    if store.is_dir() and any(store.iterdir()):
        ok("RAG vector store present (chroma_local_db/)")
        return True
    warn("RAG vector store missing/empty — will be rebuilt from content/meridian")
    return False


def check_ports() -> None:
    ports = [("8000 (FastAPI)", 8000), ("8501 (Chatbot)", 8501), ("8502 (Dashboard)", 8502)]
    if _llm_provider() == "mlx":
        ports.append((f"{MLX_PORT} (MLX LLM)", MLX_PORT))
    for name, port in ports:
        if _port_listening(port):
            warn(f"port {port} in use ({name}) — may already be running")
        else:
            print(f"    port {port} free ({name})")


# ── Phase C: install missing (idempotent) ───────────────────────────────

def _winget(pkg: str) -> bool:
    r = run([
        "winget", "install", "--id", pkg, "-e",
        "--silent", "--accept-package-agreements", "--accept-source-agreements",
    ], check=False)
    return r is None or r.returncode == 0  # None in dry-run


def install_windows() -> None:
    step("Phase C — installing missing software (Windows / winget)")
    if not which("winget"):
        err("winget not found — install App Installer from the Microsoft Store first")
        return
    if not check_python():
        if _winget(WINGET_PACKAGES["python"]):
            warn("Python 3.11 installed — PATH refresh needs a new shell; "
                 "re-run the .bat shortcut to continue")
        return
    if not check_docker():
        if _winget(WINGET_PACKAGES["docker"]):
            warn("Docker Desktop installed — starting it now (first start may take minutes)")
    if not which("ollama"):
        _winget(WINGET_PACKAGES["ollama"])
    if not which("cloudflared"):
        _winget(WINGET_PACKAGES["cloudflared"])
    if not which("ffmpeg"):
        _winget(WINGET_PACKAGES["ffmpeg"])


def install_macos() -> None:
    step("Phase C — installing missing software (macOS / Homebrew)")
    if not which("brew"):
        warn("Homebrew missing — install with:")
        print('    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        return
    # Note: no ollama on macOS — the LLM runs on Apple MLX (mlx-lm,
    # installed via pip into .venv by ensure_venv/requirements markers).
    for pkg in ("python@3.11", "cloudflared", "ffmpeg"):
        if not which(pkg):
            run(["brew", "install", pkg])
    if not which("docker"):
        run(["brew", "install", "--cask", "docker"])
        run(["open", "-a", "Docker"])
        warn("Docker Desktop installed — wait for the daemon before starting services")


def install_linux() -> None:
    step("Phase C — installing missing software (Linux / apt)")
    if os.geteuid() != 0:
        err("Linux installs require root — run with sudo")
        sys.exit(1)
    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "-qq",
         "python3.11", "python3.11-venv", "ffmpeg", "curl", "git",
         "build-essential", "libssl-dev", "libffi-dev", "python3-dev"])
    if not which("docker"):
        run(["curl", "-fsSL", "https://get.docker.com", "-o", "/tmp/get-docker.sh"])
        run(["sh", "/tmp/get-docker.sh"])
        warn("Docker installed via get.docker.com — compose plugin included")
        if not which("docker"):
            err("docker still not on PATH — open a new shell and re-run")
            sys.exit(1)
    if not which("ollama"):
        run(["curl", "-fsSL", "https://ollama.com/install.sh", "-o", "/tmp/install-ollama.sh"])
        run(["sh", "/tmp/install-ollama.sh"])
    if not which("cloudflared"):
        # Official Cloudflare apt repository
        run(["bash", "-c",
             "curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | "
             "gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-main.gpg; "
             "echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
             "https://pkg.cloudflare.com/cloudflared any main' "
             "> /etc/apt/sources.list.d/cloudflared.list; apt-get update -qq; "
             "apt-get install -y -qq cloudflared"])


def ensure_docker_daemon() -> bool:
    if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return True
    if IS_WINDOWS and Path(DOCKER_DESKTOP_WIN).is_file():
        print("    starting Docker Desktop...")
        subprocess.Popen([DOCKER_DESKTOP_WIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_LINUX:
        run(["systemctl", "start", "docker"], check=False)
    return _wait_for("docker daemon", lambda: subprocess.run(
        ["docker", "info"], capture_output=True).returncode == 0,
        60, 2, "Docker daemon ready")


def ensure_venv() -> None:
    if VENV_PY.is_file():
        ok("venv already present — skipping creation")
        return
    step("Creating .venv + installing pinned dependencies")
    py = shutil.which("python") or shutil.which("python3")
    run([py, "-m", "venv", str(ROOT / ".venv")])
    pip = str(VENV_PY) + " -m pip"
    run(pip + " install --upgrade pip setuptools wheel", shell=True)
    if IS_WINDOWS and which("nvidia-smi"):
        # GPU Windows box: install the CUDA torch build the verified env used.
        run(pip + " install torch==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124",
            shell=True, check=False)
    run(pip + " install -r " + str(ROOT / "requirements.txt"), shell=True)
    ok("dependencies installed")


def ensure_env_file() -> None:
    env = ROOT / ".env"
    if DRY_RUN:
        if not env.is_file():
            print("    $ copy .env.example -> .env")
        return
    if not env.is_file():
        example = ROOT / ".env.example"
        if example.is_file():
            shutil.copyfile(example, env)
            ok(".env created from .env.example — fill in TWILIO_*/DATABASE_URL as needed")
        else:
            warn(".env.example missing — skipping .env creation")
        return
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "DATABASE_URL"):
        if f"{key}=" not in env.read_text(encoding="utf-8", errors="replace"):
            warn(f".env missing {key} — related features will degrade")


def ensure_llm_up() -> bool:
    """Start the LLM backend for this platform (MLX server or Ollama)."""
    if _llm_provider() == "mlx":
        return _ensure_mlx_up()
    return _ensure_ollama_up()


def _ensure_mlx_up() -> bool:
    if _port_listening(MLX_PORT):
        return True
    if DRY_RUN:
        print(f"    $ start mlx_lm.server on :{MLX_PORT} (model {MLX_MODEL})")
        return True
    if not VENV_PY.is_file():
        warn("no venv — cannot start the MLX server; run full bootstrap first")
        return False
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    _detach(
        [str(VENV_PY), "-m", "mlx_lm.server", "--model", MLX_MODEL,
         "--host", "127.0.0.1", "--port", str(MLX_PORT)],
        logs / "mlx_server.log",
    )
    # First load of a 14B model can take well over a minute.
    return _wait_for(
        "MLX server", lambda: _http_get(f"{MLX_BASE_URL}/v1/models") is not None,
        90, 2, f"MLX server up on :{MLX_PORT} serving {MLX_MODEL}")


def _ensure_ollama_up() -> bool:
    if _port_listening(11434):
        return True
    if DRY_RUN:
        print("    $ (start ollama service)")
        return True
    if IS_MACOS:
        run(["brew", "services", "start", "ollama"], check=False)
    elif IS_LINUX:
        run(["systemctl", "enable", "--now", "ollama"], check=False)
    elif IS_WINDOWS:
        exe = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"))
        if exe.is_file():
            print("    launching Ollama app...")
            subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            warn("Ollama app not found at the default path — start it manually")
    return _wait_for("Ollama API", lambda: _port_listening(11434), 30, 2, "Ollama serving")


def ensure_models() -> None:
    if _llm_provider() == "mlx":
        step("Ensuring MLX models (HuggingFace downloads)")
        if not VENV_PY.is_file():
            warn("no venv — cannot download MLX models; run full bootstrap first")
            return
        for model_id in (MLX_MODEL, MLX_EMBED_MODEL):
            if _hf_model_cached(model_id):
                ok(f"model cached: {model_id}")
                continue
            print(f"    downloading {model_id} (one-time, several GB)...")
            run([str(VENV_PY), "-c",
                 f"from huggingface_hub import snapshot_download; "
                 f"snapshot_download('{model_id}')"])
            ok(f"downloaded {model_id}")
        return

    models = _ollama_models()
    missing = _missing_models(models)
    for m in REQUIRED_MODELS:
        if m not in missing:
            ok(f"model present: {m}")
        else:
            print(f"    pulling {m} (one-time, several GB)...")
            run(["ollama", "pull", m])
            ok(f"pulled {m}")


def ensure_postgres() -> bool:
    healthy = subprocess.run(
        ["docker", "exec", "elearning-postgres", "pg_isready", "-U", "elearning", "-d", "admissions"],
        capture_output=True,
    ).returncode == 0
    if healthy:
        ok("PostgreSQL accepting connections")
        return True
    print("    docker compose up -d postgres")
    run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "up", "-d", "postgres"])
    return _wait_for("PostgreSQL", lambda: subprocess.run(
        ["docker", "exec", "elearning-postgres", "pg_isready", "-U", "elearning", "-d", "admissions"],
        capture_output=True).returncode == 0,
        30, 2, "PostgreSQL ready")


def ensure_rag_store() -> None:
    store = ROOT / "chroma_local_db"
    if store.is_dir() and any(store.iterdir()):
        ok("RAG store present — skip rebuild")
        return
    if not VENV_PY.is_file():
        warn("no venv — cannot rebuild store; run full bootstrap first")
        return
    step("Rebuilding RAG vector store (content/meridian -> chroma_local_db)")
    run([str(VENV_PY), str(ROOT / "scripts" / "rebuild_rag_index.py")])
    ok("RAG store rebuilt")


def ensure_demo_data(with_demo_data: bool) -> None:
    if not with_demo_data:
        return
    step("Demo data (opt-in --with-demo-data)")
    if DRY_RUN:
        print("    $ (check leads count == 0, then seed_demo_data.py)")
        return
    check_sql = (
        "import psycopg2, os; from dotenv import load_dotenv; load_dotenv(); "
        "c=psycopg2.connect(os.environ.get('DATABASE_URL','postgresql://elearning:"
        "elearning_secret@localhost:5432/admissions')); cur=c.cursor(); "
        "cur.execute('SELECT COUNT(*) FROM leads'); print(cur.fetchone()[0])"
    )
    r = subprocess.run([str(VENV_PY), "-c", check_sql], capture_output=True, text=True, cwd=ROOT)
    count = r.stdout.strip()
    if count != "0":
        warn(f"leads table has {count} rows — refusing to seed (destructive). "
             "Run scripts/seed_demo_data.py manually if you are sure.")
        return
    run([str(VENV_PY), str(ROOT / "scripts" / "seed_demo_data.py")])
    ok("demo data seeded (14 courses, 12 leads, 8 conversations)")


# ── Phase D+E: ordered startup ──────────────────────────────────────────

def start_windows(args) -> None:
    step("Phase D — starting services (delegated to start_services.ps1)")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(ROOT / "start_services.ps1")]
    if args.with_streamlit:
        cmd.append("-WithStreamlit")
    if args.named_tunnel:
        cmd.append("-NamedTunnel")
    if DRY_RUN:
        print(f"    $ {' '.join(cmd)}")
        return
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        err("start_services.ps1 exited non-zero")
        sys.exit(1)
    if args.with_streamlit:
        step("Restoring per-port tunnels (check_and_tunnel.ps1)")
        cmd2 = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(ROOT / "check_and_tunnel.ps1")]
        if DRY_RUN:
            print(f"    $ {' '.join(cmd2)}")
            return
        subprocess.run(cmd2, cwd=ROOT)


def _detach(cmd, log_path: Path, cwd=ROOT):
    """Start a background process whose stdout/stderr go to log_path."""
    print(f"    $ {' '.join(map(str, cmd))}  > {log_path}")
    if DRY_RUN:
        return
    f = open(log_path, "ab")
    subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=f, start_new_session=True)
    f.close()


def _prewarm_llm() -> None:
    """Keep the LLM resident in memory before the first real call (PS1 parity)."""
    if DRY_RUN:
        print("    $ (pre-warm LLM)")
        return
    if _llm_provider() == "mlx":
        body = json.dumps({
            "model": MLX_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }).encode()
        req = urllib.request.Request(
            f"{MLX_BASE_URL}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120):
                ok(f"MLX model pre-warmed ({MLX_MODEL})")
        except Exception:
            warn("MLX pre-warm request failed")
        return

    models = _ollama_models()
    if not models:
        warn("No Ollama models found — run: ollama pull qwen2.5:7b")
        return
    for model in models:
        body = json.dumps({
            "model": model, "prompt": "ping", "keep_alive": "24h", "max_tokens": 1,
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_API}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120):
                ok(f"pre-warmed {model} (keep_alive=24h)")
        except Exception:
            warn(f"pre-warm failed for {model}")


def start_unix(args) -> None:
    step("Phase D — starting services (native orchestration)")
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    py = str(VENV_PY)

    # 1. FastAPI
    if not _port_listening(8000):
        _detach([py, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                logs / "fastapi.log")
    _wait_for("FastAPI", lambda: _port_listening(8000), 90, 2, "FastAPI up on :8000")

    # 1b. LLM pre-warm (MLX chat or Ollama keep_alive=24h, PS1 parity)
    _prewarm_llm()

    # 2. Cloudflare tunnel for FastAPI
    tunnel_log = logs / "cloudflared_8000.log"
    if not _read_tunnel_host(ROOT / ".whatsapp_tunnel"):
        _detach(["cloudflared", "tunnel", "--url", "http://localhost:8000"], tunnel_log)
        host = None
        if not DRY_RUN:
            for _ in range(25):
                time.sleep(2)
                host = _parse_tunnel_url(tunnel_log)
                if host:
                    break
        if host:
            (ROOT / ".whatsapp_tunnel").write_text(host)
            ok(f"FastAPI tunnel: {host}")
        else:
            warn("could not capture tunnel URL — check logs/cloudflared_8000.log")

    # 3. Twilio webhooks (voice + status callback)
    host = _read_tunnel_host(ROOT / ".whatsapp_tunnel")
    if host:
        step("Phase E — updating Twilio webhooks")
        run([py, str(ROOT / "scripts" / "update_twilio_webhook.py"), host])

    # 4. Streamlit apps + per-port tunnels
    if args.with_streamlit:
        if not _port_listening(8501):
            _detach(["streamlit", "run", "app.py", "--server.port", "8501", "--server.headless", "true"],
                    logs / "streamlit.log")
        if not _port_listening(8502):
            _detach(["streamlit", "run", "dashboard.py", "--server.port", "8502", "--server.headless", "true"],
                    logs / "dashboard.log")
        _wait_for("Streamlit", lambda: _port_listening(8501) and _port_listening(8502), 30, 2,
                  "Streamlit apps up on :8501/:8502")
        for port, cache in ((8501, ".tunnel_8501"), (8502, ".tunnel_8502")):
            log_f = logs / f"cloudflared_{port}.log"
            _detach(["cloudflared", "tunnel", "--url", f"http://localhost:{port}"], log_f)
            if DRY_RUN:
                continue
            for _ in range(25):
                time.sleep(2)
                h = _parse_tunnel_url(log_f)
                if h:
                    (ROOT / cache).write_text(h)
                    ok(f"port {port} tunnel: {h}")
                    break


def _parse_tunnel_url(log_path: Path):
    if not log_path.is_file():
        return None
    m = re.search(r"https://([a-zA-Z0-9\-]+\.trycloudflare\.com)", log_path.read_text(errors="replace"))
    return m.group(1) if m else None


def _read_tunnel_host(cache_file: Path):
    if cache_file.is_file():
        h = cache_file.read_text().strip()
        if h and _http_get(f"https://{h}/", timeout=8) is not None:
            return h
    return None


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="One-click pre-req check, install, ordered start")
    ap.add_argument("--check-only", action="store_true", help="verify pre-reqs and exit (no changes)")
    ap.add_argument("--dry-run", action="store_true", help="print every action without executing")
    ap.add_argument("--skip-install", action="store_true", help="skip install phase (launch only)")
    ap.add_argument("--with-streamlit", action="store_true", help="also start chatbot (8501) + dashboard (8502)")
    ap.add_argument("--with-demo-data", action="store_true", help="seed demo data ONLY when the DB is empty")
    ap.add_argument("--named-tunnel", action="store_true", help="use the named Cloudflare tunnel (Windows)")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    print(_c("b", "University Admissions — One-Click Bootstrap"))
    print(f"Platform: {platform.system()} {platform.machine()}  (dry-run: {DRY_RUN})")

    # ── Phase B: check ──
    step("Phase B — pre-requisite check")
    check_python()
    check_docker()
    check_llm()
    check_cloudflared()
    check_ffmpeg()
    check_gpu()
    check_env_file()
    check_venv()
    check_rag_store()
    check_ports()

    if args.check_only:
        print("\nCheck-only run complete — nothing was changed.")
        return 0

    # ── Phase C: install ──
    if not args.skip_install:
        if IS_WINDOWS:
            install_windows()
        elif IS_MACOS:
            install_macos()
        elif IS_LINUX:
            install_linux()
        if not DRY_RUN:
            ensure_docker_daemon()
            ensure_venv()
            ensure_env_file()
            ensure_llm_up()
            ensure_models()
            ensure_postgres()
            ensure_rag_store()
            ensure_demo_data(args.with_demo_data)
    else:
        step("Phase C skipped (--skip-install)")

    # ── Phase D: start ──
    if IS_WINDOWS:
        start_windows(args)
    else:
        ensure_docker_daemon()
        ensure_postgres()
        ensure_llm_up()
        start_unix(args)

    print("\n" + _c("g", "Bootstrap complete."))
    if DRY_RUN:
        print("(dry-run — no changes were made)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
