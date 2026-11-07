"""WhatsApp language command, and the persistence that survives a restart."""

from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_command_sets_session_language_and_confirms(monkeypatch):
    import app.main as main
    from app.crm.session import ConversationSession
    from fastapi import BackgroundTasks

    wa = ConversationSession(
        conversation_id="conv-1", channel="whatsapp", key="whatsapp:+1555"
    )

    async def fake_resolve(*args, **kwargs):
        return wa

    monkeypatch.setattr(
        "app.crm.session.resolve_whatsapp_session", fake_resolve
    )

    bg = BackgroundTasks()
    response = await main.twilio_whatsapp_webhook(
        background_tasks=bg,
        Body="Spanish",
        From="whatsapp:+1555",
        To="whatsapp:+1444",
        WaId="1555",
        NumMedia="0",
        MediaUrl0="",
        MediaContentType0="",
        Language="",
    )

    assert response.status_code == 200
    assert "español" in response.body.decode()
    assert wa.language == "es"
    # The command turn is logged with the language it selected.
    assert bg.tasks and bg.tasks[0].kwargs["language"] == "es"


@pytest.mark.anyio
async def test_command_is_not_stored_as_a_name(monkeypatch):
    """The command is handled before lead lookup/name capture.

    If the handler reached the name branch, "Italian" could be written to the
    lead row. The early return is the guarantee; this test proves no lead
    lookup happens.
    """
    import app.main as main
    from app.crm.session import ConversationSession
    from fastapi import BackgroundTasks

    wa = ConversationSession(
        conversation_id="conv-2", channel="whatsapp", key="whatsapp:+1666"
    )

    async def fake_resolve(*args, **kwargs):
        return wa

    calls = []

    async def boom(*args, **kwargs):
        calls.append("lead lookup")

    monkeypatch.setattr("app.crm.session.resolve_whatsapp_session", fake_resolve)
    monkeypatch.setattr("app.leads.models.get_lead_by_phone", boom)

    response = await main.twilio_whatsapp_webhook(
        background_tasks=BackgroundTasks(),
        Body="Italian",
        From="whatsapp:+1666",
        To="whatsapp:+1444",
        WaId="1666",
        NumMedia="0",
        MediaUrl0="",
        MediaContentType0="",
        Language="",
    )

    assert response.status_code == 200
    assert wa.language == "it"
    assert calls == []


@pytest.mark.anyio
async def test_session_language_persists_across_registry_loss(monkeypatch):
    from app.crm import session as crm_session

    crm_session.reset()

    async def fake_recent(phone_number, within_seconds, channel="whatsapp"):
        return {"conversation_id": "conv-db", "language": "es"}

    monkeypatch.setattr(
        "app.leads.models.get_recent_conversation_for_phone", fake_recent
    )

    session = await crm_session.resolve_whatsapp_session(
        "+1777", idle_window_hours=24
    )
    assert session.conversation_id == "conv-db"
    assert session.language == "es"
    crm_session.reset()


def test_start_normalizes_the_language():
    from app.crm import session as crm_session

    crm_session.reset()
    session = crm_session.start("whatsapp", "+1888", language="pt-BR")
    assert session.language == "pt"
    crm_session.reset()


def test_next_turn_uses_the_thread_language():
    from app.languages import resolve_answer_language

    # Persisted command wins over the form field and the default.
    assert resolve_answer_language("fr", "es", "en") == "fr"
    # No persisted language: the form field wins.
    assert resolve_answer_language("", "es", "en") == "es"
    # Neither: the deployment default.
    assert resolve_answer_language("", "", "en") == "en"
    # Regional codes are normalized on the way through.
    assert resolve_answer_language("pt-BR", "", "en") == "pt"
