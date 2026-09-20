"""Endpointing: one live decision, and the pause window stops being dead time.

Two claims are asserted, and the second is the one that matters.

**BRD-04 -- exactly one live end-of-speech decision, its delay documented.**
The delay used to be the literal `30` in a constructor default no caller passed
and no environment variable reached, while a second, abandoned Silero VAD
announced `stop_secs=0.5` in the log as if it were live.

**Speculative STT must be transcript-identical.** The change moves the STT pass
into the endpointing window; its entire justification is that this is a pure
latency move with no behavioural effect. So the test does not check that a
transcript was produced -- it checks the speculative transcript EQUALS the one
the non-speculative path produces from the same audio. A test asserting only
"we got a transcript" would pass on a wrong transcript.

The negative controls are the point of the file. Speculation must NOT be used
when the caller resumed mid-pause, and must NOT survive into the next
utterance. Both are asserted by building the situation and requiring the stale
result to be refused -- without those, a broken implementation that always
reused whatever it had would pass.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_endpointing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

import numpy as np  # noqa: E402

import app.voice_handler as V  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def _speech(n: int) -> list[bytes]:
    t = np.arange(160) / 8000.0
    tone = (np.sin(2 * np.pi * 300 * t) * 12000).astype(np.int16)
    return [V.pcm_to_ulaw(tone.tobytes()) for _ in range(n)]


def _silence(n: int) -> list[bytes]:
    zero = V.pcm_to_ulaw(np.zeros(160, dtype=np.int16).tobytes())
    return [zero for _ in range(n)]


class FakeSTT:
    """Stands in for `_transcribe`: records the audio it was handed."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def __call__(self, audio):
        self.calls.append(len(audio))
        return f"transcript-of-{len(audio)}-samples", False


def _session(advance_ms: int) -> tuple[V.VoiceCallSession, FakeSTT]:
    os.environ["VAD_SPECULATIVE_ADVANCE_MS"] = str(advance_ms)
    try:
        sess = V.VoiceCallSession()
    finally:
        os.environ.pop("VAD_SPECULATIVE_ADVANCE_MS", None)
    fake = FakeSTT()
    sess._transcribe = fake            # type: ignore[assignment]
    return sess, fake


async def _feed_to_endpoint(sess: V.VoiceCallSession, resume_after: int | None = None):
    """Speech, then silence until the turn closes. Optionally the caller resumes
    `resume_after` silent frames in, then goes quiet again for good."""
    for c in _speech(25):
        sess.feed_audio(c)
    for i, c in enumerate(_silence(80), 1):
        if sess.feed_audio(c):
            return "closed"
        if resume_after is not None and i == resume_after:
            for s in _speech(8):
                sess.feed_audio(s)
            for c2 in _silence(80):
                if sess.feed_audio(c2):
                    return "closed-after-resume"
    return "never-closed"


