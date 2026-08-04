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
_STT_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small.en")  # small.en or medium.en


def _get_stt_model():
    """Load faster-whisper on CUDA once and cache (numpy bypass avoids PyAV DLL)."""
    global _stt_model
    if _stt_model is not None:
        return _stt_model

    from faster_whisper import WhisperModel

    # Detect compute device
    try:
        from app.platform import detect_compute_device
        platform = detect_compute_device()
        device = platform["device"]
        compute_type = platform["compute_type"]
    except Exception:
        device = "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES") else "cpu"
        compute_type = "int8" if device == "cuda" else "float32"

    logger.info(f"Loading faster-whisper {_STT_MODEL_SIZE} on {device} ({compute_type})...")
    _stt_model = WhisperModel(_STT_MODEL_SIZE, device=device, compute_type=compute_type)
    logger.info(f"faster-whisper {_STT_MODEL_SIZE} ready on {device}")
    return _stt_model


def _get_tts_engine():
    """Load Kokoro ONNX once and cache — GPU-accelerated via CUDA."""
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine

    # Force CUDA execution provider for 5-10x faster TTS
    import onnxruntime as ort
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available:
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
        logger.info("Kokoro TTS: CUDA GPU enabled")
    else:
        logger.warning("Kokoro TTS: CUDA not available, using CPU (slow)")

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

    async def process_utterance(self) -> tuple[list[bytes], str]:
        """
        Run the full pipeline on the accumulated audio:

           µ-law buffer → PCM WAV → Whisper STT → RAG + LLM → Kokoro TTS → µ-law chunks

        Returns a tuple of (ulaw_chunks, transcript_dialogue) where
        transcript_dialogue is the full exchange (caller + AI) for logging.
        """
        if not self._audio_buffer:
            self.reset_utterance()
            return [], ""

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
            return [], ""

        logger.info(f"VoiceCall: transcript = \"{transcript[:120]}\"")

        # Add to conversation history
        self._conversation_history.append(f"Caller: {transcript}")

        # ── Step 3: RAG + LLM ─────────────────────────────────────
        answer = await self._query_llm(transcript)
        if not answer:
            # Return transcript even if LLM fails — still useful for logging
            dialogue = f"Caller: {transcript}\nAssistant: (no response)"
            return [], dialogue

        self._conversation_history.append(f"Assistant: {answer}")
        logger.info(f"VoiceCall: answer ({len(answer)} chars) = \"{answer[:120]}...\"")

        # Build dialogue for transcript logging
        dialogue = f"Caller: {transcript}\nAssistant: {answer}"

        # ── Step 4: Kokoro TTS → PCM ──────────────────────────────
        tts_pcm = await self._synthesise(answer)
        if tts_pcm is None or len(tts_pcm) == 0:
            return [], dialogue  # Transcript saved even if TTS fails

        # ── Step 5: PCM → µ-law chunks (320 samples = 20 ms at 16 kHz) ──
        return self._pcm_to_ulaw_chunks(tts_pcm), dialogue

    # ── Domain dictionary for phone audio corrections ──────────────

    _CORRECTIONS = {
        "held you": "FDU", "hold you": "FDU", "hold u": "FDU",
        "intuition": "tuition", "faze": "fees",
        "you empty": "UMD", "you and the": "UMD", "empty": "UMD",
        "emma": "MBA", "gp a": "GPA", "i elts": "IELTS",
        "toefl": "TOEFL", "jimat": "GMAT", "g mat": "GMAT",
    }

    def _post_process_transcript(self, text: str) -> str:
        """Apply domain corrections to fix common STT errors on phone audio."""
        result = text
        text_lower = result.lower()
        for wrong, correct in self._CORRECTIONS.items():
            if wrong in text_lower:
                # Case-insensitive replacement
                import re
                result = re.sub(wrong, correct, result, flags=re.IGNORECASE)
        return result.strip()

    # ── Internal ──────────────────────────────────────────────────

    async def _transcribe(self, audio_16k_int16: np.ndarray) -> str:
        """Run faster-whisper STT in a thread (GPU via CTranslate2).

        Accepts int16 numpy array directly — no PyAV/file I/O needed,
        which avoids the AppLocker DLL block on this machine.
        """
        try:
            model = _get_stt_model()
            # Convert int16 → float32 for faster-whisper
            audio_float = audio_16k_int16.astype(np.float32) / 32768.0

            # faster-whisper API returns (segments, info) — different from openai-whisper
            segments, info = await asyncio.to_thread(
                lambda: model.transcribe(
                    audio_float,
                    language="en",
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters=dict(
                        threshold=0.5,
                        min_speech_duration_ms=250,
                    ),
                )
            )
            transcript = " ".join(seg.text.strip() for seg in segments)
            transcript = self._post_process_transcript(transcript)
            logger.info(
                f"STT: \"{transcript[:100]}\" "
                f"(lang={info.language}, prob={info.language_probability:.2f})"
            )
            return transcript
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

    # Simple TTS cache — keyed by text hash, avoids re-synthesis of common phrases
    _tts_cache: dict[int, tuple[np.ndarray, int]] = {}
    _tts_cache_max = 50

    async def _synthesise(self, text: str) -> np.ndarray | None:
        """Run Kokoro TTS in a thread, return float32 PCM array. Cached for speed."""
        try:
            kokoro = _get_tts_engine()
            tts_text = text[:500] if len(text) > 500 else text

            # Check cache
            cache_key = hash(tts_text)
            if cache_key in self._tts_cache:
                cached_audio, cached_sr = self._tts_cache[cache_key]
                logger.debug(f"TTS cache HIT: {tts_text[:60]}...")
                return cached_audio.copy()

            audio, sr = await asyncio.to_thread(
                kokoro.create, tts_text, voice="af_heart", speed=1.0
            )
            # Store in cache
            if len(self._tts_cache) >= self._tts_cache_max:
                # Evict oldest
                oldest = next(iter(self._tts_cache))
                del self._tts_cache[oldest]
            self._tts_cache[cache_key] = (audio.copy(), sr)

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
