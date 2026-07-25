"""
University Admissions Advisor — Streamlit Web UI
===============================================
Run:    streamlit run app.py
"""

import os
import sys
import warnings
import requests
import streamlit as st

warnings.filterwarnings("ignore")

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
    st.markdown("### Powered by")
    st.markdown("🐪 **Qwen 2.5 7B** (local LLM)")
    st.markdown("📚university profiles")
    st.markdown("---")
    st.caption("All data stays on your machine. No internet required.")

# ── Title ──────────────────────────────────────────────────────────
st.title("🎓 University Admissions Advisor")
st.caption("Ask me anything about UMD or FDU — admissions, tuition, programs, and more.")

# ── Load the RAG chain (cached, runs only once) ────────────────────
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

    llm = ChatOllama(model="qwen2.5:7b", temperature=0.0)

    system_prompt = (
        "You are a warm, helpful, and highly precise University Admissions Advisor. "
        "Your goal is to guide students through their inquiries using ONLY the provided university profile context.\n\n"
        "CONVERSATIONAL TONE RULES:\n"
        "1. Be encouraging, professional, and approachable.\n"
        "2. Avoid mechanical language. Use natural transitions.\n"
        "3. Keep responses concise and easy to read.\n\n"
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
        st.success("✅ Bot is ready!")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hello! I'm your University Admissions Advisor. How can I help you today?"}
    ]

# ── Display chat history ───────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Handle user input ──────────────────────────────────────────────
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
        st.session_state.messages.append({"role": "assistant", "content": answer})

# ── Clear chat button ──────────────────────────────────────────────
if st.button("🗑️ Clear Chat"):
    st.session_state.messages = [
        {"role": "assistant", "content": "Hello! I'm your University Admissions Advisor. How can I help you today?"}
    ]
    st.rerun()
