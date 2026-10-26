"""Extraction for the DSPMU site index.

HTML is converted to a small markdown subset (headings, paragraphs, lists and
tables). PDFs use pypdf for text and pdfplumber for tables; if pypdf returns
almost nothing, the page is rendered with PyMuPDF and OCR'd with Tesseract.
OCR is a fallback, not a source of truth: the build marks every OCR-derived
document for human review.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import pymupdf
import pypdf
from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image

logger = logging.getLogger("siteindex.extract")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_TESSDATA_DIR = PROJECT_ROOT / ".tessdata"
DEFAULT_OCR_LANGS = ("eng", "hin")

_SKIP = {"script", "style", "noscript", "svg", "canvas", "form", "iframe"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class HtmlDoc:
    url: str
    title: str
    text: str
    markdown: str
    main_chars: int
    emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfDoc:
    pages: int
    text: str
    tables: tuple[tuple[tuple[tuple[str, ...], ...], ...], ...]
    ocr: bool
    ocr_available: bool
    ocr_lang: str
    ocr_quality: str
    needs_review: bool
    error: str = ""

    @property
    def table_count(self) -> int:
        return sum(len(page) for page in self.tables)


def decode_cloudflare_emails(html: str) -> tuple[str, ...]:
    """Decode ``data-cfemail`` values without running Cloudflare's script."""
    out: list[str] = []
    for value in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html or ""):
        try:
            key = int(value[:2], 16)
            decoded = "".join(
                chr(int(value[index:index + 2], 16) ^ key)
                for index in range(2, len(value), 2)
            )
        except (ValueError, IndexError):
            continue
        if decoded and decoded not in out:
            out.append(decoded)
    return tuple(out)


def _inline(node: Tag, base_url: str) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name in _SKIP:
        return ""
    if node.name in ("ul", "ol", "table"):
        # Nested blocks are rendered by _render, never duplicated inline.
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "a":
        text = node.get_text(" ", strip=True)
        href = (node.get("href") or "").strip()
        if href and not href.startswith("#") and text:
            return f"[{text}]({urljoin(base_url, href)})"
        return text or href
    if node.name in ("strong", "b"):
        text = node.get_text(" ", strip=True)
        return f"**{text}**" if text else ""
    if node.name in ("em", "i"):
        text = node.get_text(" ", strip=True)
        return f"*{text}*" if text else ""
    return "".join(_inline(child, base_url) for child in node.children)


def _table_markdown(table: Tag, base_url: str) -> str:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        values = []
        for cell in cells:
            text = _inline(cell, base_url).replace("|", r"\|")
            text = re.sub(r"\s+", " ", text).strip()
            values.append(text)
        if any(values):
            rows.append(values)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(out)


def _render(node: Tag, base_url: str) -> str:
    if isinstance(node, NavigableString):
        return ""
    if not isinstance(node, Tag):
        return ""
    if node.name in _SKIP:
        return ""
    if node.name in _HEADINGS:
        text = node.get_text(" ", strip=True)
        return f"\n{'#' * _HEADINGS[node.name]} {text}\n" if text else ""
    if node.name == "p":
        text = _inline(node, base_url).strip()
        return f"\n{text}\n" if text else ""
    if node.name == "li":
        text = _inline(node, base_url).strip()
        out = f"\n- {text}\n" if text else "\n"
        child_list = node.find(["ul", "ol"], recursive=False)
        if child_list is not None:
            out += _render(child_list, base_url)
        return out
    if node.name == "blockquote":
        text = node.get_text(" ", strip=True)
        return f"\n> {text}\n" if text else ""
    if node.name == "table":
        table = _table_markdown(node, base_url)
        return f"\n{table}\n" if table else ""
    if node.name in ("ul", "ol"):
        return "\n" + "".join(_render(child, base_url) for child in node.children) + "\n"
    if node.name in ("a", "strong", "b", "em", "i", "br"):
        return _inline(node, base_url)
    return "".join(_render(child, base_url) for child in node.children)


