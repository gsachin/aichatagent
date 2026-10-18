"""Client for the isolated AI4Bharat Indic-TTS service.

The Indic-TTS checkpoints and their 2023-era Coqui TTS fork are deliberately
NOT installed into this application's Python 3.11 venv. They run in a separate
service (Docker/WSL/Python 3.8-3.10) exposing a tiny HTTP contract:

    POST {INDIC_TTS_URL}/synthesize
      {"text": "...", "language": "hi"}
    -> 200 audio/wav

This client decodes the WAV into the same ``(float32 mono, sample_rate)`` shape
Kokoro returns, so the existing resampling and µ-law conversion in
``app.voice_handler`` are unchanged.
"""

from __future__ import annotations

import io
import logging

import httpx

from app.config import settings

logger = logging.getLogger("indic_tts")

#: Every language the AI4Bharat Indic-TTS v1 release provides a checkpoint for.
INDIC_TTS_LANGUAGES = frozenset({
    "as", "bn", "brx", "gu", "hi", "kn", "ml", "mni", "mr", "or", "raj", "ta", "te",
})

_TIMEOUT_S = 30.0


def is_available() -> bool:
    """Cheap health probe; never raises."""
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{settings.INDIC_TTS_URL.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:                                    # noqa: BLE001
        return False


def synthesise(text: str, language: str):
    """Return ``(float32 mono numpy array, sample_rate)`` or ``None``.

    ``None`` is the caller's signal to use the existing fixed-response path;
    it never means "speak silence".
    """
    if not text or not text.strip():
        return None
    language = (language or settings.INDIC_TTS_LANGUAGE).strip().lower()
    if language not in INDIC_TTS_LANGUAGES:
        logger.warning("Indic-TTS: unsupported language %r", language)
        return None
    try:
        import numpy as np
        import soundfile as sf

        url = f"{settings.INDIC_TTS_URL.rstrip('/')}/synthesize"
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.post(url, json={"text": text, "language": language})
            resp.raise_for_status()
            audio, sr = sf.read(
                io.BytesIO(resp.content), dtype="float32", always_2d=True
            )
        if audio.size == 0:
            return None
        if audio.shape[1] > 1:
            audio = audio.mean(axis=1)
        else:
            audio = audio[:, 0]
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        return audio, int(sr)
    except Exception:                                    # noqa: BLE001
        logger.exception("Indic-TTS synthesis failed (language=%s)", language)
        return None
