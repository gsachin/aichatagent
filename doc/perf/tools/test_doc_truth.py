"""Does the document still describe the artifact?

A status line is a claim written once and read forever, while the thing it
describes keeps changing. On 2026-09-19 that produced five stale lines in a
single audit -- US-001 claiming 17/17 when the suite ran 24, US-013 claiming
39/39 when it ran 46, US-007 and US-012 and US-006 each quoting a DoD count
their own section contradicted. Three of the five were drift introduced the same
day, by ticking a DoD box without updating the line that quotes the count.

Nothing was wrong with any of those stories. The documents had simply stopped
being true, and no mechanism noticed.

This file notices. It checks, for every story:

  1. a status line exists at all
  2. its status keyword is one this programme uses
  3. its `DoD n/m` equals a mechanical count of the tick boxes in its own DoD
     section -- **statically, always**
  4. the suite it names exists
  5. with `--run`, its `n/m` equals that suite's actual output

WHY 3 AND 5 ARE DIFFERENT. The DoD count can be verified without executing
anything, so it is checked on every run. The suite count cannot: it is a claim
about what a program printed, and the only honest way to check it is to run the
program. So `--run` executes each named suite and compares. It is off by default
because it takes minutes and because `test_us006_residency` mutates live engine
state (it unloads and reloads the model), which is not something a documentation
check should do behind your back.

A suite that cannot produce a summary line -- it hung, it needs a service that is
down, it errored before finishing -- is reported **UNVERIFIED**, never as a pass
and never as a failure. The absence of a reading is not a reading of zero, which
is the same rule `MOD-06` applies to a stage mark that never fired.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_doc_truth.py
      .venv/Scripts/python.exe doc/perf/tools/test_doc_truth.py --run
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
os.chdir(PROJ)

STORIES = PROJ / "doc" / "sdlc" / "stories"
TOOLS = PROJ / "doc" / "perf" / "tools"

#: Capture the WHOLE status line, not just the bold status word.
#:
#: The first version of this file used `\*\*([^*]+)\*\*`, which stops at the
#: closing `**` -- so the DoD and suite claims, which sit *after* the bold, were
#: never in the string being searched. Every claim check then found nothing,
#: skipped, and PASSED. The column printed empty and the gate was green.
#:
#: That is the third silent-instrument failure in this session: the US-012
#: isolation test reporting PASS on zero keys, `STATUS_RE` in
#: test_ac_traceability.py missing `re.M`, and this. All three passed while
#: blind. The lesson is not "write better regexes" -- it is that a checker must
#: assert it found something, not merely that it found nothing wrong.
STATUS_RE = re.compile(r"^- \*\*Status:\*\* (.*)$", re.M)
STATUS_WORD_RE = re.compile(r"\*\*([^*]+)\*\*")
DOD_CLAIM_RE = re.compile(r"DoD\s+(\d+)\s*/\s*(\d+)")
SUITE_CLAIM_RE = re.compile(r"`?(test_\w+\.py)`?\s*(\d+)\s*/\s*(\d+)")
SUMMARY_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")

#: The programme's status vocabulary. A word outside it is not a status this
#: register can count, so it fails rather than being silently skipped.
VOCAB = ("IMPLEMENTED", "PARTIAL", "BLOCKED", "AWAITING", "IN PROGRESS", "NOT STARTED")

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def truth(path: Path) -> tuple[int, int]:
    """(ticked, total) from the story's own DoD section, counted mechanically."""
    body = re.split(r"\n## ", path.read_text(encoding="utf-8").split("## Definition of Done")[-1])[0]
    boxes = re.findall(r"^- \[( |x)\]", body, re.M)
    return sum(1 for b in boxes if b == "x"), len(boxes)


