"""US-005 acceptance tests — streaming TTS behind TTS_STREAM. No network, no model.

Covers the story's build-time contract (PO build-approved 2026-09-19, Phase 4a):
  AC-1   the first audio chunk is emitted while later clauses still synthesise
  AC-2   a cache hit is served immediately, stream or batch
  AC-3   a synthesis failure ends in the spoken fallback, never silence
  AC-4   the revert is the FLAG: TTS_STREAM off keeps the batch path unchanged
  TAC-3  the stream marks `tts_first_chunk` on the trace, with an emitter
  TAC-4  mid-stream chunks snap to whole 20 ms frames without padding gaps

The synthesiser is stubbed (a fake async `create_stream`), so this runs in
milliseconds and never touches the engine.
Run:  .venv/Scripts/python.exe doc/perf/tools/test_us005_stream_tts.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

import numpy as np                                   # noqa: E402
_TRACE_TMP = PROJ / "logs" / "us005_test_trace.jsonl"
os.environ["PERF_TRACE"] = "1"
os.environ["PERF_TRACE_FILE"] = str(_TRACE_TMP)
if _TRACE_TMP.exists():
    _TRACE_TMP.unlink()
import app.voice_handler as vh                       # noqa: E402
from app.voice_handler import VoiceCallSession       # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


class FakeKokoro:
    """Two 100 ms chunks at 24 kHz, like create_stream would produce."""

    def __init__(self, fail_after: int | None = None):
        self.fail_after = fail_after          # None = never; N = raise at chunk N
        self.calls = []

    def create(self, text, voice=None, speed=None):
        self.calls.append(("batch", text, voice, speed))
        return np.zeros(2400, dtype=np.float32), 24000

    async def create_stream(self, text, voice=None, speed=None):
        self.calls.append(("stream", text, voice, speed))
        for i in range(2):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("provider gone")
            yield (np.full(2400, 0.1 * (i + 1), dtype=np.float32), 24000)


def install_fake(fail_after: int | None = None) -> FakeKokoro:
    fake = FakeKokoro(fail_after)
    vh._get_tts_engine = lambda: fake
    return fake


def fresh_session() -> VoiceCallSession:
    s = VoiceCallSession()
    s.call_id = "MZus005test"
    # A trace context exists only inside a call; the marks below are asserted
    # on the methods' own behaviour (cache note fields), not on the trace.
    return s


print("\nUS-005 streaming TTS (TTS_STREAM flag)\n")

# ── AC-4 / the revert is the flag, and the batch path is untouched ──────────
check("AC-4   TTS_STREAM defaults OFF (floors ship disabled; DG-03 adoption gate)",
      vh.TTS_STREAM is False)
src = inspect.getsource(vh.VoiceCallSession._synthesise)
check("AC-4   the batch path still calls the batch create(), unchanged",
      "kokoro.create" in src and "create_stream" not in src)
src_po = inspect.getsource(vh.VoiceCallSession.process_utterance)
check("AC-4   the streaming branch is behind the flag",
      "if TTS_STREAM:" in src_po and "self._synthesise(answer)" in src_po)
check("AC-4   a stream failure falls back to the spoken fixed response",
      '_speak_fixed_response(reason="synthesis failed")' in src_po)

# ── TAC-4 / chunk -> frames: whole 20 ms frames, never a padded mid-gap ─────
s = fresh_session()
frames = s._chunk_to_ulaw_frames(np.zeros(2400, dtype=np.float32), 24000)
check("TAC-4   100 ms at 24 kHz -> 5 whole 20 ms frames at 8 kHz",
      len(frames) == 5 and all(len(f) == 160 for f in frames),
      f"{len(frames)} frames, sizes={sorted({len(f) for f in frames})}")
odd = s._chunk_to_ulaw_frames(np.zeros(2410, dtype=np.float32), 24000)
check("TAC-4   a sub-frame tail is DROPPED mid-stream, never padded into a gap",
      len(odd) == 5, f"{len(odd)} frames")
check("TAC-4   a chunk too small for one frame yields nothing",
      s._chunk_to_ulaw_frames(np.zeros(100, dtype=np.float32), 24000) == [])

# ── AC-1 / the sentence splitter: the first chunk must be SMALL ────────────
split = vh._split_sentences
check("AC-1   the splitter cuts at sentence boundaries",
      split("Tuition is $17,000 per year. Scholarships follow. Apply by March!") ==
      ["Tuition is $17,000 per year.", "Scholarships follow.", "Apply by March!"])
check("AC-1   a price before a sentence boundary is never split",
      split("It costs $1,700. Scholarships exist.") ==
      ["It costs $1,700. Scholarships exist."])
check("AC-1   a period followed by a lowercase word is not a boundary",
      split("e.g. something follows.") == ["e.g. something follows."])
check("AC-1   one sentence stays one chunk",
      split("A single sentence.") == ["A single sentence."])
check("AC-1   empty input yields nothing", split("   ") == [])

# ── AC-2 / a cache hit is served immediately, stream or batch ───────────────
fake0 = install_fake()
s = fresh_session()
VoiceCallSession._shared_tts_cache.clear()
cache = VoiceCallSession._shared_tts_cache
cache[("hello", vh._tts_voice(), vh._tts_speed())] = (
    np.zeros(2400, dtype=np.float32), 24000, "MZother")


async def drain(generator):
    out = []
    async for item in generator:
        out.append(item)
    return out


import asyncio  # noqa: E402

hit = asyncio.run(drain(s.synthesise_stream("hello")))
check("AC-2   a cache hit yields once, immediately, without the engine",
      len(hit) == 1 and hit[0][1] == 24000
      and not fake0.calls, "engine was touched on a hit")

# ── AC-1 / a miss streams: first chunk while the rest still synthesises ─────
fake = install_fake()
s = fresh_session()
miss = asyncio.run(drain(s.synthesise_stream("Tuition is here. Scholarships too.")))
check("AC-1   a miss yields every chunk the synthesiser produced",
      len(miss) == 4 and all(sr == 24000 for _, sr in miss))
check("AC-1   the FIRST chunk is not the last chunk (streaming, not batch)",
      not np.array_equal(miss[0][0], miss[-1][0]))
check("AC-1   the engine is called PER SENTENCE, so the first chunk is one sentence",
      len(fake.calls) == 2 and all(kind == "stream" for kind, *_ in fake.calls)
      and fake.calls[0][1] == "Tuition is here." and fake.calls[1][1] == "Scholarships too.",
      str(fake.calls))
key = ("Tuition is here. Scholarships too.", vh._tts_voice(), vh._tts_speed())
check("AC-1   the assembled audio is cached, so the DAT-11 entry shape is unchanged",
      key in cache and cache[key][1] == 24000
      and len(cache[key][0]) == 9600, "assembled length")

# ── AC-3 / a failure before any audible chunk ends in the fallback ──────────
fake = install_fake(fail_after=0)      # raise before the first chunk
s = fresh_session()


async def streamed_or_none():
    return await s._synthesise_streamed("doomed", None)

result = asyncio.run(streamed_or_none())
check("AC-3   nothing audible + failure -> None (the caller's fallback signal)",
      result is None)

# ── TAC-3 / the trace mark exists and is emitted by the streaming path ──────
import app.perf_trace as pt  # noqa: E402
assert pt.ENABLED and pt.LOG_PATH.endswith("us005_test_trace.jsonl")

check("TAC-3   tts_first_chunk is a declared stage, between llm_done and tts_done",
      "tts_first_chunk" in pt.STAGES
      and pt.STAGES.index("llm_done") < pt.STAGES.index("tts_first_chunk")
      < pt.STAGES.index("tts_done"))
check("TAC-3   the emitter marks tts_first_chunk before tts_done on a miss",
      'mark("tts_first_chunk")' in inspect.getsource(
          vh.VoiceCallSession.synthesise_stream))
check("TAC-3   the batch path has no tts_first_chunk emitter (absent = batch)",
      "tts_first_chunk" not in src)


async def trace_order():
    # A streamed turn's marks must produce the two new segments on the record.
    t = pt.TurnTrace(call_id="MZt", turn_id=0)
    for name in ("vad_end", "stt_done", "llm_sent", "retrieval_done", "llm_done",
                 "tts_first_chunk", "tts_done", "first_audio_sent"):
        t.mark(name)
    t.emit()


asyncio.run(trace_order())
import json  # noqa: E402
rec = json.loads(_TRACE_TMP.read_text(encoding="utf-8").strip().splitlines()[-1])
check("TAC-3   a streamed turn records llm_done -> tts_first_chunk",
      "seg_llm_done__tts_first_chunk_ms" in rec)
check("TAC-3   and tts_first_chunk -> tts_done (the rest of synthesis)",
      "seg_tts_first_chunk__tts_done_ms" in rec
      and "seg_tts_done__first_audio_sent_ms" in rec)

# The batch path is retained behind the flag: with it OFF the stream helpers
# are never reached on a turn -- verified live; here the flag itself.
# The sink closure rebinds the first-frame flag: without `nonlocal` the
# assignment makes it closure-local and the read-before-assign raises
# UnboundLocalError on the very first chunk (observed live 2026-09-20).
_main_src = (PROJ / "app" / "main.py").read_text(encoding="utf-8")
check("TAC-5   the send closure declares nonlocal for the first-frame flag",
      "nonlocal _marked_first_frame" in _main_src)

check("AC-4   the flag is a single, documented setting (BRD-15 revert shape)",
      "TTS_STREAM=0" in open(".env.example", encoding="utf-8").read())

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
