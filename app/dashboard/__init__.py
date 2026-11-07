"""
Streamlit admin dashboard — University Admissions Voice Assistant.

Provides pages for:
- Overview  : KPI cards and quick actions
- Leads     : CRUD table with filters and action buttons
- Conversations : transcript viewer with search
- Scheduler : follow-up calendar/table and scheduling forms

The dashboard is a separate Streamlit app (dashboard.py, port 8502)
that talks to the FastAPI backend via REST.
"""
