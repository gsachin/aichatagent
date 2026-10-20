"""
FastAPI application — University Admissions Voice Assistant.

Transport-agnostic server supporting:
- WAV test harness (test_transport.py)
- Browser microphone page (voice_client.html)
- Twilio Media Streams (with real credentials)
- Twilio outbound calling (automatic lead dialling)
- WhatsApp messaging + voice notes
- MCP tools for AI-driven lead management

Endpoints:
    GET  /                       — health check + navigation
    GET  /voice                  — browser mic page (voice_client.html)
    GET  /call                   — quick outbound call page (quick_call.html)
    WS   /ws/voice               — raw PCM audio endpoint (pipeline-ready)
    WS   /ws/voice/text          — text query through RAG + LLM pipeline
    GET  /twilio/voice           — TwiML response for inbound calls
    WS   /ws/twilio              — Twilio Media Streams (8 kHz u-law)
    WS   /ws/twilio-outbound     — Twilio Media Streams — outbound calls
    POST /twilio/outbound/status — Outbound call status callback
    POST /twilio/whatsapp        — WhatsApp webhook (text + documents; voice notes unsupported)
    POST /api/quick-call         — One-shot create lead + queue outbound call
    GET  /api/call-queue         — Poll call status for a lead
    GET  /mcp/sse                — MCP SSE transport
    POST /mcp/messages           — MCP message handler

Run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import os
import sys

# ── Bypass AppLocker DLL blocks (MUST be before any other imports) ──
# The xxhash DLL is blocked by Windows Application Control policy.
# LangChain → langsmith → xxhash triggers the block.
# These env vars disable langsmith tracing to avoid the import chain.
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")
os.environ.setdefault("HF_HUB_ENABLE_HF_XET", "0")

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import asyncio as _asyncio
import base64
import re
import time as _time
from datetime import datetime, timezone
from xml.sax.saxutils import escape
import json
import logging
from pathlib import Path
from urllib.parse import quote
from dataclasses import dataclass, field
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Deliberately below the load_dotenv() call above and the AppLocker setdefaults,
# which must run first. Importing app.config also loads .env itself, so this is
# order-independent in practice — but keeping it here means the module-level
# reads below cannot run before the environment is prepared.
from app import audio_format
from app.config import settings
from app.languages import (
    configured_foreign_languages,
    is_supported_language,
    language_from_command,
    normalize_language_code,
    resolve_answer_language,
)

def _configure_logging() -> None:
    """Apply LOG_LEVEL and LOG_FILE from the environment.

    Both keys shipped in .env and were read by nothing: the level was pinned to
    INFO and no file handler existed, so `logs/voice_assistant.log` was never
    created and had never been written to. Found by US-011's reader sweep --
    a key that is set and read by nothing is invisible until someone sweeps
    for exactly that.
    """
    level_name = settings.LOG_LEVEL
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("voice_api").warning(
            "LOG_LEVEL=%r is not a logging level; using INFO", level_name)
        level = logging.INFO
    else:
        logging.basicConfig(level=level)

    log_file = settings.LOG_FILE
    if not log_file:
        return
    try:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError as exc:
        # A bad LOG_FILE must not stop the stack from serving calls.
        logging.getLogger("voice_api").warning(
            "LOG_FILE=%r could not be opened (%s); file logging disabled",
            log_file, exc)


_configure_logging()
logger = logging.getLogger("voice_api")


# ── US-007 Phase 1.1: the call-time readiness surface (/health + /ready) ──
# The boot gate is assessed once at startup (warm, all four clauses) and the
# verdict is cached here. `/ready` serves the cache; `?refresh=1` re-runs only
# the cheap clauses (services, residency, GPU, config) in a worker thread and
# carries the prefix clause from the boot assessment -- a poll never re-warms.
_readiness_cache: dict | None = None
_readiness_lock = _asyncio.Lock()

#: How long the boot assessment waits for its OWN port to accept a connection
#: before running anyway. uvicorn binds :8000 only after the lifespan returns,
#: and on this box the startup path can take tens of seconds to get there (a
#: dead Postgres costs ~2 s per attempt, and the workers' first polls run
#: before the bind). Generous on purpose: the wait is a background task, and a
#: verdict that is late is better than a verdict that is false.
BOOT_BIND_WAIT_S = 60.0


# NOTE: a second, earlier copy of the tunnel-host resolver used to sit here.
# Python took the later definition, so this one was dead code — two answers to
# one question, which is the shape US-011 exists to remove. The survivor now
# delegates to app.config.tunnel_host().


# ── Startup / shutdown ───────────────────────────────────────────────

_db_available = False
_outbound_worker = None
_follow_up_scheduler = None
_crm_worker = None
_greeting_warmup_task = None

#: Fill the fixed-greeting cache after the server is up. Background work: it
#: never delays binding, and it stops as soon as a real call is active. The
#: first call in each of these languages otherwise pays the cold TTS path
#: (measured 3.6-4.0 s for hi/en before the greeting path was streamed).
_GREETING_WARMUP_ENABLED = os.environ.get(
    "GREETING_WARMUP", "1"
).strip().lower() not in ("0", "false", "off", "no")
#: Core languages the IVR always offers, plus every foreign language this
#: deployment enables. Pre-rendered assets cover all nine and are checked
#: first, so this costs nothing when the deploy step ran; it is the fallback
#: for a fresh clone or a skipped build, where the first call would otherwise
#: pay live synthesis. Kept as a plain tuple because tests monkeypatch it.
_GREETING_WARMUP_LANGUAGES = (
    "en", "hi", "gu", *sorted(configured_foreign_languages())
)


async def _warm_greeting_cache(delay: float = 2.0) -> None:
    """Pre-synthesise the fixed greetings while the box is idle."""
    await _asyncio.sleep(delay)
    try:
        from app.voice_handler import (
            VoiceCallSession,
            greeting_asset_frames,
            greeting_cache_get,
            greeting_cache_key,
            greeting_cache_put,
        )
        from app.voice_system_prompt import call_greeting
    except Exception:                            # noqa: BLE001
        logger.exception("Greeting warmup imports failed")
        return

    for language in _GREETING_WARMUP_LANGUAGES:
        for direction in ("inbound", "outbound"):
            if _active_call_sids:
                logger.info("Greeting warmup paused: a call is active")
                return
            try:
                text = call_greeting(language, direction)
                key = greeting_cache_key(language, direction, text)
                if greeting_cache_get(key):
                    continue
                if greeting_asset_frames(language, direction, text):
                    logger.info(
                        "Greeting asset present for %s/%s; warmup skipped",
                        language, direction,
                    )
                    continue
                session = VoiceCallSession(
                    language=language, direction=direction
                )
                frames: list[bytes] = []
                async for audio, sr in session.synthesise_stream(text):
                    frames.extend(session._chunk_to_ulaw_frames(audio, sr))
                if frames:
                    greeting_cache_put(key, frames)
                    logger.info(
                        "Greeting warmup cached %s/%s (%d frames)",
                        language, direction, len(frames),
                    )
            except Exception:                    # noqa: BLE001
                logger.exception(
                    "Greeting warmup failed for %s/%s", language, direction
                )

# AEC workaround: Twilio <Stream> has no echo-cancellation attribute, so the
# caller's mic re-captures our own speech ("Listen to her again" artifacts) and
# that bleed must not reach the STT.
#
# The DECISION moved to the session in US-014 (`VoiceCallSession.
# admit_inbound_frame`, app/voice_handler.py) so that both Twilio handlers share
# one decision point (TRD-05) instead of each holding a copy. This name is kept
# because it is the setting an operator reaches for, and because the pre-US-014
# escape hatch still means what it always did: with `BARGE_IN_ENABLED=0`,
# MUTE_STT_DURING_TTS=1 discards the caller's overlapping speech and 0 lets the
# bleed through. With the gate ON, the gate supersedes it.
MUTE_STT_DURING_TTS = settings.MUTE_STT_DURING_TTS


from contextlib import asynccontextmanager


async def _ws_send(websocket, text: str) -> bool:
    """
    Send one JSON text frame over a call WebSocket.

    Twilio closes media streams on caller hangup, no-answer, or tunnel
    blips; an in-flight send then raises. Treat every send failure as a
    clean disconnect (returns False) instead of crashing the handler —
    crashing made Twilio play the "We seem to have lost the connection"
    fallback <Say> while the call was still live.
    """
    try:
        await websocket.send_text(text)
        return True
    except Exception as e:
        logger.info(f"WS send failed (stream closed): {type(e).__name__}")
        return False


async def _hangup_twilio_call(call_sid: str) -> None:
    """
    End a live Twilio call via REST — used by the deterministic hangup
    when a hard sign-off phrase ("bye", "see ya") is detected pre-LLM.
    Safe to call with a stream_sid fallback; Twilio rejects unknown SIDs
    and we swallow the error.
    """
    if not call_sid:
        return
    try:
        from twilio.rest import Client

        from app.config import settings

        def _update():
            Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).calls(
                call_sid
            ).update(status="completed")

        await _asyncio.to_thread(_update)
        logger.info(f"Call terminated (sign-off detected): {call_sid}")
    except Exception:
        logger.exception("Twilio hangup failed")


_machine_profile_cache: dict = {"at": 0.0, "result": None}


def _machine_profile_status() -> dict:
    """
    Drift status for health/dashboard payloads (5-minute TTL so
    monitoring polls don't re-probe the hardware every request).
    """
    now = _time.monotonic()
    if _machine_profile_cache["result"] is None or now - _machine_profile_cache["at"] > 300:
        try:
            from app.hardware_profile import check_drift

            drift = check_drift()
            _machine_profile_cache["result"] = {
                "state": drift["state"],
                "tier_id": drift.get("tier_id"),
                "diffs": drift.get("diffs", []),
            }
        except Exception:
            _machine_profile_cache["result"] = {"state": "error", "tier_id": None, "diffs": []}
        _machine_profile_cache["at"] = now
    return _machine_profile_cache["result"]


@asynccontextmanager
def _reconcile_workers() -> None:
    """Report the configured FASTAPI_WORKERS against what is actually running.

    FASTAPI_WORKERS is machine-sizing metadata written by the hardware profiler
    (app/hardware_profile.py), not a worker count.

    Reading it here does not change the worker count, and it is deliberately NOT
    wired to `--workers`: this stack holds model residency (whisper, Kokoro) and
    per-call session state in-process, so a second worker loads a second copy of
    every model. On a 16 GB card already holding ~13 GB, that is a crash, not a
    speedup.

    CORRECTED 2026-09-21 (US-011, TAC-3), in two steps. This docstring used to
    assert that "No launcher passes it to uvicorn -- start_services.ps1,
    start_services.sh, the Dockerfile and docker-compose all start a single
    process", and the warning below told the operator "No launcher honours the
    setting; the running count is 1". Both were false. The list had simply never
    included start.sh, whose non-DEV_MODE branch passed:

        --workers "${FASTAPI_WORKERS:-4}"

    start.sh is not dead code -- it is the launcher SETUP_GUIDE.md,
    README_DEPLOYMENT.md and SETUP_COMPLETE.md point operators at. So the key
    was honoured on that path, and substituted 4 when it was unset OR empty:
    four workers each holding their own copy of the models, which is the crash
    described above, reached by doing nothing.

    The launcher decision that followed was to remove the flag rather than tune
    its default, so start.sh now starts one process like every other launcher.
    The original claim is therefore true again -- but because no launcher passes
    the flag, rather than because the list of launchers was incomplete. The key
    is reported as NOT IN EFFECT below, and is inert on every path.
    """
    raw = settings.FASTAPI_WORKERS
    if not raw:
        return
    try:
        configured = int(raw)
    except ValueError:
        logger.warning("FASTAPI_WORKERS=%r is not an integer; ignoring", raw)
        return
    if configured > 1:
        logger.warning(
            "FASTAPI_WORKERS=%d is set, and no launcher honours it: this stack "
            "runs a single uvicorn worker by design (in-process model "
            "residency and per-call session state), so the running count is 1. "
            "The key is machine-sizing metadata written by the hardware "
            "profiler -- its value is not a worker count, so 4 does not mean "
            "four processes. Reported as NOT IN EFFECT; see "
            "doc/sdlc/modules/MOD-07-config-boot.md.",
            configured,
        )


async def lifespan(app_instance):
    """Initialize and cleanup resources."""
    global _db_available, _outbound_worker, _follow_up_scheduler
    global _greeting_warmup_task
    # Startup
    _reconcile_workers()

    # Surface the three independent choices at one place: generation provider,
    # context source, and TTS provider. A misconfigured openai provider fails
    # here, before a caller is on the line.
    try:
        from app.config import ConfigurationError
        from app.llm_backend import provider_name

        logger.info(
            "LLM provider=%s context=%s tts=%s",
            provider_name(), settings.CONTEXT_MODE, settings.TTS_PROVIDER,
        )
        if not settings.LLM_API_KEY:
            logger.warning(
                "LLM_API_KEY is empty: every LLM call will use the fixed-response "
                "path until the key is added to .env and the stack is restarted."
            )
        if settings.CONTEXT_MODE == "doc":
            from app.doc_context import document_stats, source_label

            stats = document_stats()
            logger.info(
                "Document context: %s — %d bytes, %d tokens%s",
                source_label(), stats.bytes_read, stats.tokens,
                " [TRUNCATED]" if (stats.truncated_bytes or stats.truncated_tokens) else "",
            )
    except ConfigurationError:
        logger.exception("Invalid LLM configuration — refusing to start")
        raise
    except Exception:
        logger.exception("LLM configuration check failed")

    try:
        from app.database import init_db

        _db_available = await init_db()
        if _db_available:
            logger.info("Database: PostgreSQL connected — lead capture enabled")
        else:
            logger.info("Database: not available — running without lead capture")
    except Exception as e:
        logger.warning(f"Database init skipped: {e}")
        _db_available = False

    # Machine-profile drift check: warn when the hardware differs from
    # what .env was sized for (scripts/predeploy.py). Cheap (~100 ms),
    # stdlib-only, never raises.
    try:
        from app.hardware_profile import check_drift

        drift = check_drift()
        if drift["state"] == "drifted":
            logger.warning(
                "Machine drift detected: %s — config was sized for tier %s. %s",
                drift.get("diffs"), drift.get("tier_id"), drift.get("hint"),
            )
        elif drift["state"] == "no_profile":
            logger.info("No machine profile found — run: python scripts/predeploy.py")
        elif drift["state"] == "ok":
            logger.info("Machine profile matches config (tier %s)", drift.get("tier_id"))
    except Exception:
        logger.debug("Machine-profile drift check skipped", exc_info=True)

    # Pre-warm the document, the configured STT provider and Kokoro so the
    # first turn is warm.
    try:
        import asyncio as _asyncio

        def _warmup():
            from app.rag import warmup as rag_warmup

            rag_warmup()
            logger.info("Document context pre-warmed")
            # Load the provider chain: Sarvam key check + IndicConformer models
            # when they are selected, or faster-whisper when it is primary.
            from app import stt as stt_registry

            stt_registry.warmup()
            logger.info("STT providers pre-warmed")

            # Pre-load Kokoro too (C4 / Class A). Whisper was warmed here and
            # Kokoro was not, so the FIRST synthesis of the FIRST call loaded
            # the ONNX model -- from inside an `async def`, on the event loop,
            # while every other live call, media stream and keepalive waited.
            # A caller's first answer is the turn they are most likely to be
            # timing, and it was the one paying a model load.
            try:
                from app.voice_handler import _get_tts_engine, warmup_foreign_g2p

                _get_tts_engine()
                warmup_foreign_g2p()
                logger.info("Kokoro TTS and ja/zh G2P pre-warmed")
            except Exception as tts_exc:                # noqa: BLE001
                logger.warning(f"Kokoro pre-warm skipped: {tts_exc}")

        await _asyncio.to_thread(_warmup)
    except Exception as e:
        logger.warning(f"Warmup skipped: {e}")

    # US-007 Phase 1.1: assess the boot gate once so `/ready` serves the SAME
    # verdict the operator's CLI gate produces -- warmth included. Deferred to a
    # background task: uvicorn binds the port only AFTER startup completes, so
    # an assessment run inline in the lifespan probes its own :8000 socket
    # before it exists and would report the app it is serving as DOWN (the
    # CLI gate never has this problem - start_services.ps1 runs it after the
    # app is up). Never blocks startup; a failed assessment leaves the cache
    # empty and `/ready` falls back to a full assessment per request.
    #
    # The deferral WAITS for our own socket rather than sleeping a fixed 3 s.
    # It used to sleep, and the assumption behind that (startup is quick) does
    # not hold here: with Postgres down the startup path blocks the loop for
    # ~20 s while the workers' first DB attempts time out, so the clause-1
    # probe fired BEFORE uvicorn bound and reported the app it was serving as
    # down -- "NOT READY (FastAPI :8000 not listening)" logged on a stack that
    # answered `/health` a second later (measured 2026-09-21).

    async def _assess_readiness_at_boot() -> None:
        # The write below must name the MODULE global. A `global` declared in
        # the enclosing lifespan does not carry into a nested function, so
        # without this line the verdict is computed, logged, and thrown away:
        # the cache stays None, and every first `/ready` re-runs the whole
        # gate -- warm included -- instead of serving the boot verdict.
        global _readiness_cache
        try:
            from app import boot_readiness as _boot

            _boot.load_env()
            deadline = _time.monotonic() + BOOT_BIND_WAIT_S
            while _time.monotonic() < deadline:
                if await _asyncio.to_thread(
                        _boot.tcp_listening, "127.0.0.1",
                        settings.FASTAPI_PORT, 0.5):
                    break
                await _asyncio.sleep(0.25)

            _booted = (await _asyncio.to_thread(_boot.assess, True)).to_dict()
            _booted["status"] = "ready" if _booted.get("ready") else "not_ready"
            _readiness_cache = _booted
            logger.info("Readiness assessed at boot: %s (%s)",
                        "READY" if _booted.get("ready") else "NOT READY",
                        "; ".join(_booted.get("missing") or ["no gaps"])[:200])
        except Exception as e:
            logger.warning(f"Boot readiness assessment skipped: {e}")

    _readiness_task = _asyncio.create_task(_assess_readiness_at_boot())

    if _GREETING_WARMUP_ENABLED:
        _greeting_warmup_task = _asyncio.create_task(_warm_greeting_cache())
        logger.info("Greeting warmup scheduled")

    # Start outbound call worker
    try:
        from app.outbound.caller import OutboundCallWorker

        _outbound_worker = OutboundCallWorker(
            poll_interval=settings.OUTBOUND_POLL_INTERVAL
        )
        await _outbound_worker.start()
        logger.info("Outbound call worker started")
    except Exception as e:
        logger.warning(f"Outbound call worker failed to start: {e}")
        _outbound_worker = None

    # Start follow-up scheduler
    try:
        from app.outbound.scheduler import FollowUpScheduler

        _follow_up_scheduler = FollowUpScheduler(
            poll_interval=settings.FOLLOW_UP_POLL_INTERVAL
        )
        await _follow_up_scheduler.start()
        logger.info("Follow-up scheduler started")
    except Exception as e:
        logger.warning(f"Follow-up scheduler failed to start: {e}")
        _follow_up_scheduler = None

    # Start the CRM outbox worker — drains failed CRM writes and expires lapsed
    # offers. Until this existed, a transient CRM failure queued a row that
    # nothing ever replayed.
    try:
        from app.crm.worker import CrmOutboxWorker

        _crm_worker = CrmOutboxWorker(
            poll_interval=settings.CRM_OUTBOX_POLL_INTERVAL
        )
        await _crm_worker.start()
        logger.info("CRM outbox worker started")
    except Exception as e:
        logger.warning(f"CRM outbox worker failed to start: {e}")
        _crm_worker = None

    yield
    # Shutdown
    if _readiness_task and not _readiness_task.done():
        _readiness_task.cancel()
    if _greeting_warmup_task and not _greeting_warmup_task.done():
        _greeting_warmup_task.cancel()
    if _outbound_worker:
        _outbound_worker.stop()
    if _follow_up_scheduler:
        await _follow_up_scheduler.stop()
    if _crm_worker:
        await _crm_worker.stop()
    try:
        from app import tts_sarvam

        await tts_sarvam.aclose()
    except Exception:                            # noqa: BLE001
        logger.debug("Sarvam TTS client close skipped", exc_info=True)
    logger.info("Server shutting down")


app = FastAPI(title="University Admissions Voice Assistant", lifespan=lifespan)

# Serve static files (CSS, JS, audio) for dashboard + voice client
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Shared helpers ────────────────────────────────────────────────────


def _resolve_tunnel_host() -> str:
    """
    Resolve the public tunnel hostname for Twilio callbacks.

    Delegates to app.config.tunnel_host(), which is the one home for this
    resolution — it was previously implemented here, again above (dead, the
    later definition won), in app/offers/service.py and in
    app/outbound/caller.py, all four byte-equivalent.
    """
    from app.config import tunnel_host

    return tunnel_host()


# ── TwiML template ───────────────────────────────────────────────────
#
# `<Parameter>` is the only way the caller's number can reach the media stream:
# Twilio Media Streams does not put the caller's number in the `start` event, so
# it is captured at the webhook and threaded through as a custom parameter. The
# value is XML-escaped on the way in (R14) — a `&` or `<` in a query parameter
# would otherwise produce TwiML that Twilio rejects at call time.
#
# `<Stream>` is therefore no longer self-closing.

def _stream_markup(host: str, phone: str = "", language: str = "",
                   locked: bool = False) -> str:
    """Build the `<Stream>` element, carrying the caller number and language.
    Both values reach the media stream as custom `<Parameter>`s because Media
    Streams does not carry them in the `start` event. Values are XML-escaped
    (R14); an `&` or `<` would otherwise produce TwiML Twilio rejects.

    ``locked`` says the language was explicitly chosen (an IVR digit or a
    pinned ?language=), not merely the default. The media stream needs that
    distinction to decide whether a per-utterance detection may change the
    answer language; without it every call looks like a deliberate choice.
    """
    url = f"wss://{host}/ws/twilio"
    params = []
    language = normalize_language_code(language)
    if phone:
        params.append(
            f'<Parameter name="phone" value="{escape(phone, {chr(34): "&quot;"})}" />'
        )
    if language:
        params.append(
            f'<Parameter name="language" value="{escape(language, {chr(34): "&quot;"})}" />'
        )
    if locked:
        params.append('<Parameter name="languageLock" value="1" />')
    if not params:
        return f'<Stream url="{url}" />'
    return f'<Stream url="{url}">' + "".join(params) + "</Stream>"


#: Inbound IVR digit -> answer language. 1/2/3 are the demo languages; any
#: other digit (or no input) keeps DEFAULT_LANGUAGE.
_LANGUAGE_BY_DIGIT = {"1": "en", "2": "hi", "3": "gu"}

#: Second-level menu, reached by pressing 9 on the first menu. The order is the
#: order of DEFAULT_FOREIGN_LANGUAGES.
_FOREIGN_LANGUAGE_BY_DIGIT = {
    "1": "es", "2": "fr", "3": "it", "4": "ja", "5": "pt", "6": "zh",
}


def _language_for_call(digits: str, default: str = "") -> str:
    """Map the IVR digit to a language code (default when unrecognised)."""
    code = _LANGUAGE_BY_DIGIT.get(
        (digits or "").strip(), default or settings.DEFAULT_LANGUAGE
    )
    return normalize_language_code(code)


TWIML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        {stream}
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back or try our WhatsApp channel for immediate assistance.</Say>
</Response>"""

# IVR menu — shown before connecting to AI
TWIML_IVR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" timeout="3" action="{action}" method="GET">
        <Say voice="Polly.Joanna">
            Welcome to the Meridian University Admissions helpline.
            Press 1 for English.
            Press 2 for Hindi.
            Press 3 for Gujarati.
            Press 4 to continue in the default language.
            Or, simply start speaking to ask any question in English.
        </Say>
    </Gather>
    <Say voice="Polly.Joanna">I didn't receive any input. Connecting you to the AI assistant now.</Say>
    <Connect>
        {stream}
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back later.</Say>
</Response>"""

#: The same first menu plus the second-level entry. Used only when
#: FOREIGN_LANGUAGES is non-empty, so the disabled feature restores the
#: original template byte-for-byte.
TWIML_IVR_TEMPLATE_FOREIGN = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" timeout="3" action="{action}" method="GET">
        <Say voice="Polly.Joanna">
            Welcome to the Meridian University Admissions helpline.
            Press 1 for English.
            Press 2 for Hindi.
            Press 3 for Gujarati.
            Press 4 to continue in the default language.
            For Spanish, French, Italian, Japanese, Portuguese or Chinese, press 9.
            Or, simply start speaking to ask any question in English.
        </Say>
    </Gather>
    <Say voice="Polly.Joanna">I didn't receive any input. Connecting you to the AI assistant now.</Say>
    <Connect>
        {stream}
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back later.</Say>
</Response>"""

#: Second-level menu. It is English because no language has been chosen yet;
#: the AI answers in the language selected by the digit.
TWIML_FOREIGN_IVR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" timeout="3" action="{action}" method="GET">
        <Say voice="Polly.Joanna">
            Press 1 for Spanish.
            Press 2 for French.
            Press 3 for Italian.
            Press 4 for Japanese.
            Press 5 for Portuguese.
            Press 6 for Chinese.
        </Say>
    </Gather>
    <Say voice="Polly.Joanna">I didn't receive any input. Connecting you to the AI assistant now.</Say>
    <Connect>
        {stream}
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back later.</Say>
</Response>"""


def _foreign_menu_action(language: str = "") -> str:
    """The second-level Gather action, carrying the stage and any pinned code."""
    suffix = f"&amp;language={quote(language)}" if language else ""
    return f"/twilio/voice/connect?stage=foreign{suffix}"


# US-016: the refusal. A third caller hears a sentence produced ahead of time
# and the call ends. There is deliberately NO <Connect> here -- no media stream
# is established, so no session, no history and no KV allocation is created for
# a call that has no capacity to run in. The carrier has already answered the
# PSTN leg (an inbound call cannot be declined by the app); the only lever the
# application has is which TwiML it returns, and this returns the refusal.
TWIML_BUSY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{audio_url}</Play>
    <Hangup/>
</Response>"""

# Used only when the pre-synthesised asset is absent. See `_busy_twiml`.
TWIML_BUSY_SAY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{text}</Say>
    <Hangup/>
</Response>"""

#: Carrier fallback for the six foreign languages. Twilio's `<Say>` needs a
#: voice that can pronounce the language; the English template above stays
#: byte-identical for the English path.
TWIML_BUSY_SAY_FOREIGN_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="{voice}" language="{language}">{text}</Say>
    <Hangup/>
</Response>"""

#: Polly voices Twilio documents for the six languages. The exact voice list is
#: carrier-side; the language attribute is what makes the fallback audible even
#: if a voice name is retired.
_POLLY_FOREIGN_VOICE = {
    "es": ("Polly.Lupe", "es-MX"),
    "fr": ("Polly.Celine", "fr-FR"),
    "it": ("Polly.Bianca", "it-IT"),
    "ja": ("Polly.Takumi", "ja-JP"),
    "pt": ("Polly.Camila", "pt-BR"),
    "zh": ("Polly.Zhiyu", "cmn-CN"),
}


def _busy_twiml(host: str, language: str = "") -> str:
    """TwiML that plays the busy message and connects nothing.

    The pre-synthesised asset is the path: it is the assistant's own voice,
    produced ahead of time, and playing it costs this box nothing (US-016
    AC-3/AC-6). `app/static/` is gitignored, though, so a stack that has not
    run `scripts/build_call_assets.py` has no asset -- and a refusal that
    played a missing file would disconnect the caller in silence, which is a
    worse outcome than the over-capacity call it exists to prevent.

    So the fallback is the carrier's own `<Say>`: a different voice, but words
    the caller can act on, and still zero cost to this box -- the synthesis
    happens at Twilio, not here. The boot gate reports the missing asset
    separately, so the degraded voice is visible rather than silent.
    """
    from app.admission import BUSY_ASSET, asset_available, busy_asset, busy_text

    code = normalize_language_code(language)
    asset = busy_asset(code)
    if asset_available(asset):
        return TWIML_BUSY_TEMPLATE.format(
            audio_url=f"https://{host}/static/audio/{asset}")
    if asset != BUSY_ASSET and asset_available(BUSY_ASSET):
        logger.warning(
            "/twilio: busy asset %s missing -- using the English asset", asset)
        return TWIML_BUSY_TEMPLATE.format(
            audio_url=f"https://{host}/static/audio/{BUSY_ASSET}")

    text = busy_text(code)
    logger.warning(
        "/twilio: busy asset %s missing -- falling back to carrier <Say>. "
        "Build it: .venv/Scripts/python.exe scripts/build_call_assets.py", asset)
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    voice = _POLLY_FOREIGN_VOICE.get(code)
    if voice:
        return TWIML_BUSY_SAY_FOREIGN_TEMPLATE.format(
            voice=voice[0], language=voice[1], text=escaped)
    return TWIML_BUSY_SAY_TEMPLATE.format(text=escaped)


# ── u-law conversion utilities ───────────────────────────────────────
# The width argument is the pipeline's, from `app/audio_format.py`, rather than a
# literal repeated at each call site. The voice pipeline's own copy of this pair
# lives in `app/voice_handler.py` and reads the same constant.

def ulaw_to_pcm(ulaw_bytes: bytes) -> bytes:
    """Convert 8 kHz u-law bytes to 16-bit linear PCM bytes."""
    import audioop
    return audioop.ulaw2lin(ulaw_bytes, audio_format.SAMPLE_WIDTH)


def pcm_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit linear PCM bytes to 8 kHz u-law bytes."""
    import audioop
    return audioop.lin2ulaw(pcm_bytes, audio_format.SAMPLE_WIDTH)


# ── WebSocket: Raw PCM (local voice pipeline) ────────────────────────

@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    """
    WebSocket endpoint for streaming PCM audio frames (16-bit, mono, 16 kHz).

    Accepts:  binary PCM audio frames
    Returns:  binary PCM audio (echo for now — pipeline wiring pending GPU)

    Full pipeline path (on GPU machine):
        audio in -> VAD -> STT -> RAG -> LLM -> TTS -> audio out
    """
    await websocket.accept()
    logger.info("WS /ws/voice: client connected")

    transcript_parts: list[str] = []

    try:
        while True:
            data = await websocket.receive_bytes()

            # Attempt pipeline processing if available
            try:
                # In the full pipeline, this runs:
                #   VAD -> Whisper STT -> RAG -> LLM -> Kokoro TTS
                # For now, echo back (transport validation)
                response = data
            except Exception:
                response = data

            await websocket.send_bytes(response)

    except WebSocketDisconnect:
        logger.info("WS /ws/voice: client disconnected")
        # Post-call: save transcript + extract lead
        await _handle_disconnect(transcript_parts)

    except KeyError:
        logger.info("WS /ws/voice: non-binary frame, ignoring")
        await websocket.close(code=1003, reason="Binary frames only")

    except Exception:
        logger.exception("WS /ws/voice: unexpected error")
        await _handle_disconnect(transcript_parts)
        try:
            await websocket.close()
        except Exception:
            pass


# ── WebSocket: Text query through RAG + LLM ──────────────────────────

@app.websocket("/ws/voice/text")
async def websocket_voice_text(websocket: WebSocket):
    """
    WebSocket endpoint for text queries through the RAG + LLM pipeline.

    Accepts:  JSON {"query": "your question"}
    Returns:  JSON {"answer": "...", "context_used": true/false}

    This bypasses STT/TTS and tests the core AI directly.
    Works on any machine — no GPU needed.
    """
    await websocket.accept()
    logger.info("WS /ws/voice/text: client connected")

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
                query = msg.get("query", "")
            except json.JSONDecodeError:
                query = data  # plain text = query

            if not query.strip():
                await websocket.send_text(json.dumps({
                    "error": "Empty query",
                }))
                continue

            logger.info(f"RAG query: {query[:80]}...")

            # Run through the pipeline
            try:
                from app.pipeline import build_rag_prompt, test_pipeline_with_text

                language = normalize_language_code(
                    str(msg.get("language", "") if isinstance(msg, dict) else "")
                )
                answer = await test_pipeline_with_text(query, language=language or None)

                await websocket.send_text(json.dumps({
                    "query": query,
                    "answer": answer,
                    "language": language or "en",
                    "status": "ok",
                }))
            except Exception as e:
                logger.exception("RAG pipeline error")
                await websocket.send_text(json.dumps({
                    "query": query,
                    "error": str(e),
                    "status": "error",
                }))

    except WebSocketDisconnect:
        logger.info("WS /ws/voice/text: client disconnected")
    except Exception:
        logger.exception("WS /ws/voice/text: unexpected error")
        try:
            await websocket.close()
        except Exception:
            pass


# ── US-014: the turn as its own task ─────────────────────────────────
# One cause, two defects. `process_utterance` used to be awaited inline in the
# receive loop, so nothing read the socket while a turn ran:
#
#   1. **No barge-in.** A caller frame arriving mid-turn is buffered by the
#      ASGI layer and delivered only after playback had finished, so the
#      caller's interruption was answered late as a brand-new question. The
#      guard meant to drop those frames lived at the receive site, and its
#      `tts_playing` flag was True only while the receive site was NOT running
#      -- `BARGE_IN_DETECTED` appears 0 times in 13.7 MB of live call logs.
#   2. **WS 1011.** A handler busy in a thread reads nothing, so the carrier's
#      keepalive pings went unanswered and the stream died. That is the
#      measured conclusion of `doc/perf/tools/_ws1011_probe.py`, and it is how
#      both harness-clean cold N=2 runs in `doc/perf/runs` ended.
#
# So the receive loop owns the socket for the life of the connection and the
# turn owns the audio. Nothing cancels the turn task: it is the only sender, so
# it is also the only thing that can stop what the caller HEARS, and letting it
# stop itself keeps its cleanup off the reader's critical path.

@dataclass
class _VoiceTurn:
    """What one turn needs, grouped so the turn can be a task of its own."""

    websocket: Any
    session: Any
    label: str                       # "WS /ws/twilio" / "WS /ws/twilio-outbound"
    stream_sid: str = ""
    call_sid: str = ""
    transcript_parts: list[str] = field(default_factory=list)
    call_linker: Any = None
    #: Set when the turn itself ended the call, so the reader's disconnect path
    #: does not hang up a second time -- Twilio answers the repeat with a 404
    #: and the log calls it "Twilio hangup failed", which is a red herring that
    #: has already cost one investigation.
    ended: dict = field(default_factory=lambda: {"by_turn": False})


async def _clear_carrier_playback(websocket, stream_sid: str) -> bool:
    """Empty the carrier's buffered playback (Twilio Media Streams `clear`).

    Stopping our sends is not enough on its own: Twilio plays out what it has
    already buffered, so without this the caller keeps hearing the agent for as
    long as that buffer lasts. Measured on the 2026-09-22 call: 601 chunks --
    12.02 s of audio -- were written to the socket in 7 ms and the log declared
    the agent finished, while the caller went on hearing it. `clear` is the only
    message that stops audio already handed over, and it is available because
    `_stream_markup` builds a bidirectional <Connect><Stream>.
    """
    return await _ws_send(websocket, json.dumps({
        "event": "clear", "streamSid": stream_sid or "",
    }))


async def _write_frames(t: _VoiceTurn, frames: list[bytes], sent: dict,
                        marked: dict | None = None) -> None:
    """Write frames to the carrier, stopping the moment the caller speaks.

    `sent["n"]` counts writes and `marked` carries the US-001 first-audio mark
    for a caller turn; the greeting has no trace to mark and passes None.
    Shared by a turn's playback and the greeting, because "what we sent, in send
    order" is what the echo gate's reference is built from in both.
    """
    session = t.session
    if sent["n"] == 0 and frames:
        # SPEAKING means "audible", so arm the gate at the first frame that
        # actually reaches the carrier — not before the pipeline. See
        # `VoiceCallSession.ensure_speaking` for the live failure this fixes.
        # An empty chunk list must not arm it: there is still nothing to hear.
        session.ensure_speaking()
    for chunk in frames:
        if session.cancel_requested():
            # The caller is talking. Stop feeding the carrier -- the caller
            # of this function sends `clear` so they stop HEARING us too.
            return
        out_payload = base64.b64encode(chunk).decode("ascii")
        response = json.dumps({
            "event": "media",
            "streamSid": t.stream_sid or "",
            "media": {"payload": out_payload},
        })
        if not await _ws_send(t.websocket, response):
            raise WebSocketDisconnect(code=1000)
        # The echo gate's reference is what was SENT, in send order: one
        # inbound frame consumes one of these, which is what keeps the gate
        # aligned with what the caller is hearing right now.
        session.note_playback([chunk])
        sent["n"] += 1
        # US-001: first_audio_sent is taken at the socket write, not at the
        # end of synthesis — this is the end of BRD-02's measured segment.
        if marked is not None and not marked["first"]:
            marked["first"] = True
            _t = getattr(session, "_trace", None)
            if _t is not None:
                _t.mark("first_audio_sent")
                _t.emit()


async def _await_playout(session) -> int:
    """Hold the turn open until the caller has HEARD what was written.

    `_write_frames` hands the carrier a whole answer in milliseconds -- 633
    chunks in 7 ms on the 2026-09-29 inbound call, 1004 in ~10 ms on the
    2026-10-06 outbound one -- and Twilio plays it out afterwards on its own
    20 ms clock. This closes that gap. It is deliberately NOT a pacing loop: the
    frames are already buffered, so a stalled event loop cannot starve the
    caller's audio, and the session's playout queue models the buffer, drained
    on the wall clock by `VoiceCallSession._advance_reference`.

    Without the wait, SPEAKING -- and with it the echo gate and the cancel
    signal -- covers the silence BEFORE playback and none of the playback
    itself: over 16 MB of live call logs every `BARGE_IN_ADMITTED` carries
    `ref_env=0.0`, which is a gate that has never once been evaluated against
    playing audio, i.e. a caller talking over a 20 s answer being ignored for
    its whole length.

    Returns the frames still unplayed when it stops: the honest count of what
    the caller did not hear.
    """
    from app.voice_handler import FRAME_MS

    tick = FRAME_MS / 1000.0
    while not session.cancel_requested() and session.playout_remaining():
        await _asyncio.sleep(tick)
    return session.playout_remaining()


async def _run_voice_turn(t: _VoiceTurn) -> None:
    """One caller turn: pipeline, playback, transcript, hangup. Never raises."""
    session = t.session
    sent = {"n": 0}
    marked = {"first": False}

    async def _send_chunks(frames: list[bytes]) -> None:
        """The TTS-stream sink and the batch send loop — see `_write_frames`."""
        await _write_frames(t, frames, sent, marked)

    # The gate is armed by `_write_frames` at the first frame that actually
    # reaches the carrier. Arming it here, before STT/LLM/TTS, made caller
    # speech during a silent pipeline look like a barge-in: the cancel event
    # was set and the answer was discarded before a single frame existed. On
    # the 2026-10-20 call that cancelled 5 of 7 turns. Under TTS_STREAM the
    # first chunk flows through `_send_chunks` mid-pipeline, so the gate is
    # still armed before any audible frame is sent.

    try:
        from app.voice_handler import TTS_STREAM, _turn_timeout_s

        bound = _turn_timeout_s()
        try:
            tts_chunks, dialogue, end_call = await _asyncio.wait_for(
                session.process_utterance(
                    chunk_sink=_send_chunks if TTS_STREAM else None),
                timeout=bound,
            )
        except _asyncio.TimeoutError:
            # A turn may not hold the line open forever. This is a backstop, not
            # a latency target: the live p95 for time-to-first-audio is 7.8 s
            # and the worst of 3,781 traced turns was 27.7 s, so the bound is
            # set well clear of every turn that is merely slow.
            #
            # It was added because "stuck" turned out to be reachable -- see
            # `TURN_TIMEOUT_S` in app/config.py for the wakeup defect that
            # stalled every turn on a transport without constant frame traffic.
            #
            # Order matters: ask the turn to stop first, so the pump thread
            # sees the cancel Event and stops decoding, and only then let
            # `wait_for` cancel the coroutine.
            session.request_interruption()
            session.log_event("TURN_TIMEOUT", timeout_s=bound)
            logger.error(f"{t.label}: turn exceeded {bound:.0f}s and was "
                         f"abandoned; caller gets the fixed response")
            # Fresh turn before the fallback is sent: the cancel signal just set
            # is the same one `_write_frames` checks, so without this the fixed
            # response would be refused by our own send loop and the caller
            # would hear the silence this bound exists to prevent.
            session.begin_turn()
            tts_chunks, dialogue, end_call = session.timeout_fallback(), "", False
    except WebSocketDisconnect:
        session.end_turn()
        raise                       # stream is dead — let the handler exit
    except Exception:
        logger.exception(f"{t.label}: pipeline failed")
        tts_chunks, dialogue, end_call = [], "", False

    try:
        # Save transcript for post-call lead extraction
        if dialogue:
            t.transcript_parts.append(dialogue)
            # Push to SSE for live dashboard
            if t.stream_sid and t.stream_sid in _active_call_sids:
                _active_call_sids[t.stream_sid]["transcript"].append(dialogue)
                _push_transcript_event("transcript", t.stream_sid, {
                    "dialogue": dialogue,
                })

            # ── CRM: link as soon as the caller's name is known ──
            # Spawned, never awaited: this is a live call, and an attempt runs an
            # LLM extraction. The linker declines once linked or out of tries, so
            # the cost is bounded and usually paid once. See app/crm/voice.py.
            if t.call_linker is not None and t.call_linker.can_try:
                from app.crm.tasks import spawn as _spawn_crm_task

                _spawn_crm_task(
                    t.call_linker.attempt(" ".join(t.transcript_parts)),
                    label=f"crm link for call {t.stream_sid}",
                )

        # The batch path sends everything here; a streamed turn already went out
        # through the sink, chunk by chunk, mid-pipeline.
        remaining = tts_chunks[sent["n"]:]
        session.log_event("AGENT_SPEECH_STARTED", chunks=len(remaining))
        if remaining and not session.cancel_requested():
            await _send_chunks(remaining)

        # Written is not heard. The turn -- and with it SPEAKING, the echo gate
        # and the cancel signal -- lives until the carrier has played the last
        # frame, not until the socket accepted it. This is also what makes the
        # greeting's window real: see `_await_playout`.
        unplayed = await _await_playout(session)

        if session.cancel_requested():
            await _clear_carrier_playback(t.websocket, t.stream_sid)
            if unplayed or sent["n"] < len(tts_chunks):
                # The caller heard a prefix: either frames are still queued in
                # the carrier (`unplayed`) or some were never written at all.
                # On the batch path generation has already finished and the
                # whole answer is in the history -- see the method's note.
                session.note_answer_truncated()
            # `reason` is the point: `AGENT_SPEECH_STOPPED` used to mean "handed
            # to the socket", which is not the same event as "the caller stopped
            # hearing us", and the difference is exactly what this story is.
            # `played_chunks` is that same difference as a number: writes minus
            # what was still queued when the caller cut in. Before the playout
            # wait it was always the full count, on every call, including the
            # ones the caller had talked over from the first word.
            session.log_event("AGENT_SPEECH_STOPPED", reason="interrupted",
                              played_chunks=max(0, sent["n"] - unplayed))
        else:
            session.log_event("AGENT_SPEECH_STOPPED", reason="complete",
                              played_chunks=sent["n"])
    finally:
        # Every exit path, or the gate would keep rejecting the caller's
        # ordinary speech as echo for the rest of the call.
        session.end_turn()

    # Deterministic hangup: caller sign-off detected → end call
    if end_call:
        logger.info(f"{t.label}: ending call after sign-off ({t.stream_sid})")
        t.ended["by_turn"] = True
        await _hangup_twilio_call(t.call_sid)
        try:
            await t.websocket.close()
        except Exception:
            pass


# ── the greeting is delivered once per call ──────────────────────────
#: Calls whose scripted greeting has already been spoken, keyed by the
#: carrier's CallSid, with the time it was claimed.
#:
#: The greeting is spawned from the `start` branch of the media-stream handler,
#: so anything that produces a second `start` for the same call produces a
#: second greeting. That is not hypothetical: the handler overwrites
#: `turn["task"]` without cancelling the first task, and a WebSocket reconnect
#: arrives as a brand new session with a new `streamSid` but the SAME CallSid.
#: Keyed by CallSid for exactly that reason -- a redial is a new CallSid and
#: SHOULD greet again; a reconnect of the same call must not.
#:
#: In-process, like `_active_call_sids` below, and correct for the same reason:
#: the stack runs one uvicorn worker by design (see `_reconcile_workers`), so
#: every leg of a call lands in this process. The TTL only bounds the dict; a
#: call cannot outlive it and still be greeted.
_GREETED_CALLS: dict[str, float] = {}
_GREETING_TTL_S = 3600.0


def _greeting_timeout_s() -> float:
    """Bound on synthesising the greeting.

    Shorter than a turn's bound because this is a fixed script through a single
    engine: there is no retrieval, no decode and no tool work that could
    legitimately take the time a turn may. A greeting that cannot be synthesised
    in this long is not going to be, and the caller should not sit in silence
    while we wait to find out.
    """
    from app.voice_handler import _turn_timeout_s

    return min(15.0, _turn_timeout_s())


def _claim_greeting(call_key: str) -> bool:
    """True the first time this call asks to greet; False on every repeat."""
    if not call_key:
        # No identity to dedupe on. Suppressing would risk silencing the
        # greeting on every call; greeting twice is the lesser failure.
        return True
    now = _time.time()
    for stale in [k for k, ts in _GREETED_CALLS.items() if now - ts > _GREETING_TTL_S]:
        _GREETED_CALLS.pop(stale, None)
    if call_key in _GREETED_CALLS:
        return False
    _GREETED_CALLS[call_key] = now
    return True


async def _run_greeting(t: _VoiceTurn, text: str) -> None:
    """Speak the opening greeting as agent speech, and yield if the caller talks.

    The greeting used to be written inline by the receive loop -- 389 chunks in
    3 ms on the 2026-09-29 inbound call -- with `mark_listening()` running the
    moment the last byte left. So the echo gate covered the writing and not the
    7.8 s the caller was hearing, nothing could stop the greeting once it had
    started, and the reader was blocked for its whole synthesis. As a task it is
    agent speech like any turn: SPEAKING for its playout window, the cancel
    signal live so a caller who talks over it takes the floor, and the reader
    free the entire time.

    Never called for a turn: a caller turn is `_run_voice_turn`. This one has no
    pipeline and no history -- it is a script, spoken under the same rules.

    Spoken at most ONCE per call. On a repeat attempt the script is not
    re-spoken, but it IS still written into this session's history: a reconnect
    builds a new session, and the model has to know the caller was already
    greeted or it opens the next turn by introducing itself again.
    """
    session = t.session
    sent = {"n": 0}

    if not _claim_greeting(t.call_sid or t.stream_sid):
        logger.info(f"{t.label}: greeting already delivered for "
                    f"{t.call_sid or t.stream_sid}; not re-speaking it")
        session.note_greeting_spoken(text)
        return

    try:
        from app.voice_handler import (
            generate_ulaw_greeting,
            greeting_asset_frames,
            greeting_cache_get,
            greeting_cache_key,
            greeting_cache_put,
            _use_sarvam_tts,
        )

        # US-014: the greeting is agent speech, so the echo gate is armed for it
        # exactly as for a turn, and -- as for a turn -- it stays armed until the
        # caller has heard the last frame rather than until the last write.
        session.begin_turn()
        session.mark_speaking()

        direction = (getattr(session, "direction", "") or "inbound").strip().lower()
        greeting_start = _time.perf_counter()

        def _note_first_audio(source: str, frames: list[bytes]) -> None:
            """One greppable line for the first call after a deploy/restart."""
            logger.info(
                "%s: GREETING_FIRST_AUDIO language=%s direction=%s "
                "source=%s frames=%d elapsed_ms=%.0f",
                t.label, session.language, direction, source, len(frames),
                (_time.perf_counter() - greeting_start) * 1000.0,
            )
        cache_key = greeting_cache_key(session.language, direction, text)
        cached = greeting_cache_get(cache_key)
        if cached:
            logger.info(
                f"{t.label}: greeting cache hit ({session.language}/"
                f"{direction}, {len(cached)} frames)"
            )
            session.note_greeting_spoken(text)
            _note_first_audio("cache", cached)
            await _write_frames(t, cached, sent)
        else:
            # A pre-rendered asset is the first-call path after a restart: the
            # file is already in the agent's voice, so no model or network is
            # touched. A missing/drifted asset falls through to live synthesis.
            asset = greeting_asset_frames(session.language, direction, text)
            if asset:
                logger.info(
                    f"{t.label}: greeting asset hit ({session.language}/"
                    f"{direction}, {len(asset)} frames)"
                )
                greeting_cache_put(cache_key, asset)
                session.note_greeting_spoken(text)
                _note_first_audio("asset", asset)
                await _write_frames(t, asset, sent)
            else:
                frames: list[bytes] = []
                noted = False
                first_audio = False

                async def _stream_greeting() -> None:
                    nonlocal noted, first_audio
                    # Neither provider needs the whole script before the first
                    # frame: Kokoro streams per sentence and Sarvam streams PCM
                    # as it is generated. The first sentence is what the caller
                    # hears while the rest is still being synthesised.
                    async for audio, sr in session.synthesise_stream(text):
                        if not noted:
                            # The greeting is agent speech, so the transcript
                            # of this call must contain it -- but only once
                            # audio actually exists; a failed stream must not
                            # teach the model it already introduced itself.
                            session.note_greeting_spoken(text)
                            noted = True
                        chunk_frames = session._chunk_to_ulaw_frames(audio, sr)
                        if chunk_frames and not first_audio:
                            first_audio = True
                            _note_first_audio("stream", chunk_frames)
                        frames.extend(chunk_frames)
                        await _write_frames(t, chunk_frames, sent)

                try:
                    await _asyncio.wait_for(
                        _stream_greeting(), timeout=_greeting_timeout_s()
                    )
                except Exception:
                    # The streaming path is shared with every turn, but the
                    # greeting must not lose its batch fallback on Kokoro. Only
                    # fall back when nothing has been spoken: repeating the
                    # start of the greeting would be worse than the delay.
                    if frames or _use_sarvam_tts(session.language):
                        raise
                    logger.exception(
                        "%s: streaming greeting failed before audio; "
                        "falling back to batch synthesis", t.label,
                    )
                    frames = await _asyncio.wait_for(
                        _asyncio.to_thread(
                            generate_ulaw_greeting, text,
                            language=session.language,
                        ),
                        timeout=_greeting_timeout_s(),
                    )
                    if frames:
                        session.note_greeting_spoken(text)
                        _note_first_audio("batch", frames)
                    await _write_frames(t, frames, sent)

                if frames:
                    greeting_cache_put(cache_key, frames)
                    logger.info(
                        f"{t.label}: greeting synthesised and cached "
                        f"({session.language}/{direction}, {len(frames)} frames)"
                    )

        await _await_playout(session)
        if session.cancel_requested():
            # The caller spoke over the greeting. Stop the carrier's buffer too,
            # or they keep hearing a script they have already talked across.
            await _clear_carrier_playback(t.websocket, t.stream_sid)
        logger.info("AI greeting sent via TTS")
    except WebSocketDisconnect:
        logger.info(f"{t.label}: stream ended during the greeting")
    except Exception:
        # Non-fatal, as before: a call with no greeting still runs.
        logger.exception("Failed to send AI greeting (non-fatal)")
    finally:
        # Every exit path, or the gate would keep rejecting the caller's
        # ordinary speech -- including the first thing the caller says.
        session.end_turn()


# ── WebSocket: Twilio Media Streams ──────────────────────────────────

@app.websocket("/ws/twilio")
async def websocket_twilio(websocket: WebSocket):
    """
    Twilio Media Streams WebSocket endpoint for INBOUND calls.

    Runs the full STT → RAG → LLM → TTS pipeline:
      1. Receive µ-law audio chunks from Twilio.
      2. Accumulate until the caller stops speaking (VAD).
      3. Transcribe with Whisper → query RAG + Qwen LLM.
      4. Synthesise answer with Kokoro TTS → stream back as µ-law.
    """
    from app.voice_handler import VoiceCallSession
    from app.voice_handler import FRAME_ECHO, FRAME_MUTED

    await websocket.accept()
    logger.info("WS /ws/twilio: inbound call connected (AI pipeline active)")

    stream_sid: str | None = None
    call_sid: str = ""
    transcript_parts: list[str] = []
    session = VoiceCallSession()  # direction defaults to "inbound"
    # Created when the stream starts, once the caller's number is known. None
    # until then, so a media frame arriving first cannot NameError.
    call_linker = None

    # US-014: the turn runs as its own task so this loop keeps reading the
    # socket. `transcript_parts` is handed over as the same list object, so the
    # post-call handler in the `finally` below sees everything the turn appended.
    call = _VoiceTurn(websocket=websocket, session=session,
                      label="WS /ws/twilio", transcript_parts=transcript_parts)
    turn: dict = {"task": None, "deferred": False}

    async def _run_turn() -> None:
        """Start one turn. Swallows everything: a task's exception has no reader."""
        call.stream_sid = stream_sid or ""
        call.call_sid = call_sid
        call.call_linker = call_linker
        try:
            await _run_voice_turn(call)
        except WebSocketDisconnect:
            logger.info("WS /ws/twilio: turn ended with the stream")
        except Exception:
            logger.exception("WS /ws/twilio: turn failed")

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            event = msg.get("event", "")

            if event == "connected":
                logger.info("WS /ws/twilio: connected")

            elif event == "start":
                start_payload = msg.get("start", {}) or {}
                stream_sid = msg.get("streamSid", start_payload.get("streamSid", ""))
                call_sid = msg.get("callSid", start_payload.get("callSid", ""))
                logger.info(f"WS /ws/twilio: stream started — {stream_sid} (call {call_sid})")

                # Track for SSE live-call monitor
                _active_call_sids[stream_sid] = {
                    "call_sid": call_sid or stream_sid,
                    "direction": "inbound",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "transcript": [],
                }

                # US-016: this is the moment a session becomes live, and the
                # only moment. The admission counter is fed from the real
                # lifecycle rather than from requests, so a refused call can
                # never inflate the count it was refused by.
                from app.admission import REGISTRY

                REGISTRY.register(stream_sid)
                session.call_id = stream_sid
                # Mint the conversation id now, while the call is starting. The CRM
                # needs it here — lookup-or-create is the only endpoint that returns
                # a userId, so this is the one chance to name the conversation.
                #
                # The caller's number comes through as a <Parameter> on <Stream>
                # (see _stream_markup): Media Streams does not carry it in the
                # start event, so the webhook is the only place it exists.
                caller_number = str(
                    (start_payload.get("customParameters") or {}).get("phone") or ""
                )
                # The requested answer language travels as a TwiML <Parameter>
                # (same channel as the phone number, which Media Streams does
                # not carry in the start event). Empty keeps DEFAULT_LANGUAGE.
                # `languageLock=1` means the caller pressed an IVR digit (or
                # the number pinned ?language=), so this is a choice and STT
                # auto-detection must not rewrite it mid-call.
                start_params = start_payload.get("customParameters") or {}
                output_language = normalize_language_code(
                    str(start_params.get("language") or "")
                )
                language_locked = str(
                    start_params.get("languageLock") or ""
                ).strip().lower() in ("1", "true", "yes")
                if output_language:
                    session.set_language(
                        output_language, "ivr" if language_locked else "default"
                    )
                logger.info(
                    "WS /ws/twilio: language=%s source=%s",
                    session.language, session.language_source,
                )
                from app.crm import session as crm_session

                inbound_session = crm_session.start(
                    "inbound_call", stream_sid, phone_number=caller_number,
                    language=session.language,
                )
                conversation_id = inbound_session.conversation_id
                logger.info(
                    f"WS /ws/twilio: caller number {'received' if caller_number else 'missing'}"
                )

                # The CRM link for this call. It cannot happen yet — linking needs
                # a name, and the caller has not said anything — so it is offered
                # each turn below until it lands or runs out of tries.
                from app.crm.voice import CallLinker

                call_linker = CallLinker(
                    channel="inbound_call",
                    conversation_id=conversation_id,
                    phone_number=caller_number,
                    session_key=stream_sid,
                )
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "inbound",
                    "conversation_id": conversation_id,
                })

                # Send an initial AI greeting via TTS. It runs as a task, like a
                # turn, so the reader keeps the socket: see `_run_greeting`.
                from app.voice_system_prompt import call_greeting

                greeting = call_greeting(session.language, "inbound")
                call.stream_sid = stream_sid or ""
                call.call_sid = call_sid or ""
                turn["task"] = _asyncio.create_task(_run_greeting(call, greeting))

            elif event == "media":
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    continue

                # ── US-014: the ONE inbound-frame decision (TRD-05) ──
                # This was four copy-pasted lines per handler, and its condition
                # -- `tts_playing` -- was True only while this loop was NOT
                # reading the socket, so neither copy could ever run. The
                # decision lives on the session now, where the audio buffer is.
                verdict = session.admit_inbound_frame(ulaw_bytes)
                if verdict == FRAME_ECHO:
                    continue            # our own playback coming back, rejected
                if verdict == FRAME_MUTED:
                    continue            # BARGE_IN_ENABLED=0: the documented discard
                # FRAME_INTERRUPTION needs nothing here. The turn task notices
                # the cancel itself -- it owns the sends, so it is also what
                # flushes the carrier's buffer -- and this frame falls through
                # to feed_audio, so the words the caller interrupted with are
                # the first words of their next turn.

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    task = turn["task"]
                    if task is not None and not task.done():
                        # Still speaking. feed_audio does NOT clear the buffer,
                        # so the utterance is intact and the next frame
                        # re-triggers this; it starts the moment the turn ends.
                        # In practice the gap is one frame, because the turn
                        # stops itself as soon as the cancel is set. Logged on
                        # the transition only: the condition stays true for every
                        # frame of the deferral.
                        if not turn["deferred"]:
                            turn["deferred"] = True
                            session.log_event("TURN_DEFERRED")
                    else:
                        turn["deferred"] = False
                        turn["task"] = _asyncio.create_task(_run_turn())

            elif event == "dtmf":
                dtmf_digit = msg.get("dtmf", {}).get("digit", "?")
                logger.info(f"WS /ws/twilio: DTMF digit '{dtmf_digit}' — ignored (pipeline handles in-band audio)")

            elif event == "stop":
                logger.info(f"WS /ws/twilio: stream stopped — {stream_sid}")
                if stream_sid and stream_sid in _active_call_sids:
                    _active_call_sids[stream_sid]["ended_at"] = datetime.now(timezone.utc).isoformat()
                    _push_transcript_event("call_ended", stream_sid)
                break

    except WebSocketDisconnect:
        logger.info("WS /ws/twilio: client disconnected")
        # Stream died while the call may still be live (caller hangup,
        # no-answer, or tunnel blip) — end the call via REST so Twilio
        # never plays the "We seem to have lost the connection" fallback
        # <Say> into the caller's ear. Unless the turn already ended it:
        # `_run_voice_turn` closes the socket after a sign-off hangup, so the
        # reader wakes here on a call that is already completed. Twilio answers
        # the repeat with a 404 and the log reports "Twilio hangup failed",
        # which reads like a defect and is not one.
        if not call.ended["by_turn"]:
            await _hangup_twilio_call(call_sid)
    except Exception:
        logger.exception("WS /ws/twilio: unexpected error")
        if not call.ended["by_turn"]:
            await _hangup_twilio_call(call_sid)
    finally:
        # The reader no longer owns the turn, so nothing else will stop it. At
        # teardown it is cancelled here -- its own `finally` still runs, which is
        # what leaves the session LISTENING for the post-call path below.
        task = turn["task"]
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (_asyncio.CancelledError, Exception):
                pass
        # Clear the active-call entry on ANY exit path — prevents phantom
        # "active calls" when Twilio's stop event never arrives.
        if stream_sid and stream_sid in _active_call_sids:
            entry = _active_call_sids.pop(stream_sid, None)
            if entry and "ended_at" not in entry:
                _push_transcript_event("call_ended", stream_sid)

            # US-016: the slot is freed here, on every exit path from the
            # handler. A refused caller dialling back finds a free slot and is
            # admitted as a fresh session -- no state from the refusal is
            # carried into it, because a refused call never had any.
            if stream_sid:
                from app.admission import REGISTRY

                REGISTRY.release(stream_sid)
        # Close the session and recover its id before the registry drops it —
        # the post-call handler needs it to write the conversation.
        from app.crm import session as crm_session

        ended = crm_session.end("inbound_call", stream_sid) if stream_sid else None
        await _handle_disconnect(
            transcript_parts,
            ended.conversation_id if ended else "",
            channel=ended.channel if ended else "inbound_call",
            # The carrier-supplied number, which the webhook threaded through
            # the TwiML: it is the real caller, where the post-call extraction
            # has only what the caller happened to say.
            phone_number=ended.phone_number if ended else "",
            # Set mid-call by the linker, if it managed to link.
            crm_user_id=(call_linker.crm_user_id if call_linker else "")
            or (ended.crm_user_id if ended else ""),
            language=getattr(ended, "language", "")
            or getattr(session, "language", ""),
        )
        try:
            await websocket.close()
        except Exception:
            pass


