"""Voice→WhatsApp reference delivery (Phase 12).

The voice turn enqueues a request; a bounded daemon worker checks WhatsApp
registration, deduplicates and throttles, then sends a deterministic message
with the call context and evidence references. The call path never waits for
registration, the template decision, the send or a retry.

Registration and consent:
- a lead exists for the number (last-ten-digit tolerant match),
- no ``whatsapp_opt_out_at``,
- explicit ``whatsapp_opt_in``, or a recent inbound WhatsApp conversation
  within ``NGA_WHATSAPP_REGISTRATION_WINDOW_DAYS``.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.config import settings
from app.references.formatting import (
    Reference,
    answer_summary,
    build_voice_reference_message,
    normalize_phone,
    phone_hash,
    phone_last4,
    references_from_citations,
    whatsapp_address,
)
from app.sitegraph.store import GraphStore

logger = logging.getLogger("references.delivery")


@dataclass(frozen=True)
class DeliveryConfig:
    enabled: bool = False
    mode: str = "turn"
    max_per_call: int = 3
    min_interval_s: int = 20
    snippet_chars: int = 240
    registration_window_days: int = 30
    require_opt_in: bool = False
    template_sid: str = ""
    retry_max: int = 3
    retry_backoff_s: float = 2.0
    log_retention_days: int = 30

    @classmethod
    def from_settings(cls) -> DeliveryConfig:
        mode = (settings.NGA_REFERENCE_DELIVERY_MODE or "turn").lower()
        if mode not in ("turn", "call_end"):
            mode = "turn"
        return cls(
            enabled=settings.NGA_REFERENCE_DELIVERY_ENABLED,
            mode=mode,
            max_per_call=max(1, settings.NGA_REFERENCE_MAX_PER_CALL),
            min_interval_s=max(0, settings.NGA_REFERENCE_MIN_INTERVAL_S),
            snippet_chars=max(40, settings.NGA_REFERENCE_SNIPPET_CHARS),
            registration_window_days=max(
                1, settings.NGA_WHATSAPP_REGISTRATION_WINDOW_DAYS
            ),
            require_opt_in=settings.NGA_WHATSAPP_REFERENCE_REQUIRE_OPT_IN,
            template_sid=settings.NGA_WHATSAPP_TEMPLATE_SID,
            retry_max=max(0, settings.NGA_REFERENCE_RETRY_MAX),
            retry_backoff_s=max(0.0, settings.NGA_REFERENCE_RETRY_BACKOFF_S),
            log_retention_days=max(0, settings.NGA_REFERENCE_LOG_RETENTION_DAYS),
        )


@dataclass(frozen=True)
class RegistrationResult:
    registered: bool
    reason: str
    last_inbound_at: str = ""
    within_24h: bool = False


@dataclass(frozen=True)
class ReferenceDeliveryRequest:
    call_sid: str
    conversation_id: str
    phone_number: str
    channel: str
    question: str
    answer_summary: str
    citations: tuple = ()
    mode: str = "turn"


@dataclass
class DeliveryOutcome:
    node_id: str = ""
    status: str = ""
    reason: str = ""
    twilio_sid: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "reason": self.reason,
            "twilio_sid": self.twilio_sid,
        }


def _as_iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_lead_lookup(phone: str) -> dict | None:
    from app.leads.models import get_lead_whatsapp_state

    return asyncio.run(get_lead_whatsapp_state(phone))


def _default_conversation_lookup(lead_id: str, days: int) -> bool:
    from app.leads.models import has_recent_whatsapp_conversation

    return asyncio.run(has_recent_whatsapp_conversation(lead_id, days=days))


def is_registered(
    phone_number: str,
    *,
    config: DeliveryConfig | None = None,
    lead_lookup: Callable[[str], dict | None] | None = None,
    conversation_lookup: Callable[[str, int], bool] | None = None,
    now: datetime | None = None,
) -> RegistrationResult:
    """Apply the registration and consent rules; fail closed."""
    limits = config or DeliveryConfig.from_settings()
    phone = normalize_phone(phone_number)
    if not phone:
        return RegistrationResult(False, "invalid_phone")
    try:
        lead = (lead_lookup or _default_lead_lookup)(phone)
    except Exception:  # noqa: BLE001 - an unavailable DB is not consent
        logger.exception("WhatsApp registration lookup failed")
        return RegistrationResult(False, "registration_error")
    if not lead:
        return RegistrationResult(False, "no_lead")
    if lead.get("whatsapp_opt_out_at"):
        return RegistrationResult(False, "opted_out")
    if lead.get("whatsapp_opt_in"):
        last = _as_iso(lead.get("last_inbound_at"))
        return RegistrationResult(
            True,
            "explicit_opt_in",
            last_inbound_at=last,
            within_24h=_within_24h(last, now),
        )
    if limits.require_opt_in:
        return RegistrationResult(False, "opt_in_required")
    last = _as_iso(lead.get("last_inbound_at"))
    if not last and conversation_lookup is not None:
        try:
            if conversation_lookup(str(lead.get("id") or ""), limits.registration_window_days):
                return RegistrationResult(
                    True, "recent_conversation", within_24h=False
                )
        except Exception:  # noqa: BLE001
            logger.exception("WhatsApp conversation lookup failed")
            return RegistrationResult(False, "registration_error")
    if not last:
        return RegistrationResult(False, "no_whatsapp_history")
    parsed = _parse_datetime(last)
    if parsed is None:
        return RegistrationResult(False, "no_whatsapp_history")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=limits.registration_window_days
    )
    if parsed < cutoff:
        return RegistrationResult(False, "no_whatsapp_history")
    return RegistrationResult(
        True,
        "recent_conversation",
        last_inbound_at=last,
        within_24h=_within_24h(last, now),
    )


def _within_24h(last_inbound_at: str, now: datetime | None) -> bool:
    parsed = _parse_datetime(last_inbound_at)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - parsed <= timedelta(hours=24)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _citation_key(citation) -> str:
    if isinstance(citation, dict):
        return str(citation.get("node_id") or citation.get("url") or "")
    return str(
        getattr(citation, "node_id", "")
        or getattr(citation, "url", "")
        or ""
    )


def _merge_citations(*groups):
    merged = []
    seen: set[str] = set()
    for group in groups:
        for citation in group or ():
            key = _citation_key(citation)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(citation)
    return tuple(merged)


class ReferenceDeliveryService:
    """Bounded asynchronous delivery with deterministic formatting."""

    def __init__(
        self,
        store: GraphStore,
        config: DeliveryConfig | None = None,
        *,
        sender: Callable[[str, str], tuple[bool, str]] | None = None,
        template_sender: Callable[[str, str, dict], tuple[bool, str]] | None = None,
        lead_lookup: Callable[[str], dict | None] | None = None,
        conversation_lookup: Callable[[str, int], bool] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        now_iso: Callable[[], str] = _now_iso,
        start_worker: bool = True,
    ) -> None:
        self.store = store
        self.config = config or DeliveryConfig.from_settings()
        self._sender = sender
        self._template_sender = template_sender
        self._lead_lookup = lead_lookup
        self._conversation_lookup = conversation_lookup
        self._clock = clock
        self._sleep = sleep
        self._now_iso = now_iso
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._pending_calls: dict[str, ReferenceDeliveryRequest] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._worker_enabled = bool(start_worker)
        if start_worker and self.config.enabled:
            self._start_worker()
            self._purge_old()

    def _start_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="nga-reference-delivery",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                self.process_request(request)
            except Exception:  # noqa: BLE001 - the worker must not die
                logger.exception("reference delivery job failed")
            finally:
                self._queue.task_done()

    def _enqueue(self, request: ReferenceDeliveryRequest) -> None:
        if not self.config.enabled:
            return
        if self._worker_enabled:
            self._start_worker()
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            logger.warning("reference delivery queue full; dropping request")

    def submit_turn(self, request: ReferenceDeliveryRequest) -> None:
        """Queue one cited turn. Never blocks and never raises."""
        if not self.config.enabled or not request.citations:
            return
        if not (request.call_sid and request.phone_number):
            return
        mode = (request.mode or self.config.mode).lower()
        if mode == "call_end":
            with self._lock:
                existing = self._pending_calls.get(request.call_sid)
                if existing is None:
                    self._pending_calls[request.call_sid] = request
                else:
                    merged = _merge_citations(
                        existing.citations, request.citations
                    )
                    self._pending_calls[request.call_sid] = (
                        ReferenceDeliveryRequest(
                            call_sid=existing.call_sid,
                            conversation_id=(
                                existing.conversation_id
                                or request.conversation_id
                            ),
                            phone_number=existing.phone_number,
                            channel=existing.channel,
                            question=existing.question,
                            answer_summary=existing.answer_summary,
                            citations=merged,
                            mode="call_end",
                        )
                    )
            return
        self._enqueue(
            ReferenceDeliveryRequest(
                call_sid=request.call_sid,
                conversation_id=request.conversation_id,
                phone_number=request.phone_number,
                channel=request.channel,
                question=request.question,
                answer_summary=request.answer_summary,
                citations=request.citations,
                mode="turn",
            )
        )

    def flush_call(self, call_sid: str) -> None:
        if not self.config.enabled or not call_sid:
            return
        with self._lock:
            request = self._pending_calls.pop(call_sid, None)
        if request is not None:
            self._enqueue(request)

    def process_request(
        self, request: ReferenceDeliveryRequest
    ) -> list[DeliveryOutcome]:
        """Synchronous delivery path; used by the worker and by tests."""
        if not self.config.enabled:
            return []
        phone = normalize_phone(request.phone_number)
        if not phone:
            self._record_skip(request, "invalid_phone")
            return [DeliveryOutcome(status="skipped", reason="invalid_phone")]
        registration = is_registered(
            phone,
            config=self.config,
            lead_lookup=self._lead_lookup,
            conversation_lookup=self._conversation_lookup,
        )
        if not registration.registered:
            self._record_skip(request, registration.reason)
            return [
                DeliveryOutcome(status="skipped", reason=registration.reason)
            ]

        references = references_from_citations(
            request.citations,
            limit=self.config.max_per_call,
            snippet_chars=self.config.snippet_chars,
        )
        selected: list[Reference] = []
        sent_count = self.store.sent_reference_count(request.call_sid)
        for reference in references:
            if self.store.has_sent_reference(
                request.call_sid, reference.node_id
            ):
                # Already sent: never overwrite the sent row with a skip.
                continue
            if sent_count >= self.config.max_per_call:
                self._record(request, reference, "skipped", reason="call_cap")
                continue
            selected.append(reference)
        if not selected:
            return [
                DeliveryOutcome(status="skipped", reason="no_new_references")
            ]

        if (request.mode or self.config.mode) == "turn":
            self._throttle(request.call_sid)

        message = build_voice_reference_message(
            request.question,
            answer_summary(request.answer_summary, 300),
            selected,
        )
        if not message:
            self._record_skip(request, "empty_message")
            return [DeliveryOutcome(status="skipped", reason="empty_message")]

        if not registration.within_24h:
            if not self.config.template_sid:
                for reference in selected:
                    self._record(
                        request,
                        reference,
                        "template_required",
                        reason="template_required",
                    )
                return [
                    DeliveryOutcome(
                        status="template_required",
                        reason="template_required",
                    )
                ]
            ok, result = self._send_with_retry(
                request,
                message,
                template=True,
            )
        else:
            ok, result = self._send_with_retry(request, message, template=False)

        status = "sent" if ok else "failed"
        outcomes: list[DeliveryOutcome] = []
        for reference in selected:
            self._record(
                request,
                reference,
                status,
                twilio_sid=result if ok else "",
                error="" if ok else result,
            )
            outcomes.append(
                DeliveryOutcome(
                    node_id=reference.node_id,
                    status=status,
                    twilio_sid=result if ok else "",
                )
            )
        return outcomes

    def _throttle(self, call_sid: str) -> None:
        if self.config.min_interval_s <= 0:
            return
        last = self.store.last_reference_sent_at(call_sid)
        parsed = _parse_datetime(last)
        if parsed is None:
            return
        elapsed = self._clock() - parsed.timestamp()
        wait = self.config.min_interval_s - elapsed
        if wait > 0:
            self._sleep(min(wait, float(self.config.min_interval_s)))

    def _send_with_retry(
        self,
        request: ReferenceDeliveryRequest,
        message: str,
        *,
        template: bool,
    ) -> tuple[bool, str]:
        attempts = self.config.retry_max + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                if template:
                    sender = self._template_sender or self._default_template_sender
                    ok, result = sender(
                        request.phone_number,
                        self.config.template_sid,
                        {"1": message},
                    )
                else:
                    sender = self._sender or self._default_sender
                    ok, result = sender(request.phone_number, message)
            except Exception as exc:  # noqa: BLE001 - bounded retry below
                ok, result = False, str(exc)
            if ok:
                return True, str(result)
            last_error = str(result)
            if attempt < attempts - 1 and self.config.retry_backoff_s > 0:
                self._sleep(self.config.retry_backoff_s * (2 ** attempt))
        return False, last_error

    @staticmethod
    def _default_sender(to_number: str, body: str) -> tuple[bool, str]:
        from app.messaging import send_whatsapp_message

        sender = (
            settings.TWILIO_WHATSAPP_NUMBER or settings.TWILIO_PHONE_NUMBER
        )
        return send_whatsapp_message(
            whatsapp_address(to_number),
            whatsapp_address(sender),
            body,
        )

    @staticmethod
    def _default_template_sender(
        to_number: str, content_sid: str, variables: dict
    ) -> tuple[bool, str]:
        from app.messaging import send_whatsapp_template

        sender = (
            settings.TWILIO_WHATSAPP_NUMBER or settings.TWILIO_PHONE_NUMBER
        )
        return send_whatsapp_template(
            whatsapp_address(to_number),
            whatsapp_address(sender),
            content_sid,
            variables,
        )

    def _record(
        self,
        request: ReferenceDeliveryRequest,
        reference: Reference,
        status: str,
        *,
        reason: str = "",
        twilio_sid: str = "",
        error: str = "",
    ) -> None:
        try:
            self.store.record_reference_delivery(
                call_sid=request.call_sid,
                conversation_id=request.conversation_id,
                phone_hash=phone_hash(request.phone_number),
                phone_last4=phone_last4(request.phone_number),
                node_id=reference.node_id,
                url=reference.url,
                mode=request.mode or self.config.mode,
                status=status,
                reason=reason,
                twilio_sid=twilio_sid,
                error=error,
                created_at=self._now_iso(),
            )
        except Exception:  # noqa: BLE001 - delivery never affects the call
            logger.exception("could not record reference delivery")

    def _record_skip(
        self, request: ReferenceDeliveryRequest, reason: str
    ) -> None:
        try:
            self.store.record_reference_delivery(
                call_sid=request.call_sid,
                conversation_id=request.conversation_id,
                phone_hash=phone_hash(request.phone_number),
                phone_last4=phone_last4(request.phone_number),
                node_id="",
                url="",
                mode=request.mode or self.config.mode,
                status="skipped",
                reason=reason,
                created_at=self._now_iso(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record skipped reference delivery")

    def _purge_old(self) -> None:
        days = self.config.log_retention_days
        if days <= 0:
            return
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            removed = self.store.purge_reference_deliveries(cutoff)
            if removed:
                logger.info("purged %d old reference delivery rows", removed)
        except Exception:  # noqa: BLE001
            logger.exception("reference delivery purge failed")

    def shutdown(self) -> None:
        if self._worker is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._worker.join(timeout=5.0)
        self._worker = None


_SERVICE: ReferenceDeliveryService | None = None
_SERVICE_LOCK = threading.Lock()


def get_delivery_service() -> ReferenceDeliveryService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = ReferenceDeliveryService(
                GraphStore(settings.NGA_GRAPH_SQLITE_PATH),
                DeliveryConfig.from_settings(),
            )
        return _SERVICE


def reset_delivery_service() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            try:
                _SERVICE.shutdown()
            except Exception:  # noqa: BLE001
                pass
        _SERVICE = None


def flush_voice_references(call_sid: str) -> None:
    if not settings.NGA_REFERENCE_DELIVERY_ENABLED:
        return
    try:
        get_delivery_service().flush_call(call_sid)
    except Exception:  # noqa: BLE001 - call teardown must not fail
        logger.exception("reference delivery flush failed")
