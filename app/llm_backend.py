"""
LLM Backend Abstraction — Ollama (Windows/Linux) or Apple MLX (macOS)
======================================================================

Single runtime-selected backend for LLM chat and RAG embeddings.

Provider selection:
    - `LLM_PROVIDER=ollama`  -> Ollama everywhere (explicit override)
    - `LLM_PROVIDER=mlx`     -> MLX everywhere (explicit override)
    - `LLM_PROVIDER=auto`    -> MLX on Apple Silicon (Darwin + arm64),
                                Ollama elsewhere (incl. Docker/Linux).

Ollama path: byte-for-byte compatible with the previous direct calls
(`ollama.chat` + `/api/tags` model discovery + OllamaEmbeddings).

MLX path: talks to `mlx_lm.server` (OpenAI-compatible HTTP API) at
`MLX_BASE_URL`. The official server has no `/v1/embeddings`, so Mac
embeddings come from sentence-transformers (`MLX_EMBED_MODEL`, same
768-dim nomic family as Ollama's `EMBED_MODEL`).

All heavy imports are function-local so Windows/Linux/Docker can import
this module without mlx-lm / sentence-transformers installed.
"""

import json
import logging
import os
import platform as _platform
import re
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any, AsyncIterator

# Bypass AppLocker-style DLL blocks — same class of issue as hf_xet.dll
os.environ.setdefault("HF_HUB_ENABLE_HF_XET", "0")

logger = logging.getLogger("llm_backend")

# ── Ollama config (unchanged semantics) ────────────────────────────────

#: Default host is the literal IPv4, NOT "localhost". On Windows "localhost"
#: resolves to ::1 first and Ollama binds IPv4 only, so the refused IPv6
#: connect burns ~2,066 ms in SYN retransmits before Python falls back --
#: paid on every model-resolution and embedding call. 127.0.0.1 skips the
#: doomed attempt (~15 ms). Class A: same server, same bytes.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
# Single source of truth for the default context window (env: OLLAMA_NUM_CTX).
# 8192 — the production voice system prompt (~3.5k tokens) plus RAG context
# needs this much; scripts/predeploy.py sizes it per machine.
DEFAULT_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
OLLAMA_NUM_CTX = DEFAULT_NUM_CTX  # backward-compat alias
OLLAMA_TEMPERATURE = os.environ.get("OLLAMA_TEMPERATURE", "")  # "" = ollama default
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

#: US-004 (streaming LLM, PO build-approved 2026-09-19): the generation is
#: consumed as token deltas and cut into clauses, so TTS can start on the
#: first clause while the model still decodes. Behind a flag; the batch path
#: (`_chat_ollama` non-streamed) is retained unchanged as the BRD-15 revert.
LLM_STREAM = os.environ.get("LLM_STREAM", "0").strip().lower() in (
    "1", "true", "yes", "on")

# ── MLX config (macOS Apple Silicon) ───────────────────────────────────

