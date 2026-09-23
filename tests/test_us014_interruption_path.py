"""US-014 — the turn runner: what actually happens to the caller's ear.

`_run_voice_turn` is where a turn stops being bookkeeping and starts being
something a caller experiences: which frames reach the carrier, whether playback
is flushed, how the turn ends, and whether the call is hung up once or twice.
None of that needs an engine, a model or a network — a stub socket and a stub
`process_utterance` reach every branch, which is the point of separating the
runner from the handler.

The interruption is simulated the way it really arrives: the READER sets the
cancel (it is the reader that sees the caller's frame), and the turn notices at
its next frame boundary. Nothing here cancels the turn task, because nothing in
the design does — the turn stops itself, which is what keeps its cleanup off the
reader's critical path.

What this file guards, in the RCA's terms:

* `AGENT_SPEECH_STOPPED` used to mean "handed to the socket". On a real call it
  fired 7-13 ms after `STARTED`, with up to 27.5 s of audio still queued in the
  carrier. The stop must now say WHICH stop it was, and an interruption must
  flush what the caller would otherwise keep hearing.
* A hangup the turn performs must not be repeated by the handler's disconnect
  path — the repeat reaches Twilio as a 404 and the log calls it "Twilio hangup
  failed", which is a red herring that has already cost one investigation.

Note on configuration: `TTS_STREAM` is off in `.env`, so in the SHIPPED
configuration there is no streaming sink and an interruption lands in the batch
send loop. Both configurations are covered, because the shipped one is what a
caller meets.
"""
from __future__ import annotations

import json
import logging

import pytest

import app.voice_handler as vh
from app import main
from app.voice_handler import VoiceCallSession

CHUNK = b"\x7f" * 160          # one 20 ms µ-law frame


class FakeWS:
    """Enough of a WebSocket to observe what a turn writes, and to fail.

    `interrupt_after` is the reader: on the Nth write it sets the session's
    cancel, exactly as `admit_inbound_frame` does when the caller speaks over
    the agent mid-playback.
    """

    def __init__(self, session=None, interrupt_after: int | None = None,
                 fail_after: int | None = None):
        self.sent: list[dict] = []
        self.session = session
        self.interrupt_after = interrupt_after
        self.fail_after = fail_after
        self.closed = False

    async def send_text(self, text: str) -> None:
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("stream closed")
        self.sent.append(json.loads(text))
        if (self.session is not None and self.interrupt_after is not None
                and len(self.sent) >= self.interrupt_after):
            self.session.request_interruption()

    async def close(self) -> None:
        self.closed = True


def kinds(ws: FakeWS) -> list[str]:
    return [m.get("event", "") for m in ws.sent]


def make_turn(monkeypatch, *, interrupt_after: int | None = None, stream: bool = False,
              end_call: bool = False, fail_after: int | None = None,
              chunks: list[bytes] | None = None, sink_frames: int = 2):
    """A turn whose pipeline is canned but whose runner is the real one."""
    session = VoiceCallSession()
    chunks = [CHUNK] * 10 if chunks is None else chunks
    monkeypatch.setattr(vh, "TTS_STREAM", stream)

    async def fake_process_utterance(chunk_sink=None):
        session.begin_turn()
        if chunk_sink is not None:
            # Streaming: clauses leave mid-pipeline, before the turn returns.
            for frame in chunks[:sink_frames]:
                await chunk_sink([frame])
            return chunks, "Caller: hello\nAssistant: hi", end_call
        return chunks, "Caller: hello\nAssistant: hi", end_call

    monkeypatch.setattr(session, "process_utterance", fake_process_utterance)
    ws = FakeWS(session=session, interrupt_after=interrupt_after, fail_after=fail_after)
    turn = main._VoiceTurn(websocket=ws, session=session, label="test",
                           stream_sid="MZ1", call_sid="CA1")
    return session, ws, turn


# ── the shipped configuration: the batch send loop ──────────────────────────

@pytest.mark.anyio
async def test_a_completed_turn_sends_every_frame_and_says_so(monkeypatch):
    session, ws, turn = make_turn(monkeypatch)
    await main._run_voice_turn(turn)

    media = [m for m in ws.sent if m.get("event") == "media"]
    assert len(media) == 10, "every frame of a completed turn must be sent"
    assert all(m["streamSid"] == "MZ1" for m in media)
    assert not ws.closed
    assert session.state == "LISTENING", "the session must not be left SPEAKING"


@pytest.mark.anyio
async def test_an_interruption_flushes_the_carrier_and_stops_sending(monkeypatch):
    """The defect this story exists to remove, asserted on the wire.

    Before US-014 the agent kept sending every remaining chunk and the carrier
    kept playing them out, so the caller heard the whole answer they had just
    talked over. Now the runner stops writing AND sends `clear` — the only
    message that empties audio the carrier has already buffered.
    """
    _session, ws, turn = make_turn(monkeypatch, interrupt_after=3)
    await main._run_voice_turn(turn)

    k = kinds(ws)
    assert "clear" in k, "an interruption must flush the carrier's buffer"
    assert k.count("clear") == 1
    assert k.count("media") < 10, "sending must stop when the caller takes the floor"
    assert k.index("clear") > max(i for i, e in enumerate(k) if e == "media")


@pytest.mark.anyio
async def test_no_event_is_sent_after_the_clear(monkeypatch):
    """Ordering, not just presence: anything written after `clear` plays again."""
    _session, ws, turn = make_turn(monkeypatch, interrupt_after=3)
    await main._run_voice_turn(turn)
    k = kinds(ws)
    assert k[k.index("clear"):] == ["clear"], f"wrote after the flush: {k}"


