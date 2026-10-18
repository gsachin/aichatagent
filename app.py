"""
University Admissions Advisor — Streamlit Web UI (text chat)
=============================================================
Text chat interface powered by OpenAI + the full Meridian knowledge base.
Run:    streamlit run app.py
        (or: python -m streamlit run app.py --server.headless true)
"""

import os
import re
import uuid
import warnings
import threading
import requests
import streamlit as st

warnings.filterwarnings("ignore")
os.environ["HF_HUB_ENABLE_HF_XET"] = "0"

# Load the project .env from the repo root. Anchored to this file so CWD does
# not matter.
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from app.streamlit_backend import backend_healthy, sync_lead, sync_program
from app.offers.service import missing_fields_text
from app.programs import detect_program, is_explicit_program_choice

# Eligibility per canonical program, taken verbatim from the Meridian
# knowledge base (content/meridian/meridian_knowledge_base.md). Keyed on the
# names app.programs returns.
#
# Three copies of this table used to sit inline in the state machine, keyed on
# "Computer Science", "Data Science", "Engineering", "Business Analytics" and
# "Information Systems" — none of which the detector can return, and three of
# which Meridian does not offer — so every lookup fell through to the generic
# line and the eligibility the student was shown was never the KB's.
PROGRAM_ELIGIBILITY = {
    "MBA": "a bachelor's degree",
    "MA": "a bachelor's degree",
    "M.Tech": "a B.Tech or B.E. degree",
    "MCA": "a bachelor's degree in computing",
    "M.Com": "a bachelor's degree in commerce",
    "M.Sc": "a bachelor's degree in science",
    "B.Tech": "10+2 with Physics, Chemistry and Mathematics (PCM)",
    "B.Tech Computer Science": "10+2 with Physics, Chemistry and Mathematics (PCM)",
    "B.Tech AI & Machine Learning": "10+2 with Physics, Chemistry and Mathematics (PCM)",
    "B.Tech Information Technology": "10+2 with Physics, Chemistry and Mathematics (PCM)",
    "B.Sc": "10+2 with PCM or PCB",
    "BBA": "10+2 in any stream",
    "BCA": "10+2 in any stream",
    "B.Com": "10+2 in any stream",
    "BA": "10+2 in any stream",
}
DEFAULT_ELIGIBILITY = "a relevant bachelor's degree"


# ── Public backend URL resolution ───────────────────────────────────

def _resolve_public_backend() -> str:
    """
    Base URL for user-facing backend links (e.g. offer-letter PDFs).

    The chat UI is served through its own Cloudflare tunnel, so links to
    backend endpoints must use a host the user can actually reach — the
    FastAPI tunnel host — not localhost.

    Order: DASHBOARD_API_URL env var (set by start_services), TUNNEL_HOST
    env var, .whatsapp_tunnel file, .tunnel_8000 file, localhost fallback.
    """
    env_url = os.environ.get("DASHBOARD_API_URL", "") or os.environ.get("TUNNEL_HOST", "")
    if env_url:
        return env_url if env_url.startswith("http") else f"https://{env_url}"

    root = os.path.dirname(os.path.abspath(__file__))
    for fname in (".whatsapp_tunnel", ".tunnel_8000"):
        fpath = os.path.join(root, fname)
        try:
            with open(fpath) as fh:
                host = fh.read().strip()
        except OSError:
            host = ""
        if host:
            return f"https://{host}"

    return "http://localhost:8000"


# ── Background helper: log interaction + trigger sentiment ──────────

def _with_answer_language(text: str) -> str:
    """Append the selected answer-language rule to the chain input."""
    code = st.session_state.get("answer_language", "en")
    if not code or code == "en":
        return text
    from app.voice_system_prompt import language_instruction

    return f"{text}\n\n{language_instruction(code)}"


