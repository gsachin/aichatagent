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

import os
import threading
import time
from contextlib import contextmanager
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

    @contextmanager
    def background_unit(self, label: str = "", mode: str | None = None):
        """A unit of work nobody is waiting to hear.

        Defers until no voice turn is in flight and the background slot is
        free, and records how long it waited. `TAC-2` is the invariant this
        enforces; the recorded timestamp is the evidence for it.
        """
        work_class, defect = classify(mode) if mode is not None else (BACKGROUND, None)
        if defect is not None:
            with self._cv:
                self.records.classification_defects.append(
                    {"label": label, "mode": mode, "reason": defect, "ts": time.time()})
            with self.voice_turn(label=f"unclassified:{label}"):
                yield
            return

        if work_class == VOICE:
            with self.voice_turn(label=label):
                yield
            return

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
            # REFUSED, not admitted.
            #
            # This used to fall through and start anyway, recording the breach
            # after the fact. That made TAC-2's invariant advisory: "zero
            # background starts during a voice turn" was true only while
            # nothing waited long enough to time out, and a caller on a long
            # turn would eventually have background work running inside it.
            # A refusal keeps the invariant absolute, and TAC-6 requires the
            # refusal to name its reason rather than be silently dropped.
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
        try:
            yield
        finally:
            with self._cv:
                self._background_active -= 1
                self._cv.notify_all()


#: Process-wide gate. One box, one event loop, one admission decision point
#: (US-016 owns the caller-facing half; this owns the unit-of-work half, and
#: they are deliberately the same gate rather than two that can disagree).
GATE = WorkGate(
    max_background=int(os.environ.get("BG_MAX_CONCURRENT", "1") or 1),
    defer_timeout_s=float(os.environ.get("BG_DEFER_TIMEOUT_S", "30") or 30),
)


def policy_status() -> dict:
    """Operational snapshot, for logs and the load harness."""
    return {
        "max_background": GATE._max_background,
        "defer_timeout_s": GATE._defer_timeout_s,
        "enabled": os.environ.get("BG_PRIORITY_ENABLED", "1") not in ("0", "false", "off"),
        **GATE.snapshot(),
    }
