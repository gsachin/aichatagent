"""Outbound calls ask the callee which language to continue in.

The call is answered, a one-digit menu is played before the media stream
starts, and the press selects/locks the answer language. A timeout or digit 4
keeps the language the call would have opened in, so the menu can never strand
a caller in a language they did not choose.
"""

from __future__ import annotations

import asyncio


def _call_webhook(**kwargs):
    from app.main import twilio_outbound_voice_webhook

    return asyncio.run(twilio_outbound_voice_webhook(**kwargs))


def _call_action(**kwargs):
    from app.main import twilio_outbound_language_webhook

    return asyncio.run(twilio_outbound_language_webhook(**kwargs))


def test_worker_requests_the_menu(override_settings):
    from app.outbound.caller import _outbound_context_params

    override_settings(
        OUTBOUND_LANGUAGE="", OUTBOUND_LANGUAGE_MENU=True
    )
    params = _outbound_context_params(
        "lead-1", "+911234567890", {"language": "hi"}
    )
    assert params["language"] == "hi"
    assert params["languageSource"] == "lead"
    assert params["selectLanguage"] == "1"

    override_settings(OUTBOUND_LANGUAGE_MENU=False)
    assert "selectLanguage" not in _outbound_context_params(
        "lead-1", "+911234567890", {"language": "hi"}
    )


def test_menu_is_played_before_the_stream(monkeypatch):
    """The answered call hears the menu in the fallback language, then connects."""
    monkeypatch.setattr(
        "app.admission.asset_available", lambda name: True
    )
    body = _call_webhook(
        leadId="lead-1",
        phone="+911234567890",
        language="hi",
        languageSource="lead",
        selectLanguage="1",
        selected="",
    ).body.decode()

    assert '<Gather input="dtmf" numDigits="1" timeout="10"' in body
    assert 'actionOnEmptyResult="false"' in body
    assert "/twilio/outbound-language?" in body
    assert "leadId=lead-1" in body
    assert "language=hi" in body
    assert "languageSource=lead" in body
    # The pre-rendered Hindi menu is in the approved call voice.
    assert "/static/audio/menu_hi_" in body
    assert "<Play>" in body
    # The timeout path is the normal connect, in the fallback language.
    assert "<Connect>" in body
    assert '<Parameter name="language" value="hi" />' in body
    assert '<Parameter name="languageSource" value="lead" />' in body


def test_menu_skipped_once_selected(monkeypatch):
    monkeypatch.setattr(
        "app.admission.asset_available", lambda name: True
    )
    body = _call_webhook(
        leadId="lead-1",
        phone="+911234567890",
        language="hi",
        languageSource="lead",
        selectLanguage="1",
        selected="1",
    ).body.decode()
    assert "<Gather" not in body
    assert "<Connect>" in body


def test_menu_falls_back_to_carrier_tts_without_the_asset(monkeypatch):
    """A fresh clone has no pre-rendered audio yet; still ask, in English."""
    monkeypatch.setattr(
        "app.admission.asset_available", lambda name: False
    )
    body = _call_webhook(
        leadId="lead-1",
        phone="+911234567890",
        language="hi",
        languageSource="lead",
        selectLanguage="1",
    ).body.decode()
    assert "<Play>" not in body
    assert '<Say voice="Polly.Joanna">Hello, this is' in body
    assert "Press 1 for English" in body


def test_digit_selects_and_locks_the_language(override_settings):
    override_settings(
        SUPPORTED_LANGUAGES="en,hi,gu", FOREIGN_LANGUAGES=""
    )
    from app.voice_handler import VoiceCallSession

    body = _call_action(
        leadId="lead-1",
        phone="+911234567890",
        language="hi",
        languageSource="lead",
        Digits="3",
    ).body.decode()
    assert '<Parameter name="language" value="gu" />' in body
    assert '<Parameter name="languageSource" value="selected" />' in body

    session = VoiceCallSession(language="gu", language_source="selected")
    assert session._language_locked is True


def test_digit_four_and_timeout_keep_the_fallback(override_settings):
    override_settings(
        SUPPORTED_LANGUAGES="en,hi,gu", FOREIGN_LANGUAGES=""
    )
    for digits in ("4", ""):
        body = _call_action(
            leadId="lead-1",
            phone="+911234567890",
            language="hi",
            languageSource="lead",
            Digits=digits,
        ).body.decode()
        assert '<Parameter name="language" value="hi" />' in body
        assert '<Parameter name="languageSource" value="lead" />' in body


def test_unsupported_digit_keeps_the_fallback(override_settings):
    """Gu is configured on the demo, but not every deployment offers it."""
    override_settings(SUPPORTED_LANGUAGES="en,hi", FOREIGN_LANGUAGES="")
    body = _call_action(
        leadId="lead-1",
        phone="+911234567890",
        language="hi",
        languageSource="lead",
        Digits="3",
    ).body.decode()
    assert '<Parameter name="language" value="hi" />' in body
    assert '<Parameter name="languageSource" value="lead" />' in body


