> **Lens:** PO / TPO (technical ACs) / Architect (HLD, LLD) · **Engagement:** Brownfield — `D:\project\universityDemo`

# US-005 — Stream TTS synthesis onto the first-audio path [Lens: PO]

- **Status:** **BLOCKED - `DG-03` (adoption); BUILD APPROVED behind flags (2026-09-19, Phase 4a PO ruling)** — timing-only change (`TTS_STREAM` flag), batch path retained for BRD-15; adoption still gated on `DG-03` (changes what the caller hears)

- **Story:** As a **caller listening for a reply**, I want **the assistant's voice to start as soon as the first sentence is ready**, so that **I hear an answer forming rather than a single long silence followed by a whole paragraph**.
- **Business value:** The synthesis term is owned here and `06-architecture.md` §2 names it: "kokoro `create()` — batch API; the streaming variant exists in the installed library and is unused." The caller currently waits for a complete audio buffer before a single frame is written to the carrier.
- **Priority:** **Must** — TPO ordering note: depends on US-004 landing first (there is nothing to synthesise incrementally until generation streams), and it is the second half of the same user-visible improvement. Behind a single setting, so the revert is one config change (`BRD-15`).

## Acceptance Criteria [Lens: PO]

**AC-1.** Streaming synthesis is real, not nominal.

```gherkin
Scenario: The first audio chunk is emitted before the answer is fully synthesised
  Given streaming synthesis is enabled
  When a multi-sentence answer is spoken on a live call
  Then the first audio chunk reaches the carrier before the whole answer has been synthesised
  And the trace shows a measurable gap between the first-chunk mark and the completion mark

Scenario: Streaming synthesis is disabled
  Given the streaming setting is unset
  When the same answer is spoken
  Then the previous batch `create()` behaviour returns exactly
  And no code revert is required
```

**AC-2.** A synthesis failure is spoken, never silent.

```gherkin
Scenario: Synthesis returns nothing for a generated answer
  Given an answer exists and synthesis fails
  When the turn would speak it
  Then the caller hears the spoken fallback in that same turn
  And the other caller's in-flight turn is unaffected

Scenario: The speech models are under VRAM pressure
  Given VRAM pressure at two callers
  When synthesis runs
  Then TTS degrades to the CPU execution provider and still speaks
  And the turn is slower but never silent
```

**AC-3.** Chunk boundaries respect the carrier's framing.

```gherkin
Scenario: A chunk boundary falls mid-frame
  Given a streaming chunk ends part-way through a 20 ms µ-law frame
  When the chunk is resampled and framed
  Then the boundary is snapped to a frame boundary before framing
  And the carrier never receives a truncated frame
```

**AC-4.** The words do not change.

```gherkin
Scenario: The answer is spoken in more pieces
  Given a question is answered with streaming synthesis on and then off
  When the two transcripts of the spoken audio are compared
  Then the words, order and meaning are identical
  And only the timing of their delivery differs
```

Technical acceptance criteria [Lens: TPO] — performance, security, resilience checks the code must pass:

- TAC-1: **First chunk latency** — first synthesis chunk ≤ **400 ms** after the first complete sentence is available from generation.
- TAC-2: **Turn latency gain — a prediction with a decision rule, not a pass threshold.**
  - **Prediction:** streaming removes **300–600 ms** from p50 `processing_ms` versus the batch baseline (basis: the measured synthesis term in the current serial chain).
  - **Acceptance:** measure it with the `US-002` harness at N=1 over ≥100 turns in two separated windows, and report the actual delta.
  - **Decision:** the gain is *not* required to land inside 300–600 ms, because that range was derived from a prediction rather than from a requirement. The change is kept if it materially improves first-audio latency with no quality regression (`BRD-09`) and no resource breach (`BRD-12`); it is stopped and debugged if the measured gain is **under 50% of the prediction** (`WF-03` step 5). A gain *above* the range is a result, not a failure.
- TAC-3: **VRAM budget** — with two concurrent calls, peak VRAM stays at or below 90% of the measured 16,311 MiB device budget with no system-memory spill, read from the GPU and not inferred (`BRD-11`).
- TAC-4: **No cross-caller audio** — under the N=2 harness, zero occurrences of caller B receiving audio synthesised for caller A, across three consecutive runs. This is the `DG-06` isolation test discharged jointly with US-012.
- TAC-5: **Non-blocking streaming** — the streaming API is consumed without blocking the single event loop; caller B's turn is not delayed by caller A's synthesis (`REC-02`).
- TAC-6: **Frame discipline** — over a 60-second multi-chunk stream, zero partial frames are written to the carrier and no frame is more than 5 ms off its 20 ms slot.
- TAC-7: **Behaviour surface** — all 28 intents still transcribe and speak correctly after the change, judged end-to-end (`BRD-18`).
- TAC-8: **Reversibility** — disabling streaming synthesis by one setting restores the batch behaviour, demonstrated (`BRD-15`).

