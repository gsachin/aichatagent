#!/usr/bin/env python
"""Does delivering the prompt as a `system` turn stop the agent answering it?

    ./.venv/Scripts/python.exe doc/voice-demo/measure_role_ab.py --runs 3

The hypothesis under test (BUGS.md, cross-cutting note): the voice path sends its
whole 18.9k-char prompt as a single `user` message, and a `user` turn is something
the model is entitled to reply to. That is the proposed shared mechanism behind
BUG-01 (recites the prompt), BUG-02 (narrates compliance with my style rules) and
BUG-03 (scaffolds).

One variable changes: which role carries the instructions.

    A: messages=[{"role":"user",   "content": <system prompt> + <assembled turn>}]
    B: messages=[{"role":"system", "content": <system prompt>},
                 {"role":"user",   "content": <assembled turn>}]

The eval set is the four real calls in `conversations`, replayed turn by turn with
their real preceding history -- the same transcripts that produced the defects.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg2                                                    # noqa: E402

# ── the defects, as observable output properties ────────────────────

#: A lead-in announcing the answer rather than giving it.
SCAFFOLD = re.compile(
    r"(here'?s|here is|here are)\b[^.\n]{0,60}\b(responses?|replies?|answers?)\b"
    r"|here is the ai'?s"
    r"|thank you for the (detailed )?guidelines"
    r"|as a (professional )?(real-?time )?voice ai",
    re.IGNORECASE,
)

#: The agent grading itself against rules the caller never heard (BUG-02).
SELF_GRADE = re.compile(
    r"as per the guidelines|within the two-sentence limit|two-sentence limit"
    r"|i'?ve responded naturally|asked only one question"
    r"|per the (guidelines|instructions)|as instructed",
    re.IGNORECASE,
)

#: The agent reciting its own instructions (BUG-01).
RECITAL = re.compile(
    r"let'?s begin the conversation"
    r"|i will follow these rules"
    r"|re-?evaluate the conversation using the updated context"
    r"|classify the latest statement conceptually"
    r"|use the complete relevant conversation context"
    r"|i will also keep in mind the following"
    r"|context compression",
    re.IGNORECASE,
)


def classify(answer: str, caller_turn: str) -> str:
    a = (answer or "").strip()
    if not a:
        return "EMPTY"
    if RECITAL.search(a):
        return "RECITAL"
    if SELF_GRADE.search(a):
        return "SELF-GRADE"
    if SCAFFOLD.search(a):
        return "SCAFFOLD"
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()   # noqa: E731
    na, nc = norm(a), norm(caller_turn)
    if na and nc and len(nc) > 18 and (na == nc or (nc in na and len(na) <= len(nc) * 1.35)):
        return "ECHO"
    return "ok"


def load_conversations(limit: int = 6) -> list[list[tuple[str, str]]]:
    """Real transcripts, parsed into ordered (role, text) turns."""
    from app.config import settings

    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """select transcript from conversations
           where created_at > now() - interval '2 days'
             and channel in ('outbound_call','inbound_call')
             and length(coalesce(transcript,'')) > 200
           order by created_at desc limit %s""", (limit,))
    out = []
    for (tr,) in cur.fetchall():
        turns = []
        for part in re.split(r"(?=(?:Caller|Assistant): )", tr or ""):
            part = part.strip()
            if part.startswith("Caller:"):
                turns.append(("caller", part[7:].strip()))
            elif part.startswith("Assistant:"):
                turns.append(("agent", part[10:].strip()))
        if sum(1 for r, _ in turns if r == "caller") >= 3:
            out.append(turns)
    conn.close()
    return out


def build_messages(role: str, system: str, turn_prompt: str) -> list[dict]:
    if role == "system":
        return [{"role": "system", "content": system},
                {"role": "user", "content": turn_prompt}]
    return [{"role": "user", "content": system + "\n\n" + turn_prompt}]


def run_arm(role: str, convos, runs: int):
    import ollama
    from app import voice_handler as vh
    from app.llm_backend import KEEP_ALIVE, DEFAULT_NUM_CTX
    from app.rag import retrieve_context
    from app.voice_system_prompt import build_voice_system_prompt

    counts: Counter = Counter()
    examples: list[tuple[str, str, str]] = []
    for convo in convos:
        for i, (r, text) in enumerate(convo):
            if r != "caller":
                continue
            hist = [f"{'Assistant' if rr == 'agent' else 'Caller'}: {t}"
                    for rr, t in convo[:i]]
            ctx = retrieve_context(text)
            session = vh.VoiceCallSession()
            session._conversation_history = hist
            session.intro_done = True
            session.note_facts(text)
            system = build_voice_system_prompt(ctx)
            turn = session._build_llm_prompt(text)
            msgs = build_messages(role, system, turn)
            for _ in range(runs):
                try:
                    resp = ollama.chat(model="llama3.2:3b", messages=msgs,
                                       options={"num_ctx": int(DEFAULT_NUM_CTX)},
                                       keep_alive=KEEP_ALIVE)
                    raw = (resp.get("message") or {}).get("content", "")
                except Exception as exc:                            # noqa: BLE001
                    counts["ERROR"] += 1
                    continue
                spoken = vh.scrub_meta_leak(raw) or ""
                verdict = classify(spoken, text)
                counts[verdict] += 1
                if verdict != "ok" and len(examples) < 5:
                    examples.append((verdict, text[:40], spoken[:120]))
    return counts, examples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--calls", type=int, default=6)
    args = ap.parse_args()

    convos = load_conversations(args.calls)
    turns = sum(1 for c in convos for r, _ in c if r == "caller")
    print(f"eval set: {len(convos)} REAL calls from the conversations table, "
          f"{turns} caller turns, {args.runs} samples each\n")

    for role, label in (("user", "ARM A — prompt as a USER turn (SHIPPED)"),
                        ("system", "ARM B — prompt as a SYSTEM turn")):
        counts, examples = run_arm(role, convos, args.runs)
        total = sum(counts.values())
        defects = total - counts.get("ok", 0)
        print("=" * 70)
        print(f"{label}")
        print("=" * 70)
        for k, v in counts.most_common():
            print(f"   {k:<11} {v:>3} / {total}   {100 * v / max(total, 1):>5.1f}%")
        print(f"   {'DEFECTS':<11} {defects:>3} / {total}   "
              f"{100 * defects / max(total, 1):>5.1f}%")
        for verdict, q, a in examples[:3]:
            print(f"     [{verdict}] Q={q!r}")
            print(f"                A={a!r}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
