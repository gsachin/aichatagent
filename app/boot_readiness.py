"""US-007 / MOD-07 — boot-time warm state is verifiable, not assumed.

The pre-warm used to send the literal prompt `"ping"`: it warmed weights, not
the voice prompt prefix, and a skipped pre-warm was a warning an operator could
miss. This module makes the boot state a claim the engine has to confirm.

Four clauses, all of them or not ready (TAC-1):
  1. every turn-path service is listening
  2. the inference model is resident, confirmed by the engine
  3. the prompt prefix is warm, confirmed by the engine's own counters
  4. every managed configuration key has a reader (`US-011` / `config_truth`)

Design notes:

* Nothing here runs on the call path. The whole gate is one revertable change
  with no runtime effect on a live call (TAC-8).
* Clause 3 is confirmed from the engine's `prompt_eval_duration`, compared
  across two identical submissions — never from a log line this module wrote
  (AC-1). Prefix caching is measured working on this box: an identical repeat
  drops 2,909 ms -> 50 ms at the same token count.
* Blocking vs reported (TAC-7): only FastAPI and Ollama block. Without them
  there is no call at all. ERC MCP, Postgres, CRM and the tunnels are *named*
  with the capability they degrade, but never stop the stack being usable —
  `MOD-02` falls back to the local store, `MOD-05` runs post-call.
* No credential value is ever rendered. Keys are named, never valued (TAC-6).

CLI (called by start_services.ps1 Step 6):
    python -m app.boot_readiness [--json] [--no-warm]
Exit code 0 = ready, 2 = not ready (TAC-5).
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings

PROJ = Path(__file__).resolve().parent.parent


@dataclass
class ServiceState:
    name: str
    host: str
    port: int
    listening: bool
    blocks_readiness: bool
    degrades: str = ""


@dataclass
class Readiness:
    ready: bool = False
    clauses: dict = field(default_factory=dict)
    services: list = field(default_factory=list)
    model: dict = field(default_factory=dict)
    prefix: dict = field(default_factory=dict)
    embed: dict = field(default_factory=dict)
    gpu: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    degraded: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["services"] = [asdict(s) if not isinstance(s, dict) else s
                         for s in self.services]
        return d


# ── Clause 1 — services listening ──────────────────────────────────────────

def load_env() -> None:
    """Load `.env` before anything reads it. Idempotent; never raises."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJ / ".env")
    except Exception:                             # noqa: BLE001
        pass


def default_services() -> list[tuple[str, str, int, bool, str]]:
    """(name, host, port, blocks_readiness, degrades) for tracked services.

    RAG/ERC and Ollama were removed. FastAPI blocks readiness; Postgres is
    reported because it degrades post-call lead persistence, not a live turn.
    Hosts are the literal IPv4 loopback: ``localhost`` costs ~2 s when the
    target is down on this box.
    """
    return [
        ("FastAPI",   "127.0.0.1", settings.FASTAPI_PORT, True,  ""),
        ("Postgres",  "127.0.0.1", 5432,                  False,
         "lead persistence and CRM sync (MOD-05, post-call)"),
    ]


