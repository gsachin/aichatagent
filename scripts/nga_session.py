#!/usr/bin/env python3
"""Conversation memory demo: plan → execute → evidence → record.

The conversation ID is durable in ``nga_turn_event``. A second turn with the
same ID sees the previous topic and resolves follow-ups such as "what about its
fee?". ``--dry-run`` records the event without calling the LLM, so follow-up
behaviour can be inspected offline.

Usage:
    .venv/bin/python scripts/nga_session.py \
        --conversation-id demo-1 --query "hostel" --dry-run

    .venv/bin/python scripts/nga_session.py \
        --conversation-id demo-1 --query "what about its fee?" --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.answer import answer_from_execution  # noqa: E402
from app.agent.evidence import (  # noqa: E402
    assemble_evidence,
    render_citations,
)
from app.agent.executor import execute_plan  # noqa: E402
from app.agent.facts import FactStore  # noqa: E402
from app.agent.session import ConversationMemory  # noqa: E402
from app.config import settings  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402
from app.sitegraph.tools import SiteToolServer  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Conversation memory and follow-up planning demo."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--conversation-id", default="demo-session")
    parser.add_argument("--query", required=True)
    parser.add_argument("--channel", default="chat")
    parser.add_argument("--language", default=None)
    parser.add_argument("--deadline-ms", type=int, default=8000)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--json", action="store_true")
    guard = parser.add_mutually_exclusive_group()
    guard.add_argument("--pii-guard", dest="pii_guard", action="store_true")
    guard.add_argument("--no-pii-guard", dest="pii_guard", action="store_false")
    parser.set_defaults(pii_guard=settings.NGA_PII_GUARD_ENABLED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = GraphStore(args.db)
    if args.reset:
        store.delete_turn_events(args.conversation_id)
    memory = ConversationMemory(
        store,
        channel=args.channel,
        history_limit=settings.NGA_SESSION_HISTORY_TURNS,
    )
    state, plan = memory.plan(
        args.conversation_id,
        args.query,
        channel=args.channel,
        language=args.language,
    )
    server = SiteToolServer(args.db, pii_guard=args.pii_guard)
    execution = execute_plan(
        server, plan, max_steps=args.max_steps, deadline_ms=args.deadline_ms
    )
    bundle = assemble_evidence(
        execution,
        query=args.query,
        facts=FactStore(),
        pii_guard=args.pii_guard,
    )

    result = None
    if not args.dry_run:
        result = answer_from_execution(
            execution,
            query=args.query,
            pii_guard=args.pii_guard,
            language=args.language,
        )

    event = memory.record(
        args.conversation_id,
        args.query,
        plan=plan,
        execution=execution,
        bundle=bundle,
        answer_result=result,
        channel=args.channel,
        language=args.language,
        answer_status="dry_run" if args.dry_run else None,
    )
    after = memory.load(
        args.conversation_id, channel=args.channel, language=args.language
    )

    payload = {
        "conversation_id": args.conversation_id,
        "turn_id": event.turn_id,
        "state_before": {
            "current_node_id": state.current_node_id,
            "current_node_title": state.current_node_title,
            "programme": state.programme,
            "language": state.language,
            "recent_turns": state.recent_turns,
        },
        "plan": {
            "intent": plan.intent.value,
            "steps": [
                {"tool": step.tool, "args": step.args} for step in plan.steps
            ],
        },
        "execution": execution.to_dict(),
        "evidence": bundle.to_dict(),
        "answer_result": result.to_dict() if result else None,
        "session_after": {
            "current_node_id": after.current_node_id,
            "current_node_title": after.current_node_title,
            "programme": after.programme,
            "language": after.language,
            "recent_turns": after.recent_turns,
        },
        "event": event.to_dict(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"conversation: {args.conversation_id}  turn: {event.turn_id}")
    print(
        "state before: "
        f"topic={state.current_node_title or '-'} "
        f"programme={state.programme or '-'} "
        f"language={state.language or '-'}"
    )
    print(
        "plan: "
        + plan.intent.value
        + " -> "
        + ", ".join(step.tool for step in plan.steps)
    )
    print(f"execution: {execution.status} ({execution.steps_used} steps)")
    if result is not None:
        print(f"answer status: {result.status}")
        print(result.answer or "(no verified answer)")
        citations = render_citations(bundle)
        if citations:
            print()
            print(citations)
    else:
        print("dry run: no LLM call; the turn was recorded from evidence.")
    print(
        "state after: "
        f"topic={after.current_node_title or '-'} "
        f"programme={after.programme or '-'} "
        f"language={after.language or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
