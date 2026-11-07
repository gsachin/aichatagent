#!/usr/bin/env python3
"""
predeploy.py — machine-adaptive .env configuration
====================================================
Detects the machine (RAM, VRAM, CPU cores, GPU, cloud vs local), picks a
sizing tier, and merges machine-derived values into the marked MACHINE
PROFILE block in .env. Secrets and manual overrides outside the block
are never touched.

Stdlib-only — runs under any Python, before the venv exists.

Usage:
  python scripts/predeploy.py            # size .env, print what changed
  python scripts/predeploy.py --dry-run  # print the diff table, write nothing
  python scripts/predeploy.py --auto     # no-op when up to date (bootstraps)
  python scripts/predeploy.py --check    # status only; exit 2 if stale
  python scripts/predeploy.py --force    # rewrite the block even if unchanged
  python scripts/predeploy.py --env PATH # target a different .env
  python scripts/predeploy.py --json     # machine-readable summary
  python scripts/predeploy.py --quiet    # no output on success

Exit codes: 0 ok/unchanged, 1 error, 2 drifted or no profile (--check only).
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.hardware_profile import (  # noqa: E402
    MANAGED_KEYS,
    PROFILE_MARKER_OPEN,
    _env_map,
    _find_block,
    _read_env_text,
    apply_profile,
    check_drift,
    detect_hardware,
    get_snapshot_path,
    load_snapshot,
    select_tier,
    write_snapshot,
)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Detect machine hardware and size .env accordingly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[0],
    )
    p.add_argument("--dry-run", "--diff", action="store_true",
                   help="print the change table but write nothing")
    p.add_argument("--auto", action="store_true",
                   help="no-op (exit 0) when profile is up to date")
    p.add_argument("--check", action="store_true",
                   help="status only; exit 2 when drifted or missing")
    p.add_argument("--force", action="store_true",
                   help="rewrite the block and snapshot even if unchanged")
    p.add_argument("--env", metavar="PATH", default=None,
                   help="target .env file (default: <repo>/.env)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable summary")
    p.add_argument("--quiet", action="store_true",
                   help="no output on success")
    return p.parse_args(argv)


def _current_values(env_path):
    """Managed-key values currently in .env: block copy wins over outside."""
    text = _read_env_text(env_path)
    lines = text.splitlines()
    span = _find_block(lines)
    if span:
        outside = lines[:span[0]] + lines[span[1] + 1:]
        block = lines[span[0]:span[1] + 1]
    else:
        outside, block = lines, []
    merged = _env_map(outside)
    merged.update(_env_map(block))
    return merged


def format_table(hw, tier, env_path, result, protected):
    """Human-readable diff table."""
    cur = _current_values(env_path)
    rows = []
    rows.append(f"Machine profile: {tier['tier_id']}  "
                f"({hw.get('chip_tier') or hw.get('gpu_name') or hw['platform']}, "
                f"{hw.get('ram_gb')} GB RAM, {hw.get('cpu_cores')} cores, "
                f"vram={hw.get('vram_gb')})")
    if tier.get("warn"):
        rows.append(f"WARNING: {tier['warn']}")

    header = f"{'KEY':22} {'CURRENT':34} {'NEW':34} ACTION"
    rows.append(header)
    applied = result.get("applied", tier["values"])
    for key in MANAGED_KEYS:
        action = result["actions"].get(key, "?")
        if action == "manual-skip":
            current, shown = cur.get(key, ""), "—"
        elif action == "adopted":
            current, shown = cur.get(key, ""), applied.get(key, "")
        else:
            current, shown = cur.get(key, ""), applied.get(key, "")
        rows.append(f"{key:22} {current[:32]:34} {shown[:32]:34} {action}")

    for key in sorted(protected):
        rows.append(f"{key:22} {'(protected)':34} {'—':34} never-touched")
    rows.append(f"Result: {result_summary(result)}")
    return "\n".join(rows)


def result_summary(result):
    parts = [f"{sum(1 for a in result['actions'].values() if a == k)} {k}"
             for k in ("update", "insert", "adopted", "unchanged", "manual-skip")]
    if result["removed_from_block"]:
        parts.append(f"{len(result['removed_from_block'])} removed-from-block")
    return ", ".join(parts)


def _block_present(env_path) -> bool:
    try:
        return _find_block(_read_env_text(env_path).splitlines()) is not None
    except OSError:
        return False


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    env_path = Path(args.env) if args.env else REPO_ROOT / ".env"
    snapshot = load_snapshot(env_path)

    if not env_path.is_file():
        print(f"ERROR: {env_path} not found — run bootstrap_services.py first.", file=sys.stderr)
        return 1

    hw = detect_hardware()
    tier = select_tier(hw)

    # ── --check: status only ──
    if args.check:
        drift = check_drift(env_path)
        payload = {
            "state": drift["state"], "tier_id": tier["tier_id"],
            "snapshot_tier": drift.get("tier_id"), "diffs": drift.get("diffs"),
            "block_present": _block_present(env_path),
        }
        if args.as_json:
            print(json.dumps(payload, indent=2))
        else:
            status = "ok" if (drift["state"] == "ok" and payload["block_present"]) else drift["state"]
            print(f"machine profile: {status} (sized tier: {tier['tier_id']}, "
                  f"snapshot tier: {drift.get('tier_id') or 'none'}, "
                  f"block: {'present' if payload['block_present'] else 'missing'})")
        return 0 if (drift["state"] == "ok" and payload["block_present"]) else 2

    # ── --auto: exit early when already up to date ──
    if args.auto and snapshot is not None and not args.force:
        drift = check_drift(env_path)
        if drift["state"] == "ok" and _block_present(env_path):
            if not args.quiet:
                print(f"Machine profile up to date ({tier['tier_id']}) — nothing to do.")
            return 0

    # ── apply ──
    result = apply_profile(env_path, tier, dry_run=args.dry_run, snapshot=snapshot)

    protected = set()
    for line in _read_env_text(env_path).splitlines():
        kv = None
        s = line.lstrip()
        if s.startswith("#") or "=" not in s:
            continue
        key = s.partition("=")[0].strip()
        if key and key not in MANAGED_KEYS:
            protected.add(key)

    if not args.dry_run and (result["changed"] or args.force or snapshot is None):
        write_snapshot(env_path, tier, hw, result["actions"], result["removed_from_block"])

    if args.as_json:
        print(json.dumps({
            "tier_id": tier["tier_id"], "label": tier["label"],
            "warn": tier.get("warn"), "changed": result["changed"],
            "dry_run": args.dry_run, "actions": result["actions"],
            "removed_from_block": result["removed_from_block"],
            "hardware": {k: hw.get(k) for k in
                         ("os", "arch", "cpu_cores", "ram_gb", "device", "platform",
                          "gpu_name", "vram_gb", "chip_tier", "is_container")},
            "snapshot": str(get_snapshot_path(env_path)),
        }, indent=2))
    elif not args.quiet:
        print(format_table(hw, tier, env_path, result, protected))

    return 0


if __name__ == "__main__":
    sys.exit(main())
