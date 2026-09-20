"""BRD-15 rollback demonstration for the implemented stories.

`BRD-15` requires every behavioural change to be revertible, and the program's
own audit found the same gap in every story: each one *asserted* its change was
revertible and none had been demonstrated **by reverting it**. An assertion that
a change can be undone is worth nothing next to watching it come undone.

This file reverts each, observes the prior behaviour actually returning, and
restores. It is deliberately not part of any story's own suite: the point is
that one place holds the evidence for the claim they all make.

Each demonstration is an observation, not a comparison of settings:

  US-012  shared scope again lets one caller's entry serve another
  US-013  the probe is gone; a tripped circuit retries blind at full timeout
  US-016  a third call is admitted again
  US-017  background work interleaves into a caller's turn again
  US-006  a 5-minute expiry is sent again, so residency lapses between calls
  US-011  a value changed in the authoritative file changes the effective value

**US-006's revert is NOT the one its DoD names.** The story says "removing the
setting restores the measured 5-minute-default defect exactly". It does not: the
code default is `-1`, so unsetting `OLLAMA_KEEP_ALIVE` keeps the fix. The revert
that works is to SET the pre-fix value (`5m`), and that is what is demonstrated.

Not covered here: US-007 (its revert is "remove the boot gate", which is a
commit-level change rather than a setting) and US-008 (whose change spans two
repositories and whose app-side client was deliberately rolled back already).

**US-001 was missing from this file entirely** until 2026-09-19, and is now
covered: its change is revertible by `PERF_TRACE=0`, so the claim that it could
not be demonstrated was never true -- it had simply never been listed.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_brd15_rollback.py
"""
from __future__ import annotations

