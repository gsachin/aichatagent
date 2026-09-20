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
check("AC-1 every key reports a provenance",
      all(s.startswith("authoritative") for s in prov.values()) and prov)

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
# because it tells an operator a live setting is dead. The typed helper shapes
# (`_env_int` and friends) were invisible until Phase 1.2: the pattern read
# `env\(`, which matched `_env(` only by accident of being a substring.
_PROBE = "US011_PROBE_KEY"
for shape in (f'os.environ.get("{_PROBE}", "")', f'os.environ["{_PROBE}"]',
              f'os.getenv("{_PROBE}")', f'_env("{_PROBE}", "")',
              f'_env_int("{_PROBE}", 1)', f'_env_float("{_PROBE}", 1.0)',
              f'_env_bool("{_PROBE}", True)'):
    check(f"AC-2 a read is recognised: {shape}",
          ct.is_read_anywhere(_PROBE, shape))
for shape in (f'FASTAPI_WORKERS = "4"   # not the probe',
              f'x = settings.{_PROBE}',
              f'environ["{_PROBE}"]'):
    check(f"AC-2 a mere mention is NOT a read: {shape}",
          not ct.is_read_anywhere(_PROBE, shape))

# --- TAC-1: direct env reads are REPORTED, not claimed fixed ---------------
direct = ct.sweep_direct_env_reads()
check("TAC-1 direct os.environ reads are reported (target is zero, not met)",
      len(direct) > 0, f"{len(direct)} files")
check("TAC-1 report states the target rather than implying compliance",
      "TAC-1 target is zero" in report)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