## HLD — Architecture Slice [Lens: Architect]

`MOD-04` owns both speech models and the TTS cache. Synthesis is the last serial term before the carrier; streaming it is a within-module change using the pinned library's own API.

```mermaid
flowchart LR
  subgraph MOD4[MOD-04 Speech Services]
    TXT[Answer clauses from MOD-03] --> CACHE{TTS cache<br/>key = hash(agent text)<br/>no PII in key}
    CACHE -->|hit| AUD[Cached audio copy]
    CACHE -->|miss| KOK[kokoro create_stream per clause<br/>24 kHz float32 async generator]
    KOK --> AUD
    AUD --> PCM[PCM float32 chunks]
  end
  PCM --> SNAP[Snap to frame boundary]
  SNAP --> RES[Resample 24 kHz to 8 kHz µ-law]
  RES -->|20 ms frames| WS[Carrier socket write main.py]
  WS --> MARK[first_audio_sent mark MOD-06]
  KOK -.first chunk.-> M1[tts_first_chunk mark]
  KOK -.VAD must not gate endpointing.-> RMS[RMS endpoint 600 ms MOD-01]
  VRAM[VRAM pressure] -.degrade.-> CPUP[CPU execution provider<br/>slower but available]
  CPUP --> AUD
  style KOK fill:#eef
```

- **Components touched:**
  - `MOD-04` / `app/voice_handler.py` — the `kokoro.create()` call site is replaced by consumption of the library's own streaming generator; the batch path is retained behind the setting.
  - `MOD-04` / `app/voice_handler.py` — the resample and µ-law framing path is unchanged, but a chunk boundary must land on a frame boundary before framing.
  - `MOD-04` — the execution-provider list is the VRAM-pressure lever: on pressure, TTS moves to the CPU provider rather than skipping speech.
  - `MOD-01` — the turn loop consumes audio chunks and writes frames as they arrive; the socket write is where `first_audio_sent` is stamped (US-001).
  - `MOD-06` — a `tts_first_chunk` mark is added beside `tts_done`, so streaming is measurable rather than asserted.
- **Interaction summary:**
  1. A complete clause arrives from generation (US-004); the cache is consulted on a hash of the agent's own text — no caller data is in the key.
  2. On a miss, `create_stream()` yields `(audio, sample_rate)` chunks as they are produced; on a hit, the cached copy is served immediately.
  3. Each chunk is snapped to a frame boundary, resampled 24 kHz → 8 kHz and framed as 20 ms µ-law; frames are written to the carrier socket as they are produced, so first audio is emitted while later clauses are still being synthesised.
  4. The `tts_first_chunk` and `tts_done` marks are recorded; `first_audio_sent` is stamped at the socket write.
  5. **Failure path:** a synthesis failure produces the spoken fallback for that turn, logged and traced as a degraded turn — the caller never hears silence; the other caller's turn is untouched because no shared mutable state exists on this path.
  6. **Failure path:** under VRAM pressure, the provider list switches TTS to CPU execution; the turn is slower and still spoken, and recovery reverts to GPU when VRAM frees.

## LLD — Implementation Detail [Lens: Architect]

- **Classes / functions / APIs** — verified against the pinned library on 2026-09-18: `kokoro-onnx==0.5.0` exposes `create_stream(text, voice, speed, lang, is_phonemes, trim)` returning `AsyncGenerator[tuple[ndarray, int], None]` — an async generator yielding `(audio, sample_rate)` pairs, alongside the batch `create(...)` used today.

