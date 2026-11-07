"""The in-chat language picker at the start of the Streamlit conversation.

The sidebar selector still works; the pills are an additive control shown only
until the first user message, and the two stay in sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def app(monkeypatch):
    """The real app.py, with its external side effects stubbed."""
    import requests
    from streamlit.testing.v1 import AppTest

    import app.llm_backend as llm_backend
    import app.streamlit_backend as streamlit_backend

    monkeypatch.setattr(llm_backend, "is_ready", lambda: True)
    monkeypatch.setattr(llm_backend, "provider_name", lambda: "openai")
    monkeypatch.setattr(streamlit_backend, "backend_healthy", lambda *a, **k: True)
    monkeypatch.setattr(
        streamlit_backend, "sync_lead", lambda **k: ("lead_test", "")
    )
    monkeypatch.setattr(
        streamlit_backend, "sync_program", lambda *a, **k: ("lead_test", "")
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: None)

    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=60)
    at.session_state["rag_chain"] = object()
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def _pills(at):
    return at.pills(key="chat_language_pick")


def _labels(at):
    return list(_pills(at).options)


def test_picker_is_shown_at_the_start_with_all_languages(app):
    pills = _pills(app)
    assert pills.value == "English"
    assert _labels(app) == [
        "English", "Hindi", "Gujarati",
        "Spanish (Español)", "French (Français)", "Italian (Italiano)",
        "Japanese (日本語)", "Portuguese (Português)", "Chinese (中文)",
    ]


def test_pill_selection_updates_sidebar_and_greeting(app):
    _pills(app).select("Gujarati").run()
    assert not app.exception, [str(e) for e in app.exception]

    assert app.session_state["answer_language"] == "gu"
    assert app.selectbox(key="answer_language_name").value == "Gujarati"
    assert _pills(app).value == "Gujarati"
    assert app.session_state["messages"][0]["content"].startswith("નમસ્તે!")


def test_sidebar_selection_updates_the_pills(app):
    app.selectbox(key="answer_language_name").select("Hindi").run()
    assert not app.exception, [str(e) for e in app.exception]

    assert app.session_state["answer_language"] == "hi"
    assert _pills(app).value == "Hindi"
    assert app.session_state["messages"][0]["content"].startswith("नमस्ते!")


def test_picker_disappears_on_the_first_message_run(app):
    app.chat_input[0].set_value("Kuntesh").run()
    assert not app.exception, [str(e) for e in app.exception]

    assert len(app.pills) == 0
    # The sidebar remains available for later changes.
    assert app.selectbox(key="answer_language_name") is not None


def test_picker_and_sidebar_alternate_without_state_errors(app):
    _pills(app).select("Gujarati").run()
    app.selectbox(key="answer_language_name").select("Hindi").run()
    _pills(app).select("Chinese (中文)").run()
    assert not app.exception, [str(e) for e in app.exception]

    assert app.session_state["answer_language"] == "zh"
    assert app.selectbox(key="answer_language_name").value == "Chinese (中文)"
    assert _pills(app).value == "Chinese (中文)"


def test_foreign_disabled_shows_the_core_three(monkeypatch, override_settings):
    override_settings(FOREIGN_LANGUAGES="")
    # Rebuild the app under the disabled setting.
    import requests
    from streamlit.testing.v1 import AppTest

    import app.llm_backend as llm_backend
    import app.streamlit_backend as streamlit_backend

    monkeypatch.setattr(llm_backend, "is_ready", lambda: True)
    monkeypatch.setattr(llm_backend, "provider_name", lambda: "openai")
    monkeypatch.setattr(streamlit_backend, "backend_healthy", lambda *a, **k: True)
    monkeypatch.setattr(
        streamlit_backend, "sync_lead", lambda **k: ("lead_test", "")
    )
    monkeypatch.setattr(
        streamlit_backend, "sync_program", lambda *a, **k: ("lead_test", "")
    )
    monkeypatch.setattr(requests, "post", lambda *a, **k: None)

    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=60)
    at.session_state["rag_chain"] = object()
    at.run()
    assert not at.exception, [str(e) for e in at.exception]

    assert _labels(at) == ["English", "Hindi", "Gujarati"]
    assert at.selectbox(key="answer_language_name").options == [
        "English", "Hindi", "Gujarati",
    ]
