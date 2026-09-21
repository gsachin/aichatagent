# Session handoff — 2026-09-21 (US-011 TAC-1 / Phase 1.2, closed)

> **Purpose:** resume point for the SDLC completion drive, replacing
> `session-handoff-2026-09-20.md` as the newest one. Read the 2026-09-19 handoff
> and `sdlc-completion-drive-status.md` for the drive's full context, then this.
> **Branch:** `sdlc/us011-tac1-config-migration`. **Repo:** `D:\project\universityDemo`.

## Headline

**Phase 1.2 is done. `sweep_unexplained_env_reads()` returns empty.**

- Unexplained reads **68 → 0** (67 migrated, one was not a read at all)
- Files reading `os.environ` directly: **12 → 9**, and those 9 hold nothing but
  allowlisted dynamic keys
- 16 direct reads remain, every one on `DYNAMIC_KEYS` with a written reason
- Ten commits, all on the branch, per-file as the plan required

## What landed (10 commits)

| Commit | What |
|---|---|
| `227cac8` | The gate counted an env **WRITE** as a read. `os.environ["ONNX_PROVIDER"] = requested` is not a lookup, so the gate was reporting a migration site nobody can move — and the obvious "fix" would have been to allowlist it. `_ASSIGNMENT` now excludes assignment forms (and augmented ones) while still counting `==`. Plus `reads_in_source()`, so a literal can be scanned, and the TAC-1 positive control now actually scans its probe instead of only asserting the probe was not allowlisted. |
| `aa09dca` | `voice_handler.py` 15 → 0. The two VAD keys split deliberately: import-time fallbacks resolve through `Settings`, per-session readers stay dynamic. |
| `de3e551` | `llm_backend.py` 15 → 0. `KEEP_ALIVE` stays a mutable module global because `test_brd15_rollback` reassigns it. |
| `f585518` | `rag_legacy.py` 12 → 0, and the import-order trap its negative control documented is now closed — that control **inverted** and was rewritten rather than deleted. |
| `d9bc7db` | `rag_mcp.py` 8 → 0, split by mutation evidence: 6 resolved, 2 allowlisted. |
| `a8f77c1` | `pipeline.py` 4 → 0. |
| `7d5d540` | `rag.py` + `admission.py` → 0, and **the gate caught this change**: `_env_int_or` was a helper shape the sweep didn't know, so `OLLAMA_NUM_PREDICT` was reported INERT. Enumerating suffixes has now gone stale twice; `_ENV_HELPER` accepts any suffix. |
| `c127739` | `boot_readiness.py` + `hardware_profile.py` → 0. |
| `3126a8d` | `main.py` 4 of 5 → 0. |
| `e46efcb` | `FASTAPI_WORKERS` → 0, with its TAC-3 report corrected (below). |
| `86065de` | The story, `MOD-03` and this handoff updated; two doc `file:line` citations this session had invalidated were corrected. |
| `5fd363d` | **`test_us011_import_order.py`** — the migration's own claim, tested. 42 module-level constants across the six migrated modules, three child processes each (`bare` / `explicit` / `default`), all with the same `.env`-stripped environment: `bare == explicit` proves import order no longer decides, `bare != default` proves `.env` was genuinely consulted. Rendered non-vacuous by reverting `app/rag_legacy.py` alone to its pre-migration form and watching it fail with the real defect (`OLLAMA_MODEL: 'qwen2.5:7b-instruct-q3_K_M' != 'llama3.2:3b'`), then restoring. |

## The findings worth carrying forward

**1. `FASTAPI_WORKERS` is honoured — by one launcher the docstring never listed.**
The code asserted "No launcher passes it to uvicorn … all start a single
process" and warned the operator "No launcher honours the setting; the running
count is 1". Both false:

```
start.sh:164   --workers "${FASTAPI_WORKERS:-4}"
```

That is the non-`DEV_MODE` branch of a launcher `SETUP_GUIDE.md` (10 refs),
`README_DEPLOYMENT.md` (11) and `SETUP_COMPLETE.md` (17) all point operators at.
So the honest answer for TAC-3 is **neither** branch the plan anticipated — it is
CONDITIONAL on which launcher ran. Two consequences, both worse than the
mismatch the function exists to report:

- on that path the key IS the running count, so a value above 1 is a real
  second worker, not just a contradiction sitting in `.env`;
- `${VAR:-4}` substitutes 4 when the key is unset **or empty**, so launching that
  way without it gives four uvicorn workers each holding their own whisper and
  Kokoro — the crash the docstring warns about, reached by doing nothing.

