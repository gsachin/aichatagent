"""US-008 shared-client re-merge checks (Phase 3.3, 2026-09-20). No network.

The 2026-09-19 revert of the module-level httpx.Client was a one-variable
isolation while the 1011 keepalive deaths were unexplained; Phase 0.4
root-caused those deaths elsewhere (the event-loop freeze), so the shared
client is back. These checks pin what the re-merge must preserve:

  T-6    consecutive calls reuse ONE client (no fresh TCP connect per call)
  T-6    the pool bounds are US-008's (8 connections / 4 keepalive)
  TAC-x  the read budget stays PER REQUEST (probe-aware), never a client
         default that a stalled call could hold past its own budget
  TAC-x  concurrent threads share the client without constructing a second

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us008_shared_client.py
"""
from __future__ import annotations

import inspect
import os
import sys
import threading
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv

load_dotenv(PROJ / ".env")

import app.rag_mcp as m  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


print("\nUS-008 shared MCP client (re-merged)\n")

m._client = None
c1 = m._get_client()
c2 = m._get_client()
check("T-6   consecutive calls reuse ONE client (no fresh pool per call)",
      c1 is c2)

client_src = inspect.getsource(m._get_client)
check("T-6   the pool bounds are US-008's: 8 connections / 4 keepalive",
      "max_connections=8" in client_src
      and "max_keepalive_connections=4" in client_src)

src = inspect.getsource(m._post)
check("TAC-x  the read budget stays per request (probe-aware), not a client default",
      "timeout=_timeout(probe=_probe_in_flight())" in src)
check("TAC-x  the shared client carries NO default timeout",
      "timeout" not in inspect.getsource(m._get_client))

# Concurrent threads must all see the SAME client (httpx.Client is
# thread-safe; retrieval runs in worker threads).
m._client = None
seen: list = []


def grab():
    seen.append(m._get_client())


threads = [threading.Thread(target=grab) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("TAC-x   concurrent threads share the client without a second construction",
      len(seen) == 8 and all(c is seen[0] for c in seen))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
