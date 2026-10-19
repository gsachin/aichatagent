"""
Real-time voice call handler — runs STT → RAG → LLM → TTS inside a
Twilio Media Streams WebSocket loop.

Reuses the same components that power WhatsApp voice notes:
  - openai-whisper (base) for speech-to-text
  - app.rag.query_rag() for RAG + LLM
  - kokoro_onnx.Kokoro for text-to-speech

Flow (inside the WebSocket media loop):
  1. Accumulate µ-law audio chunks until the caller stops speaking.
  2. Decode → PCM, run Whisper STT.
  3. Feed transcription to the shared RAG module → LLM answer.
  4. Synthesise answer with Kokoro TTS → PCM.
  5. Encode PCM → µ-law, send back through WebSocket.
  6. Listen for the next utterance.

Usage (in app/main.py):
    from app.voice_handler import VoiceCallSession

    session = VoiceCallSession()
    async for out_ulaw in session.handle_media(ulaw_bytes):
        await websocket.send_text(...)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io as _io
import logging
import re
import os
import threading
import time as _time
from pathlib import Path

import numpy as np
from typing import AsyncIterator, Callable

# US-011 TAC-1: settings resolve once, through one loader that also reads .env.
# Reading os.environ here meant the value in force depended on whether some
# other importer had already loaded .env — this module never did.
from app import audio_format
from app.config import settings
from app.languages import (
    CJK_LANGUAGES,
    DEFAULT_FOREIGN_LANGUAGES,
    LATIN_FOREIGN_LANGUAGES,
    configured_foreign_languages,
    contains_cjk,
    min_transcript_chars,
    normalize_language_code,
)
from app.localized_text import CLOSING_REPLIES, NOISE_LADDERS as FOREIGN_NOISE_LADDERS
from app.voice_system_prompt import language_instruction

logger = logging.getLogger("voice_handler")

# Structured call-event timeline (Phase 0 observability): every line is
#   EVENT <call_id> turn=<n> <NAME> [key=value ...]
# so a call can be reconstructed end-to-end from the log.
voice_events = logging.getLogger("voice_events")

# ── Lazy-loaded models (shared across all sessions) ───────────────────

_stt_model = None
_tts_engine = None
_STT_MODEL_SIZE = settings.WHISPER_MODEL  # small / small.en / medium
_SUPPORTED_LANGUAGES = {
    part.strip().lower()
    for part in (settings.SUPPORTED_LANGUAGES or "").split(",")
    if part.strip()
}
_TTS_LANGUAGE_WARNED: set[str] = set()


def _configured_stt_language() -> str | None:
    """None means auto-detect; a code forces that STT language."""
    code = (settings.STT_LANGUAGE or "auto").strip().lower()
    return None if code in ("", "auto") else code


def _get_stt_model():
    """Load faster-whisper on CUDA once and cache (numpy bypass avoids PyAV DLL)."""
    global _stt_model
    if _stt_model is not None:
        return _stt_model

    from faster_whisper import WhisperModel

    # Detect compute device — CTranslate2 has no Metal backend, so Apple
    # Silicon (mps) maps to CPU with NEON-accelerated int8.
    try:
        from app.platform import get_whisper_device_config
        device, compute_type = get_whisper_device_config()
    except Exception:
        device = "cuda" if settings.CUDA_VISIBLE_DEVICES else "cpu"
        compute_type = "int8" if device == "cuda" else "float32"

    logger.info(f"Loading faster-whisper {_STT_MODEL_SIZE} on {device} ({compute_type})...")
    # WHISPER_NUM_THREADS is sized per machine by scripts/predeploy.py.
    # Note: pipecat's WhisperSTTSettings has no cpu_threads param, so this
    # applies to the voice_handler STT path only.
    _stt_model = WhisperModel(
        _STT_MODEL_SIZE,
        device=device,
        compute_type=compute_type,
        cpu_threads=settings.WHISPER_NUM_THREADS,
    )
    logger.info(f"faster-whisper {_STT_MODEL_SIZE} ready on {device}")
    return _stt_model


def _get_tts_engine():
    """Load Kokoro ONNX once and cache.

    Requests the CUDA execution provider, then reports which provider the
    session ACTUALLY got.

    The original version logged "CUDA GPU enabled" whenever
    `ort.get_available_providers()` listed CUDA — that lists providers compiled
    into the wheel, not providers that instantiate. On this box the CUDA
    provider fails ("Require cuDNN 9.* and CUDA 13.*"; torch ships CUDA 12.8)
    and ONNX Runtime silently falls back to CPU, so the log claimed a 5-10x
    speedup that was never happening while the CPU did all the work.

    Measured consequence: RTF ~0.6 on CPU, which is the ~6 s single-session
    synthesis and the 12-16 s N=2 bottleneck. The claim is now read from the
    session itself, so it cannot be true-by-accident again.
    """
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine

    # Ask for CUDA. Whether we GET it is checked below, from the session.
    import onnxruntime as ort
    requested = "CUDAExecutionProvider" if "CUDAExecutionProvider" in \
        ort.get_available_providers() else None
    if requested:
        os.environ["ONNX_PROVIDER"] = requested

    # Make CUDA/cuDNN actually resolvable. On Windows the ORT wheel does NOT
    # bundle them, so the provider fails to instantiate and ONNX Runtime
    # silently falls back to CPU. torch ships CUDA 12.8 + cuDNN 9 and registers
    # its own lib dir on import; preload_dlls() reuses those.
    #
    # This is the difference between RTF 0.63 and RTF 0.06 on this box — the
    # ~6 s single-session synthesis and the 12-16 s two-caller bottleneck.
    # Order matters: torch first, then preload_dlls(), then the session.
    try:
        import torch  # noqa: F401  (registers torch's bundled CUDA/cuDNN dirs)
        ort.preload_dlls()
    except Exception as exc:
        logger.warning("Kokoro TTS: CUDA DLL preload skipped (%s)", exc)

    from kokoro_onnx import Kokoro

    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "pipecat", "kokoro-onnx")
    _tts_engine = Kokoro(
        os.path.join(cache_dir, "kokoro-v1.0.onnx"),
        os.path.join(cache_dir, "voices-v1.0.bin"),
    )

    # The truth, read back from the session that was actually built.
    try:
        active = _tts_engine.sess.get_providers()
        on_gpu = "CUDAExecutionProvider" in active
    except Exception:
        active, on_gpu = ["<unknown>"], False

    if on_gpu:
        logger.info("Kokoro TTS: running on CUDA (%s)", ",".join(active))
    else:
        logger.warning(
            "Kokoro TTS: CPU ONLY — requested %s, session got %s. Synthesis is "
            "~0.6x real-time and contends with everything else on the CPU; this "
            "is the dominant cost of a two-caller turn. See doc/perf/"
            "IMPLEMENTATION_STATUS.md.",
            requested or "(nothing)", ",".join(active),
        )
    return _tts_engine


#: Voice and speaking rate for the LIVE call path.
#:
#: Both keys shipped in .env and were ignored here: the call site hardcoded
#: voice="af_heart", speed=1.0. KOKORO_VOICE was read by app/pipeline.py, so it
#: looked alive in a grep while the Twilio path -- the one that actually serves
#: callers -- never consulted it. Reading them here makes the live path agree
#: with the rest of the stack.
#:
#: Speed is clamped to the range Kokoro renders cleanly; outside it the output
#: degrades into artefacts, which is worse than ignoring the setting. A value
#: that will not parse falls back to 1.0 with a warning rather than raising --
#: a typo in .env must not stop the stack from answering calls.
TTS_VOICE = settings.KOKORO_VOICE


def _parse_tts_speed(raw: str) -> float:
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        logger.warning("KOKORO_SPEED=%r is not a number; using 1.0", raw)
        return 1.0
    clamped = min(max(speed, 0.5), 2.0)
    if clamped != speed:
        logger.warning(
            "KOKORO_SPEED=%.2f is outside the clean-synthesis range 0.5-2.0; "
            "using %.2f", speed, clamped)
    return clamped


TTS_SPEED = _parse_tts_speed(settings.KOKORO_SPEED)

#: US-012 AC-4: the isolation fallback must be a CONFIGURATION change, not a code
#: revert. "shared" is the DG-06 decision (one process-wide cache, reused across
#: callers); "per_call" bounds the cache to a single session's utterances and
#: gives up cross-caller reuse in exchange for isolation that needs no argument.
TTS_CACHE_SCOPE = settings.TTS_CACHE_SCOPE

#: US-005 (streaming TTS, PO build-approved 2026-09-19): the FIRST audio chunk
#: of an answer is sent while the rest is still being synthesised. Behind a
#: flag because adoption is gated on DG-03; the batch path (`_synthesise`) is
#: retained unchanged as the BRD-15 revert (flip the flag, no code change).
TTS_STREAM = settings.TTS_STREAM

#: TTS provider selection. ``kokoro`` is the default and preserves today's
#: behaviour. ``indic_tts`` routes Indic languages to the isolated AI4Bharat
#: service; English still uses Kokoro, and Hindi falls back to Kokoro's
#: hf_alpha if the service is unavailable.
TTS_PROVIDER = settings.TTS_PROVIDER

#: Kokoro voices for the languages it supports. The code is the leading letter
#: of the voice name (a=American English, b=British, e=Spanish, f=French,
#: h=Hindi, i=Italian, j=Japanese, p=Portuguese, z=Chinese).
_KOKORO_LANGUAGE_VOICES = {
    "en": "af_heart",
    "hi": "hf_alpha",
    "es": "ef_dora",
    "fr": "ff_siwis",
    "it": "if_sara",
    "ja": "jf_alpha",
    "pt": "pf_dora",
    "zh": "zf_xiaoxiao",
}

#: espeak-ng language code for kokoro-onnx's phonemizer. `Kokoro.create` and
#: `create_stream` default to ``en-us`` for EVERY voice. That default is
#: audible: Hindi text phonemized as en-us carries `(hi)...(enus)` language
#: switch flags and an English phone set, which is the "foreign accent" heard
#: on live calls (log: "extra phones may appear in the en-us phoneset"). The
#: voice's language, not the library default, must choose the phoneme set.
_KOKORO_PHONEME_LANG = {
    "en": "en-us",
    "hi": "hi",
    "es": "es",
    "fr": "fr-fr",
    "it": "it",
    "ja": "ja",
    "pt": "pt-br",
    "zh": "cmn",
}


def _kokoro_lang_for(language: str | None, voice: str = "") -> str:
    """espeak-ng language for a Kokoro voice.

    English is the only language with two voice families: ``a*`` is American,
    ``b*`` is British. Every other mapped voice has one phoneme set.
    """
    code = normalize_language_code(language or settings.DEFAULT_LANGUAGE or "en")
    if code == "en" and (voice or "").startswith("b"):
        return "en-gb"
    return _KOKORO_PHONEME_LANG.get(code, "en-us")

#: Languages the Indic-TTS release provides. Mirrors app/indic_tts.py; kept
#: here so the hot path does not import the HTTP client just to decide.
_INDIC_TTS_LANGUAGES = frozenset({
    "as", "bn", "brx", "gu", "hi", "kn", "ml", "mni", "mr", "or", "raj", "ta", "te",
})


def _tts_voice_for(language: str | None) -> str:
    """Kokoro voice for a language, falling back to the configured voice.

    An explicitly configured voice wins when it already belongs to the
    requested language, so KOKORO_VOICE stays meaningful. English accepts both
    the American (a*) and British (b*) voice families.
    """
    code = normalize_language_code(language or settings.DEFAULT_LANGUAGE)
    configured = _tts_voice()
    if code == "en" and configured[:1] in ("a", "b"):
        return configured
    mapped = _KOKORO_LANGUAGE_VOICES.get(code)
    if mapped and configured[:1] == mapped[:1]:
        return configured
    return mapped or configured


#: TTS-only readings for Latin tokens that the Japanese/Chinese G2P leaves as
#: unknown symbols (the Kokoro ONNX tokenizer filters unknown characters, which
#: would drop the word from the audio). The transcript and logs keep the
#: original text; only the synthesiser sees the reading.
_TTS_READINGS = {
    "ja": (
        (r"Meridian University", "メリディアン大学"),
        (r"\bMBA\b", "経営学修士"),
        (r"\bB\.?\s?Tech\b", "ビーテック"),
        (r"\bBCA\b", "ビーシーエー"),
        (r"\bB\.?\s?Com\b", "ビーコム"),
    ),
    "zh": (
        (r"Meridian University", "梅里迪安大学"),
        (r"\bMBA\b", "工商管理硕士"),
        (r"\bB\.?\s?Tech\b", "技术学士"),
        (r"\bBCA\b", "计算机应用学士"),
        (r"\bB\.?\s?Com\b", "商学学士"),
    ),
}

_MISAKI_G2P: dict[str, object] = {}
_MISAKI_LOCK = threading.Lock()
_MISAKI_WARNED = False


def _tts_text_for(text: str, language: str | None) -> str:
    """Replace Latin tokens whose reading the ja/zh G2P cannot produce."""
    code = normalize_language_code(language)
    for pattern, replacement in _TTS_READINGS.get(code, ()):
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _kokoro_phonemes(text: str, language: str | None) -> str:
    """Phonemes for Japanese/Chinese via misaki, or "" for every other path.

    ``kokoro-onnx`` 0.5.0 phonemizes every language through espeak-ng. That is
    fine for the Latin languages and Hindi, but it cannot read Japanese kanji:
    it inserts English scaffolding ("(en)chinese letter") into the phoneme
    stream, which is why the round-trip harness heard gibberish. misaki is the
    G2P the Kokoro model was trained with for ja/zh; its phonemes go straight
    to the ONNX model with ``is_phonemes=True``.

    A missing package or a G2P failure falls back to the old path rather than
    breaking a call; boot reports the missing optional dependency.
    """
    global _MISAKI_WARNED
    code = normalize_language_code(language)
    if code not in ("ja", "zh") or not (text or "").strip():
        return ""
    try:
        from misaki import ja, zh
    except Exception:                                    # noqa: BLE001
        if not _MISAKI_WARNED:
            _MISAKI_WARNED = True
            logger.warning(
                "misaki is not installed; ja/zh TTS falls back to espeak-ng, "
                "which misreads kanji/Han. Install misaki[zh] + "
                "pyopenjtalk-plus + fugashi + unidic-lite."
            )
        return ""
    try:
        with _MISAKI_LOCK:
            g2p = _MISAKI_G2P.get(code)
            if g2p is None:
                g2p = ja.JAG2P() if code == "ja" else zh.ZHG2P()
                _MISAKI_G2P[code] = g2p
            phonemes, _tokens = g2p(_tts_text_for(text, code))
        return phonemes or ""
    except Exception:                                    # noqa: BLE001
        logger.exception("misaki G2P failed for %s; using the espeak path", code)
        return ""


def warmup_foreign_g2p() -> None:
    """Load the misaki dictionaries for enabled ja/zh at boot.

    The first Japanese/Chinese synthesis otherwise pays the dictionary load
    (fugashi/unidic or jieba) on the caller's first turn. Never raises: a
    missing G2P is a degradation, not a reason to refuse to boot.
    """
    for code in sorted(configured_foreign_languages()):
        if code in ("ja", "zh"):
            try:
                _kokoro_phonemes("こんにちは" if code == "ja" else "您好", code)
            except Exception:                            # noqa: BLE001
                logger.exception("Foreign G2P warmup failed for %s", code)


def _kokoro_render(engine, text: str, language: str | None,
                   voice: str, speed: float):
    """Batch Kokoro synthesis, using misaki phonemes for ja/zh."""
    phonemes = _kokoro_phonemes(text, language)
    if phonemes:
        return engine.create(
            phonemes, voice=voice, speed=speed, is_phonemes=True
        )
    return engine.create(
        text, voice=voice, speed=speed,
        lang=_kokoro_lang_for(language, voice),
    )


async def _kokoro_stream(engine, text: str, language: str | None,
                         voice: str, speed: float):
    """Streaming Kokoro synthesis, using misaki phonemes for ja/zh.

    Phonemization is CPU-bound and can load the misaki dictionaries on first
    use, so it runs in a worker thread; the event loop keeps serving other
    calls while it runs.
    """
    phonemes = await asyncio.to_thread(_kokoro_phonemes, text, language)
    if phonemes:
        return engine.create_stream(
            phonemes, voice=voice, speed=speed, is_phonemes=True
        )
    return engine.create_stream(
        text, voice=voice, speed=speed,
        lang=_kokoro_lang_for(language, voice),
    )


def _use_indic_tts(language: str | None) -> bool:
    """Does this turn belong to the Indic-TTS service?"""
    code = normalize_language_code(language or settings.DEFAULT_LANGUAGE)
    if code in DEFAULT_FOREIGN_LANGUAGES:
        return False
    return TTS_PROVIDER == "indic_tts" and code in _INDIC_TTS_LANGUAGES


#: Languages routed to Sarvam Bulbul. The demo sets `gu`; English and Hindi
#: remain on Kokoro. Parsed once, like the Kokoro maps above.
_SARVAM_TTS_LANGUAGES = frozenset(
    part.strip().lower()
    for part in (settings.SARVAM_TTS_LANGUAGES or "").split(",")
    if part.strip()
)


def _use_sarvam_tts(language: str | None) -> bool:
    """Does this turn belong to Sarvam Bulbul?

    The key check is deliberately not here: a missing key should flow through
    the adapter as a TTS failure and the fixed-response path, not silently
    select the English Kokoro voice for Gujarati text.
    """
    code = normalize_language_code(language or settings.DEFAULT_LANGUAGE)
    if code in DEFAULT_FOREIGN_LANGUAGES:
        return False
    return TTS_PROVIDER == "sarvam" and code in _SARVAM_TTS_LANGUAGES

#: BUG-07 boundary (`app/voice_grounding.py`): a spoken number or named thing
#: must be traceable to what retrieval returned, or to something the caller
#: said. ON by default; `GROUNDING_ENABLED=0` is the one-line revert, matching
#: the convention the other voice flags use.
GROUNDING_ENABLED = settings.GROUNDING_ENABLED

#: How many turns an unoffered subject stays in the prompt after the caller
#: last named it. Live call `853548f5` is the reason this exists: the block was
#: built from a call-long accumulator, so the model was told on every turn to
#: deny mechanical engineering -- and did, six times, at a caller asking about
#: Computer Science fees. Two turns is enough to hold a position across a
#: follow-up without outliving the conversation it belonged to.
_UNOFFERED_TTL_TURNS = 2

#: The caller telling us to move on. On `853548f5` they said all three of these
#: and were ignored:
#:     "focus only on computer science. I don't need mechanical engineering now."
#:     "I told you to change the context from mechanical engineering to computer science."
#:     "We will only discuss about the computer science."
_CHANGE_CUE = re.compile(
    r"\b(?:change the (?:context|topic|subject)"
    r"|move on|forget (?:it|that)|drop (?:it|that)"
    r"|focus only|only (?:talk|discuss)|don'?t need|do not need"
    r"|not interested in|switch to|rather (?:talk|discuss)|instead)\b",
    re.IGNORECASE,
)


#: BUG-18: the NAME half of the same boundary -- a programme, school or
#: department the corpus does not carry may not be spoken. Separate from
#: `GROUNDING_ENABLED` because this branch was removed once before, on evidence
#: that later turned out to be contaminated by BUG-16. If a live call disagrees
#: with it, this is the setting to turn off while the reason is found, and the
#: rest of the boundary keeps working.
GROUNDING_NAMES_ENABLED = settings.GROUNDING_NAMES_ENABLED


def kb_index():
    """The corpus index the name branch compares against, or None.

    None means "no name checking": either the flag is off, or the index could
    not be read. The guard treats a missing index as no judgement rather than as
    evidence of fabrication -- the BUG-16 lesson, and a guard that cannot see
    must abstain.
    """
    if not GROUNDING_NAMES_ENABLED:
        return None
    try:
        from app.kb_entities import default_index

        return default_index()
    except Exception:                              # noqa: BLE001
        logger.warning("kb index unavailable; name grounding disabled this call")
        return None


def _cache_key_sha(cache_key: tuple) -> str:
    """Short, stable digest of a cache key, safe to write to a trace.

    Deterministic across processes, unlike `hash()` of a str, which is salted
    per interpreter (PYTHONHASHSEED). In-process that salt is harmless; in a log
    it would make two callers' keys for the same utterance look different, and
    the isolation test reads keys across a whole run.

    The digest is truncated to 12 hex characters and is one-way, so no agent
    text lands in the trace (TAC-4).
    """
    import hashlib

    raw = "\x1f".join(str(part) for part in cache_key)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _tts_voice() -> str:
    return TTS_VOICE


def _tts_speed() -> float:
    return TTS_SPEED


#: Kokoro's Japanese voice is paced differently from the Latin voices. With
#: misaki phonemes, 1.0-1.3 transcribe cleanly and 1.4+ starts to smear, so
#: 1.2 is the fast-but-intelligible point. Chinese is stable at 1.2. These
#: multipliers apply only to the six foreign languages; en/hi/gu keep
#: KOKORO_SPEED.
_FOREIGN_KOKORO_SPEED = {"ja": 1.2, "zh": 1.2}


def _tts_speed_for(language: str | None = None) -> float:
    """Kokoro speed for a language (existing languages keep the setting)."""
    code = normalize_language_code(language)
    return _tts_speed() * _FOREIGN_KOKORO_SPEED.get(code, 1.0)


# ── the fixed greeting cache ──────────────────────────────────────────
#: The opening script is fixed per language and direction, but the live path
#: re-synthesised it on every call. Kokoro's batch path bypassed the TTS cache
#: entirely, and ``TTS_CACHE_SCOPE=per_call`` deliberately discards a session's
#: cache at hang-up, so a dedicated cache is the only one that survives calls.
#: Frames are stored already converted to 20 ms µ-law chunks: a hit can go
#: straight to ``_write_frames``, with no model, no synthesiser and no network.
_GREETING_CACHE: dict[tuple, list[bytes]] = {}
_GREETING_CACHE_MAX = 64
_GREETING_CACHE_LOCK = threading.Lock()


def greeting_cache_key(language: str | None, direction: str, text: str) -> tuple:
    """Identity of a greeting: language, direction, wording and voice setup.

    Any configuration change that would produce different audio changes the
    key, so a stale entry can never be served for a new voice or pace.
    """
    code = normalize_language_code(
        language or settings.DEFAULT_LANGUAGE or "en"
    )
    direction = (direction or "inbound").strip().lower()
    if _use_sarvam_tts(code):
        from app import tts_sarvam

        provider = "sarvam_tts"
        voice = tts_sarvam.speaker_for(code)
        speed = float(settings.SARVAM_TTS_PACE)
        variant = (
            f"{settings.SARVAM_TTS_MODEL}|{settings.SARVAM_TTS_SAMPLE_RATE}"
        )
    elif _use_indic_tts(code):
        provider = "indic_tts"
        voice = ""
        speed = _tts_speed_for(code)
        variant = settings.INDIC_TTS_URL
    else:
        provider = "kokoro"
        voice = _tts_voice_for(code)
        speed = _tts_speed_for(code)
        variant = ""
    return (
        code, direction, text, provider, voice, round(float(speed), 3), variant,
    )


def greeting_cache_get(key: tuple) -> list[bytes] | None:
    with _GREETING_CACHE_LOCK:
        frames = _GREETING_CACHE.get(key)
        return list(frames) if frames is not None else None


def greeting_cache_put(key: tuple, frames: list[bytes]) -> None:
    if not frames:
        return
    with _GREETING_CACHE_LOCK:
        if key not in _GREETING_CACHE and len(_GREETING_CACHE) >= _GREETING_CACHE_MAX:
            _GREETING_CACHE.pop(next(iter(_GREETING_CACHE)))
        _GREETING_CACHE[key] = list(frames)


def clear_greeting_cache() -> None:
    with _GREETING_CACHE_LOCK:
        _GREETING_CACHE.clear()


# ── u-law ↔ PCM conversion (stdlib audioop) ───────────────────────────
# Every conversion in the voice path funnels through these two, and both take the
# sample width as an argument — so the number comes from `app/audio_format.py`
# instead of a literal at each call site. Twilio's wire format carries 8-bit
# u-law samples; what these produce and consume is the pipeline's 16-bit linear
# PCM.

def ulaw_to_pcm(ulaw_bytes: bytes) -> bytes:
    import audioop
    return audioop.ulaw2lin(ulaw_bytes, audio_format.SAMPLE_WIDTH)


def pcm_to_ulaw(pcm_bytes: bytes) -> bytes:
    import audioop
    return audioop.lin2ulaw(pcm_bytes, audio_format.SAMPLE_WIDTH)


def frame_rms(ulaw_chunk: bytes) -> float:
    """RMS amplitude of one µ-law frame, on the VAD's own scale.

    Extracted so `is_silent` and the US-014 echo gate measure energy the SAME
    way. Two copies of this would be two definitions of "loud", and the defect
    would surface as a gate whose `BARGE_IN_ABS_FLOOR` silently did not mean
    what `is_silent`'s 80 means -- the same reasoning that keeps `_prepare_audio`
    single-copy for the speculative and real STT passes.
    """
    if len(ulaw_chunk) < 2:
        return 0.0
    try:
        samples = np.frombuffer(ulaw_to_pcm(ulaw_chunk), dtype=np.int16)
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    except Exception:                                   # noqa: BLE001 — never break a call
        return 0.0


def _reference_rms(playout: list[bytes], cursor: int = 0) -> float:
    """RMS of the audio being played out at `cursor`, holding TWO frames.

    Two rather than one (~40 ms) because the echo of the frame being played and
    the echo of the frame before it are both in flight at once, and the carrier
    jitters by a frame in either direction. Holding one frame would let the
    reference momentarily read as silence between two loud frames, and a
    reference of zero is a gate with no ratio at all.
    """
    window = playout[cursor:cursor + 2]
    if not window:
        return 0.0
    return max(frame_rms(f) for f in window)


# ── Standalone helper for main.py greeting ────────────────────────────


def _turn_outcome(chunks: list[bytes]) -> list[bytes]:
    """Record a turn on the four-outcome axis, then pass the audio through.

    US-016 AC-2: `served`, `degraded`, `failed` and `refused` are four different
    things and are never collapsed. `degraded` is recorded where the fixed
    response is played, `refused` where a call is turned away at admission, and
    this covers the other two -- a turn that produced speech, and a turn that
    produced none.
    """
    from app.admission import REGISTRY

    if chunks:
        REGISTRY.note_served()
    else:
        REGISTRY.note_failed()
    return chunks


def load_call_asset_ulaw(name: str, *, chunk: int = 320) -> list[bytes] | None:
    """Read a pre-synthesised call asset as µ-law chunks. No model, no synthesis.

    US-016 AC-5: the fixed response and the busy message are the two sentences
    a caller hears when the machinery is not available to say anything else.
    Both are read from disk here -- this function touches no engine and no
    synthesiser, which is precisely why it still works when one or both are
    gone. Returns None if the asset is missing, and the caller decides what to
    do rather than being handed silence.
    """
    from app.admission import asset_path

    path = asset_path(name)
    if not path.is_file():
        return None
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                logger.warning("call asset %s is not mono 16-bit PCM", name)
                return None
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
        if rate != 8000:
            # Built at carrier rate, so this is a guard rather than a path.
            logger.warning("call asset %s is %d Hz, expected 8000", name, rate)
            return None
    except Exception:
        logger.exception("call asset %s could not be read", name)
        return None

    # `chunk` is BYTES OF 16-BIT PCM: 320 bytes = 160 samples = 20 ms at 8 kHz.
    # The µ-law output per chunk is 160 bytes, which is the media-stream frame
    # size, so a greeting asset and a live stream are framed identically.
    return [pcm_to_ulaw(pcm[i:i + chunk]) for i in range(0, len(pcm), chunk)]


# ── pre-rendered greeting assets ──────────────────────────────────────
#: The greeting is fixed per language and direction, so it can be rendered
#: once by ``scripts/build_call_assets.py`` and loaded from disk on the first
#: call after a restart. The filename carries a digest of the same identity
#: used by the in-memory cache (language, direction, wording, provider, voice,
#: speed, model/sample rate), so a voice or text change produces a new name
#: and the live path runs until the asset is rebuilt -- never stale audio.
def _greeting_digest(key: tuple) -> str:
    payload = "\x1f".join(str(part) for part in key)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def greeting_asset_name(language: str | None, direction: str, text: str) -> str:
    key = greeting_cache_key(language, direction, text)
    code, direction_name = key[0], key[1]
    return f"greeting_{direction_name}_{code}_{_greeting_digest(key)}.wav"


def greeting_asset_frames(
    language: str | None, direction: str, text: str
) -> list[bytes] | None:
    """Ready-to-send µ-law frames for a pre-rendered greeting, or ``None``."""
    frames = load_call_asset_ulaw(greeting_asset_name(language, direction, text))
    return frames or None


def generate_ulaw_greeting(text: str, language: str = "") -> list[bytes]:
    """TTS → µ-law chunker for the fixed opening script.

    The greeting is spoken through the same provider as the answer: Gujarati
    via Sarvam Bulbul (8 kHz linear16) and other languages via Kokoro. Passing
    the call language here is what keeps a Gujarati call from opening in an
    English voice.

    Returns a list of µ-law byte chunks ready for Twilio Media Streams.
    """
    code = normalize_language_code(language or settings.DEFAULT_LANGUAGE or "en")
    if _use_sarvam_tts(code):
        from app import tts_sarvam

        audio, sr = tts_sarvam.synthesise(
            text, code,
            speaker=tts_sarvam.speaker_for(code),
            pace=float(settings.SARVAM_TTS_PACE),
        )
    else:
        kokoro = _get_tts_engine()
        voice = _tts_voice_for(code) if code else _tts_voice()
        if code:
            audio, sr = _kokoro_render(
                kokoro, text, code, voice, _tts_speed_for(code)
            )
        else:
            audio, sr = kokoro.create(
                text, voice=voice, speed=_tts_speed(), lang="en-us"
            )
    from scipy.signal import resample

    target_len = int(len(audio) * 8000 / sr)
    target_len -= target_len % 160
    if target_len <= 0:
        return []
    audio_8k = resample(audio, target_len) if sr != 8000 else audio[:target_len]
    audio_8k_int16 = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)
    chunk_size = 160
    chunks = []
    for i in range(0, len(audio_8k_int16), chunk_size):
        chunk = audio_8k_int16[i : i + chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
        chunks.append(pcm_to_ulaw(chunk.tobytes()))
    return chunks


# ── VoiceCallSession — runs the full AI pipeline per utterance ────────

_CLARIFICATION_RE = re.compile(
    r"(which (specific )?(program|course|intake|semester|term)|"
    r"what (program|course|intake|semester|term)|could you (please )?"
    r"(specify|clarify|tell me)|did you mean|just to confirm|"
    r"are you (looking|asking|interested|referring|planning)|"
    r"fall,? ?spring)",
    re.IGNORECASE,
)


def _is_clarification(text: str) -> bool:
    """True when an assistant turn reads as a clarification question."""
    return "?" in text and bool(_CLARIFICATION_RE.search(text))


# ── STT noise gate + deterministic hangup (pre-LLM guardrails) ────────

STT_MIN_AVG_LOGPROB = settings.STT_MIN_AVG_LOGPROB
STT_MAX_NO_SPEECH_PROB = settings.STT_MAX_NO_SPEECH_PROB
STT_MIN_CHARS = settings.STT_MIN_CHARS
# Utterances shorter than this are treated as noise bursts (15 × 20 ms = 300 ms).
MIN_UTTERANCE_FRAMES = settings.MIN_UTTERANCE_FRAMES

# ── BRD-04: the ONE live end-of-speech decision ─────────────────────────
# "The system shall have exactly one live end-of-speech decision, its delay
#  documented, and its value reconciled explicitly against BRD-02."
#
# This is it. `is_silent()` gates the audio and `feed_audio()` counts trailing
# silence; when the count reaches VAD_SILENCE_FRAMES the turn is closed. There
# is no second decision on this path, and there must never be one -- a second
# VAD with a different delay is two systems disagreeing about when a caller
# stopped talking, which is how a caller gets talked over.
#
# Reconciled against BRD-02 (p50 <= 700 ms for TURN_E2E_MS): this delay is
# measured at 600 ms, i.e. 86% of that budget before STT has run (`AS-04`).
# BRD-02's 700 ms is therefore NOT reachable by tuning this number -- see
# doc/perf/us015-model-decision.md for the measured chain. The value is kept
# because 600 ms is the pause tolerance callers are given, and the fixtures
# encode a 240 ms mid-turn hesitation as a real caller behaviour that must
# survive. Lowering it is not a free win: it splits callers who pause.
VAD_SILENCE_MS = settings.VAD_SILENCE_MS
VAD_SILENCE_FRAMES = max(1, round(VAD_SILENCE_MS / 20))


# ── US-014: the turn state and the ONE inbound-frame decision ───────────
# `doc/sdlc/03-data-state-analysis.md` SM-01 names SPEAKING -> PROCESSING an
# illegal transition, because `MUTE_STT_DURING_TTS` discarded the audio. The
# state existed only as the `tts_playing` local in each WebSocket handler, and
# it was True only while the handler was NOT reading the socket -- so the guard
# that consumed it could never fire. Stating the state on the session, where the
# audio buffer lives, is what makes one decision point possible instead of the
# two copies that existed.
#
# READ THIS BEFORE ADDING A SECOND VAD: the gate below decides when an
# interruption STARTS, never when a turn ENDS. `feed_audio` remains BRD-04's one
# end-of-speech decision. An admitted interruption is appended to the same
# buffer and closed by the same 600 ms rule as any other utterance.
STATE_LISTENING = "LISTENING"
STATE_PROCESSING = "PROCESSING"
STATE_SPEAKING = "SPEAKING"

#: The verdicts of `admit_inbound_frame`. Exactly one of these, per frame.
FRAME_LISTENING = "LISTENING"        # not speaking: an ordinary caller frame
FRAME_INTERRUPTION = "INTERRUPTION"  # admitted while speaking: stop and listen
FRAME_ECHO = "ECHO"                  # rejected: our own playback coming back
FRAME_MUTED = "MUTED"                # BARGE_IN_ENABLED off: the documented discard

BARGE_IN_ENABLED = settings.BARGE_IN_ENABLED
BARGE_IN_ECHO_GAIN = settings.BARGE_IN_ECHO_GAIN
BARGE_IN_MARGIN = settings.BARGE_IN_MARGIN
BARGE_IN_ABS_FLOOR = settings.BARGE_IN_ABS_FLOOR
BARGE_IN_MIN_FRAMES = settings.BARGE_IN_MIN_FRAMES
BARGE_IN_REF_DECAY = settings.BARGE_IN_REF_DECAY
#: The pre-existing AEC workaround, now the gate's OFF-mode behaviour: with
#: BARGE_IN_ENABLED=0 this key decides between discarding the caller's
#: overlapping speech (1, the default) and letting the mic bleed through (0).
#: Read here rather than in the handlers because the decision moved onto the
#: session -- one decision point, TRD-05.
MUTE_STT_DURING_TTS = settings.MUTE_STT_DURING_TTS
#: One frame of carrier audio, and therefore one tick of the playout clock.
FRAME_MS = 20

#: Appended to a history entry when the caller cut in before the answer had
#: finished playing. The prompt's "Interruption Handling" section describes this
#: marking; the two are asserted to agree by `tests/test_us014_barge_in.py`.
TRUNCATION_MARK = ("[the caller began speaking before I finished - they may not "
                   "have heard the end of this]")


#: Read per session, like the VAD knobs, so a deployment can move the bound and
#: a test can vary it without reloading the module.
def _turn_timeout_s() -> float:
    raw = os.environ.get("TURN_TIMEOUT_S", "").strip()
    if not raw:
        return float(settings.TURN_TIMEOUT_S)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("TURN_TIMEOUT_S=%r is not a number; using %s",
                       raw, settings.TURN_TIMEOUT_S)
        return float(settings.TURN_TIMEOUT_S)
    return value if value > 0 else float(settings.TURN_TIMEOUT_S)


# ── Facts the caller has already given (the demo's slot memory) ────────
# The prompt's own "Context Is Mandatory" section promises the model an
# "Important Facts" list and a conversation summary. Nothing produced either:
# the model received the last 6 history lines and nothing else, so a fact given
# on turn 2 was gone by turn 8 and the agent asked for it again. This is the
# cheapest honest fix -- regex over the caller's own words, on the same turn,
# with no extra round trip.
#
# Deliberately NOT an LLM extraction pass. The path this feeds is already over
# its latency budget, and a second model call per turn would spend the whole
# gain of streaming to recover one name.
_FACT_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")),
    ("phone", re.compile(r"(?:\+\d[\d\s().-]{7,}\d|\b\d{10}\b)")),
    ("name", re.compile(
        r"\b(?:my name is|this is|i am|i'?m)\s+([A-Z][a-z]{2,20})\b"
        # The correction form. Matching a bare "it's <Capitalised>" would fire
        # on "it's Computer Science" and "it's Monday"; requiring the contrast
        # ("it's Sam NOT Sean") costs the ability to catch a bare restatement
        # and buys precision on the one case the story names.
        r"|\bit'?s\s+([A-Z][a-z]{2,20})\s+not\b"
        r"|\bnot\s+[A-Z][a-z]{2,20},?\s+it'?s\s+([A-Z][a-z]{2,20})\b")),
    ("program", re.compile(
        # Named programs only. Bare "bachelor"/"master" were here and had to go:
        # "I completed my bachelor degree" is the caller's PRIOR QUALIFICATION,
        # not a program they want, and it was recorded as `program: bachelor`
        # and injected under "do not ask for these again" -- telling the model
        # the caller was interested in a bachelor's programme when they had
        # already finished one. Heard on the 2026-10-10 call. A caller who
        # genuinely wants "a bachelor's in Computer Science" still lands, via
        # the subject name.
        r"\b(B\.?\s?Tech|M\.?\s?Tech|MBA|BBA|BCA|MCA|M\.?Sc|B\.?Sc|"
        r"PhD|Computer Science|Information Technology|"
        r"Data Science|Business Administration)\b", re.IGNORECASE)),
    ("intake", re.compile(
        r"\b(fall|spring|summer|autumn|winter)\s*(?:20\d\d|intake|semester|term)?\b",
        re.IGNORECASE)),
)

#: Slots where a later mention replaces the earlier one (there is one name, one
#: email). `program` is deliberately absent: a caller who names two programs is
#: interested in two programs, and collapsing them loses the one they mentioned
#: second. A correction ("no, it's Sam not Sean") is a new mention of `name`,
#: so last-write-wins already implements the correction.
_FACT_SCALAR_SLOTS = frozenset({"email", "phone", "name", "intake"})

INTRO_GUARD = (
    "The greeting has already been delivered on this call. Never greet, "
    "welcome or introduce yourself again in any wording, and never open a "
    "reply with \"Hello\", \"Hi\" or your own name.\n"
)

#: How many history LINES (caller + agent, so half as many exchanges) are sent
#: as raw transcript. Was a bare `[-6:]` -- three exchanges -- which is why a
#: fact given on turn 2 had fallen out of the prompt by turn 8. 12 lines is six
#: exchanges, and the known-facts block above carries the durable slots for
#: everything older than that. Bounded rather than unbounded because every line
#: is prefill: at p50 4,410 prompt tokens a doubling of the transcript is not
#: free, and this path is measured against a latency budget.
HISTORY_WINDOW_LINES = 12

#: Style rules sent with the caller's question. Tighter than the system
#: prompt's own two-sentence rule because that one is 700 lines away from the
#: question and a 3B model weights the near text most: live answers measured
#: p50 218 chars (~40 words) against a 25-word target.
_VOICE_STYLE_RULES = (
    "Answer naturally as a university admissions advisor, in at most two short "
    "sentences and about 25 words. One question at a time, and only if you need "
    "the answer. Do not repeat back what the caller just told you. Do not "
    "re-ask anything already in the known facts. Never ask the same or similar "
    "clarifying question twice in a row. If the caller repeats a similar "
    "answer, STOP asking — state your best interpretation and answer with "
    "concrete information from the university profile, then invite the caller "
    "to correct you."
)


def _vad_silence_frames() -> int:
    """Read the live end-of-speech delay at SESSION construction.

    Read per call rather than once at import so a deployment can change the
    delay without a rebuild, and so a test can construct a session with a
    different delay without reloading the module. BRD-04's "configuration that
    does not execute shall not be presented as the live setting" cuts both
    ways: a value nobody can reach is not a setting.
    """
    raw = os.environ.get("VAD_SILENCE_MS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return max(1, round(int(raw) / 20))
    return VAD_SILENCE_FRAMES


#: BRD-04 does not forbid closing a turn EARLY if the work has already been
#: done -- it forbids a second *decision*. Speculative STT keeps the single
#: decision and its single 600 ms delay; it merely transcribes the audio the
#: caller has already produced while the remaining silence is still being
#: counted. The result is used only if the audio did not change, so the
#: transcript is identical to the non-speculative one by construction. Set to
#: 0 to disable. Default: start STT 220 ms before the endpoint fires, which is
#: the measured STT p50 (203 ms) rounded up -- starting earlier would usually
#: finish before the window closed and waste a GPU pass on resumed callers.
VAD_SPECULATIVE_ADVANCE_MS = settings.VAD_SPECULATIVE_ADVANCE_MS


def _speculative_advance_ms() -> int:
    """Read the speculative lead at SESSION construction.

    Same reasoning as `_vad_silence_frames`: a value read once at import is a
    value a deployment cannot change and a test cannot vary, which is the
    defect BRD-04 names. Read per session so both can.
    """
    raw = os.environ.get("VAD_SPECULATIVE_ADVANCE_MS", "").strip()
    if raw.isdigit():
        return int(raw)
    return VAD_SPECULATIVE_ADVANCE_MS

_NOISE_WORDS = {"is", "uh", "um", "oh", "ah", "hmm", "hm", "mhm", "eh", "huh"}

#: Fixed reply for gated/noisy input — deliberately bypasses the LLM.
NOISE_REPLY = "Sorry, I didn't quite catch that — could you repeat that?"

#: Escalation after repeated noise-gated turns — the line itself is bad.
LINE_QUALITY_REPLY = (
    "It sounds like the line is breaking up. "
    "Could you try speaking a little closer to the phone?"
)

#: Step 3 — change the channel instead of repeating the same sentence.
ALT_CHANNEL_REPLY = (
    "The connection doesn't seem to be improving. You can also reach us "
    "on WhatsApp or email the admissions office, and we'll get back to "
    "you quickly."
)

#: Step 4+ — human-transfer offer. Never loop the identical sentence.
HUMAN_TRANSFER_REPLY = (
    "I'm sorry, I'm still not able to hear you clearly. I'll ask a human "
    "counselor to call you back. Could you tell me once more which number "
    "we should reach?"
)

_NOISE_LADDER = [NOISE_REPLY, LINE_QUALITY_REPLY, ALT_CHANNEL_REPLY, HUMAN_TRANSFER_REPLY]

#: The same ladder in the caller's language. The fixed English lines used to be
#: spoken to Hindi callers whenever Whisper produced a low-confidence turn --
#: after the caller had explicitly asked for Hindi. Each step stays distinct for
#: the same reason as the English ladder (test_d4_ladder...).
_NOISE_LADDER_HI = [
    "माफ़ कीजिए, मैं ठीक से सुन नहीं पाई। क्या आप दोबारा कह सकते हैं?",
    "लगता है लाइन पर आवाज़ साफ़ नहीं आ रही। कृपया फ़ोन के थोड़ा और पास बोलिए।",
    "कनेक्शन ठीक नहीं हो रहा है। आप हमें WhatsApp पर संदेश भेज सकते हैं, "
    "या एडमिशन ऑफ़िस को ईमेल कर सकते हैं।",
    "माफ़ कीजिए, मैं अब भी साफ़ नहीं सुन पा रही हूँ। मैं किसी सलाहकार से आपको "
    "कॉल करने के लिए कहूँगी। क्या आप अपना नंबर दोबारा बता सकते हैं?",
]

_NOISE_LADDER_GU = [
    "માફ કરશો, મને બરાબર સંભળાયું નહીં. શું તમે ફરી કહી શકશો?",
    "લાઇન પર અવાજ સાફ આવતો નથી. કૃપા કરીને ફોનની થોડી નજીક બોલો.",
    "કનેક્શન ઠીક થઈ રહ્યું નથી. તમે અમને WhatsApp પર સંદેશ મોકલી શકો છો, "
    "અથવા એડમિશન ઓફિસને ઈમેલ કરી શકો છો.",
    "માફ કરશો, હજી પણ સ્પષ્ટ સંભળાતું નથી. હું કોઈ કાઉન્સેલરને તમને કૉલ કરવા "
    "કહીશ. શું તમે તમારો નંબર ફરી કહી શકશો?",
]

_NOISE_LADDERS = {
    "en": _NOISE_LADDER,
    "hi": _NOISE_LADDER_HI,
    "gu": _NOISE_LADDER_GU,
}

# The six foreign ladders live in app/localized_text.py; the three above stay
# byte-identical. `_noise_line` already falls back to English for any code
# without an entry.
_NOISE_LADDERS.update(FOREIGN_NOISE_LADDERS)

# ── D1 output-boundary scrubber ────────────────────────────────────────
# Leaked reasoning/meta narration observed in real calls. Sentence-level
# filter: any sentence containing one of these markers is dropped before
# the reply is spoken or logged. Clean text passes through untouched.
_META_MARKERS = (
    "based on",
    "here's the relevant",
    "university profile",
    "the caller",
    "asked for clarification",
    "let's assume",
    "natural response",
    "this response provides",
    "provide concrete information",
    "without asking",
    "given that",
    "they are interested",
    # Observed live 2026-10-06/07 (46 occurrences in logs/voice_assistant.log):
    # the model announced the reply it was about to give instead of giving it.
    # "Here's a possible response:" / "Here's a response as a university
    # admissions advisor:" were spoken to the caller verbatim -- the marker set
    # above did not contain either phrase, so `scrub_meta_leak` returned the
    # text UNTOUCHED at its fast path and nothing downstream could catch it.
    "possible response",
    "as a university admissions advisor",
)


#: Meta lead-in glued directly to real content ("Here's the relevant
#: information from the university profile: The MBA is…") — strip the
#: prefix so the real content survives.
#:
#: The second alternative covers the scaffold family, where the real answer
#: follows the colon and was otherwise discarded along with the lead-in. Both
#: quotes are left to `_unwrap_quoted` rather than consumed here -- eating only
#: the opening one leaves an orphan `"` at the end of the spoken sentence.
#:
#: The verb is matched as `'?s| is| are` deliberately. Three separate wordings
#: have now been heard on live calls -- "Here's a possible response:",
#: "Here's a response as a university admissions advisor:", "Here is a response
#: to the student's question:" -- and enumerating exact phrases is why the third
#: reached a caller after the first two were fixed. The noun and the optional
#: qualifiers are likewise open, because the model varies those too.
#:
#: This is still pattern-matching a symptom. The same test that removes the
#: 700-line system prompt removes these scaffolds entirely (6/6 clean answers,
#: no lead-in of any kind), so the durable fix is the prompt, not this regex.
_PREFIX_STRIP = re.compile(
    r"^here'?s the relevant information\b[^:\n]*[:：]\s*"
    r"|^here(?:'?s| is| are)\s+"
    # Zero or more qualifiers, each a single determiner or adjective. Repeated
    # rather than a fixed slot because the model stacks them ("some possible
    # responses", "a short sample reply") and a one-slot pattern missed the
    # stacked forms -- which then fell through to the marker check and had the
    # whole sentence DROPPED, losing the real answer with it.
    r"(?:(?:a|an|the|some|my|our|your|possible|potential|sample|suggested|"
    r"natural|appropriate|suitable|short|brief)\s+)*"
    r"(?:responses?|replies?|answers?)\b[^:\n]*[:：]\s*",
    re.IGNORECASE,
)

#: A quote opened by the scaffold above and never closed by the model.
_UNWRAP_QUOTED = re.compile(r"^[\"'“”](.*?)[\"'“”]?$", re.DOTALL)


def _unwrap_quoted(text: str) -> str:
    """Drop the quotes a scaffolded reply was wrapped in, if it was."""
    return _UNWRAP_QUOTED.sub(r"\1", text).strip()


def scrub_meta_leak(text: str) -> str | None:
    """
    Hard output boundary (D1): remove reasoning/meta narration from an
    LLM answer before it reaches TTS. Returns None when nothing usable
    remains after scrubbing. Clean text passes through untouched.
    """
    low = text.lower()
    # The fast path must consult BOTH lists. It used to check only
    # `_META_MARKERS`, so a scaffold that `_PREFIX_STRIP` already knew how to
    # remove was returned untouched before the strip could run -- the marker
    # list and the strip list had to be kept in sync by hand, and a phrase in
    # one but not the other was silently spoken. Heard live on the 2026-10-10
    # call: "Here's a response to the student's question:" went to TTS because
    # no marker contained it, while the pattern above matched it exactly.
    if not any(m in low for m in _META_MARKERS) and not _PREFIX_STRIP.match(text):
        return text  # clean reply — byte-identical pass-through

    sentences = [
        s
        for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*", text)
        if s.strip()
    ]
    kept = []
    for s in sentences:
        s2, stripped = _PREFIX_STRIP.subn("", s)
        if stripped:
            # Only unwrap where a scaffold was actually removed: a caller-facing
            # quote the model wrote on purpose is not ours to edit.
            s2 = _unwrap_quoted(s2)
        if s2.strip() and not any(m in s2.lower() for m in _META_MARKERS):
            kept.append(s2)
    out = re.sub(r"\s{2,}", " ", " ".join(kept)).strip(" .\n")
    # A complete CJK answer can be a few characters; the twelve-character
    # English minimum would replace it with the noise line.
    if len(out) < (2 if contains_cjk(out) else 12):
        return None
    return out

CLOSING_PHRASES_RE = re.compile(
    r"\b(bye|bye[- ]bye|goodbye|see ya|see you|talk later|"
    r"that's all|thank you bye|have a good day)\b",
    re.IGNORECASE,
)

#: Fixed closing line for the deterministic hangup path.
CLOSING_REPLY = "Thank you for calling Meridian University Admissions. Have a great day!"

#: Sign-off cues in the six foreign languages. Deliberately narrow: bare
#: "thanks"/"obrigado"/"salut" are too common mid-conversation to end a call.
_FOREIGN_CLOSING_RE = {
    "es": re.compile(
        r"\b(adiós|adios|hasta luego|chao|eso es todo|nos vemos)\b",
        re.IGNORECASE,
    ),
    "fr": re.compile(
        r"\b(au revoir|à bientôt|a bientot|c'est tout|c’est tout|bonne journée)\b",
        re.IGNORECASE,
    ),
    "it": re.compile(
        r"\b(arrivederci|a presto|è tutto|e tutto|buona giornata)\b",
        re.IGNORECASE,
    ),
    "ja": re.compile(r"(さようなら|さよなら|またね|以上です|終わりです|ありがとうございました)"),
    "pt": re.compile(
        r"\b(tchau|até logo|ate logo|é tudo|e tudo|até mais)\b",
        re.IGNORECASE,
    ),
    "zh": re.compile(r"(再见|再見|拜拜|没有了|就这些|就這些)"),
}


def is_closing_phrase(text: str, language: str | None = None) -> bool:
    """Hard sign-off cues — handled deterministically, before the LLM."""
    if CLOSING_PHRASES_RE.search(text):
        return True
    pattern = _FOREIGN_CLOSING_RE.get(normalize_language_code(language))
    return bool(pattern and pattern.search(text))


def closing_reply(language: str | None = None) -> str:
    """The closing line in the call's language (English fallback)."""
    return CLOSING_REPLIES.get(normalize_language_code(language), CLOSING_REPLY)


