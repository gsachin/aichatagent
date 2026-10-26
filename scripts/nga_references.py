#!/usr/bin/env python3
"""Inspect and purge the voice→WhatsApp reference delivery log (Phase 12).

The log stores only a phone hash and the last four digits, plus the exact
reference node/url and the delivery status. It never stores the message body.

Usage:
    .venv/bin/python scripts/nga_references.py --status
    .venv/bin/python scripts/nga_references.py --list --limit 20
    .venv/bin/python scripts/nga_references.py --purge
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.sitegraph.metrics import iso_before, utc_now  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or purge the reference delivery log (Phase 12)."
    )
    parser.add_argument("--db", default=settings.NGA_GRAPH_SQLITE_PATH)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--days", type=int, default=settings.NGA_REFERENCE_LOG_RETENTION_DAYS
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _status(store: GraphStore, *, as_json: bool) -> int:
    now = utc_now()
    payload = {
        "lifetime": store.reference_delivery_summary(),
        "last_24h": store.reference_delivery_summary(
            since_iso=iso_before(now, 86_400)
        ),
        "enabled": settings.NGA_REFERENCE_DELIVERY_ENABLED,
        "mode": settings.NGA_REFERENCE_DELIVERY_MODE,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return 0
    lifetime = payload["lifetime"]
    recent = payload["last_24h"]
    print("NGA reference delivery")
    print(
        f"  delivery:      {'on' if payload['enabled'] else 'off'} "
        f"(mode {payload['mode']})"
    )
    print(
        "  lifetime:      "
        f"{lifetime['sent']} sent, {lifetime['failed']} failed, "
        f"{lifetime['template_required']} template, "
        f"{lifetime['skipped']} skipped"
    )
    print(
        "  last 24h:      "
        f"{recent['sent']} sent, {recent['failed']} failed, "
        f"{recent['template_required']} template, "
        f"{recent['skipped']} skipped"
    )
    return 0


def _list(store: GraphStore, limit: int, *, as_json: bool) -> int:
    rows = store.reference_deliveries(limit=limit)
    if as_json:
        print(json.dumps({"rows": rows, "total": len(rows)}, indent=2))
        return 0
    print(f"reference deliveries: {len(rows)}")
    for row in rows:
        print(
            f"  {row['created_at']}  {row['status']:<18} "
            f"phone=***{row['phone_last4']} call={row['call_sid'] or '-'} "
            f"node={row['node_id'] or '-'} reason={row['reason'] or '-'}"
        )
    return 0


def _purge(store: GraphStore, days: int, *, as_json: bool) -> int:
    cutoff = iso_before(utc_now(), max(1, int(days)) * 86_400)
    removed = store.purge_reference_deliveries(cutoff)
    if as_json:
        print(json.dumps({"removed": removed, "before": cutoff}, indent=2))
    else:
        print(f"purged {removed} reference delivery row(s) before {cutoff}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = GraphStore(args.db)
    if args.purge:
        return _purge(store, args.days, as_json=args.json)
    if args.list:
        return _list(store, max(1, args.limit), as_json=args.json)
    return _status(store, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
