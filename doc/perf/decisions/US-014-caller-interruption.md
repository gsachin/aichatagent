> **Lens:** Architect (options, costs, evidence) + TPO (feasibility, sequencing) · **Decided by:** **Product Owner** · **Class: C** (changes user-perceivable turn-taking) · **Inputs:** `01-brd.md` BRD-19, `03-data-state-analysis.md` SM-01/SM-02, `07-brownfield-reconciliation.md` REC-01, `09-intent-catalog.md` row 22 · **Closes:** `CV-01`

# Decision Record — Caller interruption (`US-014`)

## STATUS: DECIDED — Option 2 (enable with a bounded guard)

Adopted in `doc/perf/decisions/2026-09-19-po-rulings-phase-4a.md` ruling 5, recorded here in the
machine-readable form `TAC-1` requires:

```jsonc
{ "decision": "enable_with_guard",
  "class": "C",
  "signed_off_by": "Product Owner (2026-09-19 Phase 4a ruling, ruling 5)",
  "date": "2026-09-22",
  "options_considered": ["keep_and_document", "enable_with_guard",
                         "enable_with_echo_suppression"],
  "artifacts_touched": ["app/main.py", "app/voice_handler.py",
                        "app/voice_system_prompt.py", "app/config.py"] }
```

**What was adopted.** The Option 2 in the table below — not Option 3, and the distinction is
load-bearing. There is **no acoustic echo cancellation**: the gate is a bounded, reference-aware
energy test, and the carrier's echo path is a *declared assumption* (`BARGE_IN_ECHO_GAIN`), not a
measurement. The Phase 4a ruling's phrase "enable with echo-suppression" is the same decision
labelled from the story's option set; the reconciliation is recorded here rather than by editing a
signed ruling.

**It is ON by default.** The PO ruling is that the voice yields: a caller who speaks over the agent
takes the floor. `BARGE_IN_ENABLED=1` is the shipped value and the code default, recorded in `.env`
so it is visible; `0` is the `BRD-15` revert and is a one-line configuration change.

The gate's operating point is **measured**, not assumed —
`doc/perf/tools/measure_us014_guard.py` over the app's own rendered speech, recorded in
`doc/perf/us014-echo-guard-tuning.md`: 0 false-admits on the agent's own voice across echo gains
−6…−24 dB and delays 0…160 ms, and a caller at the agent's own level heard in 35/35 cells at 8–9
frames (~160 ms). Two limits are stated there rather than smoothed over: the carrier's echo path is
**modelled, not measured** (`ECHO_GAIN` is a declared, deliberately pessimistic assumption), and a
caller ~12 dB quieter than the passage they talk over is heard about half the time. What remains
unmeasured: in-situ loopback confirmation, any live call, and N=2 isolation with both callers
interrupting.

---

## 1. The contradiction being resolved

`CV-01`, verified in code:

| Side | What it says | Where |
|---|---|---|
| **The prompt** | "Immediately yield to the caller… Treat the caller's new utterance as the highest-priority current input" | `app/voice_system_prompt.py` §211 *Interruption Handling*, §502 *Interrupted Agent Responses* |
| **The architecture** | While the agent speaks, every inbound audio frame is **discarded** and the partial buffer is reset | `app/main.py:624–627`; `MUTE_STT_DURING_TTS` defaults to `1` (`main.py:87–91`), not overridden in `.env` |

The agent is instructed to do something the platform makes impossible. The caller who interrupts is not heard, while the assistant has been told to act as though they were.

**CORRECTION, 2026-09-22 — the "architecture" column above was wrong, and the code was worse than
the record.** That guard *could not execute*. It sat at the WebSocket receive site and its
`tts_playing` flag was True only while the turn was running — and the turn runs *inside* the
receive loop, so the loop was never reading the socket while the flag was set. `BARGE_IN_DETECTED`
appears **0 times** in 13.7 MB of live call logs (2026-09-19 → 2026-09-22), which is what a dead
branch looks like from outside.

