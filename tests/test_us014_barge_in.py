"""US-014 — the caller-interruption gate.

The decision table for `VoiceCallSession.admit_inbound_frame`, which is the ONE
place a caller frame is admitted or rejected while the agent is speaking
(TRD-05). No network, no models, no app boot: the gate is pure audio-in,
verdict-out, which is the point of putting it on the session rather than in the
two WebSocket handlers it was copy-pasted into.

Why the table matters — the defect this closes was not a wrong threshold but a
guard that could never run. `tts_playing` was True only while the handler was
NOT reading the socket, so `BARGE_IN_DETECTED` appears 0 times in a 13.7 MB log
of live calls, and the caller's overlapping speech was deferred and replayed as
a fresh turn instead of being heard. A gate that decides per frame is testable;
a flag that is never true is not.

The last group runs the gate over the app's own rendered fixture speech, because
the operating point is a claim about real audio and nothing else can check it.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pytest

import app.voice_handler as vh

FIXTURE_GLOB = os.path.join("doc", "perf", "tools", "fixtures", "*", "turn*.ulaw")


# ── fixtures ────────────────────────────────────────────────────────────────

def frame(amplitude: int, samples: int = 160) -> bytes:
    """One 20 ms µ-law frame whose PCM RMS is ~`amplitude`.

    A constant value, so RMS == amplitude before encoding, and µ-law round-trips
    a constant closely enough that the verdicts below are never near a boundary
    by accident. Amplitudes are chosen far from every threshold on purpose: a
    test that sits on a boundary tests the float, not the decision.
    """
    pcm = np.full(samples, amplitude, dtype=np.int16).tobytes()
    return vh.pcm_to_ulaw(pcm)


def scaled(ulaw_frame: bytes, gain: float) -> bytes:
    """Attenuate a real µ-law frame by `gain`.

    Decode, scale, re-encode: the same thing the carrier's echo path does to our
    voice, applied to genuine speech rather than to a synthetic constant.
    """
    pcm = np.frombuffer(vh.ulaw_to_pcm(ulaw_frame), dtype=np.int16).astype(np.float64)
    return vh.pcm_to_ulaw(np.clip(pcm * gain, -32768, 32767).astype(np.int16).tobytes())


@pytest.fixture
def session() -> vh.VoiceCallSession:
    return vh.VoiceCallSession()


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock. `tick(n)` advances n x 20 ms frames.

    The reference advances on WALL CLOCK, so a test that loops over frames
    without advancing time is a test of a stalled stream. Making the clock
    explicit is what lets both cases be tested.
    """
    now = {"t": 1000.0}
    monkeypatch.setattr(vh._time, "monotonic", lambda: now["t"])

    def tick(n: int = 1) -> None:
        now["t"] += vh.FRAME_MS * n / 1000.0

    return tick


@pytest.fixture
def gate(monkeypatch, clock):
    """The gate ON, with the defaults the config ships. Returns the session-maker."""
    monkeypatch.setattr(vh, "BARGE_IN_ENABLED", True)
    monkeypatch.setattr(vh, "BARGE_IN_ECHO_GAIN", 0.5)
    monkeypatch.setattr(vh, "BARGE_IN_MARGIN", 2.0)
    monkeypatch.setattr(vh, "BARGE_IN_ABS_FLOOR", 150.0)
    monkeypatch.setattr(vh, "BARGE_IN_MIN_FRAMES", 6)
    return vh.VoiceCallSession


# ── the decision table ──────────────────────────────────────────────────────

def test_not_speaking_is_an_ordinary_caller_frame(session):
    """A frame while LISTENING is never gated: the caller has the floor."""
    assert session.state == vh.STATE_LISTENING
    assert session.admit_inbound_frame(frame(900)) == vh.FRAME_LISTENING


def test_quiet_frame_over_playback_is_echo(gate, clock):
    """Below the absolute floor, during playback: our own bleed, rejected."""
    s = gate()
    s.mark_speaking([frame(1000)] * 20)
    clock()
    assert s.admit_inbound_frame(frame(100)) == vh.FRAME_ECHO


