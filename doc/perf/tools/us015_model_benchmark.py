"""US-015 - Model/runtime Pareto benchmark (latency leg). v2.

Fixes three flaws in v1:
  1. v1 reused one identical prompt, so the KV prefix cache was always warm and
     "prefill" measured the fully-cached case. Production does not look like
     that: retrieval returns different chunks per turn, so the cache breaks at
     {context}. v2 varies the context per call to measure the realistic
     cache-break cost, and reports the fully-cached case separately.
  2. v1 divided the concurrent aggregate by 2, understating it. v2 computes
     aggregate from wall time.
  3. v1 measured decode RATE, which cannot distinguish batching from queueing -
     both leave per-stream rate intact. v2 measures WALL TIME, which can:
     if N=2 wall ~ N=1 wall, the engine batched; if N=2 wall ~ 2x N=1 wall, it
     serialised. This is the difference between "two callers served together"
     and "two callers taking turns", and it is the whole concurrency question.

The plan's rule (MOD-03 A.5 criterion 3, TRD-22): the N=2 rate is a
MEASUREMENT, not a requirement. This script asserts no floor.

Quality is NOT measured here - that needs the frozen golden set (DG-03).
Read-only: issues inference requests, changes no config, writes no repo state.
Run:  .venv/Scripts/python.exe doc/perf/tools/us015_model_benchmark.py
"""
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from app.voice_system_prompt import build_voice_system_prompt  # noqa: E402

URL = "http://localhost:11434/api/chat"
NUM_CTX = 8192
NS_PER_MS = 1e6
NUM_PREDICT = 100

CANDIDATES = [m.strip() for m in os.environ.get(
    "BENCH_MODELS", "llama3.2:3b,qwen2.5:14b").split(",") if m.strip()]

# Distinct retrieval results, so each call breaks the prefix cache at {context}
# the way a real turn does.
_CHUNKS = [
    "Meridian University offers undergraduate and postgraduate programmes. The Bachelor of Technology in Computer Science is a four-year programme requiring 60 percent in Physics, Chemistry and Mathematics. Annual tuition is 185,000 rupees, with merit scholarships up to 40 percent above 90 percent. Applications open 1 March and close 30 June.",
    "Hostel accommodation is guaranteed for first-year students and costs 72,000 rupees per year including meals. Campus facilities include a central library, sports complex and four hostels. The admissions office can be reached at admissions@meridian.edu.",
    "The School of Business offers an MBA requiring a bachelor's degree with 50 percent and a valid entrance score. Fees for the MBA are 240,000 rupees per year. Scholarships of up to 30 percent are available for candidates with two years of work experience.",
    "Doctoral programmes require a master's degree with 55 percent and a research proposal. Full-time PhD scholars receive a stipend of 25,000 rupees per month. Applications for the doctoral intake close on 31 May.",
    "Meridian University was established in 1994 and holds NAAC A+ accreditation. It comprises six schools with over 12,000 students. The placement cell reports an average package of 620,000 rupees for engineering graduates.",
    "Scholarships and financial aid include merit awards, need-based grants and sports quotas. Merit scholarships cover up to 40 percent of tuition for candidates scoring above 90 percent in the qualifying examination. Education loans are facilitated through partner banks.",
]
_QUESTIONS = [
    "What programs do you offer?", "Tell me about the fees.",
    "What are the important dates?", "Do you have hostel facilities?",
    "What scholarships are available?", "Is the university accredited?",
]


