"""
Phase 3 — RAG Retrieval & Ollama LLM Manual Test
==================================================
Demonstrates ChromaDB vector search + Ollama Qwen 2.5 for contextual
university admissions responses.

Usage:
    python test_rag_llm.py

What it does:
    1. Creates an in-memory ChromaDB collection with 3 sample admissions docs
    2. Defines query_admissions_bot(user_query) using RAG + Ollama
    3. Tests: "What is the tuition fee and deadline?"
    4. Prints the full RAG pipeline output

Pass criteria:
    - LLM responds with "$15,000 per year" and "August 1st"
    - VRAM usage stays ~4.0 GB (on GPU machine)
"""

import json
import sys
import urllib.request

# ── Configuration ────────────────────────────────────────────────────

DEV_PLAN_MODEL = "qwen2.5:6b-instruct-q4_K_M"
OLLAMA_URL = "http://127.0.0.1:11434"
NUM_CTX = 2048  # restricted context window to preserve GPU memory

SAMPLE_DOCS = [
    "Undergraduate tuition fee for 2026 is $15,000 per year. "
    "Application deadline is August 1st.",
    "Computer Science requires a minimum GPA of 3.2 and SAT score of 1200.",
    "International students must submit TOEFL scores above 80 "
    "or IELTS above 6.5.",
]


# ── Helpers ──────────────────────────────────────────────────────────

def check_ollama() -> str | None:
    """Return the best available Qwen model name, or None if Ollama is down."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]

        # Prefer the exact dev-plan model
        if DEV_PLAN_MODEL in models:
            return DEV_PLAN_MODEL

        # Fall back to any Qwen
        qwen = [m for m in models if "qwen" in m.lower()]
        if qwen:
            return qwen[0]

        # Last resort: use whatever is available
        if models:
            return models[0]

        return None
    except Exception as e:
        print(f"[ERROR] Ollama not reachable: {e}")
        return None


def _vram_report(label: str = "") -> None:
    """Print current GPU VRAM usage."""
    try:
        import torch
        if torch.cuda.is_available():
            total = torch.cuda.get_device_properties(0).total_mem / (1024**3)
            used = torch.cuda.memory_allocated(0) / (1024**3)
            reserved = torch.cuda.memory_reserved(0) / (1024**3)
            free = total - reserved
            tag = f"[{label}] " if label else ""
            print(f"  {tag}VRAM: {used:.2f} GB used, "
                  f"{reserved:.2f} GB reserved, "
                  f"{free:.2f} GB free / {total:.2f} GB total")
        else:
            print(f"  [{label}] VRAM: N/A (no CUDA GPU)")
    except ImportError:
        pass


# ── Main ─────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print("  Phase 3 — RAG + LLM Integration Test")
    print("=" * 60)
    print()

    # ---- Check Ollama ----
    model = check_ollama()
    if model is None:
        print("[FAIL] Ollama is not running or no models are pulled.")
        print("       Start Ollama and pull a Qwen model:")
        print(f"       ollama pull {DEV_PLAN_MODEL}")
        return 1

    print(f"  Using model: {model}")
    print(f"  num_ctx: {NUM_CTX}")
    print()

    # ---- VRAM before ----
    _vram_report("before")

    # ---- Step 1: Build ChromaDB collection ----
    print("-- Step 1: Build ChromaDB collection --")

    import chromadb

    client = chromadb.Client()
    collection = client.create_collection(name="admissions")

    collection.add(
        documents=SAMPLE_DOCS,
        ids=[f"doc-{i}" for i in range(len(SAMPLE_DOCS))],
    )
    print(f"  [OK] Added {collection.count()} documents to 'admissions' collection")
    print()

    # ---- Step 2: Define RAG query function ----
    print("-- Step 2: Define query_admissions_bot() --")

    import ollama

    def query_admissions_bot(user_query: str) -> str:
        """
        Search ChromaDB for top-2 relevant chunks, inject context into
        prompt, and return the LLM-generated answer.
        """
        # Retrieve top-2 context chunks
        results = collection.query(
            query_texts=[user_query],
            n_results=2,
        )
        context_chunks = results["documents"][0]

        # Build the prompt
        prompt = (
            "You are a university admissions assistant. Answer the user's "
            "question using ONLY the provided context. If the context does "
            "not contain the answer, say so.\n\n"
            "Context:\n"
            + "\n".join(f"- {c}" for c in context_chunks)
            + f"\n\nQuestion: {user_query}"
        )

        # Query Ollama
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": NUM_CTX},
        )
        return response["message"]["content"]

    print("  [OK] query_admissions_bot() defined")
    print()

    # ---- Step 3: Test query ----
    print("-- Step 3: Test query --")

    test_query = "What is the tuition fee and deadline?"
    print(f"  Query: \"{test_query}\"")

    answer = query_admissions_bot(test_query)
    print(f"  Answer: \"{answer}\"")
    print()

    # ---- Step 4: Validate ----
    print("-- Step 4: Validate response --")
    answer_lower = answer.lower()

    checks = [
        ("Tuition amount ($15,000)", "15000" in answer_lower or "15,000" in answer_lower or "$15,000" in answer_lower),
        ("Deadline (August 1st)", "august" in answer_lower and "1" in answer_lower),
    ]

    all_ok = True
    for label, ok in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] {label}")

    print()

    # ---- VRAM after ----
    _vram_report("after")

    # ---- Summary ----
    print()
    print("=" * 60)
    if all_ok:
        print("  Phase 3 PASSED!")
        print("  RAG pipeline returns context-aware answers from ChromaDB.")
        print("  Ready for Phase 4 (Full Pipecat Pipeline).")
    else:
        print("  Phase 3: Some checks failed.")
        print("  The model may need a different prompt or the context may")
        print("  not have been injected correctly. Check the output above.")
    print("=" * 60)

    # Cleanup
    client.delete_collection("admissions")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
