"""Webhook-level wiring for the WhatsApp and voice NGA seams.

These tests stop one layer below a live Twilio request: they exercise the
handler functions that the webhook and media stream call, with the external
transport (Twilio signature, Postgres lead, STT/TTS) mocked out. The NGA
evidence itself is covered by ``test_channel_parity.py``.
"""

from __future__ import annotations

import asyncio

import pytest

import app.pipeline
import app.rag
from app.main import _whatsapp_chat_rag
from app.voice_handler import VoiceCallSession


def test_whatsapp_webhook_chat_helper_passes_the_whatsapp_nga_seam(
    monkeypatch, override_settings
):
    override_settings(NGA_ENABLED=True, NGA_MODE="agentic")
    captured: dict = {}

    def fake_run(
        user_text,
        mode="voice",
        *,
        retrieval_query=None,
        history=None,
        profile=None,
        language=None,
        agentic_text=False,
        channel=None,
        conversation_id=None,
        **kwargs,
    ):
        captured.update(
            {
                "user_text": user_text,
                "mode": mode,
                "retrieval_query": retrieval_query,
                "history": history,
                "profile": profile,
                "language": language,
                "agentic_text": agentic_text,
                "channel": channel,
                "conversation_id": conversation_id,
            }
        )
        return "The hostel has a warden office."

    monkeypatch.setattr(app.pipeline, "run_rag_query_sync", fake_run)

    result = asyncio.run(
        _whatsapp_chat_rag(
            "hostel",
            history=["Student: hi", "Assistant: hello"],
            profile={"Program of interest": "BA History"},
            program="BA History",
            language="en",
            conversation_id="wa-conv-1",
            to_number="whatsapp:+10000000000",
        )
    )

    assert result == "The hostel has a warden office."
    assert captured["user_text"] == "hostel"
    assert captured["mode"] == "chat"
    assert captured["agentic_text"] is True
    assert captured["channel"] == "whatsapp"
    assert captured["conversation_id"] == "wa-conv-1"
    assert captured["language"] == "en"
    assert captured["history"] == ["Student: hi", "Assistant: hello"]
    assert captured["profile"] == {"Program of interest": "BA History"}
    # The retrieval query is program-seeded; the prompt keeps the raw question.
    assert captured["retrieval_query"] == "hostel BA History"


def test_whatsapp_webhook_chat_helper_does_not_double_seed_a_named_program(
    monkeypatch, override_settings
):
    override_settings(NGA_ENABLED=True, NGA_MODE="agentic")
    captured: dict = {}

    def fake_run(user_text, mode="voice", *, retrieval_query=None, **kwargs):
        captured["retrieval_query"] = retrieval_query
        return "ok"

    monkeypatch.setattr(app.pipeline, "run_rag_query_sync", fake_run)

    asyncio.run(
        _whatsapp_chat_rag(
            "BA History fee",
            program="BA History",
            conversation_id="wa-conv-2",
        )
    )

    assert captured["retrieval_query"] == "BA History fee"


def test_voice_batch_handler_passes_the_voice_nga_seam(
    monkeypatch, override_settings
):
    override_settings(NGA_ENABLED=True, NGA_MODE="agentic")
    captured: dict = {}

    def fake_run(
        prompt,
        mode,
        retrieval_query,
        *,
        reference_sink=None,
        conversation_id=None,
        language=None,
        channel=None,
        voice_fast=False,
        **kwargs,
    ):
        captured.update(
            {
                "prompt": prompt,
                "mode": mode,
                "retrieval_query": retrieval_query,
                "conversation_id": conversation_id,
                "language": language,
                "channel": channel,
                "voice_fast": voice_fast,
            }
        )
        return "The hostel has a warden office."

    monkeypatch.setattr(app.pipeline, "run_rag_query_sync", fake_run)
    session = VoiceCallSession(language="en")
    session.conversation_id = "voice-conv-1"

    answer = asyncio.run(session._query_llm("hostel"))

    assert answer == "The hostel has a warden office."
    assert captured["mode"] == "voice"
    assert captured["retrieval_query"] == "hostel"
    assert captured["conversation_id"] == "voice-conv-1"
    assert captured["language"] == "en"
    assert captured["channel"] == "voice"
    assert captured["voice_fast"] is True
    assert "hostel" in captured["prompt"]