def vram_used_mib():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def one_call(model, idx, results, slot, vary=True):
    """One non-streaming chat call. vary=True changes the context each call."""
    if vary:
        ctx = "\n\n".join(_CHUNKS[(idx * 2 + k) % len(_CHUNKS)] for k in range(5))
        question = _QUESTIONS[idx % len(_QUESTIONS)]
    else:
        ctx = "\n\n".join([_CHUNKS[0]] * 5)
        question = _QUESTIONS[0]
    system = build_voice_system_prompt(ctx)

    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": question}],
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT,
                    "temperature": 0.3},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            resp = json.load(r)
    except Exception as exc:
        results[slot] = {"error": repr(exc)}
        return
    wall = (time.time() - t0) * 1000
    ptok = resp.get("prompt_eval_count", 0)
    pms = resp.get("prompt_eval_duration", 0) / NS_PER_MS
    gtok = resp.get("eval_count", 0)
    gms = resp.get("eval_duration", 0) / NS_PER_MS
    results[slot] = {
        "wall_ms": wall, "load_ms": resp.get("load_duration", 0) / NS_PER_MS,
        "prompt_tokens": ptok, "prefill_ms": pms,
        "decode_tokens": gtok, "decode_ms": gms,
        "prefill_tps": (ptok / (pms / 1000)) if pms > 0 else 0.0,
        "decode_tps": (gtok / (gms / 1000)) if gms > 0 else 0.0,
    }


def run_concurrent(model, n, rounds, vary=True):
    """Fire n simultaneous requests per round; return per-round groups."""
    groups = []
    for rnd in range(rounds):
        slots = [None] * n
        threads = [
            threading.Thread(target=one_call,
                             args=(model, rnd * n + i, slots, i, vary))
            for i in range(n)]
        wall0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        group_wall = (time.time() - wall0) * 1000
        ok = [s for s in slots if s and "error" not in s]
        if ok:
            groups.append({"wall_ms": group_wall, "streams": ok})
    return groups


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


print("=" * 100)
print("US-015 MODEL / RUNTIME BENCHMARK v2  -  latency leg")
print("Context VARIES per call, so the prefix cache breaks at {context} as it")
print("does in a real turn. This measures the realistic case, not a warm-cache best case.")
print("=" * 100)

