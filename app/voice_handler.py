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
import re
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger("voice_handler")

# Structured call-event timeline (Phase 0 observability): every line is
#   EVENT <call_id> turn=<n> <NAME> [key=value ...]
# so a call can be reconstructed end-to-end from the log.
voice_events = logging.getLogger("voice_events")

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

    # Detect compute device — CTranslate2 has no Metal backend, so Apple
    # Silicon (mps) maps to CPU with NEON-accelerated int8.
    try:
        from app.platform import get_whisper_device_config
        device, compute_type = get_whisper_device_config()
    except Exception:
        device = "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES") else "cpu"
        compute_type = "int8" if device == "cuda" else "float32"

    logger.info(f"Loading faster-whisper {_STT_MODEL_SIZE} on {device} ({compute_type})...")
    # WHISPER_NUM_THREADS is sized per machine by scripts/predeploy.py.
    # Note: pipecat's WhisperSTTSettings has no cpu_threads param, so this
    # applies to the voice_handler STT path only.
    _stt_model = WhisperModel(
        _STT_MODEL_SIZE,
        device=device,
        compute_type=compute_type,
        cpu_threads=int(os.environ.get("WHISPER_NUM_THREADS", "4")),
    )
    logger.info(f"faster-whisper {_STT_MODEL_SIZE} ready on {device}")
    return _stt_model


def _get_tts_engine():
    """Load Kokoro ONNX once and cache.

    Requests the CUDA execution provider, then reports which provider the
    session ACTUALLY got.

    The original version logged "CUDA GPU enabled" whenever
    `ort.get_available_providers()` listed CUDA — that lists providers compiled
    into the wheel, not providers that instantiate. On this box the CUDA
    provider fails ("Require cuDNN 9.* and CUDA 13.*"; torch ships CUDA 12.8)
    and ONNX Runtime silently falls back to CPU, so the log claimed a 5-10x
    speedup that was never happening while the CPU did all the work.

    Measured consequence: RTF ~0.6 on CPU, which is the ~6 s single-session
    synthesis and the 12-16 s N=2 bottleneck. The claim is now read from the
    session itself, so it cannot be true-by-accident again.
    """
    global _tts_engine
    if _tts_engine is not None:
        return _tts_engine

    # Ask for CUDA. Whether we GET it is checked below, from the session.
    import onnxruntime as ort
    requested = "CUDAExecutionProvider" if "CUDAExecutionProvider" in \
        ort.get_available_providers() else None
    if requested:
        os.environ["ONNX_PROVIDER"] = requested

    # Make CUDA/cuDNN actually resolvable. On Windows the ORT wheel does NOT
    # bundle them, so the provider fails to instantiate and ONNX Runtime
    # silently falls back to CPU. torch ships CUDA 12.8 + cuDNN 9 and registers
    # its own lib dir on import; preload_dlls() reuses those.
    #
    # This is the difference between RTF 0.63 and RTF 0.06 on this box — the
    # ~6 s single-session synthesis and the 12-16 s two-caller bottleneck.
    # Order matters: torch first, then preload_dlls(), then the session.
    try:
        import torch  # noqa: F401  (registers torch's bundled CUDA/cuDNN dirs)
        ort.preload_dlls()
    except Exception as exc:
        logger.warning("Kokoro TTS: CUDA DLL preload skipped (%s)", exc)

    from kokoro_onnx import Kokoro

    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "pipecat", "kokoro-onnx")
    _tts_engine = Kokoro(
        os.path.join(cache_dir, "kokoro-v1.0.onnx"),
        os.path.join(cache_dir, "voices-v1.0.bin"),
    )

    # The truth, read back from the session that was actually built.
    try:
        active = _tts_engine.sess.get_providers()
        on_gpu = "CUDAExecutionProvider" in active
    except Exception:
        active, on_gpu = ["<unknown>"], False

    if on_gpu:
        logger.info("Kokoro TTS: running on CUDA (%s)", ",".join(active))
    else:
        logger.warning(
            "Kokoro TTS: CPU ONLY — requested %s, session got %s. Synthesis is "
            "~0.6x real-time and contends with everything else on the CPU; this "
            "is the dominant cost of a two-caller turn. See doc/perf/"
            "IMPLEMENTATION_STATUS.md.",
            requested or "(nothing)", ",".join(active),
        )
    return _tts_engine


