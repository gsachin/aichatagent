#!/usr/bin/env python3
"""Measure the SQLite NGA turn-event write on the chat thread.

This is the write that `app/agent/text.py` performs after a cited agentic
answer. It is deliberately synchronous so the next turn sees the previous
turn; the benchmark answers whether that is inside the chat budget.

Usage:
    .venv/bin/python scripts/bench_nga_events.py --iterations 200
    .venv/bin/python scripts/bench_nga_events.py --iterations 200 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.answer import AnswerResult
from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.executor import ExecutionResult
from app.agent.models import Intent, Plan, PlanStep
from app.agent.session import ConversationMemory
from app.sitegraph.metrics import utc_now
from app.sitegraph.store import GraphStore


def _synthetic_turn():
    plan = Plan(
        intent=Intent.PAGE_TOPIC,
        steps=(PlanStep("site.search", {"query": "hostel", "limit": 3}),),
    )
    execution = ExecutionResult(
        plan=plan,
        results=[{"tool": "site.search", "status": "ok", "data": {}}],
        steps_used=1,
    )
    item = EvidenceItem(
        evidence_id="ev-1",
        node_id="bench.node",
        title="Hostel",
        url="https://dspmuranchi.ac.in/cms/hostel",
        authority="official_page",
        verified_at=utc_now(),
        snippet="Boys and girls hostels with a warden office.",
        text="Boys and girls hostels with a warden office.",
    )
    bundle = EvidenceBundle(
        query="hostel", intent=Intent.PAGE_TOPIC, items=[item]
    )
    result = AnswerResult(
        query="hostel",
        answer="The hostel has a warden office.",
        status="verified",
        citations=[
            {
                "node_id": item.node_id,
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "authority": item.authority,
            }
        ],
        evidence=bundle,
        plan=plan,
        execution=execution,
    )
    return plan, execution, bundle, result


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100)
    return ordered[max(0, min(len(ordered) - 1, index))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the NGA turn-event write."
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    iterations = max(1, int(args.iterations))

    plan, execution, bundle, result = _synthetic_turn()
    with tempfile.TemporaryDirectory(prefix="nga-event-bench-") as directory:
        store = GraphStore(Path(directory) / "bench.sqlite3")
        memory = ConversationMemory(store, channel="bench")
        times: list[float] = []
        for index in range(iterations):
            started = time.perf_counter()
            memory.record(
                "bench-conversation",
                f"question {index}",
                plan=plan,
                execution=execution,
                bundle=bundle,
                answer_result=result,
                channel="bench",
            )
            times.append((time.perf_counter() - started) * 1000)

    payload = {
        "iterations": iterations,
        "summary": {
            "p50_ms": round(statistics.median(times), 3),
            "p95_ms": round(_percentile(times, 95), 3),
            "p99_ms": round(_percentile(times, 99), 3),
            "mean_ms": round(statistics.fmean(times), 3),
            "max_ms": round(max(times), 3),
            "writes_per_second": round(1000 / statistics.fmean(times), 1),
        },
        "chat_budget_ms": 8000,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    summary = payload["summary"]
    print("NGA turn-event write benchmark")
    print(f"  iterations:        {iterations}")
    print(f"  p50:               {summary['p50_ms']} ms")
    print(f"  p95:               {summary['p95_ms']} ms")
    print(f"  p99:               {summary['p99_ms']} ms")
    print(f"  mean:              {summary['mean_ms']} ms")
    print(f"  max:               {summary['max_ms']} ms")
    print(f"  throughput:        {summary['writes_per_second']} writes/s")
    print("  chat budget:       8000 ms (text turn)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
