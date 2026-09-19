"""Do the module docs' code citations still point at the code they name?

Module docs cite the implementation the way a review comment does:
`voice_handler.py:675`, `llm_backend.py:135–175`. A line number is a claim about
a moving target, and the code moves.

Two failures this is written for, both found in the 2026-09-19 audit:

  * `MOD-03` B.3 asserted "keep-alive applied on the serving path" while B.8
    recorded that `keep_alive` was passed only in the boot ping. The two rows
    disagreed and nothing noticed.
  * `US-012`'s story cites `voice_handler.py:666, 675, 681` for the copy
    discipline and the cache key. Those lines now point at `feed_audio` and the
    VAD code; the copy discipline moved to `:1099` and `:1121`.

WHAT THIS CHECKS, AND WHAT IT CANNOT. For each citation it looks for a
backticked identifier on the same line of prose, then asks whether that
identifier appears within a window of the cited lines. Three outcomes, and the
distinction matters more than the check:

  * **OK** -- a named symbol found near the cited line. The citation still
    points where it says.
  * **DRIFTED** -- identifiers are named, none is near the cited line. The code
    moved and the citation did not.
  * **UNVERIFIED** -- no identifier named on the line, so there is nothing to
    check the number against. Many citations are like this: `voice_handler.py:301`
    sits beside prose reading "30 frames x 20 ms" and never names a symbol.

UNVERIFIED is not a pass. It is the honest answer for "this citation cannot be
checked mechanically", and it is reported separately so a reader can see the
difference between a citation that was verified and one that was never
checkable. Counting the second as the first is how a programme reports coverage
it does not have.

The window is a heuristic and is stated rather than hidden: a symbol within
WINDOW lines above or below the cited range counts as near. Widen it and the
check stops catching drift; narrow it and it cries wolf on citations that point
at a function's middle. 20 lines is chosen to catch the real failures above -- the
US-012 case had moved 400+ lines -- while tolerating a citation that lands
inside a long function.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_doc_citations.py
      .venv/Scripts/python.exe doc/perf/tools/test_doc_citations.py --verbose
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
os.chdir(PROJ)

MODULES = PROJ / "doc" / "sdlc" / "modules"
STORIES = PROJ / "doc" / "sdlc" / "stories"

#: `path.py:123` or `path.py:123–145` (en-dash) or `path.py:123-145`.
CITE_RE = re.compile(r"`?([A-Za-z_][\w/]*\.py):(\d+)(?:\s*[–\-]\s*(\d+))?`?")

#: Backticked lowercase identifiers: `emit`, `_synthesise`, `llm_backend.chat`.
IDENT_RE = re.compile(r"`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)`")

#: Backticks are not a symbol marker. Prose, literals and fragments wear them
#: too, and the first run of this file produced findings like "names en" (from
#: `small.en`, a Whisper model name) and "names false, txt, py". A finding built
#: on one of those would be wrong, so they are excluded by shape: a symbol is at
#: least three characters and is not a bare literal.
LITERALS = {"true", "false", "none", "null", "py", "txt", "json", "int", "str",
            "yes", "no", "on", "off", "any", "all"}


def symbols_in(line: str) -> set[str]:
    out = set()
    for tok in IDENT_RE.findall(line):
        last = tok.split(".")[-1]
        if len(last) >= 3 and last.lower() not in LITERALS:
            out.add(last)
    return out


#: Cited files that live in another repository. The module docs legitimately
#: reference the enterprise-rag-core tree, which is not in this one; reporting
#: them as "file not found" would be a false finding on every run.
EXTERNAL_ROOTS = (Path("D:/project/enterprise-rag-core"),)

#: Programme ids wear backticks too — `DAT-04`, `REC-10`, `BRD-06` — and are not
#: symbols. They are excluded by the lowercase-only rule above, not by a list.
WINDOW = 20

#: How close a backticked symbol must sit to a citation in the prose to be read
#: as explaining it, in characters. Wide enough for "the only caller of
#: `create_local_voice_pipeline` is a test script" after a citation; narrow
#: enough to exclude a symbol named later in the same paragraph for another
#: reason.
PROXIMITY = 90

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def resolve(name: str) -> Path | None:
    """Resolve a cited filename to a real path.

    Docs cite both `app/main.py:466` and bare `main.py:466`. A bare name is
    resolved by searching the app tree rather than by guessing a prefix, so a
    file that moved between packages is still found -- or reported missing.
    """
    direct = PROJ / name
    if direct.is_file():
        return direct
    hits = sorted((PROJ / "app").rglob(Path(name).name))
    hits = [h for h in hits if "__pycache__" not in str(h)]
    return hits[0] if hits else None


def definition_lines(src: list[str], name: str) -> list[int]:
    """Lines where `src` DEFINES `name` as a function or a class.

    Two deliberate restrictions, each removing a false finding this file
    produced before it had them:

    Mentioning a name is not defining it. `prompt_eval_count` is mentioned in a
    dozen places and defined nowhere -- it is an engine response field -- so
    only definition sites are evidence about where a symbol lives.

    And an ASSIGNMENT is not a symbol either. Matching `name =` flagged
    `MOD-07:92`, where `` `applied` `` is a field name inside
    `.machine_profile.json` and the match was a local variable at line 520 that
    has nothing to do with the citation. Data keys and locals wear the same
    shape as callables, so the check keeps to `def` and `class`, where the name
    is unambiguously the thing a citation points at.
    """
    pat = re.compile(
        rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\b"
        rf"|^\s*class\s+{re.escape(name)}\b")
    return [n for n, l in enumerate(src, 1) if pat.search(l)]


def audit_doc(path: Path) -> tuple[list[str], int, int, set[str]]:
    """Return (drifted, ok_count, unverified_count, dangling_names)."""
    drifted: list[str] = []
    ok = unv = 0
    dangling: set[str] = set()
    lines = path.read_text(encoding="utf-8").splitlines()

    for i, line in enumerate(lines, 1):
        for m in CITE_RE.finditer(line):
            name, start = m.group(1), int(m.group(2))
            end = int(m.group(3)) if m.group(3) else start
            target = resolve(name)
            where = f"{path.name}:{i} -> {name}:{start}" + (f"-{end}" if end != start else "")

            if target is None:
                ext = any(any(r.rglob(Path(name).name)) for r in EXTERNAL_ROOTS if r.is_dir())
                if ext:
                    unv += 1          # cited, real, but not auditable from here
                else:
                    drifted.append(f"{where}  [file not found in this repository]")
                continue
            src = target.read_text(encoding="utf-8", errors="replace").splitlines()
            if start > len(src):
                drifted.append(f"{where}  [file has only {len(src)} lines]")
                continue

            # Symbols named NEAR this citation in the sentence.
            #
            # Proximity is required, not decoration. US-012's DoD line names
            # `_cache_key_sha` (which lives at 191) and separately cites
            # `voice_handler.py:1070` for the cache key. Checking every symbol on
            # the line against every citation on it flagged that as drift when
            # the citation was correct -- a false finding produced by reading a
            # sentence as a bag of words. A symbol explains a citation when it
            # sits beside it in the prose, so that is what is checked.
            idents = {s for s in symbols_in(line)
                      if abs(line.find(s) - m.start()) <= PROXIMITY}
            if not idents:
                unv += 1
                continue

            # Locate by DEFINITION, not by mention. A citation claims the code
            # it names lives at that line, so the question is where the file
            # defines it. Mention-matching produced findings like "names
            # langchain; found at 411, 503" -- an import in the region, not the
            # symbol the prose was about.
            defined = {x: definition_lines(src, x) for x in idents}
            defined = {x: v for x, v in defined.items() if v}
            dangling |= {x for x in idents if not defined.get(x)}

            if not defined:
                unv += 1
                continue

            lo, hi = max(1, start - WINDOW), min(len(src), end + WINDOW)
            if any(any(lo <= n <= hi for n in v) for v in defined.values()):
                ok += 1
            else:
                loc = ", ".join(f"{x} defined at {v[0]}" for x, v in sorted(defined.items()))
                drifted.append(f"{where}  [{loc}; cited range is {lo}-{hi}]")
    return drifted, ok, unv, dangling


def main() -> int:
    ap = argparse.ArgumentParser(description="Do code citations still point at the code?")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--docs", default=None,
                    help="comma-separated glob dirs (default: modules + stories)")
    args = ap.parse_args()

    print("=" * 76)
    print("Code citations -- do `file.py:LINE` references still point at the symbol?")
    print("=" * 76)

    docs: list[Path] = []
    for d in (MODULES, STORIES):
        docs += sorted(d.glob("*.md"))

    all_drift, tot_ok, tot_unv, all_dangling = [], 0, 0, set()
    print(f"\n  {'document':<46}{'drifted':>8}{'ok':>6}{'unverif':>9}")
    print("  " + "-" * 70)
    for doc in docs:
        drifted, ok, unv, dangling = audit_doc(doc)
        tot_ok += ok
        tot_unv += unv
        all_dangling |= dangling
        all_drift += [f"{doc.name}: {d}" for d in drifted]
        if drifted or args.verbose:
            print(f"  {doc.name:<46}{len(drifted):>8}{ok:>6}{unv:>9}")

    print(f"\n  {tot_ok} citations verified, {tot_unv} unverifiable "
          f"(no symbol named on the line), {len(all_drift)} drifted")
    print(f"  WINDOW = {WINDOW} lines either side of the cited range")

    check("no citation points at a file, a line, or a definition that is not "
          "where it says", not all_drift, f"{len(all_drift)} drifted")
    for d in all_drift:
        print(f"        {d}")

    if all_dangling:
        print(f"\n  ADVISORY -- {len(all_dangling)} backticked name(s) the cited "
              f"file never defines.")
        print("  Not failed on: prose wears backticks too, and a name that is an "
              "engine field or a package is not a locator.")
        for d in sorted(all_dangling)[:12]:
            print(f"        {d}")

    # NEGATIVE CONTROL. A stale citation must be visible.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "planted.py"
        # `def`, because an assignment is not a symbol this check accepts -- its
        # own negative control has to satisfy the rules the check applies.
        target.write_text("\n".join(f"def line_{n}(): pass" for n in range(1, 61)),
                          encoding="utf-8")
        doc = Path(td) / "PLANTED.md"
        doc.write_text("- Real: `planted.py:10` names `line_10`.\n"
                       "- Drifted: `planted.py:55` names `line_10`, which is defined at 10.\n"
                       "- Past EOF: `planted.py:900` names `line_10`.\n"
                       "- Dangling: `planted.py:20` names `never_defined_anywhere`.\n",
                       encoding="utf-8")
        old_proj = globals()["PROJ"]
        try:
            globals()["PROJ"] = Path(td)
            drifted, ok, unv, dang = audit_doc(doc)
        finally:
            globals()["PROJ"] = old_proj
        joined = " | ".join(drifted)
        check("NEGATIVE CONTROL: a defined symbol cited far from its definition is caught",
              "line_10 defined at 10" in joined, joined)
        check("NEGATIVE CONTROL: a line past end-of-file is caught",
              "only 60 lines" in joined, joined)
        check("NEGATIVE CONTROL: and a correct citation is NOT flagged",
              ok >= 1 and not any("planted.py:10 " in d for d in drifted),
              f"ok={ok} drifted={drifted}")
        check("NEGATIVE CONTROL: a name the file never defines is ADVISORY, not "
              "drift -- prose wears backticks too",
              "never_defined_anywhere" not in joined and "never_defined_anywhere" in dang,
              f"dangling={sorted(dang)}")

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
