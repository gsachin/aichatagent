"""TTS provider selection and Indic-TTS fallback (no service required)."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest


def test_kokoro_voice_mapping(monkeypatch):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_VOICE", "af_heart")
    assert vh._tts_voice_for("en") == "af_heart"
    assert vh._tts_voice_for("hi") == "hf_alpha"
    assert vh._tts_voice_for("ta") == "af_heart"   # no Kokoro Tamil voice


def test_kokoro_phonemizer_language():
    import app.voice_handler as vh

    assert vh._kokoro_lang_for("hi", "hf_alpha") == "hi"
    assert vh._kokoro_lang_for("en", "af_heart") == "en-us"
    assert vh._kokoro_lang_for("en", "bf_emma") == "en-gb"
    assert vh._kokoro_lang_for("ta", "af_heart") == "en-us"


def test_kokoro_stream_uses_hindi_phonemes(monkeypatch):
    import app.voice_handler as vh

    class _FakeKokoro:
        def __init__(self):
            self.langs: list[str] = []

        async def create_stream(self, text, voice=None, speed=None,
                                lang="en-us"):
            self.langs.append(lang)
            yield np.full(80, 0.1, dtype=np.float32), 24000

    fake = _FakeKokoro()
    monkeypatch.setattr(vh, "_get_tts_engine", lambda: fake)
    session = vh.VoiceCallSession(language="hi")

    async def collect():
        return [chunk async for chunk in
                session.synthesise_stream("unique hindi stream sample")]

    chunks = asyncio.run(collect())
    assert fake.langs == ["hi"]
    assert len(chunks) == 1


def test_sarvam_tts_routing(monkeypatch):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu"}))
    assert vh._use_sarvam_tts("gu") is True
    assert vh._use_sarvam_tts("hi") is False


def test_hindi_routes_to_sarvam_when_listed(monkeypatch, override_settings):
    """Hindi on Bulbul is the quality fix: Kokoro hf_alpha mangles "MBA"."""
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu", "hi"}))
    override_settings(SARVAM_API_KEY="test-key")
    assert vh._use_sarvam_tts("hi") is True


def test_hindi_without_a_key_falls_back_to_kokoro(monkeypatch, override_settings):
    """Hindi has a local voice; a keyless deployment must still answer.

    Gujarati deliberately does not fall back: Kokoro has no Gujarati voice, so
    the adapter's failure path is better than an English voice reading it.
    """
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu", "hi"}))
    monkeypatch.setattr(vh, "_SARVAM_KEY_WARNED", True)
    override_settings(SARVAM_API_KEY="")
    assert vh._use_sarvam_tts("hi") is False
    assert vh._use_sarvam_tts("gu") is True


def test_hindi_speaker_is_the_hindi_persona(override_settings):
    import app.tts_sarvam as tts

    override_settings(SARVAM_TTS_SPEAKER="", SARVAM_TTS_SPEAKERS="")
    assert tts.speaker_for("hi") == "aparna_hi_customer"
    assert tts.speaker_for("gu") == "pooja_gu_customer"


def test_a_gujarati_override_does_not_become_hindi_voice(override_settings):
    """The live .env used to set SARVAM_TTS_SPEAKER=pooja_gu_customer.

    Enabling Hindi without a language check would have made it speak in the
    Gujarati persona -- the exact opposite of the quality fix.
    """
    import app.tts_sarvam as tts

    override_settings(SARVAM_TTS_SPEAKER="pooja_gu_customer",
                      SARVAM_TTS_SPEAKERS="")
    assert tts.speaker_for("hi") == "aparna_hi_customer"
    assert tts.speaker_for("gu") == "pooja_gu_customer"


def test_per_language_speaker_map_wins(override_settings):
    import app.tts_sarvam as tts

    override_settings(
        SARVAM_TTS_SPEAKER="pooja_gu_customer",
        SARVAM_TTS_SPEAKERS=(
            "gu:pooja_gu_conversational,hi:aparna_hi_customer"
        ),
    )
    assert tts.speaker_for("gu") == "pooja_gu_conversational"
    assert tts.speaker_for("hi") == "aparna_hi_customer"


def test_hindi_tts_text_normalises_devanagari_acronyms():
    """The model occasionally writes एमबीए; Bulbul reads that as "ambhee".

    The Latin form is pronounced letter-by-letter by Bulbul, so the synthesis
    input is canonicalised without touching the transcript.
    """
    from app.voice_handler import _tts_text_for

    assert "MBA" in _tts_text_for("मुझे एमबीए करना है", "hi")
    assert "B.Tech" in _tts_text_for("बी.टेक चार साल का है", "hi")
    assert "B.Sc" in _tts_text_for("बी.एससी", "hi")
    assert _tts_text_for("MBA", "hi") == "MBA"
    assert _tts_text_for("एमबीए", "en") == "एमबीए"


def test_hindi_sarvam_receives_the_normalised_text(monkeypatch, override_settings):
    import app.tts_sarvam as tts
    import app.voice_handler as vh

    captured = {}

    async def fake_stream(text, language, *, speaker=None, pace=None):
        captured.update(text=text, language=language, speaker=speaker)
        yield np.full(80, 0.1, dtype=np.float32)

    monkeypatch.setattr(tts, "astream", fake_stream)
    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu", "hi"}))
    override_settings(SARVAM_API_KEY="test-key", SARVAM_TTS_PACE=1.1,
                      SARVAM_TTS_SPEAKER="", SARVAM_TTS_SPEAKERS="")

    session = vh.VoiceCallSession(language="hi")

    async def collect():
        return [chunk async for chunk in
                session.synthesise_stream("मुझे एमबीए करना है।")]

    asyncio.run(collect())
    assert captured["text"] == "मुझे MBA करना है।"
    assert captured["language"] == "hi"
    assert captured["speaker"] == "aparna_hi_customer"


def test_kokoro_hindi_strips_language_switch_flags():
    """Kokoro's fallback path must not vocalise (en)/(hi) as "in"/"hi"."""
    import app.voice_handler as vh

    class _Tokenizer:
        def phonemize(self, text, lang):
            assert lang == "hi"
            return "(en)ˈɛm bˈi ˈeɪ(hi) kˈʌɾnˌaː"

    class _FakeEngine:
        tokenizer = _Tokenizer()

        def create(self, text, **kwargs):
            self.seen = (text, kwargs)
            return np.zeros(80, dtype=np.float32), 24000

    engine = _FakeEngine()
    vh._kokoro_render(engine, "MBA करना", "hi", "hf_alpha", 1.0)
    phonemes, kwargs = engine.seen
    assert "(" not in phonemes and ")" not in phonemes
    assert kwargs["is_phonemes"] is True