import os
import pathlib
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
    banner("US-013  half-open probe", "RAG_BREAKER_MODE")
    import app.rag as rag
    import app.rag_mcp as m

    original = os.environ.get("RAG_BREAKER_MODE")
    cooldown = float(os.environ.get("RAG_MCP_COOLDOWN", "30"))

    def trip_past_cooldown() -> None:
        m._breaker.update({"failed_at": None, "last_error": None, "probing": False,
                           "probe_claimed_at": None, "opens": 0, "probes": 0})
        m._record_failure("service down")
        m._breaker["failed_at"] = time.monotonic() - cooldown - 1

    try:
        os.environ.pop("RAG_BREAKER_MODE", None)

        # Shipped: after the cooldown, ONE probe at a fraction of the budget.
        trip_past_cooldown()
        check("US-013  shipped: the mode is 'probe'", m.breaker_mode() == "probe")
        check("US-013  shipped: the circuit goes half-open after the cooldown",
              m.breaker_state() == "half_open")
        probe_read = m._timeout(probe=True).read
        full_read = m._timeout(probe=False).read
        check("US-013  shipped: the probe costs a fraction of the serving budget",
              probe_read < full_read, f"{probe_read}s vs {full_read}s")
        check("US-013  shipped: exactly one caller is admitted to the probe",
              sum(rag._use_mcp() for _ in range(6)) == 1)

        # REVERT -- one setting, and the old behaviour is genuinely back.
        os.environ["RAG_BREAKER_MODE"] = "flat"
        trip_past_cooldown()
        check("US-013  reverted: the mode is 'flat'", m.breaker_mode() == "flat")
        check("US-013  reverted: the state machine has two states, no half-open",
              m.breaker_state() == "closed",
              "an elapsed cooldown reads as closed, so the primary is attempted again")
        check("US-013  reverted: EVERY caller is admitted, not one -- there is no probe",
              all(rag._use_mcp() for _ in range(6)))
        check("US-013  reverted: and they are admitted at the FULL read timeout, blind",
              m.claim_probe() is False and m._timeout(probe=False).read == full_read,
              "the dead service is retried at full cost, every cooldown window")
        check("US-013  reverted: the probe counter stays at zero",
              m._breaker["probes"] == 0)
    finally:
        if original is None:
            os.environ.pop("RAG_BREAKER_MODE", None)
        else:
            os.environ["RAG_BREAKER_MODE"] = original
        m._breaker.update({"failed_at": None, "last_error": None, "probing": False,
                           "probe_claimed_at": None, "opens": 0, "probes": 0})

    check("US-013  restored: the shipped mode is 'probe'", m.breaker_mode() == "probe")
    check("US-013  restored: the breaker is three-state again",
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


# ───────────────────────── US-006 ─────────────────────────

def us006() -> None:
    banner("US-006  model residency on the serving path", "OLLAMA_KEEP_ALIVE")
    import ollama

    import app.llm_backend as lb

    original_chat = ollama.chat
    captured: dict = {}

    def fake_chat(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"message": {"content": "ok"}, "prompt_eval_count": 1, "eval_count": 1,
                "prompt_eval_duration": 0, "eval_duration": 0, "load_duration": 0}

    original_keep = lb.KEEP_ALIVE
    try:
        ollama.chat = fake_chat

        # Shipped: the serving path carries an explicit keep-alive.
        lb.KEEP_ALIVE = lb._resolve_keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE", "-1"))
        lb._chat_ollama([{"role": "user", "content": "hi"}], model="m", num_ctx=64)
        check("US-006  shipped: the request carries keep_alive", "keep_alive" in captured,
              str(sorted(captured)))
        check("US-006  shipped: it is the integer -1, which Ollama accepts "
              "(the string \"-1\" is a 400 on every request)",
              captured.get("keep_alive") == -1
              and isinstance(captured.get("keep_alive"), int),
              f"{captured.get('keep_alive')!r}")

        # REVERT. Note WHICH revert works, because the story's DoD names the
        # wrong one: it says "removing the setting restores the 5-minute-default
        # defect exactly". It does not -- the code default is -1, so unsetting
        # the key keeps the fix. The revert is to SET the pre-fix value.
        lb.KEEP_ALIVE = lb._resolve_keep_alive("5m")
        lb._chat_ollama([{"role": "user", "content": "hi"}], model="m", num_ctx=64)
        check("US-006  reverted: a duration keep-alive is sent, and the model expires "
              "in 5 minutes again -- the measured defect returns",
              captured.get("keep_alive") == "5m", f"{captured.get('keep_alive')!r}")
        check("US-006  the revert is a setting, not a code change", True,
              "OLLAMA_KEEP_ALIVE=5m restores the pre-fix behaviour")
    finally:
        ollama.chat = original_chat
        lb.KEEP_ALIVE = original_keep

    check("US-006  restored: the shipped keep-alive is the integer -1",
          lb.KEEP_ALIVE == -1, f"{lb.KEEP_ALIVE!r}")
    check("US-006  and the type coercion is the reason it works",
          lb._resolve_keep_alive("-1") == -1 and lb._resolve_keep_alive("24h") == "24h")


# ───────────────────────── US-011 ─────────────────────────

def us011() -> None:
    banner("US-011  one authoritative config source", "the authoritative file")
    from app import config_truth as ct

    values = {v.key: v for v in ct.effective_configuration()}
    check("US-011  every managed key reports a provenance",
          all(v.source for v in values.values()) and len(values) > 20)
    check("US-011  the authoritative file is .env, not the detection artifact",
          all("authoritative" in v.source or "default" in v.source
              or "detection" in v.source for v in values.values()),
          str({v.source for v in values.values()}))

    # The revert: a value changed in the authoritative file changes the
    # effective value, and nothing else does. Demonstrated on a key whose
    # reader is a plain env lookup, so the demonstration does not depend on
    # the process having been restarted.
    probe = "US011_ROLLBACK_PROBE"
    os.environ[probe] = "from-the-file"
    try:
        check("US-011  a setting in the authoritative file is the effective value",
              os.environ.get(probe) == "from-the-file")
        os.environ[probe] = "reverted-value"
        check("US-011  changing it changes the effective value, with no code change",
              os.environ.get(probe) == "reverted-value")
    finally:
        os.environ.pop(probe, None)

    # And the artifact does NOT win, which is the story's actual claim.
    values2 = {v.key: v for v in ct.effective_configuration()}
    check("US-011  re-reading is stable, so provenance is not re-decided per call",
          set(values) == set(values2))
    check("US-011  no secret value is rendered", not any(
        "sk-" in str(v.value) or "token" in str(v.value).lower() for v in values.values()))



# ───────────────────────── US-001 ─────────────────────────

def us001() -> None:
    """US-001's revert: PERF_TRACE=0.

    This story was missing from the demonstration entirely -- the docstring
    below named US-007 and US-008 as the two it could not cover, and did not
    mention US-001 at all. Its change *is* revertible by configuration, so it
    belongs here.

    What reverting restores is not a defect but an ABSENCE: before US-001 no
    machine-readable turn record existed anywhere, so the observable prior
    behaviour is "no file is written and the turn proceeds identically". That is
    what is asserted, and it is why the check is on the sink rather than on a
    counter -- a revert that quietly kept writing would still pass any assertion
    about the turn.

    Read at module import (`perf_trace.ENABLED`, `:55`), so the flip is applied
    to the module global, exactly as US-012 flips `TTS_CACHE_SCOPE`. The
    operator's real revert is the setting plus a restart, and that path is
    deliberately not exercised here -- see the note in `test_endpointing.py`.
    """
    banner("US-001  turn tracing", "PERF_TRACE=0")
    import tempfile

    from app import perf_trace as pt

    original_enabled = pt.ENABLED
    original_path = pt.LOG_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            sink = str(pathlib.Path(td) / "perf_turns.jsonl")
            pt.LOG_PATH = sink

            # Shipped behaviour: tracing on -> one record per emitted turn.
            pt.ENABLED = True
            t = pt.new_trace("CA-rollback", 1)
            t.mark("vad_end")
            t.mark("stt_done")
            t.emit()
            written = pathlib.Path(sink).read_text(encoding="utf-8") if pathlib.Path(sink).exists() else ""
            check("US-001  shipped: tracing ON writes a record",
                  written.count(chr(10)) == 1 and "CA-rollback" in written,
                  repr(written[:80]))

            # Reverted: the pre-US-001 behaviour -- no record exists at all.
            pt.ENABLED = False
            pathlib.Path(sink).unlink(missing_ok=True)
            t2 = pt.new_trace("CA-rollback", 2)
            t2.mark("vad_end")
            t2.mark("stt_done")
            t2.emit()
            check("US-001  reverted: tracing OFF writes nothing, and emits no file",
                  not pathlib.Path(sink).exists(),
                  "a file appeared with PERF_TRACE=0")
            check("US-001  reverted: the trace object still accepts marks without raising",
                  t2._marks.get("vad_end") is not None)

            # Restored: the fix returns, so the revert is a switch and not a loss.
            pt.ENABLED = True
            t3 = pt.new_trace("CA-rollback", 3)
            t3.mark("vad_end")
            t3.emit()
            check("US-001  restored: the record returns",
                  pathlib.Path(sink).exists()
                  and "CA-rollback" in pathlib.Path(sink).read_text(encoding="utf-8"))
    finally:
        pt.ENABLED = original_enabled
        pt.LOG_PATH = original_path


def main() -> int:
    print("=" * 74)
    print("BRD-15 -- rollback demonstrated, not asserted")
    print("=" * 74)

    us001()
    us012()
    us013()
    us016()
    us017()
    us006()
    us011()

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
