> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-008 — De-serialize retrieval so no caller waits behind another [Lens: PO]

- **Status:** **IMPLEMENTED - measured, but NO acceptance test exists · DoD 1/7**

- **Story:** As a **caller on the second line**, I want **my retrieval to run while Caller A's retrieval is still running**, so that **my answer does not start late because a stranger asked a slow question**.
- **Business value:** Retrieval is the program's only **non-resource** bottleneck — one caller's slow query delays another caller's turn with no CPU, VRAM or bandwidth anywhere near saturation. `BRD-07` is a fairness requirement, and it is the one degradation a caller experiences for no physical reason at all.
- **Priority:** **Must** — TPO ordering note: this is a change **inside `MOD-02`** (and, for the primary path, inside the `enterprise-rag-core` repository), sequenced independently of the streaming work so the two experiments cannot confound each other (`BRD-05`'s rule: one variable at a time).

## Acceptance Criteria [Lens: PO]

**AC-1.** One caller's retrieval does not delay another caller's turn.

```gherkin
Scenario: Two callers retrieve at the same time
  Given two callers are live and both issue retrieval on the same turn
  When both retrievals run
  Then both complete without one waiting for the other's full duration
  And each caller's turn stays within 1.5x of its solo latency

Scenario: One caller's retrieval is stalled
  Given caller A's retrieval is stalled for 2.5 seconds
  When caller B retrieves in that window
  Then caller B's first-audio time is unaffected by the stall
  And caller B's turn completes within the concurrency target
```

**AC-2.** An abandoned retrieval does not hold the caller.

```gherkin
Scenario: A retrieval exceeds its deadline
  Given the retrieval exceeds its budget
  When the deadline fires
  Then the retrieval is abandoned rather than awaited
  And the turn continues on the degraded rung

Scenario: A retrieval times out for one caller
  Given caller A's retrieval times out and falls back
  When caller B's turn runs
  Then caller B is unaffected and its own retrieval is not delayed by A's fallback cost
```

**AC-3.** Two callers keep two separate groundings.

```gherkin
Scenario: Two callers ask different questions at once
  Given caller A and caller B both retrieve
  When the results are assembled
  Then each turn carries only its own chunks
  And no turn contains another caller's query or chunk set

Scenario: Two callers ask the same question
  Given caller A and caller B ask the same thing
  When both retrieve
  Then two independent retrievals run with no shared result state
  And neither turn is served from the other's response
```

**AC-4.** De-serialization does not change what is retrieved.

```gherkin
Scenario: The same query under both designs
  Given a query is issued before and after the change
  When the returned chunks are compared
  Then the chunk set, order and citation labels are identical
  And only the timing of their availability has changed
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **Overlap** — two concurrent retrievals overlap; the second adds no more than **1.5×** to the first's completion (`BRD-05`), asserted at N=2 with an artificial 2.5 s stall injected into caller A's retrieval.
- TAC-2: **Retrieval p95 ≤ 400 ms** at N=1 and at N=2 (`Assumed` — no retrieval-stage measurement exists yet; US-001's `retrieval_done` mark is what replaces this assumption with a number). Abort ceiling unchanged: 2,500 ms read / 1,000 ms connect.
- TAC-3: **Degraded rungs** — keyword-only ≤ `Assumed: 150 ms` p95; local-store fallback ≤ `Assumed: 300 ms` p95. Both are assertions to be replaced by `DAT-07` measurement, not claims.
- TAC-4: **The event loop is not occupied by a synchronous scan** — a retrieval must not block the single event loop for the duration of a CPU-bound scan (`REC-02`: one process, one event loop shared with the turn path). Measured as: caller B's turn is not stalled while caller A's retrieval scans.
- TAC-5: **Contract unchanged** — the `retrieve_context` tool contract the app depends on is unchanged in shape (one tool, `REC-04`); the fix must not widen the surface and must not pull reranker or semantic-cache cost back onto the hot path.
- TAC-6: **Text path unregressed** — `/ws/voice/text` calls `run_rag_query_sync` and shares this code (`REC-09`); its behaviour is unchanged.
- TAC-7: **Load** — an N=2 run with the injected stall shows `turns_over_3000ms: 0` and both callers' records separated, over three consecutive runs.
- TAC-8: **Isolation** — no turn's retrieved context contains another caller's query or chunk set, observed at N=2 (`BRD-06`).

## HLD — Architecture Slice [Lens: Architect]

The blocker is inside the primary path. ERC runs **one worker with no `workers` argument** and performs its dense and BM25 legs as **synchronous CPU-bound calls wrapped in an `asyncio.gather`** — so the two "parallel" legs are serial and the whole service is a single-threaded queue. With peak concurrency 2 (`AS-01`), caller B's retrieval waits for caller A's. Note the shape: the fallback path is *already* in-process and de-serializable; the primary path is where the head-of-line block lives.

```mermaid
flowchart LR
  QA[Caller A query] --> DISP_A{Dispatcher USE_MCP_RAG}
  QB[Caller B query] --> DISP_B{Dispatcher USE_MCP_RAG}
  DISP_A -->|auto or on| MCP[MCP client :8010]
  DISP_B -->|auto or on| MCP
  MCP --> ERC[ERC :8010 - one worker]
  subgraph ERC_IN[ERC hybrid engine - the BRD-07 blocker]
    DENSE[Chroma dense leg] --> RRF[RRF fusion alpha 0.3]
    BM25[BM25 full-corpus scan] --> RRF
  end
  ERC --> ERC_IN
  ERC_IN -.BEFORE: synchronous legs behind an asyncio.gather,<br/>serial in one event loop, second caller queues.-> QUEUE[B's retrieval waits for A's]
  ERC_IN -.AFTER: the scan does not occupy the loop;<br/>two retrievals are in flight.-> OVERLAP[A and B overlap]
  RRF --> FMT[Format with section labels]
  FMT --> GUARD[Context guard warn only]
  GUARD --> OUT[Ranked chunks, per caller]
  DISP_A -->|off, or breaker open| LEG_A[Legacy local store]
  DISP_B -->|off, or breaker open| LEG_B[Legacy local store]
  LEG_A --> OUT
  LEG_B --> OUT
  OUT -.per-caller marks.-> TR[MOD-06 retrieval_start / retrieval_done<br/>plus the rung that served]
  style QUEUE fill:#fee
  style OVERLAP fill:#efe
```

- **Components touched:**
  - `MOD-02` boundary / `enterprise-rag-core` (a separate repository, `D:\project\enterprise-rag-core`) — the retrieval work must not occupy the event loop for the duration of a synchronous scan; the dense and BM25 legs must actually overlap or must not block the loop.
  - `MOD-02` / `app/rag_mcp.py` — the client is made safe for two in-flight requests; a new `httpx.Client` per request is replaced by one reused client (shared with US-013's breaker change).
  - `MOD-02` / `app/rag_legacy.py` — the `PersistentClient` rebuilt per call is reused, so the fallback path is fast enough that two callers degrade without queueing.
  - `MOD-02` / `app/rag.py` — the dispatcher is **not** restructured (classified Reusable); per-request state stays per-request.
  - `MOD-06` — the retrieval marks (US-001) plus a per-turn `retrieval_rung` note, so which rung served is a measurement fact rather than an inference.
- **Interaction summary:**
  1. Two callers each issue a query through their own dispatcher call; no query state is shared between them.
  2. On the primary path, the MCP client sends both requests; inside ERC the dense and BM25 legs run without occupying the event loop, so both callers' retrievals are in flight together.
  3. Each caller's result is fused, formatted with section labels and returned to that caller's turn — the chunk sets never cross.
  4. **Failure path:** if a retrieval exceeds its deadline it is abandoned, not awaited; the turn continues on the degraded rung (keyword-only, then the local store) and the trace records which rung served.
  5. **Failure path:** if caller A's retrieval times out and pays the local-store fallback, caller B's retrieval is unaffected — there is no shared queue and no shared result state.
  6. **Failure path:** if the ERC change cannot land without altering the `retrieve_context` contract, the fallback design is a bounded timeout plus the local store, and `BRD-07` is met by **isolation** rather than by parallelism — stated as the outcome, not discovered late.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — Python 3.11, `httpx` already pinned:

```python
# app/rag_mcp.py  (MOD-02) - the client, made safe for two in-flight requests

class McpClient:
    def __init__(self, base_url: str, read_timeout_s: float = 2.5,
                 connect_timeout_s: float = 1.0) -> None: ...
    async def retrieve_context(self, query: str) -> list[Chunk]: ...
        # one reused client; no per-request construction
        # raises RetrievalTimeout / RetrievalConnectError / RetrievalMalformed
        # never returns a partial list as if it were complete

# app/rag.py  (MOD-02) - dispatcher, shape unchanged (Reusable)
async def retrieve_context(question: str) -> RetrievalResult: ...
    # returns ranked chunks, OR an explicit not-relevant signal (US-009)
    # rung recorded on the result: "primary" | "keyword_only" | "local"

@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[Chunk, ...]
    rung: Literal["primary", "keyword_only", "local"]
    not_relevant: bool
    best_distance: float | None
```

```python
# enterprise-rag-core  (separate repo, ERC-side change with its own test gate)
# BEFORE - the "parallel" legs are serial and occupy the loop
#   dense, bm25 = await asyncio.gather(
#       self._dense_scan(query),        # synchronous CPU-bound
#       self._bm25_scan(query),         # synchronous CPU-bound
#   )
# AFTER - the scan does not occupy the event loop, so two retrievals can be in flight
#   dense, bm25 = await asyncio.gather(
#       asyncio.to_thread(self._dense_scan, query),
#       asyncio.to_thread(self._bm25_scan, query),
#   )
# The `retrieve_context` tool contract is unchanged (TAC-5).
```

- **Data schema changes** — none. There is no store or table on this path; the change is to how work is scheduled, and `DAT-07` gains a `retrieval_rung` note (US-001) so the effect is measurable.

```python
# app/rag_legacy.py  (MOD-02) - the fallback path is made cheap enough to degrade into
# BEFORE: a PersistentClient is rebuilt per call
# AFTER : the client is constructed once and reused for the process lifetime
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| MCP returns nothing (empty result set) | Not an error: the explicit "no information" marker is substituted into the prompt and the assistant says it does not know. Distinguished in the trace from below-floor |
| MCP times out | Fall back to the local store **within the same retrieval budget** — not after it. The turn continues; the trace records the local rung |
| MCP connect fails | Immediately local; a connect failure is not a reason to wait for the read timeout |
| Breaker open | The local store serves; no per-turn probing of a known-dead service (the probe is US-013's) |
| Embedding hop down | Keyword-only rung; the dense leg is skipped and BM25 still answers |
| Query empty or whitespace | No retrieval; the existing `if not question.strip(): return None` guard is kept |
| Two callers query simultaneously | Two independent retrievals, no shared query state. Asserted at N=2 with an injected stall, not argued |
| One caller's retrieval is abandoned at its deadline | The other caller is untouched; abandonment is per-request, not per-process |
| Malformed or partial MCP response | Treated as a retrieval failure, not as empty context — falling through to "no information" on a parse error would silently degrade grounding quality |
| ERC cannot be changed without altering the tool contract | `BRD-07` is met by isolation (bounded timeout plus local store) instead of parallelism; recorded as the outcome |
| Idempotency | A retrieval is read-only and side-effect free; a retried turn re-queries. No cache is added — `REC-04` records the semantic cache as unreachable and this story does not wire it |

- **Error handling** — `RetrievalTimeout` and `RetrievalConnectError` (bounded, rung-downgrading, never awaited past the budget), `RetrievalMalformed` (a parse failure is a retrieval failure, never an empty context), and `EmbeddingHopUnavailable` (downgrades to the keyword-only rung). Every failure path resolves to a rung that produces an answer or an honest "no information" — never to a stalled loop, and never to a caller B who is waiting for caller A's problem to end. Cancellation is per-request: abandoning caller A's retrieval does not disturb caller B's in-flight one.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-03 row |
|---|---|---|---|
| T-1 | unit | Two `retrieve_context` coroutines run concurrently under a fake ERC that sleeps; wall time ≈ max, not sum | Happy path main flow 3–4 |
| T-2 | unit | A retrieval past its deadline raises `RetrievalTimeout` and is not awaited to completion | Timeout E3 |
| T-3 | unit | A malformed MCP response raises `RetrievalMalformed` and does not return an empty list | Invalid input |
| T-4 | unit | An empty result set returns the explicit not-relevant marker, distinguishable from a failure | Missing data |
| T-5 | unit | The embedding hop failing downgrades the rung to keyword-only and still returns chunks | Dependency failure |
| T-6 | unit | `McpClient` reuses one HTTP client across calls (no per-request construction) | Duplicate request |
| T-7 | unit | A query that is empty or whitespace performs no retrieval | Partial data |
| T-8 | integration | Two callers with different queries: each turn carries only its own chunks, asserting no chunk-set crossover | Concurrent operation |
| T-9 | integration | Each session's own noise gate applies independently; one caller's gated turn does not affect the other's retrieval | Invalid input (per-session gate) |
| T-10 | integration | Two callers asking the identical question produce two independent retrievals and no shared result state | Duplicate request |
| T-11 | integration | Caller A's retrieval times out and falls back; caller B's turn latency is unchanged | Timeout E3 / Dependency failure |
| T-12 | integration | Caller B hangs up mid-retrieval; caller A's retrieval completes normally | Cancellation |
| T-13 | integration | A process-level retry after a failed retrieval does not reuse the failed response | Retry |
| T-14 | integration | `/ws/voice/text` retrieves correctly after the change (`REC-09`, TAC-6) | — (regression guard) |
| T-15 | e2e | The same query returns an identical chunk set, order and citation labels before and after the change (AC-4, TAC-5) | Partial data (per-session grounding unchanged) |
| T-16 | load | N=2 with a 2.5 s artificial stall on caller A: caller B's first-audio time is unmoved and B stays within 1.5× of solo (TAC-1) | Concurrent operation (this is the use case) |
| T-17 | load | Three consecutive N=2 runs: `turns_over_3000ms: 0`, both records separated, no turn exceeding the per-turn cap (TAC-7) | Recovery |
| T-18 | load | Retrieval p95 measured at N=1 and N=2 and reported against the `Assumed` 400 ms allowance (TAC-2) | — (TAC) |
| T-19 | load | Breaker open / dependency killed: caller A degrades to local and caller B completes unchanged | Dependency failure / Partial completion |
| T-20 | load | One caller mid-turn when the other ends: both handled, neither turn is lost | Partial completion |

## Traceability
- Parent module: `MOD-02` (Retrieval & Grounding)
- Technical requirement: `TRD-07` (make retrieval concurrent: no caller waits behind another caller's query)
- Use case: `UC-03` (serve a second caller concurrently) — all `✓` rows of its Scenario Coverage table are covered by T-1…T-20
- Business requirement: `BRD-07` (retrieval does not serialize callers); contributes to `BRD-05` (two simultaneous callers), `BRD-06` (no cross-caller context, asserted at N=2), `BRD-13` (the retrieval rung of the degradation ladder) and `BRD-14` (the deadline that makes abandonment possible — the bound itself is `TRD-09`/US-013)
- Data gap / state machine: `DG-02` (no arrival-rate measurement exists — the injected-stall assertion is how the concurrency claim is made checkable); implements the `SM-02` RETRIEVING path for two simultaneous callers
- Reconciliation: `REC-02` (one process, one event loop — a blocking retrieval is also a blocked turn path, which is why the fix cannot be "add more workers" on fixed hardware); `REC-04` (the app calls exactly one tool; widening the contract would pull reranker and semantic-cache cost back onto the hot path); `REC-09` (the text path shares this code); `REC-03` is adjacent — consolidation changes *which store* answers, not the dispatcher's shape, and is US-010's
- Related workflow: `WF-02` steps 3–5 and its partial-completion row for step 3 (retrieval serialization → **gap** today)

## Definition of Done
- [ ] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-8)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-20)
- [ ] Perf/load test passed against the story's TACs (TAC-1 1.5× overlap with the injected 2.5 s stall, TAC-2 retrieval p95 at N=1 and N=2, TAC-7 three consecutive N=2 runs)
- [x] Schema migration applied — n/a (no durable store on this path)
- [ ] Module docs updated if contracts changed — `MOD-02` B.3 (`retrieve_context` row) and B.6 (edge-case table) if the implementation differs from `TRD-07`; the ERC-side change is recorded against the ERC test gate
- [ ] `BRD-15` rollback demonstrated: the concurrency change is revertible by configuration or a single revertable commit on each side (`app/` and `enterprise-rag-core`)
- [ ] If the ERC change cannot land without altering the `retrieve_context` contract, the fallback design (bounded timeout + local store, meeting `BRD-07` by isolation) is recorded as the adopted outcome


**Outstanding — this story is the weakest in the program.** No acceptance test exists at all: it is marked implemented on a measurement (retrieval mean 7,164 -> 2,227 ms) with no suite covering its ACs or TACs, and no LLD test mapping. TAC-1/TAC-2/TAC-7 (1.5x overlap, retrieval p95 at N=1 and N=2, three consecutive N=2 runs) have not been run as specified. `MOD-02` not reconciled.