The real behaviour was neither "yield" nor "discard" but **defer and replay**: frames arriving
mid-turn were buffered by the ASGI layer and delivered after playback, where `feed_audio` took them
as a fresh caller turn. The 2026-09-22 call shows it — `AGENT_SPEECH_STARTED chunks=1376` →
`AGENT_SPEECH_STOPPED` **13 ms later**, with 27.5 s of audio still to play out of the carrier's
buffer, and the caller speaking 3 ms after queueing began:

```
15:51:38,264  TTS generated (27.5s at 24000 Hz)
15:51:38,286  turn=6 AGENT_SPEECH_STARTED chunks=1376
15:51:38,299  turn=6 AGENT_SPEECH_STOPPED        <- 13 ms: written to socket, not played
15:51:38,302  turn=6 USER_SPEECH_STARTED         <- the caller, talking over it
15:51:38,308  turn=7 UTTERANCE_PROCESSING frames=98   <- answered, late, as a fresh turn
```

Three consequences the record's original framing missed, all now fixed or recorded:

1. The caller's interruption was answered **late and out of context**, as a new question, after the
   agent had finished a script they were talking over.
2. `AGENT_SPEECH_STOPPED` meant "handed to the socket", never "the caller stopped hearing us" — a
   telemetry statement that was false in the same direction as the defect, which is why four days of
   logs and every perf run failed to surface it.
3. The same root cause killed the stream with **WS 1011**: a handler busy in a thread reads nothing,
   so the carrier's keepalive pings go unanswered (`doc/perf/tools/_ws1011_probe.py` had already
   diagnosed exactly this; the two harness-clean cold N=2 runs in `doc/perf/runs` died that way).

The estimate of "roughly 300–500 prompt tokens" describing unreachable behaviour was also an
overestimate. Measured against the engine (`llama3.2:3b`, `prompt_eval_count`, 2026-09-22):

| Assembled prompt | `prompt_eval_count` | chars |
|---|---|---|
| before (as shipped) | 3,615 | 17,832 |
| after — `BARGE_IN_ENABLED=0` (the shipped default) | 3,595 | 17,665 |
| after — `BARGE_IN_ENABLED=1` | 3,635 | 17,885 |

The net change is −20 tokens in the OFF configuration and +20 in the ON one: the unreachable
*claims* are removed and the honest contract replaces them, so the saving is far smaller than the
story assumed — and in the enabled configuration the prompt is slightly *longer*, which the story's
"removing 300–500 tokens" framing did not anticipate. This is why `TAC-2` says the counts are
recorded rather than estimated.

## 2. Why the guard exists (do not treat it as a bug)

`main.py:87–91` states it plainly: Twilio `<Stream>` has no echo-cancellation attribute, so while TTS plays, the microphone re-captures the agent's own voice. Without the guard, the transcript of "Listen to her again" artifacts gets fed back to the model — the agent answers itself, then answers that answer. **The guard is a workaround for a missing carrier feature, not an oversight.**

Any option that removes the guard must answer what replaces it. Option 2's answer is the gate in
§5: the reference signal is already in hand because we synthesised the µ-law we sent, so admission
can be decided against what the caller is hearing *now*, without an acoustic canceller.

## 3. Options

### Option A — Keep the behaviour, correct the prompt *(recommended as the immediate step)*

Remove the two interruption sections from the system prompt so it stops describing unreachable behaviour; document the limitation.

| | |
|---|---|
| **Effort** | ~2 hours — a prompt edit plus a re-score |
| **Latency side-effect** | **Positive.** Removes ~300–500 tokens from the static prompt. Note the measured A4 result: the static prefix is **prefix-cached** (2,909 ms → 50 ms on an identical repeat), so this is a *turn-1* saving, not a per-turn one — modest, and it should not be sold as a latency win |
| **Quality risk** | Changes model input → Class B → requires the frozen golden set (`DG-03`), which does not yet exist |
| **Caller experience** | Unchanged — still cannot interrupt |
| **Risk** | Low. Reverting is a prompt revert |
| **Honest framing** | This does not fix the caller's problem. It stops the system *lying about itself*. |

