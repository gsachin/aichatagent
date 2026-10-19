"""Pre-synthesise the call assets US-016 plays (BRD-13, AC-3).

Two assets, produced ONCE, ahead of time, in the assistant's own voice:

  busy.wav            the refusal a third caller hears when both lines are live
  fixed_response.wav  what a caller hears when the inference engine is lost

They exist so that neither situation requires anything to be synthesised or
generated at request time. That is the whole point of the story: the refusal
path must not consume the resources it exists to protect, and the engine-loss
path must work when the engine -- or the synthesiser, or both -- is gone.

Twilio `<Play>` is served WAV, PCM, 8 kHz, mono, 16-bit: the carrier's own
narrowband rate, which is what the media stream carries anyway.

Wording constraints (AC-5) are enforced by a check, not by intention: no
internal vocabulary ("error", "timeout", "connection", a model name) and no
promise the surviving components cannot keep. The check runs here so a bad line
cannot reach a caller, and the same rules are re-asserted in the test suite.

Run once after a voice change, and any time the boot gate reports drift:

    .venv/Scripts/python.exe scripts/build_call_assets.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import wave
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

# The wording lives in app/admission.py, because the refusal path also needs it
# for its carrier-<Say> fallback when the assets are absent. One home for the
# words means the recording and the fallback cannot drift apart.
from app.admission import (  # noqa: E402
    BUSY_TEXT,
    FIXED_RESPONSE_TEXT,
    busy_asset,
    busy_text,
    fixed_response_asset,
    fixed_response_text,
)
from app.languages import DEFAULT_FOREIGN_LANGUAGES, contains_cjk  # noqa: E402

#: Internal vocabulary that must never reach a caller (AC-5).
FORBIDDEN_IN_WORDING = (
    "error", "timeout", "timed out", "connection", "connect", "exception",
    "token", "quota", "server", "engine", "model", "qwen", "llama", "ollama",
    "whisper", "kokoro", "rag", "latency", "500", "503",
)

TARGET_SR = 8000


def check_wording(name: str, text: str) -> list[str]:
    """AC-5, enforced at build time so a bad line cannot reach a caller."""
    problems = []
    low = text.lower()
    for word in FORBIDDEN_IN_WORDING:
        if word in low:
            problems.append(f"{name}: contains internal vocabulary {word!r}")
    if not text.strip():
        problems.append(f"{name}: empty")
    if len(text.split()) > 60:
        problems.append(f"{name}: {len(text.split())} words is too long for a refusal")
    return problems


def check_foreign_wording(name: str, text: str) -> list[str]:
    """Non-empty and short enough for a refusal, without English word rules.

    The English forbidden-vocabulary list is English-specific; applying it to
    Japanese or Chinese would be meaningless. Translation review owns the
    wording quality for these six; this check owns the shape.
    """
    problems = []
    if not text.strip():
        problems.append(f"{name}: empty")
    limit = 120 if contains_cjk(text) else 240
    if len(text) > limit:
        problems.append(f"{name}: {len(text)} characters is too long for a refusal")
    return problems


def synthesise(text: str, out_path: Path, *,
               language: str | None = None,
               voice: str | None = None) -> dict:
    """Render one asset at the agent's voice, in carrier format.

    ``language=None`` is the English path and must stay byte-identical: it
    calls ``engine.create`` exactly as it did before this language work (no
    ``lang`` argument). A foreign language selects its Kokoro voice and
    phoneme set so the output is not English pronunciation.
    """
    import numpy as np
    from app.languages import normalize_language_code
    from app.voice_handler import (
        _get_tts_engine, _kokoro_lang_for, _kokoro_render, _tts_speed,
        _tts_speed_for, _tts_voice, _tts_voice_for,
    )

    engine = _get_tts_engine()
    if language is None:
        audio, sr = engine.create(text, voice=_tts_voice(), speed=_tts_speed())
        voice, lang = _tts_voice(), None
    else:
        voice = voice or _tts_voice_for(language)
        speed = _tts_speed_for(language)
        lang = (
            "misaki"
            if normalize_language_code(language) in ("ja", "zh")
            else _kokoro_lang_for(language, voice)
        )
        # Kokoro's `create` truncates at 510 phonemes and then indexes the
        # voice array with the pre-truncation count, which raises IndexError
        # for a long CJK sentence. Sentence-sized chunks keep every call below
        # the limit; the concatenated WAV is one asset with natural pauses.
        parts = [p for p in re.split(r"(?<=[.!?;。！？])\s*", text) if p.strip()]
        if len(parts) > 1:
            pieces = []
            for part in parts:
                chunk, sr = _kokoro_render(
                    engine, part, language, voice, speed
                )
                pieces.append(np.asarray(chunk, dtype=np.float32))
            audio = np.concatenate(pieces)
        else:
            audio, sr = _kokoro_render(
                engine, text, language, voice, speed
            )
    audio = np.asarray(audio, dtype=np.float32)

    if sr != TARGET_SR:
        from scipy.signal import resample
        audio = resample(audio, int(len(audio) * TARGET_SR / sr)).astype(np.float32)
        sr = TARGET_SR

    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())

    data = out_path.read_bytes()
    return {
        "file": out_path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sample_rate": sr,
        "channels": 1,
        "sample_width": 2,
        "duration_s": round(len(pcm16) / sr, 3),
        "text": text,
        "language": language or "en",
        "voice": voice,
        "phoneme_language": lang,
    }


def main() -> int:
    from app.admission import ASSET_MANIFEST, BUSY_ASSET, FIXED_RESPONSE_ASSET, asset_path

    print("=" * 74)
    print("US-016 -- building the pre-synthesised call assets")
    print("=" * 74)

    jobs = [
        (BUSY_ASSET, BUSY_TEXT, None),
        (FIXED_RESPONSE_ASSET, FIXED_RESPONSE_TEXT, None),
    ]
    for code in DEFAULT_FOREIGN_LANGUAGES:
        jobs.append((busy_asset(code), busy_text(code), code))
        jobs.append((fixed_response_asset(code), fixed_response_text(code), code))

    problems = (
        check_wording("busy", BUSY_TEXT)
        + check_wording("fixed_response", FIXED_RESPONSE_TEXT)
    )
    for name, text, code in jobs:
        if code is not None:
            problems.extend(check_foreign_wording(name, text))
    for p in problems:
        print(f"  REFUSED  {p}")
    if problems:
        print("\n  Fix the wording before building: a caller must never hear this.")
        return 1
    print("  wording check: English vocabulary/length and foreign shape pass")

    voice = os.environ.get("KOKORO_VOICE", "af_heart")
    speed = float(os.environ.get("KOKORO_SPEED", "1.0") or 1.0)

    manifest = {
        "voice": voice.strip() or "af_heart",
        "speed": speed,
        "format": {"container": "wav", "codec": "pcm_s16le", "sample_rate": TARGET_SR,
                   "channels": 1, "note": "Twilio <Play> carrier format"},
        "assets": {},
    }

    for name, text, code in jobs:
        info = synthesise(text, asset_path(name), language=code)
        manifest["assets"][name] = info
        print(f"  {name:<24} {info['duration_s']:>6.2f}s  {info['bytes']:>8} bytes  "
              f"sha256 {info['sha256'][:12]}  lang {info['language']}  "
              f"voice {info['voice']}")

    asset_path(ASSET_MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n  manifest -> {asset_path(ASSET_MANIFEST)}")
    print(f"  voice {manifest['voice']} at speed {manifest['speed']}")
    print("\n  Served at /static/audio/<file>; Twilio <Play> points at the tunnel host.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
