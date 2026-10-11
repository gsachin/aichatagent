"""Client-demo track — BUG-07/BUG-18: a spoken claim must be traceable to a source.

Scope, decided on 2026-10-10 and REOPENED on 2026-10-11 after call `944b503f`:

    NUMBERS are checked, and so are NAMES.

Names were checked once, removed, and are back. The history matters, because
the reason they were removed is the reason they can now return.

    REMOVED, because four real decisions from the QA calls:
      FALSE POSITIVES (all name lists, all refused a correct answer)
        "We offer ... fields such as Engineering, Computer Science, Business ..."
        "a 2-year Master of Arts in Arts and a 3-year Bachelor of Arts in Arts"
        "Master of Arts, Master of Business Administration, Master of Engineering"
      TRUE POSITIVES (all numbers)
        "a 10% discount for international students"   -- no file mentions it
        "within the next 24 hours"                    -- nothing can schedule that
        "$14,500 per year"                            -- the context said $14,200

    BROUGHT BACK, because call `944b503f` (doc/voice-demo/REVIEW-2026-10-10-
    inbound.md) showed what the absence cost: the agent told a caller about a
    "mechanical engineering program", priced it at $14,600, and gave it a GPA
    and SAT requirement. None of it exists. The boundary ran on that call and
    let every one of them through.

Three things changed, and each is a test below:

  * Names are checked against the CORPUS (`app/kb_entities.py`), not against
    this turn's retrieval. Absence from one retrieval is not absence from the
    university -- judging it that way is what produced the false positives.
  * Matching is CASE-INSENSITIVE. The old check caught "Master of Mechanical
    Engineering" and missed "our mechanical engineering program" on the very
    next turn, because only the first was capitalised.
  * A name is a defect only when the sentence names NOTHING real. That is the
    gate that lets the three false positives above pass while catching the
    fabrication. `test_a_programme_list_is_SPOKEN` is unchanged and must stay
    passing -- if it breaks, this change is wrong, not the test.

The cost is still asserted rather than left implicit: `test_an_invented_name_
IS_caught` was `..._is_NOT_caught_this_is_the_known_trade` and its message read
"if this now FAILS, the name branch is back". It failed; the branch is back.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kb_entities import EntityIndex  # noqa: E402
from app.voice_grounding import (  # noqa: E402
    GROUNDED_REFUSAL, enforce_grounding, is_grounded, ungrounded_claims,
)

#: The real retrieved context for the "duration and curriculum" turn.
CTX = (
    "[§ Undergraduate Programs] B.Tech in Information Technology at Meridian "
    "University: 4 years, School of Computer Science. Eligibility: 10+2 with "
    "Physics, Chemistry and Mathematics (PCM). Tuition: $14,200 per year."
)

#: The context the programme-list turn actually had. FP2 below carries the
#: numbers 2-year and 3-year, and those are GROUNDED here -- checking them
#: against CTX would fail for the right reason (wrong fixture) and look like a
#: code fault. Fixtures must be the turn's real context, not a convenient one.
CTX_ARTS = (
    "[§ Undergraduate Programs] Bachelor of Arts (BA) at Meridian University: "
    "3 years, School of Arts. Tuition: $7,900 per year. "
    "[§ Postgraduate Programs] Master of Arts (MA) at Meridian University: "
    "2 years, School of Arts."
)

#: The M.Sc entry, and the reason `1200` used to pass: `$11,200` contains it.
CTX_SCIENCE = (
    "[§ Postgraduate Programs] Master of Science (M.Sc) at Meridian University: "
    "2 years, School of Science. Eligibility: a bachelor's degree in science. "
    "Tuition: $11,200 per year."
)

#: Two real programmes with two real tuitions, for the attribution check.
CTX_TWO = (
    "[§ Undergraduate Programs] B.Tech in Computer Science at Meridian "
    "University: 4 years, School of Computer Science. Tuition: $14,500 per "
    "year. [§ Postgraduate Programs] M.Tech (various specializations) at "
    "Meridian University: 2 years, School of Engineering. Tuition: $14,600 "
    "per year."
)

#: The MBA, plus a fee that belongs to no programme in particular.
CTX_FEES = (
    "[§ Postgraduate Programs] Master of Business Administration (MBA) at "
    "Meridian University: 2 years, School of Business / Management. Tuition: "
    "$18,500 per year. [§ Fees Structure] Meridian University application / "
    "processing fee (one-time, non-refundable): $60 for undergraduate and $75 "
    "for postgraduate applications."
)


# ── what must be STOPPED (all real, all from live calls) ─────────────

@pytest.mark.parametrize("said,claim", [
    # the fabricated discount, verbatim from the 2026-10-10 QA call
    ("Our Postgraduate Program in Computer Science tuition fees are $9,600 per "
     "year, with a 10% discount for international students.", "10%"),
    # the ungrounded commitment, verbatim
    ("I'll make sure to get back to you within the next 24 hours.", "24 hour"),
    # the wrong tuition: the context says $14,200
    ("Our B.Tech in Information Technology costs $14,500 per year.", "14,500"),
])
def test_an_ungrounded_number_is_caught(said, claim):
    assert not is_grounded(said, CTX)


def test_the_wrong_tuition_is_caught():
    assert not is_grounded(
        "Our B.Tech in Information Technology program costs $14,500 per year.", CTX)


# ── what must PASS, unchanged (all real false positives) ─────────────

@pytest.mark.parametrize("said,ctx", [
    # FP1 — refused a caller on the first QA call
    ("We offer undergraduate and postgraduate programs in fields such as "
     "Engineering, Computer Science, Business, Arts, Science.", CTX),
    # FP2 — correct programmes and durations, clumsy phrasing. Checked against
    # the ARTS context, because its 2-year and 3-year are grounded there.
    ("We offer a 2-year Master of Arts in Arts and a 3-year Bachelor of Arts "
     "in Arts.", CTX_ARTS),
    # FP3 — refused a caller on the second QA call
    ("We offer a range of postgraduate programs, including Master of Arts, "
     "Master of Business Administration, and Master of Engineering.", CTX),
])
def test_a_programme_list_is_SPOKEN(said, ctx):
    """The failures that removed the name branch.

    Each of these was thrown away in front of a caller with "I don't have that
    detail in front of me" -- about the university's own programmes.
    """
    assert is_grounded(said, ctx)
    assert enforce_grounding(said, ctx) == said


def test_a_grounded_number_is_returned_byte_identical():
    said = ("B.Tech in Information Technology at Meridian University is a 4 years "
            "program with tuition of $14,200 per year.")
    assert is_grounded(said, CTX)
    assert enforce_grounding(said, CTX) == said


def test_number_words_and_digits_are_the_same_claim():
    """The regression that made the first version of this guard unusable.

    The context writes "4 years"; an answer saying "four-year" is CORRECT. An
    earlier check flagged it because it compared strings, and a guard that
    rejects correct answers is worse than no guard.
    """
    for said in ("It is a four year program.", "It is a 4-year program.",
                 "It is a 4 years program."):
        assert is_grounded(said, CTX), said


def test_money_punctuation_does_not_change_the_claim():
    assert is_grounded("Tuition is 14200 per year.", CTX)
    assert is_grounded("Tuition is $14,200 per year.", CTX)


def test_a_sentence_starting_capital_is_not_a_claim():
    assert is_grounded("Would you like to know more?", CTX)
    assert ungrounded_claims("Thank you for calling today.", CTX) == []


# ── the fabricated names the boundary now catches ────────────────────

@pytest.mark.parametrize("said", [
    # the original BUG-07 fabrication -- an invented school, no number
    "We have a unique program in our School of International Studies.",
    # heard on the 2026-10-10 QA call, alongside a database claim
    "I checked our database again, and it appears we have a Master of "
    "Mechanical Engineering program.",
    # general knowledge the knowledge base does not carry (0 files mention it)
    "You will need to pay the Department of Homeland Security and a SEVIS fee.",
])
def test_an_invented_name_IS_caught(said):
    """This test used to assert the OPPOSITE, and its message said so.

    It read `test_an_invented_name_is_NOT_caught_this_is_the_known_trade`, with
    the assertion message "if this now FAILS, the name branch is back". It was
    an honest record of a deliberate limitation, and it was the tripwire for
    exactly this change.

    What it measured was real -- a refusal is worse than the fabrication it
    prevents -- but the measurements that justified it were taken while the
    BUG-16 wiring fault had emptied the evidence list, so everything was being
    refused. Names are judged against the corpus now, and the programme-list
    cases above stay green.
    """
    assert not is_grounded(said, CTX)
    assert enforce_grounding(said, CTX) == GROUNDED_REFUSAL


@pytest.mark.parametrize("said", [
    # the exact sentence that walked past the guard on call 944b503f, turn 17.
    # Capitalised, the old regex caught it; lowercase, it did not.
    "Yes, I have found the information on our mechanical engineering program, "
    "and it's a 4-year degree.",
    # turn 25 -- the same fabrication, priced with a REAL number ($14,600 is
    # M.Tech's tuition), which is why presence-alone could not catch it
    "Our mechanical engineering program is a 4-year degree with a tuition of "
    "$14,600 per year.",
    # turn 38 -- an invented admission requirement. "3.5" is a decimal and was
    # never a claim; "1200" matched inside the M.Sc tuition "$11,200".
    "We require a minimum GPA of 3.5 and SAT scores of 1200 or higher for "
    "admission to our mechanical engineering program.",
    # turn 34 -- fabricated curriculum, which the KB says is not published
    "We have a mechanical engineering program, which is a highly "
    "interdisciplinary field that combines principles from physics, "
    "mathematics, and materials science.",
])
def test_the_call_944b503f_fabrications_are_caught(said):
    """Verbatim from the call that reopened this branch. Each one was SPOKEN."""
    assert not is_grounded(said, CTX)


def test_case_does_not_decide_whether_a_name_is_checked():
    """BUG-18(a). The escape from the old guard was a shift key.

    Turn 12 of call 944b503f wrote "a Master of Mechanical Engineering program"
    and was dropped. Turn 17 wrote "our mechanical engineering program" and was
    spoken. Same fabrication, same call, four turns apart.
    """
    for said in ("We have a Master of Mechanical Engineering program.",
                 "we have a master of mechanical engineering program.",
                 "We have a mechanical engineering program.",
                 "WE HAVE A MECHANICAL ENGINEERING PROGRAM."):
        assert not is_grounded(said, CTX), said


def test_a_number_is_not_matched_inside_a_longer_number():
    """BUG-18(c). `1200` was accepted because it sits inside `$11,200`.

    CTX_SCIENCE carries the M.Sc tuition $11,200, which is what the invented SAT
    score matched against once punctuation was stripped.
    """
    assert not is_grounded(
        "We require SAT scores of 1200 or higher.", CTX_SCIENCE)
    # ...while the number that IS the tuition stays grounded.
    assert is_grounded("The tuition is $11,200 per year.", CTX_SCIENCE)


def test_a_decimal_is_a_claim():
    """BUG-18(c). `3.5` counted as two digits, so an invented GPA was invisible."""
    assert not is_grounded("We require a minimum GPA of 3.5.", CTX)


def test_a_number_must_belong_to_the_programme_it_is_attached_to():
    """BUG-18(d). Presence is not attribution.

    CTX_TWO carries two real programmes and two real tuitions. Swapping them
    keeps every number present in the evidence, so only an attribution check can
    see it -- and this is BUG-07's original defect shape, "$14,500 when the
    context said $14,200", one level harder.
    """
    assert is_grounded("B.Tech in Computer Science costs $14,500 per year.", CTX_TWO)
    assert not is_grounded(
        "B.Tech in Computer Science costs $14,600 per year.", CTX_TWO)


def test_a_shared_fee_is_not_policed_by_attribution():
    """The application fee belongs to no programme, so it attaches to any.

    Attribution has to be narrow or it refuses sentence after sentence: every
    caller who asks "what does the MBA cost, and is there an application fee"
    would be told the agent does not have the detail.
    """
    assert is_grounded("The MBA costs $18,500 per year.", CTX_FEES)
    assert is_grounded(
        "The MBA costs $18,500 per year, plus a $60 application fee.", CTX_FEES)


# ── the corpus index, which is what names are judged against ─────────

def test_names_are_judged_against_the_corpus_not_this_turn():
    """The distinction that makes the name branch usable at all.

    A sentence naming a real programme is grounded even when THIS turn's
    retrieval is about something else entirely -- which is the false positive
    that removed the branch the first time.
    """
    unrelated = "[§ Contact Information] Phone: +1 (555) 204-7890.\nCaller: hello"
    assert is_grounded("We offer a Master of Business Administration.", unrelated)


def test_an_index_can_be_supplied_for_a_literal_corpus():
    """The index is a parameter so a test need not depend on the shipped KB."""
    small = EntityIndex(
        "## Undergraduate Programs\n\n### astro\n\n"
        "B.Tech in Astrology at Meridian University: 4 years. "
        "Tuition: $1,000 per year.\n")
    assert small.is_known("B.Tech in Astrology")
    assert not small.is_known("B.Tech in Computer Science")
    assert not is_grounded("We have a Mechanical Engineering program.", CTX,
                           small)
    assert is_grounded("We have a B.Tech in Astrology program.", CTX, small)


# ── BUG-18, second round: the live call that beat the first fix ──────
#
# Every sentence here is verbatim from call `fbced641` (2026-10-11), where SIX
# fabrications were spoken and three correct answers were refused. The first
# fix passed its own archive gate 7/7 and still missed all of these, because
# the archive held the phrasings it had and the model produced ones it did not.

@pytest.mark.parametrize("said", [
    # turns 2-3. The guard missed it because "bachelor's" split into
    # `bachelor` + `s`, and the phrase `bachelor s` IS in the corpus
    # ("a bachelor's degree").
    "To be eligible for our mechanical engineering program, you'll need to have "
    "completed a bachelor's degree in mechanical engineering or a related field.",
    "To be eligible, you'll need to have completed a bachelor's degree in "
    "mechanical engineering or a related field. Can you tell me what your GPA is?",
    # turn 8. TWO fabrications in one sentence, and `Master's` did the same
    # trick: the phrase `Master s` is in the corpus ("a relevant master's
    # degree"), and the gate then suppressed the whole sentence -- so the
    # second fabrication rode along on the first one's excuse.
    "We have a few different program options available, including a Master's in "
    "Mechanical Engineering and a PhD in Mechanical Engineering.",
    # turn 9
    "We have a Master's in Mechanical Engineering with a focus on advanced "
    "materials and manufacturing, and a PhD in Mechanical Engineering with a "
    "specialization in robotics and automation.",
    # turn 11
    "The tuition for our PhD in Mechanical Engineering is $9,600 per year.",
])
def test_the_live_call_fabrications_are_caught(said):
    assert not is_grounded(said, CTX)


def test_a_possessive_is_not_a_known_name():
    """The artifact that hid six fabrications. `bachelor s` is not an entity."""
    assert not is_grounded("I have a bachelor's degree in mechanical engineering.", CTX)
    # ...and the mirror image, so the fix is not a blanket ban on possessives.
    assert is_grounded("I have a bachelor's degree in computer science.", CTX)


def test_each_name_is_judged_on_its_own_content():
    """PER SLOT. One known name must not excuse another name beside it."""
    assert not is_grounded(
        "We offer a Master's in Mechanical Engineering and a PhD in "
        "Mechanical Engineering.", CTX)
    # a list of REAL programmes still passes -- the BUG-16 trade, per slot
    assert is_grounded(
        "We offer a Bachelor of Arts and a Master of Arts.", CTX)


def test_a_keyword_first_name_needs_a_connective():
    """`program specifically` is not a NAME SLOT; `PhD in X` is.

    The first fix dropped "It seems like you're interested in the PhD program,
    specifically the robotics and automation specialization." because the walk
    ran past the keyword into an adverb. Adding "specifically" to a stoplist
    would have been the sixth wording of the same mistake -- the register has
    recorded since BUG-03 that enumeration is the wrong mechanism.
    """
    assert is_grounded(
        "It seems like you're interested in the PhD program, specifically the "
        "robotics and automation specialization.", CTX)
    # but a connective makes it a slot, and an unknown name there IS a claim
    assert not is_grounded("We have a PhD in Mechanical Engineering.", CTX)


def test_a_single_word_is_not_a_name_after_a_keyword():
    """`interdisciplinary program` and `providing program details` are English."""
    assert is_grounded("It's a highly interdisciplinary program that combines "
                       "theoretical foundations with practical applications.", CTX)
    assert is_grounded("Can you help us understand what would make our system "
                       "more accurate in providing program details?", CTX)
    # two words is a name, and an unknown one is still caught
    assert not is_grounded("We have a Mac Datonix program.", CTX)


def test_a_coordinating_and_does_not_make_a_name():
    """"your degree and GPA" is a pair of requirement types, not a programme.

    Caught by the archive sweep, not by a test: with "and" admitted as a
    connective, `degree and GPA` became a name slot and the guard refused a
    correct eligibility question in front of a caller.
    """
    assert is_grounded(
        "Can I confirm your eligibility requirements, such as your degree and GPA?",
        CTX)


# ── BUG-29: recombination of real names into an invented one ─────────
#
# Live call `2e9952a7`, 2026-10-11. The agent said "Master of Science in
# Computer Science" SIX times -- an invention -- and the guard never fired,
# because the phrase splits into two genuine Meridian names and each was
# established on its own. The invention is in the JOIN.

@pytest.mark.parametrize("said", [
    "Our Master of Science in Computer Science program is designed to help "
    "you develop advanced skills.",
    "Our tuition for the Master of Science in Computer Science program is "
    "$9,600 per year.",
    "We have a Bachelor of Commerce in Computer Science program.",
])
def test_a_degree_from_another_school_is_caught(said):
    assert not is_grounded(said, CTX)


@pytest.mark.parametrize("said,ctx", [
    # the SAME grammar, and correct: a degree with a trailing field, both from
    # one school. This is the phrase that makes the rule non-syntactic.
    # Checked against the ARTS context, because its 2-year and 3-year are
    # grounded there -- the fixture must be the context the numbers came from.
    ("We offer a 2-year Master of Arts in Arts and a 3-year Bachelor of Arts "
     "in Arts.", CTX_ARTS),
    ("We offer a Master of Science.", CTX),
    ("We offer a Master of Science in Science.", CTX),
    ("Our Master of Business Administration program is 2 years.", CTX_FEES),
])
def test_a_degree_within_its_own_school_is_spoken(said, ctx):
    assert is_grounded(said, ctx)


def test_the_school_rule_abstains_when_it_cannot_reason():
    """A guard that invents grounds to flag is BUG-16 again.

    An unrecognised degree has no school to compare against, so the rule falls
    back to the ordinary content test rather than condemning the phrase.
    """
    from app.kb_entities import default_index

    index = default_index()
    assert index.programme_school_of("Master of Science") == "science"
    assert index.programme_school_of("Bachelor of Arts") == "arts"
    assert index.programme_school_of("Master of Nonsense") == ""
    assert index.school_of_phrase("Computer Science") == "computer science"
    assert index.school_of_phrase("Zoology") == ""


# ── the enforcement policy ───────────────────────────────────────────

def test_a_dropped_sentence_does_not_leave_a_dangling_question():
    """Measured failure of the first enforcement policy.

    Dropping the offending sentence left "Would you like to know more about
    that?" -- a question about something the caller never heard.
    """
    said = ("Our program costs $99,000 per year for international students. "
            "Would you like to know more about that?")
    out = enforce_grounding(said, CTX)
    assert out == GROUNDED_REFUSAL
    assert "know more about that" not in out


def test_a_partial_drop_does_not_leave_a_vaguer_promise():
    """The other measured failure.

    Cutting "within 24 hours" left the promise intact and harder to notice.
    """
    said = ("We'll expedite the scholarship review process for you. I'll make "
            "sure to get back to you within the next 24 hours.")
    out = enforce_grounding(said, CTX)
    assert "24 hour" not in out
    assert "expedite" not in out


def test_the_boundary_never_returns_silence():
    """BUG-05 is the empty turn. A guard must not create one."""
    for said in ("Fees are $99,000 for the MBA.",
                 "The deposit is $5,555 payable in 36 hours.",
                 "Tuition rose 44% in 2019."):
        out = enforce_grounding(said, CTX)
        assert out and out.strip(), f"silence for {said!r}"


def test_a_mixed_turn_keeps_its_grounded_sentence():
    """A grounded sentence survives when it still carries a specific fact."""
    said = ("B.Tech in Information Technology lasts 4 years. "
            "The deposit is $5,555.")
    out = enforce_grounding(said, CTX)
    assert "4 years" in out
    assert "5,555" not in out


def test_empty_input_is_handled():
    assert enforce_grounding("", CTX) == ""
    assert ungrounded_claims("", CTX) == []


# ── fail-open, and evidence rules ────────────────────────────────────

def test_empty_evidence_means_no_judgement_not_everything_is_a_lie():
    """The lesson of the 2026-10-10 QA call.

    A wiring fault (`ContextVar` set inside `asyncio.to_thread`, which copies
    the context in but not out) left the evidence list empty. The guard then
    refused CORRECT answers. With nothing to check against it has no
    information, and absence of evidence is not evidence of fabrication.
    """
    real_answer = ("We offer undergraduate and postgraduate programs in fields "
                   "such as Engineering, Computer Science, Business, Arts, Science.")
    for empty in ("", "   ", "\n", None):
        assert ungrounded_claims(real_answer, empty) == [], repr(empty)
        assert enforce_grounding(real_answer, empty) == real_answer, repr(empty)


def test_the_boundary_still_fires_when_there_is_evidence():
    """Fail-open must not become never-fires."""
    assert not is_grounded("The deposit is $5,555.", CTX)


def test_something_the_caller_said_is_grounded():
    """The caller's own words are evidence -- repeating them is not a lie."""
    said = "The fee is $3,333 per year."
    assert not is_grounded(said, CTX)
    assert is_grounded(said, CTX + "\nCaller: they told me $3,333")


