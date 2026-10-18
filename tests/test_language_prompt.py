"""Language plumbing: the answer-language rule lives in the USER turn.

The system prompt + document prefix must stay byte-identical across languages
so OpenAI prompt caching can reuse it. These tests pin that separation.
"""

from __future__ import annotations

import asyncio


def test_voice_prompt_contains_language_instruction():
    from app.voice_handler import VoiceCallSession

    session = VoiceCallSession(language="hi")
    prompt = session._build_llm_prompt("What is the tuition?")
    assert "Answer in Hindi" in prompt
    assert "simple, everyday words" in prompt
    assert "Translate every sentence" in prompt
    assert "degree abbreviations" in prompt


def test_language_rule_forbids_mixed_english():
    """The old rule named "fees" as a keep-in-English item, so the model kept
    the English WORD ("प्रवेश, programmes, fees, scholarships"). The rule now
    names only proper nouns, numbers and URLs as Latin-script exceptions."""
    from app.voice_system_prompt import language_instruction

    rule = language_instruction("hi")
    assert "Do not leave English words" in rule
    assert "Translate programme fields" in rule
    assert "currency amounts" in rule


def test_hindi_self_reference_is_feminine():
    """The voice is female; Hindi first-person forms must agree.

    Before this, a female Kokoro voice said "सुन नहीं पाया" / "कहूँगा" and the
    outbound greeting said "बोल रहा हूँ" -- audibly a different person.
    """
    from app.voice_handler import _NOISE_LADDER_HI
    from app.voice_system_prompt import _INBOUND_GREETING, _OUTBOUND_GREETING

    text = (
        " ".join(_NOISE_LADDER_HI)
        + _INBOUND_GREETING["hi"]
        + _OUTBOUND_GREETING["hi"]
    )
    for masculine in (
        "सुन नहीं पाया", "सुन पा रहा हूँ", "कहूँगा",
        "सहायक हूँ", "बोल रहा हूँ",
    ):
        assert masculine not in text
    for feminine in (
        "सुन नहीं पाई", "सुन पा रही हूँ", "कहूँगी",
        "सहायिका", "बोल रही हूँ",
    ):
        assert feminine in text


def test_female_identity_is_in_both_prompts():
    """Voice and text chat must both know the assistant is female.

    The voice providers are already female (Kokoro af_heart/hf_alpha, Sarvam
    pooja_gu_customer); the prompt is what keeps Hindi verb forms and text-chat
    self-references consistent with them.
    """
    from app.rag import SYSTEM_PROMPT
    from app.voice_system_prompt import (
        build_voice_system_prompt,
        language_instruction,
    )

    voice = build_voice_system_prompt("CTX")
    assert "female" in voice.lower()
    assert "feminine" in voice.lower()
    assert "female" in SYSTEM_PROMPT.lower()
    assert "feminine" in SYSTEM_PROMPT.lower()
    assert "सकती हूँ" in language_instruction("hi")


def test_call_greeting_uses_the_call_language():
    from app.voice_system_prompt import call_greeting

    assert "admissions assistant" in call_greeting("en")
    assert "एडमिशन" in call_greeting("hi")
    assert "એડમિશન" in call_greeting("gu")
    assert "Meridian University" in call_greeting("gu", "outbound")


def test_system_prompt_has_no_language_instruction():
    from app.voice_system_prompt import build_voice_system_prompt

    system = build_voice_system_prompt("Some context")
    assert "Answer in Hindi" not in system
    assert "Answer in English" not in system


def test_query_rag_appends_language_to_user_turn(monkeypatch, override_settings):
    import app.llm_backend as lb
    import app.rag as rag

    captured = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "ok"

    monkeypatch.setattr(lb, "chat", fake_chat)
    override_settings(CONTEXT_MODE="doc")
    rag.query_rag("What is the fee?", language="hi")
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "Meridian University" in messages[0]["content"]
    assert "Answer in Hindi" in messages[1]["content"]
    assert "Student's question: What is the fee?" in messages[1]["content"]


def test_pipeline_build_prompt_parts_language(override_settings):
    from app.pipeline import build_rag_prompt_parts

    override_settings(CONTEXT_MODE="doc")
    system, user = build_rag_prompt_parts("fees?", language="ta")
    assert "Answer in Tamil" in user
    assert "Answer in Tamil" not in system


def test_inbound_ivr_maps_digits_to_languages():
    from app.main import _language_for_call, _stream_markup

    assert _language_for_call("1") == "en"
    assert _language_for_call("2") == "hi"
    assert _language_for_call("3") == "gu"
    assert _language_for_call("9", "hi") == "hi"      # unknown -> default
    assert _language_for_call("", "gu") == "gu"

    markup = _stream_markup("example.trycloudflare.com", "+15551234567", "hi")
    assert '<Parameter name="phone" value="+15551234567" />' in markup
    assert '<Parameter name="language" value="hi" />' in markup


def test_query_rag_stream_appends_language(monkeypatch, override_settings):
    import app.llm_backend as lb
    import app.rag as rag

    captured = {}

    async def fake_stream(prompt, **kwargs):
        captured["prompt"] = prompt
        yield "answer"

    monkeypatch.setattr(lb, "generate_stream", fake_stream)
    override_settings(CONTEXT_MODE="doc")

    async def run():
        return [item async for item in rag.query_rag_stream("fees?", language="bn")]

    assert asyncio.run(run()) == ["answer"]
    assert "Answer in Bengali" in captured["prompt"]
