"""Language normalization, the feature gate and the six-language prompt rules."""

from __future__ import annotations

import pytest


def test_normalizes_regional_and_script_codes():
    from app.languages import normalize_language_code

    assert normalize_language_code("es-MX") == "es"
    assert normalize_language_code("es_MX") == "es"
    assert normalize_language_code("pt-BR") == "pt"
    assert normalize_language_code("zh-CN") == "zh"
    assert normalize_language_code("cmn") == "zh"
    assert normalize_language_code("en-IN") == "en"
    # Existing codes keep their identity.
    for code in ("en", "hi", "gu", "ta", "bn", "or"):
        assert normalize_language_code(code) == code
    assert normalize_language_code("") == ""
    assert normalize_language_code(None) == ""


def test_feature_gate_reads_the_setting(override_settings):
    import app.languages as languages

    assert languages.configured_foreign_languages() == frozenset(
        {"es", "fr", "it", "ja", "pt", "zh"}
    )
    override_settings(FOREIGN_LANGUAGES="")
    assert languages.configured_foreign_languages() == frozenset()
    assert languages.is_foreign_code("es") is False
    override_settings(FOREIGN_LANGUAGES="es,fr,it,ja,pt,zh")
    # The parse cache is keyed by the raw value, so returning to the previous
    # value is immediate when the override ends.
    assert languages.is_foreign_code("es") is True


def test_short_utterance_thresholds():
    from app.languages import min_clause_chars, min_transcript_chars

    assert min_transcript_chars("ja") == 1
    assert min_transcript_chars("zh") == 1
    assert min_transcript_chars("es") == 2
    assert min_transcript_chars("pt") == 2
    assert min_transcript_chars("en") == 3
    assert min_transcript_chars("hi") == 3
    assert min_clause_chars("ja") == 1
    assert min_clause_chars("zh") == 1
    assert min_clause_chars("es") == 12
    assert min_clause_chars("hi") == 12


def test_cjk_detection():
    from app.languages import contains_cjk

    assert contains_cjk("日本語です")
    assert contains_cjk("中文回答")
    assert not contains_cjk("¿Cuál es la matrícula?")
    assert not contains_cjk("नमस्ते")


def test_language_instruction_normalizes_and_adds_script_notes():
    from app.voice_system_prompt import language_instruction

    es = language_instruction("es-MX")
    assert es.startswith("Answer in Spanish.")
    assert "neutral Latin American Spanish" in es

    pt = language_instruction("pt-BR")
    assert "Brazilian Portuguese" in pt

    zh = language_instruction("zh-CN")
    assert zh.startswith("Answer in Chinese.")
    assert "Simplified Chinese" in zh
    assert "。！？" in zh

    ja = language_instruction("ja")
    assert "Japanese script" in ja
    assert "。！？" in ja

    # Existing languages must not gain a foreign script note.
    assert "neutral Latin American" not in language_instruction("en")
    assert "standard French" not in language_instruction("hi")


def test_prompt_parts_for_regional_codes():
    from app.pipeline import build_rag_prompt_parts

    _system, user = build_rag_prompt_parts("fees?", language="pt-BR")
    assert "Answer in Portuguese." in user
    assert "Brazilian Portuguese" in user

    _system, user = build_rag_prompt_parts("fees?", language="es-MX")
    assert "Answer in Spanish." in user


def test_command_parser_accepts_whole_message_commands():
    from app.languages import language_from_command

    assert language_from_command("Spanish") == "es"
    assert language_from_command("español") == "es"
    assert language_from_command("French?") == "fr"
    assert language_from_command("/it") == "it"
    assert language_from_command("/ja") == "ja"
    assert language_from_command("中文") == "zh"

    # Bare "it" is an English pronoun, not a language command.
    assert language_from_command("it") is None
    # A command must be the whole message.
    assert language_from_command("I speak Spanish") is None
    assert language_from_command("¿Habla español?") is None
    # English is only a command when the thread is already foreign.
    assert language_from_command("English") is None
    assert language_from_command("English", current="es") == "en"
    assert language_from_command("/en") == "en"


def test_foreign_codes_never_route_to_sarvam_or_indic_tts(monkeypatch):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"es", "gu"}))
    for code in ("es", "fr", "it", "ja", "pt", "zh"):
        assert vh._use_sarvam_tts(code) is False, code

    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    for code in ("es", "fr", "it", "ja", "pt", "zh"):
        assert vh._use_indic_tts(code) is False, code


@pytest.mark.parametrize(
    "code,voice,phonemes",
    [
        ("es", "ef_dora", "es"),
        ("fr", "ff_siwis", "fr-fr"),
        ("it", "if_sara", "it"),
        ("ja", "jf_alpha", "ja"),
        ("pt", "pf_dora", "pt-br"),
        ("zh", "zf_xiaoxiao", "cmn"),
    ],
)
def test_kokoro_voice_and_phoneme_maps_for_six(monkeypatch, code, voice, phonemes):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_VOICE", "af_heart")
    assert vh._tts_voice_for(code) == voice
    assert vh._kokoro_lang_for(code, voice) == phonemes
    # A regional code must resolve too, not fall back to English.
    assert vh._tts_voice_for(f"{code}-XX") == voice