def test_greeting_uses_sarvam_for_gujarati(monkeypatch):
    import app.tts_sarvam as tts
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu"}))
    monkeypatch.setattr(
        tts, "synthesise",
        lambda text, language, **kwargs: (
            np.full(320, 0.1, dtype=np.float32), 8000
        ),
    )

    chunks = vh.generate_ulaw_greeting("નમસ્તે", language="gu")
    assert chunks
    assert all(isinstance(chunk, bytes) for chunk in chunks)


def test_sarvam_tts_dispatch(monkeypatch, override_settings):
    import app.tts_sarvam as tts
    import app.voice_handler as vh

    captured = {}

    def fake_synth(text, language, *, speaker=None, pace=None):
        captured.update(text=text, language=language,
                        speaker=speaker, pace=pace)
        return np.full(240, 0.1, dtype=np.float32), 8000

    monkeypatch.setattr(tts, "synthesise", fake_synth)
    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu"}))
    override_settings(SARVAM_TTS_PACE=1.1,
                      SARVAM_TTS_SPEAKER="pooja_gu_customer")

    session = vh.VoiceCallSession(language="gu")
    audio = asyncio.run(session._synthesise("unique sarvam gujarati text"))

    assert audio is not None
    assert audio.shape == (240,)
    assert captured["language"] == "gu"
    assert captured["speaker"] == "pooja_gu_customer"
    assert captured["pace"] == 1.1


