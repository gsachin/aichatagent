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
        ["📊 Overview", "👥 Leads", "💬 Conversations", "📅 Scheduler", "📚 Courses"],
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
        "📚 Courses": "courses",
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


def api_upload(path: str, field_name: str, filename: str,
               file_bytes: bytes, extra_fields: dict | None = None) -> dict | None:
    """
    Upload a file via multipart/form-data to the FastAPI backend.

    Returns parsed JSON or None on failure.
    """
    import urllib.request
    import json as _json
    import uuid

    boundary = f"----streamlit-{uuid.uuid4().hex}"
    parts = []
    for k, v in (extra_fields or {}).items():
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        )
    parts.append(
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"{field_name}\"; "
        f"filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    )
    header = "".join(parts).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + file_bytes + footer

    url = f"{BACKEND_URL}{path}"
    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        st.error(f"Upload error (POST {path}): {e}")
        return None


def api_call_bytes(path: str) -> bytes | None:
    """Make a GET request returning raw bytes (for file downloads)."""
    import urllib.request

    url = f"{BACKEND_URL}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception as e:
        st.error(f"Download error (GET {path}): {e}")
        return None