# ── WebSocket: Twilio Media Streams — outbound calls ─────────────────

@app.websocket("/ws/twilio-outbound")
async def websocket_twilio_outbound(websocket: WebSocket):
    """
    Twilio Media Streams WebSocket for OUTBOUND calls.

    Runs the full STT → RAG → LLM → TTS pipeline:
      1. Receive µ-law audio chunks from Twilio.
      2. Accumulate until the caller stops speaking (VAD).
      3. Transcribe with Whisper → query RAG + Qwen LLM.
      4. Synthesise answer with Kokoro TTS → stream back as µ-law.
    """
    from app.voice_handler import VoiceCallSession
    from app.voice_handler import FRAME_ECHO, FRAME_MUTED

    await websocket.accept()
    logger.info("WS /ws/twilio-outbound: outbound call connected (AI pipeline active)")

    stream_sid: str | None = None
    call_sid: str = ""
    transcript_parts: list[str] = []
    session = VoiceCallSession(direction="outbound")

    # US-014: as in the inbound handler, the turn is a task so this loop keeps
    # reading the socket. Same cause, same two defects -- see `_run_voice_turn`.
    call = _VoiceTurn(websocket=websocket, session=session,
                      label="WS /ws/twilio-outbound", transcript_parts=transcript_parts)
    turn: dict = {"task": None, "deferred": False}

    async def _run_turn() -> None:
        """Start one turn. Swallows everything: a task's exception has no reader."""
        call.stream_sid = stream_sid or ""
        call.call_sid = call_sid
        try:
            await _run_voice_turn(call)
        except WebSocketDisconnect:
            logger.info("WS /ws/twilio-outbound: turn ended with the stream")
        except Exception:
            logger.exception("WS /ws/twilio-outbound: turn failed")

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            event = msg.get("event", "")

            if event == "connected":
                logger.info("WS /ws/twilio-outbound: connected")

            elif event == "start":
                start_payload = msg.get("start", {}) or {}
                stream_sid = msg.get(
                    "streamSid", start_payload.get("streamSid", "")
                )
                call_sid = msg.get("callSid", start_payload.get("callSid", ""))
                logger.info(f"WS /ws/twilio-outbound: stream started — {stream_sid} (call {call_sid})")

                # Track for SSE live-call monitor
                _active_call_sids[stream_sid] = {
                    "call_sid": call_sid or stream_sid,
                    "direction": "outbound",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "transcript": [],
                }
                session.call_id = stream_sid
                # Mint the conversation id while the call is starting — see the
                # inbound handler. Unlike inbound, this call already knows who it
                # dialled: the worker puts the lead id and number on the webhook
                # URL, they arrive here as <Parameter>s, and the lead row gives
                # us the name and email. So outbound can link at call start.
                outbound_params = start_payload.get("customParameters") or {}
                outbound_lead_id = str(outbound_params.get("leadId") or "")
                outbound_phone = str(outbound_params.get("phone") or "")
                outbound_language = normalize_language_code(
                    str(outbound_params.get("language") or "")
                )
                # "operator" and "campaign" are explicit choices and lock the
                # language; "lead" is the person's stored preference and stays
                # open to strong auto-detection (the stricter switch gate still
                # rejects one-word flips). Older URLs carry no source: treat a
                # non-empty language as the lead's preference, which is what it
                # was before this parameter existed.
                outbound_language_source = str(
                    outbound_params.get("languageSource") or ""
                ).strip().lower() or ("lead" if outbound_language else "default")
                if outbound_language:
                    session.set_language(
                        outbound_language, outbound_language_source
                    )
                logger.info(
                    "WS /ws/twilio-outbound: language=%s source=%s",
                    session.language, session.language_source,
                )
                from app.crm import session as crm_session

                outbound_session = crm_session.start(
                    "outbound_call",
                    stream_sid,
                    phone_number=outbound_phone,
                    lead_id=outbound_lead_id,
                    language=session.language,
                )
                conversation_id = outbound_session.conversation_id
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "outbound",
                    "conversation_id": conversation_id,
                })
                logger.info(
                    f"WS /ws/twilio-outbound: lead id "
                    f"{'received' if outbound_lead_id else 'missing'}, caller number "
                    f"{'received' if outbound_phone else 'missing'}"
                )

                # ── CRM: link the person we dialled ──────────────────────
                # Spawned so the greeting is never delayed by a CRM call, and
                # because the whole dial already happened — nothing here is on
                # the answer path.
                if outbound_lead_id:
                    from app.crm.tasks import spawn as _spawn_crm_task

                    _spawn_crm_task(
                        _link_outbound_call(
                            lead_id=outbound_lead_id,
                            conversation_id=conversation_id,
                            phone_number=outbound_phone,
                            session_key=stream_sid,
                        ),
                        label=f"crm link for outbound call {stream_sid}",
                    )

                # Send an initial AI greeting via TTS — identify caller + reason
                # per the voice system prompt's outbound section. A task, for the
                # same reasons as inbound: see `_run_greeting`.
                from app.voice_system_prompt import call_greeting

                greeting = call_greeting(session.language, "outbound")
                call.stream_sid = stream_sid or ""
                call.call_sid = call_sid or ""
                turn["task"] = _asyncio.create_task(_run_greeting(call, greeting))

            elif event == "media":
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    continue

                # US-014: the ONE inbound-frame decision (TRD-05). This was the
                # second copy of a guard that could not execute -- see the
                # inbound handler for the full note.
                verdict = session.admit_inbound_frame(ulaw_bytes)
                if verdict == FRAME_ECHO:
                    continue            # our own playback coming back, rejected
                if verdict == FRAME_MUTED:
                    continue            # BARGE_IN_ENABLED=0: the documented discard

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    task = turn["task"]
                    if task is not None and not task.done():
                        if not turn["deferred"]:
                            turn["deferred"] = True
                            session.log_event("TURN_DEFERRED")
                    else:
                        turn["deferred"] = False
                        turn["task"] = _asyncio.create_task(_run_turn())

            elif event == "dtmf":
                dtmf_digit = msg.get("dtmf", {}).get("digit", "?")
                logger.info(f"WS /ws/twilio-outbound: DTMF digit '{dtmf_digit}' — ignored (pipeline handles in-band audio)")

            elif event == "stop":
                logger.info(
                    f"WS /ws/twilio-outbound: stream stopped — {stream_sid}"
                )
                if stream_sid and stream_sid in _active_call_sids:
                    _active_call_sids[stream_sid]["ended_at"] = datetime.now(timezone.utc).isoformat()
                    _push_transcript_event("call_ended", stream_sid)
                break

    except WebSocketDisconnect:
        logger.info("WS /ws/twilio-outbound: client disconnected")
        # Stream died while the call may still be live — end it via REST
        # so Twilio never plays the "lost connection" fallback <Say>.
        # Unless the turn already ended it (see the inbound handler).
        if not call.ended["by_turn"]:
            await _hangup_twilio_call(call_sid)
    except Exception:
        logger.exception("WS /ws/twilio-outbound: unexpected error")
        if not call.ended["by_turn"]:
            await _hangup_twilio_call(call_sid)
    finally:
        # The reader no longer owns the turn; at teardown it is stopped here.
        task = turn["task"]
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (_asyncio.CancelledError, Exception):
                pass
        # Clear the active-call entry on ANY exit path — prevents phantom
        # "active calls" when Twilio's stop event never arrives.
        if stream_sid and stream_sid in _active_call_sids:
            entry = _active_call_sids.pop(stream_sid, None)
            if entry and "ended_at" not in entry:
                _push_transcript_event("call_ended", stream_sid)
        from app.crm import session as crm_session

        ended = crm_session.end("outbound_call", stream_sid) if stream_sid else None
        await _handle_disconnect(
            transcript_parts,
            ended.conversation_id if ended else "",
            channel=ended.channel if ended else "outbound_call",
            # We dialled this number, so it is authoritative — not a guess.
            phone_number=ended.phone_number if ended else "",
            # Set at call start by the outbound linker, if it linked.
            crm_user_id=ended.crm_user_id if ended else "",
            language=getattr(ended, "language", "")
            or getattr(session, "language", ""),
        )