def test_loud_frame_over_loud_playback_is_still_echo(gate, clock):
    """The ratio half of the gate.

    An 800-amplitude frame clears the absolute floor of 150 on its own. It is
    still echo, because the passage being played out is at 1000 and the bleed
    from it can reach 800 -- which is why a floor-only gate is not enough and
    the reference is carried at all.
    """
    s = gate()
    s.mark_speaking([frame(1000)] * 40)
    clock()
    assert vh.frame_rms(frame(800)) > vh.BARGE_IN_ABS_FLOOR, "test premise: clears the floor alone"
    assert s.admit_inbound_frame(frame(800)) == vh.FRAME_ECHO


def test_a_click_does_not_interrupt(gate, clock):
    """Below BARGE_IN_MIN_FRAMES consecutive frames: a click, not a word."""
    s = gate()
    s.mark_speaking([frame(200)] * 40)
    verdicts = []
    for _ in range(vh.BARGE_IN_MIN_FRAMES - 1):
        clock()
        verdicts.append(s.admit_inbound_frame(frame(3000)))
    assert verdicts == [vh.FRAME_ECHO] * (vh.BARGE_IN_MIN_FRAMES - 1)
    assert s.state == vh.STATE_SPEAKING, "a click must not stop playback"


def test_caller_speech_over_playback_interrupts(gate, clock):
    """The happy path: speech loud enough, sustained long enough, is believed."""
    s = gate()
    s.mark_speaking([frame(200)] * 40)
    verdicts = []
    for _ in range(vh.BARGE_IN_MIN_FRAMES):
        clock()
        verdicts.append(s.admit_inbound_frame(frame(3000)))
    assert verdicts[-1] == vh.FRAME_INTERRUPTION
    assert verdicts[:-1] == [vh.FRAME_ECHO] * (vh.BARGE_IN_MIN_FRAMES - 1)


def test_interruption_returns_the_floor_to_the_caller(gate, clock):
    """After admission the state is LISTENING and the rest of the utterance is
    ordinary caller audio — not re-litigated frame by frame."""
    s = gate()
    s.mark_speaking([frame(200)] * 40)
    for _ in range(vh.BARGE_IN_MIN_FRAMES):
        clock()
        s.admit_inbound_frame(frame(3000))
    assert s.state == vh.STATE_LISTENING
    assert s._turn_began_as_interruption is True
    assert s.admit_inbound_frame(frame(3000)) == vh.FRAME_LISTENING


def test_a_quiet_frame_resets_the_run(gate, clock):
    """Speech has to be continuous. One quiet frame between loud ones restarts
    the count, so a stuttering echo cannot accumulate its way to an interrupt."""
    s = gate()
    s.mark_speaking([frame(200)] * 40)
    for _ in range(vh.BARGE_IN_MIN_FRAMES - 1):
        clock()
        s.admit_inbound_frame(frame(3000))
    clock()
    s.admit_inbound_frame(frame(100))                      # the gap
    assert s._barge_run == 0
    clock()
    assert s.admit_inbound_frame(frame(3000)) == vh.FRAME_ECHO
    assert s.state == vh.STATE_SPEAKING


# ── the revert (TAC-7 / BRD-15) ─────────────────────────────────────────────

def test_disabled_gate_discards_and_resets(monkeypatch, session, clock):
    """BARGE_IN_ENABLED=0 is the documented discard, and it is load-bearing.

    This is the behaviour the docs always described and the code never had: with
    the reader split the guard becomes reachable for the first time, so the
    revert now means something. It discards the caller's speech AND resets the
    partial buffer, so nothing of the overlapping utterance survives into the
    next turn.
    """
    monkeypatch.setattr(vh, "BARGE_IN_ENABLED", False)
    session._audio_buffer.extend([frame(500), frame(600)])
    session.mark_speaking([frame(900)] * 20)

    clock()
    assert session.admit_inbound_frame(frame(900)) == vh.FRAME_MUTED
    assert session._audio_buffer == [], "the partial utterance must not survive"


def test_disabled_gate_leaves_listening_alone(monkeypatch, session):
    """The revert changes only the SPEAKING window; an ordinary turn is untouched."""
    monkeypatch.setattr(vh, "BARGE_IN_ENABLED", False)
    assert session.admit_inbound_frame(frame(900)) == vh.FRAME_LISTENING


