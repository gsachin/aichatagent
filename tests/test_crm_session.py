"""
Tests for the conversation session registry (Phase 1 of the Salesforce integration).

Phase 1's gate is: *a session exposes a stable conversation_id from its first
moment, with CRM_ENABLED=false — i.e. zero behaviour change.* These tests cover
the first half; the second half is that nothing here performs I/O.

The registry is process-local module state, so every test starts from a clean
slate via the autouse ``clean_registry`` fixture.
"""

from __future__ import annotations

import pytest

from app.crm import session as crm_session


@pytest.fixture(autouse=True)
def clean_registry():
    """Every test gets an empty registry and leaves one behind."""
    crm_session.reset()
    yield
    crm_session.reset()


# ── identity ─────────────────────────────────────────────────────────

def test_start_returns_a_conversation_id():
    s = crm_session.start("inbound_call", "CA123")
    assert s.conversation_id
    assert s.channel == "inbound_call"
    assert s.key == "CA123"


def test_id_is_stable_within_a_session():
    """The whole point: repeated lookups during one conversation agree."""
    first = crm_session.start("inbound_call", "CA123")
    again = crm_session.start("inbound_call", "CA123")
    assert first is again
    assert first.conversation_id == again.conversation_id


def test_duplicate_start_event_does_not_fork_the_conversation():
    """
    Twilio can redeliver a `start` event. The second one must not mint a new
    conversation, or the CRM would see two conversations for one call.
    """
    a = crm_session.start("inbound_call", "CA999").conversation_id
    b = crm_session.start("inbound_call", "CA999").conversation_id
    assert a == b
    assert len(crm_session.active("inbound_call")) == 1


def test_ids_are_distinct_across_sessions():
    a = crm_session.start("inbound_call", "CA1").conversation_id
    b = crm_session.start("inbound_call", "CA2").conversation_id
    assert a != b


def test_same_key_on_different_channels_does_not_collide():
    """A stream sid and a phone number are both opaque strings — keep them apart."""
    call = crm_session.start("inbound_call", "SAME").conversation_id
    wa = crm_session.start("whatsapp", "SAME").conversation_id
    assert call != wa


def test_explicit_conversation_id_is_honoured():
    """WhatsApp resumption passes an id read back from the database."""
    s = crm_session.start("whatsapp", "+15550100", conversation_id="conv-from-db")
    assert s.conversation_id == "conv-from-db"


def test_blank_key_gets_a_generated_one_instead_of_sharing_a_session():
    """
    A blank key must not funnel every anonymous conversation into one session.
    """
    a = crm_session.start("whatsapp", "")
    b = crm_session.start("whatsapp", "")
    assert a.key and b.key
    assert a.conversation_id != b.conversation_id


# ── lifecycle ────────────────────────────────────────────────────────

def test_end_returns_the_session_so_the_id_survives():
    """
    end() returns rather than discards: the disconnect handler needs the id at
    exactly the moment the session disappears from the registry.
    """
    started = crm_session.start("inbound_call", "CA7")
    ended = crm_session.end("inbound_call", "CA7")
    assert ended is not None
    assert ended.conversation_id == started.conversation_id
    assert crm_session.get("inbound_call", "CA7") is None


def test_end_on_unknown_key_is_none_not_an_error():
    assert crm_session.end("inbound_call", "never-started") is None


def test_ending_frees_the_key_for_a_new_conversation():
    first = crm_session.start("inbound_call", "CA1").conversation_id
    crm_session.end("inbound_call", "CA1")
    second = crm_session.start("inbound_call", "CA1").conversation_id
    assert first != second


def test_start_enriches_but_does_not_overwrite_identity():
    s = crm_session.start("inbound_call", "CA5")
    crm_session.start("inbound_call", "CA5", phone_number="+15550111", lead_id="lead-1")
    assert s.phone_number == "+15550111"
    assert s.lead_id == "lead-1"

    # A second call with different values must not rewrite what we already have.
    crm_session.start("inbound_call", "CA5", phone_number="+19999999")
    assert s.phone_number == "+15550111"


def test_set_crm_user_id_attaches_to_a_live_session():
    crm_session.start("inbound_call", "CA8")
    crm_session.set_crm_user_id("inbound_call", "CA8", "a0X000000000001")
    assert crm_session.get("inbound_call", "CA8").crm_user_id == "a0X000000000001"


def test_set_crm_user_id_on_unknown_session_is_a_no_op():
    crm_session.set_crm_user_id("inbound_call", "nope", "a0X")  # must not raise


# ── expiry ───────────────────────────────────────────────────────────

def test_session_is_not_expired_immediately():
    s = crm_session.start("whatsapp", "+15550100")
    assert not s.is_expired(idle_window_seconds=60)


