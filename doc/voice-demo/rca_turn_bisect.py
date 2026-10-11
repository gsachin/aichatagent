"""RCA bisect: which part of the turn hangs, and does the batch arm cause it?

    python rca_turn_bisect.py streamed-only   # streamed turn, nothing before it
    python rca_turn_bisect.py batch-then-streamed
    python rca_turn_bisect.py query-only      # _query_llm_streamed, no TTS/session

Each stage is bounded and the watchdog names what is still pending, so a stall
locates itself instead of being inferred from a missing log line.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.llm_backend as lb  # noqa: E402
import app.voice_handler as vh  # noqa: E402

QUESTION = "What programs do you offer?"
T0 = time.perf_counter()


def mark(msg: str) -> None:
    print(f"  [{time.perf_counter() - T0:7.2f}s] {msg}", flush=True)


def dump_threads() -> None:
    frames = sys._current_frames()
    for t in threading.enumerate():
        f = frames.get(t.ident)
        where = (f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno} "
                 f"in {f.f_code.co_name}") if f else "?"
        mark(f"    thread {t.name!r} alive={t.is_alive()} at {where}")


async def bounded(label: str, coro, seconds: float = 40.0):
    task = asyncio.create_task(coro, name=label)

    async def watchdog():
        waited = 0.0
        while waited < seconds:
            await asyncio.sleep(5)
            waited += 5
            if task.done():
                return
            if waited % 15 == 0:
                mark(f"    still running at {waited:.0f}s ...")
                dump_threads()
                for t in asyncio.all_tasks():
                    if t is task or t.done():
                        continue
                    mark(f"    pending task {t.get_name()!r}: {t.get_coro()!r}"[:180])

    wd = asyncio.create_task(watchdog())
    try:
        return await asyncio.wait_for(task, timeout=seconds)
    except asyncio.TimeoutError:
        mark(f"*** {label} TIMED OUT after {seconds}s ***")
        return None
    finally:
        wd.cancel()


def make_session(stream: bool):
    vh.TTS_STREAM = stream
    lb.LLM_STREAM = stream
    s = vh.VoiceCallSession()
    s.call_id = "RCA"

    async def fake_transcribe(_a):
        return QUESTION, False

    s._transcribe = fake_transcribe
    s._total_frames = 60
    s._audio_buffer = [b"\x00" * 160] * 60
    return s


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "streamed-only"
    mark(f"mode={mode}")

    # Warm Kokoro on the MAIN thread -- an import in a worker thread is
    # pathologically slow on this box (see the module docstring).
    from app.voice_handler import _get_tts_engine
    _get_tts_engine()
    mark("TTS engine warm")

    if mode == "batch-then-streamed":
        s = make_session(False)
        mark("batch turn ...")
        out = await bounded("batch turn", s.process_utterance())
        mark(f"batch returned: {None if out is None else len(out[0])} chunks")

    if mode == "query-only":
        s = make_session(True)
        s.begin_turn()
        mark("_query_llm_streamed only (no TTS, no sink) ...")
        res = await bounded("query", s._query_llm_streamed(QUESTION, None,
                                                           cancel=s._cancel))
        if res is not None:
            mark(f"query returned: answer={len(res[0])} chars, {len(res[1])} chunks")
        return

    s = make_session(True)
    if mode == "streamed-with-sink":
        # The ONE variable that separates the passing repros from the hang:
        # a sink makes `_speak_clause` synthesise each clause while the model is
        # still decoding, instead of collecting clauses and returning.
        sent: list[int] = []

        async def sink(frames):
            sent.append(len(frames))

        mark("streamed turn WITH chunk_sink ...")
        out = await bounded("streamed turn (sink)", s.process_utterance(chunk_sink=sink))
        mark(f"streamed returned: {None if out is None else len(out[0])} chunks; "
             f"sink called {len(sent)} time(s)")
        return

    mark("streamed turn ...")
    out = await bounded("streamed turn", s.process_utterance())
    mark(f"streamed returned: {None if out is None else len(out[0])} chunks")


asyncio.run(main())
mark("done")
