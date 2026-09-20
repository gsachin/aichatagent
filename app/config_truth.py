"""
Configuration truth — MOD-07, TRD-24 / TRD-25. Implements US-011.

Answers two questions the plan could not previously answer without reading the
loader by hand:

  1. For every setting, what value is ACTUALLY in force, and where did it come
     from? (`.env`, a documented code default, or a detection artifact that was
     consulted and overridden.)
  2. Which written keys are referenced from NO code path? (REC-02's
     `FASTAPI_WORKERS` was the first of five found.)

Why this exists: `BRD-16` requires the effective value of any setting to be
discoverable. Three documents describe configuration and nothing reconciled
them, so a tuning decision could be written, silently discarded, and then
blamed for a latency number it never influenced. The plan's standing rule —
"set is not live" — has six recorded instances; this module is how that stops
being discovered by accident.

Read-only. Imports stdlib only. Never renders a secret value.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
APP_DIR = PROJECT_ROOT / "app"

#: Substrings that mark a key as sensitive. Its VALUE is never rendered —
#: only whether it is present (TAC-4).
_SENSITIVE_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASS", "KEY",
                      "CREDENTIAL", "SID", "AUTH")

#: Keys that must parse as integers. A value that cannot parse is a boot-time
#: failure naming the key (TAC-5) — never a silent fallback, because a silent
#: fallback is indistinguishable from a deliberate choice.
_INT_KEYS = ("FASTAPI_WORKERS", "OLLAMA_NUM_CTX", "WHISPER_NUM_THREADS",
             "RAG_TOP_K", "RAG_FETCH_K", "MIN_UTTERANCE_FRAMES", "STT_MIN_CHARS")

_FLOAT_KEYS = ("RAG_SIMILARITY_THRESHOLD", "RAG_MCP_TIMEOUT", "OLLAMA_TEMPERATURE",
               "KOKORO_SPEED", "RAG_MCP_COOLDOWN")

#: A "read" is an env lookup, not a mere mention. `app/hardware_profile.py`
#: writes FASTAPI_WORKERS as a string in a tier table — a naive substring scan
#: calls that "read", which is precisely the error this module exists to catch.
#:
#: The last pattern covers the helper call shapes (`_env`, `_env_int`,
#: `_env_float`, `_env_bool`) that several modules wrap `os.environ` in. It
#: previously read `env\(`, which matched `_env(` only by accident of being a
#: substring and MISSED every typed variant — so a key read as
#: `_env_int("FASTAPI_PORT", 8000)` was counted as unread and would be reported
#: INERT. That is a false positive in the direction that matters: it tells an
#: operator a setting is dead when the runtime is using it. `admission.py`'s
#: `max_concurrent()` documents hitting this class from the other side (it uses
#: a literal read because the sweep could not verify the helper form).
_READ_PATTERNS = (
    r'os\.environ\.get\(\s*["\']{k}["\']',
    r'os\.environ\[\s*["\']{k}["\']\s*\]',
    r'os\.getenv\(\s*["\']{k}["\']',
    r'(?<![\w.])_?env(?:_int|_float|_bool)?\(\s*["\']{k}["\']',
)


@dataclass(frozen=True)
class EffectiveValue:
    """One setting's value and where it came from."""
    key: str
    value: str | None          # None when sensitive
    present: bool              # sensitive keys report presence, never value
    source: str                # "authoritative (.env)" | "code default" | "detection artifact"
    sensitive: bool
    inert: bool = False        # written but referenced from no code path

    def render(self) -> str:
        shown = "<present>" if self.sensitive else ("<unset>" if self.value is None else self.value)
        flags = "  [INERT - written but never read]" if self.inert else ""
        return f"  {self.key:<28} {shown:<24} <- {self.source}{flags}"


def is_sensitive(key: str) -> bool:
    up = key.upper()
    return any(m in up for m in _SENSITIVE_MARKERS)


