#!/usr/bin/env python3
"""Run today's voice guard against the split DSPMU prompt and entity corpora.

The prompt corpus is ``core_cards.md`` + ``site_map.md`` + ``recent_notices.md``.
The full programme entity corpus is ``voice_kb.md`` and is used only by
``EntityIndex`` — it is not loaded into the prompt. This is the acceptance check
for the prompt split: the guard must accept the common DSPMU facts in the
prompt, and reject a wrong fee, an invented programme and a student roll number.
No LLM call is made.
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

PROMPT_FILES = ("core_cards.md", "site_map.md", "recent_notices.md")
VOICE_KB = "voice_kb.md"

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
        "--dir",
        default=str(ROOT / "content/dspmu"),
        help="directory holding the generated prompt and entity files",
    )
    parser.add_argument(
        "--voice-kb",
        default=VOICE_KB,
        help="entity corpus filename (relative to --dir)",
    )
    args = parser.parse_args(argv)

    base = Path(args.dir)
    missing = [name for name in (*PROMPT_FILES, args.voice_kb) if not (base / name).exists()]
    if missing:
        print(
            f"missing {', '.join(missing)} in {base}; run scripts/build_site_index.py first",
            file=sys.stderr,
        )
        return 1

    context = "\n\n".join((base / name).read_text(encoding="utf-8") for name in PROMPT_FILES)
    voice_kb = (base / args.voice_kb).read_text(encoding="utf-8")
    index = EntityIndex(voice_kb)

    failures = 0
    print(f"prompt corpus: {', '.join(PROMPT_FILES)} ({len(context):,} chars)")
    print(
        f"entity corpus: {args.voice_kb} ({len(voice_kb):,} chars, "
        f"{len(index.names)} entities; not in prompt)"
    )
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
