"""C3 (Meridian KB repopulation) app-side contract tests:
E5 embed-endpoint repair, N1 retrieval-query decoupling, collection-name config."""
import asyncio

import pytest


def test_e5_held_embedding_function_posts_modern_batch_endpoint(monkeypatch):
    """The held EF must post {"input": [...], keep_alive} to /api/embed and read
    "embeddings" — the legacy /api/embeddings mix-up (returns {"embedding": []})
    silently killed the hybrid path."""
    from app.llm_backend import get_embedding_function

    ef = get_embedding_function()
    posted = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1] * 4, [0.2] * 4]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)
    out = ef(["a", "b"])
    assert posted["url"].endswith("/api/embed")
    assert posted["json"]["input"] == ["a", "b"]
    assert posted["json"]["keep_alive"] is not None
    # chromadb may normalise the EF result to numpy — compare values, not type
    import numpy as np
    assert np.allclose(np.asarray(out, dtype=float), [[0.1] * 4, [0.2] * 4])


def _patch_rag_llm(monkeypatch):
    import app.llm_backend as lb
    import app.rag_legacy as rl

    monkeypatch.setattr(rl, "_get_available_model", lambda: "m")
    monkeypatch.setattr(lb, "chat", lambda **kwargs: "answer")


def test_n1_query_rag_retrieves_on_retrieval_query(monkeypatch):
    """retrieve_context must receive retrieval_query, not the full prompt."""
    import app.rag as rag

    seen = {}

    def fake_retrieve(query):
        seen["query"] = query
        return "[§ Fees]\n$18,500"

    monkeypatch.setattr(rag, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(rag, "RAG_SIMILARITY_THRESHOLD", 0.0)
    _patch_rag_llm(monkeypatch)
    rag.query_rag("FULL PROMPT with history and rules", mode="voice",
                  retrieval_query="MBA fees")
    assert seen["query"] == "MBA fees"


def test_n1_query_rag_defaults_to_question(monkeypatch):
    import app.rag as rag

    seen = {}

    def fake_retrieve(query):
        seen["query"] = query
        return ""

    monkeypatch.setattr(rag, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(rag, "RAG_SIMILARITY_THRESHOLD", 0.0)
    _patch_rag_llm(monkeypatch)
    rag.query_rag("plain question?", mode="chat")
    assert seen["query"] == "plain question?"


def test_n1_stream_retrieves_on_retrieval_query(monkeypatch):
    import app.rag as rag

    seen = {}

    def fake_retrieve(query):
        seen["query"] = query
        return "[§ Fees]\n$18,500"

    monkeypatch.setattr(rag, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(rag, "RAG_SIMILARITY_THRESHOLD", 0.0)

    async def fake_stream(prompt, model=None, num_ctx=None, temperature=None, cancel=None):
        yield "ok"

    # generate_stream is imported locally inside query_rag_stream — patch the
    # backend symbol it imports from.
    import app.llm_backend as lb
    import app.rag_legacy as rl
    monkeypatch.setattr(lb, "generate_stream", fake_stream)
    monkeypatch.setattr(rl, "_get_available_model", lambda: "m")

    async def run():
        async for _ in rag.query_rag_stream(
            "FULL PROMPT", mode="voice", retrieval_query="hostel fees"
        ):
            pass

    asyncio.run(run())
    assert seen["query"] == "hostel fees"


def test_rag_collection_name_config(monkeypatch):
    monkeypatch.setenv("RAG_COLLECTION_NAME", "meridian_kb__v1")
    import importlib
    import app.rag_legacy as rl

    importlib.reload(rl)
    assert rl.RAG_COLLECTION_NAME == "meridian_kb__v1"
    monkeypatch.delenv("RAG_COLLECTION_NAME")
    importlib.reload(rl)
    assert rl.RAG_COLLECTION_NAME == "langchain"