def test_the_agents_own_output_is_not_evidence():
    """A fabrication must not launder itself.

    Measured during validation: when the agent's turns were added to the
    evidence list, an invented figure on one turn grounded every later turn
    that repeated it.
    """
    assert not is_grounded("The deposit is $5,555.", CTX)


# ── the wiring, not the policy ───────────────────────────────────────

def test_evidence_survives_the_real_threaded_retrieval_path():
    """The test that was missing, and whose absence cost a live call.

    Every other test here calls `enforce_grounding` directly with a context.
    The live path does not: `query_rag_stream` runs `retrieve_context` inside
    `asyncio.to_thread`, and a `ContextVar.set()` in that worker thread is
    DISCARDED -- `to_thread` copies the calling context in, not out.

    Measured on the 2026-10-10 QA call: retrieval returned 1,087 chars, the
    evidence read "", and the boundary refused correct answers in front of a
    caller. So this asserts on the SEAM, not on the policy.
    """
    import asyncio

    from app.rag import last_retrieved_context, query_rag_stream

    async def run() -> str:
        try:
            async for _ in query_rag_stream(
                "What programmes do you offer?", mode="voice",
                retrieval_query="What programmes do you offer?",
            ):
                break                      # evidence is set before the first yield
        except Exception:                  # noqa: BLE001
            pass                           # generation may fail; retrieval already ran
        return last_retrieved_context()

    evidence = asyncio.run(run())
    assert evidence, (
        "evidence was empty after a real streamed retrieval: the boundary would "
        "check nothing and refuse correct answers (BUG-16, 2026-10-10)")
    assert "computer science" in evidence.lower()
