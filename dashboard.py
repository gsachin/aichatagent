"""
Streamlit Admin Dashboard — University Admissions Voice Assistant.

Run: streamlit run dashboard.py --server.port 8502

Connects to the FastAPI backend (default http://localhost:8000).
Set DASHBOARD_API_URL env var to point to a tunnel URL for remote access.
"""

import streamlit as st

# Import pages
from app.dashboard.pages import setup_page, show_sidebar
from app.dashboard.overview import render as overview_page
from app.dashboard.leads_page import render as leads_page
from app.dashboard.conversations_page import render as conversations_page
from app.dashboard.scheduler_page import render as scheduler_page


def main():
    setup_page()

    # ── Sidebar ──────────────────────────────────────────────────
    page = show_sidebar()

    # ── Page routing ─────────────────────────────────────────────
    if page == "overview":
        overview_page()
    elif page == "leads":
        leads_page()
    elif page == "conversations":
        conversations_page()
    elif page == "scheduler":
        scheduler_page()


if __name__ == "__main__":
    main()
else:
    # Allow running via `streamlit run dashboard.py`
    main()
