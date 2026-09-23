# US-014 — echo-gate operating point

> **Status: MEASURED at the shipped values, ON by default.** The sweep has been run
> (`doc/perf/tools/measure_us014_guard.py`); the numbers are in §3. Two limits stand and are stated
> rather than smoothed over: the echo path is **modelled, not measured on the carrier** (`echo_gain`
> is a declared assumption), and a caller far quieter than the passage they talk over is heard only
> about half the time. What is *not* yet measured is §5.1–5.4.
>
> `TAC-4` asks for thresholds "tuned on real speech and recorded, because an over-sensitive guard
> makes the agent answer the caller's cough and an under-sensitive one reproduces today's behaviour
> while claiming otherwise". Both halves of that sentence are checked below, on the app's own
> rendered speech.

## 1. The decision

`threshold = max(BARGE_IN_ABS_FLOOR, ref_env × BARGE_IN_ECHO_GAIN × BARGE_IN_MARGIN)`

| Key | Shipped | Status |
|---|---|---|
| `BARGE_IN_ENABLED` | `1` | **ON by default** — the voice yields (PO ruling). `0` is the `BRD-15` revert |
| `BARGE_IN_ECHO_GAIN` | `0.5` (−6 dB) | **DECLARED ASSUMPTION, not a measurement** — and deliberately the pessimistic end |
| `BARGE_IN_MARGIN` | `2.0` (+6 dB) | measured at this value, §3 |
| `BARGE_IN_ABS_FLOOR` | `150` | bounded by measurement, §4 |
| `BARGE_IN_MIN_FRAMES` | `6` (120 ms) | measured at this value, §3 — latency 8–9 frames |
| `BARGE_IN_REF_DECAY` | `0.95` / 20 ms tick (~270 ms memory) | chosen, not swept |

`ref_env` is the RMS of the audio the carrier is playing to the caller **at that moment**, advanced
on the playout clock (`_advance_reference`), not of the whole answer that was sent.

### Why the gain and the margin are two keys and not one ratio

They are two different quantities and only one of them is ours:

* **`ECHO_GAIN`** — how much of our own voice returns to us. A property of the carrier's acoustic
  path. `US-002` TAC-9 puts the real path out of local reach, so it cannot be tuned here; it can
  only be **declared** and swept. It is in the config, and in this record, so the assumption is
  visible instead of hidden inside a single number.
* **`MARGIN`** — how far above that echo the caller must be. Ours to choose.

Folding them into one ratio is what produced the first draft's fatal default, `ref_env × 2.0`. See
§2.

## 2. The rejected first operating point, and the measurement that rejected it

The first implementation used a single `BARGE_IN_ECHO_RATIO = 2.0`. Measured against the app's own
rendered speech — `doc/perf/tools/fixtures/*/turn*.ulaw`, 8 kHz µ-law, the same voice and the same
wire representation the live path uses — per-20 ms-frame RMS:

| fixture turn | frames | speech p10 | p50 | p90 | max | silent frames |
|---|---|---|---|---|---|---|
| `deadlines-02/turn0` | 167 | 231 | 2,151 | 3,858 | 6,063 | 21 |
| `deadlines-02/turn1` | 128 | 298 | 2,266 | 3,909 | 6,088 | 23 |
| `eligibility-03/turn0` | 192 | 271 | 2,188 | 4,729 | 6,856 | 36 |
| `eligibility-03/turn1` | 52 | 720 | 2,243 | 4,940 | 5,377 | 13 |
| `escalation-04/turn0` | 178 | 325 | 1,768 | 4,113 | 6,873 | 29 |
| `escalation-04/turn1` | 101 | 163 | 1,903 | 4,197 | 5,662 | 13 |
| `fees-01/turn0` | 163 | 214 | 1,657 | 4,379 | 6,053 | 23 |
| `fees-01/turn1` | 128 | 157 | 2,139 | 4,305 | 5,828 | 19 |
| `fees-01/turn2` | 80 | 189 | 2,456 | 4,227 | 5,109 | 12 |

The agent's own speech sits at p50 ≈ 2,000 and p90 ≈ 4,200 RMS — and **a caller's own speech sits at
the same p50/p90**, which is the entire difficulty of the problem: both sides of the echo are the
same signal class.

