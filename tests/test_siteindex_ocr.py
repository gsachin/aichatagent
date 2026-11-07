"""Hindi OCR language selection and command wiring."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.siteindex import extract


def test_requested_languages_default_to_english_then_hindi(monkeypatch):
    monkeypatch.delenv("TESSERACT_OCR_LANGS", raising=False)
    assert extract.requested_ocr_langs() == ("eng", "hin")
    assert extract.requested_ocr_langs(("hin",)) == ("hin",)


def test_requested_languages_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TESSERACT_OCR_LANGS", "hin,eng")
    assert extract.requested_ocr_langs() == ("hin", "eng")


def test_installed_languages_are_selected_in_requested_order():
    available = frozenset({"eng", "hin", "osd", "snum"})
    assert extract.select_ocr_langs(available, ("eng", "hin")) == ("eng", "hin")
    assert extract.select_ocr_langs(available, ("hin", "eng")) == ("hin", "eng")
    assert extract.select_ocr_langs(available, ("eng",)) == ("eng",)


def test_missing_hindi_falls_back_to_english():
    assert extract.select_ocr_langs(frozenset({"eng"}), ("eng", "hin")) == ("eng",)


def test_no_installed_language_means_ocr_is_unavailable():
    assert extract.select_ocr_langs(frozenset(), ("eng", "hin")) == ()


def test_ocr_image_uses_lstm_and_the_configured_tessdata(monkeypatch, tmp_path):
    captured = {}

    class Result:
        returncode = 0
        stdout = "परीक्षा सूचना"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    text = extract._ocr_image(
        "/usr/local/bin/tesseract",
        tmp_path / "page.png",
        "eng+hin",
        tessdata_dir="/repo/.tessdata",
    )

    assert text == "परीक्षा सूचना"
    command = captured["command"]
    assert "--oem" in command
    assert command[command.index("--oem") + 1] == "1"
    assert "--tessdata-dir" in command
    assert command[command.index("--tessdata-dir") + 1] == "/repo/.tessdata"
    assert command[-2:] == ["-l", "eng+hin"]


def test_the_local_tessdata_directory_is_preferred(monkeypatch, tmp_path):
    local = tmp_path / ".tessdata"
    local.mkdir()
    monkeypatch.delenv("TESSERACT_TESSDATA_DIR", raising=False)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(extract, "LOCAL_TESSDATA_DIR", local)

    assert extract.default_tessdata_dir() == str(local)


def test_environment_tessdata_overrides_the_repo_default(monkeypatch, tmp_path):
    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.setenv("TESSERACT_TESSDATA_DIR", str(configured))
    monkeypatch.setattr(extract, "LOCAL_TESSDATA_DIR", tmp_path / "missing")

    assert extract.default_tessdata_dir() == str(configured)


def test_actual_hindi_ocr_when_the_model_and_font_are_available():
    tesseract = shutil.which("tesseract")
    if not tesseract:
        pytest.skip("tesseract is not installed")
    tessdata = extract.default_tessdata_dir()
    if "hin" not in extract._tesseract_langs(tessdata):
        pytest.skip("Hindi traineddata is not installed")

    font_path = Path(
        "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"
    )
    if not font_path.is_file():
        pytest.skip("a Devanagari test font is not available")

    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw, ImageFont

    image_path = Path.cwd() / ".siteindex-ocr-hindi-test.png"
    try:
        image = Image.new("RGB", (1000, 280), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 64)
        draw.text((30, 40), "कुलपति कार्यालय सूचना", font=font, fill="black")
        draw.text((30, 150), "हिंदी में परीक्षा सूचना", font=font, fill="black")
        image.save(image_path)

        text = extract._ocr_image(
            tesseract,
            image_path,
            "hin",
            tessdata_dir=tessdata,
        )
    finally:
        image_path.unlink(missing_ok=True)

    assert text
    assert any("ऀ" <= char <= "ॿ" for char in text)
    assert "सूचना" in text


def test_extract_pdf_runs_hindi_ocr_on_a_scanned_page():
    tesseract = shutil.which("tesseract")
    if not tesseract:
        pytest.skip("tesseract is not installed")
    tessdata = extract.default_tessdata_dir()
    if "hin" not in extract._tesseract_langs(tessdata):
        pytest.skip("Hindi traineddata is not installed")

    font_path = Path(
        "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"
    )
    if not font_path.is_file():
        pytest.skip("a Devanagari test font is not available")

    fpdf = pytest.importorskip("fpdf")
    from PIL import Image, ImageDraw, ImageFont

    image_path = Path.cwd() / ".siteindex-ocr-hindi-page.png"
    pdf_path = Path.cwd() / ".siteindex-ocr-hindi-page.pdf"
    try:
        image = Image.new("RGB", (1200, 400), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 72)
        draw.text((40, 80), "कुलपति कार्यालय सूचना", font=font, fill="black")
        draw.text((40, 220), "हिंदी में परीक्षा सूचना", font=font, fill="black")
        image.save(image_path)

        pdf = fpdf.FPDF()
        pdf.add_page()
        pdf.image(str(image_path), x=10, y=10, w=190)
        pdf_path.write_bytes(bytes(pdf.output()))

        doc = extract.extract_pdf(
            pdf_path.read_bytes(),
            ocr=True,
            max_ocr_pages=1,
            ocr_langs=("hin",),
            tessdata_dir=tessdata,
        )
    finally:
        image_path.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)

    assert doc.ocr is True
    assert doc.ocr_lang == "hin"
    assert "सूचना" in doc.text
