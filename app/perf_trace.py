"""
Per-turn latency tracing — MOD-06, TRD-20 / TRD-21. Implements US-001.

One JSON line per voice turn, carrying each stage boundary the turn passed
plus the inference engine's own counters. Read by the harness (US-002) and the
evaluation runner (US-003); never read by anything on the turn path.

Four contracts, each enforced by construction rather than by test:

  TRD-20  Dependency-free. This module imports stdlib only — no `app.*`. That
          is what makes "tracing cannot fail a call" structurally true: there
          is nothing here that can fail to import, and every public method
          swallows its own exceptions.
  TRD-21  Absent is not zero. A stage that did not run is ABSENT from the
          record, never reported as 0 ms. `stages_seen` is always present so a
          reader can tell a short turn from a broken one.
  TAC-4   No caller PII. The record carries no transcript text, no phone
          number, no caller-supplied string. `call_id` is the carrier's opaque
          stream id; notes carry counts, flags and engine integers only.
  REC-12  Re-based, not rewritten. The draft this replaces had no retrieval
          mark and captured no engine counters, so `retrieval_ms` — the metric
          BRD-01 names first — was uncomputable from it.

Stage set and why each exists:

  vad_end            trailing-silence trigger fires (the 600 ms RMS gate).
                     Zero point for processing_ms. NOT caller_speech_end —
                     see BRD-02's canonical-clock definition; endpointing_ms
                     is the gap the caller experiences before this mark.
  stt_done           Whisper returned a final transcript.
  retrieval_done     retrieval returned chunks. Marked from app/rag.py via
                     mark_current(); this is what makes retrieval_ms derivable.
  llm_sent           the blocking Ollama chat request was issued.
  llm_first_token    FIRST STREAMED TOKEN. Deliberately never marked while the
                     LLM call is non-streaming (llm_backend.py:145): with no
                     streaming there is no first-token event, and marking
                     llm_sent's instant here would fabricate a TTFT. It starts
                     appearing when US-004 lands. Its absence is the honest
                     signal that TTFT is not yet measurable.
  llm_done           the full completion returned.
  tts_done           Kokoro returned the complete audio buffer.
  first_audio_sent   first media frame written to the Twilio stream. End of
                     the measured segment in BRD-02.

Output: logs/perf_turns.jsonl, one object per turn, append-only.
"""
from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from pathlib import Path

ENABLED = os.environ.get("PERF_TRACE", "1") == "1"
LOG_PATH = os.environ.get("PERF_TRACE_FILE", os.path.join("logs", "perf_turns.jsonl"))

#: Ordered marks. Durations are computed between consecutive marks that are
#: present, so a turn that skips stages still yields a coherent timeline.
#:
#: ORDER MATTERS, and the obvious order was wrong. Retrieval happens INSIDE the
#: blocking LLM call (query_rag -> retrieve_context), so `retrieval_done` fires
#: AFTER `llm_sent`, not before it. Listing it earlier produced a negative
#: `seg_retrieval_done__llm_sent` on the first real trace — caught by running
#: against a live call, not by inspection.
STAGES = (
    "vad_end",
    "stt_done",
    "llm_sent",
    "retrieval_done",
    # "llm_first_token" REMOVED 2026-09-19 -- declared here and emitted by
    # NOTHING, so every full turn reported `stages_seen: 7` against
    # `stages_expected: 8`, measured over 200 consecutive turns.
    #
    # That is worse than a missing mark. An absent mark is a fact about one turn;
    # a declared-but-unemittable stage makes the count permanently short on EVERY
    # turn, so a consumer cannot tell "this feature has not shipped" from "a stage
    # silently failed" -- and the count that exists precisely to make absence
    # visible becomes noise. `test_us001_tracer.py:102` asserts the RECORD lacks
    # the key, which is correct for a non-streaming engine, while this tuple
    # counted it as expected. The two disagreed and the tuple was wrong.
    #
    # US-004 (streaming) is where it comes back, WITH an emitter. Re-add it then,
    # not before: this tuple is a claim about what a turn can produce.
    "llm_done",
    # "tts_first_chunk" (US-005, streaming TTS) — the instant the FIRST audio
    # chunk of the answer left the synthesiser. Absent on the batch path (a
    # fact about that turn, like any skipped stage); present on streamed
    # turns, where llm_done->tts_first_chunk is the wait the caller pays for
    # the first audible audio and tts_first_chunk->first_audio_sent is the
    # framing/socket leg. Emitted only by the streaming emitter, never counted
    # without one (the llm_first_token lesson above).
    "tts_first_chunk",
    "tts_done",
    "first_audio_sent",
)

#: The active trace for the current call. Each carrier WebSocket is its own
#: asyncio Task with its own context copy, so concurrent callers get isolated
#: traces by construction (BRD-06). asyncio.to_thread copies the context, which
#: is how notes from inside the LLM worker thread reach the right turn.
_active: ContextVar["TurnTrace | None"] = ContextVar("perf_active_trace", default=None)


