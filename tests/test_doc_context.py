"""Full-document context tests (no embeddings, no network)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from app import doc_context


def _reset(override_settings, **kwargs):
    override_settings(**kwargs)
    doc_context.clear_cache()


def test_current_kb_loads(override_settings):
    _reset(override_settings, CONTEXT_MODE="doc")
    text = doc_context.document_context()
    stats = doc_context.document_stats()
    assert "Meridian University" in text
    assert stats.tokens > 0
    assert stats.bytes_read > 0
    assert stats.truncated_bytes is False
    assert stats.truncated_tokens is False


def test_byte_cap_truncates(tmp_path: Path, override_settings):
    path = tmp_path / "big.md"
    path.write_text("x" * 100, encoding="utf-8")
    _reset(
        override_settings,
        DOC_PATHS=str(path),
        DOC_MAX_BYTES=10,
        DOC_MAX_TOKENS=1000,
    )
    text = doc_context.document_context()
    stats = doc_context.document_stats()
    assert len(text.encode("utf-8")) <= 10
    assert stats.truncated_bytes is True


def test_token_cap_truncates_at_section(tmp_path: Path, override_settings):
    path = tmp_path / "sections.md"
    path.write_text(
        "# KB\n\n## One\n" + ("alpha " * 50) + "\n## Two\n" + ("beta " * 50),
        encoding="utf-8",
    )
    _reset(
        override_settings,
        DOC_PATHS=str(path),
        DOC_MAX_BYTES=10_000,
        DOC_MAX_TOKENS=20,
    )
    text = doc_context.document_context()
    stats = doc_context.document_stats()
    assert stats.truncated_tokens is True
    assert stats.tokens <= 20 + 5          # approximate boundary
    assert len(text) < len(path.read_text(encoding="utf-8"))


def test_cache_reloads_when_file_changes(tmp_path: Path, override_settings):
    path = tmp_path / "kb.md"
    path.write_text("version one", encoding="utf-8")
    _reset(override_settings, DOC_PATHS=str(path), DOC_MAX_TOKENS=1000)
    assert "version one" in doc_context.document_context()

    path.write_text("version two", encoding="utf-8")
    # Force a distinct mtime_ns even on a coarse filesystem clock.
    future = time.time_ns() + 1_000_000_000
    os.utime(path, ns=(future, future))
    assert "version two" in doc_context.document_context()


def test_missing_document_is_empty(override_settings):
    _reset(
        override_settings,
        DOC_PATHS="does-not-exist-anywhere.md",
        DOC_MAX_BYTES=1000,
        DOC_MAX_TOKENS=1000,
    )
    assert doc_context.document_context() == ""
    stats = doc_context.document_stats()
    assert stats.bytes_read == 0
    assert stats.tokens == 0
