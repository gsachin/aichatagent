# Session handoff — 2026-09-19 (late)

> **Purpose:** this file is the resume point. If the machine loses power, **read this file first**,
> then `doc/sdlc/unblock-plan.md`, then **`doc/sdlc/sdlc-completion-drive-status.md`** (the
> current drive's complete prompt + status + next actions).
> **Written by:** Claude Code session on branch `perf/us007-boot-readiness` (HEAD `5f1d6a0`).
> **Update this file after every completed item.**

---

## 1. What the user asked for, in order

1. "report the comprehensive status table, explain the status in simple english" → delivered.
2. "can you make a plan to unblock all userstories, its ACs and DOD then make a plan to fix,
   grab a Production grade AI architect (exp in software, its scaling, MLOps … hands on)"
   → delivered as `doc/sdlc/unblock-plan.md` (Plan A = unblock, Plan B = fix).
3. "start on A1, A3, A5. Make sure until A1, A2 and A5 all AC & DOD should completed before
   moving to next. also store conversation in some file as power … may shutdown in a mins"
   → **this file** + the progress table in §5.

The user's standing instruction: **each item's acceptance criteria and definition of done must be
fully complete before moving to the next item.** No half-finished items.

---

## 2. Progress tracker (update after each item)

| Item | What | Status | Verified by |
|---|---|---|---|
| **S0** | Write this handoff file | **DONE** | file exists |
| **A1** | Run-store discard rule: discard when `first_audio_n == 0` or `partial` | **DONE** | commit `216af99`; self-test contract `a1_*` 5/5 (incl. provenance `a1_fresh_summary_records_current_harness_hash`); 9 pre-rule empty/partial artifacts re-evaluated → `__DISCARDED`; store now has 0 non-discarded empty/partial runs |
| **A3** | Surface `mcp_rag_status()` on `/api/perf/policy` | **DONE** | `GET /api/perf/policy` now returns `retrieval` beside `admission` + `work_priority` (additive; keys unchanged). `mcp_rag_status()` is a pure in-memory snapshot — no HTTP, no blocking call, so the endpoint returns 200 with a stated status even when MCP is unreachable (session_ok/last_error/breaker_state render the outage). No credential value (loopback URL only). Suite 51/51 (+5 A3 checks); live-verified: `breaker_state: closed, session_ok: true`. US-013 story status updated |
| **A5** | Release orphaned `qwen2.5:14b`; give `nomic-embed-text` a real lease | **DONE** | 14B released live (VRAM used 14,796 → 5,528 MiB; `/api/ps` clean). Root cause of its post-reboot return found: `test_us006_residency.py` fell back to the literal `"qwen2.5:14b"` when run without `.env` (doc-truth runs suites bare) — fixed to resolve the serving model. Embed lease held on all three paths: ERC MCP client (`EMBED_KEEP_ALIVE`, default -1 — **uncommitted in the ERC repo, flagged to user**), chromadb fallback subclass, langchain legacy `keep_alive=KEEP_ALIVE`. Gate asserts embed residency via `/api/ps` (reported, never blocking — cold embed degrades, not fails); suite 40/40 incl. A5 checks. Live: after one retrieval, `nomic-embed-text` resident with 2318 expiry; `/ready?refresh=1` reports `embed.resident: true` |
| **A2** | Emit both clocks + `endpointing_ms`; record `cap_basis` | **DONE** | commit `102a04e`; self-test `a2_*` 4/4 True (join bug fixed via `obs(sid=…)`); live dual-clock re-run deferred to Phase 2 per drive-status |
| **P0.4** | WebSocket 1011 keepalive diagnosis + fix | **DONE** | root cause = pre-`7c19426` loop-blocking cold loads (greeting Kokoro on the loop); to_thread + lifespan pre-warm landed 18:03 IST. Receive-bound theory tested and REFUTED (`_ws1011_probe.py`: protocol-layer pongs answered at 0 ms mid-turn, HTTP max 906 ms). Harness fix: `--ws-ping-interval/--ws-ping-timeout`, default disabled for cold sessions, basis recorded; 4/4 `ws_ping_*` self-test checks True. Cold N=2 verifications: `T024150Z` (2 turns/session) and `T025002Z` (1 turn/session, new code) — both complete, no 1011 |
| **P1.1** | US-007 `/health` + `/ready` (call-time readiness surface) | **DONE** | `GET /health` = liveness; `GET /ready` = boot gate's cached verdict + `status` (deferred assess in lifespan — uvicorn binds after startup); `?refresh=1` = cheap re-assessment in a thread, prefix clause carried, never re-warms. Suite 37/37 (+7 checks). Live: `/ready` → `ready:true` 4/4 clauses; harness `--readiness-url` says "ready (readiness endpoint)" (assumed-banner gone). plan-state :159/:167/:174 fixed. Call-arrival PO ruling queued (Phase 4a item 6) |

---

## 3. Plan A / Plan B — the agreed plan

Full text: **`doc/sdlc/unblock-plan.md`** (written this session, uncommitted).

**Plan A — Unblock** (ordered by leverage ÷ effort × risk):

- **A1** Run-store discard rule. Runs `T181605Z` / `T182610Z` are the only harness-clean N=2 runs
  in the store (`harness_fault: false`, `tac1_holds: true`) and contain `first_audio_n: 0`,
  `dropped_turns: 200`, `partial: true`, **`discarded: false`**. The rule keys only on the turn
  cap (`doc/perf/tools/load_harness.py:1645`). Anyone enumerating runs for `discarded != true`
  gets two empty runs as the only valid evidence.
- **A2** Disambiguate the clock. Harness clock (`load_harness.py:1596-1601`, from last speech
  frame, includes the 600 ms endpoint wait) vs app clock (`app/perf_trace.py:28-31`, `vad_end` is
  explicitly NOT caller-speech-end). Joined over 800 turns: harness − app = p50 **exactly 500.0 ms**.
  **Same turns: 62 breaches on the harness clock, 21 on the app clock.**
- **A3** Surface the breaker. `mcp_rag_status()` (`app/rag_mcp.py:454`) carries `last_error`,
  `breaker_opens`, `session_ok`, and is called only inside `app/rag.py:153,262`.
  `/api/perf/policy` (`app/main.py:2702-2723`) returns admission + work priority only.
- **A4** Re-baseline; restate `plan-state.md:169,174`.
- **A5** Reclaim VRAM waste + lease the embed model.
- **A6** Time the `tools/call` in `app/rag_mcp.py:116-131`; answer H3 under load.
- **A7** **The PO critical path** — 15 intents. The only item that unblocks the latency cap.

**Plan B — Fix:** B1 tagging sweep (56 ids) · B2 cite ids in `test_brd15_rollback.py` (zero tokens
today) · B3 the 62 untested ids · B4 the 6 retire ids · B5 load/soak gates (needs a quiet box) ·
B6 US-007 commit-level revert + US-008 cross-repo · B7 the DG-03-blocked stories · B8 extend the C1
gate to cover `plan-state.md`.

---

## 4. Measurements taken this session (all live, reproducible)

| Evidence | Value | How to reproduce |
|---|---|---|
| GPU | RTX 5060 Ti, **16,311 MiB**; used 12,510; free 3,543 | `nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv` |
| Resident LLMs | `qwen2.5:14b` **8.44 GiB** (ctx 2048) + `llama3.2:3b` 2.89 GiB (ctx 8192), both `Forever` | `curl -s 127.0.0.1:11434/api/ps` |
| Orphan | `qwen2.5:14b` is requested by **no code path and no config key** — only two comments mention it (`app/rag.py:38`, `app/hardware_profile.py:246`) | `grep -rn "qwen2.5:14b" app/` |
| Engine config | `OLLAMA_NUM_PARALLEL:1` · `OLLAMA_FLASH_ATTENTION:false` · `OLLAMA_KV_CACHE_TYPE:` empty | Ollama `server.log`, `msg="server config"` lines |
| Engine keys unmanaged | The three keys above appear **only in prose** — in no `.env`, script, or `.py` | repo-wide grep |
| Model load time | `llama-server started in 39.47 seconds` | server.log |
| MCP service probe | 40 back-to-back `retrieve_context`: p50 **788 ms**, p95 **889 ms**, max **3,355 ms** (cold first call), **0/40** reached the 6.0 s ceiling | see §6 script |
| Current config (3B), 3 h, n=1,195 | first-audio **p50 2,219 · p90 3,218 · p95 7,641** ms; **154 (13%) over cap** | §6 script |
| Breach composition | 68 = DEF-001 timeout; **86 (56%) are not** | §6 script |
| Stage deltas (over-cap vs under-cap, non-timeout) | retrieval +16 · llm +328 · **tts +618** · to_audio +15 | §6 script |

**Three premise corrections (the plan's core):**

1. **`plan-state.md:169`'s headline is a superseded configuration.** "p50 3,609 ms · 96 of 108 over"
   is run `20260919T112547Z` = **04:25 local**, when `.env` still said `qwen2.5:14b` (the 13:07
   edit changed it to `llama3.2:3b`; `.env.bak.20260919T041845` still reads `qwen2.5:14b`; the
   Ollama log shows a live 14B load at 11:12:52).
2. **`DEF-001` C3 is refuted.** Two independent decompositions: architect pass 78–85% of breaches
   are *not* the timeout; my own 56%. The timeout is ~6% of turns — it occupies the p95 slot, it
   does not cause most breaches. Counterfactual: fixing it moves the harness clock **62 → 53**.
3. **S2 is not a safe one-setting change.** H3 was recorded as unmeasured; it is measurable and the
   answer is **yes** — successful retrievals reach 2,625 ms in `T203610Z`, and the live probe hit
   **3,355 ms** on a cold call. The fallback also isn't reliably 600 ms (7,484 / 7,656 / 8,140 ms
   observed).

**Also found:** `DEF-001` C4 is half-stale — the fallback warning **does** fire (219 hits in
`logs/voice_assistant.log`); only the endpoint is missing. And `harness_sha256` in every artifact
is `f7b78897…` while the file today is `166f1c01…` — provenance is recorded and never checked.

**Reconciliation:** an external "production-grade remediation plan" was supplied. Most of its
Track-1/2 items are **already built here** (traceability gate, rollback harness, config resolver,
work classes, admission leases, breaker, cache key with voice/speed). The full reconciliation table
is in `doc/sdlc/unblock-plan.md` §2. **Its "~10 intents" figure is wrong — the tool reports 16
(15 left).**

---

## 5. Current repo state

- Branch `perf/us007-boot-readiness`, HEAD `c8a31b6` — **committed this session: `216af99` (A1 + run-store re-evaluation), `102a04e` (A2 dual clocks), `b4ca40a` (Phase 0.4 harness ping knobs), `c8a31b6` (Phase 1.1 /health + /ready).**
- New untracked: `doc/sdlc/unblock-plan.md`, `doc/sdlc/session-handoff-2026-09-19.md` (this file).
- `eval/rag_baseline.json` etc. were committed earlier by the D4 work.
- Live processes: Ollama `:11434`, app `:8000`, MCP `:8010`, CRM `:8098`, streamlit `:8501`/`:8502`.

## 6. Reproduction scripts used

Trace analysis (current config, cap composition, stage deltas) — run from the repo root with
`./.venv/Scripts/python.exe`:

```python
import json, datetime, statistics as st
rows=[json.loads(l) for l in open('logs/perf_turns.jsonl',encoding='utf-8') if l.strip()]
def ts(r):
    t=r.get('ts')
    if isinstance(t,(int,float)): return datetime.datetime.fromtimestamp(t/1000 if t>1e11 else t)
    return datetime.datetime.fromisoformat(str(t).replace('Z','+00:00')).astimezone()
cur=[r for r in rows if r.get('model_used')=='llama3.2:3b' and r.get('first_audio_sent_ms')]
cur=[r for r in cur if ts(r)>=max(ts(x) for x in cur)-datetime.timedelta(hours=3)]
TO=range(5800,7200)
fa=sorted(r['first_audio_sent_ms'] for r in cur)
p=lambda x: fa[min(len(fa)-1,int(len(fa)*x))]
over=[r for r in cur if r['first_audio_sent_ms']>3000]
print('n=%d p50=%.0f p90=%.0f p95=%.0f over=%d (%.0f%%)'%(
    len(cur),p(.5),p(.9),p(.95),len(over),100*len(over)/len(cur)))
print('timeout-band:',sum(1 for r in cur if (r.get('retrieval_ms') or 0) in TO))
```

MCP latency probe: `/tmp/mcp_probe.py` (initialize → 40 × `tools/call retrieve_context`).
Rebuild it from `app/rag_mcp.py:116-235` if `/tmp` is cleared.

---

## 7. What to do next

**Read this file, then `doc/sdlc/unblock-plan.md`, then continue per
`doc/sdlc/sdlc-completion-drive-status.md`.**

Status after this session (all committed, each item's AC/DoD complete):
A1 ✓ `216af99` · A2 ✓ `102a04e` · A3 ✓ `d176be3` · A5 ✓ `b9d3da7` · Phase 0.4 ✓
`b4ca40a` · Phase 1.1 ✓ `c8a31b6` · **Phase 4a ✓ `8ae8ff4` — all six PO rulings
recorded** (`doc/perf/decisions/2026-09-19-po-rulings-phase-4a.md`): cap keys on
the harness clock; US-004/005/009/010 build-approved behind flags; `/ready?
refresh=1` closes US-006 T-11/T-16 + US-007 T-17 + the US-007 call-arrival DoD
box (US-007 now DoD 6/8); US-014 = enable with echo-suppression; US-016 wording
approved.

**Next, in order:**

1. **US-004 freeze root-cause (the Phase 2 blocker).** Live evidence: run
   `T043413Z` (LLM+TTS streams on, N=2 warm): 35 streamed turns clean
   (first-audio p50 1,625 · p90 2,015 · over-cap 6% · TTFT p50 281 ms), then
   the event loop froze — the log stops at the first clause-TTS of the two
   concurrent streams (`logs/fastapi_ph23.log` / `voice_assistant.log` at
   21:36:12 PDT; both sessions then died on 1011). Prime suspect: misaki /
   espeak phonemization running ON the loop inside `kokoro.create_stream`
   (`_speak_clause` → `synthesise_stream`), racy under two concurrent streams.
   Fix candidates: phonemize in a worker thread, or serialize phonemization
   with a lock, then re-run the dual-stream N=2 and re-measure.
2. Phase 2.3 qualifying runs once the freeze is fixed (cold N=2 smoke → warm
   N=2 ≥100 turns ×2 consecutive, 0 over-cap, harness-clean).
3. Phase 1.2 (US-011 TAC-1 migration) + Phase 1.3 (T-11 trace fields), then
   Phase 3 story closures (3.3 US-008 incl. ERC `asyncio.to_thread` commits —
   **ERC repo changes are flagged, never committed without the user**) → Phase
   4b (US-003 signoff packets, 15 intents) → Phase 5 → Phase 6.

**Uncommitted by design:** the ERC MCP embed-lease change in
`D:\project\enterprise-rag-core` (`enterprise_rag/hybrid.py` — `EMBED_KEEP_ALIVE`,
default -1); the MCP service is running it live. Flag to the user for a commit
decision there.

**Stack state:** Ollama :11434, app :8000 (with /health+/ready + A3 policy
surface), MCP :8010 running the embed-lease code. CRM :8098 and streamlit are
down — start via start_services.ps1 when needed. Use 127.0.0.1, never localhost.
