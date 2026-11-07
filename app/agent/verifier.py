"""Claim-level verifier for generated answers.

Every currency amount, date, URL, email, phone and programme name in the
answer must be present in the evidence bundle. Fee amounts must be grounded by
a ``verified_fact`` item; other claims may be grounded by an official page.
Critical failures block the answer. A URL-only failure removes the sentence
that contains it and marks the result rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.agent.evidence import EvidenceBundle
from app.programs import detect_program_mentions

_AMOUNT_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+91[\s-]?)?[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{3,4}(?!\d)"
)
# A name token is a capitalised word or an initial. It deliberately does not
# end in a bare ".", so a sentence boundary is never read as part of the name:
# without this, "Prof. Rajiv Manohar. Source:" extracted the person as
# "Rajiv Manohar Source" and the verifier blocked a correct answer.
_NAME_TOKEN = r"(?:[A-Z]\.|[A-Z][A-Za-z'’\-]*)"
_NAME_BODY = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}}"
_PERSON_TITLE_RE = re.compile(
    r"\b(?:Prof(?:essor)?\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?)\s+"
    rf"{_NAME_BODY}"
)
_ROLE_NAME_RE = re.compile(
    r"(?i:\b(?:vice[\s-]?chancellor|vc)\b\s*(?:is|was|:|-|–|—)\s*)"
    rf"((?:Prof(?:essor)?\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?)?\s*"
    rf"{_NAME_BODY})"
)
_NAME_ROLE_RE = re.compile(
    rf"((?:Prof(?:essor)?\.?|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Shri|Smt\.?)?\s*"
    rf"{_NAME_BODY})\s+"
    r"(?i:(?:is|was|has been)\s+(?:the\s+)?(?:vice[\s-]?chancellor|vc))\b"
)
_PERSON_HONORIFICS = frozenset({
    "prof", "professor", "dr", "mr", "mrs", "ms", "shri", "smt",
})
_ORGANISATION_WORDS = frozenset({
    "university", "college", "institute", "department", "school",
    "faculty", "office", "cell", "committee", "council", "board",
    "government", "jharkhand", "ranchi", "india", "association",
    "society", "trust", "academy", "centre", "center", "mission",
    "programme", "program",
})
_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b"),
    re.compile(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)[a-z]*\s+(\d{4})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b",
        re.IGNORECASE,
    ),
)
_DATE_FORMATS = (
    "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
    "%d %B, %Y", "%d %b, %Y",
)
_PROGRAMME_STOPWORDS = frozenset({
    "of", "in", "the", "and", "for", "a", "an",
})
_GENERIC_DEGREE_WORDS = frozenset({
    "bachelor", "arts", "science", "commerce", "administration",
})
_ACRONYMS = frozenset({"ba", "bsc", "bcom", "bca", "bba"})
_CRITICAL_KINDS = frozenset({
    "amount", "date", "programme", "email", "phone", "person",
})
_UG_LEVEL_RE = re.compile(
    r"\b(?:b\.?\s?sc|b\.?\s?a|b\.?\s?com|bba|bca|b\.?\s?ed|"
    r"b\.?\s?f\.?\s?sc|ug|undergraduate|under\s+graduate)\b",
    re.IGNORECASE,
)
_PG_LEVEL_RE = re.compile(
    r"\b(?:m\.?\s?sc|m\.?\s?a|m\.?\s?com|mba|mca|ll\.?\s?m|"
    r"m\.?\s?ed|mha|pg|postgraduate|post\s+graduate)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Claim:
    kind: str
    raw: str
    normalized: str
    start: int
    end: int
    critical: bool
    evidence_ids: tuple[str, ...] = ()
    level: str = ""  # UG | PG | PhD for a programme claim

    @classmethod
    def from_dict(cls, data: dict) -> "Claim":
        return cls(
            kind=str(data.get("kind") or ""),
            raw=str(data.get("raw") or ""),
            normalized=str(data.get("normalized") or ""),
            start=int(data.get("start") or 0),
            end=int(data.get("end") or 0),
            critical=bool(data.get("critical")),
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            level=str(data.get("level") or ""),
        )


@dataclass
class VerificationResult:
    answer: str
    verified: bool
    status: str  # verified | rewritten | blocked
    claims: list[Claim] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "verified": self.verified,
            "status": self.status,
            "claims": [
                {
                    "kind": claim.kind,
                    "raw": claim.raw,
                    "normalized": claim.normalized,
                    "critical": claim.critical,
                    "evidence_ids": list(claim.evidence_ids),
                    "level": claim.level,
                }
                for claim in self.claims
            ],
            "failures": self.failures,
            "citations": self.citations,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResult":
        return cls(
            answer=str(data.get("answer") or ""),
            verified=bool(data.get("verified")),
            status=str(data.get("status") or "blocked"),
            claims=[
                Claim.from_dict(item)
                for item in (data.get("claims") or [])
            ],
            failures=list(data.get("failures") or []),
            citations=list(data.get("citations") or []),
            notes=list(data.get("notes") or []),
        )


def _amounts(text: str) -> set[int | float]:
    out: set[int | float] = set()
    for match in _AMOUNT_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        out.add(int(value) if value.is_integer() else value)
    return out


def _normalise_date(raw: str) -> str:
    text = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text.lower()


def _dates(text: str) -> set[str]:
    out: set[str] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text or ""):
            out.add(_normalise_date(match.group(0)))
    return {value for value in out if value}


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _person_key(value: str) -> str:
    """Name comparison key: case, punctuation and honorific insensitive."""
    tokens = re.findall(r"[a-z0-9]+", (value or "").lower())
    tokens = [token for token in tokens if token not in _PERSON_HONORIFICS]
    return " ".join(tokens)


def _looks_like_organisation(value: str) -> bool:
    return bool(set(re.findall(r"[a-z0-9]+", value.lower())) & _ORGANISATION_WORDS)


def _tokens(text: str) -> set[str]:
    return {
        re.sub(r"s$", "", token) if len(token) > 3 else token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
    }


def _programme_tokens(programme: str) -> set[str]:
    return _tokens(programme) - _PROGRAMME_STOPWORDS


def _programme_grounded(programme: str, evidence_text: str) -> bool:
    evidence_tokens = _tokens(evidence_text)
    tokens = _programme_tokens(programme)
    subject = tokens - _ACRONYMS - _GENERIC_DEGREE_WORDS
    acronyms = tokens & _ACRONYMS
    if subject and subject <= evidence_tokens:
        return True
    if acronyms and acronyms <= evidence_tokens:
        return True
    if not subject and not acronyms and tokens and tokens <= evidence_tokens:
        return True
    return False


def _extract_claims(answer: str) -> list[Claim]:
    text = answer or ""
    url_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    claims: list[Claim] = []
    seen: set[tuple[str, int, int]] = set()

    def inside_url(start: int, end: int) -> bool:
        return any(start < u_end and end > u_start for u_start, u_end in url_spans)

    for match in _AMOUNT_RE.finditer(text):
        raw = match.group(0)
        value = match.group(1).replace(",", "")
        key = ("amount", match.start(), match.end())
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            Claim(
                "amount", raw, value, match.start(), match.end(), True
            )
        )

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if inside_url(match.start(), match.end()):
                continue
            raw = match.group(0)
            key = ("date", match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                Claim(
                    "date", raw, _normalise_date(raw),
                    match.start(), match.end(), True,
                )
            )

    for match in _URL_RE.finditer(text):
        claims.append(
            Claim(
                "url", match.group(0), match.group(0),
                match.start(), match.end(), False,
            )
        )

    for match in _EMAIL_RE.finditer(text):
        if inside_url(match.start(), match.end()):
            continue
        claims.append(
            Claim(
                "email", match.group(0), match.group(0).lower(),
                match.start(), match.end(), True,
            )
        )

    for match in _PHONE_RE.finditer(text):
        if inside_url(match.start(), match.end()):
            continue
        claims.append(
            Claim(
                "phone", match.group(0), _digits(match.group(0)),
                match.start(), match.end(), True,
            )
        )

    # Person names are critical: a wrong VC or dean must not pass because it
    # is not a fee/date. The role form catches "VC is X"; the honorific form
    # catches "Prof. X" in prose. Organisation names that begin with Dr/Prof
    # are excluded so "Dr. Shyama Prasad Mukherjee University" is not a person.
    person_keys: set[str] = set()
    person_matches: list[tuple[int, int, str]] = []
    for match in _ROLE_NAME_RE.finditer(text):
        person_matches.append(
            (match.start(1), match.end(1), match.group(1))
        )
    for match in _NAME_ROLE_RE.finditer(text):
        person_matches.append(
            (match.start(1), match.end(1), match.group(1))
        )
    for match in _PERSON_TITLE_RE.finditer(text):
        person_matches.append(
            (match.start(), match.end(), match.group(0))
        )
    for start, end, raw in sorted(person_matches):
        if inside_url(start, end):
            continue
        value = raw.strip(" .,;:()[]")
        key = _person_key(value)
        if not key or key in person_keys or _looks_like_organisation(value):
            continue
        # A title regex can stop just before "University" in
        # "Dr. Shyama Prasad Mukherjee University"; do not call that a person.
        following = text[end:end + 40].lstrip().lower()
        if re.match(
            r"(?:university|college|institute|department|school|faculty)\b",
            following,
        ):
            continue
        person_keys.add(key)
        claims.append(
            Claim("person", value, key, start, end, True)
        )

    # Only an explicit degree/level + subject is a programme claim. The old
    # UG-fee alias detector turned "M.Sc. Information Technology" into
    # "B.Sc. Information Technology" and blocked a correct answer.
    for mention in detect_program_mentions(text):
        claims.append(
            Claim(
                "programme",
                mention.raw,
                mention.subject,
                mention.start,
                mention.end,
                True,
                level=mention.level,
            )
        )

    claims.sort(key=lambda claim: (claim.start, claim.end))
    return claims


_CITATION_RE = re.compile(r"\[(ev-\d+)\]")


def _sentence_bounds(text: str, position: int) -> tuple[int, int]:
    start = 0
    for marker in (".", "!", "?", "\n"):
        found = text.rfind(marker, 0, position)
        if found >= start:
            start = found + 1
    ends = [
        found for found in (
            text.find(marker, position)
            for marker in (".", "!", "?", "\n")
        )
        if found >= 0
    ]
    return start, (min(ends) if ends else len(text))


def _attach_citations(answer: str, claims: list[Claim]) -> list[Claim]:
    """Attach ``[ev-N]`` citations in the same sentence to each claim."""
    citations = [
        (match.start(), match.group(1))
        for match in _CITATION_RE.finditer(answer or "")
    ]
    if not citations:
        return claims
    out: list[Claim] = []
    for claim in claims:
        # Whole-text programme claims are not sentence-local; leave them
        # uncited and let the global fallback verify them.
        if claim.start == 0 and claim.end >= len(answer):
            out.append(claim)
            continue
        start, end = _sentence_bounds(answer, claim.start)
        ids = tuple(dict.fromkeys(
            evidence_id
            for position, evidence_id in citations
            if start <= position <= end
        ))
        out.append(replace(claim, evidence_ids=ids) if ids else claim)
    return out


def _remove_sentences(answer: str, failures: list[dict]) -> str:
    raw_values = [failure["raw"] for failure in failures if failure.get("raw")]
    if not raw_values:
        return answer
    sentences = re.split(r"(?<=[.!?])\s+", answer or "")
    kept = [
        sentence for sentence in sentences
        if not any(raw in sentence for raw in raw_values)
    ]
    return " ".join(kept).strip()


def verify_answer(
    answer: str,
    bundle: EvidenceBundle,
    *,
    require_citations: bool = False,
) -> VerificationResult:
    """Verify every claim in *answer* against *bundle*.

    When the answer carries ``[ev-N]`` markers, each claim is checked against
    those cited items only. Without markers, the historical whole-bundle
    fallback is kept. ``require_citations`` turns an uncited critical claim
    into a failure; it defaults to false for compatibility with callers and
    fixtures that predate citations.
    """
    text = (answer or "").strip()
    if not text:
        return VerificationResult(
            answer="",
            verified=False,
            status="blocked",
            citations=[citation.to_dict() for citation in bundle.citations],
            notes=["empty_answer"],
        )
    if not bundle.items:
        return VerificationResult(
            answer="",
            verified=False,
            status="blocked",
            citations=[],
            notes=["no_evidence"],
        )

    fact_items = [item for item in bundle.items if item.authority == "verified_fact"]
    page_items = [item for item in bundle.items if item.authority != "verified_fact"]
    fact_text = " ".join(item.text for item in fact_items)
    page_text = " ".join(item.text for item in page_items)
    all_text = f"{fact_text} {page_text}".strip()
    fact_amounts = _amounts(fact_text)
    all_dates = _dates(all_text)
    evidence_urls = {item.url for item in bundle.items if item.url}
    evidence_digits = _digits(all_text)
    evidence_lower = all_text.lower()
    person_names = {
        _person_key(item.person_name)
        for item in bundle.items
        if item.person_name
    }
    evidence_people = _person_key(all_text)

    claims = _attach_citations(text, _extract_claims(text))
    items_by_id = {item.evidence_id: item for item in bundle.items}
    failures: list[dict] = []
    for claim in claims:
        missing_ids = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_id not in items_by_id
        ]
        if missing_ids:
            failures.append(
                {
                    "kind": claim.kind,
                    "raw": claim.raw,
                    "normalized": claim.normalized,
                    "reason": "citation_not_found",
                    "critical": claim.kind in _CRITICAL_KINDS,
                }
            )
            continue
        if (
            require_citations
            and claim.kind in _CRITICAL_KINDS
            and not claim.evidence_ids
            and not (claim.start == 0 and claim.end >= len(text))
        ):
            failures.append(
                {
                    "kind": claim.kind,
                    "raw": claim.raw,
                    "normalized": claim.normalized,
                    "reason": "uncited_claim",
                    "critical": True,
                }
            )
            continue

        cited_items = [
            items_by_id[evidence_id]
            for evidence_id in claim.evidence_ids
        ]
        cited_text = " ".join(item.text for item in cited_items)
        scope_text = cited_text or all_text
        scope_lower = scope_text.lower()
        scope_digits = _digits(scope_text)
        scope_dates = _dates(scope_text) if cited_text else all_dates
        scope_urls = (
            {item.url for item in cited_items if item.url}
            if cited_text else evidence_urls
        )
        scope_people = _person_key(scope_text) if cited_text else evidence_people
        scope_person_names = (
            {
                _person_key(item.person_name)
                for item in cited_items
                if item.person_name
            }
            if cited_text else person_names
        )

        reason = ""
        if claim.kind == "amount":
            try:
                value = float(claim.normalized)
            except ValueError:
                value = None
            if value is None or value not in fact_amounts:
                reason = "fee_amount_not_in_verified_fact"
            elif cited_text and value not in _amounts(cited_text):
                reason = "claim_not_in_cited_evidence"
        elif claim.kind == "date":
            if claim.normalized not in scope_dates:
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "date_not_in_evidence"
                )
        elif claim.kind == "url":
            if claim.raw not in scope_urls and claim.raw not in scope_text:
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "url_not_in_evidence"
                )
        elif claim.kind == "email":
            if claim.normalized not in scope_lower:
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "email_not_in_evidence"
                )
        elif claim.kind == "phone":
            if claim.normalized not in scope_digits:
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "phone_not_in_evidence"
                )
        elif claim.kind == "person":
            if (
                claim.normalized not in scope_person_names
                and claim.normalized not in scope_people
            ):
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "person_not_in_evidence"
                )
        elif claim.kind == "programme":
            if not _programme_grounded(claim.normalized, scope_text):
                reason = (
                    "claim_not_in_cited_evidence"
                    if cited_text else "programme_not_in_evidence"
                )
            elif claim.level == "PG" and _UG_LEVEL_RE.search(scope_text) and not _PG_LEVEL_RE.search(scope_text):
                reason = "programme_level_mismatch"
            elif claim.level == "UG" and _PG_LEVEL_RE.search(scope_text) and not _UG_LEVEL_RE.search(scope_text):
                reason = "programme_level_mismatch"
        if reason:
            failures.append(
                {
                    "kind": claim.kind,
                    "raw": claim.raw,
                    "normalized": claim.normalized,
                    "reason": reason,
                    "critical": claim.kind in _CRITICAL_KINDS,
                }
            )

    notes: list[str] = []
    if not claims:
        notes.append("no_claims")
    if not fact_items:
        notes.append("no_verified_fact_evidence")

    critical = [failure for failure in failures if failure["critical"]]
    if critical:
        return VerificationResult(
            answer="",
            verified=False,
            status="blocked",
            claims=claims,
            failures=failures,
            citations=[citation.to_dict() for citation in bundle.citations],
            notes=notes,
        )

    if failures:
        rewritten = _remove_sentences(text, failures)
        if not rewritten:
            return VerificationResult(
                answer="",
                verified=False,
                status="blocked",
                claims=claims,
                failures=failures,
                citations=[citation.to_dict() for citation in bundle.citations],
                notes=notes,
            )
        return VerificationResult(
            answer=rewritten,
            verified=True,
            status="rewritten",
            claims=claims,
            failures=failures,
            citations=[citation.to_dict() for citation in bundle.citations],
            notes=notes,
        )

    return VerificationResult(
        answer=text,
        verified=True,
        status="verified",
        claims=claims,
        failures=[],
        citations=[citation.to_dict() for citation in bundle.citations],
        notes=notes,
    )
