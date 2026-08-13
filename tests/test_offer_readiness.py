"""
Tests for offer readiness validation and Streamlit backend helpers.

Run: python -m pytest tests/test_offer_readiness.py -v
"""

import pytest
from unittest.mock import patch, MagicMock


# ==========================================================================
# missing_fields_text
# ==========================================================================

class TestMissingFieldsText:
    def test_single_field(self):
        from app.offers.service import missing_fields_text
        assert "your full name" in missing_fields_text(["name"])

    def test_two_fields(self):
        from app.offers.service import missing_fields_text
        result = missing_fields_text(["name", "email"])
        assert "your full name" in result
        assert "your email address" in result
        assert " and " in result

    def test_multiple_fields(self):
        from app.offers.service import missing_fields_text
        result = missing_fields_text(["name", "email", "document"])
        assert ", " in result
        assert " and " in result
        assert "at least one document" in result

    def test_unknown_field_passthrough(self):
        from app.offers.service import missing_fields_text
        result = missing_fields_text(["unknown_field"])
        assert "unknown_field" in result

    def test_empty_list(self):
        from app.offers.service import missing_fields_text
        # empty list -> empty string
        result = missing_fields_text([])
        assert result == ""


# ==========================================================================
# evaluate_offer_readiness
# ==========================================================================

class TestEvaluateOfferReadiness:
    def _make_lead(self, **overrides):
        return {
            "id": "test-id",
            "name": "Test User",
            "email": "test@example.com",
            "phone_number": "+1234567890",
            "program_interest": "MBA",
            **overrides,
        }

    @pytest.mark.asyncio
    async def test_lead_not_found(self, monkeypatch):
        from app.offers.service import evaluate_offer_readiness

        async def fake_get_lead(_):
            return None
        monkeypatch.setattr("app.leads.models.get_lead", fake_get_lead)

        result = await evaluate_offer_readiness("nonexistent")
        assert result["ready"] is False
        assert "lead" in result["missing"]
        assert result["lead"] is None
        assert result["documents"] == 0

    @pytest.mark.asyncio
    async def test_all_ready(self, monkeypatch):
        from app.offers.service import evaluate_offer_readiness

        async def fake_get_lead(_):
            return self._make_lead()
        async def fake_list_docs(_):
            return [{"id": "d1"}]
        monkeypatch.setattr("app.leads.models.get_lead", fake_get_lead)
        monkeypatch.setattr("app.offers.models.list_documents", fake_list_docs)

        result = await evaluate_offer_readiness("test-id")
        assert result["ready"] is True
        assert result["missing"] == []
        assert result["documents"] == 1

    @pytest.mark.asyncio
    async def test_missing_program_interest(self, monkeypatch):
        from app.offers.service import evaluate_offer_readiness

        async def fake_get_lead(_):
            return self._make_lead(program_interest="")
        async def fake_list_docs(_):
            return [{"id": "d1"}]
        monkeypatch.setattr("app.leads.models.get_lead", fake_get_lead)
        monkeypatch.setattr("app.offers.models.list_documents", fake_list_docs)

        result = await evaluate_offer_readiness("test-id")
        assert result["ready"] is False
        assert "program_interest" in result["missing"]
        assert result["documents"] == 1

    @pytest.mark.asyncio
    async def test_missing_name_and_no_docs(self, monkeypatch):
        from app.offers.service import evaluate_offer_readiness

        async def fake_get_lead(_):
            return self._make_lead(name="   ")
        async def fake_list_docs(_):
            return []
        monkeypatch.setattr("app.leads.models.get_lead", fake_get_lead)
        monkeypatch.setattr("app.offers.models.list_documents", fake_list_docs)

        result = await evaluate_offer_readiness("test-id")
        assert result["ready"] is False
        assert "name" in result["missing"]
        assert "document" in result["missing"]
        assert result["documents"] == 0

    @pytest.mark.asyncio
    async def test_missing_email_and_phone(self, monkeypatch):
        from app.offers.service import evaluate_offer_readiness

        async def fake_get_lead(_):
            return self._make_lead(email="", phone_number="")
        async def fake_list_docs(_):
            return [{"id": "d1"}]
        monkeypatch.setattr("app.leads.models.get_lead", fake_get_lead)
        monkeypatch.setattr("app.offers.models.list_documents", fake_list_docs)

        result = await evaluate_offer_readiness("test-id")
        assert result["ready"] is False
        assert "email" in result["missing"]
        assert "phone_number" in result["missing"]
        assert "program_interest" not in result["missing"]


