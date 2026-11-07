"""Evidence adequacy gate tests.

A non-empty bundle is not evidence. These tests pin the cases the live agent
must treat as inadequate so it explores or refuses instead of answering from
generic pages.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.adequacy import assess_bundle, deep_search_query
from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.models import Intent

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _item(
    evidence_id: str = "ev-1",
    *,
    title: str,
    snippet: str = "",
    text: str = "",
    authority: str = "official_page",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        node_id=f"node-{evidence_id}",
        title=title,
        url=f"https://dspmuranchi.ac.in/{evidence_id}",
        authority=authority,
        verified_at="2026-10-26T00:00:00Z",
        snippet=snippet or text or title,
        text=text or snippet or title,
    )


def _bundle(query: str, items: list[EvidenceItem], intent=Intent.PAGE_TOPIC):
    return EvidenceBundle(query=query, intent=intent, items=items)


def test_deep_search_query_strips_conversational_filler():
    assert deep_search_query("which programs currently university") == "programmes"
    assert (
        deep_search_query(
            "can you check deep search to find university program"
        )
        == "programmes"
    )
    assert deep_search_query("what programmes are offered") == "programmes"
    assert deep_search_query("library timings") == "library timings"
    assert deep_search_query("hostel rules") == "hostel rules"


def test_empty_bundle_is_inadequate():
    result = assess_bundle("vice chancellor", _bundle("vice chancellor", []))
    assert result.adequate is False
    assert result.reasons


def test_no_specific_terms_is_inadequate():
    result = assess_bundle(
        "who are you", _bundle("who are you", [_item(title="About the University")])
    )
    assert result.adequate is False
    assert "no_specific_terms" in result.reasons


def test_vice_chancellor_title_is_adequate():
    result = assess_bundle(
        "who is the vice chancellor",
        _bundle(
            "who is the vice chancellor",
            [
                _item(
                    title="Vice Chancellor",
                    text="Prof. Rajiv Manohar is the Vice Chancellor.",
                )
            ],
        ),
    )
    assert result.adequate is True


def test_complaint_status_snippet_is_adequate():
    result = assess_bundle(
        "complaint status",
        _bundle(
            "complaint status",
            [
                _item(
                    title="Grievance Redressal Cell",
                    snippet="Track Complaint Status and current progress.",
                    text="Grievance Redressal Cell. Track Complaint Status.",
                )
            ],
        ),
    )
    assert result.adequate is True


def test_generic_programme_pages_are_inadequate():
    result = assess_bundle(
        "which programs currently university",
        _bundle(
            "which programs currently university",
            [
                _item(
                    title="Best Practices",
                    text="The programme currently follows the NEP framework.",
                ),
                _item(
                    evidence_id="ev-2",
                    title="Cbcs Nep 2020",
                    text="The university currently runs programmes under NEP.",
                ),
            ],
        ),
    )
    assert result.adequate is False
    assert "list_shape_missing" in result.reasons or "missing_terms" in result.reasons


def test_courses_page_can_answer_a_programme_list_question():
    result = assess_bundle(
        "what programmes are offered",
        _bundle(
            "what programmes are offered",
            [
                _item(
                    title="Vocational & Self-Finance Courses",
                    text="Self-Financing/Vocational Courses. Department of Yoga.",
                )
            ],
        ),
    )
    assert result.adequate is True


def test_library_timings_with_exam_notice_is_inadequate():
    result = assess_bundle(
        "library timings",
        _bundle(
            "library timings",
            [
                _item(
                    title="EXAMINATION NOTICE - REVISED EXAMINATION TIMINGS",
                    text="Revised Examination Timings UG Semester IV.",
                )
            ],
        ),
    )
    assert result.adequate is False
    assert "missing_terms" in result.reasons


def test_hostel_rules_with_fee_notice_is_inadequate():
    result = assess_bundle(
        "hostel rules",
        _bundle(
            "hostel rules",
            [
                _item(
                    title="Notification Regarding Hostel Allotment Fees - 2022",
                    text="Hostel allotment fee for the academic session.",
                )
            ],
        ),
    )
    assert result.adequate is False
    assert "missing_terms" in result.reasons


def test_a_matching_verified_fact_is_adequate_even_for_contact_intent():
    fact = _item(
        title="University contact",
        text="Registrar email registrar@dspmuranchi.ac.in phone +91 930 455 261",
        authority="verified_fact",
    )
    result = assess_bundle(
        "contact registrar",
        _bundle("contact registrar", [fact], intent=Intent.CONTACT),
    )
    assert result.adequate is True


def test_time_scope_requires_dates_that_cover_the_request():
    covered = assess_bundle(
        "exam schedule oct 2026",
        _bundle(
            "exam schedule oct 2026",
            [
                _item(
                    title="Examination Schedule",
                    text="Semester I examination will be held on 13-10-2026.",
                )
            ],
        ),
    )
    assert covered.adequate is True

    uncovered = assess_bundle(
        "exam schedule oct 2026",
        _bundle(
            "exam schedule oct 2026",
            [_item(title="Examination Schedule", text="Schedule will be notified.")],
        ),
    )
    assert uncovered.adequate is False
    assert "time_scope_uncovered" in uncovered.reasons
