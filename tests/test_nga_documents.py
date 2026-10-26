"""Phase 11: background PDF extraction, OCR review and document tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.sitegraph.documents import (
    DocumentOptions,
    discover_documents,
    refresh_documents,
)
from app.sitegraph.fetch import FetchError, FetchResult
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer
from app.siteindex.extract import PdfDoc, extract_pdf
from tests.nga_helpers import BASE, HOSTEL, build_store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_URL = BASE + "/uploads/hostel-rules.pdf"
DOC_TITLE = "Hostel rules"
HOSTS = ("dspmuranchi.ac.in",)


def _pdf(*pages: str) -> bytes:
    fpdf = pytest.importorskip("fpdf")
    document = fpdf.FPDF()
    document.set_auto_page_break(False)
    for text in pages or ("Hostel rules and fee details.",):
        document.add_page()
        document.set_font("Helvetica", size=12)
        document.multi_cell(0, 10, text)
    return bytes(document.output())


class FakeDocFetcher:
    def __init__(self, bodies: dict[str, bytes | Exception | None]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    def fetch_document(self, url, *, etag="", last_modified="", max_bytes=None):
        self.calls.append(url)
        value = self.bodies.get(url)
        if value is None:
            return FetchResult(
                url=url,
                final_url=url,
                status=304,
                body=b"",
                content_type="application/pdf",
                etag=etag,
                last_modified=last_modified,
                not_modified=True,
            )
        if isinstance(value, Exception):
            raise value
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            body=value,
            content_type="application/pdf",
            etag='"pdf-v1"',
            last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
        )


def _seed_link(
    store: GraphStore,
    *,
    text: str = "Hostel rules and fee details for residents.",
) -> None:
    store.record_page(
        HOSTEL,
        text=text,
        markdown=text,
        tables=(),
        sections=({"heading": "Hostel", "text": text},),
        links=({"title": DOC_TITLE, "url": DOC_URL},),
        documents=({"title": DOC_TITLE, "url": DOC_URL},),
        language="en",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at="2026-10-24T00:00:00Z",
        verified_at="2026-10-24T00:00:00Z",
        size_bytes=len(text),
        etag='"page-v1"',
        last_modified="",
        http_status=200,
        next_due_at="2026-10-25T00:00:00Z",
    )


def _options(**overrides) -> DocumentOptions:
    values = dict(
        ttl_s=3600,
        limit=5,
        min_chars=20,
        ocr=False,
        allowed_hosts=HOSTS,
        now="2026-10-24T10:00:00Z",
    )
    values.update(overrides)
    return DocumentOptions(**values)


def _document_node(store: GraphStore):
    docs = store.documents(HOSTEL)
    assert len(docs) == 1
    return docs[0]


def test_extract_pdf_caps_processed_pages():
    body = _pdf("First page text.", "Second page text.")

    capped = extract_pdf(body, ocr=False, max_pages=1)
    full = extract_pdf(body, ocr=False)

    assert capped.pages == 1
    assert "First page" in capped.text
    assert "Second page" not in capped.text
    assert full.pages == 2
    assert "Second page" in full.text


def test_fetch_document_accepts_pdf_and_rejects_non_pdf():
    from app.sitegraph.fetch import SiteFetcher

    class Response:
        def __init__(self, body: bytes, content_type: str) -> None:
            self.url = DOC_URL
            self.status_code = 200
            self.content = body
            self.headers = {"content-type": content_type}
            self.text = ""

        def iter_content(self, chunk_size=8192):
            yield self.content

        def close(self):
            pass

    class Session:
        def __init__(self) -> None:
            self.body = b"%PDF-1.7\ncontent"
            self.content_type = "application/pdf"
            self.headers: dict[str, str] = {}

        def get(self, url, timeout=None, allow_redirects=True, headers=None):
            if url.endswith("/robots.txt"):
                return Response(b"", "text/plain")
            return Response(self.body, self.content_type)

    session = Session()
    fetcher = SiteFetcher(
        HOSTS,
        user_agent="DSPMU-Test/1.0",
        timeout_s=1.0,
        delay_s=0.0,
        max_bytes=1000,
        respect_robots=False,
        session=session,
    )

    result = fetcher.fetch_document(DOC_URL)
    assert result.body.startswith(b"%PDF-")

    session.body = b"<html>not a pdf</html>"
    with pytest.raises(FetchError):
        fetcher.fetch_document(DOC_URL)

    session.body = b"%PDF-1.7\ncontent"
    session.content_type = "image/png"
    with pytest.raises(FetchError):
        fetcher.fetch_document(DOC_URL)


def test_discovery_creates_a_pending_document_node(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)

    linked = discover_documents(
        store, allowed_hosts=HOSTS, pii_guard=False
    )
    node = _document_node(store)
    document = store.document(node.node_id)

    assert linked == 1
    assert node.title == DOC_TITLE
    assert node.content_type.value == "pdf"
    assert document is not None
    assert document.extraction_status == "pending"
    assert store.document_summary()["pending"] == 1


def test_unlinked_sitemap_pdf_is_not_due(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)

    due = store.due_documents(limit=10)

    assert due == []
    explicit = store.due_documents(
        limit=10, node_ids=("uploads.news.fee-pdf",)
    )
    assert [node.node_id for node in explicit] == ["uploads.news.fee-pdf"]


def test_discovery_skips_unsafe_links(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    text = "A page with unsafe document links."
    store.record_page(
        HOSTEL,
        text=text,
        markdown=text,
        tables=(),
        sections=({"heading": "Hostel", "text": text},),
        links=(),
        documents=(
            {"title": "Plain", "url": "http://dspmuranchi.ac.in/a.pdf"},
            {"title": "Elsewhere", "url": "https://evil.example/b.pdf"},
            {"title": "Result list", "url": BASE + "/result/list.pdf"},
        ),
        language="en",
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at="2026-10-24T00:00:00Z",
        verified_at="2026-10-24T00:00:00Z",
        size_bytes=len(text),
        etag="",
        last_modified="",
        http_status=200,
        next_due_at="2026-10-25T00:00:00Z",
    )

    linked = discover_documents(
        store,
        allowed_hosts=HOSTS,
        blocked_path_tokens=("result",),
        pii_guard=True,
    )

    assert linked == 0
    assert store.documents(HOSTEL) == []


def test_refresh_indexes_pdf_and_search_opens_it(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("The hostel fee is 3410 rupees per year and the warden office is open daily.")
    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: body}),
        _options(),
        pii_guard=False,
    )

    node = _document_node(store)
    document = store.document(node.node_id)
    assert stats.indexed == 1
    assert document.extraction_status == "done"
    assert document.answerable is True
    assert "3410 rupees" in document.text
    assert store.document_summary()["done"] == 1

    server = SiteToolServer(db, pii_guard=False)
    search = server.call("site.search", {"query": "warden"})
    assert search["status"] == "ok"
    hit = search["data"]["results"][0]
    assert hit["node_id"] == node.node_id
    assert hit["content_status"] == "indexed"
    assert hit["pages"] == 1

    opened = server.call("site.open", {"node_id": node.node_id})
    assert opened["status"] == "ok"
    assert opened["data"]["body_available"] is True
    assert "3410 rupees" in opened["data"]["sections"][0]["text"]
    assert opened["data"]["content_status"] == "indexed"

    listed = server.call("site.documents", {"node_id": HOSTEL})
    assert listed["status"] == "ok"
    assert listed["data"]["documents"][0]["extraction_status"] == "done"
    assert listed["data"]["documents"][0]["needs_review"] is False

    freshness = server.call("site.freshness", {"node_id": node.node_id})
    assert freshness["data"]["pages"] == 1
    assert freshness["data"]["extraction_status"] == "done"
    assert freshness["data"]["needs_review"] is False

    from app.agent.executor import execute_plan
    from app.agent.models import Intent, Plan, PlanStep

    plan = Plan(
        intent=Intent.PAGE_TOPIC,
        steps=(PlanStep("site.search", {"query": "warden", "limit": 3}),),
    )
    execution = execute_plan(server, plan, deadline_ms=5000)
    assert execution.steps_used == 2
    assert execution.synthetic_steps[0]["tool"] == "site.open"
    assert execution.results[-1]["data"]["body_available"] is True


def test_ocr_text_is_not_answerable_until_reviewed(tmp_path, monkeypatch):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)

    def fake_extract(body, **kwargs):
        return PdfDoc(
            pages=1,
            text="Scanned hostel rule: visitors must sign the register.",
            tables=(),
            ocr=True,
            ocr_available=True,
            ocr_lang="eng",
            ocr_quality="good",
            needs_review=True,
        )

    monkeypatch.setattr("app.sitegraph.documents.extract_pdf", fake_extract)
    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: _pdf("scan")}),
        _options(),
        pii_guard=False,
    )
    node = _document_node(store)
    server = SiteToolServer(db, pii_guard=False)

    assert stats.needs_review == 1
    assert store.document(node.node_id).needs_review is True
    assert server.call("site.search", {"query": "visitors"})["data"]["results"] == []
    opened = server.call("site.open", {"node_id": node.node_id})
    assert opened["data"]["content_status"] == "needs_review"
    assert opened["data"]["body_available"] is False
    assert "review" in opened["data"]["note"].lower()

    assert store.mark_document_reviewed(
        node.node_id, reviewer="registrar office", now="2026-10-24T11:00:00Z"
    )
    search = server.call("site.search", {"query": "visitors"})
    assert search["data"]["results"][0]["node_id"] == node.node_id
    opened = server.call("site.open", {"node_id": node.node_id})
    assert "sign the register" in opened["data"]["sections"][0]["text"]
    assert store.document(node.node_id).reviewed_by == "registrar office"


def test_content_pii_blocks_text_when_the_guard_is_enabled(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Award list Aadhaar 123456789012 candidate.")

    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: body}),
        _options(),
        pii_guard=True,
    )
    node = store.get_node(
        store.documents(HOSTEL)[0].node_id
    )
    document = store.document(node.node_id)
    server = SiteToolServer(db, pii_guard=True)

    assert stats.blocked == 1
    assert document.text == ""
    assert document.pii_flagged is True
    assert node.status.value == "blocked"
    assert server.call("site.open", {"node_id": node.node_id})["status"] == "BLOCKED"


def test_failed_fetch_preserves_the_last_good_text(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Warden office hours are 10am to 5pm.")
    refresh_documents(
        store, FakeDocFetcher({DOC_URL: body}), _options(), pii_guard=False
    )
    node = _document_node(store)
    first = store.document(node.node_id)

    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: FetchError("connection reset")}),
        _options(force=True, node_ids=(node.node_id,)),
        pii_guard=False,
    )
    second = store.document(node.node_id)

    assert stats.errors == 1
    assert second.text == first.text
    assert second.extraction_status == "done"
    assert store.fetch_state(node.node_id)["failure_count"] == 1


def test_same_pdf_is_not_extracted_twice(tmp_path, monkeypatch):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Warden office hours are 10am to 5pm.")
    calls = {"n": 0}
    original = extract_pdf

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("app.sitegraph.documents.extract_pdf", counting)
    refresh_documents(
        store, FakeDocFetcher({DOC_URL: body}), _options(), pii_guard=False
    )
    doc_node = _document_node(store)
    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: body}),
        _options(force=True, node_ids=(doc_node.node_id,)),
        pii_guard=False,
    )

    assert calls["n"] == 1
    assert stats.unchanged == 1


def test_not_modified_is_revalidated_without_reextracting(tmp_path, monkeypatch):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Warden office hours are 10am to 5pm.")
    refresh_documents(
        store, FakeDocFetcher({DOC_URL: body}), _options(), pii_guard=False
    )
    node = _document_node(store)
    calls = {"n": 0}
    original = extract_pdf

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("app.sitegraph.documents.extract_pdf", counting)
    stats = refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: None}),
        _options(force=True, node_ids=(node.node_id,)),
        pii_guard=False,
    )

    assert stats.not_modified == 1
    assert calls["n"] == 0
    assert store.fetch_state(node.node_id)["http_status"] == 304


def test_non_pdf_response_is_an_error(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    fetcher = FakeDocFetcher({DOC_URL: b"<html>not a pdf</html>"})

    stats = refresh_documents(store, fetcher, _options(), pii_guard=False)

    assert stats.errors == 1
    assert store.document_summary()["errors"] == 1
    assert store.document(_document_node(store).node_id).text == ""


def test_document_ttl_revalidates_after_a_cached_fetch(tmp_path):
    store, _db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Warden office hours are 10am to 5pm.")
    refresh_documents(
        store, FakeDocFetcher({DOC_URL: body}), _options(), pii_guard=False
    )
    node = _document_node(store)

    due_now = store.due_documents(limit=10, now="2026-10-24T10:30:00Z")
    due_later = store.due_documents(limit=10, now="2026-10-24T12:00:00Z")

    assert node.node_id not in [item.node_id for item in due_now]
    assert node.node_id in [item.node_id for item in due_later]


def test_review_cli_approves_and_indexes(tmp_path, monkeypatch):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)

    def fake_extract(body, **kwargs):
        return PdfDoc(
            pages=1,
            text="Scanned rule: visitors must sign the register.",
            tables=(),
            ocr=True,
            ocr_available=True,
            ocr_lang="eng",
            ocr_quality="good",
            needs_review=True,
        )

    monkeypatch.setattr("app.sitegraph.documents.extract_pdf", fake_extract)
    refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: _pdf("scan")}),
        _options(),
        pii_guard=False,
    )
    node = _document_node(store)

    listed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_documents.py"),
            "--db",
            str(db),
            "--list-review",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["total"] == 1

    missing_reviewer = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_documents.py"),
            "--db",
            str(db),
            "--review",
            node.node_id,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert missing_reviewer.returncode == 2
    assert "--reviewer" in missing_reviewer.stderr

    reviewed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_documents.py"),
            "--db",
            str(db),
            "--review",
            node.node_id,
            "--reviewer",
            "registrar office",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert reviewed.returncode == 0, reviewed.stderr
    assert store.document(node.node_id).needs_review is False
    assert store.document_summary()["review_pending"] == 0


def test_documents_cli_discovers_links_without_network(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_documents.py"),
            "--db",
            str(db),
            "--discover-only",
            "--force",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert "1" in completed.stdout
    assert len(store.documents(HOSTEL)) == 1


def test_documents_cli_status_is_offline(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    _seed_link(store)
    body = _pdf("Warden office hours are 10am to 5pm.")
    refresh_documents(
        store,
        FakeDocFetcher({DOC_URL: body}),
        _options(ttl_s=315_360_000),
        pii_guard=False,
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_documents.py"),
            "--db",
            str(db),
            "--status",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["done"] == 1
    assert payload["due"] == 0


def test_documents_cli_requires_the_flag_and_network(tmp_path):
    _store, db = build_store(tmp_path, pii_guard=False)
    base = [
        sys.executable,
        str(PROJECT_ROOT / "scripts/nga_documents.py"),
        "--db",
        str(db),
    ]
    guarded = subprocess.run(
        base + ["--allow-network"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    no_network = subprocess.run(
        base + ["--force"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert guarded.returncode == 2
    assert "NGA_PDF_INDEX_ENABLED" in guarded.stderr
    assert no_network.returncode == 2
    assert "--allow-network" in no_network.stderr
