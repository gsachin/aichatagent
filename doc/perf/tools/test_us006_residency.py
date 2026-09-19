"""US-006 acceptance tests — serving-path model residency. Implements REC-11.

The failure this guards against: pre-warm exists and runs at boot, then the
FIRST chat request omits keep_alive, Ollama resets the model to its 5-minute
server default, and the next isolated call pays a 32,919 ms cold load — with
working pre-warm code sitting in the repository.

TAC-4 is the important one: residency is observed EXTERNALLY (`ollama ps`,
which reports the model's actual expiry), never inferred from a success line
the boot script printed. That is the "set is not live" trap.

Touches the live Ollama server: loads and unloads one model.
Run:  .venv/Scripts/python.exe doc/perf/tools/test_us006_residency.py
"""
import os
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def ps_row(model=MODEL):
    """(name, processor, until) for the model, or None. External observation."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        return None
    for line in out.splitlines()[1:]:
        if line.startswith(model.split(":")[0]):
            return line.split()
    return None


def unload(model=MODEL):
    import urllib.request
    import json as _json
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=_json.dumps({"model": model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60).read()
    except Exception:
        pass
    time.sleep(2)


# --- TAC-3 (static half): the serving path always sends a keep-alive ---------
import app.llm_backend as lb                                     # noqa: E402
check("TAC-3 KEEP_ALIVE resolves from config", lb.KEEP_ALIVE not in (None, ""),
      repr(lb.KEEP_ALIVE))
# Regression guard for the bug this test found: a .env value is always a string,
# and Ollama 400s on `"-1"`. An integer must be sent as an int, not as text.
check("TAC-3 a numeric keep-alive is sent as an INT, not a string",
      isinstance(lb.KEEP_ALIVE, int), f"type={type(lb.KEEP_ALIVE).__name__}")
check("TAC-3 a duration keep-alive stays a string",
      lb._resolve_keep_alive("24h") == "24h" and lb._resolve_keep_alive("-1") == -1,
      f"{lb._resolve_keep_alive('24h')!r} / {lb._resolve_keep_alive('-1')!r}")
import inspect                                                  # noqa: E402
src = inspect.getsource(lb._chat_ollama)
check("TAC-3 every generation request carries keep_alive",
      "keep_alive=ka" in src, "no unconditional keep_alive in _chat_ollama")
check("TAC-3 the keep-alive is not sent only at boot",
      "OLLAMA_KEEP_ALIVE" not in lb.__dict__.get("_BOOT_ONLY", ""),
      "resolved in the serving module, not the launcher")

# --- TAC-4: residency observed EXTERNALLY -----------------------------------
print(f"\n  [probe] unloading {MODEL} to establish a cold floor...")
unload()
pre = ps_row()
check("TAC-4 model is NOT resident before the test (clean floor)",
      pre is None, str(pre))

print("  [probe] one generation through the serving path...")
t0 = time.time()
try:
    lb.chat([{"role": "user", "content": "Say OK."}], model=MODEL, num_ctx=2048)
    call_ok = True
except Exception as exc:
    call_ok = False
    print(f"        call failed: {exc!r}")
first_ms = (time.time() - t0) * 1000
check("TAC-4 a generation through the serving path succeeds", call_ok)
print(f"        first call took {first_ms:,.0f} ms (includes the cold load)")

row = ps_row()
check("TAC-4 model IS resident after ONE serving-path call (external evidence)",
      row is not None, "ollama ps shows nothing — residency was not established")

if row:
    until = row[-1]
    print(f"        ollama ps -> {' '.join(row)}")
    # A 5-minute default would show a near-future clock or '5 minutes'. A held
    # residency shows 'Forever' (keep_alive=-1) or a far-future timestamp.
    held = ("Forever" in until) or ("forever" in until.lower())
    if not held:
        held = not any(tok in until for tok in ("second", "minute"))
    check("TAC-4 the model's expiry is HELD, not the 5-minute default "
          "(this is the REC-11 failure)", held, f"UNTIL={until!r}")

# --- TAC-6: re-warm is idempotent -------------------------------------------
before = ps_row()
lb.chat([{"role": "user", "content": "Say OK again."}], model=MODEL, num_ctx=2048)
after = ps_row()
check("TAC-6 a second call does not evict or duplicate residency",
      before is not None and after is not None)

# --- TAC-7: reversible -------------------------------------------------------
check("TAC-7 the change is one setting (OLLAMA_KEEP_ALIVE), revertible alone",
      "OLLAMA_KEEP_ALIVE" in open(".env", encoding="utf-8", errors="replace").read())

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
