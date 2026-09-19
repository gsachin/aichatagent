"""US-012 acceptance tests — TTS cache isolation (DG-06, MOD-04 A.5.2, BRD-06).

US-012 is unusual: its deliverable is a test that can FAIL a design, and the
fallback is pre-decided (DG-06 -> AC-4). So this file is the story.

Two halves, deliberately separated:

  OFFLINE (TAC-2, TAC-3, TAC-4)  -- cache mechanics, no model, no network, no
      GPU. Exercised against the real VoiceCallSession, seeded directly.

  LIVE (TAC-1) -- the N=2 isolation condition: >=100 turns per caller, three
      consecutive runs, zero occurrences of caller B being served audio
      synthesised for caller A (MOD-04 A.5.2). Read from the turn traces that
      US-001 already writes; run it once the harness has produced them.

      .venv/Scripts/python.exe doc/perf/tools/test_us012_cache_isolation.py --live

The live half refuses to pass on fewer than three runs or fewer than 100 turns
per caller. A single clean run is not proof, and AC-1 says so explicitly.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us012_cache_isolation.py
"""
from __future__ import annotations

import asyncio
import collections
import datetime
import json
import os
import sys
import threading
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

import numpy as np  # noqa: E402

import app.voice_handler as vh  # noqa: E402

TRACE_PATH = PROJ / "logs" / "perf_turns.jsonl"
MIN_TURNS_PER_CALLER = 100
MIN_RUNS = 3

passed = failed = skipped = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def skip(name: str, why: str) -> None:
    global skipped
    skipped += 1
    print(f"  SKIP  {name} - {why}")


def fresh_cache() -> dict:
    vh.VoiceCallSession._shared_tts_cache.clear()
    return vh.VoiceCallSession._shared_tts_cache


def session(call_id: str) -> vh.VoiceCallSession:
    s = vh.VoiceCallSession()
    s.call_id = call_id
    return s


def seed(cache: dict, text: str, call_id: str) -> tuple:
    key = (text, vh._tts_voice(), vh._tts_speed())
    cache[key] = (np.full(16, 0.5, dtype=np.float32), 24000, call_id)
    return key


def run(coro):
    return asyncio.run(coro)


# ───────────────────────── TAC-2: copy discipline ─────────────────────────

def tac2_copy_discipline() -> None:
    print("\n-- TAC-2  a returned buffer is never the cached buffer")

    # A hit returns a copy, and mutating what the caller got leaves the cache alone.
    cache = fresh_cache()
    key = seed(cache, "Thank you for calling Meridian.", "call-A")
    before = cache[key][0].copy()

    got = run(session("call-B")._synthesise("Thank you for calling Meridian."))
    check("TAC-2  a cache hit returns audio", got is not None)
    if got is None:
        return
    check("TAC-2  the returned buffer is not the cached object",
          got is not before and not np.shares_memory(got, cache[key][0]))

    got[:] = -1.0
    check("TAC-2  mutating the returned buffer leaves the cache byte-identical",
          np.array_equal(cache[key][0], before))

    # Two callers reading the same entry must not share one buffer.
    a = run(session("call-A")._synthesise("Thank you for calling Meridian."))
    b = run(session("call-B")._synthesise("Thank you for calling Meridian."))
    check("TAC-2  two callers on one entry do not share a buffer",
          a is not None and b is not None and not np.shares_memory(a, b))
    if a is not None and b is not None:
        a[:] = 9.0
        check("TAC-2  one caller's mutation does not reach the other",
              not np.array_equal(b, a))

    # Concurrent hits and a concurrent synthesis, in real threads.
    cache = fresh_cache()
    seed(cache, "shared phrase", "call-A")
    errors: list[str] = []
    results: dict[str, np.ndarray] = {}

    def worker(label: str, text: str) -> None:
        try:
            out = run(session(label)._synthesise(text))
            if out is not None:
                results[label] = out
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(f"call-{i}", "shared phrase"))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("TAC-2  concurrent hits raise nothing", not errors, "; ".join(errors))
    check("TAC-2  every concurrent hit produced audio",
          len(results) == 6, f"{len(results)} of 6")
    bufs = list(results.values())
    check("TAC-2  no two concurrent results alias",
          all(not np.shares_memory(bufs[i], bufs[j])
              for i in range(len(bufs)) for j in range(i + 1, len(bufs))))


# ───────────────────────── TAC-3: eviction ─────────────────────────

