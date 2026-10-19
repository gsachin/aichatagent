"""Foreign-language voice input: routing, switching and text boundaries.

The fake Whisper model mirrors tests/test_multilingual_stt.py; no audio is
decoded and no cloud provider is called.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest


class _Segment:
    def __init__(self, text: str, avg_logprob: float = -0.2,
                 no_speech_prob: float = 0.1):
        self.text = text
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


class _Info:
    def __init__(self, language: str, probability: float):
        self.language = language
        self.language_probability = probability


class _Model:
    def __init__(self, info: _Info, segments=None):
        self.info = info
        self.segments = segments or [_Segment("What is the MBA fee?")]
        self.languages: list = []

    def transcribe(self, audio, language=None, **kwargs):
        self.languages.append(language)
        return iter(self.segments), self.info


def _session(monkeypatch, model, language="en"):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "_get_stt_model", lambda: model)
    return vh.VoiceCallSession(language=language)


def _audio(seconds: float = 1.0):
    return np.zeros(int(16000 * seconds), dtype=np.int16)


def test_foreign_session_calls_whisper_only(monkeypatch, override_settings):
    import app.stt_indic as indic
    import app.stt_sarvam as sarvam

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("foreign sessions must not call Sarvam/IndicConformer")

    monkeypatch.setattr(sarvam, "transcribe", must_not_be_called)
    monkeypatch.setattr(sarvam, "is_configured", lambda: True)
    monkeypatch.setattr(indic, "transcribe", must_not_be_called)
    override_settings(STT_LANGUAGE="auto")

    model = _Model(_Info("es", 0.95), [_Segment("¿Cuál es la matrícula?")])
    session = _session(monkeypatch, model, language="es")

    transcript, low_conf = asyncio.run(session._transcribe(_audio()))
    assert transcript
    assert low_conf is False
    assert model.languages == [None]
    assert session.language == "es"


def test_non_foreign_session_keeps_the_sarvam_chain(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam
    from app.stt_types import STTResult

    def must_not_load_whisper():
        raise AssertionError("an English Sarvam turn must not load Whisper")

    monkeypatch.setattr(
        sarvam, "is_configured", lambda: True
    )
    monkeypatch.setattr(
        sarvam, "transcribe",
        lambda *a, **k: STTResult(
            transcript="What is the MBA fee?",
            language="en",
            language_probability=0.95,
            provider="sarvam",
        ),
    )
    monkeypatch.setattr(
        "app.voice_handler._get_stt_model", must_not_load_whisper
    )
    override_settings(STT_PROVIDER="sarvam", STT_FALLBACK_PROVIDERS="")

    import app.voice_handler as vh

    session = vh.VoiceCallSession(language="en")
    transcript, low_conf = asyncio.run(session._transcribe(_audio()))
    assert transcript == "What is the MBA fee?"
    assert low_conf is False
    assert session.language == "en"


def test_foreign_session_switches_to_another_foreign(monkeypatch, override_settings):
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)
    model = _Model(_Info("fr", 0.91), [_Segment("Bonjour, quel est le tarif ?")])
    session = _session(monkeypatch, model, language="es")

    asyncio.run(session._transcribe(_audio()))
    assert session.language == "fr"


def test_foreign_session_does_not_switch_to_english(monkeypatch, override_settings):
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)
    model = _Model(_Info("en", 0.99), [_Segment("What is the MBA fee?")])
    session = _session(monkeypatch, model, language="es")

    asyncio.run(session._transcribe(_audio()))
    assert session.language == "es"


def test_english_session_does_not_switch_to_foreign(monkeypatch, override_settings):
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)
    model = _Model(_Info("es", 0.99), [_Segment("¿Cuál es la matrícula?")])
    session = _session(monkeypatch, model, language="en")

    asyncio.run(session._transcribe(_audio()))
    assert session.language == "en"


def test_cjk_switch_requires_cjk_characters(monkeypatch, override_settings):
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    # A Latin-script proper noun must not flip a Spanish call to Japanese.
    model = _Model(_Info("ja", 0.99), [_Segment("Tokyo")])
    session = _session(monkeypatch, model, language="es")
    asyncio.run(session._transcribe(_audio()))
    assert session.language == "es"

    model = _Model(
        _Info("ja", 0.99), [_Segment("東京の授業料はいくらですか？")]
    )
    session = _session(monkeypatch, model, language="es")
    # Three seconds of audio keeps the CJK rate test below its 12 units/s
    # threshold; the sentence is normal speech, not a Whisper loop.
    asyncio.run(session._transcribe(_audio(3.0)))
    assert session.language == "ja"


def test_pinned_stt_language_blocks_switching(monkeypatch, override_settings):
    override_settings(STT_LANGUAGE="es", STT_LANGUAGE_MIN_PROB=0.5)
    model = _Model(_Info("fr", 0.99), [_Segment("Bonjour, quel est le tarif ?")])
    session = _session(monkeypatch, model, language="es")

    asyncio.run(session._transcribe(_audio()))
    assert model.languages == ["es"]
    assert session.language == "es"


def test_short_foreign_utterances_pass_the_noise_gate():
    from app.voice_handler import is_noise_fragment

    assert is_noise_fragment("sí", "es") is False
    assert is_noise_fragment("sì", "it") is False
    assert is_noise_fragment("はい", "ja") is False
    assert is_noise_fragment("是", "zh") is False
    # The default has no language and must keep the English rule.
    assert is_noise_fragment("sí") is True
    assert is_noise_fragment("ok") is True


def test_foreign_transcript_skips_english_homophone_corrections(monkeypatch):
    import app.voice_handler as vh

    session = vh.VoiceCallSession(language="es")
    assert session._post_process_transcript("la faze del emma") == "la faze del emma"

    english = vh.VoiceCallSession(language="en")
    assert english._post_process_transcript("emma") == "MBA"


def test_cjk_hallucination_guard():
    from app.voice_handler import looks_like_hallucination

    assert looks_like_hallucination("東京の授業料は年間180万円です。", 3.0) is False
    assert looks_like_hallucination("東京の授業料は" * 20, 1.0) is True


def test_speculative_route_for_foreign_is_local(monkeypatch, override_settings):
    import app.voice_handler as vh

    override_settings(STT_PROVIDER="sarvam")
    assert vh.VoiceCallSession(language="en")._stt_route_is_cloud() is True
    assert vh.VoiceCallSession(language="es")._stt_route_is_cloud() is False


def test_split_sentences_cjk_and_accented_capitals():
    from app.voice_handler import _split_sentences

    assert _split_sentences("最初の文です。次の文です！") == [
        "最初の文です。", "次の文です！"
    ]
    assert _split_sentences("Él llegó. Ñandú cantó.") == [
        "Él llegó.", "Ñandú cantó."
    ]
    # The existing Indic/English corpus is protected by the baseline tests.
    assert _split_sentences("Hello there. How are you?") == [
        "Hello there.", "How are you?"
    ]


def test_clause_cutter_cjk_does_not_drop_complete_answer():
    from app.llm_backend import ClauseCutter

    cutter = ClauseCutter(language="ja")
    clauses = cutter.feed("はい。わかりました。")
    assert [c.text for c in clauses] == ["はい。", "わかりました。"]
    assert cutter.flush() is None

    # A complete three-character answer must survive: it is emitted by feed(),
    # not dropped for being shorter than the Latin twelve-character minimum.
    cutter = ClauseCutter(language="ja")
    assert [c.text for c in cutter.feed("はい。")] == ["はい。"]
    assert cutter.flush() is None


def test_grounding_sentences_cjk():
    from app.voice_grounding import _sentences

    assert _sentences("最初の文です。次の文です！") == [
        "最初の文です。", "次の文です！"
    ]


def test_grounding_number_claims_normalize_full_width_digits():
    from app.voice_grounding import _number_claims

    claims = _number_claims("学費は１８,５００ドルです。")
    assert claims, "full-width digits must be recognized as a claim"
    assert "18,500" in claims[0]


def test_foreign_closing_phrase_and_reply():
    from app.voice_handler import closing_reply, is_closing_phrase

    assert is_closing_phrase("adiós", "es") is True
    assert is_closing_phrase("au revoir", "fr") is True
    assert is_closing_phrase("さようなら", "ja") is True
    assert is_closing_phrase("再见", "zh") is True
    assert is_closing_phrase("bye", "es") is True
    assert is_closing_phrase("adiós", "en") is False

    assert "Gracias por llamar" in closing_reply("es")
    assert "Merci d'avoir appelé" in closing_reply("fr")
    assert closing_reply("en").startswith("Thank you for calling")
    assert closing_reply("hi").startswith("Thank you for calling")
