"""
Shared RAG Module — University Admissions Assistant (Meridian)
=============================================================
Single source of truth for retrieval-augmented generation.
Used by ALL interfaces: Streamlit, WhatsApp/FastAPI, and future integrations.

Meridian pivot (2026-08-14):
- Source: content/meridian/meridian_knowledge_base.md (clean markdown, no UMD/FDU)
- Runtime LOADS the persisted store; only scripts/rebuild_rag_index.py WRITES.
- Validation gate blocks foreign/dummy content before any build.
- Optional (env-gated, OFF by default): hybrid search (dense + BM25/RRF),
  similarity threshold, temperature control, context overflow guard.

Provides:
    get_vector_store()   — load persisted ChromaDB (LangChain, raw fallback)
    build_vector_store() — validated build used only by the rebuild script
    query_rag(question, mode="voice"|"chat")  — full RAG pipeline, returns answer string
    retrieve_context(q)  — MMR (or hybrid) retrieval, returns formatted context
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Bypass AppLocker DLL blocks — same class of issue as hf_xet.dll
os.environ.setdefault("HF_HUB_ENABLE_HF_XET", "0")

# Bypass langsmith/xxhash DLL block triggered by langchain_core imports
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
os.environ.setdefault("LANGCHAIN_API_KEY", "")
os.environ.setdefault("LANGCHAIN_PROJECT", "")

logger = logging.getLogger("rag_module")

# ── Configuration ────────────────────────────────────────────────────

CHROMA_DB_PATH = Path(os.environ.get(
    "CHROMA_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "chroma_local_db"),
))

# Single source of truth for the knowledge base (Meridian).
# Append entries here when new document types are added.
SOURCES = [
    {
        "path": Path(__file__).resolve().parent.parent / "content" / "meridian" / "meridian_knowledge_base.md",
        "doc_type": "meridian_profile",
    },
]

OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")
# Single source of truth: app.llm_backend.DEFAULT_NUM_CTX (env: OLLAMA_NUM_CTX).
# 8192 default — voice calls run the full production voice system prompt
# (~3.5k tokens) plus RAG context; scripts/predeploy.py sizes it per machine.
from app.llm_backend import DEFAULT_NUM_CTX as OLLAMA_NUM_CTX  # noqa: E402
OLLAMA_TEMPERATURE = os.environ.get("OLLAMA_TEMPERATURE", "")  # "" = ollama default
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Chunking (markdown-aware)
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "600"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "90"))

# Retrieval settings
MMR_FETCH_K = int(os.environ.get("RAG_FETCH_K", "20"))
MMR_K = int(os.environ.get("RAG_TOP_K", "5"))

# Optional features — all default to current behavior (OFF)
RAG_SEARCH_MODE = os.environ.get("RAG_SEARCH_MODE", "mmr")  # "mmr" | "hybrid"
RAG_SIMILARITY_THRESHOLD = float(os.environ.get("RAG_SIMILARITY_THRESHOLD", "0.0"))  # cosine distance; 0.0 = disabled
RAG_MAX_CONTEXT_CHARS = int(os.environ.get("RAG_MAX_CONTEXT_CHARS", "0"))  # 0 = off

# ── Ingestion validation gate ─────────────────────────────────────────

EXPECTED_MARKERS = ["meridian university"]
# Any of these in a source → refuse to build (UMD/FDU/Maryland contamination).
# Includes OCR variants ("maradian") that appear in the contaminated PDF.
BLOCKED_MARKERS = [
    "university of maryland", "terrapin", "fafsa", "mddcs", "mhec",
    "fairleigh dickinson", "umd", "fdu", "maradian", "marradian",
]


def validate_source(text: str) -> list[str]:
    """
    Check a candidate source before embedding.

    Returns a list of issues; empty list = source is clean.
    """
    low = text.lower()
    issues = []
    if not any(m in low for m in EXPECTED_MARKERS):
        issues.append(f"expected institution not found: {EXPECTED_MARKERS}")
    for m in BLOCKED_MARKERS:
        if m in low:
            issues.append(f"foreign content marker found: '{m}'")
    return issues


# ── System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a helpful University Admissions Advisor for Meridian University. "
    "Answer questions using the provided university profile context.\n\n"
    "RULES:\n"
    "1. Use facts from the context. When the context has relevant data, "
    "present it clearly — use Markdown for tables and figures.\n"
    "2. NEVER invent numbers, fees, URLs, or program names. "
    "Only cite dollar amounts and figures that appear in the context.\n"
    "3. When you state fees, dates, or figures, cite the section they come "
    "from, e.g. (§ Fees Structure).\n"
    "4. If the context truly has NO relevant data for a question, "
    "say: \"I don't have that specific information in the university profile.\"\n"
    "5. Be concise and conversational.\n\n"
    "Context:\n{context}"
)

# ── Vector Store — load (runtime) vs build (rebuild script only) ──────

_vector_store = None  # Module-level cache


def get_vector_store():
    """
    Load the persisted ChromaDB vector store. Never rebuilds in place —
    ingestion is owned exclusively by scripts/rebuild_rag_index.py.

    Cached at module level — loaded once per process.
    """
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    if not CHROMA_DB_PATH.is_dir() or not any(CHROMA_DB_PATH.iterdir()):
        logger.warning(
            f"ChromaDB not found at {CHROMA_DB_PATH} — run scripts/rebuild_rag_index.py"
        )
        return None

    try:
        _vector_store = _load_with_langchain()
        if _vector_store is not None:
            return _vector_store
    except Exception as e:
        logger.warning(f"LangChain vector store unavailable ({e}), using raw ChromaDB")

    # Fallback: raw ChromaDB (no LangChain dependency)
    _vector_store = _build_raw_chromadb()
    return _vector_store


def _load_with_langchain():
    """Load the persisted collection through LangChain (MMR-capable)."""
    from langchain_community.vectorstores import Chroma
    from app.llm_backend import get_langchain_embeddings

    return Chroma(
        persist_directory=str(CHROMA_DB_PATH),
        embedding_function=get_langchain_embeddings(),
    )


def _load_markdown_sections(path: Path) -> list:
    """
    Split a markdown knowledge file into Documents, one per '## ' section.
    The heading is attached as 'section' metadata (propagates to chunks).
    """
    from langchain_core.documents import Document

    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)
    docs = []
    for part in parts[1:]:  # parts[0] is front-matter before the first heading
        if not part.strip():
            continue
        lines = part.split("\n", 1)
        heading = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        if body.strip():
            docs.append(Document(page_content=body.strip(), metadata={"section": heading}))
    return docs


def build_vector_store(dest_dir=None):
    """
    Validated build of the vector store into a FRESH directory.

    Used only by scripts/rebuild_rag_index.py. Returns the Chroma store
    on success, or None if validation/building failed (never raises).
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import Chroma
    from app.llm_backend import get_langchain_embeddings

    dest = Path(dest_dir) if dest_dir else CHROMA_DB_PATH

    all_chunks = []
    for src in SOURCES:
        path = src["path"]
        if not path.is_file():
            logger.error(f"Source not found: {path}")
            return None

        text = path.read_text(encoding="utf-8")
        issues = validate_source(text)
        if issues:
            logger.error(f"VALIDATION FAILED for {path.name}: {issues}")
            return None

        docs = _load_markdown_sections(path)
        for d in docs:
            d.metadata.update({
                "source_file": path.name,
                "doc_type": src["doc_type"],
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            })

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n### ", "\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(docs)
        logger.info(f"{path.name}: {len(docs)} sections -> {len(chunks)} chunks")
        all_chunks.extend(chunks)

    if not all_chunks:
        logger.error("No chunks produced — build aborted")
        return None

    logger.info(f"Building {len(all_chunks)} chunks into {dest} (cosine space)")
    embeddings = get_langchain_embeddings()
    return Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        persist_directory=str(dest),
        collection_metadata={"hnsw:space": "cosine"},
    )