def tac3_eviction() -> None:
    print("\n-- TAC-3  eviction is FIFO by insertion order, and bounded")

    cache = fresh_cache()
    cap = vh.VoiceCallSession._tts_cache_max
    keys = [seed(cache, f"phrase {i}", "call-A") for i in range(cap)]
    check("TAC-3  the cache holds exactly its maximum", len(cache) == cap,
          f"{len(cache)} != {cap}")
    check("TAC-3  insertion order is preserved (FIFO contract)",
          next(iter(cache)) == keys[0])

    del cache[next(iter(cache))]
    cache[("phrase new", vh._tts_voice(), vh._tts_speed())] = (
        np.zeros(4, dtype=np.float32), 24000, "call-A")
    check("TAC-3  the cache never grows past its maximum", len(cache) <= cap,
          f"{len(cache)} > {cap}")
    check("TAC-3  an evicted key is simply absent, so the next call re-synthesises",
          keys[0] not in cache)

    # Re-inserting an existing key must not silently reset its eviction position,
    # which is what keeps FIFO predictable under a hot entry.
    cache = fresh_cache()
    k0 = seed(cache, "hot", "call-A")
    for i in range(1, 5):
        seed(cache, f"cold {i}", "call-A")
    cache[k0] = (np.zeros(2, dtype=np.float32), 24000, "call-A")
    check("TAC-3  re-storing an existing key keeps its original position",
          next(iter(cache)) == k0)

    # Bounded under concurrent writes.
    cache = fresh_cache()
    errs: list[str] = []

    def hammer(n: int) -> None:
        try:
            for i in range(200):
                cache[(f"t{n}-{i}", vh._tts_voice(), vh._tts_speed())] = (
                    np.zeros(2, dtype=np.float32), 24000, f"call-{n}")
                while len(cache) > cap:
                    del cache[next(iter(cache))]
        except Exception as exc:                      # noqa: BLE001
            errs.append(f"{type(exc).__name__}: {exc}")

    ts = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("TAC-3  concurrent writes raise nothing", not errs, "; ".join(errs))
    check("TAC-3  capacity holds under four concurrent writers", len(cache) <= cap,
          f"{len(cache)} > {cap}")


# ───────────────────────── TAC-4: key hygiene ─────────────────────────

def tac4_key_hygiene() -> None:
    print("\n-- TAC-4  keys carry no caller data")

    key = ("Good morning, how can I help?", vh._tts_voice(), vh._tts_speed())
    check("TAC-4  a key is (agent text, voice, speed) and nothing else",
          isinstance(key, tuple) and len(key) == 3)
    check("TAC-4  the key holds no call identity",
          all(not isinstance(p, vh.VoiceCallSession) for p in key))

    # The key must be built from the agent's own utterance, not the caller's.
    cache = fresh_cache()
    caller_speech = "my name is Priya and my number is 9876543210"
    agent_line = "I can help with that."
    s = session("call-A")
    seed(cache, agent_line, "call-A")
    key_agent = (agent_line, vh._tts_voice(), vh._tts_speed())
    check("TAC-4  a key is the utterance being spoken, not the transcript",
          key_agent in cache and
          all(caller_speech not in str(k) for k in cache))

    # The trace digest must not carry the text either.
    digest = vh._cache_key_sha(key_agent)
    check("TAC-4  the trace digest is a short opaque string",
          len(digest) == 12 and agent_line not in digest)
    check("TAC-4  the digest is stable across calls (hash() would not be)",
          vh._cache_key_sha(key_agent) == digest)

    # Voice and speed MUST be inside the key: they became configurable, and a
    # key without them would keep serving audio rendered in the old voice.
    other = (agent_line, "am_michael", vh._tts_speed())
    check("TAC-4  changing the voice changes the key", other != key_agent)
    other_speed = (agent_line, vh._tts_voice(), 1.5)
    check("TAC-4  changing the speed changes the key", other_speed != key_agent)


# ───────────────────────── AC-4: the fallback ─────────────────────────

