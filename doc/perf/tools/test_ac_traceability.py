"""Does every acceptance criterion have a test that names it?

The 2026-09-19 audit found five stories marked IMPLEMENTED whose acceptance
criteria were not all implemented -- US-007 ticks a box for an observation the
system cannot make, US-011 claims "ACs verified" for an unimplemented TAC-5,
US-013 claims record fields nothing writes, US-015's deliverable has no code,
US-017's cited run is DISCARDED against its own TAC. Every one has a green
suite.

The cause is structural, not incidental: **suites were written alongside the
code, so they encode what was built rather than what was specified.** A green
suite proves the tests pass. It does not prove the acceptance criteria are met,
and nothing was checking the difference.

This file checks the difference. It extracts every AC-n and TAC-n a story
declares and asks whether the story's own test files cite it. An id that no test
names is an untraced requirement.

WHAT THIS DOES NOT CLAIM. A citation is not a proof of coverage. A test can name
`TAC-5` and assert something weaker; this file cannot see that, and it does not
pretend to. It catches the cheaper and more common failure -- a requirement that
nothing ever mentions again after the story was written. The stronger claim
needs a human reading the test, which is what the audit did for five stories and
what no script replaces.

AND THE CONVERSE IS ALSO NOT CLAIMED: an uncited requirement is not necessarily
an untested one. An untraced id has two causes needing different fixes, and this
file cannot tell them apart:

  * **untested** -- no test asserts it. Fix: write one. `US-006`'s TAC-1, TAC-2,
    TAC-5 and TAC-8 are this: they are the first-token p95, cold-load,
    VRAM-at-N=2 and 30-minute soak gates, and the same audit that prompted this
    file found they had never been run.
  * **untagged** -- a test covers it but does not name it. Fix: tag the test.
    `US-006`'s AC-1..AC-4 are this: `test_us006_residency.py` exercises them
    under `TAC-n` names, so the behaviour is checked and the criterion is still
    unfindable from the suite.

Both are traceability gaps and both fail the DoD's "tests from the LLD test
scenarios pass" box, but a reader sent to fix the wrong one wastes the trip. The
report says which ids are uncited; a human says which of the two it is.

**The verdict depends on the story's own claimed status.** A story marked
BLOCKED has no tests by design and its gaps are expected; a story marked
IMPLEMENTED that leaves a requirement untraced is a finding. Failing on the
former would make this gate noise, and a gate that cries wolf is a gate that
gets switched off.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_ac_traceability.py
      .venv/Scripts/python.exe doc/perf/tools/test_ac_traceability.py --verbose
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
os.chdir(PROJ)

STORIES = PROJ / "doc" / "sdlc" / "stories"
TOOLS = PROJ / "doc" / "perf" / "tools"
EVAL = PROJ / "eval"

AC_RE = re.compile(r"^\*\*AC-(\d+)\.?\*\*", re.M)
TAC_RE = re.compile(r"^- TAC-(\d+):", re.M)
LLD_RE = re.compile(r"^\| T-(\d+) \|", re.M)
#: `re.M` is load-bearing and its absence was a real bug caught only by noticing
#: the status column printed empty for all 18 stories. Without it `^` anchors to
#: the start of the whole file, every story parsed as status `""`, no story
#: matched `startswith("IMPLEMENTED")`, and the gate PASSED -- reporting no
#: findings from a checker that could not see a single status line. Same shape
#: as the US-012 "PASS on zero keys" and the STT-219 aggregate in this session:
#: the instrument failed silently and the result looked clean.
STATUS_RE = re.compile(r"^- \*\*Status:\*\* \*\*([A-Z][A-Z \-]*)", re.M)

#: A story in one of these states is expected to have gaps. Everything else is
#: claiming delivery, and an untraced requirement in it is a finding.
NOT_CLAIMING_DELIVERY = ("BLOCKED", "NOT STARTED", "AWAITING", "IN PROGRESS", "PARTIAL")

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def story_files() -> list[Path]:
    return sorted(STORIES.glob("US-*.md"))


def parse_story(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    head = text.split("## ", 1)[0]
    status = (STATUS_RE.search(head) or [None, ""])[1].strip()
    sid = re.match(r"(US-\d+)", p.name).group(1)
    return {
        "id": sid,
        "path": p,
        "status": status,
        "acs": sorted({int(x) for x in AC_RE.findall(text)}),
        "tacs": sorted({int(x) for x in TAC_RE.findall(text)}),
        "lld": sorted({int(x) for x in LLD_RE.findall(text)}),
        "text": text,
    }


def evidence_files(story: dict) -> list[Path]:
    """The files that may legitimately cite this story's ids.

    Scoped deliberately. `AC-1` exists in every story and means something
    different in each, so a citation only counts inside a file belonging to this
    story: either its own suite by name, or a file the story itself names.
    Counting a bare `AC-1` anywhere would credit every story with every other
    story's coverage -- a whole programme of false greens.
    """
    out: list[Path] = []
    num = story["id"].split("-")[1]
    out += sorted(TOOLS.glob(f"test_us{num}*.py"))
    out += sorted(TOOLS.glob(f"us{num}_*.py"))
    for m in re.findall(r"`([\w/\.]+\.py)`", story["text"]):
        for base in (TOOLS, EVAL, PROJ):
            cand = base / m
            if cand.is_file():
                out.append(cand)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def citations(files: list[Path]) -> set[str]:
    """Every AC-n / TAC-n / T-n token appearing in the given files."""
    found: set[str] = set()
    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        found |= set(re.findall(r"\b(?:TAC|AC|T)-\d+\b", src))
    return found


def audit(story: dict) -> dict:
    files = evidence_files(story)
    cited = citations(files)
    uncovered_ac = [f"AC-{n}" for n in story["acs"] if f"AC-{n}" not in cited]
    uncovered_tac = [f"TAC-{n}" for n in story["tacs"] if f"TAC-{n}" not in cited]
    uncovered_lld = [f"T-{n}" for n in story["lld"] if f"T-{n}" not in cited]
    return {"files": files, "ac": uncovered_ac, "tac": uncovered_tac, "lld": uncovered_lld}


def main() -> int:
    ap = argparse.ArgumentParser(description="AC/TAC traceability check")
    ap.add_argument("--verbose", action="store_true", help="list covered ids too")
    args = ap.parse_args()

    print("=" * 76)
    print("Acceptance-criteria traceability -- does every criterion have a test?")
    print("=" * 76)

    stories = [parse_story(p) for p in story_files()]
    findings: list[str] = []
    total_ac = total_tac = total_lld = 0
    gaps_ac = gaps_tac = gaps_lld = 0

    print(f"\n  {'story':<8}{'status':<14}{'files':>6}{'AC un':>7}{'TAC un':>8}{'T un':>6}")
    print("  " + "-" * 70)
    for s in stories:
        r = audit(s)
        total_ac += len(s["acs"]); total_tac += len(s["tacs"]); total_lld += len(s["lld"])
        gaps_ac += len(r["ac"]); gaps_tac += len(r["tac"]); gaps_lld += len(r["lld"])
        flag = ""
        if s["status"].startswith(("IMPLEMENTED",)) and (r["ac"] or r["tac"]):
            flag = "  <-- FINDING"
            findings.append(
                f"{s['id']} ({s['status']}): "
                + ", ".join(r["ac"] + r["tac"]) + " cited by no test")
        print(f"  {s['id']:<8}{s['status'][:13]:<14}{len(r['files']):>6}"
              f"{len(r['ac']):>7}{len(r['tac']):>8}{len(r['lld']):>6}{flag}")
        if args.verbose and r["files"]:
            print(f"           files: {', '.join(f.name for f in r['files'])}")

    print(f"\n  totals: {total_ac} ACs, {total_tac} TACs, {total_lld} LLD scenarios")
    print(f"  untraced: {gaps_ac} ACs, {gaps_tac} TACs, {gaps_lld} LLD scenarios")

    # The gate. Only a story CLAIMING DELIVERY can fail it.
    check("no story claiming IMPLEMENTED leaves an AC or TAC untraced",
          not findings, f"{len(findings)} story(ies)")
    for f in findings:
        print(f"        {f}")

    # NEGATIVE CONTROL. If the scan cannot see an untraced criterion, a clean
    # result above means nothing.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fake_story = Path(td) / "US-999-planted.md"
        fake_story.write_text(
            "- **Status:** **IMPLEMENTED - planted**\n\n"
            "**AC-1.** Planted and covered.\n\n**AC-2.** Planted and NOT covered.\n\n"
            "- TAC-1: **Planted and not covered either.**\n", encoding="utf-8")
        fake_suite = TOOLS / "test_us999_planted.py"
        fake_suite.write_text('check("AC-1 planted", True)\n', encoding="utf-8")
        try:
            planted = parse_story(fake_story)
            planted["text"] = fake_story.read_text(encoding="utf-8")
            r = audit(planted)
            check("NEGATIVE CONTROL: an untraced AC in a planted story is caught",
                  r["ac"] == ["AC-2"], str(r["ac"]))
            check("NEGATIVE CONTROL: an untraced TAC in a planted story is caught",
                  r["tac"] == ["TAC-1"], str(r["tac"]))
            check("NEGATIVE CONTROL: and a traced AC is NOT reported",
                  "AC-1" not in r["ac"], str(r["ac"]))
        finally:
            fake_suite.unlink(missing_ok=True)

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
