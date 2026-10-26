"""Request-time retrieval from the public DSPMU website.

No test here touches the network. The retriever accepts a session, so every
transport case runs against fixture HTML; the RAG integration test injects a
prebuilt result.
"""

from __future__ import annotations

import threading

import pytest

from app.context_bundle import ContextBundle
from app.live_site import LiveSite, LiveSiteConfig, LiveSiteResult

SITEMAP_URL = "https://dspmuranchi.ac.in/sitemap.xml"
HOSTEL_URL = "https://dspmuranchi.ac.in/cms/hostel"
HOSTEL_RULES_URL = "https://dspmuranchi.ac.in/cms/hostel-rules"
SCHOLARSHIP_URL = "https://dspmuranchi.ac.in/cms/scholarships-student-freeship"
RESULTS_URL = "https://dspmuranchi.ac.in/cms/results"


def _sitemap(*urls: str) -> str:
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def _page(title: str, body: str) -> str:
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<nav>Home About</nav><main><h1>{title}</h1>{body}</main>"
        "<footer>Footer</footer></body></html>"
    )


class FakeResponse:
    def __init__(self, url: str, text: str, status: int = 200,
                 content_type: str = "text/html; charset=utf-8") -> None:
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.content = text.encode("utf-8")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse],
                 robots: str = "User-agent: *\nAllow: /\n",
                 robots_status: int = 200) -> None:
        self.responses = responses
        self.robots = robots
        self.robots_status = robots_status
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, timeout=None, allow_redirects=True, headers=None):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(
                url, self.robots, self.robots_status, "text/plain; charset=utf-8"
            )
        if url in self.responses:
            return self.responses[url]
        return FakeResponse(
            url,
            "<html><body><h1>404 - Page Not Found</h1>"
            "<a>Go Back Home</a></body></html>",
        )

    def page_fetches(self) -> list[str]:
        return [url for url in self.calls if not url.endswith("/robots.txt")]


def _config(**overrides) -> LiveSiteConfig:
    values = dict(
        base_url="https://dspmuranchi.ac.in",
        sitemap_url=SITEMAP_URL,
        allowed_hosts=("dspmuranchi.ac.in",),
        timeout_s=1.0,
        delay_s=0.0,
        max_pages=2,
        max_chars=2000,
        cache_ttl_s=600.0,
        sitemap_ttl_s=3600.0,
        respect_robots=True,
    )
    values.update(overrides)
    return LiveSiteConfig(**values)


def _session(*urls: str, pages: dict[str, FakeResponse] | None = None) -> FakeSession:
    responses = {
        SITEMAP_URL: FakeResponse(
            SITEMAP_URL, _sitemap(*urls), content_type="application/xml"
        )
    }
    responses.update(pages or {})
    return FakeSession(responses)


def test_selects_and_extracts_a_matching_page():
    session = _session(
        HOSTEL_URL,
        pages={
            HOSTEL_URL: FakeResponse(
                HOSTEL_URL,
                _page(
                    "Hostel",
                    "<p>Boys and girls hostels with mess, Wi-Fi and a warden "
                    "office. Apply through the hostel office before the "
                    "semester begins.</p>",
                ),
            )
        },
    )
    site = LiveSite(_config(), session=session)

    result = site.retrieve("What are the hostel facilities?")

    assert result is not None
    assert result.urls == (HOSTEL_URL,)
    assert "Boys and girls hostels" in result.text
    assert f"Source: {HOSTEL_URL}" in result.text
    assert result.titles == ("Hostel",)


def test_unmatched_question_makes_no_page_fetch():
    session = _session(HOSTEL_URL)
    site = LiveSite(_config(), session=session)

    assert site.retrieve("quantum flux capacitor") is None
    assert session.page_fetches() == [SITEMAP_URL]


def test_soft_404_is_skipped_and_the_next_candidate_is_used():
    session = _session(
        HOSTEL_URL,
        HOSTEL_RULES_URL,
        pages={
            HOSTEL_URL: FakeResponse(
                HOSTEL_URL,
                "<html><body><h1>404 - Page Not Found</h1>"
                "<p>Go Back Home</p></body></html>",
            ),
            HOSTEL_RULES_URL: FakeResponse(
                HOSTEL_RULES_URL,
                _page("Hostel Rules", "<p>" + ("Quiet hours and visitor rules. " * 8) + "</p>"),
            ),
        },
    )
    site = LiveSite(_config(), session=session)

    result = site.retrieve("hostel rules")

    assert result is not None
    assert result.urls == (HOSTEL_RULES_URL,)


def test_pii_titled_page_is_refused():
    session = _session(
        SCHOLARSHIP_URL,
        pages={
            SCHOLARSHIP_URL: FakeResponse(
                SCHOLARSHIP_URL,
                _page(
                    "Merit List of Selected Candidates",
                    "<p>" + ("Awarded to candidates. " * 10) + "</p>",
                ),
            )
        },
    )
    site = LiveSite(_config(), session=session)

    assert site.retrieve("scholarship list") is None