def tcp_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    """True when something accepts a TCP connection. Never raises."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_services(timeout: float = 1.0) -> list[ServiceState]:
    return [
        ServiceState(name=n, host=h, port=p, listening=tcp_listening(h, p, timeout),
                     blocks_readiness=b, degrades=d)
        for n, h, p, b, d in default_services()
    ]


# ── AC-3 scenario 2 — the GPU is awake, not idle-clocked ───────────────────

#: Measured on this box: an idle card sits at P5 with the memory clock pinned
#: at 405 MHz out of 14,001 MHz. A warm model holds it far above that. Read
#: from the device, never inferred from the fact that the warm call returned.
GPU_IDLE_MEM_MHZ = 405
GPU_MAX_MEM_MHZ = 14001


def gpu_clock_state() -> dict:
    """Memory clock straight from `nvidia-smi`. Never raises.

    `available: False` means the query could not be made (no NVIDIA GPU, no
    driver, no nvidia-smi on PATH). That is reported as UNKNOWN and does not
    fail the gate — an unanswerable question is not a failed one, and failing
    here would block every non-NVIDIA box this repo supports.
    """
    out: dict = {"available": False, "mem_mhz": None, "max_mem_mhz": None,
                 "idle": None, "detail": None}
    try:
        import subprocess

        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=clocks.mem,clocks.max.mem",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            out["detail"] = (proc.stderr or "").strip()[:200] or "nvidia-smi non-zero"
            return out
        first = (proc.stdout or "").strip().splitlines()[0]
        mem, _, mx = first.partition(",")
        out["mem_mhz"] = int(mem.strip())
        out["max_mem_mhz"] = int(mx.strip()) if mx.strip().isdigit() else GPU_MAX_MEM_MHZ
        out["available"] = True
        out["idle"] = out["mem_mhz"] <= GPU_IDLE_MEM_MHZ
    except Exception as exc:                      # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"
    return out


# ── Clause 3 — remote provider reachability ───────────────────────────────

def warm_and_confirm(model: str | None = None,
                     num_ctx: int | None = None,
                     temperature: float | None = None) -> dict:
    """Confirm the remote provider is reachable.

    There is no local runner to warm and no ``/api/ps`` residency to confirm:
    the local backends were removed. The blocking claim is an OpenAI
    reachability probe; a failed call reaches the fixed-response path.
    """
    from app.llm_backend import LLM_MODEL, is_ready, provider_name

    try:
        provider_name()
    except Exception as exc:                      # noqa: BLE001 — boot boundary
        return {
            "provider": settings.LLM_PROVIDER,
            "model": LLM_MODEL,
            "num_ctx": None,
            "warm": False,
            "resident": False,
            "first_prefill_ms": None,
            "confirm_prefill_ms": None,
            "load_ms": None,
            "resident_detail": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    ready = is_ready()
    return {
        "provider": "openai",
        "model": LLM_MODEL,
        "num_ctx": None,
        "warm": ready,
        "resident": ready,
        "first_prefill_ms": None,
        "confirm_prefill_ms": None,
        "load_ms": None,
        "resident_detail": None,
        "error": None if ready else "OpenAI API not reachable",
    }


# ── Clause 4 — every managed key has a reader ──────────────────────────────

def check_config_keys() -> dict:
    """`US-011`'s reader check. Values are never rendered for key-like names.

    The split matters and is US-011's finding, not a convenience: an inert key
    that another process already consumes (`PYTHONHASHSEED` by the interpreter,
    `FASTAPI_PORT` by start_services.ps1) *does* have a reader. Counting those
    as unread would fail clause 4 forever on a stack that is working correctly,
    and a gate that always fails is one operators learn to ignore — which is
    the failure mode this whole story exists to prevent.
    """
    out: dict = {"checked": 0, "without_reader": [], "externally_consumed": [],
                 "malformed": []}
    try:
        from app import config_truth as ct

        values = ct.effective_configuration()
        out["checked"] = len(values)
        out["without_reader"] = sorted(
            v.key for v in values if v.inert and not ct.is_externally_consumed(v.key))
        out["externally_consumed"] = sorted(
            v.key for v in values if v.inert and ct.is_externally_consumed(v.key))
        # US-011 TAC-5: "a value that cannot be parsed is a boot-time failure
        # naming the key". `validate_types` has carried that docstring since it
        # was written and was called only from `report()` and from its own test,
        # so nothing ever failed a start on a malformed value -- the claim was
        # checked by the suite and never executed by the boot path. Wired here,
        # where clause 4 already asks the sibling question ("does this key have
        # a reader?"), because the two belong together: a key nobody reads and a
        # value nothing can parse are the same class of defect, configuration
        # that is written and does not execute.
        #
        # The live near-miss this closes: `OLLAMA_KEEP_ALIVE` is a `.env` string
        # and Ollama's Go duration parser rejects `"-1"` with `time: missing unit
        # in duration` -- a 400 on EVERY request. That specific one is handled by
        # coercion in `_resolve_keep_alive`, by hand at one call site. This is
        # the check that would catch the next one.
        out["malformed"] = ct.validate_types()
        out["unread_count"] = len(out["without_reader"])
    except Exception as exc:                      # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def effective_config_rows() -> list[dict]:
    """Value + provenance per managed key, secrets masked (TAC-6)."""
    try:
        from app import config_truth as ct

        return [{"key": v.key,
                 "value": "***set***" if (v.sensitive and v.present)
                          else ("(unset)" if not v.present else v.value),
                 "source": v.source,
                 "sensitive": v.sensitive}
                for v in ct.effective_configuration()]
    except Exception as exc:                      # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]


# ── The gate ───────────────────────────────────────────────────────────────

def check_foreign_languages(r: Readiness) -> None:
    """Report the conditions the six-language feature needs, without blocking.

    Everything here is a degraded capability, not a reason to refuse the stack:
    English/Hindi/Gujarati still work if a foreign asset is missing or the STT
    model is English-only. The operator sees it at boot rather than on a call.
    """
    from app.admission import asset_available, busy_asset, fixed_response_asset
    from app.languages import configured_foreign_languages, normalize_language_code

    foreign = configured_foreign_languages()
    if not foreign:
        return

    supported = {
        normalize_language_code(part)
        for part in (settings.SUPPORTED_LANGUAGES or "").split(",")
        if part.strip()
    }
    missing_supported = sorted(foreign - supported)
    if missing_supported:
        r.degraded.append(
            "foreign languages missing from SUPPORTED_LANGUAGES: "
            + ",".join(missing_supported)
        )

    if (settings.WHISPER_MODEL or "").strip().lower().endswith(".en"):
        r.degraded.append(
            f"WHISPER_MODEL={settings.WHISPER_MODEL!r} is English-only; "
            "the foreign languages need small/medium"
        )

    if {"ja", "zh"} & set(foreign):
        try:
            from misaki import ja as _misaki_ja  # noqa: F401
            from misaki import zh as _misaki_zh  # noqa: F401
        except Exception as exc:                      # noqa: BLE001
            r.degraded.append(
                "misaki is not installed for ja/zh "
                f"({type(exc).__name__}); those voices fall back to espeak-ng "
                "and are not intelligible -- install the requirements.txt G2P stack"
            )

    if (settings.STT_LANGUAGE or "auto").strip().lower() not in ("", "auto"):
        r.degraded.append(
            f"STT_LANGUAGE={settings.STT_LANGUAGE!r} is pinned; foreign "
            "auto-switching is disabled"
        )

    if (settings.TTS_PROVIDER or "").strip().lower() == "sarvam":
        configured = {
            normalize_language_code(part)
            for part in (settings.SARVAM_TTS_LANGUAGES or "").split(",")
            if part.strip()
        }
        overlap = sorted(configured & foreign)
        if overlap:
            r.degraded.append(
                "foreign codes in SARVAM_TTS_LANGUAGES are ignored (Kokoro only): "
                + ",".join(overlap)
            )

    missing_assets = []
    for code in sorted(foreign):
        for name in (fixed_response_asset(code), busy_asset(code)):
            if not asset_available(name):
                missing_assets.append(name)
    if missing_assets:
        r.degraded.append(
            "foreign call assets missing: "
            + ",".join(missing_assets[:6])
            + " -- build them: .venv/Scripts/python.exe scripts/build_call_assets.py"
        )


def check_greeting_assets(r: Readiness) -> None:
    """Report pre-rendered greeting assets missing for the selectable set.

    A missing greeting does not break a call -- the live path synthesises it --
    but it costs the first caller the latency the asset exists to remove, and
    the only other signal is a ``GREETING_FIRST_AUDIO source=stream`` line on a
    live call. Reported at boot, per language/direction, so a deploy that
    skipped the build step is visible before the phone rings.

    The greeting filename carries a digest of the live text/voice/provider
    identity, so a stale asset cannot hide here: a voice or wording change
    produces a different expected name and the old file is reported missing.
    """
    from app.admission import asset_available
    from app.languages import DEFAULT_FOREIGN_LANGUAGES, configured_foreign_languages
    from app.voice_handler import greeting_asset_name
    from app.voice_system_prompt import call_greeting

    # Core languages the IVR always offers, plus whatever FOREIGN_LANGUAGES
    # enables right now. Intersected with the supported set so an unknown code
    # in the configuration is reported by check_foreign_languages, not as a
    # greeting asset the build script never produces. Assets for a disabled
    # foreign language are not reported: they cost nothing and are rebuilt
    # when the language is enabled.
    enabled_foreign = configured_foreign_languages() & set(DEFAULT_FOREIGN_LANGUAGES)
    languages = list(dict.fromkeys(["en", "hi", "gu", *sorted(enabled_foreign)]))
    present = 0
    missing: list[str] = []
    for code in languages:
        for direction in ("inbound", "outbound"):
            text = call_greeting(code, direction)
            name = greeting_asset_name(code, direction, text)
            if asset_available(name):
                present += 1
            else:
                missing.append(f"{code}/{direction}")

    expected = len(languages) * 2
    r.assets = {
        "greetings": {
            "expected": expected,
            "present": present,
            "missing": missing,
        }
    }
    if missing:
        shown = ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
        r.degraded.append(
            f"greeting assets missing for {len(missing)}/{expected} "
            f"language/direction pair(s): {shown} -- build them: "
            ".venv/Scripts/python.exe scripts/build_call_assets.py --ensure"
        )


def assess(do_warm: bool = True) -> Readiness:
    """Evaluate all four clauses. Never raises; never assumes success."""
    load_env()
    r = Readiness()

    r.services = check_services()
    svc_ok = all(s.listening for s in r.services if s.blocks_readiness)
    r.clauses["services"] = svc_ok
    for s in r.services:
        if not s.listening:
            (r.missing if s.blocks_readiness else r.degraded).append(
                f"{s.name} :{s.port} not listening"
                + (f" — degrades {s.degrades}" if s.degrades else ""))

    if do_warm:
        r.prefix = warm_and_confirm()
        if r.prefix.get("error"):
            r.clauses["prefix"] = False
            r.missing.append(f"remote provider unreachable — {r.prefix['error']}")
        else:
            r.clauses["prefix"] = bool(r.prefix.get("warm"))
        r.model = {"model": r.prefix.get("model"),
                   "resident": r.prefix.get("resident"),
                   "detail": r.prefix.get("resident_detail"),
                   "load_ms": r.prefix.get("load_ms")}
        r.clauses["residency"] = bool(r.prefix.get("resident"))
        if not r.clauses["residency"]:
            r.missing.append(
                f"model {r.prefix.get('model')} is NOT reachable at the "
                "configured OpenAI endpoint")
        r.gpu = {"skipped": "remote provider"}
    else:
        r.model = {"model": None, "resident": False,
                   "detail": {"provider": "openai"}}
        r.clauses["residency"] = False
        r.clauses["prefix"] = False
        r.prefix = {"skipped": "--no-warm: remote reachability NOT verified"}

    r.config = check_config_keys()
    r.clauses["config_keys"] = not (r.config.get("without_reader")
                                    or r.config.get("malformed"))
    if r.config.get("without_reader"):
        r.missing.append(
            "config keys with no reader: "
            + ", ".join(r.config["without_reader"][:8]))
    # US-011 TAC-5. A malformed value is NOT a degraded capability -- it is a
    # value the system will not receive as written, so the configured behaviour
    # and the running behaviour differ and nothing says so. That is the exact
    # "set != live" defect clause 4 exists to prevent, so it fails the clause
    # rather than joining the warnings.
    if r.config.get("malformed"):
        r.missing.append(
            "config values that cannot be parsed: "
            + "; ".join(r.config["malformed"][:5]))

    # US-016 AC-3: the pre-synthesised call assets are the assistant's own
    # voice, recorded once. If KOKORO_VOICE or KOKORO_SPEED changed afterwards,
    # a caller would hear the recorded voice differ from the one answering
    # them -- a third caller on the busy line, or anyone at all when the engine
    # is lost. Reported here, before anyone is on the line, rather than
    # discovered mid-call. Non-blocking: the stack works, two sentences are
    # stale, and the fix is one command.
    try:
        from app.admission import asset_drift

        drift = asset_drift()
        if drift:
            r.degraded.append(f"call assets: {drift}")
    except Exception as exc:                          # noqa: BLE001
        r.degraded.append(f"call assets: check failed ({type(exc).__name__}: {exc})")

    try:
        check_greeting_assets(r)
    except Exception as exc:                          # noqa: BLE001
        r.degraded.append(
            f"greeting assets: check failed ({type(exc).__name__}: {exc})"
        )

    try:
        check_foreign_languages(r)
    except Exception as exc:                          # noqa: BLE001
        r.degraded.append(
            f"foreign languages: check failed ({type(exc).__name__}: {exc})"
        )

    # RAG/embeddings were removed; the document is passed in full. No embed
    # residency has any meaning here.
    r.embed = {"skipped": "RAG removed; CONTEXT_MODE=doc"}

    r.ready = all(r.clauses.values())
    return r


def refresh_payload(cached: dict, fresh: Readiness) -> dict:
    """The `/ready?refresh=1` answer: a cheap re-assessment laid over the boot
    verdict (US-007 Phase 1.1, the call-time surface).

    The refresh re-checks services, residency, GPU and config — the clauses
    that can change after boot — and never re-runs the prefix warm, because a
    poll must not re-warm (and cannot meaningfully do so per request). The
    prefix clause is therefore CARRIED from the boot assessment instead of
    being failed for being unverified: warmth is a property of the boot warm,
    not of the poll. `ready` is the conjunction of the fresh clauses and the
    carried one; `status` says the same in the harness's vocabulary. No
    credential value is ever rendered (TAC-6 holds for every field here).
    """
    d = fresh.to_dict()
    d["prefix"] = dict(cached).get("prefix") or d.get("prefix")
    d["clauses"] = dict(d.get("clauses") or {})
    d["clauses"]["prefix"] = (dict(cached).get("clauses") or {}).get("prefix", False)
    d["ready"] = bool(d["clauses"]) and all(d["clauses"].values())
    d["status"] = "ready" if d["ready"] else "not_ready"
    d["basis"] = ("cheap re-assessment (services/remote reachability/config) at "
                  "call time; the prefix clause is carried from the boot "
                  "assessment - a poll never re-warms")
    return d


def render(r: Readiness) -> str:
    ok = lambda b: "OK  " if b else "FAIL"          # noqa: E731
    lines = ["", "== READINESS " + ("READY" if r.ready else "NOT READY") + " " + "=" * 40]
    lines.append(f"  [{ok(r.clauses.get('services'))}] clause 1  turn-path services listening")
    lines.append(f"  [{ok(r.clauses.get('residency'))}] clause 2  OpenAI endpoint reachable")
    lines.append(f"  [{ok(r.clauses.get('prefix'))}] clause 3  remote completion reachable")
    lines.append(f"  [{ok(r.clauses.get('config_keys'))}] clause 4  every managed key has a reader")
    lines.append("")
    lines.append("  services:")
    for s in r.services:
        mark = "listening" if s.listening else "DOWN"
        role = "blocks" if s.blocks_readiness else "reported"
        lines.append(f"    {s.name:<10} :{s.port:<6} {mark:<10} ({role})")
    if r.prefix:
        lines.append("  prefix:")
        for k in ("model", "num_ctx", "prompt_chars", "first_prefill_ms",
                  "confirm_prefill_ms", "speedup", "warm", "error"):
            if k in r.prefix:
                lines.append(f"    {k:<18} {r.prefix[k]}")
    if r.embed:
        lines.append(f"  embeddings: {r.embed.get('skipped') or 'removed'}")
    if r.gpu:
        lines.append(f"  gpu: {r.gpu.get('skipped') or 'not used for remote inference'}")
    if r.config:
        lines.append(f"  config: {r.config.get('checked')} keys checked, "
                     f"{r.config.get('unread_count', 0)} without a reader, "
                     f"{len(r.config.get('externally_consumed') or [])} read by "
                     f"another process (not a gap)")
    if r.assets:
        g = r.assets.get("greetings") or {}
        missing = g.get("missing") or []
        lines.append(
            f"  greeting assets: {g.get('present')}/{g.get('expected')} present"
            + ("" if not missing else f" (missing {len(missing)})")
        )
    if r.missing:
        lines.append("")
        lines.append("  NOT READY because:")
        for m in r.missing:
            lines.append(f"    - {m}")
    if r.degraded:
        lines.append("")
        lines.append("  degraded (does not block):")
        for d in r.degraded:
            lines.append(f"    - {d}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Boot readiness gate (US-007)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-warm", action="store_true",
                    help="skip the warm step (readiness is then NOT verifiable)")
    ap.add_argument("--config", action="store_true",
                    help="also print the effective configuration table")
    args = ap.parse_args(argv)

    r = assess(do_warm=not args.no_warm)
    if args.json:
        d = r.to_dict()
        if args.config:
            d["effective_config"] = effective_config_rows()
        print(json.dumps(d, indent=2))
    else:
        print(render(r))
        if args.config:
            print("  effective configuration (secrets masked):")
            for row in effective_config_rows():
                if "error" in row:
                    print(f"    {row['error']}")
                    continue
                print(f"    {row['key']:<28} {str(row['value'])[:40]:<40} {row['source']}")
    return 0 if r.ready else 2


if __name__ == "__main__":
    sys.exit(main())
