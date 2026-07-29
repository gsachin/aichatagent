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
import io as _io
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger("voice_handler")

# ── Lazy-loaded models (shared across all sessions) ───────────────────

_stt_model = None
_tts_engine = None


def _get_stt_model():
    """Load openai-whisper once and cache."""
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    import whisper

    logger.info("Loading Whisper base model for voice calls...")
    _stt_model = whisper.load_model("base")
    logger.info("Whisper model ready")
    return _stt_model


def _get_tts_engine():
    """Load Kokoro ONNX once and cache."""
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine
    from kokoro_onnx import Kokoro

    cache_dir = os.path.expanduser(r"~\.cache\pipecat\kokoro-onnx")
    _tts_engine = Kokoro(
        os.path.join(cache_dir, "kokoro-v1.0.onnx"),
        os.path.join(cache_dir, "voices-v1.0.bin"),
    )
    logger.info("Kokoro TTS engine ready")
    return _tts_engine


# ── u-law ↔ PCM conversion (stdlib audioop) ───────────────────────────

def ulaw_to_pcm(ulaw_bytes: bytes) -> bytes:
    import audioop
    return audioop.ulaw2lin(ulaw_bytes, 2)


def pcm_to_ulaw(pcm_bytes: bytes) -> bytes:
    import audioop
    return audioop.lin2ulaw(pcm_bytes, 2)


# ── Standalone helper for main.py greeting ────────────────────────────


def generate_ulaw_greeting(text: str) -> list[bytes]:
    """
    Quick TTS → µ-law chunker for use in WebSocket start handlers.

    Returns a list of µ-law byte chunks ready for Twilio Media Streams.
    """
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(text, voice="af_heart", speed=1.0)
    from scipy.signal import resample

    target_len = int(len(audio) * 8000 / 24000)
    audio_8k = resample(audio, target_len)
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

