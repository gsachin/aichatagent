"""
Regression tests for call-channel labelling.

``_handle_disconnect()`` hardcoded ``channel="inbound_call"``, and the outbound
Media Streams handler calls that same function — so every outbound call was
written to ``conversations.channel`` and ``leads.source`` as ``inbound_call``.

The dashboard already knew the difference (``conversations_page.py`` offers an
outbound filter and an outbound icon), so the outbound filter simply never
matched anything. These tests pin the channel to the call that produced it.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def neutral_collaborators(monkeypatch):
    """
    Stub everything _handle_disconnect reaches for, so the test exercises the
    channel plumbing and nothing else — and makes no LLM or database calls.
    """
    captured: dict = {}

    async def fake_extract(_transcript):
        # Returning None skips the lead-resolution branch entirely.
        return None

    async def fake_post_call_handler(**_kwargs):
        return False

    async def fake_score_transcript(**_kwargs):
        return {}

    async def fake_handle_post_interaction(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr("app.database.extract_lead_from_transcript", fake_extract)
    monkeypatch.setattr("app.pipeline.post_call_handler", fake_post_call_handler)
    monkeypatch.setattr("app.sentiment.scorer.score_transcript", fake_score_transcript)
    monkeypatch.setattr(
        "app.leads.service.handle_post_interaction", fake_handle_post_interaction
    )
    return captured


@pytest.mark.anyio
async def test_outbound_call_is_logged_as_outbound(neutral_collaborators):
    """The bug: outbound calls were labelled inbound."""
    from app.main import _handle_disconnect

    await _handle_disconnect(
        ["caller", "asked", "about", "fees"],
        "conv-out-1",
        channel="outbound_call",
    )

    assert neutral_collaborators["channel"] == "outbound_call"


@pytest.mark.anyio
async def test_inbound_call_is_logged_as_inbound(neutral_collaborators):
    from app.main import _handle_disconnect

    await _handle_disconnect(
        ["caller", "asked", "about", "fees"],
        "conv-in-1",
        channel="inbound_call",
    )

    assert neutral_collaborators["channel"] == "inbound_call"


@pytest.mark.anyio
async def test_channel_defaults_to_inbound_for_legacy_callers(neutral_collaborators):
    """
    The browser voice endpoint (/ws/voice) predates the channel parameter and
    passes neither id nor channel — it must keep behaving as before.
    """
    from app.main import _handle_disconnect

    await _handle_disconnect(["some", "browser", "audio"])

    assert neutral_collaborators["channel"] == "inbound_call"
    assert neutral_collaborators["conversation_id"] == ""


@pytest.mark.anyio
async def test_conversation_id_and_channel_travel_together(neutral_collaborators):
    """Both come from the same ended session; neither may be dropped."""
    from app.main import _handle_disconnect

    await _handle_disconnect(["hi"], "conv-xyz", channel="outbound_call")

    assert neutral_collaborators["conversation_id"] == "conv-xyz"
    assert neutral_collaborators["channel"] == "outbound_call"


@pytest.mark.anyio
async def test_empty_transcript_short_circuits(neutral_collaborators):
    """Nothing to log means no downstream call at all."""
    from app.main import _handle_disconnect

    await _handle_disconnect([], "conv-empty", channel="outbound_call")

    assert neutral_collaborators == {}


@pytest.mark.anyio
async def test_session_channel_reaches_the_log(neutral_collaborators):
    """
    The whole chain the WebSocket handler relies on: the session knows which
    kind of call it is, and that answer must survive into the logged row.
    """
    from app.crm import session as crm_session
    from app.main import _handle_disconnect

    crm_session.reset()
    crm_session.start("outbound_call", "CA-outbound-1")
    ended = crm_session.end("outbound_call", "CA-outbound-1")

    await _handle_disconnect(["hi"], ended.conversation_id, channel=ended.channel)

    assert neutral_collaborators["channel"] == "outbound_call"
    assert neutral_collaborators["conversation_id"] == ended.conversation_id
    crm_session.reset()
