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
Two shapes, because these are the ones a caller can be misled by and the ones
that can be verified mechanically:

  * a NUMBER, with an optional unit ("$14,500", "4 years", "3.5")
  * a NAME PHRASE -- a programme, school or department, matched
    case-insensitively and anchored on a keyword ("School of X", "X program").

BUG-18, and why the name branch came back
-----------------------------------------
A name check ran here once and was removed, because it refused CORRECT
programme lists in front of callers. That measurement was taken on the same
calls as the BUG-16 wiring fault, which left the evidence list empty -- so
everything was being refused and the name branch was indicted on contaminated
evidence. Replayed over all 81 archived calls, an entity-aware name check flags
11 names; three are regex noise and seven are real fabrications, including
BUG-07's own "School of International Studies".

What makes it safe is one rule, in `_name_claims`: a name is a claim only if
the corpus does not carry ITS OWN content. "Master of Engineering" beside
"Master of Arts" passes -- a slightly-wrong rename of a real programme is the
documented BUG-16 trade. "a bachelor's degree in mechanical engineering" fails,
because `mechanical engineering` is not a thing this university has. Names are
judged against the corpus index (`app/kb_entities.py`), not against this turn's
retrieval -- absence from one retrieval is not absence from the university.

The rule is PER SLOT, never per sentence. An earlier version asked whether the
sentence named anything real and let one answer excuse every other name in it;
on live call `fbced641` that let `Master s` -- a possessive artifact -- excuse
`PhD in Mechanical Engineering` beside it, and six fabrications were spoken.

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
import unicodedata

from app.kb_entities import (
    EntityIndex,
    default_index,
    name_candidates,
    name_slots,
    normalise as _norm,
)
from app.languages import normalize_language_code

__all__ = ["ungrounded_claims", "is_grounded", "enforce_grounding", "GROUNDED_REFUSAL"]

#: What the caller hears instead, when a turn has nothing left that is grounded.
#: A sentence, never silence -- an empty turn is BUG-05, and a guard that
#: produces one has traded a wrong answer for no answer.
#:
#: This is the first of several, and the plurality is the point. A boundary that
#: substitutes ONE fixed string every time it fires does not just refuse, it
#: loops, and the caller cannot escape it. Measured on live call `fbced641`:
#: this sentence was spoken **9 times across 8 of 25 turns**, one turn carrying
#: it twice, and the caller ended the call over it --
#:
#:     "Why are you repeating?"  "this is a defect in your system"
#:     "then stop repeating the thing"  "You stop the call."
#:
#: BUG-08 recorded repetition as a defect the MODEL committed sometimes. A guard
#: that speaks makes it unconditional, which is worse -- so the guard owns it.
GROUNDED_REFUSAL = (
    "I don't have that detail in front of me. I can check and come back to you, "
    "or help with something else about the programme."
)

#: Rotation, used in order for consecutive refusals. Same meaning, different
#: sentence, so a caller who pushes twice does not hear the same words twice.
#: Deliberately none of them promises a lookup -- see UNOFFERED_REFUSAL.
REFUSAL_VARIANTS = (
    GROUNDED_REFUSAL,
    "That isn't something I can confirm from the university profile. I can help "
    "with anything else about programmes, fees or applying.",
    "I don't want to guess at that one. What I can do is take you through what "
    "the profile does cover.",
)

#: What the caller hears once refusals start repeating (BUG-17 follow-up).
#:
#: A guard must terminate. Twice is a refusal; a third time is a loop, and the
#: honest move at that point is to hand the caller to a human rather than say
#: the same thing again in new words. The contact details are the ones the
#: profile publishes (Contact Information).
#:
#: Two lines, alternating, and that is not decoration: measured on `fbced641`,
#: a single terminal line repeated six times is the same loop one step further
#: out. "Never the same twice running" has to hold for EVERY refusal, including
#: the last one -- otherwise the caller hears a stuck record and hangs up, which
#: is exactly what happened.
ESCALATION_REFUSALS = (
    "I've given you everything the university profile has on that. The "
    "admissions office can take it further -- admissions@meridian.edu, or "
    "+1 (555) 204-7890, Monday to Friday, 9 to 5.",
    "That's as far as I can take this one, and I'd rather say so than keep "
    "going round. Admissions can help on admissions@meridian.edu or "
    "+1 (555) 204-7890. Is there anything else I can look at for you?",
)

