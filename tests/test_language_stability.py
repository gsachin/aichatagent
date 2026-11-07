"""Explicit language choice is authoritative; auto-detect cannot rewrite it.

Regression for the 2026-10-20 outbound call:

1. The caller said "हाँ मुझे हिंदी में बात करनी है" (hi, probability 1.00).
2. Sarvam then transcribed the single word "admission" as "એડમિશન." and labelled
   it Gujarati at 0.72.
3. `_apply_detected_language` accepted it because the only gates were
   probability >= 0.5 and length >= 3, so the session, the LLM instruction and
   the TTS voice all switched to Gujarati. The next Hindi sentence switched it
   back, and the next short word switched it again — hi/gu/hi/gu/hi in 40 s.

The rules now: an explicit request wins and locks; an explicit selection (IVR,
operator, campaign) never drifts; an unlocked session may still switch, but
only on a probability and transcript length that a one-word loanword cannot
satisfy.
"""
from __future__ import annotations

import asyncio

import numpy as np

GUJARATI_WORD = "એડમિશન."
GUJARATI_SENTENCE = "એડમિશન વિશે માહિતી જોઈએ છે."
HINDI_REQUEST = "हाँ मुझे हिंदी में बात करनी है।"
HINDI_QUESTION = "मुझे कौन कौन से program available हैं उनका उनके बारे में बताइए।"
GUJARATI_REQUEST = "मुझे गुजराती में बात करनी है।"


def _auto(override_settings):
    """Pin the live auto-detect settings so a developer's .env cannot change it."""
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)


def test_requested_language_recognises_the_live_phrase():
    from app.languages import requested_language

    assert requested_language(HINDI_REQUEST) == "hi"
    assert requested_language(GUJARATI_REQUEST) == "gu"
    assert requested_language("Can you speak in Gujarati please?") == "gu"
    assert requested_language("speak Chinese please") == "zh"
    assert requested_language("हिंदी बोलो") == "hi"
    # Not a request: the caller is talking about the language, not asking for it.
    assert requested_language("I am studying Hindi literature") is None
    assert requested_language("What are the Hindi course fees?") is None


def test_supported_languages_include_indian_and_foreign(override_settings):
    from app.languages import is_supported_language

    override_settings(
        SUPPORTED_LANGUAGES="en,hi,gu,zh",
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
    )
    assert is_supported_language("hi")
    assert is_supported_language("gu")
    assert is_supported_language("zh")
    assert not is_supported_language("ko")


def test_disabling_foreign_languages_overrides_the_supported_list(override_settings):
    """FOREIGN_LANGUAGES is the switch; SUPPORTED_LANGUAGES must not undo it."""
    from app.languages import is_supported_language

    override_settings(SUPPORTED_LANGUAGES="en,hi,zh", FOREIGN_LANGUAGES="")
    assert is_supported_language("hi")
    assert not is_supported_language("zh")


def test_locked_hindi_ignores_the_single_word(override_settings):
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="hi", language_source="ivr")

    session._apply_detected_language("gu", 0.72, GUJARATI_WORD, False)

    assert session.language == "hi"
    assert session.language_source == "ivr"
    assert session._language_locked


def test_locked_hindi_ignores_even_a_confident_detection(override_settings):
    """Only another explicit request may overrule an explicit selection."""
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="hi", language_source="ivr")

    session._apply_detected_language("gu", 0.99, GUJARATI_SENTENCE, False)

    assert session.language == "hi"


def test_locked_hindi_switches_on_an_explicit_spoken_request(override_settings):
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="hi", language_source="ivr")

    session._apply_detected_language("hi", 1.0, GUJARATI_REQUEST, False)

    assert session.language == "gu"
    assert session.language_source == "spoken"
    assert session._language_locked


def test_unlocked_default_ignores_the_single_word(override_settings):
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="en")      # source "default"

    session._apply_detected_language("gu", 0.72, GUJARATI_WORD, False)

    assert session.language == "en"


def test_unlocked_default_switches_on_a_full_hindi_sentence(override_settings):
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="en")

    session._apply_detected_language("hi", 1.0, HINDI_QUESTION, False)

    assert session.language == "hi"
    assert session.language_source == "detected"
    assert not session._language_locked


def test_a_switch_below_the_switch_probability_is_ignored(override_settings):
    """0.5 is enough to ACCEPT a detection, not to rewrite the call."""
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="en")

    session._apply_detected_language("hi", 0.74, HINDI_QUESTION, False)

    assert session.language == "en"


def test_a_locked_foreign_call_can_follow_an_explicit_request(override_settings):
    """The caller's instruction outranks the Indic/foreign routing policy."""
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    session = VoiceCallSession(language="zh", language_source="operator")

    session._apply_detected_language("zh", 1.0, HINDI_REQUEST, False)

    assert session.language == "hi"
    assert session.language_source == "spoken"


def test_locked_session_sends_its_language_as_the_sarvam_hint(
    monkeypatch, override_settings
):
    """The hint is what keeps "admission" in Devanagari for a locked Hindi call."""
    import app.stt_sarvam as sarvam
    from app.stt_types import STTResult
    from app.voice_handler import VoiceCallSession

    _auto(override_settings)
    seen = {}

    def fake_transcribe(audio, language_hint=None):
        seen["hint"] = language_hint
        return STTResult(
            transcript="एडमिशन के बारे में बताइए",
            language="hi",
            language_probability=0.95,
            provider="sarvam",
        )

    monkeypatch.setattr(sarvam, "transcribe", fake_transcribe)

    locked = VoiceCallSession(language="hi", language_source="ivr")
    asyncio.run(locked._transcribe_sarvam(np.zeros(1600, dtype=np.int16)))
    assert seen["hint"] == "hi"

    seen.clear()
    automatic = VoiceCallSession(language="en")
    asyncio.run(automatic._transcribe_sarvam(np.zeros(1600, dtype=np.int16)))
    assert seen["hint"] is None


def test_stream_markup_carries_the_lock_flag():
    from app.main import _stream_markup

    markup = _stream_markup(
        "example.trycloudflare.com", "+15551234567", "hi", locked=True
    )
    assert '<Parameter name="language" value="hi" />' in markup
    assert '<Parameter name="languageLock" value="1" />' in markup
    assert "languageLock" not in _stream_markup(
        "example.trycloudflare.com", "+15551234567", "hi"
    )
