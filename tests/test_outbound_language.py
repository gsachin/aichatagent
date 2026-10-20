"""Outbound calls open in the lead's language, not a global default.

The 2026-10-18 outbound calls logged ``language=default`` and greeted in
English while the caller was detected as Gujarati: the worker only consulted
``OUTBOUND_LANGUAGE`` and ignored the lead's stored language. The lead wins;
the setting remains the campaign default for leads that never chose one.
"""

from __future__ import annotations


def test_lead_language_wins(override_settings):
    from app.outbound.caller import _outbound_language

    override_settings(OUTBOUND_LANGUAGE="gu")
    assert _outbound_language({"language": "hi"}) == "hi"
    assert _outbound_language({"language": "hi-IN"}) == "hi"
    assert _outbound_language({"language": ""}) == "gu"
    assert _outbound_language({}) == "gu"
    assert _outbound_language(None) == "gu"


def test_no_language_stays_empty(override_settings):
    from app.outbound.caller import _outbound_language

    override_settings(OUTBOUND_LANGUAGE="")
    assert _outbound_language({"language": ""}) == ""
    assert _outbound_language(None) == ""


def test_operator_override_wins(override_settings):
    """The quick-call language selector is the outbound IVR digit.

    It is the per-call explicit choice, so it outranks both the lead's stored
    preference and the campaign default.
    """
    from app.outbound.caller import _outbound_language

    override_settings(OUTBOUND_LANGUAGE="gu")
    assert _outbound_language({"language": "hi"}, "zh") == "zh"
    assert _outbound_language({"language": "hi"}, "HI") == "hi"


def test_language_source_decides_whether_the_session_locks(override_settings):
    from app.outbound.caller import _outbound_language_source

    override_settings(OUTBOUND_LANGUAGE="gu")
    assert _outbound_language_source({"language": "hi"}, "zh") == "operator"
    assert _outbound_language_source({"language": "hi"}) == "lead"
    assert _outbound_language_source({"language": ""}) == "campaign"

    override_settings(OUTBOUND_LANGUAGE="")
    assert _outbound_language_source({"language": ""}) == ""
    assert _outbound_language_source(None) == ""


def test_outbound_webhook_carries_the_language_source():
    """The source must survive the webhook, not only the dial URL.

    The first version of this fix sent `languageSource=operator` on the Twilio
    call URL but the webhook's Query signature did not declare it, so the media
    stream saw no source and treated an operator's explicit Hindi choice as the
    lead's soft preference.
    """
    import asyncio

    from app.main import twilio_outbound_voice_webhook

    response = asyncio.run(
        twilio_outbound_voice_webhook(
            leadId="lead-1",
            phone="+911234567890",
            language="hi",
            languageSource="operator",
        )
    )
    body = response.body.decode()
    assert '<Parameter name="language" value="hi" />' in body
    assert '<Parameter name="languageSource" value="operator" />' in body
