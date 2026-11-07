"""Bounded deep-search service tests (no network)."""

from __future__ import annotations

from dataclasses import dataclass

from app.deepsearch.service import (
    DeepSearchOptions,
    deep_search,
    deep_search_message,
)

BASE = "https://dspmuranchi.ac.in"
SEARCH_HTML = (
    "<html><body>"
    f'<a href="/cms/hostel">Hostel warden office</a>'
    f'<a href="/uploads/merit-list.pdf">Merit List 2026</a>'
    "</body></html>"
)
PAGE_HTML = (
    "<html><main><h1>Hostel</h1><p>"
    "The hostel has a warden office and the warden is available daily for "
    "student support and residential enquiries."
    "</p></main></html>"
)


@dataclass
class Response:
    status: int = 200
    text: str = ""
    final_url: str = ""
    body: bytes = b""
    content_type: str = "text/html"
    not_modified: bool = False


class FakeFetcher:
    def __init__(self, pages: dict[str, Response]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url, **_kwargs):
        self.calls.append(url)
        return self.pages.get(url, Response(status=404))

    def fetch_document(self, url, **_kwargs):
        return self.fetch(url)


def test_deep_search_fetches_a_result_and_returns_site_open_evidence():
    fetcher = FakeFetcher(
        {
            f"{BASE}/search?q=hostel": Response(
                text=SEARCH_HTML, final_url=f"{BASE}/search?q=hostel"
            ),
            f"{BASE}/cms/hostel": Response(
                text=PAGE_HTML, final_url=f"{BASE}/cms/hostel"
            ),
        }
    )
    result = deep_search(
        "hostel",
        fetcher,
        options=DeepSearchOptions(max_results=3, max_fetches=1),
    )

    assert result.status == "ok"
    assert result.fetched == 1
    item = result.results[0]
    assert item["tool"] == "site.open"
    assert item["data"]["node"]["url"].endswith("/cms/hostel")
    assert "warden office" in item["data"]["sections"][0]["text"]
    assert all("merit-list" not in url for url in result.urls)


def test_empty_shell_is_returned_as_a_handoff_not_discarded():
    html = "<html><body><main><h1>Hostel</h1></main></body></html>"
    fetcher = FakeFetcher(
        {
            f"{BASE}/search?q=hostel+rules": Response(
                text=(
                    '<div class="search-result-card">'
                    '<h5 class="result-title"><a href="/cms/hostel">Hostel</a></h5>'
                    "</div>"
                ),
                final_url=f"{BASE}/search?q=hostel+rules",
            ),
            f"{BASE}/cms/hostel": Response(text=html, final_url=f"{BASE}/cms/hostel"),
        }
    )
    result = deep_search(
        "hostel rules",
        fetcher,
        options=DeepSearchOptions(max_results=3, max_fetches=1),
    )

    assert result.status == "ok"
    item = result.results[0]
    assert item["data"]["handoff"] is True
    assert item["data"]["body_available"] is False
    assert item["data"]["content_status"] == "empty_shell"
    assert item["data"]["node"]["url"].endswith("/cms/hostel")


def test_search_cards_preserve_the_site_ranking():
    cards = (
        '<div class="search-result-card">'
        '<h5 class="result-title"><a href="/cms/academic-programmes">'
        "Academic Programmes</a></h5></div>"
        '<div class="search-result-card">'
        '<h5 class="result-title"><a href="/cms/about-dspmu">About</a></h5></div>'
        '<a href="/cms/academic-council">Nav link</a>'
    )
    fetcher = FakeFetcher(
        {
            f"{BASE}/search?q=programmes": Response(
                text=cards, final_url=f"{BASE}/search?q=programmes"
            ),
            f"{BASE}/cms/academic-programmes": Response(
                text="<main><h1>Academic Programmes</h1></main>",
                final_url=f"{BASE}/cms/academic-programmes",
            ),
        }
    )
    result = deep_search(
        "programmes",
        fetcher,
        options=DeepSearchOptions(max_results=3, max_fetches=1),
    )

    assert result.urls[0].endswith("/cms/academic-programmes")
    assert "/cms/academic-council" not in result.urls


def test_deep_search_returns_no_results_when_only_pii_links_exist():
    html = '<a href="/uploads/merit-list.pdf">Merit List 2026</a>'
    fetcher = FakeFetcher(
        {
            f"{BASE}/search?q=topper": Response(
                text=html, final_url=f"{BASE}/search?q=topper"
            ),
        }
    )

    result = deep_search(
        "topper",
        fetcher,
        options=DeepSearchOptions(
            max_results=3, max_fetches=1, pii_guard=True
        ),
    )

    assert result.status == "no_results"
    assert result.urls == []


def test_deep_search_message_is_localized():
    assert "official university website" in deep_search_message("en")
    assert deep_search_message("hi") != deep_search_message("en")
    assert deep_search_message("gu") != deep_search_message("en")