def test_the_old_escape_hatch_still_opens(monkeypatch, session, clock):
    """`MUTE_STT_DURING_TTS=0` + the gate off passes frames through, as before.

    Both keys keep their meaning and both stay read: BARGE_IN_ENABLED decides
    whether the gate runs, MUTE_STT_DURING_TTS decides what happens when it does
    not. A read-by-nobody setting is the defect US-011 exists to catch, so this
    pairing is asserted rather than assumed.
    """
    monkeypatch.setattr(vh, "BARGE_IN_ENABLED", False)
    monkeypatch.setattr(vh, "MUTE_STT_DURING_TTS", False)
    session.mark_speaking([frame(900)] * 20)
    assert session.admit_inbound_frame(frame(900)) == vh.FRAME_LISTENING


# ── reference alignment on the playout clock ────────────────────────────────

def test_mark_speaking_primes_the_reference(gate):
    """The onset hole.

    A session entering SPEAKING with `_ref_env == 0` would apply the ratio to
    zero, so for the first frames after playback starts the gate is the absolute
    floor alone — exactly when the carrier begins returning our own voice. The
    reference must be primed at the moment playback starts, not on the first tick.
    """
    s = gate()
    s.mark_speaking([frame(3000)] * 10)
    assert s._ref_env > 1000, f"reference not primed: {s._ref_env}"
    # And the consequence: the agent's own voice at the declared echo gain is
    # rejected from the very first frame, with no window of exposure.
    assert s.admit_inbound_frame(scaled(frame(3000), vh.BARGE_IN_ECHO_GAIN)) == vh.FRAME_ECHO


def test_reference_follows_playout_not_send(gate, clock):
    """The reference tracks the frame being played, not the loudest frame sent.

    A long answer is a loud passage followed by a quiet one. If the envelope
    were taken from the whole sent buffer it would still be at the loud value
    when the quiet passage is playing, and the caller's speech during that
    passage would have to shout over a passage that ended seconds ago.
    """
    s = gate()
    s.mark_speaking([frame(3000)] * 5 + [frame(200)] * 40)
    clock(6)                                   # the loud passage plays out
    s.admit_inbound_frame(frame(0))            # the sampling opportunity
    assert s._ref_env < 1000, f"reference stuck at the loud passage: {s._ref_env}"


def test_reference_advances_on_wall_clock_not_frames(gate, clock):
    """Frames are the opportunity to sample the playout position; time is the
    position. Ten frames delivered in the same instant are still 0 ms of
    playback, so the reference must not move ten frames along."""
    s = gate()
    s.mark_speaking([frame(3000)] * 50)
    for _ in range(10):                        # no clock advance
        s.admit_inbound_frame(frame(0))
    assert s._ref_env > 1000, "reference advanced without time passing"

    clock(3)                                   # now three frames have played
    s.admit_inbound_frame(frame(0))
    assert len(s._playout) < 50, "reference did not advance with the clock"


def test_reference_decays_once_the_queue_is_drained(gate, clock):
    """Nothing queued but still SPEAKING (a carrier buffer that ran dry): the
    threshold must fall back to the absolute floor rather than keep a stale one."""
    s = gate()
    s.mark_speaking([frame(3000), frame(3000)])
    loud = s._ref_env
    assert loud > vh.BARGE_IN_ABS_FLOOR, f"test premise: reference primed, got {loud}"

    s._playout.clear()                         # the carrier's buffer runs dry
    clock(40)
    s.admit_inbound_frame(frame(0))

    assert s._ref_env < loud, "the reference must fall once nothing is playing"
    # The consequence that matters: after enough time the gate is back on its
    # absolute floor, so the caller is measured against the floor rather than
    # against a passage that ended.
    clock(120)
    s.admit_inbound_frame(frame(0))
    assert max(vh.BARGE_IN_ABS_FLOOR,
               s._ref_env * vh.BARGE_IN_ECHO_GAIN * vh.BARGE_IN_MARGIN) == vh.BARGE_IN_ABS_FLOOR


def test_a_stalled_stream_resyncs_instead_of_replaying(gate, clock):
    """More than 500 ms behind is a stall, not playout. Draining the queue and
    falling back to the floor is honest; replaying half a second of playout in
    one step would produce a reference that means nothing."""
    s = gate()
    s.mark_speaking([frame(3000)] * 100)
    clock(500)                                 # 10 s of no inbound audio
    s.admit_inbound_frame(frame(0))
    assert s._playout == [], "a stalled stream should re-sync, not replay playout"


