"""Two models, the same cases -- do they actually differ?

`us015_quality_screen.py` produces a pass rate per model. A pass rate on its
own cannot answer the question a model choice turns on, because the two models
answer *the same cases*: the comparison is paired, and the informative quantity
is not 15/30 vs 7/30 but how many cases one model gets right that the other gets
wrong, in each direction.

This file computes that, and refuses to overstate it:

  * only cases BOTH runs scored (`pass`/`fail`) enter the comparison -- a case
    one run blocked carries no information about either model;
  * the discordant pairs are tested with **McNemar's exact test** (binomial on
    the discordant count). The chi-square approximation is not used: with a
    handful of discordant pairs it is wrong, and with zero it is undefined;
  * the result is reported as a p-value AND as the number of cases it rests on,
    so "no difference" is never printed without saying how weak the evidence
    for that is.

A screen that reports a winner from 2 discordant cases is worse than one that
reports nothing. The headline this file prints is whichever of those the data
supports.

Run:  .venv/Scripts/python.exe doc/perf/tools/us015_pairwise.py \
          doc/perf/us015-screen-base.json --a qwen2.5:14b --b llama3.2:3b
      # or let it pick the two runs in the file:
      .venv/Scripts/python.exe doc/perf/tools/us015_pairwise.py doc/perf/us015-screen-base.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
os.chdir(PROJ)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts b and c.

    Under the null the discordant pairs are a fair coin: each of the b+c of
    them falls either way with p=0.5. The two-sided p is the probability of a
    split at least as lopsided as the one observed.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def wilson(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """Wilson score interval -- correct at small n, unlike the normal one."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, run in data.items():
        if "rows" not in run:
            raise SystemExit(
                f"{path}: '{name}' has no per-case rows. This file compares "
                f"paired outcomes and cannot work from a tally. Re-run "
                f"us015_quality_screen.py (it now writes rows).")
    return data


