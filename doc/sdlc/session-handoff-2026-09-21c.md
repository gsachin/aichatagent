# Session handoff — 2026-09-21c (US-011 DoD drive, Phase 1.3, performance report)

> **Power-off resume point.** Supersedes `session-handoff-2026-09-21b.md` (which
> closed Phase 1.2 and Bucket D — still the best account of that work).
> **Read order:** `doc/sdlc/sdlc-completion-drive-status.md` →
> `doc/perf/PERFORMANCE_REPORT_2026-09-21.md` (new, evidence-graded) →
> `session-handoff-2026-09-21b.md` → this.
> **Branch:** `sdlc/us011-tac1-config-migration`. **Repo:** `D:\project\universityDemo`.

## Headline

Nine commits this session. **Nothing is uncommitted and nothing is pushed** — the
branch has no upstream configured. The tree is safe to lose; the work is on disk
in git.

What landed, in order: the `start.sh` OOM path removed · Bucket D (engine-level
`OLLAMA_*` keys) · **US-013's breaker finally wired to the serving path** · two
US-011 DoD boxes closed · T-14 (run summary carries its configuration) ·
**TAC-6 provenance, which had been a constant** · T-11/T-12 driven · a full
performance report.

## The nine commits

| Commit | What |
|---|---|
| `b175b97` | `fix(us011)`: `start.sh` stops passing `--workers`, so the key is inert on every launcher |
| `a4fed49` | `docs(us011)`: the launcher decision recorded, MOD-07's row reconciled |
| `132efa5` | `feat(us011)`: Bucket D — engine-level `OLLAMA_*` keys get a home and a carve-out |
| `0ac0a16` | `fix(us013)`: **the breaker finally gates the serving path**, and the record says so |
| `5bf4790` | `docs(us011)`: two DoD boxes closed — the artifact demoted, MOD-07 reconciled |
| `dc8d48b` | `feat(us011)`: T-14 — the run summary carries the configuration behind its numbers |
| `27b34bd` | `fix(us011)`: **TAC-6 — provenance was a constant, and a passing check enforced it** |
| `d278b44` | `test(us011)`: T-11 and T-12 driven; the two remaining partials named with reasons |
| `0dd0d2e` | `docs(perf)`: **performance report** — what improved, what is pending, how critical |

## US-011 status: DoD **6/8**

| Box | State |
|---|---|
| 2 — LLD test mapping (T-1…T-17) | **13 pass, 2 partial, 2 not run.** T-14, T-1, T-10, T-11, T-12 closed this session |
| 3 — TAC-8 no-regression load test | **NOT RUN** — needs a quiet box + the full stack |

**The two remaining partials, with their reasons** (do not re-litigate without
reading the evidence table in the story):

- **T-8** — the "after a restart" half would mean a test writing to `.env`, the
  untracked runtime authority with no VCS safety net. Left partial *deliberately*.
- **T-15** — and its split is the finding: a malformed value behaves differently
  **by key class**, and no single path has both halves. A key parsed at import
  (`int(_env(...))`, `app/config.py:127`) raises before serving — so the stack
  stops, but the key is named only inside a traceback. A key that survives import
  is named properly by `check_config_keys`, but that makes the stack *not-ready*
  rather than stopping it. **Closing it needs a key-naming path for import-time
  parse failures** — a change to how ~110 fields resolve. Not started.

## Next actions, in order

1. **`US-011` box 2 remainder** — either the T-15 key-naming path (~1–2 h, touches
   how every `Settings` field resolves), or accept T-8/T-15 as documented
   partials and tick the box on that basis. **This is a judgement call for the PO.**
2. **`US-011` box 3 — the TAC-8 load pair** (~2–4 h). Blocked on a **quiet box**.
   Note the "before" half means checking out the pre-migration revision and
   running the same load against it — the migration is 11+ commits back.
3. Back to the drive's order: **Phase 2.3 qualifying runs** (needs a quiet box),
   Phase 3 story closures, 4b, 5, 6. See the drive status.

## The three findings that would be expensive to rediscover

**1. US-013's circuit breaker gated nothing, for its entire life.**
`rag._use_mcp()` had **no caller in `app/`** — `git log -S` shows the name in
exactly one commit, `a049bc5`, the one that *defined* it. So every turn during an
outage called the dead service at the full 6.0 s timeout; `claim_probe()` never
ran, so `_post` never selected the 1.5 s probe budget and `breaker_probes` could
only ever read 0. **`TAC-4`'s "bounded per window" was untrue as wired.** The
suite passed 51/51 throughout because it calls `_use_mcp` and `claim_probe`
directly — it tested the mechanism, not its reachability. Fixed by routing all
three primary call sites through `rag_mcp.admit_primary()`; verified non-vacuous
by reverting one file (reports *"10 trips for 10 turns"*; with the fix, 1).