def test_blocked_path_is_never_fetched():
    session = _session(RESULTS_URL)
    site = LiveSite(_config(), session=session)

    assert site.retrieve("exam results") is None
    assert RESULTS_URL not in session.page_fetches()


def test_pages_are_cached_between_questions():
    session = _session(
        HOSTEL_URL,
        pages={
            HOSTEL_URL: FakeResponse(
                HOSTEL_URL,
                _page("Hostel", "<p>" + ("Hostel information. " * 10) + "</p>"),
            )
        },
    )
    site = LiveSite(_config(), session=session)

    assert site.retrieve("hostel") is not None
    assert site.retrieve("hostel facilities") is not None

    assert session.page_fetches().count(HOSTEL_URL) == 1


def test_robots_disallow_blocks_the_fetch():
    session = _session(
        HOSTEL_URL,
        pages={
            HOSTEL_URL: FakeResponse(
                HOSTEL_URL,
                _page("Hostel", "<p>" + ("Hostel information. " * 10) + "</p>"),
            )
        },
    )
    session.robots = "User-agent: *\nDisallow: /cms/hostel\n"
    site = LiveSite(_config(), session=session)

    assert site.retrieve("hostel") is None


def test_host_outside_the_allowlist_is_ignored():
    evil = "https://evil.example/hostel"
    session = _session(evil, HOSTEL_URL, pages={
        HOSTEL_URL: FakeResponse(
            HOSTEL_URL,
            _page("Hostel", "<p>" + ("Hostel information. " * 10) + "</p>"),
        )
    })
    site = LiveSite(_config(), session=session)

    result = site.retrieve("hostel")

    assert result is not None
    assert result.urls == (HOSTEL_URL,)
    assert evil not in session.page_fetches()


def test_empty_shell_is_not_used():
    session = _session(
        HOSTEL_URL,
        pages={HOSTEL_URL: FakeResponse(HOSTEL_URL, _page("Hostel", ""))},
    )
    site = LiveSite(_config(), session=session)

    assert site.retrieve("hostel") is None


def _dspmu_live_settings(override_settings, **overrides):
    values = dict(
        LIVE_SITE_ENABLED=True,
        LIVE_SITE_MODE="novel",
        PROGRAM_PROFILE="dspmu",
    )
    values.update(overrides)
    override_settings(**values)


def test_live_gate_covers_router_misses_and_novel_terms(override_settings):
    from app.rag import _needs_live_site

    _dspmu_live_settings(override_settings)
    fallback = ContextBundle(text="static profile", kind="fallback")
    fee = ContextBundle(text="BA History annual fee is 3410", kind="fee")

    assert _needs_live_site("what is the hostel fee", fallback) is True
    assert _needs_live_site("hostel facilities", fee) is True
    assert _needs_live_site("BA History fee", fee) is False
    assert _needs_live_site("fees?", fee) is False


def test_live_gate_is_off_for_boundaries_and_other_brands(override_settings):
    from app.rag import _needs_live_site

    _dspmu_live_settings(override_settings)
    assert _needs_live_site(
        "show the merit list", ContextBundle(text="x", kind="boundary")
    ) is False
    _dspmu_live_settings(override_settings, PROGRAM_PROFILE="other")
    assert _needs_live_site(
        "hostel facilities", ContextBundle(text="x", kind="fallback")
    ) is False


def test_augment_appends_live_text_and_provenance(override_settings, monkeypatch):
    from app import rag

    _dspmu_live_settings(override_settings)
    monkeypatch.setattr(
        "app.live_site.retrieve_live_context",
        lambda query: LiveSiteResult(
            text="## Live university website\n\n### Hostel\nSource: " + HOSTEL_URL,
            urls=(HOSTEL_URL,),
            titles=("Hostel",),
            fetched_at="2026-10-24 18:00 UTC",
        ),
    )
    static = ContextBundle(
        text="static profile", node_ids=("core_cards",), source_urls=("https://a",),
        kind="fallback",
    )

    combined = rag._augment_with_live("hostel facilities", static)

    assert combined.text.startswith("static profile")
    assert "Live university website" in combined.text
    assert combined.kind == "fallback+live"
    assert combined.source_urls == ("https://a", HOSTEL_URL)
    assert "live_site" in combined.node_ids


@pytest.mark.anyio
async def test_stream_builds_the_bundle_off_the_event_loop(monkeypatch):
    from app import rag

    caller_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_bundle(query: str) -> ContextBundle:
        seen["thread"] = threading.get_ident()
        return ContextBundle(text="static profile")

    async def fake_stream(prompt: str, **kwargs):
        yield "ok"

    monkeypatch.setattr(rag, "context_bundle_for_query", fake_bundle)
    monkeypatch.setattr("app.llm_backend.generate_stream", fake_stream)

    chunks = [item async for item in rag.query_rag_stream("hostel facilities")]

    assert chunks == ["ok"]
    assert seen["thread"] != caller_thread