def test_touch_resets_the_idle_window():
    s = crm_session.start("whatsapp", "+15550100")
    s.last_activity = s.last_activity.replace(year=s.last_activity.year - 1)
    assert s.is_expired(idle_window_seconds=60)
    s.touch()
    assert not s.is_expired(idle_window_seconds=60)


def test_purge_expired_drops_only_stale_sessions():
    fresh = crm_session.start("whatsapp", "+15550100")
    stale = crm_session.start("whatsapp", "+15550199")
    stale.last_activity = stale.last_activity.replace(year=stale.last_activity.year - 1)

    dropped = crm_session.purge_expired(idle_window_seconds=3600)

    assert dropped == 1
    assert crm_session.get("whatsapp", "+15550100") is fresh
    assert crm_session.get("whatsapp", "+15550199") is None


def test_purge_can_be_scoped_to_one_channel():
    call = crm_session.start("inbound_call", "CA1")
    wa = crm_session.start("whatsapp", "+15550100")
    for s in (call, wa):
        s.last_activity = s.last_activity.replace(year=s.last_activity.year - 1)

    assert crm_session.purge_expired(3600, channel="whatsapp") == 1
    assert crm_session.get("inbound_call", "CA1") is call


# ── WhatsApp: resume within the idle window ──────────────────────────

@pytest.mark.anyio
async def test_whatsapp_resumes_within_the_idle_window():
    """
    Nine messages in one sitting must be one conversation, not nine — otherwise
    the CRM would see nine conversations for one student.
    """
    first = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    second = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    assert first.conversation_id == second.conversation_id


@pytest.mark.anyio
async def test_whatsapp_starts_a_new_conversation_after_the_window():
    first = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    # Age the session past the window.
    s = crm_session.get("whatsapp", "+15550100")
    s.last_activity = s.last_activity.replace(year=s.last_activity.year - 1)

    second = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    assert first.conversation_id != second.conversation_id


@pytest.mark.anyio
async def test_whatsapp_resumes_from_the_database_when_the_registry_is_cold(monkeypatch):
    """
    A restart mid-conversation must not split one WhatsApp conversation in two,
    so the resolver falls back to the most recent row for that number.
    """
    async def fake_lookup(phone_number, within_seconds, channel="whatsapp"):
        assert phone_number == "+15550100"
        assert channel == "whatsapp"
        return {"conversation_id": "conv-survived-restart"}

    monkeypatch.setattr(
        "app.leads.models.get_recent_conversation_for_phone", fake_lookup, raising=True
    )

    s = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    assert s.conversation_id == "conv-survived-restart"


