"""US-018 deliverables 3 and 4 — the relevance set and the metric definitions.

`RAG-Optimized` is what a relevance measurement selects; it enters service only
through `BRD-09`'s non-inferiority gate. That gate needs two things this module
supplies: something to be relevant *to*, and a way to score it.

**The relevance set is DERIVED, not judged.** The story says the KB's section
structure derives it, and the golden set already carries `required_facts` per
case. So a section is relevant to a case when it contains at least one of that
case's required facts — a containment test `checks.kb_traceable` already
performs. Nothing here invents what "relevant" means; it reads it off a
structure that exists and states the rule.

**This is SCREEN-GRADE and the module says so in its own output.** The rule is
mechanical, but the facts it reads are ground truth that is **137/161
unapproved** (`DG-03`). A relevance set derived from unapproved ground truth
cannot settle an adoption decision, exactly as the `US-015` quality screen
cannot. What it can do is rank two configurations against each other on the same
basis — which is what a non-inferiority gate needs — and that is the claim made
for it, not a stronger one.

**Metrics are binary-gain.** A section is relevant or it is not, because the
derivation above yields a set rather than grades. `recall@k`, `MRR` and `nDCG@k`
are therefore defined in their binary forms, and `nDCG` says so rather than
silently implying graded judgements nobody made.

Serves `BRD-21`, `DAT-01`. Read-only: nothing on the serving path imports it.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

KB_PATH = PROJ / "content" / "meridian" / "meridian_knowledge_base.md"

#: `## Heading` / `### Heading`. The heading text is the section id, because it
#: is also what the retrieval path emits as `[§ Heading]` (rag_legacy.py:473),
#: so a retrieved chunk and a relevant section are directly comparable with no
#: mapping table in between.
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.M)


def sections(text: str | None = None) -> dict[str, str]:
    """The KB as {section id: body text}.

    Split on `##`/`###` headings. The preamble before the first heading is not
    a section and is excluded -- it is the document title and an index, and
    treating it as retrievable content would make everything relevant.
    """
    src = KB_PATH.read_text(encoding="utf-8") if text is None else text
    out: dict[str, str] = {}
    marks = list(_HEADING.finditer(src))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        out[m.group(2).strip()] = src[m.end():end].strip()
    return out


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def relevance_set(cases: list[dict] | None = None,
                  kb: dict[str, str] | None = None) -> dict[str, list[str]]:
    """{case_id: [section ids containing at least one of its required facts]}.

    A case with no required facts yields an empty list, not a guess -- it is
    unscoreable for retrieval and is reported as such by `coverage()` below.
    """
    if cases is None:
        from eval import checks as C
        cases = C.load_cases()
    kb = kb or sections()
    loose = {k: _loose(v) for k, v in kb.items()}

    out: dict[str, list[str]] = {}
    for case in cases:
        facts = case.get("required_facts") or []
        hits = []
        for sec, body in loose.items():
            for f in facts:
                probe = f if isinstance(f, str) else " ".join(
                    str(x) for x in (f.get("any_of") or []) if x)
                if probe and _loose(probe) and _loose(probe) in body:
                    hits.append(sec)
                    break
        out[case["case_id"]] = hits
    return out


def coverage(rel: dict[str, list[str]]) -> dict:
    """How much of the golden set this relevance set can actually score."""
    total = len(rel)
    scored = sum(1 for v in rel.values() if v)
    approved = 0
    try:
        from eval import checks as C
        for c in C.load_cases():
            if c.get("ground_truth_status") == "verified" and rel.get(c["case_id"]):
                approved += 1
    except Exception:                             # noqa: BLE001
        pass
    return {
        "cases": total,
        "scoreable": scored,
        "unscoreable": total - scored,
        "from_verified_ground_truth": approved,
        "grade": "SCREEN-GRADE",
        "grade_reason": (f"derived from ground truth that is "
                         f"{total - approved}/{total} unapproved (DG-03)"),
    }


# ── Metrics. Binary gain throughout, for the reason in the module docstring. ──

def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Fraction of relevant sections found in the first k. 1.0 when nothing is
    relevant -- vacuously true, and the caller must exclude those cases rather
    than let them inflate an average."""
    if not relevant:
        return 1.0
    top = set(retrieved[:k])
    return len(top & set(relevant)) / len(set(relevant))


def reciprocal_rank(retrieved: list[str], relevant: list[str]) -> float:
    """1/rank of the first relevant section, 0.0 if none is retrieved."""
    rel = set(relevant)
    for i, sec in enumerate(retrieved, 1):
        if sec in rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Binary-gain nDCG@k.

    With binary gains this is the standard form: DCG is the sum of 1/log2(i+1)
    over relevant sections in the top k, normalised by the ideal ordering (all
    relevant sections first). Not graded -- see the module docstring.
    """
    rel = set(relevant)
    if not rel:
        return 1.0
    dcg = sum(1.0 / math.log2(i + 1)
              for i, sec in enumerate(retrieved[:k], 1) if sec in rel)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(rel), k) + 1))
    return dcg / ideal if ideal else 0.0


def tokens_injected(context: str) -> int:
    """A cheap, stable proxy for what a context costs the prompt.

    Words, not a tokenizer: this must be comparable across configurations
    without depending on which tokenizer the inference engine happens to use,
    and the comparison is what `BRD-09` needs. Absolute counts would be wrong;
    the ratio between two configurations is right.
    """
    return len(context.split())


if __name__ == "__main__":                        # pragma: no cover
    rel = relevance_set()
    cov = coverage(rel)
    print("US-018 deliverables 3-4 -- relevance set and metrics")
    print(f"  sections in the KB     : {len(sections())}")
    for k, v in cov.items():
        print(f"  {k:<24}: {v}")
    demo = next((c for c, v in rel.items() if v), None)
    if demo:
        print(f"\n  example {demo}: relevant = {rel[demo][:3]}")
