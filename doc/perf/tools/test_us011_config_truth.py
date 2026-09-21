"""US-011 acceptance tests for app/config_truth.py. No network, no model.

Covers the story's technical acceptance criteria:
  TAC-2  inert keys are found and reported by name
  TAC-3  FASTAPI_WORKERS resolves to the truth, not to a comfortable fiction
  TAC-4  no secret VALUE appears in the report
  TAC-5  a malformed value is named, not silently defaulted
  AC-1   .env is the authority; the detection artifact never wins

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us011_config_truth.py
"""
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

from app import config_truth as ct          # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


env = ct.parse_env_file()
report = ct.report()

# --- AC-1 / REC-05: .env is the authority; the artifact never wins ---------
check("AC-1 .env is the authority and was actually parsed", len(env) > 20,
      f"{len(env)} keys")
prov = {ev.key: ev.source for ev in ct.effective_configuration()}
# This check used to read `all(s.startswith("authoritative"))`. That was true
# only because `source` was a CONSTANT -- the assertion enshrined the defect,
# and implementing TAC-6 correctly is what broke it (58 `Settings` fields with
# no `.env` entry now report "code default", which is the point). The property
# worth asserting is that each origin is a KNOWN one and that they are
# DISTINGUISHED rather than stamped.
_ORIGINS = {"authoritative (.env)", "code default"}
check("AC-1 every key reports a provenance",
      bool(prov) and set(prov.values()) <= _ORIGINS, str(set(prov.values())))
check("TAC-6 the origins are distinguished, not a single stamped value",
      len(set(prov.values())) >= 2,
      f"a constant source answers nothing: {set(prov.values())}")

# TAC-6's third origin. Exactly the keys where the artifact disagrees with .env
# must carry the note, and no others -- stated as an equality so the check holds
# (and still tests something) on a box with no detection artifact at all.
_env_now = ct.parse_env_file()
_applied = ct._profile_applied()
_divergent = {k for k, v in _applied.items() if k in _env_now and _env_now[k] != v}
_noted = {e.key for e in ct.effective_configuration() if e.artifact}
check("TAC-6 an overridden artifact value is recorded, and only where it was overridden",
      _noted == _divergent, f"noted {sorted(_noted)}, expected {sorted(_divergent)}")

# --- T-10: no authoritative file -> documented defaults, reported as such ----
# Before 2026-09-21 this scenario was not merely untested, it was UNREACHABLE:
# the row set WAS `.env`'s keys, so removing the file produced an EMPTY report
# rather than a defaulted one, and "documented default" was not an origin the
# code could express.
#
# The simulation has to strip os.environ as well as the file. `app/config.py`
# calls `load_dotenv` at import, so the singleton `settings` already holds the
# `.env` values; patching the file reader alone would leave `settings` serving
# configured values while the report claimed "code default" -- a green test
# asserting the wrong thing, which is the failure mode this whole file exists
# to avoid. Rebuilding `Settings()` with the keys gone is what a fresh clone
# actually does.
import app.config as _cfg                                   # noqa: E402

_removed = {k: os.environ.pop(k) for k in list(_env_now) if k in os.environ}
_real_parse, _real_settings = ct.parse_env_file, ct.settings
try:
    ct.parse_env_file = lambda path=None: {}
    ct.settings = _cfg.Settings()          # rebuilt with the keys absent
    _defaults = ct.effective_configuration()
finally:
    ct.parse_env_file, ct.settings = _real_parse, _real_settings
    os.environ.update(_removed)

check("T-10 with no authoritative file every key still reports",
      len(_defaults) > 20, f"{len(_defaults)} rows")
check("T-10 ...and each is sourced from a documented code default",
      all(v.source == "code default" for v in _defaults),
      str({v.source for v in _defaults}))
check("T-10 ...and none claims the artifact was overridden",
      all(v.artifact == "" for v in _defaults),
      str([v.key for v in _defaults if v.artifact][:5]))
check("T-10 ...and the restored run is back to reporting both origins",
      {v.source for v in ct.effective_configuration()} == _ORIGINS,
      "the simulation leaked into the live configuration")

# --- T-11: a renamed key leaves the old name reported inert ------------------
# The scenario's point is that a rename cannot leave a silently-ignored setting
# behind: the NEW name is read, and the OLD name -- still sitting in the file --
# is reported inert rather than carried as though it did something.
_rename_probe = dict(_env_now)
_rename_probe["OLLAMA_MODEL_V2"] = _env_now.get("OLLAMA_MODEL", "x")
_renamed_inert = ct.sweep_inert_keys(_rename_probe)
check("T-11 a renamed key leaves the old name reported inert",
      "OLLAMA_MODEL_V2" in _renamed_inert, str(_renamed_inert))
