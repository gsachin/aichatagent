"""OpenAI adapter contract tests (no network, no API key).

The voice path's contract is exact: ``generate_stream`` yields string deltas and
then exactly one ``EngineCounters``; it raises ``GenerationFailed`` for an empty
or errored stream and ``GenerationCancelled`` when the caller interrupts. These
tests pin that contract for the remote provider with a fake client, so they run
in CI/on a laptop without ``LLM_API_KEY``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config import ConfigurationError


@pytest.fixture(autouse=True)
def _reset_caches():
    import app.llm_backend as lb

    old_provider = lb._provider
    old_client = lb._openai_client_singleton
    lb._provider = None
    lb._openai_client_singleton = None
    yield
    lb._provider = old_provider
    lb._openai_client_singleton = old_client


def _chunk(content=None, usage=None):
    choices = []
    if content is not None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices, usage=usage)


def _usage(prompt=100, completion=12):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )


class _FakeStream:
    def __init__(self, chunks, error=None):
        self._chunks = list(chunks)
        self._error = error
        self.closed = False

    def __iter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._error:
            raise self._error

    def close(self):
        self.closed = True


class _FakeCompletions:
    def __init__(self, response=None, stream=None, error=None):
        self.response = response
        self.stream = stream
        self.error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        if kwargs.get("stream"):
            return self.stream
        return self.response


class _FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=_FakeCompletions(**kwargs))


def _openai(monkeypatch, **kwargs):
    import app.llm_backend as lb

    client = _FakeClient(**kwargs)
    monkeypatch.setattr(lb, "_openai_client", lambda: client)
    lb._provider = "openai"
    return client


@pytest.mark.anyio
async def test_generate_stream_openai_order_and_counters(monkeypatch):
    import app.llm_backend as lb

    stream = _FakeStream([
        _chunk("Tuition is "),
        _chunk("here. "),
        _chunk("Scholarships too."),
        _chunk(usage=_usage()),
    ])
    client = _openai(monkeypatch, stream=stream)

    items = []
    async for item in lb.generate_stream(
        "question", system="instructions", model="qwen2.5:7b",
        num_ctx=8192, temperature=0.3,
    ):
        items.append(item)

    deltas = [i for i in items if isinstance(i, str)]
    counters = [i for i in items if isinstance(i, lb.EngineCounters)]
    assert deltas == ["Tuition is ", "here. ", "Scholarships too."]
    assert len(counters) == 1
    assert counters[0].prompt_eval_count == 100
    assert counters[0].eval_count == 12
    assert counters[0].prompt_eval_duration_ns is None
    assert counters[0].eval_duration_ns is None
    kwargs = client.chat.completions.last_kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["max_completion_tokens"] == 192
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    # The pump thread closes the remote stream in its finally block, just after
    # the counters event; the consumer returns first, so wait for the close.
    for _ in range(50):
        if stream.closed:
            break
        await asyncio.sleep(0.01)
    assert stream.closed


@pytest.mark.anyio
async def test_generate_stream_openai_empty_fails(monkeypatch):
    import app.llm_backend as lb

    _openai(monkeypatch, stream=_FakeStream([_chunk(usage=_usage())]))
    with pytest.raises(lb.GenerationFailed):
        async for _ in lb.generate_stream("question"):
            pass


@pytest.mark.anyio
async def test_generate_stream_openai_cancel(monkeypatch):
    import app.llm_backend as lb

    _openai(monkeypatch, stream=_FakeStream([_chunk("first "), _chunk("second")]))
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(lb.GenerationCancelled):
        async for _ in lb.generate_stream("question", cancel=cancel):
            pass


@pytest.mark.anyio
async def test_generate_stream_openai_error_does_not_call_ollama(monkeypatch):
    import app.llm_backend as lb

    class _Boom(RuntimeError):
        pass

    _openai(monkeypatch, error=_Boom("upstream down"))
    with pytest.raises(lb.GenerationFailed):
        async for _ in lb.generate_stream("question"):
            pass


def test_chat_openai_maps_json_and_predict(monkeypatch):
    import app.llm_backend as lb

    client = _openai(
        monkeypatch,
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"name": "A"}'))],
            usage=_usage(10, 5),
        ),
    )
    lb._provider = "openai"
    calls = []
    monkeypatch.setattr(
        "app.perf_trace.note_current", lambda **kw: calls.append(kw)
    )
    out = lb.chat(
        [{"role": "user", "content": "JSON please"}],
        preferred=["qwen2.5:7b"], num_predict=64, json_mode=True,
    )
    assert out == '{"name": "A"}'
    kwargs = client.chat.completions.last_kwargs
    assert kwargs["max_completion_tokens"] == 64
    assert kwargs["response_format"] == {"type": "json_object"}
    assert calls and calls[0]["provider"] == "openai"


def test_openai_requires_key(monkeypatch, override_settings):
    import app.llm_backend as lb

    override_settings(LLM_PROVIDER="openai")
    monkeypatch.setattr(lb, "LLM_API_KEY", "")
    lb._openai_client_singleton = None
    with pytest.raises(ConfigurationError):
        lb._openai_client()


def test_local_llm_and_embedding_apis_are_removed():
    import app.llm_backend as lb

    for name in (
        "_chat_ollama", "_chat_mlx", "_iter_ollama_events",
        "get_embedding_function", "get_langchain_embeddings",
        "embedding_provider_name",
    ):
        assert not hasattr(lb, name), f"{name} should have been deleted"
