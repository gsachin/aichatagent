"""Notice PDF body extraction and PII gating."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.notice_store import build_store, search_notices
from app.siteindex.extract import PdfDoc
from app.siteindex.notice_bodies import extract_notice_bodies


def _write_pdf(path: Path, text: str) -> None:
    fpdf = pytest.importorskip("fpdf")
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, text)
    path.write_bytes(bytes(pdf.output()))


@pytest.fixture
def catalogue(tmp_path: Path) -> Path:
    path = tmp_path / "notices.jsonl"
    rows = [
        {
            "id": "clean",
            "date": "2026-09-21",
            "title": "Holiday Notice",
            "category": "Holiday",
            "url": "https://example.test/clean.pdf",
            "pii": False,
        },
        {
            "id": "pii",
            "date": "2026-09-20",
            "title": "Examination Notice",
            "category": "Examination",
            "url": "https://example.test/pii.pdf",
            "pii": False,
        },
        {
            "id": "title-pii",
            "date": "2026-09-19",
            "title": "Merit List of Selected Candidates",
            "category": "Miscellaneous",
            "url": "https://example.test/title.pdf",
            "pii": False,
        },
        {
            "id": "missing",
            "date": "2026-09-18",
            "title": "Notice Without A Cached PDF",
            "category": "Miscellaneous",
            "url": "https://example.test/missing.pdf",
            "pii": False,
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_extracts_clean_bodies_and_blocks_pii(catalogue, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _write_pdf(
        pdf_dir / "clean.pdf",
        "The campus will remain closed on Monday because of a holiday.",
    )
    _write_pdf(pdf_dir / "pii.pdf", "Roll No: 123456")

    bodies = tmp_path / "bodies"
    stats = extract_notice_bodies(
        catalogue,
        bodies,
        pdf_dir=pdf_dir,
        cache_dir=tmp_path / "cache",
        ocr=False,
    )

    assert stats.scanned == 3
    assert stats.indexed == 1
    assert stats.pii_blocked == 1
    assert stats.missing_pdf == 1
    assert stats.skipped_title_pii == 1
    assert (bodies / "clean.txt").is_file()
    assert not (bodies / "pii.txt").exists()

    manifest = [
        json.loads(line)
        for line in (bodies / "manifest.jsonl").read_text().splitlines()
    ]
    by_id = {row["id"]: row for row in manifest}
    assert by_id["clean"]["status"] == "indexed"
    assert by_id["pii"]["status"] == "pii_blocked"
    assert "roll_or_registration_number" in by_id["pii"]["pii_reasons"]
    assert by_id["missing"]["status"] == "missing_pdf"
    assert "title-pii" not in by_id


def test_clean_bodies_are_searchable_in_the_fts_store(catalogue, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _write_pdf(
        pdf_dir / "clean.pdf",
        "The campus will remain closed on Monday because of a holiday.",
    )
    _write_pdf(pdf_dir / "pii.pdf", "Roll No: 123456")

    bodies = tmp_path / "bodies"
    extract_notice_bodies(
        catalogue,
        bodies,
        pdf_dir=pdf_dir,
        cache_dir=tmp_path / "cache",
        ocr=False,
    )
    db = tmp_path / "notices.sqlite3"
    stats = build_store(catalogue, db, body_dir=bodies)

    assert stats.body_rows == 1
    rows = search_notices("campus", db_path=db)
    assert rows is not None
    assert [row["id"] for row in rows] == ["clean"]


def test_ocr_bodies_are_review_only(catalogue, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _write_pdf(pdf_dir / "clean.pdf", "A clean extracted page.")
    _write_pdf(pdf_dir / "pii.pdf", "A second page.")

    def fake_extractor(body: bytes, *, ocr: bool = False, **kwargs) -> PdfDoc:
        return PdfDoc(
            pages=1,
            text="Scanned text that still needs review.",
            tables=(),
            ocr=True,
            ocr_available=True,
            ocr_lang="eng",
            ocr_quality="good",
            needs_review=True,
        )

    bodies = tmp_path / "bodies"
    stats = extract_notice_bodies(
        catalogue,
        bodies,
        pdf_dir=pdf_dir,
        cache_dir=tmp_path / "cache",
        ocr=True,
        extractor=fake_extractor,
    )

    assert stats.needs_review == 2
    assert not (bodies / "clean.txt").exists()
    assert not (bodies / "pii.txt").exists()
    assert (bodies / "_review" / "clean.txt").is_file()
    assert (bodies / "_review" / "pii.txt").is_file()


def test_a_stale_body_is_removed_when_the_new_extraction_is_pii(
    catalogue, tmp_path
):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _write_pdf(pdf_dir / "pii.pdf", "Roll No: 123456")
    bodies = tmp_path / "bodies"
    bodies.mkdir()
    (bodies / "pii.txt").write_text("old clean body", encoding="utf-8")

    stats = extract_notice_bodies(
        catalogue,
        bodies,
        pdf_dir=pdf_dir,
        cache_dir=tmp_path / "cache",
        ocr=False,
        skip_existing=False,
    )

    assert stats.pii_blocked == 1
    assert not (bodies / "pii.txt").exists()


def test_limit_applies_to_non_pii_rows(catalogue, tmp_path):
    bodies = tmp_path / "bodies"
    stats = extract_notice_bodies(
        catalogue,
        bodies,
        cache_dir=tmp_path / "cache",
        limit=1,
        ocr=False,
    )

    assert stats.scanned == 1
    assert stats.missing_pdf == 1
