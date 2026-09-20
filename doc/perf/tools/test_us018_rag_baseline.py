"""US-018 deliverables 1-2: name the configuration, record the baseline.

US-018's characterisation half is unblocked and needs no golden set. This covers
the two mechanical deliverables:

  1. `RAG-Baseline` is NAMED and inspectable (`app/rag_config.py`).
  2. It is RECORDED to `eval/rag_baseline.json`, so a later `RAG-Optimized` has
     something concrete to be non-inferior to.

Deliverables 3 and 4 -- the relevance set and the metric definitions -- are NOT
covered and are not claimed. They need a judgement about what "relevant" means,
which is adjacent to the question `DG-03` exists for. A baseline nobody can score
is the half-built state `US-003` already demonstrates the cost of.

**The important test here is the environment one.** `rag_legacy` reads its
constants from the environment at import and falls back to code defaults for
anything absent, so a process that never loaded `.env` reports
`search_mode = mmr` while the running application reports `hybrid`. The first
run of the recorder did exactly that and wrote a baseline describing a
configuration nothing runs. The negative control below asserts that the
difference is real, so the guard protecting against it is not decoration.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us018_rag_baseline.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from app import rag_config as rc  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


print("=" * 74)
print("US-018 deliverables 1-2 -- RAG-Baseline named and recorded")
print("=" * 74)

# ── 1. Named ───────────────────────────────────────────────────────────
b = rc.baseline()
check("the baseline is named", b["config_name"] == rc.BASELINE_NAME == "RAG-Baseline")
check("and names the configuration it will be compared against",
      b["optimized_alternative"] == "RAG-Optimized")
check("it records that it was never selected against anything",
      b["selected_against"] is None, repr(b["selected_against"]))
check("and says so in words, so an incumbent is not read as a choice",
      "never selected" in b["provenance"], b["provenance"][:60])

# The values must be the six `US-018` names, and they must be READ not restated.
vals = b["values"]
check("all six settings are present",
      set(vals) == {"top_k", "fetch_k", "chunk_size", "chunk_overlap",
                    "search_mode", "similarity_threshold"}, str(sorted(vals)))
check("the values are the live constants, not copies",
      vals["top_k"] == rc.live_values()["top_k"])

# ── 2. The two preconditions that made this the honest baseline ─────────
from app import rag_legacy as rl  # noqa: E402

check("RAG-Baseline is what runs: top_k matches the retrieval path",
      vals["top_k"] == rl.MMR_K, f"{vals['top_k']} vs {rl.MMR_K}")
check("...and fetch_k, chunking and mode do too",
      (vals["fetch_k"], vals["chunk_size"], vals["chunk_overlap"], vals["search_mode"])
      == (rl.MMR_FETCH_K, rl.CHUNK_SIZE, rl.CHUNK_OVERLAP, rl.RAG_SEARCH_MODE))
check("the corpus is identified, because a baseline without one is not a baseline",
      len(b["corpus_sha256"]) == 64, b["corpus_sha256"][:16])

# ── 3. Recorded ────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "rag_baseline.json"
    written = rc.record_baseline(out=out)
    check("recording writes the file", written == out and out.is_file())
    loaded = json.loads(out.read_text(encoding="utf-8"))
    check("the file round-trips with the same content",
          loaded["config_name"] == "RAG-Baseline" and loaded["values"] == vals)
    check("recording is idempotent (a second call is not an error)",
          rc.record_baseline(out=out) == out)

# ── 4. NEGATIVE CONTROL: the environment trap is real ──────────────────
# Without `.env`, rag_legacy falls back to its code defaults and the mode reads
# `mmr`. With it, `hybrid`. If these ever became equal the guard below would be
# testing nothing, so the difference is asserted rather than assumed.
_default = rl.__dict__.get("RAG_SEARCH_MODE")
_env_mode = b["values"]["search_mode"]
check("NEGATIVE CONTROL: the recorded mode is the CONFIGURED one, not the "
      "code default", _env_mode == "hybrid",
      f"recorded {_env_mode!r}, expected 'hybrid' from .env:141")

import subprocess  # noqa: E402
bare = subprocess.run(
    [sys.executable, "-c",
     "from app import rag_legacy as r; print(r.RAG_SEARCH_MODE)"],
    capture_output=True, text=True, cwd=str(PROJ),
    env={k: v for k, v in os.environ.items() if k != "RAG_SEARCH_MODE"})
bare_mode = (bare.stdout or "").strip()
check("NEGATIVE CONTROL: a bare process reports the CODE DEFAULT instead, so "
      "the two really do differ and the guard matters",
      bare_mode == "mmr", f"bare process reported {bare_mode!r}")

print()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
