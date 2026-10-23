#!/usr/bin/env python3
"""Validate the frozen UniversityBotDemoAPI demo against the development tree.

Run with the development virtual environment:

    python scripts/validate_demo.py
    python scripts/validate_demo.py --check-http
    python scripts/validate_demo.py --report DEMO_FREEZE_VALIDATION.md

Exit code is 1 only when a check FAILS. BLOCKED checks (for example, missing
demo API keys or a stopped stack) are reported honestly and do not fail the
validation. This script is development-only and is never copied into the demo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

DEV_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEMO = Path(r"D:\project\UniversityBotDemoAPI")

#: Keys that are expected to differ between development and the demo.
ALLOWED_DIFFERENCES = {
    "DATABASE_URL",
    "FASTAPI_HOST",       # dev 0.0.0.0, demo 127.0.0.1
    "BACKEND_BASE",       # demo-only helper value
    "LLM_API_KEY",
    "SARVAM_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_WHATSAPP_NUMBER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASS",
}

#: Secrets that are allowed to be blank in the demo until the operator fills them.
OPTIONAL_IN_DEMO = {
    "LLM_API_KEY",
    "SARVAM_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_WHATSAPP_NUMBER",
}

FORBIDDEN_DIRS = (
    "tests",
    "doc",
    "models",
    "tts_service",
    "chroma_local_db",
    "chroma_local_db_pre_c4",
)

REQUIRED_FILES = (
    "app/main.py",
    "app.py",
    "dashboard.py",
    "requirements.txt",
    "content/meridian/meridian_knowledge_base.md",
    "README_DEMO.md",
    ".demo_directory",
    ".demo_manifest.json",
)

REQUIRED_TABLES = {"leads", "conversations", "courses"}


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    def ok(self, name: str, detail: str = "") -> None:
        self.add("PASS", name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self.add("FAIL", name, detail)

    def blocked(self, name: str, detail: str = "") -> None:
        self.add("BLOCKED", name, detail)

    def warn(self, name: str, detail: str = "") -> None:
        self.add("WARN", name, detail)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status, _, _ in self.rows:
            out[status] = out.get(status, 0) + 1
        return out

    def has_failures(self) -> bool:
        return any(status == "FAIL" for status, _, _ in self.rows)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_get(url: str, timeout: float = 5.0) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(200).decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def validate(
    demo: Path,
    *,
    check_http: bool,
    fastapi_url: str,
    streamlit_url: str,
    crm_url: str,
) -> Results:
    results = Results()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    if not demo.is_dir():
        results.fail("demo directory", f"missing: {demo}")
        return results
    results.ok("demo directory", str(demo))

    for name in REQUIRED_FILES:
        path = demo / name
        if path.is_file():
            results.ok(f"required file: {name}")
        else:
            results.fail(f"required file: {name}", "missing")

    for name in FORBIDDEN_DIRS:
        if (demo / name).exists():
            results.fail(f"forbidden path absent: {name}/", "present")
        else:
            results.ok(f"forbidden path absent: {name}/")

    secret_like = []
    skip_parts = {".venv", "data", "logs", ".git", ".demo_tmp"}
    for path in demo.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(demo)
        if any(part in skip_parts for part in relative.parts):
            continue
        if path.name.startswith(".env.") or path.suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
            secret_like.append(relative.as_posix())
    if secret_like:
        results.fail("no secret-like files", ", ".join(sorted(secret_like)[:10]))
    else:
        results.ok("no secret-like files")

    markdown = sorted(path.name for path in demo.glob("*.md"))
    if markdown == ["README_DEMO.md"]:
        results.ok("only one markdown file", "README_DEMO.md")
    else:
        results.fail("only one markdown file", ", ".join(markdown))

    # ------------------------------------------------------------------
    # Manifest integrity
    # ------------------------------------------------------------------
    manifest_path = demo / ".demo_manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            results.ok("manifest readable")
        except (OSError, json.JSONDecodeError) as exc:
            results.fail("manifest readable", str(exc))

    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    if files:
        missing = []
        mismatched = []
        for rel, meta in files.items():
            path = demo / rel
            if not path.is_file():
                missing.append(rel)
            elif meta.get("sha256") and sha256(path) != meta["sha256"]:
                mismatched.append(rel)
        if missing:
            results.fail("manifest files present", f"{len(missing)} missing, e.g. {missing[:5]}")
        else:
            results.ok("manifest files present", f"{len(files)} files")
        if mismatched:
            results.fail("manifest hashes match", f"{len(mismatched)} mismatched, e.g. {mismatched[:5]}")
        else:
            results.ok("manifest hashes match", f"{len(files)} files")
    else:
        results.fail("manifest file map", "empty or missing")

    dev_head = subprocess.run(
        ["git", "-C", str(DEV_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if dev_head and manifest.get("source_commit") == dev_head:
        results.ok("source commit recorded", dev_head)
    elif dev_head:
        results.fail("source commit recorded", f"manifest={manifest.get('source_commit')!r} dev={dev_head!r}")
    else:
        results.blocked("source commit recorded", "development git HEAD unavailable")

    if manifest.get("source_tracked_dirty"):
        results.warn(
            "development tree clean at sync",
            "manifest says tracked development files were modified; commit before the next sync",
        )
    else:
        results.ok("development tree clean at sync")

    # ------------------------------------------------------------------
    # Environment parity
    # ------------------------------------------------------------------
    dev_env = parse_env(DEV_ROOT / ".env")
    demo_env_path = demo / ".env"
    demo_env = parse_env(demo_env_path)

    if not demo_env_path.is_file():
        results.blocked("demo .env", "not created yet; run demo_setup.ps1")
    else:
        results.ok("demo .env present")
        dsn = demo_env.get("DATABASE_URL", "")
        parsed = urlparse(dsn)
        db_name = (parsed.path or "").lstrip("/") if parsed.scheme.startswith("postgres") else ""
        if db_name == "admissions_demo":
            results.ok("demo database name", "admissions_demo")
        else:
            results.fail("demo database name", f"found {db_name!r}")

        if demo_env.get("CRM_ENABLED", "").strip().lower() == "true":
            results.ok("CRM_ENABLED", "true")
        else:
            results.fail("CRM_ENABLED", f"found {demo_env.get('CRM_ENABLED')!r}")

        mismatched: list[str] = []
        missing_keys: list[str] = []
        for key, dev_value in sorted(dev_env.items()):
            if key in ALLOWED_DIFFERENCES:
                continue
            if key not in demo_env:
                if dev_value:
                    missing_keys.append(key)
                continue
            if dev_env.get(key, "") != demo_env.get(key, ""):
                mismatched.append(key)

        if missing_keys:
            results.fail("runtime env keys present", "missing: " + ", ".join(missing_keys))
        else:
            results.ok("runtime env keys present", "all non-allowed development keys are present")
        if mismatched:
            results.fail("runtime env parity", "different: " + ", ".join(mismatched))
        else:
            results.ok("runtime env parity", "all non-allowed values match development")
        empty_optional = [key for key in sorted(OPTIONAL_IN_DEMO) if not demo_env.get(key)]
        if empty_optional:
            results.blocked("demo secrets filled", "empty: " + ", ".join(empty_optional))
        else:
            results.ok("demo secrets filled")

        if demo_env.get("LLM_API_KEY") and demo_env.get("SARVAM_API_KEY"):
            results.ok("LLM and Sarvam keys configured")
        else:
            results.blocked(
                "LLM and Sarvam keys configured",
                "chat/voice checks need demo LLM_API_KEY and SARVAM_API_KEY",
            )
        twilio_keys = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "TWILIO_WHATSAPP_NUMBER")
        if all(demo_env.get(key) for key in twilio_keys):
            results.ok("Twilio credentials configured")
        else:
            results.blocked(
                "Twilio credentials configured",
                "voice/WhatsApp checks need all four TWILIO_* values",
            )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    if demo_env.get("DATABASE_URL"):
        try:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(demo_env["DATABASE_URL"], connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_database()")
                    current = cur.fetchone()[0]
                    cur.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                    tables = {row[0] for row in cur.fetchall()}
                if current == "admissions_demo":
                    results.ok("database connection", "current_database=admissions_demo")
                else:
                    results.fail("database connection", f"current_database={current!r}")
                missing_tables = REQUIRED_TABLES - tables
                if missing_tables:
                    results.fail("database schema", "missing tables: " + ", ".join(sorted(missing_tables)))
                else:
                    results.ok("database schema", "leads, conversations, courses present")
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            results.blocked("database connection", str(exc))
    else:
        results.blocked("database connection", "DATABASE_URL not available")

    # ------------------------------------------------------------------
    # Demo venv import
    # ------------------------------------------------------------------
    venv_python = demo / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        pip_show = subprocess.run(
            [str(venv_python), "-m", "pip", "show", "fastapi"],
            cwd=demo,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if pip_show.returncode != 0:
            results.blocked(
                "demo venv imports app.main",
                "the demo venv exists but dependencies are not installed yet (fastapi missing)",
            )
        else:
            psycheck = subprocess.run(
                [str(venv_python), "-c", "import psycopg2; print('psycopg2 OK')"],
                cwd=demo,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if psycheck.returncode != 0 and "Application Control policy" in (psycheck.stderr or ""):
                results.blocked(
                    "demo venv imports app.main",
                    "Smart App Control blocks psycopg2 in the demo venv; use the approved interpreter workaround or add a policy exclusion",
                )
            else:
                proc = subprocess.run(
                    [str(venv_python), "-c", "import app.main; print('import OK')"],
                    cwd=demo,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if proc.returncode == 0 and "import OK" in proc.stdout:
                    results.ok("demo venv imports app.main")
                else:
                    results.fail("demo venv imports app.main", (proc.stderr or proc.stdout).strip()[-500:])
    else:
        results.blocked("demo venv imports app.main", f"{venv_python} not created yet")

    # ------------------------------------------------------------------
    # HTTP checks (optional; requires the demo stack to be running)
    # ------------------------------------------------------------------
    if check_http:
        crm_root = crm_url.rstrip("/")
        for label, url in (
            ("FastAPI health", fastapi_url.rstrip("/") + "/"),
            ("Streamlit health", streamlit_url.rstrip("/") + "/_stcore/health"),
            ("CRM health", crm_root + "/health"),
        ):
            status, body = http_get(url)
            if status is not None and 200 <= status < 400:
                results.ok(label, f"HTTP {status} ({url})")
            elif status is None:
                results.blocked(label, f"{url}: {body[:180]}")
            else:
                results.fail(label, f"HTTP {status} ({url}): {body[:180]}")

        crm_read_url = crm_root + "/admissions"
        status, body = http_get(crm_read_url)
        if status is not None and 200 <= status < 300:
            results.ok("CRM read endpoint", f"HTTP {status} ({crm_read_url})")
        elif status is None:
            results.blocked("CRM read endpoint", f"{crm_read_url}: {body[:180]}")
        else:
            results.fail("CRM read endpoint", f"HTTP {status} ({crm_read_url}): {body[:180]}")
    else:
        results.blocked("HTTP health checks", "run with --check-http while the demo stack is running")

    return results


def render(results: Results) -> str:
    lines = ["# Demo Freeze Validation", ""]
    lines.append(f"Generated: {__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("| Status | Check | Detail |")
    lines.append("|---|---|---|")
    for status, name, detail in results.rows:
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {status} | {name} | {detail} |")
    lines.append("")
    summary = results.summary()
    lines.append("## Summary")
    lines.append("")
    lines.append(", ".join(f"{key}={value}" for key, value in sorted(summary.items())))
    lines.append("")
    if results.has_failures():
        lines.append("Result: **FAIL** — fix the checks marked FAIL.")
    else:
        lines.append("Result: **PASS with blocked checks** — blocked checks require credentials, a running stack, or an external service.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the frozen demo.")
    parser.add_argument("--demo", type=Path, default=DEFAULT_DEMO)
    parser.add_argument("--check-http", action="store_true", help="probe the running demo stack and CRM")
    parser.add_argument("--fastapi-url", default="http://127.0.0.1:8000")
    parser.add_argument("--streamlit-url", default="http://127.0.0.1:8501")
    parser.add_argument("--crm-url", default="http://127.0.0.1:8098")
    parser.add_argument("--report", type=Path, default=None, help="write a markdown report")
    args = parser.parse_args(argv)

    results = validate(
        args.demo.expanduser().resolve(),
        check_http=args.check_http,
        fastapi_url=args.fastapi_url,
        streamlit_url=args.streamlit_url,
        crm_url=args.crm_url,
    )
    text = render(results)
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
        print(f"Report written to {args.report}")
    return 1 if results.has_failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