#: What the caller hears when the turn asserted something the university does
#: not offer. Two phrasings, alternated.
#:
#: BUG-17. `GROUNDED_REFUSAL` is the wrong sentence for this case, and it was
#: the one spoken on call 944b503f: "I don't have that detail in front of me. I
#: can check and come back to you" says the information exists somewhere and
#: that the agent will go and find it. Both are false, both are what the caller
#: pushed against for six turns, and the second is the promise that made the
#: capitulation feel like the agent finally doing its job.
#:
#: Two of them because the firm negative is the RIGHT answer and should not be
#: rotated away into vagueness -- but it must not be the same sentence twice
#: running either. A caller who pushes twice hears the same fact, twice, in
#: different words; a caller who pushes a third time is handed to a human.
UNOFFERED_REFUSALS = (
    "Meridian University does not offer {subject}. I can take you through the "
    "programmes we do offer, or put you in touch with the admissions office.",
    "{subject_cap} isn't one of Meridian University's programmes. I'm happy to "
    "go through what we do offer, or the admissions office can help further.",
)

#: "4 years", "$14,500", "3.5", "24 hours", "3-year".
#:
#: The leading guard is what stops `I-20` being read as the number 20: a digit
#: that follows a letter, another digit or a hyphen inside a token belongs to
#: that token, not to a claim of its own. Without it, every `I-20` in a
#: sentence becomes a spurious `20` looking for a source.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.\-/])"
    r"\$?\d[\d,]*(?:\.\d+)?\s*(?:-|\s)?\s*"
    r"(?:year|years|month|months|week|weeks|day|days|hour|hours|minute|minutes)?",
    re.IGNORECASE,
)


def _is_claimable_number(tok: str) -> bool:
    """Is this token specific enough to be worth checking?

    A bare `2` matches almost any passage, so it is not a claim. A decimal is:
    `3.5` was an invented GPA requirement, and the old rule ignored it because
    it counted digits and `3.5` has only two.
    """
    digits = re.sub(r"\D", "", tok)
    if not digits:
        return False
    if "$" in tok:
        return True
    if re.search(r"\d\.\d", tok):
        return True
    if re.search(r"\d\s*-?\s*(?:year|month|week|day|hour|minute)", tok, re.IGNORECASE):
        return True
    return len(digits) >= 3


def _number_claims(text: str) -> list[str]:
    # Japanese/Chinese replies may use full-width digits (１８,５００). NFKC
    # normalizes them to ASCII for the claim check; the spoken text is never
    # rewritten.
    normalized = unicodedata.normalize("NFKC", text or "")
    return [m.group(0).strip() for m in _NUMBER.finditer(normalized)
            if _is_claimable_number(m.group(0).strip())]


def _properties_present(text: str, index: EntityIndex) -> list[str]:
    """Name contents in `text` that the corpus DOES carry.

    Used for attribution -- these are the programmes the sentence is about, so
    these are the ones whose numbers it may quote -- and by `_is_substantive`,
    to decide whether a surviving sentence still says something specific.
    """
    return [c for c in name_candidates(text) if index.is_known(c)]


#: A denied thing is not a claimed thing. "Meridian University does not offer an
#: MDA program" is a CORRECT answer, and the replay over the archived calls
#: caught the guard refusing it -- the entity is unknown, so the name branch
#: read the denial as an invention. It is the mirror of the defect this branch
#: exists for, and the consequence is the same one BUG-16 taught: a caller
#: hears the agent refuse to answer a question it answered correctly.
#:
#: Any negation cue suppresses NAME claims for that sentence. Deliberately
#: blunt: a sentence that denies something may still be checked for numbers, and
#: errs toward letting a sentence through rather than refusing a true negative.
_NEGATION = re.compile(
    r"\b(?:not|no|never|none|nothing|cannot|without|nor)\b"
    r"|n't\b",
    re.IGNORECASE,
)


def _name_claims(text: str, index: EntityIndex) -> list[str]:
    """Name slots in `text` whose NAME the corpus does not carry.

    PER SLOT, and that is the fix. The first version asked "does this sentence
    name anything real?" and let one answer excuse every other name in the
    sentence. On live call `fbced641` that is exactly what happened:

        "...including a Master's in Mechanical Engineering and a PhD in
         Mechanical Engineering."

    `Master's` split into `Master` + `s`, the phrase `Master s` is in the corpus
    ("a relevant master's degree"), and the gate then passed the whole sentence
    -- including the second fabrication sitting beside it. Six fabrications were
    spoken to that caller.

    What survives of the old rule is the part that was right: a name whose own
    content the corpus knows is not a claim. "Master of Engineering" beside
    "Master of Arts" is a slightly-wrong rename and passes, which is the BUG-16
    trade this register already documented -- but it now earns that on its own
    name, never on a neighbour's.

    A DENIAL is not a claim either -- see `_NEGATION`.
    """
    if _NEGATION.search(text or ""):
        return []
    out: list[str] = []
    for slot in name_slots(text):
        if slot.is_established(index):
            continue
        if slot.phrase not in out:
            out.append(slot.phrase)
    return out


