"""With LLM_PROVIDER=openai, no path may call a local model.

These tests guard the operator's explicit requirement: every LLM request goes
to OpenAI, and a failure reaches the fixed-response path rather than a second
local model. They use fakes only; no network and no API key are required.
"""

from __future__ import annotations

from types import SimpleNamespace


def _force_openai(monkeypatch):
    import app.llm_backend as lb

    monkeypatch.setattr(lb, "_provider", "openai")
    monkeypatch.setattr(lb, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(lb, "LLM_MODEL", "gpt-test")
    monkeypatch.setattr(lb, "LLM_MAX_TOKENS", 192)
    return lb


def test_chat_never_falls_back_to_ollama(monkeypatch):
    lb = _force_openai(monkeypatch)
    # The local adapter was deleted, not merely disabled. This is a structural
    # guard: re-adding a local fallback must be a deliberate new code path.
    assert not hasattr(lb, "_chat_ollama")
    assert not hasattr(lb, "_iter_ollama_events")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="remote answer")
                )],
                usage=None,
            )
        ))
    )
    monkeypatch.setattr(lb, "_openai_client", lambda: client)
    assert lb.chat([{"role": "user", "content": "hi"}]) == "remote answer"


def test_pick_model_returns_remote_model(monkeypatch):
    lb = _force_openai(monkeypatch)
    assert lb.pick_model(["qwen2.5:7b", "llama3.2:3b"]) == "gpt-test"


def test_boot_warm_does_not_call_ollama(monkeypatch):
    lb = _force_openai(monkeypatch)
    import app.boot_readiness as br

    monkeypatch.setattr(lb, "is_ready", lambda: True)
    assert not hasattr(br, "_ollama_chat")
    assert not hasattr(br, "ollama_ps")
    result = br.warm_and_confirm()
    assert result["provider"] == "openai"
    assert result["warm"] is True
    assert result["error"] is None


def test_pipecat_service_uses_openai(monkeypatch):
    _force_openai(monkeypatch)
    from app.pipeline import _build_llm_service

    service = _build_llm_service()
    assert service is not None
    assert type(service).__name__ == "OpenAILLMService"
