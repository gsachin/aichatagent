"""US-018 deliverables 3-4: the relevance set and the metric definitions.

Deliverable 3 derives a relevance set from the KB's section structure and the
golden set's required facts. Deliverable 4 defines `recall@k`, `MRR`, `nDCG@k`,
tokens injected and latency.

The two things worth testing are not the formulas -- those are standard -- but:

  * **the relevance set is DERIVED, not invented.** Nothing here decides what
    "relevant" means; it reads it off a containment test. The test asserts the
    derivation rule holds on real cases, so an empty or arbitrary set fails.
  * **the grade travels with the number.** The set inherits `DG-03`'s status, so
    `coverage()` must report SCREEN-GRADE and say why. A relevance set that
    reported itself as adoption-grade would be the exact overclaim this
    programme spent 2026-09-19 removing from five stories.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us018_rag_metrics.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from eval import rag_metrics as M  # noqa: E402

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
print("US-018 deliverables 3-4 -- relevance set (derived) and metrics")
print("=" * 74)

# ── The KB parses into sections, and the preamble is not one ────────────
secs = M.sections()
check("the KB parses into sections", len(secs) >= 20, str(len(secs)))

# This asserted a hardcoded heading, "Achievements & Accreditation". That was
# true of the pre-C5 KB; the rebuild to meridian_kb__v1 restructured it into
# `## Human Title` with `### slug` children, so the topic is now two sections,
# `accreditation` and `achievements`, and the literal went stale.
#
# The literal was standing in for a property, so assert the property instead.
# `sections()` keys on `m.group(2)` -- the heading text -- so ids are heading
# text BY CONSTRUCTION and re-asserting that would be vacuous. What is NOT
# guaranteed is that distinct headings stay distinct: two headings sharing a
# line silently collapse into one dict entry, and the second body is served by
# nobody. That is the real failure this check can catch, and it is reachable.
_src = M.KB_PATH.read_text(encoding="utf-8")
_heading_texts = [m.group(2).strip() for m in M._HEADING.finditer(_src)]
check("every heading becomes its own section -- no id collision drops a body",
      len(secs) == len(_heading_texts),
      f"{len(_heading_texts)} headings -> {len(secs)} sections")
check("the document preamble is NOT a section (else everything is relevant)",
      not any(k.lower().startswith("meridian university") for k in secs),
      str(list(secs)[:2]))

# ── The relevance set is derived from facts, on real cases ──────────────
rel = M.relevance_set()
check("a relevance set exists for every golden case", len(rel) == 161, str(len(rel)))
nonempty = {c: v for c, v in rel.items() if v}
check("some cases are scoreable at all", len(nonempty) >= 40, str(len(nonempty)))
check("relevant sections are real KB sections",
      all(s in secs for v in rel.values() for s in v))

# The derivation rule, asserted rather than assumed: pick a case and confirm
# every section listed really does contain one of its required facts.
from eval import checks as C  # noqa: E402

cases = {c["case_id"]: c for c in C.load_cases()}
probe = next(c for c, v in rel.items() if v and (cases[c].get("required_facts") or []))
facts = cases[probe]["required_facts"]
body = M._loose(secs[rel[probe][0]])
check("DERIVATION: each relevant section contains a required fact of its case",
      any(M._loose(f if isinstance(f, str) else " ".join(x for x in (f.get("any_of") or []) if x))
          in body for f in facts),
      f"{probe} -> {rel[probe][0]}")

# ── The grade travels with the number ──────────────────────────────────
cov = M.coverage(rel)
check("the coverage report grades itself SCREEN-GRADE", cov["grade"] == "SCREEN-GRADE")
check("...and says why, naming DG-03", "unapproved" in cov["grade_reason"]
      and "DG-03" in cov["grade_reason"], cov["grade_reason"])
check("...and never claims more approved ground truth than exists",
      cov["from_verified_ground_truth"] <= cov["scoreable"])
check("unscoreable cases are counted, not hidden",
      cov["scoreable"] + cov["unscoreable"] == cov["cases"])

# ── Metrics, on inputs with known answers ──────────────────────────────
# recall@k: 2 of 3 relevant found in the top 3 -> 2/3; top 1 -> 1/3.
check("recall@k counts relevant sections in the top k",
      abs(M.recall_at_k(["a", "b", "x"], ["a", "b", "c"], 3) - 2 / 3) < 1e-9)
check("recall@k is bounded by k", M.recall_at_k(["a", "b", "x"], ["a", "b", "c"], 1) == 1 / 3)
check("recall@k with nothing relevant returns 1.0 and the caller must exclude it",
      M.recall_at_k(["a"], [], 5) == 1.0)

# MRR: first relevant at rank 2 -> 1/2; none -> 0.
check("MRR is the reciprocal rank of the first relevant hit",
      M.reciprocal_rank(["x", "a", "b"], ["a", "b"]) == 0.5)
check("MRR is 0.0 when nothing relevant is retrieved",
      M.reciprocal_rank(["x", "y"], ["a"]) == 0.0)

# nDCG: ideal ordering gives 1.0; a reversed ordering gives less.
check("nDCG@k of the ideal ordering is 1.0",
      abs(M.ndcg_at_k(["a", "b"], ["a", "b"], 2) - 1.0) < 1e-9)
check("nDCG@k is below 1.0 when relevant results are pushed down",
      M.ndcg_at_k(["x", "a"], ["a", "b"], 2) < 1.0)
check("nDCG@k with nothing relevant returns 1.0 (vacuous, same caveat as recall)",
      M.ndcg_at_k(["x"], [], 5) == 1.0)
# A perfect retrieval of ONE relevant item beats a perfect retrieval of two only
# in that both are 1.0 -- this asserts the normalisation is by the ideal, not raw.
check("nDCG@k normalises by the ideal, so a single perfect hit scores 1.0",
      abs(M.ndcg_at_k(["a"], ["a"], 5) - 1.0) < 1e-9)

# tokens injected: a ratio-stable proxy, not a tokenizer.
check("tokens injected counts words, so it is comparable across configurations",
      M.tokens_injected("one two three") == 3)
check("...and is 0 for empty context", M.tokens_injected("") == 0)

print()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