class TurnTrace:
    """Latency marks for one caller turn. Every method is non-throwing."""

    __slots__ = ("call_id", "turn_id", "_t0", "_marks", "_notes")

    def __init__(self, call_id: str, turn_id: int):
        self.call_id = call_id
        self.turn_id = turn_id
        self._t0 = time.monotonic()
        self._marks: dict[str, float] = {}
        self._notes: dict[str, object] = {}

    def mark(self, name: str) -> None:
        """Record a stage boundary. First write wins; later ones are ignored."""
        try:
            self._marks.setdefault(name, time.monotonic())
        except Exception:
            pass

    def note(self, **fields) -> None:
        """Attach non-timing context: counts, flags, engine integers. No PII."""
        try:
            self._notes.update(fields)
        except Exception:
            pass

    def emit(self) -> None:
        """Append one JSON line. Silently does nothing if disabled or failing."""
        try:
            if not ENABLED:
                return
            base = self._marks.get("vad_end", self._t0)
            rec: dict[str, object] = {
                "call_id": self.call_id,
                "turn_id": self.turn_id,
                "ts": round(time.time(), 3),
            }

            prev_name = prev_t = None
            for stage in STAGES:
                t = self._marks.get(stage)
                if t is None:
                    continue                       # absent, not zero (TRD-21)
                rec[f"{stage}_ms"] = round((t - base) * 1000, 1)
                if prev_t is not None:
                    rec[f"seg_{prev_name}__{stage}_ms"] = round((t - prev_t) * 1000, 1)
                prev_name, prev_t = stage, t

            if prev_t is not None:
                rec["total_ms"] = round((prev_t - base) * 1000, 1)
                rec["processing_ms"] = rec["total_ms"]

            # Always present, so a short turn is distinguishable from a
            # truncated record (TAC-6).
            rec["stages_seen"] = sum(1 for s in STAGES if s in self._marks)
            rec["stages_expected"] = len(STAGES)

            # retrieval_ms is MEASURED, not derived. `retrieval_done` is marked
            # by app/rag.py on every return path, so this segment is a direct
            # observation.
            #
            # It was previously derived by subtracting the engine's counters
            # from the llm window. That was wrong twice over: it omitted model
            # LOAD (inflating retrieval to 11.7 s on one trace), and it omitted
            # Ollama QUEUE time, which is large under two-caller load because
            # the engine's own counters measure compute only. Both errors
            # inflated retrieval — it read 5,432 ms when the real figure was
            # 2,227 ms. Derivation by subtraction keeps absorbing whatever it
            # was not told about; a direct mark cannot.
            if {"llm_sent", "retrieval_done"} <= self._marks.keys():
                rec["retrieval_ms"] = round(
                    (self._marks["retrieval_done"] - self._marks["llm_sent"]) * 1000, 1)

            # The rest of the LLM window, accounted for honestly. Whatever is
            # left after load, prefill and generation is engine queue time —
            # named, so it cannot masquerade as retrieval again.
            pre = self._notes.get("prefill_ms")
            gen = self._notes.get("generation_ms")
            load = self._notes.get("load_ms")
            if ({"llm_sent", "llm_done"} <= self._marks.keys()
                    and isinstance(pre, (int, float))
                    and isinstance(gen, (int, float))):
                wall = (self._marks["llm_done"] - self._marks["llm_sent"]) * 1000
                load_ms = float(load) if isinstance(load, (int, float)) else 0.0
                ret_ms = rec.get("retrieval_ms", 0.0) or 0.0
                queue = wall - float(pre) - float(gen) - load_ms - float(ret_ms)
                rec["llm_window_ms"] = round(wall, 1)
                rec["llm_queue_ms"] = round(queue, 1)
                rec["llm_queue_derivable"] = queue >= 0

            rec.update(self._notes)

            path = Path(LOG_PATH)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass


# ── Module-level API used by the turn path and by MOD-02/MOD-03 ──────────

def new_trace(call_id: str, turn_id: int) -> TurnTrace:
    """Open a trace and make it active for this context. Never raises."""
    try:
        trace = TurnTrace(call_id, turn_id)
        _active.set(trace)
        return trace
    except Exception:
        return TurnTrace("", 0)


def current() -> "TurnTrace | None":
    """The active trace, or None. Safe from any thread that inherited context."""
    try:
        return _active.get()
    except Exception:
        return None


def mark_current(name: str) -> None:
    """Mark a stage on the active trace. No-op when there is none."""
    try:
        t = _active.get()
        if t is not None:
            t.mark(name)
    except Exception:
        pass


def note_current(**fields) -> None:
    """Attach notes to the active trace. No-op when there is none."""
    try:
        t = _active.get()
        if t is not None:
            t.note(**fields)
    except Exception:
        pass


def emit_current() -> None:
    """Emit the active trace. No-op when there is none."""
    try:
        t = _active.get()
        if t is not None:
            t.emit()
    except Exception:
        pass
