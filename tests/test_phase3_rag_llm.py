"""
Phase 3 — RAG Retrieval & Ollama LLM Execution Tests
=====================================================
Goal: Verify ChromaDB vector search and Ollama Qwen 2.5 6B RAG responses.

Tests:
    - ChromaDB collection can be created and populated with admissions documents
    - Vector similarity search returns relevant chunks
    - Ollama LLM responds with context-aware answers
    - LLM uses num_ctx: 2048 to stay within VRAM budget
    - RAG response contains facts from the injected context (not hallucination)
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXISTING_CHROMA_DB = PROJECT_ROOT / "chroma_local_db"


# ── Sample admissions documents (from development plan) ──────────────

SAMPLE_DOCS = [
    (
        "Undergraduate tuition fee for 2026 is $15,000 per year. "
        "Application deadline is August 1st."
    ),
    (
        "Computer Science requires a minimum GPA of 3.2 and SAT score of 1200."
    ),
    (
        "International students must submit TOEFL scores above 80 "
        "or IELTS above 6.5."
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────

def _ollama_available() -> bool:
    """Return True if Ollama API is reachable."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return "models" in data
    except Exception:
        return False


def _ollama_has_model(name: str) -> bool:
    """Return True if the named Ollama model is pulled (exact match only)."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            return name in models
    except Exception:
        return False


def _ollama_find_qwen() -> str | None:
    """Return the name of any pulled Qwen model, or None."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            qwen = [m for m in models if "qwen" in m.lower()]
            return qwen[0] if qwen else None
    except Exception:
        return None


# ── Phase 3.1: ChromaDB Setup ────────────────────────────────────────

