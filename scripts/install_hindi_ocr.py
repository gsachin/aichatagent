#!/usr/bin/env python3
"""Install Hindi Tesseract data into the repo-local ``.tessdata`` directory.

The runtime does not download models. This is a one-time development/deployment
step:

* downloads ``hin.traineddata`` from tesseract-ocr/tessdata_best (or _fast)
* links/copies the system English/OSD data beside it
* verifies that Tesseract can see ``hin``

The directory is gitignored (the model is ~12 MB); the extractor automatically
uses it when present, or ``TESSERACT_TESSDATA_DIR`` can point elsewhere.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / ".tessdata"
BASES = {
    "best": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/",
    "fast": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/",
}
SYSTEM_DATA = ("eng.traineddata", "osd.traineddata", "snum.traineddata")


def _system_tessdata() -> Path | None:
    binary = shutil.which("tesseract")
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'in "([^"]+)"', proc.stdout or "")
    return Path(match.group(1)) if match else None


def _link_or_copy(source: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    try:
        target.symlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _download(url: str, target: Path) -> int:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DSPMU-SiteIndex/0.1 (OCR setup)"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as handle:
            temp = Path(handle.name)
            try:
                shutil.copyfileobj(response, handle)
            except Exception:
                handle.close()
                temp.unlink(missing_ok=True)
                raise
    size = temp.stat().st_size
    if size < 1_000_000:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded Hindi data is only {size} bytes")
    temp.replace(target)
    return size


def _available(dest: Path) -> str:
    binary = shutil.which("tesseract")
    if not binary:
        return "(tesseract not found)"
    proc = subprocess.run(
        [binary, "--tessdata-dir", str(dest), "--list-langs"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (proc.stdout or proc.stderr or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--quality", choices=("best", "fast"), default="best")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    hindi = dest / "hin.traineddata"

    system = _system_tessdata()
    if system and system.is_dir():
        for name in SYSTEM_DATA:
            source = system / name
            target = dest / name
            if source.is_file() and (args.force or not target.exists()):
                _link_or_copy(source, target)

    if args.check:
        print(_available(dest))
        return 0 if hindi.is_file() else 1

    if hindi.is_file() and not args.force:
        print(f"Hindi data already present: {hindi} ({hindi.stat().st_size:,} bytes)")
    else:
        size = _download(BASES[args.quality] + "hin.traineddata", hindi)
        print(f"downloaded {args.quality} Hindi data: {hindi} ({size:,} bytes)")

    english = dest / "eng.traineddata"
    if not english.exists():
        size = _download(BASES[args.quality] + "eng.traineddata", english)
        print(f"downloaded {args.quality} English data: {english} ({size:,} bytes)")

    listing = _available(dest)
    print(listing)
    if "hin" not in listing.split():
        print("tesseract does not see hin.traineddata", file=sys.stderr)
        return 1
    print(f"Use: TESSERACT_TESSDATA_DIR={dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
