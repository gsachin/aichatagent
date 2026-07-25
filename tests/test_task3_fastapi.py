"""
Task 3 — Create app/main.py (FastAPI Skeleton)
===============================================
Goal: FastAPI app starts, GET / returns JSON health check, server is reachable.
"""

import json

import httpx
import pytest


class TestTask3FastAPI:
    """Tests for the FastAPI health-check skeleton."""

    def test_app_object_importable(self):
        """The FastAPI 'app' instance must be importable from app.main."""
        from app.main import app
        assert app is not None

    def test_app_has_routes(self):
        """The app must have at least one registered route."""
        from app.main import app
        assert len(app.routes) >= 1, "No routes registered on the FastAPI app"

    @pytest.mark.anyio
    async def test_root_returns_200(self, test_server: str):
        """GET / must return HTTP 200."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    @pytest.mark.anyio
    async def test_root_content_type_json(self, test_server: str):
        """GET / must return JSON content-type."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/")
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct, f"Expected JSON, got '{ct}'"

    @pytest.mark.anyio
    async def test_root_returns_status_ok(self, test_server: str):
        """GET / JSON body must contain 'status': 'ok'."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/")
        data = resp.json()
        assert data.get("status") == "ok", f"Expected status='ok', got {data}"

    @pytest.mark.anyio
    async def test_root_body_is_valid_json(self, test_server: str):
        """GET / body must parse as valid JSON without exceptions."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_server + "/")
        try:
            parsed = resp.json()
            assert isinstance(parsed, dict), "JSON root must be a dict"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON from /: {e}")

    @pytest.mark.anyio
    async def test_server_shutdown_clean(self, test_server: str):
        """Server must respond to multiple consecutive requests without error."""
        async with httpx.AsyncClient() as client:
            for _ in range(3):
                resp = await client.get(test_server + "/")
                assert resp.status_code == 200
