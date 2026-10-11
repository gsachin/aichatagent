"""RCA: does `generate_stream` deliver anything, with the loop otherwise idle?

Isolates the streamed generation from the session, the TTS and the sockets.
The watchdog reports, at the moment it stalls, whether the pump thread is still
alive and whether the deltas are sitting unread in the queue -- which is the
difference between "the producer is blocked" and "the consumer was never woken".
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm_backend import generate_stream  # noqa: E402

T0 = time.perf_counter()


def mark(msg: str) -> None:
    print(f"  [{time.perf_counter() - T0:7.2f}s] {msg}", flush=True)


def dump_threads() -> None:
    frames = sys._current_frames()
    for t in threading.enumerate():
        f = frames.get(t.ident)
        where = f"{f.f_code.co_filename.split(os.sep)[-1]}:{f.f_lineno} in {f.f_code.co_name}" if f else "?"
        mark(f"thread {t.name!r} alive={t.is_alive()} daemon={t.daemon} at {where}")


async def main() -> None:
    n = 0
    agen = generate_stream("Say hello in one short sentence.", model="llama3.2:3b")

    async def consume():
        nonlocal n
        async for item in agen:
            n += 1
            mark(f"item {n}: {str(item)[:70]!r}")

    task = asyncio.create_task(consume(), name="consume")

    async def watchdog():
        for i in range(3):
            await asyncio.sleep(10)
            if task.done():
                return
            mark(f"--- stalled {10 * (i + 1)}s, deltas so far: {n} ---")
            dump_threads()
            # Reach into the generator's queue: if items are here, the producer
            # ran and the consumer was simply never woken.
            try:
                q = agen.ag_frame.f_locals.get("queue") if agen.ag_frame else None
                mark(f"queue size={q.qsize() if q is not None else 'n/a'} "
                     f"waiters={len(getattr(q, '_getters', [])) if q is not None else 'n/a'}")
            except Exception as exc:            # noqa: BLE001
                mark(f"queue probe failed: {exc}")

    wd = asyncio.create_task(watchdog(), name="watchdog")
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=35)
        mark(f"COMPLETED with {n} items")
    except asyncio.TimeoutError:
        mark(f"*** TIMED OUT after 35s with {n} items ***")
    wd.cancel()


asyncio.run(main())
mark("done")
