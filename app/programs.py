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
one program, and an ASCII-word boundary is what keeps ``mba`` out of
``mumbai``.
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
    # Devanagari (Hindi) spellings. A dotted alias compiles to an optional
    # separator, so "एम.कॉम" also matches "एमकॉम" and "एम कॉम". Longer names
    # come first: "बी.एससी" must win over "बी.ए", whose pattern would
    # otherwise match its prefix.
    "मास्टर ऑफ बिजनेस एडमिनिस्ट्रेशन": "MBA",
    "कंप्यूटर साइंस": "B.Tech Computer Science",
    "इंफॉर्मेशन टेक्नोलॉजी": "B.Tech Information Technology",
    "एम.बी.ए": "MBA",
    "एम.सी.ए": "MCA",
    "एम.टेक": "M.Tech",
    "बी.टेक": "B.Tech",
    "बी.सी.ए": "BCA",
    "बी.बी.ए": "BBA",
    "बी.कॉम": "B.Com",
    "बी.एससी": "B.Sc",
    "बी.ए": "BA",
    "एम.एससी": "M.Sc",
    "एम.कॉम": "M.Com",
    "एम.ए": "MA",
    # Gujarati spellings, same ordering rule.
    "માસ્ટર ઓફ બિઝનેસ એડમિનિસ્ટ્રેશન": "MBA",
    "કમ્પ્યુટર સાયન્સ": "B.Tech Computer Science",
    "ઇન્ફોર્મેશન ટેકનોલોજી": "B.Tech Information Technology",
    "એમ.બી.એ": "MBA",
    "એમ.સી.એ": "MCA",
    "એમ.ટેક": "M.Tech",
    "બી.ટેક": "B.Tech",
    "બી.સી.એ": "BCA",
    "બી.બી.એ": "BBA",
    "બી.કોમ": "B.Com",
    "બી.એસસી": "B.Sc",
    "બી.એ": "BA",
    "એમ.એસસી": "M.Sc",
    "એમ.કોમ": "M.Com",
    "એમ.એ": "MA",
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

#: Punctuation that can sit on the edge of a whitespace token. The Indic
#: danda and CJK stops are here so "हो गया।" and "完成。" compare cleanly.
_WORD_PUNCT = ".,!?;:()[]{}\"'।、，！？；："


def _program_alias_pattern(alias: str) -> "re.Pattern[str]":
    """
    Compile an alias into a boundary-anchored pattern.

    A run of dots/spaces inside an alias becomes a separator that is optional
    when the alias was dotted ("m.tech" matches "m. tech" and "mtech") and
    required when it was only spaced ("computer science" stays two words).

    The boundary is a lookaround for ASCII letters/digits/underscore, not
    ``\\b``. Indic vowel signs (matras) are combining marks and are not ``\\w``
    in Python's ``re``, so ``\\b`` fails on an alias that ends with one
    ("બી.એસસી"), and it also refuses a locative suffix that is glued to the
    program name ("એમ.કોમમાં"). The ASCII boundary keeps the original guard
    (``mba`` is not inside ``mumbai``) while letting Indic text match.
    """
    parts = _SEPARATOR_RE.split(alias)
    separators = _SEPARATOR_RE.findall(alias)

    pattern = ""
    for index, part in enumerate(parts):
        pattern += re.escape(part)
        if index < len(separators):
            pattern += r"[.\s]*" if "." in separators[index] else r"\s+"
    return re.compile(
        r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"
    )


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
    # Hindi choice phrasing. Words that ask for information ("बताइए",
    # "जानकारी", "क्या") are deliberately absent: "एम.कॉम के बारे में बताइए"
    # names a program but does not choose one.
    "मैं", "मुझे", "मेरा", "मेरी", "को", "में", "के", "की", "का", "लिए",
    "लेना", "लेनी", "लेने", "चाहता", "चाहती", "चाहते", "चाहिए", "हूँ",
    "हूं", "है", "हैं", "करना", "करनी", "करने", "पसंद", "चुनना", "चुन",
    "और", "कृपया", "प्रवेश", "एडमिशन", "दाखिला",
    # Gujarati choice phrasing.
    "હું", "મને", "મારું", "મારી", "ને", "માં", "ના", "ની", "નો", "માટે",
    "લેવો", "લેવું", "લેવી", "છે", "છું", "કરવો", "કરવું", "કરવી",
    "પસંદ", "અને", "કૃપા", "કરીને", "એડમિશન", "પ્રવેશ", "દાખલો", "જોઈએ",
    # Romance-language fillers. The tokenizer now sees accented words too, so
    # without these a reply such as "quiero M.Com" would look like a mention.
    "quiero", "quisiera", "deseo", "me", "gustaría", "en", "el", "la",
    "de", "del", "para", "estudiar", "tomar", "programa", "curso", "por",
    "favor", "inscribirme", "matricularme", "admisión", "admision",
    "je", "veux", "voudrais", "souhaite", "m'inscrire", "inscrire", "le",
    "les", "du", "pour", "étudier", "programme", "cours", "s'il", "vous",
    "plaît", "admission",
    "voglio", "vorrei", "iscrivermi", "il", "lo", "per", "studiare",
    "programma", "corso", "favore", "ammissione",
    "quero", "gostaria", "inscrever", "em", "na", "estudar", "matricular",
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
    # Tokenise on whitespace, not on ``\w``: Indic vowel signs (matras) are
    # combining marks, not word characters, so a regex such as ``[^\W\d_]+``
    # would split "કેવું" into fragments that can never match a filler word.
    remaining = []
    for raw_word in re.findall(r"\S+", text):
        word = raw_word.strip(_WORD_PUNCT)
        if word and word not in PROGRAM_CHOICE_FILLER:
            remaining.append(word)
    return not remaining