def run_suite(name: str, timeout: int = 180) -> tuple[int, int] | None:
    """Execute a suite and return (passed, failed), or None if it gave no summary."""
    exe = PROJ / ".venv" / "Scripts" / "python.exe"
    try:
        p = subprocess.run([str(exe), str(TOOLS / name)], capture_output=True,
                           text=True, timeout=timeout, cwd=str(PROJ))
    except subprocess.TimeoutExpired:
        return None
    m = None
    for m in SUMMARY_RE.finditer(p.stdout or ""):
        pass
    return (int(m.group(1)), int(m.group(2))) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Do documents still describe the artifacts?")
    ap.add_argument("--run", action="store_true",
                    help="also execute each named suite and compare its counts "
                         "(minutes; one suite mutates live engine state)")
    args = ap.parse_args()

    print("=" * 76)
    print("Document vs artifact -- do the status lines still describe the code?")
    print("=" * 76)

    stories = sorted(STORIES.glob("US-*.md"))
    missing_status, bad_vocab = [], []
    dod_drift, suite_missing, count_drift, unverified = [], [], [], []

    print(f"\n  {'story':<8}{'DoD claim':>11}{'DoD real':>10}{'suite':<32}{'claim':>8}")
    print("  " + "-" * 70)
    for p in stories:
        sid = re.match(r"(US-\d+)", p.name).group(1)
        text = p.read_text(encoding="utf-8")
        head = text.split("## ", 1)[0]

        m = STATUS_RE.search(head)
        if not m:
            missing_status.append(sid)
            print(f"  {sid:<8}{'—':>11}{'—':>10}{'(no status line)':<32}")
            continue
        line = m.group(1).strip()
        word = (STATUS_WORD_RE.search(line) or [None, ""])[1].strip()

        # A status line whose bold word is unreadable would silently skip the
        # vocabulary check below, which is how this file passed while blind.
        if not word:
            bad_vocab.append(f"{sid}: no bold status word")
        elif not word.upper().startswith(VOCAB):
            bad_vocab.append(f"{sid}: {word!r}")

        ticked, total = truth(p)
        dc = DOD_CLAIM_RE.search(line)
        claim_str = f"{dc.group(1)}/{dc.group(2)}" if dc else "—"
        real_str = f"{ticked}/{total}"
        if dc and (int(dc.group(1)), int(dc.group(2))) != (ticked, total):
            dod_drift.append(f"{sid}: claims DoD {claim_str}, section holds {real_str}")

        sc = SUITE_CLAIM_RE.search(line)
        if sc:
            sname, cp, ct = sc.group(1), int(sc.group(2)), int(sc.group(3))
            sclaim = f"{cp}/{ct}"
            exists = (TOOLS / sname).is_file()
            if not exists:
                suite_missing.append(f"{sid}: names {sname}, which does not exist")
                print(f"  {sid:<8}{claim_str:>11}{real_str:>10}{sname:<32}{sclaim:>8}  MISSING")
                continue
            note = ""
            if args.run and sname != Path(__file__).name:
                got = run_suite(sname)
                if got is None:
                    unverified.append(f"{sid}: {sname} produced no summary")
                    note = "UNVERIFIED"
                elif got[0] != cp or (got[0] + got[1]) != ct:
                    count_drift.append(
                        f"{sid}: claims {sclaim} for {sname}, it runs {got[0]}/{got[0] + got[1]}")
                    note = f"actual {got[0]}/{got[0] + got[1]}"
            print(f"  {sid:<8}{claim_str:>11}{real_str:>10}{sname:<32}{sclaim:>8}  {note}")
        else:
            print(f"  {sid:<8}{claim_str:>11}{real_str:>10}{'(none named)':<32}")

    print(f"\n  {len(stories)} stories")
    check("every story carries a status line", not missing_status, str(missing_status))
    check("every status keyword is in the programme's vocabulary",
          not bad_vocab, str(bad_vocab))
    check("every DoD count in a status line equals its own section",
          not dod_drift, f"{len(dod_drift)} drifted")
    for d in dod_drift:
        print(f"        {d}")
    check("every named suite exists", not suite_missing, str(suite_missing))
    if args.run:
        check("every quoted suite count equals the suite's actual output",
              not count_drift, f"{len(count_drift)} drifted")
        for d in count_drift:
            print(f"        {d}")
        if unverified:
            print(f"  NOTE  {len(unverified)} suite(s) UNVERIFIED — no summary line:")
            for u in unverified:
                print(f"        {u}")
    else:
        print("  SKIP  suite counts not compared (pass --run to execute them)")

    # NEGATIVE CONTROL. A stale claim must be visible, or a clean run means nothing.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        planted = Path(td) / "US-999-planted.md"
        planted.write_text(
            "- **Status:** **IMPLEMENTED - planted** · `test_planted.py` 99/99 · DoD 7/7\n\n"
            "## Definition of Done\n- [x] one\n- [ ] two\n", encoding="utf-8")
        t = planted.read_text(encoding="utf-8")
        line = STATUS_RE.search(t.split("## ", 1)[0]).group(1)
        dc = DOD_CLAIM_RE.search(line)
        ticked, total = truth(planted)
        check("NEGATIVE CONTROL: a planted DoD drift is detected",
              (int(dc.group(1)), int(dc.group(2))) != (ticked, total),
              f"claim {dc.group(1)}/{dc.group(2)} vs real {ticked}/{total}")
        sc = SUITE_CLAIM_RE.search(line)
        check("NEGATIVE CONTROL: a planted suite-name drift is detected",
              sc is not None and not (TOOLS / sc.group(1)).is_file(),
              f"named {sc.group(1) if sc else None}")

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
