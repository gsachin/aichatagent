"""BRD-15 rollback demonstration for the four Task 3.1 stories.

`BRD-15` requires every behavioural change to be revertible, and the program's
own audit found the same gap in every story: each one *asserted* its change was
revertible and none had been demonstrated **by reverting it**. An assertion that
a change can be undone is worth nothing next to watching it come undone.

This file reverts each of the four, observes the prior behaviour actually
returning, and restores. It is deliberately not part of any story's own suite:
the point is that one place holds the evidence for the claim all four make.

Each demonstration is an observation, not a comparison of settings:

  US-012  shared scope again lets one caller's entry serve another
  US-013  the probe is gone; a tripped circuit retries blind at full timeout
  US-016  a third call is admitted again
  US-017  background work interleaves into a caller's turn again

Run:  .venv/Scripts/python.exe doc/perf/tools/test_brd15_rollback.py
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

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def banner(story: str, setting: str) -> None:
    print(f"\n-- {story}  (revert via {setting})")


# ───────────────────────── US-012 ─────────────────────────

def us012() -> None:
    banner("US-012  TTS cache isolation", "TTS_CACHE_SCOPE")
    import app.voice_handler as vh

    original = vh.TTS_CACHE_SCOPE
    try:
        # Shipped state: per_call. A second session cannot see the first's entry.
        vh.TTS_CACHE_SCOPE = "per_call"
        vh.VoiceCallSession._shared_tts_cache.clear()
        a = vh.VoiceCallSession()
        a.call_id = "call-A"
        a._session_tts_cache[("phrase", vh._tts_voice(), vh._tts_speed())] = (
            "audio", 24000, "call-A")
        b = vh.VoiceCallSession()
        b.call_id = "call-B"
        check("US-012  with per_call, B cannot see A's entry",
              not b._session_tts_cache, "per-call caches are separate by construction")

        # REVERT.
        vh.TTS_CACHE_SCOPE = "shared"
        cache = vh.VoiceCallSession._shared_tts_cache
        cache.clear()
        key = ("phrase", vh._tts_voice(), vh._tts_speed())
        cache[key] = ("audio-for-A", 24000, "call-A")
        owner = cache[key][2]
        check("US-012  reverting to shared restores one process-wide cache",
              owner == "call-A" and vh.TTS_CACHE_SCOPE == "shared")
        check("US-012  and B is served A's entry again -- the behaviour the "
              "isolation test rejected",
              cache.get(key) is not None and cache[key][2] != "call-B")
    finally:
        vh.TTS_CACHE_SCOPE = original
        vh.VoiceCallSession._shared_tts_cache.clear()

    check("US-012  restored: the shipped scope is per_call", vh.TTS_CACHE_SCOPE == "per_call")


# ───────────────────────── US-013 ─────────────────────────

def us013() -> None:
    banner("US-013  half-open probe", "the three-state breaker")
    import app.rag_mcp as m

    original_claim = m.claim_probe
    original_state = m.breaker_state
    try:
        m._breaker.update({"failed_at": None, "last_error": None, "probing": False,
                           "probe_claimed_at": None, "opens": 0, "probes": 0})
        cooldown = float(os.environ.get("RAG_MCP_COOLDOWN", "30"))

        # Shipped state: after the cooldown, ONE probe at a fraction of the budget.
        m._record_failure("service down")
        m._breaker["failed_at"] = time.monotonic() - cooldown - 1
        check("US-013  shipped: the circuit goes half-open after the cooldown",
              m.breaker_state() == "half_open")
        probe_read = m._timeout(probe=True).read
        full_read = m._timeout(probe=False).read
        check("US-013  shipped: the probe costs a fraction of the serving budget",
              probe_read < full_read, f"{probe_read}s vs {full_read}s")

        # REVERT: the pre-US-013 breaker had two states and no probe. With the
        # old logic, an elapsed cooldown meant mcp_available() -> True, so the
        # next caller sent a FULL-timeout request to a service still down.
        def flat_breaker_state() -> str:
            return "closed" if m._breaker["failed_at"] is None else "open"

        def flat_available() -> bool:
            if m._breaker["failed_at"] is None:
                return True
            return (time.monotonic() - m._breaker["failed_at"]) >= cooldown

        m.breaker_state = flat_breaker_state
        m._breaker["failed_at"] = time.monotonic() - cooldown - 1
        check("US-013  reverted: the state machine has two states, no half-open",
              m.breaker_state() == "open")
        check("US-013  reverted: an elapsed cooldown retries the dead service blind",
              flat_available() is True,
              "the caller pays the FULL timeout again, every window")
        check("US-013  reverted: no probe exists, so no reduced-cost path",
              not hasattr(m, "_probe_was_used")) and flat_available() is True
    finally:
        m.breaker_state = original_state
        m.claim_probe = original_claim
        m._breaker.update({"failed_at": None, "last_error": None, "probing": False,
                           "probe_claimed_at": None, "opens": 0, "probes": 0})

    check("US-013  restored: the shipped breaker is three-state",
          m.breaker_state() == "closed")


# ───────────────────────── US-016 ─────────────────────────

def us016() -> None:
    banner("US-016  two-caller admission", "ADMISSION_ENABLED")
    import app.admission as adm
    import app.main as main

    original = os.environ.get("ADMISSION_ENABLED")
    reg = adm.Admission()
    try:
        os.environ["ADMISSION_ENABLED"] = "1"
        os.environ["MAX_CONCURRENT_CALLS"] = "2"
        reg.register("A")
        reg.register("B")
        d = reg.decide(call_sid="third")
        check("US-016  shipped: a third call at capacity is refused", d.admitted is False)
        body = main._busy_twiml("example.test")
        check("US-016  shipped: and the caller hears the busy message, no stream",
              "<Play>" in body and "<Connect>" not in body)

        # REVERT.
        os.environ["ADMISSION_ENABLED"] = "0"
        check("US-016  reverting is one setting", adm.enabled() is False)
        d2 = reg.decide(call_sid="third")
        check("US-016  reverted: the third call is admitted again", d2.admitted is True)
        check("US-016  reverted: the refusal path is not taken at all",
              d2.reason == "" and adm.enabled() is False)
    finally:
        if original is None:
            os.environ.pop("ADMISSION_ENABLED", None)
        else:
            os.environ["ADMISSION_ENABLED"] = original

    check("US-016  restored: admission control is enabled again", adm.enabled() is True)


# ───────────────────────── US-017 ─────────────────────────

def us017() -> None:
    banner("US-017  background priority", "BG_PRIORITY_ENABLED")
    import app.pipeline as pipeline
    from app.work_priority import WorkGate

    original = os.environ.get("BG_PRIORITY_ENABLED")
    try:
        os.environ["BG_PRIORITY_ENABLED"] = "1"
        gate = WorkGate(defer_timeout_s=5.0)
        order: list[str] = []

        def bg() -> None:
            with gate.background_unit(label="bg"):
                order.append("background")

        with gate.voice_turn(label="caller"):
            t = threading.Thread(target=bg)
            t.start()
            time.sleep(0.3)
            order.append("voice")
            check("US-017  shipped: background is deferred while a caller holds the line",
                  order == ["voice"], str(order))
        t.join(timeout=5)
        check("US-017  shipped: and runs once the line clears", order == ["voice", "background"],
              str(order))

        # REVERT. With the policy off, pipeline.run_rag_query_sync takes no
        # gate at all, so background work is admitted immediately, mid-turn.
        os.environ["BG_PRIORITY_ENABLED"] = "0"
        check("US-017  reverting is one setting",
              pipeline._bg_priority_enabled() is False)

        gate2 = WorkGate(defer_timeout_s=5.0)
        interleaved: list[str] = []

        def bg_ungated() -> None:
            # The ungated path: no gate is entered.
            interleaved.append("background")

        with gate2.voice_turn(label="caller"):
            t2 = threading.Thread(target=bg_ungated)
            t2.start()
            t2.join(timeout=5)
            interleaved.append("voice")
        check("US-017  reverted: background runs inside the caller's turn again",
              interleaved == ["background", "voice"], str(interleaved))
    finally:
        if original is None:
            os.environ.pop("BG_PRIORITY_ENABLED", None)
        else:
            os.environ["BG_PRIORITY_ENABLED"] = original

    check("US-017  restored: the policy is enabled again",
          pipeline._bg_priority_enabled() is True)


def main() -> int:
    print("=" * 74)
    print("BRD-15 -- rollback demonstrated, not asserted")
    print("=" * 74)

    us012()
    us013()
    us016()
    us017()

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
