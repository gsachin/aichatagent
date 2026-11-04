"""
Pure offer-eligibility decision.

No database, network, or settings access: the caller passes the lead, the
program and the stored validation rows, and gets a decision back. That keeps the
rule table-testable and makes shadow mode and enforcement use the same code.

The rule is the "based on the marks" rule agreed on 2026-11-04:

* Bachelor programs gate on the Class-12 marksheet (percentage >= 70).
* Master and MBA programs gate on the undergraduate transcript (GPA >= 3.2/4.0).
* A passed gate document issues the offer; a below-threshold mark does not.
* A wrong document type asks for the correct document — it is not a rejection.
* An unreadable or uncertain document goes to manual review.

The API has already applied the numeric threshold; this module reads its
``validation_status``/``requirement_status`` rather than re-deriving the score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.crm.identity import program_level, validation_document_type

GATE_DOCUMENTS = {
    "Bachelor's": "CLASS_12_MARKSHEET",
    "Master's": "UNDERGRADUATE_TRANSCRIPT",
}

GATE_LABELS = {
    "CLASS_12_MARKSHEET": "12th marksheet",
    "UNDERGRADUATE_TRANSCRIPT": "undergraduate transcript",
}

MISMATCH_CODES = frozenset({
    "DOCUMENT_TYPE_MISMATCH",
    "ACADEMIC_LEVEL_MISMATCH",
    "DOCUMENT_NOT_ACCEPTED_FOR_PROGRAM",
})


@dataclass(frozen=True)
class OfferDecision:
    """What the offer pipeline should do, and why."""

    action: str
    reason: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _canonical_type(row: Mapping[str, Any]) -> str:
    canonical = str(row.get("api_document_type") or "").strip().upper()
    if canonical:
        return canonical
    return validation_document_type(row.get("doc_type")) or ""


def _reason_codes(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("reason_codes") or ""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [
        part.strip()
        for part in str(raw).replace(",", ";").split(";")
        if part.strip()
    ]


def _active_gate_row(
    validation_results: Sequence[Mapping[str, Any]] | None,
    gate_type: str,
) -> Mapping[str, Any] | None:
    """The latest active row for the gate document type, if any."""
    candidates = [
        row
        for row in (validation_results or [])
        if row.get("is_active") is True and _canonical_type(row) == gate_type
    ]
    return candidates[-1] if candidates else None


def _mark(row: Mapping[str, Any]) -> str:
    for key in ("extracted_score", "extracted_percentage", "extracted_gpa"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _threshold(row: Mapping[str, Any]) -> str:
    for key in ("score_threshold", "minimum_required_percentage", "minimum_required_gpa"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _describe(mark: str, threshold: str) -> str:
    if mark and threshold:
        return f"shows {mark} (requirement {threshold})"
    if mark:
        return f"shows {mark}"
    return "could not be read reliably"


def assess_offer_eligibility(
    lead: Mapping[str, Any] | None,
    program: object,
    validation_results: Sequence[Mapping[str, Any]] | None,
) -> OfferDecision:
    """Decide whether the marks in the stored evidence justify an offer."""
    lead = lead or {}
    program_text = program if str(program or "").strip() else lead.get("program_interest")

    level = program_level(program_text)
    if level is None:
        return OfferDecision(
            action="block_unknown",
            reason="program-unclassified",
            message=(
                "We could not match your program to an admission level. "
                "Our admissions team will review your application."
            ),
            evidence={"program": str(program_text or "")},
        )

    gate_type = GATE_DOCUMENTS[level]
    gate_label = GATE_LABELS[gate_type]
    row = _active_gate_row(validation_results, gate_type)
    if row is None:
        return OfferDecision(
            action="block_missing",
            reason="gate-document-missing",
            message=f"To check your eligibility, please upload your {gate_label}.",
            evidence={"program_level": level, "gate_document": gate_type},
        )

    status = str(row.get("validation_status") or "").strip().upper()
    requirement = str(row.get("requirement_status") or "").strip().upper()
    codes = _reason_codes(row)
    mark = _mark(row)
    threshold = _threshold(row)
    evidence = {
        "program_level": level,
        "gate_document": gate_type,
        "document_type": _canonical_type(row),
        "document_id": str(row.get("document_id") or ""),
        "score": mark,
        "threshold": threshold,
        "validation_status": status,
        "requirement_status": requirement,
        "reason_codes": codes,
    }

    if status == "PASSED":
        return OfferDecision(
            action="issue",
            reason="requirement-satisfied",
            message=(
                f"Your {gate_label} {_describe(mark, threshold)}. "
                "You meet the requirement — I'm generating your offer letter now."
            ),
            evidence=evidence,
        )

    if status == "FAILED":
        if any(code in MISMATCH_CODES for code in codes):
            return OfferDecision(
                action="block_wrong_document",
                reason="wrong-document",
                message=(
                    f"That document does not match what your program needs. "
                    f"Please upload your {gate_label}."
                ),
                evidence=evidence,
            )
        return OfferDecision(
            action="block_not_eligible",
            reason="below-threshold",
            message=(
                f"Your {gate_label} shows {mark or 'a mark'}, below the "
                f"{threshold or 'required'} threshold. I can't issue an offer "
                "for this program."
            ),
            evidence=evidence,
        )

    if status == "REVIEW_REQUIRED" or requirement == "REVIEW_REQUIRED":
        return OfferDecision(
            action="block_review",
            reason="manual-review",
            message=(
                "I couldn't reliably read the marks on your document. "
                "Our admissions team will review it."
            ),
            evidence=evidence,
        )

    return OfferDecision(
        action="block_unknown",
        reason="validation-unknown",
        message=(
            "I received your document but couldn't verify it right now. "
            "Our team will review it."
        ),
        evidence=evidence,
    )