def _log_and_score_async(phone: str, transcript: str, channel: str,
                         lead_id: str = "", conversation_id: str = ""):
    """
    Fire-and-forget: POST the exchange to the backend so it gets
    logged to the conversations table and sentiment-scored.

    Runs in a daemon thread — never blocks the UI.

    *conversation_id* is this chat's own id. Without it every logged exchange
    becomes its own conversation row, which is both wrong and — since Salesforce
    names the conversation by that id — impossible to reconcile with the CRM.
    """
    def _post():
        try:
            payload = {
                "phone_number": phone,
                "transcript": transcript,
                "channel": channel,
            }
            if lead_id:
                payload["lead_id"] = lead_id
            if conversation_id:
                payload["conversation_id"] = conversation_id
            requests.post(
                "http://localhost:8000/api/interactions/log",
                json=payload,
                timeout=15,
            )
        except Exception:
            pass  # Never let logging failures surface to the user

    t = threading.Thread(target=_post, daemon=True)
    t.start()

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
        "Ask questions about **Meridian University** programs, "
        "tuition, admission requirements, and more."
    )
    st.markdown("---")
    st.markdown("### Answer Language")
    _language_name = st.selectbox(
        "The assistant will answer in this language",
        ["English", "Hindi", "Gujarati"],
        index=0,
        key="answer_language_name",
    )
    st.session_state["answer_language"] = {
        "English": "en", "Hindi": "hi", "Gujarati": "gu"
    }[_language_name]
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
            # Create or find lead, including program_interest if known
            phone = st.session_state.get("lead_phone", f"streamlit_{st.session_state.get('lead_name', 'user')}")
            lid, err = sync_lead(
                name=st.session_state.get("lead_name", ""),
                email=st.session_state.get("lead_email", ""),
                phone=phone,
                program=st.session_state.get("lead_program", ""),
                conversation_id=st.session_state.get("conversation_id", ""),
            )
            if lid:
                st.session_state["lead_id"] = lid
                lead_id = lid
            if err:
                st.error(f"Cannot reach backend server at localhost:8000. Make sure it's running. ({err})")
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
                    readiness = data.get("offer_readiness")
                    if data.get("offer_letter"):
                        offer = data["offer_letter"]
                        offer_id = offer.get("id", "")
                        pdf_url = f"{_resolve_public_backend()}/api/offers/{offer_id}/pdf"
                        st.success(f"✅ Document uploaded! Offer letter for {offer.get('program','')} generated and sent!")
                        st.markdown(f"📄 [**Click here to view/download your Offer Letter (PDF)**]({pdf_url})")
                        st.balloons()
                        st.session_state["offer_generated"] = True
                        st.session_state["show_apply_prompt"] = False
                        st.session_state["awaiting_field"] = None
                    elif readiness and readiness.get("missing"):
                        st.success("✅ Document uploaded! Your file is saved, but your offer is on hold.")
                        st.warning(
                            f"Before I can generate your offer letter, I still need: "
                            f"**{missing_fields_text(readiness['missing'])}**."
                        )
                        if readiness["missing"] == ["program_interest"]:
                            st.session_state["awaiting_field"] = "program"
                            st.info("Just type the program you want, e.g. *MBA* or *B.Tech Computer Science*.")
                    else:
                        st.success("✅ Document uploaded successfully! To trigger an offer letter, make sure you've set your program interest in the chat.")
                else:
                    st.error(f"Upload failed (HTTP {resp.status_code}). Please try again.")
            except Exception as e:
                st.error(f"Upload error: {e}")
        else:
            st.warning("Please complete the chat conversation first (name, email, phone) so we can create your profile.")
    st.markdown("---")
    st.markdown("### Powered by")
    st.markdown("☁️ **OpenAI** (remote LLM)")
    st.markdown("👂 **Faster-Whisper** (multilingual STT)")
    st.markdown("📚 **Meridian University** knowledge base")
    st.markdown("---")
    st.caption(
        "Prompts, call transcripts and the knowledge base are sent to the "
        "configured OpenAI endpoint."
    )

# ── Title ──────────────────────────────────────────────────────────
st.title("🎓 University Admissions Advisor")
st.caption("Ask me anything about Meridian University — type your question below.")

# ── Load RAG chain (cached, runs once) ─────────────────────────────
@st.cache_resource(show_spinner=False)
def load_rag_chain():
    """Build the document-context chat chain.

    RAG was removed: the retriever returns the whole configured document, so
    the Streamlit surface follows the same no-embeddings path as voice/text.
    """
    from langchain_classic.chains import create_retrieval_chain
    from langchain_classic.chains.combine_documents import create_stuff_documents_chain
    from langchain_core.prompts import ChatPromptTemplate

    from app.config import settings

    # Use shared RAG module — single source of truth for all interfaces
    from app.rag import SYSTEM_PROMPT
    from app.llm_backend import get_chat_model

    from langchain_core.documents import Document
    from langchain_core.runnables import RunnableLambda

    from app.doc_context import document_context

    retriever = RunnableLambda(
        lambda _input: [Document(page_content=document_context())]
    )

    # OpenAI is the only provider; LLM_MODEL is resolved inside the adapter.
    # Do not pass temperature: reasoning models reject non-default values.
    llm = get_chat_model(num_ctx=None)

    system_prompt = SYSTEM_PROMPT

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])

    qa_chain = create_stuff_documents_chain(llm, prompt)
    return create_retrieval_chain(retriever, qa_chain)


