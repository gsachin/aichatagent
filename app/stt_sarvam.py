"""Sarvam AI Saaras speech-to-text adapter (REST).

Why this provider: Saaras is trained on Indian languages and code-mixed speech,
supports 8 kHz telephony natively, and accepts up to 50 domain keyterms. The
keyterms are the part that matters for intent quality here: "Meridian",
"B.Tech", "MBA" and the programme names are exactly the tokens a generic ASR
turns into plausible-looking nonsense.

The live path sends one utterance per request (Sarvam's REST limit is 30 s), so
this adapter fits the existing buffer-after-endpoint architecture. The realtime
WebSocket API can replace it later without changing this module's callers.

No network call happens at import time. A missing ``SARVAM_API_KEY`` raises
``STTError`` and the voice handler falls through to the local provider.
"""

from __future__ import annotations

import io
import json
import logging
import time
import wave

import numpy as np
import requests

from app.config import settings
from app.stt_types import STTError, STTResult

logger = logging.getLogger("stt_sarvam")

#: Domain keyterms. Sarvam allows 50 terms of <=64 characters each. Latin
#: acronyms are what the LLM/grounding boundary expects; the native-script
#: forms help when the caller pronounces the name in Hindi/Gujarati.
_DEFAULT_KEYTERMS = [
    "Meridian University",
    "Meridian",
    "मेरिडियन यूनिवर्सिटी",
    "મેરિડિયન યુનિવર્સિટી",
    "B.Tech",
    "B.Tech Computer Science",
    "B.Tech AI and Machine Learning",
    "B.Tech Information Technology",
    "BBA",
    "BCA",
    "B.Com",
    "BA",
    "B.Sc",
    "MBA",
    "MCA",
    "M.Tech",
    "M.Sc",
    "MA",
    "M.Com",
    "बी.टेक",
    "बीबीए",
    "बीसीए",
    "बी.कॉम",
    "एमबीए",
    "एमसीए",
    "બી.ટેક",
    "બીબીએ",
    "બીસી.એ",
    "બી.કોમ",
    "એમબીએ",
    "એમસીએ",
    "admission",
    "एडमिशन",
    "એડમિશન",
    "fees",
    "फीस",
    "ફી",
    "tuition",
    "ट्यूशन",
    "ટ્યુશન",
    "eligibility",
    "पात्रता",
    "પાત્રતા",
    "scholarship",
    "छात्रवृत्ति",
    "શિષ્યવૃત્તિ",
    "hostel",
    "हॉस्टल",
    "હોસ્ટેલ",
]

#: Sarvam BCP-47 code -> the short code the voice session uses.
_BCP_TO_CODE = {
    "as-IN": "as", "bn-IN": "bn", "brx-IN": "brx", "doi-IN": "doi",
    "en-IN": "en", "gu-IN": "gu", "hi-IN": "hi", "kn-IN": "kn",
    "kok-IN": "kok", "ks-IN": "ks", "mai-IN": "mai", "ml-IN": "ml",
    "mni-IN": "mni", "mr-IN": "mr", "ne-IN": "ne", "od-IN": "or",
    "pa-IN": "pa", "sa-IN": "sa", "sat-IN": "sat", "sd-IN": "sd",
    "ta-IN": "ta", "te-IN": "te", "ur-IN": "ur",
}
_CODE_TO_BCP = {code: bcp for bcp, code in _BCP_TO_CODE.items()}


def _keyterms() -> list[str]:
    raw = (settings.SARVAM_STT_KEYTERMS or "").strip()
    terms = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if raw else list(_DEFAULT_KEYTERMS)
    )
    return [term[:64] for term in terms[:50]]


def _language_code(hint: str | None) -> str:
    """BCP-47 code for the request, or ``unknown`` for auto-detection."""
    code = (hint or "").strip().lower().replace("_", "-")
    if code in ("", "auto", "unknown"):
        return "unknown"
    base = code.split("-", 1)[0]
    if base == "or":
        base = "od"                     # Sarvam spells Odia "od-IN"
    return _CODE_TO_BCP.get(base, "unknown")


def _detected_language(code: str | None) -> str:
    if not code:
        return ""
    return _BCP_TO_CODE.get(code.strip(), code.strip().split("-", 1)[0].lower())


def _wav_bytes(audio_16k_int16: np.ndarray) -> bytes:
    pcm = np.asarray(audio_16k_int16, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


def is_configured() -> bool:
    return bool((settings.SARVAM_API_KEY or "").strip())


def health() -> dict:
    """Non-secret provider status for logs/readiness."""
    return {
        "provider": "sarvam",
        "configured": is_configured(),
        "model": settings.SARVAM_STT_MODEL,
        "mode": settings.SARVAM_STT_MODE,
        "endpoint": settings.SARVAM_STT_URL,
        "keyterms": len(_keyterms()),
    }


def transcribe(
    audio_16k_int16: np.ndarray,
    *,
    language_hint: str | None = None,
) -> STTResult:
    """Transcribe one utterance with Saaras v4.

    Raises ``STTError`` on a missing key, transport error, non-2xx response or
    malformed body. The caller is responsible for falling back.
    """
    key = (settings.SARVAM_API_KEY or "").strip()
    if not key:
        raise STTError("SARVAM_API_KEY is empty")

    language_code = _language_code(language_hint)
    data = {
        "model": settings.SARVAM_STT_MODEL,
        "mode": settings.SARVAM_STT_MODE,
        "language_code": language_code,
    }
    terms = _keyterms()
    if terms:
        # Sarvam expects the JSON-encoded array in ONE multipart form field.
        data["keyterms"] = json.dumps(terms, ensure_ascii=False)

    started = time.perf_counter()
    try:
        response = requests.post(
            settings.SARVAM_STT_URL,
            headers={"api-subscription-key": key},
            data=data,
            files={
                "file": (
                    "utterance.wav",
                    _wav_bytes(audio_16k_int16),
                    "audio/wav",
                )
            },
            timeout=(
                min(3.0, settings.SARVAM_STT_TIMEOUT_S),
                settings.SARVAM_STT_TIMEOUT_S,
            ),
        )
    except requests.RequestException as exc:
        raise STTError(f"Sarvam request failed: {exc}") from exc

    if not response.ok:
        detail = (response.text or "").strip().replace("\n", " ")[:300]
        raise STTError(
            f"Sarvam HTTP {response.status_code}: {detail or '(empty body)'}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise STTError("Sarvam returned a non-JSON body") from exc

    transcript = str(payload.get("transcript") or "").strip()
    latency_ms = (time.perf_counter() - started) * 1000.0
    raw_code = str(payload.get("language_code") or "")
    try:
        probability = float(payload.get("language_probability") or 0.0)
    except (TypeError, ValueError):
        probability = 0.0

    logger.info(
        "Sarvam STT: %d chars, lang=%s prob=%.2f, %.0f ms%s",
        len(transcript), raw_code or "?", probability, latency_ms,
        " (empty)" if not transcript else "",
    )
    return STTResult(
        transcript=transcript,
        language=_detected_language(raw_code),
        language_probability=probability,
        provider="sarvam",
        latency_ms=latency_ms,
        raw_language_code=raw_code,
    )