def test_batch_converter_keeps_sarvam_8k_duration(monkeypatch):
    """A Sarvam 8 kHz buffer must not be resampled as if it were 24 kHz.

    The noise-gated and no-speech fallback lines take the batch path. With the
    old hard-coded 24000 assumption, a 3.4 s Gujarati fallback was resampled
    to 1.13 s and played three times too fast -- the "fast-forwarded noise"
    callers heard on short/unintelligible turns.
    """
    import app.tts_sarvam as tts
    import app.voice_handler as vh

    monkeypatch.setattr(
        tts, "synthesise",
        lambda text, language, **kwargs: (
            np.zeros(8000, dtype=np.float32), 8000
        ),
    )
    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu"}))
    session = vh.VoiceCallSession(language="gu")

    audio, sr = asyncio.run(
        session._synthesise_with_rate("unique fallback rate regression text")
    )
    assert sr == 8000
    chunks = session._pcm_to_ulaw_chunks(audio, sr)
    assert len(chunks) == 50          # 1.0 s / 20 ms -- not 1/3 of it


def test_sarvam_tts_stream_dispatch(monkeypatch, override_settings):
    import app.tts_sarvam as tts
    import app.voice_handler as vh

    captured = {}

    async def fake_stream(text, language, *, speaker=None, pace=None):
        captured.update(language=language, speaker=speaker, pace=pace)
        yield np.full(80, 0.1, dtype=np.float32)
        yield np.full(80, 0.2, dtype=np.float32)

    monkeypatch.setattr(tts, "astream", fake_stream)
    monkeypatch.setattr(vh, "TTS_PROVIDER", "sarvam")
    monkeypatch.setattr(vh, "_SARVAM_TTS_LANGUAGES", frozenset({"gu"}))
    override_settings(SARVAM_TTS_PACE=1.1,
                      SARVAM_TTS_SPEAKER="pooja_gu_customer")

    session = vh.VoiceCallSession(language="gu")

    async def collect():
        return [chunk async for chunk in
                session.synthesise_stream("unique sarvam stream text")]

    chunks = asyncio.run(collect())
    assert len(chunks) == 2
    assert all(sr == 8000 for _audio, sr in chunks)
    assert captured["language"] == "gu"
    assert captured["speaker"] == "pooja_gu_customer"


def test_sarvam_tts_request_contract(monkeypatch, override_settings):
    import app.tts_sarvam as tts

    captured = {}

    class _Response:
        status_code = 200

        def iter_bytes(self, chunk_size=3200):
            yield np.array([100, -100, 200], dtype="<i2").tobytes()

        def read(self):
            return b""

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None, json=None):
            captured.update(method=method, url=url, headers=headers, json=json)
            return _Stream()

    monkeypatch.setattr(tts.httpx, "Client", _Client)
    override_settings(
        SARVAM_API_KEY="test-key",
        SARVAM_TTS_SPEAKER="pooja_gu_customer",
        SARVAM_TTS_PACE=1.1,
        SARVAM_TTS_SAMPLE_RATE=8000,
    )

    audio, sr = tts.synthesise("નમસ્તે, ફી કેટલી છે?", "gu")

    assert sr == 8000
    assert audio.shape == (3,)
    assert captured["method"] == "POST"
    assert captured["headers"]["api-subscription-key"] == "test-key"
    body = captured["json"]
    assert body["language_code"] == "gu-IN"
    assert body["speaker"] == "pooja_gu_customer"
    assert body["model"] == "bulbul:v4-flash"
    assert body["output_audio_codec"] == "linear16"
    assert body["speech_sample_rate"] == 8000
    assert body["pace"] == 1.1
    assert body["enable_preprocessing"] is True


def test_sarvam_tts_missing_key(monkeypatch, override_settings):
    import app.tts_sarvam as tts

    override_settings(SARVAM_API_KEY="")
    with pytest.raises(tts.TTSError, match="SARVAM_API_KEY"):
        tts.synthesise("નમસ્તે", "gu")


