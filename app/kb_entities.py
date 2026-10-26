"""Corpus index for the voice grounding boundary (BUG-18).

Why this module exists
----------------------
`app/voice_grounding.py` decided what the agent may say. It knew one thing: the
*evidence* it was handed. That is a per-turn, per-call view, and two failures
followed from it, both measured on the 2026-10-10 inbound call `944b503f`
(`doc/voice-demo/REVIEW-2026-10-10-inbound.md`):

  * **It could not tell a real programme from an invented one.** Absence from
    this turn's retrieval is not evidence of absence from the university. An
    answer naming "Master of Mechanical Engineering" was refused on one turn
    (right) and spoken on the next (wrong) purely because the model stopped
    capitalising it -- the check was a regex over proper nouns, not a fact.

  * **It could not tell whose number a number was.** `$14,600` is real: it is
    M.Tech's tuition. Attached to a programme that does not exist, it passed,
    because the guard asked "does this number appear in the evidence?" and never
    "does this number belong to the thing being described?"

Both questions have the same answer source: **what the corpus actually
contains**, which does not change from turn to turn. That is what this module
indexes.

The corpus is exactly one file. The Chroma collection behind retrieval holds 35
chunks and all 35 come from `content/dspmu/voice_kb.md`, so
the index and the evidence have one source of truth between them.

The name branch, and why it can come back
-----------------------------------------
A name check was tried before and removed.
records why: it refused three CORRECT programme lists in front of callers. Those
measurements were taken on the same calls as the BUG-16 wiring fault, which left
the evidence list empty -- so everything was being refused, and the name branch
was indicted on contaminated evidence.

Replayed over all 81 archived calls, an entity-aware name check flags 11
distinct names; three are regex noise and seven are real fabrications, including
BUG-07's own `School of International Studies`.

The rule that makes it safe is in `voice_grounding._name_claims`, not here: an
unknown name is only a defect when the sentence offers **no known entity** as its
subject. `Master of Arts, Master of Business Administration and Master of
Engineering` names two real programmes and passes; `our mechanical engineering
program` names none and is caught.

Kept dependency-free (stdlib only) so the guard stays importable and testable
without the application.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "EntityIndex",
    "NameSlot",
    "name_slots",
    "corpus_text",
    "default_index",
    "global_numbers",
    "is_known",
    "known_names",
    "name_phrases",
    "normalise",
    "programme_numbers",
    "unoffered_entities",
]

#: The shipped knowledge base. Overridable so a test, or a re-ingested corpus,
#: can point the index at its own source without touching code.
KB_PATH_ENV = "VOICE_KB_PATH"
_DEFAULT_KB = "content/dspmu/voice_kb.md"

#: Categories whose entries name a programme, and so carry numbers that belong
#: to that programme specifically. Everything outside these sections is treated
#: as a global number -- see `EntityIndex.global_numbers`.
_PROGRAMME_CATEGORIES = (
    "undergraduate programs",
    "postgraduate programs",
    "doctoral programs",
)

# ── normalisation, shared with the guard ─────────────────────────────
#
# Lives here rather than in `voice_grounding` because the index and the claims
# must be folded the SAME way for a comparison between them to mean anything.

_WORD_NUM = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "eighteen": "18", "twenty": "20", "thirty": "30",
}


def normalise(text: str) -> str:
    """Fold the forms that mean the same thing before comparing.

    Number words to digits, money punctuation away, case down. Without this a
    correct "four-year" is flagged because the source writes "4 years", and
    `$11,200` does not match `11200`. A guard that rejects correct answers is
    worse than no guard, so this folds rather than compares literally.

    The punctuation strip keeps `.` because `B.Tech` is one token.
    """
    s = (text or "").lower()
    for word, digit in _WORD_NUM.items():
        s = re.sub(rf"\b{word}\b", digit, s)
    s = s.replace(",", "").replace("$", "")
    s = re.sub(r"[^a-z0-9.\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── entity-phrase extraction ─────────────────────────────────────────

#: Words that anchor an entity. Matched case-insensitively, on purpose: the
#: failure this fixes is that "Master of Mechanical Engineering" was checked
#: and "our mechanical engineering program" was not, because only the first was
#: capitalised. A guard that a shift key can walk past is not a guard.
_KEYWORDS = frozenset({
    "school", "program", "programme", "department", "faculty", "institute",
    "college", "centre", "center", "b.tech", "m.tech", "bachelor", "master",
    "doctoral", "doctorate", "phd", "degree", "degrees",
})

#: Words that end a name phrase. Two jobs: they stop the walk, and they stop
#: ordinary speech being read as a name. "programs in fields such as
#: Engineering" must yield nothing -- `fields` is here for exactly that case,
#: which is FP1 in the test suite. The adjectives and participles are here
#: because "a unique program" and "programs, including Master of Arts" would
#: otherwise contribute "unique" and "including" as if they were names.
_BOUNDARY = frozenset("""
the a an this that these those our your their its his her my
of in at on for with to from by as and or but so if then than
is are was were be been being has have had do does did will would can could
should may might must not no yes please
we you i they he she it them us me
anybody anyone somebody someone everybody everyone nobody noone
thing things stuff lot lots bit
field fields area areas option options type types kind kinds
program program programme programs programmes course courses school schools
department departments university universities college colleges institute institutes
faculty centre center
fee fees tuition cost costs price prices eligibility requirement requirements
duration year years month months week weeks available offered offer offers
more about which who whose when where while because
including include includes unique new various different same other another
strong great good best top own specific particular general full complete
whole main major minor single multiple several few many some any all both
each every such well
degree degrees offering offerings team alternative considering advisor adviser
details detail process step steps
highly extremely very quite really rather
get gets getting got back reach reaching check checking look looking see seeing
find finding know knowing tell telling give giving send sending make making
take taking come coming go going want wanting need needing
study studying studied pursue pursuing enrol enroll enrolling join joining
apply applying register registering interested interest choose choosing
start starting begin beginning complete completing finish finishing graduate graduating
now today tonight tomorrow yesterday then again anymore just still already
actually currently soon later always never ever instead either too also even
though however maybe perhaps probably definitely certainly simply truly
""".split())

#: A content run is only worth reporting if at least one of its words is part of
#: the university's own vocabulary. An ordinary English phrase is absent from a
#: course catalogue for the obvious reason, and treating that as "the caller
#: named something we do not offer" is how live call `853548f5` ended up telling
#: the model the university does not offer "too much piece amount", "am talking",
#: "only discuss" and "focus only".
#:
#: The bias is deliberate and it is the OPPOSITE of the guard's. A missed
#: detection costs nothing -- the model is simply not warned, and the output
#: boundary still catches any assertion. A false detection makes the agent deny
#: something the caller never asked about, and at worst deny something real.
_MIN_KNOWN_WORDS = 1

#: A word that can appear inside a name: letters, and the `&`/`.`/`'`/`-` that
#: "AI & Machine Learning", "B.Tech" and "Master's" need. No digits -- a name
#: does not contain one here, and allowing them invites "4-year" into a name.
#:
#: A word must END on a letter. Without that, "Studies." carries its sentence
#: period into the phrase and stops matching the corpus, and "program." stops
#: matching `_KEYWORDS` so the walk runs straight through a boundary --
#: "Master of Mechanical Engineering program." was extracted as one name.
#:
#: The apostrophe is part of the word, and that is a fix, not a nicety. With it
#: excluded, "bachelor's" split into `bachelor` + `s` and "Master's" into
#: `Master` + `s`, producing the phrases `bachelor s` and `Master s` -- both of
#: which the corpus contains ("a bachelor's degree", "a relevant master's
#: degree"). On live call `fbced641` that made an invented programme look
#: grounded and six fabrications were spoken to a caller.
_WORD = re.compile(r"[A-Za-z](?:[A-Za-z&.'’\-]*[A-Za-z])?")

#: A word's stem for KEYWORD and BOUNDARY comparison: everything before the
#: apostrophe. "bachelor's" reads as `bachelor`, so it still anchors; "you'll"
#: reads as `you`, which is a boundary, so the walk still stops there.
_CLITIC = re.compile(r"['’].*$")


def _bare(word: str) -> str:
    return _CLITIC.sub("", word).lower()

#: Connectives that introduce a NAME: "School OF Arts", "B.Tech IN Computer
#: Science", "PhD IN Mechanical Engineering". Subordinating only.
#:
#: "and" and "for" are coordinating and are NOT here. "Business and Management"
#: is a pair of coordinate nouns, not a head followed by its name -- and with
#: "and" admitted, "your degree and GPA" became the name slot `degree and GPA`,
#: which the guard then refused in front of a caller on the archived calls.
#: A name connective subordinates; a coordinator does not.
_CONNECTIVES = frozenset({"of", "in"})

#: How far a name may run. Long enough for "Bachelor of Business
#: Administration", short enough that a runaway walk cannot swallow a sentence.
_MAX_NAME_WORDS = 5


def _tokens(text: str) -> list[re.Match]:
    return list(_WORD.finditer(text or ""))


def _walk(tokens: list[re.Match], start: int, step: int,
          keep_connectives: bool = False) -> list[str]:
    """Collect name words from `start`, moving by `step`, stopping at a boundary.

    `keep_connectives` exists for one case, and it is the recombination defect.
    A tail normally stops at a connective, so "Master of Science in Computer
    Science" was captured as `Master of Science` plus a separate `Computer
    Science` -- two real names, each established, and the invented compound
    between them never tested. Keeping the connective in the tail makes the
    whole phrase visible so it can be judged as one name.
    """
    out: list[str] = []
    i = start
    while 0 <= i < len(tokens) and len(out) < _MAX_NAME_WORDS:
        word = tokens[i].group(0)
        low = _bare(word)
        if low in _KEYWORDS or (low in _BOUNDARY
                                and not (keep_connectives
                                         and low in _CONNECTIVES)):
            break
        out.append(word)
        i += step
    if step < 0:
        out.reverse()
    return out


class NameSlot(NamedTuple):
    """One place a name could be, and the phrase found there.

    `keyword` and `connective` are the slot's SHAPE, and the guard uses them:
    an unknown name is excused only by a known name of the same shape. That is
    the difference between a slightly-wrong rename in a list --
    "Master of Arts, Master of Business Administration, and Master of
    Engineering", which is a correct answer and must be spoken -- and an
    invention standing next to something unrelated, which must not.
    """

    phrase: str
    words: tuple[str, ...]
    keyword: str
    connective: str
    content: tuple[str, ...]

    @property
    def shape(self) -> tuple[str, str]:
        return (self.keyword, self.connective)

    @property
    def content_prefixes(self) -> list[str]:
        """Prefixes of the NAME part, longest first, TWO WORDS OR MORE.

        The name, not the whole slot. "bachelor's degree in computer science"
        is not a string the corpus contains, but every word of its name --
        "computer science" -- is, and the sentence is correct. Testing the whole
        slot made it look invented.

        Two words minimum, because a single word establishes far too much: the
        corpus contains "International" (in "International Association of
        Universities", "60+ international exchange and research partners"), so
        admitting one-word prefixes made "School of International Studies" look
        grounded. Single-word names are judged by `is_established` instead.

        This property is what makes the check PER SLOT rather than per sentence.
        The first version asked "does this sentence name anything real?" and let
        one answer excuse everything else in it; on live call `fbced641` that let
        the phrase `Master s` excuse `PhD in Mechanical Engineering` beside it.
        Each slot now stands or falls on its own name.
        """
        out: list[str] = []
        for n in range(len(self.content), 1, -1):
            if _bare(self.content[n - 1]) in _CONNECTIVES:
                continue
            out.append(" ".join(self.content[:n]))
        return out

    @property
    def segments(self) -> list[str]:
        """The name split at its connectives.

        "Science in Computer Science" -> ["Science", "Computer Science"].
        Used by the compound rule below, which needs to know which part is the
        degree and which part is the field it claims to be offered in.
        """
        out: list[str] = []
        cur: list[str] = []
        for word in self.content:
            if _bare(word) in _CONNECTIVES:
                if cur:
                    out.append(" ".join(cur))
                    cur = []
            else:
                cur.append(word)
        if cur:
            out.append(" ".join(cur))
        return out

    def is_established(self, index: "EntityIndex") -> bool:
        """Does the corpus carry THIS name?

        A one-word name is checked against the entity names the corpus
        introduces, not against the corpus text. "Arts" is a real one -- it is
        inside "Bachelor of Arts" -- while "International" and "Engineering"
        are ordinary words that happen to appear somewhere. A bare adjective
        must not establish anything.
        """
        segments = self.segments
        if len(segments) > 1 and self.connective:
            return self._compound_established(index, segments)
        if len(self.content) == 1:
            return index.mentions_entity_word(self.content[0])
        return any(index.is_known(p) for p in self.content_prefixes)

    def _compound_established(self, index: "EntityIndex",
                              segments: list[str]) -> bool:
        """`<Degree> <connective> <Field>`: both real, and from the SAME school.

        The recombination defect. On live call `2e9952a7` the agent said
        "Master of Science in Computer Science" six times and the guard never
        fired, because the phrase splits into two genuine DSPMU names --
        `Master of Science` and `Computer Science` -- and each was established on
        its own. The invention is in the JOIN, and nothing was testing the join.

        It is not a syntactic question: "Master of Science in Computer Science"
        (invented) and "Bachelor of Arts in Arts" (legitimate -- a degree with a
        trailing field) have the same grammar and the same kind of parts. What
        separates them is the university's own structure, and the corpus states
        it: every programme entry names its school.

            Master of Science  -> School of Science
            Computer Science   -> School of Computer Science    <- CROSSES
            Bachelor of Arts   -> School of Arts
            Arts               -> School of Arts                <- same

        A degree is not offered in another school's subject. That rule is
        checkable and needs no vocabulary list.

        Deliberately abstains when it cannot reason -- an unrecognised degree or
        an unrecognised field falls back to the ordinary content test, because a
        guard that invents grounds to flag is the BUG-16 failure again.
        """
        head = " ".join(filter(None, (self.keyword, self.connective,
                                      segments[0])))
        if not index.is_known(head):
            return False                      # the degree itself is not real
        school = index.programme_school_of(head)
        if not school:
            return True                       # cannot reason; do not condemn
        for field in segments[1:]:
            field_school = index.school_of_phrase(field)
            if field_school and field_school != school:
                return False                  # a degree outside its own school
        return True

    @property
    def prefixes(self) -> list[str]:
        """Longest first, never the bare keyword, ending on a content word.

        Longest first so a caller that only wants "the name" gets the full one.
        Prefixes matter to the knowability test: the model writes "B.Tech in
        Information Technology lasts 4 years", the walk swallows the verb, and
        the full string is then absent from the corpus. The prefix is present,
        and that is enough to establish the sentence is talking about something
        real.

        A prefix must END ON A CONTENT WORD. "School of" and "Master of" are
        prefixes of real names, and the corpus contains both strings, so
        admitting them made every "School of X" and "Master of Y" look known.
        """
        out: list[str] = []
        for n in range(len(self.words), 1, -1):
            if _bare(self.words[n - 1]) in _CONNECTIVES:
                continue
            out.append(" ".join(self.words[:n]))
        return out


def name_slots(text: str) -> list[NameSlot]:
    """Every name slot in `text`, with its shape.

    Two shapes, and each has a rule that is grammar rather than a word list.
    A stoplist cannot be completed -- this register already recorded that
    lesson for BUG-03 -- so neither rule enumerates vocabulary:

        keyword first   REQUIRES a connective: "School of X", "PhD in X",
                        "Master of X". "the PhD program, specifically the
                        robotics..." is not a name slot, because nothing joins
                        "program" to "specifically". Adding "specifically" to a
                        stoplist would have been the sixth wording of the same
                        mistake.

        keyword last    REQUIRES at least two words: "mechanical engineering
                        program" is a name slot; "an interdisciplinary program"
                        is not, because a single adjective is not a name.

    The keyword is part of the returned words for the first shape and absent
    for the second, so a candidate can never BE the bare keyword -- otherwise
    every sentence containing the word "school" would look like it named
    something real.
    """
    out: list[NameSlot] = []
    tokens = _tokens(text)
    for i, tok in enumerate(tokens):
        key = _bare(tok.group(0))
        if key not in _KEYWORDS:
            continue

        # keyword first -- a connective is required
        j = i + 1
        conn = ""
        if j < len(tokens):
            cand = _bare(tokens[j].group(0))
            if cand in _CONNECTIVES:
                conn = cand
                j += 1
        if conn:
            tail = _walk(tokens, j, +1, keep_connectives=True)
            if tail:
                words = (tok.group(0), tokens[i + 1].group(0), *tail)
                out.append(NameSlot(" ".join(words), words, key, conn,
                                    tuple(tail)))

        # keyword last -- two words or more
        head = _walk(tokens, i - 1, -1)
        if len(head) >= 2:
            out.append(NameSlot(" ".join(head), tuple(head), key, "",
                                tuple(head)))

    return out


def name_phrases(text: str) -> list[str]:
    """The full name phrase for each slot, de-duplicated.

    This is what gets REPORTED as a claim, so it is the longest form.
    `name_candidates` is what gets TESTED, and it includes the prefixes.
    """
    found: list[str] = []
    seen: set[str] = set()
    for slot in name_slots(text):
        key = slot.phrase.lower()
        if key not in seen:
            seen.add(key)
            found.append(slot.phrase)
    return found


def name_candidates(text: str) -> list[str]:
    """The NAME of every slot, de-duplicated. The entities a sentence mentions."""
    found: list[str] = []
    seen: set[str] = set()
    for slot in name_slots(text):
        phrase = " ".join(slot.content)
        key = phrase.lower()
        if key and key not in seen:
            seen.add(key)
            found.append(phrase)
    return found


# ── the index ────────────────────────────────────────────────────────

def _digits(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in re.finditer(r"\d[\d,]*", text or "")}


class EntityIndex:
    """What the corpus says exists, and which numbers belong to what.

    Built from the corpus text once and held; `default_index()` caches the one
    the process uses. Construct one directly to test against a literal corpus.
    """

    def __init__(self, corpus: str) -> None:
        self._corpus = corpus or ""
        self._norm = normalise(self._corpus)
        self._norm_core = re.sub(r"[^a-z0-9]", "", self._norm)
        self._programme_numbers, self._names = self._read_programmes()
        self._programme_school = self._read_programme_schools()
        self._schools = frozenset(self._programme_school.values())
        self._global_numbers = self._read_global_numbers()

    # -- names ------------------------------------------------------

    def _read_programmes(self) -> tuple[dict[str, frozenset[str]], list[str]]:
        """`{entity: numbers}` and the entity names, from the programme entries.

        Entries look like:

            ### btech-cs
            B.Tech in Computer Science at Dr. Shyama Prasad Mukherjee University: 4 years, School
            of Computer Science. Eligibility: 10+2 with ... Tuition: $14,500
            per year.

        The leading phrase is the entity; every digit in the entry body belongs
        to it. Over-inclusive on purpose -- a number wrongly attributed to an
        entity only makes the guard quieter, and quiet is the safe direction.
        """
        per_entity: dict[str, frozenset[str]] = {}
        names: list[str] = []

        category = ""
        entry_lines: list[str] = []

        def flush() -> None:
            if not entry_lines or category not in _PROGRAMME_CATEGORIES:
                return
            # The whole paragraph is both the name source and the number source.
            # Splitting them apart was a measured bug: the first line became the
            # "head" and every digit landed in an empty body, so no programme
            # owned its own tuition and attribution became a silent no-op.
            body = " ".join(entry_lines)
            for name in self._entity_names(entry_lines[0]):
                key = normalise(name)
                if not key:
                    continue
                if key not in per_entity:
                    per_entity[key] = frozenset()
                    names.append(name)
                per_entity[key] = per_entity[key] | frozenset(_digits(body))

        for raw in self._corpus.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                flush()
                entry_lines = []
                if line.startswith("## "):
                    category = line[3:].strip().lower()
                elif not line.startswith("### "):
                    category = ""          # "#" or "####" -- not an entry heading
            elif line:
                entry_lines.append(line)
        flush()
        return per_entity, names

    def _read_programme_schools(self) -> dict[str, str]:
        """`{programme: school}` from each entry's own "School of X".

        Every programme entry states its school, so this needs no curation:

            Bachelor of Arts (BA) at Dr. Shyama Prasad Mukherjee University: 3 years, School of
            Arts. Eligibility: 10+2 in any stream. Tuition: $7,900 per year.
        """
        out: dict[str, str] = {}
        category = ""
        lines: list[str] = []

        def flush() -> None:
            if not lines or category not in _PROGRAMME_CATEGORIES:
                return
            m = re.search(r"School of ([A-Za-z /&]+?)\s*[.,]", " ".join(lines))
            if not m:
                return
            school = normalise(m.group(1))
            for name in self._entity_names(lines[0]):
                key = normalise(name)
                if key:
                    out.setdefault(key, school)

        for raw in self._corpus.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                flush()
                lines = []
                if line.startswith("## "):
                    category = line[3:].strip().lower()
                elif not line.startswith("### "):
                    category = ""
            elif line:
                lines.append(line)
        flush()
        return out

    @staticmethod
    def _entity_names(head: str) -> list[str]:
        """The names an entry head introduces: the full name, and any abbreviation.

        "Bachelor of Arts (BA) at Dr. Shyama Prasad Mukherjee University: ..." -> both
        "Bachelor of Arts" and "BA", because an answer uses either.
        """
        head = re.split(r"\bat\s+[^:]{0,100}?\bUniversity\b", head)[0]
        head = head.split("—")[-1]            # doctoral entries lead with the prefix
        head = head.split(":")[0].strip(" .—-")
        if not head:
            return []
        out = [re.sub(r"\s*\([^)]*\)", "", head).strip()]
        for abbrev in re.findall(r"\(([^)]+)\)", head):
            if len(abbrev) <= 6:
                out.append(abbrev.strip())
        return [n for n in out if n]

    def _read_global_numbers(self) -> frozenset[str]:
        """Numbers the corpus states outside any programme entry.

        The application fee ($60), the caution deposit ($300), the hostel fee
        ($3,200) and the rest. These attach to any programme, so attribution
        must not police them -- "the $60 application fee" next to a named
        programme is a correct sentence.
        """
        owned: set[str] = set()
        for nums in self._programme_numbers.values():
            owned |= set(nums)
        return frozenset(_digits(self._corpus) - owned)

    @property
    def names(self) -> list[str]:
        return list(self._names)

    @property
    def programme_numbers(self) -> dict[str, frozenset[str]]:
        return dict(self._programme_numbers)

    @property
    def global_numbers(self) -> frozenset[str]:
        return self._global_numbers

    @property
    def all_programme_numbers(self) -> frozenset[str]:
        """Every number any programme owns. The set attribution polices."""
        out: set[str] = set()
        for nums in self._programme_numbers.values():
            out |= set(nums)
        return frozenset(out)

    def programme_school_of(self, name: str) -> str:
        """Which school offers `name`, as the corpus states it. "" if unknown."""
        key = normalise(name)
        if not key:
            return ""
        if key in self._programme_school:
            return self._programme_school[key]
        padded = {k: f" {k} " for k in self._programme_school}
        for k, school in self._programme_school.items():
            if f" {key} " in padded[k]:
                return school
        return ""

    def school_of_phrase(self, phrase: str) -> str:
        """The LONGEST school name inside `phrase`. "" if none.

        Longest, because "Computer Science" contains "Science" and only one of
        those is the school the phrase is about.
        """
        key = normalise(phrase)
        best = ""
        for school in self._schools:
            if len(school) > len(best) and re.search(
                    rf"(?<![a-z0-9]){re.escape(school)}(?![a-z0-9])", key):
                best = school
        return best

    def mentions_entity_word(self, word: str) -> bool:
        """Is `word` part of a name the corpus actually introduces?

        The strict test, for one-word names. `is_known` answers "does this
        string appear anywhere", which is true of "International" and useless
        for deciding whether "International Studies" is a programme.
        """
        key = normalise(word)
        if not key:
            return False
        padded = f" {key} "
        return any(padded in f" {normalise(n)} " for n in self._names)

    def is_known(self, name: str) -> bool:
        """Is `name` something the corpus states?

        Case-insensitive, on token boundaries. Substring matching without those
        boundaries is how the invented SAT score `1200` was accepted: it is
        inside `$11,200`. The same bug in this direction would make an invented
        name look known, so the boundaries matter both ways.
        """
        key = normalise(name)
        if not key:
            return False
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", self._norm):
            return True
        core = re.sub(r"[^a-z0-9]", "", key)
        return bool(core) and core in self._norm_core

    def programme_numbers_for(self, names: list[str]) -> frozenset[str]:
        """Programme numbers owned by any of `names`. EMPTY when none resolve.

        The guard uses emptiness as the signal that no programme was identified,
        and skips attribution -- which is the whole point of returning a set
        rather than folding the globals in.

        The archive replay is why. Two correct answers were refused by the first
        version of this check:

            "Eligibility is a bachelor's degree, and tuition is $18,500 per
             year."            -> named "bachelor s", which owns nothing, so
                                   the MBA's real $18,500 looked unattributable

            "Dr. Shyama Prasad Mukherjee University's MBA program is a 2-year postgraduate
             degree."         -> named "s MBA", same consequence for "2 years"

        Both sentences talk about a real programme; the extractor just did not
        land on a name that owns numbers. Attribution has to be silent there, or
        it refuses correct answers -- which is BUG-16 all over again.

        Matching is by WHOLE-PHRASE CONTAINMENT, not equality, because the
        extractor is loose on purpose. A sentence says "Master of Arts in Arts"
        and the phrase that survives the walk is "Arts"; the index key is
        "bachelor of arts". Demanding equality there left the sentence's own
        "2 years" unattributable and refused it.

        Over-inclusive in the safe direction: "Science" picks up both B.Sc and
        M.Sc, and being generous about what a sentence may claim only makes the
        guard quieter.
        """
        out: set[str] = set()
        padded = {k: f" {k} " for k in self._programme_numbers}
        for name in names:
            key = normalise(name)
            if not key:
                continue
            for k, nums in self._programme_numbers.items():
                if k == key or f" {key} " in padded[k]:
                    out |= nums
        return frozenset(out)


@lru_cache(maxsize=1)
def default_index() -> EntityIndex:
    """The process-wide index, read from the shipped knowledge base."""
    return EntityIndex(corpus_text())


@lru_cache(maxsize=1)
def corpus_text() -> str:
    """The raw corpus. Empty string when the file is unreadable.

    Never raises: the guard treats an empty corpus as "no index", and a guard
    that cannot see must abstain, not condemn (BUG-16).
    """
    override = os.environ.get(KB_PATH_ENV)
    candidates = [Path(override)] if override else []
    root = Path(__file__).resolve().parents[1]
    candidates.append(root / _DEFAULT_KB)
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


# ── convenience wrappers over the default index ──────────────────────

def is_known(name: str) -> bool:
    return default_index().is_known(name)


def known_names() -> list[str]:
    return default_index().names


def programme_numbers() -> dict[str, frozenset[str]]:
    return default_index().programme_numbers


def global_numbers() -> frozenset[str]:
    return default_index().global_numbers


def content_runs(text: str, min_words: int = 2) -> list[str]:
    """Runs of content words: what a caller names when they name nothing else.

    BUG-17's shaping half never fired on live call `fbced641`, and this is why.
    Detection was keyword-anchored -- it looked for "X program", "School of X" --
    and the caller said:

        "in mechanical engineering."
        "Bachelor degree."
        "I have 15 years of experience in mechanical engineering."

    None of those has a keyword slot. So `unoffered_entities` returned nothing,
    the prompt never said the thing does not exist, and the model invented a
    whole degree programme across six turns.

    A run is consecutive words that are not boundaries, keywords or connectives.
    Two words minimum: a bare "Kalki" is more likely an STT artifact than a
    programme, and a false positive here makes the agent deny something real.
    """
    out: list[str] = []
    run: list[str] = []
    for tok in _tokens(text):
        word = tok.group(0)
        bare = _bare(word)
        if (bare in _BOUNDARY or bare in _KEYWORDS or bare in _CONNECTIVES
                or not bare):
            if len(run) >= min_words:
                out.append(" ".join(run))
            run = []
            continue
        run.append(word)
    if len(run) >= min_words:
        out.append(" ".join(run))
    return out


def offered_entities(text: str, index: EntityIndex | None = None) -> list[str]:
    """Programmes the caller named that the corpus DOES carry.

    The counterpart to `unoffered_entities`, and it exists for one job: telling
    the difference between a caller who is still on an abandoned subject and one
    who has moved to something real.

    Live call `853548f5`, turn 12: the caller said "computer science." and the
    prompt was still telling the model that the caller had asked about
    mechanical engineering -- so it denied a subject they had left, on the turn
    they named the one they wanted. A time-to-live cannot see that; naming a
    different real programme can.
    """
    idx = index or default_index()
    if not idx._norm:
        return []
    candidates: list[str] = [" ".join(s.content) for s in name_slots(text)]
    candidates.extend(content_runs(text))
    out: list[str] = []
    for cand in candidates:
        if cand.strip() and idx.is_known(cand) and cand.lower() not in {
                o.lower() for o in out}:
            out.append(cand)
    return out


def unoffered_entities(text: str, index: EntityIndex | None = None) -> list[str]:
    """Things in `text` that the corpus does not carry.

    Two sources, because a caller names things two ways. Keyword slots catch
    "I want to study mechanical engineering program"; content runs catch
    "in mechanical engineering", which has no slot at all. Both are checked
    against the corpus, so this is a fact about the university rather than a
    judgement about the conversation.

    Only the NAME part of a slot is reported -- "mechanical engineering", not
    "degree in mechanical engineering" -- because the caller hears this back in
    a sentence, and the prompt block and the spoken refusal both read better
    with the tight phrase.

    This can fire on an STT artifact ("net interest"), which is why the prompt
    block that consumes it is phrased conditionally rather than as an assertion.
    """
    idx = index or default_index()
    if not idx._norm:
        return []
    candidates: list[str] = [" ".join(s.content) for s in name_slots(text)]
    candidates.extend(content_runs(text))
    out: list[str] = []
    for cand in candidates:
        if not cand.strip() or idx.is_known(cand):
            continue
        # Shape filter. A run of ordinary English is not a programme, and the
        # corpus is the only vocabulary list available.
        #
        # The test is on the run's HEAD -- its LAST word, which is what a noun
        # phrase in English is headed by. "mechanical engineering" ends on
        # "engineering", which the corpus uses in a programme name, so it is a
        # name candidate. "engineering related" ends on "related" and
        # "too much piece amount" ends on "amount", neither of which names
        # anything, so both are prose.
        #
        # "ANY word names something" was the first attempt, and it let
        # `engineering related` through live: one domain word plus a modifier is
        # enough to make prose look like a subject. The head is what English
        # actually agrees on.
        if not idx.mentions_entity_word(cand.split()[-1]):
            continue
        if cand.lower() not in {o.lower() for o in out}:
            out.append(cand)
    return out
