"""AI4Bharat IndicConformer-600M offline STT adapter (ONNX, onnx-asr).

The OpenVoiceOS repositories are int8 ONNX exports of AI4Bharat's
IndicConformer; each language is ~138 MB and runs without NeMo/PyTorch. This is
the local fallback for Hindi/Gujarati when Sarvam is unavailable. It has no
keyterm prompting, so it is deliberately second in the chain.

CPU is the default: int8 inference is ~0.25x RTF on this box (4x faster than
real time) and avoids competing with Kokoro/Whisper for the 16 GB card. Set
``INDIC_CONFORMER_DEVICE=cuda`` to try the CUDA provider; a failed CUDA session
falls back to CPU automatically.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from app.config import settings
from app.stt_types import STTError, STTResult

logger = logging.getLogger("stt_indic")

_MODELS: dict[str, object] = {}
_LOCK = threading.Lock()

#: Voice-session language -> HF repository.
_REPOS = {
    "hi": settings.INDIC_CONFORMER_MODEL_HI,
    "gu": settings.INDIC_CONFORMER_MODEL_GU,
}


def supported(language: str | None) -> bool:
    return (language or "").strip().lower() in _REPOS


def _providers() -> list:
    if settings.INDIC_CONFORMER_DEVICE != "cuda":
        return ["CPUExecutionProvider"]
    # On Windows the ORT wheel does not bundle cuBLAS/cuDNN. Torch ships both;
    # preload_dlls() makes them resolvable (the same fix Kokoro TTS uses).
    try:
        import onnxruntime as ort
        import torch  # noqa: F401
        ort.preload_dlls()
    except Exception as exc:                    # noqa: BLE001
        logger.warning("IndicConformer: CUDA preload skipped (%s)", exc)
    return [("CUDAExecutionProvider", {"device_id": 0}),
            "CPUExecutionProvider"]


def _load(language: str):
    repo = _REPOS.get(language)
    if not repo:
        raise STTError(f"IndicConformer has no model for {language!r}")
    with _LOCK:
        model = _MODELS.get(language)
        if model is not None:
            return model
        import onnx_asr

        started = time.perf_counter()
        try:
            model = onnx_asr.load_model(
                repo,
                quantization=settings.INDIC_CONFORMER_QUANTIZATION or None,
                providers=_providers(),
            )
        except Exception as exc:                # noqa: BLE001
            if settings.INDIC_CONFORMER_DEVICE == "cuda":
                logger.warning(
                    "IndicConformer: CUDA load failed (%s); retrying on CPU", exc
                )
                try:
                    model = onnx_asr.load_model(
                        repo,
                        quantization=settings.INDIC_CONFORMER_QUANTIZATION or None,
                        providers=["CPUExecutionProvider"],
                    )
                except Exception as cpu_exc:     # noqa: BLE001
                    raise STTError(
                        f"IndicConformer load failed for {language}: {cpu_exc}"
                    ) from cpu_exc
            else:
                raise STTError(
                    f"IndicConformer load failed for {language}: {exc}"
                ) from exc
        _MODELS[language] = model
        logger.info(
            "IndicConformer %s ready in %.1fs (%s, %s)",
            language, time.perf_counter() - started,
            settings.INDIC_CONFORMER_QUANTIZATION or "fp32",
            settings.INDIC_CONFORMER_DEVICE,
        )
        return model


def warmup() -> None:
    """Load every configured language. Failures are logged, never raised."""
    for language in _REPOS:
        try:
            _load(language)
        except Exception:                       # noqa: BLE001
            logger.exception("IndicConformer warmup failed for %s", language)


def health() -> dict:
    return {
        "provider": "indic_conformer",
        "models": dict(_REPOS),
        "quantization": settings.INDIC_CONFORMER_QUANTIZATION,
        "device": settings.INDIC_CONFORMER_DEVICE,
        "loaded": sorted(_MODELS),
    }


def transcribe(audio_16k_int16: np.ndarray, language: str) -> STTResult:
    """Transcribe one utterance with the language's IndicConformer model."""
    code = (language or "").strip().lower()
    if code not in _REPOS:
        raise STTError(f"IndicConformer does not support language {language!r}")
    model = _load(code)
    audio = np.asarray(audio_16k_int16, dtype=np.float32) / 32768.0
    started = time.perf_counter()
    try:
        text = model.recognize(audio, sample_rate=16000)
    except Exception as exc:                    # noqa: BLE001
        raise STTError(f"IndicConformer inference failed: {exc}") from exc
    if isinstance(text, list):
        text = text[0] if text else ""
    transcript = str(text or "").strip()
    latency_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "IndicConformer STT: %d chars (%s, %.0f ms)%s",
        len(transcript), code, latency_ms,
        " (empty)" if not transcript else "",
    )
    return STTResult(
        transcript=transcript,
        language=code,
        provider="indic_conformer",
        latency_ms=latency_ms,
    )
