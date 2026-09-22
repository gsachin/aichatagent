#!/usr/bin/env python
"""Phase 0.4 diagnostic: is the 1011 keepalive death a blocked loop or a
receive-bound socket? (untracked diagnostic; not part of the harness)

Three observers while one cold call runs on /ws/twilio:
  1. HTTP poller            - GET /api/perf/policy every 500 ms (loop liveness)
  2. WS pinger (separate)   - a SECOND WS connection pinging every 2 s (proves
                              a connection whose handler sits in receive_text
                              keeps its pongs even while another handler
                              processes a turn)
  3. SAME-connection pings  - the DRIVER connection manually pings every 2 s
                              during the greeting wait and the turn wait. This
                              is the victim connection: while its handler runs
                              generate_ulaw_greeting / process_utterance in a
                              thread, nobody reads its socket, so its pongs are
                              the ones that stall.

Reading the result:
  * HTTP fast + separate pongs fast + same-connection pongs stall during the
    greeting/turn   -> receive-bound. Uvicorn reads a socket only inside the
    handler's receive_text(); a handler busy in to_thread reads nothing, so
    keepalive pings on THAT connection go unanswered. This is the 1011.
  * HTTP stalls + separate pongs stall -> the event loop is genuinely blocked.
  * Nothing stalls                    -> re-check with the real harness.

Run against a FRESH app process for a cold greeting (Kokoro load ~46 s on this
box). The driver uses a rendered fixture turn (real speech, LLM path).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time

APP_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/twilio"
POLL_PATH = "/api/perf/policy"
PING_EVERY_S = 2.0
PONG_DEADLINE_S = 5.0


async def http_poller(results: list[tuple[float, float]], stop: asyncio.Event):
    """Poll a cheap endpoint every 500 ms. Records (t, latency_ms)."""
    import urllib.request
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(APP_URL + POLL_PATH, timeout=10) as r:
                r.read(64)
            results.append((time.monotonic(), (time.monotonic() - t0) * 1000.0))
        except Exception as e:
            results.append((time.monotonic(), float("inf")))
            print(f"  HTTP poll error: {type(e).__name__}: {e}")
        await asyncio.sleep(0.5)


async def ws_pinger(results: list[tuple[float, float | None]], stop: asyncio.Event):
    """Separate WS connection: manual ping every 2 s, 5 s pong deadline."""
    from websockets.asyncio.client import connect
    try:
        async with connect(WS_URL, open_timeout=15, ping_interval=None) as ws:
            while not stop.is_set():
                t0 = time.monotonic()
                try:
                    await asyncio.wait_for(ws.ping(), timeout=PONG_DEADLINE_S)
                    results.append((time.monotonic(), (time.monotonic() - t0) * 1000.0))
                except Exception:
                    results.append((time.monotonic(), None))
                await asyncio.sleep(PING_EVERY_S)
    except Exception as e:
        print(f"  pinger connection failed: {type(e).__name__}: {e}")


async def _same_conn_ping(ws, results: list[tuple[float, float | None]], deadline: float):
    """One manual ping on the driver connection; returns True when the pong
    arrived before `deadline`, False otherwise."""
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(ws.ping(), timeout=PONG_DEADLINE_S)
        results.append((time.monotonic(), (time.monotonic() - t0) * 1000.0))
        return True
    except Exception:
        results.append((time.monotonic(), None))
        return False


async def _ping_while_waiting(ws, cond, results, deadline: float):
    """Ping the driver connection every 2 s until cond() is true or deadline."""
    last_ping = 0.0
    while time.monotonic() < deadline:
        if time.monotonic() - last_ping >= PING_EVERY_S:
            last_ping = time.monotonic()
            await _same_conn_ping(ws, results, time.monotonic() + PONG_DEADLINE_S)
        if cond():
            return
        await asyncio.sleep(0.05)


async def drive_cold_call(lh, fixture_path, same_conn, stop: asyncio.Event) -> dict:
    """One cold call, harness wire format, auto-ping disabled. Returns milestones."""
    from websockets.asyncio.client import connect
    fx = lh.load_fixture(fixture_path) if fixture_path else None
    turn = fx.turns[0] if fx else None

    milestones: dict[str, float] = {"t0": time.monotonic()}
    inbound: list[tuple[float, float]] = []  # (t, chunk_ms)
    last_media_at = 0.0

    async with connect(WS_URL, open_timeout=15, ping_interval=None) as ws:
        sid = "MZ" + "0" * 30
        await ws.send(json.dumps({"event": "connected", "protocol": "Call",
                                  "version": "1.0.0"}))
        await ws.send(json.dumps({
            "event": "start", "sequenceNumber": "1", "streamSid": sid,
            "start": {"streamSid": sid, "accountSid": "AC" + "0" * 32,
                      "callSid": "CA" + "0" * 32, "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw",
                                      "sampleRate": lh.SAMPLE_RATE, "channels": 1},
                      "customParameters": {"phone": "+15550000000"}}}))
        milestones["start_sent"] = time.monotonic()

        async def reader():
            nonlocal last_media_at
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=180)
                except Exception:
                    break
                last_media_at = time.monotonic()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "media":
                    payload = str((msg.get("media") or {}).get("payload", ""))
                    chunk_ms = len(payload) * 3.0 / 4.0  # b64 bytes ~= ulaw bytes
                    inbound.append((time.monotonic(), chunk_ms / 8.0))
        rd = asyncio.create_task(reader())

        # ── Greeting wait, with same-connection pings ────────────────
        await _ping_while_waiting(
            ws,
            lambda: last_media_at and time.monotonic() - last_media_at >= 2.0,
            same_conn, time.monotonic() + 120.0)
        milestones["greeting_quiet"] = time.monotonic()
        greeting_chunks = len(inbound)

        # ── One turn: rendered speech (LLM path) or synthetic fallback ─
        if turn is not None:
            audio = turn.audio_ulaw
        else:
            audio = lh.synth_speech_ulaw(1000, seed=1) + lh.synth_silence_ulaw(800)
        seq = 2
        milestones["turn_start"] = time.monotonic()
        for i in range(0, len(audio), lh.ULAW_FRAME_BYTES):
            seq += 1
            payload = base64.b64encode(audio[i:i + lh.ULAW_FRAME_BYTES]).decode("ascii")
            await ws.send(json.dumps({"event": "media", "sequenceNumber": str(seq),
                                      "streamSid": sid, "media": {"track": "inbound",
                                                                  "chunk": str(seq),
                                                                  "timestamp": str(int(time.monotonic() * 1000)),
                                                                  "payload": payload}}))
            await asyncio.sleep(0.02)
        milestones["turn_audio_done"] = time.monotonic()

        # ── Response wait, with same-connection pings ────────────────
        n_before = greeting_chunks
        await _ping_while_waiting(
            ws,
            lambda: len(inbound) > n_before
                    and time.monotonic() - inbound[-1][0] >= 2.0,
            same_conn, time.monotonic() + 240.0)
        milestones["response_quiet"] = time.monotonic()
        milestones["turn_response_seen"] = len(inbound) > n_before
        milestones["first_audio"] = (inbound[n_before][0]
                                     if len(inbound) > n_before else None)
        try:
            await ws.send(json.dumps({"event": "stop", "sequenceNumber": "999",
                                      "streamSid": sid}))
        except Exception:
            pass
        rd.cancel()
    stop.set()
    return milestones, len(inbound)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=None,
                    help="fixture json path (rendered speech); default: synthetic")
    ap.add_argument("--hold-open", action="store_true",
                    help="end after the turn instead of sending stop")
    args = ap.parse_args()
    import load_harness as lh

    stop = asyncio.Event()
    http_rows: list[tuple[float, float]] = []
    separate_rows: list[tuple[float, float | None]] = []
    same_rows: list[tuple[float, float | None]] = []
    poller = asyncio.create_task(http_poller(http_rows, stop))
    pinger = asyncio.create_task(ws_pinger(separate_rows, stop))
    try:
        milestones, n_inbound = await drive_cold_call(
            lh, args.fixture, same_rows, stop)
    finally:
        stop.set()
        await asyncio.gather(poller, pinger, return_exceptions=True)

    t0 = milestones["t0"]
    g_a, g_b = milestones["start_sent"], milestones["greeting_quiet"]
    t_a, t_b = milestones["turn_start"], milestones["response_quiet"]

    def stats(rows, a, b):
        vals = [v for t, v in rows if a <= t <= b and v is not None]
        missed = sum(1 for t, v in rows if a <= t <= b and v is None)
        if not vals and not missed:
            return "none"
        p50 = f"p50={sorted(vals)[len(vals)//2]:.0f}" if vals else ""
        mx = f"max={max(vals):.0f}" if vals else ""
        return f"n={len(vals)} {p50} {mx} missed={missed}".strip()

    print("\n=== Phase 0.4 WS-1011 diagnosis ===")
    print(f"greeting window: {g_a - t0:.1f}s -> {g_b - t0:.1f}s "
          f"({g_b - g_a:.1f}s, cold Kokoro load + speech)")
    print(f"turn window:     {t_a - t0:.1f}s -> {t_b - t0:.1f}s "
          f"({t_b - t_a:.1f}s; response seen: {milestones['turn_response_seen']})")
    if milestones["first_audio"]:
        print(f"turn first_audio: {(milestones['first_audio'] - t_a) * 1000:,.0f} ms "
              f"(inbound media events total: {n_inbound})")
    print()
    print(f"HTTP poller    greeting window: {stats(http_rows, g_a, g_b)} ms")
    print(f"HTTP poller    turn window:     {stats(http_rows, t_a, t_b)} ms")
    print(f"separate WS    greeting window: {stats(separate_rows, g_a, g_b)} ms")
    print(f"separate WS    turn window:     {stats(separate_rows, t_a, t_b)} ms")
    print(f"SAME conn      greeting window: {stats(same_rows, g_a, g_b)} ms")
    print(f"SAME conn      turn window:     {stats(same_rows, t_a, t_b)} ms")

    same_missed = sum(1 for t, v in same_rows if v is None)
    sep_missed = sum(1 for t, v in separate_rows if v is None)
    http_max = max((v for t, v in http_rows), default=0.0)
    print()
    if same_missed and not sep_missed and http_max < 1000:
        print("VERDICT: receive-bound. The event loop stays live (HTTP fast, "
              "separate connection's pongs answered) while pings on the victim "
              "connection go unanswered -- uvicorn reads a socket only inside "
              "receive_text(), and a handler busy in to_thread reads nothing. "
              "This is the 1011 keepalive death.")
    elif same_missed and sep_missed and http_max >= 1000:
        print("VERDICT: loop blocked (HTTP and every connection stalled).")
    elif not same_missed:
        print("VERDICT: nothing stalled - the 1011 needs the real harness to "
              "reproduce (ping_interval=20).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
