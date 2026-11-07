"""The boot gate names a missing greeting before a caller discovers it.

A greeting asset is generated, not committed. Missing one does not break a
call -- live synthesis covers it -- but it costs the first caller the latency
the asset exists to remove, and the only other signal is a log line on a live
call. These tests pin the boot-time report.
"""

from __future__ import annotations

import pytest

CORE = ("en", "hi", "gu")


def _write_expected_assets(asset_dir):
    from app.languages import configured_foreign_languages
    from app.voice_handler import greeting_asset_name
    from app.voice_system_prompt import call_greeting

    languages = list(dict.fromkeys(
        [*CORE, *sorted(configured_foreign_languages())]
    ))
    names = {}
    for code in languages:
        for direction in ("inbound", "outbound"):
            name = greeting_asset_name(
                code, direction, call_greeting(code, direction)
            )
            (asset_dir / name).write_bytes(b"fake-wav")
            names[f"{code}/{direction}"] = name
    return languages, names


@pytest.fixture()
def asset_dir(tmp_path, monkeypatch):
    import app.admission as admission

    monkeypatch.setattr(admission, "STATIC_AUDIO_DIR", tmp_path)
    return tmp_path


def test_reports_present_count_without_degrading(asset_dir):
    from app.boot_readiness import Readiness, check_greeting_assets

    languages, _ = _write_expected_assets(asset_dir)
    r = Readiness()
    check_greeting_assets(r)

    assert r.assets["greetings"] == {
        "expected": len(languages) * 2,
        "present": len(languages) * 2,
        "missing": [],
    }
    assert r.degraded == []


def test_names_the_missing_language_and_direction(asset_dir):
    from app.boot_readiness import Readiness, check_greeting_assets

    _, names = _write_expected_assets(asset_dir)
    (asset_dir / names["en/inbound"]).unlink()

    r = Readiness()
    check_greeting_assets(r)

    assert r.assets["greetings"]["missing"] == ["en/inbound"]
    assert r.assets["greetings"]["present"] == r.assets["greetings"]["expected"] - 1
    assert any(
        "greeting assets missing for 1/" in message
        and "en/inbound" in message
        and "build_call_assets.py --ensure" in message
        for message in r.degraded
    )


def test_render_shows_the_greeting_summary(asset_dir):
    from app.boot_readiness import Readiness, check_greeting_assets, render

    _, names = _write_expected_assets(asset_dir)
    (asset_dir / names["hi/outbound"]).unlink()

    r = Readiness()
    check_greeting_assets(r)
    text = render(r)

    assert "greeting assets: 17/18 present (missing 1)" in text
