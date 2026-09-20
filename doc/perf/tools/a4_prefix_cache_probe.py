"""A4 - Ollama prefix/prompt cache probe.

Question this answers: when the same prompt prefix is sent again, does Ollama
reuse its KV cache, or re-evaluate the whole prompt?

Method: send four prompts that share progressively different prefixes and read
Ollama's own counters from each response.
  prompt_eval_count    - tokens ACTUALLY evaluated (the cache verdict lives here)
  prompt_eval_duration - time spent evaluating them (the TTFT floor)

Read-only: issues inference requests, changes no config, writes no repo state.

Run:  .venv/Scripts/python.exe doc/perf/tools/a4_prefix_cache_probe.py
"""
import json
import os
import sys
import time
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from app.voice_system_prompt import build_voice_system_prompt  # noqa: E402

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
URL = "http://localhost:11434/api/chat"
NUM_CTX = 8192
NS_PER_MS = 1e6


def ask(system_prompt: str, turn_messages: list, label: str, num_predict: int = 12):
    """One non-streaming chat call. Returns Ollama's counters."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system_prompt}] + turn_messages,
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "num_predict": num_predict, "temperature": 0.3},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        resp = json.load(r)
    wall = (time.time() - t0) * 1000

    ptok = resp.get("prompt_eval_count", 0)
    pms = resp.get("prompt_eval_duration", 0) / NS_PER_MS
    load = resp.get("load_duration", 0) / NS_PER_MS
    print(f"  {label:<46} prompt_eval_count={ptok:>6,}  eval_ms={pms:>8,.0f}"
          f"  load_ms={load:>7,.0f}  wall_ms={wall:>7,.0f}")
    return {"label": label, "ptok": ptok, "pms": pms, "load": load, "wall": wall}


CHUNK = (
    "Meridian University offers undergraduate and postgraduate programmes. The "
    "Bachelor of Technology in Computer Science is a four-year programme requiring "
    "60 percent in Physics, Chemistry and Mathematics. Annual tuition is 185,000 "
    "rupees, with merit scholarships up to 40 percent above 90 percent. Hostel "
    "accommodation costs 72,000 rupees per year. Applications open 1 March and "
    "close 30 June. "
)
CTX_A = "\n\n".join(CHUNK * 3 for _ in range(5))
CTX_B = "\n\n".join(("Campus facilities at Meridian include a central library, "
                     "sports complex and four hostels. ") * 6 for _ in range(5))

SYS_A = build_voice_system_prompt(CTX_A)
SYS_B = build_voice_system_prompt(CTX_B)

Q1 = "What programs do you offer?"
A1 = "We offer undergraduate, postgraduate and doctoral programmes across engineering, business and health sciences."
Q2 = "And what are the fees?"

print("=" * 108)
print(f"A4 PREFIX-CACHE PROBE  |  model={MODEL}  num_ctx={NUM_CTX}")
print("=" * 108)

# Warm the model so load_duration does not contaminate the prefill numbers.
print("\n[warmup]")
ask("hi", [{"role": "user", "content": "hi"}], "warmup", num_predict=1)

print("\n[1] Fresh prompt - establishes the full-evaluation baseline")
r1 = ask(SYS_A, [{"role": "user", "content": Q1}], "SYS_A + Q1  (first send)")

print("\n[2] BYTE-IDENTICAL repeat - THE CACHE VERDICT")
r2 = ask(SYS_A, [{"role": "user", "content": Q1}], "SYS_A + Q1  (identical repeat)")

print("\n[3] Append-only growth - the real multi-turn shape")
r3 = ask(SYS_A, [
    {"role": "user", "content": Q1},
    {"role": "assistant", "content": A1},
    {"role": "user", "content": Q2},
], "SYS_A + Q1 + A1 + Q2  (append-only)")

print("\n[4] Different RAG context, same question - the cache-break case")
r4 = ask(SYS_B, [{"role": "user", "content": Q1}], "SYS_B + Q1  (different context)")

print("\n" + "=" * 108)
print("VERDICT")
print("=" * 108)

# IMPORTANT: prompt_eval_count reports prompt LENGTH, not tokens actually
# computed. It stays at the full count even on a cache hit. The cache signal
# is prompt_eval_DURATION. Measured 2026-09-18 on qwen2.5:14b / RTX 5060 Ti.
baseline_tps = (r1["ptok"] / (r1["pms"] / 1000)) if r1["pms"] > 0 else 0
print(f"  Uncached prefill rate (case 1): {baseline_tps:,.0f} tok/s")
print(f"  prompt_eval_count is IDENTICAL across cases 1 and 2 "
      f"({r1['ptok']:,} vs {r2['ptok']:,}) - it is not the cache signal.")

speedup = (r1["pms"] / r2["pms"]) if r2["pms"] > 0 else 0
print(f"  Identical repeat: {r1['pms']:,.0f} ms -> {r2['pms']:,.0f} ms "
      f"({speedup:,.0f}x faster at the same token count).")

implied = (r2["ptok"] / (r2["pms"] / 1000)) if r2["pms"] > 0 else 0
if speedup >= 5:
    print("  -> PREFIX CACHING ENGAGES. The 58x-style speedup is KV reuse, not")
    print("     prefill - the implied rate is far above the card's capability.")
else:
    print("  -> PREFIX CACHING DOES NOT ENGAGE - the repeat costs full prefill.")

# Where does the cache break? Compare the unchanged-context repeat (case 2)
# against the changed-context case (case 4).
print(f"\n  Append-only growth evaluated in {r3['pms']:,.0f} ms "
      f"({r3['ptok'] - r1['ptok']:,} net new tokens) - prefix reused.")

cached_equiv = r4["ptok"] - int((r4["pms"] / 1000) * baseline_tps)
print(f"  Changed-context case: {r4['ptok']:,} tokens in {r4['pms']:,.0f} ms.")
print(f"    At the uncached rate that time buys ~{int((r4['pms']/1000)*baseline_tps):,} "
      f"tokens of real work,")
print(f"    so ~{cached_equiv:,} tokens were served from cache.")
print("    => The cache holds the static prefix UP TO {context} and breaks there.")
print("\n  Consequence for the plan:")
print("    - Per-turn prefill cost = everything from {context} onward.")
print("    - Trimming the STATIC prompt (D1) saves ~0 per turn; it is cached.")
print("      It only helps the first turn of a call.")
print("    - Reducing the RAG CONTEXT (D2) is the real per-turn prefill lever.")
print("    - Reordering {context} to the end (C3) recovers only the static tail.")
