"""Diagnose why the chat model reloads on every app turn.

Symptom: `ollama ps` reports `Forever` for qwen2.5:14b, `keep_alive: -1` is sent
on every generation, yet every app turn shows load_ms ~6,100 and the server log
shows `cached n_tokens = 0`. Two isolated benchmarks (A4, US-015) both cached
perfectly — but both called the chat model repeatedly with NOTHING in between.
The app calls `nomic-embed-text` for retrieval between every pair of chat calls.

Hypothesis: loading the embedding model evicts the chat model. If true, that is
one cause for both symptoms — a fresh runner means a 6 s load AND an empty KV
cache.

Ladder, each step falsifiable:

  A  chat -> chat                 baseline; must cache (this is what A4 showed)
  B  chat -> embed -> chat        the discriminator
  C  chat -> embed(keep_alive=-1) -> chat
                                  does pinning the embedder prevent the eviction?

Read-only apart from inference. Changes no config.
Run:  .venv/Scripts/python.exe doc/perf/tools/diag_residency_interleave.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from app.voice_system_prompt import build_voice_system_prompt   # noqa: E402

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embeddings"
GEN = "http://localhost:11434/api/generate"
CHAT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
NS = 1e6

CHUNK = ("Meridian University offers undergraduate and postgraduate programmes. "
         "Annual tuition is 185,000 rupees, applications close 30 June. ")
SYSTEM = build_voice_system_prompt("\n\n".join(CHUNK * 3 for _ in range(5)))


def _post(url, payload, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chat(label, vary=False, n=0):
    q = f"Question {n}: what are the fees?" if vary else "What are the fees?"
    ctx = CHUNK * (3 + (n % 3)) if vary else CHUNK * 3
    sysp = build_voice_system_prompt("\n\n".join([ctx] * 5)) if vary else SYSTEM
    r = _post(CHAT, {"model": CHAT_MODEL, "stream": False,
                     "messages": [{"role": "system", "content": sysp},
                                  {"role": "user", "content": q}],
                     "options": {"num_ctx": 8192, "num_predict": 24},
                     "keep_alive": -1})
    load = r.get("load_duration", 0) / NS
    pms = r.get("prompt_eval_duration", 0) / NS
    print(f"    {label:<34} load_ms={load:>8,.0f}  prefill_ms={pms:>8,.0f}  "
          f"ptok={r.get('prompt_eval_count')}")
    return load, pms


def embed(label, keep_alive=None):
    body = {"model": EMBED_MODEL, "prompt": "what are the tuition fees"}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    t0 = time.time()
    _post(EMBED, body)
    print(f"    {label:<34} ({(time.time()-t0)*1000:,.0f} ms)")


def resident():
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        return "?"
    names = [l.split()[0] for l in out.splitlines()[1:] if l.strip()]
    return ",".join(n.split(":")[0] for n in names) or "(none)"


def unload(model):
    try:
        _post(GEN, {"model": model, "keep_alive": 0}, timeout=60)
    except Exception:
        pass


print("=" * 92)
print("RESIDENCY INTERLEAVE DIAGNOSTIC")
print("=" * 92)

results = {}

# ---------------------------------------------------------------- A: baseline
print("\n[A] chat -> chat   (nothing in between — what A4 and US-015 did)")
unload(CHAT_MODEL); unload(EMBED_MODEL); time.sleep(2)
chat("A1 first chat (cold, loads model)")
_, p_a2 = chat("A2 second chat")
print(f"    resident after A: {resident()}")

# ------------------------------------------------------- B: embed interleaved
print("\n[B] chat -> embed -> chat   (what the app does every turn)")
chat("B1 chat")
embed("B2 EMBED (retrieval's step)")
load_b3, p_b3 = chat("B3 chat after embed")
print(f"    resident after B: {resident()}")

# ------------------------------------------- C: embed pinned with keep_alive
print("\n[C] chat -> embed(keep_alive=-1) -> chat")
chat("C1 chat")
embed("C2 EMBED pinned", keep_alive=-1)
load_c3, p_c3 = chat("C3 chat after pinned embed")
print(f"    resident after C: {resident()}")

# ------------------------------------------------------------------- verdict
print("\n" + "=" * 92)
print("VERDICT")
print("=" * 92)
print(f"  A2 (no interleave)          load_ms = {results.get('a2', float('nan')):,.0f}"
      if 'a2' in results else "")
print(f"  A: second chat after first  -> prefill {p_a2:,.0f} ms")
print(f"  B: chat after an embed      -> load {load_b3:,.0f} ms, prefill {p_b3:,.0f} ms")
print(f"  C: chat after a PINNED embed-> load {load_c3:,.0f} ms, prefill {p_c3:,.0f} ms")

evicts = load_b3 > 1000 and p_a2 < 1000
if evicts and load_c3 < 1000:
    print("\n  -> CONFIRMED: the embedding call evicts the chat model, and")
    print("     pinning the embedder with keep_alive prevents it.")
    print("     FIX: give the embedding path a keep_alive (app/llm_backend.py).")
elif evicts:
    print("\n  -> The embedding call evicts the chat model, but pinning the")
    print("     embedder does NOT prevent it. Look at OLLAMA_MAX_LOADED_MODELS.")
elif load_b3 > 1000:
    print("\n  -> The chat model reloads even without an interleaved embed.")
    print("     Something else is evicting it — escalate to OLLAMA_DEBUG=1.")
else:
    print("\n  -> NO EVICTION REPRODUCED in isolation. The cause is elsewhere in")
    print("     the app path (VRAM contention with Whisper/Kokoro is next to test).")
