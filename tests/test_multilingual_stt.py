"""Multilingual STT: Whisper's detected language drives the answer language.

No model is loaded and no audio is decoded; a fake faster-whisper model returns
a segment and an `info` object, so this runs in milliseconds.
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


def _session(monkeypatch, model):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "_get_stt_model", lambda: model)
    return vh.VoiceCallSession(language="en")


def test_auto_detects_hindi(monkeypatch, override_settings):
    model = _Model(_Info("hi", 0.91))
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    transcript, low_conf = asyncio.run(
        session._transcribe(np.zeros(1600, dtype=np.int16))
    )
    assert transcript
    assert low_conf is False
    assert model.languages == [None]          # Whisper auto-detected
    assert session.language == "hi"


def test_low_probability_keeps_previous_language(monkeypatch, override_settings):
    model = _Model(_Info("gu", 0.20))
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    asyncio.run(session._transcribe(np.zeros(1600, dtype=np.int16)))
    assert session.language == "en"


def test_unsupported_language_is_ignored(monkeypatch, override_settings):
    model = _Model(_Info("ko", 0.99))
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    asyncio.run(session._transcribe(np.zeros(1600, dtype=np.int16)))
    assert session.language == "en"


def test_low_confidence_does_not_switch(monkeypatch, override_settings):
    model = _Model(
        _Info("hi", 0.95), [_Segment("???", avg_logprob=-5.0)]
    )
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    _, low_conf = asyncio.run(
        session._transcribe(np.zeros(1600, dtype=np.int16))
    )
    assert low_conf is True
    assert session.language == "en"


def test_hallucination_guard_catches_repetition_loop():
    from app.voice_handler import looks_like_hallucination

    loop = "अगर बद्दाना वो आप प्रजाए तो तो " + "वो आप " * 20
    assert looks_like_hallucination(loop, 0.92)
    assert not looks_like_hallucination("मुझे हिंदी में बात करनी है", 1.34)
    assert not looks_like_hallucination("हाँ हाँ हाँ हाँ", 1.0)


def test_repetition_loop_is_low_confidence(monkeypatch, override_settings):
    loop = "अगर बद्दाना वो आप प्रजाए तो तो " + "वो आप " * 20
    model = _Model(_Info("hi", 0.84), [_Segment(loop)])
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="auto", STT_LANGUAGE_MIN_PROB=0.5)

    _, low_conf = asyncio.run(
        session._transcribe(np.zeros(int(0.92 * 16000), dtype=np.int16))
    )
    assert low_conf is True
    # low_conf also blocks the language switch and the fact extraction.
    assert session.language == "en"


def test_sarvam_failure_falls_back_to_whisper(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam
    from app.stt_types import STTError

    def boom(*args, **kwargs):
        raise STTError("Sarvam is down")

    monkeypatch.setattr(sarvam, "transcribe", boom)
    model = _Model(_Info("hi", 0.91))
    session = _session(monkeypatch, model)
    override_settings(
        STT_PROVIDER="sarvam",
        STT_FALLBACK_PROVIDERS="whisper",
        STT_LANGUAGE="auto",
        STT_LANGUAGE_MIN_PROB=0.5,
        SARVAM_API_KEY="test-key",
    )

    transcript, low_conf = asyncio.run(
        session._transcribe(np.zeros(1600, dtype=np.int16))
    )
    assert transcript == "What is the MBA fee?"
    assert low_conf is False
    assert session.language == "hi"


def test_sarvam_success_skips_whisper(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam
    from app.stt_types import STTResult
    import app.voice_handler as vh

    def fake_sarvam(audio, language_hint=None):
        return STTResult(
            transcript="B.Com की फीस कितनी है",
            language="hi",
            language_probability=0.95,
            provider="sarvam",
        )

    monkeypatch.setattr(sarvam, "transcribe", fake_sarvam)
    session = _session(monkeypatch, _Model(_Info("hi", 0.91)))
    monkeypatch.setattr(
        vh, "_get_stt_model",
        lambda: (_ for _ in ()).throw(AssertionError("Whisper must not load")),
    )
    override_settings(
        STT_PROVIDER="sarvam",
        STT_FALLBACK_PROVIDERS="whisper",
        STT_LANGUAGE="auto",
        STT_LANGUAGE_MIN_PROB=0.5,
        SARVAM_API_KEY="test-key",
    )

    transcript, low_conf = asyncio.run(
        session._transcribe(np.zeros(1600, dtype=np.int16))
    )
    assert "फीस" in transcript
    assert low_conf is False
    assert session.language == "hi"


def test_fixed_stt_language_is_forced(monkeypatch, override_settings):
    model = _Model(_Info("hi", 0.99))
    session = _session(monkeypatch, model)
    override_settings(STT_LANGUAGE="gu", STT_LANGUAGE_MIN_PROB=0.5)

    asyncio.run(session._transcribe(np.zeros(1600, dtype=np.int16)))
    assert model.languages == ["gu"]          # forced, not auto
    assert session.language == "en"           # no auto override
