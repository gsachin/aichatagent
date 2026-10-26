"""Phase 4: deterministic and structured planning."""

from __future__ import annotations

import json

import pytest

from app.agent.models import Intent, Plan, PlanStep, SessionState
from app.agent.planner import (
    Planner,
    PlanError,
    classify_intent,
    plan_with_llm,
    validate_plan,
)


@pytest.mark.parametrize(
    "query, expected",
    [
        ("What is the BA History fee?", Intent.FEE),
        ("latest notice", Intent.NOTICE),
        ("last date for admission", Intent.DATE),
        ("registrar email", Intent.CONTACT),
        ("BA History eligibility", Intent.PROGRAMME),
        ("hostel facilities", Intent.PAGE_TOPIC),
        ("MBA fee", Intent.BOUNDARY),
        ("", Intent.UNKNOWN),
    ],
)
def test_classify_intent(query, expected):
    assert classify_intent(query) is expected


def test_fee_plan_uses_search_and_programme_filter():
    plan = Planner().plan("What is the BA History fee?")

    assert plan.intent is Intent.FEE
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "site.search"
    assert "fee" in plan.steps[0].args["query"].lower()
    assert plan.filters["programme"]


def test_notice_and_date_plans_use_latest():
    planner = Planner()

    notice = planner.plan("latest notice")
    date = planner.plan("last date for admission")

    assert notice.intent is Intent.NOTICE
    assert notice.steps[0].tool == "site.latest"
    assert date.intent is Intent.DATE
    assert date.steps[0].tool == "site.latest"


def test_contact_plan_uses_search():
    plan = Planner().plan("registrar contact")
    assert plan.intent is Intent.CONTACT
    assert plan.steps[0].tool == "site.search"
    assert "contact" in plan.steps[0].args["query"]


def test_boundary_plan_has_no_tool_call():
    plan = Planner().plan("MBA fee")
    assert plan.intent is Intent.BOUNDARY
    assert plan.steps == ()


def test_followup_plan_opens_the_current_node_then_searches():
    state = SessionState(
        current_node_id="cms.hostel",
        current_node_title="Hostel",
        language="en",
    )

    plan = Planner().plan("what about its fee?", state)

    assert plan.intent is Intent.FEE
    assert [step.tool for step in plan.steps] == ["site.open", "site.search"]
    assert plan.steps[0].args["node_id"] == "cms.hostel"
    assert "Hostel" in plan.steps[1].args["query"]


def test_validate_plan_rejects_unsafe_shapes():
    with pytest.raises(PlanError):
        validate_plan(Plan(Intent.FEE, (PlanStep("nope", {}),)))
    with pytest.raises(PlanError):
        validate_plan(
            Plan(Intent.FEE, (PlanStep("site.search", {"query": "x", "url": "https://x"}),))
        )
    with pytest.raises(PlanError):
        validate_plan(
            Plan(Intent.FEE, (PlanStep("site.search", {"query": ""}),))
        )
    with pytest.raises(PlanError):
        validate_plan(
            Plan(
                Intent.FEE,
                tuple(PlanStep("site.search", {"query": str(i)}) for i in range(9)),
            )
        )


def test_llm_plan_is_validated_and_used():
    def fake_llm(prompt: str) -> str:
        assert "site.search" in prompt
        return json.dumps(
            {
                "intent": "page_topic",
                "steps": [
                    {"tool": "site.search", "args": {"query": "hostel", "limit": 2}}
                ],
                "answer_language": "en",
            }
        )

    plan = plan_with_llm("hostel", SessionState(), fake_llm)

    assert plan.intent is Intent.PAGE_TOPIC
    assert plan.steps[0].tool == "site.search"
    assert plan.steps[0].args["query"] == "hostel"


def test_invalid_llm_plan_falls_back_to_deterministic():
    def bad_llm(prompt: str) -> str:
        return "this is not json"

    plan = plan_with_llm("latest notice", SessionState(), bad_llm)

    assert plan.intent is Intent.NOTICE
    assert plan.steps[0].tool == "site.latest"
