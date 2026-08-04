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
    POST /twilio/whatsapp        — WhatsApp webhook (text + voice notes)
    GET  /audio/{filename}       — Serve generated TTS audio
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
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_api")


def _resolve_tunnel_host():
    import os as _os
    from pathlib import Path as _Path
    host = _os.environ.get("TUNNEL_HOST", "")
    if host:
        return host
    tf = _Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
    if tf.is_file():
        return tf.read_text().strip()
    return _os.environ.get("NGROK_HOST", "localhost:8000")


# ── Startup / shutdown ───────────────────────────────────────────────

_db_available = False
_outbound_worker = None
_follow_up_scheduler = None


from contextlib import asynccontextmanager


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

    # Pre-warm RAG and Whisper so first voice note doesn't timeout
    try:
        import asyncio as _asyncio

        def _warmup():
            # Pre-load ChromaDB vector store
            from app.rag import get_vector_store

            vs = get_vector_store()
            if vs:
                logger.info("ChromaDB vector store pre-warmed")
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


def _resolve_tunnel_host() -> str:
    """
    Resolve the public tunnel hostname for Twilio callbacks.

    Checks, in order: TUNNEL_HOST env var, .whatsapp_tunnel file,
    NGROK_HOST env var (legacy), then falls back to localhost:8000.
    Returns just the hostname (no scheme), e.g. "foo.trycloudflare.com".
    """
    tunnel_host = os.environ.get("TUNNEL_HOST", "")
    if tunnel_host:
        return tunnel_host

    tunnel_file = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
    if tunnel_file.is_file():
        return tunnel_file.read_text().strip()

    return os.environ.get("NGROK_HOST", "localhost:8000")


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
            Welcome to the University Admissions helpline.
            Press 1 for UMD programs.
            Press 2 for FDU programs.
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
    transcript_parts: list[str] = []
    session = VoiceCallSession()

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
                stream_sid = msg.get("streamSid", msg.get("start", {}).get("streamSid", ""))
                logger.info(f"WS /ws/twilio: stream started — {stream_sid}")

                # Track for SSE live-call monitor
                _active_call_sids[stream_sid] = {
                    "call_sid": stream_sid,
                    "direction": "inbound",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "transcript": [],
                }
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "inbound",
                })

                # Send an initial AI greeting via TTS
                try:
                    from app.voice_handler import generate_ulaw_greeting

                    greeting = (
                        "Hi, I'm the admissions assistant. "
                        "Ask me anything about UMD or FDU programs, "
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
                        await websocket.send_text(response)
                    logger.info("AI greeting sent via TTS")
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

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    # Process the utterance through the full AI pipeline
                    try:
                        tts_chunks, dialogue = await session.process_utterance()
                    except Exception:
                        logger.exception("VoiceCall: pipeline failed")
                        tts_chunks, dialogue = [], ""

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
                    for chunk in tts_chunks:
                        out_payload = base64.b64encode(chunk).decode("ascii")
                        response = json.dumps({
                            "event": "media",
                            "streamSid": stream_sid or "",
                            "media": {"payload": out_payload},
                        })
                        await websocket.send_text(response)

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
    except Exception:
        logger.exception("WS /ws/twilio: unexpected error")
    finally:
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
    transcript_parts: list[str] = []
    session = VoiceCallSession()

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
                stream_sid = msg.get(
                    "streamSid", msg.get("start", {}).get("streamSid", "")
                )
                logger.info(f"WS /ws/twilio-outbound: stream started — {stream_sid}")

                # Track for SSE live-call monitor
                _active_call_sids[stream_sid] = {
                    "call_sid": stream_sid,
                    "direction": "outbound",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "transcript": [],
                }
                _push_transcript_event("call_started", stream_sid, {
                    "direction": "outbound",
                })

                # Send an initial AI greeting via TTS
                try:
                    from app.voice_handler import generate_ulaw_greeting

                    greeting = (
                        "Hi, I'm the admissions assistant. "
                        "Ask me anything about UMD or FDU programs, "
                        "tuition fees, or how to apply."
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
                        await websocket.send_text(response)
                    logger.info("AI greeting sent via TTS")
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

                # Feed audio to VAD + utterance detector
                utterance_ready = session.feed_audio(ulaw_bytes)

                if utterance_ready:
                    # Process the utterance through the full AI pipeline
                    try:
                        tts_chunks, dialogue = await session.process_utterance()
                    except Exception:
                        logger.exception("VoiceCall: pipeline failed")
                        tts_chunks, dialogue = [], ""

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
                    for chunk in tts_chunks:
                        out_payload = base64.b64encode(chunk).decode("ascii")
                        response = json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid or "",
                                "media": {"payload": out_payload},
                            }
                        )
                        await websocket.send_text(response)

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
    except Exception:
        logger.exception("WS /ws/twilio-outbound: unexpected error")
    finally:
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
    host = _resolve_tunnel_host()

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

        # 1. Update the call_queue entry by CallSid
        queue_entry = await get_call_queue_by_sid(CallSid)
        if queue_entry:
            await update_call_queue_status(
                queue_entry["id"],
                "completed" if CallStatus == "completed" else "failed",
                error_message="" if CallStatus == "completed" else f"Twilio status: {CallStatus}",
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
    host = _resolve_tunnel_host()
    twiml = TWIML_IVR_TEMPLATE.format(host=host)
    logger.info(f"/twilio/voice: serving IVR menu with host={host}")
    return Response(content=twiml, media_type="application/xml")


@app.get("/twilio/voice/connect")
async def twilio_voice_connect(Digits: str = ""):
    """
    Called by Twilio after IVR <Gather> completes.
    Connects the caller to the AI WebSocket stream.
    """
    host = _resolve_tunnel_host()
    logger.info(f"/twilio/voice/connect: digit={Digits}, host={host}")
    twiml = TWIML_TEMPLATE.format(host=host)
    return Response(content=twiml, media_type="application/xml")


# ── WhatsApp voice transcription ───────────────────────────────────────

# Reuse the shared STT model from voice_handler (faster-whisper on CUDA)


def _get_stt_model():
    """Reuse the shared faster-whisper model from voice_handler."""
    from app.voice_handler import _get_stt_model as _vh_stt_model
    return _vh_stt_model()


async def _transcribe_whatsapp_audio(media_url: str, content_type: str) -> str:
    """
    Download WhatsApp voice note from Twilio and transcribe via Whisper.
    WhatsApp sends audio as OGG/Opus or MP4/AAC — soundfile handles both.
    Returns transcribed text, or empty string on failure.
    """
    import asyncio as _asyncio
    import io as _io
    import tempfile
    import urllib.request

    try:
        # Download audio from Twilio
        logger.info(f"Downloading audio: {media_url[:80]}...")
        req = urllib.request.Request(media_url)
        # Twilio requires basic auth for media access
        from app.config import settings
        auth_str = base64.b64encode(
            f"{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {auth_str}")

        audio_bytes = await _asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=30).read()
        )
        logger.info(f"Downloaded {len(audio_bytes)} bytes of audio")

        # Convert to 16kHz mono PCM using soundfile (supports OGG/MP4/WAV)
        import soundfile as sf
        import numpy as np

        audio_np, orig_sr = sf.read(_io.BytesIO(audio_bytes))
        if audio_np.ndim > 1:
            audio_np = audio_np.mean(axis=1)  # Stereo → mono

        # Resample to 16kHz if needed (Whisper expects 16kHz)
        if orig_sr != 16000 and len(audio_np) > 0:
            from scipy.signal import resample
            target_len = int(len(audio_np) * 16000 / orig_sr)
            audio_np = resample(audio_np, target_len)

        logger.info(f"Audio: {len(audio_np)/16000:.1f}s @ 16kHz")

        # Transcribe with faster-whisper (numpy array, no PyAV needed)
        model = _get_stt_model()
        audio_float = audio_np.astype(np.float32) / 32768.0
        segments, info = model.transcribe(
            audio_float, language="en", beam_size=5, vad_filter=True
        )
        transcript = " ".join(seg.text.strip() for seg in segments)

        logger.info(f"Transcribed ({len(transcript)} chars): {transcript[:100]}...")
        return transcript

    except Exception as e:
        logger.exception(f"Voice transcription failed: {e}")
        return ""


