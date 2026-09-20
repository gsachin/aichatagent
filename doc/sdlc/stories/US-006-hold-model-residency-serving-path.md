> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-006 — Hold model residency on the serving path [Lens: PO]

- **Status:** **IMPLEMENTED - ACs verified; BRD-15 demonstrated** · `test_us006_residency.py` 12/12 · DoD 5/8 · LLD mapping and the latency TACs are open

- **Story:** As a **caller who dials after the service has been idle**, I want **the model to still be in VRAM when my call arrives**, so that **I am not the person who waits 33 seconds of silence for a load that the operator already paid for at boot**.
- **Business value:** The cold load is the single largest stall in the system: a measured **32,919 ms** to load and **67,349 ms** to first token. It is long enough that a caller hangs up. `BRD-17` says the prompt prefix shall be resident before the first call is accepted; `BRD-03` says the first turn of a call shall fall within the same p95 as a warm turn.
- **Priority:** **Must** — TPO ordering note: `REC-11` is central and it *narrows* the work. The fix is **not** "add a preload" — pre-warm code already exists and runs. The fix is on the **serving path**, and it must land before any latency baseline is frozen, because a residency lapse contaminates every p95 the program reports.

## Acceptance Criteria [Lens: PO]

**AC-1.** Residency survives the first serving call. This is `REC-11`'s defect.

```gherkin
Scenario: The first real chat after boot no longer discards the pre-warm
  Given the stack has booted and the model is resident
  When the first real caller turn is served
  Then the model is still held with an explicit keep-alive after that turn completes
  And it is not reset to the engine's five-minute server default

Scenario: The stack has been idle for longer than a server-default window
  Given the stack has been up with no calls for longer than the engine's default eviction window
  When a caller's first turn begins
  Then the model is already resident and the turn pays no load time
  And the first token is produced within the warm p95
```

**AC-2.** A caller never pays the load.

```gherkin
Scenario: A call placed long after boot behaves like one placed immediately after
  Given the boot warm-up has completed
  When a call arrives 30 minutes later
  Then no turn exhibits a time-to-first-token above the warm p95
  And no turn exhibits the measured 32,919 ms load or the 67,349 ms cold-to-first-token signature

Scenario: The GPU is not idle-clocked when a call arrives
  Given the stack has been idle
  When a call arrives
  Then the memory clock is not at the measured idle state of P5 / 405 of 14,001 MHz
  And the state is observed on the GPU, not inferred from a log line
```

**AC-3.** The warm state is a fact about the running stack, not about the last call.

```gherkin
Scenario: Residency is asserted after a recovery restart
  Given one of the stack's services has been restarted
  When the stack is declared usable again
  Then residency is re-established on the serving path
  And no caller is admitted into a stack whose model has silently expired

Scenario: The model was evicted despite the keep-alive
  Given the model is no longer resident mid-life for any reason
  When a turn runs
  Then the trace records the stall rather than losing it
  And the condition is never invisible
```

**AC-4.** Two callers fit inside the card.

