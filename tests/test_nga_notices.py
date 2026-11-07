"""Deterministic notice answers and time-scope refusals."""

from __future__ import annotations

from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.executor import ExecutionResult
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan
from app.agent.text import _notice_answer_result


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    return EvidenceBundle(
        query="exam schedule",
        intent=Intent.NOTICE,
        items=list(items),
    )


def _item(title: str, text: str, evidence_id: str = "ev-1") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        node_id="notice-1",
        title=title,
        url="https://dspmuranchi.ac.in/uploads/news/schedule.pdf",
        authority="official_document",
        verified_at="2026-10-26T00:00:00Z",
        snippet=text,
        text=text,
        status="ok",
    )


def _result(query: str, bundle: EvidenceBundle):
    plan = Plan(intent=Intent.NOTICE, steps=())
    execution = ExecutionResult(plan=plan)
    return _notice_answer_result(
        query,
        plan,
        execution,
        bundle,
        facts=FactStore(),
    )


def test_a_matching_month_notice_is_answered_deterministically():
    bundle = _bundle(
        _item(
            "07 Oct 2026 Examination Schedule",
            "13-10-2026 Tuesday All Subjects; 23-10-2026 Friday MDC-101.",
        )
    )

    result = _result("exam schedule oct 2026", bundle)

    assert result is not None
    assert result.status == "verified"
    assert result.answer_type == "answer"
    assert "07 Oct 2026 Examination Schedule" in result.answer
    assert "https://dspmuranchi.ac.in/uploads/news/schedule.pdf" in result.answer


def test_a_year_label_is_not_a_march_schedule():
    bundle = _bundle(
        _item(
            "EXAMINATION SCHEDULE 2025",
            "Postgraduate end-semester examination schedule, 2025.",
        )
    )

    result = _result("exam schedule for march 2025", bundle)

    assert result is not None
    assert result.status == "no_evidence"
    assert result.answer_type == "refusal"
    assert result.error == "time_scope_mismatch"
    assert "March 2025" in result.answer
    assert "contact the registrar" in result.answer.lower()
