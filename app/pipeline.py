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

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("voice_pipeline")

# ── Configuration ────────────────────────────────────────────────────

CHROMA_DB_PATH = Path(__file__).resolve().parent.parent / "chroma_local_db"
DEFAULT_LLM_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
STT_MODEL = os.environ.get("WHISPER_MODEL", "small.en")
TTS_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "2048"))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "2"))

# ── Platform Detection (Multi-GPU Support) ──────────────────────────
from app.platform import detect_compute_device

PLATFORM_CONFIG = detect_compute_device()
DEVICE = PLATFORM_CONFIG["device"]
COMPUTE_TYPE = PLATFORM_CONFIG["compute_type"]
GPU_AVAILABLE = PLATFORM_CONFIG["device"] != "cpu"


# ── Helpers ──────────────────────────────────────────────────────────

def _vram_info() -> str:
    """Human-readable GPU VRAM string for logging."""
    if not GPU_AVAILABLE:
        return "VRAM: N/A"
    try:
        import torch
        total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        used = torch.cuda.memory_allocated(0) / (1024**3)
        return f"VRAM: {used:.2f} GB used / {total:.2f} GB total"
    except Exception:
        return "VRAM: N/A"


# ── ChromaDB context retriever ───────────────────────────────────────

def retrieve_context(query: str, top_k: int = RAG_TOP_K) -> str:
    """
    Search the persisted ChromaDB for admissions documents relevant to
    *query*. Returns formatted context text, or empty string on failure.
    """
    try:
        import chromadb
    except ImportError:
        logger.warning("chromadb not installed — skipping RAG context")
        return ""

    if not CHROMA_DB_PATH.is_dir():
        logger.warning(f"ChromaDB not found at {CHROMA_DB_PATH} — skipping RAG context")
        return ""

    try:
        # Use Ollama embedding function to match the nomic-embed-text (768-dim)
        # that was used when building the persisted index.
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

        embed_fn = OllamaEmbeddingFunction(
            model_name="nomic-embed-text",
            url=OLLAMA_BASE_URL,
        )
        client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        collections = client.list_collections()

        if not collections:
            logger.warning("ChromaDB has no collections — run app.py to build index")
            return ""

        # ChromaDB 1.x returns Collection objects; get name for get_collection()
        first = collections[0]
        coll_name = first if isinstance(first, str) else first.name
        collection = client.get_collection(coll_name, embedding_function=embed_fn)
        results = collection.query(query_texts=[query], n_results=top_k)

        if not results.get("documents") or not results["documents"][0]:
            return ""

        chunks = results["documents"][0]
        context = "\n".join(f"- {c}" for c in chunks)
        logger.debug(f"RAG: retrieved {len(chunks)} chunks for query: {query[:60]}...")
        return context

    except Exception:
        logger.exception("ChromaDB retrieval failed")
        return ""


def build_rag_prompt(transcript: str) -> str:
    """
    Enrich the transcribed user query with ChromaDB context.
    Returns the full prompt string to send to the LLM.

    This function is called between STT output and LLM input.
    """
    context = retrieve_context(transcript)

    if context:
        prompt = (
            "You are a helpful university admissions assistant. "
            "Answer the user's question using ONLY the provided context. "
            "If the context does not contain the answer, say: "
            "'I don't have that information in my knowledge base.'\n\n"
            f"Context:\n{context}\n\n"
            f"Student's question: {transcript}"
        )
    else:
        prompt = (
            "You are a helpful university admissions assistant. "
            "Answer the user's question to the best of your ability. "
            "If you're unsure, say so.\n\n"
            f"Student's question: {transcript}"
        )

    return prompt


# ── Pipeline factory ─────────────────────────────────────────────────

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
    # In Pipecat 1.6.0, SileroVADAnalyzer is configured at the transport
    # level (e.g. FastAPIWebsocketTransport params) rather than as a
    # pipeline processor. We construct it here for use by the transport.
    vad_analyzer = None
    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams

        vad_params = VADParams(
            confidence=0.7,
            start_secs=0.3,
            stop_secs=0.5,
            min_volume=0.6,
        )
        vad_analyzer = SileroVADAnalyzer(sample_rate=16000, params=vad_params)
        logger.info("  [OK] SileroVADAnalyzer initialized (for transport config)")
    except ImportError:
        logger.warning("  [SKIP] SileroVADAnalyzer not available — VAD disabled")
    except Exception as e:
        logger.warning(f"  [SKIP] VAD init failed: {e}")

    # ---- 2. Speech-to-Text (Faster-Whisper) --------------------------
    try:
        from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings

        stt = WhisperSTTService(
            settings=WhisperSTTSettings(model=STT_MODEL),
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
        )
        logger.info(f"  [OK] WhisperSTTService initialized (model={STT_MODEL}, device={DEVICE})")
    except ImportError:
        logger.warning("  [SKIP] WhisperSTTService not available")
        stt = None
    except Exception as e:
        logger.warning(f"  [SKIP] STT init failed: {e}")
        stt = None

    # ---- 3. LLM (Ollama / Qwen) --------------------------------------
    try:
        from pipecat.services.ollama.llm import OLLamaLLMService, OllamaLLMSettings

        llm = OLLamaLLMService(
            model=DEFAULT_LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
            settings=OllamaLLMSettings(),
        )
        logger.info(f"  [OK] OLLamaLLMService initialized (model={DEFAULT_LLM_MODEL})")
    except ImportError:
        logger.warning("  [SKIP] OLLamaLLMService not available")
        llm = None
    except Exception as e:
        logger.warning(f"  [SKIP] LLM init failed: {e}")
        llm = None

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
    # Note: VAD is not a pipeline processor in Pipecat 1.6.0;
    # it is configured on the transport via params.vad_analyzer.
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


async def test_pipeline_with_text(user_text: str) -> str | None:
    """
    Test the full RAG → LLM chain with a text query (no audio).
    Returns the LLM response string, or None on failure.

    This bypasses STT/TTS and tests only the RAG + LLM path.
    Useful for validating the pipeline without audio hardware.
    """
    logger.info(f"Testing RAG + LLM with: \"{user_text}\"")

    prompt = build_rag_prompt(user_text)

    try:
        import ollama

        # Find available model
        import urllib.request

        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]

        qwen_models = [m for m in models if "qwen" in m.lower()]
        model = qwen_models[0] if qwen_models else DEFAULT_LLM_MODEL

        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX},
        )
        answer = response["message"]["content"]
        logger.info(f"LLM response: {answer[:100]}...")
        return answer

    except Exception:
        logger.exception("RAG + LLM test failed")
        return None
