"""No blocking call on the event loop — a standing guard (Class A).

One asynchronous event loop serves every live call on this box: the voice
WebSockets, their VAD, their media streams and their keepalives, plus the text
chat and the WhatsApp path. A synchronous call made from an `async def` blocks
all of it.

That is not a slow caller. It is a dead one, and it happened repeatedly:

  US-017  the background gate's wait, entered from async code -- both callers
          died on `keepalive ping timeout` at turns 3-18. Fixing it moved the
          death to turn 68-70; a second blocking call inside the same function
          (a synchronous `backend_chat`) finished them off.
  C4      `generate_ulaw_greeting` on two greeting paths. It is synchronous AND
          loads the Kokoro model when cold, so the first caller's greeting
          froze the loop for a model load -- the worst possible moment, on the
          turn a caller is most likely to be timing.
  WhatsApp intent detection -- a synchronous `backend_chat` on a path that only
          runs when a caller may also be on the line.

Every one of those read as "the system is slow" or "the engine is contended"
until somebody followed the call graph into the loop. This file exists so that
following it is not required again.

**The scan is asserted in both directions.** A scan that cannot fail proves
nothing, so a known-blocking snippet is planted and the scan must catch it.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_event_loop_hygiene.py
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

#: Synchronous functions that must never be called directly from an `async def`
#: on this stack. Each performs I/O or compute long enough to matter: an HTTP
#: call to the engine, a full TTS synthesis, a model load, a retrieval.
BLOCKING = {
    "generate_ulaw_greeting",   # Kokoro synthesis, and a cold model load
    "backend_chat",             # synchronous HTTP to Ollama
    "run_rag_query_sync",       # the whole RAG+LLM chain
    "_retrieve_context",        # retrieval, sync
    "_get_tts_engine",          # loads the ONNX model
    "_get_stt_model",           # loads faster-whisper

    # The polled DB reads. Each is the sync core behind an async wrapper that
    # hands it to `asyncio.to_thread`; the four wrappers are polled every
    # 10/30/60 s whether or not there is work, so a direct call here stops the
    # loop with no traffic to blame it on. py-spy put the MainThread in
    # `psycopg2.connect <- _get_db <- get_next_queued_call <- _poll_loop`
    # (2026-09-21, measured): one failed connect to a dead Postgres costs ~2 s
    # per address family on this box, and the poll burned ~4 s of every 14.
    "_get_next_queued_call_sync",
    "_get_due_follow_ups_sync",
    "_list_expired_offers_sync",
    "_depth_sync",
}

#: Sources worth scanning: the request-handling surface. `to_thread` callers
#: are clean by construction -- the scan looks for a DIRECT call.
#:
#: `database.py` and `leads/service.py` were MISSING from this list, and that
#: omission had teeth: on 2026-09-19 the post-call path called the synchronous
#: Ollama client from four `async def`s, the loop froze inside a socket read
#: with Ollama idle, and the whole service went down for minutes. py-spy put
#: the MainThread in `httpx sync read <- ollama.chat <-
#: extract_lead_from_transcript <- handle_post_interaction <- _handle_disconnect`.
#: A guard that only watches the files you happened to think of is a guard that
#: reports green on the path nobody looked at.
SOURCES = ("app/main.py", "app/pipeline.py", "app/voice_handler.py",
           "app/admission.py", "app/work_priority.py",
           "app/database.py", "app/leads/service.py",
           # Where the `_sync` cores live. Scanning their own files is what
           # makes the four entries above enforceable rather than decorative:
           # the async wrapper's `to_thread(core, ...)` is not a direct call,
           # and anything else that reaches for a core directly is.
           "app/leads/models.py", "app/offers/models.py", "app/crm/outbox.py")

IN_SCOPE = os.environ.get("EVENT_LOOP_SCAN_PATHS")

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def _own_nodes(async_fn: ast.AsyncFunctionDef):
    """Walk an async function WITHOUT descending into nested functions.

    Two reasons, and the first one bit: `lifespan` defines a nested sync
    `_warmup` and hands it to `asyncio.to_thread`, so its `_get_stt_model()`
    call is already off the loop -- but a naive walk descends into it and
    reports a false positive. A guard that cries wolf is a guard that gets
    ignored.

    A nested `async def` is its own async function and is visited separately by
    the module-level walk, so skipping it here loses nothing.
    """
    stack = list(async_fn.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue          # its own scope; walked on its own
            stack.append(child)


def scan(src: str) -> list[str]:
    """Direct calls to a blocking name from inside an `async def`.

    `await asyncio.to_thread(f, ...)` is NOT a direct call to `f` -- the
    blocking name sits inside a Call whose func is `to_thread`, so it is
    correctly not reported. That is the whole distinction this guard rests on.
    """
    hits: list[str] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for child in _own_nodes(node):
            if not isinstance(child, ast.Call):
                continue
            name = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
            if name in BLOCKING:
                hits.append(f"{node.name}() line {node.lineno} calls {name}() "
                            f"at line {child.lineno}")
    return hits


def main() -> int:
    print("=" * 74)
    print("Event-loop hygiene -- no blocking call on the async path (Class A)")
    print("=" * 74)

    paths = [Path(p) for p in (IN_SCOPE.split(",") if IN_SCOPE else SOURCES)]
    total_hits: list[str] = []
    scanned = 0
    for p in paths:
        if not p.is_file():
            continue
        scanned += 1
        for hit in scan(p.read_text(encoding="utf-8")):
            total_hits.append(f"{p}: {hit}")
            print(f"        {p}: {hit}")

    print(f"  scanned {scanned} file(s) for {len(BLOCKING)} blocking names")
    check("no blocking call is made directly from an async def",
          not total_hits, f"{len(total_hits)} found")

    # NEGATIVE CONTROL. If the scan cannot see a planted blocking call, the
    # clean result above means nothing.
    planted = '''
import asyncio


async def handler():
    chunks = generate_ulaw_greeting("hi")
    return chunks


async def correctly_wrapped():
    return await asyncio.to_thread(generate_ulaw_greeting, "hi")


async def also_correct():
    return await asyncio.to_thread(backend_chat, messages=[])
'''
    hits = scan(planted)
    check("NEGATIVE CONTROL: the scan catches a planted blocking call",
          any("generate_ulaw_greeting" in h for h in hits), str(hits))
    check("NEGATIVE CONTROL: and does NOT flag a to_thread-wrapped call",
          not any("correctly_wrapped" in h or "also_correct" in h for h in hits),
          str(hits))

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
