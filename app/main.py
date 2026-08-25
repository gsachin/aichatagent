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
import time as _time
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_api")


# ── Startup / shutdown ───────────────────────────────────────────────

_db_available = False
_outbound_worker = None
_follow_up_scheduler = None

# AEC workaround: Twilio <Stream> has no echo-cancellation attribute, so
# while the assistant's TTS is playing we drop incoming caller audio —
# it is mostly the caller's mic re-capturing our own speech ("Listen to
# her again" artifacts). Set MUTE_STT_DURING_TTS=0 to disable.
MUTE_STT_DURING_TTS = os.environ.get("MUTE_STT_DURING_TTS", "1") == "1"


from contextlib import asynccontextmanager

from app.tunnel import resolve_tunnel_host


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
async def lifespan(app_instance):
    """Initialize and cleanup resources."""
    global _db_available, _outbound_worker, _follow_up_scheduler
    # Startup
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

    # Pre-warm RAG and Whisper so first voice note doesn't timeout
    try:
        import asyncio as _asyncio

        def _warmup():
            # Pre-load ChromaDB vector store
            from app.rag import get_vector_store

            vs = get_vector_store()
            if vs:
                logger.info("ChromaDB vector store pre-warmed")
            # Pre-load the embedding model so the first RAG query doesn't
            # pay the cold-start cost (HF config checks + model load)
            from app.llm_backend import get_embedding_function, provider_name

            if provider_name() == "mlx":
                get_embedding_function()(["warmup"])
                logger.info("Embedding model pre-warmed")
            # Pre-load Whisper model
            _get_stt_model()
            logger.info("Whisper model pre-warmed")

        await _asyncio.to_thread(_warmup)
    except Exception as e:
        logger.warning(f"Warmup skipped: {e}")

    # Start outbound call worker
    try:
        from app.outbound.caller import OutboundCallWorker
        from app.config import settings

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
        from app.config import settings

        _follow_up_scheduler = FollowUpScheduler(
            poll_interval=settings.FOLLOW_UP_POLL_INTERVAL
        )
        await _follow_up_scheduler.start()
        logger.info("Follow-up scheduler started")
    except Exception as e:
        logger.warning(f"Follow-up scheduler failed to start: {e}")
        _follow_up_scheduler = None

    yield
    # Shutdown
    if _outbound_worker:
        _outbound_worker.stop()
    if _follow_up_scheduler:
        await _follow_up_scheduler.stop()
    logger.info("Server shutting down")


app = FastAPI(title="University Admissions Voice Assistant", lifespan=lifespan)

# Serve static files (CSS, JS, audio) for dashboard + voice client
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── Shared helpers ────────────────────────────────────────────────────


# ── TwiML template ───────────────────────────────────────────────────

TWIML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/ws/twilio" />
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back or try our WhatsApp channel for immediate assistance.</Say>
</Response>"""

# IVR menu — shown before connecting to AI
TWIML_IVR_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" timeout="3" action="/twilio/voice/connect" method="GET">
        <Say voice="Polly.Joanna">
            Welcome to the Meridian University Admissions helpline.
            Press 1 for Undergraduate programs.
            Press 2 for Postgraduate programs.
            Press 3 for tuition and fees information.
            Press 4 to speak with our AI admissions assistant.
            Or, simply start speaking to ask any question.
        </Say>
    </Gather>
    <Say voice="Polly.Joanna">I didn't receive any input. Connecting you to the AI assistant now.</Say>
    <Connect>
        <Stream url="wss://{host}/ws/twilio" />
    </Connect>
    <Say voice="Polly.Joanna">Sorry, the connection was interrupted. Please call back later.</Say>
</Response>"""


# ── u-law conversion utilities ───────────────────────────────────────

def ulaw_to_pcm(ulaw_bytes: bytes) -> bytes:
    """Convert 8 kHz u-law bytes to 16-bit linear PCM bytes."""
    import audioop
    return audioop.ulaw2lin(ulaw_bytes, 2)


