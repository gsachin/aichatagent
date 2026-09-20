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
from app.admission import BUSY_TEXT, FIXED_RESPONSE_TEXT  # noqa: E402

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


def synthesise(text: str, out_path: Path) -> dict:
    """Render one asset at the agent's configured voice, in carrier format."""
    import numpy as np
    from app.voice_handler import _get_tts_engine, _tts_speed, _tts_voice

    engine = _get_tts_engine()
    audio, sr = engine.create(text, voice=_tts_voice(), speed=_tts_speed())
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
    }


def main() -> int:
    from app.admission import ASSET_MANIFEST, BUSY_ASSET, FIXED_RESPONSE_ASSET, asset_path

    print("=" * 74)
    print("US-016 -- building the pre-synthesised call assets")
    print("=" * 74)

    problems = check_wording("busy", BUSY_TEXT) + check_wording("fixed_response", FIXED_RESPONSE_TEXT)
    for p in problems:
        print(f"  REFUSED  {p}")
    if problems:
        print("\n  Fix the wording before building: a caller must never hear this.")
        return 1
    print("  wording check: no internal vocabulary, both lines within length")

    voice = os.environ.get("KOKORO_VOICE", "af_heart")
    speed = float(os.environ.get("KOKORO_SPEED", "1.0") or 1.0)

    manifest = {
        "voice": voice.strip() or "af_heart",
        "speed": speed,
        "format": {"container": "wav", "codec": "pcm_s16le", "sample_rate": TARGET_SR,
                   "channels": 1, "note": "Twilio <Play> carrier format"},
        "assets": {},
    }

    for name, text in ((BUSY_ASSET, BUSY_TEXT), (FIXED_RESPONSE_ASSET, FIXED_RESPONSE_TEXT)):
        info = synthesise(text, asset_path(name))
        manifest["assets"][name] = info
        print(f"  {name:<20} {info['duration_s']:>6.2f}s  {info['bytes']:>8} bytes  "
              f"sha256 {info['sha256'][:12]}")

    asset_path(ASSET_MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n  manifest -> {asset_path(ASSET_MANIFEST)}")
    print(f"  voice {manifest['voice']} at speed {manifest['speed']}")
    print("\n  Served at /static/audio/<file>; Twilio <Play> points at the tunnel host.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
