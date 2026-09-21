# Session handoff — 2026-09-21b (US-011 TAC-3 launcher fix; boot verified)

> **Purpose:** resume point for the SDLC completion drive, superseding
> `session-handoff-2026-09-21.md` (which closed Phase 1.2 and is still the best
> account of that work — read it first, then this). **Branch:**
> `sdlc/us011-tac1-config-migration`. **Repo:** `D:\project\universityDemo`.

## Headline

**Next-actions 1 and 2 from the 2026-09-21 handoff are both done.**

1. The migrated code **boots and serves** — first time it has run as a process.
2. `start.sh`'s `${FASTAPI_WORKERS:-4}` is **fixed** — the flag is removed, not
   tuned. The one item in Phase 1.2 that could OOM this box by omission is gone.

## 1. The boot verification (handoff next-action 1)

The stack was **already down** when this session started — only Ollama :11434 was
listening. So this was a cold start, not a restart, and nothing had to be killed.

Booted FastAPI alone, per the PO's decision (local-only; no Cloudflare tunnel, no
Twilio webhook mutation — neither exercises the config migration):

```
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Full clean startup, every stage:

- `Database: PostgreSQL connected — lead capture enabled`
- `Machine profile matches config (tier nvidia_high)`
- `RAG backend pre-warmed` · `LLM backend: ollama`
- `Whisper model pre-warmed` (faster-whisper small.en on CUDA)
- `Kokoro TTS pre-warmed` (`CUDAExecutionProvider,CPUExecutionProvider`)
- `Application startup complete.` → `GET / → 200 OK`
- `Readiness assessed at boot: READY (no gaps)`

**No behaviour change**, which is what the handoff predicted and what makes the
result uninteresting in the right way: `app/main.py` already loaded `.env` early,
so the app's own values were never wrong. The 11 migration commits are now
confirmed to survive contact with a real process, not just the static gates.

The only errors in the log are `crm.outbox` ConnectTimeouts — the Salesforce
admission API (:8098) is not running, which is expected for a FastAPI-only boot.
62 outbox entries are queued and retrying; unrelated to the config migration.

## 2. The `start.sh` fix (handoff next-action 2)

**Decision taken: remove the flag rather than tune its default.** Defaulting to 1
was rejected because it fixes only the *unset* case — this repo's `.env:238`
carries an explicit `FASTAPI_WORKERS=4`, which `start.sh:39-40` exports into the
shell, so `bash start.sh` would still have produced four uvicorn workers each
loading its own whisper and Kokoro. The flag now does not exist, and every
launcher starts one process.

Files changed:

| File | Change |
|---|---|
| `start.sh` | `--workers "${FASTAPI_WORKERS:-4}"` **removed**, replaced by a comment recording why it must not come back. `bash -n` clean. |
| `app/main.py` | `_reconcile_workers()` docstring and warning updated: the launcher is no longer CONDITIONAL. The report now says **NOT IN EFFECT**, and the "Set FASTAPI_WORKERS=1" advice is dropped — the key is sizing metadata, so its value does not need to be 1. Verified live in the boot log. |
| `app/config.py` | The field comment also named `start.sh:164`; corrected with it. |
| `doc/sdlc/stories/US-011-…md` | TAC-3 note rewritten (two steps: discovery, then the launcher decision); T-7 row now **satisfied** via the `in_effect: false` branch; "outstanding" list updated. |
| `doc/sdlc/modules/MOD-07-…md` | The `FASTAPI_WORKERS=4` row's **claim was false as written and is now true** — plus three stale citations fixed (`.env:183`→`238`, `hardware_profile.py:49,319,329`→`51`/`331`/`341`, `start_services.ps1:559`→`597`). |

A finding worth keeping: **MOD-07 never mentioned `start.sh` at all.** The module
doc asserted "every repo reference outside the test file is on the *writer*
side" while `start.sh:164` sat there as a reader. The 2026-09-21 session found
this in the code and corrected `main.py`; the doc kept the false claim until now.
The launcher fix is what reconciled the doc — it did not need editing to become
true, only the citations did.

## Verification, measured

- `test_us011_config_truth.py`: **40/40** (38 before Bucket D's two new controls)
- `test_us011_import_order.py`: **13/13, 2 noted** · `test_endpointing.py` **18/18**
- `test_doc_citations.py`: **5/5** · `test_doc_truth.py`: **6/6**
- Full `tests/` suite: **563 passed, 6 failed** — and all 6 are the pre-existing
  pair the 2026-09-21 handoff documented under finding 5, not regressions:
  `test_offer_readiness.py` ×5 (`pytest_asyncio` is not installed — the file uses
  `@pytest.mark.asyncio` at :61, :75, :91) and
  `test_task9_requirements.py::test_requirements_parseable` ×1 (`assert ' ' not in
  'onnxruntime-gpu==1.28.0; platform_system=="Windows" or …'`). Neither file
  references `start.sh`, `main.py`, `config.py` or `FASTAPI_WORKERS`.
  *Note:* the 2026-09-21 handoff's summary line says "550 passed, **1**
  pre-existing failure", which contradicts its own finding 5 (six failures). The
  numbers above are the measured ones.
- `sweep_unexplained_env_reads()`: **0** (Phase 1.2's headline still holds after
  the `main.py`/`config.py` edits — re-checked explicitly, not assumed)
- Boot verified **twice**: once on the migrated code, then again after the edits
  to confirm the new warning renders (it does — captured in
  `logs/boot-verify-after-tac3.log`)

## Stack state

**Nothing is running but Ollama** (`:11434`). MCP :8010, CRM :8098, Streamlit
:8501/:8502 were never started — a local-only boot was chosen over
`start_services.ps1`, which would also have created a Cloudflare tunnel and
repointed the live Twilio webhooks.

The boot-verify FastAPI on `127.0.0.1:8000` **was killed after the fact by Claude
Code's memory-pressure reaper**, while the session sat idle: the box went
critically low on memory and the background shell was reaped. This is an
environment event, **not a failure of the app** — the same code booted cleanly
twice and both runs are on disk. Port 8000 is free and no python process remains.

Do **not** restart it without being asked; memory may still be short. Worth
knowing for whoever does: this stack is RAM/VRAM heavy by design, since whisper
and Kokoro are held in-process, and `start_services.ps1` layers MCP, CRM,
Streamlit and Ollama pre-warm on top of that. `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1`
disables the reaper (has no effect if set from within a shell command).

Logs: `logs/boot-verify-us011.log` (migrated code) and
`logs/boot-verify-after-tac3.log` (post-fix).

**Nothing is committed.** All five changed files are in the working tree
alongside the pre-existing uncommitted `app/crm/*` + `app/leads/*` work.

## Next actions

1. ~~**Commit the TAC-3 launcher fix**~~ **DONE** — `b175b97` (code) + `a4fed49`
   (docs). Nothing pushed; the branch has no upstream.
2. ~~**Bucket D**~~ **DONE** — `.env.example` carries a "Consumed by the Ollama
   SERVICE, not by this application" section (engine keys, values measured from
   this box's own `msg="server config"` line); `_EXTERNALLY_CONSUMED` gains those
   keys as an **exact list, not an `OLLAMA` prefix**, with two new non-vacuity
   controls in `test_us011_config_truth.py` (**40/40**) — one asserts a planted
   engine key is carved out, the other that a planted unlisted `OLLAMA_*` key is
   still caught. `SMALL_TASK_NUM_CTX` needed nothing (`5c5df80` had it, commented,
   which is correct). `.env` gained the two VAD keys — **local only**, `.env` is
   gitignored, so that half cannot be committed.
   - **Found doing it:** the plan's reason for the VAD keys ("the harness reads
     them") is wrong — `test_endpointing.py` *mutates* them, it never reads them
     from `.env`. And the first draft of the `.env.example` section asserted no
     launcher sets engine keys; `docker-compose.yml:55-56` sets `OLLAMA_HOST` and
     `OLLAMA_MODELS`. Checking that claim is what surfaced `OLLAMA_MODELS`.
   - **Pre-existing, not ours:** `test_us005_stream_tts.py` fails 2/25. Cause
     confirmed by experiment — forcing `TTS_CACHE_SCOPE=shared` passes 25/25. The
     gate seeds `_shared_tts_cache` directly but reads ambient `.env`, which sets
     `per_call` (deliberate, measured, US-012, 2026-09-19). A gate that depends on
     ambient config instead of forcing the scope it tests.
3. ~~**Phase 1.3 — US-013 T-11**~~ **DONE**, and it found a defect bigger than
   the task. The per-turn `DAT-07` record now carries `retrieval_rung`,
   `retrieval_breaker` and `retrieval_dead_dependency_ms` (`app/rag.py`,
   `_note_retrieval`), on every return path. Adding them is what made the
   wiring defect visible: **the breaker gated nothing in production.**
   `rag._use_mcp()` — the function written to choose the rung — had no caller in
   `app/` at all; `git log -S` shows the name in exactly one commit, `a049bc5`,
   the one that defined it. So every turn during an outage called the dead
   service at the full 6.0 s serving timeout, `claim_probe()` was never reached,
   `_probe_in_flight()` was never true, `_post` never selected the 1.5 s probe
   budget, and `breaker_probes` could only ever read 0. **TAC-4's "bounded per
   window" was untrue as wired**, and the suite passed 51/51 throughout because
   it calls `_use_mcp` and `claim_probe` directly: it tested the mechanism, not
   its reachability.
   - **Fix:** one decision, `rag_mcp.admit_primary()`, routes all three primary
     call sites — `_retrieve_context`, `_threshold_distance` (a second call in
     the same turn, dormant only because `RAG_SIMILARITY_THRESHOLD=0.0`) and
     `MCPRetriever` (`app.py` / `admissions_bot.py`). Gating the last two
     matters beyond their own budget: `_record_failure` restamps `failed_at` on
     every failure, so an ungated caller keeps pushing the cooldown out and the
     probe never becomes eligible.
   - **Suite:** `test_us013_breaker.py` 51 → **70**, with six checks that drive
     `_retrieve_context` itself. Verified non-vacuous by reverting `app/rag.py`
     alone: they report `the primary was called 1 time(s) anyway`,
     `2 probes for one window`, `probe budgets seen: [False, False]`.
   - **Two adapter tests were made explicit rather than broken:**
     `test_rag_mcp_adapter.py` now sets `USE_MCP_RAG=auto`, because conftest's
     hermetic default is `off` and the retriever now honours it. The repo's own
     convention (the conftest comment) is that tests set the mode per test.
   - **Ripple:** the citation gate caught this change's own drift —
     `_threshold_distance` moved 163 → 237, invalidating three citations in
     `MOD-02` (`:86`, `:201`) and `US-009` (`:91`). All three updated.
4. **Second carve-out corpus** for `scripts/*.py` and the root scripts — never
   fold them into the runtime corpus. (Verified latent, not live: no `.env` key
   is currently read by scripts alone.)
5. ~~**`MACHINE_PROFILE_CHECK`** boot drift report~~ — **already implemented**,
   contrary to the earlier list: `app/main.py:294-308` calls `check_drift()` in
   the lifespan, warns on `drifted`, swallows every exception, and exposes the
   state in health/dashboard payloads (`:3331`, `:3735`). Nothing to do.
6. **Close US-011's DoD** — LLD test mapping (T-1..T-17), the remainder of
   `MOD-07` B.4/B.6 (only the `FASTAPI_WORKERS` row was reconciled here; the
   other three inert items and the rest of MOD-07's `.env` line citations are
   unaudited), and the TAC-8 no-regression load test.
7. Then the drive's order: Phase 2.3 on a **quiet box**, Phase 3 story closures,
   4b, 5, 6. Note US-013's TAC-4/TAC-6/TAC-10 **load** figures are still
   unmeasured — and they are now measurable for the first time, because the
   fields exist and the circuit actually gates. That is the natural next load
   run once the box is quiet.

Use `127.0.0.1`, never `localhost` (IPv6 stall on this box).