check("T-11 ...while the live name is not reported",
      "OLLAMA_MODEL" not in _renamed_inert)

# --- T-12: the five known instances each resolve, none in a third state ------
# REC-01 (the Pipecat VADParams block), REC-02 (FASTAPI_WORKERS), REC-05 and
# REC-11 (the sizing divergence and the pre-warm keep-alive), and the four keys
# the inert sweep originally named. "Read" or "reported inert" -- never a third
# state where a value sits in the file implying it is doing something.
_reported = {e.key: e for e in ct.effective_configuration()}
_four_named = ["FASTAPI_WORKERS", "KOKORO_SPEED", "LOG_FILE", "LOG_LEVEL"]
check("T-12 the four named keys each resolve to a reader, not to a third state",
      all(k in _reported and not _reported[k].inert for k in _four_named),
      str([k for k in _four_named if k not in _reported or _reported[k].inert]))
check("T-12 ...and each states a known origin",
      all(_reported[k].source in _ORIGINS for k in _four_named),
      str({k: _reported[k].source for k in _four_named}))
_pipeline_src = (ct.PROJECT_ROOT / "app" / "pipeline.py").read_text(
    encoding="utf-8", errors="replace")
check("T-12 REC-01 the dead Pipecat VADParams block is gone, not merely marked",
      "VADParams(" not in _pipeline_src,
      "a dead block that reads as live endpointing configuration is back")


# --- T-15: a malformed key stops the stack AND names itself -----------------
# Both halves in one place, because until 2026-09-21 no single path had both. A
# key parsed at import stopped the stack but named only the VALUE, inside a
# traceback (`ValueError: invalid literal for int() ... 'notanumber'`); a key
# that survived import was named properly by `check_config_keys`, but that made
# the stack not-READY rather than stopping it. `_env_int` / `_env_float` now
# raise `app.config.ConfigurationError` naming the key.
import subprocess                                                # noqa: E402


def _boot_with(key: str, value: str):
    """Import app.config in a CHILD process with `key` set. Returns (rc, output).

    A child process is the point: the failure this tests for happens at import,
    and the module is already imported here.
    """
    env = dict(os.environ)
    env[key] = value
    r = subprocess.run([sys.executable, "-c", "from app.config import settings"],
                       capture_output=True, text=True, env=env, timeout=180)
    return r.returncode, (r.stderr or "") + (r.stdout or "")


_rc_int, _out_int = _boot_with("VAD_SILENCE_MS", "notanumber")
check("T-15 a malformed int STOPS the stack before it serves", _rc_int != 0,
      f"exit {_rc_int}")
check("T-15 ...and the KEY is named in the operator-visible output",
      "VAD_SILENCE_MS" in _out_int and "not an integer" in _out_int,
      (_out_int.strip().splitlines() or [""])[-1][:140])

_rc_flt, _out_flt = _boot_with("RAG_MCP_TIMEOUT", "soon")
check("T-15 a malformed float stops the stack and names its key",
      _rc_flt != 0 and "RAG_MCP_TIMEOUT" in _out_flt and "not a number" in _out_flt,
      f"exit {_rc_flt}: " + (_out_flt.strip().splitlines() or [""])[-1][:140])

# The control that stops this passing for the wrong reason: if the child died
# for some unrelated cause, the two checks above would still be green.
_rc_ok, _out_ok = _boot_with("VAD_SILENCE_MS", "450")
check("T-15 a VALID value still boots, so the stop is the VALUE's doing",
      _rc_ok == 0,
      f"exit {_rc_ok}: " + (_out_ok.strip().splitlines() or [""])[-1][:140])

# --- TAC-2: inert keys found, by name --------------------------------------
inert = ct.sweep_inert_keys(env)
# TAC-2 originally asserted that FASTAPI_WORKERS and KOKORO_SPEED were reported
# inert. That was correct when written -- it is the finding that started US-011
# -- but all four unread keys now have readers (LOG_LEVEL/LOG_FILE in
# app/main.py, KOKORO_SPEED in app/voice_handler.py, FASTAPI_WORKERS
# reconciled at startup). The assertion therefore inverts to the state the
# story was reaching for: the sweep must still run and still name names, but
# the set is empty. It stays a real gate -- adding any unread key to .env, or
# removing a reader, fails it.
check("TAC-2 the inert sweep returns a sequence of names",
      isinstance(inert, (list, tuple)) and all(isinstance(k, str) for k in inert),
      str(inert))