def is_noise_fragment(text: str, language: str | None = None) -> bool:
    """Ultra-short or single-noise-word transcripts (STT artifacts).

    The threshold is language-aware for the six foreign languages: "sí" and
    "はい" are meaningful answers, while the three-character English gate would
    discard them. Without a language argument the original rule is unchanged.
    """
    code = normalize_language_code(language)
    if code in configured_foreign_languages():
        t = text.strip().strip(".,!?;。！？、，").lower()
        threshold = min_transcript_chars(code, STT_MIN_CHARS)
    else:
        t = text.strip().strip(".,!?;").lower()
        threshold = STT_MIN_CHARS
    if len(t) < threshold:
        return True
    return len(t.split()) == 1 and t in _NOISE_WORDS


def looks_like_hallucination(text: str, audio_seconds: float) -> bool:
    """Detect Whisper's repetition and over-production failures.

    Observed live on 2026-10-17: 0.92 s of telephone audio produced 191
    characters ("अगर बद्दाना ... वो आप वो आप ...") with avg_logprob -0.46 and a
    low no-speech probability, so the existing log-prob threshold could not
    gate it. Two independent signals catch that shape:

    * more than ~6 words per second of audio (normal speech is 2-4);
    * the same 2-4 word phrase repeated three or more times and covering at
      least half the transcript.

    A caller who genuinely repeats a phrase ("हाँ हाँ हाँ") has a short
    transcript and never hits the coverage test.

    Japanese and Chinese have no whitespace, so a whole transcript is one
    ``split()`` token and both tests are inert. The CJK branch tokenises each
    kana/kanji as a unit (plus Latin words in mixed text) and raises the rate
    limit to 12 units/second, which catches Whisper's 100+ character loops
    without flagging normal 5-8 character/second speech.
    """
    text = text or ""
    if contains_cjk(text):
        tokens = re.findall(
            r"[぀-ヿ㐀-䶿一-鿿豈-﫿]"
            r"|[^\s　-〿＀-￯]+",
            text,
        )
        rate_limit = 12.0
    else:
        tokens = text.split()
        rate_limit = 6.0
    if not tokens:
        return False
    # Below half a second the rate is dominated by endpointing noise; the
    # repetition test below still applies to a long transcript.
    if audio_seconds >= 0.5 and len(tokens) / audio_seconds > rate_limit:
        return True
    if len(tokens) < 8:
        return False
    for n in (2, 3, 4):
        i = 0
        while i + n <= len(tokens):
            repeats = 1
            j = i + n
            while j + n <= len(tokens) and tokens[j:j + n] == tokens[i:i + n]:
                repeats += 1
                j += n
            if repeats >= 3 and repeats * n >= len(tokens) * 0.5:
                return True
            i += 1
    return False


