"""US-001 acceptance tests for app/perf_trace.py. No model, no network.

Covers the story's technical acceptance criteria that can be checked without a
live call:
  T-3/TAC-3  no app.* imports in the trace module (static scan)
  T-4/TAC-4  no caller PII in an emitted record
  T-5/TAC-6  absent-key honesty - a missing stage is absent, never 0
  T-6/T-2    stage completeness + consecutive segments reconcile to total_ms
  T-7        engine counters captured; retrieval_ms derived and sane
  T-8        a trace with no counters omits retrieval_ms rather than guessing
  T-9        disabled tracing emits nothing and never raises
  T-10       ContextVar isolation between two concurrent traces

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us001_tracer.py
"""
import ast
import json
import os
import sys
import tempfile
import threading

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

TRACE_SRC = os.path.join(PROJ, "app", "perf_trace.py")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


# --- TAC-3: no app.* imports (the constraint that makes AC-3 structural) ---
tree = ast.parse(open(TRACE_SRC, encoding="utf-8").read())
bad = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        bad += [a.name for a in node.names if a.name.split(".")[0] == "app"]
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.module.split(".")[0] == "app":
            bad.append(node.module)
check("TAC-3 no app.* imports in perf_trace.py", not bad, str(bad))

# --- load with output redirected to a temp file ---
tmp = tempfile.mkdtemp()
os.environ["PERF_TRACE_FILE"] = os.path.join(tmp, "perf_turns.jsonl")
os.environ["PERF_TRACE"] = "1"

import importlib                                            # noqa: E402
import app.perf_trace as pt                                 # noqa: E402
importlib.reload(pt)                                        # pick up env


def emitted():
    p = pt.LOG_PATH
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


# --- T-9: disabled tracing is a no-op ---
pt.ENABLED = False
t_off = pt.new_trace("CA-off", 1)
t_off.mark("vad_end")
t_off.emit()
check("T-9 PERF_TRACE=0 emits nothing", emitted() == [])
pt.ENABLED = True

# --- T-6/T-2: full turn, stages reconcile ---
t = pt.new_trace("CA1", 1)
t.mark("vad_end")
t.mark("stt_done")
t.mark("retrieval_done")
t.mark("llm_sent")
t.note(prompt_eval_count=5666, eval_count=40,
       prefill_ms=20.0, generation_ms=20.0)
import time as _time                                          # noqa: E402
_time.sleep(0.05)            # real wall time between llm_sent and llm_done
t.mark("llm_done")
t.mark("tts_done")
t.mark("first_audio_sent")
t.emit()
recs = emitted()
check("T-2 one record emitted", len(recs) == 1)
r = recs[0]
segs = [v for k, v in r.items() if k.startswith("seg_")]
recon = abs(sum(segs) - r["total_ms"]) < 5.0
check("T-2 consecutive segments reconcile to total_ms (<=5 ms)",
      recon, f"sum={sum(segs):.1f} total={r['total_ms']}")
check("T-2 >=6 stages recorded", r["stages_seen"] >= 6, str(r["stages_seen"]))

# --- T-5/TAC-6: absent is not zero ---
check("TAC-6 llm_first_token ABSENT (non-streaming: no TTFT exists)",
      "llm_first_token_ms" not in r)
check("TAC-6 stages_expected always present", "stages_expected" in r)

# --- T-7: retrieval_ms is MEASURED from the direct segment, not derived ---
# It was previously derived by subtracting engine counters from the llm window,
# which silently absorbed model load and Ollama queue time and read 5,432 ms
# when the true figure was 2,227 ms. A direct mark cannot absorb what it was
# never told about.
check("T-7 retrieval_ms present (measured from the retrieval_done segment)",
      "retrieval_ms" in r)
if "retrieval_ms" in r:
    check("T-7 retrieval_ms is non-negative", r["retrieval_ms"] >= 0,
          f"retrieval_ms={r['retrieval_ms']}")
    seg = r.get("seg_llm_sent__retrieval_done_ms")
    check("T-7 retrieval_ms EQUALS its own segment (no hidden derivation)",
          seg is not None and abs(r["retrieval_ms"] - seg) < 0.2,
          f"retrieval_ms={r['retrieval_ms']} seg={seg}")

# --- TAC-4: no caller PII ---
blob = json.dumps(r)
forbidden = ["+1", "@", "meridian.edu", "student", "phone"]
hits = [f for f in forbidden if f.lower() in blob.lower()]
check("TAC-4 no PII / caller-supplied strings in the record", not hits, str(hits))

# --- T-8: no retrieval mark -> retrieval_ms ABSENT, not guessed ---
t2 = pt.new_trace("CA2", 2)
for s in ("vad_end", "stt_done", "llm_sent", "llm_done"):
    t2.mark(s)          # note: no retrieval_done
t2.emit()
r2 = emitted()[-1]
check("T-8 retrieval_ms OMITTED when the retrieval mark is absent",
      "retrieval_ms" not in r2, f"got {r2.get('retrieval_ms')!r}")
