"""US-011 TAC-1 — import order must not decide the resolved configuration.

THE DEFECT THIS PROVES GONE. Before Phase 1.2, `app/llm_backend.py`,
`app/rag_legacy.py` and `app/voice_handler.py` (among others) read `os.environ`
at their own import time and never loaded `.env` themselves. The value a module
resolved therefore depended on whether some EARLIER import had happened to load
`.env` first. The measured instance: `from app.leads import models` imported
first left `DATABASE_URL` unset and silently pointed the process at a different
database from the operator's, because those four copies of the six `DB_*` reads
had no loader of their own.

Every module now resolves through `app/config.py`, which loads `.env` itself, so
the answer is the same whoever imports first. That claim is testable directly,
without hard-coding a single expected value:

  bare      a process that imports the module and nothing else
  explicit  the same, except the process calls load_dotenv() first
  default   the same, except the loader is neutered -- this is the value that
            would be in force if `.env` did not exist

  bare == explicit   import order no longer decides      (the property)
  bare != default    `.env` was genuinely consulted      (not vacuous)

Every child runs with the SAME environment, with every key named in `.env`
removed, so nothing leaks in from the parent and the three runs differ only in
whether `.env` gets loaded.

Sampling MODULE CONSTANTS rather than `settings` fields is deliberate: `settings`
is a single object that always loads `.env`, so it cannot exhibit the defect and
comparing it would be a tautology. The module-level constants are where the
defect lived.

The negative control plants the old pattern and shows the harness catches it, so
a pass above cannot mean "the detector is blind" -- which matters here because
almost every assertion is an equality, and equalities pass trivially when
nothing is being compared.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us011_import_order.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]      # doc/perf/tools -> repo root
os.chdir(PROJ)
sys.path.insert(0, str(PROJ))

from app import config_truth as ct          # noqa: E402

passed = failed = noted = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def note(text):
    global noted
    noted += 1
    print(f"  NOTE  {text}")


#: module -> the module-level constants Phase 1.2 moved onto Settings. A name a
#: module does not define is reported as a failure, not skipped, so a typo in
#: this table cannot silently shrink the test.
SAMPLES = (
    ("app.voice_handler", ("_STT_MODEL_SIZE", "TTS_VOICE", "TTS_SPEED",
                           "TTS_CACHE_SCOPE", "TTS_STREAM",
                           "STT_MIN_AVG_LOGPROB", "STT_MAX_NO_SPEECH_PROB",
                           "STT_MIN_CHARS", "MIN_UTTERANCE_FRAMES",
                           "VAD_SILENCE_MS", "VAD_SPECULATIVE_ADVANCE_MS")),
    ("app.llm_backend", ("OLLAMA_BASE_URL", "OLLAMA_MODEL", "DEFAULT_NUM_CTX",
                         "OLLAMA_TEMPERATURE", "EMBED_MODEL", "LLM_STREAM",
                         "MLX_BASE_URL", "MLX_MODEL", "MLX_PORT",
                         "MLX_EMBED_MODEL", "MLX_MAX_TOKENS", "KEEP_ALIVE")),
    ("app.rag_legacy", ("CHROMA_DB_PATH", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
                        "OLLAMA_TEMPERATURE", "EMBED_MODEL", "CHUNK_SIZE",
                        "CHUNK_OVERLAP", "MMR_FETCH_K", "MMR_K",
                        "RAG_SEARCH_MODE", "RAG_SIMILARITY_THRESHOLD",
                        "RAG_MAX_CONTEXT_CHARS")),
    ("app.pipeline", ("CHROMA_DB_PATH", "DEFAULT_LLM_MODEL", "OLLAMA_BASE_URL",
                      "STT_MODEL", "TTS_VOICE")),
    ("app.rag", ("OLLAMA_NUM_PREDICT",)),
    ("app.main", ("MUTE_STT_DURING_TTS",)),
)

#: The child. `default` mode must neuter the loader BEFORE app.config imports
#: it, which is why `dotenv.load_dotenv` is patched rather than anything inside
#: app.config -- `from dotenv import load_dotenv` picks the patch up.
#:
#: The result is written behind a sentinel rather than as the only stdout line,
#: because importing these modules emits log lines and a bare JSON parse would
#: be at the mercy of whatever logged last.
_CHILD = r'''
import json, sys
mode, modname, names, env_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

if mode == "explicit":
    from dotenv import load_dotenv
    load_dotenv(env_path)
elif mode == "default":
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False

mod = __import__(modname, fromlist=["*"])
out = {}
for n in [x for x in names.split(",") if x]:
    v = getattr(mod, n, "<absent>")
    out[n] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
sys.stdout.write("\n@@RESULT@@" + json.dumps(out) + "\n")
'''


def _run(mode, module, names, extra_path=None):
    """(values, error). Runs one child and returns the constants it reported."""
    env = {k: v for k, v in os.environ.items() if k not in _env_keys()}
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run(
            [sys.executable, "-c", _CHILD, mode, module, ",".join(names),
             str(PROJ / ".env")],
            capture_output=True, text=True, cwd=str(PROJ), env=env, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out after 300 s"
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@"):]), None
    tail = ((p.stderr or "").strip().splitlines() or ["<no stderr>"])[-1]
    return None, f"no result (exit {p.returncode}); stderr tail: {tail}"


def _diff(a, b):
    return ", ".join(f"{k}: {a.get(k)!r} != {b.get(k)!r}"
                     for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k))


_env_cache = None


def _env_keys():
    global _env_cache
    if _env_cache is None:
        _env_cache = set(ct.parse_env_file())
    return _env_cache


print("=" * 74)
print("US-011 TAC-1 -- resolved configuration vs import order")
print()
print("  bare == explicit  -> import order no longer decides")
print("  bare != default   -> .env was genuinely consulted")
print("=" * 74)

# ── Negative control FIRST. If the harness cannot see the old pattern, every
#    equality below is worthless. The planted module is exactly the shape the
#    migration removed: a module-level os.environ read with no loader.
print("\n-- NEGATIVE CONTROL: the harness detects the old pattern --")
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "_us011_legacy_probe.py").write_text(
        "import os\n"
        'X = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q3_K_M")\n',
        encoding="utf-8")
    ctl_bare, e1 = _run("bare", "_us011_legacy_probe", ("X",), extra_path=td)
    ctl_expl, e2 = _run("explicit", "_us011_legacy_probe", ("X",), extra_path=td)
    check("the harness can import a module from outside app/",
          ctl_bare is not None and ctl_expl is not None, f"{e1 or ''} {e2 or ''}")
    check("NEGATIVE CONTROL: that module's value DOES depend on import order, so "
          "the equality asserted below is a real assertion",
          ctl_bare is not None and ctl_expl is not None and ctl_bare != ctl_expl,
          f"bare={ctl_bare} explicit={ctl_expl} -- the control did not diverge, "
          "so the harness cannot see the defect it exists to detect")

# ── The property, per module.
print("\n-- bare == explicit: import order no longer decides --")
compared = 0
for module, names in SAMPLES:
    bare, e1 = _run("bare", module, names)
    expl, e2 = _run("explicit", module, names)
    if bare is None or expl is None:
        check(f"{module} resolves the same however it is reached", False,
              f"import failed: {e1 or e2}")
        continue
    compared += len(bare)
    absent = sorted(k for k, v in bare.items() if v == "<absent>")
    check(f"{module} resolves the same however it is reached",
          bare == expl and not absent,
          f"the sample table names attributes this module does not define: "
          f"{absent}" if absent else _diff(bare, expl))

# ── Vacuity. The equality above would ALSO hold if `.env` were never consulted,
#    so require evidence that it changes something. Where every sampled value
#    equals its no-.env default the test genuinely cannot tell "reads .env" from
#    "ignores .env" -- that is a fact about `.env`, not a defect, so it is
#    reported rather than failed. The global check below keeps the suite honest.
print("\n-- .env is genuinely consulted --")
non_vacuous = []
for module, names in SAMPLES:
    bare, e1 = _run("bare", module, names)
    dflt, e2 = _run("default", module, names)
    if bare is None or dflt is None:
        check(f"{module}: reaches a value .env actually changes", False,
              f"import failed: {e1 or e2}")
        continue
    changed = sorted(k for k in bare if bare.get(k) != dflt.get(k))
    if changed:
        non_vacuous.append(module)
        check(f"{module}: .env changes {len(changed)} of {len(bare)} sampled "
              f"values ({', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''})",
              True)
    else:
        note(f"{module}: no sampled value differs from its no-.env default, so "
             f"this test cannot distinguish 'reads .env' from 'ignores .env' "
             f"here. Not a failure -- its .env keys equal their code defaults, "
             f"or are absent from .env. `test_us011_config_truth.py` is what "
             f"covers whether it reads through Settings at all.")

check(f"the suite is not vacuous ({compared} constants compared across "
      f"{len(SAMPLES)} modules; {len(non_vacuous)} proved .env is consulted)",
      len(non_vacuous) > 0,
      "not one module's value changed when .env was withheld, so every equality "
      "above passed without comparing anything meaningful")

print(f"\n{passed} passed, {failed} failed, {noted} noted")
sys.exit(1 if failed else 0)