def test_voice_stream_handler_passes_the_voice_nga_seam(
    monkeypatch, override_settings
):
    override_settings(NGA_ENABLED=True, NGA_MODE="agentic")
    captured: dict = {}

    async def fake_stream(
        prompt,
        *,
        cancel=None,
        retrieval_query=None,
        conversation_id=None,
        language=None,
        channel=None,
        voice_fast=False,
        **kwargs,
    ):
        captured.update(
            {
                "prompt": prompt,
                "retrieval_query": retrieval_query,
                "conversation_id": conversation_id,
                "language": language,
                "channel": channel,
                "voice_fast": voice_fast,
            }
        )
        yield "The hostel has a warden office."

    monkeypatch.setattr(app.rag, "query_rag_stream", fake_stream)
    session = VoiceCallSession(language="en")
    session.conversation_id = "voice-conv-2"
    monkeypatch.setattr(session, "grounded", lambda text: text)

    answer, frames = asyncio.run(
        session._query_llm_streamed("hostel", chunk_sink=None)
    )

    assert "The hostel has a warden office." in answer
    assert frames == []
    assert captured["retrieval_query"] == "hostel"
    assert captured["conversation_id"] == "voice-conv-2"
    assert captured["language"] == "en"
    assert captured["channel"] == "voice"
    assert captured["voice_fast"] is True
    assert "hostel" in captured["prompt"]


def test_twilio_whatsapp_health_route_is_reachable():
    from app.main import twilio_whatsapp_health

    response = asyncio.run(twilio_whatsapp_health())

    assert response.status_code == 200
    assert b"WhatsApp webhook" in response.body


def test_twilio_whatsapp_route_reaches_the_nga_chat_helper(
    monkeypatch, override_settings
):
    """Exercise the real webhook state machine with transport mocked out."""
    from types import SimpleNamespace

    from fastapi import BackgroundTasks

    from app import main as main_module
    from app.crm import session as crm_session
    from app.leads import models as lead_models

    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="agentic",
        WHATSAPP_LANGUAGE_MENU=False,
    )

    fake_session = SimpleNamespace(
        conversation_id="wa-http-conv-1",
        language="en",
        language_source="selected",
        language_menu_done=True,
        language_menu_prompts=0,
        pending_language_text="",
        awaiting="",
    )

    async def fake_resolve(from_number, idle_window_hours):
        assert from_number == "whatsapp:+10000000000"
        return fake_session

    async def fake_lead(from_number):
        return {
            "id": "lead-1",
            "name": "Test Student",
            "email": "test@example.com",
            "program_interest": "",
            "language": "en",
        }

    async def fake_history(lead_id, limit=6):
        assert lead_id == "lead-1"
        return []

    captured: dict = {}

    async def fake_chat(question, **kwargs):
        captured["question"] = question
        captured.update(kwargs)
        return "The hostel has a warden office."

    monkeypatch.setattr(
        crm_session, "resolve_whatsapp_session", fake_resolve
    )
    monkeypatch.setattr(lead_models, "get_lead_by_phone", fake_lead)
    monkeypatch.setattr(main_module, "_whatsapp_recent_turns", fake_history)
    monkeypatch.setattr(main_module, "_whatsapp_chat_rag", fake_chat)

    response = asyncio.run(
        main_module.twilio_whatsapp_webhook(
            BackgroundTasks(),
            Body="hostel",
            MediaUrl0="",
            MediaContentType0="",
            NumMedia="0",
            From="whatsapp:+10000000000",
            To="whatsapp:+10000000001",
            WaId="10000000000",
            Language="",
        )
    )

    assert response.status_code == 200
    assert "The hostel has a warden office." in response.body.decode()
    assert captured["question"] == "hostel"
    assert captured["conversation_id"] == "wa-http-conv-1"
    assert captured["language"] == "en"
    assert captured["profile"] == {
        "Name": "Test Student",
        "Email": "test@example.com",
        "Program of interest": "",
    }
    assert captured["to_number"] == "whatsapp:+10000000000"
