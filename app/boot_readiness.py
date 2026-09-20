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
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

#: A prefill at or below this is a cache hit, not a fresh evaluation. The
#: measured cold prefill for the voice prompt is ~2,900 ms and the measured
#: warm one is ~50 ms, so the gap is two orders of magnitude and the exact
#: threshold is not delicate.
PREFIX_WARM_MS = 400.0

#: `ollama.ps` reports `expires_at` years out when keep_alive=-1. Used only to
#: decide whether to call residency "held indefinitely" in the report.
KEEP_ALIVE_FOREVER = os.environ.get("OLLAMA_KEEP_ALIVE", "-1")


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
    gpu: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    degraded: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["services"] = [asdict(s) if not isinstance(s, dict) else s
                         for s in self.services]
        return d


# ── Clause 1 — services listening ──────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_env() -> None:
    """Load `.env` before anything reads it.

    Must happen before `app.llm_backend` / `app.rag` are imported: both snapshot
    their settings at import time (`KEEP_ALIVE`, `OLLAMA_NUM_CTX`,
    `OLLAMA_TEMPERATURE`). Warming with the wrong `num_ctx` or temperature would
    warm a DIFFERENT prefix than the one the caller hits, and clause 3 would
    then be confirming the wrong thing — the exact failure this story exists to
    prevent. Idempotent; never raises.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJ / ".env")
    except Exception:                             # noqa: BLE001
        pass


def default_services() -> list[tuple[str, str, int, bool, str]]:
    """(name, host, port, blocks_readiness, degrades) for every tracked service.

    Hosts are the literal IPv4 loopback on purpose: on this box a connect to
    `::1` does not fail fast, so a `localhost` probe would add ~2 s per dead
    service and make a not-ready report look like a hang.
    """
    return [
        ("FastAPI",   "127.0.0.1", _env_int("FASTAPI_PORT", 8000), True,  ""),
        ("Ollama",    "127.0.0.1", 11434,                           True,
         "no inference at all - every turn fails"),
        ("ERC MCP",   "127.0.0.1", 8010,                            False,
         "retrieval falls back to the local Chroma store (MOD-02)"),
        ("Postgres",  "127.0.0.1", 5432,                            False,
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


# ── Clause 3 — the real voice prompt, warmed and confirmed ─────────────────

def build_warm_prompt() -> str:
    """The prompt the serving path actually sends on a first turn.

    Byte-identical to `app.rag.query_rag`'s voice branch with empty retrieval,
    which is the state a boot-time warm can honestly reproduce. The prefix that
    matters is the system prompt; per TAC-4 the cache break point sits at the
    `{context}` insertion, so warming with the no-context marker warms the
    whole system-prompt prefix.
    """
    from app.voice_system_prompt import build_voice_system_prompt

    return build_voice_system_prompt("")


def _ollama_chat(prompt: str, model: str, num_ctx: int,
                 temperature: float | None, keep_alive) -> dict:
    """One chat call with the SAME shape the serving path uses.

    Sent through `ollama.chat` rather than raw HTTP so `num_ctx`, the message
    shape and the template are byte-identical to a real turn — otherwise the
    warmed prefix is not the prefix the caller will hit.
    """
    import ollama

    options: dict = {"num_ctx": int(num_ctx)}
    if temperature is not None:
        options["temperature"] = float(temperature)
    return ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options=options,
        keep_alive=keep_alive,
    )


def ollama_ps(base_url: str = "http://127.0.0.1:11434") -> list[dict]:
    """The engine's own statement of what is resident. [] on any failure."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/ps", timeout=5) as resp:
            return json.loads(resp.read()).get("models", [])
    except Exception:
        return []