#: Voice and speaking rate for the LIVE call path.
#:
#: Both keys shipped in .env and were ignored here: the call site hardcoded
#: voice="af_heart", speed=1.0. KOKORO_VOICE was read by app/pipeline.py, so it
#: looked alive in a grep while the Twilio path -- the one that actually serves
#: callers -- never consulted it. Reading them here makes the live path agree
#: with the rest of the stack.
#:
#: Speed is clamped to the range Kokoro renders cleanly; outside it the output
#: degrades into artefacts, which is worse than ignoring the setting. A value
#: that will not parse falls back to 1.0 with a warning rather than raising --
#: a typo in .env must not stop the stack from answering calls.
TTS_VOICE = os.environ.get("KOKORO_VOICE", "af_heart").strip() or "af_heart"


def _parse_tts_speed(raw: str) -> float:
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        logger.warning("KOKORO_SPEED=%r is not a number; using 1.0", raw)
        return 1.0
    clamped = min(max(speed, 0.5), 2.0)
    if clamped != speed:
        logger.warning(
            "KOKORO_SPEED=%.2f is outside the clean-synthesis range 0.5-2.0; "
            "using %.2f", speed, clamped)
    return clamped


TTS_SPEED = _parse_tts_speed(os.environ.get("KOKORO_SPEED", "1.0"))

#: US-012 AC-4: the isolation fallback must be a CONFIGURATION change, not a code
#: revert. "shared" is the DG-06 decision (one process-wide cache, reused across
#: callers); "per_call" bounds the cache to a single session's utterances and
#: gives up cross-caller reuse in exchange for isolation that needs no argument.
TTS_CACHE_SCOPE = os.environ.get("TTS_CACHE_SCOPE", "shared").strip().lower()


def _cache_key_sha(cache_key: tuple) -> str:
    """Short, stable digest of a cache key, safe to write to a trace.

    Deterministic across processes, unlike `hash()` of a str, which is salted
    per interpreter (PYTHONHASHSEED). In-process that salt is harmless; in a log
    it would make two callers' keys for the same utterance look different, and
    the isolation test reads keys across a whole run.

    The digest is truncated to 12 hex characters and is one-way, so no agent
    text lands in the trace (TAC-4).
    """
    import hashlib

    raw = "\x1f".join(str(part) for part in cache_key)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _tts_voice() -> str:
    return TTS_VOICE


def _tts_speed() -> float:
    return TTS_SPEED


# ── u-law ↔ PCM conversion (stdlib audioop) ───────────────────────────

def ulaw_to_pcm(ulaw_bytes: bytes) -> bytes:
    import audioop
    return audioop.ulaw2lin(ulaw_bytes, 2)


def pcm_to_ulaw(pcm_bytes: bytes) -> bytes:
    import audioop
    return audioop.lin2ulaw(pcm_bytes, 2)


# ── Standalone helper for main.py greeting ────────────────────────────


def _turn_outcome(chunks: list[bytes]) -> list[bytes]:
    """Record a turn on the four-outcome axis, then pass the audio through.

    US-016 AC-2: `served`, `degraded`, `failed` and `refused` are four different
    things and are never collapsed. `degraded` is recorded where the fixed
    response is played, `refused` where a call is turned away at admission, and
    this covers the other two -- a turn that produced speech, and a turn that
    produced none.
    """
    from app.admission import REGISTRY

    if chunks:
        REGISTRY.note_served()
    else:
        REGISTRY.note_failed()
    return chunks


def load_call_asset_ulaw(name: str) -> list[bytes] | None:
    """Read a pre-synthesised call asset as µ-law chunks. No model, no synthesis.

    US-016 AC-5: the fixed response and the busy message are the two sentences
    a caller hears when the machinery is not available to say anything else.
    Both are read from disk here -- this function touches no engine and no
    synthesiser, which is precisely why it still works when one or both are
    gone. Returns None if the asset is missing, and the caller decides what to
    do rather than being handed silence.
    """
    from app.admission import asset_path

    path = asset_path(name)
    if not path.is_file():
        return None
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                logger.warning("call asset %s is not mono 16-bit PCM", name)
                return None
            rate = w.getframerate()
            pcm = w.readframes(w.getnframes())
        if rate != 8000:
            # Built at carrier rate, so this is a guard rather than a path.
            logger.warning("call asset %s is %d Hz, expected 8000", name, rate)
            return None
    except Exception:
        logger.exception("call asset %s could not be read", name)
        return None

    chunk = 320  # 20 ms at 8 kHz, the Media Streams frame size
    return [pcm_to_ulaw(pcm[i:i + chunk]) for i in range(0, len(pcm), chunk)]


