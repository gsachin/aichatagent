#!/usr/bin/env python
"""A/B: what the streaming flags do to time-to-first-audio on a real turn.

    ./.venv/Scripts/python.exe doc/voice-demo/measure_turn_latency.py --runs 5

Measures the caller-visible leg of a turn -- retrieval, prefill, decode and
synthesis, up to the first frame handed to the socket -- on the REAL engines
(Ollama, Kokoro, the live retrieval path). Both configurations are driven in one
process, on the same input, alternating, so a slow patch of the box cannot be
mistaken for a difference between them.

What this deliberately does NOT include, and why
------------------------------------------------
* **VAD / endpointing (600 ms).** Identical in both arms; it cannot differ.
* **STT.** Held fixed by construction -- the transcript is supplied. It is not
  an approximation: with speculative STT on, `seg_vad_end__stt_done` measured
  p50 0 ms over 1,548 live turns, so the stage this skips is already free.
* **The carrier round trip.** Same exclusion the app's own perf trace makes.
* **Answer quality.** This is a TIMING instrument. Whether the streamed reply
  sounds better or worse is a listening question, and no number here answers it.

The baseline it is compared against is the live one, from
`logs/perf_turns.jsonl`: 1,748 turns on llama3.2:3b, time-to-first-audio
p50 2,219 ms / p90 3,395 ms / p95 7,782 ms.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics as st
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: A question the live agent actually received, and the kind that needs
#: retrieval -- so the measurement includes the slowest stage rather than
#: flattering itself on a turn that skips it.
QUESTION = "What programs do you offer?"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def _one_turn(session, question: str) -> tuple[float, float, int]:
    """Run a turn; return (time to first audio frame, total, frame count).

    The two arms do not reach "first audio" by the same route, and reporting one
    number for both would be a lie of labelling:

    * **batch** (`TTS_STREAM=0`) synthesises the whole answer before the caller
      sends anything, so first audio IS the end of the turn. The sink is never
      called on this arm.
    * **streamed** synthesises clause by clause and the sink fires mid-pipeline,
      so first audio is genuinely earlier than the end of the turn.

    Both are the honest answer to "when does the caller first hear something",
    which is why they are comparable -- but each is reported, so a reader can
    see whether a difference is in the first audio or in the tail.
    """
    import app.voice_handler as vh

    first_audio: list[float] = []

    async def sink(frames: list[bytes]) -> None:
        if frames and not first_audio:
            first_audio.append(time.perf_counter())

    async def fake_transcribe(_audio):
        return question, False

    session._transcribe = fake_transcribe
    session._total_frames = 60
    session._audio_buffer = [b"\x00" * 160] * 60
    session.begin_turn()

    t0 = time.perf_counter()
    task = asyncio.create_task(session.process_utterance(chunk_sink=sink),
                               name="turn")

    async def watchdog() -> None:
        for step in range(1, 7):
            await asyncio.sleep(10)
            if task.done():
                return
            print(f"    !! stalled {step * 10}s — pending tasks:", flush=True)
            for t in asyncio.all_tasks():
                if t is task and not t.done():
                    print(f"       turn: {t.get_coro()!r}"[:200], flush=True)
            frames = sys._current_frames()
            for th in threading.enumerate():
                f = frames.get(th.ident)
                if f and th.name not in ("MainThread",):
                    print(f"       thread {th.name!r}: "
                          f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno} "
                          f"in {f.f_code.co_name}", flush=True)

    wd = asyncio.create_task(watchdog())
    try:
        chunks, _dialogue, _end = await asyncio.wait_for(task, timeout=90)
    finally:
        wd.cancel()
    elapsed = time.perf_counter() - t0

    ttfa = (first_audio[0] - t0) * 1000.0 if first_audio else elapsed * 1000.0
    return ttfa, elapsed * 1000.0, len(chunks)


async def _one_arm_run(stream: bool, question: str) -> tuple[float, float, int]:
    """Set the flags, run one turn, restore nothing (caller owns the flags)."""
    import app.llm_backend as lb
    import app.voice_handler as vh

    vh.TTS_STREAM = stream
    lb.LLM_STREAM = stream

    session = vh.VoiceCallSession()
    session.call_id = f"MEASURE-{'streamed' if stream else 'batch'}"
    return await _one_turn(session, question)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5,
                    help="measured turns per arm (default 5)")
    ap.add_argument("--warmups", type=int, default=2,
                    help="discarded turns per arm (default 2)")
    args = ap.parse_args()

    # Mirror the app's own call timeline, so if a turn stalls the stage it
    # stalled in is printed rather than inferred. Timestamps are relative to
    # process start, which is what makes a stall visible at all.
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="    %(asctime)s %(name)s: %(message)s")
    logging.getLogger("voice_events").setLevel(logging.INFO)
    logging.getLogger("voice_handler").setLevel(logging.INFO)

    # Cold-start the engines once, outside both arms, so neither pays for a
    # model load the other does not -- the first Kokoro load alone is ~7 s.
    #
    # Warm on the MAIN thread, before the loop starts. `_get_tts_engine()` is
    # reached through `asyncio.to_thread` on the live path, and the import it
    # does (torch / onnxruntime / kokoro_onnx) took 7.3 s on the main thread but
    # did not complete in 580 s in a worker thread on this box -- which is why
    # every earlier attempt to measure anything stalled at exactly this line.
    print("pre-warming TTS/LLM on the main thread (not measured) ...", flush=True)
    from app.voice_handler import _get_tts_engine
    t_warm = time.perf_counter()
    _get_tts_engine()
    print(f"  TTS engine ready in {time.perf_counter() - t_warm:.1f} s", flush=True)

    for stream in (False, True):
        for _ in range(args.warmups):
            await _one_arm_run(stream, QUESTION)

    ttfa: dict[str, list[float]] = {"batch": [], "streamed": []}
    total: dict[str, list[float]] = {"batch": [], "streamed": []}

    # Alternated, not blocked. This box is shared with a running stack (Ollama
    # resident, two callers possible), so a drift in load would otherwise land
    # entirely on whichever arm ran second and read as a difference between
    # them.
    print(f"\nmeasuring {args.runs} turns per arm, alternating", flush=True)
    for i in range(args.runs):
        for label, stream in (("batch", False), ("streamed", True)):
            first, whole, frames = await _one_arm_run(stream, QUESTION)
            ttfa[label].append(first)
            total[label].append(whole)
            print(f"  run {i + 1:>2} {label:<9}: first_audio={first:8.1f} ms  "
                  f"turn_total={whole:8.1f} ms  frames={frames}", flush=True)

    def row(label: str) -> str:
        t = ttfa.get(label, [])
        if not t:
            return f"{label:<10} (none)"
        w = total[label]
        return (f"{label:<10}{len(t):>3}{_percentile(t, 50):>10.0f}"
                f"{_percentile(t, 95):>8.0f}{min(t):>9.0f}{max(t):>9.0f}"
                f"{_percentile(w, 50):>12.0f}")

    print("\n=== first audio / turn total ===")
    print(f"{'arm':<10}{'n':>3}{'p50':>10}{'p95':>8}{'min':>9}{'max':>9}"
          f"{'total p50':>12}")
    print(row("batch"))
    print(row("streamed"))

    b, s = ttfa.get("batch", []), ttfa.get("streamed", [])
    if b and s:
        d50 = _percentile(b, 50) - _percentile(s, 50)
        d95 = _percentile(b, 95) - _percentile(s, 95)
        print(f"\nfirst-audio improvement: p50 {d50:+.0f} ms, p95 {d95:+.0f} ms "
              f"(positive = streamed is earlier)")
        print("\nWhat this does NOT include, and must be added before comparing "
              "to the budget:")
        print("  * VAD / endpointing 600 ms (identical in both arms)")
        print("  * the carrier round trip (excluded from the live trace too)")
        print("  * STT (held fixed; measured p50 0 ms live via speculative STT)")
        print("live baseline for reference: first audio p50 2,219 ms / "
              "p95 7,782 ms (logs/perf_turns.jsonl, 1,748 turns, "
              "VAD included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
