"""Characterization tests for behaviour that must not change.

These pin the existing English/Hindi/Gujarati outputs before the six foreign
languages are added. If one of these fails, the change is not additive: either
the new code reached a path it should not have, or an existing string/rule was
rewritten instead of extended.

The hashes are of the exact strings. They are intentionally opaque: this file
guards byte-identity, while the readable assertions in the other language tests
describe what the strings say.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Exact DSPMU strings as of 2026-10-25. These guard byte-identity for the
# English, Hindi and Gujarati outputs while foreign languages are added.
_INSTRUCTION_HASHES = {
    "en": "a54cf2d501bda113a8c9a8675309682364caa1299a574db8bf9ce928b5b4549f",
    "hi": "1c59842ad342be07a39948e20fbf0f07103c640481d3ca2a5d46e3cb40abe5a1",
    "gu": "610ea6ca2caad2b7e7b274f13fa3292b9f48a9d6aafe626f41bd90cfe251cece",
}

_GREETING_HASHES = {
    ("inbound", "en"): "d007a5a300ce68f4f45cb3ffc97ffa78eb873c5a0759ccc8d8e04e951c8d921b",
    ("inbound", "hi"): "c28ae9d6fd100a0b9ba6e8bfc1dd910ee7437277a403939fb2a11db752aa7a03",
    ("inbound", "gu"): "7934023b1a1ce30e8ec636c2010afa1f9218dcd2c2ffa36410df5f442ca86e92",
    ("outbound", "en"): "0962aa33a94dfba29c80bf16447c4142c876d744527b2b00290b06cd3587e012",
    ("outbound", "hi"): "1daabbccaf17502332c9ababa140330575fd914fd9eb35bac6bb9f16c0a5c47e",
    ("outbound", "gu"): "e67ff67b0a28936abfccc3e76fe831b626691b90dc6ab8072a7dbd09ba3a1162",
}

# Deliberately includes Indic, Latin, punctuation-only and short sentences.
_SPLIT_CORPUS = [
    "Hello there. How are you?",
    "One line. Two line.",
    "नमस्ते। आप कैसे हैं?",
    "ઠીક છે. પ્રવેશ માટે અરજી કરો.",
    "पहला वाक्य। दूसरा वाक्य।",
]


def test_language_instruction_for_existing_codes_is_unchanged():
    from app.voice_system_prompt import language_instruction

    for code, expected in _INSTRUCTION_HASHES.items():
        assert _sha(language_instruction(code)) == expected, code


def test_greetings_for_existing_codes_are_unchanged():
    from app.voice_system_prompt import call_greeting

    for (direction, code), expected in _GREETING_HASHES.items():
        assert _sha(call_greeting(code, direction)) == expected, (direction, code)


def test_split_sentences_existing_corpus_is_unchanged():
    from app.voice_handler import _split_sentences

    expected = {
        "Hello there. How are you?": ["Hello there.", "How are you?"],
        "One line. Two line.": ["One line.", "Two line."],
        "नमस्ते। आप कैसे हैं?": ["नमस्ते।", "आप कैसे हैं?"],
        "ઠીક છે. પ્રવેશ માટે અરજી કરો.": ["ઠીક છે. પ્રવેશ માટે અરજી કરો."],
        "पहला वाक्य। दूसरा वाक्य।": ["पहला वाक्य।", "दूसरा वाक्य।"],
    }
    for text in _SPLIT_CORPUS:
        assert _split_sentences(text) == expected[text], text


def test_grounding_sentences_existing_corpus_is_unchanged():
    from app.voice_grounding import _sentences

    expected = {
        "Hello there. How are you?": ["Hello there.", "How are you?"],
        "One line. Two line.": ["One line.", "Two line."],
        "नमस्ते। आप कैसे हैं?": ["नमस्ते।", "आप कैसे हैं?"],
        "ઠીક છે. પ્રવેશ માટે અરજી કરો.": ["ઠીક છે.", "પ્રવેશ માટે અરજી કરો."],
        "पहला वाक्य। दूसरा वाक्य।": ["पहला वाक्य।", "दूसरा वाक्य।"],
    }
    for text in _SPLIT_CORPUS:
        assert _sentences(text) == expected[text], text


def test_clause_cutter_existing_behaviour_is_unchanged():
    from app.llm_backend import ClauseCutter

    cutter = ClauseCutter()
    assert [c.text for c in cutter.feed("Hello there. How are you?")] == [
        "Hello there."
    ]
    assert cutter.flush().text == "How are you?"

    # A final clause below MIN_CLAUSE_CHARS is deliberately dropped.
    cutter = ClauseCutter()
    assert [c.text for c in cutter.feed("Tamil sentence. Second one?")] == [
        "Tamil sentence."
    ]
    assert cutter.flush() is None

    cutter = ClauseCutter()
    assert [c.text for c in cutter.feed("One line. Two line.")] == []
    assert cutter.flush().text == "One line. Two line."

    # The danda merge for short Indic openings must still hold.
    cutter = ClauseCutter()
    assert [c.text for c in cutter.feed(
        "ठीक है। प्रवेश के लिए पहले ऑनलाइन आवेदन करना होता है।"
    )] == ["ठीक है। प्रवेश के लिए पहले ऑनलाइन आवेदन करना होता है।"]


def test_noise_fragment_defaults_are_unchanged():
    from app.voice_handler import is_noise_fragment

    # No language argument: exactly today's three-character rule.
    for text in ("no", "ok", "is", "sí", "はい", "是"):
        assert is_noise_fragment(text) is True, text
    for text in ("नमस्ते", "hello"):
        assert is_noise_fragment(text) is False, text


def test_ivr_digits_are_unchanged():
    from app.main import _language_for_call

    assert _language_for_call("1") == "en"
    assert _language_for_call("2") == "hi"
    assert _language_for_call("3") == "gu"
    assert _language_for_call("4", "hi") == "hi"
    assert _language_for_call("9", "hi") == "hi"
    assert _language_for_call("", "gu") == "gu"


def test_kokoro_maps_for_existing_languages_are_unchanged():
    from app.voice_handler import (
        _KOKORO_LANGUAGE_VOICES,
        _KOKORO_PHONEME_LANG,
    )

    assert _KOKORO_LANGUAGE_VOICES["en"] == "af_heart"
    assert _KOKORO_LANGUAGE_VOICES["hi"] == "hf_alpha"
    assert _KOKORO_PHONEME_LANG["en"] == "en-us"
    assert _KOKORO_PHONEME_LANG["hi"] == "hi"


def test_english_assets_keep_their_shape():
    """The two English assets must not be re-rendered by this work.

    ``app/static/audio/`` is gitignored, so a fresh clone reports no manifest.
    Skip rather than fail in that case; the build gate in Phase 3 compares the
    manifest when one exists.

    ``busy.wav`` is 152,620 bytes on every run but its SHA alternates between
    two values on this CUDA box (8f95c7d0… and 7c97858b…) with no code change
    between them: Kokoro/ONNX CUDA is not bit-reproducible across processes.
    The hash is therefore not a valid invariant for that asset. The length,
    voice and language are; ``fixed_response.wav`` is stable and keeps its
    hash. The code-path guarantee (English synthesis still calls
    ``engine.create`` with no ``lang``) is tested separately.
    """
    manifest_path = Path("app/static/audio/manifest.json")
    if not manifest_path.is_file():
        pytest.skip("call assets have not been built in this checkout")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    busy = manifest["assets"]["busy.wav"]
    assert busy["bytes"] == 186412
    assert busy["language"] == "en"
    assert busy["voice"] == "af_heart"

    fixed = manifest["assets"]["fixed_response.wav"]
    assert fixed["sha256"] == (
        "8c77c09d95c137cafb18aca785fe5b578aba0339af4e85b76209d50d7f7df169"
    )
    assert fixed["language"] == "en"
    assert fixed["voice"] == "af_heart"
