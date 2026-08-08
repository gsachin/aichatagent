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
    st.markdown("### 📄 Upload Documents")
    st.markdown("Upload your transcript, ID proof, or marksheets to apply for admission.")
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "png", "jpg", "jpeg"],
        key="sidebar_doc_upload",
        label_visibility="collapsed",
    )
    doc_type = st.selectbox("Document Type", ["transcript", "id_proof", "marksheet", "other"], key="sidebar_doc_type")
    if uploaded_file and st.button("📤 Upload Document", key="sidebar_upload_btn"):
        lead_id = st.session_state.get("lead_id", "")
        if not lead_id:
            # Create a lead first
            phone = f"streamlit_{st.session_state.get('user_name', 'user')}"
            try:
                import requests, json as _json
                resp = requests.post(
                    "http://localhost:8000/api/leads",
                    json={"phone_number": phone, "source": "streamlit", "name": st.session_state.get("user_name", "")},
                    timeout=10,
                )
                if resp.ok:
                    lead = resp.json()
                    st.session_state["lead_id"] = lead["id"]
                    lead_id = lead["id"]
            except Exception:
                pass
        if lead_id:
            try:
                import requests, uuid as _uuid
                boundary = f"----streamlit-{_uuid.uuid4().hex}"
                parts = [
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"doc_type\"\r\n\r\n{doc_type}\r\n",
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{uploaded_file.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n",
                ]
                header = "".join(parts).encode("utf-8")
                footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
                body = header + uploaded_file.getvalue() + footer
                resp = requests.post(
                    f"http://localhost:8000/api/leads/{lead_id}/documents",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    timeout=30,
                )
                if resp.ok:
                    data = resp.json()
                    if data.get("offer_letter"):
                        st.success("✅ Document uploaded! Offer letter generated and sent.")
                    else:
                        st.success("✅ Document uploaded successfully!")
                else:
                    st.error("Upload failed. Please try again.")
            except Exception as e:
                st.error(f"Upload error: {e}")
        else:
            st.warning("Send a message in the chat first so we can create your profile.")
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
    from langchain_ollama import ChatOllama
    from langchain_classic.chains import create_retrieval_chain
    from langchain_classic.chains.combine_documents import create_stuff_documents_chain
    from langchain_core.prompts import ChatPromptTemplate

    # Use shared RAG module — single source of truth for all interfaces
    from app.rag import get_retriever, SYSTEM_PROMPT

    retriever = get_retriever()
    if retriever is None:
        st.error("Failed to initialize vector store. Check that the PDF exists.")
        st.stop()

    # Use the VRAM-optimized Qwen instruct model
    llm = ChatOllama(model="qwen2.5:7b-instruct-q3_K_M", temperature=0.0, num_ctx=2048)

    system_prompt = SYSTEM_PROMPT

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


def truncate_for_tts(text: str, max_chars: int = 800) -> str:
    """Truncate text at a sentence boundary for natural-sounding TTS."""
    if len(text) <= max_chars:
        return text
    # Find the last sentence-ending punctuation before the limit
    truncated = text[:max_chars]
    for punct in (". ", "? ", "! ", ".\n", "?\n", "!\n"):
        last = truncated.rfind(punct)
        if last > max_chars * 0.6:  # Only use if reasonably close to limit
            return text[:last + 1]
    # Fallback: break at last space
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return text[:last_space] + "..."
    return truncated + "..."


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
    greeting = "Hello! I'm your University Admissions Advisor."
    # Check if we have lead info — if not, ask
    st.session_state.setdefault("lead_collected", False)
    st.session_state.setdefault("lead_name", "")
    st.session_state.setdefault("lead_email", "")
    st.session_state.setdefault("lead_phone", "")
    st.session_state.setdefault("lead_program", "")
    st.session_state.setdefault("lead_id", "")
    st.session_state.setdefault("show_apply_prompt", False)
    st.session_state.setdefault("awaiting_field", None)  # 'name','email','phone','program','qualification','awaiting_docs'
    if not st.session_state.lead_collected:
        greeting += " Before we start, could you tell me your name?"
        st.session_state.awaiting_field = "name"
    st.session_state.messages = [
        {"role": "assistant", "content": greeting}
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
                        tts_text = truncate_for_tts(answer)
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
        msg = prompt.strip()
        msg_lower = msg.lower()
        awaiting = st.session_state.get("awaiting_field")
        answer = None

        # ── Helper: detect admission intent in message ─────────
        admission_keywords = [
            "i want to take admission", "i want admission", "take addmission",
            "i want to enroll", "ready to enroll", "sign me up",
            "take admission", "want admission", "i am ready to take",
            "i'm ready to take", "apply now", "i want to apply",
            "i am ready", "let's proceed", "go ahead", "i want to join",
            "i am interested in joining", "admission",
        ]

        def has_admission_intent(text):
            return any(kw in text for kw in admission_keywords)

        def extract_program(text):
            """Try to extract program name from message."""
            programs = ["computer science", "mba", "data science", "engineering",
                       "business analytics", "information systems"]
            for p in programs:
                if p in text:
                    return p.title() if p != "mba" else "MBA"
            return ""

        # ── Smart name detection: skip if contains admission intent ─
        has_email = "@" in msg and "." in msg.split("@")[-1] if "@" in msg else False
        is_short_name = len(msg.split()) <= 3 and not has_email and "?" not in msg and len(msg) < 60

        # ── State: waiting for name ───────────────────────────
        if awaiting == "name":
            if has_admission_intent(msg_lower):
                # User provided name + admission intent in same message
                # Try to extract just the name part
                name_part = msg
                for kw in admission_keywords:
                    if kw in msg_lower:
                        idx = msg_lower.index(kw)
                        name_part = msg[:idx].strip()
                        break
                if name_part and len(name_part.split()) <= 4:
                    st.session_state.lead_name = name_part
                else:
                    st.session_state.lead_name = msg.split("I want")[0].strip() if "i want" in msg_lower else msg

                st.session_state.awaiting_field = "email"
                prog = extract_program(msg_lower)
                if prog:
                    st.session_state.lead_program = prog
                answer = f"Thanks {st.session_state.lead_name}! And what's your email address?"
            elif is_short_name and not has_admission_intent(msg_lower):
                st.session_state.lead_name = msg
                st.session_state.awaiting_field = "email"
                answer = f"Thanks {msg}! And what's your email address? I'll use it to send you program details."
            else:
                # Message too long for a name — ask again
                answer = f"That's a bit long for a name. Could you just tell me your first and last name?"

        # ── State: waiting for email ──────────────────────────
        elif awaiting == "email":
            if has_email:
                st.session_state.lead_email = msg
                st.session_state.awaiting_field = "phone"
                answer = "Got it! And your phone number? (So an admissions counselor can follow up with you)"
            else:
                answer = "That doesn't look like an email address. Could you share a valid email? (e.g., name@example.com)"

        # ── State: waiting for phone ──────────────────────────
        elif awaiting == "phone":
            st.session_state.lead_phone = msg
            st.session_state.awaiting_field = None
            st.session_state.lead_collected = True
            # Save to backend
            try:
                import requests
                resp = requests.post("http://localhost:8000/api/leads", json={
                    "phone_number": st.session_state.lead_phone,
                    "name": st.session_state.lead_name,
                    "email": st.session_state.lead_email,
                    "source": "streamlit",
                }, timeout=5)
                if resp.ok:
                    lead_data = resp.json()
                    st.session_state["lead_id"] = lead_data.get("id", "")
            except Exception:
                pass
            answer = (
                f"Perfect! I have your info:\n"
                f"- Name: {st.session_state.lead_name}\n"
                f"- Email: {st.session_state.lead_email}\n"
                f"- Phone: {msg}\n\n"
                f"Is this correct? (Type 'yes' or tell me what to change)"
            )

        # ── State: awaiting program confirmation ──────────────
        elif awaiting == "program":
            prog = extract_program(msg_lower)
            if not prog and len(msg.split()) <= 3:
                prog = msg.title()
            if prog:
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                # Update backend
                try:
                    import requests
                    lid = st.session_state.get("lead_id", "")
                    if lid:
                        requests.put(f"http://localhost:8000/api/leads/{lid}",
                            json={"program_interest": prog}, timeout=5)
                except Exception:
                    pass
                # Qualification check
                qual_info = {
                    "MBA": "Bachelor's degree with 50%+ marks, GMAT 550+ (or equivalent), 2+ years work experience preferred",
                    "Computer Science": "Bachelor's in CS or related field with 55%+ marks, programming knowledge, math background",
                    "Data Science": "Bachelor's in any quantitative field with 55%+ marks, basic statistics and programming knowledge",
                    "Engineering": "10+2 with Physics, Chemistry, Math (PCM) 60%+, JEE or equivalent entrance exam",
                    "Business Analytics": "Bachelor's degree with 50%+ marks, basic math/stats background",
                    "Information Systems": "Bachelor's degree with 50%+ marks, basic IT knowledge",
                }
                reqs = qual_info.get(prog, f"Relevant bachelor's degree with 50%+ marks")
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Before we proceed, here are the requirements:\n"
                    f"📋 {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )
            else:
                answer = "I didn't catch the program name. Which program? (e.g., Computer Science, MBA, Data Science)"

        # ── State: awaiting qualification confirmation ─────────
        elif awaiting == "qualification":
            if msg_lower in ("yes", "yeah", "yep", "yes i do", "i do", "i meet", "correct"):
                st.session_state.awaiting_field = "awaiting_docs"
                prog = st.session_state.get("lead_program", "the program")
                answer = (
                    f"Excellent! To process your admission for *{prog}*, please upload:\n\n"
                    "📄 **Transcript / Mark Sheet**\n"
                    "🆔 **ID Proof** (Passport, Aadhaar, etc.)\n\n"
                    "Use the file uploader in the **sidebar** ⬅️ to submit your documents. "
                    "Once uploaded, your offer letter will be generated automatically!"
                )
                st.session_state["show_apply_prompt"] = True
            elif msg_lower in ("no", "nope", "i don't", "not sure"):
                answer = "No worries! You can still explore our programs. Feel free to ask me about admission requirements, alternative programs, or how to prepare your application."
                st.session_state.awaiting_field = None
            else:
                answer = "Please answer 'yes' or 'no' — do you meet the requirements for this program?"

        # ── User confirms info ────────────────────────────────
        elif msg_lower in ("yes", "yeah", "yep", "correct", "right", "ok", "okay"):
            if st.session_state.get("lead_collected"):
                lead_prog = st.session_state.get("lead_program", "")
                if lead_prog:
                    # Already have program — ask if they want to apply
                    answer = (
                        f"Your profile is confirmed! You're interested in *{lead_prog}*.\n\n"
                        f"Would you like to proceed with the application? "
                        f"Just say: 'I want to take admission'"
                    )
                else:
                    answer = (
                        "Great! Your info is confirmed. Now, which program are you interested in? "
                        "(e.g., Computer Science, MBA, Data Science)\n\n"
                        "Or just say: 'I want to take admission in MBA'"
                    )

        # ── Profile not started yet ───────────────────────────
        elif not st.session_state.get("lead_collected") and awaiting is None:
            if has_admission_intent(msg_lower):
                # User wants admission but hasn't given profile yet
                st.session_state.awaiting_field = "name"
                prog = extract_program(msg_lower)
                if prog:
                    st.session_state.lead_program = prog
                answer = "I'd love to help you apply! First, could you tell me your name?"
            else:
                st.session_state.awaiting_field = "name"
                answer = "Hello! I'm your University Admissions Advisor. Before we start, could you tell me your name?"

        # ── Admission intent with profile complete ────────────
        elif has_admission_intent(msg_lower) and st.session_state.get("lead_collected"):
            prog = extract_program(msg_lower) or st.session_state.get("lead_program", "")
            if not prog:
                st.session_state.awaiting_field = "program"
                answer = "Which program are you interested in? (e.g., Computer Science, MBA, Data Science)"
            elif st.session_state.get("awaiting_field") != "qualification":
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                # Update backend with program
                try:
                    import requests
                    lid = st.session_state.get("lead_id", "")
                    if lid:
                        requests.put(f"http://localhost:8000/api/leads/{lid}",
                            json={"program_interest": prog, "status": "in_progress"}, timeout=5)
                except Exception:
                    pass
                qual_info = {
                    "MBA": "Bachelor's degree with 50%+ marks, GMAT 550+ (or equivalent), 2+ years work experience preferred",
                    "Computer Science": "Bachelor's in CS or related field with 55%+ marks, programming knowledge, math background",
                    "Data Science": "Bachelor's in any quantitative field with 55%+ marks, basic statistics and programming knowledge",
                    "Engineering": "10+2 with PCM 60%+, JEE or equivalent entrance exam",
                    "Business Analytics": "Bachelor's degree with 50%+ marks, basic math/stats background",
                    "Information Systems": "Bachelor's degree with 50%+ marks, basic IT knowledge",
                }
                reqs = qual_info.get(prog, "Relevant bachelor's degree with 50%+ marks")
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Here are the requirements:\n📋 {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )
            else:
                # Already in qualification flow
                pass

        # ── User mentions a program name directly ─────────────
        elif st.session_state.get("lead_collected"):
            prog = extract_program(msg_lower)
            if prog and len(msg.split()) <= 5:
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                try:
                    import requests
                    lid = st.session_state.get("lead_id", "")
                    if lid:
                        requests.put(f"http://localhost:8000/api/leads/{lid}",
                            json={"program_interest": prog}, timeout=5)
                except Exception:
                    pass
                qual_info = {
                    "MBA": "Bachelor's degree with 50%+ marks, GMAT 550+ (or equivalent), 2+ years work experience preferred",
                    "Computer Science": "Bachelor's in CS or related field with 55%+ marks, programming knowledge, math background",
                    "Data Science": "Bachelor's in any quantitative field with 55%+ marks, basic statistics and programming knowledge",
                    "Engineering": "10+2 with PCM 60%+, JEE or equivalent entrance exam",
                    "Business Analytics": "Bachelor's degree with 50%+ marks, basic math/stats background",
                    "Information Systems": "Bachelor's degree with 50%+ marks, basic IT knowledge",
                }
                reqs = qual_info.get(prog, "Relevant bachelor's degree with 50%+ marks")
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Here are the requirements:\n📋 {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )

        # ── RAG fallback ──────────────────────────────────────
        if answer is None:
            with st.spinner("Thinking..."):
                try:
                    response = st.session_state.rag_chain.invoke({"input": prompt})
                    answer = response["answer"]
                except Exception as e:
                    answer = f"Something went wrong: {e}\n\nMake sure Ollama is still running."

        st.markdown(answer)

        # ── Generate TTS audio ─
        audio_for_msg = None
        if st.session_state.tts_enabled:
            with st.spinner("🔊 Generating audio..."):
                try:
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