def test_sarvam_tts_http_error(monkeypatch, override_settings):
    import app.tts_sarvam as tts

    class _Response:
        status_code = 401

        def read(self):
            return b'{"error":"invalid key"}'

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *args):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _Stream()

    monkeypatch.setattr(tts.httpx, "Client", _Client)
    override_settings(SARVAM_API_KEY="bad-key")
    with pytest.raises(tts.TTSError, match="HTTP 401"):
        tts.synthesise("નમસ્તે", "gu")


def test_indic_provider_selection(monkeypatch):
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "kokoro")
    assert vh._use_indic_tts("ta") is False
    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    assert vh._use_indic_tts("ta") is True
    assert vh._use_indic_tts("hi") is True
    assert vh._use_indic_tts("en") is False


def test_indic_synthesis_uses_service(monkeypatch):
    import app.indic_tts as indic
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    monkeypatch.setattr(
        indic, "synthesise",
        lambda text, language: (np.full(240, 0.1, dtype=np.float32), 24000),
    )
    session = vh.VoiceCallSession(language="ta")
    audio = asyncio.run(session._synthesise("unique indic text"))
    assert audio is not None
    assert audio.shape == (240,)


def test_hindi_falls_back_to_kokoro(monkeypatch):
    import app.indic_tts as indic
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    monkeypatch.setattr(indic, "synthesise", lambda text, language: None)

    class _FakeKokoro:
        def __init__(self):
            self.langs: list[str] = []

        def create(self, text, voice=None, speed=None, lang="en-us"):
            self.langs.append(lang)
            return np.full(160, 0.2, dtype=np.float32), 24000

    fake = _FakeKokoro()
    monkeypatch.setattr(vh, "_get_tts_engine", lambda: fake)
    session = vh.VoiceCallSession(language="hi")
    audio = asyncio.run(session._synthesise("unique hindi fallback"))
    assert audio is not None
    assert audio.shape == (160,)
    # Kokoro's default "en-us" phonemizer gives Hindi an English accent.
    assert fake.langs == ["hi"]


def test_unsupported_indic_language_returns_none(monkeypatch):
    import app.indic_tts as indic
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    monkeypatch.setattr(indic, "synthesise", lambda text, language: None)
    session = vh.VoiceCallSession(language="ta")
    assert asyncio.run(session._synthesise("unique unsupported")) is None


def test_streaming_indic_yields_one_chunk(monkeypatch):
    import app.indic_tts as indic
    import app.voice_handler as vh

    monkeypatch.setattr(vh, "TTS_PROVIDER", "indic_tts")
    monkeypatch.setattr(
        indic, "synthesise",
        lambda text, language: (np.full(320, 0.1, dtype=np.float32), 24000),
    )
    session = vh.VoiceCallSession(language="bn")

    async def collect():
        return [chunk async for chunk in session.synthesise_stream("unique stream text")]

    chunks = asyncio.run(collect())
    assert len(chunks) == 1
    audio, sr = chunks[0]
    assert audio.shape == (320,)
    assert sr == 24000


def test_sarvam_async_client_is_reused(monkeypatch):
    import app.tts_sarvam as tts

    created = []

    class _Client:
        def __init__(self, *args, **kwargs):
            self.is_closed = False
            self.closed = False
            created.append(self)

        async def aclose(self):
            self.closed = True
            self.is_closed = True

    monkeypatch.setattr(tts.httpx, "AsyncClient", _Client)
    tts._ASYNC_CLIENT = None
    tts._ASYNC_CLIENT_LOOP = None

    async def use():
        first = tts._async_client()
        assert tts._async_client() is first
        await tts.aclose()
        assert first.closed
        second = tts._async_client()
        assert second is not first
        await tts.aclose()

    asyncio.run(use())
    assert len(created) == 2