def test_menu_texts_offer_all_three_and_the_fallback(override_settings):
    override_settings(FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh")
    from app.voice_system_prompt import call_language_menu

    for code in ("en", "hi", "gu"):
        text = call_language_menu(code)
        assert "1" in text and "2" in text and "3" in text and "4" in text
        assert "9" in text
    assert "गुजराती" in call_language_menu("hi")
    assert "ગુજરાતી" in call_language_menu("gu")
    # A foreign fallback is prompted in English and names itself there.
    assert "Japanese" in call_language_menu("en", "ja")
    # The first menu names the foreign set, exactly like the inbound IVR.
    assert "Spanish" in call_language_menu("en")
    assert "Chinese" in call_language_menu("en")


def test_foreign_menu_text_covers_all_six():
    from app.voice_system_prompt import call_foreign_language_menu

    text = call_foreign_language_menu()
    for name in ("Spanish", "French", "Italian", "Japanese",
                 "Portuguese", "Chinese"):
        assert name in text
    assert "Press 1 for Spanish." in text
    assert "Press 6 for Chinese." in text


def test_digit_nine_opens_the_foreign_menu(override_settings, monkeypatch):
    override_settings(
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
        SUPPORTED_LANGUAGES="en,hi,gu,es,fr,it,ja,pt,zh",
    )
    monkeypatch.setattr("app.admission.asset_available", lambda name: True)

    body = _call_action(
        leadId="lead-1",
        phone="+911234567890",
        language="en",
        languageSource="lead",
        Digits="9",
    ).body.decode()

    assert "<Gather" in body
    assert "stage=foreign" in body
    assert "/static/audio/menu_en_" in body
    # The timeout still connects in the call's fallback language.
    assert "<Connect>" in body
    assert '<Parameter name="language" value="en" />' in body
    assert '<Parameter name="languageSource" value="lead" />' in body


def test_foreign_digits_select_and_lock(override_settings):
    override_settings(
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
        SUPPORTED_LANGUAGES="en,hi,gu,es,fr,it,ja,pt,zh",
    )
    from app.voice_handler import VoiceCallSession

    for digit, code in (("1", "es"), ("4", "ja"), ("6", "zh")):
        body = _call_action(
            leadId="lead-1",
            phone="+911234567890",
            language="en",
            languageSource="lead",
            Digits=digit,
            stage="foreign",
        ).body.decode()
        assert f'<Parameter name="language" value="{code}" />' in body
        assert '<Parameter name="languageSource" value="selected" />' in body
        assert VoiceCallSession(
            language=code, language_source="selected"
        )._language_locked is True


def test_foreign_menu_timeout_or_invalid_keeps_the_fallback(override_settings):
    override_settings(
        FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh",
        SUPPORTED_LANGUAGES="en,hi,gu,es,fr,it,ja,pt,zh",
    )
    for digits in ("", "7", "9"):
        body = _call_action(
            leadId="lead-1",
            phone="+911234567890",
            language="hi",
            languageSource="lead",
            Digits=digits,
            stage="foreign",
        ).body.decode()
        assert '<Parameter name="language" value="hi" />' in body
        assert '<Parameter name="languageSource" value="lead" />' in body


def test_nine_is_not_offered_when_foreign_languages_are_disabled(
    override_settings, monkeypatch
):
    override_settings(FOREIGN_LANGUAGES="")
    monkeypatch.setattr("app.admission.asset_available", lambda name: True)
    from app.voice_system_prompt import call_language_menu

    assert "press 9" not in call_language_menu("en").lower()

    body = _call_action(
        leadId="lead-1",
        phone="+911234567890",
        language="en",
        languageSource="lead",
        Digits="9",
    ).body.decode()
    assert "<Gather" not in body
    assert '<Parameter name="language" value="en" />' in body
    assert '<Parameter name="languageSource" value="lead" />' in body


def test_disabled_foreign_code_falls_back(override_settings):
    """The second-level digit exists, but that language is switched off."""
    override_settings(FOREIGN_LANGUAGES="es,fr")
    body = _call_action(
        leadId="lead-1",
        phone="+911234567890",
        language="en",
        languageSource="lead",
        Digits="4",          # Japanese
        stage="foreign",
    ).body.decode()
    assert '<Parameter name="language" value="en" />' in body
    assert '<Parameter name="languageSource" value="lead" />' in body


def test_menu_asset_name_tracks_language_and_wording():
    from app.voice_handler import menu_asset_name

    en = menu_asset_name("en", "one")
    assert en.startswith("menu_en_") and en.endswith(".wav")
    assert en == menu_asset_name("en", "one")
    assert en != menu_asset_name("en", "two")
    assert en != menu_asset_name("hi", "one")
