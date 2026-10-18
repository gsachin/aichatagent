"""Minimal AI4Bharat Indic-TTS HTTP service.

Runs in its own container/venv because the 2023-era Coqui TTS fork and its
dependencies are not compatible with the application's Python 3.11 runtime.
The application talks to this service through ``app/indic_tts.py``.

Contract:
    GET  /health                       -> {"ok": true, "languages": [...]}
    POST /synthesize {"text","language"} -> audio/wav
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

CHECKPOINTS = Path(os.environ.get("INDIC_TTS_CHECKPOINTS", "/checkpoints"))
USE_CUDA = os.environ.get("INDIC_TTS_CUDA", "0").strip() in ("1", "true", "yes")

SUPPORTED = {
    "as", "bn", "brx", "gu", "hi", "kn", "ml", "mni", "mr", "or", "raj", "ta", "te",
}

app = FastAPI(title="Indic-TTS service")
_models: dict[str, object] = {}
_lock = threading.Lock()


class SynthRequest(BaseModel):
    text: str
    language: str = "hi"


def _language_dir(language: str) -> Path:
    # The release zips extract to <lang>/fastpitch, <lang>/hifigan, and
    # <lang>/config.json. Accept either checkpoints/<lang> or a flat
    # checkpoints/ directory containing those files.
    language = language.strip().lower()
    if language not in SUPPORTED:
        raise HTTPException(400, f"unsupported language: {language}")
    candidates = [CHECKPOINTS / language, CHECKPOINTS]
    for base in candidates:
        if (base / "fastpitch" / "best_model.pth").is_file():
            return base
    raise HTTPException(
        503,
        f"checkpoint for {language!r} not found under {CHECKPOINTS}; "
        "download and extract the language zip first",
    )


def _synthesizer(language: str):
    language = language.strip().lower()
    with _lock:
        if language not in _models:
            from TTS.utils.synthesizer import Synthesizer

            base = _language_dir(language)
            _models[language] = Synthesizer(
                model_path=str(base / "fastpitch" / "best_model.pth"),
                config_path=str(base / "config.json"),
                vocoder_path=str(base / "hifigan" / "best_model.pth"),
                vocoder_config_path=str(base / "hifigan" / "config.json"),
                use_cuda=USE_CUDA,
            )
        return _models[language]


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "languages": sorted(SUPPORTED),
        "loaded": sorted(_models),
        "checkpoints": str(CHECKPOINTS),
        "cuda": USE_CUDA,
    }


@app.post("/synthesize")
def synthesize(req: SynthRequest) -> Response:
    if not req.text or not req.text.strip():
        raise HTTPException(400, "text is empty")
    synth = _synthesizer(req.language)
    # The Coqui synthesizer is not guaranteed thread-safe; the demo's
    # concurrency is low, and serialising beats an intermittent crash.
    with _lock:
        wav = synth.tts(req.text)
    sample_rate = int(getattr(synth, "output_sample_rate", 22050))
    buf = io.BytesIO()
    sf.write(buf, wav, sample_rate, format="WAV")
    return Response(content=buf.getvalue(), media_type="audio/wav")
