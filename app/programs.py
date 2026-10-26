"""
Programme-name detection — the single source of truth.

Both chat front ends need this: the WhatsApp webhook (``app.main``) and the
Streamlit UI (``app.py``). Each grew its own copy, and both copies were wrong in
the same way, so the knowledge lives here now and the callers only import it.

``DSPMU_PROGRAM_ALIASES`` is built from the generated
``content/dspmu/facts.json``. It covers the 43 undergraduate programmes in the
2026 fee sheet. Postgraduate programme names are deliberately absent until
their list and fees are verified.

Matching is on a compiled, boundary-anchored pattern. A dotted alias compiles
to an optional-separator pattern, so ``b.sc`` / ``b. sc`` / ``bsc`` are one
programme, and an ASCII-word boundary is what keeps ``ba`` out of ``bazar``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

#: Degree families a caller may name without a subject. They are attested by
#: the DSPMU knowledge base as degree headings, but a lead that stops at "BA"
#: is stored as the family rather than guessed into a subject.
_DSPMU_DEGREE_ALIASES = {
    # Longer aliases first: "बी.ए" would otherwise match the prefix of
    # "बी.एससी", and the pattern boundary treats the following matra as
    # non-ASCII.
    "बी.एससी": "B.Sc.",
    "बी.कॉम": "B.Com.",
    "बी.बी.ए": "BBA",
    "बी.सी.ए": "BCA",
    "बी.ए": "BA",
    "બી.એસસી": "B.Sc.",
    "બી.કોમ": "B.Com.",
    "બી.બી.એ": "BBA",
    "બી.સી.એ": "BCA",
    "બી.એ": "BA",
    "bachelor of science": "B.Sc.",
    "bachelor of commerce": "B.Com.",
    "bachelor of business administration": "BBA",
    "bachelor of computer applications": "BCA",
    "bachelor of fisheries sciences": "B.F.Sc.",
    "bachelor of arts": "BA",
    "b.f.sc": "B.F.Sc.",
    "bfsc": "B.F.Sc.",
    "b.sc": "B.Sc.",
    "bsc": "B.Sc.",
    "b.com": "B.Com.",
    "bcom": "B.Com.",
    "bba": "BBA",
    "bca": "BCA",
    "b.a": "BA",
    "ba": "BA",
}

#: Hindi names for subjects that occur once in the catalogue. Subject names
#: that repeat (English, Physics) are deliberately absent: a bare "English"
#: is not enough to choose between BA English and BA English Language &
#: Literature, so the detector leaves the program unchanged.
_DSPMU_HINDI_SUBJECTS = {
    "political science": "राजनीति विज्ञान",
    "history": "इतिहास",
    "economics": "अर्थशास्त्र",
    "psychology": "मनोविज्ञान",
    "geography": "भूगोल",
    "sociology": "समाजशास्त्र",
    "anthropology": "मानवशास्त्र",
    "hindi": "हिंदी",
    "urdu": "उर्दू",
    "sanskrit": "संस्कृत",
    "philosophy": "दर्शनशास्त्र",
    "mathematics": "गणित",
    "botany": "वनस्पति विज्ञान",
    "zoology": "प्राणी विज्ञान",
    "geology": "भूविज्ञान",
    "microbiology": "सूक्ष्मजीव विज्ञान",
    "electronics": "इलेक्ट्रॉनिक्स",
    "information technology": "सूचना प्रौद्योगिकी",
    "public administration": "लोक प्रशासन",
    "music": "संगीत",
    "yogic science": "योग विज्ञान",
    "journalism & mass communication": "पत्रकारिता एवं जनसंचार",
    "environmental sc & sustainability": "पर्यावरण विज्ञान एवं स्थिरता",
}

_DSPMU_FACTS_PATH = (
    Path(__file__).resolve().parent.parent / "content" / "dspmu" / "facts.json"
)


def _normalise_space(value: str) -> str:
    return " ".join(value.split())


def _dspmu_canonical(programme: str) -> str:
    """The knowledge-base spelling of a fee-sheet programme name.

    ``Bachelor of Arts (BA) in History`` becomes
    ``Bachelor of Arts in History (BA)``; degree-only names such as
    ``Bachelor of Commerce (B.Com.)`` are already in KB form.
    """
    match = re.match(r"^(Bachelor of .+?) \(([^)]+)\) in (.+)$", programme)
    if match:
        return f"{match.group(1)} in {match.group(3)} ({match.group(2)})"
    return programme


def _dspmu_short(degree: str, subject: str) -> str:
    """A compact, speakable name such as ``BA Political Science``."""
    if degree.startswith("BA ("):
        return f"BA {subject}"
    if degree.startswith("B.Sc. ("):
        return f"B.Sc. {subject}"
    if degree in ("B.Com.", "BBA", "BCA", "B.F.Sc."):
        return degree
    return f"{degree} {subject}"


def _load_dspmu_fees() -> list[dict[str, object]]:
    try:
        payload = json.loads(_DSPMU_FACTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    fees = payload.get("fees")
    return fees if isinstance(fees, list) else []


def _build_dspmu_program_aliases() -> dict[str, str]:
    """Build the alias table from the generated fee facts.

    The canonical values are the names the knowledge base uses, so the
    detector cannot return a programme the grounding index does not carry.
    """
    rows: list[tuple[str, str, str, str, str]] = []
    for fee in _load_dspmu_fees():
        programme = _normalise_space(str(fee.get("programme") or ""))
        degree = _normalise_space(str(fee.get("degree") or ""))
        subject = _normalise_space(str(fee.get("subject") or ""))
        if not programme or not degree or not subject:
            continue
        canonical = _dspmu_canonical(programme)
        rows.append(
            (programme, canonical, _dspmu_short(degree, subject), degree, subject)
        )

    subjects = [row[4] for row in rows]
    subject_counts: dict[str, int] = {}
    for subject in subjects:
        key = subject.casefold()
        subject_counts[key] = subject_counts.get(key, 0) + 1

    def subject_is_ambiguous(subject: str) -> bool:
        """True when a bare subject also appears inside another subject.

        "Physics" is a substring of "Integrated Physics" and "English" of
        "English Language & Literature". A bare subject cannot identify those
        rows, so it must not shadow the longer programme alias.
        """
        if subject_counts.get(subject.casefold(), 0) > 1:
            return True
        pattern = re.compile(
            rf"(?<![A-Za-z]){re.escape(subject)}(?![A-Za-z])",
            re.IGNORECASE,
        )
        return any(
            subject.casefold() != other.casefold() and pattern.search(other)
            for other in subjects
        )

    aliases: dict[str, str] = {}
    for programme, canonical, short, degree, subject in rows:
        # Most specific first. The full programme name and its KB spelling
        # must beat the subject-only alias below.
        for alias in (programme, canonical, short):
            aliases.setdefault(alias.casefold(), canonical)
        degree_match = re.match(
            r"^(BA|B\.Sc\.|B\.Com\.|BBA|BCA|B\.F\.Sc\.) \(([^)]+)\)$", degree
        )
        if degree_match:
            abbreviation, qualifier = degree_match.groups()
            aliases.setdefault(
                f"{abbreviation} {qualifier}".casefold(), canonical
            )
            if abbreviation == "BA":
                aliases.setdefault(f"B.A. {qualifier}".casefold(), canonical)
        elif degree.startswith("BA "):
            aliases.setdefault(f"B.A. {subject}".casefold(), canonical)
        aliases.setdefault(degree.casefold(), canonical)
        if subject == "Journalism & Mass Communication":
            aliases.setdefault("journalism", canonical)
            aliases.setdefault("mass communication", canonical)
        key = subject.casefold()
        if not subject_is_ambiguous(subject):
            aliases.setdefault(key, canonical)
            hindi = _DSPMU_HINDI_SUBJECTS.get(key)
            if hindi:
                aliases.setdefault(hindi, canonical)

    # Degree families last: "BA" is shorter than every subject-bearing alias
    # and must not shadow one.
    aliases.update(_DSPMU_DEGREE_ALIASES)
    return aliases


DSPMU_PROGRAM_ALIASES = _build_dspmu_program_aliases()

#: The DSPMU catalogue is the only supported programme catalogue.
PROGRAM_ALIASES = DSPMU_PROGRAM_ALIASES

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


@lru_cache(maxsize=8)
def _patterns_from_items(
    items: tuple[tuple[str, str], ...]
) -> tuple[tuple["re.Pattern[str]", str], ...]:
    return tuple(
        (_program_alias_pattern(alias), canonical)
        for alias, canonical in items
    )


def _patterns_for(
    aliases: dict[str, str] | None = None,
) -> tuple[tuple["re.Pattern[str]", str], ...]:
    if aliases is None:
        return PROGRAM_PATTERNS
    return _patterns_from_items(tuple(aliases.items()))


PROGRAM_PATTERNS = _patterns_for(PROGRAM_ALIASES)


def detect_program(msg_lower: str, aliases: dict[str, str] | None = None) -> str:
    """Return the canonical programme name for a message, or ''.

    *aliases* defaults to the profile-selected catalogue. Tests and tooling
    can pass ``DSPMU_PROGRAM_ALIASES`` explicitly without reloading settings.
    """
    text = msg_lower.lower()
    for pattern, canonical in _patterns_for(aliases):
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


def is_explicit_program_choice(
    msg_lower: str,
    detected: str,
    aliases: dict[str, str] | None = None,
) -> bool:
    """
    True when the message is a course *choice*, not a mention of one.

    "b.sc physics", "B.Sc Physics please" and "i want to take admission in
    ba political science" are choices. "is ba good" and "ba or b.sc" name
    programmes but choose nothing — only a student choosing a course may
    change the stored programme.
    """
    if not detected:
        return False
    text = msg_lower
    for pattern, _ in _patterns_for(aliases):
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
