"""Streamlit onboarding speaks the language the sidebar selected.

The defect these tests pin down: the selector offered Hindi and Gujarati, but
``STREAMLIT_UI`` only had the six foreign languages, so every onboarding prompt
fell back to English. Several program-flow prompts were hardcoded English in
``app.py`` as well, bypassing the table entirely.

``app.py`` starts a Streamlit run when imported, so the assertions are on the
pure prompt table and helpers in ``app.localized_text`` -- the same functions
``app.py`` calls at runtime.
"""

from __future__ import annotations

import string

import pytest

from app.languages import DEFAULT_FOREIGN_LANGUAGES
from app.localized_text import (
    ELIGIBILITY_TEXT,
    STREAMLIT_UI,
    greeting_text,
    streamlit_ui,
)

#: What the sidebar can select: the original three minus English, plus the six
#: foreign languages when FOREIGN_LANGUAGES enables them.
SELECTABLE_UI_LANGUAGES = ("hi", "gu", *DEFAULT_FOREIGN_LANGUAGES)

#: Every key app.py passes to _ui(). English has no table entry, so each of
#: these must exist for each selectable language or the prompt falls back to
#: English -- the bug this module exists to prevent.
REQUIRED_KEYS = {
    "greeting",
    "ask_name",
    "skip_ok",
    "name_thanks_email",
    "name_too_long",
    "email_ok",
    "email_bad",
    "phone_bad",
    "profile_saved",
    "applying_name",
    "program_ask",
    "program_unknown",
    "program_requirements",
    "docs_prompt",
    "the_program",
    "qualification_no",
    "qualification_retry",
    "profile_confirmed",
    "program_example",
    "restart",
}

#: The English literals app.py uses as the fallback for each key. Kept in the
#: test so a new placeholder in app.py without a matching translation fails
#: here instead of raising KeyError in a live chat.
ENGLISH_DEFAULTS = {
    "greeting": "Hello! I'm your University Admissions Advisor.",
    "ask_name": " Before we start, could you tell me your name?",
    "skip_ok": (
        "No problem — ask me anything about programmes, fees, eligibility or "
        "applying."
    ),
    "name_thanks_email": (
        "Thanks {name}! And what's your email address? I'll use it to send you "
        "program details."
    ),
    "name_too_long": (
        "That's a bit long for a name. Could you just tell me your first and "
        "last name?"
    ),
    "email_ok": (
        "Got it! And your phone number? (So an admissions counselor can follow "
        "up with you)"
    ),
    "email_bad": (
        "That doesn't look like an email address. Could you share a valid "
        "email? (e.g., name@example.com)"
    ),
    "phone_bad": (
        "That doesn't look like a phone number. Please share a number with at "
        "least 7 digits, or type 'skip'."
    ),
    "profile_saved": (
        "Perfect! I have your info:\n- Name: {name}\n- Email: {email}\n"
        "- Phone: {phone}\n\nIs this correct? (Type 'yes' or tell me what to "
        "change)"
    ),
    "applying_name": (
        "I'd love to help you apply! First, could you tell me your name?"
    ),
    "program_ask": (
        "Which program are you interested in? (e.g., B.Tech Computer Science, "
        "MBA, M.Tech)"
    ),
    "program_unknown": (
        "I didn't catch the program name. Which program? (e.g., B.Tech "
        "Computer Science, MBA, M.Tech)"
    ),
    "program_requirements": (
        "**{prog}** — great choice! 🎓\n\nBefore we proceed, here are the "
        "requirements:\n📋 Eligibility: {reqs}\n\nDo you meet these "
        "requirements? (yes/no)"
    ),
    "docs_prompt": (
        "Excellent! To process your admission for *{prog}*, please upload:\n\n"
        "📄 **Transcript / Mark Sheet**\n"
        "🆔 **ID Proof** (Passport, Aadhaar, etc.)\n\n"
        "Use the upload widget **below** ⬇️ to submit your documents. Once "
        "uploaded, your offer letter will be generated automatically!"
    ),
    "the_program": "the program",
    "qualification_no": (
        "No worries! You can still explore our programs. Feel free to ask me "
        "about admission requirements, alternative programs, or how to prepare "
        "your application."
    ),
    "qualification_retry": (
        "Please answer 'yes' or 'no' — do you meet the requirements for this "
        "program?"
    ),
    "profile_confirmed": (
        "Your profile is confirmed! You're interested in *{prog}*.\n\nWould "
        "you like to proceed with the application? Just say: 'I want to take "
        "admission'"
    ),
    "program_example": (
        "Or just say: 'I want to take admission in B.Tech Computer Science'"
    ),
    "restart": (
        "Hello! I'm your University Admissions Advisor. Ask me anything about "
        "programmes, fees, eligibility or applying."
    ),
}

