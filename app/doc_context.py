"""Document context — full-document prompting without embeddings.

The demo passes the university knowledge base to the model in full instead of
retrieving chunks from Chroma. The current KB is ~15 KB (~3.2k tokens), so the
whole document fits comfortably in the voice prompt and removes an embedding
round trip from every turn.

This module is deliberately independent of ``app.llm_backend`` / ``app.rag`` so
it can be imported by the retrieval dispatcher and by tests without loading an
LLM provider. It reads files once, caches by ``(path, mtime_ns, size)``, and
enforces the two operator-set bounds:

* ``DOC_MAX_BYTES`` — never read more than the configured byte budget.
* ``DOC_MAX_TOKENS`` — estimate with ``tiktoken`` (the installed tokenizer);
  truncate at a markdown section boundary and log loudly rather than silently
  sending an over-budget prompt.

The truncation is a guard for future 2 MB documents, not a path the current
15 KB KB ever takes.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger("doc_context")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Tokenizer used only for an estimate. GPT-6's tokenizer may differ; the
#: configured ``DOC_MAX_TOKENS`` leaves headroom under the model's input limit.
_TOKEN_ENCODING = "o200k_base"

_lock = threading.Lock()
_cache: dict | None = None


@dataclass(frozen=True)
class DocumentStats:
    paths: tuple[str, ...]
    bytes_read: int
    chars: int
    tokens: int
    truncated_bytes: bool
    truncated_tokens: bool
    mtimes: tuple[tuple[str, int], ...]


def _resolve(path: str) -> Path:
    p = Path(path.strip())
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _paths() -> list[Path]:
    raw = (settings.DOC_PATHS or "").strip()
    if not raw:
        return []
    return [_resolve(part) for part in raw.split(",") if part.strip()]


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding(_TOKEN_ENCODING).encode(text))
    except Exception:                                    # noqa: BLE001
        # Conservative fallback: 4 characters per token.
        return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Trim to a markdown section boundary at or below ``max_tokens``.

    A hard character cut can split a paragraph mid-sentence; a section cut
    keeps the remaining prompt readable. If no section boundary fits, fall
    back to a character estimate and log it.
    """
    if max_tokens <= 0 or _count_tokens(text) <= max_tokens:
        return text, False

    sections = text.split("\n## ")
    if len(sections) > 1:
        kept = [sections[0]]
        for section in sections[1:]:
            candidate = "\n## ".join(kept + [section])
            if _count_tokens(candidate) > max_tokens:
                break
            kept.append(section)
        if len(kept) > 1:
            return "\n## ".join(kept), True

    # No usable section boundary: approximate by character count, then trim
    # back to the last paragraph break so the prompt does not end mid-word.
    approx_chars = max(1, int(len(text) * (max_tokens / max(1, _count_tokens(text)))))
    cut = text[:approx_chars]
    para = cut.rfind("\n\n")
    if para > 0:
        cut = cut[:para]
    return cut, True


def _load() -> tuple[str, DocumentStats]:
    paths = _paths()
    max_bytes = max(0, int(settings.DOC_MAX_BYTES))
    max_tokens = max(0, int(settings.DOC_MAX_TOKENS))

    pieces: list[str] = []
    mtimes: list[tuple[str, int]] = []
    total_bytes = 0
    truncated_bytes = False

    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            logger.error("document context: missing/unreadable file %s", path)
            continue
        mtimes.append((str(path), stat.st_mtime_ns))

        remaining = max_bytes - total_bytes
        if remaining <= 0:
            truncated_bytes = True
            break
        try:
            # Read at most one byte over the remaining budget so the exact
            # overflow is detectable without reading a large file twice.
            with path.open("rb") as fh:
                raw = fh.read(remaining + 1)
        except OSError:
            logger.exception("document context: failed reading %s", path)
            continue
        if len(raw) > remaining:
            raw = raw[:remaining]
            truncated_bytes = True
        total_bytes += len(raw)
        pieces.append(raw.decode("utf-8", errors="replace"))

    text = "\n\n".join(piece.strip() for piece in pieces if piece.strip())
    if truncated_bytes:
        logger.warning(
            "document context: DOC_MAX_BYTES=%d reached; prompt was truncated. "
            "Increase DOC_MAX_BYTES or the model input budget deliberately.",
            max_bytes,
        )

    tokens = _count_tokens(text) if text else 0
    truncated_tokens = False
    if text and max_tokens and tokens > max_tokens:
        text, truncated_tokens = _truncate_to_tokens(text, max_tokens)
        tokens = _count_tokens(text)
        logger.warning(
            "document context: DOC_MAX_TOKENS=%d reached (%d tokens); "
            "truncated at a section boundary. This is the 2 MB guard path, "
            "not the current 15 KB KB.",
            max_tokens,
            tokens,
        )

    stats = DocumentStats(
        paths=tuple(str(p) for p in paths),
        bytes_read=total_bytes,
        chars=len(text),
        tokens=tokens,
        truncated_bytes=truncated_bytes,
        truncated_tokens=truncated_tokens,
        mtimes=tuple(mtimes),
    )
    return text, stats


def _current_identity() -> tuple:
    id_parts = []
    for path in _paths():
        try:
            stat = path.stat()
            id_parts.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            id_parts.append((str(path), None, None))
    return (
        tuple(id_parts),
        int(settings.DOC_MAX_BYTES),
        int(settings.DOC_MAX_TOKENS),
    )


def _get() -> tuple[str, DocumentStats]:
    global _cache
    identity = _current_identity()
    with _lock:
        if _cache is not None and _cache["identity"] == identity:
            return _cache["text"], _cache["stats"]
    text, stats = _load()
    with _lock:
        _cache = {"identity": identity, "text": text, "stats": stats}
    return text, stats


def document_context() -> str:
    """The full document text for the current turn ("" when none is readable)."""
    text, _ = _get()
    return text


def document_stats() -> DocumentStats:
    """Byte/token provenance for logs, health and the benchmark tool."""
    _, stats = _get()
    return stats


def source_label() -> str:
    """A short, log-safe description of the configured sources."""
    paths = _paths()
    if not paths:
        return "(no DOC_PATHS configured)"
    return ", ".join(p.name for p in paths)


def clear_cache() -> None:
    """Tests and the benchmark tool use this after changing the document."""
    global _cache
    with _lock:
        _cache = None
