# PO rulings — Phase 4a (recorded 2026-09-19, user acting as PO in-session)

Six decisions, each with the recommendation shown and the PO's approval on the
record. These unlock Phase 2 (the latency drive) and Phase 3 (story closures).

| # | Decision | PO ruling | Consequence |
|---|---|---|---|
| 1 | **A2 `cap_basis`** — which clock the 3,000 ms cap keys on | **Harness clock** (caller speech end, including the ~600 ms endpointing window) | The cap figure includes the endpointing wait the caller experiences. Phase-2 runs are judged on `first_audio_ms` from speech end; the app's vad_end clock stays emitted per turn as a companion figure. Recorded in the harness's `cap_basis` and here. |
| 2 | **US-005 + US-004** (streaming TTS / streaming LLM) build scope | **Approve building behind flags now** | Timing-only changes behind `TTS_STREAM`/streaming flags; the batch path is retained for BRD-15 rollback. Adoption (what callers hear) stays gated on the DG-03 freeze. These are the only measured levers that reach the cap (TTS ≈ 750 ms of the median). |
| 3 | **US-009 + US-010** build scope | **Approve building behind flags now** | Floors ship disabled; nothing caller-facing changes. US-010's chunk-identity diff on the relevance set needs no ground truth. Adoption gated on DG-03 as designed. |
| 4 | **US-006 T-11/T-16 + US-007 T-17** — "observed at the moment a call arrives" | **`GET /ready?refresh=1` satisfies the wording** | A call-time read that re-reads `/api/ps` residency and both GPU clocks (never re-warms) is the observation. Closes US-006 T-11/T-16, US-007 T-17, and the unticked US-007 DoD box in one ruling. The harness performs the read at call time in Phase-2 runs (`--readiness-url`). |
| 5 | **US-014** (caller interruption, Class C) | **Enable with echo-suppression** | Caller audio keeps playing during TTS instead of being dropped; echo-suppression guards the mic bleed. Barge-in becomes possible pre-Wave-1. |
| 6 | **US-016 wording** | **Approve `BUSY_TEXT` / `FIXED_RESPONSE_TEXT` as written** | `text_approved_by` recorded. The N=3 window and unload harness flags remain for Phase 3.5. |

Implementation follows the approved plan: Phase 2 (2.1 re-profile → 2.2 US-005
streaming TTS first → US-004), Phase 3 story closures (3.3 US-008, 3.4 US-012,
3.5 US-016, 3.6 US-017, 3.7 US-001, 3.8 US-015, 3.9 US-018), US-014's
interruption work after the ruling, and Phase 4b (US-003 ground truths) still
to come as separate PO sittings.