So `ref_env × 2.0` demands the caller beat the agent's p50 passage by more than their own p90. The
gate would have opened only during the agent's quiet passages: **enabled in configuration, behaving
as before in practice** — precisely the failure `TAC-4` names. Splitting the ratio into a declared
gain (0.5) and a margin (2.0) puts the effective threshold at ≈ `ref_env`, i.e. "the caller must be
at least as loud as the passage they are talking over", which is the intended operating point.

## 3. The sweep — results at the shipped operating point

`doc/perf/tools/measure_us014_guard.py`, 9 rendered fixture turns, one as the agent and the next as
the caller. Echo modelled as the agent's own turn delayed by `delay_ms` and attenuated by `gain_db`;
the caller mixed over it from frame 40 (~0.8 s in).

**Positive control first, because a tool that cannot report "heard" makes every negative
meaningless:** a caller over silence (echo −60 dB) is heard **9/9**, so the instrument detects
interruptions.

**False-admits — echo alone, no caller** (the `UC-09` E1 failure: the agent hearing itself and
cutting its own answer short):

| echo gain | −6 dB | −9 | −12 | −15 | −18 | −21 | −24 |
|---|---|---|---|---|---|---|---|
| false-admits | **0** | 0 | 0 | 0 | 0 | 0 | 0 |

Zero across the whole echo-gain range **and** across delays 0/20/40/80/160 ms. The gate never opened
on the agent's own voice — including at −6 dB, the pessimistic assumption the gain is set to.

**Caller over echo, at the agent's own level** (35 cells: 7 gains × 5 delays):

| | result |
|---|---|
| heard | **35/35** |
| detection latency | **8–9 frames (160–180 ms)** |

**Quieter callers**, at a −12 dB echo path:

| caller level | 0 dB | −6 dB | −12 dB |
|---|---|---|---|
| heard | 9/9 | 8/9 | 5/9 |

So the honest statement of the operating point: **a caller at or near the agent's own level always
takes the floor, within ~160 ms, and the agent never takes its own voice for a caller.** A caller
12 dB below the passage they are talking over — half the amplitude — is heard about half the time.
That is the residual limitation, and it is a consequence of the design: the gate requires the caller
to stand above the echo, and on a loud passage "above the echo" and "above a quiet caller" converge.
Closing it properly needs the real echo canceller (Option 3), not a looser threshold — loosening
`MARGIN` trades this limitation for false-admits, which is the worse failure.

## 4. Why the absolute floor is where it is

`is_silent` discards below 80 RMS (`app/voice_handler.py`), so the floor must be above 80 or the
gate admits frames the VAD then rejects as silence — a detection that cannot become a turn. The
quietest speech decile in the fixture table is 157, so the floor must be below that or a
softly-spoken interruption is gated out. 150 sits inside a band of [80, 157] — deliberately narrow,
because it is the only place the two thresholds must not overlap.

## 5. What is still NOT measured

1. **The carrier's real echo path.** Modelled here as "our voice, delayed and attenuated". Nothing
   in this repo has observed the real path; `US-002` TAC-9 excludes the carrier boundary from local
   measurement. The sweep bounds what the gate does across the gains the carrier *might* have; it
   cannot tell you which one it has. If the real gain is worse than −6 dB, `BARGE_IN_ECHO_GAIN` is
   optimistic and must be re-declared.
2. **In-situ confirmation.** A harness profile looping the app's own outbound audio back as inbound
   at the chosen gain, so the gate is observed on a live turn and not only offline. The pacing rule
   that used to justify the harness's post-turn quiet period was itself falsified by this story's
   RCA (see the note in `doc/perf/tools/load_harness.py`).
3. **The live line.** No real call has exercised this. The revert is one line
   (`BARGE_IN_ENABLED=0`) and `AC-3`'s failure scenario — enabling interruption degrades
   transcription because the agent transcribes itself — is the thing to watch in the first calls.
4. **Cross-session behaviour** at N=2 with both callers interrupting (`TAC-6`, `TAC-10`).

### A note on how this sweep was wrong three times

Worth recording, because each wrong version produced a confident number. (1) It advanced the playout
clock one tick per twenty frames instead of one per frame. (2) It installed that clock *after*
`mark_speaking`, so `_ref_at` held a real timestamp against a fake clock and every tick was negative
— the reference never advanced at all. (3) It queued the whole turn as a *single* playout element,
making the reference the turn's average RMS. Each bug reported a false-admit the gate does not
produce. The tool now carries a positive control for exactly this reason, and the fixes are
commented at the site. A threshold chosen from the broken versions would have been a threshold
chosen to fit a bug.
