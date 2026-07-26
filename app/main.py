"""
FastAPI application — University Admissions Voice Assistant.

Transport-agnostic server supporting:
- WAV test harness (test_transport.py)
- Browser microphone page (voice_client.html)
- Twilio Media Streams (with real credentials)

Endpoints:
    GET  /              — health check + navigation
    GET  /voice         — browser mic page (voice_client.html)
    WS   /ws/voice      — raw PCM audio endpoint (pipeline-ready)
    WS   /ws/voice/text — text query through RAG + LLM pipeline
    GET  /twilio/voice  — TwiML response for inbound calls
    WS   /ws/twilio     — Twilio Media Streams (8 kHz u-law)

Run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_api")

# ── Startup / shutdown ───────────────────────────────────────────────

_db_available = False


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app_instance):
    """Initialize and cleanup resources."""
    global _db_available
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
    yield
    # Shutdown
    logger.info("Server shutting down")


app = FastAPI(title="University Admissions Voice Assistant", lifespan=lifespan)


# ── TwiML template ───────────────────────────────────────────────────

TWIML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/ws/twilio" />
    </Connect>
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
    Twilio Media Streams WebSocket endpoint.

    Audio flow:
      Twilio u-law -> decode to PCM -> pipeline -> encode to u-law -> Twilio
    """
    await websocket.accept()
    logger.info("WS /ws/twilio: Twilio client connected")

    stream_sid: str | None = None
    transcript_parts: list[str] = []

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

            elif event == "media":
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    continue

                # Twilio u-law -> PCM
                pcm_bytes = ulaw_to_pcm(ulaw_bytes)

                # Pipeline: PCM -> STT -> RAG -> LLM -> TTS -> PCM
                # Currently echoes back for transport validation
                out_pcm = pcm_bytes

                # PCM -> Twilio u-law
                out_ulaw = pcm_to_ulaw(out_pcm)
                out_payload = base64.b64encode(out_ulaw).decode("ascii")

                response = json.dumps({
                    "event": "media",
                    "streamSid": stream_sid or "",
                    "media": {"payload": out_payload},
                })
                await websocket.send_text(response)

            elif event == "stop":
                logger.info(f"WS /ws/twilio: stream stopped — {stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info("WS /ws/twilio: client disconnected")
        await _handle_disconnect(transcript_parts)
    except Exception:
        logger.exception("WS /ws/twilio: unexpected error")
        await _handle_disconnect(transcript_parts)
        try:
            await websocket.close()
        except Exception:
            pass


# ── HTTP: TwiML voice webhook ────────────────────────────────────────

@app.get("/twilio/voice")
async def twilio_voice_webhook():
    """
    Twilio voice webhook — returns TwiML connecting the call to /ws/twilio.

    Update the hostname below to your ngrok URL in production.
    """
    host = os.environ.get("NGROK_HOST", "your-ngrok-hostname.ngrok.io")
    twiml = TWIML_TEMPLATE.format(host=host)
    return Response(content=twiml, media_type="application/xml")


# ── HTTP: Twilio WhatsApp webhook ─────────────────────────────────────

WHATSAPP_TWIML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{answer}</Message>
</Response>"""


@app.post("/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    Body: str = Form(default=""),
    From: str = Form(default=""),
    WaId: str = Form(default=""),
):
    """
    Twilio WhatsApp webhook — receives incoming WhatsApp messages,
    runs them through the RAG pipeline, and returns the answer.

    Configure this URL in Twilio Console:
        https://<your-tunnel>/twilio/whatsapp
    """
    if not Body.strip():
        twiml = WHATSAPP_TWIML_TEMPLATE.format(
            answer="Hello! Send me a question about UMD or FDU admissions."
        )
        return Response(content=twiml, media_type="application/xml")

    logger.info(f"WhatsApp from {From} (WaId={WaId}): {Body[:100]}")

    # Run RAG in thread pool — ollama.chat() is blocking
    import asyncio as _asyncio
    from app.pipeline import run_rag_query_sync

    try:
        answer = await _asyncio.to_thread(run_rag_query_sync, Body)
    except Exception as e:
        logger.exception("WhatsApp RAG failed")
        answer = None

    if not answer:
        answer = "Sorry, I couldn't process your question right now. Please try again."

    # Escape XML special characters
    answer = answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    twiml = WHATSAPP_TWIML_TEMPLATE.format(answer=answer)
    logger.info(f"WhatsApp response ({len(answer)} chars): {answer[:80]}...")
    return Response(content=twiml, media_type="application/xml")


# ── HTTP: Health check ───────────────────────────────────────────────

@app.get("/")
async def health_check():
    """Health check + navigation to available endpoints."""
    from app.config import settings

    return JSONResponse({
        "status": "ok",
        "app": "University Admissions Voice Assistant",
        "database": "connected" if _db_available else "unavailable",
        "transport": settings.TRANSPORT_PROVIDER,
        "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID),
        "endpoints": {
            "health": "/",
            "voice_page": "/voice",
            "websocket_pcm": "/ws/voice",
            "websocket_text_rag": "/ws/voice/text",
            "twilio_webhook": "/twilio/voice",
            "twilio_websocket": "/ws/twilio",
            "whatsapp_webhook": "/twilio/whatsapp",
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