```
# app/voice_handler.py  (MOD-04)

TTS_STREAM: bool = settings.tts_stream_enabled        # single setting; unset => batch create()

async def synthesise_stream(
    text: str,
    *,
    voice: str = "af_heart",
    speed: float = 1.0,
) -> AsyncIterator[tuple[np.ndarray, int]]: ...
    # yields (audio_f32, sample_rate) per clause-derived chunk
    # cache hit  -> yields the cached copy once, immediately
    # cache miss -> delegates to kokoro.create_stream(...) per clause, chunk = a sentence
    # raises SynthesisUnavailable when the provider is gone; never yields silence

async def _synthesise(text: str) -> np.ndarray | None: ...
    # the BATCH path, retained unchanged behind TTS_STREAM=0 (BRD-15 revert)

def _snap_to_frame_boundary(audio: np.ndarray, sample_rate: int) -> np.ndarray: ...
    # truncates/pads to a whole 20 ms frame at 8 kHz before µ-law framing

def _resample_and_frame(audio: np.ndarray, sample_rate: int) -> Iterator[bytes]: ...
    # 24 kHz -> 8 kHz -> 20 ms µ-law frames; unchanged in behaviour from today

class TTSCache:
    """DAT-11. Process-wide today; US-012 owns the isolation verdict."""
    MAX_ENTRIES: int = 50                                  # class constant today
    def get(self, text: str) -> np.ndarray | None: ...      # returns a .copy()
    def put(self, text: str, audio: np.ndarray) -> None: ...  # stores a .copy(); FIFO eviction
```

- **Data schema changes** — no durable store. The `DAT-11` cache entry shape is unchanged by this story (US-012 changes its *scope*, not its fields):

```python
# app/voice_handler.py - the call site, before / after
# BEFORE
#   audio = self._tts.create(text, voice="af_heart", speed=1.0)   # complete buffer, then resample
# AFTER
#   async for chunk, sr in synthesise_stream(text):
#       for frame in _resample_and_frame(_snap_to_frame_boundary(chunk, sr), sr):
#           await self._send_media_frame(frame)                   # first frame closes BRD-02's segment
```

- **Edge cases** — enumerated, each with the decided behavior:

| Edge case | Decided behavior |
|---|---|
| Synthesis returns `None` | The turn still reaches EMITTED with the spoken fallback; the condition is logged and traced as degraded; the caller never hears silence |
| VRAM pressure at N=2 | TTS moves to the CPU execution provider; slower but spoken. Recovery reverts to GPU when VRAM frees |
| Cache eviction under concurrency | FIFO by insertion order; a concurrent hit on an evicted key re-synthesises — a latency cost, never a correctness one. Covered by US-012's isolation test |
| Partial frame at a chunk boundary | Snapped to a frame boundary before µ-law framing (§`_snap_to_frame_boundary`); the carrier never receives a truncated frame |
| Endpointing vs the model's internal VAD | The internal VAD runs after the 600 ms RMS gate and must never gate it; a setting that could is a defect (`BRD-04`) |
| Synthesis input longer than the cap | Input is capped at 500 characters per utterance today; a longer clause is split, never truncated mid-word |
| Caller hangs up mid-synthesis | The audio buffer is discarded with the session; no partial transcript is written and no lead is fabricated |
| A clause yields a zero-length audio chunk | Skipped silently; the stream continues to the next clause rather than emitting an empty frame |
| Model load failure at boot | Reported by `MOD-07`'s readiness gate as a partial start, never as a silent success (`UC-06`) |
| Streaming requested but the library returns a batch array | Treated as a contract violation: the batch path is used and the trace records that streaming did not engage, so a false "streaming" claim cannot be made |

- **Error handling** — `SynthesisUnavailable` (provider gone or model unloaded; converts the turn to the spoken fallback — `BRD-13`), `SynthesisError` (a chunk-level failure; the clauses already spoken stand, and the remainder takes the fallback text), and `ProviderFallback` (a `note`, not an error: VRAM pressure moved synthesis to CPU and the trace records the degraded rung). No exception escapes into the turn loop: the caller hears either the answer or the fallback, and the trace always records which. `REC-01` is respected — the hand-rolled loop is the target and Pipecat is **not** adopted; its `KokoroTTSService` is noted only as evidence that the streaming pattern is sound, never as a wiring proposal.

- **Test scenarios** — unit / integration / e2e / load, one checkable line each:

| # | Level | Scenario | Covers UC-02 row |
|---|---|---|---|
| T-1 | unit | `synthesise_stream` on a three-sentence answer yields ≥3 chunks and the first does not contain the third sentence's audio | Happy path |
| T-2 | unit | `TTS_STREAM=0` routes to `_synthesise` and produces the identical audio array as today (TAC-8) | — (revert) |
| T-3 | unit | `_snap_to_frame_boundary` truncates a partial frame so every emitted frame is exactly 160 bytes | Invalid input (framing) |
| T-4 | unit | A cache hit yields immediately and yields a `.copy()`; mutating the returned array does not corrupt the cache | Duplicate request |
| T-5 | unit | A zero-length audio chunk is skipped and the stream continues | Partial data |
| T-6 | unit | A failing provider raises `SynthesisUnavailable` rather than yielding silence | Dependency failure E2 |
| T-7 | integration | A live turn's `tts_first_chunk` mark precedes `tts_done` by a measurable margin on a multi-sentence answer | Happy path |
| T-8 | integration | The first media frame for a turn is written while later clauses are still synthesising | Happy path |
| T-9 | integration | Noise-gated and closing turns still speak their fixed replies through the same path | Alternate path A1, A2 |
| T-10 | integration | The spoken text streamed on vs off is identical word-for-word (AC-4) | — (AC) |
| T-11 | integration | With the provider forced to fail, the caller hears the fallback and the other caller's turn is unaffected | Dependency failure |
| T-12 | integration | Forcing VRAM pressure switches the provider to CPU, the turn still speaks, and the trace notes the degraded rung | Partial completion |
| T-13 | integration | A caller hang-up mid-synthesis discards the buffer and fabricates no lead or transcript | Cancellation |
| T-14 | e2e | A live call answers a multi-sentence question with audible speech beginning before synthesis completes | Happy path |
| T-15 | load | N=1, ≥100 turns, two separated windows: p50 gain 300–600 ms vs the batch baseline (TAC-2); a gain under 50% of prediction stops the work | — (TAC / phase gate) |
| T-16 | load | First chunk ≤ 400 ms after the first complete sentence, over ≥100 turns (TAC-1) | — (TAC) |
| T-17 | load | N=2: peak VRAM ≤ 90% of 16,311 MiB with zero sysmem spill, read from the GPU (TAC-3) | Concurrent operation E2 |
| T-18 | load | N=2 three consecutive runs: zero occurrences of caller B hearing caller A's synthesised audio (TAC-4) | Concurrent operation |
| T-19 | load | 60-second multi-chunk stream: zero partial frames, no frame more than 5 ms off its 20 ms slot (TAC-6) | — (TAC) |
| T-20 | load | All 28 intents still transcribe and speak end-to-end after the change (TAC-7) | Happy path (behaviour surface) |

## Traceability
- Parent module: `MOD-04` (Speech Services)
- Technical requirement: `TRD-13` (streaming synthesis on the first-audio path, using the library's own API)
- Use case: `UC-02` (receive a grounded answer) — every `✓` row of its Scenario Coverage table that this story touches is covered by T-1…T-20; `UC-03` is touched through the N=2 isolation assertions
- Business requirement: `BRD-02` (the synthesis term of the latency target); contributes to `BRD-05` (two callers), `BRD-06` (isolation, proven jointly with US-012), `BRD-11` (the VRAM budget), `BRD-13` (a speech failure is heard as a fallback), `BRD-15` (one-setting revert) and `BRD-18` (28 intents preserved)
- Data gap / state machine: implements the `SM-02` SYNTHESISING → SENDING transition, which today cannot begin until a full audio buffer exists; touches `DAT-11` (the cache is consulted on this path) and `DAT-10` (transient audio buffers)
- Reconciliation: `REC-01` (Pipecat is **not** adopted — its `KokoroTTSService` already streams, which is an argument for the pattern, not for the pipeline; the hand-rolled loop stays the target)
- Related workflow: `WF-01` step 9 and step 10, and the partial-completion row for step 9 (synthesis fails → **gap** today, spoken fallback after this story)

## Definition of Done
- [ ] All ACs pass (AC-1 … AC-4, TAC-1 … TAC-8)
- [ ] Tests from the LLD test scenarios pass (T-1 … T-20)
- [ ] Perf/load test passed against the story's TACs (TAC-1 ≤400 ms first chunk, TAC-2 gain measured and compared to the 300–600 ms prediction with the ≥50% decision rule applied — the range is not itself a pass condition, TAC-3 VRAM ≤ 90% at N=2, TAC-6 framing discipline)
- [ ] Schema migration applied — n/a (no durable store; `DAT-11`'s entry shape is unchanged by this story)
- [ ] Module docs updated if contracts changed — `MOD-04` B.3 (`speech.synthesise(text) -> AsyncIterator[(pcm_f32, sample_rate)]`) and B.6 (edge-case table) if the implementation differs from `TRD-13`
- [ ] `BRD-15` rollback demonstrated: unsetting the streaming setting restores `create()` exactly
- [ ] `DG-06`'s TTS-side isolation verdict recorded (executed jointly with US-012) before any `BRD-06` claim is made
