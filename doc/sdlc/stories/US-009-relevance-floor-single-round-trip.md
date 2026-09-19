> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-009 — Relevance floor enforced without a second round trip [Lens: PO]

- **Story:** As a **caller asking about tuition or a deadline**, I want **the assistant to say it does not have that information rather than answer from an unrelated chunk**, so that **I am never given a confident wrong number about money or dates**.
- **Business value:** `BRD-10` requires a threshold below which retrieved context is treated as not relevant. Today the gate exists in code and is switched off (`RAG_SIMILARITY_THRESHOLD=0.0`), so "I don't know" is unreachable and the assistant grounds on whatever came back. A confident wrong answer about a fee is worse than a refusal.
- **Priority:** **Must** (with a stated dependency) — TPO ordering note: the floor **ships calibrated-pending**. It is buildable and testable today; its *value* is unadoptable until the frozen set exists and the PO has approved critical-intent ground truth (`DG-03`, US-003). `BRD-10` is only fully satisfied once `DG-03` clears, and that is recorded rather than implied.

## Acceptance Criteria [Lens: PO]

**AC-1.** Refusal is reachable.

```gherkin
Scenario: A question with no relevant content in the knowledge base
  Given the caller asks something the knowledge base does not cover
  When retrieval returns its best chunk below the floor
  Then the module returns an explicit not-relevant signal
  And the assistant says it does not have that information
  And the trace records the result as below-floor

Scenario: A question with relevant content
  Given the caller asks something the knowledge base covers
  When retrieval returns chunks at or above the floor
  Then the chunks are used as context as before
  And no refusal is produced
```

**AC-2.** Enabling the floor does not double retrieval traffic. This is `REC-08`'s constraint.

```gherkin
Scenario: A turn retrieves with the floor enabled
  Given the floor is enabled
  When a turn performs retrieval
  Then exactly one retrieval round trip is issued for that turn
  And the per-turn MCP request count is unchanged from the floor-disabled baseline

Scenario: The floor is disabled
  Given the floor is returned to its disabled value
  When a turn retrieves
  Then today's behaviour returns exactly
  And no code revert is required
```

**AC-3.** The three outcomes are distinguishable in the record.

```gherkin
Scenario: A retrieval produces no results
  Given the store returns an empty result set
  When the turn's record is emitted
  Then the outcome is recorded as "no results"
  And it is distinguishable from "results below floor"

Scenario: A retrieval times out
  Given the retrieval exceeds its deadline
  When the turn's record is emitted
  Then the outcome is recorded as "timed out"
  And it is distinguishable from both of the above
```

**AC-4.** The floor is a decision the Product Owner can see.