def pcm_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit linear PCM bytes to 8 kHz u-law bytes."""
    import audioop
    return audioop.lin2ulaw(pcm_bytes, 2)


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

                answer = await test_pipeline_with_text(query)

                await websocket.send_text(json.dumps({
                    "query": query,
                    "answer": answer,
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

    await websocket.accept()
    logger.info("WS /ws/twilio: inbound call connected (AI pipeline active)")

    stream_sid: str | None = None
    call_sid: str = ""
    tts_playing: bool = False
    transcript_parts: list[str] = []
    session = VoiceCallSession()  # direction defaults to "inbound"

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
                session.call_id = stream_sid
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "inbound",
                })

                # Send an initial AI greeting via TTS
                try:
                    from app.voice_handler import generate_ulaw_greeting

                    greeting = (
                        "Hi, I'm the admissions assistant. "
                        "Ask me anything about Meridian University programs, "
                        "tuition fees, or how to apply."
                    )
                    chunks = generate_ulaw_greeting(greeting)
                    logger.info(f"AI greeting: {len(chunks)} chunks")
                    for chunk in chunks:
                        out_payload = base64.b64encode(chunk).decode("ascii")
                        response = json.dumps({
                            "event": "media",
                            "streamSid": stream_sid or "",
                            "media": {"payload": out_payload},
                        })
                        if not await _ws_send(websocket, response):
                            raise WebSocketDisconnect(code=1000)
                    logger.info("AI greeting sent via TTS")
                except WebSocketDisconnect:
                    raise  # stream is dead — exit the handler cleanly
                except Exception:
                    logger.exception("Failed to send AI greeting (non-fatal)")

            elif event == "media":
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    continue

                # AEC workaround: drop caller audio while our TTS is
                # playing (it is mostly the mic re-capturing our own speech).
                # Logged as BARGE_IN_DETECTED — pre-Wave-1 the system cannot
                # cancel TTS playback, only shield STT from the bleed.
                if MUTE_STT_DURING_TTS and tts_playing:
                    session.log_event("BARGE_IN_DETECTED")
                    session.reset_utterance()
                    continue

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    # Process the utterance through the full AI pipeline
                    try:
                        tts_chunks, dialogue, end_call = await session.process_utterance()
                    except Exception:
                        logger.exception("VoiceCall: pipeline failed")
                        tts_chunks, dialogue, end_call = [], "", False

                    # Save transcript for post-call lead extraction
                    if dialogue:
                        transcript_parts.append(dialogue)
                        # Push to SSE for live dashboard
                        if stream_sid and stream_sid in _active_call_sids:
                            _active_call_sids[stream_sid]["transcript"].append(dialogue)
                            _push_transcript_event("transcript", stream_sid, {
                                "dialogue": dialogue,
                            })

                    # Send TTS audio chunks back through the WebSocket
                    session.log_event("AGENT_SPEECH_STARTED", chunks=len(tts_chunks))
                    tts_playing = True
                    try:
                        for chunk in tts_chunks:
                            out_payload = base64.b64encode(chunk).decode("ascii")
                            response = json.dumps({
                                "event": "media",
                                "streamSid": stream_sid or "",
                                "media": {"payload": out_payload},
                            })
                            if not await _ws_send(websocket, response):
                                raise WebSocketDisconnect(code=1000)
                    finally:
                        tts_playing = False
                    session.log_event("AGENT_SPEECH_STOPPED")

                    # Deterministic hangup: caller sign-off detected → end call
                    if end_call:
                        logger.info(f"WS /ws/twilio: ending call after sign-off ({stream_sid})")
                        await _hangup_twilio_call(call_sid)
                        break

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
        # <Say> into the caller's ear.
        await _hangup_twilio_call(call_sid)
    except Exception:
        logger.exception("WS /ws/twilio: unexpected error")
        await _hangup_twilio_call(call_sid)
    finally:
        # Clear the active-call entry on ANY exit path — prevents phantom
        # "active calls" when Twilio's stop event never arrives.
        if stream_sid and stream_sid in _active_call_sids:
            entry = _active_call_sids.pop(stream_sid, None)
            if entry and "ended_at" not in entry:
                _push_transcript_event("call_ended", stream_sid)
        await _handle_disconnect(transcript_parts)
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

    await websocket.accept()
    logger.info("WS /ws/twilio-outbound: outbound call connected (AI pipeline active)")

    stream_sid: str | None = None
    call_sid: str = ""
    tts_playing: bool = False
    transcript_parts: list[str] = []
    session = VoiceCallSession(direction="outbound")

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
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "outbound",
                })

                # Send an initial AI greeting via TTS — identify caller + reason
                # per the voice system prompt's outbound section.
                try:
                    from app.voice_handler import generate_ulaw_greeting
                    from app.voice_system_prompt import AGENT_NAME, COMPANY_NAME

                    greeting = (
                        f"Hi, this is {AGENT_NAME} calling from {COMPANY_NAME} "
                        f"Admissions. Do you have a moment to talk about our "
                        f"programs, tuition fees, or how to apply?"
                    )
                    chunks = generate_ulaw_greeting(greeting)
                    logger.info(
                        f"AI greeting: {len(chunks)} chunks"
                    )
                    for chunk in chunks:
                        out_payload = base64.b64encode(chunk).decode("ascii")
                        response = json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid or "",
                                "media": {"payload": out_payload},
                            }
                        )
                        if not await _ws_send(websocket, response):
                            raise WebSocketDisconnect(code=1000)
                    logger.info("AI greeting sent via TTS")
                except WebSocketDisconnect:
                    raise  # stream is dead — exit the handler cleanly
                except Exception:
                    logger.exception("Failed to send AI greeting (non-fatal)")

            elif event == "media":
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    continue

                # AEC workaround: drop caller audio while our TTS is
                # playing (it is mostly the mic re-capturing our own speech).
                # Logged as BARGE_IN_DETECTED — pre-Wave-1 the system cannot
                # cancel TTS playback, only shield STT from the bleed.
                if MUTE_STT_DURING_TTS and tts_playing:
                    session.log_event("BARGE_IN_DETECTED")
                    session.reset_utterance()
                    continue

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    # Process the utterance through the full AI pipeline
                    try:
                        tts_chunks, dialogue, end_call = await session.process_utterance()
                    except Exception:
                        logger.exception("VoiceCall: pipeline failed")
                        tts_chunks, dialogue, end_call = [], "", False

                    # Save transcript for post-call lead extraction
                    if dialogue:
                        transcript_parts.append(dialogue)
                        # Push to SSE for live dashboard
                        if stream_sid and stream_sid in _active_call_sids:
                            _active_call_sids[stream_sid]["transcript"].append(dialogue)
                            _push_transcript_event("transcript", stream_sid, {
                                "dialogue": dialogue,
                            })

                    # Send TTS audio chunks back through the WebSocket
                    session.log_event("AGENT_SPEECH_STARTED", chunks=len(tts_chunks))
                    tts_playing = True
                    try:
                        for chunk in tts_chunks:
                            out_payload = base64.b64encode(chunk).decode("ascii")
                            response = json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid or "",
                                    "media": {"payload": out_payload},
                                }
                            )
                            if not await _ws_send(websocket, response):
                                raise WebSocketDisconnect(code=1000)
                    finally:
                        tts_playing = False
                    session.log_event("AGENT_SPEECH_STOPPED")

                    # Deterministic hangup: caller sign-off detected → end call
                    if end_call:
                        logger.info(f"WS /ws/twilio-outbound: ending call after sign-off ({stream_sid})")
                        await _hangup_twilio_call(call_sid)
                        break

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
        await _hangup_twilio_call(call_sid)
    except Exception:
        logger.exception("WS /ws/twilio-outbound: unexpected error")
        await _hangup_twilio_call(call_sid)
    finally:
        # Clear the active-call entry on ANY exit path — prevents phantom
        # "active calls" when Twilio's stop event never arrives.
        if stream_sid and stream_sid in _active_call_sids:
            entry = _active_call_sids.pop(stream_sid, None)
            if entry and "ended_at" not in entry:
                _push_transcript_event("call_ended", stream_sid)
        await _handle_disconnect(transcript_parts)


# ── HTTP: Outbound call voice TwiML (fetched by Twilio) ──────────────

@app.api_route("/twilio/outbound-voice", methods=["GET", "POST"])
async def twilio_outbound_voice_webhook():
    """
    Twilio fetches this URL when an outbound call is answered.
    Returns TwiML that connects to the Media Streams WebSocket.

    Accepts both GET and POST because Twilio may use either method
    depending on how the outbound call is initiated.
    """
    host = resolve_tunnel_host()

    from app.outbound.twiml import outbound_connect_twiml

    twiml = outbound_connect_twiml(host)
    logger.info(f"/twilio/outbound-voice: serving TwiML with host={host}")
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
async def twilio_voice_webhook():
    """
    Twilio voice webhook — serves IVR menu first.
    After the caller presses a digit (or timeout), connects to /ws/twilio.
    """
    host = resolve_tunnel_host()
    twiml = TWIML_IVR_TEMPLATE.format(host=host)
    logger.info(f"/twilio/voice: serving IVR menu with host={host}")
    return Response(content=twiml, media_type="application/xml")


@app.get("/twilio/voice/connect")
async def twilio_voice_connect(Digits: str = ""):
    """
    Called by Twilio after IVR <Gather> completes.
    Connects the caller to the AI WebSocket stream.
    """
    host = resolve_tunnel_host()
    logger.info(f"/twilio/voice/connect: digit={Digits}, host={host}")
    twiml = TWIML_TEMPLATE.format(host=host)
    return Response(content=twiml, media_type="application/xml")


# ── Shared STT model (voice pipeline warmup) ───────────────────────────

# Reuse the shared STT model from voice_handler (faster-whisper on CUDA)


def _get_stt_model():
    """Reuse the shared faster-whisper model from voice_handler."""
    from app.voice_handler import _get_stt_model as _vh_stt_model
    return _vh_stt_model()


# ── WhatsApp conversation logger ──────────────────────────────────────

async def _log_whatsapp_conversation(phone_number: str, transcript: str):
    """
    Log a WhatsApp interaction to the new leads + conversations tables.

    Safe to call as a background task — failures are logged but never
    propagated, so they won't affect the Twilio response.
    """
    try:
        from app.leads.service import log_interaction

        await log_interaction(
            phone_number=phone_number,
            channel="whatsapp",
            transcript=transcript,
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
# Longer/more specific aliases first — first substring match wins.

_MERIDIAN_PROGRAMS = {
    # Compound / specific names first — first substring match wins
    "master of business administration": "MBA",
    "ai & machine learning": "B.Tech AI & Machine Learning",
    "computer science": "B.Tech Computer Science",
    "information technology": "B.Tech Information Technology",
    "computer applications": "BCA",
    "business administration": "BBA",
    # Short aliases
    "mba": "MBA",
    "mca": "MCA",
    "m.tech": "M.Tech",
    "b.tech": "B.Tech",
    "bca": "BCA",
    "bba": "BBA",
    "b.com": "B.Com",
    "b.sc": "B.Sc",
    "b.a": "BA",
}


def _detect_meridian_program(msg_lower: str) -> str:
    """Return the canonical Meridian program name for a message, or ''."""
    msg_lower = msg_lower.lower()
    for alias, canonical in _MERIDIAN_PROGRAMS.items():
        if alias in msg_lower:
            return canonical
    return ""


# Words that mark a message as a knowledge question even when it's short
# and has no "?" — e.g. "fees", "hostel". Kept narrow so admission intent
# ("I want to take admission") is never misrouted to RAG.
_KB_QUESTION_KEYWORDS = (
    "fees", "tuition", "fee structure", "scholarship", "courses",
    "programs", "hostel", "placement", "eligibility", "duration",
    "deadline", "entrance", "intake", "how much", "refund",
)


async def _whatsapp_chat_rag(question: str) -> str:
    """
    RAG answer for WhatsApp text chat using the chat-oriented prompt
    (mode="chat" — same Markdown SYSTEM_PROMPT as the Streamlit chat).
    """
    from app.pipeline import run_rag_query_sync

    try:
        answer = await _asyncio.to_thread(run_rag_query_sync, question, mode="chat")
        if answer:
            # Escape for the TwiML XML payload
            return answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    except Exception:
        logger.exception("WhatsApp RAG failed")
    return "Sorry, I couldn't process your question. Please try again."


async def _whatsapp_offer_on_done(lead_id: str, lead_name: str) -> str:
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

        offer = await generate_and_send_offer(lead_id)
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
    strong = [
        "i want to take admission",
        "i want admission",
        "take the admission",
        "take admission",
        "want to take admission",
        "take addmission",  # common typo
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
        "admission", "enroll", "apply", "i am ready",
        "let's proceed", "go ahead", "i'm interested",
        "join the program", "confirm my", "i want to study",
        "proceed with", "i'd like to apply",
    ]
    has_medium = any(kw in msg_lower for kw in medium)
    if not has_medium:
        return False, ""

    # LLM confirmation
    try:
        from app.llm_backend import chat as backend_chat, default_model, small_task_num_ctx

        raw = backend_chat(
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
            num_ctx=small_task_num_ctx(1024),
        ).strip().lower()
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
            return result
        return None
    except Exception:
        logger.exception("_handle_offer_response failed")
        return None


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

    # Greetings and knowledge questions are answered directly — never
    # mistaken for profile info (e.g. "fees" must not become a name).
    if msg_lower in ("hi", "hello", "hey", "hii", "hi there", "namaste"):
        answer = "Hello! 👋 I'm the Meridian University admissions assistant. What's your name?"
    elif "?" in Body or any(k in msg_lower for k in _KB_QUESTION_KEYWORDS):
        answer = await _whatsapp_chat_rag(Body)
    # State machine for collecting missing info
    elif not lead_name and is_name_like and not has_email and msg_lower not in ("done", "finish", "finished"):
        # User likely provided their name
        if lead_id:
            await update_lead(lead_id, name=Body.strip())
        lead_name = Body.strip()  # Refresh local
        if not lead_email:
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
        answer = "Hi! Before I help you, could you tell me your name?"
    elif not lead_email:
        # Missing email — ask for it
        answer = f"Hi {lead_name}! Could you share your email address? I'll use it to send you program details and follow up later."
    else:
        # All info present — check admission intent first, then ACCEPT/DECLINE

        # ── Admission intent detection ──────────────────────────
        is_interested, detected_prog = await _detect_admission_intent_whatsapp(msg_lower)
        if is_interested:
            if lead_id:
                await update_lead(lead_id, status="in_progress")
            if detected_prog and not lead_program:
                if lead_id:
                    await update_lead(lead_id, program_interest=detected_prog)
                lead_program = detected_prog
            if not lead_program:
                answer = "Which program are you interested in? (e.g., B.Tech Computer Science, MBA, BCA)"
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
            if lead_program:
                answer = (
                    f"Great {lead_name}! You're confirmed for *{lead_program}*. "
                    f"To proceed with admission, say: 'I want to take admission'"
                )
            else:
                answer = "Great! Which program are you interested in? (e.g., B.Tech Computer Science, MBA, BCA)"
        elif msg_lower in ("no", "nope", "wrong", "change"):
            answer = "No problem! What would you like to update? Your name, email, or program interest?"
        elif msg_lower in ("done", "finish", "finished"):
            # All documents uploaded — generate the offer letter now.
            if not lead_program:
                answer = "Which program are you interested in? (e.g., B.Tech Computer Science, MBA, BCA)"
            else:
                answer = await _whatsapp_offer_on_done(lead_id, lead_name)
        # ── FIX: Capture program name from short replies (Critical bug fix) ──
        elif lead_name and lead_email and not lead_program and len(msg_lower.split()) <= 3:
            detected = _detect_meridian_program(msg_lower)
            if detected:
                if lead_id:
                    await update_lead(lead_id, program_interest=detected)
                lead_program = detected
                answer = (
                    f"**{detected}** — great choice! 🎓\n\n"
                    f"To proceed with your admission, say: 'I want to take admission' "
                    f"and I'll guide you through the document upload process."
                )
            else:
                # Short message that isn't a program — treat it as a question.
                answer = await _whatsapp_chat_rag(Body)
        else:
            # Everything else goes to RAG — same knowledge answers as Streamlit.
            answer = await _whatsapp_chat_rag(Body)

    # Safety: fallback answer
    try:
        _ = answer
    except NameError:
        answer = f"I have you as {lead_name or 'there'}. How can I help with Meridian admissions?"

    # WhatsApp renders plain text — strip LLM Markdown before sending.
    answer = _strip_markdown(answer)

    # Log conversation
    background_tasks.add_task(
        _log_whatsapp_conversation,
        phone_number=From,
        transcript=f"User: {Body}\nAssistant: {answer}",
    )

    twiml = WHATSAPP_TWIML_TEMPLATE.format(answer=answer)
    logger.info(f"WhatsApp response ({len(answer)} chars): {answer[:80]}...")
    return Response(content=twiml, media_type="application/xml")


# ── REST API: Dashboard data ──────────────────────────────────────────


@app.post("/api/leads")
async def api_create_lead(req: Request):
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
async def api_update_lead(lead_id: str, req: Request):
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
        return result
    return JSONResponse({"error": "Lead not found"}, status_code=404)


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

    Body: {"phone_number": "+91...", "name": "Optional"}
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    phone = body.get("phone_number", "").strip()
    if not phone:
        return JSONResponse({"error": "phone_number is required"}, status_code=400)

    from app.leads.models import add_to_call_queue, upsert_lead_by_phone

    lead = await upsert_lead_by_phone(
        phone_number=phone,
        name=body.get("name", ""),
        source="quick-call",
    )
    if not lead:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    queue_entry = await add_to_call_queue(lead_id=lead["id"])
    if not queue_entry:
        return JSONResponse({"error": "Failed to queue call"}, status_code=503)

    return {
        "lead": lead,
        "call_queue": queue_entry,
        "tunnel_host": resolve_tunnel_host(),
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
        import os as _os

        DATABASE_URL = _os.environ.get("DATABASE_URL", "")
        DB_HOST = _os.environ.get("DB_HOST", "localhost")
        DB_PORT = _os.environ.get("DB_PORT", "5432")
        DB_NAME = _os.environ.get("DB_NAME", "admissions")
        DB_USER = _os.environ.get("DB_USER", "postgres")
        DB_PASSWORD = _os.environ.get("DB_PASSWORD", "")

        if DATABASE_URL:
            conn_str = DATABASE_URL
        else:
            conn_str = (
                f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
                f"user={DB_USER} password={DB_PASSWORD}"
            )

        conn = psycopg2.connect(conn_str)
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


@app.get("/api/calls/live")
async def api_calls_live(stream: bool = False):
    """
    Get active calls with transcript snippets.
    Set ?stream=true for SSE (Server-Sent Events) real-time streaming.
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
        "lead_id": "optional-uuid (if already known)"
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

    if not transcript:
        return JSONResponse({"error": "transcript is required"}, status_code=422)

    try:
        from app.leads.service import log_interaction

        conv = await log_interaction(
            phone_number=phone,
            channel=channel,
            transcript=transcript,
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
):
    """Upload a document for a lead. Auto-triggers offer letter if conditions met."""
    from app.leads.models import get_lead
    from app.offers.models import add_document
    from pathlib import Path
    import uuid
    import os as _os

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    # Sanitize filename
    safe_name = Path(file.filename or "upload").name
    safe_name = safe_name.replace(" ", "_")

    # Determine storage path
    data_dir = Path(_os.environ.get("DATA_DIR", "data"))
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

    # Auto-trigger offer letter if the lead is fully ready
    offer_result = None
    from app.offers.service import evaluate_offer_readiness, generate_and_send_offer
    readiness = await evaluate_offer_readiness(lead_id)
    if readiness["ready"]:
        offer_result = await generate_and_send_offer(lead_id)

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
    """Serve the browser-based voice client page."""
    html_path = Path(__file__).resolve().parent / "static" / "voice_client.html"
    if not html_path.is_file():
        return JSONResponse(
            {"message": "Voice client page not found.", "status": "error"},
            status_code=404,
        )
    return FileResponse(html_path, media_type="text/html")


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

async def _handle_disconnect(transcript_parts: list[str]) -> None:
    """Post-call handler: save transcript, extract lead data, link to lead, and run sentiment scoring."""
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

    # ── Step 2: Resolve lead_id from extracted data ────────────────
    resolved_lead_id = ""
    resolved_phone = ""
    if extracted_lead:
        from app.leads.models import get_lead_by_phone, upsert_lead_by_phone
        # Try phone first
        phone = extracted_lead.get("phone", "") or extracted_lead.get("phone_number", "")
        if phone:
            lead = await get_lead_by_phone(phone)
            if not lead:
                lead = await upsert_lead_by_phone(
                    phone_number=phone,
                    name=extracted_lead.get("name", ""),
                    email=extracted_lead.get("email", ""),
                    program_interest=extracted_lead.get("program", ""),
                    source="inbound_call",
                )
            if lead:
                resolved_lead_id = lead["id"]
                resolved_phone = phone
        # Fallback: try name match
        if not resolved_lead_id and extracted_lead.get("name"):
            from app.leads.models import list_leads
            leads = await list_leads(search=extracted_lead["name"], limit=1)
            if leads:
                resolved_lead_id = leads[0]["id"]

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
            channel="inbound_call",
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