**`start.sh` is NOT changed.** Whether its default should be 4 is a launcher
decision, not a config migration. **It is the one item in this whole phase that
can OOM this box by omission.**

**2. The gate is only as good as its patterns, and it has failed twice.**
`env\(` missed every typed helper; the enumeration that replaced it missed
`_env_int_or` the day it was written. Both times the symptom was a key the
runtime genuinely reads being reported INERT — the false positive that matters,
because it tells an operator a live setting is dead. The pattern now accepts any
suffix. If a third shape appears, suspect the pattern before the code.

**3. A comment that quotes a read shape counts as a read.** The sweep matches raw
source. One drafted comment in `llm_backend.py` containing a literal
`os.environ.get("OLLAMA_KEEP_ALIVE", ...)` was reported as an unexplained site at
line 226 until reworded.

**4. Two dead things found and flagged, not deleted.**
`app/boot_readiness.py`'s `KEEP_ALIVE_FOREVER` has no reader anywhere, and holds
the **string** `"-1"` where the live value is the **int** `-1` — the string is a
400 on every request, so any future caller that picks it up breaks calls, not
just residency. And two documents state `RAG_MCP_TIMEOUT` defaults to 2.5 while
the code says 6.0 (`bootstrap_services.py` already independently asserts 2.5 is
the stale value).

**5. Pre-existing failures, verified not mine.** `tests/test_offer_readiness.py`
fails 5/18 (pytest-asyncio is not installed — this is the file the 2026-09-20
handoff's subset already excluded). `tests/test_task9_requirements.py` fails on a
requirements marker containing spaces. `test_ac_traceability.py` reports 7
stories with untraced ACs — US-011 is not among them. Each was confirmed
identical with the changes stashed.

## Current numbers (measured, not estimated)

- Unexplained reads: **0** (of 16 direct reads, all allowlisted)
- Files reading `os.environ` directly: **9**
- `test_us011_config_truth.py`: **38/38** (was 34/34)
- `test_us011_import_order.py`: **13/13, 2 noted** (~21 s) — new, see below
- Full `tests/` suite: **550 passed**, 1 pre-existing failure
- Targeted subset: **331 passed**
- Doc gates: `test_doc_citations.py` 5/5, `test_doc_truth.py` 6/6

## Next actions, in order

1. **Restart the stack and watch the boot.** The static half of this is now done
   — `test_us011_import_order.py` proves the resolved values no longer depend on
   import order, and the gate proves every read goes through `Settings`. What is
   still unverified is that a real process boots and serves on the migrated code:
   eleven commits changed how every module resolves configuration, and nothing
   has run them as a process. Expect no behaviour change (`app/main.py` already
   loaded `.env` early, so the app's own values were never wrong) — a difference
   is the interesting signal, not a regression to shrug at.

2. **Decide `start.sh`'s `${FASTAPI_WORKERS:-4}`** (finding 1). Either default it
   to 1 or make it read `.env`, and say which. Nothing else in this phase has
   this blast radius.
2. **Bucket D** — the engine-level `OLLAMA_*` keys documented in `.env.example`
   under "consumed by the Ollama service", with `_EXTERNALLY_CONSUMED`
   carve-outs. Plus `.env.example` gains `SMALL_TASK_NUM_CTX`; `.env` gains
   `VAD_SILENCE_MS` and `VAD_SPECULATIVE_ADVANCE_MS` (the harness reads those two).
3. **Second carve-out corpus** for `scripts/*.py` and the root scripts — never
   fold them into the runtime corpus.
4. **`MACHINE_PROFILE_CHECK`** as a non-blocking boot drift report (discharges
   `DG-05` visibility).
5. **Close US-011's DoD**: LLD test mapping (T-1..T-17), `MOD-07` B.4/B.6
   reconciliation, the TAC-8 no-regression load test. T-7's box now records that
   the `in_effect` dichotomy was itself the error.
6. Then back to the drive's order: Phase 1.3 remainder (US-013 T-11 trace
   fields), Phase 2.3 on a **quiet box**, Phase 3 story closures, 4b, 5, 6.

## Stack state

Ollama :11434, app :8000, MCP :8010, Streamlit :8501/:8502, CRM :8098 — all
running, untouched by this session. **The running app is still the pre-migration
code**: nothing restarted it, and the migrated modules now resolve `.env` at
import where they previously depended on import order, so a restart is needed
before these changes are live — and a restart is the first real test of them.

Ten commits are on the branch; nothing is pushed. The working tree still carries
the uncommitted `app/crm/*` + `app/leads/*` work that was there at the start;
none of it was staged or committed by this session.

Use `127.0.0.1`, never `localhost` (IPv6 stall on this box).