```gherkin
Scenario: The floor value is chosen
  Given the frozen set is available and the PO has approved critical-intent ground truth
  When the floor is calibrated
  Then the value and the evidence behind it are recorded
  And no value is adopted while the frozen set blocks

Scenario: The floor is enabled before its value can be calibrated
  Given the frozen set is not yet available
  When the floor ships
  Then it ships calibrated-pending, with the value recorded as provisional
  And the adoption of a final value is a PO decision
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **Exactly one retrieval round trip per turn** with the floor enabled — measured as the MCP `tools/call` count per turn, identical to the floor-disabled baseline (`REC-08`).
- TAC-2: **No added latency** — enabling the floor does not raise retrieval p95 above the 400 ms allowance; the distance information is already in the primary response.
- TAC-3: **The floor is enforced from the response already in hand** — the second `top_k=1` call in `_threshold_distance` is removed, not merely made conditional.
- TAC-4: **Refusal is grounded, not decorative** — a below-floor result produces the explicit not-relevant marker in the prompt, and the assistant's reply contains no factual claim from the rejected chunk. Asserted by checking the reply against the rejected chunk's distinctive tokens.
- TAC-5: **No cross-caller effect** — the floor is per-request state; caller A's below-floor refusal does not change caller B's retrieval (asserted at N=2).
- TAC-6: **Reversible by configuration** — reverting the floor setting restores today's `0.0` behaviour exactly, without a code revert (`BRD-15`).
- TAC-7: **The floor cannot be bypassed by a cache** — no semantic cache is wired on this path (`REC-04` notes a cache on this path would be a correctness risk under `BRD-10`, because a cached answer from a lower-floor query would bypass the floor).
- TAC-8: **Load** — an N=2 run with the floor enabled shows the per-turn MCP request count unchanged, retrieval p95 within allowance, and no turn exceeding the per-turn cap.

## HLD — Architecture Slice [Lens: Architect]

The gate exists at `rag.py:163` and is switched off. **The obvious implementation is wrong**: `_threshold_distance` issues a *second* MCP `tools/call` with `top_k=1`, so enabling the gate naively doubles per-turn retrieval traffic to answer a question the first response already answered.

```mermaid
flowchart LR
  Q[Query text] --> DISP{Dispatcher USE_MCP_RAG}
  DISP -->|auto or on| MCP[MCP client :8010]
  DISP -->|off| LEG[Legacy local]
  MCP --> ERC[ERC hybrid engine]
  ERC --> RRF[RRF fusion alpha 0.3]
  RRF --> RESP[self-contained response:<br/>chunks + distances]
  RESP --> FLOOR{Relevance floor}
  LEG --> RESP2[Legacy response:<br/>chunks + distances]
  RESP2 --> FLOOR
  FLOOR -->|at or above floor| FMT[Format with section labels]
  FLOOR -->|below floor| NOTREL[Explicit not-relevant signal]
  FMT --> OUT[Ranked chunks]
  NOTREL --> PROMPT[No-information marker in the prompt]
  OUT --> TR[MOD-06 note: outcome + best distance + rung]
  NOTREL --> TR
  WRONG[REMOVED: _threshold_distance<br/>second tools/call top_k=1] -.x.-> MCP
  style WRONG fill:#fee
  style FLOOR fill:#eef
```

- **Components touched:**
  - `MOD-02` / `app/rag.py` — the relevance gate is enabled from the distance information **already present in the primary retrieval response**; the second `top_k=1` call is removed.
  - `MOD-02` / `app/rag.py` — a below-floor result returns the explicit not-relevant signal on `RetrievalResult` (`not_relevant`, `best_distance`) rather than an empty list, so callers can distinguish it from "no results".
  - `MOD-02` / `app/rag_legacy.py` — the fallback path returns comparable distances, because a floor is only meaningful if both paths return the same distances for the same question (`DG-01`'s precondition, satisfied in full by US-010).
  - `MOD-01` / `app/voice_system_prompt.py` — the existing "no information" marker section is substituted on a not-relevant result; the prompt text is unchanged, only the branch that reaches it becomes reachable.
  - `MOD-06` — the trace records the outcome (`no_results` | `below_floor` | `timed_out`), the best distance and the rung.
- **Interaction summary:**
  1. A query is dispatched to the primary path (or the local fallback, per `USE_MCP_RAG`).
  2. The response carries the chunks **and** their distances; the floor is evaluated against those distances — no second call is issued.
  3. At or above the floor, chunks proceed to formatting and into the prompt as today.
  4. **Failure path:** below the floor, the explicit not-relevant signal is returned and the no-information marker is substituted; the assistant says it does not know, and the trace records `below_floor` with the best distance.
  5. **Failure path:** an empty result set is *not* an error but a different outcome — recorded as `no_results`, distinct from `below_floor`.
  6. **Failure path:** a timeout takes the degraded rung (US-013) and is recorded as `timed_out`, distinct from both.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — Python 3.11:

```python
# app/rag.py  (MOD-02)

RAG_SIMILARITY_THRESHOLD: float = settings.RAG_SIMILARITY_THRESHOLD   # 0.0 today = disabled

@dataclass(frozen=True)
class Chunk:
    text: str
    section_label: str          # must be identical on every path (DG-01)
    distance: float             # lower = closer; present in the primary response already
    source_ref: str

@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[Chunk, ...]
    rung: Literal["primary", "keyword_only", "local"]
    outcome: Literal["ok", "no_results", "below_floor", "timed_out"]
    not_relevant: bool          # True => substitute the no-information marker
    best_distance: float | None

