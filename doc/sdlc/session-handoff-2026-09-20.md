# Session handoff — 2026-09-20 (Phase 1.2 / US-011 TAC-1)

> **Purpose:** resume point for the SDLC completion drive, replacing
> `session-handoff-2026-09-19.md` as the newest one. Read the 2026-09-19 handoff
> and `sdlc-completion-drive-status.md` for the drive's full context, then this.
> **Branch:** `sdlc/us011-tac1-config-migration`, cut from `feature/meridian-kb-population`
> (`aa9a9e5`). **Repo:** `D:\project\universityDemo`.

## Why this item and not Phase 2.3

The 2026-09-19 drive-status named **Phase 2.3 qualifying runs** as next. Those
require a **quiet box** — cold N=2 smoke, then two consecutive warm N=2 runs at
≥100 turns/session with zero over-cap. The box was not quiet (a live WhatsApp
demo with tunnels up, and the app, MCP, Ollama, CRM and both Streamlit UIs
running), which would both disturb the demo and invalidate the measurements'
own premise. **Phase 1.2 (US-011 TAC-1) was taken instead** — it is the
highest-volume remaining item, it is pure code work, and it is the item the
plan sequences *before* the measurement stories are trusted rather than before
they are run.

**Phase 2.3 is still the outstanding latency item** and should be run on a quiet
box. Note also that it now needs re-planning: the stack has since moved to the
`meridian_kb__v1` collection and the C3/C5 RAG work landed, so the retrieval leg
those runs would measure is not the one the predictions were made against.

## What landed (4 commits, all on the branch)

| Commit | What |
|---|---|
| `0a969a1` | The reader sweep sees the typed helper shapes. `_READ_PATTERNS` ended with `env\(`, matching `_env(` only by accident of being a substring and missing `_env_int`/`_env_float`/`_env_bool` — so a key read that way was counted unread and would be reported **INERT**. One site today (`boot_readiness.py:122`), masked by `FASTAPI_PORT`'s carve-out, so nothing was misreported yet; fixed before the migration adds more of these shapes. |
| `3f7245e` | One `database_dsn()` home replacing four byte-identical copies of the six `DB_*` reads. **This was a live defect** — see below. |
| `a194965` | One `tunnel_host()` replacing four copies of the tunnel resolution, one of which was dead code (`app/main.py` defined `_resolve_tunnel_host` twice; the later definition silently won). |
| `4667281` | The TAC-1 gate itself: a per-read scan and a reasoned dynamic allowlist. Plus six files migrated. |

## The two findings worth carrying forward

**1. The database config was order-dependent.** `app/database.py`,
`app/leads/models.py`, `app/sentiment/models.py` and an inline block in
`app/main.py` each read the six `DB_*` keys from `os.environ` **at their own
import time**, and none of them loads `.env`. Reproduced:

```python
from app.leads import models      # imported first; nothing loaded .env
models.DATABASE_URL               # '' -> the localhost/postgres defaults
```

so the process silently targets a different database from the operator's. It
works in the running app only because `app.main` happens to import `app.config`
early. All four now resolve through `settings`, which makes the answer
independent of import order. The same block's "PostgreSQL not available"
warning logged the connection string verbatim (password included on the
fallback path); scanning every log in `logs/` for every sensitive value found
**no** occurrence, so it is a latent path closed, not a leak cleaned up.

**2. Writing the Settings block guessed six defaults wrong.** The first draft
used `SENTIMENT_W1..W4` = 0.4/0.3/0.2/0.1, `SENTIMENT_EWMA_LAMBDA` 0.3,
`MIN_LABELED_OUTCOMES` 5, `BG_DEFER_TIMEOUT_S` 10s. The call sites use
0.30/0.30/0.25/0.15, 0.35, 100 and 30s. Corrected before commit. **Every
remaining migration must read the default off the call site first** — including
any `or <default>` empty-value guard, which several sites have.

## Current numbers (measured, not estimated)

- Files reading `os.environ` directly: **21 → 12**
- Direct read sites: **121 reads** total, of which **9 are allowlisted dynamic**
  and **68 are unexplained**
- `test_us011_config_truth.py`: **34/34** (was 17/17)
- Targeted test subset: **331 passed, 0 failed** (`-k "sentiment or work_priority
  or crm or rag or phase"`, excluding the `pytest-asyncio` file)

## Next actions, in order

1. **Continue Phase 1.2** — migrate the remaining 68 reads, per file, per the
   table in `doc/sdlc/stories/US-011-single-config-source-of-truth.md`. Order by
   count: `voice_handler.py` (16), `llm_backend.py` (15), `rag_legacy.py` (12),
   `rag_mcp.py` (8), `main.py` (5), `pipeline.py` (5), then the singletons.
   **Read each default from its call site.** Commit per file.
2. **`FASTAPI_WORKERS` is not a mechanical move** — it is `TAC-3`'s subject and
   must either be wired to something that honours it or reported as
   `in_effect: false` with the `REC-02` reason. Give it its own commit.
3. **Bucket D** — the engine-level `OLLAMA_*` keys documented in `.env.example`
   under "consumed by the Ollama service", with `_EXTERNALLY_CONSUMED`
   carve-outs. Plus `.env.example` gains `SMALL_TASK_NUM_CTX`; `.env` gains
   `VAD_SILENCE_MS` and `VAD_SPECULATIVE_ADVANCE_MS` (the harness reads those).
4. **Second carve-out corpus** for `scripts/*.py` and the root scripts — never
   fold them into the runtime corpus.
5. **`MACHINE_PROFILE_CHECK`** as a non-blocking boot drift report (discharges
   `DG-05` visibility).
6. **Close Phase 1.2**: the gate reads zero unexplained reads, and the story's
   DoD boxes for TAC-1 are ticked with that evidence.
7. Then back to the drive's order: Phase 1.3 remainder (US-013 T-11 trace
   fields — the breaker surface itself landed in `d176be3`), Phase 2.3 on a
   quiet box, Phase 3 story closures, 4b, 5, 6.

## Stack state

Ollama :11434, app :8000, MCP :8010, Streamlit :8501/:8502, CRM :8098 — all
running. Three Cloudflare tunnels up (`.whatsapp_tunnel`, `.tunnel_8501`,
`.tunnel_8502`). **The running app is the pre-migration code** — nothing in this
session restarted it, and none of the migrated modules is on a hot path this
demo exercises, but a restart is needed before these changes are live.

Use `127.0.0.1`, never `localhost` (IPv6 stall on this box).
