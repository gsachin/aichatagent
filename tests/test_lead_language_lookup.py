"""Outbound reads the lead's language from the conversation that recorded it.

The worker already preferred ``lead["language"]`` over ``OUTBOUND_LANGUAGE``,
but ``get_lead`` never selected a language column, so the value was always
empty and every outbound opened on the campaign default. These tests pin the
lookup and the write-side plumbing that feeds it.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Conn:
    def __init__(self, row):
        self._cur = _Cursor(row)
        self.autocommit = False

    def cursor(self):
        return self._cur


def _patch_db(monkeypatch, row):
    import app.leads.models as models

    conn = _Conn(row)

    @contextmanager
    def _get_db():
        yield conn

    monkeypatch.setattr(models, "_get_db", _get_db)
    return conn


def _lead_row(language="hi"):
    return (
        "11111111-1111-1111-1111-111111111111",
        "+15550100",
        "Kuntesh",
        "k@example.com",
        "MBA",
        "pending",
        "manual",
        "",
        0,
        None,
        None,
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
        language,
    )


@pytest.mark.anyio
async def test_get_lead_selects_the_latest_conversation_language(monkeypatch):
    from app.leads.models import get_lead

    conn = _patch_db(monkeypatch, _lead_row("hi"))
    lead = await get_lead("11111111-1111-1111-1111-111111111111")

    assert lead is not None
    assert lead["language"] == "hi"
    assert "conversations c" in conn._cur.sql
    assert "c.lead_id = l.id" in conn._cur.sql
    assert "c.language <> ''" in conn._cur.sql


@pytest.mark.anyio
async def test_get_lead_by_phone_selects_the_same_language(monkeypatch):
    from app.leads.models import get_lead_by_phone

    _patch_db(monkeypatch, _lead_row("gu"))
    lead = await get_lead_by_phone("+15550100")

    assert lead is not None
    assert lead["language"] == "gu"


def test_row_mapper_keeps_the_original_thirteen_column_shape():
    from app.leads.models import _row_to_lead_dict

    lead = _row_to_lead_dict(_lead_row("hi")[:13])

    assert lead["name"] == "Kuntesh"
    assert "language" not in lead, (
        "the sentiment branch reads columns 13-16; the language must be "
        "mapped outside _row_to_lead_dict"
    )


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.anyio
async def test_interaction_log_passes_language_to_the_conversation(monkeypatch):
    import app.leads.models as models
    import app.leads.service as service
    import app.main as main

    seen = {}

    async def fake_get_lead_by_phone(phone):
        return {"id": "lead-1"}

    async def fake_get_crm_user_id(lead_id):
        return ""

    async def fake_log_interaction(**kwargs):
        seen.update(kwargs)
        return {"id": "conv-1", "lead_id": "lead-1"}

    monkeypatch.setattr(models, "get_lead_by_phone", fake_get_lead_by_phone)
    monkeypatch.setattr(models, "get_lead_crm_user_id", fake_get_crm_user_id)
    monkeypatch.setattr(service, "log_interaction", fake_log_interaction)

    response = await main.api_log_interaction(_Request({
        "phone_number": "+15550100",
        "transcript": "User: hi\nAssistant: hello",
        "channel": "streamlit",
        "language": "hi",
    }))

    assert response["status"] == "ok"
    assert seen["language"] == "hi"
