"""`build_call_assets.py --ensure` is the deploy path: build only what is stale.

A fresh clone has no `app/static/audio/` (it is gitignored), so the launchers
must generate the greeting set. A restart must not re-render 32 assets, and a
voice change must invalidate the old ones. These tests pin both behaviours
without loading a model or touching the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


def _load_build_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_call_assets.py"
    spec = importlib.util.spec_from_file_location("build_call_assets_ensure_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def build(tmp_path, monkeypatch):
    """The build script writing into a temp asset directory."""
    import app.admission as admission

    monkeypatch.setattr(admission, "STATIC_AUDIO_DIR", tmp_path)
    module = _load_build_script()
    calls = {"fixed": [], "greeting": []}

    def fake_synthesise(text, out_path, *, language=None, voice=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = f"{language or 'en'}|{text}".encode("utf-8")
        out_path.write_bytes(data)
        return {
            "file": out_path.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "sample_rate": 8000,
            "channels": 1,
            "sample_width": 2,
            "duration_s": 0.1,
            "text": text,
            "language": language or "en",
            "voice": voice or "fake",
            "phoneme_language": None,
        }

    def fake_greeting(text, out_path, language):
        calls["greeting"].append(out_path.name)
        return fake_synthesise(text, out_path, language=language)

    monkeypatch.setattr(module, "synthesise", fake_synthesise)
    monkeypatch.setattr(module, "synthesise_greeting", fake_greeting)
    return module, tmp_path, calls


def test_ensure_builds_everything_once_then_is_a_noop(build, capsys):
    module, asset_dir, calls = build

    assert module.main(["--ensure"]) == 0
    # 14 fixed/busy (English + six foreign pairs) + 18 greetings (9 languages
    # x inbound/outbound).
    assert len(calls["greeting"]) == 18
    assert len(list(asset_dir.glob("*.wav"))) == 32
    assert (asset_dir / "manifest.json").is_file()

    calls["fixed"].clear()
    calls["greeting"].clear()
    assert module.main(["--ensure"]) == 0
    assert calls["greeting"] == []
    assert "all 32 call assets are present and current" in capsys.readouterr().out


def test_ensure_rebuilds_only_a_missing_greeting(build):
    module, asset_dir, calls = build

    assert module.main(["--ensure"]) == 0
    calls["greeting"].clear()

    missing = sorted(asset_dir.glob("greeting_inbound_en_*.wav"))
    assert len(missing) == 1
    missing[0].unlink()

    assert module.main(["--ensure"]) == 0
    assert len(calls["greeting"]) == 1
    assert calls["greeting"][0] == missing[0].name


def test_ensure_rebuilds_all_when_the_voice_changes(build, monkeypatch, capsys):
    module, asset_dir, calls = build

    assert module.main(["--ensure"]) == 0
    calls["greeting"].clear()
    names_before = {p.name for p in asset_dir.glob("greeting_*.wav")}

    # A real voice change arrives through `.env` and a restart, which makes
    # both the build's env read and the runtime's imported `TTS_VOICE` agree.
    # In-process we have to move both.
    import app.voice_handler as vh

    monkeypatch.setenv("KOKORO_VOICE", "am_other")
    monkeypatch.setattr(vh, "TTS_VOICE", "am_other")
    assert module.main(["--ensure"]) == 0

    # Every asset is rebuilt; the greeting filenames whose voice moved carry a
    # new digest, so no stale file can be selected under the new identity.
    assert "0/32 current; building 32" in capsys.readouterr().out
    assert len(calls["greeting"]) == 18
    names_after = {p.name for p in asset_dir.glob("greeting_*.wav")}
    assert names_after - names_before, "the changed voice must produce new names"


def test_failed_build_leaves_the_previous_manifest_alone(build, capsys):
    module, asset_dir, _ = build

    assert module.main(["--ensure"]) == 0
    manifest_before = (asset_dir / "manifest.json").read_text(encoding="utf-8")

    # Make one greeting unbuildable, then remove it so the next run retries.
    greeting = sorted(asset_dir.glob("greeting_inbound_en_*.wav"))[0]
    greeting.unlink()

    def boom(text, out_path, language):
        raise RuntimeError("provider unavailable")

    module.synthesise_greeting = boom
    assert module.main(["--ensure"]) == 1

    # A manifest that claimed the new state while one asset failed would make
    # readiness lie; the previous record is kept and the failure is reported.
    assert (asset_dir / "manifest.json").read_text(encoding="utf-8") == manifest_before
    assert "manifest left unchanged" in capsys.readouterr().out