# ── HTTP: Outbound call voice TwiML (fetched by Twilio) ──────────────

@app.api_route("/twilio/outbound-voice", methods=["GET", "POST"])
async def twilio_outbound_voice_webhook(
    leadId: str = Query(""), phone: str = Query(""), language: str = Query(""),
    languageSource: str = Query(""),
):
    """
    Twilio fetches this URL when an outbound call is answered.
    Returns TwiML that connects to the Media Streams WebSocket.

    Accepts both GET and POST because Twilio may use either method
    depending on how the outbound call is initiated.

    `leadId`/`phone` are put on the URL by the outbound worker
    (app/outbound/caller.py) and passed straight through as `<Parameter>`s, so
    the media stream can tell who it is talking to and link them to the CRM.
    """
    host = _resolve_tunnel_host()

    from app.outbound.twiml import outbound_connect_twiml

    twiml = outbound_connect_twiml(
        host, {
            "leadId": leadId,
            "phone": phone,
            "language": language,
            # Without this the media stream cannot tell an operator's explicit
            # choice from the lead's stored preference, and would leave both
            # open to per-utterance drift.
            "languageSource": languageSource,
        }
    )
    logger.info(
        f"/twilio/outbound-voice: serving TwiML with host={host}, "
        f"lead={'set' if leadId else 'none'}, language={language or 'default'}, "
        f"source={languageSource or 'default'}"
    )
    return Response(content=twiml, media_type="application/xml")


