"""Sarvam Bulbul v4 Flash text-to-speech adapter (HTTP streaming).

Kokoro has no Gujarati voice: `_tts_voice_for("gu")` falls back to an English
voice and the caller hears Gujarati text read with English phonemes. Bulbul v4
Flash carries Gujarati personas and returns raw 8 kHz linear16 PCM from
`/text-to-speech/stream`, which is the carrier's rate — no resampling between
Sarvam and Twilio.

Only the languages listed in `SARVAM_TTS_LANGUAGES` route here; the demo sets
`gu`. English and Hindi stay on Kokoro until the operator adds them.

No network call happens at import time. A missing key raises `TTSError`; the
voice handler logs it and falls back through the existing TTS failure path.
"""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator

import httpx
import numpy as np

from app.config import settings

logger = logging.getLogger("tts_sarvam")


class TTSError(RuntimeError):
    """Sarvam TTS failed; the caller decides whether to degrade or fall back."""


#: Bulbul v4 Flash persona IDs. The demo routes Gujarati only; the Hindi entry
#: is here so adding "hi" to SARVAM_TTS_LANGUAGES does not require a code edit.
_DEFAULT_SPEAKERS = {
    "gu": "pooja_gu_customer",
    "hi": "aparna_hi_customer",
    "en": "simran_en_customer",
}

_LANG_TO_BCP = {
    "hi": "hi-IN",
    "en": "en-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "or": "od-IN",
    "pa": "pa-IN",
    "ta": "ta-IN",
    "te": "te-IN",
}


def is_configured() -> bool:
    return bool((settings.SARVAM_API_KEY or "").strip())


def supported(language: str | None) -> bool:
    return (language or "").strip().lower() in _LANG_TO_BCP


def speaker_for(language: str | None) -> str:
    configured = (settings.SARVAM_TTS_SPEAKER or "").strip()
    if configured:
        return configured
    return _DEFAULT_SPEAKERS.get(
        (language or "").strip().lower(), "pooja_gu_customer"
    )


def _payload(text: str, language: str, speaker: str | None,
             pace: float | None) -> dict:
    code = (language or "gu").strip().lower()
    bcp = _LANG_TO_BCP.get(code)
    if not bcp:
        raise TTSError(f"Bulbul has no language mapping for {language!r}")
    return {
        "text": text[:3500],
        "language_code": bcp,
        "speaker": speaker or speaker_for(code),
        "model": settings.SARVAM_TTS_MODEL,
        "output_audio_codec": "linear16",
        "speech_sample_rate": int(settings.SARVAM_TTS_SAMPLE_RATE),
        "pace": float(pace if pace is not None else settings.SARVAM_TTS_PACE),
        "enable_preprocessing": True,
    }


def _headers() -> dict:
    key = (settings.SARVAM_API_KEY or "").strip()
    if not key:
        raise TTSError("SARVAM_API_KEY is empty")
    return {"api-subscription-key": key, "Content-Type": "application/json"}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        settings.SARVAM_TTS_TIMEOUT_S,
        connect=min(3.0, settings.SARVAM_TTS_TIMEOUT_S),
    )


def _pcm(data: bytes) -> np.ndarray:
    # Little-endian 16-bit mono PCM. The caller sets 8 kHz.
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _error_body(response: httpx.Response) -> str:
    try:
        body = response.read().decode("utf-8", errors="replace")
    except Exception:                            # noqa: BLE001
        body = ""
    return body.strip().replace("\n", " ")[:300] or "(empty body)"


async def astream(
    text: str,
    language: str,
    *,
    speaker: str | None = None,
    pace: float | None = None,
) -> AsyncIterator[np.ndarray]:
    """Yield raw float32 PCM chunks at the configured sample rate (8 kHz).

    The response is a binary stream; each chunk is converted to float32 as it
    arrives. A trailing odd byte is carried to the next chunk, and dropped at
    the end, so a provider chunk boundary can never shift the sample alignment.
    """
    if not text.strip():
        return
    payload = _payload(text, language, speaker, pace)
    headers = _headers()
    started = time.perf_counter()
    first = None
    pending = b""
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            async with client.stream(
                "POST", settings.SARVAM_TTS_URL, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(
                        "utf-8", errors="replace"
                    ).strip().replace("\n", " ")[:300]
                    raise TTSError(
                        f"Sarvam TTS HTTP {response.status_code}: "
                        f"{body or '(empty body)'}"
                    )
                async for raw in response.aiter_bytes(chunk_size=3200):
                    if not raw:
                        continue
                    data = pending + raw
                    if len(data) % 2:
                        pending = data[-1:]
                        data = data[:-1]
                    else:
                        pending = b""
                    if not data:
                        continue
                    if first is None:
                        first = time.perf_counter()
                        logger.info(
                            "Sarvam TTS first chunk after %.0f ms (%s, %s)",
                            (first - started) * 1000.0,
                            payload["language_code"], payload["speaker"],
                        )
                    yield _pcm(data)
        if first is None:
            raise TTSError("Sarvam TTS returned no audio")
    except httpx.HTTPError as exc:
        raise TTSError(f"Sarvam TTS request failed: {exc}") from exc
    finally:
        logger.info(
            "Sarvam TTS stream: %d chars, %.0f ms%s",
            len(payload["text"]), (time.perf_counter() - started) * 1000.0,
            " (no audio)" if first is None else "",
        )


def synthesise(
    text: str,
    language: str,
    *,
    speaker: str | None = None,
    pace: float | None = None,
) -> tuple[np.ndarray, int]:
    """Batch wrapper: join the HTTP stream into one float32 buffer."""
    if not text.strip():
        raise TTSError("empty TTS text")
    payload = _payload(text, language, speaker, pace)
    headers = _headers()
    started = time.perf_counter()
    parts: list[np.ndarray] = []
    pending = b""
    try:
        with httpx.Client(timeout=_timeout()) as client:
            with client.stream(
                "POST", settings.SARVAM_TTS_URL, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    raise TTSError(
                        f"Sarvam TTS HTTP {response.status_code}: "
                        f"{_error_body(response)}"
                    )
                for raw in response.iter_bytes(chunk_size=3200):
                    if not raw:
                        continue
                    data = pending + raw
                    if len(data) % 2:
                        pending = data[-1:]
                        data = data[:-1]
                    else:
                        pending = b""
                    if data:
                        parts.append(_pcm(data))
    except httpx.HTTPError as exc:
        raise TTSError(f"Sarvam TTS request failed: {exc}") from exc

    if not parts:
        raise TTSError("Sarvam TTS returned no audio")
    audio = np.concatenate(parts)
    logger.info(
        "Sarvam TTS: %d chars -> %.2fs at %d Hz (%.0f ms)",
        len(payload["text"]), len(audio) / settings.SARVAM_TTS_SAMPLE_RATE,
        settings.SARVAM_TTS_SAMPLE_RATE,
        (time.perf_counter() - started) * 1000.0,
    )
    return audio, int(settings.SARVAM_TTS_SAMPLE_RATE)


def health() -> dict:
    """Non-secret provider status."""
    return {
        "provider": "sarvam_tts",
        "configured": is_configured(),
        "model": settings.SARVAM_TTS_MODEL,
        "languages": sorted(
            part.strip().lower()
            for part in (settings.SARVAM_TTS_LANGUAGES or "").split(",")
            if part.strip()
        ),
        "speaker": settings.SARVAM_TTS_SPEAKER or dict(_DEFAULT_SPEAKERS),
        "sample_rate": settings.SARVAM_TTS_SAMPLE_RATE,
        "endpoint": settings.SARVAM_TTS_URL,
    }
