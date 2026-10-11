"""Grounding boundary — a spoken claim must be traceable to retrieved context.

BUG-07 (doc/voice-demo/BUGS.md). The agent told a caller about a "School of
International Studies" that appears in no source, quoted a tuition of $14,500
when the context it was given said $14,200, and renamed "B.Tech in Information
Technology" to "B.Tech in Computer Science". None of those were invented from
nothing: the model recombines and embellishes the facts it is handed.

Why a guard rather than a prompt rule
-------------------------------------
The prompt already says "Never invent numbers, fees, URLs, or program names",
and an "Unknown Information" section says to decline when a detail is absent.
That was already there when the caller heard the invented school. Prompt rules
do not bind reliably on this model -- the same lesson as BUG-01/02/03, where
four rewrites of a prompt and a regex failed and one role change fixed it.

So this is an output boundary, like `scrub_meta_leak`: it decides what may be
spoken, rather than asking the model to decide.

What counts as a claim
----------------------
Deliberately narrow. Only two shapes are checked, because those are the ones a
caller can be misled by and the ones that can be verified mechanically:

  * a NUMBER, with an optional unit ("$14,500", "4 years", "24 hours")
  * a MULTI-WORD PROPER NOUN ("School of International Studies", "B.Tech in
    Computer Science")

Single capitalised words are NOT checked. "Meridian" and "B.Tech" alone match
almost any passage and would flag correct answers; the damage in BUG-07 came
from multi-word names.

Normalisation is the part that matters
--------------------------------------
The first version of this flagged "four-year" as ungrounded because the context
says "4 years". A guard that rejects correct answers is worse than no guard, so
number words and digits are folded together (and money punctuation stripped)
before comparison. Validated against every turn of the call that produced
BUG-07, not against invented examples.
"""
from __future__ import annotations

import re

__all__ = ["ungrounded_claims", "is_grounded", "enforce_grounding", "GROUNDED_REFUSAL"]

#: What the caller hears instead, when a turn has nothing left that is grounded.
#: A sentence, never silence -- an empty turn is BUG-05, and a guard that
#: produces one has traded a wrong answer for no answer.
GROUNDED_REFUSAL = (
    "I don't have that detail in front of me. I can check and come back to you, "
    "or help with something else about the programme."
)

_WORD_NUM = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "eighteen": "18", "twenty": "20", "thirty": "30",
}

#: "4 years", "$14,500", "24 hours", "3-year".
_NUMBER = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?\s*(?:-|\s)?\s*"
    r"(?:year|years|month|months|week|weeks|day|days|hour|hours|minute|minutes)?",
    re.IGNORECASE,
)

#: Two or more capitalised words, allowing lowercase connectives.
#: "School of International Studies", "B.Tech in Computer Science".
#:
#: The inner `(?:\.[A-Z]...)` form exists so an abbreviation like "B.Tech" is
#: ONE token, while a sentence-ending period is not swallowed. Allowing a bare
#: `.` in the word class made "November. Can I confirm" match as a single name
#: -- a sentence boundary read as part of a proper noun.
_PROPER = re.compile(
    r"\b[A-Z][A-Za-z&]*(?:\.[A-Z][A-Za-z&]*)*"
    r"(?:\s+(?:of|the|and|for|in|at|&)\s+[A-Z][A-Za-z&]*(?:\.[A-Z][A-Za-z&]*)*"
    r"|\s+[A-Z][A-Za-z&]*(?:\.[A-Z][A-Za-z&]*)*)+"
)

#: Words that begin a sentence and so are capitalised for grammar, not for
#: naming. Stripped before proper-noun extraction.
_SENTENCE_STARTERS = {
    "the", "we", "our", "you", "your", "i", "it", "this", "that", "there",
    "they", "those", "these", "and", "but", "so", "if", "for", "with", "at",
    "yes", "no", "okay", "sure", "great", "thank", "thanks", "sorry",
}


