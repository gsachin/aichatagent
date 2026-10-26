"""Phase 3: content refresh, body index, freshness and canonical dedupe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.sitegraph.compiler import compile_graph
from app.sitegraph.fetch import FetchError, FetchResult
from app.sitegraph.refresh import RefreshOptions, refresh_graph
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests/fixtures/nga"
BASE = "https://dspmuranchi.ac.in"
HOSTEL = BASE + "/cms/hostel"
STUDENT_HOSTEL = BASE + "/student-life/hostel"
HOSTEL_NODE = "cms.hostel"
STUDENT_HOSTEL_NODE = "student-life.hostel"


def _sitemap(*urls: str) -> str:
    body = "".join(
        f"<url><loc>{url}</loc><lastmod>2026-10-24</lastmod></url>" for url in urls
    )
    return (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def _build(tmp_path, *, urls=(HOSTEL,), pii_guard=False):
    result = compile_graph(
        _sitemap(*urls) if urls else (FIXTURES / "sitemap.xml").read_text(
            encoding="utf-8"
        ),
        (FIXTURES / "home.html").read_text(encoding="utf-8"),
        base_url=BASE,
        sitemap_url=BASE + "/sitemap.xml",
        pii_guard=pii_guard,
        generated_at="2026-10-24T00:00:00Z",
    )
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
    return store, db


class FakeFetcher:
    def __init__(self, pages: dict[str, dict | Exception]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, url: str, *, etag: str = "", last_modified: str = ""):
        self.calls.append((url, etag, last_modified))
        value = self.pages.get(url)
        if value is None:
            raise FetchError(f"no fake page for {url}")
        if isinstance(value, Exception):
            raise value
        if value.get("not_modified"):
            return FetchResult(
                url=url,
                final_url=url,
                status=304,
                body=b"",
                content_type="text/html; charset=utf-8",
                etag=etag,
                last_modified=last_modified,
                not_modified=True,
            )
        body = str(value.get("body") or "").encode("utf-8")
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            body=body,
            content_type="text/html; charset=utf-8",
            etag=str(value.get("etag") or ""),
            last_modified=str(value.get("last_modified") or ""),
        )


def _page(body: str = "Boys and girls hostels with a warden office.") -> dict:
    return {
        "body": (
            "<html><head><title>Hostel</title></head><body>"
            "<nav>Home</nav>"
            f"<main><h1>Hostel</h1><p>{body}</p>"
            '<a href="/uploads/hostel-rules.pdf">Hostel rules</a></main>'
            "<footer>Footer</footer></body></html>"
        ),
        "etag": '"v1"',
        "last_modified": "Wed, 21 Oct 2026 07:28:00 GMT",
    }


def _options(**overrides) -> RefreshOptions:
    values = dict(
        ttl_s=3600,
        limit=5,
        force=False,
        min_chars=20,
        node_ids=(HOSTEL_NODE,),
        now="2026-10-24T00:00:00Z",
    )
    values.update(overrides)
    return RefreshOptions(**values)


def test_refresh_stores_body_indexes_it_and_sets_freshness(tmp_path):
    store, db = _build(tmp_path)
    stats = refresh_graph(store, FakeFetcher({HOSTEL: _page()}), _options())

    assert stats.checked == 1
    assert stats.fetched == 1
    assert stats.changed == 1
    assert stats.errors == 0
    assert stats.documents == 1

    version = store.page_version("cms.hostel")
    assert version is not None
    assert "warden office" in version.text
    assert version.content_hash
    node = store.get_node("cms.hostel")
    assert node.content_hash == version.content_hash
    assert node.verified_at == "2026-10-24T00:00:00Z"
    state = store.fetch_state("cms.hostel")
    assert state["next_due_at"] == "2026-10-24T01:00:00Z"
    assert state["etag"] == '"v1"'

    server = SiteToolServer(db, pii_guard=False)
    search = server.call("site.search", {"query": "warden"})
    assert search["status"] == "ok"
    assert search["data"]["results"][0]["node_id"] == "cms.hostel"
    assert search["data"]["results"][0]["body_match"] is True
    assert "warden" in search["data"]["results"][0]["snippet"]

    opened = server.call("site.open", {"node_id": "cms.hostel"})
    assert opened["status"] == "ok"
    assert opened["data"]["body_available"] is True
    assert opened["data"]["content_status"] == "indexed"
    assert "warden" in opened["data"]["sections"][0]["text"]
    assert opened["data"]["documents"][0]["url"].endswith("hostel-rules.pdf")

    freshness = server.call("site.freshness", {"node_id": "cms.hostel"})
    assert freshness["data"]["content_status"] == "indexed"
    assert freshness["data"]["next_due_at"] == "2026-10-24T01:00:00Z"


def test_same_content_does_not_create_a_new_version(tmp_path):
    store, _db = _build(tmp_path)
    refresh_graph(store, FakeFetcher({HOSTEL: _page()}), _options())
    first = store.page_version("cms.hostel")

    stats = refresh_graph(
        store,
        FakeFetcher({HOSTEL: _page()}),
        _options(force=True),
    )
    second = store.page_version("cms.hostel")

    assert stats.unchanged == 1
    assert stats.changed == 0
    assert second.version == first.version
    assert store.fetch_state("cms.hostel")["http_status"] == 200


def test_304_is_revalidated_without_touching_content(tmp_path):
    store, _db = _build(tmp_path)
    refresh_graph(store, FakeFetcher({HOSTEL: _page()}), _options())
    first = store.page_version("cms.hostel")

    stats = refresh_graph(
        store,
        FakeFetcher({HOSTEL: {"not_modified": True}}),
        _options(force=True),
    )

    assert stats.not_modified == 1
    assert store.page_version("cms.hostel").version == first.version
    assert store.get_node("cms.hostel").verified_at == "2026-10-24T00:00:00Z"


def test_empty_shell_is_recorded_and_body_is_hidden(tmp_path):
    store, db = _build(tmp_path)
    page = _page("tiny")
    stats = refresh_graph(store, FakeFetcher({HOSTEL: page}), _options(min_chars=120))

    assert stats.empty == 1
    assert store.get_node("cms.hostel").status.value == "empty_shell"
    server = SiteToolServer(db, pii_guard=False)
    opened = server.call("site.open", {"node_id": "cms.hostel"})
    assert opened["status"] == "BLOCKED"


def test_soft_404_is_recorded(tmp_path):
    store, _db = _build(tmp_path)
    error = FetchError("soft_404: page not found", status=200)

    stats = refresh_graph(store, FakeFetcher({HOSTEL: error}), _options())

    assert stats.soft_404 == 1
    assert store.get_node("cms.hostel").status.value == "soft_404"


def test_fetch_failure_is_recorded_without_losing_content(tmp_path):
    store, _db = _build(tmp_path)
    refresh_graph(store, FakeFetcher({HOSTEL: _page()}), _options())
    first = store.page_version("cms.hostel")

    stats = refresh_graph(
        store,
        FakeFetcher({HOSTEL: FetchError("connection reset")}),
        _options(force=True),
    )

    assert stats.errors == 1
    assert store.page_version("cms.hostel").version == first.version
    state = store.fetch_state("cms.hostel")
    assert state["failure_count"] == 1
    assert "connection reset" in state["error"]


def test_identical_pages_are_canonicalised_and_search_hides_duplicates(tmp_path):
    store, db = _build(tmp_path, urls=(HOSTEL, STUDENT_HOSTEL))
    page = _page()
    stats = refresh_graph(
        store,
        FakeFetcher({HOSTEL: page, STUDENT_HOSTEL: page}),
        _options(node_ids=(HOSTEL_NODE, STUDENT_HOSTEL_NODE)),
    )

    assert stats.canonical_duplicates == 1
    canonical = store.get_node("cms.hostel")
    duplicate = store.get_node("student-life.hostel")
    assert canonical.canonical_id == "cms.hostel"
    assert duplicate.canonical_id == "cms.hostel"

    server = SiteToolServer(db, pii_guard=False)
    search = server.call("site.search", {"query": "hostel"})
    ids = [item["node_id"] for item in search["data"]["results"]]
    assert "cms.hostel" in ids
    assert "student-life.hostel" not in ids


def test_refresh_cli_dry_run_lists_due_nodes(tmp_path):
    store, db = _build(tmp_path)
    store.record_fetch_failure(
        "cms.hostel",
        checked_at="2026-10-01T00:00:00Z",
        next_due_at="2026-10-01T00:05:00Z",
        error="test",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_refresh.py"),
            "--db",
            str(db),
            "--dry-run",
            "--limit",
            "5",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["total"] >= 1
    assert any(item["node_id"] == "cms.hostel" for item in data["due"])