MLX_BASE_URL = os.environ.get("MLX_BASE_URL", "http://127.0.0.1:1234")
MLX_MODEL = os.environ.get("MLX_MODEL", "mlx-community/Qwen2.5-14B-Instruct-4bit")
MLX_PORT = int(os.environ.get("MLX_PORT", "1234"))
MLX_EMBED_MODEL = os.environ.get("MLX_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
# mlx_lm.server has no context-size flag — max_tokens is the only output
# window control on the MLX path.
MLX_MAX_TOKENS = int(os.environ.get("MLX_MAX_TOKENS", "2048"))

_provider: str | None = None


def provider_name() -> str:
    """Return the active backend: 'mlx' or 'ollama'."""
    global _provider
    if _provider is None:
        explicit = os.environ.get("LLM_PROVIDER", "auto").strip().lower()
        if explicit in ("mlx", "ollama"):
            _provider = explicit
        else:
            _provider = (
                "mlx"
                if sys.platform == "darwin" and _platform.machine() in ("arm64", "aarch64")
                else "ollama"
            )
        logger.info(f"LLM backend: {_provider}")
    return _provider


# ── Chat ────────────────────────────────────────────────────────────────

def _max_tokens(num_ctx: int) -> int:
    """
    Map an Ollama-style num_ctx to an MLX max_tokens.

    mlx_lm.server defaults max_tokens to 512 (would truncate RAG answers)
    and caps its default context at 8192 — keep answers within a sane
    window while never going below 512. Upper bound via MLX_MAX_TOKENS.
    """
    return min(max(int(num_ctx), 512), MLX_MAX_TOKENS)


def default_model(preferred=None) -> list[str]:
    """
    Ordered model-preference list for the active backend, with the
    env-configured OLLAMA_MODEL first. Call sites pass their historical
    fallback tags; the env value (set by scripts/predeploy.py) wins.
    """
    prefs = list(preferred or [])
    return [OLLAMA_MODEL] + [p for p in prefs if p != OLLAMA_MODEL]


def small_task_num_ctx() -> int:
    """
    Context window for small utility LLM calls (intent detection, lead
    extraction, sentiment).

    Defaults to DEFAULT_NUM_CTX -- the SAME value the serving path uses.
    Ollama keeps one runner per model and tears it down whenever a request
    arrives with a different num_ctx, paying a full cold reload (~6-11 s
    measured on this box) on the next voice turn. Call sites having their
    own historical values (512/1024/2048/4096) meant five context sizes
    against one model, so the reload was paid constantly.

    Agreement is therefore the only safe default, and it is enforced in
    code rather than by a setting: SMALL_TASK_NUM_CTX is an escape hatch
    for a deliberate, measured divergence -- never a required key. It also
    means predeploy re-sizing OLLAMA_NUM_CTX for a different machine cannot
    silently reintroduce the thrashing.
    """
    return int(os.environ.get("SMALL_TASK_NUM_CTX", str(DEFAULT_NUM_CTX)))


def _chat_mlx(messages, *, model=None, num_ctx=None, temperature=None) -> str:
    """POST /v1/chat/completions on the MLX server (OpenAI-compatible)."""
    import httpx

    # Model names containing "/" are treated as MLX repo ids.
    num_ctx = DEFAULT_NUM_CTX if num_ctx is None else num_ctx
    body_model = model if (model and "/" in model) else MLX_MODEL
    payload = {
        "model": body_model,
        "messages": messages,
        "max_tokens": _max_tokens(num_ctx),
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)

    # Per-call client — never share across threads.
    with httpx.Client(timeout=180) as client:
        resp = client.post(f"{MLX_BASE_URL}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


#: US-006 / REC-11. Ollama resets a model's keep-alive to the SERVER DEFAULT on
#: any request that omits it, so a request that never sends one silently undoes
#: the boot pre-warm on the first turn of the process's life. The pre-warm in
#: start_services.ps1 Step 6 asks for 24h; `_chat_ollama` then asked for nothing
#: and got 5 minutes — which is why a 32,919 ms cold load was measured WITH
#: working pre-warm code in the repository.
#:
#: Residency is therefore held on the SERVING path, not the boot path. This is
#: resolved once, here, and sent on every generation request (TAC-3: zero
#: keep-alive resets).
def _resolve_keep_alive(raw: str):
    """Coerce the configured keep-alive into the type Ollama actually accepts.

    A `.env` value is ALWAYS a string, and Ollama's Go duration parser rejects
    `"-1"` with `time: missing unit in duration "-1"` — a 400 on every request.
    So an integer (including a negative one) must be sent as an int, while a
    duration such as "24h" stays a string. Getting this wrong does not degrade
    residency, it breaks every call.
    """
    s = str(raw).strip()
    try:
        return int(s)
    except ValueError:
        return s


KEEP_ALIVE = _resolve_keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE", "-1"))

#: Set when the installed ollama client rejects the keep_alive kwarg, so the
#: condition is visible rather than silently degrading residency to 5 minutes.
_keep_alive_unsupported = False


def _chat_ollama(messages, *, model=None, preferred=None, num_ctx=None,
                 temperature=None, keep_alive=None, num_predict=None) -> str:
    """ollama.chat with explicit keep-alive so residency survives the first call."""
    import ollama

    if model is None:
        model = pick_model(list(preferred) if preferred else None)
    num_ctx = DEFAULT_NUM_CTX if num_ctx is None else num_ctx
    options = {"num_ctx": int(num_ctx)}
    if temperature is not None:
        options["temperature"] = float(temperature)
    # Tail guard. Generation is otherwise unbounded, so the worst case is set
    # by the model's own appetite rather than by anything we chose -- and on a
    # live call the worst case is the one that matters. None preserves the old
    # behaviour exactly, which is why this is opt-in per call site and NOT a
    # module default: `database.py` asks for structured JSON (lead extraction)
    # and a 192-token ceiling there would truncate the object mid-field.
    if num_predict is not None:
        options["num_predict"] = int(num_predict)
    ka = KEEP_ALIVE if keep_alive is None else keep_alive
    try:
        response = ollama.chat(model=model, messages=messages, options=options,
                               keep_alive=ka)
    except TypeError:
        # Client predates the keep_alive kwarg. Degrade LOUDLY: residency will
        # fall back to the server default and BRD-17 is unmet.
        global _keep_alive_unsupported
        if not _keep_alive_unsupported:
            _keep_alive_unsupported = True
            logger.warning(
                "Ollama client does not accept keep_alive; residency will fall "
                "back to the server default (%s). BRD-17 is NOT met. Upgrade the "
                "ollama client or set OLLAMA_KEEP_ALIVE on the server.",
                os.environ.get("OLLAMA_KEEP_ALIVE", "server default"),
            )
        response = ollama.chat(model=model, messages=messages, options=options)
    # US-001 / REC-12: capture the engine's own counters so prefill and
    # generation are separable from the single llm_sent..llm_done block.
    # This is what makes retrieval_ms derivable in app/perf_trace.py — the
    # draft tracer could not compute the metric BRD-01 names first.
    # note_current is a no-op when no trace is active.
    try:
        from app.perf_trace import note_current

        note_current(
            model_used=model,
            prompt_eval_count=response.get("prompt_eval_count"),
            eval_count=response.get("eval_count"),
            prefill_ms=round(response.get("prompt_eval_duration", 0) / 1e6, 1),
            generation_ms=round(response.get("eval_duration", 0) / 1e6, 1),
            load_ms=round(response.get("load_duration", 0) / 1e6, 1),
            keep_alive=ka,
            # A non-trivial load_duration means the model was NOT resident and
            # had to be read from disk. This is the residency-lapse signal
            # US-006 requires: an eviction that happened anyway is visible on
            # the trace rather than silent.
            residency_lapse=bool(response.get("load_duration", 0) / 1e6 > 500),
        )
    except Exception:
        pass
    return response["message"]["content"]


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
    """
    Single-shot chat completion. Returns the assistant text.

    - messages: list of {"role": ..., "content": ...} dicts.
    - model: explicit model/tag. On MLX, names containing "/" are passed
      through as repo ids; otherwise MLX_MODEL is used.
    - preferred: ordered list of Ollama tags to prefer (Ollama path only).
    - num_ctx: context window (Ollama) / max_tokens ceiling (MLX).
    - temperature: None = provider default (preserves today's behavior).
    - json_mode: reserved for API parity. Ollama call sites historically
      relied on prompt + defensive regex parsing (no format="json"), so
      both paths keep that behavior — no request-level change.
    - num_predict: Ollama ONLY — output-token ceiling. None (the default)
      means unbounded, exactly as before. The MLX path ignores it rather
      than mapping it to max_tokens, because that mapping cannot be tested
      on this box and an untested platform branch is worse than a
      documented gap. Pass it from the VOICE answer path, never from a
      structured-output path.
    """
    if provider_name() == "mlx":
        return _chat_mlx(messages, model=model, num_ctx=num_ctx, temperature=temperature)
    return _chat_ollama(
        messages, model=model, preferred=preferred, num_ctx=num_ctx,
        temperature=temperature, num_predict=num_predict,
    )


# ── Model discovery ────────────────────────────────────────────────────

#: US-004 / TRD-10: /api/tags is resolved ONCE per process. The round trip
#: ran per utterance before (pick_model on every generation); the tag list
#: cannot change under a running stack, and a failed read retries on the
#: next call rather than pinning an empty list forever.
_tags_cache: list[str] | None = None


def _cached_tags() -> list[str]:
    global _tags_cache
    if _tags_cache is None:
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                _tags_cache = [m.get("name", "")
                               for m in json.loads(resp.read()).get("models", [])]
        except Exception:
            return []
    return _tags_cache


def pick_model(preferred=None) -> str:
    """
    Best available model for the active backend.

    Ollama: exact-match precedence over `preferred`, then any qwen tag,
    then OLLAMA_MODEL (mirrors the old rag._get_available_model logic).
    MLX: always MLX_MODEL (the server serves one model).
    """
    if provider_name() == "mlx":
        return MLX_MODEL

    prefs = list(preferred or [])
    models = _cached_tags()
    if models:
        for preference in prefs:
            if preference in models:
                return preference

        qwen_models = [m for m in models if "qwen" in m.lower()]
        if qwen_models:
            return qwen_models[0]

    return OLLAMA_MODEL


def list_models() -> list[str]:
    """Model names served by the active backend (empty list on failure)."""
    try:
        if provider_name() == "mlx":
            req = urllib.request.Request(f"{MLX_BASE_URL}/v1/models")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            return [m.get("id", "") for m in data.get("data", [])]
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


# ── US-004: streamed generation, clause cutting, per-process model cache ──


@dataclass(frozen=True)
class EngineCounters:
    """The engine's own account of a generation (UC-07 E1: absent = None,
    never a fabricated 0)."""

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
    """The stream produced nothing usable; MOD-01 speaks the fallback."""


class GenerationCancelled(Exception):
    """The caller hung up (or the handler died) mid-generation."""


class ClauseCutter:
    """Accumulates token deltas and emits complete clauses.

    A clause ends at sentence punctuation followed by whitespace and a
    capital, and never right after a digit -- the SAME boundary rule
    `_split_sentences` uses in voice_handler, so a clause handed to the
    per-sentence TTS stream is never re-split. Clauses shorter than
    MIN_CLAUSE_CHARS are re-merged into the next one rather than spoken
    alone; a trailing partial clause is dropped by flush() unless it meets
    the minimum (a half-sentence is never spoken).
    """

    MIN_CLAUSE_CHARS = 12
    _BOUNDARY = re.compile(r"(?<![0-9][.!?;])(?<=[.!?;])\s+(?=[A-Z])")

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[Clause]:
        self._buf += delta
        out: list[Clause] = []
        guard = 0
        while guard < 10_000:
            guard += 1
            m = self._BOUNDARY.search(self._buf)
            if not m:
                break
            text = self._buf[:m.end()].strip()
            self._buf = self._buf[m.end():]
            if len(text) >= self.MIN_CLAUSE_CHARS:
                out.append(Clause(text=text, is_final=False))
            else:
                # Too short to speak alone: keep it for the next clause.
                # CRITICAL: re-join WITHOUT re-adding the whitespace the
                # boundary consumed -- re-adding it re-creates the same match,
                # and `search` then finds the SAME boundary forever. That
                # infinite loop froze the event loop live (2026-09-20, run
                # T043413Z: a short first clause like "Sure. " hung the
                # whole process; py-spy caught MainThread inside feed()).
                # Without the space the boundary regex cannot match there
                # again, and the fragment rides into the next clause.
                self._buf = text + self._buf
        if guard >= 10_000:
            # Never reached with the merge above; the guard turns a future
            # non-progress bug into a loud defect instead of a hung loop.
            logger.error("ClauseCutter: guard tripped; dropping buffer")
            self._buf = ""
        return out

    def flush(self) -> Clause | None:
        text = self._buf.strip()
        if len(text) >= self.MIN_CLAUSE_CHARS:
            self._buf = ""
            return Clause(text=text, is_final=True)
        return None


async def generate_stream(
    prompt: str,
    *,
    model: str | None = None,
    num_ctx: int | None = None,
    temperature: float | None = None,
    cancel: "asyncio.Event | None" = None,
) -> AsyncIterator[str | EngineCounters]:
    """US-004: yield str deltas from a streamed generation, then exactly one
    `EngineCounters` as the final item.

    The blocking `ollama.chat(stream=True)` iterator runs in a worker thread;
    deltas cross to the event loop through a queue, so the loop never blocks
    on a decode step. Raises `GenerationFailed` when the stream errors or
    produces nothing, and `GenerationCancelled` when `cancel` is set
    mid-stream (the underlying stream is abandoned).
    """
    import asyncio as _asyncio
    import threading as _threading

    import ollama

    if model is None:
        model = pick_model(default_model(("qwen2.5:7b-instruct-q3_K_M",)))
    options: dict[str, Any] = {"num_ctx": int(num_ctx or DEFAULT_NUM_CTX)}
    if temperature is not None:
        options["temperature"] = float(temperature)

    queue: "asyncio.Queue[tuple[str, Any]]" = _asyncio.Queue()

    def _pump() -> None:
        counters: dict[str, Any] = {}
        any_piece = False
        try:
            stream = ollama.chat(
                model=model, messages=[{"role": "user", "content": prompt}],
                options=options, keep_alive=KEEP_ALIVE, stream=True,
            )
            for chunk in stream:
                if cancel is not None and cancel.is_set():
                    queue.put_nowait(("cancelled", None))
                    return
                piece = str((chunk.get("message") or {}).get("content", ""))
                for key in ("prompt_eval_count", "eval_count",
                            "prompt_eval_duration", "eval_duration"):
                    if chunk.get(key) is not None:
                        counters[key] = chunk.get(key)
                if piece:
                    any_piece = True
                    queue.put_nowait(("delta", piece))
                if chunk.get("done"):
                    break
            if not any_piece:
                # An empty generation must never reach synthesis (TRD-04).
                queue.put_nowait(("error", "empty generation stream"))
                return
            queue.put_nowait(("counters", EngineCounters(
                prompt_eval_count=counters.get("prompt_eval_count"),
                eval_count=counters.get("eval_count"),
                prompt_eval_duration_ns=counters.get("prompt_eval_duration"),
                eval_duration_ns=counters.get("eval_duration"),
            )))
        except Exception as exc:                        # noqa: BLE001 — thread boundary
            queue.put_nowait(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            queue.put_nowait(("eof", None))

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
            if yielded:
                # Partial answer exists; the caller decides (flush() drops the
                # trailing half-sentence). Signal by raising -- partial text is
                # already with the cutter.
                raise GenerationFailed(payload)
            raise GenerationFailed(payload)
        else:                                            # eof without counters
            if not yielded:
                raise GenerationFailed("empty generation stream")
            return


def is_ready() -> bool:
    """Quick reachability probe of the active backend's API."""
    try:
        if provider_name() == "mlx":
            req = urllib.request.Request(f"{MLX_BASE_URL}/v1/models")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def health() -> dict:
    """Human-readable backend status (provider, url, model, readiness)."""
    return {
        "provider": provider_name(),
        "base_url": MLX_BASE_URL if provider_name() == "mlx" else OLLAMA_BASE_URL,
        "model": MLX_MODEL if provider_name() == "mlx" else OLLAMA_MODEL,
        "ready": is_ready(),
        "model_count": len(list_models()),
    }


# ── Embeddings ─────────────────────────────────────────────────────────

_st_embed_model = None
_st_embedding_function = None


class _NomicEmbeddingModel:
    """
    Minimal nomic-embed-text-v1.5 wrapper via transformers (macOS MLX path).

    Uses the model repo's documented usage: mean pooling over token
    embeddings followed by L2 normalization. Avoids sentence-transformers'
    AutoProcessor path, which breaks on this model with transformers 5.x
    ("Unrecognized processing class").
    """

    def __init__(self):
        from transformers import AutoModel, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(MLX_EMBED_MODEL)
        self._model = AutoModel.from_pretrained(MLX_EMBED_MODEL, trust_remote_code=True)
        self._model.eval()

    def encode(self, texts, normalize_embeddings=True):
        import torch

        encoded = self._tokenizer(
            list(texts), padding=True, truncation=True, return_tensors="pt"
        )
        with torch.no_grad():
            outputs = self._model(**encoded)
        token_embeddings = outputs[0]  # [batch, seq, dim]
        mask = encoded["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
        summed = (token_embeddings * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-9)
        mean_pooled = summed / counts
        if normalize_embeddings:
            mean_pooled = torch.nn.functional.normalize(mean_pooled, p=2, dim=1)
        return mean_pooled.numpy()


def _get_st_embedding_model():
    """Lazy singleton local embedding model (macOS MLX path only)."""
    global _st_embed_model
    if _st_embed_model is None:
        _st_embed_model = _NomicEmbeddingModel()
        logger.info(f"Embedding model loaded: {MLX_EMBED_MODEL}")
    return _st_embed_model


def get_embedding_function():
    """
    chromadb-compatible EmbeddingFunction for the active backend.

    Ollama -> OllamaEmbeddingFunction (exact current behavior).
    MLX    -> local sentence-transformers wrapper (768-dim nomic family,
              dimension-compatible with the persisted Chroma store).
    """
    if provider_name() == "ollama":
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

        # A5 (2026-09-19): chromadb's OllamaEmbeddingFunction posts
        # /api/embeddings WITHOUT keep_alive, so every retrieval reset the
        # embed model to the server's 5-minute default and the next caller
        # paid a cold embed load (~1-3 s on this box, observed in the Ollama
        # server log). The subclass pins the same lease the chat path holds.
        # C3 (2026-09-20): E5 regression fix — the legacy /api/embeddings
        # endpoint takes "prompt" and returns a singular "embedding" key;
        # posting {"input": ...} there returns 200 with {"embedding": []} and
        # the KeyError on ["embeddings"] was swallowed, silently killing the
        # hybrid path (dense-MMR only despite RAG_SEARCH_MODE=hybrid). Post to
        # the modern batch /api/embed instead and read "embeddings".
        class _HeldEmbeddingFunction(OllamaEmbeddingFunction):
            def __call__(self, input):
                import httpx

                base = self._base_url.rstrip("/")
                texts = [input] if isinstance(input, str) else list(input)
                url = base if base.endswith("/api/embed") else f"{base}/api/embed"
                with httpx.Client(timeout=60) as client:
                    resp = client.post(
                        url,
                        json={"model": self.model_name, "input": texts,
                              "keep_alive": KEEP_ALIVE},
                    )
                    resp.raise_for_status()
                    return resp.json()["embeddings"]

        return _HeldEmbeddingFunction(model_name=EMBED_MODEL, url=OLLAMA_BASE_URL)

    global _st_embedding_function
    if _st_embedding_function is None:
        from chromadb.utils.embedding_functions import EmbeddingFunction as _ChromaEF

        class _STEmbeddingFunction(_ChromaEF):
            def __call__(self, inputs):
                model = _get_st_embedding_model()
                if isinstance(inputs, str):
                    inputs = [inputs]
                return model.encode(list(inputs), normalize_embeddings=True).tolist()

        _st_embedding_function = _STEmbeddingFunction()
    return _st_embedding_function


def get_langchain_embeddings():
    """
    LangChain Embeddings-compatible object for the active backend.

    MLX path applies the nomic task prefixes (search_document /
    search_query) and L2-normalizes, matching Ollama's nomic-embed-text.
    """
    if provider_name() == "ollama":
        from langchain_ollama import OllamaEmbeddings

        # A5 (2026-09-19): keep_alive is passed explicitly -- without it every
        # embed request resets the model to the server's 5-minute default and
        # the next retrieval pays a cold embed load.
        return OllamaEmbeddings(model=EMBED_MODEL, keep_alive=KEEP_ALIVE)

    from langchain_core.embeddings import Embeddings

    class _STLangChainEmbeddings(Embeddings):
        def embed_documents(self, texts):
            model = _get_st_embedding_model()
            inputs = [f"search_document: {t}" for t in texts]
            return model.encode(inputs, normalize_embeddings=True).tolist()

        def embed_query(self, text):
            model = _get_st_embedding_model()
            return model.encode(
                [f"search_query: {text}"], normalize_embeddings=True
            )[0].tolist()

    return _STLangChainEmbeddings()


# ── LangChain chat model (chain sites: root app.py, admissions_bot.py) ─

def get_chat_model(model=None, temperature=None, num_ctx=None):
    """
    LangChain chat model for the active backend.

    Ollama -> ChatOllama (exact current construction).
    MLX    -> ChatOpenAI pointed at the local mlx_lm.server.
    """
    num_ctx = DEFAULT_NUM_CTX if num_ctx is None else num_ctx

    if provider_name() == "mlx":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=MLX_MODEL,
            base_url=f"{MLX_BASE_URL}/v1",
            api_key="mlx",  # local server — any non-empty key works
            temperature=temperature if temperature is not None else 0.0,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model or OLLAMA_MODEL,
        temperature=temperature if temperature is not None else 0.0,
        num_ctx=int(num_ctx),
    )