def _clean_markdown(markdown: str) -> str:
    text = (markdown or "").replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_html(html: str, base_url: str, url: str = "") -> HtmlDoc:
    """Extract the main content of a CMS page as text and markdown."""
    soup = BeautifulSoup(html, "lxml")
    title_node = soup.find("h1") or soup.title
    title = title_node.get_text(" ", strip=True) if title_node else ""
    title = re.split(r"\s+[|·—]\s+", title)[0].strip()

    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        return HtmlDoc(url=url or base_url, title=title, text="", markdown="", main_chars=0)

    for tag in main.find_all(list(_SKIP) + ["nav", "footer", "header"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip()
    markdown = _clean_markdown(_render(main, base_url))

    emails: list[str] = list(decode_cloudflare_emails(html))
    for email in _EMAIL.findall(text):
        if email not in emails:
            emails.append(email)

    return HtmlDoc(
        url=url or base_url,
        title=title,
        text=text,
        markdown=markdown,
        main_chars=len(text),
        emails=tuple(emails),
    )


def default_tessdata_dir() -> str | None:
    """The local or operator-supplied tessdata directory, if any."""
    configured = (
        os.environ.get("TESSERACT_TESSDATA_DIR")
        or os.environ.get("TESSDATA_PREFIX")
        or ""
    ).strip()
    if configured:
        return configured
    if LOCAL_TESSDATA_DIR.is_dir():
        return str(LOCAL_TESSDATA_DIR)
    return None


def requested_ocr_langs(explicit: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Languages to request, in fallback order."""
    if explicit:
        return tuple(lang.strip() for lang in explicit if lang.strip())
    raw = os.environ.get("TESSERACT_OCR_LANGS", ",".join(DEFAULT_OCR_LANGS))
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _tesseract_langs(tessdata_dir: str | None = None) -> frozenset[str]:
    binary = shutil.which("tesseract")
    if not binary:
        return frozenset()
    command = [binary]
    if tessdata_dir:
        command += ["--tessdata-dir", tessdata_dir]
    command += ["--list-langs"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    lines = [line.strip() for line in (proc.stdout or "").splitlines()]
    return frozenset(line for line in lines[1:] if line and not line.startswith("List"))


def select_ocr_langs(
    available: frozenset[str] | set[str],
    requested: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Choose the languages Tesseract actually has.

    Requested order is preserved. If none of the requested languages are
    installed, fall back to English, then Hindi. An empty result means OCR is
    unavailable.
    """
    wanted = requested or requested_ocr_langs()
    chosen = [lang for lang in wanted if lang in available]
    if chosen:
        return tuple(chosen)
    if "eng" in available:
        return ("eng",)
    if "hin" in available:
        return ("hin",)
    return ()


def _ocr_image(
    binary: str,
    image: Path,
    lang: str,
    *,
    tessdata_dir: str | None = None,
) -> str:
    command = [
        binary,
        str(image),
        "stdout",
        "--oem",
        "1",
        "--psm",
        "3",
    ]
    if tessdata_dir:
        command += ["--tessdata-dir", tessdata_dir]
    command += ["-l", lang]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("OCR failed for %s: %s", image.name, exc)
        return ""
    if proc.returncode != 0:
        logger.warning("tesseract exited %s for %s: %s", proc.returncode, image.name, proc.stderr[:200])
        return ""
    return (proc.stdout or "").strip()


def _ocr_pdf(
    body: bytes,
    *,
    max_pages: int = 8,
    ocr_langs: tuple[str, ...] | None = None,
    tessdata_dir: str | None = None,
) -> tuple[str, bool, str, str]:
    binary = shutil.which("tesseract")
    if not binary:
        return "", False, "", "unavailable"
    tessdata_dir = tessdata_dir or default_tessdata_dir()
    available = _tesseract_langs(tessdata_dir)
    selected = select_ocr_langs(available, ocr_langs)
    if not selected:
        return "", False, "", "unavailable"
    lang = "+".join(selected)
    requested = requested_ocr_langs(ocr_langs)
    missing = [item for item in requested if item not in available]
    if missing:
        logger.warning(
            "OCR languages %s are not installed in %s; using %s",
            ",".join(missing),
            tessdata_dir or "the system tessdata directory",
            lang,
        )
    try:
        document = pymupdf.open(stream=body, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyMuPDF could not open the PDF for OCR: %s", exc)
        return "", True, lang, "failed"

    text_parts: list[str] = []
    # Tesseract in this environment reads a workspace path more reliably than
    # /tmp; keep the temporary files inside the repo and clean them up.
    with tempfile.TemporaryDirectory(prefix=".siteindex-ocr-", dir=Path.cwd()) as temp_dir:
        temp = Path(temp_dir)
        for index, page in enumerate(document):
            if index >= max_pages:
                break
            try:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                image_path = temp / f"page-{index}.png"
                pixmap.save(str(image_path))
                text = _ocr_image(binary, image_path, lang, tessdata_dir=tessdata_dir)
                if len(re.sub(r"\W", "", text)) < 40:
                    rotated = temp / f"page-{index}-rotated.png"
                    Image.open(image_path).rotate(180, expand=True).save(rotated)
                    alternate = _ocr_image(binary, rotated, lang, tessdata_dir=tessdata_dir)
                    if len(re.sub(r"\W", "", alternate)) > len(re.sub(r"\W", "", text)):
                        text = alternate
                if text:
                    text_parts.append(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OCR failed on page %d: %s", index + 1, exc)
    joined = "\n\n".join(text_parts).strip()
    chars = len(re.sub(r"\W", "", joined))
    quality = "good" if chars >= 200 else ("low" if chars else "none")
    return joined, True, lang, quality


def extract_pdf(
    body: bytes,
    *,
    ocr: bool = True,
    max_ocr_pages: int = 8,
    ocr_langs: tuple[str, ...] | None = None,
    tessdata_dir: str | None = None,
    max_pages: int | None = None,
) -> PdfDoc:
    """Extract text and tables from a PDF, with an OCR fallback for scans.

    ``ocr_langs`` defaults to ``eng,hin``; installed languages are selected in
    that order. OCR text is always marked for human review. ``max_pages``
    caps how many leading pages are read; ``pages`` in the result is the
    number of pages actually processed.
    """
    page_limit = max(1, int(max_pages)) if max_pages else None
    pages = 0
    text = ""
    tables: list[tuple[tuple[tuple[str, ...], ...], ...]] = []
    error = ""

    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        selected = (
            reader.pages[:page_limit] if page_limit else reader.pages
        )
        pages = len(selected)
        text = "\n".join((page.extract_text() or "") for page in selected).strip()
    except Exception as exc:  # noqa: BLE001
        error = f"pypdf: {type(exc).__name__}: {exc}"

    try:
        with pdfplumber.open(io.BytesIO(body)) as document:
            selected_pages = (
                document.pages[:page_limit] if page_limit else document.pages
            )
            for page in selected_pages:
                raw_tables = page.extract_tables() or []
                page_tables = []
                for raw in raw_tables:
                    rows = tuple(
                        tuple((cell or "").strip() for cell in row)
                        for row in raw
                        if row
                    )
                    if rows:
                        page_tables.append(rows)
                tables.append(tuple(page_tables))
                if not pages:
                    pages = len(selected_pages)
    except Exception as exc:  # noqa: BLE001
        if not error:
            error = f"pdfplumber: {type(exc).__name__}: {exc}"

    ocr_used = False
    ocr_available = False
    ocr_lang = ""
    ocr_quality = "text"
    needs_review = False

    if ocr and len(re.sub(r"\W", "", text)) < 80:
        ocr_pages = (
            min(max(1, int(max_ocr_pages)), page_limit)
            if page_limit else max(1, int(max_ocr_pages))
        )
        ocr_text, ocr_available, ocr_lang, ocr_quality = _ocr_pdf(
            body,
            max_pages=ocr_pages,
            ocr_langs=ocr_langs,
            tessdata_dir=tessdata_dir,
        )
        if ocr_text:
            text = ocr_text
            ocr_used = True
            needs_review = True
        elif len(re.sub(r"\W", "", text)) < 80:
            needs_review = True

    return PdfDoc(
        pages=pages,
        text=text,
        tables=tuple(tables),
        ocr=ocr_used,
        ocr_available=ocr_available,
        ocr_lang=ocr_lang,
        ocr_quality=ocr_quality,
        needs_review=needs_review,
        error=error,
    )
