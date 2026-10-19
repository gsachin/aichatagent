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