# ── Initialize ─────────────────────────────────────────────────────
if "rag_chain" not in st.session_state:
    with st.spinner("🔧 Loading the knowledge base and connecting the LLM..."):
        # Quick LLM backend health check (OpenAI, Ollama or MLX server)
        from app.config import ConfigurationError
        from app.llm_backend import is_ready, provider_name

        try:
            active_provider = provider_name()
        except ConfigurationError as exc:
            st.error(f"❌ {exc}")
            st.stop()

        if not is_ready():
            if active_provider == "openai":
                st.error("❌ Cannot reach the OpenAI API. Check LLM_API_KEY / LLM_BASE_URL.")
            elif active_provider == "mlx":
                st.error("❌ Cannot reach the MLX server. Start it with start_services.sh.")
            else:
                st.error("❌ Cannot reach the configured LLM provider. Check LLM_API_KEY / LLM_BASE_URL.")
            st.stop()

        st.session_state.rag_chain = load_rag_chain()
        st.success("✅ Bot is ready! Type your question below.")

# ── Backend-health banner (prevents silent data loss) ──────────────────
@st.cache_data(ttl=10)
def _backend_reachable() -> bool:
    return backend_healthy()

if not _backend_reachable():
    st.warning(
        "⚠️ The backend server (port 8000) is not running. Your profile "
        "and offer letter can't be saved until it's up. Start it with:\n\n"
        "`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`"
    )

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
    # Conversation id for this chat session, stable for the life of the browser
    # tab. Salesforce needs it at conversation *start* — lookup-or-create is the
    # only call that returns a userId, so it is the one chance to name this chat
    # (plan §8.2). Streamlit keeps its own session, so it is not registered in
    # app.crm.session, which serves the server-side channels.
    st.session_state.setdefault("conversation_id", str(uuid.uuid4()))
    if not st.session_state.lead_collected:
        greeting += " Before we start, could you tell me your name?"
        st.session_state.awaiting_field = "name"
    st.session_state.messages = [
        {"role": "assistant", "content": greeting}
    ]

