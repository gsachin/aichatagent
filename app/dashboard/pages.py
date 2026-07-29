"""
Dashboard navigation and shared utilities.
"""

from __future__ import annotations

import streamlit as st

# Backend base URL — reads from env so it can point to the tunnel URL
import os

BACKEND_URL = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")


def setup_page():
    """Configure Streamlit page settings once per session."""
    st.set_page_config(
        page_title="Admissions Admin Dashboard",
        page_icon="🎓",
        layout="wide",
    )


def show_sidebar():
    """Render the sidebar nav and return the selected page name."""
    st.sidebar.title("🎓 Admin Dashboard")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigation",
        ["📊 Overview", "👥 Leads", "💬 Conversations", "📅 Scheduler"],
    )
    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"Backend: {BACKEND_URL}\n"
        "Twilio: +19788198953"
    )

    # Map display names to page keys
    return {
        "📊 Overview": "overview",
        "👥 Leads": "leads",
        "💬 Conversations": "conversations",
        "📅 Scheduler": "scheduler",
    }[page]


def api_call(method: str, path: str, body: dict | None = None) -> dict | list | None:
    """
    Make a REST call to the FastAPI backend.

    Returns parsed JSON or None on failure.
    """
    import urllib.request
    import json as _json

    url = f"{BACKEND_URL}{path}"
    try:
        data = None
        if body is not None:
            data = _json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        st.error(f"API error ({method} {path}): {e}")
        return None
