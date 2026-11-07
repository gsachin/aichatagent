"""The DSPMU source-side handoff stays public, exact and reproducible."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from app.notice_store import is_likely_pii_title
from app.pii import scan_text

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "content" / "dspmu" / "source_fixes"
FACTS = ROOT / "content" / "dspmu" / "facts.json"
PHONE = "+91 930 455 261"


def _feed() -> dict:
    return json.loads((SOURCE / "notices.json").read_text(encoding="utf-8"))


def _redirects() -> list[dict[str, str]]:
    with (SOURCE / "redirects_non_pii.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _run_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_source_fixes.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_generated_files_are_current():
    proc = _run_check()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "source-fix files are current (9 files)" in proc.stdout


def test_feed_is_public_and_uses_canonical_urls():
    feed = _feed()
    items = feed["items"]

    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    assert feed["feed_url"] == "https://dspmuranchi.ac.in/notices.json"
    assert len(items) == 1520
    assert len({item["id"] for item in items}) == len(items)

    for item in items:
        assert not is_likely_pii_title(item["title"]), item["title"]
        assert not scan_text(item["title"]).flagged, item["title"]
        assert item["_dspmu"]["pii"] is False
        assert item["_dspmu"]["scope"] in {"current_archive", "needs_review"}
        assert item["url"].startswith("https://dspmuranchi.ac.in/pdf/")
        assert item["_dspmu"]["legacy_url"].startswith(
            "https://oldsite.dspmuranchi.ac.in/pdf/News/"
        )
        assert item["date_published"].endswith("+05:30")


def test_redirect_csv_is_exact_and_matches_the_feed():
    rows = _redirects()
    items = _feed()["items"]

    assert len(rows) == len(items) == 1520
    assert len({row["source_url"] for row in rows}) == len(rows)
    assert {row["source_url"] for row in rows} == {item["url"] for item in items}

    scopes = [row["scope"] for row in rows]
    assert scopes.count("current_archive") == 1512
    assert scopes.count("needs_review") == 8
    feed_scope = {item["url"]: item["_dspmu"]["scope"] for item in items}
    assert all(row["scope"] == feed_scope[row["source_url"]] for row in rows)
    for row in rows:
        source = urlsplit(row["source_url"])
        target = urlsplit(row["target_url"])
        assert source.netloc == "dspmuranchi.ac.in"
        assert source.path.startswith("/pdf/")
        assert target.netloc == "oldsite.dspmuranchi.ac.in"
        assert target.path.startswith("/pdf/News/")
        assert row["status_code"] == "301"
        assert row["notice_id"].startswith("notice-")
        assert row["scope"] in {"current_archive", "needs_review"}


def test_llms_and_cms_pages_are_ready_for_review():
    llms = (SOURCE / "llms.txt").read_text(encoding="utf-8")
    fee = (SOURCE / "cms" / "fee-structure.md").read_text(encoding="utf-8")
    exams = (SOURCE / "cms" / "examination-notices.md").read_text(encoding="utf-8")
    request = (SOURCE / "university-request.md").read_text(encoding="utf-8")
    facts = json.loads(FACTS.read_text(encoding="utf-8"))

    assert PHONE in llms
    assert "registrar@dspmuranchi.ac.in" in llms
    assert "https://dspmuranchi.ac.in/notices.json" in llms
    assert "TODO" not in llms
    assert PHONE in fee
    assert PHONE in request
    assert "+91 930 455 2610" not in request
    compact_fee = " ".join(fee.split())
    for row in facts["fees"]:
        assert " ".join(row["programme"].split()) in compact_fee
    assert "43" in fee
    assert "Examination Notices" in exams
    assert "https://dspmuranchi.ac.in/notices.json" in exams
    assert exams.count("https://dspmuranchi.ac.in/pdf/") == 40
    assert "merit" in exams.lower()
