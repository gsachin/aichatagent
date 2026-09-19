"""Two-caller admission control and the deterministic fixed response (US-016).

`BRD-05` sizes this box for two concurrent callers. A third call has no
capacity to run in, and `06-architecture.md` §5 / `MOD-01` R3 own the rule as
one sentence -- *refuse a third call rather than degrade all three* -- with no
implementation contract behind it. `BRD-13` adds a second obligation of the
same shape: losing the inference engine must end in a **deterministic fixed
response**, which the requirement calls a build item, not an accepted gap.

Both obligations deliver the same thing at the moment they matter: *a caller
hears a prepared sentence, with no model and no synthesiser running at request
time.* That is why they are one story and one asset set.

Four outcomes, never collapsed
------------------------------
`served`, `degraded`, `failed`, `refused`. A refusal is **not** a failure
(AC-2): the service worked, it declined to over-commit. Counting a refusal
inside an availability or failure figure is a defect, so the counters are kept
separate here rather than summed for convenience.

The refusal happens at the carrier-facing endpoint
--------------------------------------------------
An inbound PSTN call cannot be declined by the app -- the carrier answers it
either way. The app's only lever is the TwiML it returns: speak the busy
message from a pre-synthesised asset and connect no media stream. No session,
no history and no KV allocation is created for that call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("admission")

STATIC_AUDIO_DIR = Path(__file__).resolve().parent / "static" / "audio"
BUSY_ASSET = "busy.wav"
FIXED_RESPONSE_ASSET = "fixed_response.wav"
ASSET_MANIFEST = "manifest.json"

#: The words a caller may hear, in one place, because they are used twice: the
#: build script renders them to the asset set, and the refusal path falls back
#: to the carrier's own `<Say>` when the asset is not on disk. `app/static/`
#: is gitignored, so a fresh clone has no assets until the build script runs --
#: and a refusal that produced a silent disconnect would be a worse failure
#: than the over-capacity call it exists to prevent.
#:
#: AC-5 constrains both strings: no internal vocabulary (no "error", "timeout",
#: "connection", no model name) and no promise the surviving components cannot
#: keep. `scripts/build_call_assets.py` enforces the rules at build time and
#: the test suite re-asserts them, so a hand-edited line cannot reach a caller.
BUSY_TEXT = (
    "Thank you for calling Meridian University Admissions. "
    "All of our advisors are on calls at the moment. "
    "Please try again in a few minutes and we will be glad to help."
)

FIXED_RESPONSE_TEXT = (
    "I'm sorry, I didn't catch that properly just now. "
    "Could you say that again for me?"
)

#: The four outcomes (AC-2). Ordered by nothing: they are distinct categories,
#: and any summary that adds two of them together is wrong.
OUTCOMES = ("served", "degraded", "failed", "refused")


def enabled() -> bool:
    """US-016 revert: one setting, no code change (`BRD-15`)."""
    return os.environ.get("ADMISSION_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


def max_concurrent() -> int:
    """Capacity from configuration; the `BRD-05` sizing is two.

    The key is read through a literal here, not via an `_env_int(name)`
    helper. A helper takes the key as a variable, and `US-011`'s reader sweep
    looks for genuine env lookups by name -- so the indirect form is reported
    as an unread key, correctly, because the sweep cannot prove it is read.
    That is the gate working: the first version of this function did use a
    helper and the boot gate refused it.
    """
    try:
        return max(1, int(os.environ.get("MAX_CONCURRENT_CALLS", "2")))
    except (TypeError, ValueError):
        return 2


# ── the live-session registry ──────────────────────────────────────────────

@dataclass
class Decision:
    """A single-valued admission outcome, with the input that produced it.

    AC-4: admitted or refused, never both and never neither, and the live count
    that produced the decision travels with it so the record can be checked
    rather than trusted.
    """

    admitted: bool
    live: int
    limit: int
    call_sid: str = ""
    reason: str = ""

    def as_record(self) -> dict:
        return {
            "outcome": "admitted" if self.admitted else "refused",
            "live_at_decision": self.live,
            "limit": self.limit,
            "call_sid": self.call_sid,
            "reason": self.reason,
            "ts": time.time(),
        }


@dataclass
class _Records:
    decisions: list[dict] = field(default_factory=list)
    #: Counted separately from failures, and named separately. A refusal that
    #: ends up inside a failure or availability figure is the defect AC-2 names.
    refused: int = 0
    admitted: int = 0
    #: Turn outcomes, which are NOT the same axis as admission decisions: a
    #: call is admitted or refused, a turn is served, degraded or failed. The
    #: `outcomes` mapping reports the turn axis, because that is the one the
    #: failure-capability matrix counts and the one a summary must not collapse.
    served_turns: int = 0
    failed_turns: int = 0
    asset_plays: list[dict] = field(default_factory=list)
    fixed_response_plays: int = 0
    engine_calls_at_refusal: int = 0
    synthesis_calls_at_refusal: int = 0

    def snapshot(self) -> dict:
        return {
            "outcomes": {
                "served": self.served_turns,
                "degraded": self.fixed_response_plays,
                "failed": self.failed_turns,
                "refused": self.refused,
            },
            "decisions": len(self.decisions),
            "admitted": self.admitted,
            "decision_records": list(self.decisions[-20:]),
            "asset_plays": len(self.asset_plays),
            "asset_play_records": list(self.asset_plays[-20:]),
            "engine_calls_at_refusal": self.engine_calls_at_refusal,
            "synthesis_calls_at_refusal": self.synthesis_calls_at_refusal,
        }


class Admission:
    """Live-session registry and the single admission decision point.

    The decision is taken from the live session count (TAC-1). Sessions are
    registered by the media-stream handler and released when it ends, so the
    count is the real lifecycle rather than a request counter -- a refused call
    never becomes a session, and therefore can never inflate the count it was
    refused by.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._live: dict[str, float] = {}
        self.records = _Records()

    # ── lifecycle ──────────────────────────────────────────────────────
    def register(self, call_sid: str) -> None:
        """Record a session as live. Called once the media stream starts."""
        with self._lock:
            self._live[call_sid or f"anon-{time.time()}"] = time.monotonic()

    def release(self, call_sid: str) -> None:
        with self._lock:
            self._live.pop(call_sid, None)

    @property
    def live_count(self) -> int:
        with self._lock:
            return len(self._live)

    def live_ids(self) -> list[str]:
        with self._lock:
            return list(self._live)

    # ── the decision ───────────────────────────────────────────────────
    def decide(self, call_sid: str = "") -> Decision:
        """Would this call be admitted right now? Single-valued and recorded."""
        limit = max_concurrent()
        with self._lock:
            live = len(self._live)
            admitted = (not enabled()) or live < limit
            d = Decision(admitted=admitted, live=live, limit=limit, call_sid=call_sid,
                         reason="" if admitted else
                         f"{live} session(s) live at the {limit}-call limit")
            self.records.decisions.append(d.as_record())
            if admitted:
                self.records.admitted += 1
            else:
                self.records.refused += 1
        return d

    def note_served(self) -> None:
        """A turn completed with generated speech: the `served` outcome."""
        with self._lock:
            self.records.served_turns += 1

    def note_failed(self) -> None:
        """A turn ended with no audio at all: the `failed` outcome.

        Kept apart from `degraded` (the caller heard the fixed response) and
        from `refused` (no turn began). Three different things, three counters.
        """
        with self._lock:
            self.records.failed_turns += 1

    def note_asset_played(self, asset: str, call_sid: str = "") -> None:
        """Record that a caller heard a prepared asset rather than generated audio."""
        with self._lock:
            self.records.asset_plays.append(
                {"asset": asset, "call_sid": call_sid, "ts": time.time()})
            if asset == FIXED_RESPONSE_ASSET:
                self.records.fixed_response_plays += 1

    def snapshot(self) -> dict:
        with self._lock:
            out = self.records.snapshot()
            out.update({"live_sessions": len(self._live), "live_ids": list(self._live),
                        "limit": max_concurrent(), "enabled": enabled()})
            return out

    def reset_records(self) -> None:
        with self._lock:
            self.records = _Records()


