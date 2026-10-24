"""Conservative text-level PII canaries for notice bodies.

The notice catalogue's category/title filter is not a substitute for scanning
the extracted PDF text. This module does not try to identify every person; it
flags the shapes that commonly carry student PII in Indian university notices:

* roll / registration / enrolment / application identifiers
* dates of birth and parent names
* Aadhaar/PAN/bank-account identifiers
* merit, selection, waiting and candidate/student lists

A flagged body is never indexed. The scan returns reason labels, not matched
strings, so a log line cannot become a second copy of the PII.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Pattern labels are stable; they are written to the review manifest.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "roll_or_registration_number",
        re.compile(
            r"\b(?:roll|registration|enrol(?:l)?ment|application)"
            r"\s*(?:no\.?|number|id)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "date_of_birth",
        re.compile(r"\b(?:date\s+of\s+birth|d\.?\s*o\.?\s*b\.?)\b", re.IGNORECASE),
    ),
    (
        "parent_name",
        re.compile(
            r"\b(?:father|mother|guardian)(?:'s)?\s+name\b", re.IGNORECASE
        ),
    ),
    (
        "aadhaar_or_pan",
        re.compile(
            r"\b(?:aadhaar|aadhar|pan)\s*(?:card|no\.?|number)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bank_account",
        re.compile(
            r"\b(?:bank\s+account|account\s+no\.?|ifsc)\b", re.IGNORECASE
        ),
    ),
    (
        "merit_or_selection_list",
        re.compile(
            r"\b(?:merit|selection|selected|waiting|provisional)\s+list\b"
            r"|\bselected\s+candidates?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "candidate_or_student_name",
        re.compile(
            r"\b(?:name\s+of\s+(?:the\s+)?(?:candidate|student)"
            r"|(?:candidate|student)(?:'s)?\s+name)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "aadhaar_like_number",
        re.compile(r"\b\d{12}\b"),
    ),
    (
        "roll_like_identifier",
        re.compile(r"\b[A-Z]{2,6}[-/]?\d{6,}\b"),
    ),
)


@dataclass(frozen=True)
class PiiScan:
    flagged: bool
    reasons: tuple[str, ...] = ()

    @property
    def reason_text(self) -> str:
        return ", ".join(self.reasons)


def scan_text(text: str) -> PiiScan:
    """Return the reason labels found in *text*; never the matched values."""
    reasons: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.search(text or "") and label not in reasons:
            reasons.append(label)
    return PiiScan(flagged=bool(reasons), reasons=tuple(reasons))


def is_clean(text: str) -> bool:
    return not scan_text(text).flagged