check("TAC-2 no managed key is inert (REC-02, closed)",
      len(inert) == 0, f"still inert: {inert}")
check("TAC-2 the four previously-inert keys are no longer reported",
      not {"FASTAPI_WORKERS", "KOKORO_SPEED", "LOG_FILE", "LOG_LEVEL"} & set(inert),
      "a reader was removed")

# Positive control: the sweep must still be *able* to find an unread key. Without
# this, an empty result above could mean "clean" or could mean "detector broken".
probe = dict(env)
probe["US011_CANARY_UNREAD_KEY"] = "1"
check("TAC-2 the sweep still detects a planted unread key",
      "US011_CANARY_UNREAD_KEY" in ct.sweep_inert_keys(probe),
      "detector is broken, not the config")

# ...and the other half of that control, because the engine carve-out and the
# canary pull in opposite directions. An engine key planted in `.env` must be
# reported as EXTERNALLY CONSUMED rather than inert, or an operator who
# documents one is told a live engine setting is dead. But an OLLAMA_* key
# that nothing reads and that is NOT on the list must still be caught, or the
# carve-out has quietly become the `OLLAMA` prefix it deliberately is not.
_ENGINE = ("OLLAMA_NUM_PARALLEL", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_FLASH_ATTENTION")
engine_probe = dict(env)
engine_probe.update({k: "2" for k in _ENGINE})
still_inert = [k for k in _ENGINE if k in ct.sweep_inert_keys(engine_probe)]
check("TAC-2 a planted engine key is carved out, not reported inert",
      not still_inert, f"reported inert: {still_inert}")

engine_probe["OLLAMA_TUNED_BY_NOBODY"] = "1"
check("TAC-2 an UNLISTED OLLAMA_* key is still caught -- the carve-out is a "
      "list, not a prefix",
      "OLLAMA_TUNED_BY_NOBODY" in ct.sweep_inert_keys(engine_probe),
      "the carve-out widened to a prefix and stopped catching real inert keys")
check("TAC-2 every inert key appears in the rendered report",
      all(k in report for k in inert), str([k for k in inert if k not in report]))
ext = [k for k in env if ct.is_externally_consumed(k) and k not in
       [ev.key for ev in ct.effective_configuration() if not ev.inert]]
check("TAC-2 external consumers are NOT miscounted as inert",
      "PYTHONUNBUFFERED" not in inert and "STREAMLIT_PORT" not in inert)

# --- TAC-4: no secret VALUE in the output ----------------------------------
secrets = [v for k, v in env.items()
           if ct.is_sensitive(k) and len(v) >= 8 and not v.startswith("+")
           and "/" not in v]
leaked = [s for s in secrets if s in report]
check("TAC-4 no secret value appears in the report",
      not leaked, f"{len(leaked)} leaked of {len(secrets)} checked")
sens_keys = [k for k in env if ct.is_sensitive(k)]
check("TAC-4 sensitive keys are still NAMED (presence, not value)",
      any(k in report for k in sens_keys), str(sens_keys[:3]))
check("TAC-4 a sensitive value renders as a marker",
      "<present>" in report or "<unset>" in report)

# --- TAC-5: malformed values are named, never silently defaulted -----------
bad = ct.validate_types({"FASTAPI_WORKERS": "four", "RAG_TOP_K": "5",
                         "RAG_MCP_TIMEOUT": "soon"})
check("TAC-5 malformed int is named", any("FASTAPI_WORKERS" in b for b in bad), str(bad))
check("TAC-5 malformed float is named", any("RAG_MCP_TIMEOUT" in b for b in bad), str(bad))
check("TAC-5 a valid value produces no complaint",
      not any("RAG_TOP_K" in b for b in bad))
check("TAC-5 the live .env has no malformed values",
      ct.validate_types(env) == [], str(ct.validate_types(env)))

# --- AC-2 / TAC-2: what counts as a READ -----------------------------------
# The sweep must recognise every lookup shape, or a key the runtime genuinely
# uses is reported INERT — a false positive in the direction that matters,
# because it tells an operator a live setting is dead. This has now gone wrong
# twice: the pattern once read `env\(`, matching `_env(` only by accident of
# being a substring and missing every typed variant; the enumeration that
# replaced it then missed `_env_int_or` the moment it was introduced, and the
# TAC-2 inert check caught OLLAMA_NUM_PREDICT looking unread. The pattern now
# accepts any suffix, so neither the list below nor the pattern can go stale
# the same way again.
_PROBE = "US011_PROBE_KEY"
for shape in (f'os.environ.get("{_PROBE}", "")', f'os.environ["{_PROBE}"]',
              f'os.getenv("{_PROBE}")', f'_env("{_PROBE}", "")',
              f'_env_int("{_PROBE}", 1)', f'_env_float("{_PROBE}", 1.0)',
              f'_env_bool("{_PROBE}", True)', f'_env_int_or("{_PROBE}", 1)',
              f'_env_whatever_future_suffix("{_PROBE}", 1)'):
    check(f"AC-2 a read is recognised: {shape}",
          ct.is_read_anywhere(_PROBE, shape))
for shape in (f'FASTAPI_WORKERS = "4"   # not the probe',
              f'x = settings.{_PROBE}',
              f'environ["{_PROBE}"]'):
    check(f"AC-2 a mere mention is NOT a read: {shape}",
          not ct.is_read_anywhere(_PROBE, shape))

# --- TAC-1: direct env reads are REPORTED, not claimed fixed ---------------
# TAC-1's target is zero *unexplained* reads, not zero reads. A read is
# "explained" when its key is on the dynamic allowlist: the value must be
# re-read at call time because tests and the BRD-15 rollback path change it
# in-process, which a value resolved once at import cannot observe.
sites = ct.scan_direct_env_reads()
unexplained = ct.sweep_unexplained_env_reads()
check("TAC-1 every direct read is recorded as file:line:key",
      all(len(s) == 3 and isinstance(s[1], int) and isinstance(s[2], str)
          for s in sites), f"{len(sites)} sites")
check("TAC-1 multi-line reads are seen (the key sits on the next line)",
      any(k == "CHROMA_DB_PATH" for _, _, k in sites) or
      all(k != "<missing>" for _, _, k in sites),
      "pattern matched no key for a multi-line read")
check("TAC-1 setdefault WRITES are not counted as reads",
      all(k != "HF_HUB_ENABLE_HF_XET" for _, _, k in sites),
      "a write was counted as a read")
# The subscript form is the trap: `os.environ["K"] = x` and `os.environ["K"]`
# differ by two characters, and counting the write invents a migration site
# with no lookup in it. app/voice_handler.py's ONNX_PROVIDER write was reported
# as an unexplained read this way. Both spellings are checked, plus the
# comparison operator that must still read as a read.
check("TAC-1 subscript WRITES are not counted as reads",
      all(k != "ONNX_PROVIDER" for _, _, k in sites),
      "os.environ['K'] = x was counted as a read")
check("TAC-1 a subscript READ still counts, and == is not an assignment",
      ct.reads_in_source('a = os.environ["US011_PROBE"]')
      and ct.reads_in_source('b = os.environ["US011_PROBE"] == "1"')
      and not ct.reads_in_source('os.environ["US011_PROBE"] = "1"')
      and not ct.reads_in_source('os.environ["US011_PROBE"] += "1"'),
      "the assignment guard swallows a read, or lets a write through")
check("TAC-1 the dynamic allowlist is non-empty and every entry gives a reason",
      ct.DYNAMIC_KEYS and all(len(v) > 20 for v in ct.DYNAMIC_KEYS.values()),
      f"{len(ct.DYNAMIC_KEYS)} allowlisted")
check("TAC-1 no allowlisted key is reported unexplained",
      not {k for _, _, k in unexplained} & set(ct.DYNAMIC_KEYS),
      str(sorted({k for _, _, k in unexplained} & set(ct.DYNAMIC_KEYS))))
check("TAC-1 the unexplained sweep is a subset of all reads",
      set(unexplained) <= set(sites))
# Positive control: the gate must be able to fail. Without this, a zero below
# could mean "clean" or "detector broken".
_probe_src = 'x = os.environ.get("US011_UNALLOWLISTED_PROBE", "")'
check("TAC-1 the gate can still see an un-allowlisted read",
      any(k == "US011_UNALLOWLISTED_PROBE" for _, k in ct.reads_in_source(_probe_src))
      and "US011_UNALLOWLISTED_PROBE" not in ct.DYNAMIC_KEYS,
      "the probe was not detected, so a zero below would prove nothing")
check("TAC-1 the report names the remaining work rather than implying compliance",
      "must reach zero" in report)
check("TAC-1 report counts the allowlisted reads separately",
      "kept by design" in report)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