@pytest.mark.anyio
async def test_the_stop_event_says_which_stop_it_was(monkeypatch, caplog):
    """`AGENT_SPEECH_STOPPED` may no longer be an unqualified claim.

    "We finished writing" and "the caller stopped hearing us" are different
    events, and only the log can tell them apart — which is why four days of
    logs failed to surface the defect.
    """
    with caplog.at_level(logging.INFO, logger="voice_events"):
        _session, _ws, turn = make_turn(monkeypatch, interrupt_after=3)
        await main._run_voice_turn(turn)
    assert "AGENT_SPEECH_STOPPED reason=interrupted" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="voice_events"):
        _session2, _ws2, turn2 = make_turn(monkeypatch)
        await main._run_voice_turn(turn2)
    assert "AGENT_SPEECH_STOPPED reason=complete" in caplog.text


# ── the streaming configuration (TTS_STREAM=1) ──────────────────────────────

@pytest.mark.anyio
async def test_the_sink_stops_mid_stream(monkeypatch):
    """With streaming on, the frame in flight is the last one that leaves."""
    _session, ws, turn = make_turn(monkeypatch, stream=True, interrupt_after=1)
    await main._run_voice_turn(turn)
    k = kinds(ws)
    assert k.count("media") == 1, "a streamed turn must stop feeding the carrier"
    assert "clear" in k


# ── cancellation state ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_cancel_signal_is_fresh_for_each_turn(monkeypatch):
    """A reused Event left set would cancel the next turn the moment it began."""
    session, _ws, turn = make_turn(monkeypatch, interrupt_after=3)
    await main._run_voice_turn(turn)
    assert not session.cancel_requested(), "the cancel outlived its turn"

    fresh = VoiceCallSession()
    fresh.begin_turn()
    assert not fresh.cancel_requested()


@pytest.mark.anyio
async def test_requesting_an_interruption_before_a_turn_is_safe(monkeypatch):
    """`admit_inbound_frame` can fire between turns; there is no Event yet."""
    session = VoiceCallSession()
    session.request_interruption()            # must not raise
    assert not session.cancel_requested()


@pytest.mark.anyio
async def test_the_answer_the_caller_did_not_finish_hearing_is_marked(monkeypatch):
    """The batch path's re-context gap.

    With `TTS_STREAM=0` an interruption lands in the send loop, so generation has
    ALREADY completed and the whole answer is in the history. The caller heard a
    prefix, and the frame count is the only evidence of how much. Without the
    marker the next turn's model believes it delivered an answer the caller is
    still waiting for, and will refuse to repeat it.
    """
    session, _ws, turn = make_turn(monkeypatch, interrupt_after=3)
    session._conversation_history.append("Caller: hello")
    session._conversation_history.append("Assistant: " + "the full answer")
    await main._run_voice_turn(turn)

    assert session._conversation_history[-1].startswith("Assistant: the full answer")
    assert vh.TRUNCATION_MARK in session._conversation_history[-1]


@pytest.mark.anyio
async def test_a_completed_answer_is_not_marked(monkeypatch):
    """The marker must mean something: no truncation, no marker."""
    session, _ws, turn = make_turn(monkeypatch)
    session._conversation_history.append("Assistant: " + "the full answer")
    await main._run_voice_turn(turn)
    assert vh.TRUNCATION_MARK not in session._conversation_history[-1]


# ── teardown paths ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_dead_socket_stops_the_turn(monkeypatch):
    """`_ws_send` returning False is a disconnect, not an error to retry."""
    _session, ws, turn = make_turn(monkeypatch, fail_after=2)
    with pytest.raises(main.WebSocketDisconnect):
        await main._run_voice_turn(turn)
    assert len(ws.sent) == 2


@pytest.mark.anyio
async def test_the_session_is_left_listening_even_when_the_turn_raises(monkeypatch):
    """Every exit path, or the gate rejects the caller's ordinary speech as echo
    for the rest of the call."""
    session = VoiceCallSession()

    async def boom(chunk_sink=None):
        session.begin_turn()
        session.mark_speaking()
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(session, "process_utterance", boom)
    turn = main._VoiceTurn(websocket=FakeWS(), session=session, label="test",
                           stream_sid="MZ1", call_sid="CA1")
    await main._run_voice_turn(turn)          # the runner swallows it
    assert session.state == "LISTENING"


@pytest.mark.anyio
async def test_a_signoff_hangup_marks_the_call_ended_by_the_turn(monkeypatch):
    """So the reader's disconnect path does not hang up a second time."""
    calls: list[str] = []

    async def fake_hangup(call_sid):
        calls.append(call_sid)

    monkeypatch.setattr(main, "_hangup_twilio_call", fake_hangup)
    _session, ws, turn = make_turn(monkeypatch, end_call=True)
    await main._run_voice_turn(turn)

    assert calls == ["CA1"], "the turn must hang the call up exactly once"
    assert turn.ended["by_turn"] is True
    assert ws.closed, "closing the socket is what wakes the reader to exit"


@pytest.mark.anyio
async def test_an_ordinary_turn_does_not_claim_the_call_ended(monkeypatch):
    _session, ws, turn = make_turn(monkeypatch)
    await main._run_voice_turn(turn)
    assert turn.ended["by_turn"] is False
    assert not ws.closed
