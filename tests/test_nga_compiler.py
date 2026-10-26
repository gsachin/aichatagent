"""Phase 1: DSPMU navigation graph compiler and SQLite store.

No test touches the network. The CLI test reads the fixture cache through the
same SourceBook path the production command uses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.sitegraph.compiler import compile_graph
from app.sitegraph.models import (
    Authority,
    ContentType,
    NodeStatus,
    PiiClass,
)
from app.sitegraph.store import GraphStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests/fixtures/nga"
BASE = "https://dspmuranchi.ac.in"
HOME_URL = BASE + "/"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _compile(**overrides):
    sitemap_xml = overrides.pop("sitemap_xml", _read("sitemap.xml"))
    home_html = overrides.pop("home_html", _read("home.html"))
    values = dict(
        base_url=BASE,
        sitemap_url=BASE + "/sitemap.xml",
        allowed_hosts=("dspmuranchi.ac.in", "www.dspmuranchi.ac.in"),
        min_coverage=0.95,
        source_mode="fixture",
        generated_at="2026-10-24T00:00:00Z",
    )
    values.update(overrides)
    result = compile_graph(sitemap_xml, home_html, **values)
    return result, {node.node_id: node for node in result.nodes}


# ── configuration safety ────────────────────────────────────────────

def test_nga_is_off_and_has_no_answer_path_integration():
    from app.config import settings
    from app.rag import context_bundle_for_query

    assert settings.NGA_ENABLED is False
    assert settings.NGA_MODE == "off"
    assert settings.NGA_COVERAGE_MIN == 0.95

    # The Phase 1 graph is not wired into the answer seam. This call must
    # still be the static bundle the current channels use.
    bundle = context_bundle_for_query("What is the hostel fee?")
    assert "Live university website" not in bundle.text


# ── hierarchy ───────────────────────────────────────────────────────

def test_compiles_nav_sections_and_pages():
    _result, nodes = _compile()

    assert nodes["home"].parent_id is None
    assert nodes["home"].depth == 0

    about = nodes["section.about-dspmu"]
    assert about.content_type is ContentType.SECTION
    assert about.parent_id == "home"
    assert about.depth == 1

    page = nodes["cms.about-the-university"]
    assert page.parent_id == about.node_id
    assert page.depth == 2
    assert page.title == "About the University"
    assert page.nav_label == "About the University"
    assert page.section == "About DSPMU"

    hostel = nodes["cms.hostel"]
    assert hostel.parent_id == "section.student-life"
    assert hostel.depth == 2
    assert hostel.section == "Student Life"

    vc = nodes["page.vice-chancellor"]
    assert vc.parent_id == "section.administration"
    assert vc.title == "Vice Chancellor"


def test_sitemap_path_under_a_nav_section_attaches_to_that_section():
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://dspmuranchi.ac.in/student-life/hostel</loc>"
        "<lastmod>2026-10-24</lastmod></url></urlset>"
    )
    _result, nodes = _compile(sitemap_xml=sitemap)

    hostel = nodes["student-life.hostel"]
    assert hostel.parent_id == "section.student-life"
    assert hostel.depth == 2
    assert hostel.section == "Student Life"


def test_sitemap_only_nodes_fall_back_to_home():
    _result, nodes = _compile()

    fee = nodes["cms.fee-structure"]
    assert fee.parent_id == "home"
    assert fee.depth == 1
    assert fee.section == "CMS Pages"
    assert fee.content_type is ContentType.HTML

    syllabus = nodes["syllabus"]
    assert syllabus.parent_id == "home"
    assert syllabus.section == "Syllabus"


def test_external_and_pdf_nodes_are_typed():
    _result, nodes = _compile()

    external = nodes["external.example-org.portal"]
    assert external.content_type is ContentType.EXTERNAL
    assert external.authority is Authority.EXTERNAL
    assert external.status is NodeStatus.EXTERNAL
    assert external.parent_id == "home"

    pdf = nodes["uploads.news.fee-pdf"]
    assert pdf.content_type is ContentType.PDF
    assert pdf.authority is Authority.OFFICIAL_DOCUMENT
    assert pdf.parent_id == "home"


def test_pii_guard_can_be_disabled_for_published_pages():
    result, nodes = _compile(pii_guard=False)

    assert nodes["cms.results"].status is NodeStatus.OK
    assert nodes["cms.results"].pii_class is PiiClass.PUBLIC
    assert nodes["departments.history"].status is NodeStatus.OK
    assert nodes["departments.history"].pii_class is PiiClass.PUBLIC
    assert result.coverage["blocked"] == 0
    assert result.coverage["pii_guard"] is False


def test_blocked_pii_and_staff_nodes_are_typed_but_not_answerable():
    _result, nodes = _compile()

    results = nodes["cms.results"]
    assert results.status is NodeStatus.BLOCKED
    assert results.pii_class is PiiClass.STUDENT_LIST
    assert results.content_type is ContentType.HTML

    department = nodes["departments.history"]
    assert department.status is NodeStatus.BLOCKED
    assert department.pii_class is PiiClass.STAFF_DIRECTORY


def test_every_non_root_node_has_a_valid_parent_and_depth():
    result, nodes = _compile()

    assert result.nodes
    for node in nodes.values():
        if node.node_id == "home":
            continue
        assert node.parent_id in nodes, node.node_id
        assert node.depth == nodes[node.parent_id].depth + 1, node.node_id

        seen = set()
        cursor = node
        while cursor.parent_id is not None:
            assert cursor.node_id not in seen
            seen.add(cursor.node_id)
            cursor = nodes[cursor.parent_id]
        assert cursor.node_id == "home"


# ── coverage ────────────────────────────────────────────────────────

def test_coverage_report_counts_and_gate():
    result, nodes = _compile()
    coverage = result.coverage

    assert coverage["sitemap_urls"] == 12
    assert coverage["represented_sitemap_urls"] == 12
    assert coverage["coverage_pct"] == 1.0
    assert coverage["meets_gate"] is True
    assert coverage["nav_nodes"] == 6
    assert coverage["nav_links"] == 6
    assert coverage["sections"] == 3
    assert coverage["pages"] == 10
    assert coverage["documents"] == 1
    assert coverage["external"] == 1
    assert coverage["blocked"] == 2
    assert coverage["departments"] == 1
    assert coverage["top_level_sections"] == [
        "About DSPMU", "Administration", "Student Life",
    ]
    assert coverage["orphans"] == []
    assert any("invalid_sitemap_urls:1" == warning for warning in coverage["warnings"])
    assert coverage["graph_hash"] == result.graph_hash
    assert coverage["unique_nodes"] == len(nodes) == 15


def test_invalid_sitemap_url_is_reported_not_silently_dropped():
    result, _nodes = _compile()
    missing = result.coverage["missing_sitemap_urls"]

    assert any(
        item["url"] == "mailto:test@example.com" and item["reason"] == "invalid_url"
        for item in missing
    )


def test_empty_sitemap_fails_the_coverage_gate():
    empty = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>mailto:test@example.com</loc></url></urlset>"
    )
    result = compile_graph(
        empty,
        _read("home.html"),
        base_url=BASE,
        sitemap_url=BASE + "/sitemap.xml",
        generated_at="2026-10-24T00:00:00Z",
    )

    assert result.coverage["sitemap_urls"] == 0
    assert result.coverage["coverage_pct"] == 0.0
    assert result.coverage["meets_gate"] is False
    assert "sitemap_empty" in result.coverage["warnings"]


def test_duplicate_urls_are_deduplicated():
    _result, nodes = _compile()
    hostel_urls = [node for node in nodes.values() if node.url == BASE + "/cms/hostel"]

    assert len(hostel_urls) == 1
    assert hostel_urls[0].node_id == "cms.hostel"


def test_graph_hash_is_stable_and_content_sensitive():
    first, _ = _compile(generated_at="2026-01-01T00:00:00Z")
    second, _ = _compile(generated_at="2026-02-02T00:00:00Z")
    changed, _ = _compile(
        home_html=_read("home.html").replace(">Hostel<", ">Hostels<")
    )

    assert first.graph_hash == second.graph_hash
    assert changed.graph_hash != first.graph_hash


# ── store ───────────────────────────────────────────────────────────

def test_sqlite_store_round_trip(tmp_path):
    result, nodes = _compile()
    store = GraphStore(tmp_path / "graph.sqlite3")

    store.replace_graph(
        result.nodes,
        result.edges,
        coverage=result.coverage,
        graph_hash=result.graph_hash,
        generated_at=result.generated_at,
        source_mode="fixture",
    )

    loaded = {node.node_id: node for node in store.load_nodes()}
    edges = store.load_edges()
    run = store.latest_run()

    assert store.node_count() == len(nodes) == 15
    assert store.edge_count() == len(result.edges)
    assert loaded["cms.hostel"].parent_id == "section.student-life"
    assert loaded["cms.hostel"].content_type is ContentType.HTML
    assert loaded["cms.results"].status is NodeStatus.BLOCKED
    assert loaded["uploads.news.fee-pdf"].authority is Authority.OFFICIAL_DOCUMENT
    assert any(
        edge.kind == "parent_of"
        and edge.src_id == "section.student-life"
        and edge.dst_id == "cms.hostel"
        for edge in edges
    )
    assert any(
        edge.kind == "documents"
        and edge.dst_id == "uploads.news.fee-pdf"
        for edge in edges
    )
    assert run is not None
    assert run["graph_hash"] == result.graph_hash
    assert run["coverage"]["coverage_pct"] == 1.0


def test_store_replaces_previous_graph_atomically(tmp_path):
    result, _nodes = _compile()
    store = GraphStore(tmp_path / "graph.sqlite3")
    kwargs = dict(
        coverage=result.coverage,
        graph_hash=result.graph_hash,
        generated_at=result.generated_at,
        source_mode="fixture",
    )
    store.replace_graph(result.nodes, result.edges, **kwargs)
    store.replace_graph(result.nodes, result.edges, **kwargs)

    assert store.node_count() == 15
    assert store.edge_count() == len(result.edges)


# ── CLI ─────────────────────────────────────────────────────────────

def test_cli_compiles_from_cache_without_network(tmp_path):
    cache = tmp_path / "cache"
    current = cache / "current"
    current.mkdir(parents=True)
    (current / "home.html").write_text(_read("home.html"), encoding="utf-8")
    (current / "sitemap.xml").write_text(_read("sitemap.xml"), encoding="utf-8")
    db = tmp_path / "graph.sqlite3"
    coverage = tmp_path / "coverage.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_compile.py"),
            "--cache-dir",
            str(cache),
            "--db",
            str(db),
            "--coverage",
            str(coverage),
            "--min-coverage",
            "0.9",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert db.is_file()
    assert coverage.is_file()
    data = json.loads(coverage.read_text(encoding="utf-8"))
    assert data["coverage_pct"] == 1.0
    assert data["meets_gate"] is True
    assert "NGA graph compile" in completed.stdout
