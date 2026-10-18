"""STT provider selection, boot warmup and health.

Order: ``STT_PROVIDER`` first, then ``STT_FALLBACK_PROVIDERS`` in the order
written. The voice handler owns the per-utterance fallback loop; this module
owns the startup view so operators can see what will serve a call.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("stt")

_KNOWN = {"sarvam", "indic_conformer", "whisper"}


def provider_order() -> list[str]:
    primary = (settings.STT_PROVIDER or "whisper").strip().lower()
    fallback = [
        part.strip().lower()
        for part in (settings.STT_FALLBACK_PROVIDERS or "").split(",")
        if part.strip()
    ]
    order: list[str] = []
    for provider in [primary, *fallback]:
        if provider and provider not in order:
            order.append(provider)
    return order


def warmup() -> None:
    """Prepare the configured providers. Never raises: a warmup failure must
    not stop the stack from answering calls; the next provider is tried on the
    first utterance."""
    order = provider_order()
    logger.info("STT providers: %s", " -> ".join(order) or "(none)")

    for provider in order:
        if provider not in _KNOWN:
            logger.warning("STT: unknown provider %r in the chain", provider)
            continue
        try:
            if provider == "sarvam":
                from app import stt_sarvam

                status = stt_sarvam.health()
                if status["configured"]:
                    logger.info(
                        "STT Sarvam ready: model=%s mode=%s keyterms=%d",
                        status["model"], status["mode"], status["keyterms"],
                    )
                else:
                    logger.warning(
                        "STT Sarvam selected but SARVAM_API_KEY is empty; "
                        "the chain will fall through to the local providers"
                    )
            elif provider == "indic_conformer":
                from app import stt_indic

                stt_indic.warmup()
            elif provider == "whisper":
                # Warm it even when it is a fallback: English calls still use
                # Whisper (IndicConformer is only tried for hi/gu), and the
                # first English call must not pay a cold model load.
                from app.voice_handler import _get_stt_model

                _get_stt_model()
        except Exception:                       # noqa: BLE001
            logger.exception("STT warmup failed for %s", provider)


def health() -> dict:
    """Non-secret provider status. Safe to expose in /ready diagnostics."""
    payload: dict = {"providers": provider_order(), "primary": None}
    if payload["providers"]:
        payload["primary"] = payload["providers"][0]
    try:
        from app import stt_sarvam

        payload["sarvam"] = stt_sarvam.health()
    except Exception as exc:                    # noqa: BLE001
        payload["sarvam"] = {"error": str(exc)}
    try:
        from app import stt_indic

        payload["indic_conformer"] = stt_indic.health()
    except Exception as exc:                    # noqa: BLE001
        payload["indic_conformer"] = {"error": str(exc)}
    payload["whisper"] = {"model": settings.WHISPER_MODEL}
    return payload