def generate_ulaw_greeting(text: str) -> list[bytes]:
    """
    Quick TTS → µ-law chunker for use in WebSocket start handlers.

    Returns a list of µ-law byte chunks ready for Twilio Media Streams.
    """
    kokoro = _get_tts_engine()
    audio, sr = kokoro.create(text, voice=_tts_voice(), speed=_tts_speed())
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

_CLARIFICATION_RE = re.compile(
    r"(which (specific )?(program|course|intake|semester|term)|"
    r"what (program|course|intake|semester|term)|could you (please )?"
    r"(specify|clarify|tell me)|did you mean|just to confirm|"
    r"are you (looking|asking|interested|referring|planning)|"
    r"fall,? ?spring)",
    re.IGNORECASE,
)


def _is_clarification(text: str) -> bool:
    """True when an assistant turn reads as a clarification question."""
    return "?" in text and bool(_CLARIFICATION_RE.search(text))


# ── STT noise gate + deterministic hangup (pre-LLM guardrails) ────────

STT_MIN_AVG_LOGPROB = float(os.environ.get("STT_MIN_AVG_LOGPROB", "-4.0"))
STT_MAX_NO_SPEECH_PROB = float(os.environ.get("STT_MAX_NO_SPEECH_PROB", "0.8"))
STT_MIN_CHARS = int(os.environ.get("STT_MIN_CHARS", "3"))
# Utterances shorter than this are treated as noise bursts (15 × 20 ms = 300 ms).
MIN_UTTERANCE_FRAMES = int(os.environ.get("MIN_UTTERANCE_FRAMES", "15"))

_NOISE_WORDS = {"is", "uh", "um", "oh", "ah", "hmm", "hm", "mhm", "eh", "huh"}

#: Fixed reply for gated/noisy input — deliberately bypasses the LLM.
NOISE_REPLY = "Sorry, I didn't quite catch that — could you repeat that?"

#: Escalation after repeated noise-gated turns — the line itself is bad.
LINE_QUALITY_REPLY = (
    "It sounds like the line is breaking up. "
    "Could you try speaking a little closer to the phone?"
)

#: Step 3 — change the channel instead of repeating the same sentence.
ALT_CHANNEL_REPLY = (
    "The connection doesn't seem to be improving. You can also reach us "
    "on WhatsApp or email the admissions office, and we'll get back to "
    "you quickly."
)

#: Step 4+ — human-transfer offer. Never loop the identical sentence.
HUMAN_TRANSFER_REPLY = (
    "I'm sorry, I'm still not able to hear you clearly. I'll ask a human "
    "counselor to call you back. Could you tell me once more which number "
    "we should reach?"
)

_NOISE_LADDER = [NOISE_REPLY, LINE_QUALITY_REPLY, ALT_CHANNEL_REPLY, HUMAN_TRANSFER_REPLY]

# ── D1 output-boundary scrubber ────────────────────────────────────────
# Leaked reasoning/meta narration observed in real calls. Sentence-level
# filter: any sentence containing one of these markers is dropped before
# the reply is spoken or logged. Clean text passes through untouched.
_META_MARKERS = (
    "based on",
    "here's the relevant",
    "university profile",
    "the caller",
    "asked for clarification",
    "let's assume",
    "natural response",
    "this response provides",
    "provide concrete information",
    "without asking",
    "given that",
    "they are interested",
)


#: Meta lead-in glued directly to real content ("Here's the relevant
#: information from the university profile: The MBA is…") — strip the
#: prefix so the real content survives.
_PREFIX_STRIP = re.compile(
    r"^here'?s the relevant information\b[^:\n]*[:：]\s*", re.IGNORECASE
)


def scrub_meta_leak(text: str) -> str | None:
    """
    Hard output boundary (D1): remove reasoning/meta narration from an
    LLM answer before it reaches TTS. Returns None when nothing usable
    remains after scrubbing. Clean text passes through untouched.
    """
    low = text.lower()
    if not any(m in low for m in _META_MARKERS):
        return text  # clean reply — byte-identical pass-through

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    kept = []
    for s in sentences:
        s2 = _PREFIX_STRIP.sub("", s)
        if s2.strip() and not any(m in s2.lower() for m in _META_MARKERS):
            kept.append(s2)
    out = re.sub(r"\s{2,}", " ", " ".join(kept)).strip(" .\n")
    if len(out) < 12:
        return None
    return out

