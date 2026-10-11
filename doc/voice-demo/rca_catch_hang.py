"""Catch the intermittent streamed-turn hang red-handed.

Three instruments, all writing to a FILE (stdout redirection is itself a
suspect, and a file survives a hard exit):

1. **Timed `_write_to_self`.** The event loop wakes cross-thread callers by
   writing a byte to a self-pipe socket. On the Windows ProactorEventLoop that
   write blocks when the pipe is full and nobody is draining it. Every call is
   timed; anything slow is recorded with the calling thread's name.
2. **All-thread dump via faulthandler** at the moment a turn stalls, which
   shows the exact frame each thread is parked in -- including threads that
   never touch Python-level tracing.
3. **Asyncio task stacks** via `Task.get_stack()`, which gives the real await
   chain rather than a repr walk.

Runs enough turns to trigger it (the hang needs several turns, not a cold
path), then exits hard so a partially-written log is still useful.

    python doc/voice-demo/rca_catch_hang.py
"""
from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

OUT = Path(__file__).with_name(os.environ.get("RCA_LOG", "rca_catch_hang.log"))
FH = open(OUT, "w", encoding="utf-8", buffering=1)   # line-buffered

QUESTION = "What programs do you offer?"

#: Turns to run before the watchdog starts caring. The hang has only ever
#: appeared once the process has served several turns.
TURNS = 14
STALL_AFTER_S = 45.0

_write_self_calls: list[tuple[float, str]] = []   # (elapsed_s, thread name)
_orig_write_to_self = None


def log(msg: str) -> None:
    FH.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def install_write_to_self_probe() -> bool:
    """Time every self-pipe wakeup; record the slow ones."""
    global _orig_write_to_self
    try:
        from asyncio import proactor_events
        loop_cls = proactor_events.BaseProactorEventLoop
    except Exception as exc:                       # noqa: BLE001
        log(f"probe NOT installed ({exc}); not a proactor loop?")
        return False

    _orig_write_to_self = loop_cls._write_to_self

    def timed(self):                               # noqa: ANN001
        t0 = time.perf_counter()
        try:
            return _orig_write_to_self(self)
        finally:
            dt = time.perf_counter() - t0
            if dt > 0.05:                          # 50 ms is already pathological
                _write_self_calls.append((dt, threading.current_thread().name))
                log(f"  !! _write_to_self took {dt * 1000:.0f} ms "
                    f"on thread {threading.current_thread().name!r}")

    loop_cls._write_to_self = timed
    log("probe installed on BaseProactorEventLoop._write_to_self")
    return True