def _grounded_in(needle: str, hay: str, hay_core: str) -> bool:
    """Whole-token presence, with a punctuation-blind fallback.

    The fallback is kept because the corpus writes "4 years" where an answer
    writes "4-year", and folding those was a hard-won fix. What is NOT kept is
    unbounded substring matching, which is how the invented `1200` was accepted
    inside `$11,200`: a run of digits has to be its own run, not a fragment of a
    longer one.
    """
    if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay):
        return True
    core = re.sub(r"[^a-z0-9]", "", needle)
    if not core:
        return False
    if core.isdigit():
        return re.search(rf"(?<!\d){re.escape(core)}(?!\d)", hay_core) is not None
    return core in hay_core

def ungrounded_claims(answer: str, context: str,
                      entities: EntityIndex | None = None,
                      *, check_names: bool = True) -> list[str]:
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

    `entities` is the corpus index the NAME branch compares against. It
    defaults to the real one, so the index does not change from turn to turn;
    it is a parameter only so a test can supply a literal corpus.
    """
    if not answer or not answer.strip():
        return []
    if not (context or "").strip():
        return []
    hay = _norm(context)
    hay_core = re.sub(r"[^a-z0-9]", "", hay)
    if not hay_core:
        return []
    index = entities if entities is not None else default_index()

    out: list[str] = []

    # -- NUMBERS: present in the evidence, and owned by what is described ---
    #
    # Two questions, because the first one alone let the worst case through.
    # `$14,600` is a real number -- it is M.Tech's tuition -- and the model
    # attached it to a programme that does not exist. "Is it in the evidence"
    # said yes. "Is it this entity's" is the question that catches it.
    named = _properties_present(answer, index)
    owned = index.programme_numbers_for(named)
    for claim in _number_claims(answer):
        needle = _norm(claim)
        if not needle:
            continue
        if not _grounded_in(needle, hay, hay_core):
            out.append(claim)
            continue
        # It is somewhere in the evidence. Is it THIS sentence's entity's?
        #
        # Only when a programme was actually identified. "Eligibility is a
        # bachelor's degree, and tuition is $18,500 per year" names "bachelor",
        # which owns no numbers -- and treating that as "a programme was named,
        # and it cannot claim $18,500" refused a correct answer about the MBA.
        # No programme identified means no attribution, not strict attribution.
        if not owned:
            continue
        digits = re.sub(r"\D", "", needle)
        if not digits or digits in index.global_numbers:
            continue
        if digits in index.all_programme_numbers and digits not in owned:
            out.append(claim)

    # -- NAMES: is this a thing the corpus actually states? -----------------
    # In relaxed mode for a translated answer, the English entity index cannot
    # match a translated programme name, so only numbers are enforced. The
    # prompt still asks the model to keep Latin-script names; this is the
    # safety net for when it does not.
    if check_names:
        out.extend(_name_claims(answer, index))
    return out


def is_grounded(answer: str, context: str,
                entities: EntityIndex | None = None,
                *, check_names: bool = True) -> bool:
    return not ungrounded_claims(
        answer, context, entities, check_names=check_names
    )


def _sentences(text: str) -> list[str]:
    # Indic scripts use the danda, not a capitalised next sentence. CJK uses
    # 。！？ with no following space, so its boundary is zero-width.
    return [s for s in re.split(
        r"(?<=[.!?])\s+|(?<=[।॥])\s*|(?<=[。！？])\s*", text or ""
    ) if s.strip()]


#: A sentence is worth speaking on its own only if it still carries a specific
#: grounded fact -- a number, or a NAME the corpus knows. Checked as a function
#: rather than one regex because the sentence's FIRST word is always
#: capitalised, so a pattern matching any capitalised word counts "Would you
#: like to know more about that?" as substantive. That is how a dangling
#: question about a deleted sentence survived the first version of this guard.
def _is_substantive(sentence: str, index: EntityIndex) -> bool:
    if re.search(r"\$?\d", sentence):
        return True
    return bool(_properties_present(sentence, index))


def enforce_grounding(answer: str, context: str, *,
                      entities: EntityIndex | None = None,
                      unoffered: list[str] | None = None,
                      refusal_index: int = 0,
                      subject_denied: int = 0,
                      check_names: bool = True,
                      language: str | None = None,
                      log=None) -> str:
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

    `unoffered` is what the caller has asked about this call that the university
    does not have, as recorded by the session from the caller's own words. When
    a refusal is forced and one of those is what the turn was about, the refusal
    is the plain negative (`UNOFFERED_REFUSAL`) rather than the generic "I don't
    have that detail", which promises a lookup that does not exist.

    `refusal_index` is how many turns IN A ROW this session has now refused,
    owned by the caller -- the guard is pure and cannot count. It selects the
    wording, and past two it escalates to the human channel instead of saying
    the same thing a third time. See `REFUSAL_VARIANTS`.
    """
    if not answer or not answer.strip():
        return answer
    index = entities if entities is not None else default_index()
    kept, dropped = [], []
    for sentence in _sentences(answer):
        bad = ungrounded_claims(
            sentence, context, index, check_names=check_names
        )
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
    if survivors and (
        not check_names or any(_is_substantive(s, index) for s in kept)
    ):
        return survivors
    if log is not None and survivors:
        log("GROUNDING_REFUSED", reason="survivors carried no grounded fact",
            survivors=survivors[:120])

    subject = _refusal_subject(dropped, unoffered)
    variants, escalations, unoffered_lines, _markers = _refusal_strings(language)
    if subject and subject_denied < len(unoffered_lines):
        if log is not None:
            log("GROUNDING_UNOFFERED", subject=subject[:80])
        mid = _mid_sentence(subject)
        return unoffered_lines[subject_denied].format(
            subject=mid, subject_cap=mid[:1].upper() + mid[1:])
    if log is not None:
        log("GROUNDING_ESCALATED", refusals=refusal_index)
    return _refusal_for(refusal_index, language)


