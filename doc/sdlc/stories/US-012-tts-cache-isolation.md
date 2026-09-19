> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-012 — Prove the TTS cache safe for two callers, or make it per-call [Lens: PO]

- **Status:** **NOT STARTED**

- **Story:** As a **caller**, I want **the audio I hear to be mine and nobody else's**, so that **two people talking to the assistant at the same time never hear a fragment of each other's conversation**.
- **Business value:** `BRD-06` requires caller isolation **demonstrated under concurrent load, not asserted**, and the TTS cache is the one piece of shared mutable state on the audio path. Today the safety argument is an assertion — "keys are content hashes, so identical text maps to identical audio" — and `DG-06` explicitly records that the assertion is not sufficient: the risk is not the key, it is a buffer that is returned and then mutated.
- **Priority:** **Must** — TPO ordering note: this is a **test that can fail a design**, and it must run before any `BRD-06` compliance claim is made. The fallback is pre-decided (`DG-06`), so a failure is a design change, not a blocked program.

## Acceptance Criteria [Lens: PO]

**AC-1.** The isolation claim is a measurement, not an argument.

```gherkin
Scenario: Two callers run a full load condition
  Given the two-caller harness is driving both sessions
  When the condition completes at least 100 turns per caller
  Then zero cross-call cache hits are observed
  And the result is reported as pass or fail, not as a rate

Scenario: The test is run more than once
  Given the isolation condition is repeated
  When three consecutive runs complete
  Then no run shows a cross-call hit
  And a single clean run is not offered as proof
```

**AC-2.** A returned buffer is never the cached buffer.

```gherkin
Scenario: The same text is synthesised twice
  Given one utterance's audio is cached
  When a later utterance hits that cache entry
  Then the caller receives a copy, and the cached entry is unchanged
  And a mutation of the returned buffer does not alter the cache

Scenario: A cached buffer is served while another caller synthesises
  Given caller A hits a cache entry
  When caller B synthesises a different utterance concurrently
  Then neither caller's audio contains any part of the other's
```

**AC-3.** Eviction under concurrency is a latency cost, never a correctness one.

```gherkin
Scenario: An entry is evicted while a hit is in flight
  Given the cache is at its capacity and eviction is FIFO by insertion order
  When a concurrent hit targets an entry being evicted
  Then the utterance is re-synthesised and spoken correctly
  And no caller receives a truncated or empty result

Scenario: The capacity is reached during two-caller load
  Given both callers are synthesising
  When the entry count reaches the cache maximum
  Then the behaviour stays bounded and correct
  And no unbounded growth is observed
```

**AC-4.** If the test fails, the fallback is applied rather than argued away.

