"""Polite, bounded page fetch for the NGA refresh worker.

Only allowlisted HTTPS hosts are fetched. Redirects are followed manually so
every hop is revalidated. Conditional headers are supported (``If-None-Match``
and ``If-Modified-Since``), response size and content type are capped, and the
university's soft-404 page (HTTP 200 with a "page not found" body) is rejected.

This module is used by the background refresh worker, not by the live answer
path. The live path reads the already-indexed content store.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger("sitegraph.fetch")

_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_PDF_CONTENT_TYPES = ("application/pdf", "application/octet-stream")
_REDIRECT_CODES = {301, 302, 303, 307, 308}


class FetchError(RuntimeError):
    """A source could not be fetched or failed a content/policy check."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str
    etag: str
    last_modified: str
    not_modified: bool = False

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def soft_404_reason(text: str, content_type: str = "") -> str:
    """The DSPMU host answers missing paths with HTTP 200 and its 404 page."""
    lowered = (text or "")[:12000].lower()
    if "html" not in (content_type or "").lower() and content_type:
        return ""
    if "404 - page not found" in lowered:
        return "soft_404: page not found"
    if "page not found" in lowered and "go back home" in lowered:
        return "soft_404: page not found"
    return ""


class SiteFetcher:
    """Allowlisted HTTPS fetcher with conditional GET and soft-404 rejection."""

    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        *,
        user_agent: str,
        timeout_s: float = 6.0,
        delay_s: float = 0.25,
        max_bytes: int = 2_000_000,
        respect_robots: bool = True,
        session: requests.Session | None = None,
        clock=time.monotonic,
        max_redirects: int = 3,
    ) -> None:
        self.allowed_hosts = frozenset(
            host.strip().lower() for host in allowed_hosts if host.strip()
        )
        self.user_agent = user_agent
        self.timeout_s = max(1.0, float(timeout_s))
        self.delay_s = max(0.0, float(delay_s))
        self.max_bytes = max(1024, int(max_bytes))
        self.respect_robots = respect_robots
        self.max_redirects = max(0, int(max_redirects))
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.5"
            ),
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.7",
        })
        self._delay_lock = threading.Lock()
        self._robots_lock = threading.Lock()
        self._last_request = 0.0
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}

    def fetch(
        self,
        url: str,
        *,
        etag: str = "",
        last_modified: str = "",
    ) -> FetchResult:
        return self._fetch(
            url,
            etag=etag,
            last_modified=last_modified,
            allowed_content_types=_ALLOWED_CONTENT_TYPES,
            max_bytes=self.max_bytes,
            require_pdf=False,
        )

    def fetch_document(
        self,
        url: str,
        *,
        etag: str = "",
        last_modified: str = "",
        max_bytes: int | None = None,
    ) -> FetchResult:
        """Fetch one background-indexed PDF, with a magic-byte check."""
        return self._fetch(
            url,
            etag=etag,
            last_modified=last_modified,
            allowed_content_types=_PDF_CONTENT_TYPES,
            max_bytes=max_bytes or self.max_bytes,
            require_pdf=True,
        )

    def _fetch(
        self,
        url: str,
        *,
        etag: str,
        last_modified: str,
        allowed_content_types: tuple[str, ...],
        max_bytes: int,
        require_pdf: bool,
    ) -> FetchResult:
        current = self._validate(url)
        if not self._robots_allowed(current):
            raise FetchError(f"robots.txt disallows {current}")
        self._respect_delay()

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if require_pdf:
            headers["Accept"] = "application/pdf"

        response = None
        redirects = 0
        while True:
            response = self._session.get(
                current,
                timeout=(3.0, self.timeout_s),
                allow_redirects=False,
                headers=headers,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status in _REDIRECT_CODES:
                location = (getattr(response, "headers", {}) or {}).get("location", "")
                self._close(response)
                if not location:
                    raise FetchError(f"redirect without Location from {current}")
                if redirects >= self.max_redirects:
                    raise FetchError(f"too many redirects from {url}")
                current = self._validate(urljoin(current, location))
                redirects += 1
                continue
            break

        try:
            status = int(getattr(response, "status_code", 0) or 0)
            response_headers = getattr(response, "headers", {}) or {}
            content_type = response_headers.get("content-type", "")
            response_etag = response_headers.get("etag", "") or etag
            response_last_modified = (
                response_headers.get("last-modified", "") or last_modified
            )
            if status == 304:
                return FetchResult(
                    url=url,
                    final_url=getattr(response, "url", current) or current,
                    status=status,
                    body=b"",
                    content_type=content_type,
                    etag=response_etag,
                    last_modified=response_last_modified,
                    not_modified=True,
                )
            if status >= 400:
                raise FetchError(f"HTTP {status} for {current}", status=status)
            if status < 200 or status >= 300:
                raise FetchError(f"unexpected HTTP {status} for {current}", status=status)
            if not any(
                allowed in (content_type or "").lower()
                for allowed in allowed_content_types
            ):
                raise FetchError(
                    f"unsupported content type {content_type!r} for {current}"
                )

            body = self._read_body(response, max_bytes)
            if require_pdf and b"%PDF-" not in body[:1024]:
                raise FetchError(f"response is not a PDF: {current}")
            result = FetchResult(
                url=url,
                final_url=getattr(response, "url", current) or current,
                status=status,
                body=body,
                content_type=content_type,
                etag=response_etag,
                last_modified=response_last_modified,
            )
            reason = soft_404_reason(result.text, content_type)
            if reason:
                raise FetchError(f"{reason}: {current}")
            return result
        finally:
            self._close(response)

    # ── internals ───────────────────────────────────────────────

    def _validate(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            raise FetchError(f"only HTTPS is allowed: {url}")
        host = (parsed.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise FetchError(f"host not allowlisted: {host or url}")
        return url

    def _read_body(self, response, max_bytes: int) -> bytes:
        content_length = (getattr(response, "headers", {}) or {}).get(
            "content-length", ""
        )
        try:
            if content_length and int(content_length) > max_bytes:
                raise FetchError(
                    f"response too large ({content_length} bytes)"
                )
        except ValueError:
            pass

        iterator = getattr(response, "iter_content", None)
        if callable(iterator):
            chunks: list[bytes] = []
            total = 0
            for chunk in iterator(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(f"response exceeds {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)

        body = getattr(response, "content", b"") or b""
        if len(body) > max_bytes:
            raise FetchError(f"response exceeds {max_bytes} bytes")
        return body

    def _respect_delay(self) -> None:
        if self.delay_s == 0:
            return
        with self._delay_lock:
            wait = self.delay_s - (self._clock() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = self._clock()

    def _robots_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = self._clock()
        with self._robots_lock:
            cached = self._robots.get(origin)
        if cached is not None and now < cached[0]:
            parser = cached[1]
            return parser is None or parser.can_fetch(self.user_agent, url)

        parser: RobotFileParser | None = None
        try:
            response = self._session.get(
                origin + "/robots.txt",
                timeout=(3.0, self.timeout_s),
                allow_redirects=True,
                headers={"Accept": "text/plain,*/*;q=0.8"},
            )
            content_type = (
                getattr(response, "headers", {}) or {}
            ).get("content-type", "").lower()
            if int(getattr(response, "status_code", 0) or 0) == 200 and "html" not in content_type:
                parser = RobotFileParser()
                parser.set_url(origin + "/robots.txt")
                parser.parse(str(getattr(response, "text", "") or "").splitlines())
            else:
                parser = None
        except Exception as exc:  # noqa: BLE001 - unavailable robots != deny
            logger.warning("robots.txt unavailable for %s (%s)", origin, exc)
            parser = None
        with self._robots_lock:
            self._robots[origin] = (now + 3600.0, parser)
        return parser is None or parser.can_fetch(self.user_agent, url)

    @staticmethod
    def _close(response) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort
                pass