**2. `TAC-6` provenance was a constant, and a passing check was enforcing it.**
`effective_configuration()` enumerated `.env`'s keys and stamped every row
`"authoritative (.env)"`. So **58 of 110 `Settings` fields had no reported
provenance at all**, and `overridden_detection_artifact` was unreachable. The
check named *"AC-1 every key reports a provenance"* asserted
`all(s.startswith("authoritative"))` — true only because the value was constant,
so **it failed the moment provenance was implemented correctly**. The report is
now 122 rows with computed provenance (64 authoritative + 58 code default), and
it surfaces three real divergences that were previously invisible:
`OLLAMA_MODEL` (`applied=qwen2.5:14b` vs `llama3.2:3b`), `WHISPER_NUM_THREADS`
(6 vs 4), `LLM_PROVIDER`.

**3. `start.sh` was the one launcher that honoured `FASTAPI_WORKERS`.**
`--workers "${FASTAPI_WORKERS:-4}"` in the non-`DEV_MODE` branch, and `.env:238`
sets it to `4` explicitly. Four uvicorn workers × whisper + Kokoro each = the OOM
the stack is designed to avoid, reached by following the launcher `SETUP_GUIDE.md`,
`README_DEPLOYMENT.md` and `SETUP_COMPLETE.md` all point operators at. Flag
removed rather than tuned (defaulting to 1 would not have helped — the explicit
`.env` value still wins).

## Verification — run these to confirm the tree is good

```bash
cd /d/project/universityDemo
.venv/Scripts/python.exe doc/perf/tools/test_us011_config_truth.py    # 51/51
.venv/Scripts/python.exe doc/perf/tools/test_us013_breaker.py        # 71/71
.venv/Scripts/python.exe doc/perf/tools/test_brd15_rollback.py       # 42/42
.venv/Scripts/python.exe doc/perf/tools/test_doc_citations.py        # 5/5
.venv/Scripts/python.exe doc/perf/tools/test_doc_truth.py            # 6/6
.venv/Scripts/python.exe -m pytest tests/ -q                         # 563 passed, 6 failed
```

**The 6 failures are pre-existing and environmental** — `test_offer_readiness.py` ×5
(`pytest_asyncio` is not installed) and `test_task9_requirements.py` ×1 (a space in
a requirements marker). Identical before and after every change this session.

`doc/perf/tools/test_us005_stream_tts.py` also fails 2/25 — **pre-existing**, and
diagnosed: forcing `TTS_CACHE_SCOPE=shared` makes it pass 25/25. The gate seeds
`_shared_tts_cache` directly but reads ambient `.env`, which sets `per_call`
(deliberate, measured, US-012, 2026-09-19). A gate that depends on ambient config
instead of forcing the scope it tests.

## Stack state

**Nothing is running but Ollama** (`:11434`). The boot-verify FastAPI instance was
**killed by Claude Code's memory-pressure reaper** while this session sat idle —
an environment event, not a failure of the app. Do not restart without being
asked; memory may still be short. This stack is RAM/VRAM heavy by design (whisper
and Kokoro held in-process), so bring it up deliberately.

`start_services.ps1` is the safe launcher (no `--workers`, single process). It
also creates a Cloudflare tunnel and repoints the live Twilio webhooks — that is
an outward-facing side effect, so use it only when the full stack is wanted.

## Environment gotchas (carried forward)

- **Use `127.0.0.1`, never `localhost`** — IPv6 stalls ~2,066 ms on this box.
  (`doc/perf/PERFORMANCE_REPORT_2026-09-21.md` §2.3 has the measurements.)
  Note `.env.example:26` still teaches the trap — a known one-word fix, not applied.
- **`.env` is gitignored.** Changes there cannot be committed. `git add` by
  explicit path only.
- **A pre-existing uncommitted workstream must not be swept into commits:**
  `app/crm/*`, `app/leads/*`, `doc/salesforce/*`, `salesforceAPISchema`,
  `scripts/verify_gate7_status.py`, `tests/test_crm_*`. It was there at session
  start and is still there, untouched. `doc/perf/runs/*.json` are also untracked.
- **Git email** in this repo is `pradeepdgenai@users.noreply.github.com` (GH007).
  Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- **Do not edit the working tree while a background pytest run is in flight** —
  a `git stash` mid-run invalidated one suite result this session. Kill and re-run.

## The performance report

`doc/perf/PERFORMANCE_REPORT_2026-09-21.md` — the single evidence-graded file for
"how much faster is it and what is left". Headline: **~2.5× faster at p50**
(live p50 5,141 → 2,063 ms; cap breaches 92.4% → 15.2% across 3,626 turns), and
**the 3,000 ms cap is still not met** — best powered run is 8 breaches in 120
turns against a criterion of zero, and all 18 powered runs are discarded.

Its §4 ranks what is pending by consequence. **Only one item carries an explicit
severity in the whole doc set**: `DEF-001`, *"Critical — the single largest
contributor to BRD-05 failures and it is invisible in the logs"*, status
**DEFERRED**. It carries its own counter-evidence: fixing it halves the p95 and
still leaves 26% of turns over the cap. §5 states the evidence base's limits
plainly — 71 of 73 run artifacts discarded, `harness_fault: true` on every N=2
run, carrier hop excluded, one hour of measurements voided outright.