class VoiceCallSession:
    """
    Handles a single outbound (or inbound) voice call.

    Accumulates audio, runs STT when the caller pauses, queries the
    RAG + LLM pipeline, synthesises the answer via TTS, and returns
    µ-law audio chunks ready to be sent back over Twilio Media Streams.
    """

    def __init__(
        self,
        silence_threshold_frames: int = 30,  # ~600 ms at 20 ms/frame
        max_utterance_frames: int = 300,     # ~6 seconds max
        sample_rate: int = 8000,             # Twilio uses 8 kHz
    ):
        self._silence_threshold = silence_threshold_frames
        self._max_utterance = max_utterance_frames
        self._sample_rate = sample_rate

        # Audio buffer
        self._audio_buffer: list[bytes] = []      # raw µ-law chunks
        self._silence_count = 0
        self._total_frames = 0

        # Conversation context (accumulated across utterances)
        self._conversation_history: list[str] = []

    # ── Public API ──────────────────────────────────────────────────

    def reset_utterance(self):
        """Clear the current utterance buffer (call when speech ends)."""
        self._audio_buffer.clear()
        self._silence_count = 0
        self._total_frames = 0

    def is_silent(self, ulaw_chunk: bytes) -> bool:
        """
        Quick energy-based check: is this chunk mostly silence?

        Decodes a few samples and checks RMS amplitude.
        """
        if len(ulaw_chunk) < 2:
            return True
        try:
            pcm = ulaw_to_pcm(ulaw_chunk)
            samples = np.frombuffer(pcm, dtype=np.int16)
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
            return rms < 80  # threshold for 8 kHz µ-law
        except Exception:
            return True

    def feed_audio(self, ulaw_chunk: bytes) -> bool:
        """
        Feed one µ-law chunk into the session.

        Returns True if the caller has just stopped speaking
        (i.e. we should now process the utterance).
        """
        silent = self.is_silent(ulaw_chunk)

        if not silent:
            # Speech — accumulate
            self._audio_buffer.append(ulaw_chunk)
            self._silence_count = 0
            self._total_frames += 1
        elif self._audio_buffer:
            # Silence AFTER speech — count trailing silence
            self._silence_count += 1

        # Trigger on trailing silence OR max utterance length
        if self._audio_buffer and (
            self._silence_count >= self._silence_threshold
            or self._total_frames >= self._max_utterance
        ):
            return True
        return False

    async def process_utterance(self) -> list[bytes]:
        """
        Run the full pipeline on the accumulated audio:

           µ-law buffer → PCM WAV → Whisper STT → RAG + LLM → Kokoro TTS → µ-law chunks

        Returns a list of µ-law byte chunks ready to stream back.
        """
        if not self._audio_buffer:
            self.reset_utterance()
            return []

        # ── Step 1: Decode µ-law → PCM WAV bytes ─────────────────
        combined_ulaw = b"".join(self._audio_buffer)
        pcm_bytes = ulaw_to_pcm(combined_ulaw)

        # Convert to 16 kHz mono for Whisper (resample from 8 kHz)
        audio_8k = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Resample 8k → 16k using scipy
        try:
            from scipy.signal import resample

            target_len = int(len(audio_8k) * 16000 / 8000)
            audio_16k = resample(audio_8k, target_len)
        except Exception:
            # Fallback: simple repeat
            audio_16k = np.repeat(audio_8k, 2)

        audio_16k_int16 = (audio_16k * 32767).clip(-32768, 32767).astype(np.int16)

        logger.info(
            f"VoiceCall: processing utterance ({len(self._audio_buffer)} chunks, "
            f"{len(audio_8k)/8000:.1f}s at 8 kHz)"
        )

        # ── Step 2: Whisper STT ───────────────────────────────────
        transcript = await self._transcribe(audio_16k_int16)
        self.reset_utterance()

        if not transcript or not transcript.strip():
            logger.info("VoiceCall: empty transcript — nothing to answer")
            return []

        logger.info(f"VoiceCall: transcript = \"{transcript[:120]}\"")

        # Add to conversation history
        self._conversation_history.append(f"Caller: {transcript}")

        # ── Step 3: RAG + LLM ─────────────────────────────────────
        answer = await self._query_llm(transcript)
        if not answer:
            return []

        self._conversation_history.append(f"Assistant: {answer}")
        logger.info(f"VoiceCall: answer ({len(answer)} chars) = \"{answer[:120]}...\"")

        # ── Step 4: Kokoro TTS → PCM ──────────────────────────────
        tts_pcm = await self._synthesise(answer)
        if tts_pcm is None or len(tts_pcm) == 0:
            return []

        # ── Step 5: PCM → µ-law chunks (320 samples = 20 ms at 16 kHz) ──
        return self._pcm_to_ulaw_chunks(tts_pcm)

    # ── Internal ──────────────────────────────────────────────────

    async def _transcribe(self, audio_16k_int16: np.ndarray) -> str:
        """Run Whisper STT in a thread (it blocks).

        Whisper expects float32 audio in range [-1.0, 1.0], but we
        receive int16.  Convert before calling the model.
        """
        try:
            model = _get_stt_model()
            # Convert int16 → float32 for whisper compatibility
            audio_float = audio_16k_int16.astype(np.float32) / 32768.0
            result = await asyncio.to_thread(
                lambda: model.transcribe(audio_float, language="en", fp16=False)
            )
            return (result.get("text") or "").strip()
        except Exception:
            logger.exception("VoiceCall: STT failed")
            return ""

    async def _query_llm(self, question: str) -> str:
        """Query the shared RAG + LLM pipeline in a thread."""
        try:
            from app.pipeline import run_rag_query_sync

            # Include recent conversation for context
            if self._conversation_history:
                context = "\n".join(self._conversation_history[-6:])
                prompt = (
                    f"Previous conversation:\n{context}\n\n"
                    f"The caller just said: \"{question}\"\n"
                    f"Answer naturally as a university admissions advisor. "
                    f"Keep responses concise for voice (under 3 sentences)."
                )
            else:
                prompt = question

            return await asyncio.to_thread(run_rag_query_sync, prompt) or ""
        except Exception:
            logger.exception("VoiceCall: LLM query failed")
            return ""

    async def _synthesise(self, text: str) -> np.ndarray | None:
        """Run Kokoro TTS in a thread, return float32 PCM array."""
        try:
            kokoro = _get_tts_engine()
            # Keep it short for phone calls
            tts_text = text[:500] if len(text) > 500 else text
            audio, sr = await asyncio.to_thread(
                kokoro.create, tts_text, voice="af_heart", speed=1.0
            )
            logger.info(f"VoiceCall: TTS generated ({len(audio)/sr:.1f}s at {sr} Hz)")
            return audio
        except Exception:
            logger.exception("VoiceCall: TTS failed")
            return None

    def _pcm_to_ulaw_chunks(self, audio: np.ndarray) -> list[bytes]:
        """
        Convert a float32 PCM array (Kokoro output, typically 24 kHz) to
        a list of 8 kHz µ-law byte chunks suitable for Twilio Media Streams.

        Each chunk is 320 bytes (20 ms at 8 kHz = 160 samples × 2 bytes).
        """
        # Kokoro outputs at 24 kHz by default — resample to 8 kHz for Twilio
        from scipy.signal import resample

        target_len = int(len(audio) * 8000 / 24000)
        audio_8k = resample(audio, target_len)
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
