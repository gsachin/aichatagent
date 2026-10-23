"""Tests for scripts/sync_demo.py.

These tests never touch the real demo target or the development .env. They
build a small fake source tree and use temporary directories.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SYNC_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_demo.py"
_spec = importlib.util.spec_from_file_location("sync_demo", SYNC_PATH)
assert _spec is not None and _spec.loader is not None
sync = importlib.util.module_from_spec(_spec)
sys.modules["sync_demo"] = sync
_spec.loader.exec_module(sync)


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "dev"
    _write(source / "app" / "main.py", "print('main')\n")
    _write(source / "app" / "__init__.py", "")
    _write(source / "app" / "static" / "index.html", "<html></html>\n")
    _write(source / "app" / "static" / "audio" / "greeting.wav", "wav\n")
    _write(source / "app" / "__pycache__" / "junk.pyc", "junk\n")
    _write(source / "app" / ".env", "SECRET=1\n")
    _write(source / "requirements.txt", "fastapi\n")
    _write(source / "app.py", "print('ui')\n")
    _write(source / "dashboard.py", "print('dash')\n")
    _write(source / "content" / "meridian" / "meridian_knowledge_base.md", "KB\n")
    _write(source / "scripts" / "build_call_assets.py", "# build\n")
    _write(source / "scripts" / "seed_demo_data.py", "# seed\n")
    _write(source / "scripts" / "update_twilio_webhook.py", "# twilio\n")
    for template in sync.DEMO_TEMPLATES:
        _write(source / template, f"# {Path(template).name}\n")
    return source


def make_config(tmp_path: Path, *, extra_files: dict[str, str] | None = None) -> sync.SyncConfig:
    source = make_source(tmp_path)
    include_files = dict(sync.INCLUDE_FILES)
    if extra_files:
        include_files.update(extra_files)
    return sync.SyncConfig(
        source=source,
        dest=tmp_path / "demo",
        include_files=include_files,
        include_dirs=dict(sync.INCLUDE_DIRS),
        templates=dict(sync.DEMO_TEMPLATES),
    )


def run(
    cfg: sync.SyncConfig,
    *,
    mode: str = "sync",
    init: bool = False,
    confirm_deletions: bool = False,
    force: bool = False,
    allow_dirty_source: bool = False,
    allow_dirty_demo: bool = False,
    adopt: bool = False,
) -> int:
    reporter = sync.Reporter(log_path=None, quiet=True)
    return sync.run_sync(
        cfg,
        mode=mode,
        init=init,
        confirm_deletions=confirm_deletions,
        force=force,
        allow_dirty_source=allow_dirty_source,
        allow_dirty_demo=allow_dirty_demo,
        adopt=adopt,
        verbose=False,
        reporter=reporter,
    )


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_policy_excludes_secrets_cache_and_keeps_runtime_assets(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    source_map = sync.build_source_map(cfg)

    assert "app/main.py" in source_map.files
    assert "app/static/audio/greeting.wav" in source_map.files
    assert "app/.env" not in source_map.files
    assert "app/__pycache__/junk.pyc" not in source_map.files
    assert "app/.env" in source_map.skipped
    assert not source_map.missing


def test_refuses_target_inside_source(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    bad = dataclasses.replace(cfg, dest=cfg.source / "demo")
    with pytest.raises(sync.SyncError):
        sync.validate_paths(bad, adopt=False, for_sync=True)


def test_refuses_non_empty_target_without_sentinel(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.dest.mkdir(parents=True)
    (cfg.dest / "someone-elses-file.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(sync.SyncError):
        sync.validate_paths(cfg, adopt=False, for_sync=True)


# ---------------------------------------------------------------------------
# Sync flow
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert run(cfg, mode="dry-run") == 0
    assert not cfg.dest.exists()


def test_first_sync_and_second_noop(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert run(cfg, mode="sync", init=True) == 0

    manifest = json.loads((cfg.dest / cfg.manifest_name).read_text(encoding="utf-8"))
    expected = len(sync.build_source_map(cfg).files)
    assert len(manifest["files"]) == expected
    assert manifest["status"] == "clean"
    assert (cfg.dest / cfg.sentinel_name).exists()
    assert (cfg.dest / cfg.version_name).exists()
    assert not (cfg.dest / "app" / ".env").exists()
    assert not (cfg.dest / "app" / "__pycache__").exists()

    before = (cfg.dest / "app" / "main.py").stat().st_mtime_ns
    assert run(cfg, mode="sync", init=False) == 0
    after = (cfg.dest / "app" / "main.py").stat().st_mtime_ns
    assert before == after


def test_update_copies_changed_file(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert run(cfg, mode="sync", init=True) == 0

    (cfg.source / "app" / "main.py").write_text("print('changed')\n", encoding="utf-8")
    assert run(cfg, mode="sync", init=False) == 0
    assert (cfg.dest / "app" / "main.py").read_text(encoding="utf-8") == "print('changed')\n"


def test_conflict_requires_force(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert run(cfg, mode="sync", init=True) == 0

    (cfg.dest / "app" / "main.py").write_text("demo edit\n", encoding="utf-8")
    (cfg.source / "app" / "main.py").write_text("source edit\n", encoding="utf-8")

    assert run(cfg, mode="sync", init=False) == 2
    assert (cfg.dest / "app" / "main.py").read_text(encoding="utf-8") == "demo edit\n"
    manifest = json.loads((cfg.dest / cfg.manifest_name).read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"

    assert run(cfg, mode="sync", init=False, force=True) == 0
    assert (cfg.dest / "app" / "main.py").read_text(encoding="utf-8") == "source edit\n"


def test_obsolete_requires_confirmation(tmp_path: Path) -> None:
    cfg = make_config(
        tmp_path,
        extra_files={"scripts/old.py": "scripts/old.py"},
    )
    _write(cfg.source / "scripts" / "old.py", "# old\n")
    assert run(cfg, mode="sync", init=True) == 0
    assert (cfg.dest / "scripts" / "old.py").exists()

    cfg2 = dataclasses.replace(cfg, include_files=dict(sync.INCLUDE_FILES))
    assert run(cfg2, mode="sync", init=False) == 2
    assert (cfg2.dest / "scripts" / "old.py").exists()

    assert run(cfg2, mode="sync", init=False, confirm_deletions=True) == 0
    assert not (cfg2.dest / "scripts" / "old.py").exists()


def test_protected_paths_are_preserved(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.dest.mkdir(parents=True)
    (cfg.dest / ".env").write_text("DEMO=1\n", encoding="utf-8")
    (cfg.dest / "data" / "documents").mkdir(parents=True)
    (cfg.dest / "data" / "documents" / "keep.txt").write_text("keep\n", encoding="utf-8")

    assert run(cfg, mode="sync", init=True, adopt=True) == 0
    assert (cfg.dest / ".env").read_text(encoding="utf-8") == "DEMO=1\n"
    assert (cfg.dest / "data" / "documents" / "keep.txt").exists()


# ---------------------------------------------------------------------------
# Git state
# ---------------------------------------------------------------------------


def test_dirty_source_blocks_sync(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    _git_init(cfg.source)
    (cfg.source / "requirements.txt").write_text("fastapi==1\n", encoding="utf-8")

    with pytest.raises(sync.SyncError):
        run(cfg, mode="sync", init=True)
    assert not cfg.dest.exists()


def test_dirty_demo_git_blocks_sync(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert run(cfg, mode="sync", init=True) == 0
    _git_init(cfg.dest)
    (cfg.dest / "app.py").write_text("demo edit\n", encoding="utf-8")

    with pytest.raises(sync.SyncError):
        run(cfg, mode="sync", init=False)


def test_audit_flags_unclassified_runtime_script(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    _git_init(cfg.source)
    _write(cfg.source / "scripts" / "new_runtime.py", "# new\n")
    subprocess.run(["git", "add", "."], cwd=cfg.source, check=True)
    subprocess.run(["git", "commit", "-m", "new"], cwd=cfg.source, check=True, capture_output=True)

    reporter = sync.Reporter(log_path=None, quiet=True)
    assert sync.run_audit(cfg, strict=True, reporter=reporter) == 1