# ── Display chat history ───────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

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

        def _looks_like_phone(text: str) -> bool:
            # At least 7 digits; "+", spaces, dashes and parentheses allowed.
            # "Yes" (the live-chat transcript) must not become a phone number.
            return len(re.sub(r"\D", "", text or "")) >= 7

        def _looks_like_question(text: str) -> bool:
            """A knowledge question should reach the chain, not the name gate."""
            lowered = (text or "").lower()
            if "?" in text:
                return True
            if re.search(
                r"\b(list|programmes?|programs?|fees?|tuition|deadline|eligib"
                r"ility|apply|admission|documents?)\b",
                lowered,
            ):
                return True
            return any(
                marker in text for marker in
                ("क्या", "सूची", "कितनी", "फीस", "प्रवेश", "कार्यक्रम",
                 "શું", "યાદી", "કેટલી", "ફી", "પ્રવેશ", "કાર્યક્રમ")
            )

        _SKIP_WORDS = {"skip", "later", "no", "not now", "maybe later"}

        def extract_program(text):
            """
            Canonical Meridian program name for a message, or ''.

            Delegates to app.programs, the same detector the WhatsApp webhook
            uses. The local list this replaces was ["computer science", "mba",
            "data science", "engineering", "business analytics", "information
            systems"] — four of which Meridian does not run — and matched by
            raw substring, so "i am from mumbai" returned MBA.
            """
            return detect_program(text)

        # ── Smart name detection: skip if contains admission intent ─
        has_email = "@" in msg and "." in msg.split("@")[-1] if "@" in msg else False
        is_short_name = len(msg.split()) <= 3 and not has_email and "?" not in msg and len(msg) < 60

        # ── State: waiting for name ───────────────────────────
        if awaiting == "name":
            if msg_lower in _SKIP_WORDS:
                # Let the visitor use the assistant without giving a profile.
                st.session_state.lead_collected = True
                st.session_state.awaiting_field = None
                answer = (
                    "No problem — ask me anything about programmes, fees, "
                    "eligibility or applying."
                )
            elif _looks_like_question(msg):
                # Answer the question now; the name prompt remains pending for
                # a later turn. Previously any long message was rejected as
                # "too long for a name", including knowledge questions.
                answer = None
            elif has_admission_intent(msg_lower):
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
        elif awaiting == "phone" and msg_lower not in _SKIP_WORDS \
                and not _looks_like_phone(msg):
            answer = (
                "That doesn't look like a phone number. Please share a number "
                "with at least 7 digits, or type 'skip'."
            )
        elif awaiting == "phone":
            phone_value = (
                f"streamlit_{uuid.uuid4().hex[:12]}"
                if msg_lower in _SKIP_WORDS else msg
            )
            st.session_state.lead_phone = phone_value
            st.session_state.awaiting_field = None
            st.session_state.lead_collected = True
            # Save to backend — include program_interest if already known
            lid, err = sync_lead(
                name=st.session_state.get("lead_name", ""),
                email=st.session_state.get("lead_email", ""),
                phone=phone_value,
                program=st.session_state.get("lead_program", ""),
                lead_id=st.session_state.get("lead_id", ""),
                conversation_id=st.session_state.get("conversation_id", ""),
            )
            if lid:
                st.session_state["lead_id"] = lid
            if err:
                st.warning(
                    f"⚠️ I saved your profile here, but the server couldn't "
                    f"be reached ({err}). Your offer letter won't generate "
                    f"until it's back — you can still chat, and we'll retry later."
                )
            answer = (
                f"Perfect! I have your info:\n"
                f"- Name: {st.session_state.lead_name}\n"
                f"- Email: {st.session_state.lead_email}\n"
                f"- Phone: {phone_value}\n\n"
                f"Is this correct? (Type 'yes' or tell me what to change)"
            )

        # ── State: awaiting program confirmation ──────────────
        elif awaiting == "program":
            prog = extract_program(msg_lower)
            # The old fallback here took *any* message of three words or fewer
            # as the program name (`prog = msg.title()`), so "no thanks" became
            # a course called "No Thanks" and was synced to the lead. A program
            # now has to be a program Meridian actually runs.
            if prog:
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                # Update backend
                lid, err = sync_program(
                    prog,
                    name=st.session_state.get("lead_name", ""),
                    email=st.session_state.get("lead_email", ""),
                    phone=st.session_state.get("lead_phone", ""),
                    lead_id=st.session_state.get("lead_id", ""),
                    conversation_id=st.session_state.get("conversation_id", ""),
                )
                if lid:
                    st.session_state["lead_id"] = lid
                if err:
                    st.warning(
                        f"⚠️ I noted *{prog}* here, but couldn't save it to "
                        f"the server ({err}). It'll be synced before your "
                        f"documents are processed."
                    )
                # Qualification check — eligibility comes from the knowledge
                # base, keyed on the canonical program names app.programs
                # returns (see PROGRAM_ELIGIBILITY at the top of this file).
                reqs = PROGRAM_ELIGIBILITY.get(prog, DEFAULT_ELIGIBILITY)
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Before we proceed, here are the requirements:\n"
                    f"📋 Eligibility: {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )
            else:
                answer = "I didn't catch the program name. Which program? (e.g., B.Tech Computer Science, MBA, M.Tech)"

        # ── State: awaiting qualification confirmation ─────────
        elif awaiting == "qualification":
            if msg_lower in ("yes", "yeah", "yep", "yes i do", "i do", "i meet", "correct"):
                # Sync program_interest to backend BEFORE transitioning
                prog = st.session_state.get("lead_program", "")
                if prog:
                    lid, err = sync_program(
                        prog,
                        name=st.session_state.get("lead_name", ""),
                        email=st.session_state.get("lead_email", ""),
                        phone=st.session_state.get("lead_phone", ""),
                        lead_id=st.session_state.get("lead_id", ""),
                        conversation_id=st.session_state.get("conversation_id", ""),
                    )
                    if lid:
                        st.session_state["lead_id"] = lid
                    if err:
                        st.warning(
                            f"⚠️ Backend sync delayed ({err}). Your offer "
                            f"will be generated once the server is reachable."
                        )
                st.session_state.awaiting_field = "awaiting_docs"
                answer = (
                    f"Excellent! To process your admission for *{prog or 'the program'}*, please upload:\n\n"
                    "📄 **Transcript / Mark Sheet**\n"
                    "🆔 **ID Proof** (Passport, Aadhaar, etc.)\n\n"
                    "Use the upload widget **below** ⬇️ to submit your documents. "
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
                    # Already have program — ensure it's synced, then ask to apply
                    sync_program(
                        lead_prog,
                        name=st.session_state.get("lead_name", ""),
                        email=st.session_state.get("lead_email", ""),
                        phone=st.session_state.get("lead_phone", ""),
                        lead_id=st.session_state.get("lead_id", ""),
                        conversation_id=st.session_state.get("conversation_id", ""),
                    )
                    answer = (
                        f"Your profile is confirmed! You're interested in *{lead_prog}*.\n\n"
                        f"Would you like to proceed with the application? "
                        f"Just say: 'I want to take admission'"
                    )
                else:
                    answer = (
                        "Great! Your info is confirmed. Now, which program are you interested in? "
                        "(e.g., B.Tech Computer Science, MBA, M.Tech)\n\n"
                        # The worked example used to be "I want to take admission
                        # in MBA" — the only program ever demoed this way. Real
                        # students copied it verbatim, which is how a lead ends
                        # up stored as MBA before anyone asked for MBA. The
                        # example is now the first program in the list instead.
                        "Or just say: 'I want to take admission in B.Tech Computer Science'"
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
                answer = "Which program are you interested in? (e.g., B.Tech Computer Science, MBA, M.Tech)"
            elif st.session_state.get("awaiting_field") != "qualification":
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                # Update backend with program
                lid, err = sync_program(
                    prog,
                    name=st.session_state.get("lead_name", ""),
                    email=st.session_state.get("lead_email", ""),
                    phone=st.session_state.get("lead_phone", ""),
                    lead_id=st.session_state.get("lead_id", ""),
                    conversation_id=st.session_state.get("conversation_id", ""),
                )
                if lid:
                    st.session_state["lead_id"] = lid
                if err:
                    st.warning(
                        f"⚠️ I noted *{prog}* here, but couldn't save it to "
                        f"the server ({err}). It'll be synced before your "
                        f"documents are processed."
                    )
                reqs = PROGRAM_ELIGIBILITY.get(prog, DEFAULT_ELIGIBILITY)
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Here are the requirements:\n📋 Eligibility: {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )
            else:
                # Already in qualification flow
                pass

        # ── User mentions a program name directly ─────────────
        elif st.session_state.get("lead_collected"):
            prog = extract_program(msg_lower)
            # Only an actual choice sets the course. This branch used to take
            # any mention of five words or fewer, so "is mba good?" enrolled the
            # student in MBA — the same rule the WhatsApp webhook applies.
            if prog and is_explicit_program_choice(msg_lower, prog):
                st.session_state.lead_program = prog
                st.session_state.awaiting_field = "qualification"
                lid, err = sync_program(
                    prog,
                    name=st.session_state.get("lead_name", ""),
                    email=st.session_state.get("lead_email", ""),
                    phone=st.session_state.get("lead_phone", ""),
                    lead_id=st.session_state.get("lead_id", ""),
                    conversation_id=st.session_state.get("conversation_id", ""),
                )
                if lid:
                    st.session_state["lead_id"] = lid
                if err:
                    st.warning(
                        f"⚠️ I noted *{prog}* here, but couldn't save it to "
                        f"the server ({err}). It'll be synced before your "
                        f"documents are processed."
                    )
                reqs = PROGRAM_ELIGIBILITY.get(prog, DEFAULT_ELIGIBILITY)
                answer = (
                    f"**{prog}** — great choice! 🎓\n\n"
                    f"Here are the requirements:\n📋 Eligibility: {reqs}\n\n"
                    f"Do you meet these requirements? (yes/no)"
                )

        # ── RAG fallback ──────────────────────────────────────
        if answer is None:
            with st.spinner("Thinking..."):
                try:
                    response = st.session_state.rag_chain.invoke(
                        {"input": _with_answer_language(prompt)}
                    )
                    answer = response["answer"]
                except Exception as e:
                    answer = f"Something went wrong: {e}\n\nMake sure LLM_API_KEY and LLM_MODEL are configured (OpenAI)."

        st.markdown(answer)

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
        })

        # ── Log interaction + trigger sentiment scoring ────────────
        exchange = f"User: {prompt}\nAssistant: {answer}"
        _log_and_score_async(
            phone=st.session_state.get("lead_phone", ""),
            transcript=exchange,
            channel="streamlit",
            lead_id=st.session_state.get("lead_id", ""),
            conversation_id=st.session_state.get("conversation_id", ""),
        )