# ── Async voice note processing ────────────────────────────────────────


def _send_whatsapp_message(
    to_number: str,
    from_number: str,
    body: str,
    media_url: str | None = None,
):
    """Send a WhatsApp message via Twilio REST API, optionally with media."""
    try:
        from twilio.rest import Client
        from app.config import settings

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        kwargs = {"from_": from_number, "body": body, "to": to_number}
        if media_url:
            kwargs["media_url"] = [media_url]

        msg = client.messages.create(**kwargs)
        extra = " + audio" if media_url else ""
        logger.info(f"Twilio message sent: {msg.sid} → {to_number}{extra}")
        return True
    except Exception as e:
        logger.exception(f"Twilio send failed: {e}")
        return False


async def _process_voice_note_async(
    media_url: str,
    content_type: str,
    from_number: str,
    to_number: str,
):
    """
    Background task: process a WhatsApp voice note end-to-end.
    1. Download + transcribe audio
    2. Run RAG pipeline
    3. Send answer via Twilio REST API
    """
    from app.pipeline import run_rag_query_sync

    # Step 1: Transcribe
    transcript = await _transcribe_whatsapp_audio(media_url, content_type)
    if not transcript:
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body="I couldn't understand the audio. Please try again or type your question.",
        )
        return

    # Step 2: RAG
    try:
        answer = await _asyncio.to_thread(run_rag_query_sync, transcript)
    except Exception:
        logger.exception("Async RAG failed")
        answer = None

    if not answer:
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body="Sorry, I couldn't process your question right now. Please try again.",
        )
        return

    # Step 3: Generate TTS audio
    audio_filename = await _asyncio.to_thread(_generate_tts_audio, answer)

    # Step 4: Resolve tunnel host
    tunnel_host = _resolve_tunnel_host()
    if tunnel_host and tunnel_host != "localhost:8000":
        logger.info(f"Tunnel host: {tunnel_host}")

    # Step 5: Send reply (text always; audio only if available)
    if audio_filename and tunnel_host:
        audio_url = f"https://{tunnel_host}/audio/{audio_filename}"
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body=answer,
            media_url=audio_url,
        )
        logger.info(f"Voice reply sent: text + audio ({audio_filename})")
    else:
        if not audio_filename:
            logger.warning("TTS audio generation failed — sending text-only reply")
        elif not tunnel_host:
            logger.warning("TUNNEL_HOST not set — sending text-only reply")
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body=answer,
        )
        logger.info("Voice reply sent: text-only")

    logger.info(f"Voice note processed: {from_number} ← {len(answer)} chars")

    # Log conversation to new leads subsystem
    try:
        from app.leads.service import log_interaction

        await log_interaction(
            phone_number=from_number,
            channel="whatsapp",
            transcript=f"User (voice): {transcript}\nAssistant: {answer}",
        )
    except Exception:
        logger.exception("Failed to log voice-note conversation (non-fatal)")


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