def parse_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """KEY=VALUE pairs from the authoritative file. Comments and blanks skipped."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k = k.strip()
            if k:
                out[k] = v.strip()
    except FileNotFoundError:
        pass
    return out


def _app_corpus(exclude: tuple[str, ...] = ("config_truth.py",)) -> str:
    """All app source, minus this module (which names every key by design)."""
    parts = []
    for p in sorted(APP_DIR.rglob("*.py")):
        if p.name in exclude:
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def is_read_anywhere(key: str, corpus: str | None = None) -> bool:
    """True when some code path actually LOOKS UP this key (TAC-2)."""
    corpus = _app_corpus() if corpus is None else corpus
    for pat in _READ_PATTERNS:
        if re.search(pat.format(k=re.escape(key)), corpus):
            return True
    return False


#: Keys consumed by a process OTHER than this application's Python code.
#: A naive "not in app/" sweep calls these inert, which is a false positive:
#: the interpreter reads PYTHON*, Streamlit itself reads STREAMLIT_*, and the
#: PowerShell launcher reads the host/port pair. Reported separately so a real
#: inert key is not buried among them.
_EXTERNALLY_CONSUMED_PREFIXES = ("PYTHON", "STREAMLIT")
_EXTERNALLY_CONSUMED = ("FASTAPI_HOST", "FASTAPI_PORT")


def is_externally_consumed(key: str) -> bool:
    return (key.startswith(_EXTERNALLY_CONSUMED_PREFIXES)
            or key in _EXTERNALLY_CONSUMED)


def sweep_inert_keys(env: dict[str, str] | None = None,
                     include_external: bool = False) -> tuple[str, ...]:
    """Keys written in configuration but read from no code path (TAC-2).

    By default excludes keys consumed by another process (see
    `_EXTERNALLY_CONSUMED`), because "no Python code reads it" and "nothing
    reads it" are different claims and only the second one is a defect.
    """
    env = parse_env_file() if env is None else env
    corpus = _app_corpus()
    return tuple(sorted(
        k for k in env
        if not is_read_anywhere(k, corpus)
        and (include_external or not is_externally_consumed(k))
    ))


def sweep_direct_env_reads(exclude_files: tuple[str, ...] = ("config.py", "config_truth.py")) -> tuple[str, ...]:
    """Files reading os.environ directly instead of through app/config.py.

    TAC-1 aspires to zero. This REPORTS the current state rather than
    asserting compliance — the refactor is not part of US-011 and claiming it
    without doing it would be exactly the 'set is not live' failure the module
    exists to catch.

    File-level only, and therefore not actionable on its own: it cannot tell a
    file that reads an allowlisted dynamic key from one that reads a key nobody
    documented. Use `sweep_unexplained_env_reads()` for the gate.
    """
    hits = []
    for p in sorted(APP_DIR.rglob("*.py")):
        if p.name in exclude_files:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if re.search(r"os\.environ(?:\.get\(|\[)|os\.getenv\(", src):
            hits.append(str(p.relative_to(PROJECT_ROOT)))
    return tuple(hits)


# ── TAC-1: which direct reads are allowed to remain ─────────────────────────

#: Keys whose read is deliberately dynamic — read from the environment at CALL
#: time, not resolved once at import. Keeping these out of `Settings` is the
#: design, not a gap: the test suite and the `BRD-15` rollback path change them
#: at runtime and require the very next call to observe the change, which an
#: import-time value cannot do. Each entry says why, because an allowlist
#: without reasons is just a list of places nobody looked.
#:
#: Anything NOT in this map must resolve through `app/config.py`. That is the
#: TAC-1 gate: `sweep_unexplained_env_reads()` returns empty.
DYNAMIC_KEYS: dict[str, str] = {
    "USE_MCP_RAG": (
        "The MCP-first seam's mode. test_rag_dispatcher flips it per test "
        "(14 sites) to exercise off/auto/on against one interpreter."),
    "ADMISSION_ENABLED": (
        "US-016's revert switch. BRD-15 requires reverting by configuration, "
        "and admission.enabled() is the function the rollback path calls."),
    "MAX_CONCURRENT_CALLS": (
        "Sizing for the admission lease. Read at lease time so a test can "
        "lower the ceiling without restarting the process."),
    "BG_PRIORITY_ENABLED": (
        "Turns the background/voice admission gate on and off; the gate is "
        "exercised both ways in-process."),
    "RAG_BREAKER_MODE": (
        "Breaker policy. test_brd15_rollback sets it at runtime and asserts "
        "the next read reflects it — the rollback demonstration itself."),
    "RAG_COLLECTION_NAME": (
        "Blue/green collection switch (C5). Flipped at runtime to prove the "
        "seam moves without a restart."),
    "PERF_TRACE": (
        "Tracing toggle; enabled around a block of code under test."),
    "PERF_TRACE_FILE": (
        "Trace destination, set per test to keep runs out of the real file."),
    "MACHINE_PROFILE_CHECK": (
        "Boot drift report switch (DG-05). Read at boot, tested both ways."),
}

_READ_SITE_PATTERNS = (
    re.compile(r'os\.environ\.get\(\s*["\'](?P<k>[A-Za-z_][A-Za-z0-9_]*)["\']'),
    re.compile(r'os\.environ\[\s*["\'](?P<k>[A-Za-z_][A-Za-z0-9_]*)["\']\s*\]'),
    re.compile(r'os\.getenv\(\s*["\'](?P<k>[A-Za-z_][A-Za-z0-9_]*)["\']'),
    re.compile(r'(?<![\w.])_?env(?:_int|_float|_bool)?\(\s*["\'](?P<k>[A-Za-z_][A-Za-z0-9_]*)["\']'),
)


def scan_direct_env_reads(
    exclude_files: tuple[str, ...] = ("config.py", "config_truth.py"),
) -> tuple[tuple[str, int, str], ...]:
    """Every direct env read as (file, line, key).

    Pattern-matched over whole files rather than line by line, because a read's
    key can sit on the line after the call — `os.environ.get(\\n "KEY",` is how
    several of these are written. `os.environ.setdefault` writes are not reads
    and are not matched.
    """
    sites: list[tuple[str, int, str]] = []
    for p in sorted(APP_DIR.rglob("*.py")):
        if p.name in exclude_files:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in _READ_SITE_PATTERNS:
            for m in pat.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                sites.append((str(p.relative_to(PROJECT_ROOT)), line, m.group("k")))
    return tuple(sites)


def sweep_unexplained_env_reads() -> tuple[tuple[str, int, str], ...]:
    """Direct reads of keys that are NOT on the dynamic allowlist.

    This is the TAC-1 gate. Empty means every remaining direct read is one the
    project has decided to keep and written down; non-empty means a read was
    added that nothing accounts for.
    """
    return tuple(s for s in scan_direct_env_reads() if s[2] not in DYNAMIC_KEYS)


def validate_types(env: dict[str, str] | None = None) -> list[str]:
    """Malformed values, named. Boot-time failures, not silent fallbacks (TAC-5)."""
    env = parse_env_file() if env is None else env
    problems: list[str] = []
    for k in _INT_KEYS:
        if k in env and env[k] != "":
            try:
                int(env[k])
            except ValueError:
                problems.append(f"{k}={env[k]!r} is not an integer")
    for k in _FLOAT_KEYS:
        if k in env and env[k] != "":
            try:
                float(env[k])
            except ValueError:
                problems.append(f"{k}={env[k]!r} is not a number")
    return problems


def effective_configuration() -> tuple[EffectiveValue, ...]:
    """Value + provenance per key. Sensitive values are never rendered (TAC-4)."""
    env = parse_env_file()
    corpus = _app_corpus()
    out: list[EffectiveValue] = []
    for key in sorted(env):
        sens = is_sensitive(key)
        out.append(EffectiveValue(
            key=key,
            value=None if sens else env[key],
            present=env[key] != "",
            source="authoritative (.env)",
            sensitive=sens,
            inert=not is_read_anywhere(key, corpus),
        ))
    return tuple(out)


def report() -> str:
    """Human-readable effective configuration. Safe to log: no secret values."""
    env = parse_env_file()
    lines = [
        "EFFECTIVE CONFIGURATION  (authority: .env; REC-05)",
        "=" * 78,
    ]
    for ev in effective_configuration():
        lines.append(ev.render())

    inert = sweep_inert_keys(env)
    lines += ["", f"INERT KEYS (written; no Python code path reads them): {len(inert)}"]
    for k in inert:
        lines.append(f"  {k}")
    if not inert:
        lines.append("  none")

    ext = sorted(k for k in env if is_externally_consumed(k)
                 and not is_read_anywhere(k, _app_corpus()))
    lines += ["", f"CONSUMED BY ANOTHER PROCESS, not by app code "
                  f"(not defects): {len(ext)}"]
    for k in ext:
        lines.append(f"  {k}")

    bad = validate_types(env)
    lines += ["", f"MALFORMED VALUES (would fail at start, TAC-5): {len(bad)}"]
    for b in bad:
        lines.append(f"  {b}")
    if not bad:
        lines.append("  none")

    sites = scan_direct_env_reads()
    unexplained = sweep_unexplained_env_reads()
    dynamic = tuple(s for s in sites if s[2] in DYNAMIC_KEYS)
    lines += [
        "",
        f"DIRECT os.environ READS outside app/config.py: {len(sites)} sites "
        f"in {len(sweep_direct_env_reads())} files",
        f"  kept by design (dynamic allowlist): {len(dynamic)}",
        f"  UNEXPLAINED - TAC-1 gate, must reach zero: {len(unexplained)}",
    ]
    for f, ln, k in unexplained:
        lines.append(f"    {f}:{ln}  {k}")
    if not unexplained:
        lines.append("    none - every remaining direct read is a documented "
                     "dynamic key")

    lines += [
        "",
        "KNOWLEDGE-BASE PROVENANCE",
        "=" * 78,
        "  .env is the runtime authority. .machine_profile.json is a detection",
        "  artifact: it is never read at runtime (REC-05), so a value there that",
        "  differs from .env is NOT in effect. check_drift() compares only",
        "  'detected' hardware fields, never the 'applied' block.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