def _build_raw_chromadb():
    """Fallback: load existing ChromaDB without LangChain dependency."""
    import chromadb
    try:
        from app.llm_backend import get_embedding_function
        embed_fn = get_embedding_function()
    except Exception:
        embed_fn = None

    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    collections = client.list_collections()
    if not collections:
        return None

    first = collections[0]
    coll_name = first if isinstance(first, str) else first.name
    collection = client.get_collection(coll_name, embedding_function=embed_fn)
    # Wrap in a simple object that mimics the LangChain interface we need
    return _RawChromaWrapper(collection)


class _RawChromaWrapper:
    """Minimal wrapper around raw ChromaDB collection for MMR-free retrieval."""
    def __init__(self, collection):
        self._coll = collection

    def as_retriever(self, **kwargs):
        return _RawRetriever(self._coll, kwargs.get("search_kwargs", {}))


class _FakeDoc:
    """Minimal doc stub — avoids langchain_core import (AppLocker block)."""
    def __init__(self, content, metadata=None):
        self.page_content = content
        self.metadata = metadata or {}


class _RawRetriever:
    def __init__(self, collection, kwargs):
        self._coll = collection
        self._k = kwargs.get("k", 5)

    def invoke(self, query):
        results = self._coll.query(query_texts=[query], n_results=self._k)
        docs = results.get("documents", [[]])[0]
        return [_FakeDoc(d) for d in docs]