def _apply_floor(chunks: Sequence[Chunk], threshold: float) -> tuple[tuple[Chunk, ...], bool]:
    """Enforce the floor from distances already in hand. ONE round trip (REC-08)."""
    if threshold <= 0.0:
        return tuple(chunks), False                  # floor disabled: today's behaviour exactly
    keep = tuple(c for c in chunks if c.distance <= threshold)
    if not keep:
        return (), True                              # explicit not-relevant, not an empty list
    return keep, False

# REMOVED by this story - the second MCP call that answered a question the
# first response already answered:
#   def _threshold_distance(query: str) -> float:
#       resp = mcp_call_tool("retrieve_context", {"query": query, "top_k": 1})   # <-- second round trip
```

- **Data schema changes** — none. `DAT-01` is the only corpus in scope; the change is a predicate over distances the response already carries. The `DAT-07` record gains an outcome note (see US-001):

```jsonc
// DAT-07 record fragment
{ "retrieval_outcome": "below_floor", "retrieval_best_distance": 0.71, "retrieval_rung": "primary" }
// "no_results" and "timed_out" are distinct values, never merged into "below_floor"
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| Empty result set | Not an error: the no-information marker is substituted and the assistant says it does not know. Recorded as `no_results`, distinguished from `below_floor` |
| Chunks below the floor | Rejected; the same not-relevant path; the trace records the best distance |
| Floor disabled (`threshold <= 0.0`) | Today's behaviour returns exactly, including today's willingness to ground on unrelated chunks |
| Query empty or whitespace | No retrieval at all; the existing `if not question.strip(): return None` guard is kept |
| Distances absent from a response | Treated as a retrieval failure and takes the degraded rung — never as "above floor". A missing distance must not silently disable the floor |
| Keywords-only rung (BM25, no dense leg) | Distances may be unavailable on that rung; the floor is then reported as **not applied** on the trace, never as passed |
| Two callers, one below floor | Per-request state; caller B's retrieval is unaffected |
| The floor rejects everything for a whole intent | A calibration signal, surfaced from the trace and fed back to the floor's value — the mechanism by which a too-aggressive value is caught |
| A cached answer would bypass the floor | No cache is wired on this path; `REC-04` records the semantic cache as unreachable and this story does not wire it (`BRD-10` correctness risk) |
| Threshold malformed | Start reports the parse failure rather than defaulting silently (`BRD-16`) |

- **Error handling** — this story adds no new error class. A below-floor result is **not** an error: it is `outcome="below_floor"` and a valid turn that ends in the assistant saying it does not know. A missing distance is the one genuinely ambiguous input and it fails **closed**: unavailable distance information takes the degraded rung rather than assuming relevance, because assuming relevance is exactly the defect `BRD-10` exists to fix. The floor's value being provisional (`DG-03` blocked) is recorded in configuration and in the adoption note, not silently treated as final.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-02 row |
|---|---|---|---|
| T-1 | unit | `_apply_floor` with `threshold=0.0` returns all chunks and `not_relevant=False` (disabled = today exactly) | Happy path |
| T-2 | unit | `_apply_floor` with chunks all above threshold returns `()` and `True`, not an empty-tuple-with-false | Missing data |
| T-3 | unit | `_apply_floor` keeps the subset at or below threshold and leaves the order unchanged | Partial data |
| T-4 | unit | A response carrying no distances on the keyword-only rung reports the floor as "not applied", never "passed" | Invalid input |
| T-5 | unit | The disabled setting reproduces today's behaviour byte-for-byte on a fixture (TAC-6) | Duplicate request |
| T-6 | integration | A turn with the floor enabled issues exactly one MCP `tools/call` (TAC-1) — asserted by counting calls at the MCP client | Duplicate request |
| T-7 | integration | A below-floor turn produces the no-information marker in the assembled prompt and the assistant refuses (TAC-4) | Missing data |
| T-8 | integration | An at-or-above-floor turn grounds normally and produces no refusal | Happy path |
| T-9 | integration | `no_results`, `below_floor` and `timed_out` appear as three distinct outcomes in the record | Timeout |
| T-10 | integration | The reply to a below-floor question contains none of the rejected chunk's distinctive tokens | Partial data |
| T-11 | integration | A noise-gated turn never reaches retrieval, so the floor is not consulted | Alternate path A1 |
| T-12 | integration | A clarification-forced turn (A3) still retrieves and grounds normally | Alternate path A3 |
| T-13 | integration | A retried turn re-queries and re-applies the floor; no cached result bypasses it | Retry |
| T-14 | integration | The embedding hop failing downgrades to keyword-only and the floor's status is honestly reported | Dependency failure |
| T-15 | integration | `/ws/voice/text` obeys the same floor after the change (`REC-09`) | — (regression guard) |
| T-16 | e2e | A live call asking an uncovered question hears "I don't have that information" rather than a wrong answer | Missing data |
| T-17 | load | N=2 with the floor enabled: per-turn MCP request count unchanged, retrieval p95 within the 400 ms allowance, no turn above the cap (TAC-1, TAC-2, TAC-8) | Concurrent operation |
| T-18 | load | Caller A below floor and caller B above floor in the same window: neither affects the other (TAC-5) | Concurrent operation |
| T-19 | load | Recovery: after a degraded rung, the next turn returns to the primary path and the floor is applied again | Recovery |
| T-20 | load | Partial completion: a turn whose retrieval was abandoned at its deadline still emits a record stating the outcome | Partial completion |

