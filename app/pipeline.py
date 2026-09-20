"""
Pipecat Voice Pipeline — University Admissions Voice Assistant
===============================================================
Chains VAD → STT → RAG enrichment → LLM → TTS into a streaming
Pipecat pipeline loop.

Pipecat 1.6.0 API:
    WhisperSTTService:   pipecat.services.whisper.stt
    KokoroTTSService:    pipecat.services.kokoro.tts
    OLLamaLLMService:    pipecat.services.ollama.llm
    SileroVADAnalyzer:   pipecat.audio.vad.silero
    Pipeline:            pipecat.pipeline.pipeline
    PipelineTask:        pipecat.pipeline.task
    PipelineRunner:      pipecat.pipeline.runner

Architecture:
    Audio In → Silero VAD → Whisper STT → RAG (ChromaDB) → Qwen LLM → Kokoro TTS → Audio Out

Usage (from run_pipeline_test.py or FastAPI transport):
    from app.pipeline import create_local_voice_pipeline
    runner, task = await create_local_voice_pipeline(transport)
"""

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("voice_pipeline")

# ── Configuration ────────────────────────────────────────────────────

CHROMA_DB_PATH = Path(os.environ.get(
    "CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db"),
))
DEFAULT_LLM_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
#: Literal IPv4, not "localhost" -- see app/llm_backend.OLLAMA_BASE_URL.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
STT_MODEL = os.environ.get("WHISPER_MODEL", "small.en")
TTS_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
# Single source of truth: app.llm_backend.DEFAULT_NUM_CTX (env: OLLAMA_NUM_CTX).
# Voice calls run the full production voice system prompt (~3.5k tokens)
# plus RAG context; scripts/predeploy.py sizes it per machine.
from app.llm_backend import DEFAULT_NUM_CTX as NUM_CTX  # noqa: E402

# ── Platform Detection (Multi-GPU Support) ──────────────────────────
from app.platform import detect_compute_device

PLATFORM_CONFIG = detect_compute_device()
DEVICE = PLATFORM_CONFIG["device"]
COMPUTE_TYPE = PLATFORM_CONFIG["compute_type"]
GPU_AVAILABLE = PLATFORM_CONFIG["device"] != "cpu"


# ── Helpers ──────────────────────────────────────────────────────────

def _vram_info() -> str:
    """Human-readable GPU VRAM string for logging (CUDA or Apple unified)."""
    if not GPU_AVAILABLE:
        return "VRAM: N/A"
    try:
        if PLATFORM_CONFIG["device"] == "cuda":
            import torch
            total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            used = torch.cuda.memory_allocated(0) / (1024**3)
            return f"VRAM: {used:.2f} GB used / {total:.2f} GB total"
        if PLATFORM_CONFIG["platform"] == "apple_silicon":
            from app.platform import _get_system_memory_gb
            total = _get_system_memory_gb()
            if total > 0:
                return f"Unified memory: {total:.1f} GB (Metal/MLX shared)"
    except Exception:
        pass
    return "VRAM: N/A"


# ── ChromaDB context retriever ───────────────────────────────────────
# NOTE: retrieval is centralized in app.rag (single source of truth).
# The live voice path uses app.pipeline.run_rag_query_sync -> app.rag.query_rag.
# The local retrieve_context() duplicate was removed 2026-08-14 — use
#   from app.rag import retrieve_context


def build_rag_prompt(transcript: str) -> str:
    """
    Enrich the transcribed user query with ChromaDB context and wrap it
    in the production voice system prompt (app.voice_system_prompt).
    Retrieval stays centralized in app.rag; the voice behavioral rules
    (interruption handling, turn-taking, spoken-output) live in the
    voice prompt so chat interfaces keep their Markdown-oriented prompt.

    This function is called between STT output and LLM input.
    """
    from app.rag import retrieve_context
    from app.voice_system_prompt import build_voice_system_prompt

    context = retrieve_context(transcript)

    prompt = build_voice_system_prompt(context)
    prompt += f"\n\nStudent's question: {transcript}"
    return prompt


