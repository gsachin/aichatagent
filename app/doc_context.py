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

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.context_bundle import ContextBundle

logger = logging.getLogger("doc_context")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Tokenizer used only for an estimate. GPT-6's tokenizer may differ; the
#: configured ``DOC_MAX_TOKENS`` leaves headroom under the model's input limit.
_TOKEN_ENCODING = "o200k_base"

_lock = threading.Lock()
_cache: dict | None = None
_manifest_cache: dict | None = None


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


def _manifest_path() -> Path | None:
    raw = (settings.CONTEXT_MANIFEST_PATH or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _read_manifest() -> dict:
    """Read the sidecar IDs for the current prompt, cached by path/mtime/size."""
    global _manifest_cache
    path = _manifest_path()
    if path is None:
        return {}
    try:
        stat = path.stat()
    except OSError:
        return {}
    identity = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _manifest_cache is not None and _manifest_cache["identity"] == identity:
            return _manifest_cache["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    with _lock:
        _manifest_cache = {"identity": identity, "data": data}
    return data


def document_context() -> str:
    """The full document text for the current turn ("" when none is readable)."""
    text, _ = _get()
    return text


def document_bundle() -> ContextBundle:
    """The text and provenance for THIS turn.

    The text is exactly what ``document_context()`` returns. The sidecar
    manifest, when present, adds the fact/node IDs and source URLs that the
    guard and logs can carry without re-reading a different document.
    """
    text, _ = _get()
    manifest = _read_manifest()
    node_ids = _strings(manifest.get("node_ids"))
    expected = {path.stem for path in _paths()}
    if manifest and expected and set(node_ids) != expected:
        # A stale sidecar must not attach another prompt's facts/sources to
        # this text. Keep the text; drop the provenance.
        logger.warning(
            "context manifest node_ids %s do not match DOC_PATHS %s; "
            "dropping bundle provenance",
            sorted(node_ids),
            sorted(expected),
        )
        return ContextBundle(text=text)
    valid_until = manifest.get("valid_until")
    return ContextBundle(
        text=text,
        fact_ids=_strings(manifest.get("fact_ids")),
        node_ids=node_ids,
        notice_ids=_strings(manifest.get("notice_ids")),
        source_urls=_strings(manifest.get("source_urls")),
        valid_until=str(valid_until) if valid_until else None,
    )


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
    global _cache, _manifest_cache
    with _lock:
        _cache = None
        _manifest_cache = None
