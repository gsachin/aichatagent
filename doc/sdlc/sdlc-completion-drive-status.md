# SDLC Completion Drive — complete prompt + current status (power-off resume)

> **Purpose:** if power dies, read this file FIRST, then `doc/sdlc/unblock-plan.md`
> and the newest `doc/sdlc/session-handoff-2026-09-21*.md`. Written 2026-09-19 (late
> session); **status refreshed 2026-09-21** — see the delta immediately below, and
> `doc/perf/PERFORMANCE_RESUME_2026-09-21.md` for the performance half.
> Branch: `performance-improvement` (was `perf/us007-boot-readiness` when this file
> was written; then `sdlc/us011-tac1-config-migration`).
> Repo: `D:\project\universityDemo` (sibling: `D:\project\enterprise-rag-core`).

## What moved since this file was written (delta, 2026-09-21)

This file was 25 commits stale. Everything the old "next actions" list called
pending is done, plus work that was not on it. **Do not work the queue below
this box — it is history.**

| Was listed as pending | Actual state |
|---|---|
| **Phase 1.2 — US-011 TAC-1 config migration** | **DONE** — the per-read sweep, buckets A–D, and the import-order trap. `16202b1` … `86065de` |
| **Phase 1.3 — A3 breaker + T-11 trace fields** | **DONE** — and the breaker was found to gate *nothing*: `rag._use_mcp()` had no caller in `app/`, so `TAC-4`'s "bounded per window" was untrue as wired. Now routed through `rag_mcp.admit_primary()` (`0ac0a16`, `d278b44`) |
| **Phase 2 — latency drive** | **US-005 streaming TTS built** (`43a3fb9`), **US-004 streaming LLM built** (`96f3407`), its N=2 freeze root-caused (`3a1a101`, ClauseCutter re-matching its own boundary). Both **behind flags, default-OFF** |
| **Phase 4a PO decisions** | **DONE** — all six (`8ae8ff4`) |
| **Phase 6 — `run_all_gates.py`** | **BUILT** (`f005913`) — 22 gates, one scoreboard. `doc/perf/tools/run_all_gates.py` |
| *(not on the old list)* | **US-011 TAC-6 provenance was a constant** and a passing check was enforcing it (`27b34bd`); **`ConfigurationError`** was specified in the LLD and never existed (`2d21eba`, 46 call sites) |

**Still open, and it is the same list as before — see the rewritten next actions.**
Note the branch changed twice; `git branch -a` before assuming.

## The complete prompt (what the user asked for, this drive)

> Pasted: an assessment showing the SDLC is not release-ready — 0/18 stories fully
> DoD-complete, 42 ACs / 106 TACs untraced, 33/147 DoD boxes checked, status docs
> disagree, US-007 live readiness fails a config-reader clause, several stories
> blocked/partially measured. User instruction: "make sure you research plan and
> execute carefully, keeeeeeeep watch on each US progress (including ACs and DoDs
> all should work)" + mid-turn: "Make sur every USs".

**User decisions (confirmed via question tool, 2026-09-19):**
1. **User acts as PO in-session** — DG-03 ground truths + sign-offs (US-003/004/005/
   009/010/014/018, US-016 wording) are processed in-session.
2. **Sequencing: risk-ranked.**
3. **Latency: fix until the 3,000 ms cap passes** — harness-clean N=2 warm runs with
   zero turns over cap; cap levers = US-004 (streaming LLM) + US-005 (streaming TTS),
   both PO-unlockable now.
4. **Commits: checkpoint per story at gate-green.** Never commit secrets.
5. **Every US covered — all 18.**

**Full approved plan:** `C:\Users\ADMIN\.claude\plans\twinkling-wibbling-starfish.md`
(phases 0–6, per-story baseline table, risk register, verification commands).
Standing instruction (session-handoff): each item's AC and DoD fully complete before
moving to the next. Agreed per-item AC/DoD for A1/A3/A5/A2 in session-handoff §7.