# ── Pipeline factory ─────────────────────────────────────────────────

def _build_llm_service():
    """
    Build the Pipecat LLM service for the active backend.

    Ollama (Windows/Linux) -> OLLamaLLMService (unchanged).
    MLX (Apple Silicon)    -> OpenAILLMService pointed at mlx_lm.server
                              (OpenAI-compatible, streaming included).
    Returns None when the service is unavailable or init fails.
    """
    from app.llm_backend import provider_name, MLX_MODEL, MLX_BASE_URL

    try:
        if provider_name() == "mlx":
            from pipecat.services.openai.llm import OpenAILLMService

            llm = OpenAILLMService(
                settings=OpenAILLMService.Settings(model=MLX_MODEL),
                base_url=f"{MLX_BASE_URL}/v1",
                api_key="mlx",  # local server — any non-empty key works
            )
            logger.info(f"  [OK] OpenAILLMService initialized (MLX model={MLX_MODEL})")
            return llm

        from pipecat.services.ollama.llm import OLLamaLLMService, OllamaLLMSettings

        llm = OLLamaLLMService(
            model=DEFAULT_LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
            settings=OllamaLLMSettings(),
        )
        logger.info(f"  [OK] OLLamaLLMService initialized (model={DEFAULT_LLM_MODEL})")
        return llm
    except ImportError:
        logger.warning("  [SKIP] LLM service not available for this backend")
        return None
    except Exception as e:
        logger.warning(f"  [SKIP] LLM init failed: {e}")
        return None


