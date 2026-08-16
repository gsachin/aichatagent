"""
University Admissions Advisor Bot
=================================
Previously on Google Colab → Adapted for Windows local machine.

Run:   python admissions_bot.py
"""

import os
import sys
import warnings

warnings.filterwarnings("ignore")

# ── Step 0: Verify LLM backend (Ollama or MLX) ─────────────────────
print("--> Checking if AI server is running...")
from app.llm_backend import is_ready, list_models, provider_name

if is_ready():
    models = list_models()
    print(f"   Active ({provider_name()}). Models: {', '.join(models) or '(none listed)'}")
else:
    print(f"   ERROR: {provider_name()} backend not reachable. "
          f"Is it running? (start_services.sh / start_services.ps1)")
    sys.exit(1)

# ── Step 1: Load the shared vector store ────────────────────────────
# LEGACY NOTE (2026-08-14): this bot no longer ingests documents.
# It previously built its own Chroma store from the old UMD/FDU PDF at
# 800/150 chunking, which polluted the shared store with duplicate
# generations. Ingestion is now owned exclusively by
# scripts/rebuild_rag_index.py; this bot only READS the shared store.

from app.rag import get_retriever

retriever = get_retriever()
if retriever is None:
    print("   ERROR: Vector store not found. Run scripts/rebuild_rag_index.py first.")
    sys.exit(1)
print("   Vector store loaded (shared Meridian store).")

# ── Step 3: Build RAG chain ────────────────────────────────────────
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from app.llm_backend import get_chat_model

print(f"\n--> Booting Qwen 2.5 (temp=0.0) via {provider_name()}...")
llm = get_chat_model(model="qwen2.5:7b", temperature=0.0)

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
    "2. Rely on Meridian University facts only.\n"
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
    # CLI mode: pass question as argument, e.g.  python admissions_bot.py "tell me about the Meridian MBA"
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
