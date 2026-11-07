"""Extract notice PDFs into PII-gated body text for the FTS5 store.

The default path is offline: read a local ``--pdf-dir`` or the existing
site-index cache. Network access is explicit (``--allow-network``) and goes
through :class:`app.siteindex.fetch.SourceBook`, which checks robots.txt, uses
a contactable custom user agent, and waits one second between requests.

A body is written to ``bodies_dir/<notice-id>.txt`` only when the extracted
text passes :func:`app.pii.scan_text`. PII-flagged and OCR/review-needed bodies
are recorded in the manifest but their text is not persisted. That keeps the
review queue from becoming a second sensitive-data store.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.notice_store import (
    DEFAULT_NOTICES_PATH,
    is_likely_pii_title,
    read_notices,
)
from app.pii import scan_text
from app.siteindex.extract import PdfDoc, extract_pdf
from app.siteindex.fetch import SourceBook, SourceSpec

logger = logging.getLogger("siteindex.notice_bodies")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "content/dspmu/cache"
DEFAULT_BODIES_DIR = PROJECT_ROOT / "content/dspmu/notice_bodies"


@dataclass(frozen=True)
class NoticeBodyStats:
    scanned: int = 0
    indexed: int = 0
    pii_blocked: int = 0
    needs_review: int = 0
    missing_pdf: int = 0
    empty: int = 0
    errors: int = 0
    skipped_existing: int = 0
    skipped_title_pii: int = 0

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "indexed": self.indexed,
            "pii_blocked": self.pii_blocked,
            "needs_review": self.needs_review,
            "missing_pdf": self.missing_pdf,
            "empty": self.empty,
            "errors": self.errors,
            "skipped_existing": self.skipped_existing,
            "skipped_title_pii": self.skipped_title_pii,
        }


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _read_pdf(
    row: dict,
    *,
    pdf_dir: Path | None,
    cache_dir: Path,
    allow_network: bool,
    source_book: SourceBook | None,
) -> bytes | None:
    notice_id = str(row["id"])
    if pdf_dir is not None:
        for suffix in (".pdf", ".PDF"):
            path = pdf_dir / f"{notice_id}{suffix}"
            if path.is_file():
                return path.read_bytes()

    cached = cache_dir / "notices" / f"{notice_id}.pdf"
    if cached.is_file():
        return cached.read_bytes()

    if allow_network and source_book is not None:
        spec = SourceSpec(
            key=f"notice-{notice_id}",
            url=str(row.get("url") or ""),
            relpath=f"notices/{notice_id}.pdf",
            kind="pdf",
        )
        try:
            return source_book.load(spec).body
        except Exception as exc:  # noqa: BLE001 - recorded per notice
            logger.warning("notice %s fetch failed: %s", notice_id, exc)
            return None
    return None


def extract_notice_bodies(
    notices_path: str | Path = DEFAULT_NOTICES_PATH,
    bodies_dir: str | Path = DEFAULT_BODIES_DIR,
    *,
    pdf_dir: str | Path | None = None,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    allow_network: bool = False,
    limit: int | None = None,
    ocr: bool = False,
    ocr_langs: tuple[str, ...] | None = None,
    tessdata_dir: str | None = None,
    skip_existing: bool = True,
    extractor: Callable[..., PdfDoc] = extract_pdf,
) -> NoticeBodyStats:
    """Extract notice bodies and write a manifest; never persist flagged text.

    ``ocr_langs`` defaults to ``eng,hin`` and is used only when ``ocr`` is
    true. OCR/review-needed text is written to ``bodies_dir/_review`` (gitignored)
    and is never read by :func:`app.notice_store.build_store`.
    """
    body_root = _resolve(bodies_dir)
    body_root.mkdir(parents=True, exist_ok=True)
    review_dir = body_root / "_review"
    pdf_root = _resolve(pdf_dir) if pdf_dir else None
    cache_root = _resolve(cache_dir)
    manifest_path = body_root / "manifest.jsonl"

    rows = read_notices(notices_path)
    selected: list[dict] = []
    title_pii = 0
    for row in rows:
        title = str(row.get("title") or "")
        if row.get("pii") or is_likely_pii_title(title):
            title_pii += 1
            continue
        selected.append(row)
    if limit is not None:
        selected = selected[: max(0, int(limit))]

    source_book = (
        SourceBook(cache_root, allow_network=True)
        if allow_network
        else None
    )

    counts = {
        "scanned": 0,
        "indexed": 0,
        "pii_blocked": 0,
        "needs_review": 0,
        "missing_pdf": 0,
        "empty": 0,
        "errors": 0,
        "skipped_existing": 0,
    }
    manifest: list[dict] = []

    for row in selected:
        counts["scanned"] += 1
        notice_id = str(row["id"])
        title = str(row.get("title") or "")
        body_path = body_root / f"{notice_id}.txt"
        review_path = review_dir / f"{notice_id}.txt"
        record = {
            "id": notice_id,
            "url": str(row.get("url") or ""),
            "title": title,
            "status": "",
            "chars": 0,
            "ocr": False,
            "needs_review": False,
            "pii_reasons": [],
            "content_hash": "",
            "review_path": "",
            "error": "",
        }

        if skip_existing and body_path.is_file():
            review_path.unlink(missing_ok=True)
            counts["skipped_existing"] += 1
            record["status"] = "existing"
            manifest.append(record)
            continue

        body = _read_pdf(
            row,
            pdf_dir=pdf_root,
            cache_dir=cache_root,
            allow_network=allow_network,
            source_book=source_book,
        )
        if body is None:
            body_path.unlink(missing_ok=True)
            review_path.unlink(missing_ok=True)
            counts["missing_pdf"] += 1
            record["status"] = "missing_pdf"
            manifest.append(record)
            continue

        try:
            doc = extractor(
                body,
                ocr=ocr,
                ocr_langs=ocr_langs,
                tessdata_dir=tessdata_dir,
            )
        except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop the run
            body_path.unlink(missing_ok=True)
            review_path.unlink(missing_ok=True)
            counts["errors"] += 1
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            manifest.append(record)
            continue

        text = (doc.text or "").strip()
        record["chars"] = len(text)
        record["ocr"] = bool(doc.ocr)
        record["needs_review"] = bool(doc.needs_review)
        record["error"] = doc.error

        if not text:
            body_path.unlink(missing_ok=True)
            review_path.unlink(missing_ok=True)
            counts["empty"] += 1
            record["status"] = "empty"
            manifest.append(record)
            continue

        scan = scan_text(text)
        if scan.flagged:
            body_path.unlink(missing_ok=True)
            review_path.unlink(missing_ok=True)
            counts["pii_blocked"] += 1
            record["status"] = "pii_blocked"
            record["pii_reasons"] = list(scan.reasons)
            manifest.append(record)
            continue

        if doc.needs_review or doc.ocr:
            # OCR text is not indexed until a human has verified it. Keep a
            # copy in the gitignored _review directory so the review can
            # actually happen; build_store only reads <id>.txt at the top
            # level, so this text cannot enter the prompt by accident.
            body_path.unlink(missing_ok=True)
            review_dir.mkdir(parents=True, exist_ok=True)
            review_path.write_text(text + "\n", encoding="utf-8")
            try:
                record["review_path"] = str(
                    review_path.relative_to(body_root)
                )
            except ValueError:
                record["review_path"] = str(review_path)
            counts["needs_review"] += 1
            record["status"] = "needs_review"
            manifest.append(record)
            continue

        body_bytes = (text + "\n").encode("utf-8")
        body_path.write_bytes(body_bytes)
        review_path.unlink(missing_ok=True)
        counts["indexed"] += 1
        record["status"] = "indexed"
        record["content_hash"] = hashlib.sha256(body_bytes).hexdigest()
        manifest.append(record)

    manifest_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest),
        encoding="utf-8",
    )
    return NoticeBodyStats(
        **counts,
        skipped_title_pii=title_pii,
    )
