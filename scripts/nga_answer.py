#!/usr/bin/env python3
"""Plan → execute → evidence → generate → verify for one question.

This is a controlled demo, not the channel answer path. ``--dry-run`` prints
the exact messages that would be sent to the LLM without making a network
call. The normal mode calls the configured LLM and returns only claims that
pass the verifier.

Usage:
    .venv/bin/python scripts/nga_answer.py --query "What is the BA History fee?" --dry-run
    .venv/bin/python scripts/nga_answer.py --query "complaint status" --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.answer import (  # noqa: E402
    answer_from_execution,
    build_messages,
    render_answer,
)
from app.agent.evidence import assemble_evidence  # noqa: E402
from app.agent.executor import execute_plan  # noqa: E402
from app.agent.facts import FactStore  # noqa: E402
from app.agent.planner import Planner  # noqa: E402
from app.config import settings  # noqa: E402
from app.sitegraph.tools import SiteToolServer  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, execute, assemble evidence and verify an answer."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--query", required=True)
    parser.add_argument("--deadline-ms", type=int, default=8000)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=3000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    guard = parser.add_mutually_exclusive_group()
    guard.add_argument("--pii-guard", dest="pii_guard", action="store_true")
    guard.add_argument("--no-pii-guard", dest="pii_guard", action="store_false")
    parser.set_defaults(pii_guard=settings.NGA_PII_GUARD_ENABLED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = SiteToolServer(args.db, pii_guard=args.pii_guard)
    plan = Planner().plan(args.query)
    execution = execute_plan(
        server, plan, max_steps=args.max_steps, deadline_ms=args.deadline_ms
    )
    facts = FactStore()
    bundle = assemble_evidence(
        execution,
        query=args.query,
        facts=facts,
        max_chars=args.max_chars,
        pii_guard=args.pii_guard,
    )

    if args.dry_run:
        messages = build_messages(args.query, bundle)
        payload = {
            "plan": {
                "intent": plan.intent.value,
                "steps": [
                    {"tool": step.tool, "args": step.args} for step in plan.steps
                ],
            },
            "execution": execution.to_dict(),
            "evidence": bundle.to_dict(),
            "messages": messages,
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"question: {args.query}")
            print(
                "plan: "
                + plan.intent.value
                + " -> "
                + ", ".join(step.tool for step in plan.steps)
            )
            print(
                f"execution: {execution.status} "
                f"({execution.steps_used} steps)"
            )
            print("\n--- system ---\n" + messages[0]["content"])
            print("\n--- user ---\n" + messages[1]["content"])
        return 0

    result = answer_from_execution(
        execution,
        query=args.query,
        facts=facts,
        pii_guard=args.pii_guard,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"question: {args.query}")
        print(f"status:   {result.status}")
        rendered = render_answer(result)
        print()
        print(rendered or "(no verified answer)")
        if result.verification is not None:
            print("\nverifier failures:")
            if result.verification.failures:
                for failure in result.verification.failures:
                    print(
                        f"  - {failure['kind']}: {failure['raw']} "
                        f"({failure['reason']})"
                    )
            else:
                print("  none")
    return 0 if result.status in ("verified", "rewritten", "no_evidence") else 1


if __name__ == "__main__":
    raise SystemExit(main())
