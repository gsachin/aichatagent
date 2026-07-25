"""
FastAPI application — University Admissions Voice Assistant.

Transport-agnostic server. The WebSocket endpoints accept audio from:
- WAV test harness (test_transport.py)
- Browser microphone page (voice_client.html)
- Twilio Media Streams (when credentials are configured)

Endpoints:
    GET  /              — health check + navigation
    GET  /voice         — browser mic page (voice_client.html)
    WS   /ws/voice      — raw PCM echo/voice endpoint
    GET  /twilio/voice  — TwiML response for inbound calls
    WS   /ws/twilio     — Twilio Media Streams (8 kHz u-law)

Run: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import base64
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_api")

app = FastAPI(title="University Admissions Voice Assistant")

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


# ── WebSocket: Raw PCM (local echo / pipeline) ───────────────────────

@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    """
    WebSocket endpoint for streaming PCM audio frames (16-bit, mono).

    Accepts:  binary PCM audio (16-bit, mono, 16 kHz)
    Returns:  echoed binary PCM audio (same format)

    This is the local test endpoint. In the full pipeline:
        audio in -> VAD -> STT -> RAG -> LLM -> TTS -> audio out
    """
    await websocket.accept()
    logger.info("WS /ws/voice: client connected")

    try:
        while True:
            data = await websocket.receive_bytes()
            await websocket.send_bytes(data)
    except WebSocketDisconnect:
        logger.info("WS /ws/voice: client disconnected")
    except KeyError:
        logger.info("WS /ws/voice: received non-binary frame, ignoring")
        await websocket.close(code=1003, reason="Binary frames only")
    except Exception:
        logger.exception("WS /ws/voice: unexpected error")
        try:
            await websocket.close()
        except Exception:
            pass


# ── WebSocket: Twilio Media Streams ──────────────────────────────────

@app.websocket("/ws/twilio")
async def websocket_twilio(websocket: WebSocket):
    """
    Twilio Media Streams WebSocket endpoint.

    Twilio sends JSON text frames with event types:
      - "connected"  — stream is ready
      - "start"      — stream start, includes streamSid
      - "media"      — base64-encoded 8 kHz u-law audio payload
      - "stop"       — stream ended

    Audio flow:
      Twilio u-law -> decode to PCM -> pipeline -> encode to u-law -> Twilio

    Currently echoes audio back for transport validation.
    """
    await websocket.accept()
    logger.info("WS /ws/twilio: Twilio client connected")

    stream_sid: str | None = None

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("WS /ws/twilio: invalid JSON")
                continue

            event = msg.get("event", "")

            if event == "connected":
                logger.info("WS /ws/twilio: connected event received")

            elif event == "start":
                stream_sid = msg.get("streamSid", msg.get("start", {}).get("streamSid", ""))
                logger.info(f"WS /ws/twilio: stream started — streamSid={stream_sid}")

            elif event == "media":
                # Decode base64 u-law -> PCM
                payload = msg.get("media", {}).get("payload", "")
                if not payload:
                    continue

                try:
                    ulaw_bytes = base64.b64decode(payload)
                except Exception:
                    logger.warning("WS /ws/twilio: invalid base64 payload")
                    continue

                # Convert u-law -> linear PCM
                pcm_bytes = ulaw_to_pcm(ulaw_bytes)

                # In the full pipeline, pcm_bytes flows through:
                #   VAD -> STT -> RAG -> LLM -> TTS
                # For now, echo back (transport validation)

                # Convert PCM -> u-law and send back
                out_ulaw = pcm_to_ulaw(pcm_bytes)
                out_payload = base64.b64encode(out_ulaw).decode("ascii")

                response = json.dumps({
                    "event": "media",
                    "streamSid": stream_sid or "",
                    "media": {"payload": out_payload},
                })
                await websocket.send_text(response)

            elif event == "stop":
                logger.info(f"WS /ws/twilio: stream stopped — streamSid={stream_sid}")
                break

            else:
                logger.debug(f"WS /ws/twilio: unknown event '{event}'")

    except WebSocketDisconnect:
        logger.info("WS /ws/twilio: client disconnected")
    except Exception:
        logger.exception("WS /ws/twilio: unexpected error")
        try:
            await websocket.close()
        except Exception:
            pass


# ── HTTP: TwiML voice webhook ────────────────────────────────────────

@app.get("/twilio/voice")
async def twilio_voice_webhook():
    """
    Twilio voice webhook endpoint.

    Returns TwiML XML instructing Twilio to connect the call
    to our /ws/twilio Media Streams endpoint.

    Twilio calls this endpoint when a call comes in to the
    configured phone number.
    """
    # Use the Host header to build the correct wss:// URL.
    # In production behind ngrok/Cloudflare, this will be the public hostname.
    twiml = TWIML_TEMPLATE.format(host="your-ngrok-hostname.ngrok.io")

    return Response(content=twiml, media_type="application/xml")


# ── HTTP: Health check ───────────────────────────────────────────────

@app.get("/")
async def health_check():
    """Health check + navigation to available endpoints."""
    return JSONResponse({
        "status": "ok",
        "app": "University Admissions Voice Assistant",
        "endpoints": {
            "health": "/",
            "voice_page": "/voice",
            "websocket_pcm": "/ws/voice",
            "twilio_webhook": "/twilio/voice",
            "twilio_websocket": "/ws/twilio",
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
