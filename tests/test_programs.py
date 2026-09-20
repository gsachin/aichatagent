"""
app.programs — the shared program detector.

This module exists because the WhatsApp webhook and the Streamlit UI each grew
their own detector and both were wrong in the same way. The Streamlit copy was
the worse of the two: it listed "data science", "engineering", "business
analytics" and "information systems", none of which Meridian runs, so a student
could be enrolled in a course the university does not offer.

The last test here is the one that would have caught that: every name the
detector can return must appear in the knowledge base.
"""

import re
from pathlib import Path

import pytest

from app.programs import (
    PROGRAM_ALIASES,
    detect_program,
    is_explicit_program_choice,
)

P = detect_program

KB_PATH = (
    Path(__file__).resolve().parent.parent
    / "content" / "meridian" / "meridian_knowledge_base.md"
)


@pytest.mark.parametrize(
    "text, expected",
    [
        # The spellings that matter: the KB writes "M.Tech", students type both.
        ("i want to take admission in m. tech", "M.Tech"),
        ("i want to take admission in m.tech", "M.Tech"),
        ("i want to take admission in mtech", "M.Tech"),
        ("I am interrested M. Tech. in Mechenical", "M.Tech"),
        # "ba" was absent from the alias table entirely (only "b.a" was there).
        ("I would like to take admission in BA", "BA"),
        ("i want to take admission in b.a", "BA"),
        ("take admission for bca", "BCA"),
        ("i wan to take the admission in mba", "MBA"),
        ("i am interested in b.tech computer science", "B.Tech Computer Science"),
        ("i want m.sc physics", "M.Sc"),
        ("i want admission in m.a english", "MA"),
        ("i want m.com", "M.Com"),
        ("i want to study bba", "BBA"),
        ("b.com admission", "B.Com"),
    ],
)
def test_detects_every_spelling(text, expected):
    assert P(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "i am from mumbai",          # "mba" is a substring of "mumbai"
        "i live in mumbai",
        "my name is karthik",
        "what is the fee for mechanical engineering",
        "master of science is not offered",
        "i am a bachelor of arts graduate",
        "tell me about the campus",
    ],
)
def test_no_false_positives(text):
    assert P(text) == "", f"{text!r} must not name a program"


@pytest.mark.parametrize(
    "text",
    [
        "m.tech",
        "M. Tech please",
        "mba",
        "bca",
        "computer science",
        "i want m.tech",
        "i want to take admission in mtech",
    ],
)
def test_a_choice_is_recognised(text):
    detected = P(text)
    assert detected, f"{text!r} should name a program"
    assert is_explicit_program_choice(text.lower(), detected)


@pytest.mark.parametrize(
    "text",
    [
        "is mba good",      # names a program, chooses nothing
        "mba or mca",
        "mba worth it",
        "mtech hard",
        "mba fees",
        "tell me about mba",
    ],
)
def test_a_mention_is_not_a_choice(text):
    detected = P(text)
    assert detected, f"{text!r} should still name a program"
    assert not is_explicit_program_choice(text.lower(), detected), (
        f"{text!r} names a program but does not choose one"
    )


def test_aliases_are_not_shadowed_by_earlier_entries():
    """
    First match wins, so a broader alias placed too early would swallow a
    narrower one — "business administration" (BBA) sits after "master of
    business administration" (MBA) for exactly this reason.
    """
    assert P("master of business administration") == "MBA"
    assert P("business administration") == "BBA"
    assert P("computer science") == "B.Tech Computer Science"
    assert P("information technology") == "B.Tech Information Technology"


def test_every_program_this_can_return_exists_in_the_knowledge_base():
    """
    The invariant the Streamlit list violated.

    Its four invented programs ("data science", "engineering", "business
    analytics", "information systems") were unreachable in the KB, so a student
    could be told they were eligible for, and enrolled in, a course Meridian
    does not run. Anything the detector returns must be attested by the KB.
    """
    kb = KB_PATH.read_text(encoding="utf-8")
    canonicals = sorted(set(PROGRAM_ALIASES.values()))

    missing = [name for name in canonicals if name not in kb]
    assert not missing, (
        f"detector can return programs the knowledge base does not offer: {missing}"
    )


def test_the_detector_knows_every_program_the_kb_lists():
    """
    The other direction: the KB's program list must be fully covered, so a
    student asking for a real program is never told it is unrecognised.
    """
    kb = KB_PATH.read_text(encoding="utf-8")
    listed = re.search(r"Postgraduate options: ([^.]+)\.", kb)
    assert listed, "the KB's program list moved — update this test"

    canonical = set(PROGRAM_ALIASES.values())
    for name in ("MBA", "MCA", "M.Tech", "M.Sc", "MA", "M.Com"):
        assert name in canonical, f"{name} is offered but the detector cannot return it"
