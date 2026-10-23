#!/usr/bin/env python3
"""Synchronize the approved development snapshot into the demo directory.

The demo directory is a plain directory; it may later become its own git
repository. This script never copies ``.git``, ``.env``, ``data/``, ``logs/``,
tests, or documentation. The include policy is deny-by-default: only the paths
listed in ``INCLUDE_FILES``, ``INCLUDE_DIRS``, and ``DEMO_TEMPLATES`` are
managed.

Usage::

    python scripts/sync_demo.py --dry-run
    python scripts/sync_demo.py --init --sync
    python scripts/sync_demo.py --sync
    python scripts/sync_demo.py --sync --confirm-deletions
    python scripts/sync_demo.py --audit

``--dry-run`` is the default. ``--sync`` is required for any write.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = Path(r"D:\project\UniversityBotDemoAPI")

PROJECT_NAME = "UniversityBotDemoAPI"
POLICY_VERSION = 1
MANIFEST_VERSION = 1

MANIFEST_NAME = ".demo_manifest.json"
SENTINEL_NAME = ".demo_directory"
VERSION_NAME = "DEMO_VERSION.txt"
LOG_DIR = REPO_ROOT / "logs" / "sync_demo"

#: Explicit files copied to the same relative path in the demo.
INCLUDE_FILES: dict[str, str] = {
    "requirements.txt": "requirements.txt",
    "app.py": "app.py",
    "dashboard.py": "dashboard.py",
    "content/meridian/meridian_knowledge_base.md": "content/meridian/meridian_knowledge_base.md",
    "scripts/build_call_assets.py": "scripts/build_call_assets.py",
    "scripts/seed_demo_data.py": "scripts/seed_demo_data.py",
    "scripts/update_twilio_webhook.py": "scripts/update_twilio_webhook.py",
}

#: Directories copied recursively to the same relative path.
INCLUDE_DIRS: dict[str, str] = {
    "app": "app",
}

#: Demo support files: source template -> demo-root destination.
DEMO_TEMPLATES: dict[str, str] = {
    "scripts/demo/demo_setup.ps1": "demo_setup.ps1",
    "scripts/demo/demo_start.ps1": "demo_start.ps1",
    "scripts/demo/demo_stop.ps1": "demo_stop.ps1",
    "scripts/demo/demo_seed.ps1": "demo_seed.ps1",
    "scripts/demo/demo_twilio.ps1": "demo_twilio.ps1",
    "scripts/demo/env.demo.example": "env.demo.example",
    "scripts/demo/README_DEMO.md": "README_DEMO.md",
    "scripts/demo/gitignore.demo": ".gitignore",
}

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".venv",
    "venv",
    "node_modules",
}

EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db", "Desktop.ini"}

#: Backstop against a secret accidentally landing in a managed directory.
SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "id_rsa*",
    "*.log",
)

#: Paths that are deliberately excluded from the demo and should not be
#: reported as "unclassified" by --audit.
AUDIT_IGNORED_PREFIXES = (
    "tests/",
    "doc/",
    "eval/",
    "tts_service/",
    ".github/",
    ".claude/",
    "environmentsetup/",
    "chroma_local_db",
    "data/",
    "logs/",
    "models/",
    "content/sample_data/",
    ".pytest_cache/",
)

AUDIT_IGNORED_FILES = {
    ".dockerignore",
    ".gitignore",
    ".claudeignore",
    "Dockerfile",
    "docker-compose.yml",
    "pytest.ini",
    "requirements-dev.txt",
    "University_Projwect_v1.ipynb",
    "salesforceAPISchema",
    "salesforcer_api.txt",
    "admissions_bot.py",
    "quick_call.py",
    "run_pipeline_test.py",
    "test_audio_local.py",
    "test_environment.py",
    "test_full_pipeline.py",
    "test_rag_llm.py",
    "test_transport.py",
    "validate_platform_readiness.py",
    "scripts/sync_demo.py",
    "scripts/validate_demo.py",
    "scripts/predeploy.py",
    "scripts/test_foreign_languages.py",
    "scripts/verify_gate3_whatsapp.py",
    "scripts/verify_gate45_voice.py",
    "scripts/verify_gate6_webchat.py",
    "scripts/verify_gate7_status.py",
    "scripts/verify_offer_document_upload.py",
    "scripts/verify_salesforce_api.py",
    "launch.sh",
    "launch.bat",
    "launch_tunnel.bat",
    "launch_Guide.txt",
    "setup.sh",
    "start.sh",
    "stop.sh",
    "start_demo.bat",
    "start_services.ps1",
    "start_services.sh",
    "bootstrap_services.py",
    "bootstrap_services.ps1",
    "bootstrap_services.bat",
    "bootstrap_services.sh",
    "check_and_tunnel.ps1",
    "check_and_tunnel.sh",
    "tunnel_streamlit.ps1",
    "tunnel_streamlit.sh",
    "QUICK_REFERENCE.txt",
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class SyncError(RuntimeError):
    """A validation or policy failure. The CLI exits with code 1."""


@dataclass(frozen=True)
class SyncConfig:
    source: Path
    dest: Path
    include_files: dict[str, str]
    include_dirs: dict[str, str]
    templates: dict[str, str]
    manifest_name: str = MANIFEST_NAME
    sentinel_name: str = SENTINEL_NAME
    version_name: str = VERSION_NAME


@dataclass
class SourceMap:
    files: dict[str, Path]  # demo-relative path -> source path
    covered_source: set[str]  # source-relative paths covered by the policy
    missing: list[str]
    skipped: list[str]


@dataclass
class Entry:
    dest_rel: str
    src_path: Path | None
    src_hash: str | None
    dest_hash: str | None
    prev_hash: str | None
    state: str  # ADD UPD KEEP RESTORE CONFLICT OBSOLETE GONE


@dataclass
class GitInfo:
    source_commit: str | None = None
    source_subject: str | None = None
    source_dirty: bool = False
    demo_is_git: bool = False
    demo_commit: str | None = None
    demo_dirty: bool = False


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    """True for a symlink or a Windows junction/reparse point."""
    try:
        if path.is_symlink():
            return True
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
        flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(attrs & flag)
    except OSError:
        return False


def _looks_secret(rel: str) -> bool:
    name = Path(rel).name.lower()
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in SECRET_FILE_PATTERNS)


def _git(cwd: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _remove_empty_parents(root: Path, start: Path) -> None:
    current = start
    while current != root and _is_within(current, root):
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Reporter:
    def __init__(self, log_path: Path | None, verbose: bool = False, quiet: bool = False) -> None:
        self.log_path = log_path
        self.verbose = verbose
        self.quiet = quiet
        self._lines: list[str] = []

    def line(self, message: str = "") -> None:
        if not self.quiet:
            print(message)
        self._lines.append(message)

    def close(self) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"WARNING: could not write sync log {self.log_path}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Policy and source map
# ---------------------------------------------------------------------------


def build_source_map(cfg: SyncConfig) -> SourceMap:
    """Resolve the policy into a map of demo-relative path -> source path."""
    files: dict[str, Path] = {}
    covered_source: set[str] = set()
    missing: list[str] = []
    skipped: list[str] = []

    def add(dest_rel: str, src_path: Path, src_rel: str) -> None:
        dest_rel = dest_rel.replace("\\", "/")
        if dest_rel in files and files[dest_rel] != src_path:
            raise SyncError(
                f"policy collision: {dest_rel} is produced by both "
                f"{files[dest_rel]} and {src_path}"
            )
        files[dest_rel] = src_path
        covered_source.add(src_rel.replace("\\", "/"))

    for src_rel, dest_rel in sorted(cfg.include_files.items()):
        src_path = cfg.source / src_rel
        if not src_path.is_file():
            missing.append(src_rel)
            continue
        if _looks_secret(src_rel):
            skipped.append(src_rel)
            continue
        add(dest_rel, src_path, src_rel)

    for src_dir, dest_dir in sorted(cfg.include_dirs.items()):
        root = cfg.source / src_dir
        if not root.is_dir():
            missing.append(src_dir + "/")
            continue
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
                continue
            if path.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
                continue
            if path.name in EXCLUDE_FILE_NAMES:
                continue
            if _is_reparse_point(path):
                skipped.append(path.relative_to(cfg.source).as_posix())
                continue
            if not path.is_file():
                skipped.append(path.relative_to(cfg.source).as_posix())
                continue
            src_rel = path.relative_to(cfg.source).as_posix()
            if _looks_secret(src_rel):
                skipped.append(src_rel)
                continue
            dest_rel = (Path(dest_dir) / rel).as_posix()
            add(dest_rel, path, src_rel)

    for src_rel, dest_rel in sorted(cfg.templates.items()):
        src_path = cfg.source / src_rel
        if not src_path.is_file():
            missing.append(src_rel)
            continue
        add(dest_rel, src_path, src_rel)

    return SourceMap(files=dict(sorted(files.items())), covered_source=covered_source, missing=missing, skipped=skipped)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_paths(cfg: SyncConfig, *, adopt: bool, for_sync: bool) -> None:
    source = cfg.source.resolve()
    dest = cfg.dest.resolve()

    if not source.is_dir():
        raise SyncError(f"source directory does not exist: {source}")
    for required in ("app/main.py", "requirements.txt", "app.py"):
        if not (source / required).is_file():
            raise SyncError(f"source does not look like universityDemo: missing {required}")

    if dest == source:
        raise SyncError("target and source are the same directory")
    if _is_within(dest, source):
        raise SyncError("target is inside the source directory")
    if _is_within(source, dest):
        raise SyncError("source is inside the target directory")
    if dest.parent == dest:
        raise SyncError("target cannot be a drive root")
    if dest == Path.home().resolve():
        raise SyncError("target cannot be the user's home directory")
    if dest.exists() and not dest.is_dir():
        raise SyncError(f"target exists but is not a directory: {dest}")

    if dest.exists() and any(dest.iterdir()):
        sentinel = dest / cfg.sentinel_name
        if sentinel.exists():
            try:
                payload = json.loads(sentinel.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SyncError(f"cannot read {sentinel}: {exc}") from exc
            if payload.get("project") != PROJECT_NAME:
                raise SyncError(
                    f"{sentinel} belongs to {payload.get('project')!r}, not {PROJECT_NAME!r}"
                )
            if payload.get("policy_version") != POLICY_VERSION:
                raise SyncError(
                    f"{sentinel} has policy_version {payload.get('policy_version')!r}; "
                    f"this script expects {POLICY_VERSION}"
                )
        elif not adopt:
            raise SyncError(
                f"target is not empty and has no {cfg.sentinel_name}; "
                "refusing to write. Use --adopt only if this is definitely the demo directory."
            )

    if for_sync and not dest.exists():
        # The first --init --sync creates it. --sync without --init is refused
        # later so the initial population is deliberate.
        pass


def check_git_state(
    cfg: SyncConfig,
    *,
    allow_dirty_source: bool,
    allow_dirty_demo: bool,
    for_sync: bool,
) -> GitInfo:
    info = GitInfo()

    if (cfg.source / ".git").exists():
        info.source_commit = _git(cfg.source, "rev-parse", "HEAD")
        info.source_subject = _git(cfg.source, "log", "-1", "--format=%s")
        dirty = _git(cfg.source, "status", "--porcelain", "--untracked-files=no")
        info.source_dirty = bool(dirty)
        if info.source_dirty and for_sync and not allow_dirty_source:
            raise SyncError(
                "development working tree has tracked changes; commit them "
                "or pass --allow-dirty-source"
            )
    else:
        print("WARNING: source is not a git repository; the source commit cannot be recorded.")

    if (cfg.dest / ".git").exists():
        info.demo_is_git = True
        info.demo_commit = _git(cfg.dest, "rev-parse", "HEAD")
        dirty = _git(cfg.dest, "status", "--porcelain")
        info.demo_dirty = bool(dirty)
        if info.demo_dirty and for_sync and not allow_dirty_demo:
            raise SyncError(
                "demo git working tree is dirty; commit the old demo state "
                "or pass --allow-dirty-demo"
            )

    return info


# ---------------------------------------------------------------------------
# Manifest and classification
# ---------------------------------------------------------------------------


def read_manifest(cfg: SyncConfig) -> dict | None:
    path = cfg.dest / cfg.manifest_name
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SyncError(f"manifest {path} is not a JSON object")
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise SyncError(
            f"manifest {path} has manifest_version {payload.get('manifest_version')!r}; "
            f"this script expects {MANIFEST_VERSION}"
        )
    if not isinstance(payload.get("files"), dict):
        raise SyncError(f"manifest {path} has no files map")
    return payload


def classify(cfg: SyncConfig, source_map: SourceMap, manifest: dict | None) -> list[Entry]:
    manifest_files: dict = (manifest or {}).get("files", {})
    entries: list[Entry] = []

    for dest_rel, src_path in source_map.files.items():
        src_hash = _sha256(src_path)
        dst_path = cfg.dest / dest_rel
        dst_hash = _sha256(dst_path) if dst_path.is_file() else None
        prev = manifest_files.get(dest_rel)
        prev_hash = prev.get("sha256") if isinstance(prev, dict) else None

        if dst_hash is None:
            state = "RESTORE" if prev_hash else "ADD"
        elif dst_hash == src_hash:
            state = "KEEP"
        elif prev_hash is None:
            state = "CONFLICT"
        elif dst_hash == prev_hash:
            state = "UPD"
        else:
            state = "CONFLICT"

        entries.append(
            Entry(
                dest_rel=dest_rel,
                src_path=src_path,
                src_hash=src_hash,
                dest_hash=dst_hash,
                prev_hash=prev_hash,
                state=state,
            )
        )

    for dest_rel, prev in sorted(manifest_files.items()):
        if dest_rel in source_map.files:
            continue
        dst_path = cfg.dest / dest_rel
        dst_hash = _sha256(dst_path) if dst_path.is_file() else None
        prev_hash = prev.get("sha256") if isinstance(prev, dict) else None
        state = "OBSOLETE" if dst_hash is not None else "GONE"
        entries.append(
            Entry(
                dest_rel=dest_rel,
                src_path=None,
                src_hash=None,
                dest_hash=dst_hash,
                prev_hash=prev_hash,
                state=state,
            )
        )

    return sorted(entries, key=lambda entry: (entry.state, entry.dest_rel))


def summarize(entries: list[Entry]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for entry in entries:
        summary[entry.state] = summary.get(entry.state, 0) + 1
    return summary


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _plan_mode(entries: list[Entry], *, force: bool, confirm_deletions: bool) -> dict[str, list[Entry]]:
    plan = {
        "copy": [e for e in entries if e.state in ("ADD", "UPD", "RESTORE")],
        "force_copy": [e for e in entries if e.state == "CONFLICT"] if force else [],
        "conflict": [] if force else [e for e in entries if e.state == "CONFLICT"],
        "delete": [],
        "pending": [],
        "gone": [e for e in entries if e.state == "GONE"],
    }

    for entry in entries:
        if entry.state != "OBSOLETE":
            continue
        if not confirm_deletions:
            plan["pending"].append(entry)
        elif entry.dest_hash != entry.prev_hash and not force:
            plan["conflict"].append(entry)
        else:
            plan["delete"].append(entry)

    return plan


def apply_plan(
    cfg: SyncConfig,
    entries: list[Entry],
    manifest: dict | None,
    *,
    force: bool,
    confirm_deletions: bool,
    reporter: Reporter,
) -> tuple[dict[str, dict], dict[str, int], list[Entry], list[Entry]]:
    old_files: dict = (manifest or {}).get("files", {})
    new_files: dict[str, dict] = {path: dict(meta) for path, meta in old_files.items() if isinstance(meta, dict)}

    plan = _plan_mode(entries, force=force, confirm_deletions=confirm_deletions)
    copied = 0
    deleted = 0

    for entry in plan["copy"] + plan["force_copy"]:
        assert entry.src_path is not None and entry.src_hash is not None
        _copy_atomic(entry.src_path, cfg.dest / entry.dest_rel)
        new_files[entry.dest_rel] = {
            "sha256": entry.src_hash,
            "size": entry.src_path.stat().st_size,
        }
        copied += 1
        label = "FORCE-OVERWRITE" if entry.state == "CONFLICT" else entry.state
        reporter.line(f"[{label}] {entry.dest_rel}")

    for entry in plan["conflict"]:
        reporter.line(
            f"[CONFLICT] {entry.dest_rel} "
            "(edited in demo and changed in source; use --force to overwrite)"
        )
        if entry.state == "CONFLICT" and entry.prev_hash is None:
            # Do not track a file we never synced; the next run should still
            # see it as a conflict.
            new_files.pop(entry.dest_rel, None)

    for entry in plan["delete"]:
        dst_path = cfg.dest / entry.dest_rel
        try:
            dst_path.unlink()
        except FileNotFoundError:
            pass
        new_files.pop(entry.dest_rel, None)
        deleted += 1
        reporter.line(f"[DEL] {entry.dest_rel}")
        _remove_empty_parents(cfg.dest, dst_path.parent)

    for entry in plan["pending"]:
        reporter.line(
            f"[OBSOLETE] {entry.dest_rel} "
            "(not deleted without --confirm-deletions)"
        )

    for entry in plan["gone"]:
        new_files.pop(entry.dest_rel, None)
        reporter.line(f"[GONE] {entry.dest_rel} (already absent; removed from manifest)")

    # KEEP entries are not rewritten, but their manifest entries are refreshed.
    for entry in entries:
        if entry.state == "KEEP" and entry.src_path is not None and entry.src_hash is not None:
            new_files[entry.dest_rel] = {
                "sha256": entry.src_hash,
                "size": entry.src_path.stat().st_size,
            }

    return new_files, {"copied": copied, "deleted": deleted}, plan["conflict"], plan["pending"]


# ---------------------------------------------------------------------------
# Manifest / version writers
# ---------------------------------------------------------------------------


def write_sentinel(cfg: SyncConfig) -> None:
    path = cfg.dest / cfg.sentinel_name
    if path.exists():
        return
    _atomic_write_json(
        path,
        {
            "project": PROJECT_NAME,
            "created_by": "scripts/sync_demo.py",
            "created_at_utc": _now_utc(),
            "policy_version": POLICY_VERSION,
        },
    )


def write_manifest(
    cfg: SyncConfig,
    files: dict[str, dict],
    git_info: GitInfo,
    *,
    status: str,
) -> None:
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "policy_version": POLICY_VERSION,
        "generated_at_utc": _now_utc(),
        "source_root": str(cfg.source),
        "source_commit": git_info.source_commit,
        "source_commit_subject": git_info.source_subject,
        "source_tracked_dirty": git_info.source_dirty,
        "target_root": str(cfg.dest),
        "status": status,
        "files": dict(sorted(files.items())),
    }
    _atomic_write_json(cfg.dest / cfg.manifest_name, payload)


def write_version(cfg: SyncConfig, files: dict[str, dict], git_info: GitInfo, *, status: str) -> None:
    commit = git_info.source_commit or "unknown"
    subject = git_info.source_subject or ""
    text = (
        f"{PROJECT_NAME}\n"
        f"Source: universityDemo @ {commit}\n"
        f"Subject: {subject}\n"
        f"Synced: {_now_utc()}\n"
        f"Managed files: {len(files)}\n"
        f"Status: {status}\n"
    )
    _atomic_write_text(cfg.dest / cfg.version_name, text)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(
    reporter: Reporter,
    entries: list[Entry],
    summary: dict[str, int],
    *,
    cfg: SyncConfig,
    mode: str,
    git_info: GitInfo,
    source_map: SourceMap,
) -> None:
    commit = git_info.source_commit or "unknown"
    dirty = " (dirty)" if git_info.source_dirty else " (clean)"
    demo_git = "git repo" if git_info.demo_is_git else "not a git repo"
    reporter.line(f"Source: {cfg.source} @ {commit}{dirty}")
    reporter.line(f"Target: {cfg.dest} ({demo_git})")
    reporter.line(
        f"Policy: {len(source_map.files)} managed files, "
        f"{len(source_map.missing)} missing templates"
    )
    reporter.line(f"Mode: {'SYNC' if mode == 'sync' else 'DRY RUN'}")
    reporter.line("")

    for entry in entries:
        if entry.state == "KEEP" and not reporter.verbose:
            continue
        if entry.state in ("ADD", "UPD", "RESTORE"):
            reporter.line(f"[{entry.state}] {entry.dest_rel}")
        elif entry.state == "CONFLICT":
            reporter.line(f"[CONFLICT] {entry.dest_rel}")
        elif entry.state == "OBSOLETE":
            reporter.line(f"[OBSOLETE] {entry.dest_rel}")
        elif entry.state == "GONE":
            reporter.line(f"[GONE] {entry.dest_rel}")

    reporter.line("")
    if summary:
        parts = ", ".join(f"{key}={summary[key]}" for key in sorted(summary))
        reporter.line(f"Summary: {parts}")
    else:
        reporter.line("Summary: no managed files")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _tracked_files(source: Path) -> list[str]:
    output = _git(source, "ls-files")
    if output is None:
        raise SyncError("--audit requires the development directory to be a git repository")
    return [line for line in output.splitlines() if line.strip()]


def run_audit(cfg: SyncConfig, *, strict: bool, reporter: Reporter) -> int:
    source_map = build_source_map(cfg)
    tracked = _tracked_files(cfg.source)
    unclassified = [path for path in tracked if path not in source_map.covered_source]

    interesting: list[str] = []
    for path in unclassified:
        if path.startswith(AUDIT_IGNORED_PREFIXES):
            continue
        if path in AUDIT_IGNORED_FILES:
            continue
        if path.startswith("scripts/") and path.endswith(".py"):
            interesting.append(path)
        elif "/" not in path and path.endswith((".py", ".ps1", ".bat", ".sh", ".json", ".txt")):
            interesting.append(path)
        elif path.startswith("content/"):
            interesting.append(path)
        elif path.startswith(("templates/", "static/")):
            interesting.append(path)

    reporter.line(f"Tracked files: {len(tracked)}")
    reporter.line(f"Covered by policy: {len(source_map.covered_source)}")
    reporter.line(f"Unclassified: {len(unclassified)}")
    reporter.line("")
    if interesting:
        reporter.line("Runtime-looking files outside the policy:")
        for path in sorted(interesting):
            reporter.line(f"  [UNCLASSIFIED] {path}")
        reporter.line("")
        reporter.line("Review these before the next --sync. Add them to the policy if they are runtime.")
    else:
        reporter.line("No runtime-looking files outside the policy.")

    if source_map.missing:
        reporter.line("")
        reporter.line("Missing required policy files:")
        for path in source_map.missing:
            reporter.line(f"  [MISSING] {path}")

    if source_map.skipped:
        reporter.line("")
        reporter.line("Skipped (symlink/reparse/secret-like):")
        for path in source_map.skipped:
            reporter.line(f"  [SKIP] {path}")

    if strict and (interesting or source_map.missing or source_map.skipped):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Main sync flow
# ---------------------------------------------------------------------------


def run_sync(
    cfg: SyncConfig,
    *,
    mode: str,
    init: bool,
    confirm_deletions: bool,
    force: bool,
    allow_dirty_source: bool,
    allow_dirty_demo: bool,
    adopt: bool,
    verbose: bool,
    reporter: Reporter,
) -> int:
    for_sync = mode == "sync"
    validate_paths(cfg, adopt=adopt, for_sync=for_sync)
    git_info = check_git_state(
        cfg,
        allow_dirty_source=allow_dirty_source,
        allow_dirty_demo=allow_dirty_demo,
        for_sync=for_sync,
    )
    source_map = build_source_map(cfg)

    if source_map.missing:
        raise SyncError(
            "required source files are missing: " + ", ".join(source_map.missing)
        )
    for path in source_map.skipped:
        reporter.line(f"[SKIP] {path} (symlink/reparse point or secret-like name)")

    manifest = read_manifest(cfg) if (cfg.dest / cfg.manifest_name).is_file() else None
    has_sentinel = (cfg.dest / cfg.sentinel_name).is_file()

    if for_sync and not has_sentinel and not init:
        raise SyncError(
            "target has no .demo_directory sentinel; run the first population with --init --sync"
        )
    if for_sync and not cfg.dest.exists():
        cfg.dest.mkdir(parents=True, exist_ok=True)

    entries = classify(cfg, source_map, manifest)
    summary = summarize(entries)

    render_report(
        reporter,
        entries,
        summary,
        cfg=cfg,
        mode=mode,
        git_info=git_info,
        source_map=source_map,
    )

    plan = _plan_mode(entries, force=force, confirm_deletions=confirm_deletions)
    has_pending = bool(plan["pending"]) and not confirm_deletions
    has_conflicts = bool(plan["conflict"])

    if mode == "dry-run":
        reporter.line("")
        reporter.line("No files were written (dry run).")
        if init and not has_sentinel:
            reporter.line("The first --sync --init would create .demo_directory.")
        return 2 if (has_pending or has_conflicts) else 0

    if init:
        write_sentinel(cfg)

    new_files, counts, conflicts, pending = apply_plan(
        cfg,
        entries,
        manifest,
        force=force,
        confirm_deletions=confirm_deletions,
        reporter=reporter,
    )
    status = "clean" if not conflicts and not pending else "partial"
    write_manifest(cfg, new_files, git_info, status=status)
    write_version(cfg, new_files, git_info, status=status)

    reporter.line("")
    reporter.line(
        f"Done: copied={counts['copied']} deleted={counts['deleted']} "
        f"conflicts={len(conflicts)} pending_deletions={len(pending)} status={status}"
    )
    reporter.line(f"Manifest: {cfg.dest / cfg.manifest_name}")
    reporter.line(f"Version:  {cfg.dest / cfg.version_name}")
    return 0 if status == "clean" else 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_config(dest: Path) -> SyncConfig:
    return SyncConfig(
        source=REPO_ROOT,
        dest=dest,
        include_files=dict(INCLUDE_FILES),
        include_dirs=dict(INCLUDE_DIRS),
        templates=dict(DEMO_TEMPLATES),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize universityDemo into the UniversityBotDemoAPI demo directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="preview only; the default")
    action.add_argument("--sync", action="store_true", help="write changes into the demo directory")
    parser.add_argument("--init", action="store_true", help="create the sentinel on first population")
    parser.add_argument("--confirm-deletions", action="store_true", help="remove obsolete managed files")
    parser.add_argument("--force", action="store_true", help="overwrite conflicts / hand-edited files")
    parser.add_argument("--allow-dirty-source", action="store_true", help="allow tracked changes in universityDemo")
    parser.add_argument("--allow-dirty-demo", action="store_true", help="allow a dirty demo git tree")
    parser.add_argument("--adopt", action="store_true", help="write into a non-empty directory without a sentinel")
    parser.add_argument("--audit", action="store_true", help="report tracked files outside the policy")
    parser.add_argument("--strict", action="store_true", help="audit exits 1 on unclassified runtime files")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"demo directory (default: {DEFAULT_DEST})")
    parser.add_argument("--verbose", action="store_true", help="print unchanged files too")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest = args.dest.expanduser().resolve(strict=False)
    cfg = _default_config(dest)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"sync_{_dt.datetime.now():%Y%m%d_%H%M%S}.log"
    reporter = Reporter(log_path=log_path, verbose=args.verbose)

    try:
        if args.audit:
            return run_audit(cfg, strict=args.strict, reporter=reporter)
        mode = "sync" if args.sync else "dry-run"
        return run_sync(
            cfg,
            mode=mode,
            init=args.init,
            confirm_deletions=args.confirm_deletions,
            force=args.force,
            allow_dirty_source=args.allow_dirty_source,
            allow_dirty_demo=args.allow_dirty_demo,
            adopt=args.adopt,
            verbose=args.verbose,
            reporter=reporter,
        )
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        reporter.line(f"ERROR: {exc}")
        return 1
    finally:
        reporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
