"""
Dashboard Overview page — KPI cards, quick actions, recent activity.
"""

from __future__ import annotations

import streamlit as st

from app.dashboard.pages import api_call


def render():
    st.title("📊 Dashboard Overview")
    st.markdown("---")

    # Fetch stats from backend
    stats = api_call("GET", "/api/stats") or {}

    # ── KPI row ──────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)

    total = stats.get("total_leads", 0)
    by_status = stats.get("by_status", {})
    calls_today = stats.get("calls_today", 0)
    upcoming = stats.get("upcoming_follow_ups", 0)

    with col1:
        st.metric("Total Leads", total)
    with col2:
        st.metric("Pending", by_status.get("pending", 0))
    with col3:
        st.metric("In Progress", by_status.get("in_progress", 0))
    with col4:
        st.metric("Calls Today", calls_today)
    with col5:
        st.metric("Upcoming Follow-ups", upcoming)

    st.markdown("---")

    # ── Quick actions ────────────────────────────────────────────
    st.subheader("⚡ Quick Actions")

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        if st.button("📞 Call Next Pending Lead", use_container_width=True):
            # Get first pending lead and trigger call
            leads = api_call("GET", "/api/leads?status=pending&limit=1") or []
            if leads:
                lead = leads[0]
                result = api_call("POST", f"/api/leads/{lead['id']}/call")
                if result:
                    st.success(f"Call queued for {lead.get('phone_number', 'N/A')}")
                else:
                    st.error("Failed to queue call")
            else:
                st.warning("No pending leads")

    with col_b:
        if st.button("🔄 Refresh Stats", use_container_width=True):
            st.rerun()

    with col_c:
        st.empty()  # placeholder for future quick action

    st.markdown("---")

    # ── Status breakdown ─────────────────────────────────────────
    st.subheader("Lead Status Breakdown")

    if by_status:
        import pandas as pd

        df = pd.DataFrame(
            [
                {"Status": status.title(), "Count": count}
                for status, count in by_status.items()
            ]
        )
        st.bar_chart(df.set_index("Status"), use_container_width=True)
    else:
        st.info("No lead data yet. Add leads to see stats.")

    st.markdown("---")

    # ── Recent leads ─────────────────────────────────────────────
    st.subheader("Recent Leads")

    leads = api_call("GET", "/api/leads?limit=5") or []
    if leads:
        for lead in leads:
            status_emoji = {
                "pending": "🟡",
                "in_progress": "🔵",
                "completed": "🟢",
                "failed": "🔴",
                "unreachable": "⚫",
            }.get(lead.get("status", ""), "⚪")

            with st.expander(
                f"{status_emoji} {lead.get('name', 'Unknown')} — {lead.get('phone_number', 'N/A')}"
            ):
                st.write(f"**Status:** {lead.get('status', 'N/A')}")
                st.write(f"**Program:** {lead.get('program_interest', 'Not specified')}")
                st.write(f"**Source:** {lead.get('source', 'N/A')}")
                if lead.get("notes"):
                    st.write(f"**Notes:** {lead['notes']}")
    else:
        st.info("No leads yet.")
