"""Phase 2: FTS search, ranking and the read-only tool server.

No test touches the network. The graph is compiled from the same fixtures as
Phase 1 into a temporary SQLite store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sitegraph.compiler import compile_graph
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests/fixtures/nga"
BASE = "https://dspmuranchi.ac.in"


def _compile(pii_guard: bool = True):
    return compile_graph(
        (FIXTURES / "sitemap.xml").read_text(encoding="utf-8"),
        (FIXTURES / "home.html").read_text(encoding="utf-8"),
        base_url=BASE,
        sitemap_url=BASE + "/sitemap.xml",
        pii_guard=pii_guard,
        generated_at="2026-10-24T00:00:00Z",
    )


def _server(
    tmp_path, notices_path=None, *, pii_guard: bool = True
) -> SiteToolServer:
    result = _compile(pii_guard=pii_guard)
    db = tmp_path / "graph.sqlite3"
    store = GraphStore(db)
    store.replace_graph(
        result.nodes,
        result.edges,
        coverage=result.coverage,
        graph_hash=result.graph_hash,
        generated_at=result.generated_at,
        source_mode="fixture",
    )
    return SiteToolServer(
        db, notices_path=notices_path, pii_guard=pii_guard
    )


def _search(server: SiteToolServer, query: str, **args) -> dict:
    result = server.call("site.search", {"query": query, **args})
    assert result["status"] == "ok", result
    return result["data"]


def test_search_ranks_the_hostel_page_first(tmp_path):
    data = _search(_server(tmp_path), "hostel")

    assert data["normalized_query"] == "hostel"
    assert data["language"] == "en"
    assert data["results"][0]["node_id"] == "cms.hostel"
    assert data["results"][0]["content_type"] == "html"
    assert [part["title"] for part in data["results"][0]["breadcrumb"]] == [
        "Home", "Student Life", "Hostel",
    ]
    assert data["results"][0]["snippet"]


def test_search_matches_exact_titles(tmp_path):
    server = _server(tmp_path)

    about = _search(server, "about the university")
    vc = _search(server, "vice chancellor")

    assert about["results"][0]["node_id"] == "cms.about-the-university"
    assert vc["results"][0]["node_id"] == "page.vice-chancellor"


def test_hindi_and_gujarati_topic_aliases(tmp_path):
    server = _server(tmp_path)

    hindi = _search(server, "छात्रावास क्या है")
    gujarati = _search(server, "છાત્રાલય")
    fee = _search(server, "फीस")

    assert hindi["language"] == "hi"
    assert hindi["results"][0]["node_id"] == "cms.hostel"
    assert gujarati["language"] == "gu"
    assert gujarati["results"][0]["node_id"] == "cms.hostel"
    assert fee["results"][0]["node_id"] == "cms.fee-structure"


def test_blocked_nodes_are_never_returned(tmp_path):
    server = _server(tmp_path)

    results = _search(server, "results")
    history = _search(server, "history")

    assert results["total"] == 0
    assert all(item["node_id"] != "cms.results" for item in history["results"])
    assert all(item["node_id"] != "departments.history" for item in history["results"])


def test_pii_guard_off_returns_published_results_pages(tmp_path):
    server = _server(tmp_path, pii_guard=False)

    results = _search(server, "results")
    opened = server.call("site.open", {"node_id": "cms.results"})
    departments = _search(server, "departments history")

    assert any(item["node_id"] == "cms.results" for item in results["results"])
    assert opened["status"] == "ok"
    assert opened["data"]["node"]["pii_class"] == "public"
    assert any(
        item["node_id"] == "departments.history"
        for item in departments["results"]
    )


def test_section_and_content_type_filters(tmp_path):
    server = _server(tmp_path)

    section = _search(server, "hostel", section="Student Life")
    pdf = _search(server, "fee", content_type="pdf")
    wrong_section = _search(server, "hostel", section="Administration")

    assert section["results"][0]["node_id"] == "cms.hostel"
    assert pdf["results"][0]["node_id"] == "uploads.news.fee-pdf"
    assert pdf["results"][0]["content_type"] == "pdf"
    assert wrong_section["total"] == 0


def test_limit_empty_and_no_match(tmp_path):
    server = _server(tmp_path)

    limited = _search(server, "university", limit=1)
    empty = _search(server, "")
    missing = _search(server, "quantum flux capacitor")

    assert len(limited["results"]) == 1
    assert empty["total"] == 0
    assert missing["total"] == 0


def test_navigate_by_id_path_and_breadcrumb(tmp_path):
    server = _server(tmp_path)

    by_id = server.call("site.navigate", {"node_id": "cms.hostel"})
    by_path = server.call("site.navigate", {"path": "/cms/hostel"})
    by_crumb = server.call(
        "site.navigate", {"breadcrumb": "Home > Student Life > Hostel"}
    )

    for result in (by_id, by_path, by_crumb):
        assert result["status"] == "ok", result
        data = result["data"]
        assert data["node"]["node_id"] == "cms.hostel"
        assert data["parent"]["node_id"] == "section.student-life"
        assert [part["title"] for part in data["breadcrumb"]] == [
            "Home", "Student Life", "Hostel",
        ]

    parents = server.call("site.navigate", {"node_id": "section.student-life"})
    assert [child["node_id"] for child in parents["data"]["children"]] == ["cms.hostel"]


def test_navigate_rejects_blocked_and_missing(tmp_path):
    server = _server(tmp_path)

    blocked = server.call("site.navigate", {"node_id": "cms.results"})
    missing = server.call("site.navigate", {"node_id": "nope"})

    assert blocked["status"] == "BLOCKED"
    assert missing["status"] == "NOT_FOUND"


def test_open_is_metadata_only_in_phase_two(tmp_path):
    server = _server(tmp_path)

    opened = server.call("site.open", {"node_id": "cms.hostel"})

    assert opened["status"] == "ok"
    assert opened["data"]["node"]["title"] == "Hostel"
    assert opened["data"]["body_available"] is False
    assert opened["data"]["content_status"] == "not_indexed"
    assert opened["data"]["sections"] == []

    blocked = server.call("site.open", {"node_id": "cms.results"})
    missing = server.call("site.open", {"node_id": "nope"})
    assert blocked["status"] == "BLOCKED"
    assert missing["status"] == "NOT_FOUND"


def test_documents_freshness_and_follow(tmp_path):
    server = _server(tmp_path)

    documents = server.call("site.documents", {"node_id": "home"})
    freshness = server.call("site.freshness", {"node_id": "cms.hostel"})
    external = server.call(
        "site.follow", {"edge_id": "links_to:home:external.example-org.portal"}
    )
    internal = server.call(
        "site.follow", {"edge_id": "parent_of:section.student-life:cms.hostel"}
    )
    missing = server.call("site.follow", {"edge_id": "nope"})

    assert documents["status"] == "ok"
    assert [doc["node_id"] for doc in documents["data"]["documents"]] == [
        "uploads.news.fee-pdf"
    ]
    assert freshness["status"] == "ok"
    assert freshness["data"]["lastmod"] == "2026-10-24"
    assert freshness["data"]["content_status"] == "not_indexed"
    assert external["status"] == "ok"
    assert external["data"]["external"] is True
    assert external["data"]["external_url"] == "https://example.org/portal"
    assert internal["status"] == "ok"
    assert internal["data"]["node"]["node_id"] == "cms.hostel"
    assert missing["status"] == "NOT_FOUND"


def test_latest_notices_filters_pii_and_honours_filters(tmp_path):
    notices = tmp_path / "notices.jsonl"
    rows = [
        {
            "id": "n1", "date": "2026-10-14", "date_text": "14-Oct-2026",
            "title": "Exam notice", "category": "Examination",
            "url": "https://example.org/n1", "pii": False, "source": "test",
        },
        {
            "id": "n2", "date": "2026-10-01", "date_text": "01-Oct-2026",
            "title": "Fee notice", "category": "Miscellaneous",
            "url": "https://example.org/n2", "pii": False, "source": "test",
        },
        {
            "id": "n3", "date": "2026-09-01", "date_text": "01-Sep-2026",
            "title": "Merit List of Selected Candidates", "category": "Admission",
            "url": "https://example.org/n3", "pii": False, "source": "test",
        },
        {
            "id": "n4", "date": "2026-10-10", "date_text": "10-Oct-2026",
            "title": "PII row", "category": "Admission",
            "url": "https://example.org/n4", "pii": True, "source": "test",
        },
    ]
    notices.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    server = _server(tmp_path, notices_path=notices)

    latest = server.call("site.latest", {"limit": 10})
    since = server.call("site.latest", {"since": "2026-10-01", "limit": 10})
    category = server.call(
        "site.latest", {"categories": ["Examination"], "limit": 10}
    )

    assert latest["status"] == "ok"
    assert [item["id"] for item in latest["data"]["items"]] == ["n1", "n2"]
    assert [item["id"] for item in since["data"]["items"]] == ["n1", "n2"]
    assert [item["id"] for item in category["data"]["items"]] == ["n1"]


def test_latest_with_guard_off_includes_published_rows(tmp_path):
    notices = tmp_path / "notices.jsonl"
    rows = [
        {
            "id": "n1", "date": "2026-10-14", "date_text": "14-Oct-2026",
            "title": "Exam notice", "category": "Examination",
            "url": "https://example.org/n1", "pii": False, "source": "test",
        },
        {
            "id": "n4", "date": "2026-10-10", "date_text": "10-Oct-2026",
            "title": "Published result row", "category": "Admission",
            "url": "https://example.org/n4", "pii": True, "source": "test",
        },
    ]
    notices.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    server = _server(tmp_path, notices_path=notices, pii_guard=False)

    latest = server.call("site.latest", {"limit": 10})

    assert latest["status"] == "ok"
    assert [item["id"] for item in latest["data"]["items"]] == ["n1", "n4"]


def test_latest_is_not_available_without_a_catalogue(tmp_path):
    server = _server(tmp_path, notices_path=None)

    result = server.call("site.latest", {})

    assert result["status"] == "NOT_AVAILABLE"


def test_call_validates_tools_and_arguments(tmp_path):
    server = _server(tmp_path)

    unknown = server.call("site.nope", {})
    missing_query = server.call("site.search", {})
    bad_type = server.call("site.search", {"query": "hostel", "content_type": "x"})
    clamped = server.call("site.search", {"query": "hostel", "limit": 999})

    assert unknown["status"] == "INVALID_TOOL"
    assert missing_query["status"] == "INVALID_ARGUMENT"
    assert bad_type["status"] == "INVALID_ARGUMENT"
    assert clamped["status"] == "ok"
    assert len(clamped["data"]["results"]) <= 10