## Traceability
- Parent module: `MOD-02` (Retrieval & Grounding — "decide when context is not good enough to answer from")
- Technical requirement: `TRD-08` (enforce a relevance floor without paying a second round trip)
- Use case: `UC-02` (receive a grounded answer) — the `✓` rows this story touches (happy path, alternate paths A1/A3, invalid input, missing data, partial data, duplicate request, timeout, dependency failure, retry, concurrent operation, recovery, partial completion) are covered by T-1…T-20. `UC-02` E1 (chunks below any relevance floor → the model may ground on unrelated content) is the exception flow this story closes
- Business requirement: `BRD-10` (retrieval grounding — "so the assistant says it does not know rather than answering from an unrelated chunk"); contributes to `BRD-09` (grounding quality is a quality-gate input, scored by US-003) and `BRD-15` (revertible by configuration)
- Data gap / state machine: `DG-01` (the floor is only meaningful if both paths return the same distances for the same question — this story satisfies it on the primary path and US-010 completes it by consolidation); `DG-03` is the blocker on the floor's *value* (`BRD-08` requires the evidence for it)
- Reconciliation: **`REC-08`** (central — the relevance gate is disabled and enabling it naively doubles retrieval traffic; the distance check must fold into the existing response); `REC-04` (no cache is wired on this path, and a cache would be a `BRD-10` correctness risk); `REC-09` (the text path shares this code); `REC-07` (the stale RAG report calls the threshold "DEAD CONFIG — never read"; it *is* read, and its figures are excluded as evidence)
- Related workflow: `WF-01` step 6 (retrieval) and `UC-01`'s missing-data row (retrieval returns nothing → explicit "no information" marker)

## Definition of Done
- [ ] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-8)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-20)
- [ ] Perf/load test passed against the story's TACs (TAC-1 one round trip per turn, TAC-2 retrieval p95 within the 400 ms allowance at N=2, TAC-5 no cross-caller effect)
- [ ] Schema migration applied — n/a (no durable store; the `DAT-07` outcome note is US-001's record)
- [ ] Module docs updated if contracts changed — `MOD-02` B.3 (`retrieve_context` row: "returns ranked chunks **or** an explicit not-relevant signal") and B.6 (edge-case table) if the implementation differs from `TRD-08`
- [ ] `BRD-15` rollback demonstrated: reverting the threshold setting restores today's `0.0` behaviour with no code revert
- [ ] **Floor value recorded as calibrated-pending until `DG-03` clears**; the final value is adopted on the frozen set with PO-approved critical-intent ground truth, and the adoption is recorded with the numbers behind it (`BRD-08`)