```gherkin
Scenario: Two generations at once
  Given two callers are generating simultaneously
  When the run completes
  Then peak VRAM stays at or below 90% of the measured 16,311 MiB device budget
  And there is no silent spill into shared system memory
  And neither caller is refused to protect the other
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **First token, warm, p95 ≤ 1,000 ms** for this module's share (excluding the 600 ms endpointing floor owned by `MOD-01`).
- TAC-2: **First token on a stack that completed its boot warm-up ≤ `Assumed: 2,000 ms`**; the measured **67,349 ms** cold-load-to-first-token must not be reachable after boot (`BRD-03`, `BRD-17`).
- TAC-3: **Zero keep-alive resets on the serving path** — after the change, no generation request omits the keep-alive; asserted by counting requests at the engine that carry no keep-alive (target: 0 per turn, and 1 per process lifetime at most for the initial warm).
- TAC-4: **Residency observation is external** — acceptance for `BRD-17` observes the model resident in `nvidia-smi` at the moment a call arrives, never that the boot script printed a success line. This is the "set ≠ live" trap and it is a TAC, not a test detail.
- TAC-5: **VRAM at N=2 ≤ 14,680 MiB** (90% of the measured 16,311 MiB budget) with zero visible sysmem spill. The per-stream decode rate at N=2 is **recorded, not required** (`TRD-22`). This TAC previously carried "each stream ≥ 18 tok/s (decode halves per added stream)" — that figure is **withdrawn** (`MOD-03` A.5 criterion 3).
- TAC-6: **Idempotent re-warm** — an already-warm model makes the warm step a no-op, not a second load, so a repeated start does not double-resident anything.
- TAC-7: **Reversible** — the serving-path keep-alive is a single setting; reverting it restores today's behaviour exactly (`BRD-15`).
- TAC-8: **Load** — a 30-minute idle-then-call run at N=1 and a two-caller N=2 run both show no turn with a first-token time above the warm p95 (TAC-1) and no turn above the 3,000 ms per-turn cap.

## HLD — Architecture Slice [Lens: Architect]

The pre-warm exists and runs (`start_services.ps1:659–702`, Step 6: enumerate `/api/tags`, POST a one-token `/api/generate` with `keep_alive=24h`). It is **neutralised on the serving path**: `_chat_ollama` (`llm_backend.py:185`) passes only `num_ctx` and `temperature`, and Ollama resets a model's keep-alive to the server default on any request that omits it. `OLLAMA_KEEP_ALIVE` is set nowhere in `.env` and read nowhere in `app/`. So residency is protected at boot and unprotected for the rest of the process's life — which is exactly the window the 32,919 ms measurement lives in.

```mermaid
flowchart LR
  subgraph BOOT[MOD-07 boot - already exists]
    START[start_services.ps1 Step 6] -->|/api/tags| TAGS[completion-capable models]
    TAGS -->|/api/generate one token, keep_alive=24h| WARM[Model resident]
  end
  WARM -->|... then the first real chat happens ...| GAP{{RESIDENCY GAP<br/>REC-11}}
  subgraph SERVE[MOD-03 serving path - this story]
    CHAT[_chat_ollama] -->|BEFORE: num_ctx + temperature only| RESET[Engine resets keep-alive<br/>to the 5-minute default]
    CHAT -->|AFTER: num_ctx + temperature + keep_alive| HOLD[Expiry held for the process lifetime]
  end
  GAP --> CHAT
  HOLD --> GEN[Generate per caller]
  RESET --> COLD[Measured 32,919 ms load<br/>67,349 ms to first token]
  COLD --> SILENCE[Caller hears silence and hangs up]
  GEN --> CALLER[Caller hears an answer within the warm p95]
  HOLD --> VRAM{KV per sequence 1.5 GiB<br/>x2 at N=2}
  VRAM -->|<= 14,680 MiB, no spill| BUDGET[BRD-11 satisfied]
  style GAP fill:#fee
```

- **Components touched:**
  - `MOD-03` / `app/llm_backend.py` — `_chat_ollama` passes an explicit keep-alive on **every** generation request, so the model's expiry is held by the serving path and not only by the boot ping.
  - `MOD-03` — the keep-alive value is resolved from configuration as a `DAT-09` key with a demonstrable reader (the key is *wired*, not merely written; the inert-key sweep is US-011's, and this story must not add a fifth inert key).
  - `MOD-03` — the VRAM budget at N=2 is asserted from measured values, not from a static table; `app/memory_budget.py`'s `"nvidia"` block is **not** a usable source (`REC-13`: it budgets a 6 GB machine, `qwen_llm_gb: 4.0`, `recommended_gb: 6.0`, `peak_total_gb: 5.7`, and sets `safe_threshold_percent: 95`, above `BRD-11`'s 90% ceiling — a guard that cannot fire until after the ceiling is breached).
  - `MOD-06` — a residency-lapse note on the trace, so an eviction that happens anyway is visible rather than silent.
  - `MOD-07` — consumed, not modified: this story specifies *what* must be resident and *how it is verified*; the boot sequence and its readiness gate are US-007's.
- **Interaction summary:**
  1. The stack boots; the existing Step 6 pre-warm loads the model and sets a 24-hour expiry.
  2. The first real caller turn is served; `_chat_ollama` now sends the keep-alive with the generation request, so the engine's expiry is held rather than reset to the server default.
  3. Subsequent turns continue to carry it; the model stays resident for the life of the process and no caller pays a load.
  4. **Failure path:** if the model is nevertheless evicted (a manual unload, a driver reset, a restart of the engine), the next turn pays the load and the trace records the stall — the condition is never invisible, even though the requirement is that it not happen.
  5. **Failure path:** if VRAM at N=2 would exceed the budget, the second caller is not refused and no caller's answer is degraded; the designed ladder applies (context reduced below a threshold, generation queued with a bounded wait) and the queue drains on recovery.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — Python 3.11, `ollama` client already pinned. The whole defect is an options dictionary on the serving path:

```
# app/llm_backend.py  (MOD-03)