def _norm(text: str) -> str:
    """Fold the forms that mean the same thing before comparing.

    Number words to digits, money punctuation away, case down. Without this the
    guard flags a correct "four-year" because the context writes "4 years".
    """
    s = (text or "").lower()
    for word, digit in _WORD_NUM.items():
        s = re.sub(rf"\b{word}\b", digit, s)
    s = s.replace(",", "").replace("$", "")
    s = re.sub(r"[^a-z0-9.\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _claims(text: str) -> list[str]:
    """The checkable claims in one piece of text: NUMBERS, and only numbers.

    Multi-word proper nouns were checked here and were removed after a live
    call measured the consequence. The distinction that decided it is not a
    tuning preference, it is a property of the two shapes:

      * A NUMBER is either in the source or it is invented. "$14,500" when the
        context says "$14,200"; "a 10% discount for international students" when
        no file mentions a discount; "within the next 24 hours" when nothing can
        schedule a callback. Every genuine defect this guard has caught in the
        field was of this shape, and every one was caller-damaging.

      * A NAME LIST is not decidable by matching. "We offer Master of Arts,
        Master of Business Administration and Master of Engineering" is a
        CORRECT answer that contains names absent from that single turn's
        retrieval; "B.Tech in Computer Science" when the source says
        "Information Technology" is a renaming. Identical input shape, opposite
        verdicts. On the 2026-10-10 QA calls the name branch produced ZERO true
        positives and THREE false refusals -- all of them programme lists, each
        one a visible failure in front of a caller.

    So: numbers are checked, names are not. The cost is stated rather than
    implied -- an invented name carrying no number ("Master of Mechanical
    Engineering", "Department of Homeland Security") now passes. That is the
    deliberate trade: a refusal is worse than the fabrication it prevents,
    because a caller hearing "I don't have that detail" about the university's
    own MBA has watched the agent break, whereas a plausible extra name reads
    as helpfulness until someone checks.
    """
    found: list[str] = []
    for m in _NUMBER.finditer(text or ""):
        tok = m.group(0).strip()
        # A bare number is too weak to be a claim ("2" matches anything);
        # require a unit or a currency mark.
        if re.search(r"[$]|\d\s*(?:-|\s)\s*(?:year|month|week|day|hour|minute)",
                     tok, re.IGNORECASE) or "$" in tok or len(re.sub(r"\D", "", tok)) >= 3:
            found.append(tok)
    return found


def ungrounded_claims(answer: str, context: str) -> list[str]:
    """Claims in `answer` that do not appear in `context`. Empty means grounded.

    `context` should be everything this call has legitimately seen: the
    retrieval for THIS turn, plus anything established earlier in the
    conversation. Checking against the current turn's retrieval alone flags
    correct follow-ups -- asked "in November", the retrieval returns nothing
    about Computer Science, and the agent's mention of the programme it has
    been discussing all call reads as ungrounded. That is a false positive, and
    a guard that rejects correct answers is worse than no guard.

    EMPTY EVIDENCE MEANS NO JUDGEMENT, NOT "EVERYTHING IS UNGROUNDED". This is
    the lesson of the 2026-10-10 QA call, where a wiring fault left the
    evidence list empty and the guard refused a correct answer about the
    university's own programmes. Failing open is the right direction here:
    with nothing to check against, the guard has no information, and the
    failure mode of refusing everything reads to a caller as an agent that has
    stopped working. Absence of evidence is not evidence of fabrication.
    """
    if not answer or not answer.strip():
        return []
    if not (context or "").strip():
        return []
    hay = _norm(context)
    hay_core = re.sub(r"[^a-z0-9]", "", hay)
    if not hay_core:
        return []
    out: list[str] = []
    for claim in _claims(answer):
        needle = _norm(claim)
        if not needle:
            continue
        # Compare on the digits/words only: "4 years" vs "4 year" must match.
        if needle in hay:
            continue
        core = re.sub(r"[^a-z0-9]", "", needle)
        if core and core in hay_core:
            continue
        out.append(claim)
    return out


def is_grounded(answer: str, context: str) -> bool:
    return not ungrounded_claims(answer, context)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


#: A sentence is worth speaking on its own only if it still carries a specific
#: grounded fact -- a number, or a NAME. Checked as a function rather than one
#: regex because the sentence's FIRST word is always capitalised, so a pattern
#: matching any capitalised word counts "Would you like to know more about
#: that?" as substantive. That is how a dangling question about a deleted
#: sentence survived the first version of this guard.
def _is_substantive(sentence: str) -> bool:
    if re.search(r"\$?\d", sentence):
        return True
    words = sentence.split()
    rest = " ".join(words[1:]) if len(words) > 1 else ""
    return bool(re.search(r"\b[A-Z][A-Za-z&]{2,}", rest))


def enforce_grounding(answer: str, context: str, log=None) -> str:
    """Speak only what is grounded; never return silence.

    Policy, and the reason it is not simply "drop the bad sentence":

    A turn that mixes a grounded answer with one embellished clause does not
    become correct when the embellished clause is deleted. Measured on the call
    that produced BUG-07:

      * dropping the fabricated school left "Would you like to know more about
        that?" -- a question about something the caller never heard;
      * dropping the "within 24 hours" left "We'll expedite the scholarship
        review process for you" -- the unfounded promise intact, now vaguer and
        harder to notice.

    Both are worse than declining. So the rule is: keep the survivors ONLY if at
    least one of them still carries a specific grounded fact; otherwise the turn
    is refused. Silence is never returned -- an empty turn is BUG-05, and a
    guard that produces one has traded a wrong answer for no answer at all.
    """
    if not answer or not answer.strip():
        return answer
    kept, dropped = [], []
    for sentence in _sentences(answer):
        bad = ungrounded_claims(sentence, context)
        if bad:
            dropped.append((sentence, bad))
        else:
            kept.append(sentence)
    if not dropped:
        return answer
    if log is not None:
        for sentence, bad in dropped:
            log("GROUNDING_DROPPED", claims="|".join(bad)[:160],
                sentence=sentence[:120])
    survivors = " ".join(kept).strip()
    if survivors and any(_is_substantive(s) for s in kept):
        return survivors
    if log is not None and survivors:
        log("GROUNDING_REFUSED", reason="survivors carried no grounded fact",
            survivors=survivors[:120])
    return GROUNDED_REFUSAL