check("T-8 record still emitted (absent is not zero, and not a failure)",
      r2["stages_seen"] == 4, str(r2["stages_seen"]))

# --- T-10: ContextVar isolation between concurrent traces ---
seen = {}


def worker(cid):
    tr = pt.new_trace(cid, 1)
    tr.mark("vad_end")
    seen[cid] = pt.current().call_id


threads = [threading.Thread(target=worker, args=(f"CA-{i}",)) for i in range(2)]
for th in threads:
    th.start()
for th in threads:
    th.join()
check("T-10 concurrent traces stay isolated",
      seen == {"CA-0": "CA-0", "CA-1": "CA-1"}, str(seen))

# --- never-raises under misuse ---
try:
    pt.current().mark("vad_end")      # active trace from the threads above
    pt.note_current(bogus=object())
    pt.mark_current("not_a_stage")
    pt.emit_current()
    check("T-11 misuse never raises", True)
except Exception as exc:
    check("T-11 misuse never raises", False, repr(exc))

# --- T-12: engine queue time is NAMED, and a bad derivation is flagged ---
# The llm window holds four things: load, retrieval, prefill, generation — and
# whatever is left over is engine QUEUE time. Naming it is what stops queueing
# from masquerading as retrieval, which is exactly what happened before.
t3 = pt.new_trace("CA3", 3)
for s in ("vad_end", "stt_done", "retrieval_done", "llm_sent"):
    t3.mark(s)
t3.note(prefill_ms=99999.0, generation_ms=99999.0)   # absurd vs real wall time
t3.mark("llm_done")
t3.emit()
r3 = emitted()[-1]
check("T-12 an impossible derivation is flagged, not recorded as fact",
      r3.get("llm_queue_derivable") is False,
      f"llm_queue_ms={r3.get('llm_queue_ms')} derivable={r3.get('llm_queue_derivable')}")
check("T-12 retrieval_ms is unaffected by the bad counters (it is measured)",
      isinstance(r3.get("retrieval_ms"), (int, float)) and r3["retrieval_ms"] >= 0,
      f"retrieval_ms={r3.get('retrieval_ms')}")

# --- T-1: a stage marked twice keeps its FIRST timestamp ---
# The behaviour is `setdefault` in `mark()`, and until 2026-09-19 nothing tested
# it. The reason it matters is not tidiness: a re-marked stage would move the
# boundary it represents, silently re-attributing time between two segments,
# and every derived figure downstream (retrieval_ms, llm_queue_ms) would move
# with it. The record is only trustworthy if a boundary cannot be rewritten.
t4 = pt.new_trace("CA4", 4)
t4.mark("vad_end")
first = t4._marks["vad_end"]
t4.mark("vad_end")                      # second call must be ignored
t4.mark("stt_done")
t4.emit()
r4 = emitted()[-1]
check("T-1 marking one stage twice keeps the FIRST timestamp",
      t4._marks["vad_end"] == first,
      f"{t4._marks['vad_end']!r} != {first!r}")
check("T-1 and the duplicate does not inflate stages_seen",
      r4["stages_seen"] == 2, f"stages_seen={r4['stages_seen']}, expected 2")
check("T-1 the duplicated stage still contributes exactly one segment",
      "seg_vad_end__stt_done_ms" in r4 and r4["seg_vad_end__stt_done_ms"] >= 0,
      str({k: v for k, v in r4.items() if k.startswith("seg_")}))

# --- T-13: a turn aborted mid-generation emits the marks it had ---
# Cancellation is the case the stage list is least able to describe and most
# likely to lie about: marks after `llm_sent` never arrive, and a record that
# fabricated them (or reported the shortfall as a zero) would describe a turn
# that did not happen. `llm_first_token` is deliberately NOT marked here for the
# same reason it is absent in production -- non-streaming has no TTFT (TAC-6).
t5 = pt.new_trace("CA5", 5)
for s in ("vad_end", "stt_done", "llm_sent"):
    t5.mark(s)
t5.emit()                               # caller hung up mid-generation
r5 = emitted()[-1]
check("T-13 an aborted turn still emits a record",
      r5.get("call_id") == "CA5" and r5.get("turn_id") == 5)
check("T-13 it carries the marks it had",
      all(r5.get(k) is not None for k in ("vad_end_ms", "stt_done_ms", "llm_sent_ms")),
      str({k: r5.get(k) for k in ("vad_end_ms", "stt_done_ms", "llm_sent_ms")}))
check("T-13 and invents none of the marks it never reached",
      all(k not in r5 for k in ("llm_done_ms", "tts_done_ms", "first_audio_sent_ms")),
      str({k: r5.get(k) for k in ("llm_done_ms", "tts_done_ms", "first_audio_sent_ms")}))
check("T-13 stages_seen states the shortfall rather than hiding it",
      r5["stages_seen"] < r5["stages_expected"],
      f"{r5['stages_seen']} of {r5['stages_expected']}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