WHATSAPP_TWIML_VOICE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message><Body>{answer}</Body></Message>
    <Message><Media>{audio_url}</Media></Message>
</Response>"""

# Directory for serving generated TTS audio to Twilio
_AUDIO_DIR = Path(__file__).resolve().parent / "static" / "audio"
_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _generate_tts_audio(text: str) -> str | None:
    """
    Generate TTS audio via Kokoro, save as MP3 for WhatsApp compatibility.
    Returns filename (mp3), or None if TTS is unavailable or fails.
    """
    import io as _io
    import uuid
    import numpy as np

    try:
        from kokoro_onnx import Kokoro

        cache_dir = os.path.expanduser(r"~\.cache\pipecat\kokoro-onnx")
        kokoro = Kokoro(
            os.path.join(cache_dir, "kokoro-v1.0.onnx"),
            os.path.join(cache_dir, "voices-v1.0.bin"),
        )
        # Keep audio short for WhatsApp — max 300 chars
        tts_text = text[:300] if len(text) > 300 else text
        audio, sr = kokoro.create(tts_text, voice="af_heart", speed=1.0)

        # WhatsApp supports MP3 — use soundfile to encode directly
        import soundfile as sf
        buf = _io.BytesIO()
        sf.write(buf, audio, sr, format="MP3")
        buf.seek(0)

        filename = f"reply_{uuid.uuid4().hex[:8]}.mp3"
        filepath = _AUDIO_DIR / filename
        filepath.write_bytes(buf.read())
        logger.info(f"TTS audio saved: {filename} ({len(audio)/sr:.1f}s, {filepath.stat().st_size/1024:.0f} KB)")
        return filename

    except Exception as e:
        logger.exception(f"TTS generation failed: {e}")
        return None


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve generated TTS audio files for WhatsApp voice replies."""
    filepath = _AUDIO_DIR / filename
    if not filepath.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return FileResponse(filepath, media_type=media_type)


