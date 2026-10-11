"""Client-demo track — a turn can never hold the line open forever.

This is a backstop, not a latency target. It exists because "stuck" turned out
to be reachable: `generate_stream` handed deltas to the event loop with
`asyncio.Queue.put_nowait` from a pump thread, which reaches
`loop.call_soon` -- the non-thread-safe entry point that does NOT write the
self-pipe, so the loop was never woken. Live calls masked it because Twilio
sends a media frame every 20 ms; a transport without that traffic stalled every
turn for as long as the process lived (measured: 10,425 ms to first audio
against 1,645 ms, and two runs that never returned at all).

The bug is fixed. These tests cover what makes the NEXT one a bad turn instead
of a dead call.

The assertions are deliberately about what the CALLER experiences -- frames
reaching the socket -- rather than about which helper was called. A guard that
computes a fallback and then cannot send it is indistinguishable, from the
caller's side, from no guard at all; an earlier draft of this change had
exactly that defect, because the cancel signal the timeout sets is the same one
the send loop checks.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402
from app import voice_handler as vh  # noqa: E402


class FakeSocket:
    """Records every frame the turn writes, so 'the caller heard it' is checkable."""

    def __init__(self):
        self.frames: list[str] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(text)

    def media_frames(self) -> list[str]:
        return [f for f in self.frames if '"event": "media"' in f]


def _turn(session, ws, stream_sid="MZtest") -> m._VoiceTurn:
    t = m._VoiceTurn(websocket=ws, session=session, label="WS /ws/twilio",
                     stream_sid=stream_sid, call_sid="CAtest")
    return t


@pytest.fixture
def short_timeout(monkeypatch):
    """Bound the turn at 0.3 s so a deliberate hang is quick to test."""
    monkeypatch.setenv("TURN_TIMEOUT_S", "0.3")
    assert vh._turn_timeout_s() == pytest.approx(0.3)
    return 0.3


def test_a_stuck_turn_is_abandoned_and_the_caller_still_hears_a_sentence(
        short_timeout):
    """The core guarantee: stuck turn -> spoken fallback, not silence."""
    session = vh.VoiceCallSession()
    session.call_id = "MZtest"

    async def never_returns(chunk_sink=None):
        await asyncio.sleep(3600)

    session.process_utterance = never_returns
    ws = FakeSocket()

    asyncio.run(asyncio.wait_for(m._run_voice_turn(_turn(session, ws)),
                                 timeout=20))

    assert ws.media_frames(), "the caller heard nothing at all"
    # The fixed response is a pre-rendered asset, so it plays without any model.
    assert session.state == vh.STATE_LISTENING, (
        "the session must leave SPEAKING, or the echo gate rejects the "
        "caller's ordinary speech for the rest of the call")


def test_the_timeout_is_recorded_so_a_stall_is_legible(short_timeout, caplog):
    """A stall must be countable after the fact, not inferred from silence."""
    session = vh.VoiceCallSession()
    session.call_id = "MZtest"

    async def never_returns(chunk_sink=None):
        await asyncio.sleep(3600)

    session.process_utterance = never_returns
    ws = FakeSocket()

    with caplog.at_level("INFO", logger="voice_events"):
        asyncio.run(asyncio.wait_for(m._run_voice_turn(_turn(session, ws)),
                                     timeout=20))

    assert "TURN_TIMEOUT" in caplog.text
    assert "timeout_s=0.3" in caplog.text


def test_a_turn_that_finishes_inside_the_bound_is_untouched():
    """The guard must be invisible on every turn that is merely slow."""
    session = vh.VoiceCallSession()
    session.call_id = "MZtest"

    async def quick(chunk_sink=None):
        return [b"\x00" * 160], "Caller: hi\nAssistant: hello", False

    session.process_utterance = quick
    ws = FakeSocket()

    asyncio.run(asyncio.wait_for(m._run_voice_turn(_turn(session, ws)),
                                 timeout=20))

    media = ws.media_frames()
    assert len(media) == 1, f"expected the turn's own audio, got {len(media)}"
    assert "FIXED_RESPONSE" not in "".join(ws.frames)


def test_the_bound_is_far_clear_of_every_merely_slow_turn():
    """A guard set inside the normal range would abandon real answers.

    Live evidence: time-to-first-audio p95 7,782 ms and the worst of 3,781
    traced turns 27,703 ms (logs/perf_turns.jsonl). The default must sit above
    that worst turn, or it would fire on calls that were working.
    """
    from app.config import settings

    worst_live_turn_ms = 27_703
    assert settings.TURN_TIMEOUT_S * 1000 > worst_live_turn_ms, (
        "the default bound would have abandoned a real, working call")


def test_the_greeting_is_bounded_more_tightly_than_a_turn(short_timeout):
    """A fixed script through one engine has no legitimate reason to be slow."""
    assert m._greeting_timeout_s() <= 15.0
    assert m._greeting_timeout_s() <= vh._turn_timeout_s()


def test_an_unparseable_bound_falls_back_rather_than_stopping_the_stack(
        monkeypatch):
    """A typo in .env must not take the phone line down."""
    monkeypatch.setenv("TURN_TIMEOUT_S", "thirty")
    from app.config import settings

    assert vh._turn_timeout_s() == float(settings.TURN_TIMEOUT_S)


@pytest.mark.parametrize("bad", ["0", "-5", ""])
def test_a_nonsense_bound_falls_back_to_the_default(bad, monkeypatch):
    monkeypatch.setenv("TURN_TIMEOUT_S", bad)
    from app.config import settings

    assert vh._turn_timeout_s() == float(settings.TURN_TIMEOUT_S)