def get_retriever():
    """
    Returns an MMR retriever for diverse, deduplicated results.
    MMR (Maximal Marginal Relevance) prevents near-duplicate chunks
    from dominating the context window.
    """
    vs = get_vector_store()
    if vs is None:
        return None
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": MMR_K,
            "fetch_k": MMR_FETCH_K,
            "lambda_mult": 0.5,
        },
    )


# ── Retrieval ──────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token for English text)."""
    return len(text) // 4


def _guard_context(context: str) -> str:
    """Log context-overflow risk and optionally trim (env-gated, OFF by default)."""
    est = _estimate_tokens(context)
    if est > OLLAMA_NUM_CTX * 0.8:
        logger.warning(
            f"RAG context near overflow: ~{est} tokens (limit {OLLAMA_NUM_CTX}). "
            f"Consider lowering RAG_TOP_K / RAG_FETCH_K."
        )
    if RAG_MAX_CONTEXT_CHARS and len(context) > RAG_MAX_CONTEXT_CHARS:
        context = context[:RAG_MAX_CONTEXT_CHARS]
    return context


# Cache for the hybrid path: loaded corpus + BM25 index (tiny store, built once).
_bm25_cache: dict = {}


class _MiniBM25:
    """
    Minimal BM25 scorer (stdlib only, ~30 lines) for the hybrid retrieval
    path. Avoids the rank_bm25 dependency; plenty accurate for a small
    knowledge-base corpus (< a few hundred chunks).
    """

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        import math
        from collections import Counter

        self._raw = corpus
        self.k1, self.b = k1, b
        self._math = math
        self.docs = [re.findall(r"[a-z0-9$]+", d.lower()) for d in corpus]
        self.n = len(self.docs)
        self.df: Counter = Counter()
        for tokens in self.docs:
            for term in set(tokens):
                self.df[term] += 1
        self.avgdl = sum(len(t) for t in self.docs) / max(1, self.n)

    def top(self, query: str, k: int) -> list[str]:
        """Return up to k raw docs ranked by BM25 score (zero-score docs excluded)."""
        from collections import Counter

        q = re.findall(r"[a-z0-9$]+", query.lower())
        scored: list[tuple[float, int]] = []
        for idx, tokens in enumerate(self.docs):
            dl = max(1, len(tokens))
            tf = Counter(tokens)
            score = 0.0
            for term in q:
                if term not in self.df:
                    continue
                idf = self._math.log(
                    1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5)
                )
                t = tf.get(term, 0)
                if t:
                    score += idf * (t * (self.k1 + 1)) / (
                        t + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                    )
            scored.append((score, idx))
        scored.sort(key=lambda pair: -pair[0])
        return [self._raw[idx] for score, idx in scored[:k] if score > 0]


def retrieve_context_hybrid(query: str, top_k: int = MMR_K, fetch_k: int = MMR_FETCH_K) -> str:
    """
    Hybrid retrieval: dense KNN + BM25, fused with Reciprocal Rank Fusion.

    NOTE: chromadb 1.5.9's native Search API (collection.search / Knn / Rrf)
    raises NotImplementedError on the local (PersistentClient) backend, so we
    implement hybrid locally: dense candidates from Chroma + a stdlib BM25
    scorer over the loaded corpus, fused with RRF in Python. No extra deps.

    Falls back to MMR via retrieve_context() on any failure.
    """
    import chromadb
    from app.llm_backend import get_embedding_function

    client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
    ef = get_embedding_function()
    col = client.get_collection("langchain", embedding_function=ef)

    # ── Dense side: Chroma KNN ──────────────────────────────────────
    dense_res = col.query(query_texts=[query], n_results=fetch_k, include=["documents"])
    dense_docs = (dense_res.get("documents") or [[]])[0]

    # ── Sparse side: BM25 over the loaded corpus (cached) ───────────
    if "bm25" not in _bm25_cache:
        got = col.get(include=["documents"])
        _bm25_cache["bm25"] = _MiniBM25(got.get("documents") or [])
    sparse_docs = _bm25_cache["bm25"].top(query, fetch_k)

    # ── RRF fusion (rank 0-based, k=60) ─────────────────────────────
    def _rrf(rank: int, k: int = 60) -> float:
        return 1.0 / (k + rank)

    scores: dict[str, float] = {}

    def _accumulate(candidates: list[str]) -> None:
        seen: set[str] = set()
        for rank, doc in enumerate(candidates):
            if not doc.strip():
                continue
            key = doc.strip()
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + _rrf(rank)

    _accumulate(dense_docs)
    _accumulate(sparse_docs)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
    return "\n\n---\n\n".join(doc for doc, _ in ranked)


