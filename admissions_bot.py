"""
University Admissions Advisor Bot
=================================
Previously on Google Colab → Adapted for Windows local machine.

Run:   python admissions_bot.py
"""

import os
import sys
import warnings
import requests

warnings.filterwarnings("ignore")

# ── Step 0: Verify Ollama ──────────────────────────────────────────
print("--> Checking if AI server is running...")
try:
    resp = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
    if resp.status_code == 200:
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"   Active. Models: {', '.join(models)}")
    else:
        print(f"   ERROR: Unexpected status {resp.status_code}")
        sys.exit(1)
except requests.ConnectionError:
    print("   ERROR: AI not reachable. Is it running in system tray?")
    sys.exit(1)

# ── Step 1: Load & chunk PDF ───────────────────────────────────────
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

pdf_path = os.path.join(os.getcwd(), "content", "sample_data", "UMD_and_FDU_University_Profile_Report.pdf")
print(f"\n--> Loading PDF: {pdf_path}")

if not os.path.exists(pdf_path):
    # fallback
    alt = "content/sample_data/UMD_and_FDU_University_Profile_Report.pdf"
    if os.path.exists(alt):
        pdf_path = alt
    else:
        print(f"   ERROR: PDF not found. Tried: {pdf_path}, {alt}")
        sys.exit(1)

loader = PyPDFLoader(pdf_path)
raw_docs = loader.load()
print(f"   Loaded {len(raw_docs)} pages.")

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
chunks = splitter.split_documents(raw_docs)
print(f"   Split into {len(chunks)} chunks.")

# ── Step 2: Build vector store ─────────────────────────────────────
from langchain_community.vectorstores import Chroma
from langchain_ollama import OllamaEmbeddings

print("\n--> Building vector embeddings (nomic-embed-text)...")
embeddings = OllamaEmbeddings(model="nomic-embed-text")
vector_store = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_local_db"
)
retriever = vector_store.as_retriever(search_kwargs={"k": 3})
print("   Vector store ready.")

# ── Step 3: Build RAG chain ────────────────────────────────────────
from langchain_ollama import ChatOllama
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

print("\n--> Booting Qwen 2.5 7B (temp=0.0)...")
llm = ChatOllama(model="qwen2.5:7b", temperature=0.0)

system_prompt = (
    "You are a warm, helpful, and highly precise University Admissions Advisor. "
    "Your goal is to guide students through their inquiries using ONLY the provided university profile context.\n\n"
    "CONVERSATIONAL TONE RULES:\n"
    "1. Be encouraging, professional, and approachable. Treat the student like a welcome addition to our community.\n"
    "2. Avoid purely mechanical robotic language. Use natural transitions.\n"
    "3. Keep your overall responses concise and easy to read.\n\n"
    "STRICT FACTUAL CONSTRAINTS:\n"
    "1. Rely EXCLUSIVELY on the provided context. If a detail isn't in the text, "
    "politely say so.\n"
    "2. Never merge or confuse details between UMD and FDU.\n"
    "3. Present structural information (tuition, deadlines, courses) in Markdown.\n\n"
    "Context:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

qa_chain = create_stuff_documents_chain(llm, prompt)
rag_chain = create_retrieval_chain(retriever, qa_chain)

def ask(question: str):
    """Send a question to the RAG bot and return the answer."""
    print(f"   --> Searching context and generating response...")
    response = rag_chain.invoke({"input": question})
    return response["answer"]

# ── Step 4: Chat ───────────────────────────────────────────────────
if len(sys.argv) > 1:
    # CLI mode: pass question as argument, e.g.  python admissions_bot.py "tell me about UMD"
    question = " ".join(sys.argv[1:])
    print("\n" + "=" * 60)
    print(f"   Student: {question}")
    print("=" * 60)
    try:
        answer = ask(question)
        print(f"\nAdvisor:\n{answer}")
        print("-" * 60)
    except Exception as e:
        print(f"\n   ERROR: {e}")
        print("   Is AI still running in system tray?")
else:
    # Interactive mode
    print("\n" + "=" * 60)
    print("   Bot is ready! Type your questions below.")
    print('   Type "exit" to quit.')
    print("=" * 60)
    while True:
        user_input = input("\nStudent: ")
        if user_input.lower().strip() in ("exit", "quit"):
            print("\nGoodbye!")
            break
        if not user_input.strip():
            continue
        try:
            answer = ask(user_input)
            print(f"\nAdvisor:\n{answer}")
            print("-" * 60)
        except Exception as e:
            print(f"\n   ERROR: {e}")
            print("   Is AI still running in system tray?")
            break
