"""
WhatsApp offer-letter gating tests.

The offer letter must be generated only when the student types "done"
after uploading documents — never automatically per document upload.
"""

import asyncio

from app import main as m


def _async_ret(value):
    async def f(*args, **kwargs):
        return value
    return f


def _recorder():
    class Rec:
        def __init__(self):
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.calls.append(args)
            return None
    return Rec()


# ── "done" handler ──────────────────────────────────────────────────────

def test_offer_on_done_missing_readiness_does_not_generate(monkeypatch):
    from app.offers import service as svc

    monkeypatch.setattr(
        svc, "evaluate_offer_readiness",
        _async_ret({"missing": ["name"], "ready": False}),
    )
    monkeypatch.setattr(svc, "missing_fields_text", lambda missing: "your name")
    rec = _recorder()
    monkeypatch.setattr(svc, "generate_and_send_offer", rec)

    out = asyncio.run(m._whatsapp_offer_on_done("lead1", "Sachin"))
    assert "your name" in out
    assert rec.calls == []


def test_offer_on_done_without_documents_does_not_generate(monkeypatch):
    from app.offers import models as om
    from app.offers import service as svc

    monkeypatch.setattr(
        svc, "evaluate_offer_readiness",
        _async_ret({"missing": [], "ready": True}),
    )
    monkeypatch.setattr(om, "list_documents", _async_ret([]))
    rec = _recorder()
    monkeypatch.setattr(svc, "generate_and_send_offer", rec)

    out = asyncio.run(m._whatsapp_offer_on_done("lead1", "Sachin"))
    assert "don't see any documents" in out
    assert rec.calls == []


def test_offer_on_done_generates_and_confirms(monkeypatch):
    from app.offers import models as om
    from app.offers import service as svc

    monkeypatch.setattr(
        svc, "evaluate_offer_readiness",
        _async_ret({"missing": [], "ready": True}),
    )
    monkeypatch.setattr(om, "list_documents", _async_ret([{"id": "d1"}]))

    rec = _recorder()

    async def generate(lead_id, force=False):
        rec.calls.append(lead_id)
        return {"id": "o1", "program": "MBA"}

    monkeypatch.setattr(svc, "generate_and_send_offer", generate)

    out = asyncio.run(m._whatsapp_offer_on_done("lead1", "Sachin"))
    assert "MBA" in out and "accept" in out
    assert rec.calls == ["lead1"]


# ── Document upload no longer auto-triggers ────────────────────────────

def test_document_upload_no_longer_auto_triggers_offer(monkeypatch, tmp_path):
    import urllib.request
    from types import SimpleNamespace

    import app.config as cfg
    from app.leads import models as lm
    from app.offers import models as om
    from app.offers import service as svc

    monkeypatch.setattr(cfg, "settings", SimpleNamespace(
        TWILIO_ACCOUNT_SID="sid",
        TWILIO_AUTH_TOKEN="tok",
        DATA_DIR=str(tmp_path),
    ))

    class _Resp:
        def read(self):
            return b"fake-doc-bytes"

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: _Resp())
    monkeypatch.setattr(
        lm, "get_lead_by_phone",
        _async_ret({"id": "lead1", "program_interest": "MBA"}),
    )
    monkeypatch.setattr(om, "add_document", _async_ret({"id": "doc1"}))
    rec = _recorder()
    monkeypatch.setattr(svc, "generate_and_send_offer", rec)

    asyncio.run(m._handle_whatsapp_document(
        media_url="https://api.twilio.test/doc.jpg",
        content_type="image/jpeg",
        from_number="+15550001111",
        body_text="",
    ))

    assert rec.calls == []  # offer only on 'done', never per upload
    saved = list((tmp_path / "documents" / "lead1").glob("*.jpg"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"fake-doc-bytes"