def warm_and_confirm(model: str | None = None,
                     num_ctx: int | None = None,
                     temperature: float | None = None) -> dict:
    """Submit the real voice prompt twice; confirm warmth from the engine.

    Returns a dict carrying both prefills, the load duration, the residency
    evidence and a `warm` verdict. The two numbers are reported so the operator
    reads evidence rather than a boolean this module asserts about itself.
    """
    from app.llm_backend import KEEP_ALIVE, default_model, pick_model
    from app.rag import OLLAMA_NUM_CTX, OLLAMA_TEMPERATURE

    model = model or pick_model(default_model(("qwen2.5:7b-instruct-q3_K_M",
                                               "qwen2.5:7b-instruct",
                                               "qwen2.5:7b")))
    num_ctx = int(num_ctx if num_ctx is not None else OLLAMA_NUM_CTX)
    if temperature is None and OLLAMA_TEMPERATURE:
        try:
            temperature = float(OLLAMA_TEMPERATURE)
        except (TypeError, ValueError):
            temperature = None

    out: dict = {"model": model, "num_ctx": num_ctx, "warm": False,
                 "first_prefill_ms": None, "confirm_prefill_ms": None,
                 "load_ms": None, "error": None}
    try:
        prompt = build_warm_prompt()
        out["prompt_chars"] = len(prompt)

        first = _ollama_chat(prompt, model, num_ctx, temperature, KEEP_ALIVE)
        out["first_prefill_ms"] = round(first.get("prompt_eval_duration", 0) / 1e6, 1)
        out["load_ms"] = round(first.get("load_duration", 0) / 1e6, 1)
        out["prompt_eval_count"] = first.get("prompt_eval_count")

        # Same bytes, same options. If the prefix landed in the KV cache this
        # prefill collapses; if it did not, it stays cold and warmth is refused.
        confirm = _ollama_chat(prompt, model, num_ctx, temperature, KEEP_ALIVE)
        out["confirm_prefill_ms"] = round(
            confirm.get("prompt_eval_duration", 0) / 1e6, 1)
        out["confirm_load_ms"] = round(confirm.get("load_duration", 0) / 1e6, 1)

        first_ms, confirm_ms = out["first_prefill_ms"], out["confirm_prefill_ms"]
        out["warm"] = bool(
            confirm_ms <= PREFIX_WARM_MS
            or (first_ms > 0 and confirm_ms <= first_ms * 0.5)
        )
        out["speedup"] = (round(first_ms / confirm_ms, 1)
                          if confirm_ms else None)
    except Exception as exc:                      # noqa: BLE001 — boot boundary
        out["error"] = f"{type(exc).__name__}: {exc}"

    resident = ollama_ps()
    hit = next((m for m in resident if m.get("name") == model
                or m.get("model") == model), None)
    out["resident"] = hit is not None
    out["resident_detail"] = ({
        "expires_at": hit.get("expires_at"),
        "size_bytes": hit.get("size"),
        "size_vram": hit.get("size_vram"),
        "context_length": hit.get("context_length"),
    } if hit else None)
    return out


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
            r.missing.append(f"prefix warm failed — {r.prefix['error']}")
        elif not r.prefix.get("warm"):
            r.clauses["prefix"] = False
            r.missing.append(
                "prompt prefix NOT warm — engine prefill "
                f"{r.prefix.get('confirm_prefill_ms')} ms on an identical "
                f"repeat (warm means <= {PREFIX_WARM_MS:.0f} ms)")
        else:
            r.clauses["prefix"] = True
        r.model = {"model": r.prefix.get("model"),
                   "resident": r.prefix.get("resident"),
                   "detail": r.prefix.get("resident_detail"),
                   "load_ms": r.prefix.get("load_ms")}
        r.clauses["residency"] = bool(r.prefix.get("resident"))
        if not r.clauses["residency"]:
            r.missing.append(
                f"model {r.prefix.get('model')} is NOT resident per /api/ps")
        # AC-3 scenario 2. A card reporting its idle clock immediately after a
        # successful warm call means the warm did not actually wake it — a
        # resident-but-parked model is exactly the state that makes a "first
        # call is warm" claim false. UNKNOWN never fails the gate.
        r.gpu = gpu_clock_state()
        if r.gpu.get("available") and r.gpu.get("idle"):
            r.clauses["residency"] = False
            r.missing.append(
                f"GPU is idle-clocked at {r.gpu['mem_mhz']} of "
                f"{r.gpu['max_mem_mhz']} MHz after the warm — the model is "
                f"resident but the card is parked")
    else:
        ps = ollama_ps()
        r.model = {"model": None, "resident": bool(ps),
                   "detail": {"models": [m.get("name") for m in ps]}}
        r.clauses["residency"] = bool(ps)
        r.clauses["prefix"] = False
        r.prefix = {"skipped": "--no-warm: prefix warmth NOT verified"}

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
    d["basis"] = ("cheap re-assessment (services/residency/gpu/config) at call time; "
                  "the prefix clause is carried from the boot assessment - a poll "
                  "never re-warms")
    return d


def render(r: Readiness) -> str:
    ok = lambda b: "OK  " if b else "FAIL"          # noqa: E731
    lines = ["", "== READINESS " + ("READY" if r.ready else "NOT READY") + " " + "=" * 40]
    lines.append(f"  [{ok(r.clauses.get('services'))}] clause 1  turn-path services listening")
    lines.append(f"  [{ok(r.clauses.get('residency'))}] clause 2  model resident, confirmed by the engine")
    lines.append(f"  [{ok(r.clauses.get('prefix'))}] clause 3  prompt prefix warm, confirmed by prefill")
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
    if r.gpu:
        g = r.gpu
        if g.get("available"):
            verdict = "IDLE-CLOCKED (parked)" if g.get("idle") else "awake"
            lines.append(f"  gpu: mem clock {g['mem_mhz']} of {g['max_mem_mhz']} MHz "
                         f"-> {verdict}")
        else:
            lines.append(f"  gpu: UNKNOWN — {g.get('detail') or 'nvidia-smi unavailable'}")
    if r.config:
        lines.append(f"  config: {r.config.get('checked')} keys checked, "
                     f"{r.config.get('unread_count', 0)} without a reader, "
                     f"{len(r.config.get('externally_consumed') or [])} read by "
                     f"another process (not a gap)")
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