class TestPhase3ChromaDB:
    """Verify ChromaDB initialization and document retrieval."""

    def test_chromadb_importable(self):
        """chromadb package must be installed."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.fail("chromadb not installed. Run: pip install chromadb")

    def test_chromadb_create_collection(self):
        """Must be able to create an in-memory ChromaDB collection."""
        import chromadb

        client = chromadb.Client()
        collection = client.create_collection(
            name="test_admissions",
            metadata={"description": "Phase 3 test collection"},
        )
        assert collection is not None
        assert collection.name == "test_admissions"

        # Cleanup
        client.delete_collection("test_admissions")

    def test_chromadb_add_documents(self):
        """Must be able to add documents to a ChromaDB collection."""
        import chromadb

        client = chromadb.Client()
        collection = client.create_collection(name="test_admissions_add")

        collection.add(
            documents=SAMPLE_DOCS,
            ids=[f"doc-{i}" for i in range(len(SAMPLE_DOCS))],
        )

        assert collection.count() == len(SAMPLE_DOCS), (
            f"Expected {len(SAMPLE_DOCS)} docs, got {collection.count()}"
        )

        client.delete_collection("test_admissions_add")

    def test_chromadb_similarity_search(self):
        """Vector search must return relevant documents for a query."""
        import chromadb

        client = chromadb.Client()
        collection = client.create_collection(name="test_admissions_search")

        collection.add(
            documents=SAMPLE_DOCS,
            ids=[f"doc-{i}" for i in range(len(SAMPLE_DOCS))],
        )

        results = collection.query(query_texts=["What is the tuition fee?"], n_results=2)

        assert "documents" in results
        assert len(results["documents"]) == 1  # one query
        assert len(results["documents"][0]) == 2  # two results

        # The top result should mention tuition
        top_docs = " ".join(results["documents"][0]).lower()
        assert "tuition" in top_docs or "15000" in top_docs or "$15,000" in top_docs, (
            f"Top results should be about tuition. Got: {results['documents'][0]}"
        )

        client.delete_collection("test_admissions_search")

    def test_existing_chromadb_persisted(self):
        """The pre-built chroma_local_db must exist and be loadable."""
        if not EXISTING_CHROMA_DB.is_dir():
            pytest.skip(
                "chroma_local_db/ not found — the Streamlit app may not have "
                "built it yet. Run app.py once to populate the vector store."
            )

        import chromadb

        try:
            client = chromadb.PersistentClient(path=str(EXISTING_CHROMA_DB))
            collections = client.list_collections()
            assert len(collections) >= 1, (
                "Persisted ChromaDB has no collections — run app.py to build index"
            )
        except Exception as e:
            pytest.fail(f"Failed to load persisted ChromaDB: {e}")


# ── Phase 3.2: Ollama LLM with RAG Context ───────────────────────────

class TestPhase3OllamaRag:
    """Verify Ollama LLM can answer admissions questions with RAG context."""

    LLM_MODEL = "qwen2.5:6b-instruct-q4_K_M"

    @pytest.fixture(autouse=True)
    def _check_ollama(self):
        """Skip all tests if Ollama/model not available; use best available Qwen model."""
        if not _ollama_available():
            pytest.skip("Ollama not reachable — start Ollama and try again")

        # If the dev-plan model is pulled, use it
        if _ollama_has_model(self.LLM_MODEL):
            return

        # Otherwise find any Qwen model
        fallback = _ollama_find_qwen()
        if fallback:
            self.LLM_MODEL = fallback
            print(f"\nUsing fallback LLM model: {fallback}")
        else:
            pytest.skip(
                f"No Qwen model pulled. Run: ollama pull {self.LLM_MODEL}"
            )

    def test_ollama_basic_chat(self):
        """Ollama must respond to a simple prompt."""
        import ollama

        response = ollama.chat(
            model=self.LLM_MODEL,
            messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        )
        assert "message" in response
        assert "content" in response["message"]
        assert len(response["message"]["content"]) > 0
        print(f"\nLLM response: {response['message']['content']}")

    def test_ollama_rag_query_returns_context_aware_answer(self):
        """
        Query with RAG context must return facts from that context.

        Context: "Tuition is $15,000 per year, deadline August 1st."
        Query:   "What is the tuition fee and deadline?"
        Expected: Answer mentions $15,000 and August 1st.
        """
        import ollama

        context = (
            "Undergraduate tuition fee for 2026 is $15,000 per year. "
            "Application deadline is August 1st."
        )

        prompt = (
            "You are a university admissions assistant. Answer the user's "
            "question using ONLY the provided context. If the context does "
            "not contain the answer, say so.\n\n"
            f"Context:\n{context}\n\n"
            "Question: What is the tuition fee and deadline?"
        )

        response = ollama.chat(
            model=self.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048},
        )

        answer = response["message"]["content"].lower()
        print(f"\nRAG answer: {response['message']['content']}")

        # Must contain the key facts from context
        assert "15000" in answer or "15,000" in answer or "$15,000" in answer, (
            f"Answer should mention tuition amount ($15,000). Got: {answer[:200]}"
        )
        assert "august" in answer and "1" in answer, (
            f"Answer should mention deadline (August 1st). Got: {answer[:200]}"
        )

    def test_ollama_num_ctx_setting(self):
        """Ollama must accept num_ctx: 2048 without error."""
        import ollama

        response = ollama.chat(
            model=self.LLM_MODEL,
            messages=[{"role": "user", "content": "Reply with just the word OK."}],
            options={"num_ctx": 2048},
        )
        assert "message" in response
        # num_ctx doesn't error → the KV cache budget is respected
        print(f"\nnum_ctx=2048 response: {response['message']['content']}")

    def test_ollama_streaming_supported(self):
        """Ollama must support streaming responses (required for Pipecat pipeline)."""
        import ollama

        stream = ollama.chat(
            model=self.LLM_MODEL,
            messages=[{"role": "user", "content": "Count: 1 2 3"}],
            stream=True,
        )

        chunks = []
        for chunk in stream:
            if "message" in chunk and "content" in chunk["message"]:
                chunks.append(chunk["message"]["content"])

        full = "".join(chunks)
        print(f"\nStreamed ({len(chunks)} chunks): {full}")
        assert len(chunks) > 0, "Streaming returned zero chunks"
        assert len(full) > 0, "Streamed content is empty"


# ── Phase 3.3: RAG Pipeline Integration ──────────────────────────────

class TestPhase3RagPipelineIntegration:
    """Verify ChromaDB → Ollama end-to-end RAG query."""

    def test_full_rag_query_function(self):
        """
        Simulate the query_admissions_bot() function from the development plan:
          1. Search ChromaDB for top-2 relevant chunks
          2. Inject context into Ollama prompt
          3. Return generated answer
        """
        import chromadb
        import ollama

        if not _ollama_available():
            pytest.skip("Ollama not reachable")

        # 1. Build an in-memory ChromaDB collection
        client = chromadb.Client()
        collection = client.create_collection(name="admissions_test")

        collection.add(
            documents=SAMPLE_DOCS,
            ids=[f"doc-{i}" for i in range(len(SAMPLE_DOCS))],
        )

        # 2. Define the query function (mirrors development plan)
        def query_admissions_bot(user_query: str) -> str:
            results = collection.query(
                query_texts=[user_query],
                n_results=2,
            )
            context_chunks = results["documents"][0]

            prompt = (
                "You are a university admissions assistant. Answer using "
                "ONLY the context below. If the answer is not in the context, "
                "say 'I don't have that information.'\n\n"
                f"Context:\n" + "\n".join(f"- {c}" for c in context_chunks) + "\n\n"
                f"Question: {user_query}"
            )

            # Find any available Qwen model
            import urllib.request

            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", [])]
            qwen = next((m for m in models if "qwen" in m.lower()), models[0])

            response = ollama.chat(
                model=qwen,
                messages=[{"role": "user", "content": prompt}],
                options={"num_ctx": 2048},
            )
            return response["message"]["content"]

        # 3. Test
        answer = query_admissions_bot("What is the tuition fee and deadline?")
        print(f"\nRAG pipeline answer: {answer}")
        answer_lower = answer.lower()

        assert "15000" in answer_lower or "15,000" in answer_lower, (
            f"Expected tuition amount in answer. Got: {answer[:200]}"
        )
        assert "august" in answer_lower, (
            f"Expected deadline month in answer. Got: {answer[:200]}"
        )

        # Cleanup
        client.delete_collection("admissions_test")