#: The requirement phrases app.py's PROGRAM_ELIGIBILITY table can pass to the
#: program prompt, plus its default.
REQUIRED_ELIGIBILITY = {
    "a bachelor's degree",
    "a B.Tech or B.E. degree",
    "a bachelor's degree in computing",
    "a bachelor's degree in commerce",
    "a bachelor's degree in science",
    "10+2 with Physics, Chemistry and Mathematics (PCM)",
    "10+2 with PCM or PCB",
    "10+2 in any stream",
    "a relevant bachelor's degree",
}


def _placeholders(template: str) -> set[str]:
    return {
        name
        for _, name, _, _ in string.Formatter().parse(template)
        if name
    }


@pytest.mark.parametrize("language", SELECTABLE_UI_LANGUAGES)
def test_every_selectable_language_has_every_prompt(language):
    missing = REQUIRED_KEYS - set(STREAMLIT_UI[language])
    assert not missing, f"{language} falls back to English for: {sorted(missing)}"


def test_english_is_never_the_fallback_for_a_selectable_language():
    sentinel = "__english_default__"
    for language in SELECTABLE_UI_LANGUAGES:
        for key in REQUIRED_KEYS:
            assert streamlit_ui(key, language, sentinel) != sentinel, (
                f"{language}:{key} has no translation"
            )


@pytest.mark.parametrize("language", SELECTABLE_UI_LANGUAGES)
def test_translated_prompts_keep_the_placeholders_app_py_fills(language):
    for key, default in ENGLISH_DEFAULTS.items():
        translated = STREAMLIT_UI[language][key]
        assert _placeholders(translated) == _placeholders(default), (
            f"{language}:{key} placeholders differ from the English default"
        )


@pytest.mark.parametrize("language", SELECTABLE_UI_LANGUAGES)
def test_every_prompt_renders_without_a_key_error(language):
    for key, template in STREAMLIT_UI[language].items():
        values = {name: "x" for name in _placeholders(template)}
        rendered = streamlit_ui(key, language, "unused", **values)
        assert "{" not in rendered and "}" not in rendered, (
            f"{language}:{key} has an unfilled or stray placeholder"
        )


@pytest.mark.parametrize("language", SELECTABLE_UI_LANGUAGES)
def test_every_eligibility_phrase_is_translated(language):
    missing = REQUIRED_ELIGIBILITY - set(ELIGIBILITY_TEXT[language])
    assert not missing, f"{language} has no eligibility text for: {sorted(missing)}"


def test_unlisted_requirement_falls_back_to_english():
    assert (
        ELIGIBILITY_TEXT["hi"].get("a new requirement", "a new requirement")
        == "a new requirement"
    )


def test_greeting_uses_the_selected_language_and_keeps_the_name_prompt():
    hindi = greeting_text("hi", lead_collected=False)
    assert hindi.startswith("नमस्ते!")
    assert "नाम" in hindi
    # A collected profile gets the greeting without a second name prompt.
    hindi_no_name = greeting_text("hi", lead_collected=True)
    assert hindi_no_name.startswith("नमस्ते!")
    assert hindi_no_name == STREAMLIT_UI["hi"]["greeting"]


def test_english_greeting_is_unchanged():
    assert greeting_text("en", lead_collected=False) == (
        "Hello! I'm your University Admissions Advisor."
        " Before we start, could you tell me your name?"
    )


def test_hindi_selection_replaces_the_english_greeting(monkeypatch):
    """The live defect, end to end through Streamlit's own test harness.

    The page first renders in English (the selector's default). Selecting
    Hindi must replace the greeting on screen and every onboarding prompt
    after it -- the sample chat that reported this bug stayed English through
    name, email, phone and the program eligibility question.
    """
    from pathlib import Path

    import requests
    from streamlit.testing.v1 import AppTest

    import app.llm_backend as llm_backend
    import app.streamlit_backend as streamlit_backend

    # The UI's optional side effects are not what this test is about.
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
    # Skip the LLM health check and chain construction: onboarding is
    # deterministic and must not depend on a reachable model.
    at.session_state["rag_chain"] = object()
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    assert at.session_state["messages"][0]["content"].startswith("Hello!")

    at.selectbox(key="answer_language_name").select("Hindi").run()
    assert not at.exception, [str(e) for e in at.exception]
    assert at.session_state["messages"][0]["content"].startswith("नमस्ते!")

    def say(text: str) -> str:
        at.chat_input[0].set_value(text).run()
        assert not at.exception, [str(e) for e in at.exception]
        return at.session_state["messages"][-1]["content"]

    assert "ईमेल" in say("कुन्तेश")
    assert "फ़ोन" in say("kuntesh.parikh@gmail.com")
    assert "क्या यह सही है?" in say("7017873213")
    assert "कार्यक्रम" in say("हाँ")
    eligibility = say("bcom")
    assert "पात्रता" in eligibility
    assert "किसी भी संकाय में 10+2" in eligibility

    # None of the English prompts from the reported transcript may survive.
    conversation = "\n".join(
        message["content"] for message in at.session_state["messages"]
    )
    for english in (
        "Before we start",
        "email address",
        "phone number",
        "Is this correct?",
        "great choice",
        "Eligibility:",
        "Do you meet these requirements?",
    ):
        assert english not in conversation