def _refusal_strings(language: str | None = None):
    """The refusal wording for a language, and its unoffered-line markers.

    English is the default and returns the exact constants above, so every
    pre-foreign call is byte-identical. The six foreign sets live in
    app/localized_text.py.
    """
    code = normalize_language_code(language)
    if code:
        from app.localized_text import GROUNDING_REFUSALS

        entry = GROUNDING_REFUSALS.get(code)
        if entry:
            return (
                entry["variants"],
                entry["escalations"],
                entry["unoffered"],
                entry["unoffered_markers"],
            )
    return REFUSAL_VARIANTS, ESCALATION_REFUSALS, UNOFFERED_REFUSALS, ()


def _refusal_for(refusal_index: int, language: str | None = None) -> str:
    """The line for the Nth consecutive refusal. Never the same twice running."""
    variants, escalations, _unoffered, _markers = _refusal_strings(language)
    if refusal_index < len(variants):
        return variants[refusal_index]
    return escalations[(refusal_index - len(variants))
                       % len(escalations)]


def is_refusal(text: str, language: str | None = None) -> bool:
    """Did the BOUNDARY produce this line, rather than the model?

    The session uses this to count consecutive refusals, which is what bounds
    the loop. It lives here so the set of refusal wordings and the test for
    them cannot drift apart -- a caller that prefix-matched the strings would
    silently stop counting the day a variant was reworded.

    English markers are always checked, because an English refusal can still
    surface in a foreign-language call. The localized unoffered lines contain
    the caller's subject, so a stable phrase from each phrasing is the marker.
    """
    if not text:
        return False
    if "Meridian University does not offer" in text:
        return True
    if "isn't one of Meridian University's programmes" in text:
        return True
    variants, escalations, _unoffered, markers = _refusal_strings(language)
    if text in (*variants, *escalations):
        return True
    if any(marker in text for marker in markers):
        return True
    return any(text == line for line in
               (*REFUSAL_VARIANTS, *ESCALATION_REFUSALS))


#: A subject the extractor read out of the caller's own words keeps the caller's
#: casing, so "Mechanical engineering program" arrives capitalised and lands
#: mid-sentence in the refusal. Lowercase it unless it carries an acronym or an
#: abbreviation, which is the one case where the capitals are the word.
_ACRONYMISH = re.compile(r"\b[A-Z]{2,}\b|\.")


def _mid_sentence(subject: str) -> str:
    return subject if _ACRONYMISH.search(subject) else subject.lower()


def _refusal_subject(dropped: list[tuple[str, list[str]]],
                     unoffered: list[str] | None) -> str:
    """Which unoffered thing this refused turn was about, if any.

    The longest one the caller actually named AND the turn actually mentioned,
    so the sentence the caller hears matches the subject they raised. Length
    wins because the caller's "mechanical program" yields "mechanical", and
    "mechanical engineering" is the thing worth naming back to them.

    Only the session's list is used, never a phrase lifted from the model's own
    output. A fabricated name is exactly the text that must not be echoed back
    as though it were a real programme the university has a position on.
    """
    if not unoffered:
        return ""
    text = " ".join(s for s, _ in dropped).lower()
    candidates = [u for u in unoffered if u.lower() in text]
    if not candidates:
        return ""
    return max(candidates, key=len)