# ── HTTP: Outbound call status callback ──────────────────────────────


@app.post("/twilio/outbound/status")
async def twilio_outbound_status_callback(
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
    CallDuration: str = Form(default="0"),
    To: str = Form(default=""),
    From: str = Form(default=""),
):
    """
    Twilio status callback for outbound calls.

    Twilio POSTs to this URL when an outbound call completes (or fails).
    We use it to update the call_queue entry and lead status.
    """
    logger.info(
        f"Outbound status: SID={CallSid}, status={CallStatus}, duration={CallDuration}s"
    )

    if not CallSid:
        return JSONResponse({"error": "missing CallSid"}, status_code=400)

    try:
        from app.leads.models import (
            get_call_queue_by_sid,
            get_lead_by_phone,
            update_call_queue_status,
            update_lead,
        )

        # 1. Update the call_queue entry by CallSid.
        # Only true terminal states may mark the entry failed — transient
        # progress states (ringing / answered) previously flipped the entry
        # to "failed" the moment the phone started ringing, which made the
        # CLI report failure and the worker place duplicate retry calls.
        if CallStatus == "completed":
            new_status, error = "completed", ""
        elif CallStatus in ("busy", "no-answer", "failed", "canceled"):
            new_status, error = "failed", f"Twilio status: {CallStatus}"
        elif CallStatus in ("answered", "in-progress"):
            new_status, error = "in-progress", ""
        elif CallStatus == "ringing":
            new_status, error = "ringing", ""
        else:
            # "initiated" and unknowns — leave the entry as the worker set it
            new_status, error = None, None

        queue_entry = await get_call_queue_by_sid(CallSid)
        if queue_entry:
            if new_status is not None:
                await update_call_queue_status(
                    queue_entry["id"], new_status, error_message=error
                )
            logger.info(f"Call queue entry {queue_entry['id']} updated to {CallStatus}")

        # 2. Update the lead status
        if CallStatus in ("completed", "no-answer", "busy", "failed", "canceled"):
            phone = To or ""
            if phone:
                lead = await get_lead_by_phone(phone)
                if lead:
                    lead_id = lead["id"]
                    if CallStatus == "completed":
                        await update_lead(lead_id, status="completed")
                        logger.info(f"Lead {lead_id} marked completed (outbound call)")
                    elif CallStatus == "canceled":
                        await update_lead(lead_id, status="pending")
                        logger.info(f"Lead {lead_id} call canceled — kept pending")
                    elif CallStatus in ("no-answer", "busy", "failed"):
                        attempts = lead.get("call_attempts", 0)
                        new_attempts = attempts + 1
                        from app.config import settings

                        if new_attempts < settings.MAX_CALL_ATTEMPTS:
                            await update_lead(
                                lead_id, status="pending", call_attempts=new_attempts
                            )
                            from app.leads.models import add_to_call_queue

                            await add_to_call_queue(lead_id=lead_id)
                            logger.info(
                                f"Lead {lead_id} re-queued (attempt {new_attempts}/{settings.MAX_CALL_ATTEMPTS})"
                            )
                        else:
                            await update_lead(lead_id, status="failed", call_attempts=new_attempts)
                            logger.info(f"Lead {lead_id} marked failed (max attempts)")

    except Exception:
        logger.exception("Failed to process outbound status callback")

    return JSONResponse({"status": "ok"})


# ── HTTP: TwiML voice webhook ────────────────────────────────────────

@app.get("/twilio/voice")
async def twilio_voice_webhook(From: str = Query(""), language: str = Query("")):
    """
    Twilio voice webhook — serves IVR menu first.
    After the caller presses a digit (or timeout), connects to /ws/twilio.

    `From` is the caller's number. These routes are GETs (Twilio is configured
    that way), so it arrives as a query parameter and must be declared as one —
    `Form` would silently never bind. It is threaded into the TwiML as a
    `<Parameter>` because the media stream itself never carries it.
    """
    from app.admission import REGISTRY, busy_asset, enabled as admission_enabled

    host = _resolve_tunnel_host()
    refusal_language = normalize_language_code(
        language or settings.DEFAULT_LANGUAGE
    )

    # US-016: the admission decision, taken from the live session count at the
    # carrier-facing endpoint (TAC-1). Refusing here rather than at the media
    # stream means a caller with no capacity to run in is not first walked
    # through the IVR -- they hear the busy message immediately.
    if admission_enabled():
        d = REGISTRY.decide(call_sid=From or "")
        if not d.admitted:
            REGISTRY.note_asset_played(
                busy_asset(refusal_language), call_sid=From or ""
            )
            logger.info("/twilio/voice: REFUSED (outcome=refused, live=%d, limit=%d)",
                        d.live, d.limit)
            return Response(
                content=_busy_twiml(host, refusal_language),
                media_type="application/xml",
            )

    ivr_template = (
        TWIML_IVR_TEMPLATE_FOREIGN
        if configured_foreign_languages()
        else TWIML_IVR_TEMPLATE
    )
    twiml = ivr_template.format(
        host=host,
        stream=_stream_markup(
            host, From, language or settings.DEFAULT_LANGUAGE,
            locked=bool(normalize_language_code(language)),
        ),
        # A dedicated Twilio number can pin the language with ?language=hi.
        # Digits 1/2/3 select per call; 9 opens the foreign menu; an unknown
        # digit keeps the default.
        action=(
            f"/twilio/voice/connect?language={quote(language)}"
            if language else "/twilio/voice/connect"
        ),
    )
    logger.info(f"/twilio/voice: serving IVR menu with host={host}, caller={'set' if From else 'unknown'}")
    return Response(content=twiml, media_type="application/xml")


@app.get("/twilio/voice/connect")
async def twilio_voice_connect(Digits: str = "", From: str = Query(""),
                               language: str = Query(""),
                               stage: str = Query("")):
    """
    Called by Twilio after IVR <Gather> completes.
    Connects the caller to the AI WebSocket stream.

    Twilio re-sends the original request's parameters on the `<Gather>` action,
    so `From` is still present here — which is why it is declared again rather
    than carried in a session.
    """
    from app.admission import REGISTRY, busy_asset, enabled as admission_enabled

    host = _resolve_tunnel_host()

    # Resolve the chosen language before the admission check: a refused caller
    # should hear the busy message in the language they are about to use. The
    # decision itself is unchanged.
    chosen_language = _language_for_call(
        Digits, language or settings.DEFAULT_LANGUAGE
    )

    # US-016: the binding decision. This endpoint is where <Connect><Stream>
    # would go out, so it is the last point at which a call can be refused
    # before a session exists. The check at /twilio/voice spares a caller the
    # IVR; this one is what actually guarantees no session is created, because
    # a line can fill while a caller is still pressing a digit.
    if admission_enabled():
        d = REGISTRY.decide(call_sid=From or "")
        if not d.admitted:
            REGISTRY.note_asset_played(
                busy_asset(chosen_language), call_sid=From or ""
            )
            logger.info("/twilio/voice/connect: REFUSED (outcome=refused, live=%d, limit=%d)",
                        d.live, d.limit)
            return Response(
                content=_busy_twiml(host, chosen_language),
                media_type="application/xml",
            )

    # ── Foreign-language second level ───────────────────────────────
    # Digits 1-4 on the first menu keep their exact old meaning. Only 9 opens
    # the new menu, and only while the feature is enabled; with
    # FOREIGN_LANGUAGES empty this whole block is dead.
    foreign = configured_foreign_languages()
    if foreign and stage != "foreign" and (Digits or "").strip() == "9":
        menu = TWIML_FOREIGN_IVR_TEMPLATE.format(
            stream=_stream_markup(
                host, From, chosen_language,
                locked=bool(normalize_language_code(language)),
            ),
            action=_foreign_menu_action(normalize_language_code(language)),
        )
        logger.info(
            "/twilio/voice/connect: foreign menu (pinned language=%s)",
            language or "none",
        )
        return Response(content=menu, media_type="application/xml")

    if foreign and stage == "foreign":
        chosen_language = _FOREIGN_LANGUAGE_BY_DIGIT.get(
            (Digits or "").strip(), chosen_language
        )

    # Any digit, or a pinned ?language=, is an explicit selection: the answer
    # language is locked against per-utterance drift. No digit and no pin is
    # the default, where auto-detection may still switch on strong evidence.
    explicit_language = bool((Digits or "").strip()) or bool(
        normalize_language_code(language)
    )

    logger.info(
        f"/twilio/voice/connect: digit={Digits}, language={chosen_language}, "
        f"locked={explicit_language}, host={host}, "
        f"caller={'set' if From else 'unknown'}"
    )
    twiml = TWIML_TEMPLATE.format(
        host=host,
        stream=_stream_markup(
            host, From, chosen_language, locked=explicit_language
        ),
    )
    return Response(content=twiml, media_type="application/xml")


# ── Shared STT model (voice pipeline warmup) ───────────────────────────

# Reuse the shared STT model from voice_handler (faster-whisper on CUDA)


def _get_stt_model():
    """Reuse the shared faster-whisper model from voice_handler."""
    from app.voice_handler import _get_stt_model as _vh_stt_model
    return _vh_stt_model()


# ── WhatsApp conversation logger ──────────────────────────────────────

async def _log_whatsapp_conversation(
    phone_number: str,
    transcript: str,
    conversation_id: str = "",
    crm_user_id: str = "",
    language: str = "",
):
    """
    Log a WhatsApp interaction to the new leads + conversations tables.

    ``conversation_id`` groups the many rows a WhatsApp session writes (one per
    message) under a single logical conversation, and ``crm_user_id`` records
    which Salesforce user it belongs to.

    Safe to call as a background task — failures are logged but never
    propagated, so they won't affect the Twilio response.
    """
    try:
        from app.leads.service import log_interaction

        await log_interaction(
            phone_number=phone_number,
            channel="whatsapp",
            transcript=transcript,
            conversation_id=conversation_id,
            crm_user_id=crm_user_id,
            language=language,
        )
    except Exception:
        logger.exception("Failed to log WhatsApp conversation (non-fatal)")


# ── HTTP: Twilio WhatsApp webhook ─────────────────────────────────────