def test_mark_listening_clears_the_reference(gate, clock):
    """A session left in SPEAKING would gate the caller's ordinary speech as
    echo for the rest of the call, so every exit path must land here."""
    s = gate()
    s.mark_speaking([frame(1000)] * 20)
    clock()
    s.admit_inbound_frame(frame(1000))
    assert s._playout, "test premise: playback queued"
    s.mark_listening()
    assert s.state == vh.STATE_LISTENING
    assert s._playout == []
    assert s._ref_env == 0.0
    assert s._ref_at is None
    assert s._barge_run == 0


# ── the operating point, on the app's own rendered speech (T-15) ────────────

def fixture_turns() -> list[tuple[str, bytes]]:
    """The app's rendered fixture turns: real Kokoro speech, 8 kHz µ-law.

    The same voice the agent speaks in, and the same representation it reaches
    the carrier in — which is what makes it usable evidence for the gate's
    operating point. It is NOT a measurement of the carrier's acoustic path;
    the echo path is modelled here (attenuate our voice by the declared gain),
    because US-002 TAC-9 puts the real path out of local reach.
    """
    out = []
    for path in sorted(glob.glob(FIXTURE_GLOB)):
        raw = open(path, "rb").read()
        frames = [raw[i:i + 160] for i in range(0, len(raw) // 160 * 160, 160)]
        out.append((os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path),
                    b"".join(frames)))
    return out


@pytest.mark.parametrize("name,audio", fixture_turns(), ids=lambda v: v if isinstance(v, str) else "")
def test_the_agents_own_voice_is_never_admitted(gate, clock, name, audio):
    """T-15: playback echoed back at the declared gain must not interrupt.

    This is the failure UC-09 E1 exists for — the agent hearing itself and
    cutting its own answer short — checked against genuine speech rather than a
    synthetic tone, because the gate's whole job is to separate two voices.
    """
    s = gate()
    s.mark_speaking([audio])
    for i in range(0, len(audio), 1600):       # 10-frame steps across the turn
        clock(10)
        verdict = s.admit_inbound_frame(scaled(audio[i:i + 1600][:160], vh.BARGE_IN_ECHO_GAIN))
        assert verdict != vh.FRAME_INTERRUPTION, f"{name}: admitted the agent's own voice"


@pytest.mark.parametrize("name,audio", fixture_turns(), ids=lambda v: v if isinstance(v, str) else "")
def test_real_speech_at_the_callers_level_is_admitted(gate, clock, name, audio):
    """And the other half of the same claim: a caller whose speech is as loud as
    the agent's own is heard. Both directions have to hold, or the gate is
    either deaf or credulous.

    The caller's audio is at full scale here because the fixtures ARE speech at
    conversational level; the echo is the attenuated copy tested above.
    """
    s = gate()
    s.mark_speaking([scaled(audio, 0.25)])     # a quiet passage playing out
    admitted = False
    for i in range(0, min(len(audio) - 1600, 3200), 160):
        clock()
        if s.admit_inbound_frame(audio[i:i + 160]) == vh.FRAME_INTERRUPTION:
            admitted = True
            break
    assert admitted, f"{name}: the caller spoke at the agent's own level and was not heard"


# ── the shipped default ─────────────────────────────────────────────────────

def test_the_shipped_configuration_yields_by_default():
    """The PO ruling, as an executable statement about the LIVE configuration.

    "The voice should yield always by default." Reads resolved settings rather
    than the code default, because what matters is what the running stack does:
    if someone sets `BARGE_IN_ENABLED=0` in `.env`, the voice stops yielding and
    this fails. That is the point — the revert is allowed (`BRD-15`, one line),
    but it has to be a decision rather than a silently-eroded default.
    """
    from app.config import settings

    assert settings.BARGE_IN_ENABLED is True, (
        "the voice no longer yields by default; US-014's ruling is that it does"
    )


def test_yielding_is_reachable_without_configuration():
    """A fresh session, no patching: the gate is live and admits a caller.

    Guards against the flag being declared on but never consulted — the exact
    shape of the defect this story started from, where `MUTE_STT_DURING_TTS`
    was `1` and read by a branch that could not execute.
    """
    s = vh.VoiceCallSession()
    s.mark_speaking([vh.pcm_to_ulaw(np.full(160, 200, dtype=np.int16).tobytes())] * 40)
    verdicts = [s.admit_inbound_frame(vh.pcm_to_ulaw(np.full(160, 3000, dtype=np.int16).tobytes()))
                for _ in range(vh.BARGE_IN_MIN_FRAMES)]
    assert verdicts[-1] == vh.FRAME_INTERRUPTION, (
        "a caller speaking over the agent was not admitted with the shipped defaults"
    )


# ── the prompt side of AC-2 ─────────────────────────────────────────────────

def _assembled(override_settings, enabled: bool) -> str:
    """The prompt as the model receives it, not as the source file reads.

    TAC-2: the prompt is ASSEMBLED, so a source-file search proves nothing about
    what reaches the engine. Build it the way the live path builds it. Settings
    is a frozen dataclass, so the flag is flipped through the repo's own
    `override_settings` fixture rather than monkeypatch.
    """
    override_settings(BARGE_IN_ENABLED=enabled)
    from app.voice_system_prompt import build_voice_system_prompt
    return build_voice_system_prompt("CTX")


def test_prompt_never_claims_a_capability_the_gate_lacks(override_settings):
    """AC-2 / CV-01: the two sides must agree, in EITHER direction.

    This is the defect `BRD-19` names: the shipped prompt told the agent to
    "immediately yield to the caller" while the platform discarded everything
    the caller said. The prompt may only describe the mechanism in force, so
    each configuration is asserted against its own truth.
    """
    off = _assembled(override_settings, False)
    assert "Immediately yield to the caller" not in off, "the unreachable claim is back"
    assert "cannot be interrupted" in off, "the OFF contract is missing"
    assert "the system stops your speech and the conversation shows it" not in off

    on = _assembled(override_settings, True)
    assert "cannot be interrupted" not in on, "the ON prompt still says the caller cannot interrupt"
    assert "the system stops your speech and the conversation shows it" in on, "the ON contract is missing"


def test_prompt_stops_promising_events_it_never_receives(override_settings):
    """The same defect, second instance.

    The shipped prompt listed USER_SPEECH_STARTED / AGENT_SPEECH_INTERRUPTED as
    events "the orchestration system may provide". It provides none of them --
    the model receives the transcript and nothing else -- and
    AGENT_SPEECH_INTERRUPTED was never even emitted under that name.
    """
    for enabled in (False, True):
        prompt = _assembled(override_settings, enabled)
        for ghost in ("AGENT_SPEECH_INTERRUPTED", "USER_SPEECH_PARTIAL",
                      "may be provided by the orchestration layer",
                      "AGENT_PARTIAL"):
            assert ghost not in prompt, f"prompt still promises {ghost}"


def test_prompt_names_the_marker_the_interrupted_turn_writes(override_settings):
    """The contract and the code have to agree on the same markers.

    `process_utterance` writes "[interrupted here]" into the history when a turn
    is stopped mid-generation, and `note_answer_truncated` appends
    `TRUNCATION_MARK` when playback was cut short on the batch path; the prompt
    tells the agent what those markings mean. If either side is reworded alone,
    this fails -- which is the point (AC-2).
    """
    prompt = _assembled(override_settings, True)
    assert "[interrupted here]" in prompt
    assert "may not have heard the end" in prompt

    import inspect

    from app import voice_handler

    code = inspect.getsource(voice_handler.VoiceCallSession)
    assert "[interrupted here]" in code, "code and prompt no longer agree on the marker"
    assert voice_handler.TRUNCATION_MARK.split(" - ")[0].strip("[") in prompt or \
        "may not have heard the end" in voice_handler.TRUNCATION_MARK


def test_prompt_assembles_without_placeholder_leaks(override_settings):
    """A `{interruption_contract}` slot that fails to fill would ship braces."""
    for enabled in (False, True):
        prompt = _assembled(override_settings, enabled)
        assert "{interruption_contract}" not in prompt
        assert "{context}" not in prompt
        assert "{company_name}" not in prompt


# ── the refactor this gate sits on ──────────────────────────────────────────

def test_is_silent_is_unchanged_by_the_extraction():
    """`frame_rms` was extracted from `is_silent` so the gate and the VAD
    measure energy the same way. The VAD's own 80 threshold must not have moved."""
    s = vh.VoiceCallSession()
    assert s.is_silent(frame(0)) is True
    assert s.is_silent(frame(2000)) is False
    assert vh.frame_rms(frame(2000)) > 80
    assert vh.frame_rms(b"") == 0.0
