"""
HTTP client for the Salesforce Admission API.

The API is a frozen external contract (plan decision D1), so every failure mode
it exposes is handled here rather than upstream.

The hard part is not retrying — it is knowing when *not* to. The API reports
almost everything as a bare ``500``, which collapses three very different
situations onto one status code:

* **Transient.** Salesforce rate-limited us, or the network blipped. Retry.
* **Permanent.** A restricted-picklist violation (R16), or an upstream
  validation error. Retrying is pointless and, in a retry loop, infinite.
* **Unknown state.** A ``POST`` that returned 500 may have created the record
  before failing to serialise its response (R4 — the response model requires
  ``email: str`` but the record may hold null). A blind retry then creates a
  duplicate.

:func:`classify_response` separates them from the response body, and the three
exception types drive three different caller behaviours. See ``sync.py``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("crm.client")

# Response bodies are read into error messages; keep them bounded.
_BODY_SNIPPET = 300

# Markers in the API's `detail` string that mean "never retry this".
_PERMANENT_MARKERS = (
    "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST",
    "restricted picklist",
    "INVALID_FIELD",
    "REQUIRED_FIELD_MISSING",
    "MALFORMED_QUERY",
    # A record that is gone. The API answers 500 for this, not 404 (verified live
    # 2026-09-12: `Resource Customer Not Found. Response content: [{'errorCode':
    # 'NOT_FOUND', ...}]`), so without this marker a write to a deleted record
    # would retry three times, queue, and keep failing on every replay.
    "NOT_FOUND",
    # The same condition in the shape the org reports now (observed live
    # 2026-09-22): the code is ENTITY_IS_DELETED inside the same "Resource
    # Customer Not Found" wrapper, which carries no `NOT_FOUND` substring — the
    # words are split, and the code is different — so the marker above missed it.
    # That is not a slower retry, it is a self-feeding loop: `status.push_profile`
    # queues whatever the open breaker refuses, and replaying that queue re-enters
    # the refusal, so one deleted record grew a 248-row outbox and held the
    # breaker — shared by every caller — open for hours.
    "ENTITY_IS_DELETED",
)


class CrmError(Exception):
    """Base for every CRM failure. Callers outside this module catch this."""


class CrmTransientError(CrmError):
    """Worth retrying: connect failure, timeout, 429, or an unclassified 5xx."""


class CrmPermanentError(CrmError):
    """Retrying will never help: 4xx, or a validation error reported as 500."""


class CrmUnknownStateError(CrmError):
    """
    A write that may or may not have been applied.

    Raised for a 500 on a create-shaped request. The caller must re-read before
    retrying — see ``sync.lookup_or_create``.
    """


class CrmCircuitOpen(CrmError):
    """The breaker is open. Not an error so much as a deliberate refusal."""


# ── Retry / breaker tuning ───────────────────────────────────────────────────

_BACKOFF_BASE_S = 0.25
_BACKOFF_CAP_S = 4.0


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter — keeps a burst from synchronising."""
    ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** attempt))
    return random.uniform(0.0, ceiling)


