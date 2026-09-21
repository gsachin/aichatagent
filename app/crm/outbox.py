"""
Durable retry queue for CRM writes that could not be delivered.

Without this, a Salesforce outage silently loses status updates — a student
accepts an offer and the CRM never hears about it. The status writes are small,
idempotent and order-insensitive (each sets named fields), which makes them
ideal to queue and replay.

What is queued, and what deliberately is not
--------------------------------------------
**Queued:** status updates. They are the product-visible signal — sentiment,
offer released, offer accepted — and losing one is losing a business fact.

**Not queued:** ``lookup-or-create``. A failed lookup degrades to "this
conversation has no CRM link", which is logged and survivable. Replaying a
*create* minutes later, against an API with no uniqueness constraint and a
non-deterministic lookup, is how duplicates get made. Better to miss the link
than manufacture a second student record.

Permanent failures are never queued either — see ``sync.push_status``. A
restricted-picklist violation (R16) reported as a 500 would otherwise retry
forever, which is what a naive outbox does by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger("crm.outbox")

# Replay is only ever attempted for ops this module understands.
#
# "status"  — the three-field update on /users/{id}/status.
# "profile" — the wider record update on /admissions/{id} (program, offer
#             lifecycle, admission status). Kept separate because the two routes
#             have OPPOSITE null semantics: an explicit null means "leave alone"
#             on the users route and "clear the field" on the admissions one, so
#             a replay must know which route it is replaying to.
SUPPORTED_OPS = frozenset({"status", "profile"})

# Backoff between replay attempts, indexed by attempt count.
_REPLAY_BACKOFF_S = (30, 120, 600, 1800, 3600, 7200)


def _connection_string() -> str:
    # Reuse the app's single source of DB configuration rather than copying it.
    from app.database import _connection_string as connection_string

    return connection_string()


@contextmanager
def _get_db():
    """Yield a psycopg2 connection, or None when Postgres is unavailable."""
    conn = None
    try:
        import psycopg2

        conn = psycopg2.connect(_connection_string())
        yield conn
    except Exception:
        logger.debug("crm.outbox: database unavailable", exc_info=True)
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _next_attempt_after(attempts: int) -> datetime:
    """
    When to try again, given how many attempts have already failed.

    30s, then 2m, 10m, 30m, 1h, and 2h thereafter — slow enough that a long
    Salesforce outage does not become a retry storm, fast enough that a blip
    resolves within a minute.
    """
    idx = min(max(attempts - 1, 0), len(_REPLAY_BACKOFF_S) - 1)
    return datetime.now(timezone.utc) + timedelta(seconds=_REPLAY_BACKOFF_S[idx])


async def enqueue(
    op: str,
    payload: dict[str, Any],
    *,
    crm_user_id: str = "",
    conversation_id: str = "",
    error: str = "",
) -> bool:
    """
    Persist a write for later replay. Returns True if it was stored.

    A False return is not fatal — it means the write is simply lost, which the
    caller logs. The outbox must never raise into a call path.
    """
    if op not in SUPPORTED_OPS:
        logger.error(f"crm.outbox: refusing to queue unsupported op {op!r}")
        return False

    with _get_db() as conn:
        if conn is None:
            logger.error(f"crm.outbox: no database — dropping {op} write")
            return False
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO crm_sync_outbox "
                    "(crm_user_id, conversation_id, op, payload, last_error) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        crm_user_id or None,
                        conversation_id or None,
                        op,
                        json.dumps(payload),
                        (error or "")[:500] or None,
                    ),
                )
            logger.info(
                f"crm.outbox: queued {op} for replay "
                f"(user={crm_user_id or '—'}, conv={conversation_id or '—'})"
            )
            return True
        except Exception:
            logger.exception("crm.outbox: failed to queue write")
            return False


async def depth() -> int:
    """How many writes are waiting. Surfaced as an operational metric.

    The `_sync` core runs in a worker thread: the outbox worker calls this on
    every tick (60 s), traffic or not, and a blocking connect to a dead
    Postgres freezes the event loop for every live call (see
    `get_next_queued_call` in app/leads/models.py for the py-spy evidence).
    """
    return await asyncio.to_thread(_depth_sync)


def _depth_sync() -> int:
    with _get_db() as conn:
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM crm_sync_outbox")
                row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.exception("crm.outbox: depth query failed")
            return 0


async def due(limit: int) -> list[dict[str, Any]]:
    """Pending writes whose next attempt is due, oldest first."""
    with _get_db() as conn:
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, crm_user_id, conversation_id, op, payload, attempts "
                    "  FROM crm_sync_outbox "
                    " WHERE next_attempt_at <= NOW() "
                    " ORDER BY id "
                    " LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall() or []
        except Exception:
            logger.exception("crm.outbox: due query failed")
            return []

    return [
        {
            "id": row[0],
            "crm_user_id": row[1] or "",
            "conversation_id": row[2] or "",
            "op": row[3],
            "payload": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
            "attempts": row[5] or 0,
        }
        for row in rows
    ]


async def _remove(entry_id: int) -> None:
    with _get_db() as conn:
        if conn is None:
            return
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DELETE FROM crm_sync_outbox WHERE id = %s", (entry_id,))
        except Exception:
            logger.exception(f"crm.outbox: failed to remove entry {entry_id}")


async def _defer(entry_id: int, attempts: int, error: str, *, give_up: bool = False) -> None:
    if give_up:
        logger.error(
            f"crm.outbox: giving up on entry {entry_id} after {attempts} attempts — {error}"
        )
        await _remove(entry_id)
        return

    with _get_db() as conn:
        if conn is None:
            return
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE crm_sync_outbox "
                    "   SET attempts = %s, next_attempt_at = %s, last_error = %s "
                    " WHERE id = %s",
                    (attempts, _next_attempt_after(attempts), error[:500], entry_id),
                )
        except Exception:
            logger.exception(f"crm.outbox: failed to defer entry {entry_id}")


async def drain(
    limit: int = 50,
    *,
    max_attempts: int = 10,
    replay: Callable[[dict], Awaitable[bool]] | None = None,
) -> dict[str, int]:
    """
    Replay due writes. Returns counters for logging and tests.

    ``replay`` receives the whole outbox row — the target user lives in its own
    column, not inside the body. It is injected rather than imported so this
    module keeps no dependency on ``sync`` (and therefore no import cycle). It
    must return True on success and raise ``CrmPermanentError`` for a write that
    should be dropped rather than retried.
    """
    from app.crm.client import CrmPermanentError

    if replay is None:
        from app.crm import sync

        replay = sync.replay

    stats = {"attempted": 0, "succeeded": 0, "failed": 0, "dropped": 0}
    entries = await due(limit)
    if not entries:
        return stats

    logger.info(f"crm.outbox: replaying {len(entries)} queued write(s)")

    for entry in entries:
        stats["attempted"] += 1
        attempts = entry["attempts"] + 1
        try:
            await replay(entry)
        except CrmPermanentError as exc:
            # The payload will never be accepted. Drop it rather than retry
            # forever — this is the R16 case.
            stats["dropped"] += 1
            await _defer(entry["id"], attempts, str(exc), give_up=True)
        except Exception as exc:
            stats["failed"] += 1
            await _defer(
                entry["id"], attempts, str(exc), give_up=attempts >= max_attempts
            )
        else:
            stats["succeeded"] += 1
            await _remove(entry["id"])

    logger.info(
        f"crm.outbox: replay done — {stats['succeeded']} sent, "
        f"{stats['failed']} retrying, {stats['dropped']} dropped"
    )
    return stats