# ── In-chat document upload (below chat, appears when ready for docs) ─
if st.session_state.get("awaiting_field") == "awaiting_docs" or st.session_state.get("show_apply_prompt"):
    st.markdown("---")
    st.markdown("### 📄 Upload Your Documents")
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        chat_upload = st.file_uploader(
            "Choose transcript, marksheet, or ID proof",
            type=["pdf", "png", "jpg", "jpeg"],
            key="chat_doc_upload",
            label_visibility="collapsed",
        )
    with col2:
        chat_doc_type = st.selectbox("Type", ["transcript", "id_proof", "marksheet", "other"], key="chat_doc_type2")
    with col3:
        st.markdown("<br>", unsafe_allow_html=True)
        upload_clicked = st.button("📤 Upload", key="chat_upload_btn2", use_container_width=True, type="primary")

    if chat_upload and upload_clicked:
        lead_id = st.session_state.get("lead_id", "")
        if not lead_id:
            phone = st.session_state.get("lead_phone", f"streamlit_{st.session_state.get('lead_name', 'user')}")
            lid, err = sync_lead(
                name=st.session_state.get("lead_name", ""),
                email=st.session_state.get("lead_email", ""),
                phone=phone,
                program=st.session_state.get("lead_program", ""),
                conversation_id=st.session_state.get("conversation_id", ""),
            )
            if lid:
                st.session_state["lead_id"] = lid
                lead_id = lid
            if err:
                st.error(f"Cannot connect to backend: {err}")
        if lead_id:
            try:
                import requests, uuid as _uuid
                boundary = f"----chat-{_uuid.uuid4().hex}"
                parts = [
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"doc_type\"\r\n\r\n{chat_doc_type}\r\n",
                    # The chat's conversation id, so the offer this upload
                    # triggers is logged against this conversation — the same
                    # one Salesforce was given — instead of a second, unrelated
                    # row.
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"conversation_id\"\r\n\r\n{st.session_state.get('conversation_id', '')}\r\n",
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{chat_upload.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n",
                ]
                body = "".join(parts).encode("utf-8") + chat_upload.getvalue() + f"\r\n--{boundary}--\r\n".encode("utf-8")
                resp = requests.post(
                    f"http://localhost:8000/api/leads/{lead_id}/documents",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    timeout=30,
                )
                if resp.ok:
                    data = resp.json()
                    readiness = data.get("offer_readiness")
                    if data.get("offer_letter"):
                        offer = data["offer_letter"]
                        offer_id = offer.get("id", "")
                        pdf_url = f"{_resolve_public_backend()}/api/offers/{offer_id}/pdf"
                        st.success(f"🎓 Offer letter for *{offer.get('program','')}* generated!")
                        st.markdown(f"📄 [**Click here to view/download your Offer Letter (PDF)**]({pdf_url})")
                        st.balloons()
                        st.session_state["offer_generated"] = True
                        st.session_state["awaiting_field"] = None
                        st.session_state["show_apply_prompt"] = False
                    elif readiness and readiness.get("missing"):
                        st.success("✅ Document uploaded! Your file is saved, but your offer is on hold.")
                        st.warning(
                            f"Before I can generate your offer letter, I still need: "
                            f"**{missing_fields_text(readiness['missing'])}**."
                        )
                        if readiness["missing"] == ["program_interest"]:
                            st.session_state["awaiting_field"] = "program"
                            st.info("Just type the program you want, e.g. *MBA* or *B.Tech Computer Science*.")
                    else:
                        st.success("✅ Document uploaded! You can upload more or start asking questions.")
                else:
                    st.error(f"Upload failed (HTTP {resp.status_code})")
            except Exception as e:
                st.error(f"Upload error: {e}")

# ── Clear chat button ──────────────────────────────────────────────
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🗑️ Clear Chat"):
        st.session_state.messages = [
            {"role": "assistant", "content": "Hello! I'm your University Admissions Advisor. Ask me anything about programmes, fees, eligibility or applying."}
        ]
        st.rerun()