def test_sarvam_async_client_follows_the_event_loop(monkeypatch):
    import app.tts_sarvam as tts

    created = []

    class _Client:
        def __init__(self, *args, **kwargs):
            self.is_closed = False
            created.append(self)

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(tts.httpx, "AsyncClient", _Client)
    tts._ASYNC_CLIENT = None
    tts._ASYNC_CLIENT_LOOP = None

    async def one():
        return tts._async_client()

    first = asyncio.run(one())
    second = asyncio.run(one())
    try:
        assert second is not first, "a client from a closed loop was reused"
        assert len(created) == 2
    finally:
        tts._ASYNC_CLIENT = None
        tts._ASYNC_CLIENT_LOOP = None


def test_greeting_cache_roundtrip_and_copy():
    import app.voice_handler as vh

    vh.clear_greeting_cache()
    try:
        key = vh.greeting_cache_key("hi", "outbound", "नमस्ते")
        assert vh.greeting_cache_get(key) is None
        frames = [b"a", b"b"]
        vh.greeting_cache_put(key, frames)
        cached = vh.greeting_cache_get(key)
        assert cached == frames
        cached.append(b"c")
        assert vh.greeting_cache_get(key) == frames, "the cache handed out its list"
        assert vh.greeting_cache_key("hi", "inbound", "नमस्ते") != key
        assert vh.greeting_cache_key("hi", "outbound", "नमस्ते!") != key
    finally:
        vh.clear_greeting_cache()


def test_sarvam_astream_transport_error_keeps_the_shared_client(monkeypatch,
                                                                override_settings):
    """A failed request must not close the pool another caller is streaming on."""
    import httpx

    import app.tts_sarvam as tts

    created = []

    class _Client:
        def __init__(self, *args, **kwargs):
            self.is_closed = False
            self.closed = False
            created.append(self)

        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("connection dropped")

        async def aclose(self):
            self.closed = True
            self.is_closed = True

    monkeypatch.setattr(tts.httpx, "AsyncClient", _Client)
    tts._ASYNC_CLIENT = None
    tts._ASYNC_CLIENT_LOOP = None
    override_settings(SARVAM_API_KEY="test-key")

    async def run():
        client = tts._async_client()
        with pytest.raises(tts.TTSError, match="request failed"):
            async for _chunk in tts.astream("નમસ્તે", "gu"):
                pass
        assert not client.closed, "the shared pool was closed under another caller"
        await tts.aclose()

    asyncio.run(run())
    assert len(created) == 1


def test_greeting_asset_name_tracks_the_voice_identity(monkeypatch):
    import app.voice_handler as vh

    first = vh.greeting_asset_name("hi", "outbound", "नमस्ते")
    assert vh.greeting_asset_name("hi", "outbound", "नमस्ते") == first
    assert vh.greeting_asset_name("hi", "inbound", "नमस्ते") != first
    assert vh.greeting_asset_name("hi", "outbound", "नमस्ते!") != first
    # A voice change must produce a different asset, never stale audio.
    monkeypatch.setattr(vh, "_tts_voice_for", lambda code: "hf_beta")
    assert vh.greeting_asset_name("hi", "outbound", "नमस्ते") != first


def test_greeting_asset_frames_loads_a_carrier_wav(monkeypatch, tmp_path):
    import wave

    import app.admission as admission
    import app.voice_handler as vh

    text = "asset probe greeting"
    name = vh.greeting_asset_name("hi", "outbound", text)
    with wave.open(str(tmp_path / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 1600)

    monkeypatch.setattr(admission, "asset_path", lambda asset: tmp_path / asset)
    frames = vh.greeting_asset_frames("hi", "outbound", text)

    assert frames is not None
    assert len(frames) == 10          # 1600 samples / 160 per 20 ms frame
    assert all(isinstance(frame, bytes) for frame in frames)


def test_greeting_asset_frames_is_none_when_missing(monkeypatch, tmp_path):
    import app.admission as admission
    import app.voice_handler as vh

    monkeypatch.setattr(admission, "asset_path", lambda asset: tmp_path / asset)
    assert vh.greeting_asset_frames("hi", "outbound", "no such asset") is None