### Option B — Enable interruption with a confidence guard *(not recommended)*

Set `MUTE_STT_DURING_TTS=0` and gate acceptance on VAD confidence plus a short refractory period after TTS starts.

| | |
|---|---|
| **Effort** | ~10 minutes of config, days of tuning |
| **Risk** | **High.** The feedback loop is real and the failure mode is bad: self-transcription produces an agent that talks to itself. A confidence threshold is a *heuristic* defence against an *acoustic* problem |
| **Tunable via** | Threshold sweeps on the harness; false-cutoff and self-transcription rates measured |
| **Why not recommended** | It trades a certain, benign limitation (caller must wait) for an uncertain, embarrassing one (agent answers its own voice). It is worth an **experiment on the harness**, not a production default |

### Option C — Real echo cancellation, then genuine barge-in *(the eventual answer)*

Suppress the agent's own voice acoustically or digitally so the caller's audio can be accepted continuously, then enable interruption properly.

| | |
|---|---|
| **Effort** | 3–5 days, plus real-call validation |
| **Approaches** | (a) software AEC (WebRTC AEC / Pipecat's audio filters) on the inbound stream; (b) keep the guard but replace it with **reference-signal cancellation** — subtract the known TTS output from the inbound frame rather than discarding everything |
| **Why it is the right answer** | It is the only option that gives the caller what the prompt already promises, and it removes a workaround rather than tuning it |
| **Why not now** | It cannot be validated with the local harness alone — echo is a *carrier-path* property (`US-002` TAC-9), so it needs real calls, and the harness cannot reproduce it |
| **Interaction** | `REC-01`: Pipecat ships audio filters that do exactly this, and Pipecat is already installed and pinned. This is the one place where the dead dependency might earn its place — as a *component*, not as a re-platform. That evaluation belongs in Option C's scoping, not in this decision |

## 4. Recommendation

**Adopt Option A now; scope Option C as its own story; run Option B only as a harness experiment, never as a production default.**

Rationale, in the order the plan's rules require:

1. **The contradiction must be closed regardless of which behaviour wins.** `BRD-19` requires that prompt and code agree. Option A closes it either way and is the only option that can ship before `DG-03` clears.
2. **Streaming partially mitigates the symptom.** When `US-004`/`US-005` land, the TTS playback window shrinks and the mute window with it — the caller's window to be ignored gets smaller even while the behaviour is unchanged. That weakens the urgency for Option B, which is the risky one.
3. **Option B's downside is asymmetric.** Failing to interrupt is a minor frustration; an agent that converses with its own echo on a live admissions line is a demo-ending failure. The plan's priority order puts correctness above latency, and this is a correctness question.
4. **Option C is the only real fix and it needs real calls to validate** — so it should be scoped honestly rather than smuggled in as a config change.

## 5. Decision required from the PO

| Question | Why it is yours |
|---|---|
| Adopt Option A's prompt correction now, accepting the `DG-03` gate? | It is a Class B change to model input |
| Fund Option C (3–5 days + real-call validation)? | It is the only path to the capability the prompt promised |
| May Option B ever be a default on the live line? | Engineering recommends **no**; if the answer is yes, it needs explicit acceptance of the self-transcription risk |

## 6. What would change this recommendation

- **If** real calls show the echo guard is not actually needed on the production carrier path (the artifact may be specific to speakerphone/handset conditions), **then** Option B's risk collapses and it becomes the cheap answer. **Untested — and it must be tested with real calls, not the harness.**
- **If** Option C's software AEC measurably suppresses the agent's own voice in a reference-signal test, Option C's effort estimate drops and it should be pulled forward.
- **If** `DG-03` clears before this decision is taken, Option A can ship immediately rather than waiting.

## 7. What this record does not do

It does not adopt anything. `BRD-19` remains unmet, `CV-01` remains open, `US-014` remains unstarted, and `09-intent-catalog.md` row 22 still describes a capability the architecture prevents. Those all stay true until a PO decision is recorded above.
