"""WP5: shells are navigable but not groundable; browse/enumerate answers."""

from __future__ import annotations

from dataclasses import replace

from app.agent.models import Intent
from app.agent.planner import Planner, classify_intent
from app.agent.text import build_text_answer
from app.sitegraph.models import NodeStatus
from app.sitegraph.policy import can_ground, can_navigate, is_answerable
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer
from tests.nga_helpers import compile_fixture, seed_page


def _server(tmp_path, *, documents=(), unblock_history=True):
    result = compile_fixture(pii_guard=True)
    nodes = []
    for node in result.nodes:
        if node.node_id == "cms.hostel":
            node = replace(node, status=NodeStatus.EMPTY_SHELL)
        elif node.node_id == "departments.history" and unblock_history:
            node = replace(
                node,
                status=NodeStatus.OK,
                section="Departments",
            )
        nodes.append(node)
    db = tmp_path / "graph.sqlite3"
    store = GraphStore(db)
    store.replace_graph(
        nodes,
        result.edges,
        coverage=result.coverage,
        graph_hash=result.graph_hash,
        generated_at=result.generated_at,
        source_mode="fixture",
    )
    if documents:
        seed_page(store, documents=documents)
    server = SiteToolServer(db, pii_guard=False)
    return server, store, db, result


def _must_not_call(_messages):
    raise AssertionError("a navigation answer must not call the LLM")


def test_policy_splits_grounding_from_navigation():
    result = compile_fixture(pii_guard=True)
    shell = replace(
        next(node for node in result.nodes if node.node_id == "cms.hostel"),
        status=NodeStatus.EMPTY_SHELL,
    )
    blocked = next(
        node for node in result.nodes if node.node_id == "cms.results"
    )

    assert can_ground(shell, False) is False
    assert can_navigate(shell, False) is True
    assert is_answerable(shell, False) is False
    assert can_ground(blocked, False) is False
    assert can_navigate(blocked, False) is False


def test_shell_is_searchable_and_navigable_but_not_openable_as_evidence(tmp_path):
    server, _store, _db, _result = _server(tmp_path)

    search = server.call("site.search", {"query": "hostel", "limit": 5})
    hostel = next(
        item for item in search["data"]["results"]
        if item["node_id"] == "cms.hostel"
    )
    assert hostel["groundable"] is False
    assert hostel["navigable"] is True
    assert hostel["content_status"] == "empty_shell"

    navigate = server.call("site.navigate", {"node_id": "cms.hostel"})
    assert navigate["status"] == "ok"
    assert navigate["data"]["node"]["groundable"] is False
    assert navigate["data"]["parent"]["node_id"] == "section.student-life"

    opened = server.call("site.open", {"node_id": "cms.hostel"})
    assert opened["status"] == "ok"
    assert opened["data"]["body_available"] is False
    assert opened["data"]["content_status"] == "empty_shell"


def test_browse_returns_a_shell_sections(tmp_path):
    server, _store, _db, _result = _server(tmp_path)

    browse = server.call("site.browse", {"query": "student life"})

    assert browse["status"] == "ok"
    assert browse["data"]["node"]["node_id"] == "section.student-life"
    child_ids = {
        item["node_id"] for item in browse["data"]["children"]
    }
    assert "cms.hostel" in child_ids


def test_browse_returns_department_documents(tmp_path):
    documents = (
        {
            "title": "Hostel Rules PDF",
            "url": "https://dspmuranchi.ac.in/hostel-rules.pdf",
        },
    )
    server, _store, _db, _result = _server(
        tmp_path, documents=documents
    )

    browse = server.call(
        "site.browse", {"query": "documents on the hostel page"}
    )

    assert browse["status"] == "ok"
    titles = [
        item.get("title") for item in browse["data"]["documents"]
    ]
    assert "Hostel Rules PDF" in titles


def test_department_collection_lists_department_pages(tmp_path):
    server, _store, _db, _result = _server(tmp_path)

    browse = server.call("site.browse", {"query": "list of departments"})

    assert browse["status"] == "ok"
    assert browse["data"]["collection"]["title"] == "Departments and Faculties"
    assert browse["data"]["collection"]["count"] >= 1
    assert any(
        item["node_id"] == "departments.history"
        for item in browse["data"]["children"]
    )


def test_planner_uses_browse_for_navigation_questions():
    assert classify_intent("what is under student life") is Intent.NAVIGATE
    assert classify_intent("list of departments") is Intent.NAVIGATE
    assert classify_intent("hostel facilities") is Intent.PAGE_TOPIC

    plan = Planner().plan("what is under student life")
    assert plan.intent is Intent.NAVIGATE
    assert plan.steps[0].tool == "site.browse"
    assert plan.steps[0].args["query"]


def test_student_life_browse_answer_is_deterministic(tmp_path):
    _server_obj, _store, db, _result = _server(tmp_path)

    result = build_text_answer(
        "what is under student life",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.NAVIGATE
    assert result.status == "verified"
    assert "Student Life" in result.answer
    assert "Hostel" in result.answer
    assert "registrar" in result.answer.lower()


def test_document_browse_answer_is_deterministic(tmp_path):
    documents = (
        {
            "title": "Hostel Rules PDF",
            "url": "https://dspmuranchi.ac.in/hostel-rules.pdf",
        },
    )
    _server_obj, _store, db, _result = _server(
        tmp_path, documents=documents
    )

    result = build_text_answer(
        "documents on the hostel page",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.NAVIGATE
    assert "Hostel Rules PDF" in result.answer


def test_department_collection_answer_is_deterministic(tmp_path):
    _server_obj, _store, db, _result = _server(tmp_path)

    result = build_text_answer(
        "list of departments",
        graph_path=str(db),
        pii_guard=False,
        llm=_must_not_call,
    )

    assert result is not None
    assert result.plan is not None
    assert result.plan.intent is Intent.NAVIGATE
    assert "Departments and Faculties" in result.answer
    assert "History" in result.answer
