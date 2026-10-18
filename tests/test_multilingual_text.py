"""Multilingual text boundaries and relaxed grounding.

Indic scripts do not capitalise the next sentence; the Latin-only boundary
rules would otherwise emit one large clause/sentence. Numbers stay enforced
when name checking is relaxed for translated answers.
"""

from __future__ import annotations


def test_clause_cutter_splits_on_danda():
    from app.llm_backend import ClauseCutter

    cutter = ClauseCutter()
    clauses = cutter.feed(
        "MBA की फीस $18,500 प्रति वर्ष है। अगला वाक्य यहाँ है।"
    )
    assert clauses, "a danda-terminated sentence must produce a clause"
    assert any(c.text.endswith("।") for c in clauses)


def test_clause_cutter_merges_a_short_danda_clause():
    """A short opening clause must merge forward, not stall the cutter.

    The Indic boundary is zero-width, so re-prepending a short clause used to
    match the same danda until the 10k guard dropped the whole answer -- the
    caller then heard the reply with its opening sentence missing.
    """
    from app.llm_backend import ClauseCutter

    cutter = ClauseCutter()
    clauses = cutter.feed(
        "ठीक है। प्रवेश के लिए पहले ऑनलाइन आवेदन करना होता है।"
    )
    assert [c.text for c in clauses] == [
        "ठीक है। प्रवेश के लिए पहले ऑनलाइन आवेदन करना होता है।"
    ]
    assert cutter.flush() is None


def test_split_sentences_supports_danda():
    from app.voice_handler import _split_sentences

    parts = _split_sentences("पहला वाक्य। दूसरा वाक्य।")
    assert len(parts) == 2
    assert parts[0].endswith("।")


def test_grounding_sentences_support_danda():
    from app.voice_grounding import _sentences

    parts = _sentences("पहला वाक्य। दूसरा वाक्य।")
    assert len(parts) == 2


def test_relaxed_grounding_skips_names_but_checks_numbers():
    from app.voice_grounding import ungrounded_claims

    context = "MBA tuition is $18,500 per year."
    answer = "The School of International Studies costs $18,500 per year."
    strict = ungrounded_claims(answer, context)
    relaxed = ungrounded_claims(answer, context, check_names=False)
    assert "School of International Studies" in strict
    assert relaxed == []

    bad_number = "The XYZ program costs $99,999 per year."
    assert "$99,999" in ungrounded_claims(
        bad_number, context, check_names=False
    )


def test_relaxed_grounding_keeps_translated_survivors():
    from app.voice_grounding import enforce_grounding

    context = "MBA tuition is $18,500 per year."
    answer = "यह प्रोग्राम $99,999 प्रति वर्ष है।"
    out = enforce_grounding(answer, context, check_names=False)
    # The number is not in the evidence, so the turn is refused rather than
    # spoken; the name branch being relaxed must not disable the number check.
    assert out != answer
