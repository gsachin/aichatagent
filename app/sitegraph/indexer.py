"""Build/rebuild the chunk index from stored pages and documents."""

from __future__ import annotations

from app.sitegraph.chunker import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    chunks_for_document,
    chunks_for_page,
)
from app.sitegraph.store import GraphStore

CHUNKER_VERSION = "1"


def index_version_for(graph_hash: str) -> str:
    return f"{graph_hash or 'unknown'}:chunker-{CHUNKER_VERSION}"


def _is_canonical(row: dict) -> bool:
    """Only the canonical node of a duplicate page owns retrievable chunks.

    Chunk search already joins on the same rule; indexing a non-canonical
    duplicate would create passages no query can reach.
    """
    canonical = str(row.get("canonical_id") or "")
    return not canonical or canonical == str(row.get("node_id") or "")


def index_page_chunks(
    store: GraphStore,
    row: dict,
    *,
    index_version: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    created_at: str,
) -> int:
    if not _is_canonical(row):
        return 0
    chunks = chunks_for_page(
        row,
        index_version=index_version,
        max_chars=max_chars,
        overlap=overlap,
    )
    return store.replace_node_chunks(
        str(row["node_id"]),
        [chunk.to_dict() for chunk in chunks],
        created_at=created_at,
    )


def index_document_chunks(
    store: GraphStore,
    row: dict,
    *,
    index_version: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    created_at: str,
) -> int:
    if not _is_canonical(row):
        return 0
    chunks = chunks_for_document(
        row,
        index_version=index_version,
        max_chars=max_chars,
        overlap=overlap,
    )
    return store.replace_node_chunks(
        str(row["node_id"]),
        [chunk.to_dict() for chunk in chunks],
        created_at=created_at,
    )


def index_all_chunks(
    store: GraphStore,
    *,
    index_version: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    created_at: str,
    limit: int | None = None,
) -> dict:
    pages = [row for row in store.page_version_rows() if _is_canonical(row)]
    documents = [
        row for row in store.document_rows() if _is_canonical(row)
    ]
    if limit is not None:
        pages = pages[: max(0, int(limit))]
        documents = documents[: max(0, int(limit))]
    if limit is None:
        # A full rebuild is authoritative: clear the chunk corpus once and
        # insert every current page/document passage. This also removes
        # chunks for documents that stopped being `done`.
        page_rows: list[dict] = []
        document_rows: list[dict] = []
        for row in pages:
            page_rows.extend(
                chunk.to_dict()
                for chunk in chunks_for_page(
                    row,
                    index_version=index_version,
                    max_chars=max_chars,
                    overlap=overlap,
                )
            )
        for row in documents:
            document_rows.extend(
                chunk.to_dict()
                for chunk in chunks_for_document(
                    row,
                    index_version=index_version,
                    max_chars=max_chars,
                    overlap=overlap,
                )
            )
        replaced = store.replace_all_chunks(
            page_rows + document_rows, created_at=created_at
        )
        return {
            "pages": len(pages),
            "documents": len(documents),
            "page_chunks": len(page_rows),
            "document_chunks": len(document_rows),
            "pruned": replaced["previous"],
            "chunks": replaced["chunks"],
        }

    page_chunks = 0
    document_chunks = 0
    for row in pages:
        page_chunks += index_page_chunks(
            store,
            row,
            index_version=index_version,
            max_chars=max_chars,
            overlap=overlap,
            created_at=created_at,
        )
    for row in documents:
        document_chunks += index_document_chunks(
            store,
            row,
            index_version=index_version,
            max_chars=max_chars,
            overlap=overlap,
            created_at=created_at,
        )
    return {
        "pages": len(pages),
        "documents": len(documents),
        "page_chunks": page_chunks,
        "document_chunks": document_chunks,
        "pruned": 0,
        "chunks": store.chunk_count(),
    }


__all__ = [
    "CHUNKER_VERSION",
    "index_all_chunks",
    "index_document_chunks",
    "index_page_chunks",
    "index_version_for",
]
