"""
Meridian program-name detection — the single source of truth.

Both chat front ends need this: the WhatsApp webhook (``app.main``) and the
Streamlit UI (``app.py``). Each grew its own copy, and both copies were wrong in
the same way, so the knowledge lives here now and the callers only import it.

The defects this replaces, all of which were live:

  * ``"m.tech" in msg_lower`` never matched "M. Tech" — the spelling the
    knowledge base itself uses — so a student asking for the real program
    registered nothing, and whatever was already stored on the lead was reused.
    The same test also matched ``"mba"`` inside ``"mumbai"``, so "I am from
    Mumbai" set an MBA lead.
  * The Streamlit list was ``["computer science", "mba", "data science",
    "engineering", "business analytics", "information systems"]`` — four of
    those are not Meridian programs at all (the KB offers B.Tech CS / AI & ML /
    IT, BBA, BCA, B.Com, BA, B.Sc, MBA, MCA, M.Tech, M.Sc, MA, M.Com), so a
    student could be enrolled in a course the university does not run.

Matching is on a compiled, boundary-anchored pattern. A dotted alias compiles
to an optional-separator pattern, so ``m.tech`` / ``m. tech`` / ``mtech`` are
one program, and ``\\b`` on both ends is what keeps ``mba`` out of ``mumbai``.
"""

from __future__ import annotations

import re

# Longer/more specific aliases first — first match wins.
PROGRAM_ALIASES = {
    # Compound / specific names first — first match wins
    "master of business administration": "MBA",
    "ai & machine learning": "B.Tech AI & Machine Learning",
    "computer science": "B.Tech Computer Science",
    "information technology": "B.Tech Information Technology",
    "computer applications": "BCA",
    "business administration": "BBA",
    # Short aliases
    "mba": "MBA",
    "mca": "MCA",
    "m.tech": "M.Tech",
    "b.tech": "B.Tech",
    "bca": "BCA",
    "bba": "BBA",
    "b.com": "B.Com",
    "b.sc": "B.Sc",
    "b.a": "BA",
    "ba": "BA",
    "m.sc": "M.Sc",
    "m.com": "M.Com",
    "m.a": "MA",
}

_SEPARATOR_RE = re.compile(r"[.\s]+")


def _program_alias_pattern(alias: str) -> "re.Pattern[str]":
    """
    Compile an alias into a boundary-anchored pattern.

    A run of dots/spaces inside an alias becomes a separator that is optional
    when the alias was dotted ("m.tech" matches "m. tech" and "mtech") and
    required when it was only spaced ("computer science" stays two words).
    """
    parts = _SEPARATOR_RE.split(alias)
    separators = _SEPARATOR_RE.findall(alias)

    pattern = ""
    for index, part in enumerate(parts):
        pattern += re.escape(part)
        if index < len(separators):
            pattern += r"[.\s]*" if "." in separators[index] else r"\s+"
    return re.compile(r"\b" + pattern + r"\b")


PROGRAM_PATTERNS = tuple(
    (_program_alias_pattern(alias), canonical)
    for alias, canonical in PROGRAM_ALIASES.items()
)


def detect_program(msg_lower: str) -> str:
    """Return the canonical Meridian program name for a message, or ''."""
    text = msg_lower.lower()
    for pattern, canonical in PROGRAM_PATTERNS:
        if pattern.search(text):
            return canonical
    return ""


# Words that may surround a course choice without changing what it is. The test
# for an explicit choice is: strip the program names, strip these, and if
# nothing is left the student was choosing a course and nothing else.
PROGRAM_CHOICE_FILLER = frozenset({
    "i", "id", "want", "wanna", "would", "like", "to", "take", "taking",
    "admission", "admissions", "in", "into", "for", "the", "a", "an",
    "please", "pls", "program", "programme", "course", "study", "do",
    "my", "is", "choose", "select", "join", "apply", "interested", "and",
    "prefer", "option", "go", "with", "am",
})


def is_explicit_program_choice(msg_lower: str, detected: str) -> bool:
    """
    True when the message is a course *choice*, not a mention of one.

    "m.tech", "M. Tech please" and "i want to take admission in m.tech" are
    choices. "is mba good" and "mba or mca" name programs but choose nothing —
    only a student choosing a course may change the stored program.
    """
    if not detected:
        return False
    text = msg_lower
    for pattern, _ in PROGRAM_PATTERNS:
        text = pattern.sub(" ", text)
    remaining = [
        word for word in re.findall(r"[a-z]+", text)
        if word not in PROGRAM_CHOICE_FILLER
    ]
    return not remaining
