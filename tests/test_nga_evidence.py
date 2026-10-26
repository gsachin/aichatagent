"""Phase 4: evidence and citation assembly."""

from __future__ import annotations

from app.agent.evidence import (
    assemble_evidence,
    render_citations,
    render_evidence_block,
)
from app.agent.executor import ExecutionResult, execute_plan
from app.agent.facts import FactStore
from app.agent.models import Intent, Plan, PlanStep
from tests.nga_helpers import build_server, write_notices


def _search_plan(query: str = "hostel") -> Plan:
    return Plan(
        Intent.PAGE_TOPIC,
        (PlanStep("site.search", {"query": query, "limit": 3}),),
    )


def test_verified_fee_fact_is_first_and_cited():
    execution = ExecutionResult(plan=Plan(Intent.FEE, ()))
    bundle = assemble_evidence(
        execution,
        query="What is the BA History fee?",
        facts=FactStore(),
    )

    assert bundle.verified_facts
    fact = bundle.verified_facts[0]
    assert fact.authority == "verified_fact"
    assert fact.fact_id
    assert "3,410" in fact.text
    assert fact.status == "draft"
    assert fact.url.endswith(".pdf")
    assert bundle.items[0].authority == "verified_fact"
    assert bundle.citations[0].fact_id == fact.fact_id
    block = render_evidence_block(bundle)
    assert "## Verified facts" in block
    assert "3,410" in block


def test_page_body_evidence_and_citations(tmp_path):
    server, _store, _db = build_server(tmp_path)
    execution = execute_plan(server, _search_plan())
    bundle = assemble_evidence(
        execution,
        query="hostel",
        facts=FactStore(),
    )

    hostel = next(item for item in bundle.items if item.node_id == "cms.hostel")
    assert "warden" in hostel.text
    assert hostel.content_hash
    assert hostel.verified_at == "2026-10-24T00:00:00Z"
    citation = next(c for c in bundle.citations if c.node_id == "cms.hostel")
    assert citation.url.endswith("/cms/hostel")
    assert "warden" in citation.snippet
    block = render_evidence_block(bundle)
    assert "<site_evidence>" in block
    assert "Source: https://dspmuranchi.ac.in/cms/hostel" in block
    rendered = render_citations(bundle)
    assert "Sources" in rendered
    assert "https://dspmuranchi.ac.in/cms/hostel" in rendered


def test_search_and_open_of_the_same_node_are_deduplicated(tmp_path):
    server, _store, _db = build_server(tmp_path)
    execution = execute_plan(server, _search_plan())
    bundle = assemble_evidence(execution, query="hostel")

    assert sum(
        1 for item in bundle.items if item.node_id == "cms.hostel"
    ) == 1
    item = next(item for item in bundle.items if item.node_id == "cms.hostel")
    assert "warden" in item.text


def test_latest_notice_evidence(tmp_path):
    notices = write_notices(
        tmp_path / "notices.jsonl",
        [
            {
                "id": "n1", "date": "2026-10-14", "date_text": "14-Oct-2026",
                "title": "Exam notice", "category": "Examination",
                "url": "https://example.org/n1", "pii": False, "source": "test",
            }
        ],
    )
    server, _store, _db = build_server(
        tmp_path, notices_path=notices, seed=False
    )
    execution = execute_plan(
        server, Plan(Intent.NOTICE, (PlanStep("site.latest", {"limit": 5}),))
    )
    bundle = assemble_evidence(execution, query="latest notice")

    assert bundle.items
    assert bundle.items[0].title == "Exam notice"
    assert "Examination" in bundle.items[0].snippet
    assert bundle.citations[0].url == "https://example.org/n1"


def test_body_not_indexed_is_recorded(tmp_path):
    server, _store, _db = build_server(tmp_path, seed=False)
    execution = execute_plan(
        server,
        Plan(Intent.PAGE_TOPIC, (PlanStep("site.open", {"node_id": "cms.hostel"}),)),
    )
    bundle = assemble_evidence(execution, query="hostel")

    assert bundle.items == []
    assert any(
        note == "body_not_indexed:cms.hostel" for note in bundle.notes
    )


def test_contact_fact_is_assembled():
    execution = ExecutionResult(plan=Plan(Intent.CONTACT, ()))
    bundle = assemble_evidence(
        execution, query="registrar contact", facts=FactStore()
    )

    assert bundle.verified_facts
    contact = bundle.verified_facts[0]
    assert contact.fact_id == "university.contacts"
    assert "registrar@dspmuranchi.ac.in" in contact.text


def test_pii_guard_filters_synthetic_search_results():
    execution = ExecutionResult(
        plan=Plan(Intent.PAGE_TOPIC, ()),
        results=[
            {
                "tool": "site.search",
                "status": "ok",
                "data": {
                    "results": [
                        {
                            "node_id": "cms.results",
                            "title": "Results",
                            "url": "https://dspmuranchi.ac.in/cms/results",
                            "pii_class": "student_list",
                            "status": "ok",
                            "snippet": "Result list",
                        }
                    ]
                },
            }
        ],
    )
    guarded = assemble_evidence(execution, query="results", pii_guard=True)
    unguarded = assemble_evidence(execution, query="results", pii_guard=False)

    assert guarded.items == []
    assert unguarded.items[0].node_id == "cms.results"


def test_empty_bundle_renders_nothing():
    bundle = assemble_evidence(
        ExecutionResult(plan=Plan(Intent.UNKNOWN, ())), query=""
    )

    assert render_evidence_block(bundle) == ""
    assert render_citations(bundle) == ""


def test_render_citations_limits_and_trims():
    execution = ExecutionResult(
        plan=Plan(Intent.PAGE_TOPIC, ()),
        results=[
            {
                "tool": "site.search",
                "status": "ok",
                "data": {
                    "results": [
                        {
                            "node_id": f"n{i}",
                            "title": f"Page {i}",
                            "url": f"https://dspmuranchi.ac.in/p{i}",
                            "status": "ok",
                            "snippet": "x" * 500,
                        }
                        for i in range(5)
                    ]
                },
            }
        ],
    )
    bundle = assemble_evidence(execution, query="page")
    rendered = render_citations(bundle, limit=2)

    assert rendered.count("- [") == 2
    assert "Page 0" in rendered
    assert "Page 2" not in rendered
    assert "x" * 300 not in rendered
