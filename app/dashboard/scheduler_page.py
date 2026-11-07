"""
Dashboard Scheduler page — view and manage follow-ups.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st

from app.dashboard.pages import api_call


def render():
    st.title("📅 Follow-Up Scheduler")
    st.markdown("---")

    # ── Schedule new follow-up ───────────────────────────────────
    with st.expander("➕ Schedule New Follow-Up", expanded=False):
        col_a, col_b, col_c = st.columns(3)

        with col_a:
            # Fetch leads for the dropdown
            leads = api_call("GET", "/api/leads?limit=100") or []
            lead_options = {
                f"{l.get('name', 'Unknown')} ({l.get('phone_number', 'N/A')})": l["id"]
                for l in leads
            }
            selected_lead = st.selectbox(
                "Select Lead",
                list(lead_options.keys()),
            )
            selected_lead_id = lead_options.get(selected_lead, "")

        with col_b:
            follow_up_date = st.date_input("Date", value=datetime.now() + timedelta(days=1))
            follow_up_time = st.time_input("Time", value=datetime.now().replace(hour=14, minute=0))

        with col_c:
            follow_up_type = st.selectbox("Type", ["call", "message"])
            follow_up_notes = st.text_area("Notes", placeholder="Reason for follow-up...")

        if st.button("Schedule Follow-Up", type="primary"):
            if selected_lead_id:
                scheduled_dt = datetime.combine(follow_up_date, follow_up_time)
                scheduled_iso = scheduled_dt.isoformat()

                result = api_call(
                    "POST",
                    "/api/follow-ups",
                    {
                        "lead_id": selected_lead_id,
                        "scheduled_at": scheduled_iso,
                        "type": follow_up_type,
                        "notes": follow_up_notes,
                    },
                )
                if result and "error" not in result:
                    st.success(f"Follow-up scheduled for {scheduled_iso[:19]}")
                    st.rerun()
                else:
                    st.error("Failed to schedule follow-up")
            else:
                st.warning("Please select a lead")

    st.markdown("---")

    # ── Upcoming follow-ups ──────────────────────────────────────
    st.subheader("📋 Scheduled Follow-Ups")

    # Get all leads to cross-reference
    leads = api_call("GET", "/api/leads?limit=200") or []
    lead_map = {l["id"]: l for l in leads}

    # We fetch follow-ups from the conversations endpoint for now
    # (a dedicated follow-ups list endpoint would be ideal)
    # For now, show leads with next_follow_up set
    upcoming_leads = [
        l for l in leads
        if l.get("next_follow_up")
    ]

    if upcoming_leads:
        # Sort by next_follow_up
        upcoming_leads.sort(key=lambda l: l.get("next_follow_up", ""))

        for lead in upcoming_leads:
            lead_id = lead.get("id", "")
            name = lead.get("name") or "Unknown"
            phone = lead.get("phone_number", "N/A")
            follow_up = lead.get("next_follow_up", "")
            status = lead.get("status", "pending")

            with st.expander(f"📞 {name} — {phone} — {follow_up[:19] if follow_up else 'N/A'}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**Status:** {status}")
                    st.write(f"**Program:** {lead.get('program_interest', 'N/A')}")
                with col2:
                    if st.button("📞 Call Now", key=f"fu_call_{lead_id}"):
                        api_call("POST", f"/api/leads/{lead_id}/call")
                        st.success("Call queued!")
                    if st.button("✅ Mark Complete", key=f"fu_done_{lead_id}"):
                        api_call("PUT", f"/api/leads/{lead_id}", {"status": "completed", "next_follow_up": None})
                        st.success("Marked complete!")
                        st.rerun()
    else:
        st.info("No upcoming follow-ups. Schedule one above!")
