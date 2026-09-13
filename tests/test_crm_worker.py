"""
The CRM outbox worker — the loop that had no tests while running in production.

It does two jobs and both are easy to get subtly wrong:

* **drain** queued writes. If a tick can kill the loop, the queue silently stops
  moving while looking healthy.
* **expire** offers past their validity date. This one writes to the CRM, so it
  must only ever push once per offer — and only for people the CRM actually
  knows about. Marking an offer expired locally without telling the CRM would
  hide it from the next sweep forever.
"""

from __future__ import annotations

import asyncio

import pytest

from app.crm.worker import CrmOutboxWorker

LEAD = "lead-1"
USER = "0o6T100000000cjIAA"


@pytest.fixture
def crm_on(override_settings):
    override_settings(CRM_ENABLED=True)


@pytest.fixture
def worker_deps(monkeypatch):
    """Everything _tick reaches for, captured instead of executed."""
    seen = {"drained": 0, "pushed": [], "marked": [], "resolved": []}

    async def fake_depth():
        return 2

    async def fake_flush(limit=50):
        seen["drained"] += 1
        return {"attempted": 2, "succeeded": 2, "failed": 0, "dropped": 0}

    monkeypatch.setattr("app.crm.worker.enabled", lambda: True)
    monkeypatch.setattr("app.crm.worker.outbox.depth", fake_depth)
    monkeypatch.setattr("app.crm.worker.flush_outbox", fake_flush)

    async def fake_list_expired(limit=20):
        return [{"id": "offer-1", "lead_id": LEAD, "program": "MBA"}]

    async def fake_get_user(lead_id):
        seen["resolved"].append(lead_id)
        return USER

    async def fake_push_expired(user_id):
        seen["pushed"].append(user_id)
        return True

    async def fake_mark(offer_id):
        seen["marked"].append(offer_id)
        return True

    monkeypatch.setattr("app.offers.models.list_expired_offers", fake_list_expired)
    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", fake_get_user)
    monkeypatch.setattr("app.crm.status.push_offer_expired", fake_push_expired)
    monkeypatch.setattr("app.offers.models.mark_offer_expired", fake_mark)
    return seen


# ── lifecycle ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_start_is_idempotent_and_stop_is_safe(crm_on, worker_deps):
    """Two starts must not leave two loops running; a stop without a start must not raise."""
    worker = CrmOutboxWorker(poll_interval=3600)

    await worker.stop()          # never started
    await worker.start()
    await worker.start()         # second call is a no-op
    await worker.stop()
    await worker.stop()          # double stop


@pytest.mark.anyio
async def test_the_loop_survives_a_failing_tick(crm_on, monkeypatch):
    """
    A worker that dies on one bad tick is worse than no worker: the queue looks
    healthy while nothing moves.
    """
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("tick exploded")

    monkeypatch.setattr("app.crm.worker.enabled", lambda: True)
    worker = CrmOutboxWorker(poll_interval=5)
    monkeypatch.setattr(worker, "_tick", boom)

    await worker.start()
    await asyncio.sleep(0.05)
    assert worker._task is not None and not worker._task.done(), "the loop must still be alive"
    await worker.stop()
    assert calls["n"] >= 1, "the tick ran and raised, and the loop carried on"


# ── draining ─────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_tick_drains_and_sweeps(crm_on, worker_deps):
    worker = CrmOutboxWorker()

    await worker._tick()

    assert worker_deps["drained"] == 1
    assert worker.last_drain == {"attempted": 2, "succeeded": 2, "failed": 0, "dropped": 0}
    assert worker_deps["pushed"] == [USER], "the sweep ran in the same tick"


@pytest.mark.anyio
async def test_nothing_happens_when_the_crm_is_off(worker_deps, override_settings, monkeypatch):
    override_settings(CRM_ENABLED=False)
    # The fixture forces enabled() True so the other tests can run; this one is
    # about the gate itself, so it puts the real predicate back.
    monkeypatch.setattr("app.crm.worker.enabled", lambda: False)
    worker = CrmOutboxWorker()

    await worker._tick()

    assert worker_deps["drained"] == 0
    assert worker_deps["pushed"] == [], "a disabled CRM must not be swept against"


# ── expiring offers ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_lapsed_offer_is_pushed_then_marked(crm_on, worker_deps):
    """
    Marking is what makes the sweep idempotent — without it the same offer is
    pushed on every tick, forever.
    """
    worker = CrmOutboxWorker()

    await worker._expire_stale_offers()

    assert worker_deps["pushed"] == [USER]
    assert worker_deps["marked"] == ["offer-1"], "the local row records that the CRM was told"


@pytest.mark.anyio
async def test_a_lead_with_no_crm_link_is_skipped_and_left_unmarked(crm_on, worker_deps, monkeypatch):
    """
    Only offers the CRM knows about can expire there. Marking this one anyway
    would hide it from every future sweep — a silent loss.
    """
    async def no_link(lead_id):
        return ""

    monkeypatch.setattr("app.leads.models.get_lead_crm_user_id", no_link)
    worker = CrmOutboxWorker()

    await worker._expire_stale_offers()

    assert worker_deps["pushed"] == []
    assert worker_deps["marked"] == [], "nothing was told to the CRM, so nothing is marked"


@pytest.mark.anyio
async def test_a_failed_push_leaves_the_offer_to_retry(crm_on, worker_deps, monkeypatch):
    async def push_fails(user_id):
        return False

    monkeypatch.setattr("app.crm.status.push_offer_expired", push_fails)
    worker = CrmOutboxWorker()

    await worker._expire_stale_offers()

    assert worker_deps["marked"] == [], "a failed push must not be recorded as done"


@pytest.mark.anyio
async def test_a_broken_sweep_does_not_escape(crm_on, worker_deps, monkeypatch):
    """The sweep is the last thing a tick does; its failure must not poison the tick."""
    async def boom(limit=20):
        raise RuntimeError("database gone")

    monkeypatch.setattr("app.offers.models.list_expired_offers", boom)
    worker = CrmOutboxWorker()

    await worker._expire_stale_offers()  # must not raise


# ── the query that selects them ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_the_expiry_query_is_idempotent_and_conservative(monkeypatch):
    """
    The predicate carries three decisions: only offers past their date, only ones
    the student never answered, and only ones not already expired — without that
    last clause the same offer is pushed on every single tick.
    """
    from app.offers import models as offer_models

    captured: dict = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params

        def fetchall(self):
            return []

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(offer_models, "_get_db", lambda: FakeConn())

    assert await offer_models.list_expired_offers(limit=5) == []

    sql = captured["sql"]
    assert "valid_until < CURRENT_DATE" in sql
    assert "status = 'sent'" in sql, "an answered offer must not be expired"
    assert "crm_expired_at IS NULL" in sql, "the idempotency clause"
    assert captured["params"] == (5,)


@pytest.mark.anyio
async def test_marking_expired_records_the_timestamp(monkeypatch):
    from app.offers import models as offer_models

    captured: dict = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(offer_models, "_get_db", lambda: FakeConn())

    assert await offer_models.mark_offer_expired("offer-1") is True
    assert "crm_expired_at" in captured["sql"]
    assert captured["params"][-1] == "offer-1"


@pytest.mark.anyio
async def test_no_database_is_not_an_error(monkeypatch):
    from app.offers import models as offer_models

    monkeypatch.setattr(offer_models, "_get_db", lambda: None)

    assert await offer_models.list_expired_offers() == []
    assert await offer_models.mark_offer_expired("offer-1") is False
