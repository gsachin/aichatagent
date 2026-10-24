"""Conservative text-level PII canaries."""

from __future__ import annotations

import pytest

from app.pii import is_clean, scan_text


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Roll No: 123456", "roll_or_registration_number"),
        ("Registration Number: ABC/2026/1", "roll_or_registration_number"),
        ("Date of Birth: 01-01-2005", "date_of_birth"),
        ("Father's Name: A Person", "parent_name"),
        ("Aadhaar No: 1234 5678 9012", "aadhaar_or_pan"),
        ("Merit List of Selected Candidates", "merit_or_selection_list"),
        ("Selected candidates must report", "merit_or_selection_list"),
        ("Name of the candidate: A Person", "candidate_or_student_name"),
        ("Account No: 1234567890", "bank_account"),
        ("123456789012", "aadhaar_like_number"),
        ("DSPMU2026001", "roll_like_identifier"),
    ],
)
def test_pii_shapes_are_flagged(text, reason):
    scan = scan_text(text)
    assert scan.flagged
    assert reason in scan.reasons


def test_clean_notice_text_is_not_flagged():
    text = (
        "The last date for application is 25.10.2026. "
        "The annual fee is ₹3,410 for the general category. "
        "Contact registrar@dspmuranchi.ac.in or +91 930 455 261."
    )
    assert is_clean(text)
    assert scan_text(text).reasons == ()


def test_scan_does_not_return_the_matched_value():
    scan = scan_text("Roll No: 123456")
    assert "123456" not in scan.reason_text
    assert "123456" not in " ".join(scan.reasons)
