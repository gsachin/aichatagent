"""
Task 8 — Serve voice_client.html from FastAPI + Browser Test
=============================================================
Goal: GET /voice serves the static HTML page with correct content-type.
      GET / links to /voice.
"""

import httpx
import pytest


class TestTask8ServeVoicePage:
    """Verify FastAPI serves the voice client HTML page correctly."""

    @pytest.mark.anyio
    async def test_voice_route_returns_200(self, test_server: str):
        """GET /voice must return HTTP 200."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/voice")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    @pytest.mark.anyio
    async def test_voice_route_content_type_html(self, test_server: str):
        """GET /voice must return text/html content-type."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/voice")
        ct = resp.headers.get("content-type", "")
        assert "text/html" in ct, f"Expected text/html, got '{ct}'"

    @pytest.mark.anyio
    async def test_voice_route_returns_non_empty_body(self, test_server: str):
        """GET /voice response body must be > 500 bytes (the actual HTML page)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/voice")
        assert len(resp.content) > 500, (
            f"Response too small: {len(resp.content)} bytes — expected full HTML page"
        )

    @pytest.mark.anyio
    async def test_voice_page_contains_html_markup(self, test_server: str):
        """Response body from /voice must contain recognizable HTML."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/voice")
        text = resp.text.lower()
        assert "<html" in text or "<!doctype" in text, (
            "Response does not look like HTML"
        )

    @pytest.mark.anyio
    async def test_health_page_links_to_voice(self, test_server: str):
        """GET / should mention or link to /voice."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/")
        data = resp.json()
        # The health response should have some navigation hint
        # Check for a 'links' or 'endpoints' key, or any mention of /voice in values
        found = False
        for value in data.values():
            if isinstance(value, str) and "/voice" in value:
                found = True
                break
        # If not in values, at least one key should hint at voice
        if not found:
            keys_lower = " ".join(k.lower() for k in data.keys())
            assert "voice" in keys_lower or "endpoint" in keys_lower or "link" in keys_lower, (
                f"Health page should reference the /voice endpoint. Got keys: {list(data.keys())}"
            )

    @pytest.mark.anyio
    async def test_voice_route_not_cached_aggressively(self, test_server: str):
        """The HTML page should not be cached forever (check Cache-Control is not immutable)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/voice")
        cc = resp.headers.get("cache-control", "")
        # If cache-control is set, must not be 'immutable' for a dev page
        assert "immutable" not in cc.lower(), (
            f"Voice page should not be cached immutably during development: {cc}"
        )
