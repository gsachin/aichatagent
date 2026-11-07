"""Phase 4: bounded plan execution."""

from __future__ import annotations

import pytest

from app.agent.executor import async_execute_plan, execute_plan
from app.agent.models import Intent, Plan, PlanStep
from app.agent.planner import PlanError
from tests.nga_helpers import build_server


def _search_plan(query: str = "hostel") -> Plan:
    return Plan(
        Intent.PAGE_TOPIC,
        (PlanStep("site.search", {"query": query, "limit": 3}),),
    )


def test_search_then_auto_open_returns_body(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = execute_plan(server, _search_plan())

    assert result.status == "ok"
    assert result.steps_used == 2
    assert result.synthetic_steps[0]["reason"] == "auto_open"
    assert result.results[0]["tool"] == "site.search"
    assert result.results[1]["tool"] == "site.open"
    assert result.results[1]["data"]["body_available"] is True


def test_boundary_plan_is_a_valid_empty_execution(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = execute_plan(server, Plan(Intent.BOUNDARY, ()))

    assert result.status == "ok"
    assert result.steps_used == 0
    assert result.results == []


def test_blocked_node_stops_execution(tmp_path):
    server, _store, _db = build_server(tmp_path, pii_guard=True)
    plan = Plan(Intent.PAGE_TOPIC, (PlanStep("site.open", {"node_id": "cms.results"}),))

    result = execute_plan(server, plan)

    assert result.status == "blocked"
    assert result.errors[0]["status"] == "BLOCKED"


def test_deadline_stops_before_the_first_call(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = execute_plan(server, _search_plan(), deadline_ms=0)

    assert result.deadline_exceeded is True
    assert result.status == "deadline"
    assert result.results == []


def test_byte_budget_stops_execution(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = execute_plan(server, _search_plan(), max_bytes=1)

    assert result.status == "budget"
    assert len(result.results) == 1


def test_step_limit_is_enforced_before_execution(tmp_path):
    server, _store, _db = build_server(tmp_path)
    plan = Plan(
        Intent.PAGE_TOPIC,
        (
            PlanStep("site.search", {"query": "hostel"}),
            PlanStep("site.search", {"query": "library"}),
        ),
    )

    with pytest.raises(PlanError):
        execute_plan(server, plan, max_steps=1)


def test_error_status_is_retained(tmp_path):
    server, _store, _db = build_server(tmp_path)
    plan = Plan(
        Intent.PAGE_TOPIC,
        (PlanStep("site.open", {"node_id": "missing-node"}),),
    )

    result = execute_plan(server, plan)

    assert result.status == "degraded"
    assert result.errors[0]["status"] == "NOT_FOUND"


@pytest.mark.anyio
async def test_async_execution_uses_the_same_contract(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = await async_execute_plan(server, _search_plan())

    assert result.status == "ok"
    assert result.steps_used == 2
    assert result.results[1]["tool"] == "site.open"
    assert result.results[1]["data"]["body_available"] is True


@pytest.mark.anyio
async def test_async_deadline_is_reported(tmp_path):
    server, _store, _db = build_server(tmp_path)
    result = await async_execute_plan(server, _search_plan(), deadline_ms=0)

    assert result.deadline_exceeded is True
    assert result.status == "deadline"
