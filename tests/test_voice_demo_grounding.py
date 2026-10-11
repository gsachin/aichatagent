"""Client-demo track — BUG-07: a spoken NUMBER must be traceable to a source.

Scope, decided on 2026-10-10 after the boundary was run on live calls: this
checks NUMBERS ONLY. Multi-word names were checked too, and were removed.

The evidence that decided it -- four real decisions from the QA calls:

    FALSE POSITIVES (all name lists, all refused a correct answer)
      "We offer ... fields such as Engineering, Computer Science, Business ..."
      "a 2-year Master of Arts in Arts and a 3-year Bachelor of Arts in Arts"
      "Master of Arts, Master of Business Administration, Master of Engineering"

    TRUE POSITIVES (all numbers)
      "a 10% discount for international students"   -- no file mentions it
      "within the next 24 hours"                    -- nothing can schedule that
      "$14,500 per year"                            -- the context said $14,200

A number is either in the source or it is invented. A name list is not
decidable: "Master of Arts, Master of Business Administration and Master of
Engineering" is a CORRECT answer containing names absent from that turn's
retrieval, while "B.Tech in Computer Science" is a renaming of "B.Tech in
Information Technology". Same shape, opposite verdicts.

The cost is asserted below rather than left implicit: an invented NAME with no
number attached now passes.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


# ── the cost of the decision, asserted so it cannot be forgotten ─────

@pytest.mark.parametrize("said", [
    # the original BUG-07 fabrication -- an invented school, no number
    "We have a unique program in our School of International Studies.",
    # heard on the 2026-10-10 QA call, alongside a database claim
    "I checked our database again, and it appears we have a Master of "
    "Mechanical Engineering program.",
    # general knowledge the knowledge base does not carry (0 files mention it)
    "You will need to pay the Department of Homeland Security and a SEVIS fee.",
])
def test_an_invented_name_is_NOT_caught_this_is_the_known_trade(said):
    """Deliberate, documented limitation -- not an oversight.

    Narrowing to numbers gave up invented names. It was accepted because a
    refusal is worse than the fabrication it prevents: a caller hearing
    "I don't have that detail in front of me" about the university's own MBA
    has watched the agent break, whereas a plausible extra name reads as
    helpfulness until somebody checks.

    BUG-07 therefore stays OPEN for this class. If a name check is ever
    reintroduced it must first solve the programme-list problem above.
    """
    assert is_grounded(said, CTX), "if this now FAILS, the name branch is back"


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