async def main_async() -> None:
    print("=" * 74)
    print("Endpointing -- BRD-04 (one live decision) and speculative STT")
    print("=" * 74)

    # ── BRD-04 ──────────────────────────────────────────────────────────
    print("\n-- BRD-04: one live end-of-speech decision, and it is reachable")
    check("the documented default is 600 ms", V.VAD_SILENCE_MS == 600,
          f"got {V.VAD_SILENCE_MS}")

    s = V.VoiceCallSession()
    check("a session takes its delay from the setting, not a literal",
          s._silence_threshold == V.VAD_SILENCE_FRAMES)

    os.environ["VAD_SILENCE_MS"] = "400"
    try:
        check("changing VAD_SILENCE_MS changes the live decision (400 ms = 20 frames)",
              V.VoiceCallSession()._silence_threshold == 20,
              f"got {V.VoiceCallSession()._silence_threshold}")
    finally:
        os.environ.pop("VAD_SILENCE_MS", None)
    check("an explicit argument still wins (the test seam is preserved)",
          V.VoiceCallSession(silence_threshold_frames=7)._silence_threshold == 7)

    src = (PROJ / "app" / "pipeline.py").read_text(encoding="utf-8")
    check("the abandoned second VAD is gone, not merely unused",
          "SileroVADAnalyzer(" not in src)
    # Scan the CODE, not the prose: the removal note deliberately quotes
    # `stop_secs=0.5` when explaining what was taken out, and a substring test
    # over the raw file fails on its own explanation.
    import ast as _ast

    _kw = [k.arg for n in _ast.walk(_ast.parse(src))
           if isinstance(n, _ast.Call) for k in n.keywords]
    check("and no competing stop_secs delay is declared in code",
          "stop_secs" not in _kw, f"keywords: {sorted(set(_kw))}")
    check("the removal is explained where it was", "exactly one live" in src)

    # ── Speculative STT ─────────────────────────────────────────────────
    print("\n-- Speculative STT: the transcript must be identical")

    plain, plain_stt = _session(0)
    await _feed_to_endpoint(plain)
    await asyncio.sleep(0.05)
    # Asserted BEFORE the deliberate transcribe below, which would otherwise
    # be counted as a speculative pass by this very check.
    check("with speculation OFF, no speculative pass ran", plain_stt.calls == [],
          f"{plain_stt.calls}")
    # What the ordinary path would transcribe: same helper, same buffer.
    t_plain, _ = await plain._transcribe(
        plain._prepare_audio(b"".join(plain._audio_buffer)))

    spec, spec_stt = _session(220)
    outcome = await _feed_to_endpoint(spec)
    await asyncio.sleep(0.1)                        # let the task land
    check("the turn still closes only at the full 600 ms (delay unchanged)",
          outcome == "closed", outcome)
    check("with speculation ON, a pass ran during the pause",
          len(spec_stt.calls) == 1, f"{spec_stt.calls}")

    got = spec._consume_speculative()
    check("the speculative result is offered to the turn", got is not None)
    if got:
        check("SAME transcript as the non-speculative path", got[0] == t_plain,
              f"{got[0]!r} != {t_plain!r}")
        # The transcript encodes the audio length, so this is the same claim
        # stated directly: both passes saw identical audio.
        real_len = len(spec._prepare_audio(b"".join(spec._audio_buffer)))
        check("the speculative pass saw the same audio as the real one",
              spec_stt.calls == [real_len],
              f"speculative saw {spec_stt.calls}, real would be [{real_len}]")

    # NEGATIVE CONTROL 1: a caller who resumes invalidates the speculation.
    # The pre-resume audio is 25 frames; after resuming with 8 more it is 33.
    # The implementation re-speculates on the longer buffer, so what must be
    # proven is not "no result" but "not the STALE one" -- a check for None
    # here would fail against correct code and pass against code that simply
    # never speculates.
    resumed, resumed_stt = _session(220)
    await _feed_to_endpoint(resumed, resume_after=20)
    await asyncio.sleep(0.1)
    got = resumed._consume_speculative()
    pre_samples = 25 * 160 * 2          # 25 frames, 8 kHz -> 16 kHz
    post_samples = 33 * 160 * 2
    check("NEGATIVE CONTROL: after a resume the STALE pre-resume result is "
          "refused", got is None or f"{pre_samples}-samples" not in got[0],
          f"got {got!r}")
    check("NEGATIVE CONTROL: and the fresh post-resume result is the one kept",
          got is not None and got[0] == f"transcript-of-{post_samples}-samples",
          f"got {got!r}, expected transcript-of-{post_samples}-samples")

    # NEGATIVE CONTROL 2: nothing survives into the next utterance.
    nxt, _ = _session(220)
    await _feed_to_endpoint(nxt)
    await asyncio.sleep(0.1)
    check("a fresh utterance offers its own speculation",
          nxt._consume_speculative() is not None)
    nxt.reset_utterance()
    check("NEGATIVE CONTROL: reset_utterance invalidates it (the harness loops "
          "fixtures, so identical audio across turns is realistic)",
          nxt._consume_speculative() is None)

    # Disabled by setting.
    off, off_stt = _session(0)
    await _feed_to_endpoint(off)
    await asyncio.sleep(0.05)
    check("advance=0 disables speculation entirely", off_stt.calls == [],
          f"{off_stt.calls}")

    print()
    print(f"{passed} passed, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main_async())
    raise SystemExit(1 if failed else 0)