# ==========================================================================
# Streamlit backend helpers
# ==========================================================================

class TestBackendHealthy:
    def test_healthy(self, monkeypatch):
        from app.streamlit_backend import backend_healthy

        class FakeResponse:
            status_code = 200
        monkeypatch.setattr("requests.get", lambda url, timeout: FakeResponse())
        assert backend_healthy() is True

    def test_unhealthy_status(self, monkeypatch):
        from app.streamlit_backend import backend_healthy

        class FakeResponse:
            status_code = 500
        monkeypatch.setattr("requests.get", lambda url, timeout: FakeResponse())
        assert backend_healthy() is False

    def test_connection_error(self, monkeypatch):
        from app.streamlit_backend import backend_healthy

        monkeypatch.setattr(
            "requests.get",
            lambda url, timeout: (_ for _ in ()).throw(ConnectionError("refused")),
        )
        assert backend_healthy() is False


class TestSyncLead:
    def test_sync_with_existing_id_success(self, monkeypatch):
        from app.streamlit_backend import sync_lead

        calls = []

        def fake_put(url, json, timeout):
            calls.append(("put", url, json))
            r = MagicMock()
            r.ok = True
            r.json.return_value = {"id": "lead-123", "name": "Test"}
            return r

        monkeypatch.setattr("requests.put", fake_put)

        lid, err = sync_lead("Test", "a@b.com", "+123", "MBA", "lead-123")
        assert lid == "lead-123"
        assert err == ""
        assert len(calls) == 1

    def test_sync_no_id_falls_back_to_post(self, monkeypatch):
        from app.streamlit_backend import sync_lead

        calls = []

        def fake_post(url, json, timeout):
            calls.append(("post", url, json))
            r = MagicMock()
            r.ok = True
            r.json.return_value = {"id": "lead-new"}
            return r

        monkeypatch.setattr("requests.post", fake_post)

        lid, err = sync_lead("Test", "a@b.com", "+123", "MBA", "")
        assert lid == "lead-new"
        assert err == ""

    def test_sync_no_phone_returns_error(self, monkeypatch):
        from app.streamlit_backend import sync_lead

        lid, err = sync_lead("Test", "a@b.com", "", "MBA", "")
        assert "no phone" in err.lower()

    def test_sync_all_fail_returns_error(self, monkeypatch):
        from app.streamlit_backend import sync_lead

        monkeypatch.setattr(
            "requests.put",
            lambda url, json, timeout: (_ for _ in ()).throw(ConnectionError),
        )
        monkeypatch.setattr(
            "requests.post",
            lambda url, json, timeout: (_ for _ in ()).throw(ConnectionError),
        )

        lid, err = sync_lead("Test", "a@b.com", "+123", "MBA", "lead-123")
        assert lid == "lead-123"  # original id preserved
        assert "not reachable" in err


class TestSyncProgram:
    def test_delegates_to_sync_lead(self, monkeypatch):
        from app.streamlit_backend import sync_program

        calls = []

        def fake_put(url, json, timeout):
            calls.append(json)
            r = MagicMock()
            r.ok = True
            r.json.return_value = {"id": "lead-456"}
            return r

        monkeypatch.setattr("requests.put", fake_put)

        lid, err = sync_program("Data Science", "Bob", "b@c.com", "+999", "lead-456")
        assert lid == "lead-456"
        assert err == ""
        # program_interest should be in the PUT payload
        assert any(j.get("program_interest") == "Data Science" for j in calls)
