#!/usr/bin/env python
"""Call-level gate: does a whole CONVERSATION behave?

    ./.venv/Scripts/python.exe doc/voice-demo/replay_call.py
    ./.venv/Scripts/python.exe doc/voice-demo/replay_call.py --show

Why this exists, and why it is not another turn-level test
----------------------------------------------------------
Three rounds of fixing the voice boundary, each with every gate green and the
caller unhappy:

    round 1  the guard stopped catching fabrications
    round 1  the guard spoke one fixed sentence, nine times  ->  a loop
    round 2  the loop's wording was fixed, its SUBJECT was pinned, so a caller
             asking about Computer Science fees was told six times that Meridian
             does not offer mechanical engineering

Every one of those is a property of a CONVERSATION, not of a turn. A turn-level
test asks "is this sentence grounded"; the defects were "the same thing was said
for the fifteenth time", "the instruction outlived the topic", "the list got
worse as the call went on". None of them is visible in a single turn, and the
76 unit tests, 223 replayed turns and 14 preflight checks all passed while all
three were live.

So this gate replays COMPLETE CALLS, in order, through the real session, and
asserts the things a caller notices.

Two halves
----------
1. **Recorded-transcript measures** -- no session, no model. Repetition,
   distinct-turn ratio, empty turns, and every spoken claim checked against the
   corpus. Runs over every archived call.
2. **Session replay** -- the caller's turns drive `note_unoffered` in order, and
   the assertions are on the PROMPT BLOCK over time: it must appear when the
   subject is live, and expire when the caller moves on. That is the exact
   mechanism that failed on `853548f5`, and it is deterministic.

What this cannot do: it does not call the model, so it cannot tell you what the
model would SAY. The block is the input it controls; the output boundary is
covered by the turn-level suites. Between them there is still a gap, and a live
call is still the only thing that closes it.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: An agent sentence said more than this many times in one call is a loop.
MAX_SENTENCE_REPEATS = 3
#: A call where more than this share of turns produce no audio has failed.
MAX_EMPTY_TURN_RATE = 0.30

#: Calls with a known conversation-level defect, and what must now hold.
#:
#: `853548f5` is the important one. The caller named mechanical engineering at
#: turn 1 and abandoned it at turn 7; the block kept naming it for the rest of
#: the call and the model denied it on six turns, including a direct question
#: about Computer Science fees. The assertion is on the block, because the block
#: is what this repository controls.
CALL_CASES = [
    {
        "id": "853548f5",
        "note": "topic abandoned at turn 7, denied 6 times",
        "subject": "mechanical engineering",
        # The turns that cost the caller something: 8 asked for the fee
        # structure, 12 said "computer science", 15 asked a question -- all
        # answered with a denial of a subject the caller had left behind.
        #
        # NOT all of 4-15. The caller names it again at turn 11 ("get about the
        # mechanical engineering"), and re-arming on a fresh mention is correct.
        # An earlier version of this case asserted "absent after turn 9" and
        # failed turns 11-13 -- the expectation was wrong, not the code, and
        # that distinction is the whole point of writing the contract down.
        "must_be_absent_at": [8, 12, 15],
    },
    {
        "id": "fbced641",
        "note": "six fabrications spoken",
        "subject": "mechanical engineering",
        "must_be_absent_at": [],
    },
]


def load_calls(limit: int | None = None):
    import psycopg2
    from app.config import settings

    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("select id, created_at, transcript from conversations "
                "where coalesce(transcript,'') <> '' order by created_at")
    rows = cur.fetchall()
    conn.close()
    return rows[-limit:] if limit else rows


def turns_of(transcript: str):
    return [(c.strip(), a.strip()) for c, a in
            re.findall(r"Caller:\s*(.*?)\s*Assistant:\s*(.*?)(?=\s*Caller:|\Z)",
                       transcript, re.S)]


def norm_sentence(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def measure(pairs, index) -> dict:
    """Conversation measures for one RECORDED call.

    Reported, never asserted. A recorded transcript is what a caller heard under
    whatever build was live at the time -- re-running it cannot change it, and a
    gate that fails on history is a gate everybody learns to ignore. The numbers
    go in the report; the assertions are on properties of the CURRENT code.
    """
    agent = [a for _, a in pairs if a and not a.startswith("(")]
    empty = sum(1 for _, a in pairs if not a or a.startswith("("))
    sentences: Counter = Counter()
    for a in agent:
        for s in re.split(r"(?<=[.!?])\s+", a):
            if len(s.strip()) > 35:
                sentences[norm_sentence(s)] += 1
    worst, count = (sentences.most_common(1)[0] if sentences else ("", 0))
    return {
        "turns": len(pairs),
        "empty": empty,
        "empty_rate": empty / max(len(pairs), 1),
        "repeats": count,
        "worst": worst,
    }


def replay_block(cid, pairs, case, record) -> None:
    """Drive the session's block over the caller's turns, in order.

    This is the half that CAN be asserted, because it is a property of the code
    in the repository rather than of a recording: feed the caller's turns as
    they were spoken and see how long the prompt keeps telling the model about a
    subject the caller has left behind.
    """
    from app.voice_handler import (VoiceCallSession, _CHANGE_CUE,
                                   _UNOFFERED_TTL_TURNS)

    s = VoiceCallSession()
    s.note_grounding_evidence("six schools Engineering Computer Science "
                              "Business Arts Science Commerce")
    subject = case["subject"]
    words = subject.lower().split()
    named_at, live_at = [], []

    for i, (caller, _agent) in enumerate(pairs, 1):
        s._turn += 1
        s.note_unoffered(caller)
        if subject.lower() in [e.lower() for e in s._live_unoffered()]:
            live_at.append(i)
        if any(w in caller.lower() for w in words):
            named_at.append(i)

    if not live_at:
        record("the unoffered subject was detected at all", False,
               f"{subject!r} never entered the block")
        return
    record("the unoffered subject was detected", True, f"live on turns {live_at}")

    # THE CONTRACT: the block may only be live within the TTL of the caller
    # actually naming the subject. Checked against the caller's RAW WORDS, not
    # against the session's own bookkeeping, so it is not a tautology.
    orphaned = [t for t in live_at
                if not any(m <= t <= m + _UNOFFERED_TTL_TURNS for m in named_at)]
    record("the block is never live without a recent mention from the caller",
           not orphaned,
           f"live but unmentioned at turns {orphaned}" if orphaned
           else f"caller named it at {named_at}")

    absent = case.get("must_be_absent_at") or []
    wrong = [t for t in absent if t in live_at]
    if absent:
        record("the block is clear on the turns that cost the caller something",
               not wrong,
               f"still live at turns {wrong}" if wrong
               else f"clear at turns {absent}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calls", type=int, default=None, help="most recent N calls")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    from app.kb_entities import default_index
    from app.voice_grounding import is_grounded

    index = default_index()
    print("call-level gate\n")

    calls = load_calls(args.calls)
    print(f"  archived calls: {len(calls)}\n")

    results: list[tuple[bool, str, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((ok, name, detail))

    # ── 1. every call: conversation measures (REPORTED, not asserted) ──
    print("every archived call — how the conversation went, as recorded")
    worst_rep = (0, "", "")
    worst_empty = (0.0, "", "")
    for cid, _ts, transcript in calls:
        pairs = turns_of(transcript)
        if len(pairs) < 4:
            continue
        m = measure(pairs, index)
        if m["repeats"] > worst_rep[0]:
            worst_rep = (m["repeats"], cid[:8], m["worst"][:58])
        if m["empty_rate"] > worst_empty[0]:
            worst_empty = (m["empty_rate"], cid[:8], f"{m['empty']}/{m['turns']}")
    print(f"    worst sentence repetition : x{worst_rep[0]} in {worst_rep[1]}"
          f"  {worst_rep[2]!r}")
    print(f"    worst empty-turn rate     : {worst_empty[0]:.0%} in "
          f"{worst_empty[1]}  ({worst_empty[2]})")
    print(f"    (thresholds for a NEW call: x{MAX_SENTENCE_REPEATS} repeats, "
          f"{MAX_EMPTY_TURN_RATE:.0%} empty)")

    # ── 2. the named calls: the defects that actually happened ───────
    by_id = {cid[:8]: transcript for cid, _ts, transcript in calls}
    for case in CALL_CASES:
        cid = case["id"]
        if cid not in by_id:
            print(f"\n  {cid} — NOT IN THE ARCHIVE (skipped)")
            continue
        print(f"\n  {cid} — {case['note']}")
        before = len(results)
        replay_block(cid, turns_of(by_id[cid]), case, record)
        for ok, name, detail in results[before:]:
            print(f"    [{'PASS' if ok else 'FAIL'}] {name}"
                  + (f"  -- {detail}" if detail else ""))

    # ── 3. no fabrication survived into a caller's ear ───────────────
    print("\nfabrications across the archive")
    bad = []
    for cid, _ts, transcript in calls:
        for _caller, agent in turns_of(transcript):
            if agent and not agent.startswith("("):
                if not is_grounded(agent, "(replay) " + agent, index):
                    bad.append((cid[:8], agent[:70]))
    print(f"    {len(bad)} turns carry an ungrounded claim (all reviewed in "
          f"replay_grounding.py --show)")

    failed = [r for r in results if not r[0]]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} conversation checks FAILED:")
        for _ok, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"all {len(results)} conversation checks passed.")
    print("\nA call is still the only thing that tests a call. These checks are")
    print("conversation-shaped; they are not a conversation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
