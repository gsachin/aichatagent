"""Sarvam and IndicConformer adapters, with no network or model download."""

from __future__ import annotations

import json

import numpy as np
import pytest


class _Response:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self):
        return self._payload


def test_sarvam_request_contract(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam

    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured.update(url=url, headers=headers, data=data,
                        files=files, timeout=timeout)
        return _Response({
            "transcript": "B.Tech की फीस कितनी है",
            "language_code": "hi-IN",
            "language_probability": 0.93,
            "request_id": "req-1",
        })

    monkeypatch.setattr(sarvam.requests, "post", fake_post)
    override_settings(
        SARVAM_API_KEY="test-key",
        SARVAM_STT_KEYTERMS="Meridian University,B.Tech,MBA",
    )

    result = sarvam.transcribe(np.zeros(1600, dtype=np.int16))

    assert captured["url"] == sarvam.settings.SARVAM_STT_URL
    assert captured["headers"]["api-subscription-key"] == "test-key"
    assert captured["data"]["model"] == "saaras:v4"
    assert captured["data"]["mode"] == "codemix"
    assert captured["data"]["language_code"] == "unknown"
    assert json.loads(captured["data"]["keyterms"]) == [
        "Meridian University", "B.Tech", "MBA",
    ]
    assert captured["files"]["file"][0] == "utterance.wav"
    assert captured["files"]["file"][2] == "audio/wav"
    assert result.transcript == "B.Tech की फीस कितनी है"
    assert result.language == "hi"
    assert result.language_probability == 0.93
    assert result.provider == "sarvam"


def test_sarvam_missing_key_raises(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam
    from app.stt_types import STTError

    override_settings(SARVAM_API_KEY="")
    with pytest.raises(STTError, match="SARVAM_API_KEY"):
        sarvam.transcribe(np.zeros(1600, dtype=np.int16))


def test_sarvam_http_error_raises(monkeypatch, override_settings):
    import app.stt_sarvam as sarvam
    from app.stt_types import STTError

    monkeypatch.setattr(
        sarvam.requests, "post",
        lambda *a, **k: _Response({}, status_code=429, text="rate limited"),
    )
    override_settings(SARVAM_API_KEY="test-key")
    with pytest.raises(STTError, match="HTTP 429"):
        sarvam.transcribe(np.zeros(1600, dtype=np.int16))


def test_indic_conformer_uses_language_model(monkeypatch, override_settings):
    import onnx_asr

    import app.stt_indic as indic

    calls = {}

    class _Model:
        def recognize(self, audio, sample_rate=16000):
            calls["shape"] = audio.shape
            calls["sample_rate"] = sample_rate
            return "नमस्ते, मुझे एमबीए की जानकारी चाहिए"

    def fake_load(repo, *, quantization=None, providers=None):
        calls.update(repo=repo, quantization=quantization, providers=providers)
        return _Model()

    monkeypatch.setattr(onnx_asr, "load_model", fake_load)
    indic._MODELS.clear()
    override_settings(INDIC_CONFORMER_QUANTIZATION="int8")

    result = indic.transcribe(np.zeros(1600, dtype=np.int16), "hi")

    assert calls["repo"].endswith("indicconformer-hi-onnx")
    assert calls["quantization"] == "int8"
    assert calls["sample_rate"] == 16000
    assert calls["shape"] == (1600,)
    assert result.provider == "indic_conformer"
    assert result.language == "hi"
    assert "एमबीए" in result.transcript
    indic._MODELS.clear()


def test_provider_order(override_settings):
    from app.stt import provider_order

    override_settings(
        STT_PROVIDER="sarvam",
        STT_FALLBACK_PROVIDERS="indic_conformer,whisper",
    )
    assert provider_order() == ["sarvam", "indic_conformer", "whisper"]

    override_settings(
        STT_PROVIDER="whisper",
        STT_FALLBACK_PROVIDERS="whisper,sarvam",
    )
    assert provider_order() == ["whisper", "sarvam"]
