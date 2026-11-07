"""OpenAI LLM gateway — remote Chat Completions only.

This module is the single place every LLM call goes through. The local Ollama
and MLX backends, their embeddings, model discovery, residency/keep-alive
handling and RAG retrieval integration have been removed. All generation —
voice, text, WhatsApp, lead extraction, intent checks and sentiment — reaches
OpenAI's Chat Completions API.

Contract (unchanged, and relied on by the voice handler):

* ``chat``          — synchronous single-shot completion, returns text.
* ``generate_stream`` — async iterator of ``str`` deltas followed by exactly
  one ``EngineCounters``; raises ``GenerationFailed``/``GenerationCancelled``.

No API call is made at import time. A missing ``LLM_API_KEY`` fails at the
first real request and is reported by ``health()``/``is_ready()``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.config import ConfigurationError, settings
from app.languages import (
    CJK_LANGUAGES,
    contains_cjk,
    normalize_language_code,
)

logger = logging.getLogger("llm_backend")

#: Prompt budget retained for callers that still pass ``num_ctx``. OpenAI's
#: context window is model-defined; the value is not sent as a request param.
DEFAULT_NUM_CTX = 8192

LLM_MODEL = settings.LLM_MODEL
LLM_API_KEY = settings.LLM_API_KEY
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_TIMEOUT_S = settings.LLM_TIMEOUT_S
LLM_MAX_TOKENS = settings.LLM_MAX_TOKENS
LLM_REASONING_EFFORT = settings.LLM_REASONING_EFFORT
LLM_PROMPT_CACHE_KEY = settings.LLM_PROMPT_CACHE_KEY
LLM_STREAM = settings.LLM_STREAM
LLM_STREAM_INCLUDE_USAGE = settings.LLM_STREAM_INCLUDE_USAGE

_openai_client_singleton = None
_openai_client_lock = threading.Lock()
_provider: str | None = None


def provider_name() -> str:
    """The generation provider. There is exactly one: ``openai``.

    ``auto`` is accepted as a migration alias and resolves to OpenAI too. A
    stale ``LLM_PROVIDER=ollama|mlx`` is a configuration error, not a silent
    fallback: the local backends no longer exist.
    """
    global _provider
    if _provider is None:
        configured = (settings.LLM_PROVIDER or "").strip().lower()
        if configured in ("openai", "auto", ""):
            _provider = "openai"
        elif configured in ("ollama", "mlx"):
            raise ConfigurationError(
                f"LLM_PROVIDER={configured!r} was removed. Set "
                "LLM_PROVIDER=openai (or auto) and LLM_API_KEY in .env."
            )
        else:
            raise ConfigurationError(
                f"LLM_PROVIDER={configured!r} is not supported; use 'openai'."
            )
        logger.info("LLM backend: openai (model=%s)", LLM_MODEL)
    return _provider


def default_model(preferred=None) -> list[str]:
    """Kept for call-site compatibility; OpenAI always uses ``LLM_MODEL``."""
    prefs = list(preferred or [])
    return [LLM_MODEL] + [p for p in prefs if p != LLM_MODEL]


def pick_model(preferred=None) -> str:
    """The remote model id. Local tags passed by callers are ignored."""
    provider_name()
    return LLM_MODEL


def small_task_num_ctx() -> int:
    """Prompt-size hint for utility calls; not sent to OpenAI."""
    return DEFAULT_NUM_CTX


# ── Engine accounting ─────────────────────────────────────────────────

@dataclass(frozen=True)
class EngineCounters:
    """The engine's own account of a generation (absent = None, never 0)."""

    prompt_eval_count: int | None
    eval_count: int | None
    prompt_eval_duration_ns: int | None
    eval_duration_ns: int | None


@dataclass(frozen=True)
class Clause:
    """One complete, speakable piece of the answer."""

    text: str
    is_final: bool


class GenerationFailed(Exception):
    """The stream produced nothing usable; the fixed response path speaks."""


class GenerationCancelled(Exception):
    """The caller hung up (or the handler died) mid-generation."""