async def create_local_voice_pipeline(transport=None):
    """
    Build and return the full Pipecat voice pipeline.

    Parameters:
        transport: A Pipecat transport instance (e.g. FastAPIWebsocketTransport,
                   TwilioTransport, or a local test transport).
                   If None, creates a pipeline without transport (for testing).

    Returns:
        (PipelineRunner, PipelineTask) tuple.

    Pipecat 1.6.0 pipeline flow:
        transport.input() → VAD → STT → [RAG enrichment] → LLM → TTS → transport.output()
    """
    logger.info("Creating local voice pipeline...")
    logger.info(f"  Device: {DEVICE}, Compute: {COMPUTE_TYPE}")
    logger.info(f"  LLM: {DEFAULT_LLM_MODEL} @ {OLLAMA_BASE_URL}")
    logger.info(f"  {_vram_info()}")

    # ---- 1. Voice Activity Detection ---------------------------------
    # REMOVED, deliberately -- BRD-04: "The system shall have exactly one live
    # end-of-speech decision." This used to construct a SileroVADAnalyzer with
    # `stop_secs=0.5` and log "[OK] SileroVADAnalyzer initialized (for
    # transport config)". It was never attached to anything: the transport is
    # passed in by the caller, so this function cannot reach it, and the
    # analyzer was constructed, logged as live, and dropped on the floor.
    #
    # That left two problems. It cost a Silero model load per pipeline build.
    # And it presented a SECOND end-of-speech delay -- 500 ms -- as the live
    # setting while the one that actually closes a caller's turn is
    # VoiceCallSession's 600 ms (`VAD_SILENCE_MS`). Two delays disagreeing
    # about when a caller stopped talking is exactly what BRD-04 forbids, and
    # the log line made it look reconciled.
    #
    # The live decision is `VAD_SILENCE_MS` in app/voice_handler.py. If a
    # pipecat transport is ever given a VAD again, it must take its delay from
    # that setting rather than declaring a second one here.

    # ---- 2. Speech-to-Text (Faster-Whisper) --------------------------
    try:
        from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
        from app.platform import get_whisper_device_config

        # CTranslate2 has no Metal backend — on Apple Silicon this maps
        # mps -> cpu + int8 (NEON-accelerated) instead of raising.
        stt_device, stt_compute = get_whisper_device_config()
        stt = WhisperSTTService(
            settings=WhisperSTTSettings(model=STT_MODEL),
            device=stt_device,
            compute_type=stt_compute,
        )
        logger.info(f"  [OK] WhisperSTTService initialized (model={STT_MODEL}, device={stt_device})")
    except ImportError:
        logger.warning("  [SKIP] WhisperSTTService not available")
        stt = None
    except Exception as e:
        logger.warning(f"  [SKIP] STT init failed: {e}")
        stt = None

    # ---- 3. LLM (Ollama on Windows/Linux, MLX server on Apple Silicon)
    llm = _build_llm_service()

    # ---- 4. Text-to-Speech (Kokoro) ----------------------------------
    try:
        from pipecat.services.kokoro.tts import KokoroTTSService

        tts = KokoroTTSService(voice=TTS_VOICE)
        logger.info(f"  [OK] KokoroTTSService initialized (voice={TTS_VOICE})")
    except ImportError:
        logger.warning("  [SKIP] KokoroTTSService not available")
        tts = None
    except Exception as e:
        logger.warning(f"  [SKIP] TTS init failed: {e}")
        tts = None

    # ---- 5. Assemble Pipeline ----------------------------------------
    from pipecat.pipeline.pipeline import Pipeline

    # Build the processor list.
    # Note: VAD is deliberately NOT here. In Pipecat 1.6.0 it would live on the
    # transport, but this function is handed a transport it cannot configure,
    # and the end-of-speech decision belongs to VAD_SILENCE_MS (BRD-04) -- see
    # the removal note above.
    processors = []
    if stt is not None:
        processors.append(stt)
    if llm is not None:
        processors.append(llm)
    if tts is not None:
        processors.append(tts)

    if not processors:
        raise RuntimeError(
            "No pipeline services could be initialized. "
            "Check that pipecat-ai[whisper,kokoro] is installed and Ollama is running."
        )

    pipeline = Pipeline(processors)

    # ---- 6. Create Task & Runner -------------------------------------
    try:
        from pipecat.pipeline.task import PipelineTask
        from pipecat.pipeline.runner import WorkerRunner

        task = PipelineTask(pipeline)
        runner = WorkerRunner()

        logger.info(f"  [OK] Pipeline assembled: {len(processors)} processors")
        logger.info(f"  Pipeline: {' → '.join(type(p).__name__ for p in processors)}")
        logger.info(f"  {_vram_info()}")

        return runner, task

    except ImportError as e:
        logger.warning(f"  [WARN] PipelineTask/PipelineRunner not available: {e}")
        logger.info(f"  [OK] Pipeline object created: {len(processors)} processors")
        logger.info(f"  Pipeline: {' → '.join(type(p).__name__ for p in processors)}")
        return None, pipeline


# ── Standalone test ──────────────────────────────────────────────────

# ── Post-call handler ────────────────────────────────────────────────

async def post_call_handler(transcript: str, phone_number: str = "") -> bool:
    """
    Called when a WebSocket voice session disconnects.

    Extracts lead data (name, email, program) from the transcript via
    the local LLM and saves the call record + lead payload to PostgreSQL.

    This function is safe to call even when the database is unavailable
    — it will log a warning and return False.
    """
    try:
        from app.database import handle_post_call
        return await handle_post_call(transcript=transcript, phone_number=phone_number)
    except ImportError:
        logger.warning("app.database not available — post-call save skipped")
        return False
    except Exception:
        logger.exception("post_call_handler failed")
        return False


