"""
Rebuild the RAG vector store from validated sources (single ingestion writer).

Usage:
    $env:CHROMA_DB_PATH = "chroma_local_db_new"
    python scripts/rebuild_rag_index.py

Behaviour:
    1. Validates every source against EXPECTED_MARKERS / BLOCKED_MARKERS
       (foreign content or missing Meridian identity -> exit 1, nothing written).
    2. Builds into the directory named by CHROMA_DB_PATH.
       Refuses to build into a non-empty directory unless FORCE=1.
    3. Smoke tests retrieval on the fresh build (positive queries + one
       negative canary that must NOT surface blocked content).
    4. Never touches the live store — swapping is an operator step
       (see doc/RAGPIPLINE/Implementation_Plan_Meridian_Update.md §3.8).

Rollback: rename the previous store directory back.
"""

import os
import sys
from pathlib import Path

# The app reads CHROMA_DB_PATH at import time — set it before importing app.rag.
TARGET = Path(os.environ.get(
    "CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db"),
))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("rag_rebuild")

from app.rag import (  # noqa: E402
    BLOCKED_MARKERS,
    SOURCES,
    build_vector_store,
    retrieve_context,
)

SMOKE_QUERIES = [
    "What is the tuition fee for the MBA program?",
    "What are the application deadlines?",
    "What undergraduate programs does Meridian offer?",
    "What is the hostel fee?",
    "How do I apply to Meridian?",
]

# Canary: the question whose answer would surface the contaminated
# pages of the old source PDF (Maryland financial aid / housing).
NEGATIVE_QUERY = "What is the Terrapin Commitment?"


def _target_state() -> str:
    if not TARGET.is_dir():
        return "missing"
    if not any(TARGET.iterdir()):
        return "empty"
    return "non_empty"


def main() -> int:
    logger.info(f"Target store directory: {TARGET}")

    state = _target_state()
    if state == "non_empty" and os.environ.get("FORCE") != "1":
        logger.error(
            f"Target directory {TARGET} is not empty. "
            f"Refusing to build into it (set FORCE=1 to override)."
        )
        return 1

    logger.info(f"Sources: {[str(s['path']) for s in SOURCES]}")
    for s in SOURCES:
        if not s["path"].is_file():
            logger.error(f"Source file missing: {s['path']}")
            return 1

    # 1. Build (validates internally; returns None on validation failure)
    store = build_vector_store(dest_dir=TARGET)
    if store is None:
        logger.error("Build failed (validation or embedding error).")
        return 1

    # 2. Smoke test: positive queries must retrieve context
    logger.info("Running smoke tests...")
    for q in SMOKE_QUERIES:
        ctx = retrieve_context(q)
        if not ctx.strip():
            logger.error(f"SMOKE FAIL: no context for '{q}'")
            return 1
        logger.info(f"  [OK] '{q}' -> {len(ctx)} chars of context")

    # 3. Negative canary: no blocked markers anywhere in the built store
    try:
        import chromadb
        from app.llm_backend import get_embedding_function

        client = chromadb.PersistentClient(path=str(TARGET))
        ef = get_embedding_function()
        collections = client.list_collections()
        first = collections[0]
        coll_name = first if isinstance(first, str) else first.name
        col = client.get_collection(coll_name, embedding_function=ef)
        got = col.get(include=["documents"])
        docs = got.get("documents") or []
        blob = "\n".join(docs).lower()
        hits = [m for m in BLOCKED_MARKERS if m in blob]
        if hits:
            logger.error(f"SMOKE FAIL: blocked markers present in store: {hits}")
            return 1
        logger.info(f"  [OK] store contains {len(docs)} documents, zero blocked markers")

        neg_ctx = retrieve_context(NEGATIVE_QUERY)
        for m in BLOCKED_MARKERS:
            if m in neg_ctx.lower():
                logger.error(f"SMOKE FAIL: negative canary retrieved blocked marker '{m}'")
                return 1
        logger.info(f"  [OK] negative canary clean for '{NEGATIVE_QUERY}'")
    except Exception:
        logger.exception("Store-wide marker scan failed")
        return 1

    logger.info(f"Rebuild complete at {TARGET}. Swap with the live directory when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