def dump_all(label: str) -> None:
    log(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    log("--- asyncio task stacks ---")
    try:
        for t in asyncio.all_tasks():
            if t.done():
                continue
            log(f"  task {t.get_name()!r}: {t.get_coro()!r}"[:300])
            for frame in t.get_stack():
                log(f"      {os.path.basename(frame.f_code.co_filename)}:"
                    f"{frame.f_lineno} in {frame.f_code.co_name}")
    except RuntimeError as exc:
        log(f"  (no running loop: {exc})")
    log("--- all thread stacks (faulthandler) ---")
    faulthandler.dump_traceback(file=FH, all_threads=True)
    log("--- slow self-pipe writes seen so far ---")
    if not _write_self_calls:
        log("  none")
    for dt, name in _write_self_calls[-10:]:
        log(f"  {dt * 1000:.0f} ms on {name!r}")
    FH.flush()


def start_loop_lag_probe(loop, stop: threading.Event) -> threading.Thread:
    """Measure, from a plain thread, whether the loop services cross-thread work.

    This is the direct test of the self-pipe hypothesis and it does not need the
    loop to be alive to report: `call_soon_threadsafe` is exactly the path a
    worker thread takes to hand a result back. If it is never serviced, the
    coroutine awaiting that worker never resumes -- which is the observed
    signature -- and this thread says so while it is happening.
    """

    def probe() -> None:
        while not stop.is_set():
            serviced = threading.Event()
            t0 = time.perf_counter()
            try:
                loop.call_soon_threadsafe(serviced.set)
            except RuntimeError:
                return                              # loop closed; nothing to watch
            if not serviced.wait(8.0):
                log("  !! LOOP UNRESPONSIVE: call_soon_threadsafe from a worker "
                    "thread was not serviced within 8 s")
                dump_all("LOOP UNRESPONSIVE")
            else:
                lag = time.perf_counter() - t0
                if lag > 0.25:
                    log(f"   loop lag {lag * 1000:.0f} ms")
            stop.wait(1.0)

    t = threading.Thread(target=probe, name="loop-lag-probe", daemon=True)
    t.start()
    log("loop-lag probe started (1/s, flags >250 ms)")
    return t


def make_session(stream: bool):
    import app.llm_backend as lb
    import app.voice_handler as vh

    vh.TTS_STREAM = stream
    lb.LLM_STREAM = stream
    s = vh.VoiceCallSession()
    s.call_id = "CATCH"

    async def fake_transcribe(_a):
        return QUESTION, False

    s._transcribe = fake_transcribe
    s._total_frames = 60
    s._audio_buffer = [b"\x00" * 160] * 60
    return s


async def guarded_turn(stream: bool, index: int) -> bool:
    label = f"turn {index} ({'streamed' if stream else 'batch'})"
    s = make_session(stream)
    first: list[float] = []

    async def sink(frames):
        if frames and not first:
            first.append(time.perf_counter())

    t0 = time.perf_counter()
    task = asyncio.create_task(s.process_utterance(chunk_sink=sink),
                               name=f"turn{index}")
    log(f"START {label}")

    async def watchdog():
        waited = 0.0
        while waited < STALL_AFTER_S * 2:
            await asyncio.sleep(5)
            waited += 5
            if task.done():
                return
            if waited >= STALL_AFTER_S and waited % 15 == 0:
                dump_all(f"STALL: {label} still running at {waited:.0f}s")

    wd = asyncio.create_task(watchdog())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=STALL_AFTER_S * 2)
        log(f"DONE  {label} in {(time.perf_counter() - t0) * 1000:.0f} ms")
        return True
    except asyncio.TimeoutError:
        dump_all(f"HARD STALL: {label} exceeded {STALL_AFTER_S * 2:.0f}s")
        return False
    finally:
        wd.cancel()


async def main() -> None:
    log(f"pid={os.getpid()} python={sys.version.split()[0]}")

    # The single experimental variable. `measure_turn_latency.py` configures
    # root logging and this script does not; that is the only structural
    # difference left between the run that hangs and the runs that do not.
    # `logging` holds a module-level lock across handler I/O, and records are
    # emitted from the LLM pump thread AND the loop, so a redirected stream is a
    # textbook cross-thread deadlock shape.
    if "--log" in sys.argv:
        import logging
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="    %(asctime)s %(name)s: %(message)s")
        logging.getLogger("voice_events").setLevel(logging.INFO)
        logging.getLogger("voice_handler").setLevel(logging.INFO)
        log("VARIANT: root logging ENABLED (as measure_turn_latency.py does)")
    else:
        log("VARIANT: no logging configured")

    install_write_to_self_probe()

    from app.voice_handler import _get_tts_engine
    t0 = time.perf_counter()
    _get_tts_engine()
    log(f"TTS engine warm in {time.perf_counter() - t0:.1f}s")

    loop = asyncio.get_running_loop()
    for attr in ("_csock", "_ssock"):
        sock = getattr(loop, attr, None)
        if sock is not None:
            import socket as _socket
            log(f"loop.{attr} rcvbuf={sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF)} "
                f"sndbuf={sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF)}")

    # Periodic all-thread dump from a THREAD, so it still fires when the event
    # loop is blocked in synchronous work -- the one case the asyncio watchdog
    # inside `guarded_turn` can never report, because it needs the loop to run.
    faulthandler.dump_traceback_later(60, repeat=True, file=FH)
    log("faulthandler: all-thread dump every 60 s (fires without the loop)")

    stop = threading.Event()
    start_loop_lag_probe(loop, stop)

    # Alternate arms so both get the same number of turns.
    for i in range(1, TURNS + 1):
        ok = await guarded_turn(i % 2 == 0, i)
        if not ok:
            log("hang captured -- stopping so the dump is the last thing written")
            break

    stop.set()
    faulthandler.cancel_dump_traceback_later()
    log("run complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:                   # noqa: BLE001
        log(f"unexpected exit: {type(exc).__name__}: {exc}")
    finally:
        dump_all("FINAL STATE")
        FH.close()
        os._exit(0)                                # never let a daemon thread hold us
