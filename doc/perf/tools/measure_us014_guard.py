#!/usr/bin/env python
"""US-014 TAC-4 — the echo-gate sweep. Standalone; no network, no models.

Question this answers, before `BARGE_IN_ENABLED` becomes a default: at the
shipped operating point, does the gate (a) refuse to open on our own voice, and
(b) still open for a caller talking over it? `TAC-4` names both failures —
"an over-sensitive guard makes the agent answer the caller's cough and an
under-sensitive one reproduces today's behaviour while claiming otherwise".

Material: the app's own rendered fixture turns (`fixtures/*/turn*.ulaw`) — real
Kokoro speech, 8 kHz µ-law, the same voice and wire format the live path uses.
One turn is the AGENT (the reference we sent); another is the CALLER.

The echo path is MODELLED, not measured: echo = reference, delayed by
`delay_ms` and attenuated by `gain_db`. `US-002` TAC-9 puts the carrier's real
path out of local reach, which is exactly why the gain is swept here instead of
assumed. This tool cannot tell you the carrier's gain; it tells you what the
gate does across the range of gains the carrier might have.

Read the output as three numbers per cell:
  false-admit  echo-only frames that opened the gate   (must be 0)
  heard        the caller's interruption was admitted  (must be yes)
  latency      frames from the caller's onset to admission

Run:  .venv/Scripts/python.exe doc/perf/tools/measure_us014_guard.py
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

import app.voice_handler as vh  # noqa: E402

FRAME = 160                     # 20 ms of 8 kHz µ-law
GAINS_DB = (-6, -9, -12, -15, -18, -21, -24)
LEVELS_DB = (0, -6, -12)        # the caller, relative to full scale
DELAYS_MS = (0, 20, 40, 80, 160)
ONSET_FRAME = 40                # the caller starts talking here (0.8 s in)


def load(name: str) -> bytes:
    raw = open(name, "rb").read()
    return raw[: len(raw) // FRAME * FRAME]


def frames(audio: bytes) -> list[bytes]:
    return [audio[i:i + FRAME] for i in range(0, len(audio), FRAME)]


def scale(audio: bytes, gain: float) -> np.ndarray:
    pcm = np.frombuffer(vh.ulaw_to_pcm(audio), dtype=np.int16).astype(np.float64)
    return pcm * gain


def mix(*parts: np.ndarray) -> bytes:
    total = np.sum(parts, axis=0)
    return vh.pcm_to_ulaw(np.clip(total, -32768, 32767).astype(np.int16).tobytes())


def fixture_turns() -> list[tuple[str, bytes]]:
    out = []
    for path in sorted(glob.glob(os.path.join("doc", "perf", "tools", "fixtures",
                                              "*", "turn*.ulaw"))):
        label = os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
        out.append((label, load(path)))
    return out


def build_inbound(agent_f: list[bytes], caller_f: list[bytes], gain_db: float,
                  level_db: float, delay_ms: int) -> list[bytes]:
    """One inbound stream: our own voice coming back, plus the caller over it."""
    gain = 10 ** (gain_db / 20.0)
    level = 10 ** (level_db / 20.0)
    lag = delay_ms // 20
    out: list[bytes] = []
    for i in range(len(agent_f)):
        echo_idx = i - lag
        echo = (scale(agent_f[echo_idx], gain) if echo_idx >= 0
                else np.zeros(FRAME, dtype=np.float64))
        if i >= ONSET_FRAME:
            j = min(i - ONSET_FRAME, len(caller_f) - 1)
            voice = scale(caller_f[j], level)
        else:
            voice = np.zeros(FRAME, dtype=np.float64)
        out.append(mix(echo, voice))
    return out


def run_cell(agent: bytes, caller: bytes, gain_db: float, level_db: float,
             delay_ms: int) -> tuple[int, bool, int | None]:
    """Drive the gate frame by frame. Returns (false_admits, heard, latency).

    Three things this function has to get right, each of which an earlier
    version got wrong and which are worth naming because each produced a
    confident, wrong number:

    1. **One playout element per 20 ms frame.** The live path calls
       `note_playback` once per frame written to the socket. Passing the whole
       turn as one element makes the reference a constant -- the turn's average
       RMS -- and invents a false-admit.
    2. **The clock installed BEFORE `mark_speaking`.** `mark_speaking` stamps
       `_ref_at` from `monotonic`; mixing a real timestamp with a fake clock
       makes every later tick negative, so the reference never advances and the
       gate compares a late loud frame against an early quiet one.
    3. **Keep going after a false-admit.** A cell has two halves -- did echo
       alone open the gate, and was the caller heard -- and breaking on the
       first one hides the other.
    """
    now = {"t": 1000.0}
    real_monotonic = vh._time.monotonic
    vh._time.monotonic = lambda: now["t"]
    try:
        session = vh.VoiceCallSession()
        session.mark_speaking(frames(agent))   # what we handed the carrier
        inbound = build_inbound(frames(agent), frames(caller), gain_db, level_db, delay_ms)

        false_admits = 0
        heard = False
        latency: int | None = None
        for i, frame in enumerate(inbound):
            verdict = session.admit_inbound_frame(frame)
            now["t"] += vh.FRAME_MS / 1000.0   # one 20 ms frame of playout
            if verdict != vh.FRAME_INTERRUPTION:
                continue
            if i < ONSET_FRAME:
                false_admits += 1
                # Already failed, but the caller's half still has to be
                # measured, so reset and carry on rather than aborting.
                session.state = vh.STATE_SPEAKING
                session._barge_run = 0
                continue
            if not heard:
                heard = True
                latency = i - ONSET_FRAME
            break
        return false_admits, heard, latency
    finally:
        vh._time.monotonic = real_monotonic


def main() -> int:
    vh.BARGE_IN_ENABLED = True
    turns = fixture_turns()
    if len(turns) < 2:
        print("no fixture turns found — nothing to measure")
        return 2

    print("US-014 echo-gate sweep — real Kokoro fixture speech, 8 kHz µ-law")
    print(f"operating point: ECHO_GAIN={vh.BARGE_IN_ECHO_GAIN} "
          f"MARGIN={vh.BARGE_IN_MARGIN} FLOOR={vh.BARGE_IN_ABS_FLOOR} "
          f"MIN_FRAMES={vh.BARGE_IN_MIN_FRAMES}\n")

    # 0. Positive control. If the tool cannot report a caller as heard when
    #    nothing is playing, none of the "not heard" cells below mean anything.
    control = [run_cell(a, b, -60, 0, 0)
               for a, b in zip([t[1] for t in turns], [t[1] for t in turns[1:]] + [turns[0][1]])]
    control_heard = sum(1 for _f, h, _l in control if h)
    print(f"control — caller over silence (echo -60 dB): heard {control_heard}/{len(control)}"
          f" ({'tool can detect an interruption' if control_heard == len(control) else 'TOOL IS BROKEN'})\n")

    # 1. Echo only: no caller at all. Any admission is a false admit.
    print("echo only (no caller) — false-admits by echo gain")
    print(f"{'gain dB':>8} " + " ".join(f"{t[0][:14]:>14}" for t in turns[:4]))
    worst_false = 0
    for gain_db in GAINS_DB:
        row = []
        for _label, audio in turns[:4]:
            fa, _h, _l = run_cell(audio, audio, gain_db, -60, 0)
            row.append(fa)
            worst_false = max(worst_false, fa)
        print(f"{gain_db:>8} " + " ".join(f"{v:>14}" for v in row))

    # 2. The caller over the echo: is the interruption heard, and how fast?
    print("\ncaller over echo — 'heard' and detection latency in frames"
          f" (caller at 0 dB, onset frame {ONSET_FRAME})")
    print(f"{'gain dB':>8} {'delay ms':>9} {'heard':>7} {'latency':>8}")
    unheard: list[tuple[int, int]] = []
    for gain_db in GAINS_DB:
        for delay_ms in DELAYS_MS:
            got = [run_cell(a, b, gain_db, 0, delay_ms)
                   for a, b in zip([t[1] for t in turns], [t[1] for t in turns[1:]] + [turns[0][1]])]
            heard_n = sum(1 for _f, h, _l in got if h)
            lats = [l for _f, _h, l in got if l is not None]
            print(f"{gain_db:>8} {delay_ms:>9} {heard_n:>4}/{len(got):<2} "
                  f"{(int(np.median(lats)) if lats else -1):>8}")
            if heard_n < len(got):
                unheard.append((gain_db, delay_ms))

    # 3. Quiet callers: the gate must not need a shout.
    print("\nquiet callers over echo (gain -12 dB) — heard counts")
    for level_db in LEVELS_DB:
        got = [run_cell(a, b, -12, level_db, 0)
               for a, b in zip([t[1] for t in turns], [t[1] for t in turns[1:]] + [turns[0][1]])]
        heard_n = sum(1 for _f, h, _l in got if h)
        print(f"  caller {level_db:>3} dB: {heard_n}/{len(got)}")

    print("\nverdict")
    print(f"  false-admits on echo alone : {worst_false} "
          f"({'PASS — the gate never opened on our own voice' if worst_false == 0 else 'FAIL'})")
    print(f"  cells where the caller was not heard : {len(unheard)}"
          + (f" -> {unheard[:6]}" if unheard else " (PASS)"))
    print("\n  Reminder: the echo path here is MODELLED (delay + attenuation of our")
    print("  own rendered voice), not measured on the carrier. These numbers bound")
    print("  what the gate does; they do not tell you the carrier's real gain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
