"""The Streamlit chat streams the answer instead of waiting for invoke().

The text surface used to block on ``rag_chain.invoke`` for the whole reply.
These tests drive the real app through Streamlit's AppTest harness, with the
shared async stream faked, and pin three behaviours:

* a successful stream is shown once and stored as the assistant message;
* a stream that fails before any text falls back to the old invoke path;
* a stream that fails midway keeps the text already shown and appends the
  interruption instead of re-rendering the whole reply below it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _app_test(monkeypatch, stream):
    import requests
    from streamlit.testing.v1 import AppTest

    import app.llm_backend as llm_backend
    import app.rag as rag
    import app.streamlit_backend as streamlit_backend

    # Keep the UI's optional side effects out of the test.
    monkeypatch.setattr(llm_backend, "is_ready", lambda: True)
    monkeypatch.setattr(llm_backend, "provider_name", lambda: "openai")
    monkeypatch.setattr(streamlit_backend, "backend_healthy", lambda *a, **k: True)
    monkeypatch.setattr(streamlit_backend, "sync_lead", lambda **k: ("lead_test", ""))
    monkeypatch.setattr(
        streamlit_backend, "sync_program", lambda *a, **k: ("lead_test", "")
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: None)
    monkeypatch.setattr(rag, "query_rag_stream", stream)

    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=60)
    # Skip the health check and chain construction; the profile is already
    # collected, so a knowledge question goes straight to the RAG fallback.
    at.session_state["rag_chain"] = object()
    at.session_state["lead_collected"] = True
    at.session_state["awaiting_field"] = None
    at.session_state["answer_language"] = "en"
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def _ask(at, question: str) -> str:
    at.chat_input[0].set_value(question).run()
    assert not at.exception, [str(e) for e in at.exception]
    return at.session_state["messages"][-1]["content"]


def test_streamed_answer_is_shown_and_stored(monkeypatch):
    async def stream(question, *, language=None):
        yield "The library "
        yield "is open until 8 PM."

    at = _app_test(monkeypatch, stream)
    assert _ask(at, "What is the library timing?") == (
        "The library is open until 8 PM."
    )


def test_stream_failure_before_text_uses_the_old_invoke_path(monkeypatch):
    async def stream(question, *, language=None):
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover — makes this an async generator

    at = _app_test(monkeypatch, stream)

    class _Chain:
        def invoke(self, payload):
            return {"answer": "fallback answer"}

    at.session_state["rag_chain"] = _Chain()
    assert _ask(at, "What is the library timing?") == "fallback answer"


def test_midstream_failure_keeps_the_text_already_shown(monkeypatch):
    async def stream(question, *, language=None):
        yield "The library "
        raise RuntimeError("stream broke")

    at = _app_test(monkeypatch, stream)
    answer = _ask(at, "What is the library timing?")

    assert answer.startswith("The library ")
    assert "stopped early" in answer
    assert "stream broke" in answer
