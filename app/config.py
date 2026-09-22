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
    WHISPER_MODEL: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small.en"))
    # Sized per machine by scripts/predeploy.py. Note pipecat's
    # WhisperSTTSettings has no cpu_threads param, so this reaches the
    # voice_handler STT path only.
    WHISPER_NUM_THREADS: int = field(
        default_factory=lambda: _env_int("WHISPER_NUM_THREADS", 4)
    )
    # A presence check, not a value: `_get_stt_model` picks cuda when this is
    # set and non-empty. Kept as the raw string so the truthiness test is
    # unchanged — "0" selects cuda here exactly as it did before, which is the
    # behaviour CUDA_VISIBLE_DEVICES is documented to have.
    CUDA_VISIBLE_DEVICES: str = field(
        default_factory=lambda: _env("CUDA_VISIBLE_DEVICES", "")
    )

    # ── Text-to-speech (Kokoro) ─────────────────────────────────────
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

    # ── LLM backend: Ollama ─────────────────────────────────────────
    # The literal IPv4 default is deliberate, not a shortcut: on this platform
    # "localhost" resolves to ::1 first and Ollama binds IPv4 only, so the
    # refused IPv6 connect burns ~2,066 ms in SYN retransmits before Python
    # falls back — paid on every model-resolution and embedding call.
    OLLAMA_URL: str = field(
        default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434")
    )
    OLLAMA_MODEL: str = field(
        default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
    )
    # The single source of truth for the default context window. 8192 is what
    # the production voice system prompt (~3.5k tokens) plus RAG context needs;
    # scripts/predeploy.py sizes it per machine.
    OLLAMA_NUM_CTX: int = field(default_factory=lambda: _env_int("OLLAMA_NUM_CTX", 8192))
    # "" means "use the provider's own default": the call sites guard with
    # `float(T) if T else None`, so the empty string must survive intact rather
    # than being coerced to a number here.
    OLLAMA_TEMPERATURE: str = field(default_factory=lambda: _env("OLLAMA_TEMPERATURE", ""))
    EMBED_MODEL: str = field(default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text"))
    # US-004 streaming LLM, behind a flag; the batch path is retained unchanged
    # as the BRD-15 revert.
    LLM_STREAM: bool = field(
        default_factory=lambda: _env("LLM_STREAM", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )
    LLM_PROVIDER: str = field(
        default_factory=lambda: _env("LLM_PROVIDER", "auto").strip().lower()
    )
    # An escape hatch for a deliberate, MEASURED divergence — never a required
    # key. It defaults to OLLAMA_NUM_CTX because Ollama tears a runner down
    # whenever a request arrives with a different num_ctx, paying a full cold
    # reload (~6-11 s measured on this box); five context sizes against one
    # model meant the reload was paid constantly. Re-read from the same source
    # rather than from the resolved field, so this stays correct whatever the
    # field order becomes.
    SMALL_TASK_NUM_CTX: int = field(
        default_factory=lambda: int(
            _env("SMALL_TASK_NUM_CTX", _env("OLLAMA_NUM_CTX", "8192"))
        )
    )
    # The raw text, not the resolved value: Ollama's Go duration parser rejects
    # "-1" as a duration, so an integer (including a negative one) has to be
    # sent as an int while "24h" stays a string. `_resolve_keep_alive` decides
    # that at the call site. Getting it wrong breaks every call, not merely
    # residency.
    OLLAMA_KEEP_ALIVE: str = field(
        default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "-1")
    )

    # ── LLM backend: MLX (macOS Apple Silicon) ──────────────────────
    MLX_BASE_URL: str = field(
        default_factory=lambda: _env("MLX_BASE_URL", "http://127.0.0.1:1234")
    )
    MLX_MODEL: str = field(
        default_factory=lambda: _env("MLX_MODEL", "mlx-community/Qwen2.5-14B-Instruct-4bit")
    )
    MLX_PORT: int = field(default_factory=lambda: _env_int("MLX_PORT", 1234))
    MLX_EMBED_MODEL: str = field(
        default_factory=lambda: _env("MLX_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    )
    # mlx_lm.server has no context-size flag — max_tokens is the only output
    # window control on the MLX path.
    MLX_MAX_TOKENS: int = field(default_factory=lambda: _env_int("MLX_MAX_TOKENS", 2048))

    # ── Retrieval / knowledge base ──────────────────────────────────
    # Absolute and derived from the repo root, not the CWD, exactly as the call
    # sites did — a relative default here would move with the process's working
    # directory. A relative value IN .env still resolves against the CWD, which
    # is the call sites' behaviour preserved, not an oversight.
    CHROMA_DB_PATH: str = field(
        default_factory=lambda: _env(
            "CHROMA_DB_PATH", str(Path(__file__).resolve().parent.parent / "chroma_local_db")
        )
    )
    # Chunking is markdown-aware; these are the legacy defaults.
    RAG_CHUNK_SIZE: int = field(default_factory=lambda: _env_int("RAG_CHUNK_SIZE", 600))
    RAG_CHUNK_OVERLAP: int = field(default_factory=lambda: _env_int("RAG_CHUNK_OVERLAP", 90))
    # Retrieval.
    RAG_FETCH_K: int = field(default_factory=lambda: _env_int("RAG_FETCH_K", 20))
    RAG_TOP_K: int = field(default_factory=lambda: _env_int("RAG_TOP_K", 5))
    # Optional features — every one defaults to the pre-existing behaviour.
    RAG_SEARCH_MODE: str = field(default_factory=lambda: _env("RAG_SEARCH_MODE", "mmr"))
    # Cosine distance; 0.0 = disabled.
    RAG_SIMILARITY_THRESHOLD: float = field(
        default_factory=lambda: _env_float("RAG_SIMILARITY_THRESHOLD", 0.0)
    )
    # 0 = off.
    RAG_MAX_CONTEXT_CHARS: int = field(
        default_factory=lambda: _env_int("RAG_MAX_CONTEXT_CHARS", 0)
    )
    # Output-token ceiling for the SPOKEN answer. 192 comes from the measured
    # answer-length distribution, not from taste: over 83 golden-set cases the
    # 95th percentile was 106 tokens (qwen2.5:14b) and 82 (llama3.2:3b), so a
    # ceiling of 192 truncates none of them while bounding the worst case —
    # the one thing an unbounded generation cannot give a live call. 0 or a
    # negative removes the ceiling.
    OLLAMA_NUM_PREDICT: int = field(
        default_factory=lambda: _env_int_or("OLLAMA_NUM_PREDICT", 192)
    )

    # ── MCP retrieval service ───────────────────────────────────────
    RAG_MCP_URL: str = field(
        default_factory=lambda: _env("RAG_MCP_URL", "http://127.0.0.1:8010/mcp")
    )
    # The read timeout; connect is fixed at 1.0s in the client. 6.0s, raised
    # from 2.5s: under load ERC's embedding call queues behind Ollama
    # generation, so a 2.5s read timeout fired on a service that answers in
    # milliseconds — and the fallback then paid for a SECOND embedding. Waiting
    # is strictly cheaper than timing out and re-retrieving locally.
    RAG_MCP_TIMEOUT: float = field(
        default_factory=lambda: _env_float("RAG_MCP_TIMEOUT", 6.0)
    )
    # US-013 AC-2: a half-open probe gets a fraction of the serving budget, so
    # rediscovering "still down" costs a fraction of a full timeout. The floor
    # in `_timeout` keeps a probe from reading a merely-busy service as dead.
    RAG_MCP_PROBE_FRACTION: float = field(
        default_factory=lambda: _env_float("RAG_MCP_PROBE_FRACTION", 0.25)
    )
    # Circuit-breaker cooldown.
    RAG_MCP_COOLDOWN: float = field(
        default_factory=lambda: _env_float("RAG_MCP_COOLDOWN", 30)
    )

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
    COMPANY_NAME: str = field(default_factory=lambda: _env("COMPANY_NAME", "Meridian University"))
    AGENT_NAME: str = field(default_factory=lambda: _env("AGENT_NAME", "Alex"))

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
        default_factory=lambda: _env("UNIVERSITY_NAME", "Meridian University")
    )
    OFFER_EMAIL: str = field(
        default_factory=lambda: _env("OFFER_EMAIL", "admissions@university.edu")
    )
    OFFER_VALID_DAYS: int = field(
        default_factory=lambda: _env_int("OFFER_VALID_DAYS", 30)
    )
    OFFER_GUARD_MINUTES: int = field(
        default_factory=lambda: _env_int("OFFER_GUARD_MINUTES", 1)
    )
    DEFAULT_PAYMENT_LINK: str = field(
        default_factory=lambda: _env("DEFAULT_PAYMENT_LINK", "https://pay.university.edu/admissions")
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