def _chat_ollama(
    model: str,
    messages: list[dict],
    *,
    stream: bool = True,
    num_ctx: int | None = None,
    temperature: float | None = None,
    keep_alive: str | int | None = None,     # NEW - resolved from config, always passed
) -> Iterator[dict]: ...

def _srv_options() -> dict:
    """Options sent with EVERY generation request.

    num_ctx / temperature as today, plus an explicit keep-alive so the engine's
    expiry is held by the serving path (REC-11). Omitting it is the defect.
    """
    return {
        "num_ctx": settings.OLLAMA_NUM_CTX,         # DAT-09, 8192
        "temperature": settings.OLLAMA_TEMPERATURE,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,   # NEW - wired, not merely written
    }
```

- **Data schema changes** — `DAT-09` gains one key whose reader is demonstrated (`.env` stays authoritative per `REC-05`; the key is never written into `.machine_profile.json`'s `applied` block as if that block were configuration):

```ini
# .env  (key NAMES only - no secret values appear in this artefact)
OLLAMA_KEEP_ALIVE=<duration, resolved from configuration at import>
# Reader: app/llm_backend.py::_srv_options -> passed to every /api/chat call
# Before this story: the key does not exist in .env and is read nowhere in app/.
```

```python
# Behavioural contract - what the engine sees per request
# BEFORE: {"num_ctx": 8192, "temperature": 0.2}                 -> keep-alive reset to server default
# AFTER : {"num_ctx": 8192, "temperature": 0.2, "keep_alive": …} -> expiry held for the process lifetime
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| Model evicted mid-call | The largest single stall in the system (32,919 ms load / 67,349 ms to first token). Prevented by the serving-path keep-alive; **detected by the trace** when it still happens, so it is never invisible |
| Engine restarted under the app | The next request carries the keep-alive and re-establishes residency on its own; the stack does not require a re-boot to recover |
| Keep-alive value malformed | Start reports the parse failure rather than falling back silently — a malformed value that becomes a default is indistinguishable from an absent one |
| Keep-alive set to a value shorter than the idle gap | A configuration error, surfaced as drift rather than discovered by a caller paying the load |
| A second caller arrives while the first generates | Both queue on the engine; decode halves per added stream. Below the threshold, reduce context rather than refuse; never degrade one caller's *answer* to protect the other's *speed* |
| VRAM pressure exceeds the budget | The ladder applies — TTS to CPU, generation queues — and the trace records which rung served. "It fails" is not a mode |
| Model list resolution (`/api/tags`) at boot | Unchanged by this story; the per-utterance `/api/tags` round trip is removed by US-004, and the boot-time listing stays as `MOD-07`'s |
| Prompt prefix not warm after the fix | This story holds model *weights*; the prefix warm is US-007's, and the two are separately verifiable so a weights-warm stack that is prefix-cold cannot claim full readiness |
| Ollama not running at boot | `MOD-07`'s readiness gate reports not-ready; this story does not paper over it by lazily loading on the first call |
| Reverting the setting | Removing the keep-alive restores the 5-minute server default exactly — the measured defect — with no code revert |

