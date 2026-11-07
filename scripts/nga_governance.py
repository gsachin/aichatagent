#!/usr/bin/env python3
"""Release governance gate for the NGA content and product decisions.

The gate is intentionally read-only. It reports the decisions that are still
unsigned and the content that is being served without a named reviewer or an
approved legacy allowlist. It never changes a status or removes content; those
are PO/TPO decisions recorded in ``content/dspmu/governance.json``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402

DEFAULT_REGISTER = "content/dspmu/governance.json"


def load_register(path: str | Path) -> dict:
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def catalogue_rows(payload: dict) -> list[dict]:
    rows = payload.get("programmes") or payload.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def catalogue_audit(payload: dict) -> dict:
    rows = catalogue_rows(payload)
    by_status = Counter(str(row.get("status") or "unknown") for row in rows)
    missing = [
        str(row.get("id") or row.get("name") or "?")
        for row in rows
        if not str(row.get("reviewed_by") or "").strip()
    ]
    return {
        "rows": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "missing_reviewer": len(missing),
        "missing_reviewer_ids": missing,
    }


def legacy_audit(
    rows: list[dict],
    *,
    allowed_hosts: tuple[str, ...],
    allowlist: list[str],
) -> dict:
    approved = {str(item) for item in allowlist if str(item).strip()}
    nodes: list[dict] = []
    for row in rows:
        url = str(row.get("url") or "")
        host = (urlparse(url).hostname or "").lower()
        node_id = str(row.get("node_id") or "")
        is_approved = node_id in approved or url in approved
        nodes.append(
            {
                "node_id": node_id,
                "title": str(row.get("title") or ""),
                "url": url,
                "status": str(row.get("status") or ""),
                "host_allowlisted": host in set(allowed_hosts),
                "approved": is_approved,
            }
        )
    unapproved = [
        node for node in nodes
        if node["status"] == "ok" and not node["approved"]
    ]
    return {
        "count": len(nodes),
        "approved": sum(1 for node in nodes if node["approved"]),
        "host_allowlisted": sum(
            1 for node in nodes if node["host_allowlisted"]
        ),
        "unapproved": len(unapproved),
        "nodes": nodes,
    }


def decision_signed(register: dict, key: str) -> bool:
    decision = (register.get("decisions") or {}).get(key) or {}
    return str(decision.get("status") or "").lower() == "signed"


def evaluate_blockers(
    *,
    register: dict,
    catalogue: dict,
    legacy: dict,
    catalogue_enabled: bool,
    include_draft: bool,
    pg_lists_enabled: bool,
) -> list[dict]:
    blockers: list[dict] = []
    if pg_lists_enabled and not decision_signed(register, "D4"):
        blockers.append(
            {
                "id": "d4_pg_lists_unsigned",
                "detail": "PG programme lists are enabled but D4 is unsigned",
            }
        )
    if (
        (catalogue_enabled or include_draft)
        and catalogue["missing_reviewer"] > 0
    ):
        blockers.append(
            {
                "id": "catalogue_rows_unreviewed",
                "detail": (
                    f"{catalogue['missing_reviewer']} of {catalogue['rows']} "
                    "catalogue rows have no named reviewer"
                ),
            }
        )
    if legacy["unapproved"] > 0:
        blockers.append(
            {
                "id": "legacy_nodes_unapproved",
                "detail": (
                    f"{legacy['unapproved']} legacy old-site nodes are "
                    "groundable without an approval entry"
                ),
            }
        )
    return blockers


def legacy_rows(db: str | Path) -> list[dict]:
    path = Path(db)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT node_id, title, url, status
            FROM nga_node
            WHERE url LIKE '%oldsite.dspmuranchi.ac.in%'
               OR node_id LIKE 'legacy.%'
            ORDER BY node_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def build_report(
    *,
    db: str | Path,
    catalogue_path: str | Path,
    register_path: str | Path,
) -> dict:
    register = load_register(register_path)
    catalogue_target = Path(catalogue_path)
    if not catalogue_target.is_absolute():
        catalogue_target = PROJECT_ROOT / catalogue_target
    try:
        catalogue_payload = json.loads(
            catalogue_target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        catalogue_payload = {}
    catalogue = catalogue_audit(catalogue_payload)
    allowed_hosts = tuple(
        host.strip().lower()
        for host in settings.NGA_ALLOWED_HOSTS.split(",")
        if host.strip()
    )
    legacy = legacy_audit(
        legacy_rows(db),
        allowed_hosts=allowed_hosts,
        allowlist=list(register.get("legacy_allowlist") or []),
    )
    blockers = evaluate_blockers(
        register=register,
        catalogue=catalogue,
        legacy=legacy,
        catalogue_enabled=settings.NGA_CATALOGUE_ENABLED,
        include_draft=settings.NGA_CATALOGUE_INCLUDE_DRAFT,
        pg_lists_enabled=settings.NGA_PG_PROGRAMME_LISTS_ANSWERABLE,
    )
    warnings: list[str] = []
    if not decision_signed(register, "D6"):
        warnings.append("d6_shell_strategy_unsigned")
    if not decision_signed(register, "D7"):
        warnings.append("d7_lead_capture_unsigned")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(Path(db)),
        "catalogue_path": str(catalogue_target),
        "register_path": str(Path(register_path)),
        "decisions": register.get("decisions") or {},
        "catalogue": {
            key: value
            for key, value in catalogue.items()
            if key != "missing_reviewer_ids"
        },
        "legacy": {
            key: value for key, value in legacy.items() if key != "nodes"
        },
        "legacy_nodes": legacy["nodes"],
        "blockers": blockers,
        "warnings": warnings,
        "release_ready": not blockers,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report NGA release-governance blockers."
    )
    parser.add_argument("--db", default=settings.serve_db_path)
    parser.add_argument(
        "--catalogue", default=settings.NGA_CATALOGUE_PATH
    )
    parser.add_argument("--register", default=DEFAULT_REGISTER)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        db=args.db,
        catalogue_path=args.catalogue,
        register_path=args.register,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("NGA release governance")
        print(
            f"  D4: {(report['decisions'].get('D4') or {}).get('status', 'missing')}"
        )
        print(
            "  catalogue: "
            f"{report['catalogue']['missing_reviewer']}/"
            f"{report['catalogue']['rows']} rows without a reviewer"
        )
        print(
            "  legacy: "
            f"{report['legacy']['unapproved']}/"
            f"{report['legacy']['count']} nodes unapproved"
        )
        for blocker in report["blockers"]:
            print(f"  BLOCKER {blocker['id']}: {blocker['detail']}")
        for warning in report["warnings"]:
            print(f"  warning {warning}")
        print(
            "  release gate: "
            + ("READY" if report["release_ready"] else "BLOCKED")
        )
    return 0 if (report["release_ready"] or not args.check) else 1


if __name__ == "__main__":
    raise SystemExit(main())
