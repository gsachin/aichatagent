"""The DSPMU programme catalogue built from content/dspmu/facts.json.

The production profile is DSPMU. These tests do not switch ``PROGRAM_PROFILE``
because the module-level active catalogue is already imported by the rest of
the suite; they exercise ``DSPMU_PROGRAM_ALIASES`` directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.programs import (
    DSPMU_PROGRAM_ALIASES,
    detect_program,
    is_explicit_program_choice,
)

ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "content" / "dspmu" / "voice_kb.md"


def detect(text: str) -> str:
    return detect_program(text, DSPMU_PROGRAM_ALIASES)


def _without_abbreviation(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("i want to take admission in ba political science", "Bachelor of Arts in Political Science (BA)"),
        ("i want b.a. political science", "Bachelor of Arts in Political Science (BA)"),
        ("bsc physics", "Bachelor of Science in Physics (B.Sc.)"),
        ("b.sc. chemistry", "Bachelor of Science in Chemistry (B.Sc.)"),
        ("b.sc integrated physics", "Bachelor of Science in Integrated Physics (B.Sc.)"),
        ("bsc it", "Bachelor of Science in Information Technology (B.Sc.)"),
        ("b.com admission", "Bachelor of Commerce (B.Com.)"),
        ("bba", "BBA"),
        ("bca", "BCA"),
        ("b.f.sc", "Bachelor of Fisheries Sciences (B.F.Sc.)"),
        ("ba jmc", "Bachelor of Arts in Journalism & Mass Communication (BA)"),
        ("ba kathak", "Bachelor of Arts in Kathak Dance (BA)"),
        ("ba visual art", "Bachelor of Arts in Visual Art Painting (BA)"),
        ("ba yogic science", "Bachelor of Arts in Yogic Science (BA)"),
        ("बी.ए राजनीति विज्ञान", "Bachelor of Arts in Political Science (BA)"),
        ("बी.कॉम", "B.Com."),
        ("બી.એસસી", "B.Sc."),
    ],
)
def test_detects_dspmu_spellings(text, expected):
    assert detect(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "i am from mumbai",
        "my name is karthik",
        "tell me about the campus",
        "english",       # two BA English variants; a bare subject is ambiguous
        "physics",       # Physics and Integrated Physics
        "mba",           # PG programme list is not verified yet
        "m.tech",
        "मैं मुंबई से हूँ",
    ],
)
def test_no_dspmu_false_positive(text):
    assert detect(text) == "", f"{text!r} must not name a programme"


@pytest.mark.parametrize(
    "text",
    [
        "ba political science",
        "b.sc physics please",
        "i want to take admission in b.com",
        "बी.ए राजनीति विज्ञान",
        "bba",
    ],
)
def test_a_dspmu_choice_is_recognised(text):
    detected = detect(text)
    assert detected
    assert is_explicit_program_choice(
        text.lower(), detected, DSPMU_PROGRAM_ALIASES
    )


@pytest.mark.parametrize(
    "text",
    [
        "is ba good",
        "ba or b.sc",
        "what is the fee for b.sc physics",
        "बी.ए के बारे में बताइए",
    ],
)
def test_a_dspmu_mention_is_not_a_choice(text):
    detected = detect(text)
    assert detected
    assert not is_explicit_program_choice(
        text.lower(), detected, DSPMU_PROGRAM_ALIASES
    )


def test_the_catalogue_covers_every_ug_row_in_the_fee_sheet():
    canonicals = {
        name for name in DSPMU_PROGRAM_ALIASES.values()
        if name.startswith("Bachelor")
    }
    assert len(canonicals) == 43


def test_every_returned_programme_is_attested_by_the_voice_kb():
    kb = " ".join(KB_PATH.read_text(encoding="utf-8").split())
    kb_without_abbreviations = _without_abbreviation(kb)
    missing = [
        name
        for name in sorted(set(DSPMU_PROGRAM_ALIASES.values()))
        if name not in kb
        and _without_abbreviation(name) not in kb_without_abbreviations
    ]
    assert not missing, (
        "the DSPMU detector can return programmes the entity corpus does not "
        f"offer: {missing}"
    )


def test_aliases_are_not_shadowed_by_broader_entries():
    assert detect("master of business administration") == ""
    assert detect("bba") == "BBA"
    assert detect("b.sc integrated physics") == (
        "Bachelor of Science in Integrated Physics (B.Sc.)"
    )
    assert detect("b.sc physics") == "Bachelor of Science in Physics (B.Sc.)"
