"""Shared result/error types for the pluggable STT providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class STTResult:
    """One provider's transcription of one utterance."""

    transcript: str
    language: str = ""
    language_probability: float = 0.0
    provider: str = ""
    latency_ms: float = 0.0
    raw_language_code: str = ""


class STTError(RuntimeError):
    """A provider could not transcribe; the caller tries the next provider."""