REGISTRY = Admission()


def outcome_of(served: bool, degraded: bool, refused: bool) -> str:
    """Name the outcome rather than collapsing it into a single figure."""
    if refused:
        return "refused"
    if degraded:
        return "degraded"
    return "served" if served else "failed"


# ── the pre-synthesised asset set ──────────────────────────────────────────

def asset_path(name: str) -> Path:
    return STATIC_AUDIO_DIR / name


def asset_available(name: str) -> bool:
    p = asset_path(name)
    return p.is_file() and p.stat().st_size > 0


def asset_sha256(name: str) -> str | None:
    """Hash of a served asset -- TAC-4 checks byte-identity through this."""
    p = asset_path(name)
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_manifest() -> dict:
    p = asset_path(ASSET_MANIFEST)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def asset_drift() -> str | None:
    """AC-3: report when the live voice has moved away from the recorded one.

    The assets are the assistant's own voice, produced once ahead of time. If
    `KOKORO_VOICE` or `KOKORO_SPEED` changes afterwards, a caller would hear the
    recorded voice differ from the one answering them -- so the drift is
    reported at boot, before anyone is on the line.
    """
    manifest = read_manifest()
    if not manifest:
        return "no asset manifest; the pre-synthesised call assets are missing"
    current = {
        "voice": os.environ.get("KOKORO_VOICE", "af_heart").strip() or "af_heart",
        "speed": float(os.environ.get("KOKORO_SPEED", "1.0") or 1.0),
    }
    drift = [f"{k}: recorded {manifest.get(k)!r} vs live {v!r}"
             for k, v in current.items()
             if k in manifest and manifest.get(k) != v]
    if drift:
        return ("call assets were produced with a different voice configuration ("
                + "; ".join(drift)
                + ") -- regenerate them: .venv/Scripts/python.exe scripts/build_call_assets.py")
    missing = [n for n in (BUSY_ASSET, FIXED_RESPONSE_ASSET) if not asset_available(n)]
    if missing:
        return f"call asset(s) missing or empty: {', '.join(missing)}"
    return None


def status() -> dict:
    """Operational snapshot for logs, the boot gate and the harness."""
    return {
        "enabled": enabled(),
        "max_concurrent_calls": max_concurrent(),
        "assets": {n: {"present": asset_available(n), "sha256": asset_sha256(n)}
                   for n in (BUSY_ASSET, FIXED_RESPONSE_ASSET)},
        "asset_drift": asset_drift(),
        **REGISTRY.snapshot(),
    }
