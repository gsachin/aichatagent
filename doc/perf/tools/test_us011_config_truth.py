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
check("TAC-2 inert keys are found", len(inert) > 0, str(inert))
check("TAC-2 FASTAPI_WORKERS is reported inert (REC-02)",
      "FASTAPI_WORKERS" in inert)
check("TAC-2 KOKORO_SPEED is reported inert",
      "KOKORO_SPEED" in inert, "hardcoded speed=1.0 at voice_handler.py:131,707")
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

# --- TAC-1: direct env reads are REPORTED, not claimed fixed ---------------
direct = ct.sweep_direct_env_reads()
check("TAC-1 direct os.environ reads are reported (target is zero, not met)",
      len(direct) > 0, f"{len(direct)} files")
check("TAC-1 report states the target rather than implying compliance",
      "TAC-1 target is zero" in report)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
