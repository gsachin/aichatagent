"""Embedding provider for chunk-level semantic retrieval.

Off by default. ``EMBEDDING_PROVIDER=openai`` uses the same approved OpenAI
boundary as generation. Vectors are stored as float32 in SQLite; the corpus
is small enough for cosine search, and a vector extension can replace it
later without changing the interface.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.config import settings

logger = logging.getLogger("sitegraph.embeddings")


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout_s: float = 20.0,
    ) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self.model,
            input=texts,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


def get_embedding_provider() -> EmbeddingProvider | None:
    provider = (settings.EMBEDDING_PROVIDER or "off").strip().lower()
    if provider in ("", "off", "none"):
        return None
    if provider == "openai":
        if not settings.LLM_API_KEY:
            logger.warning(
                "EMBEDDING_PROVIDER=openai but LLM_API_KEY is empty; "
                "semantic retrieval is disabled"
            )
            return None
        return OpenAIEmbeddingProvider(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout_s=settings.EMBEDDING_TIMEOUT_S,
        )
    logger.warning(
        "EMBEDDING_PROVIDER=%r is not supported; semantic retrieval is off",
        provider,
    )
    return None


__all__ = [
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "get_embedding_provider",
]
