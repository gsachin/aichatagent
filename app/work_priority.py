"""Background work yields to the caller, by policy (US-017 / BRD-20).

`BRD-20` states the operating scenario this box actually has to survive: **two
concurrent voice callers plus one background chat/admin request**, as an
acceptance condition rather than a footnote. Nothing owned the enforcement —
`08-coverage-verification.md` recorded it as an unowned gap — so a background
request could be admitted between the pieces of a caller's turn and be paid for
by someone who is actually waiting on the line.

The policy is one sentence: **a background unit never starts while a voice turn
is in flight, and at most one runs at a time.** Everything here exists to make
that sentence true and to make its violation visible in the records.

Ordering, not throughput
------------------------
This module asserts no throughput figure. The invariant is *who is admitted
while whom is in flight*, which is binary and therefore not a statistic that
can be argued with. Every timing the records carry is a measurement.

Failure direction
-----------------
`TAC-1`: a unit that arrives unclassified is admitted **as voice**, because the
failure that matters is a caller starved, not a background job let through. An
unclassified unit is admitted and recorded as a classification defect rather
than refused — refusing it would turn a labelling bug into a caller-visible
outage.

No spinning
-----------
`TAC-7`: a deferred unit blocks on a `threading.Condition`. A poll loop holding
a core would breach `BRD-12` while doing no useful work.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

#: The two classes of work that enter retrieval or inference. There is
#: deliberately no third: a class that is neither is admitted as voice.
VOICE = "voice"
BACKGROUND = "background"
CLASSES = (VOICE, BACKGROUND)


class BackgroundDeferred(Exception):
    """A background unit was refused because the line stayed busy past its budget.

    TAC-6: a deferral is never silently dropped, and a refusal names its reason.
    So a refusal is an exception carrying that reason rather than a quiet
    return, and the caller decides what a background request that could not run
    should answer -- never the caller-facing path, which must not see it at all
    (AC-2).
    """


def classify(mode: str | None) -> tuple[str, str | None]:
    """Map a caller-supplied mode onto a work class.

    Returns `(work_class, defect)`. `defect` is a reason string when the input
    was not recognisable, so an unclassified unit is still admitted — as voice —
    and the labelling bug is recorded rather than silently converted into a
    refusal (TAC-1).
    """
    value = (mode or "").strip().lower()
    if value in ("voice", "call", "phone"):
        return VOICE, None
    if value in ("chat", "background", "admin", "text", "streamlit", "whatsapp"):
        return BACKGROUND, None
    return VOICE, f"unclassified mode {mode!r} admitted as voice"


@dataclass
class _Records:
    """TAC-6: deferral is recorded, and silence is prohibited."""

    deferrals: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    classification_defects: list[dict] = field(default_factory=list)
    background_starts_during_voice: list[dict] = field(default_factory=list)
    max_background_concurrent: int = 0
    voice_turns: int = 0
    background_units: int = 0

    def snapshot(self) -> dict:
        return {
            "voice_turns": self.voice_turns,
            "background_units": self.background_units,
            "deferrals": len(self.deferrals),
            "deferral_records": list(self.deferrals[-20:]),
            "refusals": len(self.refusals),
            "refusal_records": list(self.refusals[-20:]),
            "classification_defects": len(self.classification_defects),
            "classification_defect_records": list(self.classification_defects[-20:]),
            "background_starts_during_voice": len(self.background_starts_during_voice),
            "background_start_violation_records": list(self.background_starts_during_voice[-20:]),
            "max_background_concurrent": self.max_background_concurrent,
        }


class WorkGate:
    """Admission control for the single event loop this box has.

    `max_background` defaults to 1 (`TAC-3`): bounding the residual overlap
    exposure to one dependency call, rather than leaving it proportional to the
    number of background requests that happen to arrive together.
    """

    def __init__(self, max_background: int = 1, defer_timeout_s: float = 30.0):
        self._cv = threading.Condition()
        self._voice_active = 0
        self._background_active = 0
        self._max_background = max(1, int(max_background))
        self._defer_timeout_s = float(defer_timeout_s)
        self.records = _Records()

    # ── introspection ──────────────────────────────────────────────────
    @property
    def voice_active(self) -> int:
        with self._cv:
            return self._voice_active

    @property
    def background_active(self) -> int:
        with self._cv:
            return self._background_active

    def snapshot(self) -> dict:
        with self._cv:
            out = self.records.snapshot()
            out.update({"voice_active": self._voice_active,
                        "background_active": self._background_active,
                        "max_background": self._max_background})
            return out

    def reset_records(self) -> None:
        with self._cv:
            self.records = _Records()

    # ── admission ──────────────────────────────────────────────────────
    @contextmanager
    def voice_turn(self, label: str = ""):
        """A caller's turn. Background work is held off for its whole duration."""
        with self._cv:
            self._voice_active += 1
            self.records.voice_turns += 1
        try:
            yield
        finally:
            with self._cv:
                self._voice_active -= 1
                if self._voice_active == 0:
                    # Wake the waiters exactly when the reason to wait is gone.
                    self._cv.notify_all()

    def _acquire_background(self, label: str) -> None:
        """Wait for the background slot. BLOCKS THE CALLING THREAD.

        **Never call this on the event loop.** The wait is a
        `threading.Condition.wait`, so a caller on the loop thread freezes the
        loop -- and with it every voice WebSocket, its VAD, its media stream and
        its keepalive. That is not a slow caller, it is a dead one.

        That is exactly what happened: `test_pipeline_with_text` is `async` and
        used the synchronous `background_unit`, so a deferred text query stopped
        both live calls until the query got its slot. Both sessions died on
        `keepalive ping timeout` after 3-18 turns, and the cause read as engine
        contention for as long as nobody followed the call graph into the loop.

        Async callers use `background_unit_async`, which runs this on a worker
        thread so the loop keeps turning.
        """
        started_wait = time.monotonic()
        deadline = started_wait + self._defer_timeout_s
        with self._cv:
            while (self._voice_active > 0
                   or self._background_active >= self._max_background):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # A timed wait on a condition: parks the thread, holds no core,
                # and is woken by voice_turn's notify_all rather than by polling.
                self._cv.wait(timeout=min(remaining, 1.0))
            waited_ms = (time.monotonic() - started_wait) * 1000.0

            # The budget expired and the line is still busy. The unit is
            # REFUSED, not admitted: falling through and starting anyway would
            # make TAC-2's invariant advisory, true only while nothing waited
            # long enough to time out. TAC-6 requires the refusal to name its
            # reason rather than be silently dropped.
            if self._voice_active > 0:
                self.records.background_starts_during_voice.append(
                    {"label": label, "voice_active": self._voice_active,
                     "waited_ms": round(waited_ms, 1), "ts": time.time()})
                self.records.refusals.append(
                    {"label": label, "reason": "defer budget expired; voice still in flight",
                     "waited_ms": round(waited_ms, 1), "voice_active": self._voice_active,
                     "ts": time.time()})
                raise BackgroundDeferred(
                    f"{label!r} deferred {waited_ms:.0f} ms and the line was still busy")

            self._background_active += 1
            self.records.background_units += 1
            self.records.max_background_concurrent = max(
                self.records.max_background_concurrent, self._background_active)
            if waited_ms >= 1.0:
                self.records.deferrals.append(
                    {"label": label, "waited_ms": round(waited_ms, 1), "ts": time.time()})

    def _release_background(self) -> None:
        with self._cv:
            self._background_active -= 1
            self._cv.notify_all()

    def _note_classification_defect(self, label: str, mode: str | None, defect: str) -> None:
        with self._cv:
            self.records.classification_defects.append(
                {"label": label, "mode": mode, "reason": defect, "ts": time.time()})

    @contextmanager
    def background_unit(self, label: str = "", mode: str | None = None):
        """A unit of work nobody is waiting to hear. SYNCHRONOUS CALLERS ONLY.

        Safe from a worker thread -- `run_rag_query_sync` runs under
        `asyncio.to_thread`. From async code use `background_unit_async`; using
        this there blocks the event loop and kills live calls.
        """
        work_class, defect = classify(mode) if mode is not None else (BACKGROUND, None)
        if defect is not None:
            self._note_classification_defect(label, mode, defect)
            with self.voice_turn(label=f"unclassified:{label}"):
                yield
            return

        if work_class == VOICE:
            with self.voice_turn(label=label):
                yield
            return

        self._acquire_background(label)
        try:
            yield
        finally:
            self._release_background()

    @asynccontextmanager
    async def background_unit_async(self, label: str = "", mode: str | None = None):
        """The async variant: the wait runs on a WORKER thread, never the loop.

        Same gate, same records, same refusal. The only difference is that a
        deferred unit no longer stops the process it is deferring to.
        """
        work_class, defect = classify(mode) if mode is not None else (BACKGROUND, None)
        if defect is not None:
            self._note_classification_defect(label, mode, defect)
            with self.voice_turn(label=f"unclassified:{label}"):
                yield
            return

        if work_class == VOICE:
            with self.voice_turn(label=label):
                yield
            return

        # The blocking wait goes to a worker thread. to_thread yields the event
        # loop for its whole duration, which is the entire point.
        await asyncio.to_thread(self._acquire_background, label)
        try:
            yield
        finally:
            self._release_background()


#: Process-wide gate. One box, one event loop, one admission decision point
#: (US-016 owns the caller-facing half; this owns the unit-of-work half, and
#: they are deliberately the same gate rather than two that can disagree).
#:
#: Both values resolve through app/config.py (US-011 TAC-1). BG_PRIORITY_ENABLED
#: deliberately does NOT: it is read per call so the gate can be switched on and
#: off in-process, and it is on config_truth's dynamic allowlist with that reason.
from app.config import settings

GATE = WorkGate(
    max_background=settings.BG_MAX_CONCURRENT,
    defer_timeout_s=settings.BG_DEFER_TIMEOUT_S,
)


def policy_status() -> dict:
    """Operational snapshot, for logs and the load harness."""
    return {
        "max_background": GATE._max_background,
        "defer_timeout_s": GATE._defer_timeout_s,
        "enabled": os.environ.get("BG_PRIORITY_ENABLED", "1") not in ("0", "false", "off"),
        **GATE.snapshot(),
    }