- **Error handling** — the serving-path change adds no new failure class: a rejected `keep_alive` value surfaces as the engine's own HTTP error, which `GenerationFailed` (US-004) already carries to the turn path and to the trace. A residency lapse is **not** an exception but a **note** on the trace (`residency_miss=true` plus the observed load duration), because the turn still completes — it is simply slow, and the point is that it can never be slow silently. Configuration errors are raised at **start**, not at the first call: a malformed keep-alive is a boot-time parse failure.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-06 row |
|---|---|---|---|
| T-1 | unit | `_srv_options()` always contains `keep_alive`; a test asserting its absence fails | Happy path |
| T-2 | unit | A malformed keep-alive value is reported at start, not silently defaulted | Invalid input |
| T-3 | unit | The key has a demonstrable reader (static check: the key is referenced from `app/`, not only from writer-side tooling) | Partial data (`BRD-16`'s honest-value rule) |
| T-4 | integration | After a scripted turn, querying the engine shows the model still resident with a held expiry | Happy path main flow 3 |
| T-5 | integration | Absent env keys fall back to documented defaults, and the effective value is unambiguous | Missing data |
| T-6 | integration | Repeated warm-up against an already-warm model is a no-op, not a second load (TAC-6) | Duplicate request / Alternate path A1 |
| T-7 | integration | With the model force-unloaded mid-life, the next turn completes and the trace carries `residency_miss` with the observed load duration | Dependency failure E2 |
| T-8 | integration | Restarting the engine under a running app: the next turn re-establishes residency without a stack restart | Recovery |
| T-9 | integration | Removing the keep-alive restores the server default (the revert path, demonstrated) | Cancellation (revert) / `BRD-15` |
| T-10 | integration | `/ws/voice/text` still generates normally after the options change (`REC-09`) | — (regression guard) |
| T-11 | e2e | Residency is observed in `nvidia-smi` at the instant a call arrives — not inferred from a log line (TAC-4) | Happy path / Partial completion |
| T-12 | e2e | A call 30 minutes after boot shows no first-token time above the warm p95 (TAC-2) | Timeout (startup deadline is not exceeded) |
| T-13 | load | N=1, ≥100 turns: first-token p95 ≤ 1,000 ms, cold and warm reported separately (TAC-1) | — (TAC) |
| T-14 | load | N=2, 30 minutes: peak VRAM ≤ 14,680 MiB, zero sysmem spill, decode recorded (no floor), no turn above 3,000 ms (TAC-5, TAC-8) | Concurrent operation |
| T-15 | load | N=2: neither caller refused; the queue drains and both streams complete (TAC-8) | Partial completion |
| T-16 | load | The GPU is not idle-clocked at call arrival; memory clock is read from the device, not from a document | Concurrent operation |

## Traceability
- Parent module: `MOD-03` (Inference Serving & Prompt Assembly — residency is this module's to hold)
- Technical requirement: `TRD-12` (keep the model and its prefix resident, and hold two callers inside the VRAM budget) — with its **revised scope per `REC-11`**: "make residency hold on the serving path and make the warm state verifiable", not "add a preload"
- Use case: `UC-06` (operate and recover the stack) — the `✓` rows this story touches (happy path, alternate path A1, invalid input, missing data, partial data, duplicate request, dependency failure, recovery, partial completion) are covered by T-1…T-16
- Business requirement: `BRD-17` (boot-time readiness — the residency clause); `BRD-03` (cold-start first turn) is the caller-visible consequence this story protects; contributes to `BRD-11` (VRAM budget at two callers) and `BRD-15` (one-setting revert)
- Data gap / state machine: implements the residency half of `DAT-09` model config (`keep_alive`, model tag) and the VRAM line of the `BRD-11` budget. `SM-01` OPENING → GREETING is the transition a cold model delays, because the greeting is the first thing a caller hears
- Reconciliation: **`REC-11`** (central — the pre-warm exists, runs, and is neutralised by the first serving call; the fifth instance of "set ≠ live"); `REC-02` (one process, one event loop — the generation calls must stay non-blocking); `REC-05` (`.env` is the runtime truth and the machine profile is a detection artifact); **`REC-13`** (`app/memory_budget.py`'s `"nvidia"` block encodes a stale 6 GB machine and its `safe_threshold_percent: 95` sits above `BRD-11`'s 90% ceiling — `BRD-11` owns the ceiling and the threshold derives from it); `REC-06` (`doc/model_vram_analysis.md` carries the same stale machine and is excluded as evidence); `REC-01` (Pipecat is not adopted)
- Related workflow: `WF-01` step 1 and `UC-01` E1 (model not resident → first turn pays a cold load, `BRD-03` breached and traced)

## LLD test mapping — T-1 … T-16, reconciled 2026-09-19

Mapped by reading every `check()` in `doc/perf/tools/test_us006_residency.py`
against the scenario table above. **The suite cites no `T-` id at all** — it
names `TAC-3`, `TAC-4`, `TAC-6` and `TAC-7` — so unlike `US-001` the mapping is
*absent* rather than wrong. Eleven checks cover sixteen scenarios.

| LLD | Requires | Satisfied by | Status |
|---|---|---|---|
| T-1 | `_srv_options()` always contains `keep_alive` | *"every generation request carries keep_alive"*, *"the keep-alive is not sent only at boot"*, +3 | **COVERED — but the named helper does not exist.** `_srv_options` has **zero** matches in `app/`; options are assembled inline in `_chat_ollama` (`app/llm_backend.py:193-203`). The behaviour is tested; the function the scenario names was never written |
| T-2 | A malformed keep-alive is reported at start, not silently defaulted | — | **GAP.** `_resolve_keep_alive` coerces and has no test that it *reports* rather than defaults. Note `US-011`'s TAC-5 wiring now fails the boot on an unparseable managed value, which covers the class but not this key |
| T-3 | The key has a demonstrable reader (static check) | *"every generation request carries keep_alive"*, *"not sent only at boot"* | **COVERED** |
| T-4 | After a scripted turn, the model is still resident with a held expiry | clean floor / generation succeeds / resident after ONE call / expiry HELD — 4 checks | **COVERED** |
| T-5 | Absent env keys fall back to documented defaults | *"TAC-3 KEEP_ALIVE resolves from config"* | **COVERED** |
| T-6 | Repeated warm-up is a no-op, not a second load | *"a second call does not evict or duplicate residency"* | **COVERED** |
| T-7 | Force-unloaded mid-life: the next turn completes and the trace carries `residency_miss` | — | **GAP, and the field name is wrong.** The trace carries `residency_lapse` (`app/llm_backend.py:241`); `residency_miss` appears nowhere in `app/` |
| T-8 | Engine restarted under a running app: residency re-establishes without a stack restart | — | **GAP** |
| T-9 | Removing the keep-alive restores the server default (revert demonstrated) | `test_brd15_rollback.py` reverts US-006 | **COVERED elsewhere** — the only scenario this story gets from outside its own suite |
| T-10 | `/ws/voice/text` still generates after the options change (`REC-09`) | — | **GAP.** No test under `doc/perf/tools/` touches `/ws/voice/text`, so the regression guard `REC-09` asks for does not exist |
| T-11 | Residency observed in `nvidia-smi` **at the instant a call arrives** | — | **GAP, and the observation is not possible.** `gpu_clock_state()` is called at `app/boot_readiness.py:400`, inside the boot gate; there is no readiness surface for call time |
| T-12 | A call 30 minutes after boot shows no first-token time above the warm p95 | — | **GAP** — load gate, not run |
| T-13 | N=1 ≥100 turns: first-token p95 ≤ 1,000 ms | — | **GAP** — load gate |
| T-14 | N=2 30 min: VRAM ≤ 14,680 MiB, no sysmem spill, no turn over 3,000 ms | — | **GAP** — load gate |
| T-15 | N=2: neither caller refused; the queue drains | — | **GAP** — load gate |
| T-16 | The GPU is not idle-clocked **at call arrival** | — | **GAP, same impossibility as T-11** |

**Coverage: 5 covered, 1 covered elsewhere, 10 gaps.**

### Two findings, one of which is not this story's

**`T-11` and `T-16` require an observation that cannot be made, and so does
`US-007`'s unticked DoD box.** All three demand the model's residency or the GPU
clock read *at the instant a call arrives*. The gate reads both at boot
(`app/boot_readiness.py:400`) and the app exposes no readiness route, so nothing
at call time can consult them. This is a specification-level issue appearing in
two stories rather than a defect in either, and it needs one decision — build a
call-time readiness surface, or re-word the three claims — not three separate
fixes. It is recorded here rather than fixed here because re-wording an
acceptance criterion is the Product Owner's call, not an engineer's.

**T-1 names a function that does not exist.** `_srv_options()` is specified and
the options are in fact assembled inline. The behaviour is tested and correct;
the scenario describes a refactor that never happened. Worth knowing before
someone reads the LLD as an as-built description.

**Ten gaps is not a surprise and not all of it is owed by this story.** Six
(T-12…T-16, and T-2 in part) are the load and e2e gates in the DoD's own
outstanding list; T-8 and T-10 are unwritten integration guards; T-7 needs a
field renamed or added.

## Definition of Done
- [x] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-8)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-16)
- [ ] Perf/load test passed against the story's TACs (TAC-1 first-token p95 ≤ 1,000 ms, TAC-2 ≤ 2,000 ms after boot warm-up, TAC-5 VRAM ≤ 14,680 MiB at N=2 with decode recorded, not floored)
- [ ] Schema migration applied — n/a for data; the `DAT-09` key addition is recorded and its reader demonstrated
- [x] Module docs updated if contracts changed — `MOD-03` B.3 (the Ollama `/api/chat` row gains "keep-alive applied on the serving path") and B.8; `06-architecture.md` §2 MOD-07 flow already describes the pre-warm as "exists but is undone" per `REC-11`. **Done 2026-09-19:** B.3 already carried the keep-alive text and B.8 still recorded the gap, so the two rows **contradicted each other**; B.8 is now reconciled to as-built. Three of its four claims were stale (`keep_alive` is sent on every generation, `OLLAMA_KEEP_ALIVE` is set and read, `options` gained `num_predict`); the fourth — `pick_model()`'s uncached per-utterance `/api/tags` round trip — is **still open** and remains a real per-turn cost
- [x] Acceptance observed externally: the model resident in `nvidia-smi` at the moment a call arrives (TAC-4), never from a boot success line
- [x] `BRD-15` rollback demonstrated: `test_brd15_rollback.py` sets `OLLAMA_KEEP_ALIVE=5m`, observes the request carrying a 5-minute expiry again, and restores. **The story's wording was wrong and is corrected here:** it said *removing* the setting restores the defect, but the code default is `-1`, so unsetting the key KEEPS the fix. The revert that works is to set the pre-fix value.
- [x] No new inert key introduced — the keep-alive key has a demonstrated reader and is not written into the profile's `applied` block as configuration


**Outstanding:** LLD test mapping; the TAC-1/TAC-2/TAC-5 load test (first-token p95, VRAM at N=2) has not been run as specified; `BRD-15` rollback has not been demonstrated by actually reverting the setting. ~~`MOD-03` B.8 reconciliation unverified~~ — **reconciled 2026-09-19**, and the reconciliation found a live defect in the document rather than the code: B.3 and B.8 disagreed about whether keep-alive reaches the serving path. B.8 has been corrected; B.3 was right.

**One claim survives that reconciliation and is NOT this story's to close:** `pick_model()` still performs an uncached `/api/tags` round trip per utterance. `MOD-03` B.8 classes it "resolve now", and it is not resolved. It is a per-turn HTTP call on the serving path, measured in the same window as the residency work, and it should be either fixed or explicitly re-classed.