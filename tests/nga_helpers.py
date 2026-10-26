"""Shared fixture builders for the NGA Phase 1–4 tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.sitegraph.compiler import compile_graph
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests/fixtures/nga"
BASE = "https://dspmuranchi.ac.in"
HOSTEL = "cms.hostel"


def compile_fixture(*, pii_guard: bool = True):
    return compile_graph(
        (FIXTURES / "sitemap.xml").read_text(encoding="utf-8"),
        (FIXTURES / "home.html").read_text(encoding="utf-8"),
        base_url=BASE,
        sitemap_url=BASE + "/sitemap.xml",
        pii_guard=pii_guard,
        generated_at="2026-10-24T00:00:00Z",
    )


def build_store(tmp_path, *, pii_guard: bool = True):
    result = compile_fixture(pii_guard=pii_guard)
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


def seed_page(store: GraphStore, node_id: str = HOSTEL,
              text: str = "Boys and girls hostels with a warden office.") -> None:
    store.record_page(
        node_id,
        text=text,
        markdown=text,
        tables=(),
        sections=({"heading": "Hostel", "text": text},),
        links=(),
        documents=(),
        language="en",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at="2026-10-24T00:00:00Z",
        verified_at="2026-10-24T00:00:00Z",
        size_bytes=len(text),
        etag='"v1"',
        last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
        http_status=200,
        next_due_at="2026-10-25T00:00:00Z",
    )


def build_server(tmp_path, *, pii_guard: bool = True, seed: bool = True,
                 notices_path=None):
    store, db = build_store(tmp_path, pii_guard=pii_guard)
    if seed:
        seed_page(store)
    return SiteToolServer(db, pii_guard=pii_guard, notices_path=notices_path), store, db


def write_notices(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path
