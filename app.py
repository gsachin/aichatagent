"""
University Admissions Advisor — Streamlit Web UI with Voice Support
====================================================================
Text + Voice chat interface powered by local AI.
Run:    streamlit run app.py
        (or: python -m streamlit run app.py --server.headless true)
"""

import os
import sys
import io
import warnings
import tempfile
import requests
import streamlit as st
import numpy as np

warnings.filterwarnings("ignore")
os.environ["HF_HUB_ENABLE_HF_XET"] = "0"

# ── Page config ────────────────────────────────────────────────────
st.set_page_config(
    page_title="University Admissions Advisor",
    page_icon="🎓",
    layout="centered"
)

# ── Sidebar ────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 Admissions Advisor")
    st.markdown("---")
    st.markdown("### About")
    st.markdown(
        "An AI-powered university admissions assistant. "
        "Ask questions about **UMD** and **FDU** programs, "
        "tuition, admission requirements, and more."
    )
    st.markdown("---")
    st.markdown("### Voice Mode")
    st.markdown("🎤 **Click the mic button** below the chat to ask questions by voice.")
    st.session_state.setdefault("tts_enabled", True)
    tts_enabled = st.checkbox("🔊 Speak responses aloud", value=st.session_state.tts_enabled)
    st.session_state.tts_enabled = tts_enabled
    st.markdown("---")
    st.markdown("### Powered by")
    st.markdown("🐪 **Qwen 2.5 7B** (local LLM)")
    st.markdown("👂 **Faster-Whisper** (local STT)")
    st.markdown("📚 **UMD & FDU** university profiles")
    st.markdown("---")
    st.caption("All data stays on your machine. No internet required.")

# ── Title ──────────────────────────────────────────────────────────
st.title("🎓 University Admissions Advisor")
st.caption("Ask me anything about UMD or FDU — type or use your voice.")

# ── Load RAG chain (cached, runs once) ─────────────────────────────
@st.cache_resource(show_spinner=False)
def load_rag_chain():
    """Initialize the full RAG pipeline and return the chain."""
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from langchain_ollama import OllamaEmbeddings, ChatOllama
    from langchain_classic.chains import create_retrieval_chain
    from langchain_classic.chains.combine_documents import create_stuff_documents_chain
    from langchain_core.prompts import ChatPromptTemplate

    pdf_path = os.path.join(os.getcwd(), "content", "sample_data", "UMD_and_FDU_University_Profile_Report.pdf")
    if not os.path.exists(pdf_path):
        alt = "content/sample_data/UMD_and_FDU_University_Profile_Report.pdf"
        if os.path.exists(alt):
            pdf_path = alt
        else:
            st.error(f"PDF not found. Make sure the file exists at: {pdf_path}")
            st.stop()

    loader = PyPDFLoader(pdf_path)
    raw_docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
    chunks = splitter.split_documents(raw_docs)

    embeddings = OllamaEmbeddings(model="nomic-embed-text")
    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory="./chroma_local_db"
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": 3})

    # Use the VRAM-optimized Qwen model
    llm = ChatOllama(model="qwen2.5:7b", temperature=0.0, num_ctx=2048)

    system_prompt = (
        "You are a warm, helpful, and highly precise University Admissions Advisor. "
        "Your goal is to guide students through their inquiries using ONLY the provided university profile context.\n\n"
        "CONVERSATIONAL TONE RULES:\n"
        "1. Be encouraging, professional, and approachable.\n"
        "2. Avoid mechanical language. Use natural transitions.\n"
        "3. Keep responses concise and easy to read.\n"
        "4. When responding to voice input, keep answers brief and spoken-word friendly.\n\n"
        "STRICT FACTUAL CONSTRAINTS:\n"
        "1. Rely EXCLUSIVELY on the provided context. If a detail isn't in the text, politely say so.\n"
        "2. Never confuse details between UMD and FDU.\n"
        "3. Present structural information (tuition, deadlines, courses) in Markdown.\n\n"
        "Context:\n{context}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])

    qa_chain = create_stuff_documents_chain(llm, prompt)
    return create_retrieval_chain(retriever, qa_chain)


# ── Load STT model (cached) ────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def load_stt_model():
    """Load the Faster-Whisper model for speech-to-text."""
    from faster_whisper import WhisperModel
    from app.platform import detect_compute_device

    platform_config = detect_compute_device()
    device = platform_config["device"]
    compute_type = platform_config["compute_type"]
    model = WhisperModel("small.en", device=device, compute_type=compute_type)
    return model


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


@st.cache_resource(show_spinner=False)
def load_tts():
    """Load Kokoro TTS engine (local onnx, no internet needed)."""
    try:
        from kokoro_onnx import Kokoro
        cache_dir = os.path.expanduser(r"~\.cache\pipecat\kokoro-onnx")
        model_path = os.path.join(cache_dir, "kokoro-v1.0.onnx")
        voices_path = os.path.join(cache_dir, "voices-v1.0.bin")
        return Kokoro(model_path, voices_path)
    except Exception:
        return None


def text_to_audio_bytes(text: str, voice: str = "af_heart") -> bytes:
    """Convert text to WAV audio bytes using Kokoro TTS."""
    kokoro = load_tts()
    if kokoro is None:
        raise RuntimeError("Kokoro TTS not available")
    audio, sr = kokoro.create(text, voice=voice, speed=1.0)
    # Convert float32 → int16 PCM, then wrap in WAV container
    audio_int16 = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    import wave
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    buf.seek(0)
    return buf.read()


# ── Transcribe audio ────────────────────────────────────────────────
def transcribe_audio(audio_bytes: bytes, sample_rate: int = 16000) -> str:
    """Convert raw PCM audio to text using local Whisper."""
    if len(audio_bytes) < 1000:
        return ""

    # Convert bytes to numpy float32 array
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    model = load_stt_model()
    segments, _ = model.transcribe(audio_np, beam_size=5)
    transcript = " ".join(seg.text for seg in segments).strip()

    return transcript


# ── Initialize ─────────────────────────────────────────────────────
if "rag_chain" not in st.session_state:
    with st.spinner("🔧 Loading PDF, building vector store, booting Qwen 2.5 7B..."):
        # Quick health check
        try:
            r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
            if r.status_code != 200:
                st.error("❌ Ollama responded unexpectedly. Is it running?")
                st.stop()
        except requests.ConnectionError:
            st.error("❌ Cannot reach Ollama. Make sure the app is running in your system tray.")
            st.stop()

        st.session_state.rag_chain = load_rag_chain()
        st.success("✅ Bot is ready! Type below or click 🎤 to speak.")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hello! I'm your University Admissions Advisor. Ask me anything — by text or voice."}
    ]