def retrieve_context(query: str) -> str:
    """
    MMR retrieval — returns formatted context string from ChromaDB.
    With RAG_SEARCH_MODE=hybrid, uses dense+BM25 hybrid (env-gated).
    Returns empty string if retrieval fails.
    """
    if RAG_SEARCH_MODE == "hybrid":
        try:
            return _guard_context(retrieve_context_hybrid(query, MMR_K))
        except Exception:
            logger.exception("Hybrid retrieval failed — falling back to MMR")

    retriever = get_retriever()
    if retriever is None:
        return ""

    try:
        docs = retriever.invoke(query)
        if not docs:
            logger.debug(f"No documents retrieved for: {query[:60]}...")
            return ""

        chunks = []
        for d in docs:
            sec = d.metadata.get("section", "")
            body = d.page_content
            chunks.append(f"[§ {sec}]\n{body}" if sec else body)
        context = "\n\n---\n\n".join(chunks)
        logger.debug(f"Retrieved {len(chunks)} chunks for: {query[:60]}...")
        return _guard_context(context)

    except Exception:
        logger.exception("Retrieval failed")
        return ""


# ── LLM Query ──────────────────────────────────────────────────────────

def _get_available_model() -> str:
    """Find the best available model for the active backend (see llm_backend)."""
    from app.llm_backend import default_model, pick_model

    return pick_model(default_model(("qwen2.5:7b-instruct-q3_K_M", "qwen2.5:7b-instruct", "qwen2.5:7b")))


def _best_distance(query: str) -> float | None:
    """
    Best cosine distance for the query against the store.
    Used by the similarity threshold gate (env-gated, OFF by default).
    """
    try:
        import chromadb
        from app.llm_backend import get_embedding_function

        client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        ef = get_embedding_function()
        col = client.get_collection("langchain", embedding_function=ef)
        r = col.query(query_texts=[query], n_results=1, include=["distances"])
        dists = (r.get("distances") or [[None]])[0]
        return dists[0] if dists else None
    except Exception:
        logger.exception("Threshold pre-check failed — continuing without it")
        return None


def query_rag(question: str, *, mode: str = "voice") -> str | None:
    """
    Full RAG pipeline: retrieve context -> build prompt -> query LLM.

    This is the single entry point for ALL interfaces.
    Thread-safe (no shared mutable state). Callable from sync or async contexts.

    mode:
        "voice" (default) — production voice system prompt for live phone
                            calls (app.voice_handler, inbound/outbound).
        "chat"            — Markdown-oriented SYSTEM_PROMPT, same answer
                            style as the Streamlit chat (WhatsApp text).

    Returns:
        Answer string, or None if the pipeline failed.
    """
    if not question.strip():
        return None

    # Optional gate: refuse to answer when the best match is too distant
    if RAG_SIMILARITY_THRESHOLD > 0:
        dist = _best_distance(question)
        if dist is not None and dist > RAG_SIMILARITY_THRESHOLD:
            logger.info(f"RAG threshold gate: distance {dist:.3f} > {RAG_SIMILARITY_THRESHOLD}")
            return "I don't have that specific information in the university profile."

    # Step 1: Retrieve context
    context = retrieve_context(question)

    # Step 2: Build prompt. Live voice calls keep the production voice
    # system prompt (interruption handling, spoken-output rules).
    # Text chat interfaces (WhatsApp, Streamlit app.py, admissions_bot.py)
    # use the Markdown-oriented SYSTEM_PROMPT above so answers match
    # across text channels.
    if mode == "chat":
        prompt = SYSTEM_PROMPT.format(
            context=context
            or "(No university profile information was retrieved for this question.)"
        )
    else:
        from app.voice_system_prompt import build_voice_system_prompt

        prompt = build_voice_system_prompt(context)
    prompt += f"\n\nStudent's question: {question}"

    # Step 3: Query LLM (Ollama on Windows/Linux, MLX on Apple Silicon)
    try:
        from app.llm_backend import chat as backend_chat

        model = _get_available_model()
        logger.info(f"RAG query: model={model}, context_chars={len(context)}")

        return backend_chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            num_ctx=OLLAMA_NUM_CTX,
            temperature=float(OLLAMA_TEMPERATURE) if OLLAMA_TEMPERATURE else None,
        )

    except Exception:
        logger.exception("LLM query failed")
        return None