WHATSAPP_TWIML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{answer}</Message>
</Response>"""

# ── WhatsApp helper: document upload from students ─────────────────────

async def _handle_whatsapp_document(
    media_url: str,
    content_type: str,
    from_number: str,
    body_text: str = "",
):
    """
    Download a document sent via WhatsApp, save to data/documents/,
    record in lead_documents table, and nudge the student to type
    'done' when everything is uploaded — the webhook generates the
    offer letter at that point, never per upload.
    """
    import uuid
    import urllib.request
    from pathlib import Path

    try:
        # Get or create lead
        from app.leads.models import get_lead_by_phone, upsert_lead_by_phone
        lead = await get_lead_by_phone(from_number)
        if not lead:
            lead = await upsert_lead_by_phone(phone_number=from_number, source="whatsapp")
        if not lead:
            logger.warning("_handle_whatsapp_document: could not create lead")
            return
        lead_id = lead["id"]

        # Determine file extension
        ext_map = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/jpg": ".jpg",
            "application/pdf": ".pdf",
            "image/webp": ".webp",
        }
        ext = ".bin"
        for mime, e in ext_map.items():
            if mime in content_type.lower():
                ext = e
                break

        # Download from Twilio
        logger.info(f"Downloading WhatsApp document: {media_url[:80]}...")
        req = urllib.request.Request(media_url)
        from app.config import settings
        import base64
        auth_str = base64.b64encode(
            f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {auth_str}")

        file_bytes = await _asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=30).read()
        )
        logger.info(f"Downloaded {len(file_bytes)} bytes of document")

        # Save to disk
        data_dir = Path(settings.DATA_DIR)
        lead_dir = data_dir / "documents" / lead_id
        lead_dir.mkdir(parents=True, exist_ok=True)

        short_id = str(uuid.uuid4())[:8]
        safe_name = f"whatsapp_{short_id}{ext}"
        stored_path = lead_dir / safe_name
        stored_path.write_bytes(file_bytes)

        # Map content_type to doc_type
        doc_type = "other"
        if "pdf" in content_type.lower():
            doc_type = "transcript"
        elif "image" in content_type.lower():
            doc_type = "id_proof"

        # Record in DB
        from app.offers.models import add_document
        doc = await add_document(
            lead_id=lead_id,
            filename=safe_name,
            stored_path=str(stored_path),
            doc_type=doc_type,
            mime_type=content_type,
            size_bytes=len(file_bytes),
        )
        if doc and doc.get("id"):
            # Hand the file to the CRM too — the admissions team needs the
            # transcript, not only the offer letter it justified.
            try:
                from app.crm.documents import schedule_document_upload

                schedule_document_upload(
                    lead_id=lead_id, doc_id=str(doc["id"]), file_path=str(stored_path),
                    doc_type=doc_type, original_name=safe_name,
                )
            except Exception:
                logger.exception("Could not schedule the CRM upload for a document")

        if doc and lead.get("program_interest", "").strip():
            # Offer letters are generated only when the student types "done"
            # (see twilio_whatsapp_webhook) — never auto-triggered per upload.
            logger.info(
                f"WhatsApp document saved for {lead_id} — awaiting 'done' to generate offer"
            )
        elif doc:
            # Program not set — proactively ask what the student wants
            from app.offers.service import evaluate_offer_readiness, missing_fields_text
            readiness = await evaluate_offer_readiness(lead_id)
            logger.info(
                f"WhatsApp document saved but offer not ready — "
                f"missing: {readiness['missing']}"
            )
            try:
                from app.messaging import send_whatsapp_message
                send_whatsapp_message(
                    _wa(from_number),
                    _wa(_whatsapp_from()),
                    (
                        "I received your document! But before I can generate "
                        "your offer letter, I still need: "
                        f"{missing_fields_text(readiness['missing'])}."
                    ),
                )
            except Exception:
                logger.exception("Failed to send WhatsApp readiness nudge")
        else:
            logger.error("Failed to record WhatsApp document in DB")

    except Exception:
        logger.exception("_handle_whatsapp_document failed")


# ── Meridian program-name capture ──────────────────────────────────
# The detector lives in app.programs because the Streamlit UI needs the same
# one — its own copy listed four programs Meridian does not run. Imported under
# the local names this module has always used.

from app.programs import (  # noqa: E402  (kept beside its only former use)
    detect_program as _detect_meridian_program,
    is_explicit_program_choice as _is_explicit_program_choice,
)


# Words that end a name capture. "my name is not Guruji check the start of the
# chat" must yield no name at all — the greedy capture would otherwise take
# "not Guruji check" as the student's new name.
_NAME_STOPWORDS = frozenset({
    "not", "no", "wrong", "incorrect", "isnt", "isn't", "dont", "don't",
    "check", "please", "pls", "thanks", "thank", "you", "your", "my", "the",
    "a", "an", "and", "but", "for", "here", "now", "name", "system",
    "systems", "record", "profile", "correct", "change", "update", "fix",
    "is", "was", "in", "on", "at", "to", "of",
    # "call me tomorrow" / "call me back" are requests, not names.
    "tomorrow", "today", "later", "back", "again", "afterwards", "soon",
})


def _clean_name_candidate(raw: str) -> str:
    """
    Reduce a free-text name capture to a plausible name, or ''.

    Stops at the first stopword so a trailing clause is dropped instead of
    being absorbed into the name, and rejects anything too long to be a name.
    """
    words: list[str] = []
    for chunk in raw.split():
        word = chunk.strip(".,;:!?'\"()[]-")
        if not word or word.lower() in _NAME_STOPWORDS:
            break
        words.append(word)
        if len(words) == 3:
            break
    candidate = " ".join(words).strip()
    if not candidate or len(candidate) > 40 or not candidate[0].isalpha():
        return ""
    return candidate


async def _apply_program_choice(lead_id: str, current: str, detected: str) -> str:
    """
    Persist a newly detected program and return the value to use from here on.

    The program is deliberately *not* write-once any more. It always was the
    lead's stored value that won — `if detected_prog and not lead_program` —
    so a student who asked about M.Tech after any earlier mention of MBA was
    told their admission was for MBA, and the offer letter that followed said
    MBA too. A later explicit choice now supersedes an earlier one.
    """
    if not detected or detected == current:
        return current or detected
    if lead_id:
        # Imported here for the same reason the webhook imports it locally:
        # this module is imported by the voice path too, and the lead models
        # pull in a database connection at call time.
        from app.leads.models import update_lead

        await update_lead(lead_id, program_interest=detected)
    if current:
        logger.info(f"Program switched: {current!r} -> {detected!r} (lead {lead_id or 'unknown'})")
    return detected


# Words that mark a message as a knowledge question even when it's short
# and has no "?" — e.g. "fees", "hostel". Kept narrow so admission intent
# ("I want to take admission") is never misrouted to RAG.
_KB_QUESTION_KEYWORDS = (
    "fees", "tuition", "fee structure", "scholarship", "courses",
    "programs", "hostel", "placement", "eligibility", "duration",
    "deadline", "entrance", "intake", "how much", "refund",
)


async def _whatsapp_recent_turns(lead_id: str, limit: int = 6) -> list[str]:
    """
    The last few User/Assistant turns for this lead, oldest first.

    WhatsApp has no session object, so the transcript rows are the only record
    of what was just said. Both callers of this need the same thing for
    different reasons: the RAG leg feeds it to the model so an answer can
    follow the conversation, and the state machine reads the *previous
    assistant turn* to decide what "yes" is actually answering.
    """
    if not lead_id:
        return []
    from app.leads.models import get_conversations

    try:
        rows = await get_conversations(lead_id=lead_id, channel="whatsapp", limit=limit)
    except Exception:
        logger.exception("WhatsApp history load failed (non-fatal)")
        return []

    turns: list[str] = []
    for row in reversed(rows or []):
        transcript = (row.get("transcript") or "").strip()
        if transcript:
            turns.append(transcript)
    return turns


def _last_assistant_turn(turns: list[str]) -> str:
    """
    Text of the most recent assistant reply, or ''.

    Uses rfind rather than a line scan because a logged answer can be
    multi-line — only the first line carries the "Assistant:" prefix.
    """
    for turn in reversed(turns):
        marker = turn.rfind("Assistant:")
        if marker != -1:
            text = turn[marker + len("Assistant:"):].strip()
            if text:
                return text
    return ""


async def _whatsapp_chat_rag(
    question: str,
    *,
    history: list[str] | None = None,
    profile: dict | None = None,
    program: str = "",
    language: str | None = None,
) -> str:
    """
    RAG answer for WhatsApp text chat using the chat-oriented prompt
    (mode="chat" — same Markdown SYSTEM_PROMPT as the Streamlit chat).

    `history` and `profile` are what make the chat leg stateless no longer:
    the model sees the recent turns and the fields already on the lead, so a
    follow-up like "I can't afford it" stays on the program under discussion
    and "what's my name?" can be answered from the record instead of being
    refused.

    `program` also seeds the *retrieval* query. Retrieval deliberately embeds
    the student's own utterance (the N1 fix — the instruction boilerplate used
    to dominate the vector), and a bare "I can't afford it $14600" carries no
    program signal at all, which is how a fee question about M.Tech came back
    with B.Tech IT's numbers.
    """
    from app.pipeline import run_rag_query_sync

    retrieval_query = question
    if program and not _detect_meridian_program(question.lower()):
        retrieval_query = f"{question} {program}"

    try:
        answer = await _asyncio.to_thread(
            run_rag_query_sync,
            question,
            mode="chat",
            retrieval_query=retrieval_query,
            history=history,
            profile=profile,
            language=language,
        )
        if answer:
            return answer
    except Exception:
        logger.exception("WhatsApp RAG failed")
    return "Sorry, I couldn't process your question. Please try again."


async def _whatsapp_offer_on_done(
    lead_id: str, lead_name: str, conversation_id: str = ""
) -> str:
    """
    Generate + send the offer letter when the student signals that all
    documents are uploaded ("done").

    Checks readiness, requires at least one uploaded document, then calls
    generate_and_send_offer — which is itself idempotent (24h guard), so
    a repeated "done" does not re-send a second offer letter.
    """
    from app.offers.models import list_documents
    from app.offers.service import (
        evaluate_offer_readiness,
        generate_and_send_offer,
        missing_fields_text,
    )

    try:
        readiness = await evaluate_offer_readiness(lead_id)
        if readiness["missing"]:
            return (
                "Before I can generate your offer letter, I still need: "
                f"{missing_fields_text(readiness['missing'])}."
            )

        docs = await list_documents(lead_id)
        if not docs:
            return (
                "I don't see any documents from you yet. Please send clear photos "
                "or PDFs of your transcript/marksheet and ID proof, then type 'done'."
            )

        offer = await generate_and_send_offer(
            lead_id, channel="whatsapp", conversation_id=conversation_id
        )
        if offer:
            program = offer.get("program") or ""
            return (
                f"🎓 Great news, {lead_name}! Your offer letter for {program} is ready — "
                "I've sent it here on WhatsApp and emailed you a copy. "
                "Reply 'accept' or 'decline' when you're ready."
            )
        return "Hmm, I couldn't generate your offer letter right now. Please try again in a moment."
    except Exception:
        logger.exception("WhatsApp offer generation on 'done' failed")
        return "Hmm, I couldn't generate your offer letter right now. Please try again in a moment."


def _strip_markdown(text: str) -> str:
    """
    Convert LLM Markdown to plain text for WhatsApp (Twilio renders raw text).

    Handles bold/italic/strikethrough, headers, horizontal rules,
    blockquotes, bullets, links, inline code, and Markdown table framing.
    Safe on already-plain text.
    """
    import re

    # Links: [text](url) -> text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold / italic / strikethrough
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"_{2}([^_]+)_{2}", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    # Headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^\s*([-*_])\s*(?:\1\s*){2,}$", "", text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    # Bullets -> "• "
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    # Markdown table framing: drop the |---| separator row, then side pipes
    text = re.sub(
        r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", "",
        text, flags=re.MULTILINE,
    )
    text = re.sub(r"^\s*\|", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|\s*$", "", text, flags=re.MULTILINE)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _detect_admission_intent_whatsapp(msg_lower: str) -> tuple[bool, str]:
    """
    Detect if a WhatsApp message expresses intent to proceed with admission.

    Returns (is_interested: bool, program: str).
    Program is extracted from the message if mentioned alongside admission intent.

    Uses keyword fast-path first (free), then falls back to LLM for
    semantic matching.  Catches all variations like:
    "i am ready to take admission", "let's go ahead", "sign me up",
    "yes apply now", "proceed with enrollment", etc.
    """
    # ── Extract program from message ───────────────────────────────
    detected_program = _detect_meridian_program(msg_lower)

    # ── Fast path: strong admission keywords ────────────────────────
    # Misspellings are listed explicitly because they are the common case in
    # the wild, and a miss sends the turn to the knowledge base instead of the
    # admission flow: "I would like to take admision on M.Tech" was answered
    # with eligibility prose and never registered the program at all.
    strong = [
        "i want to take admission",
        "i want admission",
        "take the admission",
        "take admission",
        "want to take admission",
        "take addmission",   # common typo
        "take admision",     # common typo
        "take admissoin",    # common typo
        "take admisson",     # common typo
        "i want to take admision",
        "i want to take addmission",
        "i want to enroll",
        "ready to enroll",
        "sign me up",
    ]
    for kw in strong:
        if kw in msg_lower:
            logger.info(f"Admission intent: strong keyword '{kw}'")
            return True, detected_program

    # ── Medium path: weaker keywords — confirm with LLM ────────────
    medium = [
        "admission", "admision", "addmission", "admisson", "enroll", "apply",
        "i am ready", "let's proceed", "go ahead", "i'm interested",
        "join the program", "confirm my", "i want to study",
        "proceed with", "i'd like to apply",
    ]
    has_medium = any(kw in msg_lower for kw in medium)
    if not has_medium:
        return False, ""

    # LLM confirmation
    try:
        from app.llm_backend import chat as backend_chat, default_model, small_task_num_ctx

        # US-017 C4 / Class A: `backend_chat` is a SYNCHRONOUS HTTP call to the
        # engine. Called directly from this async handler it blocks the event
        # loop for the whole inference -- so a WhatsApp message arriving while
        # two calls are live would freeze both of them for the duration. The
        # third instance of this defect found by the AST scan, and the one with
        # the least excuse: it is on a path that only runs when a caller is
        # also on the line.
        raw = (await _asyncio.to_thread(
            backend_chat,
            messages=[{
                "role": "user",
                "content": (
                    "You are an intent classifier for a university admissions chatbot.\n"
                    "Determine if the user's message expresses that they are READY to "
                    "proceed with admission, want to enroll, want to apply, or want to "
                    "take a program.\n\n"
                    "This includes phrases like: 'i am ready to take admission', "
                    "'let's go ahead', 'i want to join', 'sign me up', 'yes apply now', "
                    "'proceed with enrollment', 'i'd like to study here', 'confirm my seat', "
                    "'let's do it', 'i'm interested in joining', etc.\n\n"
                    "Answer ONLY 'yes' or 'no'.\n\n"
                    f"User message:\n{msg_lower[-800:]}"
                ),
            }],
            preferred=default_model(["qwen2.5:7b-instruct-q3_K_M", "qwen2.5:7b"]),
            num_ctx=small_task_num_ctx(),
        )).strip().lower()
        if raw.startswith("yes"):
            logger.info("Admission intent: LLM confirmed")
            return True, detected_program
    except Exception:
        logger.warning("Admission intent LLM check failed — skipping")
    return False, ""


async def _handle_offer_response(lead_id: str, status: str) -> dict | None:
    """
    Handle ACCEPT/DECLINE reply to an offer letter from WhatsApp.

    Finds the most recent sent offer for this lead and updates its status.
    """
    try:
        from app.offers.models import get_recent_offer_for_lead, update_offer_letter_status
        offer = await get_recent_offer_for_lead(lead_id, within_hours=720)  # 30 days
        if offer and offer.get("status") == "sent":
            result = await update_offer_letter_status(offer["id"], status)
            logger.info(f"Offer {offer['id']}: student replied '{status}' via WhatsApp")
            await _publish_offer_response(lead_id, status)
            return result
        return None
    except Exception:
        logger.exception("_handle_offer_response failed")
        return None


async def _publish_offer_response(lead_id: str, status: str) -> None:
    """
    Tell the CRM what the student answered. Never raises.

    The status keyword here is the app's own ("accepted"/"rejected"); the CRM
    wants three different fields moved together (see ``push_offer_response``).
    A lead with no CRM link is skipped — there is nothing to address.
    """
    try:
        from app.leads.models import get_lead_crm_user_id

        crm_user_id = await get_lead_crm_user_id(lead_id)
        if not crm_user_id:
            logger.info(f"Offer reply '{status}': lead {lead_id} has no CRM link — not published")
            return

        from app.crm.status import push_offer_response

        await push_offer_response(crm_user_id, status)
    except Exception:
        logger.exception("Could not publish the offer response to the CRM")


@app.post("/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    background_tasks: BackgroundTasks,
    Body: str = Form(default=""),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    From: str = Form(default=""),
    To: str = Form(default=""),
    WaId: str = Form(default=""),
    Language: str = Form(default=""),
):
    """
    Twilio WhatsApp webhook — receives incoming text, image, or document messages.

    - Voice (audio/*): unsupported — politely asked to type instead
    - Document (image/*, application/pdf): save as lead document
      (offer letter generated only when the student types 'done')
    - Text: state machine (name → email → program → admission intent → RAG chat)

    Configure this URL in Twilio Console:
        https://<your-tunnel>/twilio/whatsapp
    """

    # ── Resolve the conversation this message belongs to ─────────
    # WhatsApp has no session object and no end event — every message is an
    # independent webhook — so "the same conversation" is defined as messages
    # from this number inside the idle window (CRM_IDLE_WINDOW_HOURS; plan
    # decision D4). Resolved before the media branches so a document message
    # keeps the same session alive too. Failure is never fatal: the webhook
    # must answer Twilio within 15s regardless.
    conversation_id = ""
    wa_session = None
    try:
        from app.config import settings
        from app.crm import session as crm_session

        wa_session = await crm_session.resolve_whatsapp_session(
            From, idle_window_hours=settings.CRM_IDLE_WINDOW_HOURS
        )
        conversation_id = wa_session.conversation_id
    except Exception:
        logger.exception("WhatsApp conversation session resolution failed (non-fatal)")

    # ── Handle media messages ────────────────────────────────────
    num_media = int(NumMedia or "0")
    if num_media > 0 and MediaUrl0.strip():
        content_type = (MediaContentType0 or "").lower()

        if "audio" in content_type:
            # Voice notes are intentionally unsupported — WhatsApp is chat-only.
            logger.info(f"WhatsApp voice note from {From} — asking to type instead")
            twiml = WHATSAPP_TWIML_TEMPLATE.format(
                answer="I can't listen to voice notes. Please type your question here in the chat and I'll answer right away!"
            )
            return Response(content=twiml, media_type="application/xml")

        elif "image" in content_type or "pdf" in content_type or "document" in content_type:
            # Document upload from student — save + trigger offer letter
            logger.info(f"WhatsApp document from {From} ({MediaContentType0})")
            background_tasks.add_task(
                _handle_whatsapp_document,
                media_url=MediaUrl0,
                content_type=MediaContentType0,
                from_number=From,
                body_text=Body.strip(),
            )
            twiml = WHATSAPP_TWIML_TEMPLATE.format(
                answer="📄 Got your document! I'm processing it now. If you have more documents, send them too, or type 'done' when you're finished."
            )
            return Response(content=twiml, media_type="application/xml")

        else:
            # Unknown media type
            logger.info(f"WhatsApp unknown media from {From} ({MediaContentType0})")
            twiml = WHATSAPP_TWIML_TEMPLATE.format(
                answer="I received your file but I can only accept photos (transcripts, IDs) and PDFs. Please try sending it as an image or PDF."
            )
            return Response(content=twiml, media_type="application/xml")

    # ── Handle text ─────────────────────────────────────────────
    if not Body.strip():
        twiml = WHATSAPP_TWIML_TEMPLATE.format(
            answer="Hello! Send me a question about Meridian admissions."
        )
        return Response(content=twiml, media_type="application/xml")

    logger.info(f"WhatsApp from {From} (WaId={WaId}): {Body[:100]}")

    # ── In-chat language command ─────────────────────────────────
    # A whole-message command ("Spanish", "/es") sets this thread's answer
    # language and is confirmed without calling the LLM. It runs before lead
    # lookup and name capture so "Italian" can never be stored as a name.
    _thread_language = normalize_language_code(
        getattr(wa_session, "language", "") if wa_session else ""
    )
    _command = language_from_command(Body, _thread_language)
    if _command:
        if wa_session is not None:
            wa_session.language = _command
        from app.localized_text import WHATSAPP_LANGUAGE_CONFIRMATIONS

        answer = WHATSAPP_LANGUAGE_CONFIRMATIONS.get(
            _command, WHATSAPP_LANGUAGE_CONFIRMATIONS["en"]
        )
        background_tasks.add_task(
            _log_whatsapp_conversation,
            phone_number=From,
            transcript=f"User: {Body}\nAssistant: {answer}",
            conversation_id=conversation_id,
            language=_command,
        )
        twiml = WHATSAPP_TWIML_TEMPLATE.format(answer=escape(answer))
        return Response(content=twiml, media_type="application/xml")

    if Body.strip().lower() in ("/language", "/languages"):
        from app.localized_text import WHATSAPP_LANGUAGE_HELP

        twiml = WHATSAPP_TWIML_TEMPLATE.format(
            answer=escape(WHATSAPP_LANGUAGE_HELP)
        )
        return Response(content=twiml, media_type="application/xml")

    # The persisted thread language wins; the Twilio form field (normally
    # absent) is the next fallback, then the deployment default.
    _answer_language = resolve_answer_language(
        _thread_language,
        Language if isinstance(Language, str) else "",
        settings.DEFAULT_LANGUAGE,
    )

    # ── Lead info collection — check if lead has name/email ─────
    from app.leads.models import get_lead_by_phone, upsert_lead_by_phone, update_lead
    lead = await get_lead_by_phone(From)
    if not lead:
        lead = await upsert_lead_by_phone(phone_number=From, source="whatsapp")

    lead_id = (lead or {}).get("id", "")
    lead_name = (lead or {}).get("name", "")
    lead_email = (lead or {}).get("email", "")
    lead_program = (lead or {}).get("program_interest", "")

    # Detect if message looks like providing info
    msg_lower = Body.strip().lower()
    has_email = "@" in Body and "." in Body.split("@")[-1] if "@" in Body else False
    is_name_like = len(Body.split()) <= 3 and not has_email and "?" not in Body and len(Body) < 60

    # ── Conversation memory ──────────────────────────────────────
    # Loaded once per turn, for two different reasons: the RAG leg needs it so
    # an answer can follow the conversation (it used to be sent nothing at all,
    # so every WhatsApp question was answered as a fresh conversation), and the
    # acknowledgement handlers need the previous assistant turn to know what a
    # bare "yes" is actually answering.
    recent_turns = await _whatsapp_recent_turns(lead_id)
    last_asked = _last_assistant_turn(recent_turns)
    awaiting = (getattr(wa_session, "awaiting", "") or "")
    # What the *outbound* message for this turn expects back. Written to the
    # session once, at the end, so a branch that does not set it clears any
    # stale expectation instead of leaving it armed for the next message.
    awaiting_next = ""

    async def _chat(question: str) -> str:
        """RAG for this turn, with the conversation and known fields attached."""
        return await _whatsapp_chat_rag(
            question,
            history=recent_turns,
            profile={
                "Name": lead_name,
                "Email": lead_email,
                "Program of interest": lead_program,
            },
            program=lead_program,
            language=_answer_language,
        )

    # An explicit "my name is X" / "call me X" is an instruction, and it has to
    # beat every other branch. The name used to be settable only while blank,
    # so a student asking to correct it could never be honoured: the request
    # fell through to RAG, which invented a date-of-birth identity check that
    # no code implements, then dropped the answer on the floor.
    name_given = ""
    if not has_email:
        _name_match = re.search(
            # Deliberately narrow: "i am X" and "this is X" are not here
            # because "i am interested" and "this is urgent" are ordinary
            # sentences that would otherwise rename the student.
            r"\b(?:my name is|my name's|call me)\s+(.+)",
            Body,
            re.IGNORECASE,
        )
        if _name_match:
            name_given = _clean_name_candidate(_name_match.group(1))
            if name_given and _detect_meridian_program(name_given.lower()):
                name_given = ""

    # "correct my name" with no new name supplied — ask for it rather than
    # letting it reach the knowledge base.
    wants_name_change = bool(
        re.search(
            r"\b(?:correct|change|update|fix)\b[^.!?]{0,40}\bname\b"
            r"|\bname\b[^.!?]{0,30}\b(?:is\s+)?(?:wrong|incorrect|not)\b",
            Body,
            re.IGNORECASE,
        )
    )

    # Greetings and knowledge questions are answered directly — never
    # mistaken for profile info (e.g. "fees" must not become a name).
    if name_given:
        if lead_id:
            await update_lead(lead_id, name=name_given)
        lead_name = name_given
        if not lead_email:
            awaiting_next = "email"
            answer = (
                f"Thanks — I've corrected your name to {name_given}. "
                "What's your email address?"
            )
        else:
            answer = (
                f"Thanks — I've corrected your name to {name_given}. "
                "How can I help with your admission?"
            )
    elif wants_name_change and not name_given:
        awaiting_next = "name"
        answer = (
            "Of course — what should I change your name to? "
            "(You can just reply with the name.)"
        )
    elif msg_lower in ("hi", "hello", "hey", "hii", "hi there", "namaste"):
        # Greet a known student by name instead of asking for a name that is
        # already on file and that no branch would accept an answer to.
        if lead_name:
            answer = (
                f"Hello {lead_name}! 👋 I'm the Meridian University admissions "
                "assistant. What would you like to know?"
            )
        else:
            answer = "Hello! 👋 I'm the Meridian University admissions assistant. What's your name?"
    elif "?" in Body or any(k in msg_lower for k in _KB_QUESTION_KEYWORDS):
        answer = await _chat(Body)
    # ── Replies to a question the bot actually asked (see `awaiting`) ────
    elif awaiting == "name":
        candidate = _clean_name_candidate(Body)
        if candidate and not _detect_meridian_program(candidate.lower()):
            if lead_id:
                await update_lead(lead_id, name=candidate)
            lead_name = candidate
            answer = f"Thanks — your name is now {candidate}. How can I help with your admission?"
        else:
            awaiting_next = "name"
            answer = "Sorry, I didn't catch a name there. What should I change it to?"
    elif awaiting == "email" and has_email:
        if lead_id:
            await update_lead(lead_id, email=Body.strip())
        lead_email = Body.strip()
        answer = (
            f"Thanks {lead_name}! I've saved your email."
            if lead_name
            else "Thanks — I've saved your email."
        )
    elif awaiting == "program":
        detected = _detect_meridian_program(msg_lower)
        if detected:
            lead_program = await _apply_program_choice(lead_id, lead_program, detected)
            answer = (
                f"**{detected}** — got it! 🎓\n\n"
                "To proceed with your admission, say: 'I want to take admission' "
                "and I'll guide you through the document upload process."
            )
        else:
            awaiting_next = "program"
            answer = "Which program would you like? (e.g. B.Tech Computer Science, MBA, M.Tech, BCA)"
    elif awaiting == "update_field":
        # The "No problem! What would you like to update?" prompt used to be a
        # dead end: nothing consumed the reply, so it fell through to RAG.
        if "name" in msg_lower:
            awaiting_next = "name"
            answer = "Sure — what should I change your name to?"
        elif "email" in msg_lower or has_email:
            if has_email:
                if lead_id:
                    await update_lead(lead_id, email=Body.strip())
                lead_email = Body.strip()
                answer = "Thanks — I've updated your email address."
            else:
                awaiting_next = "email"
                answer = "Sure — what's the correct email address?"
        elif "program" in msg_lower or "course" in msg_lower:
            awaiting_next = "program"
            answer = "Sure — which program would you like instead? (e.g. B.Tech Computer Science, MBA, M.Tech, BCA)"
        else:
            awaiting_next = "update_field"
            answer = "You can update your name, email, or program interest — which one?"
    # State machine for collecting missing info
    elif (
        not lead_name
        and is_name_like
        and not has_email
        and msg_lower not in ("done", "finish", "finished")
        # A short message that names a program is a program, not a person —
        # "M.Tech" as a first message used to be stored as the student's name.
        and not _detect_meridian_program(msg_lower)
    ):
        # User likely provided their name
        if lead_id:
            await update_lead(lead_id, name=Body.strip())
        lead_name = Body.strip()  # Refresh local
        if not lead_email:
            awaiting_next = "email"
            answer = f"Thanks {Body.strip()}! What's your email address? I'll use it to send you program details and follow up."
        else:
            answer = f"Thanks {Body.strip()}! I've updated your profile. How can I help you with Meridian admissions?"
    elif not lead_email and has_email:
        # User provided their email
        if lead_id:
            await update_lead(lead_id, email=Body.strip())
        lead_email = Body.strip()  # Refresh local
        if not lead_name:
            answer = f"Got your email! And what's your name?"
        else:
            answer = f"Thanks! I've saved your email. How can I help you with admissions today?"
    elif not lead_name:
        # Missing name — ask for it
        awaiting_next = "name"
        answer = "Hi! Before I help you, could you tell me your name?"
    elif not lead_email:
        # Missing email — ask for it
        awaiting_next = "email"
        answer = f"Hi {lead_name}! Could you share your email address? I'll use it to send you program details and follow up later."
    else:
        # All info present — check admission intent first, then ACCEPT/DECLINE

        # ── Admission intent detection ──────────────────────────
        is_interested, detected_prog = await _detect_admission_intent_whatsapp(msg_lower)
        if is_interested:
            if lead_id:
                await update_lead(lead_id, status="in_progress")
            if detected_prog:
                lead_program = await _apply_program_choice(lead_id, lead_program, detected_prog)
            if not lead_program:
                awaiting_next = "program"
                answer = "Which program are you interested in? (e.g., B.Tech Computer Science, MBA, M.Tech, BCA)"
            else:
                answer = (
                    f"Great! To process your admission for *{lead_program}*, "
                    "please upload the following documents:\n\n"
                    "📄 Transcript / Mark Sheet\n"
                    "🆔 ID Proof (Passport, Aadhaar, or Driver's License)\n"
                    "📝 Any additional certificates (optional)\n\n"
                    "Just send clear photos or PDFs right here in WhatsApp. "
                    "Type 'done' when you've sent everything."
                )
        # ── ACCEPT/DECLINE offer handling ───────────────────────
        elif msg_lower in ("accept", "accepted", "i accept"):
            result = await _handle_offer_response(lead_id, "accepted")
            if result:
                answer = f"🎉 Congratulations {lead_name}! Your offer for *{result['program']}* has been accepted. You'll receive a payment link shortly to confirm your seat."
            else:
                answer = "I couldn't find a recent offer letter for you. How can I help with your admission?"
        elif msg_lower in ("decline", "declined", "reject", "rejected", "i decline", "no thanks"):
            result = await _handle_offer_response(lead_id, "rejected")
            if result:
                answer = f"Understood, {lead_name}. Your offer for *{result['program']}* has been declined. If you change your mind or want to explore other programs, just let me know!"
            else:
                answer = "I couldn't find a recent offer letter for you. How can I help with your admission?"

        # Only do RAG if user isn't confirming/changing their info
        elif msg_lower in ("yes", "yeah", "yep", "correct", "right", "ok", "okay"):
            # An acknowledgement never moves the program. Only the student
            # states a course; a "yes" to something the bot itself said is not
            # a course choice, and letting it write one is how "You're
            # confirmed for MBA" appeared out of a reply to a knowledge
            # question. If the previous turn did discuss the program already on
            # file, re-affirm it; otherwise the turn belongs to whatever was
            # really asked and goes to RAG with the conversation attached.
            offered = _detect_meridian_program(last_asked.lower()) if last_asked else ""
            if offered and offered == lead_program:
                answer = (
                    f"Great {lead_name}! You're confirmed for *{lead_program}*. "
                    f"To proceed with admission, say: 'I want to take admission'"
                )
            else:
                answer = await _chat(Body)
        elif msg_lower in ("no", "nope", "wrong", "change"):
            awaiting_next = "update_field"
            answer = "No problem! What would you like to update? Your name, email, or program interest?"
        elif msg_lower in ("done", "finish", "finished"):
            # All documents uploaded — generate the offer letter now.
            if not lead_program:
                awaiting_next = "program"
                answer = "Which program are you interested in? (e.g., B.Tech Computer Science, MBA, M.Tech, BCA)"
            else:
                answer = await _whatsapp_offer_on_done(lead_id, lead_name, conversation_id)
        # ── Capture program name from short replies ──────────────────────────
        elif lead_name and lead_email and len(msg_lower.split()) <= 3:
            detected = _detect_meridian_program(msg_lower)
            # Only a message that IS a course choice moves the program. A
            # passing mention ("is mba good?") names one but chooses nothing,
            # and must not silently enrol the student in it.
            if detected and _is_explicit_program_choice(msg_lower, detected):
                lead_program = await _apply_program_choice(lead_id, lead_program, detected)
                answer = (
                    f"**{detected}** — great choice! 🎓\n\n"
                    f"To proceed with your admission, say: 'I want to take admission' "
                    f"and I'll guide you through the document upload process."
                )
            else:
                # Short message that isn't a program — treat it as a question.
                answer = await _chat(Body)
        else:
            # Everything else goes to RAG — same knowledge answers as Streamlit.
            answer = await _chat(Body)

    # Record what this outbound message expects back (or clear a stale
    # expectation) so the next turn can tell a reply from a new question.
    if wa_session is not None:
        wa_session.awaiting = awaiting_next

    # Safety: fallback answer
    try:
        _ = answer
    except NameError:
        answer = f"I have you as {lead_name or 'there'}. How can I help with Meridian admissions?"

    # WhatsApp renders plain text — strip LLM Markdown before sending.
    answer = _strip_markdown(answer)

    # ── CRM: make sure this student exists in Salesforce ─────────
    # Runs once per turn and is cheap when it has nothing to do — a lead that is
    # already linked returns without touching the network. It declines until the
    # identity is complete enough to be safe (a name is required: the API raises
    # IndexError on an empty one, R17) and tries again on the next message.
    #
    # Note the local name/email/program are passed explicitly rather than read
    # from `lead`, which was fetched before the state machine updated the row.
    crm_user_id = ""
    try:
        from app.crm import sync as crm_sync

        crm_user_id = await crm_sync.link_conversation(
            channel="whatsapp",
            conversation_id=conversation_id,
            lead=lead,
            phone_number=From,
            email=lead_email,
            name=lead_name,
            course=lead_program,
            # The registry key, verbatim — the WhatsApp session is registered
            # under the raw `From` ("whatsapp:+1415..."), not the bare number.
            session_key=From,
        ) or ""
    except Exception:
        logger.exception("CRM link failed (non-fatal)")

    # Log conversation
    background_tasks.add_task(
        _log_whatsapp_conversation,
        phone_number=From,
        transcript=f"User: {Body}\nAssistant: {answer}",
        conversation_id=conversation_id,
        crm_user_id=crm_user_id,
        language=_answer_language,
    )

    # Escape exactly once, here at the XML boundary and after the Markdown
    # pass. Only the RAG branch used to escape its own output, so every answer
    # that echoed user-supplied text back — a name, a program — went into the
    # document raw: a student called "Tom & Jerry" produced malformed TwiML and
    # Twilio rejected the whole reply.
    twiml = WHATSAPP_TWIML_TEMPLATE.format(answer=escape(answer))
    logger.info(f"WhatsApp response ({len(answer)} chars): {answer[:80]}...")
    return Response(content=twiml, media_type="application/xml")


# ── REST API: Dashboard data ──────────────────────────────────────────


@app.post("/api/leads")
async def api_create_lead(req: Request, background_tasks: BackgroundTasks):
    """Create a new lead."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    from app.leads.models import create_lead

    result = await create_lead(
        phone_number=body.get("phone_number", ""),
        name=body.get("name", ""),
        email=body.get("email", ""),
        program_interest=body.get("program_interest", ""),
        source=body.get("source", "manual"),
        notes=body.get("notes", ""),
    )
    if result and result.get("id"):
        _schedule_chat_crm_link(background_tasks, body, result)
    return result or JSONResponse({"error": "Database unavailable"}, status_code=503)


@app.get("/api/leads")
async def api_list_leads(
    status: str = "",
    source: str = "",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """List leads with optional filters + text search."""
    from app.leads.models import list_leads

    result = await list_leads(
        status=status or None,
        source=source or None,
        search=search or None,
        limit=min(limit, 200),
        offset=offset,
    )
    return result


@app.get("/api/leads/{lead_id}")
async def api_get_lead(lead_id: str):
    """Get a single lead by ID."""
    from app.leads.models import get_lead

    result = await get_lead(lead_id)
    if result:
        return result
    return JSONResponse({"error": "Lead not found"}, status_code=404)


@app.put("/api/leads/{lead_id}")
async def api_update_lead(lead_id: str, req: Request, background_tasks: BackgroundTasks):
    """Update a lead."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    from app.leads.models import update_lead

    # Build kwargs, filtering out None values
    kwargs = {}
    for field in ["name", "email", "program_interest", "status", "source", "notes"]:
        if field in body and body[field] is not None:
            kwargs[field] = body[field]
    if "call_attempts" in body:
        kwargs["call_attempts"] = int(body["call_attempts"])
    if "next_follow_up" in body:
        kwargs["next_follow_up"] = body["next_follow_up"]

    result = await update_lead(lead_id, **kwargs)
    if result:
        # The chat sends this on every program mention; the link is a no-op once
        # the lead has a crm_user_id, so the repeat cost is one database read.
        _schedule_chat_crm_link(background_tasks, {**body, "lead_id": lead_id}, result)
        return result
    return JSONResponse({"error": "Lead not found"}, status_code=404)


# ── Web chat → CRM ───────────────────────────────────────────────────────────
#
# The Streamlit chat has no server-side session of its own: it mints a
# conversation id in the browser tab (`app.py`) and posts it with its lead. That
# id is what lets Salesforce name the conversation, and it is the reason this
# runs here rather than in the Streamlit process — the CRM client, the database
# and the circuit breaker all live in this one.

def _schedule_chat_crm_link(
    background_tasks: BackgroundTasks, body: dict, lead: dict
) -> None:
    """
    Queue the CRM link for a web-chat lead. Never raises, never blocks the reply.

    Backgrounded rather than awaited on purpose: Streamlit gives this request 10
    seconds, and a cold CRM call with retries can outlast that — while all the
    chat actually needs back is the lead id. A lead with no conversation id
    (the dashboard's "add lead" form, a manual API call) declines inside
    ``link_conversation`` with a logged reason, which is right: it is not a
    conversation.
    """
    lead_id = str(lead.get("id") or "")
    if not lead_id:
        return  # nothing to attach the link to

    try:
        background_tasks.add_task(
            _link_chat_lead,
            lead_id=lead_id,
            conversation_id=str(body.get("conversation_id") or ""),
            phone_number=body.get("phone_number") or lead.get("phone_number") or "",
            name=body.get("name") or lead.get("name") or "",
            email=body.get("email") or lead.get("email") or "",
            course=body.get("program_interest") or lead.get("program_interest") or "",
        )
    except Exception:
        logger.exception("CRM link could not be scheduled for a web-chat lead (non-fatal)")


async def _link_outbound_call(
    *, lead_id: str, conversation_id: str, phone_number: str, session_key: str
) -> None:
    """
    Link a dialled lead to the CRM at call start.

    The advantage over inbound: the lead row already exists, so this links with
    a full identity (name, email, program) straight from the database rather
    than waiting for the caller to say who they are. The ``session_key`` is the
    stream sid, so the live call can read the id back without another round trip.
    """
    try:
        from app.crm import sync as crm_sync
        from app.leads.models import get_lead

        lead = await get_lead(lead_id)
        if not lead:
            logger.warning(f"Outbound call {session_key}: lead {lead_id} not found")
            return

        await crm_sync.link_conversation(
            channel="outbound_call",
            conversation_id=conversation_id,
            lead=lead,
            phone_number=phone_number or lead.get("phone_number") or "",
            session_key=session_key,
        )
    except Exception:
        logger.exception("CRM link failed for an outbound call (non-fatal)")


async def _link_chat_lead(
    *,
    lead_id: str,
    conversation_id: str,
    phone_number: str,
    name: str,
    email: str,
    course: str,
) -> None:
    if not lead_id:
        return
    try:
        from app.crm import sync as crm_sync
        from app.leads.models import get_lead

        await crm_sync.link_conversation(
            channel="chat",
            conversation_id=conversation_id,
            lead=await get_lead(lead_id),
            phone_number=phone_number,
            email=email,
            name=name,
            course=course,
        )
    except Exception:
        logger.exception("CRM link failed for a web-chat lead (non-fatal)")


@app.post("/api/leads/{lead_id}/call")
async def api_trigger_call(lead_id: str):
    """Queue an outbound call for a lead."""
    from app.leads.models import add_to_call_queue, get_lead

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    result = await add_to_call_queue(lead_id=lead_id)
    if result:
        return result
    return JSONResponse({"error": "Database unavailable"}, status_code=503)


@app.post("/api/quick-call")
async def api_quick_call(req: Request):
    """
    One-shot convenience endpoint: create (or upsert) a lead and
    immediately queue an outbound call.  Returns both the lead and
    call_queue entry in a single response.

    Body: {"phone_number": "+91...", "name": "Optional",
           "language": "hi"}   # optional, explicit selection

    `language` is the outbound equivalent of the inbound IVR digit: an
    explicit choice that pins the answer language for the whole call. Without
    it the worker uses the lead's stored language (soft, auto-detection may
    still switch on strong evidence), then ``OUTBOUND_LANGUAGE``.
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    phone = body.get("phone_number", "").strip()
    if not phone:
        return JSONResponse({"error": "phone_number is required"}, status_code=400)

    language = normalize_language_code(body.get("language", ""))
    if language and not is_supported_language(language):
        return JSONResponse(
            {"error": f"Unsupported language: {language}"}, status_code=400
        )

    from app.leads.models import add_to_call_queue, upsert_lead_by_phone

    lead = await upsert_lead_by_phone(
        phone_number=phone,
        name=body.get("name", ""),
        source="quick-call",
    )
    if not lead:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    queue_entry = await add_to_call_queue(
        lead_id=lead["id"], language=language
    )
    if not queue_entry:
        return JSONResponse({"error": "Failed to queue call"}, status_code=503)

    return {
        "lead": lead,
        "call_queue": queue_entry,
        "tunnel_host": _resolve_tunnel_host(),
    }


@app.get("/api/leads/{lead_id}/score")
async def api_lead_score(lead_id: str):
    """Get lead quality score (1-10) with breakdown, temperature, and sentiment data."""
    from app.leads.models import get_lead, get_conversations
    from app.leads.service import calculate_lead_score

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    conversations = await get_conversations(lead_id=lead_id) or []
    score_data = calculate_lead_score(lead, conversations)

    # Enrich with sentiment data if available
    sentiment = {}
    try:
        from app.sentiment.models import get_lead_sentiment
        sent = await get_lead_sentiment(lead_id)
        if sent:
            sentiment = {
                "sentiment_category": sent.get("category", ""),
                "overall_sentiment_score": sent.get("s_lead", 0),
                "sentiment_trajectory": sent.get("trajectory", ""),
                "conversion_probability": sent.get("p_convert"),
                "last_sentiment_at": sent.get("created_at", ""),
            }
    except Exception:
        pass

    return {"lead_id": lead_id, **score_data, "sentiment": sentiment}


# ── Sentiment Analysis API ───────────────────────────────────────────


@app.post("/api/sentiment/score-transcript")
async def api_sentiment_score_transcript(req: Request):
    """
    Score a transcript for sentiment without persisting data (dry-run).

    Body: {"transcript": "Conversation text...", "lead_id": "optional-uuid"}

    If lead_id is provided, includes composite score with EWMA momentum
    and persists the result.  Without lead_id, pure dry-run.
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    transcript = body.get("transcript", "").strip()
    if not transcript:
        return JSONResponse({"error": "transcript is required"}, status_code=422)

    lead_id = body.get("lead_id", "").strip()

    try:
        from app.sentiment.scorer import score_transcript

        result = await score_transcript(
            transcript=transcript,
            lead_id=lead_id,
            prosody=body.get("prosody"),
        )
        return result
    except Exception as e:
        logger.exception("Sentiment scoring failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/leads/{lead_id}/sentiment")
async def api_lead_sentiment(lead_id: str):
    """Get the session-level aggregate sentiment for a lead."""
    from app.sentiment.models import get_lead_sentiment_history

    history = await get_lead_sentiment_history(lead_id, limit=20)
    if not history:
        return JSONResponse({"error": f"Lead {lead_id} not found or no sentiment data"}, status_code=404)

    # Compute session-level aggregate from all exchanges
    from app.sentiment.scorer import _compute_session_aggregate
    agg = await _compute_session_aggregate(lead_id)

    # Latest per-exchange for detail
    latest = history[0] if history else {}

    # Collect all emotions and objections across the session
    all_emotions = [h.get("primary_emotion", "") for h in history if h.get("primary_emotion")]
    all_objections = []
    for h in history:
        objs = h.get("objections", [])
        if isinstance(objs, list):
            all_objections.extend(objs)

    # Average buying intent and friction
    intents = [h.get("buying_intent_score", 0) for h in history if h.get("buying_intent_score")]
    frictions = [h.get("friction_score", 0) for h in history if h.get("friction_score")]
    avg_intent = sum(intents) / len(intents) if intents else 0.0
    avg_friction = sum(frictions) / len(frictions) if frictions else 0.0

    return {
        "lead_id": lead_id,
        "current_category": agg.get("category", "Nurture"),
        "overall_sentiment_score": agg.get("s_lead", 0.0),
        "sentiment_trajectory": agg.get("trajectory", "Stable"),
        "conversion_probability": agg.get("p_convert"),
        "last_s_call": latest.get("s_call", 0.0),
        "primary_emotion": agg.get("dominant_emotion", latest.get("primary_emotion", "")),
        "buying_intent_score": round(avg_intent, 4),
        "friction_score": round(avg_friction, 4),
        "objections": list(set(all_objections)),
        "updated_at": latest.get("created_at", ""),
        "exchanges": agg.get("exchanges", len(history)),
        "scoring_components": {
            "s_latest": latest.get("s_call", 0.0),
            "ewma_baseline": None,
            "i_intent": 0.5 * avg_intent,
            "friction": avg_friction,
        },
        "weights_used": {"w1": 0.30, "w2": 0.30, "w3": 0.25, "w4": 0.15},
        "history_count": len(history),
    }


@app.get("/api/leads/{lead_id}/sentiment-history")
async def api_lead_sentiment_history(
    lead_id: str,
    limit: int = 10,
):
    """Get historical sentiment scores for a lead, newest first."""
    from app.leads.models import get_lead
    from app.sentiment.models import get_lead_sentiment_history

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    history = await get_lead_sentiment_history(lead_id, limit=min(limit, 50))
    return {
        "lead_id": lead_id,
        "count": len(history),
        "history": history,
    }


@app.post("/api/sentiment/recompute/{lead_id}")
async def api_sentiment_recompute(lead_id: str):
    """
    Recompute the session-level aggregate sentiment for a lead
    from all accumulated exchanges.  Useful after a conversation
    ends to get the full-session picture instead of per-message scores.
    """
    from app.sentiment.scorer import _compute_session_aggregate
    from app.sentiment.models import update_lead_sentiment_fields

    result = await _compute_session_aggregate(lead_id)
    await update_lead_sentiment_fields(
        lead_id=lead_id,
        current_category=result["category"],
        overall_sentiment_score=result["s_lead"],
        sentiment_trajectory=result["trajectory"],
        conversion_probability=result["p_convert"],
    )
    return {"lead_id": lead_id, **result}


@app.post("/api/sentiment/explain-categorization")
async def api_sentiment_explain_categorization(req: Request):
    """
    Get a grounded, human-readable explanation for a lead's sentiment category.

    Body: {"lead_id": "uuid"}  — uses latest persisted scores
          OR
          {"s_lead": 0.78, "delta_s": 0.12, "p_convert": 0.82, ...} — manual values
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    lead_id = body.get("lead_id", "").strip()

    if lead_id:
        # Load from persisted data
        from app.sentiment.models import get_lead_sentiment

        sent = await get_lead_sentiment(lead_id)
        if not sent:
            return JSONResponse({"error": f"Lead {lead_id} not found or no sentiment data"}, status_code=404)

        from app.sentiment.categorizer import explain_categorization

        result = explain_categorization(
            s_lead=sent.get("s_lead", 0.0),
            delta_s=None,  # Can't compute from single score
            p_convert=sent.get("p_convert"),
            friction=sent.get("friction_score", 0.0),
            trajectory=sent.get("trajectory", "Stable"),
            objections=sent.get("objections", []),
            bant_completeness=0.0,
            buying_intent_score=sent.get("buying_intent_score", 0.0),
            primary_emotion=sent.get("primary_emotion", ""),
        )
        result["lead_id"] = lead_id
        return result

    # Manual mode — use provided values
    try:
        s_lead = float(body.get("s_lead", 0.0))
        delta_s_val = body.get("delta_s")
        delta_s = float(delta_s_val) if delta_s_val is not None else None
        p_convert_val = body.get("p_convert")
        p_convert = float(p_convert_val) if p_convert_val is not None else None
        friction = float(body.get("friction", 0.0))
        trajectory = str(body.get("trajectory", "Stable"))
        objections = body.get("objections", [])
        bant = float(body.get("bant_completeness", 0.0))
        buying = float(body.get("buying_intent_score", 0.0))
        emotion = str(body.get("primary_emotion", ""))
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": f"Invalid parameter value: {e}"}, status_code=422)

    from app.sentiment.categorizer import explain_categorization

    result = explain_categorization(
        s_lead=s_lead,
        delta_s=delta_s,
        p_convert=p_convert,
        friction=friction,
        trajectory=trajectory,
        objections=objections if isinstance(objections, list) else [],
        bant_completeness=bant,
        buying_intent_score=buying,
        primary_emotion=emotion,
    )
    return result


@app.get("/api/call-queue")
async def api_get_call_queue_status(lead_id: str = ""):
    """
    Get the most recent call_queue entry for a lead.
    Used by the quick-call page to poll for status.

    Query: ?lead_id=<uuid>
    Returns: {status, call_sid, error_message, ...} or {error}
    """
    if not lead_id:
        return JSONResponse({"error": "lead_id query parameter required"}, status_code=400)

    from app.leads.models import get_lead

    # Get the lead first to verify it exists, then find its latest call
    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    # Query the most recent call_queue entry for this lead
    try:
        import psycopg2

        # One resolution point (US-011 TAC-1). This block carried its own
        # copy of the six DB_* reads under an `import os as _os` alias — the
        # alias is why the reader sweep's `os.environ` patterns never saw it.
        from app.config import database_dsn

        conn = psycopg2.connect(database_dsn())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lead_id, status, call_sid, scheduled_at, "
                "started_at, completed_at, error_message, created_at "
                "FROM call_queue WHERE lead_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (lead_id,),
            )
            row = cur.fetchone()
        conn.close()

        if not row:
            return {"status": "queued", "lead_id": lead_id}

        return {
            "id": str(row[0]),
            "lead_id": str(row[1]) if row[1] else "",
            "status": row[2] or "queued",
            "call_sid": row[3] or "",
            "scheduled_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]) if row[4] else "",
            "started_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]) if row[5] else "",
            "completed_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]) if row[6] else "",
            "error_message": row[7] or "",
            "created_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]) if row[8] else "",
        }
    except Exception:
        logger.exception("Failed to query call_queue status")
        return {"error": "Database unavailable", "lead_id": lead_id}


# ── API: Batch Quick Call ────────────────────────────────────────────

_batch_jobs: dict = {}  # in-memory batch job tracker


@app.post("/api/quick-call/batch")
async def api_batch_quick_call(req: Request):
    """
    Queue multiple outbound calls in one request.
    Body: {"leads": [{"phone_number": "...", "name": "...", "program_interest": "..."}], "mode": "all_at_once"}
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    leads_data = body.get("leads", [])
    if not leads_data:
        return JSONResponse({"error": "leads array is required"}, status_code=400)

    from app.leads.models import add_to_call_queue, upsert_lead_by_phone
    import uuid

    batch_id = str(uuid.uuid4())[:8]
    queued = []
    errors = []

    for lead in leads_data:
        phone = lead.get("phone_number", "").strip()
        if not phone:
            errors.append({"phone": phone, "error": "phone_number required"})
            continue
        try:
            entry = await upsert_lead_by_phone(
                phone_number=phone,
                name=lead.get("name", ""),
                source=lead.get("source", "batch-dashboard"),
            )
            if not entry:
                errors.append({"phone": phone, "error": "Database unavailable"})
                continue
            queue_entry = await add_to_call_queue(lead_id=entry["id"])
            queued.append({
                "lead_id": entry["id"],
                "phone": phone,
                "call_queue_id": queue_entry["id"] if queue_entry else None,
            })
        except Exception as e:
            errors.append({"phone": phone, "error": str(e)})

    _batch_jobs[batch_id] = {
        "total": len(queued),
        "completed": 0,
        "results": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "batch_id": batch_id,
        "total": len(queued),
        "queued": len(queued),
        "skipped": len(errors),
        "errors": errors,
    }


@app.get("/api/quick-call/batch/{batch_id}")
async def api_batch_quick_call_status(batch_id: str):
    """Poll batch call progress."""
    job = _batch_jobs.get(batch_id)
    if not job:
        return JSONResponse({"error": "Batch not found"}, status_code=404)
    return job


# ── API: Live Calls (SSE) ────────────────────────────────────────────

# In-memory transcript queue (shared by WebSocket handlers + SSE endpoint)
_transcript_events: list[dict] = []
_active_call_sids: dict[str, dict] = {}  # call_sid -> call metadata


def _push_transcript_event(event_type: str, call_sid: str, data: dict | None = None):
    """Push a transcript event to all SSE listeners."""
    event = {
        "event": event_type,
        "call_sid": call_sid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if data:
        event.update(data)
    _transcript_events.append(event)
    # Keep only last 200 events
    if len(_transcript_events) > 200:
        _transcript_events[:] = _transcript_events[-200:]


@app.get("/health")
async def api_health():
    """Pure liveness: the process serves HTTP. No checks, no secrets, no work.

    This is the answer to "is the box up". Readiness is `/ready`.
    """
    return JSONResponse({"status": "ok"})


def _ready_with_stt(payload: dict) -> dict:
    """Attach the STT/TTS provider chains to the readiness payload (non-secret)."""
    out = dict(payload)
    try:
        from app import stt as stt_registry

        out["stt"] = stt_registry.health()
    except Exception as exc:                    # noqa: BLE001
        out["stt"] = {"error": str(exc)}
    try:
        from app import tts_sarvam

        out["tts"] = {
            "provider": settings.TTS_PROVIDER,
            "sarvam": tts_sarvam.health(),
            "kokoro_voice": settings.KOKORO_VOICE,
        }
    except Exception as exc:                    # noqa: BLE001
        out["tts"] = {"error": str(exc)}
    return out


@app.get("/ready")
async def api_ready(refresh: int = 0):
    """US-007 Phase 1.1: the boot gate's own verdict, readable at call time.

    The plain response is the cached boot assessment (all four clauses, warmth
    included) plus `status` in the harness's vocabulary. `?refresh=1` re-runs
    the cheap clauses -- services, residency, GPU, config -- in a worker thread
    and carries the prefix clause from the boot assessment: a poll never
    re-warms, and a slow model load can never block the poll (the loop stays
    free; the assessment runs in a thread). No credential value is rendered.
    """
    from app import boot_readiness as _boot

    global _readiness_cache
    if refresh != 1 and _readiness_cache is not None:
        return JSONResponse(_ready_with_stt(_readiness_cache))
    async with _readiness_lock:
        if refresh == 1:
            if _readiness_cache is None:
                # No boot verdict to carry the prefix clause from: fall back to
                # a full assessment rather than report unverified warmth as a
                # failure.
                _readiness_cache = (await _asyncio.to_thread(
                    _boot.assess, True)).to_dict()
            fresh = await _asyncio.to_thread(_boot.assess, False)
            _readiness_cache = _boot.refresh_payload(_readiness_cache, fresh)
        elif _readiness_cache is None:
            _readiness_cache = (await _asyncio.to_thread(_boot.assess, True)).to_dict()
        if "status" not in _readiness_cache:
            _readiness_cache["status"] = (
                "ready" if _readiness_cache.get("ready") else "not_ready")
        return JSONResponse(_ready_with_stt(_readiness_cache))


@app.get("/api/perf/policy")
async def api_perf_policy():
    """Admission and work-priority records, as counts (US-016, US-017).

    The load harness runs in a separate process, so the invariants these two
    stories own -- "an N=3 window produces exactly one refusal and zero new
    sessions", "zero background starts during a voice turn" -- cannot be
    counted from the harness's own side. They are properties of the app's
    decision points, so the app has to report them.

    Counts and reasons only: no phone number, no transcript, no caller text.
    `caller_ref` is a truncated digest. This is the same surface an operator
    reads to answer "why was that call refused", which is why it is a normal
    endpoint rather than a test hook.
    """
    from app.admission import status as admission_status
    from app.work_priority import policy_status
    from app.llm_backend import health as llm_health

    return JSONResponse({
        "admission": admission_status(),
        "work_priority": policy_status(),
        "retrieval": {"removed": True, "context_mode": settings.CONTEXT_MODE},
        "llm": llm_health(),
    })


@app.get("/api/calls/live")
async def api_calls_live(stream: bool = False):
    """
    Get active calls with transcript snippets.
    Set ?stream=true for SSE (Server-Sent Events) real-time streaming.

    The cockpit polls the JSON form (app/static/dashboard.js, every 2 s) instead
    of using the stream: measured 2026-09-22, a Cloudflare tunnel returns this
    response's headers but never its body, so the EventSource stayed silently
    empty for a whole call. The stream still works on a direct connection and is
    left in place for callers that terminate at the origin; both forms read the
    same in-memory state.
    """
    if not stream:
        call_sids = list(_active_call_sids.keys())
        return {
            "active_calls": call_sids,
            "count": len(call_sids),
            "details": _active_call_sids,
        }

    # SSE streaming mode
    import asyncio as _asyncio
    from starlette.responses import StreamingResponse

    async def event_stream():
        last_idx = max(0, len(_transcript_events) - 1)
        while True:
            while last_idx < len(_transcript_events):
                evt = _transcript_events[last_idx]
                yield f"event: {evt['event']}\ndata: {json.dumps(evt)}\n\n"
                last_idx += 1
            await _asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── API: Dashboard Summary ────────────────────────────────────────────


@app.post("/api/interactions/log")
async def api_log_interaction(req: Request):
    """
    Log an interaction from any channel and trigger sentiment scoring.

    Body: {
        "phone_number": "+91...",
        "transcript": "User: ...\nAssistant: ...",
        "channel": "streamlit" | "whatsapp" | "inbound_call" | "outbound_call",
        "lead_id": "optional-uuid (echoed back; the lead is resolved by phone)",
        "conversation_id": "optional-uuid — the chat's own id for this session",
        "language": "optional base code (en, hi, gu, es, ...)"
    }

    If lead_id is provided, uses it directly. Otherwise, looks up by phone
    or creates a new lead. Triggers sentiment scoring automatically.
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    phone = body.get("phone_number", "").strip()
    transcript = body.get("transcript", "").strip()
    channel = body.get("channel", "streamlit").strip()
    lead_id = body.get("lead_id", "").strip()
    conversation_id = body.get("conversation_id", "").strip()
    # Recorded on the conversation row so an outbound follow-up can open in the
    # language this chat actually used. Empty keeps the pre-language behaviour.
    language = body.get("language", "").strip()

    if not transcript:
        return JSONResponse({"error": "transcript is required"}, status_code=422)

    try:
        from app.leads.models import get_lead_by_phone, get_lead_crm_user_id
        from app.leads.service import log_interaction

        # Attach the Salesforce user if this person is already linked, so the
        # conversation row points at the same record the CRM holds.
        crm_user_id = ""
        try:
            lead = await get_lead_by_phone(phone) if phone else None
            if lead:
                crm_user_id = await get_lead_crm_user_id(lead["id"])
        except Exception:
            logger.exception("Interaction log: could not read the CRM link")

        conv = await log_interaction(
            phone_number=phone,
            channel=channel,
            transcript=transcript,
            # The chat's own conversation id, so a web chat is one conversation
            # in the table — and carries the same id Salesforce was given.
            # (lead_id is not passed: this helper upserts by phone.)
            conversation_id=conversation_id or None,
            crm_user_id=crm_user_id or None,
            language=language,
        )
        if conv:
            return {
                "status": "ok",
                "conversation_id": conv.get("id", ""),
                "lead_id": conv.get("lead_id", lead_id),
            }
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    except Exception as e:
        logger.exception("Interaction logging failed")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _get_sentiment_stats() -> dict:
    """Count leads per sentiment category for the dashboard overview."""
    try:
        import psycopg2
        from app.config import settings

        conn = psycopg2.connect(settings.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_category, COUNT(*) FROM leads "
                "WHERE current_category != '' AND current_category IS NOT NULL "
                "GROUP BY current_category"
            )
            rows = cur.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


@app.get("/api/dashboard/summary")
async def api_dashboard_summary():
    """Aggregated KPIs + recent activity in one call for the dashboard."""
    from datetime import datetime
    from app.leads.models import list_leads, get_conversations

    try:
        leads = await list_leads(limit=200) or []
    except Exception:
        leads = []
    try:
        conversations = await get_conversations(limit=20) or []
    except Exception:
        conversations = []

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count today's new leads
    new_today = sum(
        1 for l in leads
        if l.get("created_at") and str(l["created_at"]) >= today_start.isoformat()
    )

    # Hot leads
    hot_leads = sum(
        1 for l in leads
        if l.get("status") in ("in_progress",)
        and l.get("next_follow_up")
    )

    # Due follow-ups today
    today_str = today_start.strftime("%Y-%m-%d")
    due_today = sum(
        1 for l in leads
        if l.get("next_follow_up")
        and str(l["next_follow_up"])[:10] == today_str
    )

    # Pipeline by status
    pipeline = {}
    for l in leads:
        s = l.get("status", "pending")
        pipeline[s] = pipeline.get(s, 0) + 1

    return {
        "stats": {
            "active_calls": len(_active_call_sids),
            "new_leads_today": new_today,
            "due_follow_ups": due_today,
            "hot_leads": hot_leads,
            "total_pipeline": len(leads),
        },
        "pipeline": pipeline,
        "recent_activity": conversations[:10],
        "active_call_details": _active_call_sids,
        "sentiment_stats": await _get_sentiment_stats(),
        "machine_profile": _machine_profile_status(),
    }


# ── API: Demo Data Management ─────────────────────────────────────────


@app.post("/api/demo/reset")
async def api_demo_reset():
    """Clear all data and reseed with fresh demo leads + conversations."""
    try:
        import psycopg2
        from app.config import settings

        conn = psycopg2.connect(settings.DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("DELETE FROM conversations")
        cur.execute("DELETE FROM call_queue")
        cur.execute("DELETE FROM follow_ups")
        cur.execute("DELETE FROM leads")
        conn.close()

        # Run the seed script
        import subprocess, sys
        seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_data.py"
        result = subprocess.run([sys.executable, str(seed_path)], capture_output=True, text=True, timeout=30)
        return {"status": "ok", "message": "Demo data reset", "output": result.stdout.strip()}
    except Exception as e:
        logger.exception("Demo reset failed")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/demo/seed")
async def api_demo_seed():
    """Load dummy data without clearing existing data."""
    try:
        import subprocess, sys
        seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_data.py"
        result = subprocess.run([sys.executable, str(seed_path)], capture_output=True, text=True, timeout=30)
        return {"status": "ok", "message": "Dummy data loaded", "output": result.stdout.strip()}
    except Exception as e:
        logger.exception("Demo seed failed")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/conversations")
async def api_list_conversations(
    lead_id: str = "",
    channel: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """List conversations with optional filters."""
    from app.leads.models import get_conversations

    result = await get_conversations(
        lead_id=lead_id or None,
        channel=channel or None,
        limit=min(limit, 200),
        offset=offset,
    )
    return result


@app.post("/api/follow-ups")
async def api_schedule_follow_up(req: Request):
    """Schedule a follow-up action."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    from app.leads.models import schedule_follow_up

    result = await schedule_follow_up(
        lead_id=body.get("lead_id", ""),
        scheduled_at=body.get("scheduled_at", ""),
        type=body.get("type", "call"),
        notes=body.get("notes", ""),
    )
    if result:
        return result
    return JSONResponse({"error": "Database unavailable"}, status_code=503)


@app.get("/api/stats")
async def api_get_stats():
    """Return dashboard KPIs."""
    from app.leads.models import get_lead_stats

    return await get_lead_stats()


# ── REST API: Course catalog ─────────────────────────────────────────

@app.get("/api/courses")
async def api_list_courses():
    """List all active courses."""
    from app.offers.models import list_courses
    return await list_courses()


@app.post("/api/courses")
async def api_create_course(req: Request):
    """Create a new course."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    from app.offers.models import create_course
    result = await create_course(
        name=name,
        duration=body.get("duration", ""),
        fees=body.get("fees", ""),
        intake=body.get("intake", ""),
        description=body.get("description", ""),
    )
    if result:
        return result
    return JSONResponse({"error": "Database unavailable"}, status_code=503)


@app.put("/api/courses/{course_id}")
async def api_update_course(course_id: str, req: Request):
    """Update a course."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    from app.offers.models import update_course
    result = await update_course(course_id, **body)
    if result:
        return result
    return JSONResponse({"error": "Course not found"}, status_code=404)


@app.delete("/api/courses/{course_id}")
async def api_deactivate_course(course_id: str):
    """Deactivate a course (soft delete)."""
    from app.offers.models import update_course
    result = await update_course(course_id, is_active=False)
    if result:
        return {"status": "deactivated"}
    return JSONResponse({"error": "Course not found"}, status_code=404)


# ── REST API: Document upload ────────────────────────────────────────

@app.post("/api/leads/{lead_id}/documents")
async def api_upload_document(
    lead_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form("other"),
    conversation_id: str = Form(""),
):
    """Upload a document for a lead. Auto-triggers offer letter if conditions met."""
    from app.leads.models import get_lead
    from app.offers.models import add_document
    from pathlib import Path
    import uuid

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    # Sanitize filename
    safe_name = Path(file.filename or "upload").name
    safe_name = safe_name.replace(" ", "_")

    # Determine storage path
    # The same field the offer-letter path near the top of this file uses, so
    # the two can no longer disagree when .env was never loaded.
    data_dir = Path(settings.DATA_DIR)
    lead_dir = data_dir / "documents" / lead_id
    lead_dir.mkdir(parents=True, exist_ok=True)

    short_id = str(uuid.uuid4())[:8]
    stored_name = f"{short_id}_{safe_name}"
    stored_path = lead_dir / stored_name

    # Read content and write
    content = await file.read()
    stored_path.write_bytes(content)

    # Record in DB
    doc = await add_document(
        lead_id=lead_id,
        filename=safe_name,
        stored_path=str(stored_path),
        doc_type=doc_type,
        mime_type=file.content_type or "",
        size_bytes=len(content),
    )
    if not doc:
        # Clean up file if DB insert failed
        try:
            stored_path.unlink()
        except Exception:
            pass
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    # Hand the student's document to the CRM (backgrounded — three HTTP calls
    # must not sit in the student's upload request).
    if doc.get("id"):
        try:
            from app.crm.documents import schedule_document_upload

            schedule_document_upload(
                lead_id=lead_id, doc_id=str(doc["id"]), file_path=str(stored_path),
                doc_type=doc_type, original_name=safe_name,
            )
        except Exception:
            logger.exception("Could not schedule the CRM upload for a document")

    # Auto-trigger offer letter if the lead is fully ready
    offer_result = None
    from app.offers.service import evaluate_offer_readiness, generate_and_send_offer
    readiness = await evaluate_offer_readiness(lead_id)
    if readiness["ready"]:
        # This endpoint is the web chat's upload path, so the offer it triggers
        # is a chat conversation — not WhatsApp. The chat's own conversation id
        # comes with the upload so the offer is logged against that
        # conversation, rather than minting a second one the CRM never saw.
        offer_result = await generate_and_send_offer(
            lead_id, channel="chat", conversation_id=conversation_id
        )

    return {
        "document": doc,
        "offer_letter": offer_result,
        "offer_readiness": readiness,   # additive — old clients ignore unknown keys
    }


@app.get("/api/leads/{lead_id}/documents")
async def api_list_documents(lead_id: str):
    """List documents for a lead."""
    from app.offers.models import list_documents
    return await list_documents(lead_id)


@app.get("/api/documents/{document_id}/file")
async def api_serve_document(document_id: str):
    """Serve a document file for download."""
    from app.offers.models import get_document
    doc = await get_document(document_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    stored = doc.get("stored_path", "")
    if not stored or not Path(stored).is_file():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)
    return FileResponse(stored, media_type=doc.get("mime_type") or "application/octet-stream",
                        filename=doc.get("filename"))


@app.delete("/api/documents/{document_id}")
async def api_delete_document(document_id: str):
    """Delete a document (DB row + file on disk)."""
    from app.offers.models import get_document, delete_document
    doc = await get_document(document_id)
    if doc:
        stored = doc.get("stored_path", "")
        if stored:
            try:
                Path(stored).unlink(missing_ok=True)
            except Exception:
                pass
    ok = await delete_document(document_id)
    if ok:
        return {"status": "deleted"}
    return JSONResponse({"error": "Document not found"}, status_code=404)


# ── REST API: Offer readiness check ──────────────────────────────────

@app.get("/api/leads/{lead_id}/offer-readiness")
async def api_offer_readiness(lead_id: str):
    """Return what prerequisites are missing before an offer can be generated."""
    from app.offers.service import evaluate_offer_readiness
    readiness = await evaluate_offer_readiness(lead_id)
    if readiness["lead"] is None:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    return readiness


# ── REST API: Offer letters ──────────────────────────────────────────

@app.get("/api/leads/{lead_id}/offer-letters")
async def api_list_offer_letters(lead_id: str):
    """List offer letters for a lead."""
    from app.offers.models import list_offer_letters
    return await list_offer_letters(lead_id)


@app.get("/api/offers/{offer_id}")
async def api_get_offer_letter(offer_id: str):
    """Get a single offer letter by ID."""
    from app.offers.models import get_offer_letter
    result = await get_offer_letter(offer_id)
    if result:
        return result
    return JSONResponse({"error": "Offer letter not found"}, status_code=404)


@app.get("/api/offers/{offer_id}/pdf")
async def api_serve_offer_pdf(offer_id: str):
    """Serve the offer letter PDF (public URL for Twilio media + email)."""
    from app.offers.models import get_offer_letter
    offer = await get_offer_letter(offer_id)
    if not offer:
        return JSONResponse({"error": "Offer letter not found"}, status_code=404)
    pdf_path = offer.get("pdf_path", "")
    if not pdf_path or not Path(pdf_path).is_file():
        return JSONResponse({"error": "PDF not found on disk"}, status_code=404)
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"offer_letter_{offer_id[:8]}.pdf",
    )


@app.put("/api/offers/{offer_id}/status")
async def api_update_offer_status(offer_id: str, req: Request):
    """Update offer letter status (accepted / rejected)."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    status = body.get("status", "")
    if status not in ("accepted", "rejected"):
        return JSONResponse({"error": "Status must be 'accepted' or 'rejected'"}, status_code=400)
    from app.offers.models import update_offer_letter_status
    result = await update_offer_letter_status(offer_id, status)
    if result:
        # A staff member recording a decision by hand is the same fact as the
        # student typing ACCEPT — the CRM should not be able to tell them apart.
        lead_id = str(result.get("lead_id") or "")
        if lead_id:
            await _publish_offer_response(lead_id, status)
        return result
    return JSONResponse({"error": "Offer letter not found"}, status_code=404)


# ── MCP: SSE transport ────────────────────────────────────────────────


@app.get("/mcp/sse")
async def mcp_sse_endpoint(req: Request):
    """
    MCP SSE endpoint — AI clients connect here to receive tool listings
    and stream results.
    """
    try:
        from app.mcp.server import handle_sse_request

        return await handle_sse_request(req)
    except Exception as e:
        logger.exception("MCP SSE endpoint failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/mcp/messages")
async def mcp_messages_endpoint(req: Request):
    """
    MCP JSON-RPC message handler — AI clients POST tool calls here.
    """
    try:
        from app.mcp.server import handle_messages_request

        return await handle_messages_request(req)
    except Exception as e:
        logger.exception("MCP messages endpoint failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── HTTP: Health check ───────────────────────────────────────────────

@app.get("/")
async def health_check(request: Request):
    """Serve landing page for browsers, JSON health check for API clients."""
    from app.config import settings

    # Serve HTML landing page for browser requests
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        html_path = Path(__file__).resolve().parent / "static" / "index.html"
        if html_path.is_file():
            return FileResponse(html_path, media_type="text/html")

    # JSON for API clients / curl
    return JSONResponse({
        "status": "ok",
        "app": "University Admissions Voice Assistant",
        "database": "connected" if _db_available else "unavailable",
        "transport": settings.TRANSPORT_PROVIDER,
        "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID),
        "twilio_phone": settings.TWILIO_PHONE_NUMBER or "(not set)",
        "outbound_worker": "active" if _outbound_worker and _outbound_worker._running else "inactive",
        "mcp_enabled": settings.MCP_ENABLED,
        "sentiment_analysis": "enabled",
        "machine_profile": _machine_profile_status(),
        "endpoints": {
            "health": "/",
            "voice_page": "/voice",
            "quick_call_page": "/call",
            "dashboard_page": "/dashboard",
            "websocket_pcm": "/ws/voice",
            "websocket_text_rag": "/ws/voice/text",
            "twilio_webhook": "/twilio/voice",
            "twilio_websocket": "/ws/twilio",
            "twilio_outbound_ws": "/ws/twilio-outbound",
            "twilio_outbound_status": "/twilio/outbound/status",
            "whatsapp_webhook": "/twilio/whatsapp",
            "quick_call_api": "/api/quick-call",
            "call_queue_status": "/api/call-queue",
            "mcp_sse": "/mcp/sse",
            "mcp_messages": "/mcp/messages",
            "sentiment_score_transcript": "/api/sentiment/score-transcript",
            "sentiment_lead_score": "/api/leads/{lead_id}/sentiment",
            "sentiment_lead_history": "/api/leads/{lead_id}/sentiment-history",
            "sentiment_explain": "/api/sentiment/explain-categorization",
        },
    })


# ── HTTP: Voice client page ──────────────────────────────────────────

@app.get("/voice")
async def voice_page():
    """Serve the browser-based voice client page, with its audio format injected.

    The page captures at the rate `/ws/voice` is built around, and both used to
    state that format independently — the page in JavaScript, the endpoint in a
    docstring. Serving `app/audio_format.py`'s constants gives that contract one
    home. The `<!--AUDIO_CONFIG-->` placeholder stays an HTML comment if the same
    file is fetched from `/static/` (which is mounted), and the page's own
    defaults then apply, so both routes work.
    """
    html_path = Path(__file__).resolve().parent / "static" / "voice_client.html"
    if not html_path.is_file():
        return JSONResponse(
            {"message": "Voice client page not found.", "status": "error"},
            status_code=404,
        )
    audio_config = (
        "<script>window.AUDIO_CONFIG = {"
        f"sampleRate: {audio_format.SAMPLE_RATE}, "
        f"channels: {audio_format.CHANNELS}, "
        f"chunkFrames: {audio_format.CHUNK_FRAMES}"
        "};</script>"
    )
    return HTMLResponse(
        html_path.read_text(encoding="utf-8").replace("<!--AUDIO_CONFIG-->", audio_config)
    )


# ── HTTP: Quick call page ─────────────────────────────────────────────


@app.get("/call")
async def quick_call_page():
    """Serve the quick outbound call page."""
    html_path = Path(__file__).resolve().parent / "static" / "quick_call.html"
    if not html_path.is_file():
        return JSONResponse(
            {"message": "Quick call page not found.", "status": "error"},
            status_code=404,
        )
    return FileResponse(html_path, media_type="text/html")


# ── HTTP: Command Cockpit Dashboard ──────────────────────────────────


@app.get("/dashboard")
async def dashboard_page():
    """Serve the Command Cockpit single-page dashboard."""
    html_path = Path(__file__).resolve().parent / "static" / "dashboard.html"
    if not html_path.is_file():
        return JSONResponse({"error": "Dashboard page not found"}, status_code=404)
    return FileResponse(html_path, media_type="text/html")


# ── Helpers ──────────────────────────────────────────────────────────

async def _handle_disconnect(
    transcript_parts: list[str],
    conversation_id: str = "",
    channel: str = "inbound_call",
    phone_number: str = "",
    crm_user_id: str = "",
    language: str = "",
) -> None:
    """
    Post-call handler: save transcript, extract lead data, link to lead, and run sentiment scoring.

    ``conversation_id`` is handed down from the WebSocket handler, which takes it
    from ``app.crm.session`` when the stream starts. It is the only moment the id
    is available — the session is removed from the registry as the call ends.

    ``channel`` must be passed by callers that handle outbound calls. This
    function used to hardcode "inbound_call", so every outbound call was logged
    as inbound — which is why the dashboard's outbound filter and icon existed
    but never matched anything.

    ``phone_number`` is the carrier-supplied caller number. Before it was
    threaded through, this handler read a phone from the LLM extraction — which
    never asks for one — so every inbound call created a lead keyed on the empty
    string and sentiment could not be attached to a lead at all (blocker B6).

    ``crm_user_id`` is the Salesforce user the call was linked to mid-call, if it
    was. Passing it through keeps the conversation row and the lead consistent
    with the CRM.
    """
    transcript = " ".join(transcript_parts)

    if not transcript.strip():
        return

    # ── Step 1: Extract lead data from transcript ──────────────────
    extracted_lead = None
    try:
        from app.database import extract_lead_from_transcript
        extracted_lead = await extract_lead_from_transcript(transcript)
    except Exception:
        logger.exception("Lead extraction failed (non-fatal)")

    # ── Step 2: Resolve lead_id ────────────────────────────────────
    #
    # The carrier number is the identity to trust: it comes from Twilio, where
    # the extraction below is whatever the caller said out loud. The LLM branch
    # is kept for the channels that have no carrier number (the browser call
    # page) and because it also carries the name/email/program it heard.
    resolved_lead_id = ""
    resolved_phone = ""
    extracted_name = (extracted_lead or {}).get("name", "")
    extracted_email = (extracted_lead or {}).get("email", "")
    extracted_program = (extracted_lead or {}).get("program", "")

    from app.leads.models import get_lead_by_phone, upsert_lead_by_phone

    if phone_number:
        lead = await get_lead_by_phone(phone_number)
        if not lead:
            lead = await upsert_lead_by_phone(
                phone_number=phone_number,
                name=extracted_name,
                email=extracted_email,
                program_interest=extracted_program,
                source=channel,
            )
        if lead:
            resolved_lead_id = lead["id"]
            resolved_phone = phone_number

    if not resolved_lead_id and extracted_lead:
        # Fallback for callers with no carrier number: whatever the extraction
        # heard, then a name match as the last resort.
        phone = extracted_lead.get("phone", "") or extracted_lead.get("phone_number", "")
        if phone:
            lead = await get_lead_by_phone(phone)
            if not lead:
                lead = await upsert_lead_by_phone(
                    phone_number=phone,
                    name=extracted_name,
                    email=extracted_email,
                    program_interest=extracted_program,
                    source=channel,
                )
            if lead:
                resolved_lead_id = lead["id"]
                resolved_phone = phone
        if not resolved_lead_id and extracted_name:
            from app.leads.models import list_leads
            leads = await list_leads(search=extracted_name, limit=1)
            if leads:
                resolved_lead_id = leads[0]["id"]
                resolved_phone = leads[0].get("phone_number", "") or resolved_phone

    # Persist the CRM link against the lead. Mid-call the linker had no lead row
    # to write to (it does not exist until this point), so this is where the
    # association becomes durable — and what makes the next call short-circuit.
    if resolved_lead_id and crm_user_id:
        try:
            from app.leads.models import set_lead_crm_user_id

            await set_lead_crm_user_id(resolved_lead_id, crm_user_id)
        except Exception:
            logger.exception("Could not persist the CRM link on the call's lead")

    # ── Step 3: Save to legacy table ───────────────────────────────
    try:
        from app.pipeline import post_call_handler

        saved = await post_call_handler(transcript=transcript, phone_number=resolved_phone)
        if saved:
            logger.info(f"Lead saved ({len(transcript)} chars transcript)")
        else:
            logger.info("Lead save skipped (database unavailable or extraction failed)")
    except Exception:
        logger.exception("post_call_handler failed")

    # ── Step 4: Log to leads subsystem (with resolved lead_id) ────
    try:
        from app.leads.service import handle_post_interaction

        await handle_post_interaction(
            phone_number=resolved_phone,
            transcript=transcript,
            channel=channel,
            conversation_id=conversation_id,
            crm_user_id=crm_user_id,
            language=language,
        )
    except Exception:
        logger.exception("New leads-system logging failed (non-fatal)")

    # ── Step 5: Run sentiment analysis linked to lead ──────────────
    try:
        from app.sentiment.scorer import score_transcript

        await score_transcript(
            transcript=transcript,
            lead_id=resolved_lead_id,
        )
        if resolved_lead_id:
            logger.info(
                f"Sentiment scored + persisted for lead {resolved_lead_id} "
                f"({len(transcript)} chars)"
            )
        else:
            logger.info(f"Sentiment scored for call transcript ({len(transcript)} chars)")
    except Exception:
        logger.exception("Sentiment scoring failed (non-fatal)")
