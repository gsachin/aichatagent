"""Render US-002 fixture turns as REAL speech (Kokoro) instead of synthetic tones.

Why this exists: the harness's `synth_speech_ulaw()` produces speech-*like*
voiced harmonics, which pass the app's RMS gate but transcribe to an empty
string. Every turn therefore short-circuited at the noise gate and **the
retrieval, LLM and first-token path was never exercised at all** — the exact
path US-004/US-005 need measured, and the reason US-001's thread-propagation
question stayed unproven.

This renders each turn's scripted `text` through the same Kokoro voice the
agent uses, then down the same path the carrier would: 24 kHz -> 8 kHz µ-law.
The app upsamples back to 16 kHz before Whisper, and that full chain
transcribes at ~0.98 similarity (verified 2026-09-18), so the LLM now receives
a real question.

Output: doc/perf/tools/fixtures/<fixture_id>/turn<N>.ulaw   (raw 8 kHz µ-law)
        doc/perf/tools/fixtures/<fixture_id>/manifest.json  (sha256, per turn)

Rendered once and version-controlled, so a run needs no TTS dependency and a
summary's audio is reproducible from the fixture set alone.

Run:  .venv/Scripts/python.exe doc/perf/tools/render_fixtures.py [--force]
"""
import argparse
import audioop
import hashlib
import json
import os
import sys

import numpy as np
from scipy.signal import resample

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

FIXTURE_DIR = os.path.join(PROJ, "doc", "perf", "tools", "fixtures")
KOKORO_DIR = os.path.join(os.path.expanduser("~"), ".cache", "pipecat", "kokoro-onnx")
VOICE = os.environ.get("KOKORO_VOICE", "af_heart")


def kokoro_ulaw(text: str, kokoro) -> bytes:
    """text -> Kokoro 24 kHz -> 8 kHz µ-law, the carrier's representation."""
    audio, _sr = kokoro.create(text, voice=VOICE, speed=1.0)
    n8 = int(len(audio) * 8000 / 24000)
    a8 = resample(audio, n8)
    pcm16 = (a8 * 32767).clip(-32768, 32767).astype(np.int16)
    return audioop.lin2ulaw(pcm16.tobytes(), 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-render even when output already exists")
    args = ap.parse_args()

    from kokoro_onnx import Kokoro
    kokoro = Kokoro(os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"),
                    os.path.join(KOKORO_DIR, "voices-v1.0.bin"))

    fixtures = sorted(f for f in os.listdir(FIXTURE_DIR) if f.endswith(".json"))
    if not fixtures:
        print("no fixtures found")
        return 1

    rendered = skipped = 0
    for name in fixtures:
        path = os.path.join(FIXTURE_DIR, name)
        spec = json.load(open(path, encoding="utf-8"))
        fid = spec.get("fixture_id") or os.path.splitext(name)[0]
        outdir = os.path.join(FIXTURE_DIR, fid)
        os.makedirs(outdir, exist_ok=True)

        manifest = {"fixture_id": fid, "voice": VOICE, "turns": {},
                    "source_sha256": hashlib.sha256(
                        open(path, "rb").read()).hexdigest()}
        for turn in spec.get("turns", []):
            tid = turn.get("turn_id", 0)
            text = (turn.get("text") or "").strip()
            out = os.path.join(outdir, f"turn{tid}.ulaw")
            if not text:
                continue
            if os.path.exists(out) and not args.force:
                ulaw = open(out, "rb").read()
                skipped += 1
            else:
                ulaw = kokoro_ulaw(text, kokoro)
                with open(out, "wb") as fh:
                    fh.write(ulaw)
                rendered += 1
            manifest["turns"][str(tid)] = {
                "text": text,
                "ulaw_bytes": len(ulaw),
                "seconds": round(len(ulaw) / 8000, 2),
                "sha256": hashlib.sha256(ulaw).hexdigest(),
            }
        with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        print(f"  {fid:<18} {len(manifest['turns'])} turn(s) rendered")

    print(f"\n{rendered} rendered, {skipped} reused. Output: {FIXTURE_DIR}/<id>/turn<N>.ulaw")
    return 0


if __name__ == "__main__":
    sys.exit(main())
