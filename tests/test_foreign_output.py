"""Foreign voice output: greetings, ladders, assets and the English build path."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


def _load_build_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_call_assets.py"
    spec = importlib.util.spec_from_file_location("build_call_assets_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeKokoro:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, text, voice=None, speed=None, lang=None, is_phonemes=False):
        self.calls.append(
            {"text": text, "voice": voice, "speed": speed, "lang": lang,
             "is_phonemes": is_phonemes}
        )
        return np.zeros(8000, dtype=np.float32), 8000


def test_greetings_for_six_format_and_existing_codes_are_untouched():
    from app.voice_system_prompt import call_greeting

    expected = {
        "es": "asistente de admisiones",
        "fr": "assistante des admissions",
        "it": "assistente per le ammissioni",
        "ja": "入学アシスタント",
        "pt": "assistente de admissões",
        "zh": "招生助理",
    }
    for code, phrase in expected.items():
        inbound = call_greeting(code, "inbound")
        assert phrase in inbound, code
        assert "Meridian University" in inbound
        outbound = call_greeting(code, "outbound")
        assert "{agent_name}" not in outbound
        assert "Meridian University" in outbound

    # The three existing greetings are pinned by the baseline hash test; this
    # is a readable sanity check that the merge did not replace them.
    assert call_greeting("en", "inbound").startswith("Hi, I'm the admissions assistant")


def test_noise_ladders_for_six_are_distinct_and_escalate():
    from app.voice_handler import _NOISE_LADDERS

    for code in ("es", "fr", "it", "ja", "pt", "zh"):
        ladder = _NOISE_LADDERS[code]
        assert len(ladder) == 4, code
        assert len(set(ladder)) == 4, code
        assert all(line.strip() for line in ladder), code

    # Existing ladders are untouched.
    assert _NOISE_LADDERS["en"][0].startswith("Sorry, I didn't quite catch")


def test_fixed_and_busy_asset_names():
    from app.admission import (
        BUSY_ASSET,
        FIXED_RESPONSE_ASSET,
        busy_asset,
        busy_text,
        fixed_response_asset,
        fixed_response_text,
    )

    assert fixed_response_asset("") == FIXED_RESPONSE_ASSET
    assert fixed_response_asset("en") == FIXED_RESPONSE_ASSET
    assert fixed_response_asset("hi") == FIXED_RESPONSE_ASSET
    assert fixed_response_asset("es") == "fixed_response_es.wav"
    assert fixed_response_asset("pt-BR") == "fixed_response_pt.wav"
    assert busy_asset("") == BUSY_ASSET
    assert busy_asset("zh-CN") == "busy_zh.wav"

    assert "Lo siento" in fixed_response_text("es")
    assert "Desculpe" in fixed_response_text("pt-BR")
    assert fixed_response_text("hi").startswith("I'm sorry")
    assert "Gracias por llamar" in busy_text("es")
    assert busy_text("hi").startswith("Thank you for calling")


def test_foreign_fixed_response_counts_as_a_degraded_turn():
    from app.admission import REGISTRY

    REGISTRY.reset_records()
    REGISTRY.note_asset_played("fixed_response_es.wav")
    assert REGISTRY.records.fixed_response_plays == 1
    REGISTRY.reset_records()


def test_english_build_path_does_not_pass_a_phoneme_language(monkeypatch, tmp_path):
    """The English branch must stay exactly ``engine.create(text, voice, speed)``.

    Passing ``lang`` would change the synthesised bytes; the branch is what
    keeps the two English assets on their original code path.
    """
    import app.voice_handler as vh

    fake = _FakeKokoro()
    monkeypatch.setattr(vh, "_get_tts_engine", lambda: fake)
    monkeypatch.setattr(vh, "_tts_voice", lambda: "af_heart")
    monkeypatch.setattr(vh, "_tts_speed", lambda: 1.0)

    build = _load_build_script()
    build.synthesise("Hello there.", tmp_path / "x.wav", language=None)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["voice"] == "af_heart"
    assert call["speed"] == 1.0
    assert call["lang"] is None, "English must not pass a lang argument"
    assert call["is_phonemes"] is False


def test_foreign_build_path_uses_misaki_phonemes_and_chunks_long_cjk(
    monkeypatch, tmp_path
):
    import app.voice_handler as vh

    fake = _FakeKokoro()
    monkeypatch.setattr(vh, "_get_tts_engine", lambda: fake)
    monkeypatch.setattr(vh, "_tts_voice_for", lambda code: "jf_alpha")
    monkeypatch.setattr(vh, "_tts_speed_for", lambda code=None: 1.2)
    monkeypatch.setattr(vh, "_kokoro_phonemes", lambda text, code: "テスト")

    build = _load_build_script()
    build.synthesise("はい。わかりました。", tmp_path / "ja.wav", language="ja")

    assert len(fake.calls) == 2, "CJK sentences are chunked below the 510 limit"
    assert all(c["voice"] == "jf_alpha" for c in fake.calls)
    assert all(c["text"] == "テスト" for c in fake.calls)
    assert all(c["is_phonemes"] is True for c in fake.calls)
    assert all(c["lang"] is None for c in fake.calls)
    assert all(c["speed"] == 1.2 for c in fake.calls)


def test_kokoro_render_uses_misaki_only_for_ja_and_zh(monkeypatch):
    import app.voice_handler as vh

    fake = _FakeKokoro()
    monkeypatch.setattr(
        vh, "_kokoro_phonemes",
        lambda text, code: "PHONEMES" if code in ("ja", "zh") else "",
    )
    monkeypatch.setattr(vh, "_kokoro_lang_for", lambda code, voice="": "es")

    vh._kokoro_render(fake, "hola", "es", "ef_dora", 1.0)
    vh._kokoro_render(fake, "こんにちは", "ja", "jf_alpha", 1.2)

    assert fake.calls[0]["is_phonemes"] is False
    assert fake.calls[0]["lang"] == "es"
    assert fake.calls[1]["is_phonemes"] is True
    assert fake.calls[1]["text"] == "PHONEMES"


def test_misaki_reads_japanese_kanji():
    pytest.importorskip("misaki")
    from app.voice_handler import _kokoro_phonemes

    phonemes = _kokoro_phonemes("学費はいくらですか。", "ja")
    assert phonemes, "misaki must produce phonemes for Japanese"
    # ikɯɾa = いくら; if espeak were still in charge this would be English
    # scaffolding such as '(en)chinese letter'.
    assert "ikɯɾa" in phonemes
    assert "(en)" not in phonemes
    assert _kokoro_phonemes("hello", "en") == ""


def test_grounding_refusal_is_localized_and_counted():
    from app.voice_grounding import enforce_grounding, is_refusal

    answer = "The School of International Studies costs $14,500 per year."
    context = "MBA tuition is $18,500 per year."

    out = enforce_grounding(answer, context, check_names=True, language="es")
    assert any(
        phrase in out for phrase in ("No tengo", "No quiero", "Eso no")
    ), out
    assert is_refusal(out, "es") is True

    # The English wording is still the default and still recognized.
    english = enforce_grounding(answer, context, check_names=True)
    assert "I don't have that detail" in english
    assert is_refusal(english) is True


def test_localized_unoffered_refusal_is_recognized():
    from app.voice_grounding import enforce_grounding, is_refusal

    out = enforce_grounding(
        "Mechanical engineering costs $14,500 per year.",
        "MBA tuition is $18,500 per year.",
        unoffered=["Mechanical engineering"],
        check_names=True,
        language="es",
    )
    assert "no ofrece" in out, out
    assert is_refusal(out, "es") is True


def test_japanese_and_chinese_speeds_are_raised_without_touching_indic(monkeypatch):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_SPEED", 1.0)
    assert vh._tts_speed_for("ja") == 1.2
    assert vh._tts_speed_for("zh") == 1.2
    assert vh._tts_speed_for("es") == 1.0
    assert vh._tts_speed_for("en") == 1.0
    assert vh._tts_speed_for("hi") == 1.0
    assert vh._tts_speed_for("gu") == 1.0