CLOSING_PHRASES_RE = re.compile(
    r"\b(bye|bye[- ]bye|goodbye|see ya|see you|talk later|"
    r"that's all|thank you bye|have a good day)\b",
    re.IGNORECASE,
)

#: Fixed closing line for the deterministic hangup path.
CLOSING_REPLY = "Thank you for calling Meridian University Admissions. Have a great day!"


def is_closing_phrase(text: str) -> bool:
    """Hard sign-off cues — handled deterministically, before the LLM."""
    return bool(CLOSING_PHRASES_RE.search(text))


def is_noise_fragment(text: str) -> bool:
    """Ultra-short or single-noise-word transcripts (STT artifacts)."""
    t = text.strip().strip(".,!?;").lower()
    if len(t) < STT_MIN_CHARS:
        return True
    return len(t.split()) == 1 and t in _NOISE_WORDS


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
        direction: str = "inbound",          # "inbound" | "outbound" — shapes LLM behavior
    ):
        self._silence_threshold = silence_threshold_frames
        self._max_utterance = max_utterance_frames
        self._sample_rate = sample_rate
        self.direction = direction
        self._noise_streak = 0  # consecutive noise-gated turns
        self.call_id = ""       # set by the WS handler (stream_sid)

        # US-012: the per-call TTS cache. Unused while TTS_CACHE_SCOPE is
        # "shared" (the DG-06 default), where every session reads and writes the
        # class-level cache instead. It is allocated unconditionally so that
        # switching scope is a configuration change with no code path missing.
        self._session_tts_cache: dict = {}
        self._turn = 0          # utterance counter for the event timeline

        # Audio buffer
        self._audio_buffer: list[bytes] = []      # raw µ-law chunks
        self._silence_count = 0
        self._total_frames = 0

        # Conversation context (accumulated across utterances)
        self._conversation_history: list[str] = []

        # US-001 turn tracing (app/perf_trace.py). Never raises; may be None.
        self._trace = None

    def log_event(self, name: str, **fields) -> None:
        """Emit one structured event line for the call timeline."""
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        voice_events.info("EVENT %s turn=%d %s %s", self.call_id or "-", self._turn, name, extra)

    # ── Public API ──────────────────────────────────────────────────

    def reset_utterance(self):
        """Clear the current utterance buffer (call when speech ends)."""
        self._audio_buffer.clear()
        self._silence_count = 0
        self._total_frames = 0

    def _noise_reply(self) -> str:
        """
        Fixed reply for noise-gated turns (D4 escalation ladder):
          1st miss  → plain retry
          2nd miss  → line-quality hint
          3rd miss  → alternate channel (WhatsApp/email)
          4th+ miss → human-transfer offer
        Each step is a DIFFERENT sentence — never the identical line twice
        in a row, which is the defect this ladder exists to prevent.
        """
        self._noise_streak += 1
        return _NOISE_LADDER[min(self._noise_streak - 1, len(_NOISE_LADDER) - 1)]

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
            if not self._audio_buffer:
                self.log_event("USER_SPEECH_STARTED")
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
            self.log_event("USER_SPEECH_STOPPED", frames=self._total_frames)
            # US-001: open a trace for this turn. vad_end is the zero point for
            # processing_ms. The 600 ms the caller waited before this instant is
            # endpointing_ms, which BRD-02 measures separately — do not conflate
            # the two clocks. Guarded: tracing never breaks a call.
            try:
                from app.perf_trace import new_trace

                self._trace = new_trace(self.call_id, self._turn)
                self._trace.mark("vad_end")
                self._trace.note(
                    vad_frames=self._total_frames,
                    endpoint_ms=self._silence_threshold * 20,
                )
            except Exception:
                self._trace = None
            return True
        return False

    async def process_utterance(self) -> tuple[list[bytes], str, bool]:
        """
        Run the full pipeline on the accumulated audio:

           µ-law buffer → PCM WAV → Whisper STT → RAG + LLM → Kokoro TTS → µ-law chunks

        Returns a tuple of (ulaw_chunks, transcript_dialogue, end_call) where
        transcript_dialogue is the full exchange (caller + AI) for logging and
        end_call is True when a hard sign-off cue was detected (the caller
        should hang up after playing the returned audio).
        """
        if not self._audio_buffer:
            self.reset_utterance()
            return [], "", False

        self._turn += 1
        self.log_event("UTTERANCE_PROCESSING", frames=self._total_frames)

        # ── Min-burst filter: drop sub-300ms noise bursts before STT ──
        if self._total_frames < MIN_UTTERANCE_FRAMES:
            logger.info(
                f"VoiceCall: dropped {self._total_frames}-frame burst "
                f"(<{MIN_UTTERANCE_FRAMES * 20} ms) as noise"
            )
            self.reset_utterance()
            return [], "", False

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
        self.log_event("STT_STARTED")
        transcript, low_conf = await self._transcribe(audio_16k_int16)
        self.log_event("STT_FINAL", chars=len(transcript), low_conf=low_conf)
        self.reset_utterance()

        if not transcript or not transcript.strip():
            # VAD removed all audio (pure noise/echo) — one quick check
            # line is better than dead air; the LLM never sees this.
            logger.info("VoiceCall: no speech in utterance — fixed fallback reply")
            reply = self._noise_reply()
            dialogue = f"Caller: (unintelligible)\nAssistant: {reply}"
            self._conversation_history.append(f"Caller: (unintelligible)")
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm = await self._synthesise(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, False
            return _turn_outcome(self._pcm_to_ulaw_chunks(tts_pcm)), dialogue, False

        logger.info(f"VoiceCall: transcript = \"{transcript[:120]}\"")

        # Add to conversation history
        self._conversation_history.append(f"Caller: {transcript}")

        # ── Step 2b: Noise gate — short fragments / low-confidence STT
        #    get a fixed one-line check; the LLM never sees them.
        if low_conf or is_noise_fragment(transcript):
            logger.info(
                f"VoiceCall: noise-gated \"{transcript[:60]}\" "
                f"(low_conf={low_conf}) — fixed fallback reply"
            )
            reply = self._noise_reply()
            dialogue = f"Caller: {transcript}\nAssistant: {reply}"
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm = await self._synthesise(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, False
            return _turn_outcome(self._pcm_to_ulaw_chunks(tts_pcm)), dialogue, False

        # Real speech decoded — reset the noise streak.
        self._noise_streak = 0

        # ── Step 2c: Deterministic hangup — hard sign-off cues end the
        #    call with a static closing; the LLM never sees them.
        if is_closing_phrase(transcript):
            logger.info(f"VoiceCall: closing phrase detected: \"{transcript[:60]}\"")
            reply = CLOSING_REPLY
            dialogue = f"Caller: {transcript}\nAssistant: {reply}"
            self._conversation_history.append(f"Assistant: {reply}")
            tts_pcm = await self._synthesise(reply)
            if tts_pcm is None or len(tts_pcm) == 0:
                return [], dialogue, True
            return _turn_outcome(self._pcm_to_ulaw_chunks(tts_pcm)), dialogue, True

        # ── Step 3: RAG + LLM ─────────────────────────────────────
        self.log_event("LLM_STARTED")
        answer = await self._query_llm(transcript)
        self.log_event("LLM_COMPLETED", chars=len(answer or ""))
        if not answer:
            # US-016 AC-5: the engine returned nothing. This used to return []
            # -- silence on a live line. The caller now hears the fixed
            # response, played from the asset set, so the turn ends in a
            # sentence rather than in nothing. The record names it as a
            # degraded turn, which is a different outcome from served, failed
            # and refused and is never collapsed into any of them.
            dialogue = f"Caller: {transcript}\nAssistant: (no response)"
            chunks = self._speak_fixed_response(reason="empty generation")
            if chunks:
                return chunks, dialogue, False
            return [], dialogue, False

        # ── Step 3b: D1 output boundary — strip leaked reasoning/meta
        #    narration before anything is spoken.
        scrubbed = scrub_meta_leak(answer)
        if scrubbed != answer:
            self.log_event("META_LEAK_SCRUBBED", before=len(answer), after=len(scrubbed or ""))
        if scrubbed is None:
            logger.warning(f"VoiceCall: answer fully scrubbed as meta-leak: {answer[:100]!r}")
            scrubbed = NOISE_REPLY
        answer = scrubbed

        self._conversation_history.append(f"Assistant: {answer}")
        logger.info(f"VoiceCall: answer ({len(answer)} chars) = \"{answer[:120]}...\"")

        # Build dialogue for transcript logging
        dialogue = f"Caller: {transcript}\nAssistant: {answer}"

        # ── Step 4: Kokoro TTS → PCM ──────────────────────────────
        tts_pcm = await self._synthesise(answer)
        if tts_pcm is None or len(tts_pcm) == 0:
            # US-016 AC-5, second branch: the synthesiser is the thing that
            # failed. The fixed response needs no synthesiser, so the caller
            # still hears a sentence rather than the tail of a dead turn.
            dialogue = f"Caller: {transcript}\nAssistant: {answer}"
            chunks = self._speak_fixed_response(reason="synthesis failed")
            if chunks:
                return chunks, dialogue, False
            return [], dialogue, False  # Transcript saved even if TTS fails

        # ── Step 5: PCM → µ-law chunks (320 samples = 20 ms at 16 kHz) ──
        return _turn_outcome(self._pcm_to_ulaw_chunks(tts_pcm)), dialogue, False

    # ── Domain dictionary for phone audio corrections ──────────────

    _CORRECTIONS = {
        # Meridian-name corrections (common STT mis-hearings on phone audio)
        "maridian": "Meridian", "miridian": "Meridian", "meridien": "Meridian",
        "mary dian": "Meridian",
        # Generic admissions terms (valid for Meridian)
        "intuition": "tuition", "faze": "fees",
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

    async def _transcribe(self, audio_16k_int16: np.ndarray) -> tuple[str, bool]:
        """Run faster-whisper STT in a thread (GPU via CTranslate2).

        Accepts int16 numpy array directly — no PyAV/file I/O needed,
        which avoids the AppLocker DLL block on this machine.

        Returns (transcript, low_confidence): low_confidence is True when
        the best segment's avg_logprob falls below STT_MIN_AVG_LOGPROB or
        no_speech_prob exceeds STT_MAX_NO_SPEECH_PROB.
        """
        try:
            model = _get_stt_model()
            # Convert int16 → float32 for faster-whisper
            audio_float = audio_16k_int16.astype(np.float32) / 32768.0

            # faster-whisper API returns (segments, info) — different from openai-whisper.
            # Greedy decoding (beam_size=1) + condition_on_previous_text=False:
            # on noisy phone audio, beam search and previous-text conditioning
            # are what produce hallucination loops ("Listen to her again").
            segments, info = await asyncio.to_thread(
                lambda: model.transcribe(
                    audio_float,
                    language="en",
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(
                        threshold=0.5,
                        min_speech_duration_ms=250,
                        min_silence_duration_ms=500,
                        speech_pad_ms=100,
                    ),
                )
            )
            texts, logprobs, no_speech_probs = [], [], []
            for seg in segments:
                texts.append(seg.text.strip())
                logprobs.append(seg.avg_logprob)
                no_speech_probs.append(seg.no_speech_prob)
            transcript = self._post_process_transcript(" ".join(texts))
            low_conf = bool(
                logprobs
                and (
                    min(logprobs) < STT_MIN_AVG_LOGPROB
                    or max(no_speech_probs) > STT_MAX_NO_SPEECH_PROB
                )
            )
            min_logprob = min(logprobs) if logprobs else None
            logger.info(
                f"STT: \"{transcript[:100]}\" "
                f"(lang={info.language}, prob={info.language_probability:.2f}, "
                f"min_logprob={f'{min_logprob:.2f}' if min_logprob is not None else 'n/a'}, "
                f"low_conf={low_conf})"
            )
            if self._trace is not None:
                self._trace.mark("stt_done")
                self._trace.note(stt_chars=len(transcript), stt_low_conf=bool(low_conf))
            return transcript, low_conf
        except Exception:
            logger.exception("VoiceCall: STT failed")
            return "", True

    async def _query_llm(self, question: str) -> str:
        """Query the shared RAG + LLM pipeline in a thread."""
        try:
            from app.pipeline import run_rag_query_sync

            # Include recent conversation for context
            if self._conversation_history:
                context = "\n".join(self._conversation_history[-6:])
                prompt = (
                    f"This is an {self.direction} call.\n"
                    f"Previous conversation:\n{context}\n\n"
                    f"The caller just said: \"{question}\"\n"
                    f"Answer naturally as a university admissions advisor. "
                    f"Keep responses concise for voice (under 3 sentences). "
                    f"NEVER ask the same or similar clarifying question twice "
                    f"in a row. If the caller repeats a similar answer, STOP "
                    f"asking — state your best interpretation and answer with "
                    f"concrete information from the university profile, then "
                    f"invite the caller to correct you."
                )
                # Deterministic loop-breaker: if the assistant's own last
                # two+ turns were clarification questions, rebuild the
                # prompt with caller-only history and force a grounded
                # answer — quantized models sometimes ignore prompt-only
                # guidance when they can see their own question pattern.
                assistant_turns = [
                    h for h in self._conversation_history if h.startswith("Assistant: ")
                ]
                clarify_streak = 0
                for h in reversed(assistant_turns):
                    if _is_clarification(h):
                        clarify_streak += 1
                    else:
                        break
                if clarify_streak >= 2:
                    caller_turns = [
                        h for h in self._conversation_history if h.startswith("Caller: ")
                    ]
                    prompt = (
                        f"This is an {self.direction} call.\n"
                        f"Caller's recent replies:\n" + "\n".join(caller_turns[-4:]) +
                        f"\n\nThe caller just said: \"{question}\"\n"
                        f"You have already asked for clarification too many "
                        f"times. Do NOT ask any question. Answer now with "
                        f"concrete program information from the university "
                        f"profile — duration, fees, eligibility — and let "
                        f"the caller correct you."
                    )
            else:
                prompt = f"This is an {self.direction} call.\n{question}"

            # US-001: llm_sent brackets retrieval + prefill + generation. The
            # split between those three comes from the engine counters captured
            # in app/llm_backend.py and the retrieval_done mark from app/rag.py.
            # llm_first_token is deliberately NOT marked here: with a
            # non-streaming call there is no first-token event, and marking this
            # instant would fabricate a TTFT (TRD-21).
            if self._trace is not None:
                self._trace.mark("llm_sent")
            answer = await asyncio.to_thread(run_rag_query_sync, prompt) or ""
            if self._trace is not None:
                self._trace.mark("llm_done")
                self._trace.note(answer_chars=len(answer))
            return answer
        except Exception:
            logger.exception("VoiceCall: LLM query failed")
            return ""

    #: TTS cache (DG-06 / US-012).
    #:
    #: These are CLASS attributes, so the cache is process-wide: every concurrent
    #: caller shares one dict. That is the DG-06 "use-as-is" decision, and it is
    #: conditional on the N=2 isolation test -- not on the argument that content
    #: keys make sharing obviously safe.
    #:
    #: The key is (agent text, voice, speed) and nothing else -- never caller
    #: speech, caller identity or transcript content (TAC-4). Voice and speed
    #: joined the key when they became configurable in this same change: with
    #: them outside it, editing KOKORO_VOICE or KOKORO_SPEED would have kept
    #: serving audio rendered in the OLD voice from cache.
    #:
    #: The value carries the owning call_id. It is not part of the key and never
    #: affects a lookup; it exists so that "caller B received audio synthesised
    #: for caller A" is measurable rather than arguable (MOD-04 A.5.2).
    _shared_tts_cache: dict[tuple, tuple[np.ndarray, int, str]] = {}
    _tts_cache_max = 50

    async def _synthesise(self, text: str) -> np.ndarray | None:
        """Run Kokoro TTS in a thread, return float32 PCM array. Cached for speed."""
        self.log_event("TTS_STARTED", chars=len(text))
        try:
            tts_text = text[:500] if len(text) > 500 else text

            # US-012: shared (DG-06 default) or per-call (the pre-decided
            # fallback). Chosen by configuration only -- AC-4 requires the
            # revert to be a setting, never a code change.
            voice, speed = _tts_voice(), _tts_speed()
            cache = (self._session_tts_cache if TTS_CACHE_SCOPE == "per_call"
                     else VoiceCallSession._shared_tts_cache)
            cache_key = (tts_text, voice, speed)

            # Check cache
            if cache_key in cache:
                cached_audio, cached_sr, owner = cache[cache_key]
                # A "cross-call hit" is caller B being served audio that was
                # synthesised during caller A's session (MOD-04 A.5.2). It is
                # recorded, not prevented -- measuring it is the whole point of
                # US-012, and AC-4 pre-decides what a non-zero count means.
                cross_call = bool(owner) and owner != self.call_id
                logger.debug(f"TTS cache HIT: {tts_text[:60]}...")
                self.log_event("TTS_COMPLETED", cached=1, cross_call=int(cross_call))
                # US-001: mark on EVERY return path. This one was missed on the
                # first pass — the cached path returned before the mark, so a
                # cache hit produced a trace with tts_done absent and the
                # llm_done->first_audio segment unaccounted for. Found by
                # running a live call, not by inspection.
                if self._trace is not None:
                    self._trace.mark("tts_done")
                    self._trace.note(
                        tts_cache_hit=True,
                        tts_cache_scope=TTS_CACHE_SCOPE,
                        tts_cache_cross_call=cross_call,
                        tts_cache_owner=owner,
                        tts_cache_key_sha=_cache_key_sha(cache_key),
                    )
                # Copy discipline: the caller gets its own buffer, so a later
                # mutation of one caller's audio cannot reach the cache or any
                # other caller (TAC-2).
                return cached_audio.copy()

            # Only a miss needs the model. Loading it inside the hit path meant a
            # cache hit still touched the engine, which kept the isolation tests
            # from running without a GPU.
            kokoro = _get_tts_engine()
            audio, sr = await asyncio.to_thread(
                kokoro.create, tts_text, voice=voice, speed=speed
            )
            # Store in cache. Bound first, FIFO by insertion order (TAC-3):
            # re-inserting an existing key keeps its original position, which is
            # what makes the eviction order predictable.
            if cache_key not in cache and len(cache) >= self._tts_cache_max:
                oldest = next(iter(cache))
                del cache[oldest]
            cache[cache_key] = (audio.copy(), sr, self.call_id)

            logger.info(f"VoiceCall: TTS generated ({len(audio)/sr:.1f}s at {sr} Hz)")
            if self._trace is not None:
                self._trace.mark("tts_done")
                self._trace.note(
                    tts_cache_hit=False,
                    tts_cache_scope=TTS_CACHE_SCOPE,
                    tts_cache_cross_call=False,
                    # The key is recorded on the MISS path too -- this line is
                    # what establishes a key's owner. Without it only hits
                    # carried a key, so nothing could attribute one and the
                    # isolation analysis had no owner map to compare against.
                    tts_cache_key_sha=_cache_key_sha(cache_key),
                    audio_s=round(len(audio) / sr, 2),
                )
            # The cache holds its own copy, so the buffer handed back here is
            # the caller's alone.
            return audio
        except Exception:
            logger.exception("VoiceCall: TTS failed")
            return None

    def _speak_fixed_response(self, reason: str) -> list[bytes]:
        """Return the pre-synthesised fixed response as µ-law chunks.

        US-016 / BRD-13: a lost inference engine must end in a deterministic
        fixed response rather than silence. "Deterministic" is literal -- the
        bytes come off disk, so the same failure on two turns in two calls
        produces byte-identical audio, and no model generated any part of it.

        Nothing is synthesised here. That is why this still works when the
        synthesiser is down at the same time as the engine, which is the case
        AC-5 calls out explicitly.

        Returns [] when no asset applies, and the caller then terminates the
        turn cleanly rather than hanging in silence.
        """
        from app.admission import FIXED_RESPONSE_ASSET, REGISTRY

        chunks = load_call_asset_ulaw(FIXED_RESPONSE_ASSET)
        if not chunks:
            logger.error(
                "VoiceCall: fixed response unavailable (%s) and no asset to play. "
                "Build the call assets: .venv/Scripts/python.exe "
                "scripts/build_call_assets.py", reason)
            # No asset applies, so this turn is a genuine failure rather than a
            # degraded one. The caller's turn terminates cleanly and the record
            # says which of the two it was.
            REGISTRY.note_failed()
            return []
        REGISTRY.note_asset_played(FIXED_RESPONSE_ASSET, call_sid=self.call_id)
        self.log_event("FIXED_RESPONSE", reason=reason, chunks=len(chunks))
        if self._trace is not None:
            # A degraded turn is its own outcome. It is not a failure: the
            # caller heard a sentence and the call continued.
            self._trace.note(outcome="degraded", degraded_reason=reason,
                             fixed_response_played=True)
        return chunks

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