## Current status (what is done)

| Step | State | Evidence |
|---|---|---|
| Phase 0.1 secrets | DONE | `salesforcer_api.txt` + `doc/salesforce/STATUS_2026-09-13.md` restored from HEAD (plaintext Salesforce id/secret were working-tree-only; HEAD clean). `salesforceAPISchema` scanned: 0 secret hits. No commit needed (nothing changed vs HEAD). |
| A1 discard rule | DONE, committed `216af99` | harness discard on `first_audio_n==0`/partial + negative control + provenance contract (`a1_fresh_summary_records_current_harness_hash`). 9 pre-rule empty/partial artifacts re-evaluated → `__DISCARDED`; store now 0 non-discarded empty/partial. Self-test contract 5/5 A1 checks True. |
| Session-handoff tracker | DONE | A1 row → DONE; §5 updated with commit. |
| A2 dual clocks | DONE, committed `102a04e` | Harness emits per turn: `first_audio_from_speech_end_ms` (alias of `first_audio_ms`), `first_audio_from_vad_end_ms` (joined from DAT-07 row), `endpointing_ms` (harness−app); summary buckets `vad_end_first_audio_ms` + `endpointing_ms`; `cap_basis` rewritten naming both clocks. Self-test `a2_*` 4/4 True. Live dual-clock re-run deferred to Phase 2 (needs live runs, blocked on Phase 0.4). |
| Phase 0.4 WS 1011 | DONE (evidence in `_ws1011_probe.py` + runs `T024150Z`/`T025002Z`) | Root cause: the app revision serving the 18:16/18:26 IST deaths ran cold model loads (greeting Kokoro) ON the event loop → loop blocked >20 s → keepalive pings unanswered → 1011. Fix `7c19426` (18:03 IST: three blocking calls → to_thread + lifespan pre-warms Whisper/Kokoro) post-dates the deaths; warm N=2 post-fix (`T212410Z`, 200 turns) and two cold N=2 runs now complete with no 1011. Receive-bound theory REFUTED: websockets answers pings in `data_received` (protocol layer) while the handler is mid-turn — measured pongs at 0 ms during a 12 s turn, HTTP max 906 ms. Harness resilience: `--ws-ping-interval/--ws-ping-timeout` (default disabled for cold sessions — a cold turn can outlast a keepalive window; basis recorded in every summary; 4/4 `ws_ping_*` self-test checks). |
| Phase 1.1 /health + /ready | DONE, committed `c8a31b6` | `GET /health` = pure liveness. `GET /ready` = boot gate's cached verdict + `status` ("ready"/"not_ready"); assessed once in the lifespan via a DEFERRED task (uvicorn binds :8000 only after startup — an inline assess probes its own socket before it exists and reports the app DOWN; the deferred task matches the operator's CLI gate). `?refresh=1` = cheap re-assessment in a worker thread (services, `/api/ps` residency, GPU clocks, config keys), prefix clause carried from boot — a poll never re-warms, never blocks the loop. Suite 37/37 at commit `c8a31b6`; **46/46 measured 2026-09-21** (this row said "now 40/40" — stale; `test_doc_truth.py --run` caught the drift and the US-007 status line was corrected to match). Live: `/ready` → `ready:true`, 4/4 clauses; harness `--readiness-url` → "ready (readiness endpoint)", assumed-banner gone. plan-state :159/:167/:174 fixed; US-007 story DoD note + T-17 row updated (surface exists; PO ruling queued in Phase 4a item 6). |
| A5 (14B + embed lease) | DONE, committed `b9d3da7` | 14B released: VRAM used 14,796 → 5,528 MiB. Post-reboot return root-caused: `test_us006_residency.py` defaulted to the literal `qwen2.5:14b` without `.env`; doc-truth runs suites bare → each run loaded + pinned 9 GB of orphan. Fixed: suite resolves the serving model (env → .env → app default) and drops its `num_ctx=2048` (spawned a second Ollama instance). Embed lease held on all 3 paths: ERC MCP `OllamaEmbeddingClient` keep_alive (`EMBED_KEEP_ALIVE`, default -1 — **uncommitted in ERC repo, flagged to user**), chromadb fallback `_HeldEmbeddingFunction`, langchain legacy `keep_alive=KEEP_ALIVE`. Gate `check_embed_residency()` reads `/api/ps`, reported non-blocking; suite 40/40. Live: retrieval → `nomic-embed-text` resident expires 2318; `/ready` reports `embed.resident:true`. |
| **Phase 1.2 — US-011 TAC-1** | **DONE** | The per-read sweep with a reasoned dynamic allowlist (`16202b1`), buckets A–D: managed→`Settings`, dynamic allowlist, `_env*` helper shapes, engine-level `OLLAMA_*` carve-out (`132efa5`). Closed the import-order trap (a value's resolution depended on *import order*) — `5fd363d` proves it, `9951522` records it as TAC-1's evidence. Also: one tunnel-host resolver instead of four (`62222e4`), one home for the DB target (`26dd13d`), an env **WRITE** was being counted as a read (`227cac8`). Commits `08139a3` … `86065de`. |
| **Phase 1.3 — A3 breaker + T-11** | **DONE — and it exposed that the breaker gated nothing** | `rag._use_mcp()` had **no caller in `app/`** (`git log -S` shows it only in the commit that *defined* it), so during an outage every turn called the dead service at the full 6.0 s timeout, `claim_probe()` never ran, and `breaker_probes` could only read 0. **`TAC-4`'s "bounded per window" was untrue as wired.** All three primary call sites now route through `rag_mcp.admit_primary()` (`0ac0a16`); T-11 trace fields driven (`d278b44`). Non-vacuity: reverting one file reports *"10 trips for 10 turns"*; with the fix, 1. |
| **US-011 TAC-6 + T-15** | **DONE — two more "set ≠ live" finds** | TAC-6 provenance was **a constant**, and a passing check was **enforcing** it (it asserted `all(s.startswith("authoritative"))` — true only because the value was constant). The report is now 122 rows with computed provenance, surfacing three real divergences (`27b34bd`). `ConfigurationError` was specified in the LLD since the story was written and **never existed**: 46 inline `int(_env(...))` sites raised `ValueError` naming the *value*, not the *key*. Now `_env_int`/`_env_float` name the key; refactor proven behaviour-preserving against a **110-field resolved-`Settings` snapshot**, which alone caught 6 fields silently changing type `float → int` (`2d21eba`). |
| **Phase 2.1 — US-005 / US-004 streaming** | **BUILT, both behind flags, default-OFF** | US-005 streaming TTS (`43a3fb9`): first-audio p50 2,250 ms, over-cap 13%. US-004 streaming LLM (`96f3407`): p50 1,625, p90 2,015, over-cap **6%** — the largest measured cap move in the workstream. Its **adoption-blocking N=2 freeze was root-caused**: ClauseCutter re-matching its own boundary forever (`3a1a101`). Both blocked on `DG-03`, not on engineering. |
| **Phase 6 — `run_all_gates.py`** | **BUILT** (`f005913`) | 22 gates in the plan's order, one scoreboard, exit 0 iff all green. It deliberately does **not** suppress known failures: the `KNOWN` registry only annotates, and only on an **exact** count match, so a known red that worsens stops being known. |
| **Gate repairs (2026-09-21)** | **DONE** (`f005913`) | Four gates were red or green for a reason unrelated to their claim — none visible by reading code. US-005's suite seeded the *shared* cache while ambient `TTS_CACHE_SCOPE=per_call` sent the lookup elsewhere (now 27/27 under **both** scopes); US-018 asserted a heading the C5 `meridian_kb__v1` rebuild renamed (21/1 → **22/22**); `test_task9_requirements.py` asserted "no space in a requirement", which PEP 508 markers violate, so it failed while `requirements.txt` was **correct**; US-007's claim said 40/40 for a suite running 46/46. |

## Exact next actions (in order)

**Items 1–6 are history — all done. The live queue starts at 7.**

1. ~~Fix the A2 self-test join bug~~ **DONE** — `obs(sid=…)` applied, self-test `a2_*` 4/4 True.
2. ~~Commit A2~~ **DONE** — `102a04e`.
3. ~~Phase 0.4 — WebSocket 1011 keepalive diagnosis~~ **DONE** — root-caused: pre-`7c19426`
   loop-blocking cold loads; receive-bound theory refuted by `_ws1011_probe.py`; harness
   gained `--ws-ping-interval/--ws-ping-timeout`, pings default OFF for cold sessions;
   cold N=2 `T025002Z` completed with no 1011.
4. ~~Commit the Phase 0.4 harness change~~ **DONE** — `b4ca40a`.
5. ~~Phase 1.1 /health + /ready~~ **DONE** — committed `c8a31b6`. (This item read "commit
   pending"; it was already committed when the line was written.)
6. ~~Phase 1.2 US-011 TAC-1 · Phase 1.3 A3 breaker endpoint · Phase 4a PO decisions ·
   Phase 6 `run_all_gates.py`~~ **ALL DONE** — see the delta at the top. **Phase 2 was
   also started**: US-004/US-005 streaming are built behind flags.

### The live queue

7. **Phase 5.1 — traceability.** The one gate still red: `test_ac_traceability.py`
   **3 passed / 1 failed** — *"no story claiming IMPLEMENTED leaves an AC or TAC
   untraced — **7 story(ies)**"*. B1 tagging sweep (~56 ids), B2 cite AC/TAC ids in
   `test_brd15_rollback.py`, B3 the untested ids, B4 retire/cover. **Pure desk work —
   no stack needed.** This is the highest-value item that is not blocked on anything.
8. **Phase 2.3 — qualifying runs.** Needs a **quiet box**, and a re-plan: the stack moved
   to `meridian_kb__v1` and the C3/C5 RAG work landed, so the retrieval leg those runs
   would measure is not the one the predictions were made against. Bar: two consecutive
   ≥100-turn warm N=2 runs at **zero** over-cap. Best ever is **8 breaches in 120 turns.
   Read `doc/perf/PERFORMANCE_RESUME_2026-09-21.md` §3 first** — it re-ranks this phase,
   and its top item is a *measurement*, not a config change.
9. **Phase 3 — story closures 3.1→3.9.** Tally: **36/147 DoD boxes.** Several are desk
   work (3.2, 3.8, 3.9); the load/soak halves need the same quiet box.
10. **Phase 4b — US-003 ground truths** (user as PO; signoff packets, 15 intents; **137 of
    161 unapproved**). Blocks every behaviour-changing change. **No agent can do this.**
11. **Phase 5.2–5.4** — doc truth, status-doc reconciliation, `PII` on `/api/calls/live`.
12. **`US-011` box 3 — the TAC-8 no-regression load pair** (~2–4 h). The "before" half
    means checking out the pre-migration revision and running the same load against it.
    Same quiet box.

**Stack:** Ollama `:11434` is normally the only thing up; app `:8000`, MCP `:8010`, CRM
`:8098` and Streamlit `:8501/8502` are **down**. Start via `start_services.ps1` when
wanted — note it also creates a Cloudflare tunnel and repoints the **live Twilio
webhooks**, so it has outward-facing side effects. **Use `127.0.0.1`, never `localhost`**
(IPv6 stalls ~2,066 ms on this box). Full detail: the approved plan file listed above.

## Known flakiness / caveats

- `--self-test` framing audit is nondeterministic on this box (9.7 ms max deviation
  → PASS; 29.0 ms → FAIL). Pre-existing host noise = US-002 TAC-1 work (Phase 3.1:
  activate `_TimerResolution` in live runs, `--pacer-wake-lead-ms` 6→~16 ms).
  `run_all_gates.py` therefore uses `--self-test-quick` by default.
- **Memory on this box is short.** A boot-verify FastAPI instance was killed by Claude
  Code's memory-pressure reaper while the session sat idle (2026-09-19), and a
  `run_all_gates.py` board run was killed the same way (2026-09-21). Bring the stack up
  deliberately; don't leave it resident while idle.
- **`run_all_gates.py` can print an impossible duration.** One board run reported
  **19,909 s** for a gate that measures **113 s**. `subprocess.run` enforces the gate's
  own `--timeout` on the same clock, so exceeding it is impossible for a gate that
  returned normally — a suspend/resume between the two `monotonic()` reads is the only
  explanation. The column now prints `?` and a footnote rather than a number that reads
  as measured. **The verdict is unaffected.**
- **`pytest tests/` carries 5 known failures** — `test_offer_readiness.py` ×5, which use
  `@pytest.mark.asyncio` while **`pytest-asyncio` is installed nowhere and declared
  nowhere in the repo**. `requirements.txt:1` pins "exact versions from the verified-working
  environment", so installing into it was left to the user, not done silently.
  (`test_task9_requirements.py` ×1 was the sixth; fixed 2026-09-21 — it asserted *"no
  space in a requirement"*, which PEP 508 markers violate, so it failed while
  `requirements.txt` was **correct**.)
- **`.env.example:26` still teaches the `localhost` trap** — one-word fix, not applied.
  A known one-word defect that no gate catches.
- `salesforceAPISchema` has uncommitted modifications (not ours — salesforce
  workstream; leave untouched). Untracked `content/sample_data/Meridian_data/` — not ours.
  Also **pre-existing and uncommitted**: `app/crm/*`, `app/leads/*`, `doc/salesforce/*`,
  `scripts/verify_gate7_status.py`, `tests/test_crm_*`, and untracked `doc/perf/runs/*.json`.
  **Stage by explicit path only — never `git add -A`.**
- **ERC repo changes are flagged, never committed** without the user (checkpoint-commit
  policy is universityDemo-only). `EMBED_KEEP_ALIVE` in the ERC repo is still uncommitted.
- Git email in repo = `pradeepdgenai@users.noreply.github.com` (GH007 convention). All
  commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Commit-policy: checkpoint commits only, secrets scan first.
- **Do not edit the working tree while a background pytest run is in flight** — a
  `git stash` mid-run invalidated a suite result once. Kill and re-run.

## Scoreboard

**Do not trust a commit list written in a document — it goes stale the moment anything
is committed.** This section used to name nine commits and claimed the US-004 freeze was
an open root-cause item months after it had been fixed. Ask git instead:

```bash
git log --oneline 13d204e..HEAD    # 13d204e = HEAD when the 2026-09-21 session opened
```

**What is worth writing down is the gate state, because it is a measurement:**

| Gate | Result (measured 2026-09-21) |
|---|---|
| `test_ac_traceability.py` | **3 passed / 1 failed** — 7 stories claim IMPLEMENTED with untraced ACs/TACs |
| `test_doc_truth.py --run` | 7 / 7 |
| `test_doc_citations.py` | 5 / 5 |
| `test_brd15_rollback.py` | 42 / 42 |
| `test_event_loop_hygiene.py` | 3 / 3 |
| `test_endpointing.py` | 18 / 18 |
| story suites (`test_us*`) | **all green** — us001 24, us004 19, us005 27, us006 12, us007 46, us008 5, us011 55 + 13, us012 36, us013 71, us016 56, us017 53, us018 14 + 22 |
| `pytest tests/` | **563 passed / 6 failed** measured before the `test_task9` fix; **564 / 5** by arithmetic from the two affected files re-run (the full suite was not re-run — do not quote 564 as measured) |

**Run it yourself:** `.venv/Scripts/python.exe doc/perf/tools/run_all_gates.py`
(22 gates, one board, exit 0 iff all green). **DoD tally: 36/147 boxes.**
