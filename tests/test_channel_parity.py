"""Chat/WhatsApp/voice parity at the actual NGA seams.

This is not a Twilio webhook test: it exercises the shared answer, WhatsApp
agentic and voice-fast functions with the same fixture graph, which is where
evidence/citation divergence would originate.
"""

from __future__ import annotations

from app.agent.text import build_text_answer
from app.agent.voice import build_voice_bundle
from app.rag import clear_turn_context, last_context_bundle, query_rag
from tests.nga_helpers import build_store, seed_page

HOSTEL_URL = "https://dspmuranchi.ac.in/cms/hostel"


def _urls(result) -> set[str]:
    return {
        str(citation.get("url") or "")
        for citation in (result.citations or [])
        if citation.get("url")
    }


def test_text_whatsapp_and_voice_share_the_same_evidence(
    tmp_path, monkeypatch, override_settings
):
    store, db = build_store(tmp_path)
    seed_page(store, text="The hostel has a warden office and a mess.")
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="agentic",
        NGA_GRAPH_SQLITE_PATH=str(db),
        DEEP_SEARCH_ENABLED=False,
        NGA_ADEQUACY_ENABLED=True,
        NGA_CONVERSATION_STATE_ENABLED=False,
    )

    def fake_chat(messages, **kwargs):
        return "The hostel has a warden office and a mess."

    monkeypatch.setattr("app.llm_backend.chat", fake_chat)

    chat = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The hostel has a warden office and a mess.",
        channel="chat",
    )
    assert chat is not None
    chat_urls = _urls(chat)
    assert HOSTEL_URL in chat_urls

    clear_turn_context()
    whatsapp_text = query_rag(
        "hostel",
        mode="whatsapp",
        agentic_text=True,
        channel="whatsapp",
    )
    assert whatsapp_text
    whatsapp_bundle = last_context_bundle()
    assert whatsapp_bundle is not None
    whatsapp_urls = {str(item.get("url") or "") for item in whatsapp_bundle.citations}
    assert whatsapp_urls == chat_urls

    voice = build_voice_bundle(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        channel="voice",
    )
    assert voice.bundle is not None
    voice_urls = set(voice.bundle.source_urls)
    assert voice_urls == chat_urls
