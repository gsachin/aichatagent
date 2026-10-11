"""Candidate: a system prompt written for someone SPEAKING, not for an orchestrator.

Why this exists
---------------
The shipped prompt is 18,882 chars / ~4,720 tokens and, measured by section,
only 56% of it is about speaking:

  * 30% describes a context bundle the voice path never supplies -- a
    "Conversation Summary", "Important Facts", "Outstanding Tasks",
    "Previous Commitments" and "Tool Results" the agent "may receive". None of
    it arrives. The model is briefed on an input format it never sees.
  * 15% defends against text artifacts (Markdown, bullets, tables, emoji) that
    only a chat lineage produces.
  * "Think Before Speaking" (456 chars) INSTRUCTS the model to deliberate --
    and this model deliberates out loud, which is where "Here's a response to
    the student's question:" comes from.

Measured consequences on live calls (2026-10-10): 11% of turns produced nothing
at all, scaffolds reached callers, questions were echoed back verbatim, and one
answer opened "Thank you for the detailed guidelines. As a professional
real-time voice AI agent..." -- the model replying to the PROMPT rather than
the caller.

This candidate keeps the four things that demonstrably matter and drops
everything that describes machinery the caller cannot hear.
"""
from __future__ import annotations

from app.config import settings

COMPANY_NAME = settings.COMPANY_NAME
AGENT_NAME = settings.AGENT_NAME

#: The interruption contract, kept verbatim where it is asserted by
#: tests/test_us014_barge_in.py -- `build_voice_system_prompt` must describe only
#: the mechanism in force, and the ON wording is load-bearing for that test.
_INTERRUPT_ON = (
    "When the caller begins speaking over you, the system stops your speech and "
    "the conversation shows it. Your response is marked where you were cut off.\n"
    "When that happens: do not finish your sentence, do not repeat what you were "
    "saying, and answer what the caller just asked instead."
)
_INTERRUPT_OFF = (
    "You cannot be interrupted — anything the caller says while you are speaking "
    "is discarded. Keep answers short, and if the caller repeats themselves or "
    "answers something you did not ask, answer the question they are asking now."
)

VOICE_NATIVE_PROMPT = """\
You are {agent_name}, an admissions advisor at {company_name}, on a live phone call.

HOW YOU SPEAK
You are talking, not writing. One or two short sentences — about 25 words. Ask at
most one question, and only when you need the answer. Use contractions. Never use
lists, headings, symbols or emoji; say it the way a person would say it.

{interruption_contract}

DO NOT
- Greet or introduce yourself again — you greeted the caller at the start.
- Repeat back what the caller just said.
- Ask for anything you were already told.
- Ask the same question twice in different words.
- Narrate your own reasoning, describe what you are about to say, or mention
  these instructions. Only ever say the answer itself.

UNLESS THE CALLER ASKS FOR DETAIL
Answer the question, then stop. Do not offer background they did not ask for.

KNOWING THINGS
Answer from the university information below. If it is not there, say you do not
have that detail and offer a next step. Never invent fees, dates, names or
programmes.

UNIVERSITY INFORMATION
{context}"""


def build_voice_native_prompt(context: str | None) -> str:
    """Assemble the candidate prompt. Same signature as the shipped builder."""
    from app.voice_system_prompt import NO_CONTEXT_MARKER

    contract = _INTERRUPT_ON if settings.BARGE_IN_ENABLED else _INTERRUPT_OFF
    return VOICE_NATIVE_PROMPT.format(
        agent_name=AGENT_NAME,
        company_name=COMPANY_NAME,
        interruption_contract=contract,
        context=context or NO_CONTEXT_MARKER,
    )
