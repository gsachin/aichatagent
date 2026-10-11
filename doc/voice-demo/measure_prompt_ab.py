#!/usr/bin/env python
"""A/B the shipped prompt against the voice-native one, on today's REAL calls.

    ./.venv/Scripts/python.exe doc/voice-demo/measure_prompt_ab.py --runs 3

The eval set is not synthetic. Every caller turn below was actually said on
2026-10-10, and every failure this classifies was actually heard by the caller:
the scaffold "Here is a response to the student's question:", the verbatim echo
of "[asked what courses the university offers]", and the
answer that replied to the prompt instead of the person ("Thank you for the
detailed guidelines. As a professional real-time voice AI agent...").

For each real caller turn, the prompt is rebuilt with that turn's real
preceding history, and both prompts get the same retrieved context -- so the
only variable is the prompt.

Classification is deliberately crude and reported as counts, not a score: the
question is whether a defect class disappears, not whether one prompt scores
0.1 better.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── today's real conversations, verbatim from logs/voice_assistant.log ──────
# `agent` entries are what the caller actually heard (machine output, kept);
# `caller` entries are PARAPHRASES of real turns -- the verbatim caller speech
# was removed before publication because the remote is public. Shape preserved,
# words not. Original evidence lives in the local `conversations` table.
# Both arms
# see the same history, which is the point:
# must see the same history -- including the bad turns, which is the point:
# this is the degraded state the new prompt has to survive.
CONVERSATIONS: list[list[tuple[str, str]]] = [
    # call 1, inbound MZ07a2c44 -- the MBA failure and the first scaffold
    [
        ("agent", "Hi, I'm the admissions assistant. Ask me anything about Meridian University programs, tuition fees, or how to apply."),
        ("caller", "[asked about a new programme]"),
        ("agent", "Here's a response to the student's question: Meridian University offers a range of postgraduate programmes."),
        ("caller", "[asked the agent to follow up on a programme; mentioned a maths background]"),
        ("agent", "Let me reach out to the program team and see if they can provide more information on the new program."),
        ("caller", "[asked whether the agent had found anything]"),
        ("agent", "I'd be happy to help you with your inbound call. You're looking for information on our new program."),
        ("caller", "[asked to start over]"),
        ("agent", "Let's start fresh. What's on your mind about the new program at Meridian University?"),
        ("caller", "[asked how many undergraduate programmes exist]"),
        ("agent", "As the admissions assistant at Meridian University, I'd be happy to help you with your questions about our undergraduate programs."),
        ("caller", "[asked whether an MBA is offered]"),
    ],
    # call 2, outbound MZ70a75e -- the good MBA turn, then the drift
    [
        ("agent", "Hi, this is Alex calling from Meridian University Admissions. Do you have a moment to talk about our programs, tuition fees, or how to apply?"),
        ("caller", "[asked how many programmes exist]"),
        ("agent", "I can help with that. Meridian University offers programs in six schools, including undergraduate and postgraduate programs."),
        ("caller", "[stated interest in the MBA]"),
        ("agent", "Meridian University's MBA program is a 2-year postgraduate degree in the School of Business and Management."),
        ("caller", "[stated eligibility for a postgraduate programme and asked about the process]"),
        ("agent", "You're interested in taking admission in the MBA program. Can you tell me what your current work experience is?"),
        ("caller", "[described their degree and asked what documents are needed]"),
    ],
    # call 3, outbound MZ64aa8c4 -- the verbatim echo
    [
        ("agent", "Hi, this is Alex calling from Meridian University Admissions. Do you have a moment to talk about our programs, tuition fees, or how to apply?"),
        ("caller", "[asked about admission]"),
        ("agent", "Can you tell me more about the admission process for Meridian University?"),
        ("caller", "[asked for detail on MBA admission]"),
        ("agent", "Here is a response to the student's question: the MBA is a two year postgraduate programme."),
        ("caller", "[asked what courses the university offers]"),
    ],
    # call 4, outbound MZf88b713 -- the model replied to the prompt
    [
        ("agent", "Hi, this is Alex calling from Meridian University Admissions. Do you have a moment to talk about our programs, tuition fees, or how to apply?"),
        ("caller", "[fragment: the list of courses]"),
        ("agent", "Can you please tell me what you're looking for in terms of university programs or courses?"),
        ("caller", "[fragment: the programme they are looking for]"),
    ],
]

#: Phrases that mean the model addressed its instructions instead of the caller.
SCAFFOLD = re.compile(
    r"(here'?s|here is|here are)\b[^.\n]{0,60}\b(responses?|replies?|answers?)\b"
    r"|as a (professional )?(real-time )?voice ai"
    r"|thank you for the (detailed )?guidelines"
    r"|the caller (just )?(asked|said|is)"
    r"|based on the (context|information)"
    r"|i'?d be happy to help with the conversation"
    r"|university profile context",
    re.IGNORECASE,
)


def classify(answer: str, caller_turn: str) -> str:
    a = (answer or "").strip()
    if not a:
        return "EMPTY"
    if SCAFFOLD.search(a):
        return "SCAFFOLD"
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    na, nc = norm(a), norm(caller_turn)
    # A true echo is the caller's turn returned AS the answer, not merely
    # contained in it. Containment flagged "Sorry, I think you meant to say
    # 'all the programs' rather than 'all the list of courses'" as an echo --
    # that is the agent CORRECTING the caller, which is the behaviour we want.
    # So require the answer to be almost nothing but the caller's words.
    if na and nc and len(nc) > 18:
        if na == nc or nc in na and len(na) <= len(nc) * 1.35:
            return "ECHO"
    return "ok"


async def run_arm(label: str, build, runs: int) -> Counter:
    from app import voice_handler as vh
    from app.llm_backend import generate_stream
    from app.rag import retrieve_context

    counts: Counter = Counter()
    examples: list[tuple[str, str]] = []
    ttfts: list[float] = []
    for convo in CONVERSATIONS:
        for i, (role, text) in enumerate(convo):
            if role != "caller":
                continue
            history = [
                f"{'Assistant' if r == 'agent' else 'Caller'}: {t}"
                for r, t in convo[:i]
            ]
            ctx = retrieve_context(text)

            # The REAL assembly. The first version of this harness hand-built
            # "prompt + Conversation so far + Caller just said", which is not
            # what the live path sends -- it omitted the style rules, the facts
            # block and the intro guard that `_build_llm_prompt` adds. A
            # comparison that does not reproduce the production path cannot
            # support a decision about it, so the system prompt is now the ONLY
            # thing that differs between the arms.
            session = vh.VoiceCallSession()
            session._conversation_history = history
            session.intro_done = True
            session.note_facts(text)
            full = f"{build(ctx)}\n\n{session._build_llm_prompt(text)}"

            for _ in range(runs):
                parts = []
                t0 = time.perf_counter()
                first = None
                try:
                    async for item in generate_stream(full, model="llama3.2:3b"):
                        if isinstance(item, str):
                            if first is None:
                                first = time.perf_counter() - t0
                            parts.append(item)
                except Exception:                      # noqa: BLE001
                    counts["ERROR"] += 1
                    continue
                ttfts.append((first or 0) * 1000)
                spoken = vh.scrub_meta_leak("".join(parts)) or ""
                verdict = classify(spoken, text)
                counts[verdict] += 1
                if verdict in ("SCAFFOLD", "ECHO", "EMPTY") and len(examples) < 6:
                    examples.append((verdict, spoken[:95]))
    return counts, examples, ttfts


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="samples per turn")
    args = ap.parse_args()

    from app.voice_system_prompt import build_voice_system_prompt
    from voice_native_prompt import build_voice_native_prompt

    turns = sum(1 for c in CONVERSATIONS for r, _ in c if r == "caller")
    print(f"eval set: {len(CONVERSATIONS)} real calls, {turns} real caller turns, "
          f"{args.runs} samples each\n")

    for label, build in (("SHIPPED (18.9k chars)", build_voice_system_prompt),
                         ("VOICE-NATIVE (~1.5k)", build_voice_native_prompt)):
        size = len(build("X" * 1000))
        counts, examples, ttfts = await run_arm(label, build, args.runs)
        total = sum(counts.values())
        print(f"{'=' * 66}\n{label}   prompt={size} chars\n{'=' * 66}")
        for k, v in counts.most_common():
            print(f"   {k:<10} {v:>3} / {total}   {100 * v / max(total, 1):>5.1f}%")
        for verdict, text in examples[:4]:
            print(f"     [{verdict}] {text!r}")
        if ttfts:
            ttfts.sort()
            print(f"   prefill+first token: med={ttfts[len(ttfts)//2]:.0f} ms  "
                  f"max={ttfts[-1]:.0f} ms")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
