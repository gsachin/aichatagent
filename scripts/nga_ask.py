#!/usr/bin/env python3
"""Plan, execute and assemble evidence for one question (Phase 4 demo).

This does not generate an answer and does not call an LLM. It shows exactly
what the future answer path will receive: the plan, bounded tool calls,
verified facts and page citations.

Usage:
    .venv/bin/python scripts/nga_ask.py --query "What is the BA History fee?"
    .venv/bin/python scripts/nga_ask.py --query "latest notice" --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.evidence import (  # noqa: E402
    assemble_evidence,
    render_citations,
    render_evidence_block,
)
from app.agent.executor import execute_plan  # noqa: E402
from app.agent.facts import FactStore  # noqa: E402
from app.agent.planner import Planner  # noqa: E402
from app.config import settings  # noqa: E402
from app.sitegraph.tools import SiteToolServer  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, execute and assemble evidence for one question."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--query", required=True)
    parser.add_argument("--deadline-ms", type=int, default=8000)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=3000)
    guard = parser.add_mutually_exclusive_group()
    guard.add_argument(
        "--pii-guard",
        dest="pii_guard",
        action="store_true",
        help="restore the conservative PII exclusions for this demo",
    )
    guard.add_argument(
        "--no-pii-guard",
        dest="pii_guard",
        action="store_false",
        help="use the published pages without PII exclusions",
    )
    parser.set_defaults(pii_guard=settings.NGA_PII_GUARD_ENABLED)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = SiteToolServer(args.db, pii_guard=args.pii_guard)
    plan = Planner().plan(args.query)
    execution = execute_plan(
        server,
        plan,
        max_steps=args.max_steps,
        deadline_ms=args.deadline_ms,
    )
    bundle = assemble_evidence(
        execution,
        query=args.query,
        facts=FactStore(),
        max_chars=args.max_chars,
        pii_guard=args.pii_guard,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "plan": {
                        "intent": plan.intent.value,
                        "steps": [
                            {"tool": step.tool, "args": step.args}
                            for step in plan.steps
                        ],
                    },
                    "execution": execution.to_dict(),
                    "evidence": bundle.to_dict(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"question: {args.query}")
    print(
        "plan: "
        + plan.intent.value
        + " -> "
        + ", ".join(step.tool for step in plan.steps)
    )
    print(
        f"execution: {execution.status} "
        f"({execution.steps_used} steps, {execution.elapsed_ms:.1f} ms)"
    )
    if execution.errors:
        print("errors: " + json.dumps(execution.errors, ensure_ascii=False))
    if bundle.notes:
        print("notes: " + ", ".join(bundle.notes))
    print()
    print(render_evidence_block(bundle) or "(no evidence)")
    print()
    print(render_citations(bundle) or "(no citations)")
    return 0 if execution.status in ("ok", "degraded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
