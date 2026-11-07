"""DSPMU sign-off packet and decision recorder."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
SIGNOFF = EVAL / "signoff_packet.py"
GOLDEN = EVAL / "dspmu_golden_set.jsonl"
KB = EVAL / "dspmu_kb.md"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SIGNOFF), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _base_args() -> list[str]:
    return [
        "--golden",
        str(GOLDEN),
        "--kb",
        str(KB),
        "--kb-label",
        "dspmu_kb.md",
    ]


def test_status_reports_the_dspmu_counts():
    proc = _run(*_base_args(), "--status")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "golden set        : 103 cases" in proc.stdout
    assert "verified          : 27" in proc.stdout
    assert "pending           : 76" in proc.stdout


def test_packet_is_written_to_the_requested_path(tmp_path):
    packet = tmp_path / "DSPMU_SIGNOFF_PACKET.md"
    proc = _run(
        *_base_args(),
        "--packet",
        str(packet),
        "--packet-title",
        "DSPMU test sign-off packet",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert packet.is_file()
    text = packet.read_text(encoding="utf-8")
    assert text.startswith("# DSPMU test sign-off packet")
    assert "76 cases await your decision" in text
    assert "fee-gen-001" in text
    assert "Policy decisions" in text


def test_approve_and_reject_record_decisions_without_invalid_states(tmp_path):
    golden = tmp_path / "dspmu_golden_set.jsonl"
    golden.write_bytes(GOLDEN.read_bytes())
    packet = tmp_path / "packet.md"
    common = [
        "--golden",
        str(golden),
        "--kb",
        str(KB),
        "--kb-label",
        "dspmu_kb.md",
        "--packet",
        str(packet),
    ]

    approved = _run(*common, "--approve", "fee-gen-001", "--by", "Test Reviewer")
    assert approved.returncode == 0, approved.stdout + approved.stderr

    rejected = _run(
        *common,
        "--reject",
        "fee-gen-002",
        "--note",
        "needs a newer fee source",
    )
    assert rejected.returncode == 0, rejected.stdout + rejected.stderr

    rows = [
        json.loads(line)
        for line in golden.read_text(encoding="utf-8").splitlines()
    ]
    by_id = {row["case_id"]: row for row in rows}
    assert by_id["fee-gen-001"]["ground_truth_status"] == "PO_APPROVED"
    assert by_id["fee-gen-001"]["approved_by"] == "Test Reviewer"
    assert by_id["fee-gen-002"]["ground_truth_status"] == "PENDING_PO_SIGNOFF"
    assert by_id["fee-gen-002"]["rejection_note"] == "needs a newer fee source"
