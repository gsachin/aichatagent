#!/usr/bin/env python
"""Archive gate: replay every archived call through the grounding boundary.

    ./.venv/Scripts/python.exe doc/voice-demo/replay_grounding.py
    ./.venv/Scripts/python.exe doc/voice-demo/replay_grounding.py --no-evidence
    ./.venv/Scripts/python.exe doc/voice-demo/replay_grounding.py --calls 10 --show

Why this exists
---------------
The name branch was removed once on evidence that turned out to be contaminated:
the measurements came from the same calls as the BUG-16 wiring fault, which left
the evidence list empty, so EVERYTHING was refused and the name branch was
indicted along with everything else (doc/voice-demo/BUGS.md, BUG-16 -> BUG-18).

Re-instating it needs the opposite evidence, and needs it in BOTH directions:

  * the fabrications must be caught  (BUG-07, and the call that reopened this)
  * correct answers must NOT be refused  (BUG-16, and it is the louder of the two)

The unit suite asserts both on a dozen hand-written sentences. This runs the
same boundary over every agent turn in the `conversations` table, which is the
only set of sentences that were ever actually spoken to a caller.

Two modes
---------
--no-evidence (fast, seconds)
    Uses the corpus as the context for every turn. Names are judged against the
    corpus index and do not depend on the evidence at all, so this mode is
    EXACT for the name branch -- which is the part being changed. Numbers are
    under-checked here, so a number that this mode catches is certainly caught
    live; one it passes may or may not be.

default (slow, minutes)
    Re-derives the real retrieval for each caller turn and accumulates it the
    way `VoiceCallSession._grounding_evidence` does, bounded to 40 entries. This
    is what the guard actually saw. Retrieval is cached per caller utterance
    because callers repeat themselves.

Exit code is 1 if a KNOWN_BAD case is not caught, or a KNOWN_GOOD case is.
The drop count over the rest of the archive is printed for review rather than
asserted: a guess about which archived sentences were fabricated is exactly the
kind of thing that got the name branch removed the first time.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: The call that reopened BUG-18. Sentences here MUST be dropped.
CALL_E = "944b503f"

#: Verbatim from call E's transcript, each one SPOKEN to a caller. Every one is
#: absent from the knowledge base -- `grep -i mechanical` over it returns 0.
KNOWN_BAD = [
    ("944b503f", "Yes, I have found the information on our mechanical engineering "
                 "program, and it's a 4-year degree."),
    ("944b503f", "We have a mechanical engineering program that's a 4-year degree. "
                 "Can you confirm you're interested in pursuing a degree in that program?"),
    ("944b503f", "Our mechanical engineering program is a 4-year degree with a "
                 "tuition of $14,600 per year."),
    ("944b503f", "We require a minimum GPA of 3.5 and SAT scores of 1200 or higher "
                 "for admission to our mechanical engineering program."),
    ("944b503f", "We have a mechanical engineering program, which is a highly "
                 "interdisciplinary field that combines principles from physics, "
                 "mathematics, and materials science."),
    ("944b503f", "I checked our database again, and it appears we have a Master of "
                 "Mechanical Engineering program."),
    # BUG-07's own fabrication, from the call that created this boundary.
    ("7f7fbaa3", "We have a unique program in our School of International Studies "
                 "that focuses on global governance and international relations."),
    # ── call fbced641, 2026-10-11. SIX fabrications were spoken on that call,
    #    and none of these phrasings was in this list when it happened: the walk
    #    validated the sentences it had, and the model produced ones it did not.
    #    Each is verbatim from `conversations.transcript`.
    ("fbced641", "To be eligible for our mechanical engineering program, you'll "
                 "need to have completed a bachelor's degree in mechanical "
                 "engineering or a related field."),
    ("fbced641", "We have a few different program options available, including a "
                 "Master's in Mechanical Engineering and a PhD in Mechanical "
                 "Engineering."),
    ("fbced641", "We have a Master's in Mechanical Engineering with a focus on "
                 "advanced materials and manufacturing, and a PhD in Mechanical "
                 "Engineering with a specialization in robotics and automation."),
    ("fbced641", "The tuition for our PhD in Mechanical Engineering is $9,600 "
                 "per year."),
    ("fbced641", "We have a PhD program in Mechanical Engineering with a "
                 "specialization in robotics and automation."),
    # ── call 2e9952a7, 2026-10-11. BUG-29, recombination: every word here is a
    #    real Meridian name word and the grammar is ordinary, so the name branch
    #    saw two established names and never tested the JOIN. Said six times on
    #    the live call; the guard fired 0 times. Caught now by the rule that a
    #    degree may not be offered in another school's subject.
    ("2e9952a7", "Our Master of Science in Computer Science program is designed "
                 "to help you develop advanced skills."),
    ("2e9952a7", "Our tuition for the Master of Science in Computer Science "
                 "program is $9,600 per year."),
    ("2e9952a7", "The minimum English proficiency test score requirement for "
                 "our Master of Science in Computer Science program is 6.5."),
    ("2e9952a7", "We've received your request to take the admission to the "
                 "Master of Science in Computer Science programme."),
    # NOT included, and the reason is worth stating: turn 5 of that call,
    # "That's a long-term experience in mechanical engineering, which is
    # impressive.", was added here first and then removed. It is not a name
    # fabrication -- it echoes the CALLER's own disclosure ("I have 15 years of
    # experience in mechanical engineering"), and the caller's words are
    # evidence. The defect on that turn is that the agent engaged with a
    # fabricated premise at all, which is BUG-17's job (the subject should have
    # been detected and denied before the model ever saw it), not this one's.
    # A fixture that asserts the wrong layer is how this register got its first
    # four wrong conclusions.
]

#: Correct answers, including the three that removed the name branch (BUG-16).
#: These MUST be spoken. If one is refused, the change is wrong -- not the case.
KNOWN_GOOD = [
    ("", "We offer undergraduate and postgraduate programs in fields such as "
         "Engineering, Computer Science, Business, Arts, Science."),
    ("", "We offer a 2-year Master of Arts in Arts and a 3-year Bachelor of Arts "
         "in Arts."),
    ("", "We offer a range of postgraduate programs, including Master of Arts, "
         "Master of Business Administration, and Master of Engineering."),
    # a denial is a correct answer: this is the sentence the archive replay caught
    # the FIRST version of this change refusing.
    ("", "Meridian University does not offer an MDA program. We have a Master of "
         "Business Administration (MBA) program, though."),
    ("", "Our B.Tech in Computer Science program is a four-year degree."),
    ("", "We offer undergraduate and postgraduate programs across six schools, "
         "including Computer Science."),
    # ── call fbced641. Three answers the boundary DROPPED in front of a caller,
    #    for claims that were not claims: a possessive that split into
    #    `bachelor s`, and two single non-name words beside the keyword
    #    "program". The first is a fabrication and must be caught; the rest of
    #    the sentence is not, which is the whole difficulty.
    ("", "It seems like you're interested in the PhD program, specifically the "
         "robotics and automation specialization."),
    ("", "Can you help us understand what would make our system more accurate in "
         "providing program details?"),
    ("", "It's a highly interdisciplinary program that combines theoretical "
         "foundations with practical applications."),
    # the possessive, stated about something real -- the mirror of the
    # fabrication above and the reason a blanket ban on possessives is wrong.
    ("", "I have a bachelor's degree in computer science."),
]


def load_calls(limit: int | None):
    """Archived calls with a transcript, oldest first."""
    import psycopg2
    from app.config import settings

    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        "select id, channel, created_at, transcript from conversations "
        "where coalesce(transcript, '') <> '' order by created_at"
    )
    rows = cur.fetchall()
    conn.close()
    return rows[:limit] if limit else rows


def turns_of(transcript: str):
    return re.findall(r"Caller:\s*(.*?)\s*Assistant:\s*(.*?)(?=\s*Caller:|\Z)",
                      transcript, re.S)


def build_evidence(questions: list[str], live: bool, cache: dict) -> list[str]:
    """What `VoiceCallSession._grounding_evidence` held, in order.

    The live list is the retrieval for the turn, then the caller's own words,
    bounded to 40 entries. Reproduced here so the replay judges each turn on
    what the boundary actually had in front of it.
    """
    if not live:
        return ["(corpus mode: the whole knowledge base stands in for retrieval)"]
    from app.rag import retrieve_context

    evidence: list[str] = []
    for q in questions:
        if q not in cache:
            try:
                cache[q] = retrieve_context(q)
            except Exception as exc:            # noqa: BLE001
                cache[q] = ""
                print(f"    retrieval failed for {q[:50]!r}: {type(exc).__name__}")
        if cache[q]:
            evidence.append(cache[q])
        evidence.append(f"Caller: {q}")
        del evidence[:-40]                      # the session's own bound
    return evidence


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-evidence", action="store_true",
                    help="corpus mode: fast, exact for names, under-checks numbers")
    ap.add_argument("--calls", type=int, default=None, help="only the first N calls")
    ap.add_argument("--show", action="store_true", help="print every dropped sentence")
    args = ap.parse_args()

    from app.voice_grounding import enforce_grounding, is_grounded

    print("grounding archive replay\n")
    print(f"  mode: {'corpus' if args.no_evidence else 'real evidence (retrieval re-derived)'}")
    if not args.no_evidence:
        # WHICH RUNG. `tests/conftest.py` sets USE_MCP_RAG=off for hermeticity,
        # and copying that into a run meant to reflect production tests a
        # different retriever -- measured 2026-10-11: 2501 chars via local
        # Chroma against 935 via MCP for the same question, with the MCP rung
        # missing the very fee it was asked about. The rung decides what the
        # guard is given, and therefore what the guard refuses.
        import os as _os

        _mode = _os.environ.get("USE_MCP_RAG", "auto").lower()
        print(f"  retrieval rung: USE_MCP_RAG={_mode}"
              + ("" if _mode == "auto" else
                 "   <- NOT production (.env=auto); this is the test setting"))

    calls = load_calls(args.calls)
    print(f"  calls: {len(calls)}\n")

    # -- the two directions, asserted ------------------------------------
    print("KNOWN_BAD -- must be refused")
    bad_missed = []
    for call, said in KNOWN_BAD:
        ok = not is_grounded(said, "(replay) " + said)
        if not ok:
            bad_missed.append(said)
        print(f"  [{'PASS' if ok else 'FAIL'}] {call}  {said[:62]}...")

    print("\nKNOWN_GOOD -- must be spoken")
    good_refused = []
    for _, said in KNOWN_GOOD:
        ctx = f"(replay) {said} Meridian University six schools B.Tech in Computer Science"
        ok = is_grounded(said, ctx)
        if not ok:
            good_refused.append(said)
        print(f"  [{'PASS' if ok else 'FAIL'}] {said[:66]}...")

    # -- the archive sweep ------------------------------------------------
    print("\narchive sweep")
    cache: dict[str, str] = {}
    total_turns = dropped_turns = 0
    drops: list[tuple[str, str, str]] = []
    call_e_caught = 0

    for cid, _channel, _ts, transcript in calls:
        pairs = turns_of(transcript)
        evidence = build_evidence([c.strip() for c, _ in pairs], not args.no_evidence, cache)
        call_evidence = "\n".join(evidence) if evidence else ""
        if args.no_evidence:
            from app.kb_entities import corpus_text
            call_evidence = corpus_text() or " "

        for _caller, answer in pairs:
            answer = answer.strip()
            if not answer or answer.startswith("("):
                continue
            total_turns += 1
            fired: list[tuple[str, str]] = []

            def collect(name, **fields):
                # `enforce_grounding` reports the sentence it actually dropped.
                # Reading the sentence off the answer instead (first sentence)
                # made an earlier version of this report print the wrong line
                # for every turn -- worse than no report, because it reads as
                # evidence.
                if name == "GROUNDING_DROPPED":
                    fired.append((str(fields.get("claims", "")),
                                  str(fields.get("sentence", ""))))

            enforce_grounding(answer, call_evidence, log=collect)
            if fired:
                dropped_turns += 1
                if cid.startswith(CALL_E):
                    call_e_caught += 1
                for claims, sentence in fired:
                    drops.append((cid[:8], claims, sentence))

    print(f"  agent turns replayed : {total_turns}")
    print(f"  turns the guard fired: {dropped_turns} "
          f"({100 * dropped_turns / max(total_turns, 1):.1f}%)")
    print(f"  call {CALL_E} turns caught: {call_e_caught}")

    if args.show and drops:
        print("\n  every dropped sentence (review these by hand):")
        for cid, claims, sentence in drops:
            print(f"    {cid}  claims=[{claims[:70]}]")
            print(f"        {sentence}")

    # -- verdict -----------------------------------------------------------
    print()
    if bad_missed or good_refused or not dropped_turns:
        if bad_missed:
            print(f"{len(bad_missed)} known fabrication(s) NOT caught:")
            for s in bad_missed:
                print(f"  - {s[:80]}")
        if good_refused:
            print(f"{len(good_refused)} known-correct answer(s) REFUSED:")
            for s in good_refused:
                print(f"  - {s[:80]}")
        if not dropped_turns:
            print("the guard never fired: it is not running")
        return 1

    print(f"known fabrications caught  : {len(KNOWN_BAD)}/{len(KNOWN_BAD)}")
    print(f"known-correct answers kept : {len(KNOWN_GOOD)}/{len(KNOWN_GOOD)}")
    print(f"archive sweep              : {dropped_turns}/{total_turns} turns fired")
    print("\nReview the fired turns above (--show) before dialling. A count is not a")
    print("verdict: the sentences this guard drops on live calls have to be read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