def _split_sentences(text: str) -> list[str]:
    """Split answer text at sentence boundaries for per-sentence streaming.

    kokoro's `create_stream` splits phonemes into batches up to
    MAX_PHONEME_LENGTH (~510 phonemes) and prefers punctuation boundaries —
    but a typical answer (~150-250 chars) is ONE batch, so the stream yields
    one chunk equal to the whole answer and streaming buys nothing (measured
    live 2026-09-20: tts_first_chunk -> tts_done = 0 ms). Synthesising
    sentence by sentence is what makes the first chunk small: the first
    sentence's audio exists while later ones are still being synthesised.

    Conservative split: Latin sentences only on `.` `!` `?` `;` followed by
    whitespace and a capital letter (including the accented capitals used by
    Spanish, French, Italian and Portuguese), never right after a digit;
    Indic scripts split on the danda (`।`/`॥`) because they do not capitalise
    the next sentence; Japanese and Chinese split on `。` `！` `？` with no
    whitespace, because those scripts do not use word spaces. A price like
    "$1,700. Scholarships" stays intact. (An abbreviation like "Dr. Smith"
    still splits into two synthesis chunks; that costs a clause boundary of
    silence, never correctness.)
    """
    parts = re.split(
        r"(?<![0-9][.!?;])(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-ÞŒŸ])"
        r"|(?<=[।॥])\s*"
        r"|(?<=[。！？])\s*",
        text.strip(),
    )
    return [p for p in (p.strip() for p in parts) if p]


