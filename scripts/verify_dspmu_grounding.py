#!/usr/bin/env python3
"""Run today's voice guard against the generated DSPMU knowledge file.

This is milestone one's acceptance check: the guard must accept a correct DSPMU
fee and date, and reject a wrong fee, an invented programme and a student roll
number. It uses the same ``EntityIndex`` and ``is_grounded`` code the live voice
boundary uses; no LLM call is made.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.kb_entities import EntityIndex  # noqa: E402
from app.voice_grounding import is_grounded, ungrounded_claims  # noqa: E402

CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "grounded political-science fee",
        "The annual fee for Bachelor of Arts in Political Science is ₹3,410 for the general category.",
        True,
    ),
    (
        "grounded reserved-category fee",
        "The annual fee for Bachelor of Arts in Political Science is ₹3,185 for ST/SC/BC I and II.",
        True,
    ),
    (
        "wrong political-science fee",
        "The annual fee for Bachelor of Arts in Political Science is ₹14,600.",
        False,
    ),
    (
        "grounded history fee",
        "The annual fee for Bachelor of Arts in History is ₹3,410.",
        True,
    ),
    (
        "invented programme",
        "The university offers a Master of Mechanical Engineering.",
        False,
    ),
    (
        "grounded PG date",
        "The last date of application for postgraduate admission is 25 October 2026.",
        True,
    ),
    (
        # Pure numeric on purpose: the number-claim regex deliberately ignores
        # digits attached to letters (e.g. 23B22001). PII is kept out of the
        # prompt at the build stage by excluding the admission/result categories.
        "numeric roll number",
        "The selected candidate's roll number is 22001.",
        False,
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge",
        default=str(ROOT / "content/dspmu/site_map.md"),
        help="generated site_map.md used as both context and entity corpus",
    )
    args = parser.parse_args(argv)

    path = Path(args.knowledge)
    if not path.exists():
        print(f"missing {path}; run scripts/build_site_index.py first", file=sys.stderr)
        return 1
    context = path.read_text(encoding="utf-8")
    index = EntityIndex(context)

    failures = 0
    print(f"knowledge: {path} ({len(context):,} chars, {len(index.names)} entities)")
    for name, claim, expected in CASES:
        actual = is_grounded(claim, context, index)
        status = "PASS" if actual == expected else "FAIL"
        if actual != expected:
            failures += 1
        claims = ungrounded_claims(claim, context, index)
        detail = "" if not claims else f" ungrounded={claims}"
        print(f"{status:4s}  {name:34s} expected={expected!s:5s} actual={actual!s:5s}{detail}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
