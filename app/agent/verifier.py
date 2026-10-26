"""Claim-level verifier for generated answers.

Every currency amount, date, URL, email, phone and programme name in the
answer must be present in the evidence bundle. Fee amounts must be grounded by
a ``verified_fact`` item; other claims may be grounded by an official page.
Critical failures block the answer. A URL-only failure removes the sentence
that contains it and marks the result rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.agent.evidence import EvidenceBundle
from app.programs import DSPMU_PROGRAM_ALIASES, detect_program

_AMOUNT_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+91[\s-]?)?[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{3,4}(?!\d)"
)
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
    "amount", "date", "programme", "email", "phone",
})


@dataclass(frozen=True)
class Claim:
    kind: str
    raw: str
    normalized: str
    start: int
    end: int
    critical: bool


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
                }
                for claim in self.claims
            ],
            "failures": self.failures,
            "citations": self.citations,
            "notes": self.notes,
        }


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

    programme = detect_program(text.lower(), DSPMU_PROGRAM_ALIASES)
    if programme:
        claims.append(
            Claim("programme", programme, programme, 0, len(text), True)
        )

    claims.sort(key=lambda claim: (claim.start, claim.end))
    return claims


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


def verify_answer(answer: str, bundle: EvidenceBundle) -> VerificationResult:
    """Verify every claim in *answer* against *bundle*."""
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

    claims = _extract_claims(text)
    failures: list[dict] = []
    for claim in claims:
        reason = ""
        if claim.kind == "amount":
            try:
                value = float(claim.normalized)
            except ValueError:
                value = None
            if value is None or value not in fact_amounts:
                reason = "fee_amount_not_in_verified_fact"
        elif claim.kind == "date":
            if claim.normalized not in all_dates:
                reason = "date_not_in_evidence"
        elif claim.kind == "url":
            if claim.raw not in evidence_urls and claim.raw not in all_text:
                reason = "url_not_in_evidence"
        elif claim.kind == "email":
            if claim.normalized not in evidence_lower:
                reason = "email_not_in_evidence"
        elif claim.kind == "phone":
            if claim.normalized not in evidence_digits:
                reason = "phone_not_in_evidence"
        elif claim.kind == "programme":
            if not _programme_grounded(claim.normalized, all_text):
                reason = "programme_not_in_evidence"
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
