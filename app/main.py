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
import json
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

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


# ── WhatsApp voice transcription ───────────────────────────────────────

# Lazily loaded Whisper model (cached after first use)
_stt_model = None


def _get_stt_model():
    """Load openai-whisper model — cached across requests.
    Uses openai-whisper instead of faster-whisper because the PyAV DLL
    (required by faster-whisper) is blocked by AppLocker on this machine.
    """
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    import whisper
    _stt_model = whisper.load_model("base")  # better accuracy than tiny
    logger.info("Whisper base model loaded")
    return _stt_model


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

        # Transcribe with openai-whisper (expects float32 array)
        model = _get_stt_model()
        result = model.transcribe(audio_np.astype(np.float32), language="en")
        transcript = result["text"].strip()

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

    # Step 4: Resolve tunnel host (env var -> file written by start_demo.bat)
    tunnel_host = os.environ.get("TUNNEL_HOST", "")
    if not tunnel_host:
        tunnel_file = Path(__file__).resolve().parent.parent / ".whatsapp_tunnel"
        if tunnel_file.is_file():
            tunnel_host = tunnel_file.read_text().strip()
            logger.info(f"Tunnel host from file: {tunnel_host}")

    # Step 5: Send reply
    if audio_filename and tunnel_host:
        audio_url = f"https://{tunnel_host}/audio/{audio_filename}"
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body=answer,
            media_url=audio_url,
        )
    else:
        if audio_filename and not tunnel_host:
            logger.warning("TUNNEL_HOST not set — sending text-only reply")
        _send_whatsapp_message(
            to_number=from_number,
            from_number=to_number,
            body=answer,
        )

    logger.info(f"Voice note processed: {from_number} ← {len(answer)} chars")


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
