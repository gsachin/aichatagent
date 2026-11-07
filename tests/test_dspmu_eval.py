"""The DSPMU draft golden set stays well-formed and traceable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"


def test_dspmu_golden_set_passes_fixture_validation():
    proc = subprocess.run(
        [
            sys.executable,
            str(EVAL / "checks.py"),
            "--golden",
            str(EVAL / "dspmu_golden_set.jsonl"),
            "--heldout",
            str(EVAL / "dspmu_heldout.txt"),
            "--kb",
            str(EVAL / "dspmu_kb.md"),
            "--kb-label",
            "dspmu_kb.md",
            "--no-self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert "cases           : 103" in output
    assert "every case is well-formed" in output
    assert "dspmu_heldout.txt: 20 cases of 103" in output
    assert "BLOCKED (ground truth PENDING PO SIGN-OFF)" in output
