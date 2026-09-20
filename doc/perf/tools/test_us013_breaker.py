"""US-013 acceptance tests — bounded dependency calls and the half-open probe.

Covers the story's technical acceptance criteria without a network, a model or
the MCP service:

  AC-1 / TAC-1  one failure costs one budget, not two
  AC-2 / TAC-2  a tripped circuit probes at a fraction of the timeout
  AC-3 / TAC-3  one caller's outage is not another caller's latency
  AC-4 / TAC-4  the rung that served is recorded, and degradation is spoken

The breaker is exercised by driving its state directly rather than by sleeping
through real cooldowns, so the suite runs in milliseconds and states its
assumptions instead of hiding them in wall-clock time.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us013_breaker.py
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

import app.rag as rag  # noqa: E402
import app.rag_mcp as m  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def reset() -> None:
    m._breaker.update({"failed_at": None, "last_error": None, "probing": False,
                       "probe_claimed_at": None, "opens": 0, "probes": 0})


def force_open(age_s: float = 0.0) -> None:
    m._record_failure("test: service down")
    if age_s:
        m._breaker["failed_at"] = time.monotonic() - age_s


# ───────────────────── AC-1: one failure, one budget ─────────────────────

def ac1_one_budget() -> None:
    print("\n-- AC-1  one failure costs one budget, not two")

    served = m._timeout(probe=False).read
    budget = float(os.environ.get("RAG_RETRIEVAL_BUDGET", "8.0"))
    reserve = float(os.environ.get("RAG_FALLBACK_RESERVE", "1.5"))
    check("AC-1  the primary attempt leaves room for the fallback",
          served <= budget - reserve + 1e-9,
          f"read={served}s budget={budget}s reserve={reserve}s")
    check("AC-1  the connect phase is bounded well below the budget",
          m._timeout(probe=False).connect <= 1.0)
    check("AC-1  a tightening budget actually binds",
          _read_with(budget="3.0", reserve="1.0") <= 2.0 + 1e-9,
          f"read={_read_with(budget='3.0', reserve='1.0')}s with a 3 s budget")

    # While the circuit is open, a caller does not touch the primary at all --
    # so the fallback is the ONLY payment, not the second one.
    reset()
    force_open(age_s=1.0)
    check("AC-1  an open circuit sends the caller straight to the local rung",
          rag._use_mcp() is False)

    # A refused connection must not be waited on for the read timeout.
    check("AC-1  connect failure is bounded by the connect timeout, not the read",
          m._timeout(probe=False).connect < served,
          f"connect={m._timeout(probe=False).connect}s read={served}s")


def _read_with(budget: str, reserve: str) -> float:
    old = {k: os.environ.get(k) for k in ("RAG_RETRIEVAL_BUDGET", "RAG_FALLBACK_RESERVE")}
    try:
        os.environ["RAG_RETRIEVAL_BUDGET"] = budget
        os.environ["RAG_FALLBACK_RESERVE"] = reserve
        return m._timeout(probe=False).read
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ───────────────────── AC-2: probe, don't retry blind ─────────────────────

def ac2_probe() -> None:
    print("\n-- AC-2  a tripped circuit probes rather than retries blind")

    reset()
    check("AC-2  a healthy circuit is closed", m.breaker_state() == "closed")

    force_open(age_s=0.0)
    check("AC-2  a failure opens the circuit", m.breaker_state() == "open")
    check("AC-2  an open circuit refuses the primary", rag._use_mcp() is False)
    check("AC-2  an open circuit records no probe", m._breaker["probes"] == 0)

    # Cooldown elapsed -> half-open, probe permitted.
    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    check("AC-2  the circuit goes half-open once the cooldown elapses",
          m.breaker_state() == "half_open")

    full = m._timeout(probe=False).read
    probe = m._timeout(probe=True).read
    check("AC-2  the probe budget is a fraction of the full timeout", probe < full,
          f"probe={probe}s full={full}s")
    check("AC-2  the probe keeps a floor so a busy service is not read as dead",
          probe >= 0.5, f"probe={probe}s")

    check("AC-2  the first caller in the window gets the probe", rag._use_mcp() is True)
    check("AC-2  the probe slot is marked in flight", m._probe_in_flight() is True)

    # Still down: the probe fails, the circuit re-opens, and the cost of
    # finding out is the probe's, not the full timeout.
    m._record_failure("probe: still down")
    check("AC-2  a failed probe reopens the circuit", m.breaker_state() == "open")
    check("AC-2  a failed probe releases the slot", m._probe_in_flight() is False)
    check("AC-2  the cooldown restarts, so the next probe is a window away",
          rag._use_mcp() is False)

    # Recovered: the probe succeeds and serving resumes with no operator action.
    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    rag._use_mcp()
    m._clear_failure()
    check("AC-2  a successful probe closes the circuit", m.breaker_state() == "closed")
    check("AC-2  recovery needed no restart and no operator action",
          rag._use_mcp() is True)

    # A stuck slot must not disable probing forever.
    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    m.claim_probe()
    m._breaker["probe_claimed_at"] = time.monotonic() - 999.0
    check("AC-2  an abandoned probe slot is reclaimed, not held forever",
          m._probe_in_flight() is False and m.claim_probe() is True)


# ───────────────────── AC-3: one outage, not two latencies ─────────────────────

def ac3_single_flight() -> None:
    print("\n-- AC-3  one caller's outage is not another caller's latency")

    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    admitted = []
    for _ in range(8):
        admitted.append(rag._use_mcp())
    check("AC-3  exactly one caller in the window is admitted to the primary",
          sum(admitted) == 1, f"{sum(admitted)} admitted")
    check("AC-3  the other callers take the local rung",
          admitted.count(False) == 7)

    # Racing threads must not each open a probe.
    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    wins: list[bool] = []
    lock = threading.Lock()

    def racer() -> None:
        got = rag._use_mcp()
        with lock:
            wins.append(got)

    threads = [threading.Thread(target=racer) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("AC-3  sixteen concurrent callers still open exactly one probe",
          sum(wins) == 1, f"{sum(wins)} admitted")

    check("AC-3  the probe is counted once, not once per caller",
          m._breaker["probes"] == 1, f"probes={m._breaker['probes']}")


# ───────────────────── BRD-15: the revert is a setting ─────────────────────

def breaker_mode_contract() -> None:
    print("\n-- BRD-15  the revert is a setting, not a reconstruction")

    import app.rag as rag

    original = os.environ.get("RAG_BREAKER_MODE")
    cooldown = float(os.environ.get("RAG_MCP_COOLDOWN", "30"))
    try:
        os.environ.pop("RAG_BREAKER_MODE", None)
        check("BRD-15  the shipped default is 'probe'", m.breaker_mode() == "probe")

        reset()
        force_open(age_s=cooldown + 1)
        check("BRD-15  shipped: an elapsed cooldown is half-open", m.breaker_state() == "half_open")

        os.environ["RAG_BREAKER_MODE"] = "flat"
        reset()
        force_open(age_s=cooldown + 1)
        check("BRD-15  flat: an elapsed cooldown reads as closed -- the old defect",
              m.breaker_state() == "closed")
        check("BRD-15  flat: no probe slot exists to claim", m.claim_probe() is False)
        check("BRD-15  flat: every caller is admitted, at the full timeout",
              all(rag._use_mcp() for _ in range(5)))

        os.environ["RAG_BREAKER_MODE"] = "not-a-mode"
        check("BRD-15  an unrecognised mode falls back to 'probe', not to flat",
              m.breaker_mode() == "probe",
              "the caller-safe direction: a typo must not silently remove the probe")
    finally:
        if original is None:
            os.environ.pop("RAG_BREAKER_MODE", None)
        else:
            os.environ["RAG_BREAKER_MODE"] = original
        reset()

    check("BRD-15  restored: 'probe' is in force", m.breaker_mode() == "probe")


# ───────────────────── A3: the breaker is operator-readable ─────────────────

def a3_surface_contract() -> None:
    """A3 (2026-09-19): the status snapshot served on `/api/perf/policy`.

    The endpoint contract the addition must meet: ADDITIVE (a new key beside
    admission and work-priority, never a change to either), NON-BLOCKING (a
    pure in-memory read -- no HTTP, no socket, no wait), and SECRET-FREE
    (a loopback URL and counts, never a credential value).
    """
    print("\n-- A3  the breaker state is readable on the policy surface")

    reset()
    s = m.mcp_rag_status()
    check("A3  the snapshot carries the three-state breaker",
          s["breaker_state"] in ("closed", "open", "half_open")
          and isinstance(s["breaker_opens"], int)
          and isinstance(s["breaker_probes"], int))
    check("A3  the snapshot names the outage it covers",
          "last_error" in s and "session_ok" in s
          and "cooldown_active" in s and "breaker_mode" in s)
    check("A3  the read is a snapshot, not a call",
          s["mode"] in ("auto", "on", "off")
          and isinstance(s["probe_read_timeout_s"], float))
    check("A3  no credential value can appear in the snapshot",
          not any(k for k in s if any(w in k.lower()
                  for w in ("token", "secret", "password", "apikey", "api_key")))
          and s["url"].startswith("http://"))

    force_open(age_s=0.0)
    s_open = m.mcp_rag_status()
    check("A3  an open breaker reads as open on the surface",
          s_open["breaker_state"] == "open" and s_open["cooldown_active"] is True)
    reset()


# ───────────────────── AC-4: the rung is recorded ─────────────────────

def ac4_recording() -> None:
    print("\n-- AC-4  degradation is spoken, and the serving rung is recorded")

    status = m.mcp_rag_status()
    for key in ("breaker_state", "breaker_opens", "breaker_probes"):
        check(f"AC-4  status reports {key}", key in status, str(sorted(status)))
    check("AC-4  cooldown_active agrees with the state machine",
          status["cooldown_active"] == (status["breaker_state"] == "open"))

    reset()
    force_open()
    check("AC-4  an opening is counted", m.mcp_rag_status()["breaker_opens"] == 1)
    force_open()
    check("AC-4  a repeat failure inside one opening is not double-counted",
          m.mcp_rag_status()["breaker_opens"] == 1, "opens counted per outage")
    m._clear_failure()
    force_open()
    check("AC-4  a new outage after recovery counts again",
          m.mcp_rag_status()["breaker_opens"] == 2)

    # MODE=on must keep its documented meaning: never breaker-gated.
    old = os.environ.get("USE_MCP_RAG")
    try:
        os.environ["USE_MCP_RAG"] = "on"
        force_open(age_s=1.0)
        check("AC-4  USE_MCP_RAG=on still overrides the breaker (as documented)",
              rag._use_mcp() is True)
        os.environ["USE_MCP_RAG"] = "off"
        m._clear_failure()
        check("AC-4  USE_MCP_RAG=off never uses the primary",
              rag._use_mcp() is False)
    finally:
        if old is None:
            os.environ.pop("USE_MCP_RAG", None)
        else:
            os.environ["USE_MCP_RAG"] = old


# ───────────────────── LLD T-11 / T-15: the record ─────────────────────

def lld_record() -> None:
    print("\n-- LLD T-11 / T-15  the record states the rung, the state and the cost")

    reset()
    st = m.mcp_rag_status()
    check("T-11  the record carries the breaker state", "breaker_state" in st)
    check("T-11  and the probe's budget, so the outage cost is readable",
          st.get("probe_read_timeout_s") is not None,
          str(st.get("probe_read_timeout_s")))
    check("T-11  and the count of openings and probes",
          "breaker_opens" in st and "breaker_probes" in st)

    # T-15: a turn abandoned at its deadline still leaves a record. The probe's
    # reduced budget IS that deadline, and the probe is counted when claimed --
    # before the outcome is known -- so an abandoned probe is visible rather
    # than silently absent from the count.
    reset()
    force_open(age_s=float(os.environ.get("RAG_MCP_COOLDOWN", "30")) + 1.0)
    claimed = m.claim_probe()
    check("T-15  the probe is counted at claim time, before its outcome",
          claimed and m.mcp_rag_status()["breaker_probes"] == 1)
    m._breaker["probe_claimed_at"] = time.monotonic() - 999.0
    m._probe_in_flight()          # the reclaim path
    check("T-15  an abandoned probe still leaves its count behind",
          m.mcp_rag_status()["breaker_probes"] == 1,
          "a probe that died without a verdict is visible in the record")

    # The state is observable while the outage is in progress, not only after.
    reset()
    force_open(age_s=1.0)
    check("T-11  the outage state is readable mid-outage",
          m.mcp_rag_status()["breaker_state"] == "open")
    reset()


def main() -> int:
    print("=" * 74)
    print("US-013 -- bounded dependency calls and the half-open probe (BRD-14)")
    print("=" * 74)
    print(f"  cooldown      : {os.environ.get('RAG_MCP_COOLDOWN', 30)}s")
    print(f"  read timeout  : {m._timeout(probe=False).read}s")
    print(f"  probe timeout : {m._timeout(probe=True).read}s")

    ac1_one_budget()
    ac2_probe()
    ac3_single_flight()
    ac4_recording()
    lld_record()
    breaker_mode_contract()
    a3_surface_contract()
    reset()

    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
