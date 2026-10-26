"""Phase 12: chat/WhatsApp references and voice→WhatsApp delivery."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.text import build_text_answer, render_text_answer
from app.references.delivery import (
    DeliveryConfig,
    ReferenceDeliveryRequest,
    ReferenceDeliveryService,
    is_registered,
)
from app.references.formatting import (
    WHATSAPP_REFERENCE_MARKER,
    answer_summary,
    build_voice_reference_message,
    normalize_phone,
    phone_match_key,
    references_from_citations,
    render_chat_references,
    render_whatsapp_references,
    split_whatsapp_answer,
)
from app.sitegraph.alerts import AlertThresholds, evaluate_alerts
from tests.nga_helpers import BASE, HOSTEL, build_store, seed_page

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHONE = "+919876543210"
URL = BASE + "/cms/hostel"


def _citation(node_id="cms.hostel", title="Hostel", url=URL, snippet=None):
    return {
        "node_id": node_id,
        "title": title,
        "url": url,
        "snippet": snippet or "Boys and girls hostels with a warden office.",
        "authority": "official_page",
    }


def _request(**overrides):
    values = dict(
        call_sid="CA-reference-1",
        conversation_id="conv-reference-1",
        phone_number=PHONE,
        channel="inbound_call",
        question="What hostel facilities are available?",
        answer_summary="The hostel has a warden office and mess facilities.",
        citations=(_citation(),),
        mode="turn",
    )
    values.update(overrides)
    return ReferenceDeliveryRequest(**values)


def _lead(**overrides):
    values = dict(
        id="lead-1",
        whatsapp_opt_in=False,
        whatsapp_opt_out_at=None,
        last_inbound_at=datetime.now(timezone.utc).isoformat(),
    )
    values.update(overrides)
    return values


def _config(**overrides):
    values = dict(
        enabled=True,
        mode="turn",
        max_per_call=3,
        min_interval_s=0,
        snippet_chars=240,
        registration_window_days=30,
        require_opt_in=False,
        template_sid="",
        retry_max=0,
        retry_backoff_s=0.0,
        log_retention_days=30,
    )
    values.update(overrides)
    return DeliveryConfig(**values)


class Sender:
    def __init__(self, ok: bool = True, error: str = "send failed") -> None:
        self.ok = ok
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, to_number: str, body: str):
        self.calls.append((to_number, body))
        return (True, f"SM-{len(self.calls)}") if self.ok else (False, self.error)


class TemplateSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, to_number: str, content_sid: str, variables: dict):
        self.calls.append((to_number, content_sid, dict(variables)))
        return True, "SM-template"


_UNSET = object()


def _service(tmp_path, *, config=None, lead=_UNSET, sender=None):
    store, _db = build_store(tmp_path, pii_guard=False)
    sender = sender or Sender()
    resolved_lead = _lead() if lead is _UNSET else lead
    service = ReferenceDeliveryService(
        store,
        config or _config(),
        sender=sender,
        lead_lookup=lambda phone: resolved_lead,
        start_worker=False,
    )
    return service, store, sender


def test_reference_formatting_and_phone_normalization():
    references = references_from_citations(
        [_citation(), _citation(), _citation(node_id="other", url="")],
        limit=3,
    )
    assert len(references) == 1
    assert normalize_phone("whatsapp:+91 98765 43210") == PHONE
    assert phone_match_key("whatsapp:+91 98765 43210") == "9876543210"
    assert answer_summary("one   two three", 20) == "one two three"

    chat = render_chat_references(references)
    whatsapp = render_whatsapp_references(references)
    assert f"[Hostel]({URL})" in chat
    assert "Sources:" in whatsapp
    assert URL in whatsapp
    assert "](" not in whatsapp


def test_snippet_is_trimmed_and_voice_message_is_bounded():
    long_snippet = "x" * 400
    references = references_from_citations(
        [_citation(snippet=long_snippet)], snippet_chars=240
    )
    assert len(references[0].snippet) <= 240

    message = build_voice_reference_message(
        "What hostel facilities are available?",
        "The hostel has a warden office.",
        references * 3,
    )
    assert URL in message
    assert "Call context:" in message
    assert "STOP" in message
    assert len(message) <= 1600


def test_whatsapp_overflow_splits_references_from_the_answer():
    body = "answer " * 220
    references = WHATSAPP_REFERENCE_MARKER + "Hostel\n" + URL + '\n"snippet"'
    answer = body + references

    short_body, short_refs = split_whatsapp_answer("plain answer")
    body_out, refs_out = split_whatsapp_answer(answer)

    assert short_body == "plain answer"
    assert short_refs == ""
    assert URL not in body_out
    assert URL in refs_out
    assert len(body_out) <= 1600


def test_registration_rules():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(days=40)).isoformat()

    assert is_registered(
        PHONE, config=_config(), lead_lookup=lambda phone: _lead(last_inbound_at=recent)
    ).registered
    assert is_registered(
        PHONE, config=_config(), lead_lookup=lambda phone: _lead(last_inbound_at=old)
    ).reason == "no_whatsapp_history"
    assert is_registered(
        PHONE,
        config=_config(),
        lead_lookup=lambda phone: _lead(whatsapp_opt_out_at=now.isoformat()),
    ).reason == "opted_out"
    assert is_registered(
        PHONE,
        config=_config(),
        lead_lookup=lambda phone: _lead(
            whatsapp_opt_in=True, last_inbound_at=None
        ),
    ).reason == "explicit_opt_in"
    assert is_registered(
        PHONE,
        config=_config(require_opt_in=True),
        lead_lookup=lambda phone: _lead(last_inbound_at=recent),
    ).reason == "opt_in_required"
    assert is_registered(
        PHONE, config=_config(), lead_lookup=lambda phone: None
    ).reason == "no_lead"


def test_delivery_sends_and_logs_without_the_full_number(tmp_path):
    service, store, sender = _service(tmp_path)

    outcomes = service.process_request(_request())

    assert [item.status for item in outcomes] == ["sent"]
    assert len(sender.calls) == 1
    sent_to, body = sender.calls[0]
    assert sent_to == PHONE
    assert "What hostel facilities" in body
    assert URL in body
    rows = store.reference_deliveries()
    assert rows[0]["status"] == "sent"
    assert rows[0]["phone_last4"] == "3210"
    assert PHONE not in json.dumps(rows[0])
    assert store.reference_delivery_summary()["sent"] == 1


def test_unregistered_and_opted_out_never_send(tmp_path):
    service, store, sender = _service(tmp_path, lead=None)
    service.process_request(_request())
    assert sender.calls == []
    assert store.reference_delivery_summary()["skipped"] == 1
    assert store.reference_deliveries()[0]["reason"] == "no_lead"

    service, store, sender = _service(
        tmp_path / "opted",
        lead=_lead(whatsapp_opt_out_at=datetime.now(timezone.utc).isoformat()),
    )
    service.process_request(_request())
    assert sender.calls == []
    assert store.reference_deliveries()[0]["reason"] == "opted_out"


def test_delivery_dedupes_and_caps_per_call(tmp_path):
    service, store, sender = _service(
        tmp_path, config=_config(max_per_call=2)
    )
    citations = (
        _citation(node_id="n1", url=URL + "/1"),
        _citation(node_id="n2", url=URL + "/2"),
        _citation(node_id="n3", url=URL + "/3"),
    )
    service.process_request(_request(citations=citations))

    # The same call cannot send the same node again.
    service.process_request(_request(citations=(citations[0],)))
    assert len(sender.calls) == 1
    assert store.sent_reference_count("CA-reference-1") == 2


def test_24h_window_requires_a_template(tmp_path):
    lead = _lead(
        last_inbound_at=(
            datetime.now(timezone.utc) - timedelta(days=2)
        ).isoformat()
    )
    service, store, sender = _service(tmp_path, lead=lead)
    service.process_request(_request())
    assert sender.calls == []
    assert store.reference_delivery_summary()["template_required"] == 1

    template_sender = TemplateSender()
    service, store, sender = _service(
        tmp_path / "template",
        lead=lead,
        config=_config(template_sid="HX-approved"),
    )
    service._template_sender = template_sender
    outcomes = service.process_request(_request())
    assert outcomes[0].status == "sent"
    assert template_sender.calls[0][1] == "HX-approved"
    assert URL in template_sender.calls[0][2]["1"]


def test_failure_is_bounded_and_logged(tmp_path):
    sender = Sender(ok=False)
    service, store, _ = _service(
        tmp_path,
        config=_config(retry_max=0, retry_backoff_s=0),
        sender=sender,
    )
    outcomes = service.process_request(_request())

    assert outcomes[0].status == "failed"
    assert len(sender.calls) == 1
    assert store.reference_delivery_summary()["failed"] == 1


def test_send_failures_are_retried(tmp_path):
    sender = Sender(ok=False)
    service, store, _ = _service(
        tmp_path,
        config=_config(retry_max=2, retry_backoff_s=0),
        sender=sender,
    )
    service.process_request(_request())

    assert len(sender.calls) == 3
    assert store.reference_delivery_summary()["failed"] == 1


def test_call_end_mode_consolidates_until_flush(tmp_path):
    service, store, sender = _service(tmp_path, config=_config(mode="call_end"))
    first = _request(
        citations=(_citation(node_id="n1", url=URL + "/1"),),
        mode="call_end",
    )
    second = _request(
        citations=(_citation(node_id="n2", url=URL + "/2"),),
        mode="call_end",
    )

    service.submit_turn(first)
    service.submit_turn(second)
    assert sender.calls == []

    service.flush_call("CA-reference-1")
    queued = service._queue.get_nowait()
    outcomes = service.process_request(queued)

    assert len(sender.calls) == 1
    assert URL + "/1" in sender.calls[0][1]
    assert URL + "/2" in sender.calls[0][1]
    assert {item.status for item in outcomes} == {"sent"}


def test_call_end_merge_is_deduplicated(tmp_path):
    service, _, _ = _service(tmp_path, config=_config(mode="call_end"))
    citation = _citation(node_id="n1", url=URL + "/1")
    service.submit_turn(_request(citations=(citation,), mode="call_end"))
    service.submit_turn(_request(citations=(citation,), mode="call_end"))

    pending = service._pending_calls["CA-reference-1"]
    assert len(pending.citations) == 1


def test_reference_alerts_and_cli(tmp_path):
    record = {
        "run_id": "r-ref",
        "finished_at": "2026-10-25T12:00:00Z",
        "stats": {},
        "graph": {
            "exists": True,
            "compiled_at": "2026-10-25T10:00:00Z",
            "coverage_pct": 1.0,
            "coverage_min": 0.95,
        },
        "freshness": {},
        "reference_delivery_window": {
            "failed": 3,
            "template_required": 3,
        },
    }
    alerts = evaluate_alerts(
        record,
        thresholds=AlertThresholds(
            reference_failures=3, template_required=3
        ),
        now=record["finished_at"],
    )
    assert {"reference_send_failures", "reference_template_required"} <= {
        alert.code for alert in alerts
    }

    store, db = build_store(tmp_path, pii_guard=False)
    store.record_reference_delivery(
        call_sid="CA-old",
        conversation_id="conv",
        phone_hash="hash",
        phone_last4="3210",
        node_id="n1",
        url=URL,
        mode="turn",
        status="sent",
        created_at="2020-01-01T00:00:00Z",
    )
    status = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_references.py"),
            "--db",
            str(db),
            "--status",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["lifetime"]["sent"] == 1

    purged = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/nga_references.py"),
            "--db",
            str(db),
            "--purge",
            "--days",
            "1",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert purged.returncode == 0, purged.stderr
    assert json.loads(purged.stdout)["removed"] == 1


def test_cited_text_turn_is_stored_with_citations(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    result = build_text_answer(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        conversation_id="conv-event",
        channel="whatsapp",
        llm=lambda messages: "The hostel has a warden office.",
    )

    assert result is not None
    events = store.turn_events("conv-event")
    assert len(events) == 1
    assert events[0]["channel"] == "whatsapp"
    citations = events[0]["citations"]
    assert citations
    assert citations[0]["node_id"] == HOSTEL
    assert citations[0]["url"]

    rendered = render_text_answer(result, channel="whatsapp")
    assert "Sources:" in rendered
    assert "](" not in rendered


def test_voice_bundle_exposes_citations(tmp_path):
    store, db = build_store(tmp_path, pii_guard=False)
    seed_page(store)
    from app.agent.voice import build_voice_bundle

    voice = build_voice_bundle(
        "hostel",
        graph_path=str(db),
        pii_guard=False,
        deadline_ms=2000,
        max_steps=2,
        max_chars=1200,
    )

    assert voice.bundle is not None
    assert voice.bundle.citations
    assert voice.bundle.citations[0]["url"]
    assert voice.bundle.citations[0]["snippet"]


def test_voice_delivery_throttle_and_slow_sender_are_off_path(tmp_path):
    service, store, sender = _service(
        tmp_path,
        config=_config(min_interval_s=20),
    )
    store.record_reference_delivery(
        call_sid="CA-reference-1",
        conversation_id="conv",
        phone_hash="hash",
        phone_last4="3210",
        node_id="previous",
        url=URL,
        mode="turn",
        status="sent",
        created_at=(
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    waits: list[float] = []
    service._sleep = waits.append

    service.process_request(
        _request(citations=(_citation(node_id="n-next", url=URL + "/next"),))
    )

    assert waits
    assert 0 < waits[0] <= 20
    assert len(sender.calls) == 1
