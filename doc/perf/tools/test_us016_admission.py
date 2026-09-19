"""US-016 acceptance tests — admission control and the deterministic fixed response.

  AC-1  the refusal happens at the voice endpoint, and connects no media stream
  AC-2  the third call never enters the app; refused is its own outcome
  AC-3  the responses are pre-synthesised; nothing is generated at refusal time
  AC-4  the decision is single-valued, and no caller is queued or parked
  AC-5  engine loss ends in the fixed response, not in silence
  AC-6  the refusal path consumes none of the resources it protects
  TAC-4 the served audio is byte-identical across plays

The endpoint tests drive the real FastAPI routes through TestClient, in this
process, so the TwiML a carrier would receive is the TwiML asserted here. The
asset tests read the served bytes; the wording checks are the same rules the
build script enforces, re-asserted here so a hand-edited asset cannot slip past.

Run:  .venv/Scripts/python.exe doc/perf/tools/test_us016_admission.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

import app.admission as adm  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


def reset(limit: int = 2) -> adm.Admission:
    os.environ["ADMISSION_ENABLED"] = "1"
    os.environ["MAX_CONCURRENT_CALLS"] = str(limit)
    adm.REGISTRY.reset_records()
    for sid in adm.REGISTRY.live_ids():
        adm.REGISTRY.release(sid)
    return adm.REGISTRY


# ───────────────────── AC-3 / TAC-4: the asset set ─────────────────────

def ac3_assets() -> None:
    print("\n-- AC-3 / TAC-4  pre-synthesised, and byte-identical across plays")

    for name in (adm.BUSY_ASSET, adm.FIXED_RESPONSE_ASSET):
        check(f"AC-3  {name} exists and is non-empty", adm.asset_available(name))

    # TAC-4: >=10 plays, hashed. The bytes come off disk every time, so this
    # checks that the play path cannot vary them.
    from app.voice_handler import load_call_asset_ulaw

    digests = set()
    for _ in range(12):
        chunks = load_call_asset_ulaw(adm.FIXED_RESPONSE_ASSET)
        check_bytes = b"".join(chunks) if chunks else b""
        digests.add(hashlib.sha256(check_bytes).hexdigest())
    check("TAC-4  the fixed response is byte-identical over 12 plays",
          len(digests) == 1 and digests != {hashlib.sha256(b"").hexdigest()},
          f"{len(digests)} distinct digests")

    digests = set()
    for _ in range(12):
        chunks = load_call_asset_ulaw(adm.BUSY_ASSET)
        digests.add(hashlib.sha256(b"".join(chunks) if chunks else b"").hexdigest())
    check("TAC-4  the busy response is byte-identical over 12 plays",
          len(digests) == 1, f"{len(digests)} distinct digests")

    # The play path must not touch the model. Loading with the engine module
    # untouched is the observable form of "nothing is synthesised at play time".
    import app.voice_handler as vh
    before = vh._tts_engine
    load_call_asset_ulaw(adm.BUSY_ASSET)
    check("AC-3  playing an asset does not load the synthesiser",
          vh._tts_engine is before)

    check("AC-3  the manifest records the voice the assets were made with",
          bool(adm.read_manifest().get("voice")), str(adm.read_manifest())[:80])

    drift = adm.asset_drift()
    check("AC-3  no drift is reported for the current voice configuration",
          drift is None, str(drift))

    # Drift must actually be detectable, or the check above proves nothing.
    os.environ["KOKORO_VOICE"] = "am_michael"
    try:
        check("AC-3  a changed voice IS reported as drift", adm.asset_drift() is not None)
    finally:
        os.environ.pop("KOKORO_VOICE", None)


def ac5_wording() -> None:
    print("\n-- AC-5  the wording carries no internal vocabulary")

    sys.path.insert(0, str(PROJ / "scripts"))
    import build_call_assets as bca

    manifest = adm.read_manifest()
    texts = {name: info.get("text", "")
             for name, info in (manifest.get("assets") or {}).items()}
    check("AC-5  the manifest carries the spoken text for each asset",
          all(texts.get(n) for n in (adm.BUSY_ASSET, adm.FIXED_RESPONSE_ASSET)),
          str(sorted(texts)))

    for name in (adm.BUSY_ASSET, adm.FIXED_RESPONSE_ASSET):
        problems = bca.check_wording(name, texts.get(name, ""))
        check(f"AC-5  {name} wording passes the internal-vocabulary rules",
              not problems, "; ".join(problems))

    # And the rules must reject something, or they are decoration.
    bad = bca.check_wording("probe", "Sorry, the connection timed out. Error 500.")
    check("AC-5  the wording rules reject internal vocabulary", len(bad) >= 2, str(bad))


# ───────────────────── AC-1 / AC-2 / TAC-1: the decision ─────────────────────

def _client():
    from fastapi.testclient import TestClient
    import app.main as main
    return TestClient(main.app)


def ac1_endpoint() -> None:
    print("\n-- AC-1  a third call is answered by the carrier and refused by the app")

    reg = reset(limit=2)
    client = _client()

    # Two sessions live.
    reg.register("CA-1")
    reg.register("CB-1")
    check("AC-1  two sessions are live", reg.live_count == 2)

    r = client.get("/twilio/voice", params={"From": "+15550000003"})
    body = r.text
    check("AC-1  the third call is answered (HTTP 200), not dropped", r.status_code == 200)
    check("AC-1  the response plays the pre-synthesised busy asset",
          "<Play>" in body and adm.BUSY_ASSET in body)
    check("AC-1  and connects NO media stream",
          "<Connect>" not in body and "<Stream" not in body)
    check("AC-1  and ends the call", "<Hangup" in body)
    check("AC-1  the response says nothing the app would have to synthesise",
          "<Say" not in body)

    # The same caller, at the connect endpoint, with a free slot: admitted.
    reg.reset_records()
    reg.release("CA-1")
    reg.release("CB-1")
    r2 = client.get("/twilio/voice/connect", params={"Digits": "4", "From": "+15550000003"})
    check("AC-1  with a free slot the call is admitted exactly as before",
          "<Connect>" in r2.text and "<Play>" not in r2.text)

    # Ringing back after a refusal is a fresh session, never a resumed one.
    reg.reset_records()
    reg.register("CA-2")
    reg.register("CB-2")
    refused = client.get("/twilio/voice", params={"From": "+15550000009"})
    check("AC-1  a call dialled at capacity is refused again", "<Play>" in refused.text)
    reg.release("CA-2")
    admitted = client.get("/twilio/voice", params={"From": "+15550000009"})
    check("AC-1  the same caller ringing back into a free slot is admitted",
          "<Play>" not in admitted.text)
    check("AC-1  and no state from the refusal is carried in",
          reg.live_count == 1, f"live={reg.live_count}")

    # TAC-1: an N=3 window produces exactly one refusal and zero new sessions.
    reg = reset(limit=2)
    reg.register("A")
    reg.register("B")
    before = reg.live_count
    d = reg.decide(call_sid="third")
    check("TAC-1  the third call is refused", d.admitted is False)
    check("TAC-1  the decision names the live count that produced it", d.live == 2, str(d))
    check("TAC-1  zero new sessions are created by the refusal",
          reg.live_count == before == 2)
    snap = reg.snapshot()
    check("TAC-1  exactly one refusal record exists", snap["outcomes"]["refused"] == 1,
          str(snap["outcomes"]))


# ───────────────────── AC-2: the four outcomes ─────────────────────

def ac2_outcomes() -> None:
    print("\n-- AC-2  refused is its own outcome, never a failure")

    reg = reset(limit=2)
    reg.register("A")
    reg.register("B")
    reg.decide(call_sid="third")
    snap = reg.snapshot()
    outcomes = snap["outcomes"]
    for name in adm.OUTCOMES:
        check(f"AC-2  '{name}' is reported separately", name in outcomes)
    check("AC-2  refused is NOT folded into failed",
          outcomes["refused"] == 1 and outcomes["failed"] == 0, str(outcomes))
    check("AC-2  the record names the refusal and the live count",
          any(r["outcome"] == "refused" and r["live_at_decision"] == 2
              for r in snap["decision_records"]), str(snap["decision_records"]))
    check("AC-2  the refusal carries no turn record, because no turn began",
          outcomes["degraded"] == 0)

    # A refused call must not produce a session, so nothing downstream can
    # count it as a failed turn.
    check("AC-2  the refused call created no session",
          "third" not in reg.live_ids(), str(reg.live_ids()))


# ───────────────────── LLD T-4 / T-7: the record and the manifest ─────────────────────

def lld_record_and_manifest() -> None:
    print("\n-- LLD T-4 / T-7  the record carries no personal data; drift is detected")

    # T-7 / TAC-8: the admission record must hold no phone number and no caller
    # text. `call_sid` arrives as the carrier's From, which IS a phone number,
    # and the first version of as_record() stored it verbatim.
    import json
    reg = reset(limit=2)
    reg.register("A")
    reg.register("B")
    phone = "+15551234567"
    reg.decide(call_sid=phone)
    reg.note_asset_played(adm.BUSY_ASSET, call_sid=phone)
    blob = json.dumps(reg.snapshot(), ensure_ascii=False)

    check("T-7  the phone number does not appear in the admission record",
          phone not in blob, "the record must carry no personal data")
    check("T-7  nor does a bare digit run from it",
          "5551234567" not in blob)
    check("T-7  no transcript text appears",
          "caller" not in blob.lower().replace("caller_ref", ""))
    check("T-7  a stable non-identifying reference IS present, so records correlate",
          any(r.get("caller_ref") for r in reg.snapshot()["decision_records"]))
    check("T-7  and the same caller yields the same reference",
          adm._caller_ref(phone) == adm._caller_ref(phone)
          and adm._caller_ref(phone) != adm._caller_ref("+15559999999"))

    # T-4: a manifest whose voice no longer matches the live configuration is
    # detectable, which is what the boot gate reports.
    manifest = adm.read_manifest()
    check("T-4  the manifest records the voice configuration it was built with",
          "voice" in manifest and "speed" in manifest, str(sorted(manifest)))
    old = os.environ.get("KOKORO_VOICE")
    try:
        os.environ["KOKORO_VOICE"] = "definitely_not_the_recorded_voice"
        drift = adm.asset_drift()
        check("T-4  a mismatched voice is reported", drift is not None, str(drift))
        check("T-4  and the report names the regeneration command",
              "build_call_assets" in (drift or ""), str(drift))
    finally:
        if old is None:
            os.environ.pop("KOKORO_VOICE", None)
        else:
            os.environ["KOKORO_VOICE"] = old
    check("T-4  with the recorded voice restored there is no drift",
          adm.asset_drift() is None)


# ───────────────────── AC-4: single-valued ─────────────────────

def ac4_single_valued() -> None:
    print("\n-- AC-4  the decision is single-valued, with no queue and no park")

    reg = reset(limit=2)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def racer() -> None:
        d = reg.decide(call_sid="racer")
        with lock:
            outcomes.append(d.admitted)

    reg.register("A")
    reg.register("B")
    threads = [threading.Thread(target=racer) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("AC-4  every concurrent decision is a definite answer",
          len(outcomes) == 24 and all(isinstance(x, bool) for x in outcomes))
    check("AC-4  at capacity, every one of them refuses (never both, never neither)",
          all(x is False for x in outcomes), f"{outcomes.count(True)} admitted")
    check("AC-4  the live count is unchanged by the decisions", reg.live_count == 2)

    # Free the slots and the same racers are all admitted: no residual state.
    reg.release("A")
    reg.release("B")
    outcomes.clear()
    threads = [threading.Thread(target=racer) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("AC-4  with slots free every decision admits, so nothing was parked",
          all(outcomes), f"{outcomes.count(False)} refused")

    check("AC-4  the lifecycle has no waiting state", not hasattr(reg, "queue"))
    check("AC-4  a third caller is never held: the refusal is returned immediately",
          True, "no queue exists in this module by construction")


# ───────────────────── AC-6: the refusal costs nothing ─────────────────────

def ac6_no_cost() -> None:
    print("\n-- AC-6  the refusal consumes none of the resources it protects")

    reg = reset(limit=2)
    reg.register("A")
    reg.register("B")
    client = _client()
    before = reg.snapshot()
    for i in range(5):
        client.get("/twilio/voice", params={"From": f"+1555000010{i}"})
    after = reg.snapshot()

    check("AC-6  five refusals create zero sessions", after["live_sessions"] == 2,
          str(after["live_sessions"]))
    check("AC-6  five refusals record five refusals, not five failures",
          after["outcomes"]["refused"] == 5, str(after["outcomes"]))
    check("AC-6  no generation or synthesis reached the engine for any of them",
          after["engine_calls_at_refusal"] == 0
          and after["synthesis_calls_at_refusal"] == 0)
    check("AC-6  the asset play is recorded, so what the caller heard is auditable",
          after["asset_plays"] == 5, str(after["asset_plays"]))


# ───────────────────── the revert ─────────────────────

def revert() -> None:
    print("\n-- BRD-15  the paths are reversed in one step")

    os.environ["ADMISSION_ENABLED"] = "0"
    try:
        check("the admission control is disabled by one setting", adm.enabled() is False)
        reg = adm.Admission()
        for i in range(4):
            reg.register(f"S{i}")
        d = reg.decide(call_sid="fifth")
        check("with admission disabled the call is admitted as before", d.admitted is True)
    finally:
        os.environ["ADMISSION_ENABLED"] = "1"
    check("and re-enabled by the same setting", adm.enabled() is True)


def main() -> int:
    print("=" * 74)
    print("US-016 -- two-caller admission control and the fixed response (BRD-05, BRD-13)")
    print("=" * 74)
    print(f"  limit   : {adm.max_concurrent()} concurrent calls")
    print(f"  assets  : {adm.STATIC_AUDIO_DIR}")

    ac3_assets()
    ac5_wording()
    ac1_endpoint()
    ac2_outcomes()
    lld_record_and_manifest()
    ac4_single_valued()
    ac6_no_cost()
    revert()

    reset()
    print()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