def _bg_priority_enabled() -> bool:
    """US-017 AC-5: the policy is reversed by one setting, not a code change."""
    return os.environ.get("BG_PRIORITY_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


def run_rag_query_sync(user_text: str, mode: str = "voice",
                       retrieval_query: str | None = None) -> str | None:
    """
    Synchronous RAG query — safe to call from asyncio.to_thread().
    Uses shared RAG module for consistent quality across all interfaces.

    mode: "voice" (default, phone calls) or "chat" (text UIs — Markdown
    SYSTEM_PROMPT, same style as the Streamlit chat).

    retrieval_query (C3/N1): when the caller pre-builds an instruction prompt,
    retrieval embeds this instead of the whole prompt.

    US-017 / BRD-20: `mode` already distinguished the two callers, so it is
    also the admission key. A voice turn holds the gate open for its whole
    duration; a background unit waits for the line to be clear and runs at most
    one at a time. `BRD-20`'s operating scenario is two voice callers plus one
    background request, and this is the single point where that ordering is
    decided.
    """
    from app.rag import query_rag
    if not _bg_priority_enabled():
        return query_rag(user_text, mode=mode, retrieval_query=retrieval_query)

    from app.work_priority import BackgroundDeferred, classify, GATE, VOICE

    work_class, defect = classify(mode)
    if work_class == VOICE:
        label = "voice" if defect is None else f"unclassified:{mode}"
        with GATE.voice_turn(label=label):
            return query_rag(user_text, mode=mode, retrieval_query=retrieval_query)
    try:
        with GATE.background_unit(label="background", mode=mode):
            return query_rag(user_text, mode=mode, retrieval_query=retrieval_query)
    except BackgroundDeferred:
        # Refused because the line stayed busy past its budget. It answers
        # nothing rather than being interleaved -- and this is NOT a
        # caller-facing failure: no caller is waiting on it, and it must not
        # appear in any caller-facing failure figure (AC-2). The gate has
        # already recorded the reason and the wait (TAC-6).
        logger.info("background query deferred past its budget and refused: %r", mode)
        return None


async def test_pipeline_with_text(user_text: str, mode: str = "chat") -> str | None:
    """
    Test the full RAG → LLM chain with a text query (no audio).
    Returns the LLM response string, or None on failure.

    This bypasses STT/TTS and tests only the RAG + LLM path.
    Useful for validating the pipeline without audio hardware.

    US-017 TAC-1: this is inference with nobody waiting to hear it, so it goes
    through the same admission gate as every other unit of work. It used to
    call the backend directly, which made it the one path into the model that
    the priority policy could not see -- a caller's turn and a text query could
    interleave with nothing counting it. `mode` defaults to "chat" because the
    callers of this function are text UIs; pass "voice" for a caller's turn.
    """
    from app.work_priority import BackgroundDeferred, classify, GATE, VOICE

    async def _run() -> str | None:
        prompt = build_rag_prompt(user_text)
        from app.llm_backend import chat as backend_chat

        # `to_thread`, because `backend_chat` is SYNCHRONOUS and performs an
        # HTTP call to the engine. Calling it directly from an `async def`
        # blocks the event loop for the whole inference -- the same defect as
        # using the synchronous gate here, one layer further in, and it was
        # worth 3-5 s of frozen loop per admitted background unit. Fixing the
        # acquisition alone took the 2+1 window from dying at turn 3-18 to
        # dying at 68-70; this is the rest of it.
        answer = await asyncio.to_thread(
            backend_chat,
            messages=[{"role": "user", "content": prompt}],
            preferred=[DEFAULT_LLM_MODEL],
            num_ctx=NUM_CTX,
        )
        logger.info(f"LLM response: {answer[:100]}...")
        return answer

    work_class, _ = classify(mode)
    try:
        if work_class == VOICE:
            with GATE.voice_turn(label="text:voice"):
                return await _run()
        # `background_unit_async`, NOT `background_unit`. This function is
        # `async`, so it runs on the event loop; the synchronous gate blocks on
        # a threading.Condition, which would freeze the loop -- and with it
        # every live voice call -- for as long as the deferral lasted. Both
        # callers died on keepalive timeout before this was changed.
        async with GATE.background_unit_async(label="text", mode=mode):
            return await _run()
    except BackgroundDeferred:
        logger.info("text query refused: the line stayed busy past its budget")
        return None
    except Exception:
        logger.exception("RAG + LLM test failed")
        return None