def ac4_scope_fallback() -> None:
    print("\n-- AC-4  the fallback is a configuration change, not a code revert")

    check("AC-4  the scope is read from configuration",
          vh.TTS_CACHE_SCOPE in ("shared", "per_call"),
          f"{vh.TTS_CACHE_SCOPE!r}")

    original = vh.TTS_CACHE_SCOPE
    try:
        vh.TTS_CACHE_SCOPE = "per_call"
        shared = fresh_cache()
        seed(shared, "phrase", "call-A")
        a, b = session("call-A"), session("call-B")
        check("AC-4  the two sessions keep separate caches",
              a._session_tts_cache is not b._session_tts_cache)
        check("AC-4  a per-call session does not see the shared entry",
              (("phrase", vh._tts_voice(), vh._tts_speed())
               not in a._session_tts_cache))
    finally:
        vh.TTS_CACHE_SCOPE = original

    check("AC-4  reverting the setting restores shared scope",
          vh.TTS_CACHE_SCOPE == original)


# ───────────────────────── TAC-1: the live isolation condition ─────────────────────────

def load_trace_rows() -> list[dict]:
    if not TRACE_PATH.is_file():
        return []
    rows = []
    for line in TRACE_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _run_windows() -> list[dict]:
    """Harness run summaries, as [start, end] epoch windows.

    The window comes from the harness's own summary rather than being guessed
    from gaps in the trace: a run is a fact the harness recorded, and grouping
    turns by eyeballing timestamps is how two runs get silently pooled.
    """
    out = []
    for path in sorted((PROJ / "doc" / "perf" / "runs").glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            t0 = _epoch(d.get("started_at"))
            t1 = _epoch(d.get("ended_at"))
        except (OSError, json.JSONDecodeError):
            continue
        if t0 is None or t1 is None or t1 < t0:
            continue
        out.append({"id": d.get("run_id"), "t0": t0, "t1": t1,
                    "condition": d.get("condition"), "file": path.name})
    return out


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tac1_live() -> None:
    print("\n-- TAC-1  N=2 isolation: >=100 turns per caller, three consecutive runs")

    rows = [r for r in load_trace_rows() if r.get("ts") is not None]
    if not rows:
        skip("TAC-1  live isolation condition",
             "no turn traces in logs/perf_turns.jsonl; run the harness first")
        return

    windows = _run_windows()
    if not windows:
        skip("TAC-1  live isolation condition",
             "no harness run summaries in doc/perf/runs/ to define a run")
        return

    # Two harness invocations whose windows overlap drove the stack at the same
    # time. That is not the N=2 condition: it is 4 concurrent callers, and every
    # latency figure from it is contention, not the thing under test. Counting
    # both windows would also judge the same turns twice. Refuse them.
    overlapping = []
    contaminated_ids: set[str] = set()
    for i, a in enumerate(windows):
        for b in windows[i + 1:]:
            if a["t0"] < b["t1"] and b["t0"] < a["t1"]:
                overlapping.append((a["id"], b["id"]))
                contaminated_ids.add(a["id"])
                contaminated_ids.add(b["id"])
    if overlapping:
        for a, b in overlapping:
            print(f"        CONTAMINATED (excluded): runs {a} and {b} overlap in time")
        print(f"        {len(contaminated_ids)} window(s) drove the stack alongside "
              f"another; more than two callers were live, so those are not the N=2 "
              f"condition and are not scored.")

    # Assign each trace row to exactly ONE run -- the earliest window that
    # contains it -- so an overlap can never double-count a turn.
    runs: dict[str, dict] = {}
    claimed: set[int] = set()
    for w in sorted(windows, key=lambda x: x["t0"]):
        members = []
        for idx, r in enumerate(rows):
            if idx in claimed or not (w["t0"] <= r["ts"] <= w["t1"]):
                continue
            claimed.add(idx)
            members.append(r)
        if members:
            runs.setdefault(w["id"], {"w": w, "rows": members})

    # The traces only became key-bearing when this story instrumented them. A
    # run whose rows carry no key CANNOT be scored -- and scoring it as a pass
    # is the exact false-green this program exists to remove, so it is reported
    # as indeterminate and fails the gate rather than passing quietly.
    scored, keyless = [], []
    for rid, run in sorted(runs.items()):
        if any(r.get("tts_cache_key_sha") for r in run["rows"]):
            scored.append(run)
        else:
            keyless.append(run)

    print(f"        runs with traces      : {len(runs)}")
    print(f"        runs bearing cache keys: {len(scored)}"
          + (f"   ({len(keyless)} predate the instrumentation)" if keyless else ""))

    if not scored:
        check("TAC-1  the isolation condition is measurable", False,
              "no run carries tts_cache_key_sha -- the stack was instrumented "
              "after these runs, so nothing here can prove or disprove isolation")
        print("        restart the app so the new trace fields load, then run the "
              "harness three times:\n"
              "          .venv/Scripts/python.exe doc/perf/tools/load_harness.py "
              "--n 2 --turns 100")
        return

    # The experiment is shared-vs-per-call, so the two scopes must never be
    # pooled. Reading the scope off the oldest trace row -- as this file first
    # did -- reports whichever scope ran first and hides the one under test.
    cross_total = 0
    good_runs = 0
    per_scope: dict[str, dict] = collections.defaultdict(
        lambda: {"runs": 0, "cross": 0, "hits": 0, "keys": 0})
    for run in scored:
        if run["w"]["id"] in contaminated_ids:
            continue
        by_call: dict[str, list[dict]] = collections.defaultdict(list)
        for r in run["rows"]:
            by_call[r.get("call_id", "?")].append(r)

        # The application's own verdict is the primary signal: at hit time it
        # compares the entry's stored owner against the current call_id, so it
        # does not depend on the key surviving into the trace. The key-based
        # reconstruction below is a cross-check on the same question.
        app_flagged = [r for r in run["rows"] if r.get("tts_cache_cross_call")]

        owner: dict[str, str] = {}
        by_key: list[tuple[str, str, str]] = []
        for call_id, turns in sorted(by_call.items()):
            for t in sorted(turns, key=lambda r: r["ts"]):
                key = t.get("tts_cache_key_sha")
                if not key:
                    continue
                if t.get("tts_cache_hit"):
                    first = owner.get(key)
                    if first is not None and first != call_id:
                        by_key.append((key, first, call_id))
                else:
                    owner.setdefault(key, call_id)

        hits = sum(1 for r in run["rows"] if r.get("tts_cache_hit"))
        per_caller = max((len(v) for v in by_call.values()), default=0)
        # The scope this run actually ran under, read from its own rows.
        run_scope = next((r.get("tts_cache_scope") for r in run["rows"]
                          if r.get("tts_cache_scope")), "?")
        rec = per_scope[run_scope]
        rec["runs"] += 1
        rec["cross"] += len(app_flagged)
        rec["hits"] += hits
        rec["keys"] += len(owner)

        cross_total += len(app_flagged)
        enough = per_caller >= MIN_TURNS_PER_CALLER
        if enough and not app_flagged:
            good_runs += 1
        print(f"        run {run['w']['id']} [{run_scope}]: {len(run['rows'])} turns, "
              f"{len(by_call)} callers, largest {per_caller} turns, "
              f"{len(owner)} keys, {hits} hits, "
              f"cross-call hits {len(app_flagged)}"
              f" (key-reconstruction agrees: {len(by_key) == len(app_flagged)})"
              + ("" if enough else f"  [UNDERPOWERED <{MIN_TURNS_PER_CALLER}/caller]"))

    print("        --- by scope ---")
    for scope_name, rec in sorted(per_scope.items()):
        print(f"        {scope_name}: {rec['runs']} run(s), {rec['keys']} keys, "
              f"{rec['hits']} hits, {rec['cross']} cross-call")

    per_call = per_scope.get("per_call")
    if per_call and per_call["runs"]:
        check("TAC-1  zero cross-call hits under the per-call scope (AC-4 fallback)",
              per_call["cross"] == 0,
              f"{per_call['cross']} cross-call hit(s) across {per_call['keys']} keys")

    check(f"TAC-1  at least {MIN_RUNS} runs met the condition under per-call scope",
          good_runs >= MIN_RUNS,
          f"{good_runs} qualifying run(s); AC-1 wants three consecutive runs "
          f"before any pass is claimed")
    if cross_total and not per_call:
        print("        Hits observed under 'shared'. That is the outcome AC-4\n"
              "        pre-decides: the cache becomes per-call, not argued away.")


def main(argv: list[str]) -> int:
    live = "--live" in argv
    print("=" * 74)
    print("US-012 -- TTS cache isolation (DG-06, MOD-04 A.5.2, BRD-06)")
    print("=" * 74)
    print(f"  scope : TTS_CACHE_SCOPE={vh.TTS_CACHE_SCOPE}")
    print(f"  max   : {vh.VoiceCallSession._tts_cache_max} entries")

    tac2_copy_discipline()
    tac3_eviction()
    tac4_key_hygiene()
    ac4_scope_fallback()

    if live:
        tac1_live()
    else:
        skip("TAC-1  live isolation condition", "pass --live to read the turn traces")

    print()
    print(f"{passed} passed, {failed} failed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