# ── Circuit breaker ──────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Stop calling a service that is clearly down.

    Opens after ``threshold`` consecutive failures, refuses calls while open,
    and after ``reset_seconds`` allows a single probe (half-open). One success
    closes it; one failure re-opens it.

    Without this, a CRM outage turns every call, chat and WhatsApp message into
    a retry storm against a dead host — which is exactly the load the app must
    not add while it is also trying to serve students.
    """

    def __init__(self, threshold: int, reset_seconds: float) -> None:
        self.threshold = max(1, threshold)
        self.reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if (time.monotonic() - self._opened_at) >= self.reset_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """May a request go out right now?"""
        with self._lock:
            state = self._state_locked()
            if state == "open":
                return False
            if state == "half_open":
                # Let exactly one probe through; if it fails we re-open, and if
                # it succeeds record_success closes the breaker.
                self._opened_at = time.monotonic()
                return True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                if self._opened_at is None:
                    logger.warning(
                        f"crm.client: circuit breaker opened after {self._failures} "
                        f"consecutive failures — pausing for {self.reset_seconds}s"
                    )
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None


# ── Error classification ─────────────────────────────────────────────────────

def _body_text(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:  # pragma: no cover - body already consumed
        return ""
    return text[:_BODY_SNIPPET]


def _is_permanent_body(body: str) -> bool:
    return any(marker.lower() in body.lower() for marker in _PERMANENT_MARKERS)


def classify_response(response: httpx.Response, *, is_create: bool) -> CrmError | None:
    """
    Map a response onto the exception the caller should act on, or None if it
    succeeded.

    ``is_create`` is what makes a 500 ambiguous: on a POST the write may have
    landed, on a PATCH it either applied or it did not.
    """
    if response.status_code < 400:
        return None

    body = _body_text(response)
    code = response.status_code

    if code == 404:
        return CrmPermanentError(f"404 not found: {body}")

    if code == 429:
        # Must be checked before the general 4xx branch below — 429 is a 4xx but
        # is the one client-side status that is explicitly worth retrying.
        return CrmTransientError(f"429 rate limited: {body}")

    if 400 <= code < 500:
        # 422 from Pydantic, or a 400 — our payload is wrong. Never retry.
        return CrmPermanentError(f"{code}: {body}")

    if code >= 500:
        if _is_permanent_body(body):
            # The R16 case: a restricted-picklist violation arrives as a 500
            # but will never succeed no matter how often it is retried.
            return CrmPermanentError(f"{code} validation error: {body}")
        if is_create:
            # R4: the record may already exist. Retrying risks a duplicate.
            return CrmUnknownStateError(f"{code} on create — outcome unknown: {body}")
        return CrmTransientError(f"{code}: {body}")

    return CrmTransientError(f"unexpected status {code}: {body}")


# ── Client ───────────────────────────────────────────────────────────────────

class CrmClient:
    """
    Async client with timeouts, bounded retry, a circuit breaker and
    single-flight de-duplication.

    Instances are cheap; ``get_client()`` hands out a shared one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        max_retries: int = 3,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self.breaker = breaker or CircuitBreaker(
            settings.CRM_BREAKER_THRESHOLD, settings.CRM_BREAKER_RESET_S
        )
        self._inflight: dict[str, asyncio.Task] = {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
            ),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    # -- lifecycle ----------------------------------------------------------

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # D5: the API has no auth today. If a shared secret is ever put in front
        # of it, this is where it goes.
        return {"X-API-Key": self.api_key} if self.api_key else {}

    # -- single-flight ------------------------------------------------------

    async def single_flight(self, key: str, factory):
        """
        Run ``factory()`` once for a given key, however many callers ask.

        This is the only defence available against R5: the API's find-then-create
        has no lock, so two inbound calls from the same number can both miss and
        both create. Collapsing them into one request removes the race for the
        common case (same process, overlapping calls).
        """
        existing = self._inflight.get(key)
        if existing is not None:
            logger.debug(f"crm.client: single-flight joined {key}")
            return await existing

        task = asyncio.ensure_future(factory())
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

    # -- transport ----------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        is_create: bool = False,
        retries: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """
        Send one request, retrying only what is safe to retry.

        Three body shapes, mutually exclusive: ``json=`` for the JSON API,
        ``data=`` for multipart form fields, ``data=`` + ``files=`` for a
        multipart upload. The retry loop, backoff, breaker and classification
        are shared by all three.

        ``retries`` and ``timeout`` override the client defaults for this call
        only. Both exist for the document upload: the resolver needs ``retries=0``
        (a bad id answers 500, and four attempts would trip the breaker shared by
        every other caller), and ``/complete`` needs a longer read timeout than a
        phone call can afford.

        Raises the matching :class:`CrmError` subclass on failure; never returns
        a partially-handled state.
        """
        if json is not None and (data is not None or files is not None):
            # httpx silently prefers files/data over json, so a caller passing
            # both would send a body with no JSON in it and no error to show for
            # it. Refuse instead.
            raise ValueError("json= cannot be combined with data=/files=")

        body: dict[str, Any] = {}
        if files is not None:
            # Values must be ``(filename, bytes)``, never an open handle: the
            # body is rebuilt from scratch on every retry (httpx constructs a
            # fresh MultipartStream per attempt), and a consumed or non-seekable
            # handle would upload empty bytes on the second try.
            body["files"] = files
            body["data"] = data or {}
        elif data is not None:
            body["data"] = data
        elif json is not None:
            body["json"] = json

        if timeout is not None:
            # Omitted entirely when unset: `timeout=None` in httpx means *no*
            # timeout, not "use the client default" — passing it would silently
            # disable the ceiling for every other call site.
            body["timeout"] = httpx.Timeout(
                timeout, connect=settings.CRM_TIMEOUT_CONNECT_S
            )

        limit = self.max_retries if retries is None else max(0, retries)
        last_error: CrmError | None = None

        for attempt in range(limit + 1):
            if not self.breaker.allow():
                raise CrmCircuitOpen(
                    f"circuit breaker open — refusing {method} {path}"
                )

            try:
                response = await self._client.request(
                    method, path, headers=self._headers(), **body
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as exc:
                last_error = CrmTransientError(f"{type(exc).__name__}: {exc}")
                self.breaker.record_failure()
            except httpx.HTTPError as exc:
                last_error = CrmTransientError(f"{type(exc).__name__}: {exc}")
                self.breaker.record_failure()
            else:
                error = classify_response(response, is_create=is_create)
                if error is None:
                    self.breaker.record_success()
                    try:
                        return response.json()
                    except ValueError:
                        return None

                if isinstance(error, CrmPermanentError):
                    # The service answered correctly and rejected our payload.
                    # That says nothing about service health, so the breaker is
                    # deliberately left alone — a run of bad sentiment values
                    # must not open the circuit for everyone else.
                    logger.error(f"crm.client: permanent failure on {method} {path}: {error}")
                    raise error

                self.breaker.record_failure()
                last_error = error
                if isinstance(error, CrmUnknownStateError):
                    # Retrying could duplicate the record. Hand it to the caller.
                    raise error

            if attempt < limit:
                delay = _backoff_delay(attempt)
                logger.warning(
                    f"crm.client: {method} {path} attempt {attempt + 1}/"
                    f"{limit + 1} failed ({last_error}); retrying in {delay:.2f}s"
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    async def post(self, path: str, json: dict, *, is_create: bool = True) -> Any:
        return await self.request("POST", path, json=json, is_create=is_create)

    async def post_multipart(
        self,
        path: str,
        *,
        data: dict,
        files: dict,
        is_create: bool = False,
        retries: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        """
        POST ``multipart/form-data`` — form fields in ``data``, upload parts in
        ``files`` as ``{field: (filename, bytes)}``.

        ``is_create`` is the caller's judgement about whether re-sending could
        duplicate a record, and it is the only thing that decides whether a 500
        is retried (see :func:`classify_response`). For the document upload the
        three calls differ: re-sending a chunk overwrites it, so retry is safe;
        ``/complete`` creates a ContentVersion, so it is not.
        """
        return await self.request(
            "POST", path, data=data, files=files,
            is_create=is_create, retries=retries, timeout=timeout,
        )

    async def patch(self, path: str, json: dict) -> Any:
        return await self.request("PATCH", path, json=json, is_create=False)

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)


# ── Shared instance ──────────────────────────────────────────────────────────

_client: CrmClient | None = None
_client_loop: object | None = None
_client_lock = threading.Lock()


def _new_client() -> CrmClient:
    return CrmClient(
        settings.CRM_BASE_URL,
        api_key=settings.CRM_API_KEY,
        connect_timeout=settings.CRM_TIMEOUT_CONNECT_S,
        read_timeout=settings.CRM_TIMEOUT_READ_S,
        max_retries=settings.CRM_MAX_RETRIES,
    )


def get_client() -> CrmClient:
    """
    Return the shared client **for the current event loop**.

    An ``httpx.AsyncClient`` is bound to the loop that created its connection
    pool. Reused from a different loop it fails on the first pooled connection
    with ``RuntimeError: Event loop is closed`` — which surfaces as an opaque
    500-ish failure and, worse, records failures against the circuit breaker
    until everything is refused for no visible reason.

    This app genuinely has more than one loop: each uvicorn worker runs one, and
    ``app/leads/mcp_tools.py`` creates further ones via ``asyncio.run()``. So the
    client is keyed to the running loop and quietly rebuilt when that changes.

    The abandoned client is dropped rather than closed — its loop is gone, so
    ``aclose()`` could not complete there anyway. Socket cleanup falls to the
    garbage collector; the alternative is a leak of one pool per loop switch,
    which is the lesser problem.
    """
    global _client, _client_loop

    try:
        loop: object | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    with _client_lock:
        if _client is not None and _client_loop is loop:
            return _client
        if _client is not None:
            logger.debug("crm.client: event loop changed — rebuilding the shared client")
        _client = _new_client()
        _client_loop = loop
        return _client


async def aclose_client() -> None:
    """Close the shared client. Called on shutdown, and between tests."""
    global _client, _client_loop
    with _client_lock:
        client, _client = _client, None
        _client_loop = None
    if client is not None:
        await client.aclose()
