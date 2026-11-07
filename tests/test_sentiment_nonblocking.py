"""The sentiment scorer must not freeze the event loop.

``backend_chat`` is the synchronous OpenAI client. Called directly from the
``async def`` scorer it blocks every live voice WebSocket and every HTTP
response for the whole completion. On the WhatsApp path this was measured on
2026-10-23: a 28 ms greeting took 3.0 s to reach the client because the
sentiment call that followed it held the loop, and a Chinese answer took 12.3 s
end-to-end while the app itself had finished in 1.8 s.

This is the same defect class the pipeline, the intent classifiers and the
transcript extractor already fixed with ``asyncio.to_thread``; the scorer was
the one call site left. The test pins the thread boundary, not the score.
"""
from __future__ import annotations

import asyncio
import threading

from app.sentiment import scorer


def test_extract_sentiment_calls_the_sync_llm_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_chat(**kwargs):
        seen["thread"] = threading.get_ident()
        # Parsing is not what this test covers; a parseable neutral answer
        # keeps the rest of the scorer on its normal path.
        return (
            '{"primary_emotion":"neutral","s_call":0.0,'
            '"buying_intent_score":0.0,"friction_score":0.0,'
            '"bant_completeness":0.0}'
        )

    monkeypatch.setattr("app.llm_backend.chat", fake_chat)

    asyncio.run(scorer.extract_sentiment("User: hello\nAssistant: hi"))

    assert seen, "the LLM was not called"
    assert seen["thread"] != loop_thread, (
        "backend_chat ran on the event-loop thread; a slow completion would "
        "freeze every live call and every HTTP response"
    )
