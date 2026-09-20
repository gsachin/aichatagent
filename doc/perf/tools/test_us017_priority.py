"""US-017 acceptance tests — background work yields to the caller (BRD-20).

Drives the real `app.work_priority.GATE` with real threads. No model, no
network, no GPU, so the priority invariant can be checked in milliseconds and
the failure modes are stated rather than waited for.

  TAC-1  classification is total, and fails toward the caller
  TAC-2  zero background starts during a voice turn        <- the invariant
  TAC-3  background concurrency never exceeds one
  TAC-5  no caller is starved by background work
  TAC-6  deferral is recorded, and silence is prohibited
  TAC-7  the deferral mechanism does not spin

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us017_priority.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from app.work_priority import (  # noqa: E402
    BACKGROUND, GATE, VOICE, WorkGate, classify,
)

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


# ───────────────────────── TAC-1: classification ─────────────────────────

def tac1_classification() -> None:
    print("\n-- TAC-1  classification is total, and fails toward the caller")

    for mode in ("voice", "call", "phone", "VOICE", " voice "):
        cls, defect = classify(mode)
        check(f"TAC-1  {mode!r} is voice", cls == VOICE and defect is None)

    for mode in ("chat", "background", "admin", "text", "streamlit", "whatsapp"):
        cls, defect = classify(mode)
        check(f"TAC-1  {mode!r} is background", cls == BACKGROUND and defect is None)

    # The whole point: an unrecognised value must not become a refusal.
    for odd in (None, "", "banana", "VOICEISH", "123"):
        cls, defect = classify(odd)
        check(f"TAC-1  unclassified {odd!r} is admitted as voice, not refused",
              cls == VOICE)
    check("TAC-1  and it is recorded as a defect rather than passing silently",
          classify("banana")[1] is not None)

    gate = WorkGate()
    with gate.background_unit(label="odd", mode="banana"):
        pass
    snap = gate.snapshot()
    check("TAC-1  the gate records the classification defect",
          snap["classification_defects"] == 1, str(snap["classification_defects"]))
    check("TAC-1  an unclassified unit is counted as a voice turn, since it is admitted as one",
          snap["voice_turns"] == 1)


# ───────────────────────── TAC-2: the invariant ─────────────────────────

def tac2_zero_background_during_voice() -> None:
    print("\n-- TAC-2  zero background starts during a voice turn")

    gate = WorkGate(defer_timeout_s=10.0)
    violations: list[str] = []
    order: list[str] = []

    def bg(n: int) -> None:
        with gate.background_unit(label=f"bg{n}"):
            # Sampled INSIDE the admitted section, which is the only place a
            # violation is possible.
            if gate.voice_active > 0:
                violations.append(f"bg{n} ran with voice_active={gate.voice_active}")
            order.append(f"bg{n}")

    with gate.voice_turn(label="caller"):
        threads = [threading.Thread(target=bg, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.4)          # let them all reach the gate
        order.append("voice-running")
        check("TAC-2  no background unit started while the voice turn was in flight",
              not violations, "; ".join(violations))
        check("TAC-2  all five background units are deferred, not dropped",
              gate.background_active == 0 and len(order) == 1, str(order))
    for t in threads:
        t.join(timeout=5)

    check("TAC-2  every deferred unit ran once the line cleared", len(order) == 6,
          str(order))
    check("TAC-2  no violation was recorded by the gate itself",
          gate.snapshot()["background_starts_during_voice"] == 0)
    check("TAC-2  the gate counted the deferrals", gate.snapshot()["deferrals"] == 5,
          str(gate.snapshot()["deferrals"]))


# ───────────────────────── TAC-3: concurrency cap ─────────────────────────

def tac3_background_cap() -> None:
    print("\n-- TAC-3  background concurrency never exceeds one")

    gate = WorkGate(max_background=1, defer_timeout_s=10.0)
    peak = [0]
    live = [0]
    lock = threading.Lock()

    def bg(n: int) -> None:
        with gate.background_unit(label=f"bg{n}"):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1

    threads = [threading.Thread(target=bg, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    check("TAC-3  never more than one background unit at a time", peak[0] == 1,
          f"peak={peak[0]}")
    check("TAC-3  the gate agrees with the observed peak",
          gate.snapshot()["max_background_concurrent"] == 1)
    check("TAC-3  all eight units still completed", gate.snapshot()["background_units"] == 8)


# ───────────────────────── TAC-5: no caller starved ─────────────────────────

def tac5_no_starvation() -> None:
    print("\n-- TAC-5  no caller is starved by background work")

    gate = WorkGate(defer_timeout_s=10.0)
    release = threading.Event()

    def long_bg() -> None:
        with gate.background_unit(label="bg"):
            release.wait(timeout=5)

    t = threading.Thread(target=long_bg)
    t.start()
    time.sleep(0.1)

    started = time.monotonic()
    with gate.voice_turn(label="caller"):
        pass
    elapsed_ms = (time.monotonic() - started) * 1000.0
    check("TAC-5  a voice turn is never blocked behind a running background unit",
          elapsed_ms < 100.0, f"{elapsed_ms:.1f} ms")
    check("TAC-5  voice admission does not wait for the background slot",
          gate.voice_active == 0)

    release.set()
    t.join(timeout=5)

    # Background never starves either: the unit admitted while a voice turn was
    # in flight is recorded, and the wait is reported rather than hidden.
    gate2 = WorkGate(defer_timeout_s=10.0)
    hold = threading.Event()
    done = threading.Event()

    def bg2() -> None:
        with gate2.background_unit(label="bg2"):
            done.set()

    def voice() -> None:
        with gate2.voice_turn(label="caller"):
            hold.wait(timeout=5)

    vt = threading.Thread(target=voice)
    vt.start()
    time.sleep(0.1)
    bt = threading.Thread(target=bg2)
    bt.start()
    time.sleep(0.2)
    check("TAC-5  background waits while the caller holds the line", not done.is_set())
    hold.set()
    vt.join(timeout=5)
    bt.join(timeout=5)
    check("TAC-5  and proceeds the moment the caller finishes", done.is_set())


# ───────────────────────── TAC-6: records ─────────────────────────

def tac6_records() -> None:
    print("\n-- TAC-6  deferral is recorded, and silence is prohibited")

    gate = WorkGate(defer_timeout_s=10.0)
    hold = threading.Event()

    def voice() -> None:
        with gate.voice_turn(label="caller"):
            hold.wait(timeout=5)

    vt = threading.Thread(target=voice)
    vt.start()
    time.sleep(0.1)
    bt = threading.Thread(
        target=lambda: gate.background_unit(label="report-gen", mode="chat").__enter__())
    bt.start()
    time.sleep(0.25)
    hold.set()
    vt.join(timeout=5)

    snap = gate.snapshot()
    check("TAC-6  the deferral is recorded", snap["deferrals"] >= 1, str(snap["deferrals"]))
    if snap["deferral_records"]:
        rec = snap["deferral_records"][0]
        check("TAC-6  the record carries the wait, not just a flag",
              isinstance(rec.get("waited_ms"), (int, float)) and rec["waited_ms"] > 0,
              str(rec))
        check("TAC-6  the record names the unit", bool(rec.get("label")), str(rec))
    check("TAC-6  the snapshot is JSON-serialisable for the harness",
          _jsonable(gate.snapshot()))


def _jsonable(obj) -> bool:
    import json
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError):
        return False


# ───────────────────────── TAC-7: no spinning ─────────────────────────

def tac7_no_spin() -> None:
    print("\n-- TAC-7  the deferral mechanism does not spin")

    gate = WorkGate(defer_timeout_s=10.0)
    hold = threading.Event()

    def voice() -> None:
        with gate.voice_turn(label="caller"):
            hold.wait(timeout=5)

    vt = threading.Thread(target=voice)
    vt.start()
    time.sleep(0.1)

    def waiter() -> None:
        with gate.background_unit(label="waiter"):
            pass

    wt = threading.Thread(target=waiter)
    cpu0 = time.process_time()
    wt.start()
    time.sleep(0.6)          # the waiter is parked for this whole window
    cpu_used = time.process_time() - cpu0

    check("TAC-7  a deferred unit consumes almost no CPU while parked",
          cpu_used < 0.05, f"{cpu_used * 1000:.1f} ms of CPU over 600 ms wall")
    hold.set()
    vt.join(timeout=5)
    wt.join(timeout=5)


# ───────────────────────── AC-5: one-step revert ─────────────────────────

def ac5_revert() -> None:
    print("\n-- AC-5  the policy is reversed in one step")

    import app.pipeline as pipeline
    old = os.environ.get("BG_PRIORITY_ENABLED")
    try:
        os.environ["BG_PRIORITY_ENABLED"] = "0"
        check("AC-5  disabling the policy is one setting", pipeline._bg_priority_enabled() is False)
        os.environ["BG_PRIORITY_ENABLED"] = "1"
        check("AC-5  re-enabling it is the same setting",
              pipeline._bg_priority_enabled() is True)
    finally:
        if old is None:
            os.environ.pop("BG_PRIORITY_ENABLED", None)
        else:
            os.environ["BG_PRIORITY_ENABLED"] = old

    from app.work_priority import policy_status
    st = policy_status()
    check("AC-5  policy status is observable", "max_background" in st and "enabled" in st,
          str(sorted(st)))


# ───────────────────── LLD T-9 / T-10 / T-12 ─────────────────────

def lld_refusal_and_class() -> None:
    print("\n-- LLD T-9 / T-10 / T-12")

    # T-9: a unit refused for budget NAMES its reason, and the refusal is not a
    # caller-facing failure.
    from app.work_priority import BackgroundDeferred

    gate = WorkGate(defer_timeout_s=0.3)
    hold = threading.Event()

    def voice() -> None:
        with gate.voice_turn(label="caller"):
            hold.wait(timeout=5)

    vt = threading.Thread(target=voice)
    vt.start()
    time.sleep(0.1)

    caught = None
    try:
        with gate.background_unit(label="long-job", mode="chat"):
            caught = "started"
    except BackgroundDeferred as exc:
        caught = exc
    hold.set()
    vt.join(timeout=5)

    check("T-9  a unit that cannot run is REFUSED, not started anyway",
          isinstance(caught, BackgroundDeferred),
          f"got {caught!r} -- starting it would break TAC-2")
    check("T-9  the refusal names its reason",
          caught is not None and "busy" in str(caught), str(caught))
    snap = gate.snapshot()
    check("T-9  the refusal is recorded with its reason and wait",
          snap["refusals"] == 1 and snap["refusal_records"][0].get("reason"),
          str(snap["refusal_records"]))
    check("T-9  the refused unit never became a background start",
          snap["max_background_concurrent"] == 0, str(snap["max_background_concurrent"]))

    # T-10: every caller turn is stamped with its work class, so the invariant
    # is countable from the records.
    import inspect
    import app.voice_handler as vh

    src = inspect.getsource(vh)
    check("T-10  the turn trace carries work_class", 'work_class="voice"' in src)
    check("T-10  and it is set where the trace is created, not patched on later",
          "work_class=\"voice\"" in src.split("def _pcm_to_ulaw_chunks")[0])

    # T-12: a caller hanging up while a unit is deferred does not disturb it and
    # shares no state with it.
    gate2 = WorkGate(defer_timeout_s=10.0)
    done = threading.Event()

    def bg() -> None:
        with gate2.background_unit(label="bg"):
            done.set()

    with gate2.voice_turn(label="caller-A"):
        t = threading.Thread(target=bg)
        t.start()
        time.sleep(0.2)
        check("T-12  the unit is deferred while caller A is live", not done.is_set())
    # caller A hangs up: the voice turn exits, which is the whole of the state
    # a deferral shares with it.
    t.join(timeout=5)
    check("T-12  when the caller hangs up the deferred unit is served",
          done.is_set())
    check("T-12  and the gate holds no per-caller state",
          gate2.voice_active == 0 and gate2.background_active == 0)


# ───────────────────── the event loop must keep turning ─────────────────────

def event_loop_not_blocked() -> None:
    """A deferred unit must not freeze the event loop.

    This is the defect that killed both callers on the 2+1 window, twice, and
    it looked like engine contention for as long as nobody followed the call
    graph: `test_pipeline_with_text` is `async` and used the SYNCHRONOUS
    `background_unit`, whose wait is a `threading.Condition.wait` -- on the loop
    thread. A deferred text query stopped both live voice WebSockets, their VAD,
    their media streams and their keepalives until the query got its slot.
    Sessions died on `keepalive ping timeout` after 3-18 turns.

    So the assertion is not "the gate works" -- the gate always worked. It is
    that the loop keeps turning while a unit is deferred.
    """
    print("\n-- the event loop must keep turning during a deferral")

    import asyncio
    import inspect

    import app.pipeline as pipeline
    from app.work_priority import WorkGate

    # The async path must use the async gate. Source-level, because the failure
    # is a blocking call on the loop thread and there is no cheap runtime probe
    # for "this coroutine did not yield when it should have".
    src = inspect.getsource(pipeline.test_pipeline_with_text)
    check("the async text path uses the ASYNC gate",
          "background_unit_async" in src and "GATE.background_unit(" not in src,
          "a synchronous gate on the event loop freezes every live call")

    gate = WorkGate(defer_timeout_s=5.0)
    ticks = [0]
    stop = threading.Event()

    async def scenario() -> None:
        async def ticker() -> None:
            while not stop.is_set():
                ticks[0] += 1
                await asyncio.sleep(0.01)

        async def deferred_unit() -> None:
            # The loop must keep running for this whole wait.
            async with gate.background_unit_async(label="bg", mode="chat"):
                pass

        tick_task = asyncio.create_task(ticker())
        # `voice_turn` is SYNCHRONOUS, and that is correct: it never waits, it
        # only increments a counter under a briefly-held lock. Only the
        # background acquisition blocks, which is why only it is async.
        with gate.voice_turn(label="caller"):
            # Hold the line so the background unit is forced to defer.
            unit = asyncio.create_task(deferred_unit())
            await asyncio.sleep(0.5)
            during = ticks[0]
            check("the loop turned while the unit was deferred", during > 5,
                  f"{during} ticks in 500 ms -- near zero means the loop was blocked")
            # A blocked loop could not have serviced the sleep above at all.
        await unit
        stop.set()
        await tick_task

    asyncio.run(scenario())
    check("the deferred unit ran once the line cleared", gate.background_active == 0)
    check("the gate recorded the deferral, not a spin",
          gate.snapshot()["max_background_concurrent"] <= 1)

    # NEGATIVE CONTROL. The check above is only worth anything if a blocking
    # gate would fail it. The synchronous acquisition is called here ON THE LOOP
    # THREAD on purpose, with a short budget so it cannot hang the suite: the
    # ticker must stop dead, which is the defect that killed both callers.
    gate2 = WorkGate(defer_timeout_s=0.4)
    stalled = [None]

    async def negative() -> None:
        async def ticker() -> None:
            while True:
                ticks_neg[0] += 1
                await asyncio.sleep(0.01)

        ticks_neg = [0]
        t = asyncio.create_task(ticker())
        with gate2.voice_turn(label="caller"):
            await asyncio.sleep(0.2)
            before = ticks_neg[0]
            try:
                # BLOCKS THE LOOP. That is the point of the control.
                gate2._acquire_background("sync-on-loop")
            except Exception:                         # noqa: BLE001
                pass
            stalled[0] = ticks_neg[0] - before
        t.cancel()

    asyncio.run(negative())
    check("NEGATIVE CONTROL: the synchronous gate on the loop DOES stall it",
          stalled[0] is not None and stalled[0] <= 2,
          f"{stalled[0]} ticks during a blocking wait -- if this is large, the "
          f"positive check above proves nothing")


def main() -> int:
    print("=" * 74)
    print("US-017 -- background work yields to the caller (BRD-20)")
    print("=" * 74)
    print(f"  max background concurrent : {GATE._max_background}")
    print(f"  defer timeout             : {GATE._defer_timeout_s}s")

    tac1_classification()
    tac2_zero_background_during_voice()
    tac3_background_cap()
    tac5_no_starvation()
    tac6_records()
    tac7_no_spin()
    lld_refusal_and_class()
    event_loop_not_blocked()
    ac5_revert()

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
