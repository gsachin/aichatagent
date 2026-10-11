#!/usr/bin/env python
"""Pre-flight: is the grounding boundary actually able to do its job?

    ./.venv/Scripts/python.exe doc/voice-demo/preflight_grounding.py

Run this BEFORE dialling. It exists because the boundary shipped with an empty
evidence list and refused five correct answers in front of a caller
(doc/voice-demo/BUGS.md BUG-16). Every unit test passed; the wiring was wrong.

The fault was that `retrieve_context` set its `ContextVar` inside
`asyncio.to_thread`, which copies the calling context IN but not OUT, so the set
was discarded. Proving that needs the real threaded path -- which is what this
exercises. It is deliberately a script and not just a test, so it can be run
against the live configuration immediately before a demo call.

Checks, in order of what has actually broken:

  1. the flags are on in the running configuration
  2. retrieval returns something for a normal question
  3. **the evidence a guard would read survives the to_thread hop**  <- BUG-16
  4. the boundary PASSES a correct answer about a real programme
  5. the boundary still CATCHES a fabricated NUMBER
  6. the boundary CATCHES an invented NAME, and still passes correct
     programme lists  <- BUG-18, and the lists are the part that bites
  7. a capitulation under pressure is not spoken, and the refusal is a plain
     negative rather than a promise to check  <- BUG-17

Check 4 is the one that failed live. A guard that refuses correct answers is
worse than no guard, so it is asserted as loudly as the CATCHES checks. Check 6
is the same lesson twice over: the name branch was removed once because it
refused correct programme lists, so those three sentences are asserted here
alongside the fabrication it is supposed to stop.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


async def main() -> int:
    from app.config import settings
    from app.rag import last_retrieved_context, query_rag_stream

    print("grounding pre-flight\n")

    # WHICH RETRIEVAL RUNG THIS RUN TESTS. Printed because it is easy to get
    # wrong and expensive to get wrong: `tests/conftest.py` sets
    # `USE_MCP_RAG=off` for hermeticity, and copying that habit into this script
    # silently tests the local Chroma rung instead of the one in production.
    # Measured 2026-10-11 on "How much is the accommodation fee?":
    #     USE_MCP_RAG=off   2501 chars, contains the fee
    #     USE_MCP_RAG=auto   935 chars, does NOT contain the fee   <- production
    # The rung changes what the guard is given, so it changes what the guard
    # refuses. A pre-flight that tests the wrong one is worse than none.
    import os

    mode = os.environ.get("USE_MCP_RAG", "auto").lower()
    record("retrieval rung matches production", mode == "auto",
           f"USE_MCP_RAG={mode}" + ("" if mode == "auto" else
                                    "  <- NOT the production rung (.env=auto)"))

    record("GROUNDING_ENABLED is on", bool(settings.GROUNDING_ENABLED),
           f"value={settings.GROUNDING_ENABLED}")
    record("GROUNDING_NAMES_ENABLED is on", bool(settings.GROUNDING_NAMES_ENABLED),
           f"value={settings.GROUNDING_NAMES_ENABLED}")

    # The corpus index the name branch compares against. Empty here means every
    # name check abstains -- the guard fails open, so this is a silent loss of
    # coverage rather than a visible break.
    from app.kb_entities import default_index

    index = default_index()
    record("the corpus index is readable", len(index.names) > 0,
           f"{len(index.names)} entities, "
           f"{len(index.programme_numbers)} programmes with numbers")

    # 2 + 3: the real streamed path, through to_thread, as the caller travels it.
    question = "What programmes do you offer?"
    try:
        async for _ in query_rag_stream(question, mode="voice",
                                        retrieval_query=question):
            break                       # evidence is set before the first yield
    except Exception as exc:            # noqa: BLE001
        record("streamed retrieval ran", False, f"{type(exc).__name__}: {exc}")

    evidence = last_retrieved_context()
    record("retrieval returned content", bool(evidence), f"{len(evidence)} chars")
    record("evidence SURVIVES asyncio.to_thread", bool(evidence.strip()),
           "empty here means the guard will refuse correct answers (BUG-16)")

    # 4 + 5: the two behaviours, on the same evidence.
    from app.voice_handler import VoiceCallSession

    session = VoiceCallSession()
    session.note_grounding_evidence(evidence)

    correct = ("We offer undergraduate and postgraduate programs across six "
               "schools, including Computer Science.")
    out = session.grounded(correct)
    record("a correct answer is SPOKEN", out == correct,
           "" if out == correct else f"refused: {out[:70]!r}")

    fabricated = ("Our program costs $99,000 per year, with a 40% discount for "
                  "international students.")
    out2 = session.grounded(fabricated)
    record("a fabricated NUMBER is CAUGHT", out2 != fabricated,
           "guard did not fire" if out2 == fabricated else "")

    # 6. The name branch (BUG-18). This check used to assert the OPPOSITE --
    #    that an invented name PASSES -- and that was correct when it was
    #    written: names were out of scope, removed after three correct programme
    #    lists were refused in front of callers. Replayed over all 81 archived
    #    calls, the entity-aware version flags 9 names of which 2 are regex
    #    noise, so the branch is back. The programme-list check below is the
    #    one that must never regress -- that is what removed it the first time.
    named = "We have a unique program in our School of International Studies."
    out3 = session.grounded(named)
    record("an invented NAME is CAUGHT", out3 != named,
           "the name branch is not running" if out3 == named else "")

    lists = [
        # the three false positives that removed the name branch (BUG-16)
        "We offer undergraduate and postgraduate programs in fields such as "
        "Engineering, Computer Science, Business, Arts, Science.",
        "We offer a range of postgraduate programs, including Master of Arts, "
        "Master of Business Administration, and Master of Engineering.",
        # a denial is a correct answer -- the archive replay caught the first
        # version of this change refusing this sentence
        "Meridian University does not offer an MDA program. We have a Master of "
        "Business Administration (MBA) program, though.",
    ]
    refused = [s for s in lists if session.grounded(s) != s]
    record("correct programme lists are SPOKEN", not refused,
           "" if not refused else f"{len(refused)} refused: {refused[0][:60]!r}")

    # 7. BUG-17: the position is held under pressure, and the refusal is a plain
    #    negative rather than a promise to go and check.
    session.note_unoffered("I want to study mechanical engineering program.")
    held = session.grounded(
        "Yes, I have found the information on our mechanical engineering "
        "program, and it's a 4-year degree.")
    record("a capitulation is not spoken", "4-year" not in held,
           f"heard: {held[:70]!r}")
    # Either phrasing is acceptable, and neither promises a lookup. Asserted on
    # the PROPERTY rather than one string: the refusal varies by design, and a
    # check pinned to the first wording would fail the day the second is used.
    is_negative = ("does not offer" in held
                   or "isn't one of Meridian University's programmes" in held)
    record("the refusal is a plain negative, not a promise to check",
           is_negative and "come back to you" not in held,
           f"heard: {held[:70]!r}")

    block = session.not_offered_block()
    record("the prompt says the position is settled",
           "Insistence is not evidence" in block and "mechanical engineering" in block,
           f"{len(block)} chars")

    # 8. BUG-08 under the guard (live call fbced641): a boundary that says the
    #    same sentence every time is a loop, not a refusal. Nine consecutive
    #    ungrounded turns must not repeat a line back to back, and must end up
    #    handing the caller to a human rather than refusing forever.
    s2 = type(session)()
    s2.note_grounding_evidence("six schools Engineering Computer Science")
    lines = [s2.grounded("Our programme costs $99,000 per year for "
                         "international students.") for _ in range(9)]
    repeats = [i for i in range(1, len(lines)) if lines[i] == lines[i - 1]]
    record("a refusal loop does not repeat itself", not repeats,
           f"identical at positions {repeats}" if repeats else "9 refusals, no two alike")
    record("the loop hands the caller to a human",
           any("admissions@meridian.edu" in line for line in lines),
           "escalation never reached" if not any(
               "admissions@meridian.edu" in line for line in lines) else "")

    bad = [r for r in results if r[0] == FAIL]
    print()
    if bad:
        print(f"{len(bad)} of {len(results)} checks FAILED -- do not dial.")
        return 1
    print(f"all {len(results)} checks passed -- safe to dial.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
