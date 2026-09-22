> **Lens:** Architect (diagnosis) / TPO (sequencing) · **Engagement:** Brownfield — `D:\project\universityDemo`
> **Written:** 2026-09-19 · **Basis:** live measurement on the box (`nvidia-smi`, `ollama ps`, Ollama server log, a direct 40-call probe of the MCP service) + two independent code-reading passes over `app/` and the run store.

# Unblock Plan — from 8/18 implemented to 18/18 closable

**Two plans, because they have different owners.**

- **Plan A — Unblock** removes what is *stopping* work. Six items. Five are measurement or
  decision, not code. **The critical path runs through the Product Owner, not engineering.**
- **Plan B — Fix** is the remaining engineering work, sequenced so nothing is built on a
  number that is about to change.

**The single finding that shapes both:** the program's headline number is a superseded
configuration, and the latency cap cannot be reached by any currently-unblocked work. Both
statements are measured below.

---

## 0. What was measured for this plan (all live, 2026-09-19)

| Evidence | Value |
|---|---|
| GPU | RTX 5060 Ti, **16,311 MiB**; used 12,510 MiB; free 3,543 MiB |
| Resident LLMs | `qwen2.5:14b` **8.44 GiB** (ctx 2048) + `llama3.2:3b` 2.89 GiB (ctx 8192), both `keep_alive=-1` → **Forever** |
| Engine config | `OLLAMA_NUM_PARALLEL:1` · `OLLAMA_FLASH_ATTENTION:false` · `OLLAMA_KV_CACHE_TYPE:` empty |
| Model load | `llama-server started in 39.47 seconds` |
| MCP service probe | 40 back-to-back calls: p50 **788 ms**, p95 **889 ms**, max **3,355 ms** (the cold first call), **0/40 reached the 6.0 s ceiling** |
| Current config (3B), last 3 h, n=1,195 turns | first-audio **p50 2,219 · p90 3,218 · p95 7,641** ms; **154/1,195 (13%) over the 3,000 ms cap** |
| Composition of those 154 | **68 are the DEF-001 timeout (retrieval 5.8–7.2 s); 86 are not** (their retrieval p50 is a healthy 766 ms) |

---

## 1. Three facts the premise gets wrong

Stated first because two of them change the plan.

**1.1 — `plan-state.md:169`'s headline is a superseded configuration.**
It reads *"BASELINE ESTABLISHED — CAP NOT MET: p50 3,609 ms · p95 6,044 ms · n=108, 96 of 108 turns exceed the 3,000 ms cap."* That is run `20260919T112547Z` = **04:25 local**, and the model in force then was **`qwen2.5:14b`** (`.env` changed to `llama3.2:3b` at 13:07; `.env.bak.20260919T041845` still reads `qwen2.5:14b`; the Ollama log shows a live 14B load at 11:12:52). Five runs later, on the shipped configuration, the same harness measures **p50 2,672–2,781 ms with 58–77 of 198 over the cap.** The register reports the old model's number as the programme's baseline, and the summary line above it (`:174`, "4/18 DONE") disagrees with its own table (`:43`, 8/18).

**1.2 — DEF-001's C3 is refuted by the data, in both directions.**
C3 says the 8.1 s p95 *is* this timeout. Two independent decompositions disagree:

| Source | Breaches attributable to the timeout | Not the timeout |
|---|---:|---:|
| Architect pass, four runs on the harness clock | 12/62, 10/66, 16/77, 13/58 | **78–85%** |
| This plan, current config, app clock, n=1,195 | 68/154 | **56%** |

The timeout is ~6% of turns. It *occupies the p95 slot* — 7–16 timeouts per 100 turns straddle the 95th percentile — but it does not cause most breaches. **DEF-001 correctly diagnoses a mixture artifact in C7 and then commits one in the attribution.** The counterfactual is decisive: replacing every timeout's retrieval with the median successful retrieval (757 ms) moves the harness clock **62 → 53** breaches and the app clock **21 → 9**. It halves the p95 and leaves the cap unmet.