# Track which audio messages have been auto-played (prevent replay on rerun)
if "played_audio_idx" not in st.session_state:
    st.session_state.played_audio_idx = -1

# ── Display chat history ───────────────────────────────────────────
for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # Auto-play new audio that hasn't been played yet
        if msg.get("audio") and i > st.session_state.played_audio_idx:
            st.audio(msg["audio"], format="audio/wav", autoplay=True)
            st.session_state.played_audio_idx = i

# ── Voice input (audio_input) with loop guard ────────────────────
# Dynamic key prevents infinite loop: st.audio_input persists data
# across st.rerun() — changing the key creates a fresh widget each time.
if "voice_key" not in st.session_state:
    st.session_state.voice_key = 0

audio_value = st.audio_input(
    "🎤 Click to ask by voice",
    key=f"voice_{st.session_state.voice_key}"
)

if audio_value is not None:
    # Show what was recorded
    with st.chat_message("user"):
        st.audio(audio_value)
        with st.spinner("👂 Transcribing..."):
            # Read the audio bytes and convert to PCM for Whisper
            audio_bytes = audio_value.read()

            # Streamlit's audio_input returns audio in the browser's format.
            # Convert to 16kHz mono 16-bit PCM for Whisper.
            try:
                import soundfile as sf
                audio_np, orig_sr = sf.read(io.BytesIO(audio_bytes))
                # Convert to mono if stereo
                if audio_np.ndim > 1:
                    audio_np = audio_np.mean(axis=1)
                # Resample to 16kHz if needed
                if orig_sr != 16000 and len(audio_np) > 0:
                    from scipy.signal import resample
                    audio_np = resample(audio_np, int(len(audio_np) * 16000 / orig_sr))
                # Convert to int16
                audio_16k = (np.clip(audio_np, -1, 1) * 32767).astype(np.int16)
                transcript = transcribe_audio(audio_16k.tobytes(), 16000)
            except Exception as e:
                # Fallback: try reading as raw WAV
                try:
                    import wave
                    with wave.open(io.BytesIO(audio_bytes), "rb") as w:
                        pcm = w.readframes(w.getnframes())
                        transcript = transcribe_audio(pcm, w.getframerate())
                except Exception:
                    st.error(f"Audio processing error: {e}")
                    transcript = ""

        if transcript:
            st.markdown(f"🗣️ *{transcript}*")
        else:
            st.warning("Could not transcribe audio. Please speak clearly and try again.")
            transcript = ""

    if transcript:
        # Process through the RAG chain
        st.session_state.messages.append({"role": "user", "content": f"🎤 {transcript}"})

        with st.chat_message("assistant"):
            with st.spinner("🤔 Thinking..."):
                try:
                    response = st.session_state.rag_chain.invoke({"input": transcript})
                    answer = response["answer"]
                except Exception as e:
                    answer = f"⚠️ Something went wrong: {e}\n\nMake sure Ollama is still running."

            st.markdown(answer)

            # ── Generate TTS audio (store in message for chat history playback) ─
            audio_for_msg = None
            if st.session_state.tts_enabled:
                with st.spinner("🔊 Generating audio..."):
                    try:
                        # Truncate long text to keep TTS generation time reasonable
                        tts_text = answer[:500] + "..." if len(answer) > 500 else answer
                        audio_for_msg = text_to_audio_bytes(tts_text)
                    except Exception as e:
                        st.warning(f"Audio generation skipped: {e}")

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "audio": audio_for_msg
            })

        st.session_state.voice_key += 1  # Reset widget to break rerun loop
        st.rerun()

# ── Text input ─────────────────────────────────────────────────────
if prompt := st.chat_input("Ask about admissions, tuition, programs..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Searching..."):
            try:
                response = st.session_state.rag_chain.invoke({"input": prompt})
                answer = response["answer"]
            except Exception as e:
                answer = f"⚠️ Something went wrong: {e}\n\nMake sure Ollama is still running."

        st.markdown(answer)

        # ── Generate TTS audio (store in message for chat history playback) ─
        audio_for_msg = None
        if st.session_state.tts_enabled:
            with st.spinner("🔊 Generating audio..."):
                try:
                    # Truncate long text to keep TTS generation time reasonable
                    tts_text = answer[:500] + "..." if len(answer) > 500 else answer
                    audio_for_msg = text_to_audio_bytes(tts_text)
                except Exception as e:
                    st.warning(f"Audio generation skipped: {e}")

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "audio": audio_for_msg
        })

# ── Clear chat button ──────────────────────────────────────────────
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🗑️ Clear Chat"):
        st.session_state.messages = [
            {"role": "assistant", "content": "Hello! I'm your University Admissions Advisor. Ask me anything — by text or voice."}
        ]
        st.rerun()
