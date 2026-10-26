"""Phase 3: polite bounded fetch for the NGA refresh worker."""

from __future__ import annotations

import pytest

from app.sitegraph.fetch import FetchError, SiteFetcher

BASE = "https://dspmuranchi.ac.in"
PAGE = BASE + "/cms/hostel"


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        text: str = "",
        content_type: str = "text/html; charset=utf-8",
        headers: dict | None = None,
        content: bytes | None = None,
    ) -> None:
        self.url = url
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = {"content-type": content_type}
        self.headers.update(headers or {})
        self.closed = False

    def iter_content(self, chunk_size: int = 8192):
        yield self.content

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(
        self,
        responses: dict[str, FakeResponse],
        *,
        robots: str = "User-agent: *\nAllow: /\n",
        robots_status: int = 200,
    ) -> None:
        self.responses = responses
        self.robots = robots
        self.robots_status = robots_status
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, timeout=None, allow_redirects=True, headers=None):
        self.calls.append((url, dict(headers or {})))
        if url.endswith("/robots.txt"):
            return FakeResponse(
                url,
                status=self.robots_status,
                text=self.robots,
                content_type="text/plain; charset=utf-8",
            )
        return self.responses[url]

    def page_calls(self) -> list[tuple[str, dict]]:
        return [
            (url, headers) for url, headers in self.calls
            if not url.endswith("/robots.txt")
        ]


def _fetcher(session: FakeSession, **overrides) -> SiteFetcher:
    values = dict(
        allowed_hosts=("dspmuranchi.ac.in",),
        user_agent="DSPMU-Test/1.0",
        timeout_s=1.0,
        delay_s=0.0,
        max_bytes=1000,
        respect_robots=False,
    )
    values.update(overrides)
    return SiteFetcher(session=session, **values)


def test_fetches_html_with_validators():
    session = FakeSession({
        PAGE: FakeResponse(
            PAGE,
            text="<html><main><h1>Hostel</h1></main></html>",
            headers={"etag": '"abc"', "last-modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
        )
    })
    result = _fetcher(session).fetch(PAGE)

    assert result.status == 200
    assert result.not_modified is False
    assert "Hostel" in result.text
    assert result.etag == '"abc"'
    assert result.last_modified.startswith("Wed, 21 Oct")


def test_conditional_get_returns_304_without_body():
    session = FakeSession({
        PAGE: FakeResponse(PAGE, status=304, text="")
    })
    result = _fetcher(session).fetch(
        PAGE, etag='"abc"', last_modified="Wed, 21 Oct 2026 07:28:00 GMT"
    )

    assert result.not_modified is True
    assert result.status == 304
    assert result.body == b""
    headers = session.page_calls()[0][1]
    assert headers["If-None-Match"] == '"abc"'
    assert headers["If-Modified-Since"].startswith("Wed, 21 Oct")


def test_soft_404_is_rejected():
    session = FakeSession({
        PAGE: FakeResponse(
            PAGE,
            text="<html><body><h1>404 - Page Not Found</h1></body></html>",
        )
    })

    with pytest.raises(FetchError, match="soft_404"):
        _fetcher(session).fetch(PAGE)


def test_non_allowlisted_host_is_rejected_without_a_call():
    session = FakeSession({})
    with pytest.raises(FetchError, match="allowlisted"):
        _fetcher(session).fetch("https://evil.example/page")
    assert session.calls == []


def test_http_is_rejected():
    session = FakeSession({})
    with pytest.raises(FetchError, match="HTTPS"):
        _fetcher(session).fetch("http://dspmuranchi.ac.in/cms/hostel")
    assert session.calls == []


def test_redirect_is_followed_and_revalidated():
    second = BASE + "/cms/hostel-v2"
    session = FakeSession({
        PAGE: FakeResponse(
            PAGE, status=302, text="", headers={"location": second}
        ),
        second: FakeResponse(second, text="<html><main>Moved</main></html>"),
    })
    result = _fetcher(session).fetch(PAGE)

    assert result.final_url == second
    assert "Moved" in result.text
    assert [url for url, _headers in session.page_calls()] == [PAGE, second]


def test_redirect_to_external_host_is_blocked():
    session = FakeSession({
        PAGE: FakeResponse(
            PAGE,
            status=302,
            text="",
            headers={"location": "https://evil.example/page"},
        )
    })

    with pytest.raises(FetchError, match="allowlisted"):
        _fetcher(session).fetch(PAGE)


def test_unsupported_content_type_is_rejected():
    session = FakeSession({
        PAGE: FakeResponse(
            PAGE, text="%PDF-1.4", content_type="application/pdf"
        )
    })

    with pytest.raises(FetchError, match="content type"):
        _fetcher(session).fetch(PAGE)


def test_oversized_response_is_rejected():
    session = FakeSession({
        PAGE: FakeResponse(PAGE, text="x" * 2000)
    })

    with pytest.raises(FetchError, match="exceeds"):
        _fetcher(session, max_bytes=100).fetch(PAGE)


def test_robots_disallow_blocks_the_fetch():
    session = FakeSession(
        {PAGE: FakeResponse(PAGE, text="<html><main>Hostel</main></html>")},
        robots="User-agent: *\nDisallow: /cms/\n",
    )
    fetcher = _fetcher(session, respect_robots=True)

    with pytest.raises(FetchError, match="robots.txt disallows"):
        fetcher.fetch(PAGE)
    assert session.page_calls() == []


def test_missing_robots_txt_is_not_a_prohibition():
    session = FakeSession(
        {PAGE: FakeResponse(PAGE, text="<html><main>Hostel</main></html>")},
        robots="",
        robots_status=404,
    )
    fetcher = _fetcher(session, respect_robots=True)

    result = fetcher.fetch(PAGE)
    assert result.status == 200