**1.3 — S2 is not the safe one-setting change it is described as.**
H3 ("is the 6.0 s ceiling ever reached by a legitimately slow but successful query?") was recorded as unmeasured. It is measurable from existing traces, and **the answer is yes**: successful retrievals reach **2,625 ms** in `T203610Z`, and a direct probe of the service today returned **3,355 ms** on a cold first call. A 2.0 s leash would convert those into fallbacks. Two further specifics DEF-001 gets wrong: the fallback is not reliably 600 ms (observed cluster values include 7,484 / 7,656 / 8,140 ms — the fallback has its own contention tail, so S2's "worst case 2.6 s" is really 2.6–4.1 s), and shortening the leash raises the fallback rate, which raises embed load on the same saturated Ollama, which raises the timeout rate.

**C4 is half-stale.** "Zero log lines mention a timeout, a breaker opening, or a fallback" is false today — `app/rag.py:150-155` emits the warning and it fires **219 times** in `logs/voice_assistant.log`. What remains true is the part that matters: `mcp_rag_status()` is on **no endpoint**.

---

## 2. Reconciliation with the external remediation plan

A production-grade remediation plan was supplied for this PR. **Most of its Track-1 and Track-2 items are already built in this repository**, which changes what is left to do:

| Item in that plan | Actual state here |
|---|---|
| "Add a `test_ac_traceability.py` pattern… fail CI for untraced criteria" | **Built and failing honestly** — 34 AC / 91 TAC / 289 LLD untraced, 7 stories. The gate is not the gap; the ids are. |
| "Add a single rollback test harness `test_brd15_rollback.py`" | **Built, 42/42, seven stories demonstrated** |
| "Establish a single source of truth… status machine… CI fails on drift" | **Partly built** — `test_doc_truth.py` (6/0) and `test_doc_citations.py` (5/0) check *story files*. **No gate covers `plan-state.md`**, which is why it carries six stale figures. |
| "Build one canonical config resolver… remove direct env reads" | **Built** (US-011, 17/17) for `.env`-managed keys. **Blind spot:** engine-level keys (`OLLAMA_NUM_PARALLEL`, `FLASH_ATTENTION`, `KV_CACHE_TYPE`) are set **nowhere** — they appear only in prose. |
| "Cache key must include voice, speed, language, model version" | **Built** (US-012) — the voice/speed omission was found and fixed |
| "Introduce work classes / weighted scheduler / starvation prevention" | **Built** (US-017, 53/53) |
| "Admission lease pattern with stale cleanup" | **Built** (US-016, 56/56) |
| "Circuit breaker: open / half-open probe / close" | **Built** (US-013, 46/46) — but its state is on no endpoint |
| "US-007 boot readiness false positive" | **Not the finding.** The gate exits 0 (30/30). The open items are the call-arrival observation (D1) and a commit-level rollback (D3). |
| "Retrieval fallback must be observed, not assumed" | **Correct and still open** — the logging exists; the *endpoint* does not |
| Its recommended order (US-007 → US-011 → US-016 → US-017 → …) | **Does not match the blocker structure.** US-007/011/016/017 are already implemented; US-004/005/009/010 are blocked on a human, and the measured critical path is the measurement layer (§3). |

What that plan gets right and this one adopts: no story is done without code + explicit AC/TAC mapping + passing tests + evidence + rollback proof + synchronised docs; prefer additive instrumentation; do not claim done from inspection.

---

## 3. PLAN A — Unblock

Ordered by leverage ÷ (effort × risk). **Items 1–5 need no golden set and no PO decision, and can run today.**

### A1 · Fix the run store's discard rule — *the most dangerous hole* (~30 min, measurement)

Runs `T181605Z` and `T182610Z` are the **only harness-clean N=2 runs in the store** (`harness_fault: false`, `tac1_holds: true`, 0 late frames) — and they contain `first_audio_n: 0`, `dropped_turns: 200`, `partial: true`, **`discarded: false`**. Both sessions died on `ConnectionClosedError: 1011 keepalive ping timeout`. The discard rule keys only on the turn cap (`load_harness.py:1645`), so anyone enumerating runs for `discarded != true` gets **two empty runs as the only valid evidence**.

**Fix:** `discarded = True` unless `first_audio_n > 0 and not partial`. Re-run that condition and let it fail loudly.
**Unblocks:** US-002 TAC-1/TAC-9; makes every later number trustworthy.

### A2 · Disambiguate the clock, then decide `cap_basis` (~2 h eng + one PO line)

Two clocks are in play and they disagree 3×. `load_harness.py:1596-1601` measures from the last speech frame and includes the 600 ms endpoint wait; `app/perf_trace.py:28-31` says `vad_end` is explicitly **not** caller-speech-end. Joining 800 turns across four runs: harness − app = **p50 exactly 500.0 ms, min 359, max 984**. Same turns, same run: **62 breaches on the harness clock, 21 on the app clock.** Every published `turns_over_3000ms` is the harness number.

**Fix:** emit both, plus `endpointing_ms`, per turn; record `cap_basis` once in writing.
**Unblocks:** `BRD-02`; settles what "the cap" even means.

### A3 · Surface the breaker (~15 min, measurement)

`mcp_rag_status()` carries `last_error`, `breaker_opens` and `session_ok` and is called only inside `app/rag.py`. `/api/perf/policy` (`app/main.py:2702-2723`) returns admission and work priority only — **US-013 built a three-state breaker no operator can read.**
**Unblocks:** DEF-001 S3 (independently correct regardless of S1/S2); US-013.

### A4 · Re-baseline and restate the headline (~1 h, measurement)

Re-run N=2 on the shipped configuration and replace `plan-state.md:169` and `:174`. Current measured: **p50 2,219–2,781 ms, 13% over cap** — not "96 of 108 over".
**Unblocks:** `BRD-02`; removes the false premise from every downstream decision.

### A5 · Reclaim the waste, and give the embed model a lease (~10 min, config change — Class A)

`qwen2.5:14b` is **8.44 GiB resident, requested by no code path and no config key** (verified repo-wide: the only two references are comments). It was pinned by `keep_alive=-1` before the model swap and never released. Meanwhile `nomic-embed-text` — the model **every retrieval depends on** — is the only one with a short lease (**4 minutes**), and the tracer's `residency_lapse` flag covers the chat model only, so its lapse is invisible.

**Fix:** release the 14B runner; give the embed model an explicit `keep_alive`; assert both in the readiness gate.
**Expected effect on latency: probably none** — VRAM is not the binding constraint. Do it because it removes 8.44 GiB of false ceiling, a `BRD-11` exposure, and an invisible cold-start on the retrieval path. **If p50 does not move, that is the confirmation, not a failure.**
**Unblocks:** US-006 T-4; `BRD-11`/`BRD-17`.

### A6 · Answer H3 properly, then decide DEF-001 (~30 min measurement, then a decision)

The probe above answers the *idle* case. The remaining unknown is the service's distribution **under N=2**, and the 765 ms retrieval term is still undecomposed from the app — a bare probe returns in 10 ms, so the 765 ms is the embedding round-trip to a second Ollama model, not the handler.
**Fix:** wrap the single `tools/call` in `app/rag_mcp.py:116-131` with a timer and a histogram; re-measure under load.
**Then:** choose S1 (root cause) vs S2 (leash) **against measured H3**, expecting neither to meet the cap (§1.2).

### A7 · The human critical path — **this is what actually blocks the cap** (PO)

**No combination of currently-unblocked work reaches the 3,000 ms cap.** The two levers that would are `US-004` (streaming LLM) and `US-005` (streaming TTS) — TTS synthesis alone is ~750 ms of the median — and **both are `DG-03`-BLOCKED, as is US-009 and US-010.** Everything else is near its floor: STT 0 ms (speculative on 200/200 turns), LLM queue 52 ms, prefill 201 ms, generation 367 ms, endpointing 600 ms (a `BRD-04` policy value, not an engineering one).

**So the programme's latency blocker is a sign-off, not a sprint.**

| Ask | State | Cost |
|---|---|---|
| `DG-03` intents | **9 of 161 cases approved** (Out-of-scope). 128 pending: 40 bulk-approvable, 8 one sitting, **80 policy decisions across 15 intents** | `eval/signoff_packet.py --approve-intent` |
| `US-014` | Class C, needs PO sign-off | one decision |
| **D1** — build a call-time readiness surface, or re-word four claims | unsatisfiable today (verified: no `/health` or `/ready` route in `app/main.py`) | ~1 day, or one re-word |
| D3 — demonstrate US-007's commit-level revert | small | ~1 h |

> **Correction to the remediation plan:** it says the 89 policy cases cluster into "**ten intents**… the real ask is ~10 decisions". The tool reports **16 intents** (Out-of-scope approved; **15 left**). The 89 is right; the "ten" is not. This is the number the whole unblock is priced on.

---

## 4. PLAN B — Fix (the remaining engineering work)

Only after A1–A4, so nothing is built on a number that is about to move.

| # | Work | Effort | Closes |
|---|---|---|---|
| **B1** | **The tagging sweep** — 56 ids are *untagged* (the assertion exists, the id is absent): US-013 16, US-016 17, US-012 12, US-017 9, US-006 T-9/AC-1. One pass, no new tests, and it removes **false greens** as a side effect | ~1 day | ~56 of the 91 TAC/34 AC |
| **B2** | **Cite ids in `test_brd15_rollback.py`** — it is an evidence file for five stories and contains **zero** `AC-/TAC-/T-n` tokens. Five comment lines make genuinely demonstrated reverts visible | ~15 min | 8 ids, cheapest structural fix in the set |
| **B3** | **The 62 genuinely untested ids.** Load-bearing first: **US-017 TAC-5/T-16** (measured failing while the status line leads "ACs verified"); **US-006 AC-2/AC-4/TAC-1/2/5** (four ACs never measured while the DoD box "All ACs pass" is ticked); **US-012 AC-1/TAC-1/T-18** (the verdict *is* the deliverable, and the AC-4 fallback was adopted on an underpowered sample); **US-016 T-10/TAC-6** | ~3–4 days | 62 ids |
| **B4** | **The 6 retire ids** — US-006 T-11/T-16 and US-007 T-17 demand residency/clock *at the instant a call arrives*, which no surface can answer (the D1 decision); US-007 TAC-8 is commit-level; US-017 TAC-9 is unassertable | with A7/D1 | 6 ids |
| **B5** | **The load/soak gates** — US-001's 30-min N=2 soak (no ~1800 s artifact exists in the store), US-006's TAC-1/2/5, US-008's three consecutive N=2 runs, US-012's ≥100 turns/caller × 3 | ~1 day + a **quiet box** | the latency DoD boxes |
| **B6** | **US-007's commit-level revert demonstration** and **US-008's cross-repo half** | ~2 h | `BRD-15` complete |
| **B7** | **The blocked stories** — US-004, US-005 (the actual cap levers), US-009, US-010, US-018's adoption half | after `DG-03` | 5 stories |
| **B8** | **Extend the C1 gate to cover `plan-state.md`** — the one document no gate checks, which is exactly why it carries six stale figures | ~2 h | prevents recurrence |

**Also worth doing while in there:** every artifact carries `harness_sha256: f7b78897…`; the file today is `166f1c01…`. `T214401Z`'s embedded text says "US-007 is NOT STARTED" while `load_harness.py:1886` now says it is implemented. Provenance is recorded and never checked — a 30-minute assertion closes it.

---

## 5. What no plan can fix

- **The box is not quiet.** `harness_fault: true` on every N=2 run; *a run with `harness_fault: false` **and** `n > 0` has never happened.* Until one does, every N=2 absolute number carries the caveat.
- **The cap needs `DG-03`.** US-004 and US-005 are the only remaining levers on the median, and they change what the model says. That ordering is not negotiable by engineering.
- **`DG-05` is now provably divergent**, not merely at risk: `.machine_profile.json` (`updated_at 2026-08-21`) says `OLLAMA_MODEL: qwen2.5:14b` / `FASTAPI_WORKERS: "4"` against `.env:210` `llama3.2:3b` and one process.
- **`DEF-001` cannot be closed by a threshold.** Fixing it halves the p95 and leaves 26% of turns over the cap.

---

## 6. Today's five, in order

1. **A1** — discard rule (30 min). Everything downstream is measured by this rig.
2. **A3** — expose `mcp_rag_status()` (15 min). Independent of every open question.
3. **A4** — re-baseline and restate the headline (1 h). The programme is reporting a superseded number upward.
4. **A5** — release the orphaned 14B, lease the embed model (10 min). Class A.
5. **A7** — put the 15 intents in front of the PO. **It is the only item that unblocks the cap.**