@pytest.mark.anyio
async def test_whatsapp_db_failure_degrades_to_a_new_session(monkeypatch):
    """
    Running without Postgres is a supported mode in this app. A database error
    must produce a fresh conversation, not an exception in the webhook path.
    """
    async def boom(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(
        "app.leads.models.get_recent_conversation_for_phone", boom, raising=True
    )

    s = await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    assert s.conversation_id


@pytest.mark.anyio
async def test_whatsapp_does_not_resume_another_channels_conversation(monkeypatch):
    """
    A voice call from the same number an hour ago must not be treated as the
    WhatsApp conversation — the DB lookup is channel-scoped.
    """
    seen = {}

    async def fake_lookup(phone_number, within_seconds, channel="whatsapp"):
        seen["channel"] = channel
        return None

    monkeypatch.setattr(
        "app.leads.models.get_recent_conversation_for_phone", fake_lookup, raising=True
    )
    await crm_session.resolve_whatsapp_session("+15550100", idle_window_hours=6)
    assert seen["channel"] == "whatsapp"


# ── plumbing: the id must reach the SQL ──────────────────────────────

class _FakeCursor:
    def __init__(self, row):
        self._row = row
        self.sql: str | None = None
        self.params: tuple | None = None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConn:
    def __init__(self, row):
        self._cursor = _FakeCursor(row)
        self.autocommit = False

    def cursor(self):
        return self._cursor


def _fake_row(conversation_id: str):
    """A conversations row in the widened 13-column shape."""
    import uuid as _uuid

    return (
        str(_uuid.uuid4()), "lead-1", "+15550100", "whatsapp",
        "transcript", "summary", 0, "outcome", False, "", "{}",
        "2026-09-11T00:00:00+00:00", conversation_id,
    )


def _patch_db(monkeypatch, row):
    """Point app.leads.models._get_db at a fake connection."""
    from contextlib import contextmanager
    import app.leads.models as models

    conn = _FakeConn(row)

    @contextmanager
    def _fake_get_db():
        yield conn

    monkeypatch.setattr(models, "_get_db", _fake_get_db)
    return conn


@pytest.mark.anyio
async def test_conversation_id_is_written_to_the_insert(monkeypatch):
    """
    The id minted at session start must land in the INSERT — with the column
    list, the placeholders and the parameters all still in step. A mismatch here
    is silent data corruption rather than an error.
    """
    from app.leads.models import create_conversation

    conn = _patch_db(monkeypatch, _fake_row("conv-from-session"))
    await create_conversation(lead_id="lead-1", conversation_id="conv-from-session")

    sql, params = conn.cursor().sql, conn.cursor().params
    assert "conversation_id" in sql
    assert len(params) == 12, f"expected 12 bound params, got {len(params)}"
    assert sql.count("%s") == len(params), "placeholder / param count mismatch"
    assert params[-1] == "conv-from-session"


@pytest.mark.anyio
async def test_missing_conversation_id_falls_back_to_the_row_id(monkeypatch):
    """
    Legacy callers that pass nothing must keep working unchanged: the row id
    becomes the conversation id, so the column is never NULL and every existing
    query that groups by it behaves as if nothing changed.
    """
    from app.leads.models import create_conversation

    conn = _patch_db(monkeypatch, _fake_row("whatever-the-db-returns"))
    await create_conversation(lead_id="lead-1")

    params = conn.cursor().params
    assert len(params) == 12
    assert params[-1] == params[0], "without an explicit id the row id must be reused"


@pytest.mark.anyio
async def test_service_layer_forwards_the_conversation_id(monkeypatch):
    """
    app/leads/service.py sits between main.py and the models — if it drops the
    id there, the whole chain silently degrades to one conversation per row.
    """
    import app.leads.models as models
    from app.leads.service import log_interaction

    captured = {}

    async def fake_create_conversation(**kwargs):
        captured.update(kwargs)
        return {"id": "row-1", "conversation_id": kwargs.get("conversation_id")}

    async def fake_upsert(**kwargs):
        return {"id": "lead-1", "name": "", "email": "", "program_interest": ""}

    monkeypatch.setattr(models, "create_conversation", fake_create_conversation)
    monkeypatch.setattr(models, "upsert_lead_by_phone", fake_upsert)

    await log_interaction(
        phone_number="+15550100",
        channel="whatsapp",
        transcript="hello",
        conversation_id="conv-abc",
    )

    assert captured["conversation_id"] == "conv-abc"


@pytest.mark.anyio
async def test_service_layer_tolerates_a_missing_conversation_id(monkeypatch):
    """Existing callers that never pass one must still log a conversation."""
    import app.leads.models as models
    from app.leads.service import log_interaction

    captured = {}

    async def fake_create_conversation(**kwargs):
        captured.update(kwargs)
        return {"id": "row-1"}

    async def fake_upsert(**kwargs):
        return {"id": "lead-1", "name": "", "email": "", "program_interest": ""}

    monkeypatch.setattr(models, "create_conversation", fake_create_conversation)
    monkeypatch.setattr(models, "upsert_lead_by_phone", fake_upsert)

    await log_interaction(phone_number="+15550100", channel="whatsapp", transcript="hi")

    assert captured["conversation_id"] is None


# ── the phase gate: inert with the feature off ───────────────────────

def test_registry_never_touches_the_network():
    """
    Phase 1's gate is zero behaviour change with CRM_ENABLED=false. The registry
    is local-only by construction — guard it against a future edit that quietly
    adds a CRM call into the session path.

    Checked via the import graph rather than a source scan, so prose in the
    docstring (which does name the CRM endpoints) cannot false-positive.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(crm_session))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"httpx", "requests", "urllib", "urllib3", "aiohttp", "socket"}
    assert not (imported & forbidden), (
        f"app/crm/session.py must stay network-free, found {sorted(imported & forbidden)}"
    )


def test_crm_is_disabled_by_default():
    """Everything after Phase 1 is gated on this switch being explicitly enabled."""
    from app.config import settings

    assert settings.CRM_ENABLED is False
    assert settings.CRM_IDLE_WINDOW_HOURS == 6.0


def test_migration_is_additive_and_idempotent():
    """
    init_db() re-runs this DDL on every startup, so each statement must be
    safe to re-run and nothing may drop or rename a column.
    """
    from app.crm.schema import ALL_CRM_SQL

    # The backfill is the one statement that cannot carry IF NOT EXISTS — an
    # UPDATE has no such clause. It is idempotent by predicate instead: it only
    # touches rows still NULL, so a second run matches nothing.
    backfill = "UPDATE conversations"
    assert "WHERE conversation_id IS NULL" in ALL_CRM_SQL

    statements = [s.strip() for s in ALL_CRM_SQL.split(";") if s.strip()]
    checked = 0
    for stmt in statements:
        if stmt.upper().startswith(backfill.upper()):
            continue
        upper = stmt.upper()
        assert "IF NOT EXISTS" in upper, f"not idempotent: {stmt[:70]}"
        for destructive in ("DROP ", "TRUNCATE", "RENAME ", "DELETE "):
            assert destructive not in upper, f"destructive DDL: {stmt[:70]}"
        checked += 1
    assert checked >= 6, "expected the ALTERs, the table and the indexes to be checked"
