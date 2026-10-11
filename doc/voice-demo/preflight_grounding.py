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

  1. the flag is on in the running configuration
  2. retrieval returns something for a normal question
  3. **the evidence a guard would read survives the to_thread hop**  <- BUG-16
  4. the boundary PASSES a correct answer about a real programme
  5. the boundary still CATCHES a fabricated one

Check 4 is the one that failed live. A guard that refuses correct answers is
worse than no guard, so it is asserted as loudly as check 5.
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

    record("GROUNDING_ENABLED is on", bool(settings.GROUNDING_ENABLED),
           f"value={settings.GROUNDING_ENABLED}")

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

    # The narrowed scope, asserted so a future reader does not mistake the
    # absence of name-checking for a bug. Names were removed deliberately: on
    # live calls the name branch produced 0 true positives and 3 false refusals.
    named = "We have a unique program in our School of International Studies."
    name_passes = session.grounded(named) == named
    record("an invented NAME passes (deliberate scope)", name_passes,
           "as designed -- names are out of scope, see BUGS.md BUG-07"
           if name_passes else
           "UNEXPECTED: a name was refused, the name branch is back")

    bad = [r for r in results if r[0] == FAIL]
    print()
    if bad:
        print(f"{len(bad)} of {len(results)} checks FAILED -- do not dial.")
        return 1
    print(f"all {len(results)} checks passed -- safe to dial.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
