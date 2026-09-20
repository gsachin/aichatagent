"""Name the RAG configuration that is actually running.

`BRD-21` splits the retrieval configuration in two. `RAG-Baseline` is what runs
today — the incumbent values, never selected against anything, honest about
being unoptimised. `RAG-Optimized` is what a relevance measurement eventually
selects, and it enters service only through `BRD-09`'s non-inferiority gate.

Until this module existed, **neither configuration was named anywhere in the
system**. `08-coverage-verification.md` §2 records that nothing ships the
baseline definition, and `plan-state.md` records the baseline characterisation
as NOT STARTED with no owner. `US-018` is that owner.

**Every value here is READ, never restated.** That is the whole design and it is
the lesson this programme keeps re-learning: `REC-07` records that a prior
document asserted wrong values for exactly these settings, and a module that
hardcoded `top_k = 5` would become the next one the moment `.env` changed. So
`baseline()` imports the live constants from `app.rag_legacy` — the same objects
the retrieval path itself consults — and a drift is then impossible by
construction rather than by discipline.

**What this module does NOT do.** It does not define relevance, and it does not
score anything. The relevance set and the metric definitions (`recall@k`, MRR,
nDCG, tokens injected) are `US-018` deliverables 3 and 4 and they need a
judgement about what "relevant" means — which is adjacent to the question
`DG-03` exists for. Recording a baseline is useful without them; a baseline
nobody can score is the half-built state `US-003` already shows the cost of.

Serves `BRD-21`, `DAT-14`. Revertible: nothing imports it on the serving path.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

#: The two named configurations `BRD-21` requires. Baseline is what runs;
#: Optimized is what a measurement would select and it is NOT in service.
BASELINE_NAME = "RAG-Baseline"
OPTIMIZED_NAME = "RAG-Optimized"

PROJ = Path(__file__).resolve().parents[1]
KB_PATH = PROJ / "content" / "meridian" / "meridian_knowledge_base.md"
DEFAULT_BASELINE_PATH = PROJ / "eval" / "rag_baseline.json"

#: Retrieval settings the baseline pins. Each maps to the live constant that the
#: retrieval path itself reads -- the indirection is the point, not a formality.
_LIVE_SOURCES = (
    ("top_k", "rag_legacy", "MMR_K"),
    ("fetch_k", "rag_legacy", "MMR_FETCH_K"),
    ("chunk_size", "rag_legacy", "CHUNK_SIZE"),
    ("chunk_overlap", "rag_legacy", "CHUNK_OVERLAP"),
    ("search_mode", "rag_legacy", "RAG_SEARCH_MODE"),
    ("similarity_threshold", "rag_legacy", "RAG_SIMILARITY_THRESHOLD"),
)


def corpus_identity(path: Path | None = None) -> str:
    """The knowledge base's content hash.

    A baseline without a corpus identity is not a baseline: `DAT-14`'s whole
    subject is that a retrieval result means nothing without the version of the
    index it came from, and the same is true of the settings it was measured
    under. Returns "" when the KB is absent, so a caller can tell "no corpus"
    from "corpus hashed to nothing".
    """
    p = path or KB_PATH
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def live_values() -> dict:
    """The effective retrieval settings, read from the code that runs them.

    Imported at call time rather than module import, because `rag_legacy` reads
    its own constants from the environment at import — resolving late means this
    reports what a freshly-configured process would use, which is what a
    baseline is for.

    `_load_env_if_present()` runs **here**, immediately before the import, and
    not only in `record_baseline`. Putting it there was the first attempt and it
    was wrong: `baseline()` does not go through `record_baseline`, so calling it
    from a bare process still reported the code default. The negative control in
    `test_us018_rag_baseline.py` caught it. Anything that resolves these values
    must pass through this function, so this is the only place the guard has to
    be — and if a second resolution path is ever added, it must call this one.
    """
    _load_env_if_present()
    from app import rag_legacy

    return {name: getattr(rag_legacy, attr) for name, _mod, attr in _LIVE_SOURCES}


def baseline(path: Path | None = None) -> dict:
    """`RAG-Baseline`, named and inspectable.

    This is a NAME for what already runs, not a change to it. Nothing on the
    serving path imports this module, so calling it cannot alter behaviour —
    which is what makes `BRD-21`'s split cost nothing to adopt.
    """
    return {
        "config_name": BASELINE_NAME,
        "status": "in-service",
        "selected_against": None,   # never selected against anything; say so
        "optimized_alternative": OPTIMIZED_NAME,
        "values": live_values(),
        "corpus_sha256": corpus_identity(path),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Named so a later reader cannot mistake the incumbent for a choice.
        "provenance": ("incumbent values, never selected against a measurement "
                       "(REC-07); RAG-Optimized is selected by US-018's "
                       "relevance set and enters service only through BRD-09"),
    }


def _load_env_if_present() -> None:
    """Load `.env` the way the application does, before resolving values.

    **This is not housekeeping; without it the recorder writes a false
    baseline.** `rag_legacy` reads its constants from the environment at import
    and falls back to code defaults for anything absent, so a process that never
    loaded `.env` reports `search_mode = mmr` while the running application
    reports `hybrid` — the setting is `RAG_SEARCH_MODE=hybrid` in `.env:141`.

    That was measured, not imagined: the first run of this module, invoked as
    `python -m app.rag_config` with no environment loaded, recorded `mmr`. The
    file it wrote would have been a baseline that does not describe production,
    and nothing in it would have said so — the exact "set != live" defect this
    programme has now found in `FASTAPI_WORKERS`, the Pipecat VAD,
    `.machine_profile.json`, the pre-warm list, and `MOD-03` B.3.

    Idempotent and non-destructive: `load_dotenv` does not override variables
    already set in the environment, so a caller that has configured its own
    environment is unaffected.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJ / ".env")
    except Exception:                             # noqa: BLE001
        pass          # no dotenv, or no file: fall back to the real environment


def record_baseline(path: Path | None = None, out: Path | None = None) -> Path:
    """Write the baseline to `eval/rag_baseline.json`.

    A file rather than a constant, so that a later `RAG-Optimized` has something
    concrete to be non-inferior *to*. Returns the path written.
    """
    _load_env_if_present()
    dest = out or DEFAULT_BASELINE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(baseline(path), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    return dest


if __name__ == "__main__":                       # pragma: no cover
    written = record_baseline()
    print(f"{BASELINE_NAME} recorded -> {written}")
    for k, v in live_values().items():
        print(f"  {k:<22} {v}")
    print(f"  corpus_sha256          {corpus_identity()[:16]}…")