class ClauseCutter:
    """Accumulates token deltas and emits complete clauses.

    A clause ends at sentence punctuation followed by whitespace and a
    capital, and never right after a digit -- the SAME boundary rule
    ``_split_sentences`` uses in voice_handler, so a clause handed to the
    per-sentence TTS stream is never re-split. Clauses shorter than
    MIN_CLAUSE_CHARS are re-merged into the next one rather than spoken alone;
    a trailing partial clause is dropped by flush().

    Japanese and Chinese use ``。！？`` with no following whitespace, so they
    get their own zero-width boundary and a one-character minimum: a complete
    answer can be ``はい。``, and the Latin minimum would make flush() drop it
    and leave the caller in silence. Every other language keeps the existing
    twelve-character rule and boundaries.
    """

    MIN_CLAUSE_CHARS = 12
    _BOUNDARY = re.compile(
        r"(?<![0-9][.!?;])(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-ÞŒŸ])"
    )
    #: Devanagari/Gujarati/Bengali/etc. sentence terminators. These scripts do
    #: not capitalise the next sentence, so the Latin rule above cannot match.
    _BOUNDARY_INDIC = re.compile(r"(?<=[।॥])\s*")
    #: CJK terminators: zero-width, because these scripts do not use spaces.
    _BOUNDARY_CJK = re.compile(r"(?<=[。！？])\s*")
    _PUNCTUATION = ".!?;।॥。！？"

    def __init__(self, language: str | None = None) -> None:
        self._language = normalize_language_code(language)
        self._buf = ""
        #: First index not yet ruled out as a boundary. A short clause is
        #: merged forward by advancing this past its terminator instead of
        #: rewriting the buffer: the Indic boundary is zero-width, so
        #: re-prepending the same text would match the identical danda
        #: forever (and the guard would drop the whole answer).
        self._scan = 0

    def _min_chars(self) -> int:
        """Minimum clause length for this session's script."""
        if self._language in CJK_LANGUAGES or contains_cjk(self._buf):
            return 1
        return self.MIN_CLAUSE_CHARS

    def feed(self, delta: str) -> list[Clause]:
        self._buf += delta
        out: list[Clause] = []
        guard = 0
        while guard < 10_000:
            guard += 1
            window = self._buf[self._scan:]
            latin = self._BOUNDARY.search(window)
            indic = self._BOUNDARY_INDIC.search(window)
            cjk = self._BOUNDARY_CJK.search(window)
            m = min(
                (
                    candidate
                    for candidate in (latin, indic, cjk)
                    if candidate is not None
                ),
                key=lambda candidate: candidate.start(),
                default=None,
            )
            if not m:
                # A boundary may be split across deltas: the terminator is
                # already buffered but the whitespace/capital has not arrived.
                # Resume at the last terminator, or past the buffer.
                last = max(
                    (self._buf.rfind(ch, self._scan) for ch in self._PUNCTUATION),
                    default=-1,
                )
                # One character of lookbehind: the Latin regex must still see
                # the digit in "3." when it is the first character of the
                # window, or the decimal guard would stop working.
                self._scan = max(0, last - 1) if last >= 0 else len(self._buf)
                break
            end = self._scan + m.end()
            text = self._buf[:end].strip()
            if len(text) >= self._min_chars():
                self._buf = self._buf[end:]
                self._scan = 0
                out.append(Clause(text=text, is_final=False))
            else:
                # Too short to speak alone: keep it in the buffer and look
                # for the next boundary, so it merges with the next clause.
                self._scan = end
        if guard >= 10_000:                      # defensive; unreachable
            logger.error("ClauseCutter: guard tripped; dropping buffer")
            self._buf = ""
            self._scan = 0
        return out

    def flush(self) -> Clause | None:
        text = self._buf.strip()
        if len(text) >= self._min_chars():
            self._buf = ""
            return Clause(text=text, is_final=True)
        return None


