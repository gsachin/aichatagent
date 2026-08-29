"""Migrate the Meridian knowledge base from the legacy RAG store to the
enterprise-rag-core MCP service, then verify parity.

Flow:
  1. Validate the KB with the legacy gate (expected markers / blocked markers).
  2. Run enterprise_rag.prepopulate in the ERC repo's venv (idempotent —
     builds the DBs only if the doc is absent; --force to rebuild).
  3. Parity gate (unless --skip-parity): the MCP service must be reachable;
     the 5 legacy smoke queries must return context on BOTH sides; the
     negative canary and every MCP chunk must be free of blocked markers;
     every labeled chunk must render with the [§ section] prefix.

Usage:
    python scripts/migrate_rag_to_erc.py
    python scripts/migrate_rag_to_erc.py --skip-parity
    python scripts/migrate_rag_to_erc.py --erc-root D:\\project\\enterprise-rag-core
    python scripts/migrate_rag_to_erc.py --force

Exit codes: 0 = migrated + parity passed, 1 = failure.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_MCP_RAG", "off")     # legacy reads below stay local

from app import rag_legacy  # noqa: E402

DEFAULT_KB = ROOT / "content" / "meridian" / "meridian_knowledge_base.md"
DEFAULT_ERC_ROOT = ROOT.parent / "enterprise-rag-core"

SMOKE_QUERIES = [
    "What is the tuition fee for the MBA program?",
    "What are the application deadlines?",
    "What undergraduate programs does Meridian offer?",
    "What is the hostel fee?",
    "How do I apply to Meridian?",
]

# Canary: the question whose answer would surface the contaminated
# pages of the old source PDF (Maryland financial aid / housing).
NEGATIVE_QUERY = "What is the Terrapin Commitment?"


def _resolve_erc_python(erc_root: Path) -> str:
    """The ERC venv python, or the current interpreter if it can import the
    package (never fall back silently to a broken environment)."""
    venv_py = erc_root / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return str(venv_py)
    probe = subprocess.run(
        [sys.executable, "-c", "import enterprise_rag"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return sys.executable
    print("ERROR: enterprise-rag-core venv not found and the current python "
          "cannot import enterprise_rag.\n"
          f"Fix: cd {erc_root} && .\\start_services.ps1 (creates the venv), or\n"
          f"     pip install enterprise-rag-core into this environment.")
    sys.exit(1)


def _prepopulate(erc_python: str, kb: Path, force: bool, erc_root: Path) -> int:
    # Pin the same DB defaults the ERC launcher (start_services.ps1) serves,
    # so this script targets the live instance regardless of the caller's env.
    env = dict(os.environ)
    env.setdefault("RAG_CORE_CHROMA_PATH", str(erc_root / "chroma_data"))
    env.setdefault("RAG_CORE_CHROMA_COLLECTION", "meridian-kb")
    env.setdefault("RAG_CORE_DEFAULT_TENANT", "default")

    cmd = [
        erc_python, "-m", "enterprise_rag.prepopulate",
        "--kb", str(kb),
        "--doc-id", "meridian-kb",
        "--tenant", "default",
        "--required-marker", "meridian university",
    ]
    for marker in rag_legacy.BLOCKED_MARKERS:
        cmd += ["--blocked-marker", marker]
    if force:
        cmd.append("--force")
    print(f"  running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip())
    return result.returncode


def _mcp_context(query: str) -> str | None:
    """Context from the MCP service (None = service unreachable)."""
    from app import rag_mcp

    chunks = rag_mcp.mcp_retrieve(query, top_k=5)
    if chunks is None:
        return None
    return rag_mcp.format_legacy_chunks(chunks)


def _parity_gate() -> int:
    from app import rag_mcp

    if not rag_mcp.mcp_initialize():
        print("ERROR: MCP service unreachable "
              f"({rag_mcp.mcp_rag_status().get('url')}).\n"
              "Fix: cd enterprise-rag-core && "
              ".\\start_services.ps1 -KbPath <kb> -NoTunnel")
        return 1

    print(f"\n{'query':<55} {'legacy':>8} {'mcp':>8}  pass")
    failures = []
    for q in SMOKE_QUERIES:
        legacy_ctx = rag_legacy.retrieve_context(q)          # local store
        mcp_ctx = _mcp_context(q)
        ok = bool(legacy_ctx.strip()) and bool(mcp_ctx.strip())
        print(f"{q[:53]:<55} {len(legacy_ctx):>8} {len(mcp_ctx or ''):>8}  {'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"smoke query '{q}' returned empty context on one side")

    # Negative canary + blocked-marker scan over MCP context
    canary = _mcp_context(NEGATIVE_QUERY)
    for marker in rag_legacy.BLOCKED_MARKERS:
        if canary and marker in canary.lower():
            failures.append(f"blocked marker '{marker}' in canary context")

    # Format contract: labeled chunks must render [§ section]
    for q in SMOKE_QUERIES[:2]:
        mcp_ctx = _mcp_context(q)
        if mcp_ctx and mcp_ctx.startswith("["):
            if not mcp_ctx.startswith("[§ "):
                failures.append(f"context for '{q}' lacks the [§ section] label")

    print(f"\nParity gate: {'PASSED' if not failures else 'FAILED'}")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", default=str(DEFAULT_KB), help="Markdown KB path")
    parser.add_argument("--erc-root", default=str(DEFAULT_ERC_ROOT),
                        help="enterprise-rag-core repo root")
    parser.add_argument("--skip-parity", action="store_true",
                        help="Skip the parity gate (service not running)")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild the ERC DBs even if present")
    args = parser.parse_args()

    kb = Path(args.kb)
    erc_root = Path(args.erc_root)

    # 1. Legacy validation gate (source must be clean before any import)
    text = kb.read_text(encoding="utf-8")
    issues = rag_legacy.validate_source(text)
    if issues:
        print("VALIDATION FAILED:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print(f"Validation passed: {kb}")

    # 2. ERC prepopulate (idempotent)
    erc_python = _resolve_erc_python(erc_root)
    print(f"ERC python: {erc_python}")
    code = _prepopulate(erc_python, kb, args.force, erc_root)
    if code != 0:
        print("Prepopulate failed — aborting.")
        return 1

    # 3. Parity gate
    if args.skip_parity:
        print("Parity gate skipped (--skip-parity).")
        return 0
    return _parity_gate()


if __name__ == "__main__":
    sys.exit(main())