class VoiceCallSession:
    """
    Handles a single outbound (or inbound) voice call.

    Accumulates audio, runs STT when the caller pauses, queries the
    RAG + LLM pipeline, synthesises the answer via TTS, and returns
    µ-law audio chunks ready to be sent back over Twilio Media Streams.
    """

    def __init__(
        self,
        silence_threshold_frames: int | None = None,  # None -> the live BRD-04 value
        max_utterance_frames: int = 300,     # ~6 seconds max
        sample_rate: int = 8000,             # Twilio uses 8 kHz
        direction: str = "inbound",          # "inbound" | "outbound" — shapes LLM behavior
        language: str = "",                  # BCP-47-ish code; "" -> DEFAULT_LANGUAGE
    ):
        # BRD-04: one decision, and `None` resolves to the setting rather than
        # to a literal. The parameter used to default to the number 30 while no
        # caller ever passed it, so the documented "configuration" was a
        # constant that nothing could reach.
        self._silence_threshold = (_vad_silence_frames()
                                   if silence_threshold_frames is None
                                   else silence_threshold_frames)
        self._max_utterance = max_utterance_frames
        # Speculative-STT state. `_spec_epoch` invalidates a result whose
        # utterance has already been reset, so a slow transcription can never
        # be mistaken for the next caller turn's audio.
        self._spec_task = None
        self._spec_result = None
        self._spec_epoch = 0
        self._spec_started = False
        self._spec_advance_ms = _speculative_advance_ms()
        self._sample_rate = sample_rate
        self.direction = direction
        self.language = normalize_language_code(
            language or settings.DEFAULT_LANGUAGE
        )
        self._noise_streak = 0  # consecutive noise-gated turns
        self.call_id = ""       # set by the WS handler (stream_sid)

        # US-012: the per-call TTS cache. Unused while TTS_CACHE_SCOPE is
        # "shared" (the DG-06 default), where every session reads and writes the
        # class-level cache instead. It is allocated unconditionally so that
        # switching scope is a configuration change with no code path missing.
        self._session_tts_cache: dict = {}
        self._turn = 0          # utterance counter for the event timeline

        # Audio buffer
        self._audio_buffer: list[bytes] = []      # raw µ-law chunks
        self._silence_count = 0
        self._total_frames = 0

        # US-014: the turn state, and the reference signal the echo gate
        # measures the caller against. `_playout` is the outbound audio the
        # carrier is playing out on a 20 ms clock; one inbound frame consumes
        # one outbound frame, which is what keeps the reference ALIGNED with
        # what the caller is hearing right now rather than with what we sent
        # minutes ago. Without that alignment the gate would measure a quiet
        # passage late in a long answer against the loud passage at its start
        # and admit the agent's own voice as an interruption.
        self.state = STATE_LISTENING
        self._playout: list[bytes] = []
        self._ref_env = 0.0
        self._ref_at: float | None = None
        self._barge_run = 0
        #: Set when a turn began as an admitted interruption, so the min-burst
        #: noise floor can be applied knowingly (see `process_utterance`).
        self._turn_began_as_interruption = False
        #: US-004's cancel signal, produced here rather than left unwired. The
        #: chain below it (rag.query_rag_stream -> llm_backend.generate_stream)
        #: has always consumed one; nothing ever made one, so `cancel` was
        #: permanently None and `GenerationCancelled` was unreachable.
        self._cancel: asyncio.Event | None = None
        #: What the gate did during the last playback window, logged once when
        #: the window closes. Per-frame logging here would be 50 lines/second
        #: and would say nothing a count does not.
        self._barge_counts = {"echo": 0, "short": 0, "admitted": 0}

        # Conversation context (accumulated across utterances)
        self._conversation_history: list[str] = []

        #: Whether the scripted greeting has been delivered on this call. Set by
        #: the WebSocket handler through `note_greeting_spoken`, and read by
        #: `_build_llm_prompt` so the model is told not to introduce itself
        #: again. The greeting is spoken by `_run_greeting` in main.py, which
        #: ran OUTSIDE this class and wrote nothing here -- so the model's
        #: context began at the caller's first utterance, with no record that it
        #: had already said hello. 39 live answers reopened with "Hello, I'm
        #: Alex from Meridian University Admissions".
        self.intro_done = False

        #: Slots the caller has already filled, in their own words, so the model
        #: is never asked for them twice. `{slot: value}` for the scalar slots
        #: and `{slot: [values]}` for `program`. Populated by `note_facts`.
        self._facts: dict[str, object] = {}

        #: Evidence a spoken claim may be grounded in: what retrieval returned
        #: this call, plus what the CALLER said. Deliberately NOT the agent's own
        #: output -- measured during validation, letting the agent's turns into
        #: this list let a fabrication launder itself: turn 6 invented "School of
        #: International Studies", and every later turn that repeated it then
        #: passed the check by citing the earlier invention.
        self._grounding_evidence: list[str] = []

        #: Things the caller has asked about that Meridian does not offer,
        #: accumulated across the call (BUG-17). The corpus decides this, not the
        #: conversation -- `app.kb_entities.unoffered_entities` -- so it does not
        #: depend on the model having noticed anything.
        #:
        #: Accumulated rather than per-turn because the turn that matters has no
        #: entity in it. On call 944b503f the caller asked for mechanical
        #: engineering at turns 7-10, was refused at 11, and then said "You are
        #: hallucinating. You have the details." (14) and "Did you find it out?"
        #: (16). Turn 16 names nothing, and turn 16 is where the agent capitulated
        #: and invented the programme. A per-turn check would be silent exactly
        #: when it is needed.
        self._unoffered: list[str] = []

        #: Turns the boundary has had to refuse, ACROSS THE CALL -- never reset.
        #: The first version counted only CONSECUTIVE refusals, and live call
        #: `853548f5` defeated it by alternating: a good turn reset the count, so
        #: the next refusal started again at line one and the bound never
        #: engaged. A caller who interleaves is not a caller who has stopped
        #: hearing the same thing.
        self._refusals_total = 0

        #: How many times the boundary has refused EACH subject, call-wide. The
        #: specific negative is the right answer and may be said twice; after
        #: that the caller is handed to a human rather than told again.
        self._subject_denied: dict[str, int] = {}

        #: Turn index at which the caller last named each unoffered subject, so
        #: the prompt block can expire. See `_UNOFFERED_TTL_TURNS`.
        self._unoffered_seen: dict[str, int] = {}

        # US-001 turn tracing (app/perf_trace.py). Never raises; may be None.
        self._trace = None

    def note_unoffered(self, utterance: str) -> list[str]:
        """Record anything the caller named that Meridian does not offer.

        Returns what this turn ADDED, so the caller can log once rather than
        once per turn. Backed by the corpus index, so it is a fact about the
        university rather than a judgement about the conversation -- which is
        what makes it safe to keep telling the model.

        Never raises. An entity index that cannot be read must not stop a call.
        """
        try:
            from app.kb_entities import unoffered_entities
        except Exception:                          # noqa: BLE001
            return []
        try:
            found = unoffered_entities(utterance)
        except Exception:                          # noqa: BLE001
            # Loud, not silent. This half of BUG-17 degrades to "the model was
            # never told", which looks exactly like the model ignoring the
            # instruction -- the failure mode that is hardest to diagnose from a
            # transcript afterwards.
            logger.warning("VoiceCall: unoffered-entity detection failed",
                           exc_info=True)
            return []
        added = [e for e in found if e.lower() not in
                 {u.lower() for u in self._unoffered}]
        # Collapse overlap, SHORTEST WINS. The caller saying "mechanical program"
        # yields "mechanical", then "mechanical engineering program" yields
        # "mechanical engineering": the short one adds nothing and reads badly.
        #
        # The first version preferred the LONGER phrase, which was backwards and
        # cost a live call. `mechanical engineering` was already recorded when
        # `mechanical engineering now` arrived; the noisy one was the superstring,
        # so it evicted the clean entry and the list degraded toward garbage as
        # the call went on. Noise must never displace the phrase it grew out of.
        for cand in added:
            low = cand.lower()
            for existing in list(self._unoffered):
                if existing.lower() in low:
                    self._unoffered.remove(existing)
        added = [e for e in added
                 if not any(e.lower() in u.lower() and e.lower() != u.lower()
                            for u in self._unoffered)]
        self._unoffered.extend(added)
        for entity in self._unoffered:
            if entity.lower() in {e.lower() for e in added} or entity in added:
                self._unoffered_seen[entity.lower()] = self._turn
        # A caller who says "change the context", "focus only on X" or "I don't
        # need X" has moved on, and a subject they abandoned must stop being
        # denied at them. Live call `853548f5` kept denying mechanical
        # engineering at a caller asking about Computer Science fees, long after
        # they had said so three times.
        #
        # NAMING A DIFFERENT REAL PROGRAMME counts as moving on too, and the
        # time-to-live cannot see it. Turn 12 of that call: the caller said
        # "computer science." -- and the block, re-armed at turn 11, was still
        # telling the model to deny mechanical engineering, which it did. A
        # caller who names something the university offers is not still asking
        # about the thing it does not.
        if _CHANGE_CUE.search(utterance or "") or self._moved_to_offered(utterance):
            self._unoffered.clear()
            self._unoffered_seen.clear()
            added = []
        return added

    def _moved_to_offered(self, utterance: str) -> bool:
        """Has the caller switched to something real, leaving the subject behind?

        Only when the utterance names a programme the corpus HAS and does not
        also name the subject it would be abandoning -- "mechanical engineering
        or computer science, which do you offer?" must not clear the block.
        """
        try:
            from app.kb_entities import offered_entities
        except Exception:                          # noqa: BLE001
            return False
        try:
            if not offered_entities(utterance):
                return False
        except Exception:                          # noqa: BLE001
            return False
        low = (utterance or "").lower()
        return not any(any(w in low for w in entity.lower().split())
                       for entity in self._unoffered)

    def not_offered_block(self) -> str:
        """What the prompt says about the things Meridian does not offer.

        BUG-17. The agent held the correct refusal for two turns on call
        944b503f and then reversed it at turn 16, after the caller said "You are
        hallucinating. You have the details." No lookup happened: insistence was
        treated as evidence.

        Two halves to the fix, and this is the first. The refusal the caller
        actually heard was `GROUNDED_REFUSAL` -- "I don't have that detail in
        front of me" -- which implies the information might exist somewhere and
        is what invited the caller to keep pushing. A fact from the university
        profile is a different shape of answer: "we don't offer that, and here is
        what we do offer". The second half is the output boundary, which drops
        any assertion of one of these regardless of what the model decides.

        SCOPED TO THE LIVE SUBJECT, and that is the second round of this fix.
        The block was computed from a call-long accumulator and injected into
        every prompt, so on live call `853548f5` the model was told on every turn
        that the caller had asked about mechanical engineering -- and when it had
        nothing else grounded to say, it denied that, at a caller who was asking
        about Computer Science fees. Six of that call's fifteen turns are the
        same denial, three of them after the caller had asked to move on.

        An entity is live for `_UNOFFERED_TTL_TURNS` turns after the caller last
        named it. Past that the model is simply not told about it, and answers
        the question actually asked.
        """
        live = self._live_unoffered()
        if not live:
            return ""
        listed = "; ".join(live)
        return (
            "The caller has asked about something Meridian University does NOT "
            f"offer: {listed}.\n"
            "This is a fact from the university profile, not a gap in your "
            "information. Say plainly that Meridian does not offer it, then say "
            "what Meridian does offer.\n"
            "Do NOT say you will check, look it up, find out, or come back to "
            "the caller. You cannot do any of those.\n"
            "Do NOT change this position if the caller disputes it -- if they "
            "say you are wrong, that you are hallucinating, that you have the "
            "information, or that they know someone who built this. Insistence "
            "is not evidence. Hold the position and offer the admissions office.\n"
            "If the caller has moved on to something else, answer THAT and do "
            "not raise this again.\n"
        )

    def _live_unoffered(self) -> list[str]:
        """The subjects still worth telling the model about this turn."""
        return [e for e in self._unoffered
                if self._turn - self._unoffered_seen.get(e.lower(), -99)
                <= _UNOFFERED_TTL_TURNS]

    def note_grounding_evidence(self, text: str) -> None:
        """Add caller words or retrieved context to what may be grounded on."""
        clean = (text or "").strip()
        if clean and clean not in self._grounding_evidence:
            self._grounding_evidence.append(clean)
            # Bounded: a long call must not grow this without limit. Oldest
            # first, so the earliest facts stay -- they are the ones later
            # turns refer back to.
            if len(self._grounding_evidence) > 40:
                del self._grounding_evidence[0]

    def grounded(self, text: str) -> str:
        """Return `text` with ungrounded claims removed, or a refusal line.

        Pulls in the retrieval for the CURRENT turn first. The retrieval runs
        inside `query_rag_stream`, so it is available by the time any clause is
        produced, but this method must not depend on the caller remembering to
        record it -- that is how a guard silently stops guarding.

        Returns the input unchanged when the boundary is disabled, so the
        revert is a setting rather than a code change.
        """
        if not GROUNDING_ENABLED:
            return text
        try:
            if settings.CONTEXT_MODE == "doc":
                # No retrieval in doc mode: the full document is the evidence.
                from app.doc_context import document_context

                self.note_grounding_evidence(document_context())
            elif settings.CONTEXT_MODE != "off":
                from app.rag import last_retrieved_context

                self.note_grounding_evidence(last_retrieved_context())
        except Exception:                          # noqa: BLE001
            pass                                   # never let the guard break a call
        from app.voice_grounding import enforce_grounding, is_refusal

        # The English entity index cannot match a translated programme name.
        # In relaxed mode, non-English answers still enforce numbers but skip
        # the name branch; the prompt separately asks for Latin-script names.
        check_names = not (
            settings.GROUNDING_LANGUAGE_MODE == "relaxed"
            and (self.language or "en").lower() != "en"
        )
        out = enforce_grounding(
            text,
            "\n".join(self._grounding_evidence),
            entities=kb_index(),
            unoffered=self._unoffered,
            refusal_index=self._refusals_total,
            subject_denied=sum(self._subject_denied.values()),
            check_names=check_names,
            language=self.language,
            log=self.log_event,
        )
        # Count a turn the boundary REPLACED with a refusal. The count is
        # call-wide and never resets: a good turn between two refusals must not
        # re-arm the wording, which is how `853548f5` kept hearing the same
        # denial after every answered question.
        if out != text and is_refusal(out, self.language):
            self._refusals_total += 1
            subject = (self._live_unoffered() or [""])[0]
            if subject and subject.lower() in out.lower():
                self._subject_denied[subject.lower()] = (
                    self._subject_denied.get(subject.lower(), 0) + 1)
        return out

    def note_facts(self, utterance: str) -> dict:
        """Remember what the caller just told us, and return what changed.

        Runs on the caller's own transcript, before the prompt is built, so the
        turn that supplies a fact is also the first turn that cannot re-ask for
        it. Returns only the NEW or CHANGED slots, which is what a test can
        assert on without reaching into the prompt string.
        """
        changed: dict = {}
        if not utterance:
            return changed
        for slot, pattern in _FACT_PATTERNS:
            m = pattern.search(utterance)
            if not m:
                continue
            # Patterns with several alternatives put the capture in whichever
            # branch matched, so take the first group that actually fired.
            value = next((g for g in m.groups() if g), None) or m.group(0)
            value = value.strip()
            if not value:
                continue
            if slot in _FACT_SCALAR_SLOTS:
                if self._facts.get(slot) != value:
                    self._facts[slot] = value
                    changed[slot] = value
            else:
                seen = self._facts.setdefault(slot, [])
                if isinstance(seen, list) and value.lower() not in [
                        str(v).lower() for v in seen]:
                    seen.append(value)
                    changed[slot] = list(seen)
        return changed

    def known_facts_block(self) -> str:
        """The known-facts block for the prompt, or "" when nothing is known."""
        if not self._facts:
            return ""
        lines = []
        for slot, value in self._facts.items():
            shown = ", ".join(str(v) for v in value) if isinstance(value, list) else value
            lines.append(f"- {slot}: {shown}")
        return (
            "Known facts from earlier in this call. Do not ask for these again "
            "and do not read them back to the caller:\n" + "\n".join(lines) + "\n"
        )

    def timeout_fallback(self) -> list[bytes]:
        """What the caller hears when a turn has to be abandoned.

        The same pre-rendered asset the synthesiser-failure path uses: it needs
        no model, no synthesiser and no network, which is the whole point when
        the reason we are here is that something in that chain did not return.
        Public because the bound that produces this lives on the WebSocket
        handler, which owns the timeout -- keeping the asset choice here means
        the caller hears one consistent sentence however a turn fails.
        """
        return self._speak_fixed_response(reason="turn timeout")

    def note_greeting_spoken(self, text: str) -> None:
        """Record the scripted greeting as something the agent has SAID.

        Idempotent: a duplicated `start` event, or the reconnect path that
        deliberately does not re-speak the script, still has to leave the
        greeting in this session's history exactly once. Called before playback
        rather than after it, because an interrupted greeting is still a
        greeting the caller heard the beginning of -- and because the history
        must be correct even if the socket dies mid-script.
        """
        self.intro_done = True
        spoken = " ".join((text or "").split())
        if not spoken:
            return
        entry = f"Assistant: {spoken}"
        if entry in self._conversation_history:
            return
        # First in the transcript, because it is the first thing said.
        self._conversation_history.insert(0, entry)

    def log_event(self, name: str, **fields) -> None:
        """Emit one structured event line for the call timeline."""
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        voice_events.info("EVENT %s turn=%d %s %s", self.call_id or "-", self._turn, name, extra)

    # ── Public API ──────────────────────────────────────────────────

    def reset_utterance(self):
        """Clear the current utterance buffer (call when speech ends)."""
        self._audio_buffer.clear()
        self._silence_count = 0
        self._total_frames = 0
        # Invalidate any in-flight speculation. The epoch, not the snapshot
        # alone, is what makes this safe: the harness loops a four-fixture set,
        # so two turns genuinely can carry byte-identical audio, and a stale
        # result would otherwise be indistinguishable from a fresh one.
        self._spec_epoch += 1
        self._spec_started = False
        self._spec_result = None
        self._spec_task = None

    # ── US-014: the turn state and the ONE inbound-frame decision ──────

    def mark_speaking(self, chunks: list[bytes] | None = None) -> None:
        """Enter SPEAKING, and seed the reference the echo gate compares to.

        `chunks` is the audio being handed to the carrier. It is queued rather
        than measured here because the carrier plays it out on its own clock:
        the reference has to follow the PLAYOUT position, not the send position.

        The reference is PRIMED here, not on the first tick. Without that, a
        session entering SPEAKING has `_ref_env == 0` until an inbound frame
        arrives, so the ratio test degenerates to the absolute floor alone
        exactly at playback onset -- the moment the carrier starts returning our
        voice, and therefore the moment a floor-only gate is most likely to
        admit our own speech as an interruption.
        """
        self.state = STATE_SPEAKING
        self._barge_run = 0
        self._ref_at = _time.monotonic()
        self.note_playback(chunks)
        if self._playout:
            self._ref_env = _reference_rms(self._playout, 0)

    def note_playback(self, frames: list[bytes] | None) -> None:
        """Queue frames the carrier will play out, for the gate's reference.

        Called once per frame actually written to the socket, in send order, so
        the reference tracks what the caller is hearing rather than what was
        synthesised. A frame we never sent is a frame nobody hears, and counting
        it would raise the threshold against the caller for no reason.
        """
        if frames:
            self._playout.extend(frames)

    def playout_remaining(self) -> int:
        """Frames the carrier has been handed but the caller has not heard yet.

        This is the difference the whole story turns on: our send loop finishes
        in milliseconds (633 chunks in 7 ms on the 2026-09-29 inbound call), and
        the carrier then plays those frames out on its own 20 ms clock. "Written"
        and "heard" are therefore seconds apart, and only the second one is a
        reason to stop being SPEAKING.

        The queue is the carrier's buffer (see `note_playback`), drained on the
        wall clock by `_advance_reference` -- advancing it here is what makes
        this a measurement of what is still in the caller's ear rather than a
        count of what is still in our send loop. `_run_voice_turn` holds a turn
        open until this reaches zero.
        """
        self._advance_reference()
        return len(self._playout)

    def mark_listening(self) -> None:
        """Leave SPEAKING: playback finished, or was cancelled, or the stream died.

        Must be on every exit path from a turn. A session left in SPEAKING would
        gate the caller's ordinary speech as echo for the rest of the call.
        """
        if any(self._barge_counts.values()):
            # The gate's own account of the window it was watching. `echo` frames
            # were rejected as our own bleed, `short` runs were speech-shaped but
            # too brief to believe, `admitted` is what interrupted. This is the
            # measurement TAC-4 tunes against, and it is the signal the old
            # `BARGE_IN_DETECTED` claimed to provide per frame and never did once.
            self.log_event("BARGE_IN_SUMMARY", **self._barge_counts)
            self._barge_counts = {"echo": 0, "short": 0, "admitted": 0}
        self.state = STATE_LISTENING
        self._barge_run = 0
        self._playout.clear()
        self._ref_env = 0.0
        # A new playback window starts its own playout clock; keeping the old
        # timestamp would make the first frame of the next window look like a
        # stall of however long the silence lasted.
        self._ref_at = None

    # ── the per-turn cancel signal (US-004's producer) ─────────────────

    def begin_turn(self) -> None:
        """Open a turn and mint its cancel signal.

        Fresh per turn, never reused: an Event left set from the previous
        interruption would cancel the next turn the moment it started.

        Deliberately does NOT clear `_turn_began_as_interruption`: the gate sets
        that when it admits the interrupting frame, which is BEFORE the utterance
        closes and this runs -- clearing it here would erase the only record that
        this turn started as an interruption.
        """
        self._cancel = asyncio.Event()

    def request_interruption(self) -> None:
        """Ask the running turn to stop. Idempotent, and safe before a turn exists."""
        if self._cancel is not None:
            self._cancel.set()

    def cancel_requested(self) -> bool:
        """Has this turn been interrupted? Read by the turn's own send loop."""
        return self._cancel is not None and self._cancel.is_set()

    def end_turn(self) -> None:
        """Close the turn. Does NOT clear the caller's audio buffer."""
        self._cancel = None
        self._turn_began_as_interruption = False
        self.mark_listening()

    def note_answer_truncated(self) -> None:
        """The caller cut in mid-playback: mark the answer they did not finish hearing.

        On the BATCH path (`TTS_STREAM=0`, the shipped configuration) generation
        has already completed by the time an interruption lands -- the turn is
        stopped in its send loop -- so `process_utterance` has already written
        the whole answer into the history. The caller heard only the frames that
        were actually sent before the `clear`.

        The text cannot be truncated to match, because there is no exact mapping
        from sent audio back to characters. So the history keeps the answer and
        gains a marker saying the ending was not heard, which is what the model
        actually needs: without it, the next turn's model believes it delivered
        an answer the caller is still waiting for, and will refuse to repeat it.

        Idempotent, and a no-op when the last entry is not an answer.
        """
        if not self._conversation_history:
            return
        last = self._conversation_history[-1]
        if not last.startswith("Assistant: ") or last.endswith(TRUNCATION_MARK):
            return
        self._conversation_history[-1] = f"{last} {TRUNCATION_MARK}"

    def _advance_reference(self) -> None:
        """Move the reference along the playout clock.

        Driven by `time.monotonic()`, not by inbound frames. Frames are only the
        OPPORTUNITY to sample the playout position; if the inbound stream stalls
        or arrives in a burst, counting frames would let the reference drift
        behind what the caller is hearing and measure them against a passage
        that has already ended. Wall clock cannot drift.

        Catch-up is bounded: more than half a second behind means the stream
        stalled or the process was descheduled, and replaying that much playout
        in one step would produce a reference that means nothing. The floor is
        the honest fallback, so the queue is drained instead.
        """
        now = _time.monotonic()
        if self._ref_at is None:
            self._ref_at = now
        ticks = int(((now - self._ref_at) * 1000.0) // FRAME_MS)
        if ticks <= 0:
            return                      # same frame: the reference holds
        if ticks > 25:                  # > 500 ms behind: re-sync, do not replay
            self._ref_at = now
            self._playout.clear()
            # Decay across the gap that was missed rather than one step: a
            # single 0.95 would leave a threshold standing on a passage that
            # ended over half a second ago, and the floor is the honest fallback
            # when we no longer know what is playing. Bounded so the result is
            # deterministic rather than denormal.
            self._ref_env *= BARGE_IN_REF_DECAY ** min(ticks, 200)
            return
        self._ref_at += ticks * FRAME_MS / 1000.0
        for _ in range(ticks):
            if not self._playout:
                break
            self._playout.pop(0)
        if self._playout:
            self._ref_env = _reference_rms(self._playout, 0)
        else:
            self._ref_env *= BARGE_IN_REF_DECAY

    def admit_inbound_frame(self, ulaw_chunk: bytes) -> str:
        """The ONE place a caller frame is admitted or rejected while we speak.

        TRD-05: one decision point. Both Twilio handlers call this and nothing
        else decides; before US-014 the same four lines were copy-pasted into
        each handler and neither copy could execute.

        Returns exactly one of `FRAME_LISTENING` / `FRAME_INTERRUPTION` /
        `FRAME_ECHO` / `FRAME_MUTED`. The verdict says what the frame IS; the
        caller decides what to do about it. An admitted frame is handed to
        `feed_audio` by the handler so that the buffer, the VAD and BRD-04's one
        end-of-speech decision stay in a single place.
        """
        if self.state != STATE_SPEAKING:
            return FRAME_LISTENING

        if not BARGE_IN_ENABLED:
            if not MUTE_STT_DURING_TTS:
                # The pre-existing escape hatch, kept and still read: a
                # deployment that would rather take the mic bleed than lose the
                # caller's overlap sets this to 0 and gets the frames through.
                # With the gate ON this key is superseded -- the gate does the
                # job it was approximating by doing nothing.
                return FRAME_LISTENING
            # The BRD-15 revert. This is the discard the documents always
            # claimed and the code never did: before the reader split the guard
            # was unreachable, so the real behaviour was to buffer the caller's
            # overlapping speech and feed it back as a fresh turn after playback.
            self.reset_utterance()
            self._advance_reference()
            return FRAME_MUTED

        rms = frame_rms(ulaw_chunk)
        threshold = max(BARGE_IN_ABS_FLOOR,
                        self._ref_env * BARGE_IN_ECHO_GAIN * BARGE_IN_MARGIN)
        self._advance_reference()

        if rms < threshold:
            self._barge_counts["echo"] += 1
            if self._barge_run:
                # A run was counting and ran out: speech-shaped but too brief to
                # believe. Counted separately from plain echo, because the two
                # have different fixes -- `echo` is a gate that is too loose,
                # `short` is one that is too strict.
                self._barge_counts["short"] += 1
            self._barge_run = 0
            return FRAME_ECHO

        self._barge_run += 1
        if self._barge_run < BARGE_IN_MIN_FRAMES:
            # Still counting. A click, a lip smack or a single frame of line
            # noise clears the ratio and stops here; a spoken word does not.
            # These frames are deliberately NOT buffered: holding them would
            # mean replaying audio into the buffer from two places, and the
            # cost of not holding them is the first ~100 ms of the caller's
            # first word. If TAC-4's tuning shows that onset costing the
            # transcript, buffering the run is the contained fix.
            return FRAME_ECHO

        # Believed: the caller is speaking over the agent. From here the frame
        # is ordinary caller audio -- state returns to LISTENING so that the
        # rest of the utterance is not re-litigated frame by frame, and the
        # turn that follows is closed by BRD-04's one endpointing rule.
        self.state = STATE_LISTENING
        self._turn_began_as_interruption = True
        self._barge_counts["admitted"] += 1
        # The evidence for TAC-4: what was heard, what it was measured against,
        # and by how much it cleared it. The threshold is recorded rather than
        # recomputed later, because the reference it came from decays.
        self.log_event("BARGE_IN_ADMITTED", frames=BARGE_IN_MIN_FRAMES,
                       rms=round(rms, 1), ref_env=round(self._ref_env, 1),
                       threshold=round(threshold, 1))
        self.request_interruption()
        return FRAME_INTERRUPTION

    def _maybe_start_speculative_stt(self) -> None:
        """Transcribe the audio-so-far while the pause is still being measured.

        This does NOT close the turn early and does NOT change the delay: the
        caller is still given the full silence window. It only moves the STT
        pass inside that window, so the ~200 ms it costs is no longer added to
        the caller's wait.

        Skipped when there is no running loop (a synchronous caller, e.g. a
        unit test driving feed_audio directly), because there is nothing to
        schedule the transcription onto.
        """
        if not settings.STT_SPECULATIVE_ENABLED:
            return
        # A speculative pass would send the same utterance to the cloud twice
        # and bill it twice. Foreign sessions use local Whisper, so they keep
        # the latency-hiding pass even when the configured primary is Sarvam.
        if self._stt_route_is_cloud():
            return
        if self._spec_advance_ms <= 0 or self._spec_started:
            return
        trigger = self._silence_threshold - round(self._spec_advance_ms / 20)
        if self._silence_count < trigger:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._spec_started = True
        snapshot = b"".join(self._audio_buffer)
        epoch = self._spec_epoch
        self._spec_task = loop.create_task(self._speculative_stt(snapshot, epoch))

    async def _speculative_stt(self, snapshot: bytes, epoch: int) -> None:
        """Background transcription of a snapshot. Never raises into the caller."""
        try:
            audio = self._prepare_audio(snapshot)
            self._spec_result = (epoch, snapshot) + await self._transcribe(audio)
        except Exception:                       # noqa: BLE001
            # A failed speculation is not an error -- the real pass still runs.
            self.log_event("SPECULATIVE_STT_FAILED")
            self._spec_result = None

    def _consume_speculative(self) -> tuple[str, bool] | None:
        """The speculative transcript, if it exists and is still this audio."""
        res = self._spec_result
        if res is None:
            return None
        epoch, snapshot, text, low_conf = res
        if epoch != self._spec_epoch:
            return None                          # a later utterance; stale
        if snapshot != b"".join(self._audio_buffer):
            return None                          # caller resumed; different audio
        self.log_event("STT_FROM_SPECULATIVE", chars=len(text or ""))
        return text, low_conf

    def _prepare_audio(self, ulaw: bytes) -> np.ndarray:
        """µ-law -> resampled 16 kHz int16, for Whisper.

        Extracted so the speculative and the real pass preprocess IDENTICALLY.
        Two copies of this would be two chances to transcribe the same audio
        two different ways, and the bug would show up as an intermittent
        transcript difference on a call.
        """
        pcm_bytes = ulaw_to_pcm(ulaw)
        audio_8k = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            from scipy.signal import resample

            audio_16k = resample(audio_8k, int(len(audio_8k) * 16000 / 8000))
        except Exception:
            audio_16k = np.repeat(audio_8k, 2)
        return (audio_16k * 32767).clip(-32768, 32767).astype(np.int16)

    def _noise_reply(self) -> str:
        """
        Fixed reply for noise-gated turns (D4 escalation ladder):
          1st miss  → plain retry
          2nd miss  → line-quality hint
          3rd miss  → alternate channel (WhatsApp/email)
          4th+ miss → human-transfer offer
        Each step is a DIFFERENT sentence — never the identical line twice
        in a row, which is the defect this ladder exists to prevent.
        """
        self._noise_streak += 1
        return self._noise_line(self._noise_streak - 1)

    def _noise_line(self, index: int) -> str:
        """The escalation line in the language the caller is hearing."""
        ladder = _NOISE_LADDERS.get(self.language, _NOISE_LADDER)
        return ladder[min(max(index, 0), len(ladder) - 1)]

    def is_silent(self, ulaw_chunk: bytes) -> bool:
        """
        Quick energy-based check: is this chunk mostly silence?

        Decodes a few samples and checks RMS amplitude.
        """
        if len(ulaw_chunk) < 2:
            return True
        return frame_rms(ulaw_chunk) < 80  # threshold for 8 kHz µ-law

    def feed_audio(self, ulaw_chunk: bytes) -> bool:
        """
        Feed one µ-law chunk into the session.

        Returns True if the caller has just stopped speaking
        (i.e. we should now process the utterance).
        """
        silent = self.is_silent(ulaw_chunk)

        if not silent:
            # Speech — accumulate
            if not self._audio_buffer:
                self.log_event("USER_SPEECH_STARTED")
            self._audio_buffer.append(ulaw_chunk)
            self._silence_count = 0
            self._total_frames += 1
            # The caller resumed: whatever was speculated is now the wrong
            # audio, and a later pause in this same utterance may speculate
            # again on the longer buffer.
            self._spec_started = False
            self._spec_result = None
        elif self._audio_buffer:
            # Silence AFTER speech — count trailing silence
            self._silence_count += 1
            self._maybe_start_speculative_stt()

        # Trigger on trailing silence OR max utterance length
        if self._audio_buffer and (
            self._silence_count >= self._silence_threshold
            or self._total_frames >= self._max_utterance
        ):
            self.log_event("USER_SPEECH_STOPPED", frames=self._total_frames)
            # US-001: open a trace for this turn. vad_end is the zero point for
            # processing_ms. The 600 ms the caller waited before this instant is
            # endpointing_ms, which BRD-02 measures separately — do not conflate
            # the two clocks. Guarded: tracing never breaks a call.
            try:
                from app.perf_trace import new_trace

                self._trace = new_trace(self.call_id, self._turn)
                self._trace.mark("vad_end")
                self._trace.note(
                    vad_frames=self._total_frames,
                    endpoint_ms=self._silence_threshold * 20,
                    # US-017 T-10: every caller turn is stamped with its work
                    # class, so the priority rule ("zero background starts
                    # during a voice turn") is countable from the records after
                    # the fact rather than only observable live. A rule that
                    # cannot be counted from the record is a rule that can only
                    # be asserted.
                    work_class="voice",
                )
            except Exception:
                self._trace = None
            return True
        return False

    async def process_utterance(self, chunk_sink: Callable | None = None
                                ) -> tuple[list[bytes], str, bool]:
        """
        Run the full pipeline on the accumulated audio:

           µ-law buffer → PCM WAV → Whisper STT → RAG + LLM → Kokoro TTS → µ-law chunks

        Returns a tuple of (ulaw_chunks, transcript_dialogue, end_call) where
        transcript_dialogue is the full exchange (caller + AI) for logging and
        end_call is True when a hard sign-off cue was detected (the caller
        should hang up after playing the returned audio).
        """
        if not self._audio_buffer:
            self.reset_utterance()
            return [], "", False

        self._turn += 1
        self.begin_turn()
        self.log_event("UTTERANCE_PROCESSING", frames=self._total_frames,
                       barge_in=self._turn_began_as_interruption)

        # ── Min-burst filter: drop sub-300ms noise bursts before STT ──
        # US-014: an admitted interruption is held to a lower floor. The 300 ms
        # rule exists to keep a cough or a door slam out of the LLM, and the gate
        # has already applied that test to this audio by a different means -- it
        # cleared the echo ratio for BARGE_IN_MIN_FRAMES and was believed as
        # speech. Re-applying 300 ms would discard the very utterance the caller
        # interrupted to make ("stop", "wait", "no -- "), which is the failure
        # this story exists to fix. This is NOT a second end-of-turn decision:
        # BRD-04 still closes the turn, at 600 ms, exactly once (TAC-5).
        floor = (BARGE_IN_MIN_FRAMES if self._turn_began_as_interruption
                 else MIN_UTTERANCE_FRAMES)
        if self._total_frames < floor:
            logger.info(
                f"VoiceCall: dropped {self._total_frames}-frame burst "
                f"(<{floor * 20} ms) as noise"
            )
            self.reset_utterance()
            return [], "", False

        # ── Step 1: Decode µ-law → 16 kHz mono for Whisper ────────
        combined_ulaw = b"".join(self._audio_buffer)
        audio_16k_int16 = self._prepare_audio(combined_ulaw)

        logger.info(
            f"VoiceCall: processing utterance ({len(self._audio_buffer)} chunks, "
            f"{len(audio_16k_int16)/16000:.1f}s at 16 kHz)"
        )

        # ── Step 2: Whisper STT ───────────────────────────────────
        # Prefer the pass that already ran during the endpointing window. It is
        # the same audio and the same preprocessing, so the transcript is the
        # same one -- but it cost the caller nothing.
        spec = self._consume_speculative()
        if spec is not None:
            transcript, low_conf = spec
            # STT DID happen for this turn -- it happened during the endpointing
            # window, which is the whole point. The stage must still be closed
            # here, or the record loses it: `stt_done` is marked inside
            # `_transcribe`, which this path never calls, and a 200-turn run
            # then reports `stages_seen: 6` with no STT stage at all. The trace's
            # only job is to say where the time went, and a stage that silently
            # vanishes from it is worse than a slow one.
            if self._trace is not None:
                self._trace.mark("stt_done")
                self._trace.note(stt_chars=len(transcript),
                                 stt_low_conf=bool(low_conf),
                                 stt_from_speculative=True)
        else:
            self.log_event("STT_STARTED")
            transcript, low_conf = await self._transcribe(audio_16k_int16)
        self.log_event("STT_FINAL", chars=len(transcript), low_conf=low_conf)
        self.reset_utterance()

        if not transcript or not transcript.strip():
            # VAD removed all audio (pure noise/echo) — one quick check
            # line is better than dead air; the LLM never sees this.
            logger.info("VoiceCall: no speech in utterance — fixed fallback reply")
            reply = self._noise_reply()
            dialogue = f"Caller: (unintelligible)\nAssistant: {reply}"
            self._conversation_history.append(f"Caller: (unintelligible)")
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm, tts_sr = await self._synthesise_with_rate(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, False
            return _turn_outcome(
                self._pcm_to_ulaw_chunks(tts_pcm, tts_sr)
            ), dialogue, False

        logger.info(f"VoiceCall: transcript = \"{transcript[:120]}\"")

        # Add to conversation history
        self._conversation_history.append(f"Caller: {transcript}")

        # What the caller said is itself evidence a claim may be grounded on --
        # if they name a programme and the agent repeats it, that is not a
        # fabrication. Recorded here, before any generation, so the turn that
        # supplies the term is also the first turn that may use it.
        self.note_grounding_evidence(transcript)

        # What the caller just told us, remembered before the prompt is built so
        # THIS turn is already unable to re-ask for it. Skipped for the
        # noise-gated turns below, whose "transcript" is an STT artefact rather
        # than a statement -- extracting a name out of one would poison the
        # slot for the rest of the call with no way to notice.
        if not (low_conf or is_noise_fragment(transcript, self.language)):
            changed = self.note_facts(transcript)
            if changed:
                self.log_event("FACTS_LEARNED", slots=",".join(sorted(changed)))

        # BUG-17: anything the caller named that the university does not offer.
        # Recorded before the prompt is built, and accumulated for the rest of
        # the call, so the turn where the agent is pressured -- which usually
        # names nothing itself -- still carries the position. Gated on the same
        # noise check as the facts above: an STT artefact is not a request.
        if not (low_conf or is_noise_fragment(transcript, self.language)):
            added = self.note_unoffered(transcript)
            if added:
                self.log_event("UNOFFERED_ENTITY", entities="|".join(added)[:160])

        # ── Step 2b: Noise gate — short fragments / low-confidence STT
        #    get a fixed one-line check; the LLM never sees them.
        if low_conf or is_noise_fragment(transcript, self.language):
            logger.info(
                f"VoiceCall: noise-gated \"{transcript[:60]}\" "
                f"(low_conf={low_conf}) — fixed fallback reply"
            )
            reply = self._noise_reply()
            dialogue = f"Caller: {transcript}\nAssistant: {reply}"
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm, tts_sr = await self._synthesise_with_rate(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, False
            return _turn_outcome(
                self._pcm_to_ulaw_chunks(tts_pcm, tts_sr)
            ), dialogue, False

        # Real speech decoded — reset the noise streak.
        self._noise_streak = 0

        # ── Step 2c: Deterministic hangup — hard sign-off cues end the
        #    call with a static closing; the LLM never sees them.
        if is_closing_phrase(transcript, self.language):
            logger.info(f"VoiceCall: closing phrase detected: \"{transcript[:60]}\"")
            reply = closing_reply(self.language)
            dialogue = f"Caller: {transcript}\nAssistant: {reply}"
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm, tts_sr = await self._synthesise_with_rate(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, True
            return _turn_outcome(
                self._pcm_to_ulaw_chunks(tts_pcm, tts_sr)
            ), dialogue, True

        # ── Step 3: RAG + LLM ─────────────────────────────────────
        self.log_event("LLM_STARTED")
        from app.llm_backend import LLM_STREAM

        streamed_chunks: list[bytes] = []
        if LLM_STREAM:
            # US-004: clauses are spoken as the model decodes (the overlap
            # happens only when TTS_STREAM is on too -- without it the clauses
            # accumulate and the batch synthesis below is unchanged).
            # US-014: the cancel signal goes with it, so an interruption stops
            # the decode rather than only muting its output.
            answer, streamed_chunks = await self._query_llm_streamed(
                transcript, chunk_sink if TTS_STREAM else None, cancel=self._cancel)
        else:
            answer = await self._query_llm(transcript)
        self.log_event("LLM_COMPLETED", chars=len(answer or ""))
        if self.cancel_requested():
            # The caller interrupted while we were generating. There is nothing
            # to say and nothing to apologise for: the utterance AFTER this one
            # is already being collected in the buffer, and answering the
            # interruption with the degraded asset ("I'm sorry, I didn't catch
            # that") would respond to a successful interruption with a
            # non-sequitur -- and count the turn `degraded` when it was in fact
            # `served` by being stopped. An empty audio return is correct here.
            #
            # The history entry is what the prompt's contract is written against
            # ("your response appears with [interrupted here]"). On the streamed
            # path `parts` holds only the clauses actually SPOKEN, so the marker
            # lands at the point the caller really cut in; on the batch path
            # nothing was spoken, and saying so is more useful to the model than
            # an empty answer it might try to finish.
            heard = " ".join(answer.split()) if answer else ""
            self._conversation_history.append(
                "Assistant: " + (
                    f"{heard} [interrupted here]"
                    if heard else
                    "[interrupted here - the caller began speaking before I answered]"
                )
            )
            self.log_event("TURN_INTERRUPTED", stage="generation",
                           spoken_chars=len(heard))
            dialogue = f"Caller: {transcript}\nAssistant: (interrupted)"
            return [], dialogue, False
        if not answer:
            # US-016 AC-5: the engine returned nothing. This used to return []
            # -- silence on a live line. The caller now hears the fixed
            # response, played from the asset set, so the turn ends in a
            # sentence rather than in nothing. The record names it as a
            # degraded turn, which is a different outcome from served, failed
            # and refused and is never collapsed into any of them.
            dialogue = f"Caller: {transcript}\nAssistant: (no response)"
            chunks = self._speak_fixed_response(reason="empty generation")
            if chunks:
                return chunks, dialogue, False
            return [], dialogue, False

        # ── Step 3b: D1 output boundary — strip leaked reasoning/meta
        #    narration before anything is spoken.
        scrubbed = scrub_meta_leak(answer)
        if scrubbed != answer:
            self.log_event("META_LEAK_SCRUBBED", before=len(answer), after=len(scrubbed or ""))
        if scrubbed is None:
            logger.warning(f"VoiceCall: answer fully scrubbed as meta-leak: {answer[:100]!r}")
            scrubbed = self._noise_line(0)
        answer = scrubbed

        # BUG-07: the same boundary the streamed path applies per clause.
        answer = self.grounded(answer)

        self._conversation_history.append(f"Assistant: {answer}")
        logger.info(f"VoiceCall: answer ({len(answer)} chars) = \"{answer[:120]}...\"")

        # Build dialogue for transcript logging
        dialogue = f"Caller: {transcript}\nAssistant: {answer}"

        # ── Step 4: Kokoro TTS → PCM ──────────────────────────────
        # US-005 (streaming, behind TTS_STREAM): chunks are converted and
        # forwarded through `chunk_sink` as they are produced, so first audio
        # reaches the socket while later clauses are still synthesising. The
        # batch path below is the unchanged BRD-15 revert (flag off).
        if TTS_STREAM:
            if LLM_STREAM and streamed_chunks:
                # The clauses were already synthesised and sent as they were
                # cut; nothing left to do but record the turn.
                return _turn_outcome(streamed_chunks), dialogue, False
            tts_chunks = await self._synthesise_streamed(answer, chunk_sink)
            if tts_chunks is None or not tts_chunks:
                # US-016 AC-5, second branch: the synthesiser failed before
                # anything audible existed. The fixed response needs no
                # synthesiser, so the caller still hears a sentence.
                chunks = self._speak_fixed_response(reason="synthesis failed")
                if chunks:
                    if chunk_sink is not None:
                        await chunk_sink(chunks)
                    return chunks, dialogue, False
                return [], dialogue, False
            return _turn_outcome(tts_chunks), dialogue, False

        tts_pcm, tts_sr = await self._synthesise_with_rate(answer)
        if tts_pcm is None or len(tts_pcm) == 0:
            # US-016 AC-5, second branch: the synthesiser is the thing that
            # failed. The fixed response needs no synthesiser, so the caller
            # still hears a sentence rather than the tail of a dead turn.
            dialogue = f"Caller: {transcript}\nAssistant: {answer}"
            chunks = self._speak_fixed_response(reason="synthesis failed")
            if chunks:
                return chunks, dialogue, False
            return [], dialogue, False  # Transcript saved even if TTS fails

        # ── Step 5: PCM → µ-law chunks (320 samples = 20 ms at 16 kHz) ──
        return _turn_outcome(
            self._pcm_to_ulaw_chunks(tts_pcm, tts_sr)
        ), dialogue, False

    # ── Domain dictionary for phone audio corrections ──────────────

    _CORRECTIONS = {
        # Meridian-name corrections (common STT mis-hearings on phone audio)
        "maridian": "Meridian", "miridian": "Meridian", "meridien": "Meridian",
        "mary dian": "Meridian",
        # Generic admissions terms (valid for Meridian)
        "intuition": "tuition", "faze": "fees",
        "emma": "MBA", "gp a": "GPA", "i elts": "IELTS",
        "toefl": "TOEFL", "jimat": "GMAT", "g mat": "GMAT",
    }

    def _post_process_transcript(self, text: str) -> str:
        """Apply domain corrections to fix common STT errors on phone audio.

        The correction table is English-specific (``emma`` -> MBA, ``faze`` ->
        fees). Applying it to Devanagari/Gujarati/Gurmukhi output would corrupt
        real words, so any non-ASCII transcript is returned unchanged. A
        foreign-language session is returned unchanged too: unaccented Spanish
        or Portuguese text is still full of words this table would rewrite.
        """
        if self._foreign_stt_language():
            return text.strip()
        if any(ord(ch) > 127 for ch in text):
            return text.strip()
        result = text
        text_lower = result.lower()
        for wrong, correct in self._CORRECTIONS.items():
            if wrong in text_lower:
                # Case-insensitive replacement
                import re
                result = re.sub(wrong, correct, result, flags=re.IGNORECASE)
        return result.strip()

    # ── Internal ──────────────────────────────────────────────────

    async def _transcribe(self, audio_16k_int16: np.ndarray) -> tuple[str, bool]:
        """Transcribe one utterance.

        A session whose language is one of the enabled foreign languages goes
        straight to local faster-whisper: Sarvam and IndicConformer cannot
        serve those languages, and a successful wrong-language Sarvam
        transcript would otherwise short-circuit the chain. Every other
        session runs the existing provider chain unchanged.
        """
        if self._foreign_stt_language():
            return await self._transcribe_whisper(audio_16k_int16)
        return await self._transcribe_default_chain(audio_16k_int16)

    def _foreign_stt_language(self) -> str:
        """The session language when it is an enabled foreign language."""
        code = normalize_language_code(self.language)
        return code if code in configured_foreign_languages() else ""

    def _stt_route_is_cloud(self) -> bool:
        """Does this session's STT route bill a cloud request?

        Foreign sessions always use local Whisper. Every other session keeps
        the configured-primary rule, so the existing Sarvam double-billing
        guard is unchanged.
        """
        if self._foreign_stt_language():
            return False
        return (settings.STT_PROVIDER or "").strip().lower() == "sarvam"

    async def _transcribe_default_chain(
        self, audio_16k_int16: np.ndarray
    ) -> tuple[str, bool]:
        """The pre-foreign provider chain, verbatim.

        Sarvam Saaras is the Indic-first primary, IndicConformer is the offline
        fallback, and faster-whisper is the last resort. A provider failure is
        logged and the next one is tried, so a cloud outage never leaves the
        caller with dead air.
        """
        from app.stt import provider_order

        last_error: Exception | None = None
        for provider in provider_order():
            try:
                if provider == "sarvam":
                    from app import stt_sarvam

                    if not stt_sarvam.is_configured():
                        # Boot reports this once; do not warn on every turn.
                        last_error = RuntimeError("SARVAM_API_KEY is empty")
                        continue
                    return await self._transcribe_sarvam(audio_16k_int16)
                if provider == "indic_conformer":
                    if not self._indic_language():
                        raise RuntimeError(
                            "session language is not Hindi/Gujarati"
                        )
                    return await self._transcribe_indic(audio_16k_int16)
                if provider == "whisper":
                    return await self._transcribe_whisper(audio_16k_int16)
                raise RuntimeError(f"unknown STT provider {provider!r}")
            except Exception as exc:            # noqa: BLE001
                last_error = exc
                logger.warning(
                    "STT provider %s failed (%s); trying the next provider",
                    provider, exc,
                )
        logger.error("All STT providers failed; last error: %s", last_error)
        return "", True

    def _indic_language(self) -> str:
        code = (self.language or "").strip().lower()
        return code if code in ("hi", "gu") else ""

    async def _transcribe_sarvam(
        self, audio_16k_int16: np.ndarray
    ) -> tuple[str, bool]:
        from app import stt_sarvam

        result = await asyncio.to_thread(
            stt_sarvam.transcribe, audio_16k_int16, language_hint=None
        )
        transcript = self._post_process_transcript(result.transcript)
        hallucinated = looks_like_hallucination(
            transcript, len(audio_16k_int16) / 16000.0
        )
        low_conf = bool(hallucinated or not transcript.strip())
        detected = (result.language or "").strip().lower()
        probability = float(result.language_probability or 0.0)
        logger.info(
            "STT(sarvam): \"%s\" (lang=%s, prob=%.2f, %.0f ms, "
            "hallucination=%s, low_conf=%s)",
            transcript[:100], detected or "?", probability,
            result.latency_ms, hallucinated, low_conf,
        )
        self._apply_detected_language(detected, probability, transcript, low_conf)
        if self._trace is not None:
            self._trace.mark("stt_done")
            self._trace.note(
                stt_chars=len(transcript), stt_low_conf=bool(low_conf),
                stt_hallucination=bool(hallucinated),
                stt_language=detected, stt_language_prob=round(probability, 2),
                stt_provider="sarvam", stt_latency_ms=round(result.latency_ms, 1),
                answer_language=self.language,
            )
        return transcript, low_conf

    async def _transcribe_indic(
        self, audio_16k_int16: np.ndarray
    ) -> tuple[str, bool]:
        from app import stt_indic

        language = self._indic_language()
        result = await asyncio.to_thread(
            stt_indic.transcribe, audio_16k_int16, language
        )
        transcript = self._post_process_transcript(result.transcript)
        hallucinated = looks_like_hallucination(
            transcript, len(audio_16k_int16) / 16000.0
        )
        low_conf = bool(hallucinated or not transcript.strip())
        logger.info(
            "STT(indic_conformer): \"%s\" (lang=%s, %.0f ms, "
            "hallucination=%s, low_conf=%s)",
            transcript[:100], language, result.latency_ms,
            hallucinated, low_conf,
        )
        if self._trace is not None:
            self._trace.mark("stt_done")
            self._trace.note(
                stt_chars=len(transcript), stt_low_conf=bool(low_conf),
                stt_hallucination=bool(hallucinated),
                stt_language=language,
                stt_provider="indic_conformer",
                stt_latency_ms=round(result.latency_ms, 1),
                answer_language=self.language,
            )
        return transcript, low_conf

    async def _transcribe_whisper(
        self, audio_16k_int16: np.ndarray
    ) -> tuple[str, bool]:
        """Local faster-whisper STT (the historical path).

        Accepts int16 numpy array directly — no PyAV/file I/O needed,
        which avoids the AppLocker DLL block on this machine.

        Returns (transcript, low_confidence): low_confidence is True when
        the best segment's avg_logprob falls below STT_MIN_AVG_LOGPROB or
        no_speech_prob exceeds STT_MAX_NO_SPEECH_PROB.
        """
        try:
            # `to_thread`, because this LOADS faster-whisper when cold -- the
            # boot warmup normally makes it a cache hit, but a warmup that
            # failed (or a model that was evicted) would otherwise load it on
            # the event loop, mid-call, for every live call at once.
            model = await asyncio.to_thread(_get_stt_model)
            # Convert int16 → float32 for faster-whisper
            audio_float = audio_16k_int16.astype(np.float32) / 32768.0

            # faster-whisper API returns (segments, info) — different from openai-whisper.
            # Greedy decoding (beam_size=1) + condition_on_previous_text=False:
            # on noisy phone audio, beam search and previous-text conditioning
            # are what produce hallucination loops ("Listen to her again").
            segments, info = await asyncio.to_thread(
                lambda: model.transcribe(
                    audio_float,
                    language=_configured_stt_language(),
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(
                        threshold=0.5,
                        min_speech_duration_ms=250,
                        min_silence_duration_ms=500,
                        speech_pad_ms=100,
                    ),
                )
            )
            texts, logprobs, no_speech_probs = [], [], []
            for seg in segments:
                texts.append(seg.text.strip())
                logprobs.append(seg.avg_logprob)
                no_speech_probs.append(seg.no_speech_prob)
            transcript = self._post_process_transcript(" ".join(texts))
            # Whisper's repetition loop is not visible to avg_logprob: the live
            # example scored -0.46 while repeating "वो आप" twenty times. Gate
            # it on the transcript's own shape before the LLM sees it.
            hallucinated = looks_like_hallucination(
                transcript, len(audio_16k_int16) / 16000.0
            )
            low_conf = hallucinated or bool(
                logprobs
                and (
                    min(logprobs) < STT_MIN_AVG_LOGPROB
                    or max(no_speech_probs) > STT_MAX_NO_SPEECH_PROB
                )
            )
            min_logprob = min(logprobs) if logprobs else None
            detected = str(getattr(info, "language", "") or "").lower()
            lang_prob = float(getattr(info, "language_probability", 0.0) or 0.0)
            logger.info(
                f"STT: \"{transcript[:100]}\" "
                f"(lang={info.language}, prob={lang_prob:.2f}, "
                f"min_logprob={f'{min_logprob:.2f}' if min_logprob is not None else 'n/a'}, "
                f"hallucination={hallucinated}, low_conf={low_conf})"
            )
            self._apply_detected_language(detected, lang_prob, transcript, low_conf)
            if self._trace is not None:
                self._trace.mark("stt_done")
                self._trace.note(
                    stt_chars=len(transcript), stt_low_conf=bool(low_conf),
                    stt_hallucination=bool(hallucinated),
                    stt_language=detected, stt_language_prob=round(lang_prob, 2),
                    answer_language=self.language,
                )
            return transcript, low_conf
        except Exception:
            logger.exception("VoiceCall: Whisper STT failed")
            return "", True

    def _apply_detected_language(self, detected: str, probability: float,
                                 transcript: str, low_conf: bool) -> None:
        """Switch the answer language to the language the caller actually spoke.

        Only in ``STT_LANGUAGE=auto``. A low-confidence transcription, a short
        utterance, an unsupported code, or a probability below
        ``STT_LANGUAGE_MIN_PROB`` keeps the session's previous language.

        Foreign policy is deliberately narrower than the Indic one:

        * a call that started in ``en``/``hi``/``gu`` (or any Indic code) can
          never be switched into one of the six foreign languages; its behaviour
          stays exactly what it was before this feature;
        * a call that started in a foreign language may switch to another
          *enabled* foreign language, but not back to ``en``/``hi``/``gu`` —
          explicit selection remains authoritative;
        * a CJK detection needs CJK characters in the transcript, so a short
          proper noun cannot flip a Spanish call to Japanese.
        """
        if _configured_stt_language() is not None:
            return
        if low_conf:
            return

        detected = normalize_language_code(detected)
        current = normalize_language_code(self.language)
        if not detected:
            return

        foreign = configured_foreign_languages()
        current_foreign = current in foreign
        min_chars = (
            min_transcript_chars(detected, STT_MIN_CHARS)
            if current_foreign or detected in DEFAULT_FOREIGN_LANGUAGES
            else STT_MIN_CHARS
        )
        if len(transcript.strip()) < min_chars:
            return

        if current_foreign:
            if detected not in foreign:
                return
        else:
            # A non-foreign call must not drift into a foreign language.
            if detected in DEFAULT_FOREIGN_LANGUAGES:
                return
            if detected not in _SUPPORTED_LANGUAGES:
                return

        if probability < settings.STT_LANGUAGE_MIN_PROB:
            return
        if detected == current:
            return
        if detected in CJK_LANGUAGES and not contains_cjk(transcript):
            return
        if detected in LATIN_FOREIGN_LANGUAGES and contains_cjk(transcript):
            return

        logger.info(
            "STT: detected language %s (prob=%.2f) -> answer language %s",
            detected, probability, detected,
        )
        self.language = detected
        self.log_event("STT_LANGUAGE", language=detected,
                       prob=round(probability, 2))

    async def _query_llm(self, question: str) -> str:
        """Query the shared RAG + LLM pipeline in a thread."""
        try:
            from app.pipeline import run_rag_query_sync

            prompt = self._build_llm_prompt(question)

            # US-001: llm_sent brackets retrieval + prefill + generation. The
            # split between those three comes from the engine counters captured
            # in app/llm_backend.py and the retrieval_done mark from app/rag.py.
            # llm_first_token is deliberately NOT marked here: with a
            # non-streaming call there is no first-token event, and marking this
            # instant would fabricate a TTFT (TRD-21).
            if self._trace is not None:
                self._trace.mark("llm_sent")
            # C3/N1: retrieval embeds the caller's actual utterance, not the
            # assembled instruction prompt (history + style rules dominated
            # the dense vector and diluted the question).
            answer = await asyncio.to_thread(
                run_rag_query_sync, prompt, "voice", question
            ) or ""
            # BUG-18 (batch seam): record the evidence for the grounding
            # boundary HERE, on the loop.
            #
            # `retrieve_context` sets this ContextVar itself, but on this path it
            # runs inside the `to_thread` worker, and `to_thread` copies the
            # calling context IN, not OUT -- the set is discarded when the thread
            # ends. The streamed path was fixed for exactly this (app/rag.py:423)
            # and the batch path was left behind: `last_retrieved_context()` read
            # "" here, so `grounded()` judged every answer against the caller's
            # own words alone. That is not the fail-open case either -- the
            # caller's transcript IS in the evidence list, so the guard did judge,
            # on the wrong evidence, and a correct answer about the university's
            # own programmes is what that produces.
            #
            # `LLM_STREAM=1` is what is deployed, so this is a latent defect, not
            # a live one. Fixed here because it is the same seam and the same
            # one-line shape; a deployment that flips the flag must not silently
            # lose the evidence.
            try:
                if settings.CONTEXT_MODE == "doc":
                    from app.doc_context import document_context

                    self.note_grounding_evidence(document_context())
                elif settings.CONTEXT_MODE != "off":
                    from app.rag import last_retrieved_context

                    self.note_grounding_evidence(last_retrieved_context())
            except Exception:                      # noqa: BLE001
                pass                               # never let the guard break a call
            if self._trace is not None:
                self._trace.mark("llm_done")
                self._trace.note(answer_chars=len(answer))
            return answer
        except Exception:
            logger.exception("VoiceCall: LLM query failed")
            return ""

    async def _query_llm_streamed(
        self,
        question: str,
        chunk_sink: Callable | None,
        cancel: "asyncio.Event | None" = None,
    ) -> tuple[str, list[bytes]]:
        """US-004: the generation is consumed as clauses, and (with a sink and
        TTS_STREAM) each clause is synthesised while the model still decodes —
        the LLM/TTS overlap. Returns (answer_text, ulaw_chunks).

        The first clause's audio can reach the socket while the model is still
        producing the rest; `llm_first_token` is marked at the FIRST delta, so
        the trace carries a real TTFT for streamed turns.
        """
        try:
            from app.llm_backend import ClauseCutter, GenerationCancelled, GenerationFailed
            from app.rag import query_rag_stream

            prompt = self._build_llm_prompt(question)
            cutter = ClauseCutter(language=self.language)
            parts: list[str] = []
            chunks: list[bytes] = []
            any_audio = False
            first_delta = True
            counters = None
            if self._trace is not None:
                self._trace.mark("llm_sent")

            async def _speak_clause(text: str) -> None:
                nonlocal any_audio
                scrubbed = scrub_meta_leak(text)
                if not scrubbed:
                    return
                # BUG-07: nothing gets spoken that the caller's call cannot
                # support. Per clause, because that is the unit that reaches
                # TTS here -- and because a clause is a sentence, which is the
                # granularity the boundary reasons at.
                scrubbed = self.grounded(scrubbed)
                if not scrubbed:
                    return
                parts.append(scrubbed)
                if chunk_sink is None:
                    return
                frames = await self._synthesise_streamed(scrubbed, chunk_sink)
                if frames:
                    chunks.extend(frames)
                    any_audio = True

            cancelled = False
            try:
                # C3/N1: same retrieval-query decoupling as the batch path.
                async for item in query_rag_stream(
                    prompt, cancel=cancel, retrieval_query=question
                ):
                    if isinstance(item, str):
                        if first_delta:
                            first_delta = False
                            self.log_event("LLM_FIRST_TOKEN")
                            if self._trace is not None:
                                self._trace.mark("llm_first_token")
                        for clause in cutter.feed(item):
                            await _speak_clause(clause.text)
                    else:
                        counters = item
            except GenerationCancelled:
                # NOT a failure (AC-3). The caller interrupted and the decode
                # stopped: the trailing clause is deliberately NOT flushed,
                # because finishing the sentence is the exact behaviour US-014
                # exists to remove. Before this handler existed the broad
                # `except Exception` below caught it, logged a traceback and
                # returned ("", []) -- which `process_utterance` reads as an
                # empty engine answer and answers with the degraded "I didn't
                # catch that" asset, i.e. an apology for the caller's own
                # successful interruption.
                cancelled = True
                self.log_event("LLM_CANCELLED")
                logger.info("VoiceCall: generation cancelled by caller interruption")
            except GenerationFailed:
                logger.warning("VoiceCall: streamed generation failed")
            if not cancelled:
                # A complete trailing clause is spoken; a half-sentence never is.
                tail = cutter.flush()
                if tail is not None:
                    await _speak_clause(tail.text)

            # `parts` holds only what was actually SPOKEN, so on a cancel this is
            # the heard prefix -- which is what belongs in the conversation
            # history. Recording the full answer would tell the model it had
            # said things the caller never heard, and it would then refuse to
            # repeat them.
            answer = " ".join(parts)
            if self._trace is not None:
                self._trace.mark("llm_done")
                self._trace.note(answer_chars=len(answer))
                if counters is not None:
                    self._trace.note(
                        prompt_eval_count=counters.prompt_eval_count,
                        eval_count=counters.eval_count,
                    )
            if not answer and any_audio is False:
                return "", []
            return answer, chunks
        except Exception:
            logger.exception("VoiceCall: streamed LLM query failed")
            return "", []

    def _build_llm_prompt(self, question: str) -> str:
        """The prompt the serving path sends (shared by batch and stream).

        Assembly order is deliberate: the facts and the intro guard sit ABOVE
        the transcript because they are instructions about the whole call, and
        the caller's current utterance sits last because that is the text a
        small model weights most heavily.
        """
        head = [f"This is an {self.direction} call."]
        if self.intro_done:
            head.append(INTRO_GUARD)
        facts = self.known_facts_block()
        if facts:
            head.append(facts)
        unoffered = self.not_offered_block()
        if unoffered:
            head.append(unoffered)

        # Include recent conversation for context
        if self._conversation_history:
            context = "\n".join(self._conversation_history[-HISTORY_WINDOW_LINES:])
            prompt = (
                "\n".join(head) + "\n"
                f"Previous conversation:\n{context}\n\n"
                f"The caller just said: \"{question}\"\n"
                f"{_VOICE_STYLE_RULES}"
            )
            # Deterministic loop-breaker: if the assistant's own last
            # two+ turns were clarification questions, rebuild the
            # prompt with caller-only history and force a grounded
            # answer — quantized models sometimes ignore prompt-only
            # guidance when they can see their own question pattern.
            assistant_turns = [
                h for h in self._conversation_history if h.startswith("Assistant: ")
            ]
            clarify_streak = 0
            for h in reversed(assistant_turns):
                if _is_clarification(h):
                    clarify_streak += 1
                else:
                    break
            if clarify_streak >= 2:
                caller_turns = [
                    h for h in self._conversation_history if h.startswith("Caller: ")
                ]
                prompt = (
                    "\n".join(head) + "\n"
                    f"Caller's recent replies:\n" + "\n".join(caller_turns[-4:]) +
                    f"\n\nThe caller just said: \"{question}\"\n"
                    f"You have already asked for clarification too many "
                    f"times. Do NOT ask any question. Answer now with "
                    f"concrete program information from the university "
                    f"profile — duration, fees, eligibility — and let "
                    f"the caller correct you."
                )
        else:
            prompt = "\n".join(head) + f"\n{question}"

        # The language instruction lives in the USER turn, after the stable
        # system + document prefix, so the prompt-cache prefix does not change
        # when the requested language does.
        instruction = language_instruction(self.language)
        if instruction:
            prompt = f"{prompt}\n\n{instruction}"
        return prompt

    #: TTS cache (DG-06 / US-012).
    #:
    #: These are CLASS attributes, so the cache is process-wide: every concurrent
    #: caller shares one dict. That is the DG-06 "use-as-is" decision, and it is
    #: conditional on the N=2 isolation test -- not on the argument that content
    #: keys make sharing obviously safe.
    #:
    #: The key is (agent text, voice, speed) and nothing else -- never caller
    #: speech, caller identity or transcript content (TAC-4). Voice and speed
    #: joined the key when they became configurable in this same change: with
    #: them outside it, editing KOKORO_VOICE or KOKORO_SPEED would have kept
    #: serving audio rendered in the OLD voice from cache.
    #:
    #: The value carries the owning call_id. It is not part of the key and never
    #: affects a lookup; it exists so that "caller B received audio synthesised
    #: for caller A" is measurable rather than arguable (MOD-04 A.5.2).
    _shared_tts_cache: dict[tuple, tuple[np.ndarray, int, str]] = {}
    _tts_cache_max = 50

    async def _synthesise(self, text: str) -> np.ndarray | None:
        """Audio-only wrapper around :meth:`_synthesise_with_rate`.

        Kept because callers and tests that only need the samples should not
        have to unpick a tuple; the sample rate travels with the audio on the
        path that converts it to µ-law.
        """
        audio, _ = await self._synthesise_with_rate(text)
        return audio

    async def _synthesise_with_rate(
        self, text: str
    ) -> tuple[np.ndarray | None, int]:
        """Run TTS in a thread, return (float32 PCM, sample rate).

        The rate is part of the result because providers disagree: Kokoro is
        24 kHz, Sarvam Bulbul is 8 kHz. A batch caller that assumed 24 kHz
        resampled the 8 kHz fallback lines to a third of their length, which
        the caller heard as fast-forwarded noise.

        Default provider is Kokoro. When ``TTS_PROVIDER=indic_tts`` and the
        answer language is one of the AI4Bharat languages, the isolated Indic
        service is used; Kokoro Hindi is the fallback for ``hi`` only.
        """
        self.log_event("TTS_STARTED", chars=len(text))
        try:
            tts_text = text[:500] if len(text) > 500 else text

            # US-012: shared (DG-06 default) or per-call (the pre-decided
            # fallback). Chosen by configuration only -- AC-4 requires the
            # revert to be a setting, never a code change.
            language = (
                getattr(self, "language", "") or settings.DEFAULT_LANGUAGE
            ).strip().lower()
            if _use_sarvam_tts(language):
                provider = "sarvam_tts"
            elif _use_indic_tts(language):
                provider = "indic_tts"
            else:
                provider = "kokoro"
            if provider == "sarvam_tts":
                from app import tts_sarvam

                voice = tts_sarvam.speaker_for(language)
            elif provider == "kokoro":
                voice = _tts_voice_for(language)
            else:
                voice = ""
            if provider == "kokoro" and language not in _KOKORO_LANGUAGE_VOICES:
                if language not in _TTS_LANGUAGE_WARNED:
                    _TTS_LANGUAGE_WARNED.add(language)
                    logger.warning(
                        "Kokoro has no voice for language=%s; using %s. "
                        "Set TTS_PROVIDER=indic_tts for this language.",
                        language, voice,
                    )
            speed = (
                float(settings.SARVAM_TTS_PACE)
                if provider == "sarvam_tts" else _tts_speed_for(language)
            )
            cache = (self._session_tts_cache if TTS_CACHE_SCOPE == "per_call"
                     else VoiceCallSession._shared_tts_cache)
            cache_key = (provider, language, tts_text, voice, speed)

            # Check cache
            if cache_key in cache:
                cached_audio, cached_sr, owner = cache[cache_key]
                # A "cross-call hit" is caller B being served audio that was
                # synthesised during caller A's session (MOD-04 A.5.2). It is
                # recorded, not prevented -- measuring it is the whole point of
                # US-012, and AC-4 pre-decides what a non-zero count means.
                cross_call = bool(owner) and owner != self.call_id
                logger.debug(f"TTS cache HIT: {tts_text[:60]}...")
                self.log_event("TTS_COMPLETED", cached=1, cross_call=int(cross_call))
                # US-001: mark on EVERY return path. This one was missed on the
                # first pass — the cached path returned before the mark, so a
                # cache hit produced a trace with tts_done absent and the
                # llm_done->first_audio segment unaccounted for. Found by
                # running a live call, not by inspection.
                if self._trace is not None:
                    self._trace.mark("tts_done")
                    self._trace.note(
                        tts_cache_hit=True,
                        tts_cache_scope=TTS_CACHE_SCOPE,
                        tts_cache_cross_call=cross_call,
                        tts_cache_owner=owner,
                        tts_cache_key_sha=_cache_key_sha(cache_key),
                    )
                # Copy discipline: the caller gets its own buffer, so a later
                # mutation of one caller's audio cannot reach the cache or any
                # other caller (TAC-2).
                return cached_audio.copy(), cached_sr

            if provider == "sarvam_tts":
                from app import tts_sarvam

                audio, sr = await asyncio.to_thread(
                    tts_sarvam.synthesise, tts_text, language,
                    speaker=voice, pace=speed,
                )
            elif provider == "indic_tts":
                from app import indic_tts

                result = await asyncio.to_thread(
                    indic_tts.synthesise, tts_text, language
                )
                if result is None and language == "hi":
                    # The service is down; Hindi is the one Indic language
                    # Kokoro can still speak. The cache key is rewritten so a
                    # later service recovery is not hidden by this entry.
                    kokoro = await asyncio.to_thread(_get_tts_engine)
                    audio, sr = await asyncio.to_thread(
                        kokoro.create, tts_text,
                        voice=_KOKORO_LANGUAGE_VOICES["hi"], speed=speed,
                        lang="hi",
                    )
                    provider = "kokoro"
                    voice = _KOKORO_LANGUAGE_VOICES["hi"]
                    cache_key = (provider, language, tts_text, voice, speed)
                elif result is None:
                    raise RuntimeError(f"Indic-TTS unavailable for {language}")
                else:
                    audio, sr = result
            else:
                # Only a miss needs the model. Loading it inside the hit path
                # meant a cache hit still touched the engine, which kept the
                # isolation tests from running without a GPU.
                #
                # `to_thread` because this LOADS the Kokoro ONNX model when
                # cold -- and it was never pre-warmed at boot, so the first
                # synthesis of the first call did exactly that, on the event
                # loop, freezing every other live call for the duration. The
                # boot warmup now loads it, so this is normally a cache hit;
                # the thread hop is what makes a cold path survivable.
                kokoro = await asyncio.to_thread(_get_tts_engine)
                audio, sr = await asyncio.to_thread(
                    _kokoro_render, kokoro, tts_text, language, voice, speed,
                )
            # Store in cache. Bound first, FIFO by insertion order (TAC-3):
            # re-inserting an existing key keeps its original position, which is
            # what makes the eviction order predictable.
            if cache_key not in cache and len(cache) >= self._tts_cache_max:
                oldest = next(iter(cache))
                del cache[oldest]
            cache[cache_key] = (audio.copy(), sr, self.call_id)

            logger.info(f"VoiceCall: TTS generated ({len(audio)/sr:.1f}s at {sr} Hz)")
            if self._trace is not None:
                self._trace.mark("tts_done")
                self._trace.note(
                    tts_cache_hit=False,
                    tts_cache_scope=TTS_CACHE_SCOPE,
                    tts_cache_cross_call=False,
                    # The key is recorded on the MISS path too -- this line is
                    # what establishes a key's owner. Without it only hits
                    # carried a key, so nothing could attribute one and the
                    # isolation analysis had no owner map to compare against.
                    tts_cache_key_sha=_cache_key_sha(cache_key),
                    audio_s=round(len(audio) / sr, 2),
                )
            # The cache holds its own copy, so the buffer handed back here is
            # the caller's alone.
            return audio, sr
        except Exception:
            logger.exception("VoiceCall: TTS failed")
            return None, 0

    def _chunk_to_ulaw_frames(self, audio: np.ndarray, sr: int) -> list[bytes]:
        """One synthesiser chunk -> 20 ms µ-law frames (US-005 streaming leg).

        Resamples from the chunk's own sample rate (the stream reports it per
        chunk, so a provider change never silently misresamples) and snaps to
        a whole frame: the sub-frame tail of a MID-STREAM chunk is dropped
        rather than padded, because padding a mid-stream chunk would insert a
        silence gap into the middle of a sentence. The loss is < 20 ms of
        audio at a clause boundary -- inaudible next to the clause pause.
        """
        from scipy.signal import resample

        target = int(len(audio) * 8000 / sr)
        target -= target % 160                      # snap to a whole 20 ms frame
        if target <= 0:
            return []
        audio_8k = resample(audio, target)
        int16 = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)
        return [pcm_to_ulaw(int16[i:i + 160].tobytes())
                for i in range(0, len(int16), 160)]

    async def synthesise_stream(self, text: str, *, voice: str | None = None,
                                speed: float | None = None
                                ) -> AsyncIterator[tuple[np.ndarray, int]]:
        """US-005: yield (audio_f32, sample_rate) chunks as Kokoro produces them.

        Cache hit -> the cached copy is yielded once, immediately. Cache miss ->
        `kokoro.create_stream()` is delegated to a thread per chunk-free await
        (the generator itself runs on the loop; kokoro's async generator does
        the waiting, and the model load stays a to_thread hop). The full audio
        is still assembled and cached so the DAT-11 entry shape is unchanged.

        The first yield marks `tts_first_chunk` on the trace -- the instant
        the first audible audio of the answer existed. A failure BEFORE the
        first chunk re-raises (the caller falls back to the fixed response);
        a failure mid-stream ends the stream (the caller already heard the
        beginning, and the turn is logged degraded, never silent).
        """
        self.log_event("TTS_STARTED", chars=len(text))
        tts_text = text[:500] if len(text) > 500 else text
        language = (
            getattr(self, "language", "") or settings.DEFAULT_LANGUAGE
        ).strip().lower()
        if _use_sarvam_tts(language):
            provider = "sarvam_tts"
        elif _use_indic_tts(language):
            provider = "indic_tts"
        else:
            provider = "kokoro"
        if provider == "sarvam_tts":
            from app import tts_sarvam

            # `voice` from the Kokoro call sites is a Kokoro voice name; the
            # Sarvam speaker is a persona ID and is chosen by language.
            voice = tts_sarvam.speaker_for(language)
        elif provider == "kokoro":
            voice = voice if voice is not None else _tts_voice_for(language)
        else:
            voice = ""
        if provider == "kokoro" and language not in _KOKORO_LANGUAGE_VOICES:
            if language not in _TTS_LANGUAGE_WARNED:
                _TTS_LANGUAGE_WARNED.add(language)
                logger.warning(
                    "Kokoro has no voice for language=%s; using %s. "
                    "Set TTS_PROVIDER=indic_tts for this language.",
                    language, voice,
                )
        if provider == "sarvam_tts":
            speed = speed if speed is not None else float(settings.SARVAM_TTS_PACE)
        else:
            speed = speed if speed is not None else _tts_speed_for(language)
        cache = (self._session_tts_cache if TTS_CACHE_SCOPE == "per_call"
                 else VoiceCallSession._shared_tts_cache)
        cache_key = (provider, language, tts_text, voice, speed)

        if cache_key in cache:
            cached_audio, cached_sr, owner = cache[cache_key]
            cross_call = bool(owner) and owner != self.call_id
            logger.debug(f"TTS cache HIT (stream): {tts_text[:60]}...")
            self.log_event("TTS_COMPLETED", cached=1, cross_call=int(cross_call))
            if self._trace is not None:
                # The audio exists in full now: both marks are true at once.
                self._trace.mark("tts_first_chunk")
                self._trace.mark("tts_done")
                self._trace.note(
                    tts_cache_hit=True,
                    tts_cache_scope=TTS_CACHE_SCOPE,
                    tts_cache_cross_call=cross_call,
                    tts_cache_key_sha=_cache_key_sha(cache_key),
                )
            yield cached_audio.copy(), cached_sr
            return

        if provider == "sarvam_tts":
            # Bulbul HTTP streaming returns raw 8 kHz linear16 PCM as it is
            # generated; each chunk is yielded straight through. If the first
            # chunk fails, re-raise so the caller can fall back; a mid-stream
            # failure ends the turn degraded (the caller already heard audio).
            from app import tts_sarvam

            parts: list[np.ndarray] = []
            sr_out = int(settings.SARVAM_TTS_SAMPLE_RATE)
            first = True
            try:
                async for audio in tts_sarvam.astream(
                        tts_text, language, speaker=voice, pace=speed):
                    if first:
                        first = False
                        self.log_event("TTS_FIRST_CHUNK", samples=int(len(audio)))
                        if self._trace is not None:
                            self._trace.mark("tts_first_chunk")
                    parts.append(audio)
                    yield audio, sr_out
            except Exception:
                logger.exception("VoiceCall: Sarvam streaming TTS failed")
                if first:
                    raise
                return
            if parts:
                audio_full = np.concatenate(parts)
                if cache_key not in cache and len(cache) >= self._tts_cache_max:
                    oldest = next(iter(cache))
                    del cache[oldest]
                cache[cache_key] = (audio_full.copy(), sr_out, self.call_id)
                logger.info(
                    "VoiceCall: TTS streamed via Sarvam (%.1fs at %d Hz)",
                    len(audio_full) / sr_out, sr_out,
                )
                if self._trace is not None:
                    self._trace.mark("tts_done")
                    self._trace.note(
                        tts_cache_hit=False,
                        tts_cache_scope=TTS_CACHE_SCOPE,
                        tts_cache_cross_call=False,
                        tts_cache_key_sha=_cache_key_sha(cache_key),
                        audio_s=round(len(audio_full) / sr_out, 2),
                    )
            return

        if provider == "indic_tts":
            # Indic-TTS is non-streaming: one batch synthesis, then yield the
            # whole result as the single chunk. The caller's µ-law conversion
            # is unchanged because the sample rate travels with the chunk.
            from app import indic_tts

            result = await asyncio.to_thread(
                indic_tts.synthesise, tts_text, language
            )
            if result is None and language == "hi":
                kokoro = await asyncio.to_thread(_get_tts_engine)
                audio, sr = await asyncio.to_thread(
                    kokoro.create, tts_text,
                    voice=_KOKORO_LANGUAGE_VOICES["hi"], speed=speed,
                    lang="hi",
                )
                provider = "kokoro"
                voice = _KOKORO_LANGUAGE_VOICES["hi"]
                cache_key = (provider, language, tts_text, voice, speed)
            elif result is None:
                raise RuntimeError(f"Indic-TTS unavailable for {language}")
            else:
                audio, sr = result
            self.log_event("TTS_FIRST_CHUNK", samples=int(len(audio)))
            if self._trace is not None:
                self._trace.mark("tts_first_chunk")
                self._trace.mark("tts_done")
            if cache_key not in cache and len(cache) >= self._tts_cache_max:
                oldest = next(iter(cache))
                del cache[oldest]
            cache[cache_key] = (audio.copy(), sr, self.call_id)
            yield audio, sr
            return

        kokoro = await asyncio.to_thread(_get_tts_engine)
        parts: list[np.ndarray] = []
        sr_out: int | None = None
        first = True
        try:
            # Per SENTENCE, not per answer: kokoro's internal batching makes a
            # typical answer one chunk (measured: tts_first_chunk -> tts_done
            # = 0 ms live), which streams nothing. A sentence at a time makes
            # the first chunk the first sentence -- small, and produced while
            # the rest of the answer is still to come (US-004's clause cutter
            # will replace this splitter when it lands; the stream interface
            # here does not change).
            for sentence in _split_sentences(tts_text):
                async for audio, sr in await _kokoro_stream(
                        kokoro, sentence, language, voice, speed):
                    if first:
                        first = False
                        self.log_event("TTS_FIRST_CHUNK", samples=int(len(audio)))
                        if self._trace is not None:
                            self._trace.mark("tts_first_chunk")
                    sr_out = sr
                    parts.append(audio)
                    yield audio, sr
        except Exception:
            logger.exception("VoiceCall: streaming TTS failed")
            if first:
                raise           # nothing audible was produced -> fallback path
            return              # partial audio heard; end the turn, degraded

        # Stream complete: assemble and cache, so a repeat pays nothing and
        # the DAT-11 entry shape is unchanged by streaming.
        if parts and sr_out:
            audio_full = np.concatenate(parts)
            if cache_key not in cache and len(cache) >= self._tts_cache_max:
                oldest = next(iter(cache))
                del cache[oldest]
            cache[cache_key] = (audio_full.copy(), sr_out, self.call_id)
            logger.info(f"VoiceCall: TTS streamed ({len(audio_full) / sr_out:.1f}s "
                        f"at {sr_out} Hz)")
            if self._trace is not None:
                self._trace.mark("tts_done")
                self._trace.note(
                    tts_cache_hit=False,
                    tts_cache_scope=TTS_CACHE_SCOPE,
                    tts_cache_cross_call=False,
                    tts_cache_key_sha=_cache_key_sha(cache_key),
                    audio_s=round(len(audio_full) / sr_out, 2),
                )

    async def _synthesise_streamed(self, text: str, sink: Callable | None
                                   ) -> list[bytes] | None:
        """Drive the stream, convert per chunk, forward through the sink.

        Returns the full µ-law chunk list (the batch-path contract, so the
        caller's record-keeping is unchanged) or None when the synthesiser
        failed before any audible chunk existed. The sink is awaited per
        chunk; it is what sends the frames to the socket as produced.
        """
        chunks: list[bytes] = []
        any_audio = False
        try:
            async for audio, sr in self.synthesise_stream(text):
                any_audio = True
                frames = self._chunk_to_ulaw_frames(audio, sr)
                chunks.extend(frames)
                if sink is not None and frames:
                    await sink(frames)
        except Exception:
            logger.exception("VoiceCall: streaming TTS failed")
            return chunks if any_audio else None
        return chunks

    def _speak_fixed_response(self, reason: str) -> list[bytes]:
        """Return the pre-synthesised fixed response as µ-law chunks.

        US-016 / BRD-13: a lost inference engine must end in a deterministic
        fixed response rather than silence. "Deterministic" is literal -- the
        bytes come off disk, so the same failure on two turns in two calls
        produces byte-identical audio, and no model generated any part of it.

        Nothing is synthesised here. That is why this still works when the
        synthesiser is down at the same time as the engine, which is the case
        AC-5 calls out explicitly.

        Returns [] when no asset applies, and the caller then terminates the
        turn cleanly rather than hanging in silence.
        """
        from app.admission import (
            FIXED_RESPONSE_ASSET,
            REGISTRY,
            fixed_response_asset,
        )

        asset = fixed_response_asset(self.language)
        chunks = load_call_asset_ulaw(asset)
        if not chunks and asset != FIXED_RESPONSE_ASSET:
            logger.warning(
                "VoiceCall: fixed response %s missing; using the English asset",
                asset,
            )
            asset = FIXED_RESPONSE_ASSET
            chunks = load_call_asset_ulaw(asset)
        if not chunks:
            logger.error(
                "VoiceCall: fixed response unavailable (%s) and no asset to play. "
                "Build the call assets: .venv/Scripts/python.exe "
                "scripts/build_call_assets.py", reason)
            # No asset applies, so this turn is a genuine failure rather than a
            # degraded one. The caller's turn terminates cleanly and the record
            # says which of the two it was.
            REGISTRY.note_failed()
            return []
        REGISTRY.note_asset_played(asset, call_sid=self.call_id)
        self.log_event("FIXED_RESPONSE", reason=reason, chunks=len(chunks))
        if self._trace is not None:
            # A degraded turn is its own outcome. It is not a failure: the
            # caller heard a sentence and the call continued.
            self._trace.note(outcome="degraded", degraded_reason=reason,
                             fixed_response_played=True)
        return chunks

    def _pcm_to_ulaw_chunks(self, audio: np.ndarray, sr: int) -> list[bytes]:
        """
        Convert a float32 PCM array at ``sr`` to a list of 8 kHz µ-law byte
        chunks suitable for Twilio Media Streams.

        The rate has to travel with the audio: Kokoro returns 24 kHz and
        Sarvam Bulbul returns 8 kHz. Assuming 24 kHz for a Sarvam buffer
        resampled it to a third of its length, so the Gujarati fallback lines
        played three times too fast -- the "fast-forwarded noise" callers
        heard. No default: a forgetful call site must fail loudly, not misplay
        the audio.

        Each chunk is 320 bytes (20 ms at 8 kHz = 160 samples x 2 bytes).
        """
        from scipy.signal import resample

        target_len = int(len(audio) * 8000 / int(sr))
        if target_len <= 0:
            return []
        audio_8k = resample(audio, target_len) if int(sr) != 8000 else audio[:target_len]
        audio_8k_int16 = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)

        # Convert to µ-law in 20 ms chunks
        chunk_size = 160  # 160 samples = 20 ms at 8 kHz
        chunks = []
        for i in range(0, len(audio_8k_int16), chunk_size):
            chunk = audio_8k_int16[i : i + chunk_size]
            if len(chunk) < chunk_size:
                # Pad last chunk with silence
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
            pcm_bytes = chunk.tobytes()
            ulaw_bytes = pcm_to_ulaw(pcm_bytes)
            chunks.append(ulaw_bytes)
        return chunks
