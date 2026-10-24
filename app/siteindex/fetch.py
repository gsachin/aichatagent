"""Polite source loading for the DSPMU site-index build.

Two modes:

* **Offline** (the default) reads files from a local cache, so the checked-in
  index is reproducible without touching the live site.
* **Network** (``allow_network=True``) fetches only sources that are missing
  from the cache. It checks robots.txt, sends at most one request per second,
  and identifies the crawler with a contact address.

The fetcher does not trust HTTP 200. The university site serves its 404 page
with a 200 status, and the archive links on the new host resolve to that page.
Callers must use :meth:`FetchResult.soft_404_reason` for HTML and
:attr:`FetchResult.is_pdf` for PDFs.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger("siteindex.fetch")

DEFAULT_USER_AGENT = (
    "DSPMU-SiteIndex/0.1 (+https://dspmuranchi.ac.in; "
    "admissions research; contact: registrar@dspmuranchi.ac.in)"
)


class FetchError(RuntimeError):
    """A source could not be read, or failed the content checks."""


@dataclass(frozen=True)
class SourceSpec:
    """One named source and the cache path it occupies."""

    key: str
    url: str
    relpath: str
    kind: str  # "html" | "xml" | "pdf"


@dataclass(frozen=True)
class FetchResult:
    spec: SourceSpec
    url: str
    body: bytes
    content_type: str
    status: int
    from_cache: bool

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_pdf(self) -> bool:
        return self.body.startswith(b"%PDF-")

    def soft_404_reason(self) -> str:
        """Return a reason when this response is the site's HTML 404 page.

        The current host returns ``200 text/html`` for missing paths and for
        every dead ``/pdf/...`` archive link. Checking the title alone is not
        enough because the page keeps the site's generic ``<title>``; the
        marker lives in the body.
        """
        if self.is_pdf:
            return ""
        if "html" not in (self.content_type or "").lower():
            return ""
        head = self.text[:12000].lower()
        if "404 - page not found" in head:
            return "soft_404: page not found"
        if "page not found" in head and "go back home" in head:
            return "soft_404: page not found"
        return ""


def _content_type_for(spec: SourceSpec, body: bytes) -> str:
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if spec.kind == "pdf":
        return "application/pdf"
    if spec.kind == "xml" or spec.relpath.endswith(".xml"):
        return "application/xml"
    return "text/html"


class SourceBook:
    """Resolve :class:`SourceSpec` objects from cache or the live site."""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        allow_network: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.allow_network = allow_network
        self.user_agent = user_agent
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/pdf;q=0.8,*/*;q=0.7"
                ),
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.7",
            }
        )
        self._last_request = 0.0
        self._robots: dict[str, RobotFileParser | None] = {}

    def load(self, spec: SourceSpec) -> FetchResult:
        path = self.cache_dir / spec.relpath
        if path.exists():
            body = path.read_bytes()
            return FetchResult(
                spec=spec,
                url=spec.url,
                body=body,
                content_type=_content_type_for(spec, body),
                status=200,
                from_cache=True,
            )
        if not self.allow_network:
            raise FileNotFoundError(
                f"missing cached source {path}; run with --allow-network, or "
                "populate the cache from an authorised copy"
            )
        return self._fetch(spec, path)

    def _fetch(self, spec: SourceSpec, path: Path) -> FetchResult:
        self._assert_robots(spec.url)
        self._respect_delay()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(
                    spec.url,
                    timeout=(10.0, self.timeout),
                    allow_redirects=True,
                )
            except requests.RequestException as exc:  # pragma: no cover - network
                last_error = exc
                time.sleep(2**attempt)
                continue

            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else float(2**attempt)
                except ValueError:
                    wait = float(2**attempt)
                time.sleep(max(0.5, wait))
                continue

            if response.status_code >= 400:
                raise FetchError(
                    f"HTTP {response.status_code} for {spec.url}"
                )

            result = FetchResult(
                spec=spec,
                url=response.url,
                body=response.content,
                content_type=response.headers.get("content-type", ""),
                status=response.status_code,
                from_cache=False,
            )
            reason = result.soft_404_reason()
            if reason:
                raise FetchError(f"{reason}: {spec.url}")
            if spec.kind == "pdf" and not result.is_pdf:
                raise FetchError(
                    f"expected a PDF, got {result.content_type or 'unknown'} "
                    f"for {spec.url}"
                )

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(result.body)
            meta = {
                "url": result.url,
                "status": result.status,
                "content_type": result.content_type,
                "bytes": len(result.body),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "user_agent": self.user_agent,
            }
            path.with_suffix(path.suffix + ".meta.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
            logger.info("fetched %s -> %s (%d bytes)", spec.url, path, len(result.body))
            return result

        raise FetchError(f"could not fetch {spec.url}: {last_error}")

    def _respect_delay(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _assert_robots(self, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            parser.set_url(origin + "/robots.txt")
            try:
                response = self.session.get(
                    origin + "/robots.txt",
                    timeout=(10.0, 20.0),
                    allow_redirects=True,
                )
                if response.status_code == 200 and "html" not in (
                    response.headers.get("content-type", "").lower()
                ):
                    parser.parse(response.text.splitlines())
                    self._robots[origin] = parser
                else:
                    self._robots[origin] = None
            except requests.RequestException:
                # A missing robots.txt is not a prohibition; log and continue.
                self._robots[origin] = None
            if self._robots[origin] is None:
                logger.warning(
                    "robots.txt unavailable for %s; proceeding with the custom "
                    "user agent %s",
                    origin,
                    self.user_agent,
                )
        parser = self._robots[origin]
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            raise FetchError(
                f"robots.txt disallows {self.user_agent!r} at {url}; stopping"
            )