@app.post("/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    background_tasks: BackgroundTasks,
    Body: str = Form(default=""),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    From: str = Form(default=""),
    To: str = Form(default=""),
    WaId: str = Form(default=""),
):
    """
    Twilio WhatsApp webhook — receives incoming text OR voice messages.

    - Text: synchronous RAG reply (fits in Twilio 15s timeout)
    - Voice: returns acknowledgment immediately, processes async,
      sends reply via Twilio REST API

    Configure this URL in Twilio Console:
        https://<your-tunnel>/twilio/whatsapp
    """
    from app.pipeline import run_rag_query_sync

    # ── Handle voice notes — async processing ──────────────────
    if not Body.strip() and MediaUrl0.strip():
        logger.info(f"WhatsApp voice note from {From} ({MediaContentType0})")
        background_tasks.add_task(
            _process_voice_note_async,
            media_url=MediaUrl0,
            content_type=MediaContentType0,
            from_number=From,
            to_number=To,
        )
        twiml = WHATSAPP_TWIML_TEMPLATE.format(
            answer="🎤 Processing your voice note... you'll get a reply shortly."
        )
        return Response(content=twiml, media_type="application/xml")

    # ── Handle text ─────────────────────────────────────────────
    if not Body.strip():
        twiml = WHATSAPP_TWIML_TEMPLATE.format(
            answer="Hello! Send me a question about UMD or FDU admissions, or send a voice note."
        )
        return Response(content=twiml, media_type="application/xml")

    logger.info(f"WhatsApp from {From} (WaId={WaId}): {Body[:100]}")

    try:
        answer = await _asyncio.to_thread(run_rag_query_sync, Body)
    except Exception as e:
        logger.exception("WhatsApp RAG failed")
        answer = None

    if not answer:
        answer = "Sorry, I couldn't process your question right now. Please try again."

    answer = answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Log conversation to new leads subsystem
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
        "tunnel_host": _resolve_tunnel_host(),
    }


@app.get("/api/leads/{lead_id}/score")
async def api_lead_score(lead_id: str):
    """Get lead quality score (1-10) with breakdown and temperature."""
    from app.leads.models import get_lead, get_conversations
    from app.leads.service import calculate_lead_score

    lead = await get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    conversations = await get_conversations(lead_id=lead_id) or []
    score_data = calculate_lead_score(lead, conversations)
    return {"lead_id": lead_id, **score_data}


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
    }


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
    """Post-call handler: save transcript and extract lead data."""
    transcript = " ".join(transcript_parts)

    if not transcript.strip():
        return

    try:
        from app.pipeline import post_call_handler

        saved = await post_call_handler(transcript=transcript)
        if saved:
            logger.info(f"Lead saved ({len(transcript)} chars transcript)")
        else:
            logger.info("Lead save skipped (database unavailable or extraction failed)")
    except Exception:
        logger.exception("post_call_handler failed")

    # Also log to the new leads subsystem
    try:
        from app.leads.service import handle_post_interaction

        await handle_post_interaction(
            phone_number="",
            transcript=transcript,
            channel="inbound_call",
        )
    except Exception:
        logger.exception("New leads-system logging failed (non-fatal)")
