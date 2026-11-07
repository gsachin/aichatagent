"""Contract tests for every deterministic critical-fact answer builder.

Each builder must source its answer from the evidence bundle it verifies
against, run without an LLM, and record a reason when an applicable builder
declines so operators are never left with a silent fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.executor import ExecutionResult
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan
from app.agent.text import build_text_answer, plan_text
from tests.nga_helpers import build_store, seed_page

URL_RE = re.compile(r"https?://[^\s\])]+")
PROGRAMMES = Path(__file__).resolve().parent / "fixtures" / "nga" / "programmes.json"


def _must_not_call(_messages):
    raise AssertionError("a deterministic builder must not call the LLM")


def _bundle_corpus(result) -> str:
    bundle = result.evidence
    assert bundle is not None
    parts: list[str] = []
    for item in bundle.items:
        parts.extend([item.title, item.text, item.url])
    for citation in bundle.citations:
        parts.extend([citation.title, citation.url, citation.snippet])
    return "\n".join(parts)


def _assert_grounded(result):
    assert result is not None
    assert result.status in ("verified", "rewritten")
    assert result.answer_type == "answer"
    corpus = _bundle_corpus(result)
    for url in URL_RE.findall(result.answer):
        assert url.rstrip(".,;)") in corpus
    return result


def test_admission_builder_grounds_its_answer_in_the_bundle(tmp_path):
    _store, db = build_store(tmp_path)
    result = _assert_grounded(
        build_text_answer(
            "admission process",
            graph_path=str(db),
            pii_guard=False,
            llm=_must_not_call,
        )
    )

    assert "jharkhanduniversities.nic.in" in result.answer
    assert not any(
        decline["builder"] == "admission"
        for decline in result.builder_declines
    )


def test_fee_builder_grounds_its_answer_in_the_bundle(tmp_path):
    _store, db = build_store(tmp_path)
    result = _assert_grounded(
        build_text_answer(
            "BA History fee",
            graph_path=str(db),
            pii_guard=False,
            llm=_must_not_call,
        )
    )

    corpus = _bundle_corpus(result).replace(",", "")
    assert "3410" in result.answer.replace(",", "")
    assert "3185" in result.answer.replace(",", "")
    assert "3410" in corpus
    assert "3185" in corpus


def test_programme_builder_grounds_its_answer_in_the_bundle(
    tmp_path, override_settings
):
    override_settings(
        NGA_CATALOGUE_ENABLED=True,
        NGA_CATALOGUE_PATH=str(PROGRAMMES),
        NGA_CATALOGUE_INCLUDE_DRAFT=True,
        DEEP_SEARCH_ENABLED=False,
    )
    _store, db = build_store(tmp_path)
    result = _assert_grounded(
        build_text_answer(
            "M.Sc. Information Technology",
            graph_path=str(db),
            pii_guard=False,
            llm=_must_not_call,
        )
    )

    assert result.verification is not None
    assert result.verification.verified
    assert "M.Sc Information Technology" in result.answer
    assert "https://" in result.answer


def test_programme_builder_answers_in_hindi_without_an_llm(
    tmp_path, override_settings
):
    override_settings(
        NGA_CATALOGUE_ENABLED=True,
        NGA_CATALOGUE_PATH=str(PROGRAMMES),
        NGA_CATALOGUE_INCLUDE_DRAFT=True,
        DEEP_SEARCH_ENABLED=False,
    )
    _store, db = build_store(tmp_path)
    result = _assert_grounded(
        build_text_answer(
            "M.Sc Information Technology programme",
            graph_path=str(db),
            pii_guard=False,
            language="hi",
            llm=_must_not_call,
        )
    )

    assert result.builder == "programme"
    assert "स्नातकोत्तर" in result.answer
    assert "M.Sc Information Technology" in result.answer


def test_hindi_refusal_text_is_not_mistaken_for_an_answer():
    from app.agent.answer import looks_like_refusal

    assert looks_like_refusal(
        "M.Sc. Information Technology के बारे में सत्यापित जानकारी "
        "उपलब्ध नहीं है।",
        allow_citations=True,
    )


def test_a_cached_critical_answer_keeps_its_builder(tmp_path):
    _store, db = build_store(tmp_path)

    first = build_text_answer(
        "BA History fee",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )
    second = build_text_answer(
        "BA History fee",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert first is not None and second is not None
    assert first.builder == "fee"
    assert second.builder == "fee"
    assert second.verification is not None
    assert "cache_hit" in second.verification.notes


def test_a_hindi_llm_refusal_is_classified_as_a_refusal(tmp_path):
    store, db = build_store(tmp_path)
    seed_page(store)

    result = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: (
            "क्षमा करें, सत्यापित जानकारी उपलब्ध नहीं है।"
        ),
    )

    assert result is not None
    assert result.answer_type == "refusal"
    assert result.status == "verified"


def test_notice_builder_grounds_its_answer_in_the_bundle():
    from app.agent.text import _notice_answer_result

    item = EvidenceItem(
        evidence_id="ev-1",
        node_id="notice-1",
        title="07 Oct 2026 Examination Schedule",
        url="https://dspmuranchi.ac.in/uploads/news/schedule.pdf",
        authority="official_document",
        verified_at="2026-10-26T00:00:00Z",
        snippet="13-10-2026 Tuesday All Subjects.",
        text="13-10-2026 Tuesday All Subjects.",
        status="ok",
    )
    bundle = EvidenceBundle(
        query="exam schedule oct 2026",
        intent=Intent.NOTICE,
        items=[item],
    )
    plan = Plan(intent=Intent.NOTICE, steps=())
    declines: list[dict] = []
    result = _notice_answer_result(
        "exam schedule oct 2026",
        plan,
        ExecutionResult(plan=plan),
        bundle,
        facts=FactStore(),
        declines=declines,
    )

    assert result is not None
    assert _assert_grounded(result)
    assert declines == []


def test_builder_decline_reason_reaches_the_turn_event(tmp_path):
    facts_path = tmp_path / "empty_facts.json"
    facts_path.write_text(json.dumps({}), encoding="utf-8")
    store, db = build_store(tmp_path)
    seed_page(store)
    assert plan_text("hostel application").intent is Intent.PAGE_TOPIC

    result = build_text_answer(
        "hostel application",
        graph_path=str(db),
        pii_guard=False,
        llm=lambda messages: "The hostel page mentions a warden office.",
        facts=FactStore(facts_path),
        conversation_id="conv-builder-decline",
    )

    assert result is not None
    decline = next(
        item for item in result.builder_declines
        if item["builder"] == "admission"
    )
    assert decline["reason"] == "missing_fact"
    assert decline["detail"] == "admissions.portal"

    event = store.turn_events("conv-builder-decline")[-1]
    assert event["diagnostics"]["builder_declines"] == result.builder_declines
