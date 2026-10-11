"""RCA: is `asyncio.Queue.put_nowait` from a raw thread a lost-wakeup race?

`generate_stream` hands deltas from a plain `threading.Thread` to the event loop
through an `asyncio.Queue`. `asyncio.Queue` is NOT thread-safe, and
`put_nowait` from a foreign thread wakes the consumer by calling
`Future.set_result` -> `loop.call_soon`, whose append to `loop._ready` is not
atomic with respect to the running loop. When that append is missed, the item is
in the queue and the consumer is never woken: an indefinite hang, intermittent
by construction.

This drives both patterns a few thousand times with the loop otherwise idle --
the condition the live app never has, because inbound media frames wake the loop
every 20 ms and mask a lost wakeup.

    publish via queue.put_nowait(...)                    <- what the code does
    publish via loop.call_soon_threadsafe(q.put_nowait)  <- the documented way
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

N = 2000
TIMEOUT = 3.0


async def once(threadsafe: bool, index: int) -> bool:
    """Returns True if the item arrived within the timeout."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def publish() -> None:
        if threadsafe:
            loop.call_soon_threadsafe(q.put_nowait, ("delta", index))
        else:
            q.put_nowait(("delta", index))

    threading.Thread(target=publish, daemon=True).start()
    try:
        await asyncio.wait_for(q.get(), timeout=TIMEOUT)
        return True
    except asyncio.TimeoutError:
        return False


async def run(threadsafe: bool) -> int:
    label = "call_soon_threadsafe" if threadsafe else "put_nowait (current)"
    lost = 0
    first_loss = None
    t0 = time.perf_counter()
    for i in range(N):
        if not await once(threadsafe, i):
            lost += 1
            if first_loss is None:
                first_loss = i
    dt = time.perf_counter() - t0
    print(f"  {label:<26} {N} rounds in {dt:6.1f}s -> {lost} lost wakeup(s)"
          + (f", first at #{first_loss}" if first_loss is not None else ""),
          flush=True)
    return lost


async def main() -> int:
    print(f"asyncio.Queue handoff from a raw thread, loop otherwise idle "
          f"(timeout {TIMEOUT}s/round)")
    unsafe = await run(False)
    safe = await run(True)
    print()
    if unsafe and not safe:
        print("CONFIRMED: the current pattern loses wakeups; the thread-safe "
              "form does not.")
    elif unsafe == 0 and safe == 0:
        print("Not reproduced at this sample size. The race is real but rare "
              "here -- absence of a repro is not evidence of safety, and the "
              "fix is still correct (call_soon_threadsafe is the documented "
              "API for cross-thread scheduling).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
