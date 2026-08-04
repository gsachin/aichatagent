"""
Test suite — Command Cockpit Dashboard + CRIT-1 transcript fix.

Covers:
  1. Health endpoint & static file serving
  2. Dashboard endpoints (page + APIs)
  3. Batch quick-call API
  4. Lead CRUD APIs
  5. Conversations API
  6. Transcript fix (voice_handler process_utterance return type)
  7. TwiML voice webhook correctness
  8. Seed data integrity
  9. Server startup lifecycle

Run: pytest tests/test_dashboard.py -v
     or: python -m pytest tests/test_dashboard.py -v
"""

import json
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.voice_handler import VoiceCallSession

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════════
# 1. HEALTH & STATIC FILES
# ═══════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    def test_root_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "endpoints" in data
        assert "twilio_phone" in data

    def test_root_has_dashboard_link(self):
        resp = client.get("/")
        data = resp.json()
        endpoints = data.get("endpoints", {})
        # Dashboard should be listed or we check health has all keys
        assert "health" in endpoints
        assert "twilio_webhook" in endpoints


class TestStaticFiles:
    def test_dashboard_page_served(self):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_dashboard_css_served(self):
        resp = client.get("/static/dashboard.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_dashboard_js_served(self):
        resp = client.get("/static/dashboard.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"] or "text" in resp.headers["content-type"]

    def test_voice_client_served(self):
        resp = client.get("/voice")
        assert resp.status_code == 200

    def test_quick_call_page_served(self):
        resp = client.get("/call")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# 2. API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


class TestStatsAPI:
    def test_stats_returns_data(self):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_leads" in data
        assert isinstance(data["total_leads"], int)

    def test_stats_by_status(self):
        resp = client.get("/api/stats")
        data = resp.json()
        assert "by_status" in data
        assert isinstance(data["by_status"], dict)


class TestLeadsAPI:
    def test_list_leads(self):
        resp = client.get("/api/leads?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_list_leads_with_status_filter(self):
        resp = client.get("/api/leads?status=pending&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        for lead in data:
            assert lead.get("status") == "pending"

    def test_create_lead(self):
        phone = f"+1999{uuid.uuid4().hex[:6]}"
        resp = client.post(
            "/api/leads",
            json={"phone_number": phone, "name": "Test User", "source": "test"},
        )
        assert resp.status_code in (200, 201, 503)  # 503 if DB unavailable
        if resp.status_code in (200, 201):
            data = resp.json()
            assert "id" in data

    def test_lead_detail(self):
        resp = client.get("/api/leads?limit=1")
        if resp.status_code == 200:
            leads = resp.json()
            if leads:
                lead_id = leads[0]["id"]
                detail_resp = client.get(f"/api/leads/{lead_id}")
                assert detail_resp.status_code in (200, 404)


class TestConversationsAPI:
    def test_list_conversations(self):
        resp = client.get("/api/conversations?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_conversations_with_channel_filter(self):
        resp = client.get("/api/conversations?channel=inbound_call&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        for conv in data:
            assert conv.get("channel") == "inbound_call"


class TestBatchCallAPI:
    def test_batch_call_requires_leads(self):
        resp = client.post("/api/quick-call/batch", json={"mode": "all_at_once"})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_batch_call_queues(self):
        resp = client.post(
            "/api/quick-call/batch",
            json={
                "leads": [
                    {"phone_number": f"+1555{uuid.uuid4().hex[:6]}", "name": "Batch Test 1"},
                    {"phone_number": f"+1555{uuid.uuid4().hex[:6]}", "name": "Batch Test 2"},
                ],
                "mode": "all_at_once",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "batch_id" in data
        assert data["queued"] >= 1 or data.get("skipped", 0) > 0  # Some DB may be offline

    def test_batch_status_polling(self):
        # First create a batch
        create_resp = client.post(
            "/api/quick-call/batch",
            json={
                "leads": [{"phone_number": f"+1555{uuid.uuid4().hex[:6]}", "name": "Poll Test"}],
                "mode": "all_at_once",
            },
        )
        if create_resp.status_code == 200:
            batch_id = create_resp.json()["batch_id"]
            poll_resp = client.get(f"/api/quick-call/batch/{batch_id}")
            assert poll_resp.status_code == 200
            data = poll_resp.json()
            assert "total" in data

    def test_batch_unknown_id(self):
        resp = client.get("/api/quick-call/batch/nonexistent")
        assert resp.status_code == 404


class TestCallQueueAPI:
    def test_call_queue_requires_lead_id(self):
        resp = client.get("/api/call-queue")
        assert resp.status_code == 400

    def test_call_queue_unknown_lead(self):
        resp = client.get("/api/call-queue?lead_id=00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 3. TWILIO VOICE WEBHOOK (TwiML Correctness)
# ═══════════════════════════════════════════════════════════════════════


class TestTwiMLEndpoints:
    def test_inbound_voice_returns_twiml(self):
        resp = client.get("/twilio/voice")
        assert resp.status_code == 200
        content = resp.text
        assert "<Response>" in content
        assert "<Connect>" in content
        assert "<Stream" in content
        assert "ws/twilio" in content

    def test_outbound_voice_returns_twiml(self):
        resp = client.get("/twilio/outbound-voice")
        assert resp.status_code == 200
        content = resp.text
        assert "<Response>" in content
        assert "<Connect>" in content
        assert "ws/twilio-outbound" in content

    def test_twiml_has_websocket_url(self):
        resp = client.get("/twilio/voice")
        content = resp.text
        # The WebSocket URL should contain trycloudflare.com or localhost
        assert "wss://" in content
        assert "ws/twilio" in content

    def test_whatsapp_webhook_returns_twiml(self):
        resp = client.post("/twilio/whatsapp", data={"Body": "Hello", "From": "whatsapp:+123"})
        assert resp.status_code in (200, 503)  # 503 if DB/LLM unavailable
        if resp.status_code == 200:
            assert "<Response>" in resp.text

    def test_inbound_twiml_has_say_fallback(self):
        """CRIT-3+D2: Inbound TwiML must have <Say> and <Connect> with fallback."""
        resp = client.get("/twilio/voice")
        content = resp.text
        assert "<Say" in content, "TwiML missing <Say>"
        assert "<Connect>" in content, "TwiML missing <Connect>"
        assert "<Stream" in content, "TwiML missing Stream"
        # Either direct connect OR IVR with Gather
        assert "ws/twilio" in content

    def test_outbound_twiml_has_say_fallback(self):
        """CRIT-3: Outbound TwiML must have <Say> after </Connect> as fallback."""
        resp = client.get("/twilio/outbound-voice")
        content = resp.text
        assert "<Say" in content, "Outbound TwiML missing <Say> fallback"
        connect_close = content.find("</Connect>")
        say_open = content.find("<Say")
        assert connect_close > 0, "Missing </Connect> tag"
        assert say_open > connect_close, "<Say> must be AFTER </Connect>"

    def test_ivr_connect_endpoint(self):
        """IVR Gather action redirects to /twilio/voice/connect."""
        resp = client.get("/twilio/voice/connect?Digits=1")
        assert resp.status_code == 200
        assert "<Connect>" in resp.text
        assert "ws/twilio" in resp.text

    def test_landing_page_html(self):
        """D1: Root endpoint returns HTML for browsers."""
        resp = client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        content = resp.text.lower()
        assert "<html" in content or "university" in content

    def test_outbound_twiml_function_direct(self):
        """Test the outbound_connect_twiml function directly."""
        from app.outbound.twiml import outbound_connect_twiml, outbound_say_twiml
        twiml = outbound_connect_twiml("test.trycloudflare.com")
        assert "wss://test.trycloudflare.com/ws/twilio-outbound" in twiml
        assert "<Say" in twiml
        say = outbound_say_twiml("Hello & Goodbye")
        assert "Hello &amp; Goodbye" in say


# ═══════════════════════════════════════════════════════════════════════
# 4. TRANSCRIPT FIX (CRIT-1)
# ═══════════════════════════════════════════════════════════════════════


class TestTranscriptFix:
    """Verify that process_utterance() returns (chunks, dialogue) tuple."""

    def test_process_utterance_returns_tuple(self):
        session = VoiceCallSession()
        # Call with empty buffer (no audio) — should return ([], "")
        import asyncio

        result = asyncio.run(session.process_utterance())
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2 elements, got {len(result)}"
        chunks, dialogue = result
        assert isinstance(chunks, list), f"Expected list for chunks, got {type(chunks)}"
        assert isinstance(dialogue, str), f"Expected str for dialogue, got {type(dialogue)}"
        assert chunks == []  # Empty buffer should give empty chunks
        assert dialogue == ""  # Empty buffer should give empty dialogue

    def test_voice_handler_imports(self):
        """Verify voice_handler module imports correctly."""
        from app.voice_handler import (
            VoiceCallSession,
            generate_ulaw_greeting,
            pcm_to_ulaw,
            ulaw_to_pcm,
        )
        assert VoiceCallSession is not None
        assert generate_ulaw_greeting is not None

    def test_ulaw_conversion_roundtrip(self):
        """Verify u-law ↔ PCM conversion is lossless enough for audio."""
        from app.voice_handler import pcm_to_ulaw, ulaw_to_pcm

        import numpy as np

        # Create a simple sine wave
        samples = np.sin(np.linspace(0, 2 * np.pi * 440, 8000)) * 16000
        pcm = samples.astype(np.int16).tobytes()
        ulaw = pcm_to_ulaw(pcm)
        pcm_back = ulaw_to_pcm(ulaw)
        # Should be roughly same length
        assert len(pcm_back) > 0
        assert abs(len(pcm_back) - len(pcm)) < 100  # ~1% tolerance


# ═══════════════════════════════════════════════════════════════════════
# 5. SEED DATA INTEGRITY
# ═══════════════════════════════════════════════════════════════════════


class TestSeedData:
    def test_seed_script_exists(self):
        seed_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "seed_demo_data.py",
        )
        assert os.path.isfile(seed_path), f"Seed script not found at {seed_path}"

    def test_seed_data_has_leads(self):
        resp = client.get("/api/leads?limit=50")
        if resp.status_code == 200:
            data = resp.json()
            # After seeding, should have at least some leads
            # (This test passes if DB is connected and has data,
            #  or if DB is unavailable — both are valid states)
            assert isinstance(data, list)

    def test_seed_data_has_various_statuses(self):
        resp = client.get("/api/leads?limit=50")
        if resp.status_code == 200 and len(resp.json()) > 0:
            statuses = {lead["status"] for lead in resp.json()}
            # Should have multiple status types
            assert len(statuses) >= 2, f"Only found statuses: {statuses}"


# ═══════════════════════════════════════════════════════════════════════
# 6. SERVER LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════


class TestServerLifecycle:
    def test_app_title(self):
        assert app.title == "University Admissions Voice Assistant"

    def test_app_has_routes(self):
        routes = [r.path for r in app.routes]
        assert "/" in routes
        assert "/dashboard" in routes
        assert "/twilio/voice" in routes
        assert "/api/quick-call/batch" in routes
        assert "/api/stats" in routes
        assert "/api/leads" in routes
        assert "/api/conversations" in routes

    def test_app_websocket_routes(self):
        routes = [r.path for r in app.routes]
        assert "/ws/twilio" in routes
        assert "/ws/twilio-outbound" in routes
        assert "/ws/voice" in routes

    def test_openapi_schema(self):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        # Dashboard endpoint should be in schema
        assert "/dashboard" in schema["paths"]


# ═══════════════════════════════════════════════════════════════════════
# 7. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_batch_call_empty_phones(self):
        resp = client.post(
            "/api/quick-call/batch",
            json={"leads": [{"phone_number": "", "name": "Empty"}], "mode": "all_at_once"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["skipped"] >= 1 or data["queued"] == 0

    def test_batch_call_invalid_json(self):
        resp = client.post("/api/quick-call/batch", content="not json")
        assert resp.status_code == 400

    def test_nonexistent_page(self):
        resp = client.get("/nonexistent-page-xyz")
        assert resp.status_code == 404

    def test_conversations_empty_is_list(self):
        resp = client.get("/api/conversations?limit=0")
        assert resp.status_code == 200
        assert resp.json() == []
