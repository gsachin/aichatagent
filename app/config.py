"""
Transport configuration for the University Admissions Voice Assistant.

All settings are in one place so swapping transports (local WAV harness,
browser mic, or Twilio telephony) is a single-line change.

Loads from .env file if present (python-dotenv), with defaults for development.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Load the project .env from the repo root — anchored to this file so it
# works regardless of the process CWD (e.g. Streamlit launched elsewhere).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    """Get an environment variable with a default."""
    return os.environ.get(key, default)


class ConfigurationError(RuntimeError):
    """A configured value that cannot be used. Raised at START, naming the key.

    US-011's LLD has specified this class since the story was written --
    "`ConfigurationError` (a malformed or unparseable value; raised at **start**,
    never at first use, and naming the key)" -- and it did not exist. What existed
    instead was `int(_env(...))` at 46 call sites, which raises
    `ValueError: invalid literal for int() with base 10: 'notanumber'`: the stack
    stops, which is the correct behaviour, but the operator is told the VALUE and
    not the KEY, and has to read a traceback to find which setting is wrong.

    `US-011` `T-15` asks for both halves at once -- "stops the stack before it
    accepts a call, with the key named in the operator-visible output". Before
    this, the stop was unattributed and the attribution did not stop.

    Subclasses RuntimeError so `except Exception` handlers still see it: the boot
    path must fail loudly rather than swallow a bad setting into a default, which
    is the "set != live" failure this whole module exists to prevent.
    """


def _env_int(key: str, default: int) -> int:
    """An int from the environment, or `default`. A malformed value names the KEY.

    Empty and unset BOTH mean "use the default", which is what the
    `int(_env(k, "600") or 600)` form it replaces meant -- `or` caught the empty
    string, not a zero. A deliberate `0` still resolves to 0.
    """
    raw = _env(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(
            f"{key}={raw!r} is not an integer (default {default}). Fix it in .env, "
            f"or remove the key to use the default."
        ) from None


def _env_float(key: str, default: float) -> float:
    """A float from the environment, or `default`. A malformed value names the KEY.

    `float(default)` on the fallback path, not a bare `default`: several call
    sites write their default as `"2"` rather than `"2.0"`, and the
    `float(_env(...))` form this replaces coerced those to `2.0`. Returning the
    int `2` instead is numerically equal and type-different, which is a silent
    behaviour change -- caught by comparing every resolved field against a
    snapshot, not by reading the diff.
    """
    raw = _env(key, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(
            f"{key}={raw!r} is not a number (default {default}). Fix it in .env, "
            f"or remove the key to use the default."
        ) from None


def _env_int_or(key: str, default: int) -> int:
    """`int(...)` when the value looks like an integer, else `default`.

    For the one call site whose non-numeric value is a FALLBACK rather than a
    crash: app/rag.py's OLLAMA_NUM_PREDICT becomes 192 silently instead of
    raising at import, because a typo there must not take the spoken-answer
    path down. `lstrip("-")` admits a negative, which the call site reads as
    "no ceiling" — the same meaning as 0.
    """
    raw = _env(key, str(default)).strip()
    return int(raw) if raw.lstrip("-").isdigit() else default


@dataclass(frozen=True)
class Settings:
    # ── Transport provider ──────────────────────────────────────────
    # "websocket" = local WAV harness / browser mic (current default)
    # "twilio"    = Twilio Media Streams (requires credentials below)
    TRANSPORT_PROVIDER: str = field(default_factory=lambda: _env("TRANSPORT_PROVIDER", "websocket"))

    # ── FastAPI server ──────────────────────────────────────────────
    HOST: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    PORT: int = field(default_factory=lambda: _env_int("PORT", 8000))

    # ── Audio format (PCM) ──────────────────────────────────────────
    # Deliberately not here. `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`,
    # `AUDIO_SAMPLE_WIDTH` and `CHUNK_FRAMES` were fields that nothing read, and
    # none of them is a deployment setting: the middle two are invariants of the
    # codecs in use (audioop's u-law conversions are mono only, and the pipeline
    # is 16-bit linear PCM), and the last is 20 ms of audio, which follows the
    # rate rather than being independently settable. They are constants in
    # `app/audio_format.py` now (US-011 clause 4). Every key in this class is
    # environment-backed; a number an operator could set to a value that breaks
    # the audio does not belong among them.

    # ── Speech-to-text (faster-whisper) ─────────────────────────────
    # Multilingual by default: the six foreign languages (es/fr/it/ja/pt/zh)
    # need a model that is not English-only. `small.en` still works for an
    # English-only deployment, but it silently cannot serve those languages,
    # so the boot check reports it when FOREIGN_LANGUAGES is enabled.
    WHISPER_MODEL: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    # Sized per machine by scripts/predeploy.py. Note pipecat's
    # WhisperSTTSettings has no cpu_threads param, so this reaches the
    # voice_handler STT path only.
    WHISPER_NUM_THREADS: int = field(
        default_factory=lambda: _env_int("WHISPER_NUM_THREADS", 4)
    )
    # auto = let Whisper detect the spoken language per utterance (requires a
    # multilingual model such as "small"/"medium", not "small.en"). A fixed
    # code (en/hi/gu/...) forces that language for STT.
    STT_LANGUAGE: str = field(
        default_factory=lambda: _env("STT_LANGUAGE", "auto").strip().lower()
    )
    # Detection below this probability keeps the session's previous language.
    # Short phone utterances can score low; the value is deliberately
    # configurable and logged on every turn.
    STT_LANGUAGE_MIN_PROB: float = field(
        default_factory=lambda: _env_float("STT_LANGUAGE_MIN_PROB", 0.5)
    )
    # Changing the ANSWER language is a larger claim than accepting a
    # detection. On 8 kHz telephony, Indic LID labels a single loanword
    # ("admission") as another language with 0.6-0.7 confidence; one such
    # utterance must not rewrite the rest of the call. A switch therefore
    # needs a higher probability and enough words to be prose. Explicit
    # selections (IVR, operator, spoken request) ignore both and are
    # authoritative.
    STT_LANGUAGE_SWITCH_MIN_PROB: float = field(
        default_factory=lambda: _env_float(
            "STT_LANGUAGE_SWITCH_MIN_PROB", 0.75
        )
    )
    STT_LANGUAGE_SWITCH_MIN_CHARS: int = field(
        default_factory=lambda: _env_int("STT_LANGUAGE_SWITCH_MIN_CHARS", 12)
    )
    # A presence check, not a value: `_get_stt_model` picks cuda when this is
    # set and non-empty. Kept as the raw string so the truthiness test is
    # unchanged — "0" selects cuda here exactly as it did before, which is the
    # behaviour CUDA_VISIBLE_DEVICES is documented to have.
    CUDA_VISIBLE_DEVICES: str = field(
        default_factory=lambda: _env("CUDA_VISIBLE_DEVICES", "")
    )

    # ── STT provider (2026-10) ──────────────────────────────────────
    # whisper          — local faster-whisper, the historical path
    # sarvam           — Sarvam Saaras v4 REST (Indic-first, 8 kHz telephony)
    # indic_conformer  — AI4Bharat IndicConformer-600M via onnx-asr (offline)
    # The fallback list is tried in order when the primary fails. Whisper is
    # always last-resort (the loop does not add it implicitly).
    STT_PROVIDER: str = field(
        default_factory=lambda: _env("STT_PROVIDER", "whisper").strip().lower()
    )
    STT_FALLBACK_PROVIDERS: str = field(
        default_factory=lambda: _env(
            "STT_FALLBACK_PROVIDERS", "indic_conformer,whisper"
        ).strip().lower()
    )
    # Sarvam's REST endpoint accepts <=30 s; the session sends one utterance.
    SARVAM_API_KEY: str = field(default_factory=lambda: _env("SARVAM_API_KEY", ""))
    SARVAM_STT_URL: str = field(
        default_factory=lambda: _env(
            "SARVAM_STT_URL", "https://api.sarvam.ai/speech-to-text"
        )
    )
    SARVAM_STT_MODEL: str = field(
        default_factory=lambda: _env("SARVAM_STT_MODEL", "saaras:v4")
    )
    # codemix keeps English words (B.Tech, MBA, fees) in Latin script, which is
    # both natural for Indian callers and compatible with the grounding boundary.
    SARVAM_STT_MODE: str = field(
        default_factory=lambda: _env("SARVAM_STT_MODE", "codemix")
    )
    SARVAM_STT_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("SARVAM_STT_TIMEOUT_S", 8.0)
    )
    # Comma-separated; empty uses the built-in DSPMU domain list.
    SARVAM_STT_KEYTERMS: str = field(
        default_factory=lambda: _env("SARVAM_STT_KEYTERMS", "")
    )
    # Offline fallback. The OpenVoiceOS repositories are the ONNX exports of
    # AI4Bharat's IndicConformer; int8 is ~138 MB per language.
    INDIC_CONFORMER_MODEL_HI: str = field(
        default_factory=lambda: _env(
            "INDIC_CONFORMER_MODEL_HI",
            "OpenVoiceOS/ai4bharat-indicconformer-hi-onnx",
        )
    )
    INDIC_CONFORMER_MODEL_GU: str = field(
        default_factory=lambda: _env(
            "INDIC_CONFORMER_MODEL_GU",
            "OpenVoiceOS/ai4bharat-indicconformer-gu-onnx",
        )
    )
    INDIC_CONFORMER_QUANTIZATION: str = field(
        default_factory=lambda: _env("INDIC_CONFORMER_QUANTIZATION", "int8")
    )
    # cpu | cuda. CPU int8 is ~0.25 RTF on this box and avoids contention with
    # Kokoro/Whisper on the 16 GB card.
    INDIC_CONFORMER_DEVICE: str = field(
        default_factory=lambda: _env("INDIC_CONFORMER_DEVICE", "cpu").strip().lower()
    )
    # Cloud STT is billed per request; a speculative pass would bill the same
    # utterance twice. The live dispatch disables speculation for sarvam.
    STT_SPECULATIVE_ENABLED: bool = field(
        default_factory=lambda: _env("STT_SPECULATIVE_ENABLED", "1").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # ── Text-to-speech (Kokoro / Sarvam Bulbul) ─────────────────────
    # kokoro | sarvam | indic_tts. `sarvam` routes only the languages in
    # SARVAM_TTS_LANGUAGES; every other language stays on Kokoro. The demo uses
    # it for Gujarati (Kokoro has no Gujarati voice) and Hindi (Kokoro hf_alpha
    # mangles English acronyms such as MBA). Hindi falls back to Kokoro when
    # SARVAM_API_KEY is unset; Gujarati has no local fallback.
    SARVAM_TTS_LANGUAGES: str = field(
        default_factory=lambda: _env(
            "SARVAM_TTS_LANGUAGES", "gu,hi"
        ).strip().lower()
    )
    SARVAM_TTS_MODEL: str = field(
        default_factory=lambda: _env("SARVAM_TTS_MODEL", "bulbul:v4-flash")
    )
    SARVAM_TTS_URL: str = field(
        default_factory=lambda: _env(
            "SARVAM_TTS_URL", "https://api.sarvam.ai/text-to-speech/stream"
        )
    )
    # Persona ID, not a short v3 name. Gujarati options:
    #   pooja_gu_customer, pooja_gu_conversational, bhavik_gu_conversation.
    # This is a LEGACY single-language override: a persona whose language tag
    # does not match the requested language is ignored (with a warning), so a
    # Gujarati value here cannot silently become Hindi's voice. Prefer the
    # per-language map below for a multi-language deployment.
    SARVAM_TTS_SPEAKER: str = field(
        default_factory=lambda: _env("SARVAM_TTS_SPEAKER", "").strip()
    )
    # Per-language persona overrides, e.g.
    # "gu:pooja_gu_customer,hi:aparna_hi_customer". Takes precedence over
    # SARVAM_TTS_SPEAKER for the codes it names.
    SARVAM_TTS_SPEAKERS: str = field(
        default_factory=lambda: _env("SARVAM_TTS_SPEAKERS", "").strip()
    )
    SARVAM_TTS_PACE: float = field(
        default_factory=lambda: _env_float("SARVAM_TTS_PACE", 1.1)
    )
    # HTTP streaming accepts 8000-24000. 8000 is the Twilio carrier rate, so
    # `_pcm_to_ulaw_chunks` has no resampling to do.
    SARVAM_TTS_SAMPLE_RATE: int = field(
        default_factory=lambda: _env_int("SARVAM_TTS_SAMPLE_RATE", 8000)
    )
    SARVAM_TTS_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("SARVAM_TTS_TIMEOUT_S", 15.0)
    )
    # `.strip() or` and `.strip().lower()` are the call sites' own
    # normalisation, copied rather than re-invented so the value resolved here
    # is the one the synthesiser used.
    KOKORO_VOICE: str = field(
        default_factory=lambda: _env("KOKORO_VOICE", "af_heart").strip() or "af_heart"
    )
    # The raw string, not a float. `_parse_tts_speed` clamps to [0.5, 2.0] and
    # WARNS rather than raising — a typo in .env must not stop the stack from
    # answering calls — and its warning quotes the offending text, so the
    # unparsed value is what has to survive to the call site.
    KOKORO_SPEED: str = field(default_factory=lambda: _env("KOKORO_SPEED", "1.0"))
    # US-012 AC-4: the cache-isolation fallback must be a configuration change,
    # not a code revert. "shared" is the DG-06 decision; "per_call" bounds the
    # cache to one session's utterances.
    TTS_CACHE_SCOPE: str = field(
        default_factory=lambda: _env("TTS_CACHE_SCOPE", "shared").strip().lower()
    )
    # US-005 streaming TTS, behind a flag because adoption is gated on DG-03.
    # The batch path is retained unchanged as the BRD-15 revert.
    TTS_STREAM: bool = field(
        default_factory=lambda: _env("TTS_STREAM", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # ── STT noise gate (pre-LLM guardrails) ─────────────────────────
    STT_MIN_AVG_LOGPROB: float = field(
        default_factory=lambda: _env_float("STT_MIN_AVG_LOGPROB", -4.0)
    )
    STT_MAX_NO_SPEECH_PROB: float = field(
        default_factory=lambda: _env_float("STT_MAX_NO_SPEECH_PROB", 0.8)
    )
    STT_MIN_CHARS: int = field(default_factory=lambda: _env_int("STT_MIN_CHARS", 3))
    # Utterances shorter than this are noise bursts (15 x 20 ms = 300 ms).
    MIN_UTTERANCE_FRAMES: int = field(
        default_factory=lambda: _env_int("MIN_UTTERANCE_FRAMES", 15)
    )

    # ── End-of-speech delay (BRD-04) ────────────────────────────────
    # These two are the IMPORT-TIME fallbacks only. The live decision is read
    # per session by `_vad_silence_frames()` / `_speculative_advance_ms()`,
    # which read the environment directly and are on the dynamic allowlist:
    # a deployment must be able to move the delay without a rebuild, and a test
    # must be able to vary it without reloading the module.
    #
    # `or 600` / `or 220` mirror those readers' empty-value guard, which treats
    # an empty value as unset. Without it `int("")` would raise at import while
    # the live reader silently fell back — two answers to the same question,
    # which is the defect US-011 exists to remove.
    VAD_SILENCE_MS: int = field(
        default_factory=lambda: _env_int("VAD_SILENCE_MS", 600)
    )
    # Read the speculative lead (the measured STT p50, 203 ms, rounded up).
    VAD_SPECULATIVE_ADVANCE_MS: int = field(
        default_factory=lambda: _env_int("VAD_SPECULATIVE_ADVANCE_MS", 220)
    )

    # ── Bound on one turn (demo track, 2026-10-10) ──────────────────
    # A turn may not hold the line open forever. This is NOT a latency target:
    # the live p95 for time-to-first-audio is 7.8 s and the worst turn in
    # 3,781 traced turns was 27.7 s, so 30 s is chosen to be well clear of
    # every turn that is merely slow, and to fire only on one that is stuck.
    #
    # It exists because "stuck" turned out to be reachable. `generate_stream`
    # handed deltas to the event loop with a call that does not wake it
    # (`asyncio.Queue.put_nowait` from the pump thread -> `loop.call_soon`,
    # which -- unlike `call_soon_threadsafe` -- never writes the self-pipe).
    # Live calls masked it because Twilio sends a media frame every 20 ms, but
    # a transport without that traffic stalled every turn, for as long as the
    # process lived. That bug is fixed; this bound is what makes the NEXT one a
    # bad turn instead of a dead call.
    #
    # On expiry the caller hears the pre-rendered fixed response and the turn
    # is logged `TURN_TIMEOUT`, so a stall is legible in the record rather than
    # showing up as unexplained silence.
    TURN_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("TURN_TIMEOUT_S", 30.0)
    )

    # ── Grounding boundary (demo track, 2026-10-11) ─────────────────
    # BUG-07: the agent told a caller about a "School of International Studies"
    # that appears in no source, and quoted $14,500 where its context said
    # $14,200. The prompt already said "never invent numbers, fees, URLs, or
    # program names" and still did it, so this is an output boundary rather
    # than another instruction: a spoken number or named thing must be
    # traceable to what retrieval returned, or to something the caller said.
    #
    # ON by default. `GROUNDING_ENABLED=0` reverts to speaking whatever the
    # model produced, which is the pre-fix behaviour.
    GROUNDING_ENABLED: bool = field(
        default_factory=lambda: _env("GROUNDING_ENABLED", "1").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # The NAME half of the same boundary (BUG-18): a programme, school or
    # department absent from the university profile may not be spoken.
    #
    # Split from GROUNDING_ENABLED because this branch has been removed once
    # already. It was dropped after three correct programme lists were refused
    # in front of callers -- but that measurement was taken while the BUG-16
    # wiring fault had emptied the evidence list, so everything was being
    # refused. Replayed over all 81 archived calls, it now flags 9 names, of
    # which 2 are regex noise; the rest are real fabrications including
    # BUG-07's "School of International Studies" and the "mechanical
    # engineering" programme that reopened the defect.
    #
    # ON by default, and a separate setting so a live call can turn the name
    # check off without giving up number checking.
    GROUNDING_NAMES_ENABLED: bool = field(
        default_factory=lambda: _env("GROUNDING_NAMES_ENABLED", "1").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # ── Caller interruption (US-014, Option 2) ──────────────────────
    # Whether the caller may be heard while the agent is speaking.
    #
    # **ON by default.** The PO ruling is that the voice yields: a caller who
    # speaks over the agent takes the floor, always. This flag exists because
    # the revert must be a configuration change (`BRD-15`), not because the
    # behaviour is optional.
    #
    # Setting it to 0 is the revert, and it is NOT a no-op — it is a behaviour
    # change with a measured history. Until the receive loop read the socket
    # during a turn, the `MUTE_STT_DURING_TTS` guard at main.py:858/1166 was
    # unreachable, so the real behaviour was neither "yield" nor "discard" but
    # "defer and replay": the caller's overlapping speech was buffered by the
    # ASGI layer and fed back as a fresh turn once playback had already
    # finished. With the reader split (`US-014` T-1) the guard became reachable,
    # so 0 now means the discard the documents always claimed — the caller is
    # not heard and does not know it.
    #
    # The gate below is what makes yielding possible on a carrier with no echo
    # cancellation: it admits the caller and rejects our own voice. Its
    # operating point is measured — `doc/perf/tools/measure_us014_guard.py`,
    # recorded in `doc/perf/us014-echo-guard-tuning.md`. At the values here, 0
    # false-admits on our own voice across echo gains −6…−24 dB, and a caller at
    # the agent's own level is heard 35/35 cells at 8–9 frames (~160 ms). The
    # known limit: a caller much quieter than the passage they are talking over
    # (12 dB down) is heard about half the time.
    BARGE_IN_ENABLED: bool = field(
        default_factory=lambda: _env("BARGE_IN_ENABLED", "1").strip().lower()
        in ("1", "true", "yes", "on")
    )
    # The echo gate: `max(ABS_FLOOR, ref_env * ECHO_GAIN * MARGIN)`.
    #
    # TWO factors, not one ratio, and the split is the point. A single ratio
    # hides two different quantities inside one number:
    #   * ECHO_GAIN -- how much of our own signal comes back to us. That is a
    #     property of the carrier's acoustic path, which US-002 TAC-9 excludes
    #     from local measurement. It cannot be tuned here; it can only be
    #     DECLARED as an assumption and swept in the tuning record.
    #   * MARGIN -- how far above the echo the caller must be. That is ours to
    #     choose and ours to tune.
    # Folding them together is how a guard ends up unable to fire: measured on
    # this box's own rendered speech (doc/perf/tools/fixtures/*/turn*.ulaw,
    # 20 ms frames), the agent's speech sits at p50 ~2,000 and p90 ~4,200 RMS,
    # and a caller's own speech sits at the same p50/p90. A threshold of
    # `ref * 2.0` therefore demands the caller beat the agent's p50 passage by
    # more than their own p90 -- so the gate would open only during the agent's
    # quiet passages and the story would ship "enabled" while behaving exactly
    # as it does today. That is the failure TAC-4 names.
    #
    # Declared assumption (NOT a measurement): -6 dB, i.e. half the amplitude
    # returns. Replace with the measured path when one exists; the tuning
    # record must carry this number and where it came from.
    #
    # Both values are PROVISIONAL until doc/perf/us014-echo-guard-tuning.md
    # exists. TAC-4 requires them tuned on real speech with the evidence
    # recorded, and this comment is not that record.
    BARGE_IN_ECHO_GAIN: float = field(
        default_factory=lambda: _env_float("BARGE_IN_ECHO_GAIN", 0.5)
    )
    BARGE_IN_MARGIN: float = field(
        default_factory=lambda: _env_float("BARGE_IN_MARGIN", 2.0)
    )
    # Above `is_silent`'s own 80 (voice_handler.py:700), or the gate would admit
    # frames the VAD then discards as silence — a detection that cannot become a
    # turn. Below the quietest speech decile measured on the fixtures (~157), or
    # a softly-spoken interruption would be gated out. The band is narrow on
    # purpose: it is the one place the two thresholds must not overlap.
    BARGE_IN_ABS_FLOOR: float = field(
        default_factory=lambda: _env_float("BARGE_IN_ABS_FLOOR", 150.0)
    )
    # Consecutive admitting frames before the interruption is believed (6 x
    # 20 ms = 120 ms). A click or a lip smack clears the ratio for a frame or
    # two; a spoken word does not. Deliberately below MIN_UTTERANCE_FRAMES: an
    # interruption is detected early and then spoken, and the 300 ms noise floor
    # is applied to the turn, not to the moment of detection.
    BARGE_IN_MIN_FRAMES: int = field(
        default_factory=lambda: _env_int("BARGE_IN_MIN_FRAMES", 6)
    )
    # Per-frame decay of the playback envelope (0.95 per 20 ms frame holds
    # ~270 ms of memory). It exists because the echo of what we sent arrives
    # tens to hundreds of ms later through the carrier: the envelope has to
    # outlive the send, and must not outlive it by so long that the caller's
    # own speech is measured against a passage that has already ended.
    BARGE_IN_REF_DECAY: float = field(
        default_factory=lambda: _env_float("BARGE_IN_REF_DECAY", 0.95)
    )

    # ── Prompt sizing / streaming ────────────────────────────────────
    # Retained for call-site compatibility: OpenAI's context window is
    # model-defined, but the utility calls still pass this as a size hint.
    SMALL_TASK_NUM_CTX: int = field(
        default_factory=lambda: _env_int("SMALL_TASK_NUM_CTX", 8192)
    )
    # US-004 streaming LLM, behind a flag; the batch path is retained unchanged
    # as the BRD-15 revert.
    LLM_STREAM: bool = field(
        default_factory=lambda: _env("LLM_STREAM", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )
    LLM_PROVIDER: str = field(
        default_factory=lambda: _env("LLM_PROVIDER", "auto").strip().lower()
    )

    # ── Remote LLM backend: OpenAI Chat Completions ──────────────────
    # The only provider. ``auto`` is accepted as a migration alias and also
    # resolves to OpenAI; a stale ollama/mlx value is a configuration error.
    LLM_MODEL: str = field(
        default_factory=lambda: _env("LLM_MODEL", "gpt-6-luna").strip()
    )
    LLM_API_KEY: str = field(
        default_factory=lambda: _env("LLM_API_KEY", "").strip()
    )
    LLM_BASE_URL: str = field(
        default_factory=lambda: _env(
            "LLM_BASE_URL", "https://api.openai.com/v1"
        ).strip() or "https://api.openai.com/v1"
    )
    LLM_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("LLM_TIMEOUT_S", 15.0)
    )
    LLM_MAX_TOKENS: int = field(
        default_factory=lambda: _env_int("LLM_MAX_TOKENS", 192)
    )
    # Empty = do not send a temperature at all. OpenAI reasoning models
    # (including the default gpt-6-luna) reject non-default values with a 400,
    # so the safe default is "omit"; set a value only for a model that accepts
    # it.
    LLM_TEMPERATURE: str = field(
        default_factory=lambda: _env("LLM_TEMPERATURE", "").strip()
    )
    # "" means "do not send a token ceiling" (used by structured extraction).
    LLM_REASONING_EFFORT: str = field(
        default_factory=lambda: _env("LLM_REASONING_EFFORT", "none").strip().lower()
    )
    LLM_PROMPT_CACHE_KEY: str = field(
        default_factory=lambda: _env("LLM_PROMPT_CACHE_KEY", "").strip()
    )
    # Escape hatch for a gateway that rejects ``stream_options``. OpenAI's own
    # Chat Completions endpoint supports it; the usage-only final chunk is
    # what supplies token counts on the streamed path.
    LLM_STREAM_INCLUDE_USAGE: bool = field(
        default_factory=lambda: _env("LLM_STREAM_INCLUDE_USAGE", "1")
        .strip().lower() in ("1", "true", "yes", "on")
    )
    # "none" is the declared policy: an OpenAI failure reaches the fixed
    # response path; there is no second model.
    # (The key is intentionally not read any more.)

    # ── Document context (RAG removed) ───────────────────────────────
    CONTEXT_MODE: str = field(
        default_factory=lambda: _env("CONTEXT_MODE", "doc").strip().lower()
    )
    DOC_PATHS: str = field(
        default_factory=lambda: _env(
            "DOC_PATHS",
            "content/dspmu/core_cards.md,"
            "content/dspmu/site_map.md,"
            "content/dspmu/recent_notices.md",
        ).strip()
    )
    DOC_MAX_BYTES: int = field(
        default_factory=lambda: _env_int("DOC_MAX_BYTES", 2_000_000)
    )
    DOC_MAX_TOKENS: int = field(
        default_factory=lambda: _env_int("DOC_MAX_TOKENS", 500_000)
    )
    #: Sidecar metadata for the current prompt bundle (fact/node IDs and source
    #: URLs). Empty when no manifest is in use.
    CONTEXT_MANIFEST_PATH: str = field(
        default_factory=lambda: _env("CONTEXT_MANIFEST_PATH", "").strip()
    )
    #: Selective router inputs (CONTEXT_MODE=site). These are read only when
    #: the router is active; a missing file falls back to the full prompt.
    SITE_FACTS_PATH: str = field(
        default_factory=lambda: _env(
            "SITE_FACTS_PATH", "content/dspmu/facts.json"
        ).strip()
    )
    SITE_NOTICES_PATH: str = field(
        default_factory=lambda: _env(
            "SITE_NOTICES_PATH", "content/dspmu/notices.jsonl"
        ).strip()
    )
    SITE_NOTICE_DB_PATH: str = field(
        default_factory=lambda: _env(
            "SITE_NOTICE_DB_PATH", "content/dspmu/notices.sqlite3"
        ).strip()
    )
    SITE_NODES_DIR: str = field(
        default_factory=lambda: _env(
            "SITE_NODES_DIR", "content/dspmu/nodes"
        ).strip()
    )
    SITE_ROUTER_MAX_NOTICES: int = field(
        default_factory=lambda: _env_int("SITE_ROUTER_MAX_NOTICES", 5)
    )
    # ── NGA navigation graph (Phase 1) ───────────────────────────────
    # The graph compiler is an offline/background tool. The answer path does
    # not read these values yet; NGA_ENABLED stays false until later gates.
    NGA_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_ENABLED", "")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_MODE: str = field(
        default_factory=lambda: _env("NGA_MODE", "off").strip().lower()
    )
    #: Product decision 2026-10-24: the chat is part of the public website and
    #: the university already publishes these pages, so the URL/title PII guard
    #: is off by default. Set true to restore the conservative result/merit/
    #: selection and staff-directory exclusions, then recompile the graph.
    NGA_PII_GUARD_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_PII_GUARD_ENABLED", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_SITE_BASE_URL: str = field(
        default_factory=lambda: _env(
            "NGA_SITE_BASE_URL", "https://dspmuranchi.ac.in"
        ).strip()
    )
    NGA_SITEMAP_URL: str = field(
        default_factory=lambda: _env(
            "NGA_SITEMAP_URL", "https://dspmuranchi.ac.in/sitemap.xml"
        ).strip()
    )
    NGA_ALLOWED_HOSTS: str = field(
        default_factory=lambda: _env(
            "NGA_ALLOWED_HOSTS",
            "dspmuranchi.ac.in,www.dspmuranchi.ac.in",
        ).strip()
    )
    NGA_BLOCKED_PATH_TOKENS: str = field(
        default_factory=lambda: _env(
            "NGA_BLOCKED_PATH_TOKENS",
            "result,results,merit,selection,selected,waiting,answer,admit,"
            "archive,awarded,departments,faculties,staff,faculty,teaching,"
            "nonteaching",
        ).strip()
    )
    NGA_GRAPH_SQLITE_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_GRAPH_SQLITE_PATH", "content/dspmu/nga_graph.sqlite3"
        ).strip()
    )
    NGA_COVERAGE_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_COVERAGE_PATH", "content/dspmu/nga_coverage.json"
        ).strip()
    )
    NGA_NOTICES_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_NOTICES_PATH", "content/dspmu/notices.jsonl"
        ).strip()
    )
    NGA_COVERAGE_MIN: float = field(
        default_factory=lambda: _env_float("NGA_COVERAGE_MIN", 0.95)
    )
    # ── Phase 3 fetch / content / freshness ──────────────────────────
    NGA_FETCH_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("NGA_FETCH_TIMEOUT_S", 6.0)
    )
    NGA_FETCH_DELAY_S: float = field(
        default_factory=lambda: _env_float("NGA_FETCH_DELAY_S", 0.25)
    )
    NGA_MAX_BYTES: int = field(
        default_factory=lambda: _env_int("NGA_MAX_BYTES", 2_000_000)
    )
    NGA_MIN_PAGE_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_MIN_PAGE_CHARS", 120)
    )
    NGA_TTL_PAGE_S: int = field(
        default_factory=lambda: _env_int("NGA_TTL_PAGE_S", 86_400)
    )
    NGA_TTL_NOTICE_S: int = field(
        default_factory=lambda: _env_int("NGA_TTL_NOTICE_S", 300)
    )
    NGA_TTL_NAV_S: int = field(
        default_factory=lambda: _env_int("NGA_TTL_NAV_S", 3_600)
    )
    NGA_REFRESH_LIMIT: int = field(
        default_factory=lambda: _env_int("NGA_REFRESH_LIMIT", 10)
    )
    # ── Phase 10 refresh scheduler and alerts ────────────────────────
    # The scheduler is a separate process behind its own flag; enabling the
    # answer-path NGA flag never starts background fetching.
    NGA_SCHEDULER_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_SCHEDULER_ENABLED", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_REFRESH_INTERVAL_S: int = field(
        default_factory=lambda: _env_int("NGA_REFRESH_INTERVAL_S", 900)
    )
    NGA_REFRESH_JITTER_S: int = field(
        default_factory=lambda: _env_int("NGA_REFRESH_JITTER_S", 60)
    )
    NGA_REFRESH_MAX_CYCLES: int = field(
        default_factory=lambda: _env_int("NGA_REFRESH_MAX_CYCLES", 0)
    )
    NGA_REFRESH_REPORT_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_REFRESH_REPORT_PATH",
            "content/dspmu/nga_refresh_report.json",
        ).strip()
    )
    NGA_REFRESH_LOG_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_REFRESH_LOG_PATH", "logs/nga_refresh.jsonl"
        ).strip()
    )
    NGA_ALERT_LOG_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_ALERT_LOG_PATH", "logs/nga_alerts.jsonl"
        ).strip()
    )
    NGA_REFRESH_LOCK_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_REFRESH_LOCK_PATH", "content/dspmu/nga_refresh.lock"
        ).strip()
    )
    NGA_ALERT_COVERAGE_DROP: float = field(
        default_factory=lambda: _env_float("NGA_ALERT_COVERAGE_DROP", 0.02)
    )
    NGA_ALERT_RUN_FAILURE_STREAK: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_RUN_FAILURE_STREAK", 3)
    )
    NGA_ALERT_NODE_FAILURE_STREAK: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_NODE_FAILURE_STREAK", 3)
    )
    NGA_ALERT_ERROR_RATE: float = field(
        default_factory=lambda: _env_float("NGA_ALERT_ERROR_RATE", 0.5)
    )
    NGA_ALERT_EMPTY_SHELL_RATE: float = field(
        default_factory=lambda: _env_float("NGA_ALERT_EMPTY_SHELL_RATE", 0.35)
    )
    NGA_ALERT_SOFT_404_RATE: float = field(
        default_factory=lambda: _env_float("NGA_ALERT_SOFT_404_RATE", 0.25)
    )
    NGA_ALERT_STALE_NODES: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_STALE_NODES", 50)
    )
    NGA_ALERT_STALE_GRACE_S: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_STALE_GRACE_S", 7200)
    )
    NGA_ALERT_GRAPH_STALE_HOURS: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_GRAPH_STALE_HOURS", 168)
    )
    # ── Phase 11 background PDF/OCR index ───────────────────────────
    # Documents are fetched and OCR'd only by the refresh worker. The live
    # answer path reads stored text; OCR text waits for explicit review.
    NGA_PDF_INDEX_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_PDF_INDEX_ENABLED", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_TTL_DOCUMENT_S: int = field(
        default_factory=lambda: _env_int("NGA_TTL_DOCUMENT_S", 604_800)
    )
    NGA_PDF_REFRESH_LIMIT: int = field(
        default_factory=lambda: _env_int("NGA_PDF_REFRESH_LIMIT", 5)
    )
    NGA_PDF_MAX_BYTES: int = field(
        default_factory=lambda: _env_int("NGA_PDF_MAX_BYTES", 10_000_000)
    )
    NGA_PDF_MAX_PAGES: int = field(
        default_factory=lambda: _env_int("NGA_PDF_MAX_PAGES", 40)
    )
    NGA_PDF_MIN_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_PDF_MIN_CHARS", 80)
    )
    NGA_PDF_OCR_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_PDF_OCR_ENABLED", "true")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_PDF_OCR_MAX_PAGES: int = field(
        default_factory=lambda: _env_int("NGA_PDF_OCR_MAX_PAGES", 8)
    )
    NGA_PDF_OCR_LANGS: str = field(
        default_factory=lambda: _env("NGA_PDF_OCR_LANGS", "").strip()
    )
    NGA_PDF_FAILURE_BACKOFF_S: int = field(
        default_factory=lambda: _env_int("NGA_PDF_FAILURE_BACKOFF_S", 3600)
    )
    NGA_ALERT_DOCUMENT_ERROR_RATE: float = field(
        default_factory=lambda: _env_float("NGA_ALERT_DOCUMENT_ERROR_RATE", 0.5)
    )
    NGA_ALERT_REVIEW_BACKLOG: int = field(
        default_factory=lambda: _env_int("NGA_ALERT_REVIEW_BACKLOG", 25)
    )
    # ── Phase 12 references and voice→WhatsApp delivery ─────────────
    # Cited chat/WhatsApp answers always render their evidence references.
    # This flag enables the separate voice→WhatsApp delivery pipeline.
    NGA_REFERENCE_DELIVERY_ENABLED: bool = field(
        default_factory=lambda: _env("NGA_REFERENCE_DELIVERY_ENABLED", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_REFERENCE_DELIVERY_MODE: str = field(
        default_factory=lambda: _env(
            "NGA_REFERENCE_DELIVERY_MODE", "turn"
        ).strip().lower()
    )
    NGA_REFERENCE_MAX_PER_CALL: int = field(
        default_factory=lambda: _env_int("NGA_REFERENCE_MAX_PER_CALL", 3)
    )
    NGA_REFERENCE_MIN_INTERVAL_S: int = field(
        default_factory=lambda: _env_int("NGA_REFERENCE_MIN_INTERVAL_S", 20)
    )
    NGA_REFERENCE_SNIPPET_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_REFERENCE_SNIPPET_CHARS", 240)
    )
    NGA_WHATSAPP_REGISTRATION_WINDOW_DAYS: int = field(
        default_factory=lambda: _env_int(
            "NGA_WHATSAPP_REGISTRATION_WINDOW_DAYS", 30
        )
    )
    NGA_WHATSAPP_REFERENCE_REQUIRE_OPT_IN: bool = field(
        default_factory=lambda: _env(
            "NGA_WHATSAPP_REFERENCE_REQUIRE_OPT_IN", "false"
        )
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_WHATSAPP_TEMPLATE_SID: str = field(
        default_factory=lambda: _env(
            "NGA_WHATSAPP_TEMPLATE_SID", ""
        ).strip()
    )
    NGA_REFERENCE_RETRY_MAX: int = field(
        default_factory=lambda: _env_int("NGA_REFERENCE_RETRY_MAX", 3)
    )
    NGA_REFERENCE_RETRY_BACKOFF_S: float = field(
        default_factory=lambda: _env_float(
            "NGA_REFERENCE_RETRY_BACKOFF_S", 2.0
        )
    )
    NGA_REFERENCE_LOG_RETENTION_DAYS: int = field(
        default_factory=lambda: _env_int(
            "NGA_REFERENCE_LOG_RETENTION_DAYS", 30
        )
    )
    NGA_ALERT_REFERENCE_WINDOW_S: int = field(
        default_factory=lambda: _env_int(
            "NGA_ALERT_REFERENCE_WINDOW_S", 3600
        )
    )
    NGA_ALERT_REFERENCE_FAILURES: int = field(
        default_factory=lambda: _env_int(
            "NGA_ALERT_REFERENCE_FAILURES", 3
        )
    )
    NGA_ALERT_TEMPLATE_REQUIRED: int = field(
        default_factory=lambda: _env_int(
            "NGA_ALERT_TEMPLATE_REQUIRED", 3
        )
    )
    NGA_SESSION_HISTORY_TURNS: int = field(
        default_factory=lambda: _env_int("NGA_SESSION_HISTORY_TURNS", 6)
    )
    NGA_EVENT_RETENTION_DAYS: int = field(
        default_factory=lambda: _env_int("NGA_EVENT_RETENTION_DAYS", 90)
    )
    # ── Phase 7 shadow mode ──────────────────────────────────────────
    # Shadow runs only when NGA_ENABLED=true and NGA_MODE=shadow. It never
    # changes the answer, and only computes evidence (and optionally a
    # generated/verified answer) for comparison.
    NGA_SHADOW_LOG_PATH: str = field(
        default_factory=lambda: _env(
            "NGA_SHADOW_LOG_PATH", "logs/nga_shadow.jsonl"
        ).strip()
    )
    NGA_SHADOW_GENERATE: bool = field(
        default_factory=lambda: _env("NGA_SHADOW_GENERATE", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_SHADOW_MAX_WORKERS: int = field(
        default_factory=lambda: _env_int("NGA_SHADOW_MAX_WORKERS", 2)
    )
    NGA_SHADOW_SAMPLE_RATE: float = field(
        default_factory=lambda: _env_float("NGA_SHADOW_SAMPLE_RATE", 1.0)
    )
    NGA_SHADOW_LOG_QUERIES: bool = field(
        default_factory=lambda: _env("NGA_SHADOW_LOG_QUERIES", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_SHADOW_DEADLINE_MS: int = field(
        default_factory=lambda: _env_int("NGA_SHADOW_DEADLINE_MS", 8000)
    )
    NGA_SHADOW_MAX_STEPS: int = field(
        default_factory=lambda: _env_int("NGA_SHADOW_MAX_STEPS", 8)
    )
    NGA_SHADOW_MAX_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_SHADOW_MAX_CHARS", 3000)
    )
    # ── Phase 8 voice fast path ──────────────────────────────────────
    # Voice uses the deterministic planner, at most two tool calls, a hard
    # deadline, and a small live-evidence budget. Text channels are unchanged
    # until the agentic phase.
    NGA_VOICE_DEADLINE_MS: int = field(
        default_factory=lambda: _env_int("NGA_VOICE_DEADLINE_MS", 1500)
    )
    NGA_VOICE_MAX_STEPS: int = field(
        default_factory=lambda: _env_int("NGA_VOICE_MAX_STEPS", 2)
    )
    NGA_VOICE_MAX_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_VOICE_MAX_CHARS", 1200)
    )
    # ── Phase 9 text agentic path ────────────────────────────────────
    # Chat/WhatsApp use the bounded tool loop, evidence bundle, answer and
    # verifier. The LLM planner is optional; deterministic planning is the
    # default so the agentic path costs one generation call.
    NGA_TEXT_DEADLINE_MS: int = field(
        default_factory=lambda: _env_int("NGA_TEXT_DEADLINE_MS", 8000)
    )
    NGA_TEXT_MAX_STEPS: int = field(
        default_factory=lambda: _env_int("NGA_TEXT_MAX_STEPS", 8)
    )
    NGA_TEXT_MAX_CHARS: int = field(
        default_factory=lambda: _env_int("NGA_TEXT_MAX_CHARS", 4000)
    )
    NGA_TEXT_LLM_PLANNER: bool = field(
        default_factory=lambda: _env("NGA_TEXT_LLM_PLANNER", "false")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    NGA_FETCH_USER_AGENT: str = field(
        default_factory=lambda: _env(
            "NGA_FETCH_USER_AGENT",
            "DSPMU-Admissions-Assistant/1.0 "
            "(+https://dspmuranchi.ac.in; contact: registrar@dspmuranchi.ac.in)",
        ).strip()
    )
    NGA_ROBOTS_RESPECT: bool = field(
        default_factory=lambda: _env("NGA_ROBOTS_RESPECT", "true")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    # ── Live request-time website retrieval (prototype, disabled) ────
    # The static DSPMU corpus is a build-time snapshot. When enabled, the
    # answer pipeline may fetch one or two public HTML pages for questions
    # the snapshot does not cover. The live path is bounded (allowlisted
    # hosts, robots-aware, short timeout, TTL cache) and always degrades to
    # the static bundle. Disabled by default so tests and offline runs never
    # touch the network; the deployment .env turns it on.
    LIVE_SITE_ENABLED: bool = field(
        default_factory=lambda: _env("LIVE_SITE_ENABLED", "")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    #: ``novel`` fetches only when the router missed or the question contains
    #: a term the static bundle does not carry. ``always`` adds live context
    #: to every non-boundary turn (slower, larger prompt).
    LIVE_SITE_MODE: str = field(
        default_factory=lambda: _env("LIVE_SITE_MODE", "novel").strip().lower()
    )
    LIVE_SITE_BASE_URL: str = field(
        default_factory=lambda: _env(
            "LIVE_SITE_BASE_URL", "https://dspmuranchi.ac.in"
        ).strip()
    )
    LIVE_SITE_SITEMAP_URL: str = field(
        default_factory=lambda: _env(
            "LIVE_SITE_SITEMAP_URL", "https://dspmuranchi.ac.in/sitemap.xml"
        ).strip()
    )
    LIVE_SITE_ALLOWED_HOSTS: str = field(
        default_factory=lambda: _env(
            "LIVE_SITE_ALLOWED_HOSTS",
            "dspmuranchi.ac.in,www.dspmuranchi.ac.in",
        ).strip()
    )
    LIVE_SITE_BLOCKED_PATH_TOKENS: str = field(
        default_factory=lambda: _env(
            "LIVE_SITE_BLOCKED_PATH_TOKENS",
            "result,results,merit,selection,selected,waiting,answer,admit,"
            "archive,awarded,departments,faculties,staff,faculty,teaching,"
            "nonteaching",
        ).strip()
    )
    LIVE_SITE_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("LIVE_SITE_TIMEOUT_S", 6.0)
    )
    LIVE_SITE_DELAY_S: float = field(
        default_factory=lambda: _env_float("LIVE_SITE_DELAY_S", 0.25)
    )
    LIVE_SITE_MAX_PAGES: int = field(
        default_factory=lambda: _env_int("LIVE_SITE_MAX_PAGES", 2)
    )
    LIVE_SITE_MAX_CHARS: int = field(
        default_factory=lambda: _env_int("LIVE_SITE_MAX_CHARS", 3000)
    )
    LIVE_SITE_CACHE_TTL_S: float = field(
        default_factory=lambda: _env_float("LIVE_SITE_CACHE_TTL_S", 600.0)
    )
    LIVE_SITE_SITEMAP_TTL_S: float = field(
        default_factory=lambda: _env_float("LIVE_SITE_SITEMAP_TTL_S", 3600.0)
    )
    LIVE_SITE_RESPECT_ROBOTS: bool = field(
        default_factory=lambda: _env("LIVE_SITE_RESPECT_ROBOTS", "true")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    LIVE_SITE_USER_AGENT: str = field(
        default_factory=lambda: _env(
            "LIVE_SITE_USER_AGENT",
            "DSPMU-Admissions-Assistant/1.0 "
            "(+https://dspmuranchi.ac.in; contact: registrar@dspmuranchi.ac.in)",
        ).strip()
    )
    DEFAULT_LANGUAGE: str = field(
        default_factory=lambda: _env("DEFAULT_LANGUAGE", "en").strip().lower()
    )
    SUPPORTED_LANGUAGES: str = field(
        default_factory=lambda: _env("SUPPORTED_LANGUAGES", "en,hi").strip()
    )
    # Non-Indian languages enabled end to end (es,fr,it,ja,pt,zh). An empty
    # value disables the selectors, commands, Whisper routing and assets in
    # one place -- the one-line rollback for the whole feature.
    FOREIGN_LANGUAGES: str = field(
        default_factory=lambda: _env(
            "FOREIGN_LANGUAGES", "es,fr,it,ja,pt,zh"
        ).strip().lower()
    )
    # Campaign default for the outbound TwiML URL when the lead has no stored
    # language. Empty leaves the language off the URL (session default).
    OUTBOUND_LANGUAGE: str = field(
        default_factory=lambda: _env("OUTBOUND_LANGUAGE", "").strip().lower()
    )
    # Ask the callee, as soon as the outbound call is answered, which language
    # to continue in: a DTMF menu before the media stream starts (1 English,
    # 2 Hindi, 3 Gujarati, 4 keep the call's configured language). The choice
    # is carried as `languageSource=selected`, which locks it for the call.
    # `0` restores the old behaviour: connect straight to the stream.
    OUTBOUND_LANGUAGE_MENU: bool = field(
        default_factory=lambda: _env("OUTBOUND_LANGUAGE_MENU", "1")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    # Ask which language to continue in at the start of every WhatsApp
    # conversation, before the first message is interpreted. The choice is
    # stored on the conversation's language column with language_source, and
    # the saved language answers the held message if the menu is ignored — so
    # remembering is preserved without suppressing the menu. Within one
    # conversation the menu is asked once. `0` restores the pre-menu behavior.
    WHATSAPP_LANGUAGE_MENU: bool = field(
        default_factory=lambda: _env("WHATSAPP_LANGUAGE_MENU", "1")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    GROUNDING_LANGUAGE_MODE: str = field(
        default_factory=lambda: _env(
            "GROUNDING_LANGUAGE_MODE", "relaxed"
        ).strip().lower()
    )

    # ── TTS provider selection ───────────────────────────────────────
    # kokoro (default) preserves today's behaviour; `sarvam` routes the
    # languages in SARVAM_TTS_LANGUAGES to Bulbul v4 Flash and leaves the rest
    # on Kokoro; indic_tts is opt-in once the isolated service is running.
    TTS_PROVIDER: str = field(
        default_factory=lambda: _env("TTS_PROVIDER", "kokoro").strip().lower()
    )
    INDIC_TTS_URL: str = field(
        default_factory=lambda: _env(
            "INDIC_TTS_URL", "http://127.0.0.1:8020"
        ).strip()
    )
    INDIC_TTS_LANGUAGE: str = field(
        default_factory=lambda: _env("INDIC_TTS_LANGUAGE", "hi").strip().lower()
    )

    # ── LLM backend: OpenAI Chat Completions (the only provider) ─────
    # (The local Ollama/MLX fields and RAG/embedding fields were removed.)

    # ── Logging ─────────────────────────────────────────────────────
    # `.strip().upper()` is the call site's normalisation. A value that is not
    # a logging level name is handled there — it warns and falls back to INFO —
    # so an unrecognised level has to reach it intact rather than being
    # silently replaced here.
    LOG_LEVEL: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").strip().upper())
    # Empty means no file handler is installed at all.
    LOG_FILE: str = field(default_factory=lambda: _env("LOG_FILE", "").strip())

    # ── Call audio ──────────────────────────────────────────────────
    # AEC workaround: Twilio <Stream> has no echo-cancellation attribute, so
    # caller audio is dropped while the assistant's TTS is playing — it is
    # mostly the caller's mic re-capturing our own speech. Exact "1" match, as
    # at the call site, so "true" does NOT enable it.
    MUTE_STT_DURING_TTS: bool = field(
        default_factory=lambda: _env("MUTE_STT_DURING_TTS", "1") == "1"
    )

    # ── Boot readiness ──────────────────────────────────────────────
    # The LAUNCHER's port key, which is not the same key as PORT above: both
    # live in .env and both default 8000, but the launcher reads this one
    # (start_services.ps1) and the boot gate probes the port it will bind.
    # Listed in config_truth._EXTERNALLY_CONSUMED for that reason.
    FASTAPI_PORT: int = field(default_factory=lambda: _env_int("FASTAPI_PORT", 8000))

    # Machine-sizing metadata written by scripts/predeploy.py. Held as the RAW
    # string, defaulting to empty, because app/main.py's `_reconcile_workers`
    # distinguishes "unset" (say nothing) from "set to something that is not a
    # number" (warn) — so `int("")` must not happen on the way in.
    #
    # Not in effect on any launcher, and it is not a worker count: this stack
    # runs a single uvicorn worker by design, and app/main.py's
    # `_reconcile_workers` reports the value as NOT IN EFFECT at boot. The key
    # is sizing metadata, so its value does not need to be 1.
    FASTAPI_WORKERS: str = field(
        default_factory=lambda: _env("FASTAPI_WORKERS", "").strip()
    )

    # ── Machine detection overrides ─────────────────────────────────
    # Detection overrides, not values: each is compared against the exact
    # string "1", so unset and "" both mean "not set" and the probe falls
    # through to real detection. Held as strings so that comparison is
    # untouched — a bool field would make "false" behave differently from "".
    MACHINE_IN_CONTAINER: str = field(
        default_factory=lambda: _env("MACHINE_IN_CONTAINER", "")
    )
    MACHINE_CLOUD: str = field(default_factory=lambda: _env("MACHINE_CLOUD", ""))

    # ── Public tunnel ───────────────────────────────────────────────
    # The hostname Twilio must call back on. Declared here so the four copies
    # of the resolver (app/main.py twice — once dead — app/offers/service.py,
    # app/outbound/caller.py) read through one place.
    TUNNEL_HOST: str = field(default_factory=lambda: _env("TUNNEL_HOST", ""))
    NGROK_HOST: str = field(default_factory=lambda: _env("NGROK_HOST", ""))

    # ── Presentation ────────────────────────────────────────────────
    # The full legal name is what a caller hears and what appears in prompt
    # text. The short name is for sentences where the full name would be
    # unwieldy ("DSPMU admissions"). Both are deployment values; the legacy
    # legacy fixtures pin them in tests/conftest.py.
    COMPANY_NAME: str = field(
        default_factory=lambda: _env(
            "COMPANY_NAME", "Dr. Shyama Prasad Mukherjee University"
        )
    )
    COMPANY_SHORT_NAME: str = field(
        default_factory=lambda: _env("COMPANY_SHORT_NAME", "DSPMU")
    )
    AGENT_NAME: str = field(default_factory=lambda: _env("AGENT_NAME", "Alex"))
    ADMISSIONS_EMAIL: str = field(
        default_factory=lambda: _env(
            "ADMISSIONS_EMAIL", "registrar@dspmuranchi.ac.in"
        )
    )
    ADMISSIONS_PHONE: str = field(
        default_factory=lambda: _env("ADMISSIONS_PHONE", "+91 930 455 261")
    )
    UNIVERSITY_WEBSITE: str = field(
        default_factory=lambda: _env(
            "UNIVERSITY_WEBSITE", "https://dspmuranchi.ac.in"
        )
    )
    #: Retained for configuration compatibility; DSPMU is the only supported
    #: programme catalogue.
    PROGRAM_PROFILE: str = field(
        default_factory=lambda: _env("PROGRAM_PROFILE", "dspmu").strip().lower()
    )

    # ── Auxiliary HTTP clients ──────────────────────────────────────
    # The Streamlit helpers and the admin dashboard each call the backend.
    BACKEND_BASE: str = field(default_factory=lambda: _env("BACKEND_BASE", "http://localhost:8000"))
    BACKEND_TIMEOUT: float = field(default_factory=lambda: _env_float("BACKEND_TIMEOUT", 10))
    DASHBOARD_API_URL: str = field(
        default_factory=lambda: _env("DASHBOARD_API_URL", "http://localhost:8000")
    )

    # ── Background work admission ───────────────────────────────────
    # `or 1` / `or 30` mirror the call sites: an empty value in .env must fall
    # back rather than reach int("") and raise.
    BG_MAX_CONCURRENT: int = field(
        default_factory=lambda: _env_int("BG_MAX_CONCURRENT", 1)
    )
    BG_DEFER_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("BG_DEFER_TIMEOUT_S", 30)
    )

    # ── Sentiment scoring ───────────────────────────────────────────
    # Defaults copied from the call sites, which guard against an empty value
    # with `or`: these are what app/sentiment/scorer.py has always used.
    SENTIMENT_W1: float = field(default_factory=lambda: _env_float("SENTIMENT_W1", 0.30))
    SENTIMENT_W2: float = field(default_factory=lambda: _env_float("SENTIMENT_W2", 0.30))
    SENTIMENT_W3: float = field(default_factory=lambda: _env_float("SENTIMENT_W3", 0.25))
    SENTIMENT_W4: float = field(default_factory=lambda: _env_float("SENTIMENT_W4", 0.15))
    SENTIMENT_EWMA_LAMBDA: float = field(
        default_factory=lambda: _env_float("SENTIMENT_EWMA_LAMBDA", 0.35)
    )
    MIN_LABELED_OUTCOMES: int = field(
        default_factory=lambda: _env_int("MIN_LABELED_OUTCOMES", 100)
    )

    # ── Twilio credentials ──────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID", ""))
    TWILIO_AUTH_TOKEN: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN", ""))
    TWILIO_PHONE_NUMBER: str = field(default_factory=lambda: _env("TWILIO_PHONE_NUMBER", ""))
    TWILIO_WHATSAPP_NUMBER: str = field(default_factory=lambda: _env("TWILIO_WHATSAPP_NUMBER", ""))

    # ── PostgreSQL database ─────────────────────────────────────────
    DATABASE_URL: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    DB_HOST: str = field(default_factory=lambda: _env("DB_HOST", "localhost"))
    DB_PORT: str = field(default_factory=lambda: _env("DB_PORT", "5432"))
    DB_NAME: str = field(default_factory=lambda: _env("DB_NAME", "admissions"))
    DB_USER: str = field(default_factory=lambda: _env("DB_USER", "postgres"))
    DB_PASSWORD: str = field(default_factory=lambda: _env("DB_PASSWORD", ""))

    # ── Outbound call engine ────────────────────────────────────────
    OUTBOUND_POLL_INTERVAL: int = field(
        default_factory=lambda: _env_int("OUTBOUND_POLL_INTERVAL", 10)
    )
    MAX_CALL_ATTEMPTS: int = field(
        default_factory=lambda: _env_int("MAX_CALL_ATTEMPTS", 3)
    )

    # ── Follow-up scheduler ─────────────────────────────────────────
    FOLLOW_UP_POLL_INTERVAL: int = field(
        default_factory=lambda: _env_int("FOLLOW_UP_POLL_INTERVAL", 30)
    )

    # ── MCP server ──────────────────────────────────────────────────
    MCP_ENABLED: bool = field(
        default_factory=lambda: _env("MCP_ENABLED", "true").lower() == "true"
    )

    # ── Offer letter / documents ────────────────────────────────────
    DATA_DIR: str = field(default_factory=lambda: _env("DATA_DIR", "data"))
    UNIVERSITY_NAME: str = field(
        default_factory=lambda: _env(
            "UNIVERSITY_NAME", "Dr. Shyama Prasad Mukherjee University"
        )
    )
    OFFER_EMAIL: str = field(
        default_factory=lambda: _env(
            "OFFER_EMAIL", "registrar@dspmuranchi.ac.in"
        )
    )
    OFFER_VALID_DAYS: int = field(
        default_factory=lambda: _env_int("OFFER_VALID_DAYS", 30)
    )
    OFFER_GUARD_MINUTES: int = field(
        default_factory=lambda: _env_int("OFFER_GUARD_MINUTES", 1)
    )
    DEFAULT_PAYMENT_LINK: str = field(
        default_factory=lambda: _env("DEFAULT_PAYMENT_LINK", "")
    )

    # ── Email / SMTP ───────────────────────────────────────────────
    SMTP_HOST: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.gmail.com"))
    SMTP_PORT: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    SMTP_USER: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    SMTP_PASS: str = field(default_factory=lambda: _env("SMTP_PASS", ""))

    # ── Salesforce user API ────────────────────────────────────────
    # Master switch. OFF by default *in code*: reaching Salesforce requires an
    # explicit opt-in, so it is the deployment's .env (not this default) that
    # turns the integration on. With it off, nothing in the app calls
    # Salesforce and behaviour is identical to before the integration.
    CRM_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_ENABLED", "false").lower() == "true"
    )
    # Loopback dev deployment; see the plan's decision D2.
    CRM_BASE_URL: str = field(
        default_factory=lambda: _env("CRM_BASE_URL", "http://127.0.0.1:8098")
    )
    # WhatsApp has no session and no end event, so "one conversation" is defined
    # by an idle window. Decision D4 in the plan; this is the proposed default.
    CRM_IDLE_WINDOW_HOURS: float = field(
        default_factory=lambda: _env_float("CRM_IDLE_WINDOW_HOURS", 6)
    )
    # The API has no authentication today (plan risk R8); if a shared secret is
    # ever added in front of it, this is sent as X-API-Key. Unset = no header.
    CRM_API_KEY: str = field(default_factory=lambda: _env("CRM_API_KEY", ""))
    # Deliberately short: this sits behind a live phone call. A slow CRM must
    # give up quickly rather than delay the caller.
    CRM_TIMEOUT_CONNECT_S: float = field(
        default_factory=lambda: _env_float("CRM_TIMEOUT_CONNECT_S", 2)
    )
    CRM_TIMEOUT_READ_S: float = field(
        default_factory=lambda: _env_float("CRM_TIMEOUT_READ_S", 5)
    )
    CRM_MAX_RETRIES: int = field(
        default_factory=lambda: _env_int("CRM_MAX_RETRIES", 3)
    )
    # Consecutive failures before the breaker opens and we stop calling out.
    CRM_BREAKER_THRESHOLD: int = field(
        default_factory=lambda: _env_int("CRM_BREAKER_THRESHOLD", 5)
    )
    # How long the breaker stays open before allowing a probe request.
    CRM_BREAKER_RESET_S: float = field(
        default_factory=lambda: _env_float("CRM_BREAKER_RESET_S", 60)
    )
    # A queued write is abandoned after this many attempts. Rare — the outbox
    # only ever holds *transient* failures, since permanent ones are dropped.
    CRM_OUTBOX_MAX_ATTEMPTS: int = field(
        default_factory=lambda: _env_int("CRM_OUTBOX_MAX_ATTEMPTS", 10)
    )
    # How often the worker drains the outbox and sweeps lapsed offers. Slower
    # than the other loops on purpose: nothing here is time-critical, and each
    # tick is a database query plus, when there is work, a CRM call.
    CRM_OUTBOX_POLL_INTERVAL: int = field(
        default_factory=lambda: _env_int("CRM_OUTBOX_POLL_INTERVAL", 60)
    )

    # ── Offer-letter document upload ───────────────────────────────
    # Separate switch from CRM_ENABLED. The document upload is the one CRM
    # write that leaves residue we cannot remove — the API has no delete route
    # for a ContentVersion — so an operator needs to be able to stop it alone
    # while status sync keeps running.
    CRM_OFFER_UPLOAD_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_OFFER_UPLOAD_ENABLED", "true").lower() == "true"
    )
    # The student's own documents — transcript, ID proof. A separate switch
    # because these carry grades and identity documents, and an operator may want
    # the generated offer letter in the CRM while these stay on this host until
    # someone signs off on that egress.
    CRM_DOCUMENT_UPLOAD_ENABLED: bool = field(
        default_factory=lambda: _env("CRM_DOCUMENT_UPLOAD_ENABLED", "true").lower() == "true"
    )
    # These land in the ContentVersion fields Document_Type__c / Source__c. The
    # API validates neither (both are free strings), but both are restricted
    # picklists in the dev org, so an out-of-vocabulary value fails as a 500
    # (R16) rather than a 400 — which is why they are settings, not constants.
    #
    # "Other" is deliberate, and not the intent: the org's Document_Type__c has
    # no offer-letter value (verified against Salesforce field metadata, see
    # scripts/verify_offer_document_upload.py --describe-only). "Offer Letter"
    # becomes correct — and should replace this default — once an admin adds it
    # to the picklist. Until then "Other" is the only honest fit among
    # {Academic Transcript, Resume, Financial Document, ID Document, Admission
    # Call Recording, Chatbot Conversation Record, Test Score, Recommendation
    # Letter, Other, 10th/12th/Diploma Marksheet, Bachelor's Last-Semester
    # Result, IELTS Score, Diploma Last-Semester Result}.
    CRM_OFFER_DOCUMENT_TYPE: str = field(
        default_factory=lambda: _env("CRM_OFFER_DOCUMENT_TYPE", "Other")
    )
    # The only Source__c value the dev org uses today, and it is allowed.
    CRM_OFFER_DOCUMENT_SOURCE: str = field(
        default_factory=lambda: _env("CRM_OFFER_DOCUMENT_SOURCE", "Internal Upload")
    )
    # Offer PDFs are ~2.4 KB, so this is one chunk in practice. The cap exists so
    # a future large document still uploads rather than failing the size check.
    CRM_UPLOAD_CHUNK_BYTES: int = field(
        default_factory=lambda: _env_int("CRM_UPLOAD_CHUNK_BYTES", 1024 * 1024)
    )
    # Per-request override for the three upload calls. The shared read timeout
    # above is sized for a phone call; /complete performs a synchronous
    # ContentVersion create (base64-encoding the file and re-fetching an OAuth
    # token first), which does not fit in 5s.
    CRM_UPLOAD_TIMEOUT_S: float = field(
        default_factory=lambda: _env_float("CRM_UPLOAD_TIMEOUT_S", 30)
    )


# Module-level singleton
settings = Settings()


# ── Derived values ──────────────────────────────────────────────────
# Resolution helpers for values assembled FROM settings. They live here so
# there is one place a connection target is decided (US-011 TAC-1: `.env` is
# the authority, resolved once through app/config.py).


def database_dsn() -> str:
    """
    PostgreSQL connection string, from the resolved settings.

    One home for what used to be four byte-identical copies of the same six
    `os.environ` reads (app/database.py, app/leads/models.py,
    app/sentiment/models.py, and an inline block in app/main.py's call-queue
    endpoint). Those copies read the environment at *their own* import time,
    and nothing in those modules loads `.env` — so which value was in force
    depended on whether something else had already imported app.config.
    Importing app.leads.models first left DATABASE_URL unset and silently
    fell back to the localhost/postgres defaults, a different database from
    the one the operator configured. Resolving through `settings` makes the
    answer independent of import order, because loading `.env` is part of
    resolving it.

    `DATABASE_URL` wins when set; otherwise the discrete `DB_*` keys are
    assembled into a libpq string, exactly as the four copies did.
    """
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
    return (
        f"host={settings.DB_HOST} port={settings.DB_PORT} "
        f"dbname={settings.DB_NAME} user={settings.DB_USER} "
        f"password={settings.DB_PASSWORD}"
    )


def database_target() -> str:
    """
    The connection target with the password removed — safe to log (TAC-4).

    The fallback path used to log `database_dsn()` verbatim on a connection
    failure, which would have written the credential into logs/ (not observed
    in the current logs, so this is a latent path closed rather than a
    leak cleaned up). Sensitivity is decided the same way config_truth does
    it, so the two cannot disagree about what counts as a secret.
    """
    if settings.DATABASE_URL:
        # scheme://user:password@host -> scheme://user@host
        return re.sub(r"://([^:/@]+):[^@]*@", r"://\1@", settings.DATABASE_URL)
    return (
        f"host={settings.DB_HOST} port={settings.DB_PORT} "
        f"dbname={settings.DB_NAME} user={settings.DB_USER} "
        f"password=<redacted>"
    )


#: The hostname file the launcher writes once Cloudflare is up. Consulted only
#: when TUNNEL_HOST is unset — it carries the same value, written where both
#: the launcher and the operator can see it.
TUNNEL_FILE = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"


def tunnel_host() -> str:
    """
    Public tunnel hostname for Twilio callbacks, e.g. "foo.trycloudflare.com".

    One home for four copies of this resolution — app/main.py carried two
    (the first shadowed by the second, so it was dead), and app/offers/service.py
    and app/outbound/caller.py each had their own `_resolve_host`. Order is
    unchanged: TUNNEL_HOST, then the file the launcher writes, then the legacy
    NGROK_HOST, then localhost:8000. Returns a hostname with no scheme.

    TUNNEL_HOST is empty in the checked-in .env and is filled in at launch,
    which is why the file fallback is not merely legacy.
    """
    if settings.TUNNEL_HOST:
        return settings.TUNNEL_HOST
    if TUNNEL_FILE.is_file():
        return TUNNEL_FILE.read_text().strip()
    return settings.NGROK_HOST or "localhost:8000"
