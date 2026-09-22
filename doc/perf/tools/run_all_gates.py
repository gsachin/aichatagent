"""Phase 6 -- one command, one scoreboard: every gate in the drive's order.

The drive's exit criterion is "all gates exit 0". Before this script that
meant running eleven things by hand and reading eleven summaries, so in
practice it meant nobody ran all of them, and a red gate could sit unnoticed
for days (two were red when this was written). This runs the whole set and
prints one table.

Order is the plan's, cheapest-first within a group, so a fast failure is
visible before the slow suites start.

  .venv/Scripts/python.exe doc/perf/tools/run_all_gates.py
  .venv/Scripts/python.exe doc/perf/tools/run_all_gates.py --only us018
  .venv/Scripts/python.exe doc/perf/tools/run_all_gates.py --list
  .venv/Scripts/python.exe doc/perf/tools/run_all_gates.py --live     # needs the stack

WHAT THIS DELIBERATELY DOES NOT DO: suppress known failures. The harness's
framing audit is flaky on this host and six pytest tests fail for
environmental reasons, and it is tempting to whitelist them so the board goes
green. That is how a scoreboard stops meaning anything. Instead, a red is
always a red, and `KNOWN` below only *annotates* it -- and only when the
failure counts match the recorded ones EXACTLY, so a known red that gets worse
stops being known. `--allow-known` turns those exact matches into a pass; it is
off by default.

Exit: 0 iff every gate that ran is green (or exactly-known under
`--allow-known`). Skips never fail the run.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
TOOLS = PROJ / "doc" / "perf" / "tools"
PY = Path(sys.executable)

#: A gate is (label, argv, needs_live). `argv` is relative to the repo root.
#: `needs_live` gates are skipped unless --live is passed: they want the full
#: stack resident (Ollama + app + whisper/kokoro in-process) and a quiet box,
#: so they are not part of a desk check.
GATES: list[tuple[str, list[str], bool]] = [
    ("traceability", ["doc/perf/tools/test_ac_traceability.py"], False),
    ("doc truth", ["doc/perf/tools/test_doc_truth.py", "--run"], False),
    ("doc citations", ["doc/perf/tools/test_doc_citations.py"], False),
    ("BRD-15 rollback", ["doc/perf/tools/test_brd15_rollback.py"], False),
    ("event-loop hygiene", ["doc/perf/tools/test_event_loop_hygiene.py"], False),
    ("endpointing", ["doc/perf/tools/test_endpointing.py"], False),
    ("harness self-test", ["doc/perf/tools/load_harness.py", "--self-test-quick"], False),
]

#: Named gate -> (passed, failed, reason). Only an EXACT count match is
#: treated as known; anything else is an ordinary red. Kept empty rather than
#: guessed -- an unverified number here is the same defect this suite hunts.
KNOWN: dict[str, tuple[int, int, str]] = {}

SUMMARY_RE = re.compile(r"(\d+)\s+passed")
FAILED_RE = re.compile(r"(\d+)\s+failed")
SKIPPED_RE = re.compile(r"(\d+)\s+skipped")
SELFTEST_RE = re.compile(r"self-test:\s*(PASS|FAIL)")
FAIL_LINE_RE = re.compile(r"^\s*FAIL\s+(.*)$", re.M)
#: pytest announces failures as `FAILED path::test - reason`, never the
#: suites' indented `FAIL  name`, so a bare exit code was all the board could
#: say about the largest suite in the repo.
PYTEST_FAIL_RE = re.compile(r"^(?:FAILED|ERROR)\s+(.*)$", re.M)


def parse_counts(out: str) -> tuple[int, int, int] | None:
    """(passed, failed, skipped) from a suite's summary line, or None.

    Both orders occur in this repo: the gates print `N passed, M failed` and
    pytest prints `M failed, N passed, K warnings in 1.2s`. So scan every line
    that mentions either word and take the last, rather than assuming a shape.
    """
    best: tuple[int, int, int] | None = None
    for line in out.splitlines():
        if "passed" not in line and "failed" not in line:
            continue
        p = SUMMARY_RE.search(line)
        f = FAILED_RE.search(line)
        if p is None and f is None:
            continue
        s = SKIPPED_RE.search(line)
        best = (int(p.group(1)) if p else 0,
                int(f.group(1)) if f else 0,
                int(s.group(1)) if s else 0)
    return best


def run_gate(label: str, argv: list[str], timeout: int) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run([str(PY), *argv], cwd=PROJ, capture_output=True,
                              text=True, errors="replace", timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes)
               else (exc.stdout or ""))
        rc, timed_out = 124, True

    counts = parse_counts(out)
    self_test = SELFTEST_RE.search(out)
    if self_test is not None:
        ok = self_test.group(1) == "PASS" and rc == 0
    else:
        ok = rc == 0

    secs = time.monotonic() - started
    fails = [m.group(1).strip() for m in FAIL_LINE_RE.finditer(out)]
    if not fails:
        fails = [m.group(1).strip() for m in PYTEST_FAIL_RE.finditer(out)]

    return {
        "label": label, "argv": argv, "rc": rc, "ok": ok, "counts": counts,
        "timed_out": timed_out, "secs": secs,
        # A duration longer than the gate's own timeout is not a duration.
        # subprocess.run enforces that timeout on the same clock, so exceeding
        # it is impossible for a gate that returned normally -- the board once
        # printed 19,909 s for a gate that measures 113 s, which only a
        # suspend/resume between the two reads accounts for. Flag it rather
        # than print an impossible number in a column that reads as measured.
        "secs_suspect": secs > timeout,
        "fails": fails[:3],
        "out": out,
    }


def story_suites() -> list[tuple[str, list[str], bool]]:
    return [(p.name, [f"doc/perf/tools/{p.name}"], False)
            for p in sorted(TOOLS.glob("test_us*.py"))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default=None,
                    help="run only gates whose label or argv contains this")
    ap.add_argument("--list", action="store_true", help="list gates and exit")
    ap.add_argument("--live", action="store_true",
                    help="also run the stack-dependent gates (needs a quiet box)")
    ap.add_argument("--timeout", type=int, default=900, help="per gate, seconds")
    ap.add_argument("--allow-known", action="store_true",
                    help="exit 0 when the only reds are EXACT known-failure matches")
    args = ap.parse_args()

    gates = list(GATES) + story_suites() + [
        ("pytest tests/", ["-m", "pytest", "tests/", "-q"], False),
    ]

    if args.list:
        for label, argv, live in gates:
            print(f"  {'LIVE ' if live else '     '}{label:<34} {' '.join(argv)}")
        return 0

    if args.only:
        gates = [g for g in gates
                 if args.only.lower() in g[0].lower()
                 or any(args.only.lower() in a.lower() for a in g[1])]
        if not gates:
            print(f"no gate matches {args.only!r}", file=sys.stderr)
            return 2

    results: list[dict] = []
    skipped: list[str] = []
    for label, argv, live in gates:
        if live and not args.live:
            skipped.append(label)
            continue
        print(f"  ... {label}", flush=True)
        results.append(run_gate(label, argv, args.timeout))

    # ── scoreboard ─────────────────────────────────────────────────────────
    width = max((len(r["label"]) for r in results), default=10)
    print()
    print("=" * 78)
    print("  SDLC COMPLETION DRIVE -- ALL GATES")
    print("=" * 78)
    print(f"  {'GATE':<{width}}  {'RESULT':<18} {'CHECKS':>13}  SECS")
    print("-" * 78)

    tp = tf = 0
    reds: list[dict] = []
    known_reds: list[dict] = []
    for r in results:
        c = r["counts"]
        checks = f"{c[0]}/{c[0] + c[1]}" if c else "--"
        if c:
            tp += c[0]
            tf += c[1]

        note = ""
        known = KNOWN.get(r["label"])
        if not r["ok"] and known and c and (c[0], c[1]) == known[:2]:
            note = "  [known]"
            known_reds.append(r)
        elif not r["ok"]:
            reds.append(r)

        result = "PASS" if r["ok"] else ("TIMEOUT" if r["timed_out"] else "FAIL")
        secs_txt = f"{r['secs']:.1f}" + ("?" if r["secs_suspect"] else "")
        print(f"  {r['label']:<{width}}  {result + note:<18} {checks:>13}  {secs_txt}")

    print("-" * 78)
    n_pass = sum(1 for r in results if r["ok"])
    print(f"  {n_pass} pass, {len(reds)} fail, {len(known_reds)} known-fail"
          f"   |   checks {tp} passed, {tf} failed")

    if reds:
        print("\n  FAILURES -- first lines of each:")
        for r in reds:
            head = r["fails"][0] if r["fails"] else f"exit {r['rc']}"
            print(f"    {r['label']}: {head}")
    if known_reds:
        print("\n  KNOWN (exact-count match; not suppressed, and a worse count "
              "would make them ordinary reds):")
        for r in known_reds:
            print(f"    {r['label']}: {KNOWN[r['label']][2]}")
    if skipped:
        print(f"\n  SKIPPED (need --live and a quiet box): {', '.join(skipped)}")

    suspect = [r["label"] for r in results if r["secs_suspect"]]
    if suspect:
        print("\n  '?' marks a duration exceeding that gate's own --timeout, which "
              "subprocess.run\n      enforces on the same clock -- so the host "
              "suspended mid-gate and the\n      number is not a runtime. The "
              "verdict is unaffected. Gates: " + ", ".join(suspect))
    print()

    if reds:
        return 1
    if known_reds and not args.allow_known:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