summary = []
for model in CANDIDATES:
    print(f"\n{'=' * 100}\nMODEL: {model}\n{'=' * 100}")

    warm = [None]
    one_call(model, 0, warm, 0, vary=False)
    w = warm[0] or {}
    if "error" in w:
        print(f"  warmup FAILED - {w['error']}")
        continue
    print(f"  [warmup] load {w.get('load_ms', 0):,.0f} ms, "
          f"{w.get('prompt_tokens', 0):,} prompt tokens")
    # Unload every other model so VRAM reflects THIS model alone. Without this
    # the previous candidate stays resident and the peak is two models, not one.
    for other in CANDIDATES:
        if other.strip() != model:
            try:
                urllib.request.urlopen(urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=json.dumps({"model": other.strip(), "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=60).read()
            except Exception:
                pass
    time.sleep(2)
    vram_load = vram_used_mib()

    # Fully-cached reference (identical prompt) - the turn-2+ best case.
    cached = run_concurrent(model, 1, 3, vary=False)
    cached_prefill = avg([s["prefill_ms"] for g in cached for s in g["streams"]])

    # Realistic (context varies) - the cache-break case.
    r1 = run_concurrent(model, 1, 3, vary=True)
    s1 = [s for g in r1 for s in g["streams"]]
    n1_prefill = avg([s["prefill_ms"] for s in s1])
    n1_decode = avg([s["decode_tps"] for s in s1])
    n1_wall = avg([g["wall_ms"] for g in r1])
    n1_ptok = avg([s["prompt_tokens"] for s in s1])

    r2 = run_concurrent(model, 2, 3, vary=True)
    s2 = [s for g in r2 for s in g["streams"]]
    n2_prefill = avg([s["prefill_ms"] for s in s2])
    n2_decode_each = avg([s["decode_tps"] for s in s2])
    n2_wall = avg([g["wall_ms"] for g in r2])

    # Aggregate throughput, bracketed honestly.
    # Batching and serialisation both preserve per-stream rate, so only the
    # OVERLAP tells them apart. Using the engine's own decode durations:
    #   sum(tokens)/sum(ms)  = aggregate if the streams ran one after another
    #   sum(tokens)/max(ms)  = aggregate if the streams ran together
    # The truth lies between; the wall ratio below says where.
    lo, hi = [], []
    for g in r2:
        toks = sum(s["decode_tokens"] for s in g["streams"])
        dsum = sum(s["decode_ms"] for s in g["streams"])
        dmax = max((s["decode_ms"] for s in g["streams"]), default=0)
        if dsum > 0:
            lo.append(toks / (dsum / 1000))
        if dmax > 0:
            hi.append(toks / (dmax / 1000))
    agg_lo, agg_hi = avg(lo), avg(hi)
    vram_peak = vram_used_mib()

    wall_ratio = (n2_wall / n1_wall) if n1_wall > 0 else 0.0
    if wall_ratio < 1.4:
        verdict = "BATCHED - both callers served together"
    elif wall_ratio > 1.7:
        verdict = "SERIALISED - callers took turns; N=2 is a queue"
    else:
        verdict = "PARTIAL - some overlap, some queueing"

    print(f"\n  prompt ~{n1_ptok:,.0f} tokens (cache-break case)")
    print(f"  cached prefill (identical prompt) : {cached_prefill:,.0f} ms")
    print(f"  realistic prefill (context varies): {n1_prefill:,.0f} ms")
    print(f"  N=1  decode {n1_decode:.1f} tok/s | round wall {n1_wall:,.0f} ms")
    print(f"  N=2  decode/stream {n2_decode_each:.1f} tok/s | aggregate "
          f"{agg_lo:.1f}-{agg_hi:.1f} tok/s | round wall {n2_wall:,.0f} ms")
    print(f"  N=2 wall / N=1 wall = {wall_ratio:.2f}x  ->  {verdict}")
    print(f"  VRAM after load {vram_load:,} MiB | peak {vram_peak:,} MiB "
          f"({100.0 * vram_peak / 16311:.0f}% of 16,311)")

    summary.append({
        "model": model, "cached_prefill_ms": round(cached_prefill),
        "realistic_prefill_ms": round(n1_prefill),
        "n1_decode_tps": round(n1_decode, 1), "n1_wall_ms": round(n1_wall),
        "n2_decode_per_stream": round(n2_decode_each, 1),
        "n2_agg_lo": round(agg_lo, 1), "n2_agg_hi": round(agg_hi, 1),
        "n2_wall_ms": round(n2_wall),
        "wall_ratio": round(wall_ratio, 2), "verdict": verdict,
        "vram_peak_mib": vram_peak, "vram_pct": round(100.0 * vram_peak / 16311, 1),
    })

print(f"\n{'=' * 100}\nSUMMARY\n{'=' * 100}")
hdr = (f"{'model':<16}{'prefill':>9}{'N=1 dec':>9}{'N=2/str':>9}"
       f"{'N=2 agg':>9}{'wall x':>8}{'VRAM':>9}  verdict")
print(hdr)
print("-" * 100)
for s in summary:
    print(f"{s['model']:<16}{s['realistic_prefill_ms']:>7,}ms{s['n1_decode_tps']:>9.1f}"
          f"{s['n2_decode_per_stream']:>9.1f}"
          f"{s['n2_agg_lo']:>6.0f}-{s['n2_agg_hi']:<5.0f}"
          f"{s['wall_ratio']:>7.2f}{s['vram_pct']:>8.0f}%  {s['verdict'][:22]}")

print("\nAll figures MEASURED on this box from Ollama's own counters.")
print("'prefill' is the realistic cache-break case; the fully-cached figure is")
print("reported separately because production hits the former every turn.")
print("\nQUALITY IS NOT MEASURED - the golden set (DG-03) is not frozen, so no")
print("candidate is selected and none is recommended on this evidence alone.")