def verdicts(run: dict) -> dict[str, str]:
    return {r["case_id"]: r["verdict"] for r in run["rows"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Paired comparison of two screen runs.")
    ap.add_argument("path", help="a us015-quality-screen JSON, or a tag when --dir is given")
    ap.add_argument("--b-path", default=None,
                    help="second JSON/tag (default: the same file -- use when comparing "
                         "two MODELS; pass a second file when comparing two CONFIGS of "
                         "one model, which is the same paired question)")
    ap.add_argument("--a", default=None, help="first model (default: file order)")
    ap.add_argument("--b", default=None, help="second model (default: file order)")
    ap.add_argument("--dir", default="doc/perf", help="where to resolve a bare tag")
    args = ap.parse_args()

    def resolve(p: str) -> Path:
        q = Path(p)
        return q if q.is_file() else Path(args.dir) / f"us015-screen-{p}.json"

    path_a = resolve(args.path)
    path_b = resolve(args.b_path) if args.b_path else path_a
    data_a, data_b = load(path_a), load(path_b)

    names = list(data_a)
    a_name = args.a or (names[0] if names else None)
    if args.a is None and args.b_path and names:
        # Config comparison: the same model appears in both files.
        common = [n for n in data_a if n in data_b]
        if len(common) == 1:
            a_name = common[0]
    if a_name not in data_a:
        raise SystemExit(f"{a_name!r} not in {path_a}; have {names}")

    b_names = list(data_b)
    b_name = args.b or (a_name if a_name in data_b else (b_names[0] if b_names else None))
    if b_name not in data_b:
        raise SystemExit(f"{b_name!r} not in {path_b}; have {b_names}")

    A, B = data_a[a_name], data_b[b_name]
    va, vb = verdicts(A), verdicts(B)

    print("=" * 78)
    print("PAIRED SCREEN COMPARISON")
    print("=" * 78)
    print(f"  A: {a_name:<16} {path_a}")
    print(f"     options {A.get('options')}  tag={A.get('tag')!r}")
    print(f"  B: {b_name:<16} {path_b}")
    print(f"     options {B.get('options')}  tag={B.get('tag')!r}")

    shared = sorted(set(va) & set(vb))
    blocked = [c for c in shared if va[c] == "blocked" or vb[c] == "blocked"]
    scored = [c for c in shared if c not in set(blocked)]
    print(f"\n  cases in both runs      : {len(shared)}")
    print(f"  excluded (blocked, DG-03): {len(blocked)}"
          f"  -- a blocked case says nothing about either model")
    print(f"  PAIRED, SCORABLE        : {len(scored)}")

    if not scored:
        print("\n  No case was scored by both runs. There is nothing to compare.")
        return 2

    both = sum(1 for c in scored if va[c] == "pass" and vb[c] == "pass")
    only_a = sum(1 for c in scored if va[c] == "pass" and vb[c] == "fail")
    only_b = sum(1 for c in scored if va[c] == "fail" and vb[c] == "pass")
    neither = sum(1 for c in scored if va[c] == "fail" and vb[c] == "fail")

    n = len(scored)
    pa, pb = (both + only_a) / n, (both + only_b) / n
    la, ha = wilson(both + only_a, n)
    lb, hb = wilson(both + only_b, n)

    print(f"\n  {'':<22}{a_name:>16}{b_name:>16}")
    print(f"  {'pass / scorable':<22}{both + only_a:>10}/{n:<5}{both + only_b:>10}/{n:<5}")
    print(f"  {'pass rate':<22}{100 * pa:>15.0f}%{100 * pb:>15.0f}%")
    print(f"  {'95% Wilson':<22}{f'[{100*la:.0f}, {100*ha:.0f}]':>16}"
          f"{f'[{100*lb:.0f}, {100*hb:.0f}]':>16}")

    print(f"\n  2x2 over the {n} paired cases")
    print(f"    both pass            : {both}")
    print(f"    only {a_name:<16}: {only_a}")
    print(f"    only {b_name:<16}: {only_b}")
    print(f"    neither              : {neither}")

    p = mcnemar_exact(only_a, only_b)
    disc = only_a + only_b
    print(f"\n  McNemar exact (two-sided): p = {p:.4f}   on {disc} discordant case(s)")

    print("\n  " + "-" * 74)
    if disc == 0:
        print("  VERDICT: the two models agree on every scorable case.")
        print("           No quality difference is measurable here, in either")
        print("           direction. Choose on latency and VRAM, which are")
        print("           measured elsewhere and are not close.")
    elif p < 0.05:
        winner = a_name if only_a > only_b else b_name
        print(f"  VERDICT: {winner} is ahead, and the margin survives an exact")
        print(f"           test at this n (p = {p:.4f}, {disc} discordant cases).")
        print("           STILL SCREEN-GRADE: adoption needs PO-approved ground")
        print("           truth, which DG-03 has not yet delivered.")
    else:
        print(f"  VERDICT: NOT ESTABLISHED. The observed gap ({only_a} vs {only_b}")
        print(f"           discordant cases) is consistent with chance at this n")
        print(f"           (p = {p:.4f}). Neither model is shown better than the")
        print(f"           other on quality; a choice made on this table alone")
        print(f"           would be made on noise.")
    print("  " + "-" * 74)

    # which cases, and why -- the part a tally cannot show
    if only_a or only_b:
        print(f"\n  cases only {a_name} passed:")
        for c in scored:
            if va[c] == "pass" and vb[c] == "fail":
                why = next((r for r in B["rows"] if r["case_id"] == c), {})
                print(f"    {c:<10} {why.get('grade', ''):<14} "
                      f"{(why.get('reasons') or [''])[0][:70]}")
        print(f"\n  cases only {b_name} passed:")
        for c in scored:
            if va[c] == "fail" and vb[c] == "pass":
                why = next((r for r in A["rows"] if r["case_id"] == c), {})
                print(f"    {c:<10} {why.get('grade', ''):<14} "
                      f"{(why.get('reasons') or [''])[0][:70]}")

    # DEFECT CENSUS over every case both runs answered. A blocked case cannot
    # be scored, but "did this answer ramble" and "did it miss a fact" are
    # mechanical questions about the ANSWER, not about the ground truth's
    # approval. This is the only place the blocked 53 cases contribute, and
    # they contribute as defects counted, never as verdicts earned.
    if all("census" in r for r in A["rows"]) and all("census" in r for r in B["rows"]):
        ca = {r["case_id"]: set(r.get("census") or []) for r in A["rows"]}
        cb = {r["case_id"]: set(r.get("census") or []) for r in B["rows"]}
        ids = sorted(set(ca) & set(cb))
        print(f"\n  PAIRED DEFECT CENSUS over {len(ids)} cases "
              f"(blocked cases included -- as defects, not verdicts)")
        print(f"    {'defect':<18}{a_name:>10}{b_name:>10}{'A only':>9}{'B only':>9}"
              f"{'McNemar p':>12}")
        for defect in ("format_sentences", "format_words", "fact_missing",
                       "forbidden_claim", "format_other"):
            na = sum(1 for c in ids if defect in ca[c])
            nb = sum(1 for c in ids if defect in cb[c])
            if not na and not nb:
                continue
            oa = sum(1 for c in ids if defect in ca[c] and defect not in cb[c])
            ob = sum(1 for c in ids if defect in cb[c] and defect not in ca[c])
            pv = mcnemar_exact(oa, ob)
            mark = "  <-- differs" if pv < 0.05 else ""
            print(f"    {defect:<18}{na:>10}{nb:>10}{oa:>9}{ob:>9}{pv:>12.4f}{mark}")
        print("    A cell = cases where that model's answer shows the defect.")
        print("    'differs' means the direction is consistent beyond chance; it")
        print("    says which model has the defect MORE, not which is better.")

    # failure taxonomy -- verbosity is fixable by prompt or params; a missing
    # fact is not. The distinction decides what to do about the result.
    print("\n  failure taxonomy (all scorable failures, both runs)")
    for name, run in ((a_name, A), (b_name, B)):
        tax: collections.Counter = collections.Counter()
        for r in run["rows"]:
            if r["verdict"] != "fail":
                continue
            first = (r.get("reasons") or ["(no reason given)"])[0]
            if first.startswith("spoken-format violation"):
                tax["spoken format (verbosity -- fixable by prompt/params)"] += 1
            elif first.startswith("required fact missing"):
                tax["required fact missing (content -- not fixable by params)"] += 1
            else:
                tax[f"other: {first[:44]}"] += 1
        print(f"    {name}")
        for k, v in tax.most_common():
            print(f"      {v:>3}  {k}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