```gherkin
Scenario: A cross-call hit is observed
  Given the isolation test observes a cross-call hit
  When the outcome is recorded
  Then the cache becomes per-call, bounded to one session's utterances
  And the latency benefit is retained without the shared state

Scenario: The change is reverted
  Given the per-call design is adopted
  When it is reverted by configuration
  Then the previous behaviour returns
  And the revert is a configuration change, not a code revert
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **Zero cross-call cache hits** at N=2 over **≥100 turns per condition**, across **three consecutive runs** — a pass/fail, not a statistic (`DG-06`, `MOD-04` A.5.2).
- TAC-2: **Copy discipline held under concurrency** — the cache stores and returns a copy (`voice_handler.py:675, 681`); a test mutates a returned buffer and asserts the cache entry is byte-unchanged, and the same holds when the return and the mutation overlap with another caller's synthesis.
- TAC-3: **Eviction is FIFO by insertion order and bounded** (`voice_handler.py:677–680`) — at capacity, a concurrent hit on an evicted key re-synthesises; the utterance is spoken correctly and the cache never grows past `_tts_cache_max = 50`.
- TAC-4: **Keys carry no caller data** — keys are `hash()` of the agent's own text (`voice_handler.py:666`); the test asserts that no key derives from caller speech, caller identity, or transcript content. A key that does is a rejection, not a finding.
- TAC-5: **Cache hit/miss is recorded per utterance** — the existing event (`voice_handler.py:669, 683`) carries text length and the hit flag only, never the text; that discipline survives the change.
- TAC-6: **Per-call fallback is pre-built, not improvised** — the per-call variant is implemented behind a single setting and is demonstrably revertible (`BRD-15`), so a failed test costs a flip rather than a redesign.
- TAC-7: **Isolation independent of the cache's fate** — no caller's history, prompt, context or session metadata is reachable from the cached audio path, observed at N=2 (`BRD-06`).
- TAC-8: **Load** — the N=2 isolation run also satisfies: no turn above the 3,000 ms per-turn cap (`BRD-05`), peak VRAM ≤ 90% of 16,311 MiB, and no turn falling back to the CPU execution provider as a result of the change.
- TAC-9: **Reversibility** — the cache scope is a single setting; reverting it restores today's process-wide behaviour exactly, and the isolation verdict is re-run against whichever scope is in force.

## HLD — Architecture Slice [Lens: Architect]

The cache is declared on the class, so every concurrent call in the process shares one dictionary — and because the architecture is **one process with one event loop** (`REC-02`), "process-wide" means "across every live caller" with no process boundary to contain it. The dangerous object is not the key and not the audio content: it is the **buffer that is handed out and then written to**. A copy discipline exists; the question `DG-06` records is whether it holds when two callers are inside the cache at once, including on the eviction path.

```mermaid
flowchart TB
  subgraph PROC[One process, one event loop - REC-02]
    A[Caller A turn: SYNTHESISING] --> KEY_A[hash of the agent's text]
    B[Caller B turn: SYNTHESISING] --> KEY_B[hash of the agent's text]
    KEY_A --> CACHE[(DAT-11 process-wide TTS cache<br/>class-level dict, max 50 entries)]
    KEY_B --> CACHE
    CACHE -->|hit| COPY_A[Return a copy<br/>voice_handler.py:675, 681]
    CACHE -->|miss| SYNTH_A[kokoro synthesis]
    SYNTH_A --> STORE[Store a copy]
    STORE --> CACHE
    COPY_A --> MUT[Mutated by the framing and resample path]
    MUT --> OUT_A[Caller A audio]
    CACHE -->|capacity reached| EVICT[FIFO eviction by insertion order<br/>voice_handler.py:677-680]
    EVICT -.concurrent hit on an evicted key: re-synthesise (latency, not correctness).-> SYNTH_A
    CACHE -.-> RISK{{DG-06: cross-call hit<br/>or a mutated cached buffer}}
    RISK --> TEST{Isolation test at N=2<br/>100 turns per caller, 3 runs}
    TEST -->|zero hits| VERDICT[Keep process-wide<br/>BRD-06 demonstrated]
    TEST -->|a hit observed| PERCALL[Per-call cache: bounded to one session<br/>same latency benefit, no shared state]
    style RISK fill:#fee
    style VERDICT fill:#efe
    style PERCALL fill:#eef
```

- **Components touched:**
  - `MOD-04` / `app/voice_handler.py` — the cache's **scope** is decided by the test: kept process-wide with the copy discipline proven, or made per-call behind a single setting.
  - `MOD-04` — the eviction path (`:677–680`) and the return path (`:675, 681`) are the two places a concurrency defect can live, and both are exercised by the test.
  - `MOD-06` / harness (US-002) — the N=2 isolation condition is the instrument; this story is the condition it runs, with the pass/fail verdict recorded.
  - `MOD-01` — consumer, unchanged: the audio path continues to receive frames on sentence boundaries (`US-005`), and nothing about the framing contract moves.
  - `MOD-07` — consumed: the cache scope is a configuration setting whose effective value is reported (`US-011`), so "which scope was in force" is answerable from the run's own artifacts.
- **Interaction summary:**
  1. Both callers reach synthesis and consult the one cache; each looks up a key derived from the agent's own text.
  2. A hit returns a copy; the caller's framing and resample path mutates the copy, never the entry.
  3. A miss synthesises, stores a copy, and returns a copy.
  4. **Failure path:** at capacity, FIFO eviction removes the oldest insertion; a concurrent hit on an evicted key re-synthesises. The caller hears the correct utterance, later — a latency cost.
  5. **Failure path:** if the isolation test observes any cross-call hit, the scope becomes per-call and the same N=2 condition is re-run against the new scope; `BRD-06` is then demonstrated by the absence of shared state rather than by the discipline holding.
  6. **Failure path:** the change is reverted by configuration, restoring the process-wide scope exactly, and the verdict is re-attached to the scope in force.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — Python 3.11, `kokoro-onnx==0.5.0` already pinned:

```python
# app/voice_handler.py  (MOD-04)

_TTS_CACHE_MAX = 50            # today a class-level constant; DAT-11 capacity

class TTSCache:
    """DAT-11. Scope is decided by TRD-15's isolation test, not by argument."""

    def __init__(self, scope: Literal["process", "per_call"], max_entries: int = _TTS_CACHE_MAX) -> None: ...

    def get(self, text: str) -> ndarray | None: ...
        # returns a COPY (:675); a hit on an evicted key returns None -> re-synthesise

    def put(self, text: str, audio: ndarray) -> None: ...
        # stores a COPY (:681); FIFO eviction by insertion order at capacity (:677-680)

    @staticmethod
    def key(text: str) -> int: ...
        # hash(text) where text is the AGENT's own text (:666).
        # Never derived from caller speech, caller identity or a transcript.

# Scope selection - one setting, revertible (BRD-15)
TTS_CACHE_SCOPE: Literal["process", "per_call"] = settings.TTS_CACHE_SCOPE
```

- **Data schema changes** — `DAT-11` keeps its entry shape (key, audio copy, sample rate; max 50) and gains a **scope** attribute. No durable store is involved; `voice_handler.py:665`'s 500-character synthesis cap is unchanged:

```python
# After this story, the cache (DAT-11) is scoped and eviction-ordered
# { "scope": "process" | "per_call",      # decided by the isolation test
#   "key": "hash(agent_text)",            # no caller data in the key space
#   "audio": "<copy>", "sample_rate": 24000,
#   "max_entries": 50 }                   # FIFO by insertion order
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| Two callers request the same utterance simultaneously | Both may miss and synthesise; both receive copies. A duplicate synthesis is a latency cost, never a correctness one |
| An entry is evicted while a hit is in flight | The hit re-synthesises and the utterance is spoken correctly; never truncated, never empty |
| A returned buffer is mutated by the caller | The cache entry is unchanged — the copy discipline is what the test proves |
| The cache is at capacity during two-caller load | Bounded at 50 entries; FIFO eviction; no unbounded growth |
| A key would derive from caller speech or identity | Rejected: keys are hashes of the agent's own text only (`DG-06`, `MOD-04` A.1) |
| Synthesis fails for one caller | The spoken fallback is produced and the other caller's turn is unaffected (`BRD-13`) |
| The isolation test observes a cross-call hit | The scope becomes per-call and the condition is re-run; the verdict attaches to the scope in force |
| The greeting is synthesised on a different path (`MOD-04` B.8) | The greeting path consults the same cache under the same scope rule; it is exercised by the N=2 condition, not excluded from it |
| Cached audio is stale relative to a configuration change (voice, speed) | The cache scope change and any voice change are separate settings; a voice change requires a cache clear, and that requirement is stated rather than assumed |
| Revert after the verdict | Revertible by configuration; the verdict is re-run against the restored scope, and the previous verdict is not carried over |

- **Error handling** — this story adds **no** error class. A synthesis failure keeps its existing behaviour: the spoken fallback (`BRD-13`), never silence. A miss on an evicted key is not an error — it is a re-synthesis, and it is recorded as a miss on the existing event (`:669, 683`). The one condition that is neither an error nor a success is the **isolation verdict**, and it is modelled explicitly as pass/fail with the scope attached to it, so a `BRD-06` claim can never be made without a verdict that names the scope it was measured under.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-03 row |
|---|---|---|---|
| T-1 | unit | `put` then `get` returns a copy; mutating the returned array leaves the entry byte-identical (TAC-2) | Happy path main flow 5 |
| T-2 | unit | `key` is derived only from the agent's text; a test constructing a key from caller speech fails by construction (TAC-4) | Invalid input |
| T-3 | unit | FIFO eviction at `max_entries`; the oldest insertion goes first (`:677–680`) | Partial data |
| T-4 | unit | A hit on an evicted key returns `None` and triggers a re-synthesis, never a partial buffer | Missing data |
| T-5 | unit | The cache never exceeds `max_entries` under a synthetic burst (TAC-3) | Partial completion |
| T-6 | unit | A synthesis failure produces the spoken fallback and does not poison the cache entry | Dependency failure |
| T-7 | unit | Under `scope="per_call"`, two sessions have disjoint caches by construction | Concurrent operation |
| T-8 | integration | One session's cache is unreachable from another's under `per_call` | Missing data (each session synthesises independently) |
| T-9 | integration | The same utterance in two sessions under `process` yields two correct, independent audio buffers | Duplicate request |
| T-10 | integration | Cache hit/miss events carry text length and the hit flag only — never the text (TAC-5) | — (TAC) |
| T-11 | integration | Caller B hangs up mid-synthesis; caller A's turn completes and its audio is unaffected | Cancellation |
| T-12 | integration | A caller's session crash does not disturb the surviving caller's cache or audio | Recovery |
| T-13 | integration | The greeting path consults the same cache under the same scope rule | Alternate path A1 |
| T-14 | integration | Reverting the scope setting restores the previous behaviour; the verdict is re-attached to the restored scope (TAC-6, TAC-9) | Retry (revert and re-run) |
| T-15 | integration | One session's dependency failure does not fail the other's synthesis (`BRD-13`) | Dependency failure |
| T-16 | e2e | Two live callers hold a conversation; each hears only its own audio, judged end-to-end | Happy path |
| T-17 | e2e | One caller mid-turn when the other ends: both handled, neither turn is lost | Partial completion |
| T-18 | load | N=2 isolation: **zero** cross-call hits over ≥100 turns per caller, three consecutive runs — pass/fail (TAC-1) | Concurrent operation (this is the use case) |
| T-19 | load | N=2 plus diversity: different utterances, identical utterances and eviction pressure in one run; still zero cross-call hits | Duplicate request |
| T-20 | load | N=2 with an artificial 2.5 s stall on one caller's synthesis: the other caller's audio is unaffected and no cross-call audio appears | Timeout E3 |
| T-21 | load | N=2, 30 minutes: peak VRAM ≤ 90% of 16,311 MiB, no turn above 3,000 ms, no unexpected CPU-provider fallback (TAC-8) | — (TAC) |

## Traceability
- Parent module: `MOD-04` (Speech Services — owns `DAT-11` and the isolation proof even though `BRD-06` is stated in `MOD-01`'s terms; `TRD-15` is this module's)
- Technical requirement: `TRD-15` (TTS cache isolation under concurrent callers — proven safe for two callers or made per-call, on the test rather than on the hash argument); with `TRD-13` (streaming synthesis) supplying the framing contract this change must not break
- Use case: `UC-03` (serve a second caller concurrently) — all `✓` rows of its Scenario Coverage table are covered by T-1…T-21, with T-18 as the use case's own condition
- Business requirement: **`BRD-06`** (caller isolation — "demonstrated under concurrent load, not asserted"); contributes to `BRD-05` (two simultaneous callers), `BRD-13` (a synthesis failure is spoken, and does not fail the other caller) and `BRD-15` (the scope is one setting)
- Data gap / state machine: **`DG-06`** (use-as-is, conditional on an explicit N=2 isolation test — this story *is* the discharge of that condition); `DAT-11` gains a scope attribute; `SM-02` SYNTHESISING → SENDING is the transition whose shared state this story bounds
- Reconciliation: `REC-01` (Pipecat is not adopted — its `KokoroTTSService` already streams, and it remains a `UC-10` candidate only, not a route for this change); `REC-02` (one process, one event loop — the reason "process-wide" means "across every caller"); `REC-09` (the text path does not synthesise, and this change must not touch it)
- Related workflow: `WF-02` (the two-caller workflow) step 5 ("each caller hears only their own audio") and its concurrency row

## Definition of Done
- [ ] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-9)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-21)
- [ ] Perf/load test passed against the story's TACs (TAC-1 zero cross-call hits over ≥100 turns per caller across three runs; TAC-8 no per-turn cap breach and no VRAM breach at N=2)
- [ ] Schema migration applied — n/a for durable data; the `DAT-11` scope attribute is recorded and its effective value is reported by `US-011`'s configuration output
- [ ] Module docs updated if contracts changed — `MOD-04` B.4 (`DAT-11` row gains the scope) and B.8 risk 3, whose recorded outcome is either "test passed, cache kept process-wide" or "test failed, per-call adopted" — never left as "may fail"
- [ ] **Isolation verdict recorded with the scope it was measured under**: pass (process-wide kept) or fail (per-call adopted), and no `BRD-06` claim is made without it
- [ ] `BRD-15` rollback demonstrated: the cache scope reverts by configuration, and the verdict is re-run against the restored scope
- [ ] Cache key discipline asserted by test, not review: no key derives from caller speech, caller identity or a transcript (`TAC-4`)