def _messages(system: str | None, prompt: str) -> list[dict]:
    """Build the chat messages, putting instructions in a SYSTEM turn.

    With the instructions in the user turn the model answers them instead of
    the caller; this construction is shared by every serving path.
    """
    if system and system.strip():
        return [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    return [{"role": "user", "content": prompt}]


# ── OpenAI client ─────────────────────────────────────────────────────

def _openai_client():
    """Shared sync OpenAI client (thread-safe, one connection pool)."""
    global _openai_client_singleton
    if _openai_client_singleton is None:
        with _openai_client_lock:
            if _openai_client_singleton is None:
                if not LLM_API_KEY:
                    raise ConfigurationError(
                        "LLM_API_KEY is empty. Fill the placeholder in .env."
                    )
                import httpx
                from openai import OpenAI

                timeout = httpx.Timeout(
                    LLM_TIMEOUT_S, connect=min(4.0, LLM_TIMEOUT_S)
                )
                _openai_client_singleton = OpenAI(
                    api_key=LLM_API_KEY,
                    base_url=LLM_BASE_URL,
                    timeout=timeout,
                    max_retries=0,
                )
    return _openai_client_singleton


def _openai_request_kwargs(
    messages,
    *,
    temperature=None,
    json_mode=False,
    num_predict=None,
    stream=False,
) -> dict:
    """Map the adapter's historical arguments to OpenAI Chat Completions."""
    kwargs: dict = {"model": LLM_MODEL, "messages": messages}
    if num_predict is not None and int(num_predict) > 0:
        kwargs["max_completion_tokens"] = int(num_predict)
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if LLM_REASONING_EFFORT:
        kwargs["reasoning_effort"] = LLM_REASONING_EFFORT
    if LLM_PROMPT_CACHE_KEY:
        kwargs["prompt_cache_key"] = LLM_PROMPT_CACHE_KEY
    if stream:
        kwargs["stream"] = True
        if LLM_STREAM_INCLUDE_USAGE:
            kwargs["stream_options"] = {"include_usage": True}
    return kwargs


def _note_usage(response) -> None:
    usage = getattr(response, "usage", None)
    try:
        from app.perf_trace import note_current

        note_current(
            model_used=LLM_MODEL,
            provider="openai",
            prompt_eval_count=(
                getattr(usage, "prompt_tokens", None) if usage else None
            ),
            eval_count=(
                getattr(usage, "completion_tokens", None) if usage else None
            ),
            cached_tokens=(
                getattr(getattr(usage, "prompt_tokens_details", None),
                        "cached_tokens", None)
                if usage else None
            ),
            # OpenAI does not report engine prefill/decode durations.
            prefill_ms=None,
            generation_ms=None,
        )
    except Exception:                                    # noqa: BLE001
        pass


def _chat_openai(
    messages,
    *,
    temperature=None,
    json_mode=False,
    num_predict=None,
) -> str:
    client = _openai_client()
    response = client.chat.completions.create(**_openai_request_kwargs(
        messages, temperature=temperature, json_mode=json_mode,
        num_predict=num_predict, stream=False,
    ))
    content = ""
    if response.choices:
        content = response.choices[0].message.content or ""
    _note_usage(response)
    if not content:
        logger.warning("OpenAI returned an empty completion")
    return content


def chat(
    messages,
    *,
    model=None,
    preferred=None,
    num_ctx=None,
    temperature=None,
    json_mode=False,
    num_predict=None,
) -> str:
    """Single-shot OpenAI completion. Returns the assistant text.

    The legacy keyword arguments are accepted and ignored where they are
    local-engine concepts (``model`` tags, ``preferred`` tags, ``num_ctx``),
    so the fourteen existing call sites did not change during the migration.
    """
    provider_name()
    return _chat_openai(
        messages, temperature=temperature, json_mode=json_mode,
        num_predict=num_predict,
    )


# ── Streaming ─────────────────────────────────────────────────────────

def _iter_openai_events(messages, cancel, temperature):
    """Yield deltas/counters from OpenAI Chat Completions.

    The usage-only chunk can arrive after the chunk carrying ``finish_reason``;
    the loop runs to exhaustion rather than breaking on the first finish
    condition.
    """
    client = _openai_client()
    kwargs = _openai_request_kwargs(
        messages, temperature=temperature, num_predict=LLM_MAX_TOKENS,
        stream=True,
    )
    stream = client.chat.completions.create(**kwargs)
    usage = None
    any_piece = False
    try:
        for chunk in stream:
            if cancel is not None and cancel.is_set():
                yield ("cancelled", None)
                return
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            for choice in (getattr(chunk, "choices", None) or []):
                delta = getattr(choice, "delta", None)
                piece = getattr(delta, "content", None) if delta else None
                if isinstance(piece, str) and piece:
                    any_piece = True
                    yield ("delta", piece)
        if not any_piece:
            raise GenerationFailed("empty generation stream")
        yield ("counters", EngineCounters(
            prompt_eval_count=(
                getattr(usage, "prompt_tokens", None) if usage else None
            ),
            eval_count=(
                getattr(usage, "completion_tokens", None) if usage else None
            ),
            prompt_eval_duration_ns=None,
            eval_duration_ns=None,
        ))
    finally:
        try:
            stream.close()
        except Exception:                                # noqa: BLE001
            pass


async def generate_stream(
    prompt: str,
    *,
    model: str | None = None,
    num_ctx: int | None = None,
    temperature: float | None = None,
    cancel: "asyncio.Event | None" = None,
    system: str | None = None,
) -> AsyncIterator[str | EngineCounters]:
    """Yield ``str`` deltas, then exactly one ``EngineCounters``.

    A blocking OpenAI stream runs in a worker thread; deltas cross to the event
    loop through ``loop.call_soon_threadsafe`` (the self-pipe wakeup is the
    fix documented in the original implementation). Raises
    ``GenerationFailed`` on an error/empty stream and ``GenerationCancelled``
    when ``cancel`` is set.
    """
    import asyncio as _asyncio
    import threading as _threading

    provider_name()
    messages = _messages(system, prompt)
    queue: "asyncio.Queue[tuple[str, Any]]" = _asyncio.Queue()
    loop = _asyncio.get_running_loop()

    def emit(item: tuple[str, Any]) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            # Loop closed: the consumer is gone (caller hung up).
            pass

    def _pump() -> None:
        any_piece = False
        try:
            for kind, payload in _iter_openai_events(
                messages, cancel, temperature
            ):
                if kind == "delta":
                    any_piece = True
                    emit(("delta", payload))
                elif kind == "counters":
                    emit(("counters", payload))
                elif kind == "cancelled":
                    emit(("cancelled", None))
                    return
            if not any_piece:
                emit(("error", "empty generation stream"))
                return
        except Exception as exc:                        # noqa: BLE001 — thread boundary
            emit(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            emit(("eof", None))

    _threading.Thread(target=_pump, name="llm-stream", daemon=True).start()

    yielded = False
    while True:
        kind, payload = await queue.get()
        if kind == "delta":
            yielded = True
            yield payload
        elif kind == "counters":
            yield payload
            return
        elif kind == "cancelled":
            raise GenerationCancelled("generation cancelled mid-stream")
        elif kind == "error":
            raise GenerationFailed(payload)
        else:                                            # eof without counters
            if not yielded:
                raise GenerationFailed("empty generation stream")
            return


# ── Health / models ───────────────────────────────────────────────────

def is_ready() -> bool:
    """OpenAI reachability. False (never raises) when the key is empty."""
    try:
        provider_name()
        if not LLM_API_KEY:
            return False
        req = urllib.request.Request(
            f"{LLM_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:                                    # noqa: BLE001
        return False


def list_models() -> list[str]:
    """Model ids offered by the configured OpenAI-compatible endpoint."""
    try:
        req = urllib.request.Request(
            f"{LLM_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [m.get("id", "") for m in data.get("data", [])]
    except Exception:                                    # noqa: BLE001
        return []


def health() -> dict:
    """Human-readable backend status. The key is never rendered."""
    try:
        provider = provider_name()
        error = None
    except ConfigurationError as exc:
        provider = settings.LLM_PROVIDER
        error = str(exc)
    payload = {
        "provider": provider,
        "base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
        "ready": is_ready() if error is None else False,
        "model_count": len(list_models()) if error is None else 0,
        "api_key_configured": bool(LLM_API_KEY),
        "context_mode": settings.CONTEXT_MODE,
    }
    if error:
        payload["error"] = error
    return payload


# ── LangChain chat model (Streamlit / CLI) ────────────────────────────

def get_chat_model(model=None, temperature=None, num_ctx=None):
    """OpenAI-backed LangChain chat model.

    ``model`` from the chain sites is an Ollama-era tag and is ignored;
    ``LLM_MODEL`` is the single source of truth.
    """
    provider_name()
    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "api_key": LLM_API_KEY,
        "timeout": LLM_TIMEOUT_S,
        "max_retries": 0,
    }
    # Do not send a temperature unless explicitly requested. ChatOpenAI's
    # default is None (omitted); passing 0.0 is what caused the live Streamlit
    # 400 on a reasoning model that only accepts the provider default.
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)
